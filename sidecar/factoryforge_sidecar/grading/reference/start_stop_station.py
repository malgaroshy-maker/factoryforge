"""`start-stop-station`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "start-stop-station"


# --- start / stop station references ------------------------------------

async def _ss_body(bus, stop, latch_estop: bool, stop_at_target: bool) -> None:
    """One implementation, three behaviours, so the two wrong ones differ from
    the right one in exactly one place and nothing else."""
    scanner = Scanner(bus, latch_estop=latch_estop)
    state = {"made": 0, "present": False, "feed": 0.0, "emit": False}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        if edges["reset"]:
            state["made"] = 0

        target = int(round(scanner.setpoint))
        present = scanner.bit("part_present.detect")
        if present and not state["present"] and scanner.running:
            state["made"] += 1
        state["present"] = present

        if stop_at_target and target > 0 and state["made"] >= target:
            scanner.running = False

        if scanner.running:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 1.2 if state["emit"] else 0.3
        else:
            state["emit"] = False

        await bus.write_many({"belt.rotate": scanner.running,
                              "emitter.emit": state["emit"],
                              "produced.value": state["made"],
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ss_good(bus, stop):
    """What the exercise asks for: a latching E-stop and a batch on the pot."""
    await _ss_body(bus, stop, latch_estop=True, stop_at_target=True)


async def _ss_noestop(bus, stop):
    """Reads Start and Stop and never reads the mushroom. The belt keeps
    running through the strike, which is the one thing this station is for."""
    await _ss_body(bus, stop, latch_estop=False, stop_at_target=True)


async def _ss_runon(bus, stop):
    """Counts, displays the count, and never stops at the target -- the batch
    controller that is really just a conveyor with a display on it."""
    await _ss_body(bus, stop, latch_estop=True, stop_at_target=False)


REFERENCES = {"good": _ss_good, "noestop": _ss_noestop,
              "runon": _ss_runon}
