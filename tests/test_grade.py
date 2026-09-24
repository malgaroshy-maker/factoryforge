"""The headless grader: what it marks, and what it refuses to be fooled by.

Most of these run the real thing end to end -- a real tag bus on a real
ephemeral port, a controller on the other end of a real websocket -- because
the one question worth asking about a grader is whether a program that does
the job gets a PASS and a program that does not gets a FAIL, and neither is
answerable from unit tests.

The alternative is a grader whose only evidence is that its helper functions
return the right numbers, and the project has been bitten by exactly that
before (AGENTS.md gotcha 16).

Almost all of them pass `--lockstep`: the built-in controller and the plant
are stepped together on the plant's clock, so a verdict cannot depend on how
busy the machine running the suite is (IP-06). That is also why the suite no
longer costs the half hour of wall clock that the sum of its windows would.
The few that do not are marked, and say why.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import grade                                      # noqa: E402
import scene as scene_model                       # noqa: E402

from factoryforge_sidecar.grading.scenes import sorting_by_height as sorting  # noqa: E402

#: Long enough for the examiner's whole sheet -- Start, the mushroom, Start
#: alone, Reset, Start (IP-35) -- and for a correct controller to clear
#: MIN_SORTED with headroom: one carton every 1.8s, six seconds of belt before
#: the first one lands, and six seconds stopped for the E-stop.
PASS_WINDOW = sorting.SORT_EXAM_ENDS_BY + 5.0


def run(*args) -> int:
    return grade.main([str(a) for a in args])


#: Every graded end-to-end run below passes this unless it says why not.
LOCKSTEP = "--lockstep"


# --- the feed pattern --------------------------------------------------

def test_the_feed_pattern_is_reproducible_from_its_seed():
    """A disputed mark has to be re-runnable. The seed is in the report."""
    assert grade.feed_pattern(4321) == grade.feed_pattern(4321)
    assert grade.feed_pattern(4321) != grade.feed_pattern(8765)


def test_the_feed_pattern_is_not_an_alternation():
    """A fixed tall/short/tall/short feed can be sorted by a program that
    pushes every second carton and never reads a sensor. It would pass here
    and fail on any real line, so the order is shuffled."""
    runs = 0
    for seed in range(50):
        pattern = grade.feed_pattern(seed)
        runs += sum(1 for a, b in zip(pattern, pattern[1:]) if a == b)
    assert runs > 0, "no seed in 50 ever fed two cartons of the same height in a row"


def test_every_window_of_eight_cartons_holds_both_heights():
    """The lane minimums have to be reachable however the shuffle lands, or a
    short run fails for a reason that is the grader's fault and not the
    student's."""
    for seed in range(50):
        pattern = grade.feed_pattern(seed)
        doubled = pattern + pattern              # the scene cycles it
        for start in range(len(pattern)):
            window = doubled[start:start + 8]
            assert sum(window) >= grade.MIN_PER_LANE, (seed, start, window)
            assert 8 - sum(window) >= grade.MIN_PER_LANE, (seed, start, window)


# --- the timing advice -------------------------------------------------

def test_the_pusher_window_is_derived_from_the_scenes_own_geometry():
    """The advice a failing student gets is a number. It has to come from the
    line rather than from a constant in the grader, or a change to the belt
    speed makes the grader confidently wrong."""
    low, high = grade._beam_to_pusher_window()
    assert (round(low, 2), round(high, 2)) == (0.60, 1.20)

    original = scene_model.BELT_SPEED
    try:
        scene_model.BELT_SPEED = original / 2      # a slower line waits longer
        slow_low, slow_high = grade._beam_to_pusher_window()
    finally:
        scene_model.BELT_SPEED = original
    assert slow_low > low and slow_high > high


# --- watching, without an engine ---------------------------------------

def test_the_watcher_sees_a_force_the_tag_table_would_hide():
    """`TagTable.set` on a forced tag returns False and changes nothing
    visible, so a run sampled only for changed values would never notice."""
    sim = scene_model.SortingScene()
    watched = grade.Watched(sim)
    watched.tick(0.01)
    assert watched.forced == {}

    sim.tags.force("sensor_low.detect", True)
    watched.tick(0.01)
    assert "sensor_low.detect" in watched.forced


def test_the_watcher_measures_how_long_an_output_was_held():
    sim = scene_model.SortingScene()
    watched = grade.Watched(sim)
    for _ in range(10):
        watched.tick(0.01)
    assert watched.held_true("conveyor.rotate") == 0.0

    sim.tags.set("conveyor.rotate", True)
    for _ in range(10):
        watched.tick(0.01)
    assert watched.held_true("conveyor.rotate") == pytest.approx(0.5)
    assert watched.changes("conveyor.rotate") == 1


def _settled(ticks: int = 20):
    """A scene run forward far enough for its counters to mean something --
    the scene the rubric builds, examiner and all, since that is what
    `grade_sorting` marks."""
    sim = grade.RUBRICS["sorting-by-height"]["build"](11)
    watched = grade.Watched(sim, observe=grade.observe_sorting)
    for _ in range(ticks):
        watched.tick(0.01)
    return sim, watched


def test_the_sorting_verdict_reads_the_cartons_not_the_counters():
    """The counters are simulator-owned, so a controller can only reach them
    by forcing -- but they are still the wrong thing to mark on. This builds a
    line where both counters tell a flattering story and both cartons went to
    the wrong lane, and the verdict has to follow the cartons."""
    sim, watched = _settled()
    sim.sorted_tall.append(scene_model.Box(height=scene_model.SHORT_HEIGHT))
    sim.sorted_short.append(scene_model.Box(height=scene_model.TALL_HEIGHT))
    for _ in range(5):
        watched.tick(0.01)

    # One in each lane, which read alone is a perfect split.
    assert sim.tags.visible("counter.tall") == 1
    assert sim.tags.visible("counter.short") == 1

    report = grade.Report(scene="sorting-by-height")
    grade.grade_sorting(watched, None, report, 1.0)
    failed = {c.id for c in report.checks if not c.ok}
    assert "sort.tall_diverted" in failed
    assert "sort.short_passed" in failed


def _integrity(forces, forced_tags) -> grade.Report:
    """Run only the integrity half, with each half of the force detection
    supplied on its own."""
    sim, watched = _settled(ticks=1)
    watched.forced = dict(forced_tags)
    engine = grade.GradedEngine(watched, port=0)
    engine._client = object()          # a session still open at the end
    engine.sessions = [{"connected_at": 0.0, "disconnected_at": None}]
    engine.forces = list(forces)
    report = grade.Report(scene="sorting-by-height")
    grade.check_integrity(watched, engine, report, 1.0)
    return report


def test_a_force_seen_only_in_the_message_log_is_a_disqualification():
    """The hole the message log covers: a force set and cleared between two
    ticks leaves nothing pinned for the tick sampler to find."""
    report = _integrity([{"at": 0.4, "set": ["counter.tall"], "cleared": []}], {})
    assert report.verdict == "DISQUALIFIED"
    assert "counter.tall" in report.headline


def test_a_force_seen_only_in_the_tag_table_is_a_disqualification():
    """The hole the tick sampler covers: a force set before this grader
    started listening sends no message it will ever see."""
    report = _integrity([], {"sensor_high.detect": 0.1})
    assert report.verdict == "DISQUALIFIED"
    assert "sensor_high.detect" in report.headline


def test_a_clean_run_passes_the_integrity_half():
    report = _integrity([], {})
    assert report.verdict != "DISQUALIFIED"
    assert all(c.ok for c in report.checks)


# --- end to end --------------------------------------------------------

@pytest.fixture(scope="module")
def good_run(tmp_path_factory) -> dict:
    """One correct controller, graded once, read by several tests."""
    out = tmp_path_factory.mktemp("grade") / "good.json"
    code = run("--reference", "good", "--duration", PASS_WINDOW,
               "--seed", 11, "--wait", 30, "--json", out, LOCKSTEP)
    return {"exit": code, "report": json.loads(out.read_text(encoding="utf-8"))}


def test_a_controller_that_does_the_job_passes(good_run):
    assert good_run["exit"] == 0
    assert good_run["report"]["verdict"] == "PASS"
    assert all(check["ok"] for check in good_run["report"]["checks"])


def test_a_pass_is_earned_by_cartons_rather_than_by_the_clock(good_run):
    """Gotcha 16: a check that passes while the simulation does nothing is not
    a check. A PASS has to come with cartons in both lanes and none misrouted."""
    evidence = good_run["report"]["evidence"]
    assert evidence["sorted"] >= grade.MIN_SORTED
    assert evidence["chute"]["tall"] >= grade.MIN_PER_LANE
    assert evidence["far_end"]["short"] >= grade.MIN_PER_LANE
    assert evidence["misrouted"] == []
    assert evidence["chute"]["short"] == 0 and evidence["far_end"]["tall"] == 0


def test_the_report_is_machine_readable_and_agrees_with_the_exit_code(good_run):
    report = good_run["report"]
    assert report["tool"] == "factoryforge-grade"
    assert report["exit_code"] == good_run["exit"]
    assert report["scene"] == "sorting-by-height"
    assert isinstance(report["evidence"]["seed"], int)
    # The per-carton ledger is the appeal record: every carton that landed,
    # its height, its lane and when.
    for entry in report["evidence"]["cartons"]:
        assert set(entry) == {"carton", "height", "lane", "at"}
        assert entry["height"] in ("tall", "short")
        assert entry["lane"] in ("chute", "far-end")


def test_a_lockstep_window_also_opens_on_the_controller(good_run):
    """IP-25 did not have to change lockstep, which already waited for the
    reference controller before stepping anything. The report says so in the
    same words as a wall-clock run, so a reader never has to know which."""
    window = good_run["report"]["evidence"]["window"]
    assert window["plant_seconds_before"] == 0.0
    assert window["opened_on"] == "controller described; stepped in lockstep"
    assert window["opened_at"] >= window["controller_described_at"]


def test_a_timer_instead_of_the_sensor_fails_on_misrouted_cartons(tmp_path):
    """The failure the shuffled feed exists to catch: a pusher on a fixed
    period sorts nothing, however tidy the code looks."""
    out = tmp_path / "blind.json"
    code = run("--reference", "blind", "--duration", 18, "--seed", 11,
               "--wait", 30, "--json", out, LOCKSTEP)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL"
    assert report["evidence"]["misrouted"], "a blind timer sorted everything correctly"
    failed = {c["id"] for c in report["checks"] if not c["ok"]}
    assert failed & {"sort.tall_diverted", "sort.short_passed"}


def test_holding_the_pusher_out_fails_on_the_short_cartons(tmp_path):
    out = tmp_path / "greedy.json"
    code = run("--reference", "greedy", "--duration", 14, "--seed", 11,
               "--wait", 30, "--json", out, LOCKSTEP)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL"
    assert "sort.short_passed" in {c["id"] for c in report["checks"] if not c["ok"]}
    assert any("held out" in line for line in report["feedback"])


# --- the sorting line's operator contract (IP-35) ------------------------
#
# The brief promises that the mushroom stops the line inside 200 ms and that
# Start alone will not restart it. Until IP-35 the exam never pressed Start,
# so a program written to the brief never ran its belt here.

def test_the_sorting_pass_sat_the_whole_e_stop_test(good_run):
    """Gotcha 16: the contract checks pass because the examiner really struck
    a running line and really restarted it, not because nothing happened."""
    panel = good_run["report"]["evidence"]["panel"]
    trip, exam = panel["trip"], panel["exam"]
    assert [what for _, what in panel["presses"]] == ["start", "start", "reset", "start"]
    assert trip["moving_at_strike"] and 0 < trip["struck_travel_m"] <= panel["allowed_m"]
    assert trip["latched_travel_m"] == 0.0
    assert trip["cleared_at"] >= exam["restart_at"] and trip["restarted_at"] is not None
    assert exam["waited_for_a_clear_plate"] is True


def test_a_line_that_runs_before_start_fails_on_the_start(tmp_path):
    """The program the first-hour guide taught before IP-35: the belt runs
    whenever the mushroom is out. It sorts perfectly and is still wrong."""
    code, report = graded(tmp_path, "sorting-by-height", "nostart", 60)
    assert code == 1
    assert "line.started_by_start" in failed_ids(report)
    assert not failed_ids(report) & {"sort.tall_diverted", "sort.short_passed"}
    assert report["evidence"]["panel"]["started_without_a_press_at"][0] < 1.0
    assert any("before anybody pressed Start" in line for line in report["feedback"])


def test_a_line_that_restarts_on_start_alone_fails_the_latch(tmp_path):
    """It stops on the mushroom and waits for Start -- and takes the Start the
    examiner presses with no Reset. Exactly one check, and it is the latch."""
    code, report = graded(tmp_path, "sorting-by-height", "startalone", 60)
    assert code == 1 and failed_ids(report) == {"estop.latched_until_reset"}
    trip = report["evidence"]["panel"]["trip"]
    assert trip["latched_moved_at"] >= report["evidence"]["panel"]["exam"]["start_alone_at"]
    assert any("on Start alone" in line for line in report["feedback"])


def test_a_window_too_short_for_the_e_stop_test_does_not_pass_it(tmp_path):
    """A contract check the exam never reached is not a check that passed."""
    code, report = graded(tmp_path, "sorting-by-height", "good", 20)
    assert code == 1
    assert {"estop.latched_until_reset", "estop.restarted_after_reset"} <= failed_ids(report)


def test_the_examiner_strikes_only_with_no_tall_carton_committed_to_the_plate(
        tmp_path, monkeypatch):
    """Why the strike waits (gotcha 24, for the examiner's own rule). `good`
    times its push on a clock, as the brief allows; a strike that stranded a
    tall carton between the beam and the plate would fail it for a carton
    the E-stop missorted. Seed 2 is one of 21 in 1..40 that do, unwaited."""
    code, report = graded(tmp_path, "sorting-by-height", "good", 60, seed=2)
    assert code == 0, failed_ids(report)
    monkeypatch.setattr(sorting.SortingExam, "_plate_is_clear", lambda self: True)
    code, report = graded(tmp_path, "sorting-by-height", "good", 60, seed=2)
    assert code == 1 and failed_ids(report) == {"sort.tall_diverted"}


# `idle` and `forcer` stay on the wall clock, deliberately. They are the only
# end-to-end runs of the path a real student's sidecar takes -- the engine's own
# real-time tick loop, the grader's wall-clock window -- and neither verdict
# depends on timing: a belt nobody started never moves, and a force is
# recorded whenever it lands.

def test_a_controller_that_connects_and_does_nothing_cannot_pass(tmp_path):
    """The run that has to fail loudly, because it is also what a broken
    connection looks like from here. Wall clock, on purpose; see above."""
    out = tmp_path / "idle.json"
    code = run("--reference", "idle", "--duration", 6, "--seed", 11,
               "--wait", 30, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL"
    assert "line.ran" in {c["id"] for c in report["checks"] if not c["ok"]}
    assert any("belt never ran" in line for line in report["feedback"])


def test_forcing_the_counters_is_disqualified_not_failed(tmp_path):
    """A forced tag is a value that disagrees with the simulation on purpose.
    An instructor wants to tell 'got it wrong' apart from 'tried it on', so it
    gets its own verdict and its own exit code. Wall clock, on purpose."""
    out = tmp_path / "forcer.json"
    code = run("--reference", "forcer", "--duration", 5, "--seed", 11,
               "--wait", 30, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 3 and report["verdict"] == "DISQUALIFIED"
    assert set(report["evidence"]["forced_tags"]) == {"counter.tall", "counter.short"}
    assert report["evidence"]["forces"], "the force messages themselves were not recorded"
    # And no sorting verdict was reached at all: a disqualified run is not marked.
    assert not any(c["id"].startswith("sort.") for c in report["checks"])


#: How late the controller below connects. IP-06's experiment used 8 s, and it
#: is also comfortably more than the 4 s of margin a 24 s sorting window has:
#: `good` sorts 11 cartons in 24 s and 6 in 16 s, against 8 needed.
LATE_BY = 8.0


async def test_a_controller_that_connects_late_is_graded_from_when_it_connected(capsys):
    """IP-25. A real student starts the grader and then types the connect
    command, so their controller arrives seconds after the tag bus opens. The
    plant and the window have to start then, not when the grader did: a
    controller that connected 8 s into a 20 s batch-dosing window used to be
    graded on 12 s, missed the examiner pressing Start, and scored 0.0 L with
    nothing in the report to say why.

    Wall clock, on purpose: this is the path a real student's sidecar takes,
    and lockstep never had the problem, because it has no clock of its own to
    start early."""
    from factoryforge_sidecar.grading import core

    args = argparse.Namespace(
        scene="sorting-by-height", seed=11, duration=PASS_WINDOW, wait=30,
        quiet=True, reference=None, lockstep=False, bus_port=0, student=None,
        json_path=None)
    listening = asyncio.get_running_loop().create_future()
    grading = asyncio.create_task(
        core.run_grading(args, on_listening=listening.set_result))
    shutdown = None
    try:
        # Bounded, and on either outcome: a grader that died before binding
        # never resolves `listening`, and must not leave this test waiting.
        done, _ = await asyncio.wait({listening, grading}, timeout=15,
                                     return_when=asyncio.FIRST_COMPLETED)
        if grading in done:
            grading.result()                       # re-raise why it ended
        assert listening in done, "the grader never bound its tag bus"
        engine = listening.result()

        await asyncio.sleep(LATE_BY)               # the student, still typing
        moved_while_nobody_was_there = engine.scene.sim_time
        shutdown = await core.start_reference("good", engine.url, args.scene)
        report = await asyncio.wait_for(grading, timeout=PASS_WINDOW * 4 + 60)
    finally:
        if shutdown is not None:
            await shutdown()
        if not grading.done():
            grading.cancel()

    window = report.evidence["window"]
    assert report.verdict == "PASS", (report.headline, window,
                                      [c for c in report.checks if not c.ok])
    # Gotcha 16: a PASS has to be cartons, not a clock that ran out kindly.
    assert report.evidence["sorted"] >= grade.MIN_SORTED
    assert report.evidence["sim_seconds"] >= PASS_WINDOW

    # The plant stood still for the whole wait, and the report says when the
    # window opened, and that it opened on this controller -- on its report
    # that it was ready, which a reference makes as soon as its scan runs
    # (IP-30; tests/test_ready_window.py has a driver that is slow to).
    assert moved_while_nobody_was_there == 0.0
    assert window["plant_seconds_before"] == 0.0
    assert window["opened_on"] == "controller ready"
    assert window["controller_described_at"] >= LATE_BY * 0.9, window
    assert window["controller_ready_at"] >= window["controller_described_at"]
    assert window["opened_at"] >= window["controller_ready_at"]
    assert report.evidence["sessions"][0]["described_at"] == window["controller_described_at"]

    core.print_summary(report, args)
    out = " ".join(capsys.readouterr().out.split())      # the line is wrapped
    assert (f"The controller connected {window['controller_described_at']:.1f}s "
            f"after the grader started listening, and its driver (reference:good) "
            f"reported ready at {window['controller_ready_at']:.1f}s; the plant and "
            f"the {PASS_WINDOW:g}s window started then.") in out


# --- the other scenes, end to end -------------------------------------
#
# One PASS and one FAIL per scene, both real: a tag bus on an ephemeral port, a
# controller on the far end of a websocket, and a verdict read out of the JSON
# report. Nothing else answers the only question worth asking about a rubric,
# which is whether it can tell a program that does the job from one that does
# not (AGENTS.md gotcha 24).
#
# They used to cost wall clock -- a thermal plant took as long to heat here as
# it does in the engine -- and they no longer do, because they run in lockstep.
# The windows below are still the shortest each exercise can be marked in
# rather than the windows an instructor would use, because a window is plant
# time and the exam's script is written in plant time; `--duration` on the
# command line is longer for that reason.


def graded(tmp_path, scene: str, reference: str, duration: float,
           seed: int = 11) -> tuple[int, dict]:
    out = tmp_path / f"{scene}-{reference}.json"
    code = run("--scene", scene, "--reference", reference,
               "--duration", duration, "--seed", seed, "--wait", 30, "--json", out,
               LOCKSTEP)
    return code, json.loads(out.read_text(encoding="utf-8"))


def failed_ids(report: dict) -> set[str]:
    return {c["id"] for c in report["checks"] if not c["ok"]}


def test_the_start_stop_station_passes_a_latching_estop_and_an_exact_batch(tmp_path):
    code, report = graded(tmp_path, "start-stop-station", "good", 45)
    assert code == 0 and report["verdict"] == "PASS"
    batch = report["evidence"]["batch"]
    assert batch["made"] == batch["target"] and batch["overrun"] == 0
    # Gotcha 16: a batch of the right size on a line that never moved is not a
    # batch. Cartons have to have reached the far end.
    assert report["evidence"]["removed"] >= 4


def test_a_station_that_ignores_the_mushroom_fails_on_belt_travel(tmp_path):
    """The whole point of this scene. The failure is measured in millimetres of
    belt that moved while the station was tripped, not in the state of a lamp."""
    code, report = graded(tmp_path, "start-stop-station", "noestop", 45)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "estop.stopped_the_belt" in failed_ids(report)
    estop = report["evidence"]["estop"]
    assert estop["travel_while_tripped_m"] > estop["allowed_m"]
    assert any("NORMALLY CLOSED" in line for line in report["feedback"])


def test_a_station_that_restarts_on_start_alone_fails_on_belt_travel(tmp_path):
    """IP-35: the station used to end its trip on any Start after the strike,
    so this passed with 5 mm of belt -- the stop lag. The latch lasts until
    Reset and then Start, and the belt that ran in between is counted."""
    code, report = graded(tmp_path, "start-stop-station", "startalone", 45)
    assert code == 1 and failed_ids(report) == {"estop.stopped_the_belt"}
    estop = report["evidence"]["estop"]
    assert estop["travel_while_tripped_m"] > 10 * estop["allowed_m"]


def test_a_station_that_never_stops_at_the_target_fails_the_batch(tmp_path):
    code, report = graded(tmp_path, "start-stop-station", "runon", 45)
    assert code == 1 and report["verdict"] == "FAIL"
    assert {"batch.hit_the_number", "batch.stopped_itself"} <= failed_ids(report)


def test_the_tank_passes_a_controller_that_settles_at_both_ends(tmp_path):
    code, report = graded(tmp_path, "tank-level-control", "good", 62)
    assert code == 0 and report["verdict"] == "PASS"
    phases = report["evidence"]["phases"]
    assert len(phases) == 2 and phases[0]["setpoint"] != phases[1]["setpoint"]
    # Gotcha 16: every settling number is vacuously good on a tank that never
    # filled, so the run has to show the level actually travelled.
    assert report["evidence"]["travel"] >= 40.0
    # And the settled window is a window, not the one sample a phase whose end
    # was in the year 10000 used to fall back to.
    assert all(p["samples"] > 500 for p in phases)


def test_float_switches_fail_the_tank_on_settled_error(tmp_path):
    code, report = graded(tmp_path, "tank-level-control", "bangbang", 62)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "hold1.settled" in failed_ids(report)


def test_a_setpoint_written_into_the_program_fails_when_the_pot_moves(tmp_path):
    """The check that separates 'reads panel.setpoint' from 'holds 70'."""
    code, report = graded(tmp_path, "tank-level-control", "fixedsp", 62)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "hold2.settled" in failed_ids(report)
    assert any("written into the program" in line for line in report["feedback"])


def test_the_oven_passes_a_controller_that_closes_the_offset(tmp_path):
    code, report = graded(tmp_path, "heat-treat-station", "good", 62)
    assert code == 0 and report["verdict"] == "PASS"
    assert report["evidence"]["travel"] >= 80.0


def test_proportional_only_parks_short_of_the_oven_setpoint(tmp_path):
    """The lesson of the scene, asserted as a number: the offset is the loss
    the plate needs divided by the gain, and it gets bigger at the higher
    setpoint because the standing output does."""
    code, report = graded(tmp_path, "heat-treat-station", "ponly", 62)
    assert code == 1 and report["verdict"] == "FAIL"
    assert {"hold1.settled", "hold2.settled"} <= failed_ids(report)
    first, second = report["evidence"]["phases"]
    assert second["settled_error"] > first["settled_error"] > 3.0


def test_a_thermostat_reaches_the_oven_setpoint_and_still_fails(tmp_path):
    """The trap `hold*.steady` exists for. Mean error near zero, setpoint
    reached every couple of seconds, from alternate sides, forever."""
    code, report = graded(tmp_path, "heat-treat-station", "thermostat", 62)
    assert code == 1 and report["verdict"] == "FAIL"
    assert {"hold1.steady", "hold2.steady"} <= failed_ids(report)
    for phase in report["evidence"]["phases"]:
        assert phase["settled_error"] < 3.0, "the cycling controller missed setpoint"
        assert phase["ripple"] > 5.0


def test_the_light_curtain_passes_a_controller_that_sorts_on_the_number(tmp_path):
    code, report = graded(tmp_path, "light-curtain-sorting", "good", 62, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    evidence = report["evidence"]
    assert evidence["misrouted"] == []
    assert evidence["chute"] >= 2 and evidence["far_end"] >= 2
    # Both rules were really exercised, or the pot-moving half of the rubric
    # marked nothing.
    assert len(evidence["thresholds_seen"]) == 2


def test_a_threshold_written_into_the_program_fails_the_light_curtain(tmp_path):
    """The difference between this scene and sorting-by-height: there the rule
    is two bits of wiring, here it is a number that the run changes."""
    code, report = graded(tmp_path, "light-curtain-sorting", "fixed", 62, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "sort.followed_the_measurement" in failed_ids(report)
    wrong = report["evidence"]["misrouted"]
    assert wrong, "a fixed threshold sorted every carton correctly"
    # All of them under one threshold: that is the signature of a latched
    # setpoint rather than of a misjudged height, and the feedback says so.
    assert len({entry["threshold_m"] for entry in wrong}) == 1


def test_diverting_every_second_carton_fails_the_light_curtain(tmp_path):
    code, report = graded(tmp_path, "light-curtain-sorting", "everyother", 62, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "sort.followed_the_measurement" in failed_ids(report)


def test_the_roller_line_passes_a_controller_that_weighs_and_spaces(tmp_path):
    code, report = graded(tmp_path, "roller-line-weighing", "good", 70, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    evidence = report["evidence"]
    assert evidence["shared_the_deck"] == 0
    assert evidence["misjudged"] == []
    # The exam has to have contained at least one carton the two instruments
    # disagree about, or `metalonly` below would pass for want of a question.
    assert evidence["metal_and_weight_disagree"] >= 1


def test_rejecting_on_the_inductive_sensor_fails_when_the_limit_moves(tmp_path):
    """Metal and over-limit are the same cartons at 3000 g and different ones
    at 1500 g, because a tall cardboard carton weighs 2160 g."""
    code, report = graded(tmp_path, "roller-line-weighing", "metalonly", 70, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "reject.matched_the_weight" in failed_ids(report)
    wrong = report["evidence"]["misjudged"]
    assert wrong and all(entry["flagged"] == entry["metal"] for entry in wrong)
    assert any("inductive sensor disagree" in line for line in report["feedback"])


def test_feeding_faster_than_the_deck_fails_the_roller_line(tmp_path):
    code, report = graded(tmp_path, "roller-line-weighing", "fastfeed", 70, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert {"scale.singulated", "reject.matched_the_weight"} <= failed_ids(report)


def test_the_buffer_passes_a_release_measured_in_encoder_pulses(tmp_path):
    code, report = graded(tmp_path, "accumulation-buffer", "good", 78, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    evidence = report["evidence"]
    assert evidence["escaped_a_raised_blade"] == []
    # Gotcha 16 again, in the form this scene invites: "nothing got past the
    # blade" is trivially true of a belt that was not running, so the plant
    # counts the metres that ran underneath it.
    assert evidence["belt_travel_while_held_m"] >= 3.0
    # And the exam really did change the drive, or there was no second speed.
    # It halves it (IP-29): a buffer driven faster than the outfeed backs its
    # releases up against the outfeed, in the engine as in the model.
    assert (evidence["second_speed"]["belt_m_per_s"]
            < evidence["first_speed"]["belt_m_per_s"] * 0.6)
    # Gotcha 16 once more: a buffer that overfilled its belt spilled cartons
    # off the infeed end, and "the same size" is then about what was left.
    assert evidence["spilled_off_the_infeed_end"] == []


def test_a_release_timed_in_seconds_fails_when_the_drive_changes(tmp_path):
    """The whole scene. Same command, same blade, a drive whose top speed the
    run halved -- and half as much product out of a release timed on a
    clock."""
    code, report = graded(tmp_path, "accumulation-buffer", "timed", 78, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "release.same_size_at_both_speeds" in failed_ids(report)
    evidence = report["evidence"]
    assert (evidence["second_speed"]["mean_cartons"]
            < evidence["first_speed"]["mean_cartons"] - 1.0)
    assert any("timed in seconds" in line for line in report["feedback"])


def test_batch_dosing_passes_a_batch_that_ends_on_litres(tmp_path):
    code, report = graded(tmp_path, "batch-dosing", "good", 78, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    batches = report["evidence"]["batches"]
    assert len(batches) == 2
    # The same litres at two pump ratings, in about twice the time.
    assert batches[0]["rated_flow"] == 2 * batches[1]["rated_flow"]
    assert abs(batches[0]["delivered_L"] - batches[1]["delivered_L"]) <= 1.5
    assert batches[1]["seconds"] > batches[0]["seconds"]


def test_a_batch_timed_in_seconds_delivers_half_when_the_pump_is_re_rated(tmp_path):
    code, report = graded(tmp_path, "batch-dosing", "timed", 78, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "dose2.on_the_number" in failed_ids(report)
    first, second = report["evidence"]["batches"]
    # The lesson, and the only part of it that is a fact about the controller
    # rather than about the machine it ran on: re-rate the pump and a batch
    # ended on seconds delivers about half, while the pot did not move.
    assert second["delivered_L"] < first["delivered_L"] * 0.65
    assert second["delivered_L"] > first["delivered_L"] * 0.35

    # The first batch lands inside tolerance, asserted directly now that it can
    # be. On wall-clock scans it could not: a stopwatch cuts off at a scan
    # boundary, scans coarsen under load, and master failed here on Linux CI
    # with the first batch at 23.52 L against a 23.5 L limit. In lockstep
    # (IP-06) the scans are exact and the number is the same every run, so it
    # is a fact about the controller again. `_feedback` only writes the lesson
    # line below when this holds, which is how the old test asserted it
    # without saying so.
    assert abs(first["delivered_L"] - report["evidence"]["pot_litres"]) < 1.0, first
    assert first["delivered_L"] >= second["delivered_L"]
    assert any("ends on seconds cannot see that" in line
               for line in report["feedback"])


def test_not_zeroing_the_totaliser_ends_the_second_batch_before_it_starts(tmp_path):
    code, report = graded(tmp_path, "batch-dosing", "noreset", 78, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "dose2.on_the_number" in failed_ids(report)
    assert report["evidence"]["batches"][1]["delivered_L"] < 2.0
    assert any("over before it started" in line for line in report["feedback"])


def test_the_guarded_cell_passes_a_program_that_never_writes_the_motor(tmp_path):
    code, report = graded(tmp_path, "guarded-cell", "good", 68, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    evidence = report["evidence"]
    assert evidence["wrote_belt_rotate"] is False
    assert evidence["started_without_a_press_at"] == []
    assert evidence["transferred"] >= 3
    # The exam has to have actually stopped and restarted the cell, or the
    # check below it is about a gate that never opened.
    assert 0.3 < evidence["contactor_fraction"] < 0.95


def test_a_program_that_starts_the_motor_on_the_permissive_fails(tmp_path):
    """The whole lesson of the scene, and the one a student writes by accident.

    The relay closing hands `starter.coil` back; it does not command it. A
    program holding the coil for as long as the cell "should be running"
    restarts the machine the instant the guard is reset, with somebody still
    inside. The plant records the tick the contactor pulled in and whether
    anybody had pressed Start since it last stopped -- neither of which a
    controller can arrange from the bus."""
    code, report = graded(tmp_path, "guarded-cell", "autostart", 68, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "cell.no_start_on_the_permissive" in failed_ids(report)
    assert report["evidence"]["started_without_a_press_at"]
    assert any("automatic restart" in line for line in report["feedback"])


def test_writing_the_motors_own_tag_fails_the_guarded_cell(tmp_path):
    code, report = graded(tmp_path, "guarded-cell", "writesbelt", 68, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "cell.never_wrote_the_motor" in failed_ids(report)
    assert report["evidence"]["wrote_belt_rotate"] is True


def test_a_mute_held_past_the_scanners_limit_fails(tmp_path):
    code, report = graded(tmp_path, "guarded-cell", "tapedmute", 68, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "cell.mute_within_the_limit" in failed_ids(report)
    assert report["evidence"]["longest_mute_s"] > grade.GC_MUTE_LIMIT


def test_a_cell_that_locks_its_guard_properly_still_sits_the_whole_exam(tmp_path):
    """IP-29. The examiner obeys the gate's solenoid locks now, as the engine
    does. A program that locks while the motor can run and releases once it
    has stopped is found locked, Stop is pressed, and the gate opens -- and
    everything the exam asks after that is still asked."""
    code, report = graded(tmp_path, "guarded-cell", "guardlock", 68, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    access = report["evidence"]["access"]
    assert access["refused"] == ["guard_a", "guard_b"]
    assert access["stop_pressed_at"] is not None
    assert access["opened_at"] - access["stop_pressed_at"] < 1.0
    # Gotcha 16: the gate test did run -- the gate opened, and the cell came
    # back only on the Start after it.
    assert report["evidence"]["gate_openings"] == 1
    assert "cell.gate_stopped_it" not in failed_ids(report)
    assert report["evidence"]["transferred"] >= 3


def test_a_guard_locked_for_good_fails_on_access(tmp_path):
    """The other side of obeying the lock: a gate that never lets go keeps the
    examiner out after Stop. It fails for that, and only for that -- not by
    being marked on a gate test that could not run."""
    code, report = graded(tmp_path, "guarded-cell", "lockedshut", 68, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"cell.guard_released_for_access"}
    assert report["evidence"]["access"]["opened_at"] is None
    assert report["evidence"]["gate_openings"] == 0
    assert "cell.gate_stopped_it" not in {c["id"] for c in report["checks"]}
    assert any("never let go" in line for line in report["feedback"])


def test_the_cell_passes_a_sequence_written_on_feedback(tmp_path):
    code, report = graded(tmp_path, "pick-and-place-cell", "good", 72, seed=5)
    assert code == 0 and report["verdict"] == "PASS"
    evidence = report["evidence"]
    assert evidence["dropped"] == [] and evidence["empty_carries_at"] == []
    # Carried at both travel speeds, which is the only thing that separates
    # this from a cell that happened to work at one.
    assert evidence["placed_before_the_axis_slowed"] >= 1
    assert evidence["placed_after_the_axis_slowed"] >= 1
    # And the drive really is analog: a bit output wearing a float's clothes
    # would report its own reference back instantly.
    assert evidence["max_ramp_gap_percent"] > 1.0


def test_a_sequence_on_timers_drops_cartons_when_the_axis_slows(tmp_path):
    """Right at one travel speed, which is what makes it worth catching. The
    plant records where on the rail the vacuum was released, so a cycle that
    let go over the middle is a dropped carton and not a slow one."""
    code, report = graded(tmp_path, "pick-and-place-cell", "timed", 72, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "cell.nothing_dropped" in failed_ids(report)
    dropped = report["evidence"]["dropped"]
    assert dropped
    assert all(entry["phase"] == "slow" for entry in dropped), (
        "the timed sequence dropped cartons before the axis was even slowed, "
        "so this proves nothing about timers")
    assert all(20.0 < entry["position"] < 80.0 for entry in dropped)


# --- the scenes IP-14 added --------------------------------------------------

def test_the_star_delta_passes_a_changeover_on_speed_with_a_dead_time(tmp_path):
    code, report = graded(tmp_path, "star-delta-start", "good", 42, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: both starts really ran, and the exam really loaded the
    # machine between them, or the changeover was only ever marked once.
    light, loaded = evidence["starts"]
    assert light["reached_delta_at"] and loaded["reached_delta_at"]
    assert loaded["load_percent"] > light["load_percent"] + 40
    assert len(evidence["changeovers"]) == 2 and evidence["shorts_at"] == []


def test_star_and_delta_in_the_same_scan_trip_the_breaker(tmp_path):
    """The contacts open slower than they close: a changeover in one scan
    overlaps star and delta by 15 ms, which is a short across the supply."""
    code, report = graded(tmp_path, "star-delta-start", "samescan", 42, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "changeover.no_short" in failed_ids(report)
    assert report["evidence"]["shorts_at"]
    assert any("still arcing" in line for line in report["feedback"])


def test_a_changeover_on_a_timer_comes_early_on_a_loaded_machine(tmp_path):
    """Right on the empty machine it was calibrated on; half way up the
    run-up once the exam loads the machine."""
    code, report = graded(tmp_path, "star-delta-start", "timed", 42, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"changeover.at_speed"}
    first, second = report["evidence"]["changeovers"]
    assert first["speed"] >= 85.0 > 75.0 > second["speed"]
    assert second["peak_current"] > 3 * report["evidence"]["full_load_amps"]


def test_the_servo_passes_a_shuttle_that_waits_for_the_operator(tmp_path):
    code, report = graded(tmp_path, "servo-positioning", "good", 40, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    fault = report["evidence"]["fault"]
    # Gotcha 16: the fault really was raised mid-move, and the axis really
    # did stop and come back, or "held until Reset" is about a still axis.
    assert abs(fault["moving_at_mm_s"]) >= 200.0
    assert fault["reset_at"] <= fault["acknowledged_at"] < fault["reset_at"] + 0.2


def test_acknowledging_a_servo_error_by_itself_is_automatic_restart(tmp_path):
    code, report = graded(tmp_path, "servo-positioning", "autoack", 40, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"error.held_until_reset"}
    fault = report["evidence"]["fault"]
    assert fault["acknowledged_at"] < fault["reset_at"]
    assert fault["moved_before_reset_mm"] > 100.0
    assert any("automatic restart" in line for line in report["feedback"])


def test_a_servo_error_nobody_acknowledges_stops_the_axis_for_good(tmp_path):
    code, report = graded(tmp_path, "servo-positioning", "noack", 40, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"error.recovered"}


def test_the_cooling_tunnel_passes_split_range_with_a_deadband(tmp_path):
    code, report = graded(tmp_path, "cooling-tunnel", "good", 80, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: both actuators really worked -- the heater held the hot
    # phases and the fan brought the recipe's drop down.
    assert evidence["heater_on_s"] > 30.0 and evidence["fan_on_s"] > 2.0
    assert evidence["both_on_s"] == 0.0
    assert len(evidence["phases"]) == 3


def test_a_tunnel_that_never_runs_its_fan_is_late_to_every_drop(tmp_path):
    code, report = graded(tmp_path, "cooling-tunnel", "heatonly", 80, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "cool.arrived_in_time" in failed_ids(report)
    assert "split.no_fighting" not in failed_ids(report)
    assert report["evidence"]["arrived_after_the_drop_s"] > 1.5 * 10.0
    assert any("The fan never ran" in line for line in report["feedback"])


def test_a_fan_that_never_stops_fights_the_heater(tmp_path):
    """Holds every setpoint and meets every recipe change -- the one thing it
    gets wrong is the one thing split range is about."""
    code, report = graded(tmp_path, "cooling-tunnel", "fight", 80, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"split.no_fighting"}


def test_the_air_receiver_passes_scaled_counts_and_a_proven_valve(tmp_path):
    code, report = graded(tmp_path, "air-receiver", "good", 75, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: the receiver really cycled through the band, the exam really
    # seized the valve, and the alarm came after the travel time, not before.
    assert evidence["highest_bar"] - evidence["lowest_bar"] > 0.4
    assert evidence["stuck_command_at"] is not None
    assert 2.0 <= evidence["alarm_took_s"] <= evidence["alarm_within_s"]
    assert evidence["gauge_mean_error_bar"] < 0.1


def test_scaling_by_32767_holds_the_receiver_high(tmp_path):
    code, report = graded(tmp_path, "air-receiver", "by32767", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"receiver.held_the_band"}
    worst = report["evidence"]["worst_outside"]
    assert worst["bar"] / worst["pot"] == pytest.approx(32767 / 27648, abs=0.03)


def test_a_valve_trusted_without_its_feedback_hides_a_seizure(tmp_path):
    code, report = graded(tmp_path, "air-receiver", "nodiscrepancy", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"valve.stuck_was_caught"}


def test_a_discrepancy_check_with_no_timer_alarms_on_a_healthy_valve(tmp_path):
    code, report = graded(tmp_path, "air-receiver", "impatient", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"valve.no_false_alarm"}


def test_the_press_passes_a_program_that_obeys_its_selector_and_the_relay(tmp_path):
    code, report = graded(tmp_path, "press-station", "good", 46, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: the ram really cycled in AUTO and really stroked on two hands,
    # and the hold-to-run press really let go mid-stroke.
    modes = [s["mode"] for s in evidence["strokes"]]
    assert modes.count("AUTO") >= 2 and modes.count("MAN") == 1
    short = evidence["short_press"]
    assert 0.1 < short["at_release_m"] < evidence["bdc_trips_at_m"]
    assert short["deepest_m"] <= short["at_release_m"] + 0.01 and short["home_at"]


def test_left_and_right_is_not_the_two_hand_permissive(tmp_path):
    """The examiner ties one palm down and presses the other a second later:
    both bits true, `valid` false -- and a ram that moves on the AND."""
    code, report = graded(tmp_path, "press-station", "andhands", 46, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"mode.manual_needs_both_hands"}
    assert report["evidence"]["tie_down_travel_m"] > 0.1
    assert any("taped-down button" in line for line in report["feedback"])


def test_a_cycle_that_ignores_the_selector_fails_off_and_manual(tmp_path):
    code, report = graded(tmp_path, "press-station", "ignoresmode", 46, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"mode.off_is_off", "mode.manual_needs_both_hands"}


def test_the_rotary_index_passes_a_cycle_interlocked_on_its_switches(tmp_path):
    code, report = graded(tmp_path, "rotary-index", "good", 60, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: cartons really crossed the deck, on both deck speeds.
    assert evidence["delivered"] >= 4 and evidence["pushed_after_the_slowdown"] >= 1
    assert evidence["index_speed_then"] < evidence["index_speed_first"]


def test_a_push_timed_on_the_rated_index_meets_a_slowed_deck_half_round(tmp_path):
    code, report = graded(tmp_path, "rotary-index", "timed", 60, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "index.turned_square" in failed_ids(report)
    skewed = report["evidence"]["skewed"]
    assert skewed and all(s["at"] >= 24.0 and 30.0 < s["turned_deg"] < 88.0 for s in skewed)


def test_turning_the_deck_on_not_extended_turns_it_under_the_plate(tmp_path):
    code, report = graded(tmp_path, "rotary-index", "notretracted", 60, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"index.plate_clear_while_turning"}
    assert any("`not extended` is" in line for line in report["feedback"])


def test_the_pivot_diverter_passes_a_blade_held_until_the_chute_counts(tmp_path):
    code, report = graded(tmp_path, "pivot-divert", "good", 75, seed=5)
    assert code == 0 and report["verdict"] == "PASS", failed_ids(report)
    evidence = report["evidence"]
    # Gotcha 16: cartons really went both ways, on both belt speeds.
    assert evidence["chute"] >= 2 and evidence["far_end"] >= 2
    assert evidence["judged_after_the_slowdown"] >= 2
    assert evidence["belt_speed_then"] < evidence["belt_speed_first"]


def test_a_blade_held_on_a_stopwatch_lets_cartons_go_once_the_belt_slows(tmp_path):
    code, report = graded(tmp_path, "pivot-divert", "timed", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"divert.sorted"}
    misrouted = report["evidence"]["misrouted"]
    assert misrouted and all(m["tall"] and m["lane"] == "far" and m["released_at"] is not None
                             and m["released_at"] >= 24.0 for m in misrouted)


def test_a_blade_wired_to_the_eye_is_home_before_the_carton_arrives(tmp_path):
    code, report = graded(tmp_path, "pivot-divert", "unlatched", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "divert.sorted" in failed_ids(report)
    misrouted = report["evidence"]["misrouted"]
    assert misrouted and all(m["tall"] and m["released_at"] is None for m in misrouted)
    assert any("latch the decision" in line for line in report["feedback"])


def test_a_blade_fired_like_a_pusher_hits_the_carton(tmp_path):
    """Every carton still lands in the right lane -- the lesson is how."""
    code, report = graded(tmp_path, "pivot-divert", "late", 75, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert failed_ids(report) == {"divert.blade_ready"}
    assert report["evidence"]["struck"] and not report["evidence"]["misrouted"]


def test_turning_every_second_carton_fails_the_shuffled_feed(tmp_path):
    """Perfect against the template's own emitter, which alternates: the
    reason the exam's feed is not the engine's (docs/GRADING.md)."""
    code, report = graded(tmp_path, "pivot-divert", "everyother", 75, seed=5)
    assert code == 1 and failed_ids(report) == {"divert.sorted"}
    misrouted = report["evidence"]["misrouted"]
    assert any(m["tall"] for m in misrouted) and any(not m["tall"] for m in misrouted)


def test_nobody_connecting_is_an_error_rather_than_a_fail(tmp_path):
    """A student whose sidecar never started has not failed the exercise, and
    a marking script needs to tell the two apart."""
    out = tmp_path / "absent.json"
    code = run("--duration", 1, "--wait", 1, "--seed", 11, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 2 and report["verdict"] == "ERROR"
    assert "no controller connected" in report["headline"]
    assert report["checks"] == []


# --- the CLI contract --------------------------------------------------

def test_an_unknown_scene_is_an_error_not_a_crash(capsys):
    assert run("--scene", "no-such-scene") == 2
    assert "no rubric" in capsys.readouterr().err


MANIFEST = json.loads(
    (ROOT / "engine" / "templates" / "manifest.json").read_text(encoding="utf-8"))
SHIPPED = [entry["id"] for entry in MANIFEST]


def test_list_names_only_the_scenes_that_are_really_marked(capsys):
    """`--list` is a promise. A scene named there that has no rubric behind it
    is a class told an exercise will be marked and then finding it is not."""
    assert run("--list") == 0
    listed = capsys.readouterr().out
    for scene_id in grade.RUBRICS:
        assert scene_id in listed
        assert scene_id in SHIPPED, (
            f"{scene_id} is graded but is not a scene the engine ships")


def test_the_claim_in_the_docs_matches_the_rubrics_that_exist():
    """docs/GRADING.md's 'what this does not do' section is the honest half of
    this tool, and the way it goes wrong is by being written once and then
    outliving the code. Every scene with a rubric has to be named there, and
    every shipped scene without one has to be named there too."""
    doc = (ROOT / "docs" / "GRADING.md").read_text(encoding="utf-8")
    for scene_id in grade.RUBRICS:
        assert scene_id in doc, f"{scene_id} is graded and docs/GRADING.md never says so"
    for scene_id in SHIPPED:
        if scene_id not in grade.RUBRICS:
            assert scene_id in doc, (
                f"{scene_id} ships and is not graded, and docs/GRADING.md does "
                f"not admit it")


#: The ten scenes this grader was built for. Named rather than read from the
#: manifest, and that is deliberate: the manifest is a live list that other
#: work adds to, and a scene landing there tomorrow should not silently rewrite
#: what this claim covers. A new scene is caught by
#: `test_the_claim_in_the_docs_matches_the_rubrics_that_exist` instead, which
#: makes docs/GRADING.md admit it rather than making this test change meaning.
GRADED_TEN = [
    "sorting-by-height", "start-stop-station", "tank-level-control",
    "light-curtain-sorting", "roller-line-weighing", "pick-and-place-cell",
    "accumulation-buffer", "heat-treat-station", "guarded-cell", "batch-dosing",
]


def test_all_ten_scenes_this_was_built_for_have_a_rubric():
    """The claim this work exists to make true."""
    missing = [scene for scene in GRADED_TEN if scene not in grade.RUBRICS]
    assert not missing, f"not graded: {missing}"


def test_nothing_is_graded_that_the_engine_does_not_ship():
    """The other direction, and the one that would embarrass a marking rig:
    a rubric for a scene id no student can open."""
    assert set(grade.RUBRICS) <= set(SHIPPED), (
        f"graded but not shipped: {sorted(set(grade.RUBRICS) - set(SHIPPED))}")


def test_every_rubric_has_a_right_answer_and_a_wrong_one():
    """AGENTS.md gotcha 24: a rubric only ever seen to pass is a rubric nobody
    knows the shape of. Each scene carries a `good` that must pass and at least
    one controller that is wrong about *that scene's* lesson and must fail."""
    for scene_id, rubric in grade.RUBRICS.items():
        refs = rubric["references"]
        assert refs[0] == "good", f"{scene_id}'s first reference is not `good`"
        assert len(refs) >= 2, f"{scene_id} has no deliberately wrong controller"
        for name in refs:
            assert grade.reference_for(scene_id, name) is not None
        for name in grade.SHARED_REFERENCES:
            assert grade.reference_for(scene_id, name) is not None


async def test_two_graded_runs_can_share_a_machine():
    """HP-53 removed fixed ports project-wide. Two exam sessions on one
    machine must not be able to collide, so the default binds port 0 and asks
    the OS what it got."""
    first = grade.GradedEngine(scene_model.SortingScene(), port=0)
    second = grade.GradedEngine(scene_model.SortingScene(), port=0)
    await first.start()
    await second.start()
    try:
        assert first.actual_port != second.actual_port
        assert first.actual_port != 0 and second.actual_port != 0
    finally:
        await first.stop()
        await second.stop()


# --- lockstep ----------------------------------------------------------
#
# IP-06. A graded run against a real PLC is two machines on two wall clocks,
# and it has to be: a real PLC does not wait for anybody. A built-in reference
# controller runs in this process, so it can be made to wait -- and the tests
# above make it, because on the wall clock a mark moved with how busy the
# machine was. These are the claims that makes, asserted.

def test_lockstep_is_refused_without_a_built_in_controller(capsys):
    """A real controller scans on its own clock and cannot be stepped, so a
    lockstep run with nobody built in to step would mark a student against a
    clock their PLC never saw. Refused, before anything binds a port."""
    assert run("--lockstep", "--duration", 1, "--wait", 1) == 2
    assert "--lockstep needs --reference" in capsys.readouterr().err


def test_a_scan_that_joins_after_the_plant_has_moved_is_refused():
    """Lockstep can only promise `dt` is plant time if it has stepped every
    scan since t = 0. A controller whose scan turned up late would have missed
    some, silently -- so it is refused, and kept for `run` to raise where
    somebody is looking rather than inside the controller's own task."""
    lockstep = grade.Lockstep(grade.GradedEngine(scene_model.SortingScene(), port=0))
    lockstep.started = True

    async def body(dt: float) -> None:
        pass

    with pytest.raises(RuntimeError, match="after the plant had started"):
        lockstep.attach(body, grade.SCAN)
    assert lockstep.error is not None and lockstep.scan is None


async def test_every_built_in_controller_but_idle_scans_on_the_plants_clock():
    """A reference that timed itself with `asyncio.sleep` would go on in
    wall-clock seconds while lockstep ran the plant as fast as the machine
    allowed: a controller on a different clock from its plant, which is the
    bug IP-06 removed, back and silent. So every one of them has to hand its
    scan over -- all but `idle`, which has nothing to scan."""
    for scene_id, rubric in grade.RUBRICS.items():
        for name in tuple(rubric["references"]) + grade.SHARED_REFERENCES:
            engine = grade.GradedEngine(grade.Watched(rubric["build"](5)), port=0)
            engine.lockstep = lockstep = grade.Lockstep(engine)
            await engine.start()
            try:
                shutdown = await grade.start_reference(name, engine.url, scene_id,
                                                       lockstep)
                try:
                    scans = lockstep.scan is not None
                    assert scans == (name != "idle"), (
                        f"{scene_id}/{name}: "
                        f"{'no scan handed over' if not scans else 'idle scans'}")
                finally:
                    await shutdown()
            finally:
                await engine.stop()


#: Evidence fields stamped with the wall clock rather than the plant's. They
#: label a session and say when its window opened; nothing is marked on them.
WALL_CLOCK_LABELS = {"sessions", "window"}


def test_a_lockstep_mark_does_not_move_when_the_machine_is_busy(tmp_path, monkeypatch):
    """IP-06, asserted inside the suite rather than left to CI's weather.

    The stopwatch batch that failed on master, twice: once as fast as the
    machine allows, once with both halves stalling at random -- the plant
    between its steps, the controller between the frames it reads -- the way a
    loaded machine stalls them. On the wall clock a stall moves the cut-off,
    because the plant goes on moving while the controller is not looking. In
    lockstep the plant is not moving while anybody stalls, so the two runs must
    agree on every number the rubric reads."""
    code, calm = graded(tmp_path, "batch-dosing", "timed", 78, seed=5)

    rng = random.Random(1)
    stalls = {"plant": 0, "controller": 0}
    step, handle = grade.GradedEngine.step, grade.LockstepClient._handle

    async def stalling_step(self) -> None:
        if rng.random() < 0.02:
            stalls["plant"] += 1
            await asyncio.sleep(0.02)
        await step(self)

    async def stalling_handle(self, msg) -> None:
        if rng.random() < 0.02:
            stalls["controller"] += 1
            await asyncio.sleep(0.02)
        await handle(self, msg)

    monkeypatch.setattr(grade.GradedEngine, "step", stalling_step)
    monkeypatch.setattr(grade.LockstepClient, "_handle", stalling_handle)
    busy_code, busy = graded(tmp_path, "batch-dosing", "timed", 78, seed=5)

    # Gotcha 16: two runs agreeing proves nothing if neither did anything, and
    # a stall that never happened makes the second run the first one again.
    assert calm["evidence"]["clock"] == "lockstep"
    assert calm["evidence"]["ticks"] == 7800
    assert calm["evidence"]["batches"][0]["delivered_L"] >= 5.0
    assert stalls["plant"] > 50 and stalls["controller"] > 50, stalls

    assert busy_code == code
    assert busy["verdict"] == calm["verdict"]
    assert busy["checks"] == calm["checks"]
    assert busy["feedback"] == calm["feedback"]
    strip = lambda evidence: {k: v for k, v in evidence.items()  # noqa: E731
                              if k not in WALL_CLOCK_LABELS}
    assert strip(busy["evidence"]) == strip(calm["evidence"])


# --- the numbers docs/GRADING.md quotes (IP-27) --------------------------
#
# The wrong-controller table in GRADING.md is the shape of every rubric in one
# place, and its numbers were written by hand, before lockstep, from more than
# one seed and window, and read by nothing. "22.1 L and then 11.0 L" stayed in
# it after the stopwatch reference was recalibrated. A lockstep run is
# reproducible from its seed, so the table can quote exact runs, and this reads
# each row with a number out of the document and re-runs it. As with IP-02's
# counts, editing any number to a wrong one fails here.

#: The seed every table row is measured with. GRADING.md states the command,
#: and `test_the_table_states_the_command_it_was_measured_with` holds the two
#: together.
TABLE_SEED = 5

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _phases(key: str, fmt: str):
    return lambda e: [format(p[key], fmt) for p in e["phases"]]


def _reset_before(e: dict, at: float) -> float:
    return max(t for t, what in e["presses"] if what == "reset" and t <= at)


#: (scene, controller) -> the numbers its row quotes, formatted as the row
#: writes them, computed from that run's JSON evidence. Order does not matter;
#: every number in the row must be one of these and every one of these must be
#: in the row.
TABLE_NUMBERS = {
    ("start-stop-station", "noestop"): lambda e: [
        f"{e['estop']['travel_while_tripped_m'] * 1000:.0f}",
        f"{e['estop']['allowed_m'] * 1000:.0f}"],
    ("start-stop-station", "startalone"): lambda e: [
        f"{e['estop']['travel_while_tripped_m'] * 1000:.0f}",
        f"{e['estop']['allowed_m'] * 1000:.0f}"],
    ("sorting-by-height", "nostart"): lambda e: [
        f"{at:.2f}" for at in e["panel"]["started_without_a_press_at"]],
    ("sorting-by-height", "startalone"): lambda e: [
        f"{e['panel']['exam']['start_alone_at']:.2f}",
        f"{e['panel']['trip']['latched_travel_m'] * 1000:.0f}"],
    ("start-stop-station", "runon"): lambda e: [
        str(e["batch"]["made"]), str(e["batch"]["target"])],
    ("tank-level-control", "bangbang"): _phases("settled_error", ".1f"),
    ("tank-level-control", "fixedsp"): lambda e: [
        f"{e['phases'][1]['final']:.0f}", f"{e['phases'][1]['setpoint']:.0f}"],
    ("pick-and-place-cell", "timed"): lambda e: [
        str(e["placed_before_the_axis_slowed"]),
        f"{e['travel_speed_before']:g}", f"{e['travel_speed_after']:g}",
        str(len(e["dropped"])),
        *sorted({f"{d['position']:.1f}" for d in e["dropped"]})],
    ("accumulation-buffer", "timed"): lambda e: [
        f"{e['first_speed']['mean_cartons']:.1f}",
        f"{e['second_speed']['mean_cartons']:.1f}"],
    ("heat-treat-station", "ponly"): lambda e: [
        n for p in e["phases"]
        for n in (f"{p['settled_error']:.1f}", f"{p['setpoint']:g}")],
    ("heat-treat-station", "thermostat"): lambda e: (
        _phases("settled_error", ".1f")(e) + _phases("ripple", ".1f")(e)),
    ("guarded-cell", "autostart"): lambda e: [
        f"{e['started_without_a_press_at'][0]:.2f}",
        f"{_reset_before(e, e['started_without_a_press_at'][0]):.2f}"],
    ("guarded-cell", "tapedmute"): lambda e: [
        f"{e['longest_mute_s']:.2f}", f"{e['scanner_mute_limit_s']:g}",
        str(len(e["mute_withdrawn_at"]))],
    ("batch-dosing", "timed"): lambda e: [
        *(f"{b['delivered_L']:.1f}" for b in e["batches"]),
        f"{e['pot_litres']:g}"],
    ("star-delta-start", "samescan"): lambda e: [f"{e['shorts_at'][0]:.2f}"],
    ("star-delta-start", "timed"): lambda e: [
        f"{e['changeovers'][1]['speed']:.1f}", f"{e['changeovers'][1]['peak_current']:.1f}",
        f"{e['pot_percent']:g}"],
    ("servo-positioning", "autoack"): lambda e: [
        f"{e['fault']['acknowledged_at']:.2f}", f"{e['fault']['moved_before_reset_mm']:.0f}",
        f"{e['fault']['reset_at']:.1f}"],
    ("cooling-tunnel", "heatonly"): lambda e: [
        f"{e['arrived_after_the_drop_s']:.1f}", f"{e['recipe'][0]:g}", f"{e['recipe'][1]:g}",
        f"{e['arrive_within_s']:g}"],
    ("cooling-tunnel", "fight"): lambda e: [f"{e['both_on_s']:.1f}"],
    ("air-receiver", "by32767"): lambda e: [
        f"{e['worst_outside']['bar']:.2f}", f"{e['worst_outside']['pot']:g}"],
    ("air-receiver", "impatient"): lambda e: [f"{e['false_alarms_at'][0]:.2f}"],
    ("press-station", "andhands"): lambda e: [f"{e['tie_down_travel_m'] * 1000:.0f}"],
    ("press-station", "ignoresmode"): lambda e: [f"{e['off_travel_m'] * 1000:.0f}"],
    ("rotary-index", "timed"): lambda e: [
        str(len(e["skewed"])), f"{e['skewed'][0]['turned_deg']:.0f}",
        f"{e['index_speed_then']:g}"],
    ("rotary-index", "notretracted"): lambda e: [f"{e['turned_under_the_plate_deg']:.0f}"],
    ("pivot-divert", "timed"): lambda e: [
        str(len(e["misrouted"])), f"{e['belt_speed_then']:g}"],
    ("pivot-divert", "late"): lambda e: [
        str(len(e["struck"])), f"{e['belt_speed_first']:g}"],
}

#: The feedback excerpts under "What a student gets back": each is how one of
#: these runs' feedback lines begins, up to the "[...]".
EXCERPT_RUNS = [("heat-treat-station", "ponly"), ("accumulation-buffer", "timed"),
                ("guarded-cell", "autostart"), ("roller-line-weighing", "metalonly")]


def _grading_doc() -> str:
    return (ROOT / "docs" / "GRADING.md").read_text(encoding="utf-8")


def _table_rows() -> dict[tuple[str, str], str]:
    """(scene, controller) -> 'what it fails on', read out of GRADING.md. A
    blank scene cell continues the scene above it, as the table is written."""
    lines = _grading_doc().splitlines()
    start = lines.index("| scene | wrong controller | what it fails on |") + 2
    rows: dict[tuple[str, str], str] = {}
    scene = None
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 3, line
        scene = cells[0].strip("`") or scene
        rows[(scene, cells[1].strip("`"))] = cells[2]
    return rows


def _excerpts() -> list[str]:
    """The four bullets in the code block after 'Every scene does the same',
    each with its wrapping undone and its '[...]' cut off."""
    doc = _grading_doc()
    block = doc[doc.index("Every scene does the same"):]
    block = block[block.index("```") + 3:]
    block = block[:block.index("```")]
    bullets = [" ".join(b.split()) for b in block.split("  - ")[1:]]
    return [b[:b.index("[...]")].strip() for b in bullets]


@pytest.fixture(scope="module")
def table_run(tmp_path_factory):
    """A lockstep run of one controller at TABLE_SEED in the scene's own
    window, run once however many tests read it. Caching is sound only
    because lockstep is reproducible from the seed, which the stalling test
    above asserts."""
    runs: dict[tuple[str, str], tuple[int, dict]] = {}
    folder = tmp_path_factory.mktemp("table")

    def get(scene: str, reference: str) -> tuple[int, dict]:
        if (scene, reference) not in runs:
            out = folder / f"{scene}-{reference}.json"
            code = run("--scene", scene, "--reference", reference, "--seed",
                       TABLE_SEED, "--wait", 30, "--json", out, "--quiet", LOCKSTEP)
            runs[(scene, reference)] = (
                code, json.loads(out.read_text(encoding="utf-8")))
        return runs[(scene, reference)]
    return get


def test_the_table_states_the_command_it_was_measured_with():
    doc = _grading_doc()
    assert (f"python tools/grade.py --scene <scene> --reference <controller> "
            f"--lockstep --seed {TABLE_SEED} --json out.json") in doc


def test_every_number_in_the_table_is_one_this_file_measures():
    """Both directions. A number added to a row that nothing measures is back
    to being written by hand; a row measured here that the table lost is a
    measurement of nothing."""
    rows = _table_rows()
    # The parser has to see the table, or everything below passes blind.
    assert len(rows) >= 16 and ("batch-dosing", "timed") in rows, sorted(rows)
    with_numbers = {key for key, text in rows.items() if NUMBER.search(text)}
    assert with_numbers == set(TABLE_NUMBERS), (
        f"quoted but not measured: {sorted(with_numbers - set(TABLE_NUMBERS))}; "
        f"measured but not quoted: {sorted(set(TABLE_NUMBERS) - with_numbers)}")
    for scene, reference in rows:
        assert reference in grade.RUBRICS[scene]["references"], (scene, reference)


@pytest.mark.parametrize("scene,reference", sorted(TABLE_NUMBERS))
def test_the_wrong_controller_table_quotes_a_lockstep_run(scene, reference, table_run):
    code, report = table_run(scene, reference)
    # "What it fails on" is a claim that it fails.
    assert code == 1 and report["verdict"] == "FAIL", (scene, reference, report["headline"])
    row = _table_rows()[(scene, reference)]
    quoted = sorted(NUMBER.findall(row))
    measured = sorted(TABLE_NUMBERS[(scene, reference)](report["evidence"]))
    assert quoted == measured, (
        f"GRADING.md says {row!r}; the run at seed {TABLE_SEED} measured {measured}")


@pytest.mark.parametrize("excerpt_index", range(len(EXCERPT_RUNS)))
def test_the_feedback_excerpts_are_what_those_runs_print(excerpt_index, table_run):
    excerpts = _excerpts()
    assert len(excerpts) == len(EXCERPT_RUNS), excerpts
    _, report = table_run(*EXCERPT_RUNS[excerpt_index])
    excerpt = excerpts[excerpt_index]
    assert any(" ".join(line.split()).startswith(excerpt)
               for line in report["feedback"]), (excerpt, report["feedback"])


# --- one file per scene (IP-18) -----------------------------------------
#
# Adding a graded scene is `grading/scenes/<scene>.py` plus
# `grading/reference/<scene>.py` and nothing else -- the bar HP-34 set for
# parts. That only stays true while no shared file knows any scene by name,
# because the first one that does is the file the next scene has to edit.

from factoryforge_sidecar import grading                     # noqa: E402
from factoryforge_sidecar.grading import registry            # noqa: E402

GRADING = Path(grading.__file__).resolve().parent


def _scene_names() -> set[str]:
    """Every way a shared file could name a scene, read from the registry
    rather than written here: the id, the id as a module name, the class or
    function that builds its plant, and the class of the plant it builds."""
    names: set[str] = set()
    for scene_id, rubric in registry.rubrics().items():
        names |= {scene_id, scene_id.replace("-", "_"), rubric["build"].__name__,
                  type(rubric["build"](1)).__name__}
    names |= set(registry.references())
    return names


def _names_in_code(source: str) -> set[str]:
    """Every identifier and string literal the code uses -- comments and
    docstrings left out. A comment saying the stopwatch reference for one
    scene is why lockstep exists is history, and this codebase keeps its
    history next to the code; a string or a name is a dependency."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.add(node.value)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.alias):
            found |= {node.name, node.asname or ""}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _scenes_named(source: str, names: set[str]) -> set[str]:
    used = _names_in_code(source)
    return {name for name in names if any(name in word for word in used)}


#: Every module in the package that is not one scene's own: the core and
#: whatever the scenes and their references are built from.
SHARED_MODULES = sorted(
    path.relative_to(GRADING).as_posix() for path in GRADING.rglob("*.py")
    if path.parent.name not in ("scenes", "reference")
    or path.name.startswith("_"))


def test_the_registry_finds_every_scene_and_its_references():
    """The half of the bar the registry carries: each scene module is found
    by its own `SCENE`, and each has a reference module found the same way."""
    assert set(registry.rubrics()) == set(registry.references())
    for scene_id, rubric in registry.rubrics().items():
        assert rubric["build"](1).name == scene_id


@pytest.mark.parametrize("module", SHARED_MODULES)
def test_no_shared_grading_file_names_a_scene(module):
    """IP-18: `core.py` above all, and every other file a new scene would be
    built on. Scene ids are read from the registry, so a scene added later
    is covered without anyone editing this test."""
    names = _scene_names()
    assert "core.py" in SHARED_MODULES and len(names) >= 20, SHARED_MODULES
    # The check has to be able to see one, or it passes by being blind.
    some_scene = next(iter(sorted(registry.rubrics())))
    assert _scenes_named(f"rubric = RUBRICS[{some_scene!r}]", names) == {some_scene}

    source = (GRADING / module).read_text(encoding="utf-8")
    named = _scenes_named(source, names)
    assert not named, f"grading/{module} names {sorted(named)}"
