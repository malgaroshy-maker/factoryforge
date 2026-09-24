"""`sorting-by-height`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/sorting_by_height.py`.
"""

from __future__ import annotations

import random

from factoryforge_sidecar import sorting_scene as scene_model

from ..core import GradedEngine, Report, Watched
from ..plant import ESTOP_LIMIT, Panel, Script, TripLedger, fault_input


SCENE = "sorting-by-height"
#: The scene `--scene` means when it is left out: the line this project began
#: with, and the one docs/GRADING.md's first example grades.
DEFAULT = True


# --- the sorting-by-height rubric --------------------------------------
#
# Numbers come from sorting_scene.py rather than from this file, so a change to
# the line's geometry changes the advice a student is given instead of quietly
# making it wrong.

#: Cartons that must reach a lane before a run counts as a run at all. A check
#: that passes while the line does nothing is not a check (AGENTS.md gotcha 16).
MIN_SORTED = 8
#: ...and of those, how many in each lane, so "no tall carton went the wrong
#: way" cannot be satisfied by never making a tall carton.
MIN_PER_LANE = 3
#: Emitted cartons per repeat of the feed pattern. Pairs, shuffled within each
#: pair: any eight consecutive cartons hold at least three of each height, so
#: the lane minimums are reachable, while the order stays unguessable.
PATTERN_PAIRS = 16


def _beam_to_pusher_window() -> tuple[float, float]:
    """When the pusher must be *commanded*, measured from the high beam
    breaking, for the plate to be out while the carton is in front of it."""
    beam_break = scene_model.SENSOR_HIGH_POS - scene_model.SENSOR_WINDOW / 2
    first_catch = scene_model.PUSHER_POS - scene_model.PUSHER_CATCH
    last_catch = scene_model.PUSHER_POS + scene_model.PUSHER_CATCH
    speed = scene_model.BELT_SPEED
    travel = scene_model.PUSHER_TRAVEL_TIME
    return ((first_catch - beam_break) / speed - travel,
            (last_catch - beam_break) / speed - travel)


def feed_pattern(seed: int) -> list[bool]:
    """The heights the emitter will produce, in order.

    Shuffled, and that is the point. A fixed alternation can be sorted by a
    program that pushes every second carton and never reads a sensor -- it
    would pass, and it would fail on any real line. Seeded so a disputed mark
    can be reproduced exactly: the seed is in the report.
    """
    rng = random.Random(seed)
    pattern: list[bool] = []
    for _ in range(PATTERN_PAIRS):
        pair = [True, False]
        rng.shuffle(pair)
        pattern.extend(pair)
    return pattern


#: The operator panel's pot on the engine's sorting line, which is built in C#
#: rather than from a template (`SceneEditor.DefaultScene.cs:130`,
#: `ConfigureSetpoint(0.30f, 1.80f, "s", 0.90f)`). Nothing in this exam turns
#: it; it is here so `panel.setpoint` reads what the engine's does.
SORTING_PANEL_SETPOINT = 0.90


# --- the operator contract (IP-35) --------------------------------------
#
# The brief says "the mushroom stops the line inside 200 ms and Start alone
# will not restart it". Until IP-35 this exam never pressed Start at all, so a
# program written to the brief -- one that waits for Start -- never ran its belt
# here, and the first-hour guide taught a program that ignored Start to fit the
# grader. Now the examiner runs the same sheet the start / stop station does:
# Start to begin, the mushroom mid-run, released, Start alone (must do
# nothing), Reset, then Start (must restart). It is measured as belt travel in
# `plant.TripLedger`, the ledger that station uses too.
#
# One thing is particular to this line. The pusher's timing is a delay after
# the high beam, and a correct program times that delay on a clock, not on
# belt travel -- the brief asks for nothing else. A strike that caught a tall
# carton between the beam and the plate would stop it there while the clock
# ran on, and the carton would be missorted by the E-stop rather than by the
# program. So the examiner waits, as an operator testing a line would, for a
# moment when no tall carton is committed to the plate: none between the high
# beam and the far edge of the pusher's catch. On a feed of one carton every
# 1.8 s that comes round within a second or two; if it never does, the
# mushroom is struck anyway after SORT_STRIKE_WAIT.

#: Start, as on eight of the other scenes.
SORT_START_AT = 1.0
#: The examiner reaches for the mushroom from here...
SORT_STRIKE_FROM = 16.0
#: ...and strikes it when the belt is running and no tall carton is committed
#: to the plate, or after this long regardless.
SORT_STRIKE_WAIT = 4.0
#: The rest of the sheet, timed from the strike.
SORT_RELEASE_AFTER = 2.0
SORT_START_ALONE_AFTER = 3.0      # must not restart: the trip is latched
SORT_RESET_AFTER = 4.5            # must not restart either: Reset starts nothing
SORT_RESTART_AFTER = 6.0          # this Start must
#: How soon after that last Start the belt has to be moving again.
SORT_RESTART_WITHIN = 1.0
#: Belt the strike may still cost: `plant.ESTOP_LIMIT` of it.
SORT_ESTOP_ALLOWED = scene_model.BELT_SPEED * ESTOP_LIMIT
#: The shortest window the whole sheet fits in, strike wait included.
SORT_EXAM_ENDS_BY = (SORT_STRIKE_FROM + SORT_STRIKE_WAIT + SORT_RESTART_AFTER
                     + SORT_RESTART_WITHIN)


class SortingExam(scene_model.SortingScene):
    """The sorting line, with the tags the engine's line has and
    `sorting_scene.py` does not, and the examiner at its panel.

    `sorting_scene.py` is the deterministic scene and declares the ten tags
    `SortingTags` does. The line the engine opens is the rigid-body one, and it
    also has an operator panel and a drive fault on the conveyor and the
    pusher (`engine/fixtures/scene_tag_sets.json`). A student's mapping is
    written against that line, so the exam has to offer the same list. The
    panel is pressed (see above); the two faults are declared and never
    raised, so each holds the value an untouched engine shows.

    A subclass rather than a change to `sorting_scene.py`, which is also the
    tag-bus regression scene and has no operator in it.
    """

    def __init__(self, seed: int) -> None:
        super().__init__(emit_pattern=feed_pattern(seed))
        self.t = 0.0
        self.panel = Panel(self.tags, setpoint=SORTING_PANEL_SETPOINT)
        self.panel.declare(self.tags)
        self.tags.add(fault_input("conveyor", "Conveyor Drive Fault"))
        self.tags.add(fault_input("pusher", "Pusher Drive Fault"))
        #: Ground truth for the contract: belt travel against the panel.
        self.trip = TripLedger(self.panel)
        #: What the examiner did and when. None until it happens.
        self.exam: dict = {"reached_for_the_mushroom_at": None, "struck_at": None,
                           "waited_for_a_clear_plate": None, "start_alone_at": None,
                           "reset_at": None, "restart_at": None}
        self._strike_deadline: float | None = None
        self.script = Script([
            (SORT_START_AT, self.panel.press("start")),
            (SORT_STRIKE_FROM, self._reach_for_the_mushroom),
        ])

    # --- the examiner ---

    def _reach_for_the_mushroom(self) -> None:
        self.exam["reached_for_the_mushroom_at"] = round(self.t, 2)
        self._strike_deadline = self.t + SORT_STRIKE_WAIT

    def _plate_is_clear(self) -> bool:
        beam = scene_model.SENSOR_HIGH_POS - scene_model.SENSOR_WINDOW / 2
        far = scene_model.PUSHER_POS + scene_model.PUSHER_CATCH
        return not any(box.is_tall and beam <= box.position <= far
                       for box in self.boxes)

    def _maybe_strike(self) -> None:
        if self._strike_deadline is None:
            return
        clear = self._plate_is_clear() and bool(self.tags.visible("conveyor.rotate"))
        if not clear and self.t < self._strike_deadline:
            return
        self._strike_deadline = None
        self.exam["struck_at"] = round(self.t, 2)
        self.exam["waited_for_a_clear_plate"] = clear
        self.panel.strike()()
        at = self.t
        self.script.at(at + SORT_RELEASE_AFTER, self.panel.release())
        self.script.at(at + SORT_START_ALONE_AFTER, self._note("start_alone_at", "start"))
        self.script.at(at + SORT_RESET_AFTER, self._note("reset_at", "reset"))
        self.script.at(at + SORT_RESTART_AFTER, self._note("restart_at", "start"))

    def _note(self, key: str, button: str):
        press = self.panel.press(button)

        def do() -> None:
            press()
            self.exam[key] = round(self.t, 2)
        do.__name__ = press.__name__
        return do

    # --- the loop ---

    def tick(self, dt: float) -> None:
        self.t += dt
        self.script.run(self.t)
        self._maybe_strike()
        self.panel.tick(dt, self.t)
        running = bool(self.tags.visible("conveyor.rotate"))
        super().tick(dt)
        self.trip.step(self.t, scene_model.BELT_SPEED * dt if running else 0.0)


def build_sorting_scene(seed: int) -> SortingExam:
    return SortingExam(seed)


def observe_sorting(watched: Watched, dt: float) -> None:
    """Per-tick probe: the two edges whose spacing is the whole exercise."""
    state = getattr(watched, "probe", None)
    if state is None:
        state = watched.probe = {                       # type: ignore[attr-defined]
            "high_was": False, "extend_was": False, "beam_at": None,
            "delays": [], "sorted": [], "tall_seen": 0, "short_seen": 0,
        }
    tags = watched.tags
    high = bool(tags.visible("sensor_high.detect"))
    if high and not state["high_was"]:
        state["beam_at"] = watched.sim_time
    state["high_was"] = high

    extend = bool(tags.visible("pusher.extend"))
    if extend and not state["extend_was"] and state["beam_at"] is not None:
        state["delays"].append(round(watched.sim_time - state["beam_at"], 3))
        state["beam_at"] = None
    state["extend_was"] = extend

    inner = watched.inner
    while state["tall_seen"] < len(inner.sorted_tall):
        box = inner.sorted_tall[state["tall_seen"]]
        state["tall_seen"] += 1
        state["sorted"].append({"carton": box.id, "height":
                                "tall" if box.is_tall else "short",
                                "lane": "chute", "at": round(watched.sim_time, 2)})
    while state["short_seen"] < len(inner.sorted_short):
        box = inner.sorted_short[state["short_seen"]]
        state["short_seen"] += 1
        state["sorted"].append({"carton": box.id, "height":
                                "tall" if box.is_tall else "short",
                                "lane": "far-end", "at": round(watched.sim_time, 2)})


def grade_sorting(watched: Watched, engine: GradedEngine, report: Report,
                  duration: float) -> None:
    """Mark a sorting-by-height run. Ground truth only; tags are evidence."""
    sim = watched.inner
    probe = getattr(watched, "probe", {"delays": [], "sorted": []})
    on_belt = len(sim.boxes)
    emitted = on_belt + len(sim.sorted_tall) + len(sim.sorted_short)
    sorted_count = len(sim.sorted_tall) + len(sim.sorted_short)

    escaped = [b for b in sim.sorted_short if b.is_tall]
    diverted_short = [b for b in sim.sorted_tall if not b.is_tall]
    tall_ok = [b for b in sim.sorted_tall if b.is_tall]
    short_ok = [b for b in sim.sorted_short if not b.is_tall]

    low, high = _beam_to_pusher_window()
    delays = probe["delays"]
    mean_delay = sum(delays) / len(delays) if delays else None

    report.evidence.update({
        "emitted": emitted,
        "sorted": sorted_count,
        "still_on_belt": on_belt,
        "chute": {"total": len(sim.sorted_tall), "tall": len(tall_ok),
                  "short": len(diverted_short)},
        "far_end": {"total": len(sim.sorted_short), "short": len(short_ok),
                    "tall": len(escaped)},
        "misrouted": [e for e in probe["sorted"]
                      if (e["height"] == "tall") != (e["lane"] == "chute")][:20],
        "cartons": probe["sorted"],
        "pusher": {
            "fired": len(delays),
            "mean_delay_after_beam_s": round(mean_delay, 3) if delays else None,
            "delays_s": delays[:40],
            "required_window_s": [round(low, 3), round(high, 3)],
            "held_out_fraction": round(watched.held_true("pusher.extend"), 3),
        },
        "belt_running_fraction": round(watched.held_true("conveyor.rotate"), 3),
        "emit_pulses": watched.changes("emitter.emit") // 2,
    })

    report.add("line.ran",
               sorted_count >= MIN_SORTED,
               f"{sorted_count} cartons reached a lane "
               f"(at least {MIN_SORTED} needed in the {duration:g}s window)")
    report.add("line.both_lanes",
               len(sim.sorted_tall) >= MIN_PER_LANE and len(sim.sorted_short) >= MIN_PER_LANE,
               f"chute {len(sim.sorted_tall)}, far end {len(sim.sorted_short)} "
               f"(at least {MIN_PER_LANE} each)")
    report.add("sort.tall_diverted",
               not escaped,
               "no tall carton ran off the far end" if not escaped
               else f"{len(escaped)} tall carton(s) ran off the far end: "
                    f"{[b.id for b in escaped][:10]}")
    report.add("sort.short_passed",
               not diverted_short,
               "no short carton was diverted" if not diverted_short
               else f"{len(diverted_short)} short carton(s) went down the chute: "
                    f"{[b.id for b in diverted_short][:10]}")
    report.add("line.conservation",
               emitted == sorted_count + on_belt,
               f"{emitted} fed = {sorted_count} sorted + {on_belt} still on the belt")

    contract = _grade_contract(sim, report, watched.sim_time)

    _sorting_feedback(report, watched, sim, probe, escaped, diverted_short,
                      emitted, sorted_count, mean_delay, low, high)
    _contract_feedback(report, sim, contract)


def _grade_contract(sim: SortingExam, report: Report, window: float) -> dict:
    """The operator contract, marked as the start / stop station marks it:
    metres of belt, from `plant.TripLedger`. Four checks, because the four
    ways to get it wrong are four different programs."""
    ledger, exam = sim.trip, sim.exam
    trip = ledger.trips[0] if ledger.trips else None
    allowed = SORT_ESTOP_ALLOWED
    #: The sheet ran to its end: the last Start was pressed, and the window
    #: lasted long enough after it to see whether the belt came back.
    finished = (exam["restart_at"] is not None
                and window >= exam["restart_at"] + SORT_RESTART_WITHIN)
    short = (f"the {window:g}s window ended before the examiner finished the "
             f"E-stop test, which needs about {SORT_EXAM_ENDS_BY:g}s")

    report.evidence["panel"] = {
        "presses": sim.panel.presses,
        "exam": dict(exam),
        "trip": None if trip is None else {
            k: (round(v, 3) if isinstance(v, float) else v) for k, v in trip.items()},
        "allowed_m": round(allowed, 3),
        "started_without_a_press_at": ledger.started_without_a_press[:10],
    }

    unpressed = ledger.started_without_a_press
    report.add("line.started_by_start",
               not unpressed,
               "the belt never started without somebody pressing Start"
               if not unpressed else
               f"the belt started {len(unpressed)} time(s) with no Start press "
               f"since it last stopped, at {unpressed[:5]}s")

    if trip is None:
        report.add("estop.stopped_the_belt", False,
                   short if not finished else "the mushroom was never struck")
    elif not trip["moving_at_strike"]:
        report.add("estop.stopped_the_belt", False,
                   f"the belt was not running when the mushroom was struck at "
                   f"{trip['struck_at']:g}s, so the strike stopped nothing")
    else:
        report.add("estop.stopped_the_belt",
                   trip["struck_travel_m"] <= allowed,
                   f"the belt moved {trip['struck_travel_m'] * 1000:.0f} mm after the "
                   f"mushroom was struck (at most {allowed * 1000:.0f} mm, which is "
                   f"{ESTOP_LIMIT * 1000:.0f} ms of belt)")

    if not finished or trip is None:
        report.add("estop.latched_until_reset", False, short)
        report.add("estop.restarted_after_reset", False, short)
        return {"trip": trip, "finished": False}

    latched = trip["latched_travel_m"]
    report.add("estop.latched_until_reset",
               latched <= 1e-9,
               "the belt stayed still from the release until Reset and then Start"
               if latched <= 1e-9 else
               f"the belt moved {latched * 1000:.0f} mm between the mushroom's "
               f"release at {trip['released_at']:g}s and Reset-then-Start -- "
               f"{_restarted_on(trip, exam)}")
    back = trip["restarted_at"]
    report.add("estop.restarted_after_reset",
               trip["cleared_at"] is not None and back is not None
               and back - trip["cleared_at"] <= SORT_RESTART_WITHIN + 1e-9,
               f"the belt was running again {back - trip['cleared_at']:.2f}s after "
               f"Start at {trip['cleared_at']:g}s (within {SORT_RESTART_WITHIN:g}s)"
               if back is not None and trip["cleared_at"] is not None else
               f"the belt did not run again after Reset at {exam['reset_at']:g}s "
               f"and Start at {exam['restart_at']:g}s")
    return {"trip": trip, "finished": True}


def _restarted_on(trip: dict, exam: dict) -> str:
    """Which of the examiner's moves the belt came back on, from when it
    first moved while the trip was still latched."""
    moved = trip.get("latched_moved_at")
    if moved is None:
        return "it restarted while the trip was latched"
    if exam["reset_at"] is not None and moved >= exam["reset_at"]:
        return f"it restarted at {moved:g}s on Reset alone"
    if exam["start_alone_at"] is not None and moved >= exam["start_alone_at"]:
        return f"it restarted at {moved:g}s on Start alone, with no Reset"
    return f"it restarted at {moved:g}s, when the mushroom was released"


def _sorting_feedback(report, watched, sim, probe, escaped, diverted_short,
                      emitted, sorted_count, mean_delay, low, high) -> None:
    """Say what went wrong in the terms a student can act on.

    A bare FAIL teaches nothing, and the three ways this exercise goes wrong
    are distinguishable from the outside: the line never moved, the pusher
    never moved, or the pusher moved at the wrong moment.
    """
    say = report.feedback.append
    belt = watched.held_true("conveyor.rotate")
    pusher_out = watched.held_true("pusher.extend")
    pulses = watched.changes("emitter.emit") // 2

    if belt == 0.0:
        say("The belt never ran. Nothing you do downstream matters until "
            "`conveyor.rotate` is true -- it is a PLC output, so your program "
            "has to write it, from the moment the examiner presses Start.")
    elif belt < 0.5:
        say(f"The belt ran for only {belt * 100:.0f}% of the window. If that is "
            f"deliberate you will need a longer run to reach {MIN_SORTED} cartons.")

    if pulses == 0 and emitted == 0:
        say("No cartons were fed. `emitter.emit` makes one carton on each "
            "RISING edge -- holding it true forever produces exactly one.")
    elif emitted <= 1 and belt > 0:
        say(f"Only {emitted} carton was fed in the whole window. `emitter.emit` "
            f"has to go false again before it will make another one.")

    if pusher_out == 0.0:
        say("The pusher never came out. `pusher.extend` is a PLC output and "
            "`sensor_high.detect` is the only thing that tells a tall carton "
            "from a short one.")
    elif pusher_out > 0.9:
        say("The pusher was held out for the whole run, so it swept everything "
            "into the chute. It has to come back for the short ones.")
    elif escaped and mean_delay is not None:
        say(f"The pusher fired {mean_delay:.2f}s after the high beam broke. On "
            f"this line the carton is in front of the plate from "
            f"{low + scene_model.PUSHER_TRAVEL_TIME:.2f}s to "
            f"{high + scene_model.PUSHER_TRAVEL_TIME:.2f}s after the beam, and "
            f"the plate itself takes {scene_model.PUSHER_TRAVEL_TIME:.2f}s to "
            f"come out -- so command it between {low:.2f}s and {high:.2f}s.")
    elif escaped:
        say(f"{len(escaped)} tall carton(s) got past. The pusher fired "
            f"{len(probe['delays'])} time(s) for "
            f"{len(sim.sorted_tall) + len(escaped)} tall carton(s), so the "
            f"problem is that it did not fire, not when it fired.")

    if diverted_short and pusher_out <= 0.9:
        say(f"{len(diverted_short)} short carton(s) went down the chute. "
            f"`sensor_low.detect` sees every carton and `sensor_high.detect` "
            f"sees only the tall ones -- a carton that breaks the low beam and "
            f"not the high one is short, and must be left alone.")

    if sorted_count and not escaped and not diverted_short:
        say(f"Sorting was clean: {len(sim.sorted_tall)} tall down the chute, "
            f"{len(sim.sorted_short)} short past the end, none misrouted.")


def _contract_feedback(report, sim: SortingExam, contract: dict) -> None:
    """The operator contract, in terms of what the operator did."""
    say = report.feedback.append
    trip = contract["trip"]
    unpressed = sim.trip.started_without_a_press
    if unpressed and unpressed[0] < SORT_START_AT + 0.5:
        say(f"The belt was running at {unpressed[0]:g}s, before anybody pressed "
            f"Start. The line waits for its operator: latch the RISING edge of "
            f"`panel.start` into a run flag and drive `conveyor.rotate` from "
            f"that, not from `panel.estop` alone.")
    elif unpressed:
        say(f"The belt started at {unpressed[0]:g}s with nobody having pressed "
            f"Start since it last stopped.")

    if trip is None:
        return
    if trip["moving_at_strike"] and trip["struck_travel_m"] > SORT_ESTOP_ALLOWED:
        say(f"The belt kept moving {trip['struck_travel_m'] * 1000:.0f} mm after "
            f"the mushroom was struck. `panel.estop` is NORMALLY CLOSED: true "
            f"means healthy, so a struck mushroom reads FALSE, and the belt has "
            f"to stop on it within {ESTOP_LIMIT * 1000:.0f} ms.")
    if contract["finished"] and trip["latched_travel_m"] > 1e-9:
        say(f"The trip did not latch: {_restarted_on(trip, sim.exam)}. Releasing "
            f"the mushroom must not restart anything, and neither may Start or "
            f"Reset alone -- only Reset and THEN Start.")
    if (contract["finished"] and trip["latched_travel_m"] <= 1e-9
            and trip["restarted_at"] is None):
        say("After Reset and then Start the belt stayed stopped. The latch has to "
            "clear on Reset, and the next Start has to run the line again.")


def _summary_sorting(evidence: dict, out) -> None:
    pusher = evidence["pusher"]
    out(f"fed {evidence['emitted']}, sorted {evidence['sorted']}, "
        f"{evidence['still_on_belt']} still on the belt")
    out(f"chute   {evidence['chute']['total']:>3}  "
        f"({evidence['chute']['tall']} tall, {evidence['chute']['short']} short)")
    out(f"far end {evidence['far_end']['total']:>3}  "
        f"({evidence['far_end']['short']} short, {evidence['far_end']['tall']} tall)")
    window = pusher["required_window_s"]
    measured = pusher["mean_delay_after_beam_s"]
    out(f"pusher fired {pusher['fired']}x, "
        f"{'never' if measured is None else f'{measured:.2f}s'} after the beam "
        f"(needs {window[0]:.2f}-{window[1]:.2f}s)")
    if evidence["misrouted"]:
        out("misrouted:")
        for entry in evidence["misrouted"][:8]:
            out(f"  carton {entry['carton']:>3} ({entry['height']}) "
                f"-> {entry['lane']} at {entry['at']:.1f}s")
    trip = evidence.get("panel", {}).get("trip")
    if trip is not None:
        out(f"mushroom at {trip['struck_at']:g}s: {trip['struck_travel_m'] * 1000:.0f} mm "
            f"of belt after it (at most {evidence['panel']['allowed_m'] * 1000:.0f} mm), "
            f"{trip['latched_travel_m'] * 1000:.0f} mm while latched")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Sorting by height",
    "task": ("Run the belt when Start is pressed, feed cartons, and push the "
             "tall ones down the chute while the short ones carry on. The "
             "mushroom is normally closed and stops the line within 200 ms; "
             "its trip latches, so Start alone will not restart the line -- "
             "Reset, then Start."),
    "build": build_sorting_scene,
    "observe": observe_sorting,
    "grade": grade_sorting,
    "summary": _summary_sorting,
    "duration": 60.0,
    "references": ("good", "blind", "greedy", "nostart", "startalone"),
    "tags": ("conveyor.rotate, emitter.emit, pusher.extend, panel.green, "
             "panel.red are yours to write; panel.start, panel.stop, "
             "panel.reset, panel.estop, sensor_low.detect, sensor_high.detect, "
             "pusher.extended, pusher.retracted, counter.tall, counter.short "
             "are the line's."),
}
