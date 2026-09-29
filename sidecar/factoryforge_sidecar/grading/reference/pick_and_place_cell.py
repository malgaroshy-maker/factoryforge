"""`pick-and-place-cell`'s reference controllers: a `good` one that must pass,
and the deliberately wrong ones that must fail for this scene's own
reason.

`grading.registry` finds them by `SCENE` and `REFERENCES`. The two every
scene shares, `idle` and `forcer`, are in `_shared.py`.
"""

from __future__ import annotations

from ..lockstep import run_scan
from ..scenes.pick_and_place_cell import PP_PLACE_AT
from ._shared import Scanner, contract_references


SCENE = "pick-and-place-cell"


# --- pick and place cell references ---------------------------------------

async def _pp_body(bus, stop, *, on_feedback: bool) -> None:
    """Index, lower, grip, raise, traverse, release.

    `on_feedback` is the only difference. With it, every transition waits on
    `gantry.position`, `lowered`, `raised` or `holding`. Without it, each one
    waits a fixed number of seconds -- lengths measured off this cell at its
    original travel speed, which is what makes it look right until the run
    slows the axis down.
    """
    scanner = Scanner(bus)
    #: Percent of the rail counted as arrived. Wider than the machine's own
    #: in-position window so the two never disagree in a way that stalls it.
    ARRIVAL = 2.5
    #: What the timed version waits instead, measured at 80 %/s.
    WAITS = {"topick": 1.6, "lower": 0.6, "grip": 0.35, "raise": 0.6,
             "toplace": 1.6, "release": 0.4}
    state = {"step": "topick", "left": 0.0, "feed": 0.0, "emit": False,
             "codes": set(), "held": None,
             "last": {"gantry.lower": False, "gantry.grip": False}}

    def arrived(where: float) -> bool:
        """Position feedback against the destination *this step* wants.

        Deliberately not `gantry.inposition` alone. That bit compares the axis
        to the target the machine currently holds, and a target written this
        scan has not reached the machine yet -- so on the scan that issues a
        move it still reports "arrived", at the place you are trying to leave.
        """
        return abs(scanner.num("gantry.position") - where) <= ARRIVAL

    def done(step: str, condition: bool, dt: float) -> bool:
        if on_feedback:
            return condition
        state["left"] -= dt
        if state["left"] <= 0.0:
            state["left"] = 0.0
            return True
        return False

    def enter(step: str) -> None:
        state["step"] = step
        state["left"] = WAITS.get(step, 0.5)

    async def body(dt: float) -> None:
        scanner.scan()
        running = scanner.running
        at_station = scanner.bit("atstation.detect")

        if running:
            state["feed"] -= dt
            if state["feed"] <= 0.0 and not at_station \
                    and not scanner.bit("scanner.present"):
                state["emit"] = not state["emit"]
                state["feed"] = 1.8 if state["emit"] else 0.3
        else:
            state["emit"] = False

        if scanner.bit("scanner.read"):
            state["codes"].add(int(scanner.num("scanner.code")))

        writes = {
            "infeed.run": running,
            "infeed.speed": scanner.setpoint if running else 0.0,
            # Index the carton to a stop rather than coasting it onto a dead
            # plate: a repeatable pick needs the carton put under the cup on
            # purpose, not left wherever friction happened to stop it.
            "pickstation.rotate": running and not at_station,
            "scanner.enable": True,
            "rate.value": scanner.num("infeed.actual"),
            "emitter.emit": state["emit"],
            "gantry.target": 0.0,
            "gantry.lower": False,
            "gantry.grip": False,
            **scanner.lamps(),
        }
        # An E-stop holds the cell where it is until Reset and then Start
        # (IP-12): the axis held at the position it had, the column and the
        # cup left as they were -- a cup let go drops its carton -- and the
        # sequence resumed from the same step. A Stop still sends it home.
        if scanner.tripped and state["held"] is None:
            state["held"] = {"gantry.target": scanner.num("gantry.position"),
                             **state["last"]}
        if state["held"] is not None and not running:
            writes.update(state["held"])
            await bus.write_many(writes)
            return
        state["held"] = None
        if not running:
            state["step"] = "topick"
            await bus.write_many(writes)
            return

        lowered = scanner.bit("gantry.lowered")
        raised = scanner.bit("gantry.raised")
        holding = scanner.bit("gantry.holding")
        step = state["step"]

        if step == "topick":
            writes["gantry.target"] = 0.0
            if arrived(0.0) and raised and at_station:
                enter("lower")
        elif step == "lower":
            writes["gantry.lower"] = True
            if done(step, lowered, dt):
                enter("grip")
        elif step == "grip":
            writes["gantry.lower"] = True
            writes["gantry.grip"] = True
            if done(step, holding, dt):
                # On feedback, an empty cup sends the cycle back to waiting.
                # On timers there is nothing to notice with.
                enter("raise" if holding or not on_feedback else "topick")
                if on_feedback and not holding:
                    writes["gantry.grip"] = False
        elif step == "raise":
            writes["gantry.grip"] = True
            if done(step, raised, dt):
                enter("toplace")
        elif step == "toplace":
            writes["gantry.grip"] = True
            writes["gantry.target"] = PP_PLACE_AT
            if done(step, arrived(PP_PLACE_AT), dt):
                enter("release")
        elif step == "release":
            writes["gantry.target"] = PP_PLACE_AT
            if done(step, not holding, dt):
                enter("topick")

        state["last"] = {"gantry.lower": writes["gantry.lower"],
                         "gantry.grip": writes["gantry.grip"]}
        await bus.write_many(writes)

    await run_scan(bus, stop, body)


async def _pp_good(bus, stop):
    """Every transition waits on position, a reed or the vacuum."""
    await _pp_body(bus, stop, on_feedback=True)


async def _pp_timed(bus, stop):
    """The same sequence on a stopwatch, with waits measured off this cell at
    80 %/s. It works perfectly until the rail slows down, and then it lets go
    of the carton over the middle of it."""
    await _pp_body(bus, stop, on_feedback=False)


REFERENCES = {"good": _pp_good, "timed": _pp_timed,
              **contract_references(_pp_good)}
