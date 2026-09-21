"""The headless grader: what it marks, and what it refuses to be fooled by.

Most of these run the real thing end to end -- a real tag bus on a real
ephemeral port, a controller on the other end of a real websocket -- because
the one question worth asking about a grader is whether a program that does
the job gets a PASS and a program that does not gets a FAIL, and neither is
answerable from unit tests.

That costs about a minute of wall clock, which is the bulk of it. The
alternative is a grader whose only evidence is that its helper functions
return the right numbers, and the project has been bitten by exactly that
before (AGENTS.md gotcha 16).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import grade                                      # noqa: E402
import scene as scene_model                       # noqa: E402

#: Long enough for a correct controller to clear MIN_SORTED with headroom:
#: one carton every 1.8s, and six seconds of belt before the first one lands.
PASS_WINDOW = 24.0


def run(*args) -> int:
    return grade.main([str(a) for a in args])


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
    """A scene run forward far enough for its counters to mean something."""
    sim = scene_model.SortingScene()
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
               "--seed", 11, "--wait", 30, "--json", out)
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


def test_a_timer_instead_of_the_sensor_fails_on_misrouted_cartons(tmp_path):
    """The failure the shuffled feed exists to catch: a pusher on a fixed
    period sorts nothing, however tidy the code looks."""
    out = tmp_path / "blind.json"
    code = run("--reference", "blind", "--duration", 18, "--seed", 11,
               "--wait", 30, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL"
    assert report["evidence"]["misrouted"], "a blind timer sorted everything correctly"
    failed = {c["id"] for c in report["checks"] if not c["ok"]}
    assert failed & {"sort.tall_diverted", "sort.short_passed"}


def test_holding_the_pusher_out_fails_on_the_short_cartons(tmp_path):
    out = tmp_path / "greedy.json"
    code = run("--reference", "greedy", "--duration", 14, "--seed", 11,
               "--wait", 30, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL"
    assert "sort.short_passed" in {c["id"] for c in report["checks"] if not c["ok"]}
    assert any("held out" in line for line in report["feedback"])


def test_a_controller_that_connects_and_does_nothing_cannot_pass(tmp_path):
    """The run that has to fail loudly, because it is also what a broken
    connection looks like from here."""
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
    gets its own verdict and its own exit code."""
    out = tmp_path / "forcer.json"
    code = run("--reference", "forcer", "--duration", 5, "--seed", 11,
               "--wait", 30, "--json", out)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 3 and report["verdict"] == "DISQUALIFIED"
    assert set(report["evidence"]["forced_tags"]) == {"counter.tall", "counter.short"}
    assert report["evidence"]["forces"], "the force messages themselves were not recorded"
    # And no sorting verdict was reached at all: a disqualified run is not marked.
    assert not any(c["id"].startswith("sort.") for c in report["checks"])


# --- the other scenes, end to end -------------------------------------
#
# One PASS and one FAIL per scene, both real: a tag bus on an ephemeral port, a
# controller on the far end of a websocket, and a verdict read out of the JSON
# report. Nothing else answers the only question worth asking about a rubric,
# which is whether it can tell a program that does the job from one that does
# not (AGENTS.md gotcha 24).
#
# They cost wall clock -- a thermal plant takes as long to heat here as it does
# in the engine -- and the windows below are the shortest each exercise can be
# marked in rather than the windows an instructor would use. `--duration` on
# the command line is longer for that reason.


def graded(tmp_path, scene: str, reference: str, duration: float,
           seed: int = 11) -> tuple[int, dict]:
    out = tmp_path / f"{scene}-{reference}.json"
    code = run("--scene", scene, "--reference", reference,
               "--duration", duration, "--seed", seed, "--wait", 30, "--json", out)
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
    assert (evidence["second_speed"]["belt_m_per_s"]
            > evidence["first_speed"]["belt_m_per_s"] * 1.5)


def test_a_release_timed_in_seconds_fails_when_the_drive_speeds_up(tmp_path):
    """The whole scene. Same command, same blade, a drive whose top speed the
    run doubled -- and twice as much product out of a release timed on a
    clock."""
    code, report = graded(tmp_path, "accumulation-buffer", "timed", 78, seed=5)
    assert code == 1 and report["verdict"] == "FAIL"
    assert "release.same_size_at_both_speeds" in failed_ids(report)
    evidence = report["evidence"]
    assert (evidence["second_speed"]["mean_cartons"]
            > evidence["first_speed"]["mean_cartons"] + 1.0)
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
    # Right once: the stopwatch answer is calibrated, and its first batch lands.
    assert "dose1.on_the_number" not in failed_ids(report)
    assert second["delivered_L"] < first["delivered_L"] * 0.65
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


def test_every_shipped_scene_has_a_rubric():
    """The claim this work exists to make true. It is asserted against the
    engine's own manifest rather than against a list here, so a scene added to
    the start screen without a rubric fails this instead of quietly shipping
    ungraded."""
    assert set(grade.RUBRICS) == set(SHIPPED), (
        f"not graded: {sorted(set(SHIPPED) - set(grade.RUBRICS))}; "
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
