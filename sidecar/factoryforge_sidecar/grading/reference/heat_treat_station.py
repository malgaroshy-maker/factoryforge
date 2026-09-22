"""`heat-treat-station`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "heat-treat-station"


# --- heat treat station references ---------------------------------------

async def _oven_body(bus, stop, *, gain: float, integral_gain: float,
                     deadband: float) -> None:
    scanner = Scanner(bus)
    state = {"integral": 0.0, "on": False}

    async def body(dt: float) -> None:
        scanner.scan()
        temperature = scanner.num("oven.temperature")
        setpoint = scanner.setpoint
        power = 0.0
        if scanner.running:
            error = setpoint - temperature
            if deadband > 0.0:
                # Real hysteresis, because a thermostat without it chatters the
                # contactor to death -- and the hysteresis is precisely what
                # puts the swing in. Element on below setpoint minus the band,
                # off above setpoint plus it, latched in between.
                if temperature <= setpoint - deadband:
                    state["on"] = True
                elif temperature >= setpoint + deadband:
                    state["on"] = False
                power = 100.0 if state["on"] else 0.0
            else:
                proportional = error * gain
                if integral_gain > 0.0 and -100.0 < proportional < 100.0:
                    # Only off the stops. Integrating through a cold start's
                    # flat-out heating is textbook windup, and it is what turns
                    # a working PI into a 40 degC overshoot.
                    state["integral"] = min(max(state["integral"] + error * dt,
                                                -140.0), 140.0)
                power = min(max(proportional + state["integral"] * integral_gain,
                                0.0), 100.0)
        else:
            state["integral"] = 0.0
        await bus.write_many({"oven.heater": power,
                              "temp_gauge.value": temperature,
                              "temp_readout.value": int(round(temperature)),
                              "alarm.beacon": temperature > setpoint + 25.0,
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _oven_good(bus, stop):
    """PI, with enough integral authority to supply the whole standing output.
    An integral that can only contribute a tenth of what the plate loses cannot
    close the offset it was added to close."""
    await _oven_body(bus, stop, gain=3.5, integral_gain=0.6, deadband=0.0)


async def _oven_ponly(bus, stop):
    """The lesson, written out. Gain 3.5 and nothing else, so the plate parks
    exactly (loss / element) / gain degrees short -- and parks somewhere else
    when the setpoint moves, because the offset depends on the setpoint."""
    await _oven_body(bus, stop, gain=3.5, integral_gain=0.0, deadband=0.0)


async def _oven_thermostat(bus, stop):
    """Element full on below setpoint, off above. It reaches the setpoint every
    couple of seconds from alternate sides and never holds it: this plant
    always loses heat, so the cycling never stops."""
    await _oven_body(bus, stop, gain=0.0, integral_gain=0.0, deadband=4.0)


REFERENCES = {"good": _oven_good, "ponly": _oven_ponly,
              "thermostat": _oven_thermostat}
