"""`roller-line-weighing`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ._shared import Scanner


SCENE = "roller-line-weighing"


# --- roller line with weighing references ---------------------------------

async def _rw_body(bus, stop, *, on_metal: bool, feed_gap: float) -> None:
    scanner = Scanner(bus)
    # `metal` holds the times the inductive eye fired, not a bit. Two reasons.
    # The eye is upstream of the deck, so by the time a carton is weighed the
    # eye has long since let go of it -- a program reading the eye at the
    # moment of the verdict is reading the next carton. And the eye cannot be
    # counted against, because cardboard passes it as if the lane were empty,
    # which is the whole nature of an inductive sensor: there is no pulse per
    # carton to queue, only a pulse per *steel* carton, so the only way to
    # attach one to a carton is transit time. This is what makes `metalonly`
    # a controller that is right at one limit rather than one that is broken,
    # and being right at one limit is the failure the scene demonstrates.
    state = {"feed": 0.0, "emit": False, "peak": 0.0, "loaded": False,
             "reject": False, "clear_at": None, "metal": [], "eye": False,
             "this_is_metal": False, "now": 0.0}

    async def body(dt: float) -> None:
        scanner.scan()
        state["now"] += dt           # the scan clock; see `run_scan`
        now = state["now"]
        weight = scanner.num("scale.weight")

        # Feed into space. `feed_gap` is the whole difference between the two
        # feeding behaviours: one holds while the deck is loaded, the other
        # does not look.
        gate = weight < 20.0 or feed_gap < 1.0
        if scanner.running and gate:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = feed_gap if state["emit"] else 0.2
        else:
            state["emit"] = False

        eye = scanner.bit("metal_check.detect")
        if eye and not state["eye"]:
            state["metal"].append(now)
        state["eye"] = eye
        state["metal"] = [t for t in state["metal"] if now - t < 8.0]

        if weight > 20.0:
            if not state["loaded"]:
                state["peak"] = 0.0
                # The eye sits 0.8 m before the deck on rollers running at
                # 0.4 m/s, so a pulse two seconds ago belongs to the carton
                # arriving now. A window either side, because the infeed is not
                # a metronome.
                state["this_is_metal"] = any(1.4 <= now - t <= 2.8
                                             for t in state["metal"])
            state["loaded"] = True
            state["peak"] = max(state["peak"], weight)
        elif state["loaded"]:
            state["loaded"] = False
            state["reject"] = (state["this_is_metal"] if on_metal
                               else state["peak"] > scanner.setpoint)
            state["clear_at"] = now + 1.2

        if state["clear_at"] is not None and now >= state["clear_at"]:
            state["reject"] = False
            state["clear_at"] = None

        await bus.write_many({"infeed.rotate": scanner.running,
                              "scale.rotate": scanner.running,
                              "emitter.emit": state["emit"],
                              "weight_readout.value": int(round(weight)),
                              "panel.green": scanner.running,
                              "panel.red": state["reject"]})

    await run_scan(bus, stop, body)


async def _rw_good(bus, stop):
    """Holds the feed while the deck is loaded and judges on the peak."""
    # 3.2 s between cartons, against a 2.5 s dwell on a 1 m deck at 0.4 m/s.
    # The gate on `scale.weight` alone cannot do this: it holds the *emitter*,
    # and an emitted carton is five seconds of infeed away from the deck, so
    # the gate is answering a question about where the line was rather than
    # where it will be. Spacing is the control here, which is what the scene's
    # own brief says.
    await _rw_body(bus, stop, on_metal=False, feed_gap=3.2)


async def _rw_metalonly(bus, stop):
    """Rejects on the inductive sensor. On this line the steel cartons are also
    the heavy ones -- until the limit drops below a tall cardboard one."""
    await _rw_body(bus, stop, on_metal=True, feed_gap=3.2)


async def _rw_fastfeed(bus, stop):
    """Judges on the peak, correctly, and never holds the feed. Two cartons on
    the deck read as one peak and both verdicts are guesses."""
    await _rw_body(bus, stop, on_metal=False, feed_gap=0.9)


REFERENCES = {"good": _rw_good, "metalonly": _rw_metalonly,
              "fastfeed": _rw_fastfeed}
