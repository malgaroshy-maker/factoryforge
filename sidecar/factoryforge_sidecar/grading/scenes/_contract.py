"""The operator contract's shared half: the four checks, the feedback and the
summary line that every scene marking it through `plant.OperatorExam` uses
(IP-12). A helper and not a scene: `grading.registry` skips every module here
whose name starts with an underscore.

The sorting line and the start / stop station mark the same contract with
code of their own, written before this existed (IP-35), in metres of belt.
Everything here is in seconds of the plant being driven, so one set of words
fits a belt, a pump, a heater and an axis alike.
"""

from __future__ import annotations

from ..core import Report
from ..plant import ESTOP_LIMIT, OperatorExam

#: A float's worth of slack, so a stop that lands exactly on the limit is not
#: failed by the rounding of the tick sum it was measured with.
_SLACK = 1e-6


def _restarted_on(trip: dict, sheet: dict) -> str:
    """Which of the examiner's moves the plant came back on, from when it
    was first driven while the trip was still latched."""
    moved = trip.get("latched_moved_at")
    if moved is None:
        return "it restarted while the trip was latched"
    if sheet["reset_at"] is not None and moved >= sheet["reset_at"]:
        return f"it restarted at {moved:g}s on Reset alone"
    if sheet["start_alone_at"] is not None and moved >= sheet["start_alone_at"]:
        return f"it restarted at {moved:g}s on Start alone, with no Reset"
    return f"it restarted at {moved:g}s, when the mushroom was released"


def grade_contract(operator: OperatorExam, report: Report, window: float) -> dict:
    """`line.started_by_start`, `estop.stopped_the_<noun>`,
    `estop.latched_until_reset` and `estop.restarted_after_reset`, read off
    the plant's own ledger. Returns what the feedback needs."""
    ledger, sheet = operator.ledger, operator.sheet
    trip = operator.trip
    what = operator.what
    finished = operator.finished(window)
    short = (f"the {window:g}s window ended before the examiner finished the "
             f"E-stop test, which needs about {operator.ends_by:g}s")
    unstarted = list(ledger.began_unstarted)

    report.evidence["operator_contract"] = {
        "what_stops": what,
        "presses": operator.panel.presses,
        "sheet": dict(sheet),
        "trip": None if trip is None else {
            "struck_at": trip["struck_at"],
            "running_at_strike": trip["moving_at_strike"],
            "last_driven_after_strike_s": trip["last_moved_after_s"],
            "driven_while_struck_s": round(trip["struck_travel_m"], 3),
            "released_at": trip["released_at"], "reset_at": trip["reset_at"],
            "cleared_at": trip["cleared_at"],
            "driven_while_latched_s": round(trip["latched_travel_m"], 3),
            "latched_driven_at": trip["latched_moved_at"],
            "restarted_at": trip["restarted_at"],
        },
        "allowed_s": ESTOP_LIMIT,
        "restart_within_s": operator.restart_within,
        "began_with_nobody_starting_it_at": unstarted[:10],
    }

    report.add("line.started_by_start",
               not unstarted,
               f"{what} never started without somebody pressing Start"
               if not unstarted else
               f"{what} started {len(unstarted)} time(s) with nobody having "
               f"pressed Start, at {unstarted[:5]}s")

    stopped = f"estop.stopped_the_{operator.noun}"
    if trip is None:
        report.add(stopped, False,
                   short if sheet["reached_for_the_mushroom_at"] is None
                   else "the mushroom was never struck")
    elif not trip["moving_at_strike"]:
        report.add(stopped, False,
                   f"{what} had already stopped when the mushroom was struck at "
                   f"{trip['struck_at']:g}s, so the strike stopped nothing")
    else:
        late = trip["last_moved_after_s"] or 0.0
        report.add(stopped,
                   late <= ESTOP_LIMIT + _SLACK,
                   f"{what} stopped {late:.2f}s after the mushroom was struck at "
                   f"{trip['struck_at']:g}s (within {ESTOP_LIMIT:g}s)"
                   if late <= ESTOP_LIMIT + _SLACK else
                   f"{what} kept running {late:.2f}s after the mushroom was "
                   f"struck at {trip['struck_at']:g}s (at most {ESTOP_LIMIT:g}s)")

    if not finished or trip is None:
        report.add("estop.latched_until_reset", False, short)
        report.add("estop.restarted_after_reset", False, short)
        return {"trip": trip, "finished": False, "unstarted": unstarted}

    latched = trip["latched_travel_m"]
    report.add("estop.latched_until_reset",
               latched <= _SLACK,
               f"{what} stayed stopped from the release until Reset and then Start"
               if latched <= _SLACK else
               f"{what} ran for {latched:.2f}s between the mushroom's release at "
               f"{trip['released_at']:g}s and Reset-then-Start -- "
               f"{_restarted_on(trip, sheet)}")
    back, cleared = trip["restarted_at"], trip["cleared_at"]
    if latched > _SLACK:
        # It was running again before Reset-then-Start came, which the latch
        # check has already failed. A plant that runs in cycles may be between
        # two of them when that Start comes, and failing this too would make
        # one mistake look like two.
        report.add("estop.restarted_after_reset", True,
                   f"{what} was already running again before Reset and Start, "
                   f"from {trip['latched_moved_at']:g}s -- marked by "
                   f"estop.latched_until_reset, not here")
    else:
        report.add("estop.restarted_after_reset",
                   cleared is not None and back is not None
                   and back - cleared <= operator.restart_within + _SLACK,
                   f"{what} ran again {back - cleared:.2f}s after Start at "
                   f"{cleared:g}s (within {operator.restart_within:g}s)"
                   if back is not None and cleared is not None
                   and back - cleared <= operator.restart_within + _SLACK else
                   f"{what} ran again only {back - cleared:.2f}s after Start at "
                   f"{cleared:g}s (within {operator.restart_within:g}s)"
                   if back is not None and cleared is not None else
                   f"{what} did not run again after Reset at {sheet['reset_at']:g}s "
                   f"and Start at {sheet['restart_at']:g}s")
    return {"trip": trip, "finished": True, "unstarted": unstarted}


def contract_feedback(operator: OperatorExam, report: Report, result: dict) -> None:
    """The operator contract, in terms of what the operator did."""
    say = report.feedback.append
    what, trip = operator.what, result["trip"]
    unstarted = result["unstarted"]
    if unstarted:
        say(f"{what.capitalize()} started at {unstarted[0]:g}s with nobody having "
            f"pressed Start. The plant waits for its operator: latch the RISING "
            f"edge of `panel.start` into a run flag, drop it on Stop, and drive "
            f"the outputs from the flag.")
    if trip is None:
        return
    late = trip["last_moved_after_s"] or 0.0
    if trip["moving_at_strike"] and late > ESTOP_LIMIT + _SLACK:
        say(f"{what.capitalize()} kept running {late:.2f}s after the mushroom was "
            f"struck. `panel.estop` is NORMALLY CLOSED: true means healthy, so a "
            f"struck mushroom reads FALSE, and {what} must stop on it within "
            f"{ESTOP_LIMIT * 1000:.0f} ms.")
    if result["finished"] and trip["latched_travel_m"] > _SLACK:
        say(f"The trip did not latch: {_restarted_on(trip, operator.sheet)}. "
            f"Releasing the mushroom must not restart anything, and neither may "
            f"Start or Reset alone -- only Reset and THEN Start.")
    if (result["finished"] and trip["latched_travel_m"] <= _SLACK
            and trip["restarted_at"] is None):
        say(f"After Reset and then Start {what} stayed stopped. The latch has to "
            f"clear on Reset, and the next Start has to run the plant again.")


def mark_contract(operator: OperatorExam, report: Report, window: float) -> None:
    """The checks and their feedback, in one call from a scene's rubric."""
    contract_feedback(operator, report, grade_contract(operator, report, window))


def summary_contract(evidence: dict, out) -> None:
    contract = evidence.get("operator_contract")
    if not contract or contract["trip"] is None:
        return
    trip = contract["trip"]
    back = ("never" if trip["restarted_at"] is None or trip["cleared_at"] is None
            else f"{trip['restarted_at'] - trip['cleared_at']:.2f}s after it")
    out(f"mushroom at {trip['struck_at']:g}s: {contract['what_stops']} last ran "
        f"{trip['last_driven_after_strike_s'] or 0.0:.2f}s after it (at most "
        f"{contract['allowed_s']:g}s), {trip['driven_while_latched_s']:.2f}s while "
        f"latched, back on Reset-then-Start {back}")
