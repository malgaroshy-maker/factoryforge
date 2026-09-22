"""`sorting-by-height`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/sorting_by_height.py`.
"""

from __future__ import annotations

import random

from factoryforge_sidecar import sorting_scene as scene_model

from ..core import GradedEngine, Report, Watched


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


def build_sorting_scene(seed: int):
    return scene_model.SortingScene(emit_pattern=feed_pattern(seed))


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

    _sorting_feedback(report, watched, sim, probe, escaped, diverted_short,
                      emitted, sorted_count, mean_delay, low, high)


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
            "has to write it.")
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


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Sorting by height",
    "task": ("Run the belt, feed cartons, and push the tall ones down the "
             "chute while the short ones carry on."),
    "build": build_sorting_scene,
    "observe": observe_sorting,
    "grade": grade_sorting,
    "summary": _summary_sorting,
    "duration": 60.0,
    "references": ("good", "blind", "greedy"),
    "tags": ("conveyor.rotate, emitter.emit, pusher.extend are yours to write; "
             "sensor_low.detect, sensor_high.detect, pusher.extended, "
             "pusher.retracted, counter.tall, counter.short are the line's."),
}
