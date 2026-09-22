"""`sorting-by-height`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

import asyncio

from ..lockstep import run_scan
from ..scenes.sorting_by_height import _beam_to_pusher_window
from ._shared import EMIT_GAP, EMIT_PULSE, Feed


SCENE = "sorting-by-height"


#: How long a sorting reference holds the plate out once it fires.
STROKE_HOLD = 0.5


async def _good(bus, stop: asyncio.Event) -> None:
    """What the exercise is asking for."""
    low, high = _beam_to_pusher_window()
    delay = (low + high) / 2
    feed = Feed()
    # `strokes` holds the plant time each pending stroke starts, not a flag:
    # the next carton can break the beam before the last one's stroke is over.
    state = {"now": 0.0, "high": False, "strokes": []}

    async def body(dt: float) -> None:
        state["now"] += dt
        now = state["now"]
        high = bool(bus.read("sensor_high.detect"))
        if high and not state["high"]:
            state["strokes"].append(now + delay)
        state["high"] = high
        state["strokes"] = [at for at in state["strokes"] if now < at + STROKE_HOLD]
        await bus.write_many({
            "conveyor.rotate": True,
            "emitter.emit": feed(dt),
            "pusher.extend": any(at <= now for at in state["strokes"]),
        })

    await run_scan(bus, stop, body)


async def _blind(bus, stop: asyncio.Event) -> None:
    """Pushes on a timer and never reads a sensor. Passes a line that
    alternates; fails this one, which is why the feed pattern is shuffled."""
    #: One stroke a cycle, one second after the cycle's feed pause ends.
    CYCLE = EMIT_PULSE + EMIT_GAP + 1.0 + STROKE_HOLD
    feed = Feed()
    state = {"cycle": 0.0}

    async def body(dt: float) -> None:
        state["cycle"] += dt
        if state["cycle"] >= CYCLE:
            state["cycle"] -= CYCLE
        await bus.write_many({
            "conveyor.rotate": True,
            "emitter.emit": feed(dt),
            "pusher.extend": state["cycle"] >= CYCLE - STROKE_HOLD,
        })

    await run_scan(bus, stop, body)


async def _greedy(bus, stop: asyncio.Event) -> None:
    """Holds the plate out, so everything goes down the chute."""
    feed = Feed()

    async def body(dt: float) -> None:
        await bus.write_many({"conveyor.rotate": True, "pusher.extend": True,
                              "emitter.emit": feed(dt)})

    await run_scan(bus, stop, body)


REFERENCES = {"good": _good, "blind": _blind, "greedy": _greedy}
