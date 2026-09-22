"""`tank-level-control`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "tank-level-control"


# --- tank level control references --------------------------------------

async def _tank_body(bus, stop, *, gain: float, deadband: float,
                     fixed: float | None) -> None:
    """One implementation, three behaviours.

    `gain` with no deadband is proportional control on a plant whose only
    outflow is the drain valve, so the error really does go to zero. `deadband`
    turns it into a pair of float switches. `fixed` ignores the pot.
    """
    scanner = Scanner(bus)

    async def body(dt: float) -> None:
        scanner.scan()
        level = scanner.num("tank.level")
        setpoint = fixed if fixed is not None else scanner.setpoint
        fill = drain = 0.0
        if scanner.running:
            error = setpoint - level
            if deadband > 0.0:
                if error > deadband:
                    fill = 100.0
                elif error < -deadband:
                    drain = 100.0
            else:
                fill = min(max(error * gain, 0.0), 100.0)
                drain = min(max(-error * gain, 0.0), 100.0)
        await bus.write_many({"tank.fill": fill, "tank.drain": drain,
                              "level_readout.value": int(round(level)),
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _tank_good(bus, stop):
    """Proportional, modulating rather than saturating. A gain that pins the
    valve at 100 % until the setpoint arrives is bang-bang wearing a float's
    clothes, and it hides the nonlinearity this scene exists to show."""
    await _tank_body(bus, stop, gain=1.6, deadband=0.0, fixed=None)


async def _tank_bangbang(bus, stop):
    """A pair of float switches six percent apart. It reaches the setpoint --
    and then parks at the edge of the band, because with both valves shut this
    tank has no outflow at all."""
    await _tank_body(bus, stop, gain=0.0, deadband=6.0, fixed=None)


async def _tank_fixedsp(bus, stop):
    """Good control of the wrong number. Holds 70 % beautifully and never reads
    the pot, which is invisible until somebody turns it."""
    await _tank_body(bus, stop, gain=1.6, deadband=0.0, fixed=70.0)


REFERENCES = {"good": _tank_good, "bangbang": _tank_bangbang,
              "fixedsp": _tank_fixedsp}
