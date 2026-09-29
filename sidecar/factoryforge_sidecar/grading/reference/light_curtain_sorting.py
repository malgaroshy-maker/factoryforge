"""`light-curtain-sorting`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.light_curtain_sorting import (LC_BELT_SPEED, LC_CATCH, LC_CURTAIN_POS,
                                            LC_DIVERTER_POS, LC_TRAVEL_TIME,
                                            lc_beam_ladder)
from ._shared import Scanner, contract_references


SCENE = "light-curtain-sorting"


# --- light curtain sorting references ------------------------------------

def _lc_window() -> tuple[float, float]:
    """When the diverter must be *commanded*, measured from the beam breaking.

    Computed from the scene's own geometry rather than written down, the same
    argument `_beam_to_pusher_window` makes for the sorting line: a change to
    the belt speed changes the advice instead of quietly making it wrong.
    """
    beam_break = LC_CURTAIN_POS - 0.10
    first = (LC_DIVERTER_POS - LC_CATCH - beam_break) / LC_BELT_SPEED - LC_TRAVEL_TIME
    last = (LC_DIVERTER_POS + LC_CATCH - beam_break) / LC_BELT_SPEED - LC_TRAVEL_TIME
    return first, last


async def _lc_body(bus, stop, *, fixed: float | None, every_other: bool) -> None:
    scanner = Scanner(bus)
    low, high = _lc_window()
    delay = (low + high) / 2
    # A queue and not a single slot. The command has to be issued about 1.9 s
    # after the beam breaks and cartons arrive every 2.2 s, so there is very
    # nearly always one stroke pending when the next carton is measured -- the
    # first version of this kept one and let every overlapping pair lose a
    # carton, which read from the outside exactly like a controller that had
    # misjudged the height.
    state = {"blocked": False, "feed": 0.0, "emit": False, "seen": 0,
             "pending": [], "drop_at": None, "now": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        state["now"] += dt           # the scan clock; see `run_scan`
        now = state["now"]

        if scanner.running:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 2.0 if state["emit"] else 0.2
        else:
            state["emit"] = False

        blocked = scanner.bit("height_gauge.blocked")
        if blocked and not state["blocked"] and scanner.running:
            state["seen"] += 1
            if every_other:
                divert = state["seen"] % 2 == 0
            else:
                threshold = fixed if fixed is not None else scanner.setpoint
                divert = scanner.num("height_gauge.height") >= threshold
            if divert:
                state["pending"].append(now + delay)
        state["blocked"] = blocked

        while state["pending"] and now >= state["pending"][0]:
            state["pending"].pop(0)
            state["drop_at"] = now + 0.5
        extend = state["drop_at"] is not None and now < state["drop_at"]
        if state["drop_at"] is not None and now >= state["drop_at"]:
            state["drop_at"] = None
        if not scanner.running:
            extend = False
            state["pending"].clear()
            state["drop_at"] = None

        await bus.write_many({"belt.rotate": scanner.running,
                              "emitter.emit": state["emit"],
                              "diverter.extend": extend,
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _lc_good(bus, stop):
    """Measures each carton and compares it against the pot, read at the moment
    of the measurement."""
    await _lc_body(bus, stop, fixed=None, every_other=False)


async def _lc_fixed(bus, stop):
    """The threshold written into the program. Sorts perfectly until somebody
    turns the knob, which is the difference between this scene and the one next
    door where the rule is two bits of wiring."""
    await _lc_body(bus, stop, fixed=lc_beam_ladder()[4], every_other=False)


async def _lc_everyother(bus, stop):
    """Diverts every second carton and never reads the height at all. It would
    pass an alternating feed, which is why the feed is eight shuffled heights."""
    await _lc_body(bus, stop, fixed=None, every_other=True)


REFERENCES = {"good": _lc_good, "fixed": _lc_fixed,
              "everyother": _lc_everyother, **contract_references(_lc_good)}
