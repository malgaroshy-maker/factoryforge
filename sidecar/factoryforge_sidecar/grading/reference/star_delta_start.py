"""`star-delta-start`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "star-delta-start"

#: What `timed` waits in star. Calibrated on the empty machine, the way a
#: student would set a timing relay: the first start reaches the pot's 85 %
#: in about 3.1 s, so 3.3 s changes over cleanly -- once.
SD_TIMED_STAR = 3.3


async def _sd_body(bus, stop, *, on_speed: bool, dead_time: bool) -> None:
    """One sequence, three behaviours.

    `on_speed` changes over when `motor.speed` reaches the pot; without it,
    after a fixed time in star. `dead_time` waits for the star contactor's
    auxiliary contact to fall before energising delta; without it, the
    changeover drops star and energises delta in the same scan.
    """
    scanner = Scanner(bus)
    state = {"phase": "off", "in_star": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        healthy = scanner.bit("motor.breaker") and scanner.bit("motor.overload")
        if not healthy:
            scanner.running = False
        if not scanner.running:
            state["phase"] = "off"
        elif state["phase"] == "off":
            state["phase"] = "star"
            state["in_star"] = 0.0

        if state["phase"] == "star":
            state["in_star"] += dt
            ready = (scanner.num("motor.speed") >= scanner.setpoint if on_speed
                     else state["in_star"] >= SD_TIMED_STAR)
            if ready:
                state["phase"] = "gap" if dead_time else "delta"
        if state["phase"] == "gap" and not scanner.bit("motor.staraux"):
            state["phase"] = "delta"

        phase = state["phase"]
        await bus.write_many({
            "motor.main": phase != "off",
            "motor.star": phase == "star",
            "motor.delta": phase == "delta",
            "current_gauge.value": scanner.num("motor.current"),
            "tower.green": phase == "delta",
            "tower.yellow": phase in ("star", "gap"),
            "tower.red": not healthy,
            "panel.green": scanner.running,
            "panel.red": not healthy or scanner.tripped,
        })

    await run_scan(bus, stop, body)


async def _sd_good(bus, stop):
    """Changes over when the motor reaches the pot's speed, and waits for the
    star contacts to open before it energises delta."""
    await _sd_body(bus, stop, on_speed=True, dead_time=True)


async def _sd_samescan(bus, stop):
    """Right about when, wrong about how: drops star and energises delta in
    the same scan, so delta closes while star is still arcing."""
    await _sd_body(bus, stop, on_speed=True, dead_time=False)


async def _sd_timed(bus, stop):
    """Changes over after a fixed time in star, with a proper dead time.
    Right on the empty machine it was calibrated on, and half way up the
    run-up once the machine is loaded."""
    await _sd_body(bus, stop, on_speed=False, dead_time=True)


REFERENCES = {"good": _sd_good, "samescan": _sd_samescan, "timed": _sd_timed}
