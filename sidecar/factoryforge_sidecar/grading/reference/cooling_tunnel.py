"""`cooling-tunnel`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner, contract_references


SCENE = "cooling-tunnel"

#: The PI the heat treat station's `good` uses, on the same plate.
CT_KP, CT_KI = 3.5, 0.6
#: The split's deadband, in percent of controller output: inside it neither
#: actuator runs, so the handover cannot chatter.
CT_DEADBAND = 5.0
#: The airflow `fight` never lets the fan drop below while the line runs.
CT_FIGHT_FAN = 30.0


async def _ct_body(bus, stop, *, split: bool, fan_floor: float = 0.0) -> None:
    """One PI, and three things to do with its output.

    `split` sends a positive output to the heater and a negative one, past
    the deadband, to the fan -- and never lets the fan fall below
    `fan_floor`, which for `good` is nothing. Without `split` the output is
    clamped to the heater's 0..100 and the fan never runs.
    """
    scanner = Scanner(bus)
    state = {"integral": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        temperature = scanner.num("oven.temperature")
        setpoint = scanner.setpoint
        heater = fan = 0.0
        if scanner.running:
            error = setpoint - temperature
            proportional = error * CT_KP
            low = -100.0 if split else 0.0
            output = proportional + state["integral"] * CT_KI
            # Integrate only off the stops -- the windup rule the oven's
            # `good` follows, and the reason a 60 C drop does not leave a
            # minute of integral to unwind.
            if low < output < 100.0:
                state["integral"] = min(max(state["integral"] + error * dt,
                                            -170.0), 170.0)
            output = min(max(proportional + state["integral"] * CT_KI, low), 100.0)
            if split:
                heater = max(output, 0.0)
                fan = max(-output - CT_DEADBAND, 0.0) * 100.0 / (100.0 - CT_DEADBAND)
                fan = min(max(fan, fan_floor), 100.0)
            else:
                heater = max(output, 0.0)
        else:
            state["integral"] = 0.0
        await bus.write_many({
            "oven.heater": heater,
            "fan.run": fan > 0.0,
            "fan.speed": fan,
            "temp_gauge.value": temperature,
            "tower.green": scanner.running,
            "tower.yellow": not scanner.running,
            "tower.red": scanner.tripped,
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ct_good(bus, stop):
    """Split range with a deadband: heater above zero, fan below it, never
    both."""
    await _ct_body(bus, stop, split=True)


async def _ct_heatonly(bus, stop):
    """The heat treat station's PI, fan never touched. Every hold is fine; the
    drop in the recipe arrives as fast as the room can take the heat away."""
    await _ct_body(bus, stop, split=False)


async def _ct_fight(bus, stop):
    """`good`'s split range, with a fan that never drops below 30 % while
    the line runs -- "it is a cooling tunnel, the fan is always on". Holds
    every setpoint and meets every recipe change, and heats the air the fan
    blows away."""
    await _ct_body(bus, stop, split=True, fan_floor=CT_FIGHT_FAN)


REFERENCES = {"good": _ct_good, "heatonly": _ct_heatonly, "fight": _ct_fight,
              **contract_references(_ct_good)}
