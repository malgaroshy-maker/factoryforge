"""`sorting-by-height`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.

Since IP-35 the exam presses Start and strikes the mushroom, so every one of
these reads the panel through `_shared.Scanner`. `blind` and `greedy` answer
the panel correctly and are wrong only about sorting; `nostart` and
`startalone` sort correctly and are wrong only about the panel.
"""

from __future__ import annotations

import asyncio

from ..lockstep import run_scan
from ..scenes.sorting_by_height import _beam_to_pusher_window
from ._shared import EMIT_GAP, EMIT_PULSE, Feed, Scanner


SCENE = "sorting-by-height"


#: How long a sorting reference holds the plate out once it fires.
STROKE_HOLD = 0.5


async def _sorter(bus, stop: asyncio.Event, scanner: Scanner | None) -> None:
    """The sorting logic every right answer here shares, with the panel
    answered by `scanner` -- or, with None, not read at all: the belt runs
    whenever the mushroom is out, which is the program the first-hour guide
    taught before IP-35."""
    low, high = _beam_to_pusher_window()
    delay = (low + high) / 2
    feed = Feed()
    # `strokes` holds the plant time each pending stroke starts, not a flag:
    # the next carton can break the beam before the last one's stroke is over.
    state = {"now": 0.0, "high": False, "strokes": []}

    async def body(dt: float) -> None:
        if scanner is not None:
            scanner.scan()
            running = scanner.running
            lamps = scanner.lamps()
        else:
            running = bool(bus.read("panel.estop"))
            lamps = {}
        state["now"] += dt
        now = state["now"]
        high = bool(bus.read("sensor_high.detect"))
        if high and not state["high"]:
            state["strokes"].append(now + delay)
        state["high"] = high
        state["strokes"] = [at for at in state["strokes"] if now < at + STROKE_HOLD]
        await bus.write_many({
            "conveyor.rotate": running,
            # Fed only while the belt runs: a carton made on a stopped belt
            # lands on the one before it.
            "emitter.emit": feed(dt) if running else feed.hold(),
            "pusher.extend": any(at <= now for at in state["strokes"]),
            **lamps,
        })

    await run_scan(bus, stop, body)


async def _good(bus, stop: asyncio.Event) -> None:
    """What the exercise is asking for: Start runs the line, the mushroom
    stops it and latches, and only Reset then Start brings it back."""
    await _sorter(bus, stop, Scanner(bus))


async def _nostart(bus, stop: asyncio.Event) -> None:
    """Sorts correctly and never reads Start: the belt runs whenever the
    mushroom is out, from power-up. It stops on the strike, and starts again
    by itself the moment the mushroom is released."""
    await _sorter(bus, stop, None)


async def _startalone(bus, stop: asyncio.Event) -> None:
    """Sorts correctly and latches the trip, but lets Start clear it: the
    Start the examiner presses with the mushroom out and no Reset restarts
    the line."""
    await _sorter(bus, stop, Scanner(bus, start_clears_trip=True))


async def _blind(bus, stop: asyncio.Event) -> None:
    """Pushes on a timer and never reads a sensor. Passes a line that
    alternates; fails this one, which is why the feed pattern is shuffled."""
    #: One stroke a cycle, one second after the cycle's feed pause ends.
    CYCLE = EMIT_PULSE + EMIT_GAP + 1.0 + STROKE_HOLD
    feed = Feed()
    scanner = Scanner(bus)
    state = {"cycle": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        state["cycle"] += dt
        if state["cycle"] >= CYCLE:
            state["cycle"] -= CYCLE
        await bus.write_many({
            "conveyor.rotate": scanner.running,
            "emitter.emit": feed(dt) if scanner.running else feed.hold(),
            "pusher.extend": state["cycle"] >= CYCLE - STROKE_HOLD,
            **scanner.lamps(),
        })

    await run_scan(bus, stop, body)


async def _greedy(bus, stop: asyncio.Event) -> None:
    """Holds the plate out, so everything goes down the chute."""
    feed = Feed()
    scanner = Scanner(bus)

    async def body(dt: float) -> None:
        scanner.scan()
        await bus.write_many({"conveyor.rotate": scanner.running,
                              "pusher.extend": True,
                              "emitter.emit": feed(dt) if scanner.running else feed.hold(),
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


REFERENCES = {"good": _good, "blind": _blind, "greedy": _greedy,
              "nostart": _nostart, "startalone": _startalone}
