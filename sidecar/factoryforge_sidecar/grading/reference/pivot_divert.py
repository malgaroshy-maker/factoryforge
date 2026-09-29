"""`pivot-divert`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..plant import CARTON_LENGTH
from ..scenes.pivot_divert import PD_BELT_SPEED, PD_EYE_AT, PD_GATE_AT, PD_RELEASE
from ._shared import Scanner, contract_references


SCENE = "pivot-divert"

#: How long the emit pulse stays high.
PD_EMIT = 0.2
#: What `timed` holds the blade for, from tall_eye's rising edge: the time a
#: tall carton took to slide off at the rated speed, and half a second over,
#: which is what a student measures on the day.
PD_TIMED_HOLD = (PD_RELEASE[True] - (PD_EYE_AT - CARTON_LENGTH / 2)) / PD_BELT_SPEED + 0.5
#: When `late` swings the blade out, from tall_eye's rising edge: the moment
#: the carton's centre reaches the post at the rated speed, which is when a
#: pusher would be fired.
PD_LATE_OUT = (PD_GATE_AT - (PD_EYE_AT - CARTON_LENGTH / 2)) / PD_BELT_SPEED


async def _pd_body(bus, stop, *, hold: str, out_after: float = 0.0,
                   every_other: bool = False) -> None:
    """Feed on the pot, turn every carton tall_eye sees.

    `hold` is how long the blade stays across: "counted" (until
    `tall_count.count` has taken every tall carton seen: right), "timed"
    (PD_TIMED_HOLD from the eye, whatever the belt is doing) or "eye"
    (`gate.divert` wired straight to `tall_eye.detect`). `out_after` delays
    the swing after the eye sees the carton (0: at once, right).
    `every_other` turns every second carton entry_eye sees instead, and never
    reads tall_eye at all.
    """
    scanner = Scanner(bus)
    state = {"feed": 0.0, "was_tall": False, "was_entry": False, "entered": 0,
             "seen": 0, "base": None, "pending": []}

    async def body(dt: float) -> None:
        scanner.scan()
        s = state
        counted = int(scanner.num("tall_count.count"))
        if s["base"] is None:
            s["base"] = counted
        tall = scanner.bit("tall_eye.detect")
        rising = tall and not s["was_tall"]
        s["was_tall"] = tall
        if every_other:
            entry = scanner.bit("entry_eye.detect")
            if entry and not s["was_entry"]:
                s["entered"] += 1
            rising = entry and not s["was_entry"] and s["entered"] % 2 == 0
            s["was_entry"] = entry

        # The feed: one carton every panel.setpoint seconds while running.
        emit = False
        if scanner.running:
            s["feed"] -= dt
            if s["feed"] <= 0.0:
                s["feed"] = max(scanner.setpoint, 1.0)
            emit = s["feed"] > max(scanner.setpoint, 1.0) - PD_EMIT
        else:
            s["feed"] = 0.0

        # Each tall carton the eye sees: when to swing out, when to let go.
        if rising:
            s["seen"] += 1
            s["pending"].append({"age": 0.0, "n": s["seen"]})
        for p in s["pending"]:
            p["age"] += dt
        if hold == "counted":
            s["pending"] = [p for p in s["pending"] if counted - s["base"] < p["n"]]
        elif hold == "timed":
            s["pending"] = [p for p in s["pending"] if p["age"] < PD_TIMED_HOLD]
        divert = (tall if hold == "eye"
                  else any(p["age"] >= out_after for p in s["pending"]))

        await bus.write_many({
            "belt.rotate": scanner.running,
            "emitter.emit": emit,
            "gate.divert": divert,
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _pd_good(bus, stop):
    """Swings the blade across the moment tall_eye sees a tall carton, holds
    it until the chute's count has taken it, then homes it."""
    await _pd_body(bus, stop, hold="counted")


async def _pd_timed(bus, stop):
    """Holds the blade for the time a tall carton took at the rated speed --
    right until the belt is slowed, and then it lets cartons go half way
    along the blade."""
    await _pd_body(bus, stop, hold="timed")


async def _pd_unlatched(bus, stop):
    """`gate.divert := tall_eye.detect`: the blade is across while the
    carton is in the beam, a metre upstream, and home again before it gets
    there."""
    await _pd_body(bus, stop, hold="eye")


async def _pd_late(bus, stop):
    """Swings the blade out as the carton reaches the post, the moment a
    pusher would be fired -- and hits it."""
    await _pd_body(bus, stop, hold="counted", out_after=PD_LATE_OUT)


async def _pd_everyother(bus, stop):
    """Turns every second carton and never reads tall_eye: perfect against
    the engine's own emitter, which alternates short and tall, and wrong
    against any feed that does not."""
    await _pd_body(bus, stop, hold="counted", every_other=True)


REFERENCES = {"good": _pd_good, "timed": _pd_timed, "unlatched": _pd_unlatched,
              "late": _pd_late, "everyother": _pd_everyother,
              **contract_references(_pd_good)}
