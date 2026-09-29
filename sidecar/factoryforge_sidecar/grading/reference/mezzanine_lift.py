"""`mezzanine-lift`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.mezzanine_lift import ML_RATED_CLIMB
from ._shared import Scanner, contract_references


SCENE = "mezzanine-lift"

#: What `timed` allows the carriage to climb a level: the rated time and a
#: little over, which is exactly what a student measures on the day.
ML_TIMED = ML_RATED_CLIMB + 0.2
#: How long the emit pulse stays high: well over one poll.
ML_EMIT = 0.3


async def _ml_body(bus, stop, *, arrive: str, stop_deck: bool) -> None:
    """One lift trip per carton, two ways to get it wrong.

    `arrive` is how the program knows the carriage has reached level 1:
    "atlevel" (`atlevel` with `level` = 1: right) or "timed" (the rated climb
    on a stopwatch). `stop_deck` is whether the deck is stopped once the
    carton is aboard (right) or left running from loading until the trip's
    discharge is done.

    One carton on its way at a time: the next is emitted once the last one is
    aboard, so the infeed never holds a queue for the gate to rise into.
    """
    scanner = Scanner(bus)
    state = {"phase": "load", "t": 0.0, "waiting": False, "emit_for": 0.0,
             "was_occupied": False}

    async def body(dt: float) -> None:
        scanner.scan()
        s = state
        s["t"] += dt
        occupied = scanner.bit("lift.occupied")
        atlevel = scanner.bit("lift.atlevel")
        level = int(scanner.num("lift.level"))

        def go(phase: str) -> None:
            s["phase"], s["t"] = phase, 0.0

        if scanner.running:
            phase = s["phase"]
            if phase == "load" and occupied:
                go("settle")
            elif phase == "settle" and s["t"] >= scanner.setpoint:
                go("hoist")
            elif phase == "hoist" and ((atlevel and level == 1) if arrive == "atlevel"
                                       else s["t"] >= ML_TIMED):
                go("discharge")
            elif phase == "discharge" and scanner.bit("out_eye.detect"):
                go("lower")
            elif phase == "lower" and atlevel and level == 0:
                go("load")

            # The feed: a carton when none is on its way, the next once it is
            # aboard -- on the edge, since the carton stays aboard for a while.
            if occupied and not s["was_occupied"]:
                s["waiting"] = False
            if not s["waiting"] and s["emit_for"] <= 0.0:
                s["waiting"], s["emit_for"] = True, ML_EMIT + dt
        s["was_occupied"] = occupied
        s["emit_for"] = max(0.0, s["emit_for"] - dt)

        phase = s["phase"]
        run = scanner.running
        deck = phase in ("load", "discharge") if stop_deck else \
            phase in ("load", "settle", "hoist", "discharge")
        await bus.write_many({
            "emitter.emit": run and s["emit_for"] > 0.0,
            "infeed.rotate": run,
            "outfeed.rotate": run,
            "lift.transfer": run and deck,
            "lift.target": 1 if phase in ("hoist", "discharge") else 0,
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ml_good(bus, stop):
    """Stops the deck on `occupied`, discharges on `atlevel` at level 1,
    sends the carriage down once the outfeed eye has the carton."""
    await _ml_body(bus, stop, arrive="atlevel", stop_deck=True)


async def _ml_timed(bus, stop):
    """Discharges a fixed time after calling level 1 -- right until the hoist
    is slowed, and then with the carriage between floors."""
    await _ml_body(bus, stop, arrive="timed", stop_deck=True)


async def _ml_nostop(bus, stop):
    """Runs the deck to load and leaves it running: the carton rides straight
    across the carriage and off its far side before the lift has moved."""
    await _ml_body(bus, stop, arrive="atlevel", stop_deck=False)


REFERENCES = {"good": _ml_good, "timed": _ml_timed, "nostop": _ml_nostop,
              **contract_references(_ml_good)}
