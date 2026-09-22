"""`accumulation-buffer`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "accumulation-buffer"


# --- accumulation buffer references ---------------------------------------

async def _ab_body(bus, stop, *, by_pulses: bool) -> None:
    """Accumulate, then release. The only difference between the two is what
    ends the release: a distance the encoder measures, or a clock."""
    scanner = Scanner(bus)
    #: Seconds the timed release holds the blade down. Sized for the drive's
    #: first top speed, which is exactly the mistake: it is right until the
    #: line runs faster.
    TIMED_HOLD = 2.4
    HOLD_FOR = 9.0
    state = {"phase": "accumulate", "until": 0.0, "pulses_at": 0.0,
             "feed": 0.0, "emit": False, "now": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        state["now"] += dt           # the scan clock; see `run_scan`
        now = state["now"]
        pulses = scanner.num("enc.count")

        if scanner.running:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 0.8 if state["emit"] else 0.2
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
    drive's top speed changes, and then twice as much."""
    await _ab_body(bus, stop, by_pulses=False)


REFERENCES = {"good": _ab_good, "timed": _ab_timed}
