"""`rotary-index`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.rotary_index import RI_INDEX, RI_SPEED_FIRST
from ._shared import Scanner


SCENE = "rotary-index"

#: What `timed` allows a quarter turn: the rated time and a little over,
#: which is exactly what a student measures on the day.
RI_TIMED = RI_INDEX / RI_SPEED_FIRST + 0.2
#: How long the emit pulse stays high.
RI_EMIT = 0.3


async def _ri_body(bus, stop, *, arrive: str, back: str) -> None:
    """One index cycle, two ways to hurry it.

    `arrive` is how the program knows the deck has turned: "atindex" (the
    limit switch: right) or "timed". `back` is when it turns the deck home:
    "retracted" (the reed: right) or "notextended" (as soon as the extended
    reed drops, with the rod still out over the deck).
    """
    scanner = Scanner(bus)
    state = {"phase": "home", "t": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        s = state
        s["t"] += dt
        athome = scanner.bit("table.athome")
        atindex = scanner.bit("table.atindex")
        extended = scanner.bit("pusher.extended")
        retracted = scanner.bit("pusher.retracted")
        present = scanner.bit("deck_eye.detect")

        def go(phase: str) -> None:
            s["phase"], s["t"] = phase, 0.0

        phase = s["phase"]
        if not scanner.running:
            if phase in ("home", "drop", "settle"):
                go("home")
        elif phase == "home" and athome and retracted and not present:
            go("drop")
        elif phase == "drop" and s["t"] >= RI_EMIT:
            go("settle")
        elif phase == "settle" and present and s["t"] >= scanner.setpoint:
            go("index")
        elif phase == "index" and (atindex if arrive == "atindex" else s["t"] >= RI_TIMED):
            go("push")
        elif phase == "push" and extended:
            go("return")
        elif phase == "return" and (retracted if back == "retracted" else not extended):
            go("unindex")
        elif phase == "unindex" and athome:
            go("home")

        phase = s["phase"]
        await bus.write_many({
            "emitter.emit": phase == "drop",
            "table.index": phase in ("index", "push", "return"),
            "pusher.extend": phase == "push",
            "pusher.retract": phase != "push",
            "outfeed.rotate": scanner.running or phase not in ("home",),
            "tower.green": scanner.running,
            "tower.yellow": not scanner.running,
            "tower.red": scanner.tripped,
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ri_good(bus, stop):
    """Pushes on `atindex`, turns home on `retracted`, drops the next carton
    only with the deck home and clear."""
    await _ri_body(bus, stop, arrive="atindex", back="retracted")


async def _ri_timed(bus, stop):
    """Pushes a fixed time after commanding the index -- right until the deck
    is slowed, and then half way round."""
    await _ri_body(bus, stop, arrive="timed", back="retracted")


async def _ri_notretracted(bus, stop):
    """Turns the deck home as soon as the extended reed drops, with the rod
    still out over the deck: `not extended` taken for `retracted`."""
    await _ri_body(bus, stop, arrive="atindex", back="notextended")


REFERENCES = {"good": _ri_good, "timed": _ri_timed, "notretracted": _ri_notretracted}
