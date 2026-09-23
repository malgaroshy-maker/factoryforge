"""`accumulation-buffer`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..plant import CARTON_LENGTH
from ..scenes.accumulation_buffer import (AB_BLADE_POS, AB_BLADE_THICKNESS, AB_PITCH,
                                          AB_START_POS)
from ._shared import Scanner


SCENE = "accumulation-buffer"

#: How many cartons fit between the blade and the emitter, the first nose to
#: the blade and the last one clear of where the emitter puts the next. Ten,
#: as the template lays the line out. Feeding past it backs the queue up over
#: the emitter and off the infeed end of the belt, which the plant records --
#: and which these references did, unseen, until IP-29 gave the model an end
#: to the belt: at a carton a second the queue reached x = -5 m.
AB_BUFFER_FULL = int((AB_BLADE_POS - AB_BLADE_THICKNESS / 2 - CARTON_LENGTH / 2
                      - (AB_START_POS + CARTON_LENGTH)) // AB_PITCH) + 1


# --- accumulation buffer references ---------------------------------------

async def _ab_body(bus, stop, *, by_pulses: bool) -> None:
    """Accumulate, then release. The only difference between the two is what
    ends the release: a distance the encoder measures, or a clock."""
    scanner = Scanner(bus)
    #: Seconds the timed release holds the blade down. Sized for the drive's
    #: first top speed, which is exactly the mistake: it is right until the
    #: line runs at another.
    TIMED_HOLD = 2.4
    HOLD_FOR = 9.0
    #: `fed` counts the cartons this program has made. Fed less
    #: `released.count` is how many are on the line -- the queue, by count,
    #: which is how a buffer with no eye at its tail knows it is full. Not the
    #: exit eye: cartons leave a queue nose to tail, and a beam across a train
    #: of touching cartons never sees the gap between them.
    state = {"phase": "accumulate", "until": 0.0, "pulses_at": 0.0,
             "feed": 0.0, "emit": False, "now": 0.0, "fed": 0}

    async def body(dt: float) -> None:
        scanner.scan()
        state["now"] += dt           # the scan clock; see `run_scan`
        now = state["now"]
        pulses = scanner.num("enc.count")

        on_the_line = state["fed"] - int(scanner.num("released.count"))

        if scanner.running:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                if state["emit"]:
                    state["emit"] = False
                    state["feed"] = 0.2
                elif on_the_line < AB_BUFFER_FULL:
                    state["emit"] = True
                    state["fed"] += 1
                    state["feed"] = 0.8
        else:
            state["emit"] = False
            state["phase"] = "accumulate"
            state["until"] = now + HOLD_FOR

        if scanner.running:
            if state["phase"] == "accumulate":
                if state["until"] <= 0.0:
                    state["until"] = now + HOLD_FOR
                if now >= state["until"]:
                    state["phase"] = "release"
                    state["pulses_at"] = pulses
                    state["until"] = now + TIMED_HOLD
            elif state["phase"] == "release":
                done = (pulses - state["pulses_at"] >= scanner.setpoint
                        if by_pulses else now >= state["until"])
                if done:
                    state["phase"] = "accumulate"
                    state["until"] = now + HOLD_FOR

        # A stopped line holds what it has: dropping the blade with the belt
        # off would spill the whole buffer the moment it restarted.
        raise_blade = state["phase"] != "release" or not scanner.running
        await bus.write_many({
            "buffer.run": scanner.running,
            "buffer.speed": 100.0 if scanner.running else 0.0,
            "outfeed.rotate": scanner.running,
            "emitter.emit": state["emit"],
            "stop.raise": raise_blade,
            "enc.reset": False,
            "count_display.value": int(scanner.num("released.count")),
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ab_good(bus, stop):
    """Releases for the pot's window of encoder pulses, which is a distance."""
    await _ab_body(bus, stop, by_pulses=True)


async def _ab_timed(bus, stop):
    """Releases for a fixed 2.4 seconds, sized for the speed the line was
    running at when it was written. It lets out the right amount until the
    drive's top speed changes, and then half as much."""
    await _ab_body(bus, stop, by_pulses=False)


REFERENCES = {"good": _ab_good, "timed": _ab_timed}
