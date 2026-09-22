#!/usr/bin/env python3
"""Grade a student's PLC program against a scene, unattended.

    python tools/grade.py --list
    python tools/grade.py --scene sorting-by-height --student "a.patel"
    python tools/grade.py --scene sorting-by-height --json marks/a.patel.json

This is the *other* side of `tools/try_scene.py`. That one drives a scene the
way a PLC would, to prove the scene works. This one runs the scene and lets
somebody else's controller drive it, to find out whether *they* work. It starts
a tag bus, prints the one command a student has to run, waits for their sidecar
to connect, watches for a fixed window and reports.

    grade.py  ── tag bus ──  factoryforge-sidecar connect ── OPC UA / S7 / MQTT ── PLC
    (the exam)                 (the student's own driver)          (their program)

So the thing under test is the whole chain a real line has, over the same seam
a real PLC uses. Nothing here reaches into the controller and nothing here
drives the scene.

**The verdict comes from the plant, not from the tags.** The scene knows how
tall every carton it made was and which lane it ended in, how many litres the
pump really moved, and the tick the contactor pulled in -- and no controller
can reach any of it. Counters, sensor values and actuator commands are
*evidence*, the part a student needs in order to fix anything, but they are
never the criterion, because every one of them is reachable from the bus and a
criterion you can reach is a criterion you can fake.

**And the exam changes the plant while the program runs.** Six of the ten
scenes are gradeable only because of this: the pot moves to a second value, the
drive's top speed doubles, the gantry slows down, the pump is re-rated. None of
those is a value on the bus, so the only way to notice is to measure -- which
is exactly the difference between a program written on feedback and one written
on a stopwatch. A rubric that never moved anything would mark both the same.

**Forcing is refused, not ignored.** A forced tag is a value that disagrees
with the simulation on purpose. It is the right tool for fault injection and
the wrong tool for a graded run, and a grader that did not look would be beaten
by four lines of Node-RED. Every `force` message is recorded, every tick is
checked for a pinned tag, and either one ends the run as DISQUALIFIED rather
than FAIL -- an instructor wants to tell "got it wrong" apart from "tried it on".

Exit codes, for a marking script:

    0  PASS           every check met
    1  FAIL           the program ran and got it wrong
    2  ERROR          nothing to grade -- nobody connected, or the run broke
    3  DISQUALIFIED   tags were forced

`--json` writes the whole run: checks, per-carton ledger, measured timings,
every force. That file is the appeal record.

Honest limits are in docs/GRADING.md, and they grew rather than shrank when
this went from one scene to ten. The short version: headless Python models of
the plants rather than the 3D engine, so nothing here can jam or tip; no marks
for fault injection on any scene, though every one of them has a fault tag and
half the briefs end on it; and the operator contract itself is marked on two
scenes out of ten.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))
sys.path.insert(0, str(ROOT / "harness"))

from engine_stub import EngineStub                      # noqa: E402
from factoryforge_sidecar.tags import Tag, TagTable, TagValue  # noqa: E402
import scene as scene_model                             # noqa: E402

#: Ports a graded run may bind when the instructor asks for a fixed one. The
#: default is 0 -- the OS picks, and two runs on one machine cannot collide.
#: Fixed ports were removed project-wide under HP-53 for exactly that reason.
SUGGESTED_PORTS = range(7500, 7511)

#: How long it waits for a controller before giving up with ERROR.
DEFAULT_WAIT = 120.0

TICK_MS = 10


# --- results -----------------------------------------------------------

PASS, FAIL, ERROR, DISQUALIFIED = "PASS", "FAIL", "ERROR", "DISQUALIFIED"
EXIT = {PASS: 0, FAIL: 1, ERROR: 2, DISQUALIFIED: 3}


@dataclass
class Check:
    """One thing the run either did or did not do, with the numbers."""
    id: str
    ok: bool
    detail: str

    def to_json(self) -> dict:
        return {"id": self.id, "ok": self.ok, "detail": self.detail}


@dataclass
class Report:
    scene: str
    verdict: str = ERROR
    checks: list[Check] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    feedback: list[str] = field(default_factory=list)
    headline: str = ""

    def add(self, check_id: str, ok: bool, detail: str) -> bool:
        self.checks.append(Check(check_id, ok, detail))
        return ok


# --- watching the simulation -------------------------------------------

class Watched:
    """A scene with a recorder strapped to it.

    Wraps rather than subclasses, and touches only what `EngineStub` touches --
    `name`, `tags`, `tick` -- so the engine cannot tell the difference and
    `harness/scene.py` needs no changes to be gradable.

    Sampling happens inside `tick`, which means once per fixed step and never
    on a timer of its own. A separate sampling task would have to sleep, and
    under Windows' 15.6 ms clock floor a short sleep does not sleep at all
    (AGENTS.md gotcha 2) -- so the one place with an exact clock is the only
    place worth reading it from.
    """

    def __init__(self, inner, observe=None) -> None:
        self.inner = inner
        self._observe = observe
        self.sim_time = 0.0
        self.ticks = 0
        #: tag id -> sim time it was first seen forced.
        self.forced: dict[str, float] = {}
        #: tag id -> {"changes": n, "true_ticks": n, "ever_true": bool}
        self.activity: dict[str, dict] = {}
        self._last: dict[str, TagValue] = {}

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def tags(self) -> TagTable:
        return self.inner.tags

    def tick(self, dt: float) -> None:
        self.inner.tick(dt)
        self.sim_time += dt
        self.ticks += 1
        self._sample()
        if self._observe is not None:
            self._observe(self, dt)

    def _sample(self) -> None:
        tags = self.inner.tags
        for tag in tags:
            if tags.is_forced(tag.id):
                self.forced.setdefault(tag.id, round(self.sim_time, 3))
            value = tags.visible(tag.id)
            record = self.activity.setdefault(
                tag.id, {"changes": 0, "true_ticks": 0, "ever_true": False,
                         "min": value, "max": value})
            if tag.id in self._last and self._last[tag.id] != value:
                record["changes"] += 1
            self._last[tag.id] = value
            if value:
                record["true_ticks"] += 1
                record["ever_true"] = True
            record["min"] = min(record["min"], value)
            record["max"] = max(record["max"], value)

    def held_true(self, tag_id: str) -> float:
        """Fraction of the run this tag was true. 0.0 if it never was."""
        record = self.activity.get(tag_id)
        if not record or not self.ticks:
            return 0.0
        return record["true_ticks"] / self.ticks

    def changes(self, tag_id: str) -> int:
        return self.activity.get(tag_id, {}).get("changes", 0)


class GradedEngine(EngineStub):
    """`EngineStub` plus a record of everything the controller did to it.

    Overriding `_handle` and `_on_message` is reaching past the underscore, and
    it is done knowingly: the engine is the only place that sees a `force`
    message arrive, and `harness/engine_stub.py` is owned by the tag-bus
    stream. Sampling the tag table alone would miss a force that was set and
    cleared between two ticks, which is exactly what somebody gaming this would
    write.
    """

    def __init__(self, scene, port: int, tick_ms: int = TICK_MS) -> None:
        super().__init__(scene, host="127.0.0.1", port=port, tick_ms=tick_ms)
        self.sessions: list[dict] = []
        self.forces: list[dict] = []
        self.input_writes: list[dict] = []
        #: Every output tag the controller has ever written. One scene marks on
        #: this: `guarded-cell` asks for a program that never touches the
        #: motor's own tag, and "did you write it" is a fact about the wire
        #: rather than about the plant, so it cannot be inferred from the
        #: cartons. Nothing else uses it as a criterion.
        self.written_tags: set[str] = set()
        self.arrived = asyncio.Event()
        self._t0 = time.perf_counter()

    @property
    def elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 3)

    @property
    def controller_connected(self) -> bool:
        return self._client is not None

    async def _handle(self, ws) -> None:
        # The base class refuses a second sidecar before serving it, so only a
        # connection it actually accepted counts as a session.
        accepted = self._client is None
        session = None
        if accepted:
            session = {"connected_at": self.elapsed, "disconnected_at": None}
            self.sessions.append(session)
            self.arrived.set()
        try:
            await super()._handle(ws)
        finally:
            if session is not None:
                session["disconnected_at"] = self.elapsed

    async def _on_message(self, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "force":
            self.forces.append({
                "at": self.elapsed,
                "set": sorted(msg.get("values") or {}),
                "cleared": sorted(msg.get("clear") or []),
            })
        elif kind == "write":
            self.written_tags.update(msg.get("values") or {})
            wrong = sorted(
                tag_id for tag_id in (msg.get("values") or {})
                if (tag := self.scene.tags.get(tag_id)) is not None
                and tag.kind != "output")
            if wrong:
                self.input_writes.append({"at": self.elapsed, "tags": wrong})
        await super()._on_message(msg)


# --- the sorting-by-height rubric --------------------------------------
#
# Numbers come from harness/scene.py rather than from this file, so a change to
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


# =======================================================================
#  The other nine scenes
# =======================================================================
#
# `harness/scene.py` models one line, and it is owned by the tag-bus stream.
# The nine models below live here instead, and they are the same *kind* of
# thing: 1-D kinematic plants with no physics, faithful about tag semantics,
# sensor windows and -- where the lesson is analog -- about the engine's own
# dynamics, which are copied from the C# part and the template that configures
# it rather than invented. Every constant that matters carries the file it came
# from, so a change to a part shows up here as a number that no longer matches
# rather than as a rubric that is quietly wrong.
#
# Three rules each of them follows.
#
# **The plant keeps its own ledger.** Every model records what physically
# happened -- which carton went where, how many litres really left the pump,
# whether the contactor pulled in before anybody pressed Start -- in attributes
# no bus message reaches. That ledger is the verdict. Tags are evidence.
#
# **The plant runs the exam.** Each model owns a `Script`: a list of things an
# examiner does on the plant side, on simulation time. Pressing Start, striking
# the mushroom, turning the pot to a number chosen from the seed, and -- the
# part that makes several of these rubrics work at all -- reaching into the
# machinery mid-run and changing something physical that no tag reports. A belt
# that is suddenly half as fast. A pump rated for half the flow. A gantry that
# travels slower than it did. Those are the changes a program written on a
# stopwatch cannot survive and a program written on feedback does not notice.
#
# **Nothing here forces a tag.** The engine's own parts do -- a safety relay
# holds the starter coil down by forcing it, and a motor starter drives the belt
# the same way -- but a forced tag is this tool's disqualification signal, so
# the models reproduce the *behaviour* and never the mechanism: the plant simply
# ignores a command it is not obeying. See docs/GRADING.md, which says so.


@dataclass
class Item:
    """A carton, in one dimension. Height and mass are the plant's secret.

    `height` is metres, `mass` kilograms -- both from `BoxPhysics.cs`, where a
    carton is 0.20 x H x 0.24 at 150 kg/m3 and a steel one at 900.
    """
    height: float = 0.10
    metal: bool = False
    position: float = 0.0
    id: int = 0
    lane: str | None = None          #: set once it leaves the line
    measured: float | None = None    #: what an instrument said about it
    threshold: float | None = None   #: the rule in force when it was measured
    carried: bool = False

    @property
    def mass(self) -> float:
        density = 900.0 if self.metal else 150.0
        return 0.20 * self.height * 0.24 * density

    @property
    def grams(self) -> float:
        return self.mass * 1000.0


class Script:
    """The examiner, on simulation time.

    A list of `(seconds, what)` run in order, once each, from inside `tick`.
    Simulation time and not wall clock, so a slow machine sits an exam in the
    same order as a fast one, and nothing here sleeps -- on Windows a sleep
    under 15.6 ms does not sleep at all (AGENTS.md gotcha 2) and the tick is the
    one place with an exact clock.
    """

    def __init__(self, steps: list[tuple[float, object]]) -> None:
        self._steps = sorted(steps, key=lambda s: s[0])
        self._next = 0
        self.done: list[tuple[float, str]] = []

    def run(self, now: float) -> None:
        while self._next < len(self._steps) and now >= self._steps[self._next][0]:
            when, what = self._steps[self._next]
            self._next += 1
            what()                                         # type: ignore[operator]
            self.done.append((round(now, 2), getattr(what, "__name__", "step")))


class Panel:
    """The operator station, from the operator's side.

    Every template places one and every scene here has one, because half of
    what these exercises teach is the operator contract: momentary buttons, a
    normally-closed mushroom, a latch that Start cannot clear. The grader is
    the hand on those buttons -- it presses them from the plant side, which is
    what makes "Start while tripped did nothing" a fact about the machine
    rather than a fact about a tag somebody could have written.

    `estop` is inverted on purpose, exactly as `ButtonPanel.cs` declares it:
    normally closed, true = healthy.
    """

    #: Long enough that a controller scanning at 50 ms cannot miss the edge,
    #: short enough to still be one edge. `tools/try_scene.py` holds 0.15 s
    #: against the real engine for the same reason.
    PRESS = 0.15

    def __init__(self, tags: TagTable, prefix: str = "panel",
                 setpoint: float = 0.0) -> None:
        self.tags = tags
        self.prefix = prefix
        self._held: dict[str, float] = {}
        self.setpoint_value = setpoint
        self.healthy = True
        #: Sim time of the last Start press. Ground truth for "did the machine
        #: start because somebody started it".
        self.last_start: float | None = None
        self.presses: list[tuple[float, str]] = []
        self._t = 0.0

    def declare(self, tags: TagTable) -> None:
        p = self.prefix
        for name, title in (("start", "Start (momentary)"), ("stop", "Stop (momentary)"),
                            ("reset", "Reset (momentary)")):
            tags.add(Tag(f"{p}.{name}", f"Panel {title}", "bit", "input"))
        tags.add(Tag(f"{p}.estop", "Panel E-Stop OK (NC)", "bit", "input", value=True))
        tags.add(Tag(f"{p}.setpoint", "Panel Setpoint", "float", "input",
                     value=self.setpoint_value))
        tags.add(Tag(f"{p}.green", "Panel Green Lamp", "bit", "output"))
        tags.add(Tag(f"{p}.red", "Panel Red Lamp", "bit", "output"))

    # --- the examiner's hand ---

    def press(self, name: str):
        def do() -> None:
            self._held[name] = self._t + self.PRESS
            self.presses.append((round(self._t, 2), name))
            if name == "start":
                self.last_start = self._t
        do.__name__ = f"press {name}"
        return do

    def set_setpoint(self, value: float):
        def do() -> None:
            self.setpoint_value = float(value)
        do.__name__ = f"pot to {value}"
        return do

    def strike(self):
        def do() -> None:
            self.healthy = False
        do.__name__ = "strike the mushroom"
        return do

    def release(self):
        def do() -> None:
            self.healthy = True
        do.__name__ = "release the mushroom"
        return do

    # --- the plant side ---

    def tick(self, dt: float, now: float) -> None:
        self._t = now
        p = self.prefix
        for name in ("start", "stop", "reset"):
            self.tags.set(f"{p}.{name}", now < self._held.get(name, -1.0))
        self.tags.set(f"{p}.estop", self.healthy)
        self.tags.set(f"{p}.setpoint", float(self.setpoint_value))

    def started_since(self, when: float) -> bool:
        return self.last_start is not None and self.last_start >= when


class PlantScene:
    """What every model below has in common.

    Owns the tag table, the panel, the clock and the exam script, and offers
    the two things a rubric always wants: a place to put ground truth, and a
    `bit`/`num` pair that reads the *visible* value -- the one a force would
    change -- so a scene never accidentally grades the value underneath a pin.
    """

    name = "unnamed"

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.t = 0.0
        self.tags = TagTable([])
        self.panel = Panel(self.tags)
        self.panel.declare(self.tags)
        self.script = Script([])
        self.notes: dict = {}

    # --- reading what the controller wrote ---

    def bit(self, tag_id: str) -> bool:
        return bool(self.tags.visible(tag_id))

    def num(self, tag_id: str) -> float:
        return float(self.tags.visible(tag_id))

    # --- the loop ---

    def tick(self, dt: float) -> None:
        self.t += dt
        self.script.run(self.t)
        self.panel.tick(dt, self.t)
        self.step(dt)

    def step(self, dt: float) -> None:            # pragma: no cover - overridden
        raise NotImplementedError

    # --- helpers the models share ---

    def _declare(self, *tags: Tag) -> None:
        for tag in tags:
            self.tags.add(tag)

    def _eye(self, items: list[Item], position: float, window: float = 0.20) -> bool:
        """A diffuse photoelectric sensor: true while an item is in its window.

        Matches `harness/scene.py`, which is in turn the semantics
        `PhotoelectricSensor.cs` gives a diffuse head.
        """
        half = window / 2
        return any(abs(item.position - position) <= half for item in items)


#: A shuffled, seeded feed, the same argument as `feed_pattern` makes for the
#: sorting line: an order a controller can guess is an order it can be written
#: against. Every scene that feeds more than one kind of carton draws from one
#: of these rather than from an alternation.
def shuffled_cycle(rng: random.Random, values: list, repeats: int) -> list:
    out: list = []
    for _ in range(repeats):
        block = list(values)
        rng.shuffle(block)
        out.extend(block)
    return out


# --- start / stop station ----------------------------------------------
#
# Observable fact: how many cartons physically crossed the part-present eye
# between the Start press and the line stopping itself, and how far the belt
# travelled while the station was tripped.
#
# How a program fakes it: by running the belt for about the right length of
# time. A batch of five at a fixed feed rate is a stopwatch problem if the
# grader only ever asks for five. So the pot is a number drawn from the seed
# and the exam asks for it twice, with a different number the second time --
# and the count that decides the mark is crossings of the eye, which is a
# distance the belt really moved with a carton on it.
#
# The other half is the mushroom, and it is graded as belt travel: the line has
# to be off within 200 ms of the strike, has to stay off when the mushroom pops
# back out, has to ignore Start while latched, and has to come back only after
# Reset *and* Start. Every one of those is metres of belt, not the state of a
# lamp.

#: `engine/templates/start_stop_station.json`: belt speed 0.5 m/s, 3 m deck.
SS_BELT_SPEED = 0.5
SS_EYE_POS = 1.5
SS_EYE_WINDOW = 0.20
#: Where a carton *enters* the eye's window, which is the moment the beam
#: breaks and therefore the physical event a counter counts. Counting from the
#: middle of the window instead put the plant's ledger 0.1 m -- a fifth of a
#: second -- behind the sensor, so a correct controller that stopped the belt
#: on its fourth edge was marked as having made three.
SS_EYE_BREAK = SS_EYE_POS - SS_EYE_WINDOW / 2
SS_REMOVER_POS = 2.8
#: `docs/tag-bus.md` §4.2 and `tools/try_scene.py`: strike to stopped.
ESTOP_LIMIT = 0.200


class StartStopScene(PlantScene):
    name = "start-stop-station"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("produced.value", "Produced (Display)", "int", "output"),
            Tag("part_present.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("counter.count", "Remover (Count)", "int", "input"),
        )

        self.items: list[Item] = []
        self.removed: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        #: Ground truth. Crossings of the eye, stamped with the sim time, so a
        #: batch can be counted from the Start press that began it.
        self.crossings: list[float] = []
        #: Metres the belt moved while the station was tripped -- struck
        #: mushroom, or latched after one.
        self.travel_while_tripped = 0.0
        self.tripped_since: float | None = None
        self.stop_lag: float | None = None
        #: The batch the examiner asked for, and when it asked.
        self.batches: list[dict] = []

        big = 40                       # unreachable, so the interlocks have room
        self.batch_size = self.rng.choice([3, 4, 5])
        self.script = Script([
            (0.5, self.panel.set_setpoint(big)),
            (1.0, self.panel.press("start")),
            (12.0, self.panel.strike()),
            (14.0, self.panel.release()),
            (15.0, self.panel.press("start")),     # must not restart: still latched
            (17.0, self.panel.press("reset")),
            (18.5, self.panel.press("start")),     # this one must
            (24.0, self.panel.press("stop")),
            (26.0, self.panel.press("reset")),
            (26.5, self.panel.set_setpoint(self.batch_size)),
            (27.0, self._begin_batch),
        ])

    def _begin_batch(self) -> None:
        self.panel.press("start")()
        self.batches.append({"target": self.batch_size, "from": self.t,
                             "crossings_at": len(self.crossings)})

    # --- the plant ---

    def step(self, dt: float) -> None:
        tripped = not self.panel.healthy
        if tripped and self.tripped_since is None:
            self.tripped_since = self.t
        elif not tripped and self.tripped_since is not None and self.panel.started_since(
                self.tripped_since):
            self.tripped_since = None

        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        running = self.bit("belt.rotate")
        if running:
            moved = SS_BELT_SPEED * dt
            if self.tripped_since is not None:
                self.travel_while_tripped += moved
                if self.stop_lag is None and not self.panel.healthy:
                    self.stop_lag = self.t - self.tripped_since
            for item in self.items:
                before = item.position
                item.position += moved
                if before < SS_EYE_BREAK <= item.position:
                    self.crossings.append(self.t)
        elif self.tripped_since is not None and self.stop_lag is None and not self.panel.healthy:
            self.stop_lag = self.t - self.tripped_since

        still = []
        for item in self.items:
            if item.position >= SS_REMOVER_POS:
                self.removed.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("part_present.detect",
                      self._eye(self.items, SS_EYE_POS, SS_EYE_WINDOW))
        self.tags.set("counter.count", len(self.removed))


def grade_start_stop(watched: Watched, engine: GradedEngine, report: Report,
                     duration: float) -> None:
    sim: StartStopScene = watched.inner
    batch = sim.batches[0] if sim.batches else None
    made = len(sim.crossings) - batch["crossings_at"] if batch else 0
    target = batch["target"] if batch else sim.batch_size
    # Anything that crossed the eye more than four seconds after the target was
    # met is a line that did not stop itself.
    overrun = 0
    if batch:
        hits = sim.crossings[batch["crossings_at"]:]
        if len(hits) >= target:
            at_target = hits[target - 1]
            overrun = sum(1 for h in hits if h > at_target + 4.0)

    report.evidence.update({
        "batch": {"target": target, "made": made, "overrun": overrun},
        "crossings": len(sim.crossings),
        "removed": len(sim.removed),
        "estop": {
            "travel_while_tripped_m": round(sim.travel_while_tripped, 3),
            "allowed_m": round(SS_BELT_SPEED * ESTOP_LIMIT, 3),
            "stop_lag_s": None if sim.stop_lag is None else round(sim.stop_lag, 3),
        },
        "belt_running_fraction": round(watched.held_true("belt.rotate"), 3),
        "presses": sim.panel.presses,
        "produced_display": sim.num("produced.value"),
    })

    allowed = SS_BELT_SPEED * ESTOP_LIMIT
    report.add("line.ran",
               len(sim.removed) >= 4,
               f"{len(sim.removed)} cartons reached the far end (at least 4 needed)")
    report.add("estop.stopped_the_belt",
               sim.travel_while_tripped <= allowed,
               f"the belt moved {sim.travel_while_tripped * 1000:.0f} mm while the "
               f"station was tripped (at most {allowed * 1000:.0f} mm, which is "
               f"{ESTOP_LIMIT * 1000:.0f} ms of belt)")
    report.add("batch.hit_the_number",
               made == target,
               f"the batch made {made} against a pot of {target}")
    report.add("batch.stopped_itself",
               overrun == 0,
               "the line stopped itself at the target" if not overrun
               else f"{overrun} more carton(s) went past the eye after the target was met")

    _start_stop_feedback(report, watched, sim, made, target, overrun, allowed)


def _start_stop_feedback(report, watched, sim, made, target, overrun, allowed) -> None:
    say = report.feedback.append
    belt = watched.held_true("belt.rotate")

    if belt == 0.0:
        say("The belt never ran. `belt.rotate` is a PLC output and nothing "
            "downstream matters until your program writes it.")
    if not sim.removed and belt > 0:
        say("The belt ran but nothing reached the far end. `emitter.emit` makes "
            "one carton on each RISING edge -- holding it true makes exactly one.")

    if sim.travel_while_tripped > allowed:
        say(f"The belt kept moving with the station tripped -- "
            f"{sim.travel_while_tripped * 1000:.0f} mm of it. `panel.estop` is "
            f"NORMALLY CLOSED: true means healthy, so the mushroom reads FALSE. "
            f"And the trip has to latch: releasing the mushroom must not restart "
            f"anything, and Start must do nothing until Reset has cleared it.")

    if made > target:
        say(f"The batch overran: {made} cartons for a pot of {target}. The pot is "
            f"read fresh, not latched at power-up -- this run set it twice.")
    elif made < target and belt > 0:
        say(f"The batch stopped {target - made} short of the pot. Count the "
            f"RISING edge of `part_present.detect`; it stays true for as long as "
            f"a carton sits in the beam, which at this belt speed is many scans.")
    elif overrun:
        say("The line reached its target and carried on. At the target it has to "
            "stop itself -- nobody presses Stop for it.")
    elif made == target and sim.travel_while_tripped <= allowed:
        say(f"The batch landed exactly: {made} of {target}, and the line stopped "
            f"itself. The E-stop held the belt inside "
            f"{ESTOP_LIMIT * 1000:.0f} ms and Start would not clear the latch.")


def _summary_start_stop(evidence: dict, out) -> None:
    batch, estop = evidence["batch"], evidence["estop"]
    out(f"batch of {batch['target']}: made {batch['made']}, "
        f"{batch['overrun']} past the target")
    out(f"{evidence['crossings']} cartons crossed the eye, "
        f"{evidence['removed']} reached the far end")
    out(f"belt travel while tripped {estop['travel_while_tripped_m'] * 1000:.0f} mm "
        f"(at most {estop['allowed_m'] * 1000:.0f} mm)")


# --- the two regulators -------------------------------------------------
#
# The tank and the oven are the same exercise against two different plants, and
# they are graded by the same three numbers, because "it reached the setpoint"
# is the claim both of them are easiest to fake.
#
# **Settled error.** The mean distance from the setpoint over the last seconds
# of a phase. Proportional control alone cannot make a standing output out of
# nothing, so it parks short of an oven's setpoint by an offset you can
# calculate from the plant -- and that offset is the whole reason integral
# action exists. Grading "did it get there" would pass it.
#
# **Ripple.** Peak to peak over the same window. An on/off controller does
# reach the setpoint. It reaches it every couple of seconds, from alternate
# sides, and a plant that loses heat to the room will do that forever. A mean
# error near zero says nothing about it and the peak-to-peak says everything.
#
# **Overshoot.** The furthest past the setpoint the measurement went on the way
# there. A controller that gets a beautiful settled number by slamming the
# plant to the far stop first is one that boils the tank dry on a real line.
#
# And the pot moves mid-run, to a second value drawn from the seed. Everything
# above can be had by a program that knows what number it is aiming at; none of
# it can be had by one that only knows the number it was written with.

#: Seconds at the end of a phase that count as "settled".
SETTLE_WINDOW = 8.0


class Regulator(PlantScene):
    """A single-measurement process with a setpoint on the panel's pot."""

    #: Filled in by the subclass: what the measurement is called, in words.
    measured = "the measurement"
    unit = ""

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        #: (sim time, measurement) every tick. Ground truth: no bus message
        #: reaches the trace, only the samples the controller happened to poll.
        self.trace: list[tuple[float, float]] = []
        #: {"setpoint", "from", "to"} per phase of the exam.
        self.phases: list[dict] = []

    def measure(self) -> float:                   # pragma: no cover - overridden
        raise NotImplementedError

    def _phase(self, setpoint: float, until: float):
        def do() -> None:
            if self.phases:
                self.phases[-1]["to"] = self.t
            self.panel.set_setpoint(setpoint)()
            self.phases.append({"setpoint": float(setpoint), "from": self.t,
                                "to": until})
        do.__name__ = f"setpoint {setpoint:g}"
        return do

    def record(self) -> None:
        self.trace.append((self.t, self.measure()))


def _phase_stats(trace: list[tuple[float, float]], phase: dict) -> dict:
    """Settled error, ripple and overshoot for one phase of a regulator run."""
    setpoint = phase["setpoint"]
    inside = [(t, v) for t, v in trace if phase["from"] <= t <= phase["to"]]
    if not inside:
        return {"setpoint": setpoint, "samples": 0, "settled_error": None,
                "ripple": None, "overshoot": None, "final": None}

    # The last phase of a run has no end until the run ends, so its nominal
    # `to` is far in the future. Clamped to the last sample, because the
    # settled window is measured backwards from the end: the first version of
    # this took "the last eight seconds" of a phase ending in the year 10000,
    # found nothing, and fell back to a single sample -- which has a settled
    # error of whatever that sample was and a ripple of exactly zero. Every
    # second-phase check in the run was being decided by one number.
    end = min(phase["to"], inside[-1][0])
    start = inside[0][1]
    approach = 1.0 if setpoint >= start else -1.0
    tail = [v for t, v in inside if t >= end - SETTLE_WINDOW]
    tail = tail or [inside[-1][1]]
    past = max((v - setpoint) * approach for _, v in inside)
    return {
        "setpoint": round(setpoint, 2),
        "samples": len(inside),
        "from": round(phase["from"], 1),
        "to": round(end, 1),
        "started_at": round(start, 2),
        "settled_error": round(sum(abs(v - setpoint) for v in tail) / len(tail), 2),
        "ripple": round(max(tail) - min(tail), 2),
        "overshoot": round(max(past, 0.0), 2),
        "final": round(inside[-1][1], 2),
    }


def grade_regulator(watched: Watched, engine: GradedEngine, report: Report,
                    duration: float, *, settled: float, ripple: float,
                    overshoot: float, moved: float) -> None:
    sim: Regulator = watched.inner
    stats = [_phase_stats(sim.trace, phase) for phase in sim.phases]
    values = [v for _, v in sim.trace]
    span = (max(values) - min(values)) if values else 0.0
    unit = sim.unit

    report.evidence.update({
        "phases": stats,
        "travel": round(span, 2),
        "limits": {"settled": settled, "ripple": ripple, "overshoot": overshoot},
        "trace": [[round(t, 1), round(v, 2)] for t, v in sim.trace[::50]],
    })

    # Gotcha 16, in its process-control form: every settling check below is
    # vacuously true of a plant that never left where it started.
    report.add("plant.moved",
               span >= moved,
               f"{sim.measured} travelled {span:.1f}{unit} over the run "
               f"(at least {moved:g}{unit} needed for the rest to mean anything)")

    for index, phase in enumerate(stats, start=1):
        sp = phase["setpoint"]
        if phase["settled_error"] is None:
            report.add(f"hold{index}.settled", False,
                       f"no samples in the phase at {sp:g}{unit}")
            continue
        report.add(f"hold{index}.settled",
                   phase["settled_error"] <= settled,
                   f"at {sp:g}{unit}: settled {phase['settled_error']:.1f}{unit} "
                   f"from setpoint over the last {SETTLE_WINDOW:g}s "
                   f"(at most {settled:g}{unit})")
        report.add(f"hold{index}.steady",
                   phase["ripple"] <= ripple,
                   f"at {sp:g}{unit}: {phase['ripple']:.1f}{unit} peak to peak "
                   f"while holding (at most {ripple:g}{unit})")
        report.add(f"hold{index}.overshoot",
                   phase["overshoot"] <= overshoot,
                   f"at {sp:g}{unit}: went {phase['overshoot']:.1f}{unit} past the "
                   f"setpoint on the way (at most {overshoot:g}{unit})")

    _regulator_feedback(report, sim, stats, settled, ripple, overshoot, span, moved)


def _regulator_feedback(report, sim, stats, settled, ripple, overshoot,
                        span, moved) -> None:
    say = report.feedback.append
    unit = sim.unit

    if span < moved:
        say(f"{sim.measured.capitalize()} barely moved ({span:.1f}{unit}). Nothing "
            f"below this line means anything until the plant is actually being "
            f"driven -- check that the run command and the actuator are both "
            f"getting written.")
        return

    # Said first, and once, because it explains every other number below it:
    # the pot moved a long way and the measurement did not follow.
    deaf = (len(stats) > 1 and stats[0]["final"] is not None
            and stats[1]["final"] is not None
            and abs(stats[1]["setpoint"] - stats[0]["setpoint"]) > 10.0
            and abs(stats[1]["final"] - stats[0]["final"]) < 5.0)
    if deaf:
        say(f"The pot went from {stats[0]['setpoint']:g}{unit} to "
            f"{stats[1]['setpoint']:g}{unit} and {sim.measured} stayed at "
            f"{stats[1]['final']:g}{unit}. That is a setpoint written into the "
            f"program rather than read off `panel.setpoint` -- read it every "
            f"scan, not once at startup, and turning the knob re-tunes the line "
            f"instead of needing a download.")

    for index, phase in enumerate(stats, start=1):
        if phase["settled_error"] is None:
            continue
        sp = phase["setpoint"]
        if deaf:
            continue
        if phase["ripple"] > ripple:
            say(f"At {sp:g}{unit} the measurement swung {phase['ripple']:.1f}{unit} "
                f"peak to peak. It reaches the setpoint -- from alternate sides, "
                f"forever. On/off is not control here: the actuator modulates, so "
                f"write it a number between 0 and 100 instead of an edge.")
        elif phase["settled_error"] > settled:
            say(f"At {sp:g}{unit} it parked {phase['settled_error']:.1f}{unit} off "
                f"and stayed there. An error that stops closing is a controller "
                f"with no way to produce output from a small error -- either a "
                f"deadband that stops it acting once it is near, or proportional "
                f"action on its own, whose output IS the error times the gain and "
                f"so cannot reach zero while the plant still needs an output. "
                f"Integral action is the term that supplies one out of nothing.")
        if phase["overshoot"] > overshoot:
            say(f"At {sp:g}{unit} it went {phase['overshoot']:.1f}{unit} past the "
                f"setpoint before coming back. Full output until the setpoint "
                f"arrives is a plant with no brakes; back the actuator off as the "
                f"error closes.")

    if len(stats) > 1 and stats[0]["settled_error"] is not None \
            and stats[1]["settled_error"] is not None \
            and stats[0]["settled_error"] <= settled < stats[1]["settled_error"]:
        say(f"The first setpoint was held and the second was not. `panel.setpoint` "
            f"is the pot, and this run turned it: read it every scan rather than "
            f"latching it at startup or writing the number into the program.")


def _summary_regulator(evidence: dict, out) -> None:
    for index, phase in enumerate(evidence["phases"], start=1):
        if phase["settled_error"] is None:
            out(f"hold {index}: setpoint {phase['setpoint']:g} — no samples")
            continue
        out(f"hold {index}: setpoint {phase['setpoint']:g}, ended {phase['final']:g}, "
            f"settled {phase['settled_error']:g} off, ripple {phase['ripple']:g}, "
            f"overshoot {phase['overshoot']:g}")
    out(f"measurement travelled {evidence['travel']:g} over the run")


# --- tank level control -------------------------------------------------
#
# Observable fact: the level trace, which the plant integrates and no bus
# message reaches.
#
# How a program fakes it: "the level reached the setpoint" is true of a pair of
# float switches, of a valve slammed fully open until the number arrives, and
# of a program with 70 written into it. All three are graded out -- by the
# ripple, by the overshoot, and by the pot moving to a second level drawn from
# the seed.
#
# Outflow follows Torricelli, so the drain valve's authority grows with the
# square root of the head and a controller tuned at the top of the tank behaves
# differently at the bottom. That is why the second setpoint is a low one.
#
# Numbers from `engine/src/Parts/LevelTank.cs` and the template that configures
# it: 18 %/s at a fully open fill valve, 22 %/s draining a full tank.
TANK_FILL_RATE = 18.0
TANK_DRAIN_RATE = 22.0


class TankScene(Regulator):
    name = "tank-level-control"
    measured = "the level"
    unit = "%"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("tank.fill", "Tank Fill Valve (%)", "float", "output"),
            Tag("tank.drain", "Tank Drain Valve (%)", "float", "output"),
            Tag("level_readout.value", "Level Readout", "int", "output"),
            Tag("tank.level", "Tank Level (%)", "float", "input"),
            Tag("tank.fault", "Tank Valve Fault", "bit", "input"),
        )
        self.level = 0.0

        high = self.rng.choice([65.0, 70.0, 75.0])
        low = self.rng.choice([18.0, 22.0, 26.0])
        self.script = Script([
            (0.2, self._phase(high, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(low, until=10_000.0)),
        ])

    def measure(self) -> float:
        return self.level

    def step(self, dt: float) -> None:
        fill = min(max(self.num("tank.fill"), 0.0), 100.0)
        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        inflow = TANK_FILL_RATE * fill / 100.0
        outflow = (TANK_DRAIN_RATE * drain / 100.0
                   * (max(self.level, 0.0) / 100.0) ** 0.5)
        self.level = min(max(self.level + (inflow - outflow) * dt, 0.0), 100.0)
        self.tags.set("tank.level", self.level)
        self.record()


def grade_tank(watched, engine, report, duration) -> None:
    # A few percent, which is what the scene's own brief asks for. Ripple is
    # tighter than settled error on purpose: a controller 3 % off is mistuned,
    # while one swinging 3 % peak to peak is cycling a valve that has to last.
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=3.0, overshoot=6.0, moved=40.0)


# --- heat treat station -------------------------------------------------
#
# Observable fact: the temperature trace.
#
# How a program fakes it: the same three ways as the tank, plus one this plant
# alone can catch. The plate loses heat to the room in proportion to how far
# above it the plate is, so *holding* a temperature needs a standing heater
# output -- and a proportional controller can only make a standing output out
# of a standing error. Grade "did it reach the setpoint" and P-only passes on
# the way past. Grade the settled error and it parks, measurably, exactly the
# offset the plant's own numbers predict: at 120 degC the plate loses
# (120-20)*0.30 = 30 degC/s of heat, which is 33 % of a 90 degC/s element, and
# a gain of 3.5 can only produce 33 % from an error of 9.5 degC.
#
# Numbers from `engine/src/Parts/HeatingStation.cs` and the template: a 90
# degC/s element, a loss of 0.30 per degC above a 20 degC room, a thermal mass
# of 6. That is a first-order lag with a 20-second time constant.
OVEN_POWER = 90.0
OVEN_LOSS = 0.30
OVEN_MASS = 6.0
OVEN_AMBIENT = 20.0
#: The part's own at-temperature window, which is about its configured target
#: and not about the pot -- exactly as the engine has it.
OVEN_TARGET = 180.0
OVEN_TOLERANCE = 3.0


class OvenScene(Regulator):
    name = "heat-treat-station"
    measured = "the plate"
    unit = "C"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("oven.heater", "Heating Station Heater (%)", "float", "output"),
            Tag("temp_gauge.value", "Temperature Gauge", "float", "output"),
            Tag("temp_readout.value", "Temperature Readout", "int", "output"),
            Tag("alarm.beacon", "Alarm Beacon", "bit", "output"),
            Tag("alarm.horn", "Alarm Horn", "bit", "output"),
            Tag("oven.temperature", "Heating Station Temperature (C)",
                "float", "input"),
            Tag("oven.attemp", "Heating Station At Temperature", "bit", "input"),
            Tag("oven.fault", "Heating Station Element Fault", "bit", "input"),
        )
        self.temperature = OVEN_AMBIENT
        self.tags.set("oven.temperature", self.temperature)

        first = self.rng.choice([115.0, 125.0, 135.0])
        second = self.rng.choice([190.0, 200.0, 210.0])
        self.script = Script([
            (0.2, self._phase(first, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(second, until=10_000.0)),
        ])

    def measure(self) -> float:
        return self.temperature

    def step(self, dt: float) -> None:
        power = min(max(self.num("oven.heater"), 0.0), 100.0)
        heat = OVEN_POWER * power / 100.0
        loss = (self.temperature - OVEN_AMBIENT) * OVEN_LOSS
        self.temperature = max(self.temperature + (heat - loss) / OVEN_MASS * dt,
                               OVEN_AMBIENT)
        self.tags.set("oven.temperature", self.temperature)
        self.tags.set("oven.attemp",
                      abs(self.temperature - OVEN_TARGET) <= OVEN_TOLERANCE)
        self.record()


def grade_oven(watched, engine, report, duration) -> None:
    # 3 degC settled, against a P-only offset of 9.5 degC at the first setpoint
    # and 16 degC at the second: the margin is wide enough that a well-tuned
    # proportional-only loop still fails, which is the point of the scene.
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=5.0, overshoot=12.0, moved=80.0)


# --- light curtain sorting ----------------------------------------------
#
# Observable fact: each carton's true height, and which lane it ended in. The
# scene made the carton, so it knows; nothing on the bus carries either.
#
# How a program fakes it: two ways, and both are shut.
#
# A program that never reads the curtain can sort an alternating feed by
# pushing every second carton, so the feed is drawn from a shuffled cycle of
# eight different heights rather than from two.
#
# A program with the threshold written into it sorts perfectly at one setting,
# which is the whole difference between this scene and `sorting-by-height` --
# there the rule is two bits of wiring, here it is a number on the pot. So the
# pot is set to one value, and then to another, and every carton is marked
# against the rule that was in force when the curtain measured *it*. A carton
# in flight when the knob turned is judged by the old rule, which is what a
# real line does and what makes the check fair.
#
# Geometry and the curtain from `engine/templates/light_curtain_sorting.json`:
# a 12-beam array over 0.48 m, a 0.55 m diverter stroke at 1.83 m/s.
LC_BELT_SPEED = 0.5
LC_CURTAIN_POS = 1.2
LC_DIVERTER_POS = 2.2
LC_REMOVER_POS = 3.0
LC_CATCH = 0.15
LC_TRAVEL_TIME = 0.55 / 1.83
LC_BEAMS = 12
LC_CURTAIN_HEIGHT = 0.48


def lc_beam_ladder() -> list[float]:
    """Where each beam sits above the belt.

    `LightArray.cs`: the lowest beam is 20 mm up so the shortest carton still
    breaks one, and the rest are spread to the top of the curtain. The array
    reports the height of the *highest blocked beam*, so a carton's measured
    height is a rung of this ladder and never its true height -- which is why
    the thresholds below sit between rungs and never on one.
    """
    return [0.02 + (LC_CURTAIN_HEIGHT - 0.02) * i / (LC_BEAMS - 1)
            for i in range(LC_BEAMS)]


class LightCurtainScene(PlantScene):
    name = "light-curtain-sorting"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("diverter.extend", "Diverter (Extend)", "bit", "output"),
            Tag("height_gauge.height", "Light Array Height (m)", "float", "input"),
            Tag("height_gauge.blocked", "Light Array Blocked", "bit", "input"),
            Tag("diverter.extended", "Diverter (Extended)", "bit", "input"),
            Tag("diverter.retracted", "Diverter (Retracted)", "bit", "input",
                value=True),
            Tag("tall_count.count", "Chute Remover (Count)", "int", "input"),
            Tag("short_count.count", "Far End Remover (Count)", "int", "input"),
        )

        ladder = lc_beam_ladder()
        #: Eight heights, each sitting just above a rung so the curtain reports
        #: that rung exactly. Shuffled in blocks, so no eight consecutive
        #: cartons are an order a program could have been written against.
        self._rungs = list(range(1, 9))
        self.feed = shuffled_cycle(self.rng, self._rungs, 6)
        self._fed = 0
        self.ladder = ladder

        # Two thresholds, each halfway between two rungs, so no carton is ever
        # a tie and a boundary case is never the reason for a mark.
        first, second = self.rng.sample([3, 4, 5, 6], 2)
        self.thresholds = [(ladder[first] + ladder[first - 1]) / 2,
                           (ladder[second] + ladder[second - 1]) / 2]
        self.script = Script([
            (0.2, self.panel.set_setpoint(round(self.thresholds[0], 4))),
            (1.0, self.panel.press("start")),
            (32.0, self.panel.set_setpoint(round(self.thresholds[1], 4))),
        ])

        self.items: list[Item] = []
        self.sorted_items: list[Item] = []
        self._next_id = 1
        self._emit_edge = False
        self.extension = 0.0

    # --- the plant ---

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            rung = self.feed[self._fed % len(self.feed)]
            self._fed += 1
            self.items.append(Item(height=self.ladder[rung] + 0.005,
                                   id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        if self.bit("belt.rotate"):
            for item in self.items:
                item.position += LC_BELT_SPEED * dt

        target = 1.0 if self.bit("diverter.extend") else 0.0
        rate = dt / LC_TRAVEL_TIME
        self.extension = min(self.extension + rate, target) if target > self.extension \
            else max(self.extension - rate, target)
        self.tags.set("diverter.extended", self.extension >= 0.999)
        self.tags.set("diverter.retracted", self.extension <= 0.001)

        # The curtain. Measured once, on the beam break, and stamped with the
        # rule that was in force at that moment.
        in_curtain = [i for i in self.items
                      if abs(i.position - LC_CURTAIN_POS) <= 0.10]
        if in_curtain:
            tallest = max(in_curtain, key=lambda i: i.height)
            rung = max(y for y in self.ladder if y <= tallest.height)
            self.tags.set("height_gauge.height", rung)
            self.tags.set("height_gauge.blocked", True)
            for item in in_curtain:
                if item.measured is None:
                    item.measured = max(y for y in self.ladder if y <= item.height)
                    item.threshold = float(self.panel.setpoint_value)
        else:
            self.tags.set("height_gauge.blocked", False)
            self.tags.set("height_gauge.height", 0.0)

        still: list[Item] = []
        for item in self.items:
            if (self.extension > 0.5
                    and abs(item.position - LC_DIVERTER_POS) <= LC_CATCH):
                item.lane = "chute"
                item.carried = True
                self.sorted_items.append(item)
            elif item.position >= LC_REMOVER_POS:
                item.lane = "far-end"
                self.sorted_items.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("tall_count.count",
                      sum(1 for i in self.sorted_items if i.lane == "chute"))
        self.tags.set("short_count.count",
                      sum(1 for i in self.sorted_items if i.lane == "far-end"))


def grade_light_curtain(watched: Watched, engine: GradedEngine, report: Report,
                        duration: float) -> None:
    sim: LightCurtainScene = watched.inner
    judged = [i for i in sim.sorted_items if i.measured is not None]
    unmeasured = [i for i in sim.sorted_items if i.measured is None]
    chute = [i for i in sim.sorted_items if i.lane == "chute"]
    far = [i for i in sim.sorted_items if i.lane == "far-end"]

    wrong = [i for i in judged
             if (i.measured >= i.threshold) != (i.lane == "chute")]
    rules = sorted({round(i.threshold, 4) for i in judged})
    per_rule = {f"{r:.3f}": sum(1 for i in judged if abs(i.threshold - r) < 1e-6)
                for r in rules}

    report.evidence.update({
        "fed": sim._fed,
        "sorted": len(sim.sorted_items),
        "still_on_belt": len(sim.items),
        "chute": len(chute),
        "far_end": len(far),
        "thresholds_seen": per_rule,
        "misrouted": [{"carton": i.id, "measured_m": round(i.measured, 3),
                       "threshold_m": round(i.threshold, 3), "lane": i.lane}
                      for i in wrong][:20],
        "unmeasured": len(unmeasured),
        "diverter_out_fraction": round(watched.held_true("diverter.extend"), 3),
    })

    report.add("line.ran",
               len(sim.sorted_items) >= 8,
               f"{len(sim.sorted_items)} cartons reached a lane (at least 8)")
    report.add("line.both_lanes",
               len(chute) >= 2 and len(far) >= 2,
               f"chute {len(chute)}, far end {len(far)} (at least 2 each)")
    report.add("rule.both_settings_tested",
               len(per_rule) >= 2 and min(per_rule.values()) >= 2,
               f"cartons measured under each threshold: "
               f"{', '.join(f'{k}m x{v}' for k, v in per_rule.items())}")
    report.add("sort.followed_the_measurement",
               not wrong,
               "every carton went to the lane its measured height asked for"
               if not wrong else
               f"{len(wrong)} carton(s) went the wrong way: "
               f"{[i.id for i in wrong][:10]}")
    report.add("line.conservation",
               sim._fed == len(sim.sorted_items) + len(sim.items),
               f"{sim._fed} fed = {len(sim.sorted_items)} sorted + "
               f"{len(sim.items)} still on the belt")

    _light_curtain_feedback(report, watched, sim, wrong, per_rule)


def _light_curtain_feedback(report, watched, sim, wrong, per_rule) -> None:
    say = report.feedback.append
    belt = watched.held_true("belt.rotate")
    out = watched.held_true("diverter.extend")

    if belt == 0.0:
        say("The belt never ran. `belt.rotate` is yours to write.")
        return
    if sim._fed == 0:
        say("No cartons were fed. `emitter.emit` makes one on each RISING edge.")
        return
    if out == 0.0:
        say("The diverter never came out, so everything went past. "
            "`height_gauge.height` is a measurement in metres and "
            "`panel.setpoint` is the threshold to compare it against.")
    elif out > 0.85:
        say("The diverter was held out for nearly the whole run, so it swept "
            "everything into the chute. It has to come back for the short ones.")

    if len(per_rule) < 2:
        say("The run turned the pot to a second threshold part way through and "
            "not enough cartons were measured afterwards to mark it. If the line "
            "stopped or the feed stopped, that is why.")

    if wrong:
        by_rule: dict[float, int] = {}
        for item in wrong:
            by_rule[round(item.threshold, 3)] = by_rule.get(round(item.threshold, 3), 0) + 1
        if len(by_rule) == 1 and len(per_rule) > 1:
            only = next(iter(by_rule))
            say(f"Every misrouted carton was measured while the pot read "
                f"{only:.3f} m, and the ones under the other setting were all "
                f"correct. That is a threshold written into the program: read "
                f"`panel.setpoint` at the moment you measure each carton, not "
                f"once at startup.")
        else:
            worst = wrong[0]
            say(f"Carton {worst.id} measured {worst.measured:.3f} m against a "
                f"threshold of {worst.threshold:.3f} m and went to the "
                f"{worst.lane}. The curtain reports the highest beam it lost, so "
                f"the measurement is a rung of a 12-beam ladder over "
                f"{LC_CURTAIN_HEIGHT:g} m -- compare that number, not the bit.")
    elif len(sim.sorted_items) >= 8:
        say(f"Sorting was clean at both thresholds: "
            f"{', '.join(f'{k} m for {v} cartons' for k, v in per_rule.items())}, "
            f"none misrouted.")


def _summary_light_curtain(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, sorted {evidence['sorted']}, "
        f"{evidence['still_on_belt']} still on the belt")
    out(f"chute {evidence['chute']}, far end {evidence['far_end']}")
    out("thresholds the run used: "
        + ", ".join(f"{k} m for {v} cartons"
                    for k, v in evidence["thresholds_seen"].items()))
    for entry in evidence["misrouted"][:8]:
        out(f"  carton {entry['carton']:>3} measured {entry['measured_m']:.3f} m "
            f"against {entry['threshold_m']:.3f} m -> {entry['lane']}")


# --- roller line with weighing ------------------------------------------
#
# Observable fact: what each carton actually weighs, and whether it ever shared
# the deck with another one. The scene made them, so it knows both.
#
# How a program fakes it: by rejecting on the inductive sensor instead of the
# scale. The brief says the steel cartons are also the heavy ones, so metal and
# over-limit agree -- at one limit. They stop agreeing the moment the limit
# drops below a tall cardboard carton, which weighs 2160 g where a short steel
# one weighs 4320, and the exam moves it there. A program that flags metal
# passes the first half of the run and misses every tall carton in the second.
#
# The other half is the deck itself. Two cartons on a checkweigher read as one
# peak, and that is a real property of real checkweighers rather than a quirk
# here: the plant records whether each carton was ever weighed alongside
# another, and a line that feeds without holding fails on that regardless of
# what it did with the number.
#
# Masses from `BoxPhysics.cs`: 0.20 x H x 0.24 at 150 kg/m3 for cardboard and
# 900 for steel, so the four classes are 720 g, 2160 g, 4320 g and 12960 g.
RW_SPEED = 0.4
RW_METAL_EYE_POS = 1.2
RW_DECK_FROM = 2.0
RW_DECK_TO = 3.0
RW_REMOVER_POS = 3.3
#: How long after a carton rolls off the deck the controller has to have made
#: its mind up. Generous: a scan plus a network round trip.
RW_VERDICT_WINDOW = 0.6


class RollerWeighScene(PlantScene):
    name = "roller-line-weighing"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("infeed.rotate", "Roller Conveyor (Rotate)", "bit", "output"),
            Tag("scale.rotate", "Weighing Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("weight_readout.value", "Weight Readout", "int", "output"),
            Tag("scale.weight", "Weighing Conveyor Weight (g)", "int", "input"),
            Tag("metal_check.detect", "Inductive Sensor (Detect)", "bit", "input"),
            Tag("outfeed.count", "Remover (Count)", "int", "input"),
        )

        #: All four mass classes, shuffled in blocks of four rather than
        #: emitted on the template's `metal_every: 3` cadence. Two reasons, and
        #: the first is the same one the sorting line's feed has: an order a
        #: program can guess is an order it can be written against. The second
        #: is fairness the other way. The carton the two instruments disagree
        #: about is the tall cardboard one -- 2160 g, over a 1500 g limit and
        #: invisible to an inductive sensor -- and a block of four guarantees
        #: one, so `metalonly` cannot pass on a lucky draw.
        self.feed = shuffled_cycle(
            self.rng, [(True, False), (False, False), (True, True), (False, True)], 8)
        self._fed = 0

        # 3000 g is above every cardboard carton and below every steel one, so
        # metal and over-limit agree. 1500 g is below the tall cardboard one,
        # so they stop agreeing. That is the whole exam.
        self.limits = [3000.0, 1500.0]
        self.script = Script([
            (0.2, self.panel.set_setpoint(self.limits[0])),
            (1.0, self.panel.press("start")),
            (30.0, self.panel.set_setpoint(self.limits[1])),
        ])

        self.items: list[Item] = []
        self.weighed: list[dict] = []
        self._watching: list[dict] = []
        self._next_id = 1
        self._emit_edge = False
        self._on_deck: dict[int, dict] = {}

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            tall, metal = self.feed[self._fed % len(self.feed)]
            self.items.append(Item(height=0.30 if tall else 0.10, metal=metal,
                                   id=self._next_id))
            self._fed += 1
            self._next_id += 1
        self._emit_edge = emit

        infeed = self.bit("infeed.rotate")
        deck = self.bit("scale.rotate")
        for item in self.items:
            moving = deck if RW_DECK_FROM <= item.position < RW_DECK_TO else infeed
            if moving:
                item.position += RW_SPEED * dt

        on_deck = [i for i in self.items
                   if RW_DECK_FROM <= i.position <= RW_DECK_TO]
        self.tags.set("scale.weight", int(round(sum(i.grams for i in on_deck))))
        self.tags.set("metal_check.detect",
                      any(i.metal for i in self.items
                          if abs(i.position - RW_METAL_EYE_POS) <= 0.10))

        # A carton's record opens when it reaches the deck and closes when it
        # leaves. `shared` is the plant's own answer to "was this weighed on
        # its own", which no instrument on the line reports.
        for item in on_deck:
            record = self._on_deck.get(item.id)
            if record is None:
                record = self._on_deck[item.id] = {
                    "carton": item.id, "grams": round(item.grams),
                    "metal": item.metal, "shared": False, "limit": None,
                    "flagged": False, "at": round(self.t, 2)}
            record["shared"] = record["shared"] or len(on_deck) > 1

        for item_id, record in list(self._on_deck.items()):
            if any(i.id == item_id for i in on_deck):
                continue
            del self._on_deck[item_id]
            record["limit"] = float(self.panel.setpoint_value)
            record["until"] = self.t + RW_VERDICT_WINDOW
            self.weighed.append(record)
            self._watching.append(record)

        # The controller's verdict: the red lamp, at any point in the window
        # after the carton rolls off. Held permanently it flags everything and
        # fails on the light ones; never lit it flags nothing and fails on the
        # heavy ones, so the rule is symmetric and neither shortcut survives.
        red = self.bit("panel.red")
        for record in list(self._watching):
            if red:
                record["flagged"] = True
            if self.t > record["until"]:
                self._watching.remove(record)

        still = []
        for item in self.items:
            if item.position >= RW_REMOVER_POS:
                item.lane = "outfeed"
            else:
                still.append(item)
        removed = len(self.items) - len(still)
        if removed:
            self.tags.set("outfeed.count",
                          int(self.tags.visible("outfeed.count")) + removed)
        self.items = still


def grade_roller_weighing(watched: Watched, engine: GradedEngine, report: Report,
                          duration: float) -> None:
    sim: RollerWeighScene = watched.inner
    judged = [r for r in sim.weighed if r["limit"] is not None]
    shared = [r for r in judged if r["shared"]]
    alone = [r for r in judged if not r["shared"]]
    # Judged against what the carton really weighs, and every carton, including
    # the ones that shared the deck. Restricting this to cartons weighed alone
    # made it vacuous for exactly the runs it most needed to catch: a line that
    # never singulates has no cartons weighed alone, so "every carton was
    # judged correctly" passed while nothing had been judged at all.
    wrong = [r for r in judged if (r["grams"] > r["limit"]) != r["flagged"]]
    limits = sorted({r["limit"] for r in judged})
    per_limit = {f"{int(l)}g": sum(1 for r in judged if r["limit"] == l)
                 for l in limits}
    # The cartons the two instruments disagree about: heavy cardboard. They are
    # the ones a metal-sensing program gets wrong, so they are worth naming.
    split = [r for r in judged if (r["grams"] > r["limit"]) != r["metal"]]

    report.evidence.update({
        "fed": sim._fed,
        "weighed": len(judged),
        "weighed_alone": len(alone),
        "shared_the_deck": len(shared),
        "limits_seen": per_limit,
        "misjudged": [{"carton": r["carton"], "grams": r["grams"],
                       "limit": int(r["limit"]), "flagged": r["flagged"],
                       "metal": r["metal"]} for r in wrong][:20],
        "metal_and_weight_disagree": len(split),
        "outfeed": int(sim.tags.visible("outfeed.count")),
        "red_held_fraction": round(watched.held_true("panel.red"), 3),
    })

    report.add("line.ran",
               len(judged) >= 8 and int(sim.tags.visible("outfeed.count")) >= 6,
               f"{len(judged)} cartons crossed the scale and "
               f"{int(sim.tags.visible('outfeed.count'))} reached the outfeed "
               f"(at least 8 and 6)")
    report.add("scale.singulated",
               not shared,
               "every carton was weighed on its own" if not shared else
               f"{len(shared)} carton(s) shared the deck with another, so the "
               f"scale read the pair as one peak: "
               f"{[r['carton'] for r in shared][:10]}")
    report.add("reject.judged_them_all",
               len(judged) >= 8,
               f"{len(judged)} cartons got a verdict (at least 8, so the check "
               f"below is about the judging and not about the sample size)")
    report.add("limit.both_settings_tested",
               len(per_limit) >= 2 and min(per_limit.values()) >= 2,
               f"cartons weighed under each limit: "
               f"{', '.join(f'{k} x{v}' for k, v in per_limit.items())}")
    report.add("reject.matched_the_weight",
               not wrong,
               "every carton over the limit was flagged and no other was"
               if not wrong else
               f"{len(wrong)} carton(s) judged wrong: "
               f"{[r['carton'] for r in wrong][:10]}")

    _roller_feedback(report, watched, sim, judged, alone, shared, wrong, split,
                     per_limit)


def _roller_feedback(report, watched, sim, judged, alone, shared, wrong, split,
                     per_limit) -> None:
    say = report.feedback.append
    red = watched.held_true("panel.red")

    if not judged:
        say("Nothing crossed the scale. `infeed.rotate` and `scale.rotate` are "
            "both yours, and `emitter.emit` makes one carton per RISING edge.")
        return

    if shared:
        say(f"{len(shared)} carton(s) were on the deck together. A checkweigher "
            f"weighs what is on it, so two cartons read as one peak and both "
            f"verdicts are guesses. Hold the feed while `scale.weight` is above "
            f"zero -- a real line singulates before it weighs.")

    if red > 0.9:
        say("The red lamp was lit for essentially the whole run, which flags "
            "every carton including the light ones.")
    elif red == 0.0:
        say("The red lamp never lit, so nothing was flagged. `panel.red` is how "
            "this exercise reports a reject.")

    if wrong:
        metal_shaped = [r for r in wrong if r["flagged"] == r["metal"]]
        if split and len(metal_shaped) >= len(wrong) * 0.8:
            say(f"Every carton you got wrong is one where the scale and the "
                f"inductive sensor disagree -- {len(split)} of them in this run. "
                f"A tall cardboard carton weighs 2160 g and a short steel one "
                f"4320 g, so at a limit of 1500 g the heavy ones are no longer "
                f"only the metal ones. Judge on `scale.weight`, and use "
                f"`metal_check.detect` as the second opinion it is.")
        else:
            worst = wrong[0]
            say(f"Carton {worst['carton']} weighed {worst['grams']} g against a "
                f"limit of {int(worst['limit'])} g and was "
                f"{'flagged' if worst['flagged'] else 'passed'}. Judge each "
                f"carton on the PEAK it showed while it was on the deck, not on "
                f"whatever the cell reads as it rolls off.")

    if len(per_limit) < 2:
        say("The run turned the pot to a second limit part way through and not "
            "enough cartons were weighed afterwards to mark it.")
    elif not wrong and not shared:
        say(f"Checkweighing was clean at both limits "
            f"({', '.join(per_limit)}), every carton weighed on its own, and the "
            f"{len(split)} carton(s) where mass and material disagree were "
            f"judged on the mass.")


def _summary_roller(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, weighed {evidence['weighed']} "
        f"({evidence['weighed_alone']} alone, {evidence['shared_the_deck']} sharing "
        f"the deck), {evidence['outfeed']} reached the outfeed")
    out("limits the run used: "
        + ", ".join(f"{k} for {v} cartons" for k, v in evidence["limits_seen"].items()))
    out(f"{evidence['metal_and_weight_disagree']} carton(s) where the scale and "
        f"the inductive sensor disagree")
    for entry in evidence["misjudged"][:8]:
        out(f"  carton {entry['carton']:>3} {entry['grams']:>6} g against "
            f"{entry['limit']} g: {'flagged' if entry['flagged'] else 'passed'}"
            f"{', metal' if entry['metal'] else ''}")


# --- accumulation buffer -------------------------------------------------
#
# Observable fact: how many cartons physically passed the blade stop, and in
# which release. The plant moves them and the plant counts them.
#
# How a program fakes it: by dropping the blade for a fixed number of seconds.
# At one belt speed that releases the right amount every time, and it is the
# obvious thing to write. So the exam reaches into the drive halfway through
# the run and *doubles the belt's top speed* -- a physical change no tag
# reports, visible only as the encoder counting faster. A release measured in
# pulses is a release measured in distance and lets out the same product; a
# release measured in seconds lets out twice as much.
#
# The other half is the blade itself: nothing may pass it while it is up, and
# the encoder has to have been counting the whole time, so "the line stopped"
# is ruled out as the explanation.
#
# Numbers from `engine/templates/accumulation_buffer.json`: 100 pulses per
# metre, a 0.26 m blade stroke at 2 m/s, a drive ramping at 60 %/s.
AB_BLADE_POS = 3.0
AB_EYE_POS = 3.15
AB_REMOVER_POS = 4.2
AB_PITCH = 0.22
AB_PULSES_PER_METRE = 100.0
AB_BLADE_TIME = 0.26 / 2.0
AB_RAMP = 60.0
#: The drive's top speed, before and after the exam changes it.
AB_SPEED_FIRST = 0.5
AB_SPEED_THEN = 1.0
AB_SPEED_CHANGES_AT = 40.0


class AccumulationScene(PlantScene):
    name = "accumulation-buffer"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("buffer.run", "VFD Conveyor (Run)", "bit", "output"),
            Tag("buffer.speed", "VFD Conveyor Speed Ref (%)", "float", "output"),
            Tag("outfeed.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("stop.raise", "Stop (Raise)", "bit", "output"),
            Tag("enc.reset", "Encoder (Reset)", "bit", "output"),
            Tag("count_display.value", "Released (Display)", "int", "output"),
            Tag("buffer.actual", "VFD Conveyor Actual Speed (%)", "float", "input"),
            Tag("enc.count", "Encoder Count", "int", "input"),
            Tag("stop.up", "Stop (Blade Up)", "bit", "input"),
            Tag("stop.down", "Stop (Blade Down)", "bit", "input", value=True),
            Tag("exit_eye.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("released.count", "Remover (Count)", "int", "input"),
        )

        self.max_speed = AB_SPEED_FIRST
        self.actual_percent = 0.0
        self.blade = 0.0                 # 0 down, 1 up
        self.pulses = 0.0
        self.items: list[Item] = []
        self.released: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        #: Ground truth. One entry per time the blade was down, with the
        #: cartons that got past during it and the drive speed at the time.
        self.releases: list[dict] = []
        self._open: dict | None = None
        #: Cartons that got past a raised blade, which must be none.
        self.escaped: list[int] = []
        #: Metres of belt travelled while the blade was up, so "nothing got
        #: past" cannot be satisfied by a line that was not running.
        self.travel_while_held = 0.0
        self.speed_samples: dict[str, list[float]] = {"first": [], "then": []}

        self.window = float(self.rng.choice([100, 120, 140]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.window)),
            (1.0, self.panel.press("start")),
            (AB_SPEED_CHANGES_AT, self._speed_up),
        ])

    def _speed_up(self) -> None:
        """Reach into the drive and change what 100 % means.

        The controller cannot read this anywhere. `buffer.speed` is its own
        command and `buffer.actual` is a percentage of a maximum it is not
        told -- the only thing that changes is how fast the encoder counts,
        which is exactly the instrument a release measured in distance uses
        and a release measured in seconds does not.
        """
        self.max_speed = AB_SPEED_THEN

    @property
    def phase(self) -> str:
        return "first" if self.t < AB_SPEED_CHANGES_AT else "then"

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        running = self.bit("buffer.run")
        reference = min(max(self.num("buffer.speed"), 0.0), 100.0) if running else 0.0
        self.actual_percent += max(min(reference - self.actual_percent,
                                       AB_RAMP * dt), -AB_RAMP * dt)
        speed = self.max_speed * self.actual_percent / 100.0 if running else 0.0
        self.tags.set("buffer.actual", self.actual_percent)
        self.speed_samples[self.phase].append(speed)

        target = 1.0 if self.bit("stop.raise") else 0.0
        rate = dt / AB_BLADE_TIME
        self.blade = (min(self.blade + rate, target) if target > self.blade
                      else max(self.blade - rate, target))
        up = self.blade >= 0.999
        self.tags.set("stop.up", up)
        self.tags.set("stop.down", self.blade <= 0.001)

        self.pulses += speed * AB_PULSES_PER_METRE * dt
        if self.bit("enc.reset"):
            self.pulses = 0.0
        self.tags.set("enc.count", int(self.pulses))

        moved = speed * dt
        if up:
            self.travel_while_held += moved

        # Queue behind the blade: each carton is stopped by whatever is in
        # front of it, and the leader by the blade when the blade is up.
        blocking = self.blade > 0.5
        ahead = None
        for item in sorted(self.items, key=lambda i: i.position, reverse=True):
            was = item.position
            limit = float("inf")
            if blocking and was < AB_BLADE_POS:
                limit = AB_BLADE_POS - 0.10
            if ahead is not None:
                limit = min(limit, ahead - AB_PITCH)
            item.position = min(item.position + moved, limit)
            ahead = item.position
            if was < AB_BLADE_POS <= item.position:
                if blocking:
                    self.escaped.append(item.id)
                elif self._open is not None:
                    self._open["cartons"] += 1

        if not blocking and self._open is None:
            self._open = {"from": round(self.t, 2), "phase": self.phase,
                          "cartons": 0, "pulses_at": self.pulses}
        elif blocking and self._open is not None:
            self._open["to"] = round(self.t, 2)
            self._open["pulses"] = round(self.pulses - self._open["pulses_at"], 1)
            self._open["seconds"] = round(self._open["to"] - self._open["from"], 2)
            self.releases.append(self._open)
            self._open = None

        self.tags.set("exit_eye.detect", self._eye(self.items, AB_EYE_POS))

        still = []
        for item in self.items:
            if item.position >= AB_REMOVER_POS:
                self.released.append(item)
            else:
                still.append(item)
        self.items = still
        self.tags.set("released.count", len(self.released))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def grade_accumulation(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: AccumulationScene = watched.inner
    # A release worth comparing is one that let product out. A blade flicked
    # down for a tenth of a second is not a release, and averaging it in would
    # flatter a controller that did the job once.
    real = [r for r in sim.releases if r["cartons"] >= 1]
    first = [r for r in real if r["phase"] == "first"]
    then = [r for r in real if r["phase"] == "then"]
    avg_first, avg_then = _mean([r["cartons"] for r in first]), \
        _mean([r["cartons"] for r in then])
    moving = [s for s in sim.speed_samples["first"] if s > 0.01]
    moving_then = [s for s in sim.speed_samples["then"] if s > 0.01]
    speed_first, speed_then = _mean(moving), _mean(moving_then)

    report.evidence.update({
        "pulse_window_on_the_pot": sim.window,
        "releases": real[-12:],
        "first_speed": {"releases": len(first), "mean_cartons": round(avg_first, 2),
                        "belt_m_per_s": round(speed_first, 3)},
        "second_speed": {"releases": len(then), "mean_cartons": round(avg_then, 2),
                         "belt_m_per_s": round(speed_then, 3)},
        "escaped_a_raised_blade": sim.escaped[:20],
        "belt_travel_while_held_m": round(sim.travel_while_held, 2),
        "released_total": len(sim.released),
        "blade_up_fraction": round(watched.held_true("stop.raise"), 3),
    })

    report.add("line.ran",
               len(sim.released) >= 6,
               f"{len(sim.released)} cartons reached the outfeed remover "
               f"(at least 6)")
    report.add("blade.held_everything",
               not sim.escaped,
               "nothing got past a raised blade" if not sim.escaped else
               f"{len(sim.escaped)} carton(s) passed a raised blade: "
               f"{sim.escaped[:10]}")
    report.add("blade.held_a_running_belt",
               sim.travel_while_held >= 3.0,
               f"{sim.travel_while_held:.1f} m of belt ran under a raised blade "
               f"(at least 3 m, so holding is the blade and not a stopped line)")
    report.add("release.happened_at_both_speeds",
               len(first) >= 1 and len(then) >= 1 and max(avg_first, avg_then) >= 2.0,
               f"{len(first)} release(s) at {speed_first:.2f} m/s and {len(then)} at "
               f"{speed_then:.2f} m/s, averaging {avg_first:.1f} and "
               f"{avg_then:.1f} cartons")
    if len(first) >= 1 and len(then) >= 1:
        report.add("release.same_size_at_both_speeds",
                   abs(avg_first - avg_then) <= 1.0,
                   f"{avg_first:.1f} cartons per release at {speed_first:.2f} m/s "
                   f"against {avg_then:.1f} at {speed_then:.2f} m/s "
                   f"(at most 1 apart)")

    _accumulation_feedback(report, watched, sim, first, then, avg_first, avg_then,
                           speed_first, speed_then)


def _accumulation_feedback(report, watched, sim, first, then, avg_first, avg_then,
                           speed_first, speed_then) -> None:
    say = report.feedback.append
    up = watched.held_true("stop.raise")

    if not sim.released:
        say("Nothing reached the outfeed. `buffer.run` and `outfeed.rotate` are "
            "yours, `buffer.speed` is a percentage reference, and "
            "`emitter.emit` makes one carton per RISING edge.")
        return
    if up == 0.0:
        say("The blade was never raised, so nothing ever accumulated. "
            "`stop.raise` holds product back on a belt that keeps running.")
    elif up > 0.97:
        say("The blade was up for essentially the whole run, so nothing was "
            "ever released.")

    if sim.escaped:
        say(f"{len(sim.escaped)} carton(s) got past while `stop.up` was true. "
            f"Read the limit switch rather than the command -- the blade takes "
            f"{AB_BLADE_TIME * 1000:.0f} ms to travel and `stop.raise` is true "
            f"for all of it.")

    if len(first) >= 1 and len(then) >= 1 and abs(avg_first - avg_then) > 1.0:
        ratio = speed_then / speed_first if speed_first > 0 else 0.0
        say(f"The belt ran at {speed_first:.2f} m/s for the first half of this "
            f"run and {speed_then:.2f} m/s for the second -- {ratio:.1f} times "
            f"faster -- and your releases went from {avg_first:.1f} cartons to "
            f"{avg_then:.1f}. That is a release timed in seconds. "
            f"`panel.setpoint` is a window in ENCODER PULSES, which is a "
            f"distance: at {AB_PULSES_PER_METRE:.0f} pulses per metre, hold the "
            f"blade down until `enc.count` has advanced by that many and the "
            f"same length of product comes out at any speed.")
    elif len(first) < 1 or len(then) < 1:
        say("The run changed the drive's top speed halfway through and there was "
            "no release on one side of that change to compare. Cycle the blade "
            "more than once: accumulate, release, accumulate again.")
    elif not sim.escaped:
        say(f"The same pulse window released {avg_first:.1f} cartons at "
            f"{speed_first:.2f} m/s and {avg_then:.1f} at {speed_then:.2f} m/s. "
            f"A release timed in seconds would have let out "
            f"{avg_first * speed_then / max(speed_first, 0.01):.0f} the second "
            f"time.")


def _summary_accumulation(evidence: dict, out) -> None:
    first, then = evidence["first_speed"], evidence["second_speed"]
    out(f"pot: a {evidence['pulse_window_on_the_pot']:.0f}-pulse release window "
        f"({evidence['pulse_window_on_the_pot'] / 100:.2f} m of belt)")
    out(f"at {first['belt_m_per_s']:.2f} m/s: {first['releases']} release(s), "
        f"{first['mean_cartons']:.1f} cartons each")
    out(f"at {then['belt_m_per_s']:.2f} m/s: {then['releases']} release(s), "
        f"{then['mean_cartons']:.1f} cartons each")
    out(f"{evidence['released_total']} cartons out, "
        f"{evidence['belt_travel_while_held_m']:.1f} m of belt ran under a raised blade")


# --- batch dosing --------------------------------------------------------
#
# Observable fact: litres. The plant integrates what the pump really delivered,
# and that number is not on the bus -- `meter.total` is an instrument the
# controller can zero, and `pump.speed` is a command rather than a delivery.
#
# How a program fakes it: by running the pump at a known speed for a known
# time. Twenty litres at 120 L/min is ten seconds, and a stopwatch gets it
# exactly right, once. So the exam reaches into the pump between the two
# batches and halves what it is rated for. Same command, half the flow. A batch
# that ends on litres takes twice as long and delivers the same; a batch that
# ends on seconds delivers half and never notices.
#
# Numbers from `engine/templates/batch_dosing.json` and the parts: a pump rated
# 120 L/min ramping at 300 %/s, a flow meter with a 0.2 s time constant whose
# reset is a level rather than an edge, and a 200 L tank.
BD_RATED_FIRST = 120.0
BD_RATED_THEN = 60.0
BD_RAMP = 300.0
BD_METER_DAMPING = 0.2
BD_CAPACITY = 200.0
BD_TANK_DRAIN_RATE = 10.0


class BatchDosingScene(PlantScene):
    name = "batch-dosing"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("pump.run", "Dosing Pump (Run)", "bit", "output"),
            Tag("pump.speed", "Dosing Pump Speed (%)", "float", "output"),
            Tag("meter.reset", "Flow Meter Totaliser Reset", "bit", "output"),
            Tag("tank.fill", "Tank Fill Valve (%)", "float", "output"),
            Tag("tank.drain", "Tank Drain Valve (%)", "float", "output"),
            Tag("flow_gauge.value", "Flow Gauge", "float", "output"),
            Tag("total_display.value", "Total Display", "int", "output"),
            Tag("level_readout.value", "Level Readout", "int", "output"),
            Tag("pump.flow", "Dosing Pump Flow (L/min)", "float", "input"),
            Tag("pump.fault", "Dosing Pump Fault", "bit", "input"),
            Tag("meter.rate", "Flow Meter Rate (L/min)", "float", "input"),
            Tag("meter.total", "Flow Meter Total (L)", "int", "input"),
            Tag("tank.level", "Tank Level (%)", "float", "input"),
        )

        self.rated = BD_RATED_FIRST
        self.percent = 0.0
        self.flow = 0.0
        self.meter_rate = 0.0
        self.meter_total = 0.0
        self.level = 0.0
        #: Ground truth: litres the pump has actually moved, ever.
        self.delivered = 0.0
        #: One entry per batch the examiner asked for.
        self.batches: list[dict] = []

        self.litres = float(self.rng.choice([18.0, 20.0, 22.0, 24.0]))
        self.script = Script([
            (0.3, self.panel.set_setpoint(self.litres)),
            (1.0, self._begin("first")),
            (32.0, self._end_batch),
            (33.0, self._halve_the_pump),
            (35.0, self._begin("then")),
        ])

    def _begin(self, name: str):
        def do() -> None:
            self.panel.press("reset")()
            self.panel.press("start")()
            self.batches.append({"name": name, "from": self.t,
                                 "delivered_at": self.delivered,
                                 "level_at": self.level,
                                 "rated": self.rated, "to": None})
        do.__name__ = f"begin the {name} batch"
        return do

    def _end_batch(self) -> None:
        self.panel.press("stop")()
        if self.batches:
            self.batches[-1]["to"] = self.t
            self.batches[-1]["delivered"] = self.delivered - self.batches[-1]["delivered_at"]
            self.batches[-1]["level_rise"] = self.level - self.batches[-1]["level_at"]
        # The vessel is emptied between batches, by hand, so the second one
        # starts from the same place as the first.
        self.level = 0.0

    def _halve_the_pump(self) -> None:
        """Re-rate the pump. Nothing on the bus says so: `pump.speed` is still
        a percentage of a maximum the controller is not told, and the only
        instrument that knows is the flow meter."""
        self.rated = BD_RATED_THEN

    def step(self, dt: float) -> None:
        run = self.bit("pump.run") and not self.bit("pump.fault")
        commanded = min(max(self.num("pump.speed"), 0.0), 100.0)
        target = commanded if run else 0.0
        self.percent += max(min(target - self.percent, BD_RAMP * dt), -BD_RAMP * dt)
        self.flow = self.rated * self.percent / 100.0
        self.delivered += self.flow / 60.0 * dt

        alpha = min(dt / BD_METER_DAMPING, 1.0)
        self.meter_rate += (self.flow - self.meter_rate) * alpha
        if self.bit("meter.reset"):
            self.meter_total = 0.0
        else:
            self.meter_total += self.meter_rate / 60.0 * dt

        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        rise = self.flow / 60.0 / BD_CAPACITY * 100.0
        fall = (BD_TANK_DRAIN_RATE * drain / 100.0
                * (max(self.level, 0.0) / 100.0) ** 0.5)
        self.level = min(max(self.level + (rise - fall) * dt, 0.0), 100.0)

        self.tags.set("pump.flow", self.flow)
        self.tags.set("meter.rate", self.meter_rate)
        self.tags.set("meter.total", int(self.meter_total))
        self.tags.set("tank.level", self.level)

        if self.batches and self.batches[-1]["to"] is None:
            batch = self.batches[-1]
            batch["delivered"] = self.delivered - batch["delivered_at"]
            batch["level_rise"] = self.level - batch["level_at"]


def grade_batch_dosing(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: BatchDosingScene = watched.inner
    target = sim.litres
    batches = sim.batches
    #: Litres after the batch was supposed to be over. A dose that overshoots
    #: by carrying on is a different mistake from one that overshoots by
    #: running fast, and only the plant can tell them apart.
    tail = sim.delivered - sum(b.get("delivered", 0.0) for b in batches)

    report.evidence.update({
        "pot_litres": target,
        "batches": [{"name": b["name"], "rated_flow": b["rated"],
                     "delivered_L": round(b.get("delivered", 0.0), 2),
                     "level_rise_pct": round(b.get("level_rise", 0.0), 2),
                     "seconds": round((b["to"] or watched.sim_time) - b["from"], 1)}
                    for b in batches],
        "delivered_total_L": round(sim.delivered, 2),
        "meter_total_L": round(sim.meter_total, 2),
        "delivered_outside_a_batch_L": round(tail, 2),
        "pump_commanded_fraction": round(watched.held_true("pump.run"), 3),
    })

    first = batches[0].get("delivered", 0.0) if batches else 0.0
    report.add("dose.ran",
               first >= 5.0,
               f"the first batch moved {first:.1f} L (at least 5 L, or there is "
               f"nothing here to mark)")
    for index, batch in enumerate(batches, start=1):
        delivered = batch.get("delivered", 0.0)
        rise = batch.get("level_rise", 0.0)
        expected_rise = target / BD_CAPACITY * 100.0
        report.add(f"dose{index}.on_the_number",
                   abs(delivered - target) <= 1.5,
                   f"batch {index} delivered {delivered:.1f} L against a "
                   f"{target:g} L pot, with the pump rated "
                   f"{batch['rated']:g} L/min (within 1.5 L)")
        report.add(f"dose{index}.tank_agrees",
                   abs(rise - expected_rise) <= 1.5,
                   f"batch {index} raised a {BD_CAPACITY:g} L tank by "
                   f"{rise:.1f} %, and {target:g} L is {expected_rise:.1f} %")
    report.add("dose.cut_off",
               tail <= 1.0,
               f"{tail:.1f} L moved outside a batch (at most 1 L: the pump has "
               f"to stop when the batch does)")

    _batch_feedback(report, watched, sim, batches, target, tail)


def _batch_feedback(report, watched, sim, batches, target, tail) -> None:
    say = report.feedback.append
    if not batches or batches[0].get("delivered", 0.0) < 1.0:
        say("The pump moved nothing. `pump.run` has to be true and `pump.speed` "
            "is a percentage, not a bit -- and `meter.rate` is the only thing "
            "that knows what is actually being delivered.")
        return

    if len(batches) > 1:
        one, two = batches[0].get("delivered", 0.0), batches[1].get("delivered", 0.0)
        if two < 2.0 <= one:
            say(f"The second batch delivered {two:.1f} L and stopped almost "
                f"immediately. `meter.total` still held the first batch's "
                f"litres, so the new batch was over before it started. Zero the "
                f"totaliser before each one -- and its reset is a LEVEL, not an "
                f"edge, so hold it until `meter.total` reads back zero.")
        elif abs(one - target) <= 1.5 and abs(two - target) > 1.5:
            ratio = batches[1]["rated"] / batches[0]["rated"]
            say(f"The first batch landed on {one:.1f} L and the second on "
                f"{two:.1f} L, against the same {target:g} L pot. Between them "
                f"the pump was re-rated to {ratio:.0%} of what it was: the same "
                f"`pump.speed` now delivers {ratio:.0%} of the flow. A batch that "
                f"ends on seconds cannot see that. End it on `meter.total`, "
                f"which is litres, and the second batch takes "
                f"{1 / ratio:.0f} times as long and delivers the same.")

    for index, batch in enumerate(batches, start=1):
        delivered = batch.get("delivered", 0.0)
        if delivered > target + 1.5:
            say(f"Batch {index} overran by {delivered - target:.1f} L. The pump "
                f"takes time to stop and the meter is damped, so cutting at the "
                f"number arrives late -- taper the rate over the last few litres "
                f"so the cut-off does not carry you past it.")

    if tail > 1.0:
        say(f"{tail:.1f} L went through the pump outside a batch. When the batch "
            f"is done the pump stops: `pump.run` false, not merely a lower speed.")

    if all(abs(b.get("delivered", 0.0) - target) <= 1.5 for b in batches) \
            and len(batches) > 1:
        say(f"Both batches landed on {target:g} L, at two different pump "
            f"ratings -- {batches[0]['rated']:g} and {batches[1]['rated']:g} "
            f"L/min -- and the tank agreed with the meter. That is a batch that "
            f"ends on a quantity.")


def _summary_batch(evidence: dict, out) -> None:
    out(f"pot: {evidence['pot_litres']:g} L")
    for batch in evidence["batches"]:
        out(f"{batch['name']:>6} batch: {batch['delivered_L']:.1f} L in "
            f"{batch['seconds']:.0f}s with the pump rated "
            f"{batch['rated_flow']:g} L/min, tank +{batch['level_rise_pct']:.1f} %")
    out(f"{evidence['delivered_total_L']:.1f} L through the pump in all, "
        f"{evidence['delivered_outside_a_batch_L']:.1f} of it outside a batch")


# --- guarded cell --------------------------------------------------------
#
# Observable fact: whether the contactor ever pulled in without somebody having
# pressed Start. The plant runs the relay and the contactor, so it knows the
# tick the motor started and it knows every press, and neither is something a
# controller can arrange from the bus.
#
# How a program fakes it: it cannot, and that is the point of the scene. The
# failure this rubric exists to catch is not a shortcut, it is the thing a
# student writes by accident -- holding `starter.coil` true for as long as the
# cell "should be running", so that when the guard is shut and the relay hands
# the coil back the machine starts by itself, with somebody still inside it.
# The exam opens both gate leaves mid-run and closes them again, and watches
# what the motor does between the relay re-energising and the next Start press.
# A program that got it right does nothing at all there.
#
# Two more the plant can see and the bus cannot. `belt.rotate` is the motor's
# own tag and this exercise is the one where the program must never write it --
# the contactor runs the motor -- so the grader records every tag the
# controller ever wrote and marks that directly. And a mute held longer than
# the scanner's own limit stops being a mute: the plant notes the moment the
# scanner withdraws one, which is what a mute somebody has taped on looks like
# from inside the machine.
#
# Numbers from `engine/templates/guarded_cell.json` and the parts: a relay with
# a 0.5 s channel-sync window that energises on a RISING reset edge only, a
# contactor with a 0.06 s pull-in, a scanner with a 6 s mute limit, and a 0.7 m
# cylinder stroke at 1.4 m/s.
GC_BELT_SPEED = 0.5
GC_MUTE_EYE_POS = 1.35
GC_FIELD_FROM = 1.6
GC_FIELD_TO = 2.4
GC_PUSH_EYE_POS = 2.8
GC_STATION_POS = 3.2
GC_LINE_END_POS = 3.9
GC_SYNC_WINDOW = 0.5
GC_PULL_IN = 0.06
GC_MUTE_LIMIT = 6.0
GC_ROD_TIME = 0.7 / 1.4


class GuardedCellScene(PlantScene):
    name = "guarded-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("relay.reset", "Safety Relay Reset", "bit", "output"),
            Tag("starter.coil", "Starter Contactor Coil", "bit", "output"),
            Tag("scanner.mute", "Scanner Mute Request", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("cylinder.extend", "Cylinder Extend Coil", "bit", "output"),
            Tag("cylinder.retract", "Cylinder Retract Coil", "bit", "output"),
            # The motor's own tag. Declared exactly as the engine declares it,
            # an Output the controller *could* write -- and the one thing this
            # exercise says not to. A tag the grader hid would teach nothing.
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("relay.k1", "Safety Relay Contact K1", "bit", "input"),
            Tag("relay.k2", "Safety Relay Contact K2", "bit", "input"),
            Tag("relay.cha", "Safety Relay Channel A", "bit", "input", value=True),
            Tag("relay.chb", "Safety Relay Channel B", "bit", "input", value=True),
            Tag("relay.fault", "Safety Relay Channel Fault", "bit", "input"),
            Tag("guard_a.closed", "Gate Leaf A Closed", "bit", "input", value=True),
            Tag("guard_b.closed", "Gate Leaf B Closed", "bit", "input", value=True),
            Tag("starter.aux", "Starter Auxiliary Contact", "bit", "input"),
            Tag("starter.overload", "Starter Overload OK (NC)", "bit", "input",
                value=True),
            Tag("scanner.stop", "Scanner Protective Field Clear (NC)", "bit",
                "input", value=True),
            Tag("scanner.warn", "Scanner Warning Field Broken", "bit", "input"),
            Tag("scanner.muted", "Scanner Muting Active", "bit", "input"),
            Tag("mute_eye.detect", "Mute Eye (Detect)", "bit", "input"),
            Tag("push_eye.detect", "Push Eye (Detect)", "bit", "input"),
            Tag("cylinder.extended", "Cylinder Extended Reed", "bit", "input"),
            Tag("cylinder.retracted", "Cylinder Retracted Reed", "bit", "input",
                value=True),
            Tag("transferred.count", "Chute Remover (Count)", "int", "input"),
            Tag("line_end.count", "Line End Remover (Count)", "int", "input"),
        )

        #: The relay powers up open, so the cell powers up unable to move. That
        #: is not a fault, it is what every safety relay does.
        self.energised = False
        self.relay_fault = False
        self._discrepancy = 0.0
        self._last_reset = False
        self.guard_a = self.guard_b = True

        self.contactor = False
        self._coil_timer = 0.0
        self.mute_held = 0.0
        self.muted = False
        self.rod = 0.0
        self.items: list[Item] = []
        self.transferred: list[Item] = []
        self.line_end: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        # --- ground truth ---
        #: Sim times the contactor pulled in with no Start press since the cell
        #: last stopped. The headline of the whole scene.
        self.started_without_a_press: list[float] = []
        self._stopped_at: float | None = 0.0
        #: Metres the belt ran after a gate leaf opened.
        self.travel_after_the_gate = 0.0
        self._gate_opened_at: float | None = None
        #: Times the scanner withdrew a mute because it had been held past its
        #: own limit.
        self.mute_withdrawn: list[float] = []
        self.both_coils = 0
        self.max_mute_held = 0.0

        # The examiner's test sheet. The two presses after the gate shuts are
        # the whole exam: Reset closes the relay, and the motor must not move
        # until the Start six seconds later.
        self.script = Script([
            (2.0, self.panel.press("reset")),
            (6.0, self.panel.press("start")),
            (22.0, self._open_the_gate),
            (26.0, self._shut_the_gate),
            (28.0, self.panel.press("reset")),
            (34.0, self.panel.press("start")),
        ])

    def _open_the_gate(self) -> None:
        """Both leaves at once, and the part in the field taken out.

        The second half is not decoration. A carton standing in the protective
        field when the cell stops leaves the field un-clear, and a stopped belt
        cannot clear it -- so the scanner refuses to let the cell start, for
        ever, and a correct program is marked as one that could not restart.
        That deadlock is real and `tools/try_scene.py` demonstrates it
        deliberately, but it is not this rubric's question. Somebody opening a
        guard is somebody going in, and going in is how the part comes out.
        """
        self.guard_a = self.guard_b = False
        self._gate_opened_at = self.t
        self.items = [i for i in self.items
                      if not GC_FIELD_FROM - 0.2 <= i.position <= GC_FIELD_TO + 0.2]

    def _shut_the_gate(self) -> None:
        self.guard_a = self.guard_b = True
        self._gate_opened_at = None

    # --- the plant ---

    def step(self, dt: float) -> None:
        self._step_relay(dt)
        self._step_starter(dt)
        self._step_scanner(dt)
        self._step_line(dt)

    def _step_relay(self, dt: float) -> None:
        a, b = self.guard_a, self.guard_b
        if a != b:
            self._discrepancy += dt
            if self._discrepancy >= GC_SYNC_WINDOW:
                self.relay_fault = True
        else:
            self._discrepancy = 0.0

        healthy = a and b and not self.relay_fault
        if not healthy:
            # Immediate and unconditional. A safety output that waited for
            # anything would not be a safety output.
            self.energised = False

        reset = self.bit("relay.reset")
        if reset and not self._last_reset:
            if self.relay_fault and a == b:
                self.relay_fault = False
                self._discrepancy = 0.0
            if a and b and not self.relay_fault:
                self.energised = True
        self._last_reset = reset

        self.tags.set("relay.cha", a)
        self.tags.set("relay.chb", b)
        self.tags.set("relay.k1", self.energised)
        self.tags.set("relay.k2", self.energised)
        self.tags.set("relay.fault", self.relay_fault)
        self.tags.set("guard_a.closed", a)
        self.tags.set("guard_b.closed", b)

    def _step_starter(self, dt: float) -> None:
        # The relay holds the coil circuit open. In the engine it does that by
        # forcing the tag; here the plant simply does not obey a command the
        # relay is not passing, because a forced tag is this tool's
        # disqualification signal and the grader must not trip its own wire.
        wants = self.bit("starter.coil") and self.energised
        was = self.contactor
        if wants:
            self._coil_timer += dt
            if not self.contactor and self._coil_timer >= GC_PULL_IN:
                self.contactor = True
        else:
            self._coil_timer = 0.0
            self.contactor = False

        if self.contactor and not was:
            # The motor just started. Was it started by anybody?
            since = self._stopped_at if self._stopped_at is not None else 0.0
            if not self.panel.started_since(since):
                self.started_without_a_press.append(round(self.t, 2))
        elif was and not self.contactor:
            self._stopped_at = self.t

        self.tags.set("starter.aux", self.contactor)
        self.tags.set("belt.rotate", self.contactor)

        if self.contactor and self._gate_opened_at is not None:
            self.travel_after_the_gate += GC_BELT_SPEED * dt

    def _step_scanner(self, dt: float) -> None:
        wants_mute = self.bit("scanner.mute")
        if wants_mute:
            self.mute_held += dt
            self.max_mute_held = max(self.max_mute_held, self.mute_held)
            allowed = self.mute_held <= GC_MUTE_LIMIT
            if self.muted and not allowed:
                # The scanner taking the guard back under a standing request.
                self.mute_withdrawn.append(round(self.t, 2))
            self.muted = allowed
        else:
            self.mute_held = 0.0
            self.muted = False

        broken = any(GC_FIELD_FROM <= i.position <= GC_FIELD_TO for i in self.items)
        self.tags.set("scanner.warn", broken)
        self.tags.set("scanner.muted", self.muted)
        self.tags.set("scanner.stop", (not broken) or self.muted)

    def _step_line(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        extend = self.bit("cylinder.extend")
        retract = self.bit("cylinder.retract")
        if extend and retract:
            self.both_coils += 1
        # Energising both is not a way to hold a double-solenoid valve still,
        # it is a way to leave the rod where the last scan put it.
        rate = dt / GC_ROD_TIME
        if extend and not retract:
            self.rod = min(self.rod + rate, 1.0)
        elif retract and not extend:
            self.rod = max(self.rod - rate, 0.0)
        self.tags.set("cylinder.extended", self.rod >= 0.999)
        self.tags.set("cylinder.retracted", self.rod <= 0.001)

        if self.contactor:
            for item in self.items:
                item.position += GC_BELT_SPEED * dt

        still: list[Item] = []
        for item in self.items:
            if self.rod > 0.5 and abs(item.position - GC_STATION_POS) <= 0.22:
                item.lane = "chute"
                self.transferred.append(item)
            elif item.position >= GC_LINE_END_POS:
                item.lane = "line-end"
                self.line_end.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("mute_eye.detect", self._eye(self.items, GC_MUTE_EYE_POS))
        self.tags.set("push_eye.detect", self._eye(self.items, GC_PUSH_EYE_POS))
        self.tags.set("transferred.count", len(self.transferred))
        self.tags.set("line_end.count", len(self.line_end))


def grade_guarded_cell(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: GuardedCellScene = watched.inner
    wrote_the_motor = engine is not None and "belt.rotate" in engine.written_tags
    allowed = GC_BELT_SPEED * ESTOP_LIMIT

    report.evidence.update({
        "transferred": len(sim.transferred),
        "past_the_station": len(sim.line_end),
        "started_without_a_press_at": sim.started_without_a_press[:10],
        "wrote_belt_rotate": wrote_the_motor,
        "belt_travel_after_the_gate_m": round(sim.travel_after_the_gate, 3),
        "allowed_after_the_gate_m": round(allowed, 3),
        "longest_mute_s": round(sim.max_mute_held, 2),
        "scanner_mute_limit_s": GC_MUTE_LIMIT,
        "mute_withdrawn_at": sim.mute_withdrawn[:10],
        "both_cylinder_coils_ticks": sim.both_coils,
        "contactor_fraction": round(watched.held_true("starter.aux"), 3),
        "presses": sim.panel.presses,
    })

    report.add("cell.transferred_cartons",
               len(sim.transferred) >= 3,
               f"{len(sim.transferred)} carton(s) reached the chute "
               f"(at least 3), {len(sim.line_end)} went past the station")
    report.add("cell.never_wrote_the_motor",
               not wrote_the_motor,
               "the program never wrote belt.rotate -- the contactor runs the "
               "motor" if not wrote_the_motor else
               "the program wrote belt.rotate, which is the motor's own tag and "
               "the one thing this exercise says not to touch")
    report.add("cell.no_start_on_the_permissive",
               not sim.started_without_a_press,
               "the motor never started without somebody pressing Start"
               if not sim.started_without_a_press else
               f"the motor started {len(sim.started_without_a_press)} time(s) "
               f"with no Start press since it last stopped, at "
               f"{sim.started_without_a_press[:5]}s")
    report.add("cell.gate_stopped_it",
               sim.travel_after_the_gate <= allowed,
               f"the belt moved {sim.travel_after_the_gate * 1000:.0f} mm after a "
               f"gate leaf opened (at most {allowed * 1000:.0f} mm)")
    report.add("cell.mute_within_the_limit",
               not sim.mute_withdrawn,
               f"the longest mute was {sim.max_mute_held:.1f}s, inside the "
               f"scanner's {GC_MUTE_LIMIT:g}s limit"
               if not sim.mute_withdrawn else
               f"the scanner withdrew the mute {len(sim.mute_withdrawn)} time(s) "
               f"after it was held {sim.max_mute_held:.1f}s, past its "
               f"{GC_MUTE_LIMIT:g}s limit")
    report.add("cell.one_coil_at_a_time",
               sim.both_coils == 0,
               "both cylinder solenoids were never energised at once"
               if not sim.both_coils else
               f"both solenoids were energised together on {sim.both_coils} ticks")

    _guarded_feedback(report, watched, sim, wrote_the_motor, allowed)


def _guarded_feedback(report, watched, sim, wrote_the_motor, allowed) -> None:
    say = report.feedback.append

    if sim.started_without_a_press:
        say(f"The motor started at {sim.started_without_a_press[0]}s with nobody "
            f"having pressed Start since it last stopped. That is automatic "
            f"restart, and it is the failure this whole cell exists to prevent: "
            f"the relay closing hands `starter.coil` back to your program, it "
            f"does not command it. Shutting a gate and pressing the relay's "
            f"Reset must start nothing at all -- Start is what starts it, and "
            f"the run has to be latched off by the trip until then.")

    if wrote_the_motor:
        say("The program wrote `belt.rotate`. On this cell the contactor runs "
            "the motor: you command `starter.coil` and read `starter.aux` to "
            "find out whether it pulled in. Writing the motor's tag directly "
            "works right up until the day a guard is open and the relay is "
            "holding the coil -- and then it drives a motor the safety circuit "
            "believes it has stopped.")

    if watched.held_true("starter.aux") == 0.0:
        say("The contactor never pulled in. The relay powers up open, so the "
            "cell powers up unable to move: write `relay.reset` to close it "
            "(a RISING edge -- a level is automatic restart), then hold "
            "`starter.coil` once somebody has pressed Start.")
    elif not sim.transferred:
        say("The motor ran and nothing reached the chute. `cylinder.extend` and "
            "`cylinder.retract` are two coils, not one, and the two reeds are "
            "how you know where the rod is -- between them neither is made, so "
            "`not extended` is not `retracted`.")

    if sim.mute_withdrawn:
        say(f"The scanner stopped honouring a mute your program was still "
            f"asking for, after {sim.max_mute_held:.1f}s. Its own limit is "
            f"{GC_MUTE_LIMIT:g}s, because muting held longer than a pallet "
            f"takes to cross is muting somebody has taped on. Bridge the field "
            f"for each carton and let it go again.")

    if sim.travel_after_the_gate > allowed:
        say(f"The belt ran {sim.travel_after_the_gate * 1000:.0f} mm after a gate "
            f"leaf opened. The relay drops the coil circuit immediately; if the "
            f"motor kept turning, something other than the contactor is driving "
            f"it.")

    if (sim.transferred and not sim.started_without_a_press
            and not wrote_the_motor and not sim.mute_withdrawn):
        say(f"Clean: {len(sim.transferred)} cartons transferred, the gate "
            f"stopped the cell, the relay closing on Reset started nothing, and "
            f"`belt.rotate` was never written -- the contactor ran the motor.")


def _summary_guarded(evidence: dict, out) -> None:
    out(f"{evidence['transferred']} transferred, "
        f"{evidence['past_the_station']} past the station; the contactor was in "
        f"for {evidence['contactor_fraction'] * 100:.0f}% of the run")
    started = evidence["started_without_a_press_at"]
    out(f"motor starts with no Start press: {started if started else 'none'}")
    out(f"belt.rotate written by the program: "
        f"{'YES' if evidence['wrote_belt_rotate'] else 'no'}")
    out(f"belt after the gate opened {evidence['belt_travel_after_the_gate_m'] * 1000:.0f} mm "
        f"(at most {evidence['allowed_after_the_gate_m'] * 1000:.0f} mm)")
    out(f"longest mute {evidence['longest_mute_s']:.1f}s against a "
        f"{evidence['scanner_mute_limit_s']:g}s limit")


# --- pick and place cell -------------------------------------------------
#
# Observable fact: which cartons the gantry actually carried to the outfeed,
# and where it let go of the ones it did not. The plant owns the cup, so it
# knows whether there was anything on it and it knows the rail position at the
# moment the vacuum dropped.
#
# How a program fakes it: by sequencing on timers. Travel, lower, grip, raise,
# travel, release is six waits, and six waits of the right length look exactly
# like a controller written on feedback -- at one travel speed. So the exam
# reaches into the axis halfway through the run and slows it down. The
# feedback-driven sequence gets slower and keeps working; the timed one starts
# releasing the carton somewhere over the middle of the rail, and the plant
# records every one of those as a carton dropped on the floor rather than as a
# cycle that merely took too long.
#
# The other half is the vacuum. `gantry.holding` is true only when the cup
# really caught something, so a cycle that travels with the grip on and nothing
# held carried air -- which a timed sequencer does the moment it grips before a
# carton has been indexed.
#
# Numbers from `engine/templates/pick_and_place_cell.json`: a 2.4 m rail at
# 80 %/s with a 1.5 % in-position window, a 0.52 m stroke at 1.3 m/s, and an
# infeed behind a VFD ramping at 35 %/s to 0.8 m/s.
PP_INFEED_TO = 2.0
PP_SCANNER_POS = 1.4
PP_STATION_POS = 2.9
PP_STATION_END = 3.0
PP_STATION_SPEED = 0.5
PP_INFEED_MAX = 0.8
PP_INFEED_RAMP = 35.0
PP_TOLERANCE = 1.5
PP_LOWER_TIME = 0.52 / 1.3
PP_TRAVEL_FIRST = 80.0
PP_TRAVEL_THEN = 32.0
PP_TRAVEL_CHANGES_AT = 35.0
#: Where on the rail the outfeed is. Let go anywhere else and the carton falls.
PP_PLACE_AT = 100.0
PP_PLACE_WINDOW = 8.0
PP_CODES = (101, 102, 201)


class PickPlaceScene(PlantScene):
    name = "pick-and-place-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("infeed.run", "VFD Conveyor (Run)", "bit", "output"),
            Tag("infeed.speed", "VFD Conveyor Speed Ref (%)", "float", "output"),
            Tag("pickstation.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("scanner.enable", "Scanner Enable", "bit", "output", value=True),
            Tag("gantry.target", "Gantry Target (%)", "float", "output"),
            Tag("gantry.lower", "Gantry Lower", "bit", "output"),
            Tag("gantry.grip", "Gantry Vacuum", "bit", "output"),
            Tag("rate.value", "Rate Gauge", "float", "output"),
            Tag("alarm.beacon", "Alarm Beacon", "bit", "output"),
            Tag("alarm.horn", "Alarm Horn", "bit", "output"),
            Tag("infeed.actual", "VFD Conveyor Actual Speed (%)", "float", "input"),
            Tag("scanner.code", "Scanner Code", "int", "input"),
            Tag("scanner.read", "Scanner Read Pulse", "bit", "input"),
            Tag("scanner.present", "Scanner Item Present", "bit", "input"),
            Tag("atstation.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("gantry.position", "Gantry Position (%)", "float", "input"),
            Tag("gantry.inposition", "Gantry In Position", "bit", "input",
                value=True),
            Tag("gantry.lowered", "Gantry Lowered", "bit", "input"),
            Tag("gantry.raised", "Gantry Raised", "bit", "input", value=True),
            Tag("gantry.holding", "Gantry Holding", "bit", "input"),
            Tag("gantry.fault", "Gantry Drive Fault", "bit", "input"),
            Tag("outfeed.count", "Remover (Count)", "int", "input"),
        )

        self.travel_speed = PP_TRAVEL_FIRST
        self.actual_percent = 0.0
        self.position = 0.0
        self.held_target = 0.0
        self.lowered_frac = 0.0
        self.carried: Item | None = None
        self.items: list[Item] = []
        self._scanned: set[int] = set()
        self._next_id = 1
        self._emit_edge = False
        self.codes = shuffled_cycle(self.rng, list(PP_CODES), 12)

        # --- ground truth ---
        #: Cartons the gantry carried to the outfeed, with the sim time.
        self.placed: list[dict] = []
        #: Cartons let go of anywhere else, with the rail position it happened
        #: at. A cycle that releases over the middle of the rail has not been
        #: slow, it has dropped a carton.
        self.dropped: list[dict] = []
        #: Times the gantry reached the place end with the vacuum on and
        #: nothing on the cup.
        self.empty_carries: list[float] = []
        self._carrying_since: float | None = None
        self._was_at_place = False
        self.max_ramp_gap = 0.0

        # The pot on this cell is the infeed drive's speed reference, in
        # percent, which the template graduates 10-100.
        self.script = Script([
            (0.2, self.panel.set_setpoint(self.rng.choice([60.0, 70.0, 80.0]))),
            (1.0, self.panel.press("start")),
            (PP_TRAVEL_CHANGES_AT, self._slow_the_axis),
        ])

    def _slow_the_axis(self) -> None:
        """Turn the rail's travel speed down, mid-run.

        It is a slider in the property panel in the real editor, so a scene a
        student is handed may have any value in it. Nothing on the bus reports
        it: `gantry.position` still counts percent and `gantry.inposition`
        still says whether the axis got there. A sequence written on those two
        does not notice. A sequence written on a stopwatch lets go of the
        carton over the middle of the rail.
        """
        self.travel_speed = PP_TRAVEL_THEN

    @property
    def phase(self) -> str:
        return "fast" if self.t < PP_TRAVEL_CHANGES_AT else "slow"

    def step(self, dt: float) -> None:
        self._step_infeed(dt)
        self._step_gantry(dt)
        self._step_sensors()

    def _step_infeed(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            item = Item(id=self._next_id)
            item.measured = float(self.codes[(self._next_id - 1) % len(self.codes)])
            self.items.append(item)
            self._next_id += 1
        self._emit_edge = emit

        run = self.bit("infeed.run")
        reference = min(max(self.num("infeed.speed"), 0.0), 100.0) if run else 0.0
        self.actual_percent += max(min(reference - self.actual_percent,
                                       PP_INFEED_RAMP * dt), -PP_INFEED_RAMP * dt)
        self.max_ramp_gap = max(self.max_ramp_gap,
                                abs(reference - self.actual_percent))
        self.tags.set("infeed.actual", self.actual_percent)
        infeed_speed = PP_INFEED_MAX * self.actual_percent / 100.0 if run else 0.0

        station = self.bit("pickstation.rotate")
        for item in self.items:
            if item is self.carried:
                continue
            if item.position < PP_INFEED_TO:
                item.position = min(item.position + infeed_speed * dt, PP_INFEED_TO)
            elif station:
                item.position = min(item.position + PP_STATION_SPEED * dt,
                                    PP_STATION_END)

    def _step_gantry(self, dt: float) -> None:
        # The target the *machine* holds, taken before this tick's writes are
        # read, so `inposition` is stale on the scan that commands a move --
        # exactly as `PickPlaceArm.InPosition` is, and exactly the trap the
        # scene's own brief warns about.
        target = min(max(self.num("gantry.target"), 0.0), 100.0)
        step = self.travel_speed * dt
        if not self.bit("gantry.fault"):
            self.position += max(min(self.held_target - self.position, step), -step)
        self.tags.set("gantry.position", self.position)
        self.tags.set("gantry.inposition",
                      abs(self.position - self.held_target) <= PP_TOLERANCE)
        self.held_target = target

        want_down = self.bit("gantry.lower")
        rate = dt / PP_LOWER_TIME
        self.lowered_frac = (min(self.lowered_frac + rate, 1.0) if want_down
                             else max(self.lowered_frac - rate, 0.0))
        lowered = self.lowered_frac >= 0.999
        self.tags.set("gantry.lowered", lowered)
        self.tags.set("gantry.raised", self.lowered_frac <= 0.001)

        grip = self.bit("gantry.grip")
        if grip and self.carried is None and lowered and self.position <= PP_TOLERANCE * 2:
            under = [i for i in self.items
                     if i is not self.carried
                     and abs(i.position - PP_STATION_POS) <= 0.14]
            if under:
                self.carried = under[0]
                self.carried.carried = True
                self._carrying_since = self.t
        elif not grip and self.carried is not None:
            item = self.carried
            self.carried = None
            item.carried = False
            where = round(self.position, 1)
            if abs(self.position - PP_PLACE_AT) <= PP_PLACE_WINDOW:
                item.lane = "outfeed"
                self.placed.append({"carton": item.id, "at": round(self.t, 2),
                                    "code": int(item.measured or 0),
                                    "position": where, "phase": self.phase})
                self.items.remove(item)
                self.tags.set("outfeed.count", len(self.placed))
            else:
                # Let go anywhere but over the outfeed and it is on the floor.
                item.lane = "floor"
                self.dropped.append({"carton": item.id, "at": round(self.t, 2),
                                     "position": where, "phase": self.phase})
                self.items.remove(item)

        self.tags.set("gantry.holding", self.carried is not None and grip)

        at_place = abs(self.position - PP_PLACE_AT) <= PP_PLACE_WINDOW
        if at_place and not self._was_at_place and grip and self.carried is None:
            self.empty_carries.append(round(self.t, 2))
        self._was_at_place = at_place

    def _step_sensors(self) -> None:
        waiting = [i for i in self.items if i is not self.carried]
        in_window = [i for i in waiting
                     if abs(i.position - PP_SCANNER_POS) <= 0.13]
        self.tags.set("scanner.present", bool(in_window))
        fresh = [i for i in in_window if i.id not in self._scanned]
        if fresh and self.bit("scanner.enable"):
            self._scanned.add(fresh[0].id)
            self.tags.set("scanner.code", int(fresh[0].measured or 0))
            self.tags.set("scanner.read", True)
        else:
            # One scan wide, exactly like a panel button's pulse, which is why
            # a program has to latch it rather than poll it.
            self.tags.set("scanner.read", False)
        self.tags.set("atstation.detect",
                      any(abs(i.position - PP_STATION_POS) <= 0.14
                          for i in waiting))


def grade_pick_place(watched: Watched, engine: GradedEngine, report: Report,
                     duration: float) -> None:
    sim: PickPlaceScene = watched.inner
    fast = [p for p in sim.placed if p["phase"] == "fast"]
    slow = [p for p in sim.placed if p["phase"] == "slow"]

    report.evidence.update({
        "fed": sim._next_id - 1,
        "placed": len(sim.placed),
        "placed_before_the_axis_slowed": len(fast),
        "placed_after_the_axis_slowed": len(slow),
        "dropped": sim.dropped[:20],
        "empty_carries_at": sim.empty_carries[:10],
        "travel_speed_before": PP_TRAVEL_FIRST,
        "travel_speed_after": PP_TRAVEL_THEN,
        "codes_read": sorted({p["code"] for p in sim.placed}),
        "max_ramp_gap_percent": round(sim.max_ramp_gap, 1),
        "carried": [p["carton"] for p in sim.placed][:20],
    })

    report.add("cell.cycled",
               len(sim.placed) >= 3,
               f"{len(sim.placed)} carton(s) were carried to the outfeed "
               f"(at least 3 -- one is a cell that happened to work once)")
    report.add("cell.nothing_dropped",
               not sim.dropped,
               "the vacuum was never released away from the outfeed"
               if not sim.dropped else
               f"{len(sim.dropped)} carton(s) were let go somewhere else, at "
               f"rail positions "
               f"{sorted({d['position'] for d in sim.dropped})[:6]} %")
    report.add("cell.survived_the_slower_axis",
               len(slow) >= 1,
               f"{len(fast)} placed at {PP_TRAVEL_FIRST:g} %/s and {len(slow)} "
               f"after the axis was slowed to {PP_TRAVEL_THEN:g} %/s")
    report.add("cell.never_carried_air",
               not sim.empty_carries,
               "the gantry never travelled to the outfeed with the vacuum on "
               "and nothing on the cup" if not sim.empty_carries else
               f"{len(sim.empty_carries)} cycle(s) carried air, at "
               f"{sim.empty_carries[:5]}s")

    _pick_place_feedback(report, watched, sim, fast, slow)


def _pick_place_feedback(report, watched, sim, fast, slow) -> None:
    say = report.feedback.append

    if not sim.placed and not sim.dropped:
        if watched.held_true("infeed.run") == 0.0:
            say("The infeed never ran. `infeed.run` is a bit and `infeed.speed` "
                "is a percentage -- the drive needs both, and it ramps, so "
                "`infeed.actual` will lag whatever you ask for.")
        else:
            say("Nothing was ever picked up. `gantry.grip` only catches "
                "something when the arm is down (`gantry.lowered`) over a "
                "carton the pick station has indexed (`atstation.detect`), and "
                "`gantry.holding` tells you whether it did.")
        return

    if sim.dropped:
        spots = sorted({d["position"] for d in sim.dropped})
        in_slow = [d for d in sim.dropped if d["phase"] == "slow"]
        if len(in_slow) >= len(sim.dropped) * 0.7:
            say(f"{len(sim.dropped)} carton(s) hit the floor, "
                f"{len(in_slow)} of them after the axis was slowed from "
                f"{PP_TRAVEL_FIRST:g} %/s to {PP_TRAVEL_THEN:g} %/s -- released "
                f"at {spots[:5]} % of the rail instead of at "
                f"{PP_PLACE_AT:g} %. That is a sequence written on timers. "
                f"Wait on `gantry.position` reaching the destination this step "
                f"wants, and the same code works at any travel speed.")
        else:
            say(f"{len(sim.dropped)} carton(s) were released at {spots[:5]} % of "
                f"the rail rather than over the outfeed. Check the position "
                f"feedback against the destination the step wants -- not "
                f"`gantry.inposition` alone, which compares the axis to the "
                f"target the machine currently holds, and a target written this "
                f"scan has not reached the machine yet. On the scan that issues "
                f"a move it still reports 'arrived', at the place you are "
                f"trying to leave.")

    if sim.empty_carries:
        say(f"{len(sim.empty_carries)} cycle(s) travelled to the outfeed with "
            f"the vacuum on and nothing on the cup. `gantry.holding` is true "
            f"only when the cup really caught something -- check it after you "
            f"grip, and go back and wait if it did not.")

    if not slow and fast:
        say(f"Nothing was placed after the axis slowed down {PP_TRAVEL_CHANGES_AT:g}s "
            f"in. The cell either stalled or is still waiting for a motion that "
            f"now takes longer than it used to.")

    if sim.placed and not sim.dropped and not sim.empty_carries:
        say(f"{len(sim.placed)} cartons placed -- {len(fast)} at "
            f"{PP_TRAVEL_FIRST:g} %/s and {len(slow)} after the axis was slowed "
            f"to {PP_TRAVEL_THEN:g} %/s, with nothing dropped and every carry "
            f"holding something. That is a sequence on feedback.")


def _summary_pick_place(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, placed {evidence['placed']} "
        f"({evidence['placed_before_the_axis_slowed']} at "
        f"{evidence['travel_speed_before']:g} %/s, "
        f"{evidence['placed_after_the_axis_slowed']} at "
        f"{evidence['travel_speed_after']:g} %/s)")
    out(f"codes carried: {evidence['codes_read']}")
    out(f"largest gap between the drive's reference and its actual speed: "
        f"{evidence['max_ramp_gap_percent']:.0f} %")
    for entry in evidence["dropped"][:8]:
        out(f"  carton {entry['carton']:>3} dropped at {entry['position']:.0f} % "
            f"of the rail, {entry['at']:.1f}s ({entry['phase']})")


#: Every scene this tool can mark, and what it says it marks.
RUBRICS = {
    "sorting-by-height": {
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
    },
    "start-stop-station": {
        "title": "Start / stop station",
        "task": ("Run a batch of the size the pot asks for and stop when it is "
                 "made. The mushroom is normally closed and its trip latches: "
                 "only Reset clears it, and Reset alone starts nothing. Reset "
                 "also clears the batch count."),
        "build": StartStopScene,
        "observe": None,
        "grade": grade_start_stop,
        "summary": _summary_start_stop,
        "duration": 60.0,
        "references": ("good", "noestop", "runon"),
        "tags": ("belt.rotate, emitter.emit, produced.value, panel.green, "
                 "panel.red are yours to write; panel.start, panel.stop, "
                 "panel.reset, panel.estop, panel.setpoint, part_present.detect, "
                 "counter.count are the line's."),
    },
    "tank-level-control": {
        "title": "Tank level control",
        "task": ("Hold the tank at the level the pot asks for, with the fill "
                 "and drain valves. Outflow follows Torricelli, so the process "
                 "gain falls with level: this run asks for a high level and "
                 "then a low one."),
        "build": TankScene,
        "observe": None,
        "grade": grade_tank,
        "summary": _summary_regulator,
        "duration": 65.0,
        "references": ("good", "bangbang", "fixedsp"),
        "tags": ("tank.fill, tank.drain, level_readout.value, panel.green, "
                 "panel.red are yours to write; tank.level, tank.fault, "
                 "panel.setpoint and the buttons are the plant's."),
    },
    "heat-treat-station": {
        "title": "Heat treat station",
        "task": ("Hold the plate at the temperature on the pot. The plant is a "
                 "first-order lag losing heat to the room, so holding a "
                 "temperature needs a standing output -- and proportional "
                 "action can only make one out of a standing error."),
        "build": OvenScene,
        "observe": None,
        "grade": grade_oven,
        "summary": _summary_regulator,
        "duration": 65.0,
        "references": ("good", "ponly", "thermostat"),
        "tags": ("oven.heater, temp_gauge.value, temp_readout.value, "
                 "alarm.beacon, alarm.horn, panel.green, panel.red are yours to "
                 "write; oven.temperature, oven.attemp, oven.fault, "
                 "panel.setpoint and the buttons are the plant's."),
    },
    "light-curtain-sorting": {
        "title": "Light curtain sorting",
        "task": ("Sort on a measurement rather than on two bits. The curtain "
                 "reports how tall each carton is, in metres, and the pot is "
                 "the threshold -- read it when you measure each carton, "
                 "because this run turns it."),
        "build": LightCurtainScene,
        "observe": None,
        "grade": grade_light_curtain,
        "summary": _summary_light_curtain,
        "duration": 65.0,
        "references": ("good", "fixed", "everyother"),
        "tags": ("belt.rotate, emitter.emit, diverter.extend, panel.green, "
                 "panel.red are yours to write; height_gauge.height, "
                 "height_gauge.blocked, diverter.extended, diverter.retracted, "
                 "tall_count.count, short_count.count, panel.setpoint and the "
                 "buttons are the line's."),
    },
    "roller-line-weighing": {
        "title": "Roller line with weighing",
        "task": ("Checkweigh. Judge each carton on the peak weight it shows "
                 "crossing the deck and light panel.red for anything over the "
                 "limit on the pot. Two cartons on the deck read as one peak, "
                 "so hold the feed while the scale is loaded."),
        "build": RollerWeighScene,
        "observe": None,
        "grade": grade_roller_weighing,
        "summary": _summary_roller,
        "duration": 70.0,
        "references": ("good", "metalonly", "fastfeed"),
        "tags": ("infeed.rotate, scale.rotate, emitter.emit, "
                 "weight_readout.value, panel.green, panel.red are yours to "
                 "write; scale.weight, metal_check.detect, outfeed.count, "
                 "panel.setpoint and the buttons are the line's."),
    },
    "accumulation-buffer": {
        "title": "Accumulation buffer",
        "task": ("Let cartons pile up behind the blade stop on a belt that "
                 "never stops, then release a batch. The pot is a release "
                 "window in ENCODER PULSES, which is a distance -- and this "
                 "run changes the drive's top speed halfway through."),
        "build": AccumulationScene,
        "observe": None,
        "grade": grade_accumulation,
        "summary": _summary_accumulation,
        "duration": 80.0,
        "references": ("good", "timed"),
        "tags": ("buffer.run, buffer.speed, outfeed.rotate, emitter.emit, "
                 "stop.raise, enc.reset, count_display.value, panel.green, "
                 "panel.red are yours to write; buffer.actual, enc.count, "
                 "stop.up, stop.down, exit_eye.detect, released.count, "
                 "panel.setpoint and the buttons are the line's."),
    },
    "batch-dosing": {
        "title": "Batch dosing",
        "task": ("Dose the litres on the pot into the tank. Trim pump.speed "
                 "until meter.rate is the rate you want, and end the batch on "
                 "meter.total rather than on a clock -- this run re-rates the "
                 "pump between the two batches."),
        "build": BatchDosingScene,
        "observe": None,
        "grade": grade_batch_dosing,
        "summary": _summary_batch,
        "duration": 80.0,
        "references": ("good", "timed", "noreset"),
        "tags": ("pump.run, pump.speed, meter.reset, tank.fill, tank.drain, "
                 "flow_gauge.value, total_display.value, level_readout.value, "
                 "panel.green, panel.red are yours to write; pump.flow, "
                 "pump.fault, meter.rate, meter.total, tank.level, "
                 "panel.setpoint and the buttons are the plant's."),
    },
    "guarded-cell": {
        "title": "Guarded cell",
        "task": ("Run the transfer cell WITHOUT ever writing belt.rotate. You "
                 "command starter.coil and the contactor runs the motor. The "
                 "safety relay holds your coil off until both gate leaves are "
                 "shut and somebody resets it -- and the relay closing hands "
                 "the coil back, it does not start anything. Bridge the "
                 "scanner for each carton, within its own mute limit."),
        "build": GuardedCellScene,
        "observe": None,
        "grade": grade_guarded_cell,
        "summary": _summary_guarded,
        "duration": 70.0,
        "references": ("good", "autostart", "writesbelt", "tapedmute"),
        "tags": ("relay.reset, starter.coil, scanner.mute, emitter.emit, "
                 "cylinder.extend, cylinder.retract, panel.green, panel.red are "
                 "yours to write; relay.k1/k2/cha/chb/fault, guard_a.closed, "
                 "guard_b.closed, starter.aux, starter.overload, scanner.stop, "
                 "scanner.warn, scanner.muted, mute_eye.detect, "
                 "push_eye.detect, cylinder.extended, cylinder.retracted, "
                 "transferred.count, line_end.count are the cell's. "
                 "belt.rotate is the motor's, and writing it fails the "
                 "exercise."),
    },
    "pick-and-place-cell": {
        "title": "Pick & place cell",
        "task": ("Sequence three motions against feedback, not timers: ramp "
                 "the infeed, index each carton, then travel, lower, grip, "
                 "raise, travel, release onto the outfeed. This run slows the "
                 "rail down halfway through."),
        "build": PickPlaceScene,
        "observe": None,
        "grade": grade_pick_place,
        "summary": _summary_pick_place,
        "duration": 75.0,
        "references": ("good", "timed"),
        "tags": ("infeed.run, infeed.speed, pickstation.rotate, emitter.emit, "
                 "scanner.enable, gantry.target, gantry.lower, gantry.grip, "
                 "rate.value, alarm.beacon, alarm.horn, panel.green, panel.red "
                 "are yours to write; infeed.actual, scanner.code, "
                 "scanner.read, scanner.present, atstation.detect, "
                 "gantry.position, gantry.inposition, gantry.lowered, "
                 "gantry.raised, gantry.holding, gantry.fault, outfeed.count "
                 "and the buttons are the cell's."),
    },
}


# --- integrity ---------------------------------------------------------

def check_integrity(watched: Watched, engine: GradedEngine, report: Report,
                    duration: float) -> bool:
    """The checks that are about the run rather than the program. Returns
    False if the run cannot be marked at all."""
    report.evidence["sessions"] = engine.sessions
    report.evidence["forces"] = engine.forces
    report.evidence["input_write_attempts"] = engine.input_writes
    report.evidence["forced_tags"] = watched.forced
    report.evidence["ticks"] = watched.ticks
    report.evidence["sim_seconds"] = round(watched.sim_time, 2)

    if not engine.sessions:
        report.verdict = ERROR
        report.headline = "no controller ever connected"
        return False

    dropped = [s for s in engine.sessions if s["disconnected_at"] is not None]
    report.add("controller.stayed_connected",
               engine.controller_connected and len(engine.sessions) == 1,
               f"{len(engine.sessions)} session(s), "
               f"{len(dropped)} of them dropped before the end")

    if watched.forced or engine.forces:
        report.verdict = DISQUALIFIED
        names = sorted(set(watched.forced) |
                       {t for f in engine.forces for t in f["set"]})
        report.headline = "tags were forced: " + ", ".join(names)
        report.add("integrity.no_forced_tags", False,
                   f"forced: {', '.join(names)}")
        report.feedback.append(
            "A forced tag is a value that disagrees with the simulation on "
            "purpose. It is the right tool for fault injection and not for a "
            "graded run, so this run was not marked. Clear every force "
            "(`force` with `clear`, or the engine's tag inspector) and run "
            "again.")
        return False
    report.add("integrity.no_forced_tags", True, "no tag was forced")

    report.add("integrity.no_input_writes",
               not engine.input_writes,
               "no writes aimed at simulator-owned tags" if not engine.input_writes
               else f"{len(engine.input_writes)} write(s) aimed at simulator-owned "
                    f"tags, which the engine refused: "
                    f"{sorted({t for w in engine.input_writes for t in w['tags']})}")
    if engine.input_writes:
        report.feedback.append(
            "Something tried to write a tag the simulator owns. `kind` is from "
            "the controller's point of view: `input` means the simulator writes "
            "it and you read it. The engine refused those writes.")
    return True


# --- reference controllers ---------------------------------------------
#
# Built-in stand-ins for a student, so the grader can be pointed at a known
# answer. Two reasons they exist. An instructor wants to know the rubric is
# alive before they trust a mark -- a grader that fails everything looks
# exactly like a cohort that cannot program. And a grader nobody can make PASS
# is the same bug as a test that passes while the simulation does nothing
# (AGENTS.md gotcha 16), which is why `good` is the first one.
#
# They connect over a real websocket through `TagBusClient` -- the same client
# `factoryforge-sidecar connect` uses -- so they cross the same seam a real
# controller does. They run in this process, which a real one never does.

#: Two of these are the same on every scene, so they are written once: a
#: controller that connects and does nothing must never be able to pass, and a
#: controller that forces its way to a flattering number must be disqualified
#: rather than failed. The rest are per scene, because a wrong answer is only
#: interesting when it is wrong about that scene's own lesson.
SHARED_REFERENCES = ("idle", "forcer")

#: The emitter makes one carton per rising edge. 1.8s apart is comfortably
#: more than the 1.2s a carton takes to clear the pusher.
EMIT_PULSE = 0.2
EMIT_GAP = 1.6

#: A reference controller's scan. Comfortably above Windows' 15.6 ms timer
#: floor, where a shorter sleep does not sleep at all (AGENTS.md gotcha 2).
SCAN = 0.05


async def _feed(bus, stop: asyncio.Event, gap: float = EMIT_GAP,
                tag: str = "emitter.emit") -> None:
    while not stop.is_set():
        await bus.write(tag, True)
        await asyncio.sleep(EMIT_PULSE)
        await bus.write(tag, False)
        await asyncio.sleep(gap)


class Scanner:
    """A reference controller's scan loop, with the panel already solved.

    Every scene below that has an operator station wants the same three things
    -- momentary edges, a normally-closed mushroom, a latch only Reset clears --
    and writing that four times would be four chances to write it differently.
    This is the same contract `Station` in `tools/try_scene.py` implements
    against the 3D engine, and it is deliberately the *correct* one: a wrong
    reference is wrong about its scene's lesson, not about the panel.
    """

    def __init__(self, bus, latch_estop: bool = True) -> None:
        self.bus = bus
        self.latch_estop = latch_estop
        self.running = False
        self.tripped = False
        self._prev = {"start": False, "stop": False, "reset": False}

    def bit(self, tag_id: str) -> bool:
        value = self.bus.read(tag_id)
        return bool(value) if value is not None else False

    def num(self, tag_id: str) -> float:
        value = self.bus.read(tag_id)
        return float(value) if value is not None else 0.0

    @property
    def setpoint(self) -> float:
        return self.num("panel.setpoint")

    def scan(self) -> dict[str, bool]:
        now = {k: self.bit(f"panel.{k}") for k in ("start", "stop", "reset")}
        edges = {k: now[k] and not self._prev[k] for k in now}
        self._prev = now

        healthy = self.bit("panel.estop")
        if self.latch_estop and not healthy:
            self.tripped = True
        elif edges["reset"]:
            self.tripped = False

        if self.tripped or edges["stop"]:
            self.running = False
        elif edges["start"] and (healthy or not self.latch_estop):
            self.running = True
        edges["healthy"] = healthy
        return edges

    def lamps(self) -> dict:
        return {"panel.green": self.running, "panel.red": self.tripped}


async def run_scan(bus, stop: asyncio.Event, body, period: float = SCAN) -> None:
    """Call `body(dt)` on a fixed scan until told to stop.

    `dt` is REAL elapsed time, not the nominal period. Passing the period is
    the mistake AGENTS.md gotcha 3 records for the engines themselves --
    "stepping once per sleep(tick_ms) runs the sim slow" -- arriving here
    instead. A scan takes `period` plus however long the body and the event
    loop took, and the plant advances by that whole amount because it runs on
    its own wall-clock accumulator. A controller counting only `period`
    therefore under-counts elapsed time, by nothing at all on an idle machine
    and by a lot on a busy one.

    That is not academic: the stopwatch reference for batch-dosing computes a
    cut-off in seconds, and under-counting made it run the pump past the
    number -- 23.8 L against a 22 L pot on Linux CI, where the same code
    lands 22.0 L here. Nine graded tests failed that way, and every one of
    them read as a flaky grader rather than as a controller whose clock was
    wrong.
    """
    last = time.perf_counter()
    while not stop.is_set():
        now = time.perf_counter()
        dt, last = now - last, now
        await body(dt)
        await asyncio.sleep(period)


async def _stroke(bus, delay: float, hold: float = 0.5) -> None:
    await asyncio.sleep(delay)
    await bus.write("pusher.extend", True)
    await asyncio.sleep(hold)
    await bus.write("pusher.extend", False)


async def _good(bus, stop: asyncio.Event) -> None:
    """What the exercise is asking for."""
    low, high = _beam_to_pusher_window()
    delay = (low + high) / 2

    async def on_update(values) -> None:
        # Updates are delta-only, so a True here *is* the rising edge -- no
        # edge memory, and no poll loop under the 15.6ms clock floor.
        if values.get("sensor_high.detect") is True:
            asyncio.create_task(_stroke(bus, delay))

    bus.on_update(on_update)
    await bus.write("conveyor.rotate", True)
    await _feed(bus, stop)


async def _blind(bus, stop: asyncio.Event) -> None:
    """Pushes on a timer and never reads a sensor. Passes a line that
    alternates; fails this one, which is why the feed pattern is shuffled."""
    await bus.write("conveyor.rotate", True)
    feeder = asyncio.create_task(_feed(bus, stop))
    try:
        while not stop.is_set():
            await asyncio.sleep(EMIT_PULSE + EMIT_GAP)
            await _stroke(bus, 1.0)
    finally:
        feeder.cancel()


async def _greedy(bus, stop: asyncio.Event) -> None:
    """Holds the plate out, so everything goes down the chute."""
    await bus.write("conveyor.rotate", True)
    await bus.write("pusher.extend", True)
    await _feed(bus, stop)


async def _idle(bus, stop: asyncio.Event) -> None:
    """Connects and does nothing. The run that must not be able to pass."""
    await stop.wait()


async def _forcer(bus, stop: asyncio.Event) -> None:
    """Forces whatever counter the scene has, so the numbers read well.

    Generic, because every scene has something a controller would rather lie
    about than earn. It pins every simulator-owned counter it can see.
    """
    targets = {tag.id: 20 for tag in bus.table
               if tag.id.endswith((".count", ".total"))
               or tag.id in ("counter.tall", "counter.short")}
    run_tags = [t for t in ("conveyor.rotate", "belt.rotate", "buffer.run",
                            "infeed.rotate", "scale.rotate") if t in bus.table]
    for tag_id in run_tags:
        await bus.write(tag_id, True)
    feeder = (asyncio.create_task(_feed(bus, stop))
              if "emitter.emit" in bus.table else None)
    try:
        while not stop.is_set():
            await bus.force(targets or {"panel.green": True})
            await asyncio.sleep(0.5)
    finally:
        if feeder is not None:
            feeder.cancel()


# --- start / stop station references ------------------------------------

async def _ss_body(bus, stop, latch_estop: bool, stop_at_target: bool) -> None:
    """One implementation, three behaviours, so the two wrong ones differ from
    the right one in exactly one place and nothing else."""
    scanner = Scanner(bus, latch_estop=latch_estop)
    state = {"made": 0, "present": False, "feed": 0.0, "emit": False}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        if edges["reset"]:
            state["made"] = 0

        target = int(round(scanner.setpoint))
        present = scanner.bit("part_present.detect")
        if present and not state["present"] and scanner.running:
            state["made"] += 1
        state["present"] = present

        if stop_at_target and target > 0 and state["made"] >= target:
            scanner.running = False

        if scanner.running:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 1.2 if state["emit"] else 0.3
        else:
            state["emit"] = False

        await bus.write_many({"belt.rotate": scanner.running,
                              "emitter.emit": state["emit"],
                              "produced.value": state["made"],
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _ss_good(bus, stop):
    """What the exercise asks for: a latching E-stop and a batch on the pot."""
    await _ss_body(bus, stop, latch_estop=True, stop_at_target=True)


async def _ss_noestop(bus, stop):
    """Reads Start and Stop and never reads the mushroom. The belt keeps
    running through the strike, which is the one thing this station is for."""
    await _ss_body(bus, stop, latch_estop=False, stop_at_target=True)


async def _ss_runon(bus, stop):
    """Counts, displays the count, and never stops at the target -- the batch
    controller that is really just a conveyor with a display on it."""
    await _ss_body(bus, stop, latch_estop=True, stop_at_target=False)


# --- tank level control references --------------------------------------

async def _tank_body(bus, stop, *, gain: float, deadband: float,
                     fixed: float | None) -> None:
    """One implementation, three behaviours.

    `gain` with no deadband is proportional control on a plant whose only
    outflow is the drain valve, so the error really does go to zero. `deadband`
    turns it into a pair of float switches. `fixed` ignores the pot.
    """
    scanner = Scanner(bus)

    async def body(dt: float) -> None:
        scanner.scan()
        level = scanner.num("tank.level")
        setpoint = fixed if fixed is not None else scanner.setpoint
        fill = drain = 0.0
        if scanner.running:
            error = setpoint - level
            if deadband > 0.0:
                if error > deadband:
                    fill = 100.0
                elif error < -deadband:
                    drain = 100.0
            else:
                fill = min(max(error * gain, 0.0), 100.0)
                drain = min(max(-error * gain, 0.0), 100.0)
        await bus.write_many({"tank.fill": fill, "tank.drain": drain,
                              "level_readout.value": int(round(level)),
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _tank_good(bus, stop):
    """Proportional, modulating rather than saturating. A gain that pins the
    valve at 100 % until the setpoint arrives is bang-bang wearing a float's
    clothes, and it hides the nonlinearity this scene exists to show."""
    await _tank_body(bus, stop, gain=1.6, deadband=0.0, fixed=None)


async def _tank_bangbang(bus, stop):
    """A pair of float switches six percent apart. It reaches the setpoint --
    and then parks at the edge of the band, because with both valves shut this
    tank has no outflow at all."""
    await _tank_body(bus, stop, gain=0.0, deadband=6.0, fixed=None)


async def _tank_fixedsp(bus, stop):
    """Good control of the wrong number. Holds 70 % beautifully and never reads
    the pot, which is invisible until somebody turns it."""
    await _tank_body(bus, stop, gain=1.6, deadband=0.0, fixed=70.0)


# --- heat treat station references ---------------------------------------

async def _oven_body(bus, stop, *, gain: float, integral_gain: float,
                     deadband: float) -> None:
    scanner = Scanner(bus)
    state = {"integral": 0.0, "on": False}

    async def body(dt: float) -> None:
        scanner.scan()
        temperature = scanner.num("oven.temperature")
        setpoint = scanner.setpoint
        power = 0.0
        if scanner.running:
            error = setpoint - temperature
            if deadband > 0.0:
                # Real hysteresis, because a thermostat without it chatters the
                # contactor to death -- and the hysteresis is precisely what
                # puts the swing in. Element on below setpoint minus the band,
                # off above setpoint plus it, latched in between.
                if temperature <= setpoint - deadband:
                    state["on"] = True
                elif temperature >= setpoint + deadband:
                    state["on"] = False
                power = 100.0 if state["on"] else 0.0
            else:
                proportional = error * gain
                if integral_gain > 0.0 and -100.0 < proportional < 100.0:
                    # Only off the stops. Integrating through a cold start's
                    # flat-out heating is textbook windup, and it is what turns
                    # a working PI into a 40 degC overshoot.
                    state["integral"] = min(max(state["integral"] + error * dt,
                                                -140.0), 140.0)
                power = min(max(proportional + state["integral"] * integral_gain,
                                0.0), 100.0)
        else:
            state["integral"] = 0.0
        await bus.write_many({"oven.heater": power,
                              "temp_gauge.value": temperature,
                              "temp_readout.value": int(round(temperature)),
                              "alarm.beacon": temperature > setpoint + 25.0,
                              **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _oven_good(bus, stop):
    """PI, with enough integral authority to supply the whole standing output.
    An integral that can only contribute a tenth of what the plate loses cannot
    close the offset it was added to close."""
    await _oven_body(bus, stop, gain=3.5, integral_gain=0.6, deadband=0.0)


async def _oven_ponly(bus, stop):
    """The lesson, written out. Gain 3.5 and nothing else, so the plate parks
    exactly (loss / element) / gain degrees short -- and parks somewhere else
    when the setpoint moves, because the offset depends on the setpoint."""
    await _oven_body(bus, stop, gain=3.5, integral_gain=0.0, deadband=0.0)


async def _oven_thermostat(bus, stop):
    """Element full on below setpoint, off above. It reaches the setpoint every
    couple of seconds from alternate sides and never holds it: this plant
    always loses heat, so the cycling never stops."""
    await _oven_body(bus, stop, gain=0.0, integral_gain=0.0, deadband=4.0)


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
             "pending": [], "drop_at": None}

    async def body(dt: float) -> None:
        scanner.scan()
        now = time.perf_counter()

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
             "this_is_metal": False}

    async def body(dt: float) -> None:
        scanner.scan()
        now = time.perf_counter()
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
             "feed": 0.0, "emit": False}

    async def body(dt: float) -> None:
        scanner.scan()
        now = time.perf_counter()
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


# --- batch dosing references ----------------------------------------------

async def _bd_body(bus, stop, *, by_litres: bool, zero_the_meter: bool,
                   open_loop: bool = False) -> None:
    scanner = Scanner(bus)
    #: The dose rate the inner loop aims for, and what a stopwatch would make
    #: of it: 20 L at 100 L/min is twelve seconds. Right once.
    DOSE_RATE = 100.0
    CREEP_LITRES = 4.0
    KP, KI = 0.25, 1.0
    state = {"phase": "zero", "speed": 0.0, "integral": 0.0, "since": 0.0,
             "seconds": 0.0}

    async def body(dt: float) -> None:
        edges = scanner.scan()
        target = scanner.setpoint
        total = scanner.num("meter.total")
        rate = scanner.num("meter.rate")

        if edges["start"]:
            state["phase"] = "zero" if zero_the_meter else "dose"
            state["integral"] = 0.0
            state["seconds"] = 0.0
        if not scanner.running:
            state["phase"] = "idle"

        zeroing = False
        dosing = False
        if scanner.running:
            if state["phase"] == "zero":
                # A level, not an edge: hold it until the totaliser reads back
                # zero, so this batch counts from zero rather than from what
                # the last one left.
                zeroing = True
                if total <= 0.0:
                    state["phase"] = "dose"
            elif state["phase"] == "dose":
                dosing = True
                state["seconds"] += dt
                # The stopwatch answer is calibrated against the pump's
                # nameplate, not against a dose rate it never reaches: twenty
                # litres at 120 L/min is ten seconds, and at full speed that is
                # exactly right -- once.
                # x1.07 because this controller was calibrated on the line,
                # the way a student would calibrate it: the pump ramps, the
                # command lands a scan late, and the first batch came out a
                # litre short until the number was nudged. That calibration is
                # the whole trap -- it is a measurement of one pump on one day.
                seconds_for = (target / BD_RATED_FIRST * 60.0 * 1.07 if open_loop
                               else target / DOSE_RATE * 60.0)
                done = (total >= target if by_litres
                        else state["seconds"] >= seconds_for)
                if done:
                    state["phase"] = "done"
                    dosing = False

        flow_setpoint = 0.0
        if dosing and open_loop:
            state["speed"] = 100.0
        elif dosing:
            remaining = max(target - total, 0.0)
            taper = (1.0 if remaining >= CREEP_LITRES
                     else max(remaining / CREEP_LITRES, 0.25))
            flow_setpoint = DOSE_RATE * (taper if by_litres else 1.0)
            error = flow_setpoint - rate
            if 0.5 < state["speed"] < 99.5:
                state["integral"] = min(max(state["integral"] + error * KI * dt,
                                            -100.0), 100.0)
            state["speed"] = min(max(error * KP + state["integral"], 0.0), 100.0)
        else:
            state["speed"] = 0.0
            state["integral"] = 0.0

        await bus.write_many({
            "meter.reset": zeroing,
            "pump.run": dosing,
            "pump.speed": state["speed"],
            "tank.fill": 0.0,
            "tank.drain": 0.0,
            "flow_gauge.value": rate,
            "total_display.value": int(total),
            "level_readout.value": int(round(scanner.num("tank.level"))),
            **scanner.lamps()})

    await run_scan(bus, stop, body)


async def _bd_good(bus, stop):
    """Zeroes the totaliser, trims the pump against the meter, and ends the
    batch on litres."""
    await _bd_body(bus, stop, by_litres=True, zero_the_meter=True)


async def _bd_timed(bus, stop):
    """Runs the pump flat out for the number of seconds the pot's litres take
    at the pump's nameplate flow. Exactly right until the pump is re-rated,
    and then exactly half."""
    await _bd_body(bus, stop, by_litres=False, zero_the_meter=True,
                   open_loop=True)


async def _bd_noreset(bus, stop):
    """Ends on litres, correctly, and never zeroes the totaliser -- so the
    second batch is over before it starts."""
    await _bd_body(bus, stop, by_litres=True, zero_the_meter=False)


# --- guarded cell references ----------------------------------------------

async def _gc_body(bus, stop, *, latch_the_trip: bool, write_the_motor: bool,
                   mute_window: float) -> None:
    """One implementation, four behaviours.

    `latch_the_trip` is the one that matters. With it, a safety trip holds the
    coil off until somebody presses Start again -- the relay handing the coil
    back is a permissive and not a command. Without it, the coil follows "the
    cell should be running" and the machine restarts itself the moment the
    relay closes, which is the failure the whole cell exists to prevent.
    """
    scanner = Scanner(bus)
    state = {"safety_trip": True, "mute_until": 0.0, "eye": False,
             "push": False, "transfer": "idle", "wait": 0.0,
             "feed": 0.0, "emit": False}
    #: Push eye to the transfer station: 0.4 m at 0.5 m/s.
    PUSH_DELAY = 0.8

    async def body(dt: float) -> None:
        now = time.perf_counter()
        # Read Reset before the panel scan consumes it: this cell has two
        # things to reset, the relay's latch and the controller's, and one
        # button does both the way it does on a real cell.
        reset = scanner.bit("panel.reset")
        scanner.scan()

        # Neither of these is computed here. The relay decides whether its
        # contacts are closed and the scanner decides whether its field is
        # clear; recomputing either would be a second, unrated opinion about a
        # safety function.
        relay_closed = scanner.bit("relay.k1") and scanner.bit("relay.k2")
        field_clear = scanner.bit("scanner.stop")

        if not relay_closed or not field_clear:
            state["safety_trip"] = True
        if reset and relay_closed and field_clear:
            state["safety_trip"] = False
        if latch_the_trip and state["safety_trip"]:
            scanner.running = False

        running = scanner.running and not (latch_the_trip and state["safety_trip"])
        coil = running if latch_the_trip else (relay_closed and field_clear
                                               and scanner.running)

        eye = scanner.bit("mute_eye.detect")
        if eye and not state["eye"] and running:
            state["mute_until"] = now + mute_window
        state["eye"] = eye
        mute = running and now < state["mute_until"]

        motor = scanner.bit("starter.aux")
        if motor:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["emit"] = not state["emit"]
                state["feed"] = 4.0 if state["emit"] else 0.3
        else:
            state["emit"] = False

        extended = scanner.bit("cylinder.extended")
        retracted = scanner.bit("cylinder.retracted")
        push = scanner.bit("push_eye.detect")
        if not motor:
            state["transfer"] = "idle" if retracted else "retracting"
        else:
            if push and not state["push"] and state["transfer"] == "idle" and retracted:
                state["transfer"] = "waiting"
                state["wait"] = PUSH_DELAY
            if state["transfer"] == "waiting":
                state["wait"] -= dt
                if state["wait"] <= 0.0:
                    state["transfer"] = "extending"
            elif state["transfer"] == "extending" and extended:
                state["transfer"] = "retracting"
            elif state["transfer"] == "retracting" and retracted:
                state["transfer"] = "idle"
        state["push"] = push

        writes = {
            # The operator's own Reset, passed through. The relay acts on the
            # rising edge only, so a level held true would close it once at
            # power-up and never again -- and a level that re-closed it by
            # itself would be the automatic restart the relay exists to refuse.
            "relay.reset": reset,
            "starter.coil": coil,
            "scanner.mute": mute,
            "emitter.emit": state["emit"],
            "cylinder.extend": state["transfer"] == "extending",
            "cylinder.retract": state["transfer"] == "retracting",
            "panel.green": running,
            "panel.red": state["safety_trip"] or scanner.tripped,
        }
        if write_the_motor:
            writes["belt.rotate"] = running
        await bus.write_many(writes)

    await run_scan(bus, stop, body)


async def _gc_good(bus, stop):
    """Latches the trip, so a closing relay starts nothing."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=False,
                   mute_window=3.5)


async def _gc_autostart(bus, stop):
    """Holds the coil for as long as the cell *should* be running, so the motor
    restarts by itself the instant the relay closes. Nobody pressed anything."""
    await _gc_body(bus, stop, latch_the_trip=False, write_the_motor=False,
                   mute_window=3.5)


async def _gc_writesbelt(bus, stop):
    """Right about the relay and wrong about who runs the motor: it drives
    belt.rotate itself, which is the one tag this exercise forbids."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=True,
                   mute_window=3.5)


async def _gc_tapedmute(bus, stop):
    """Bridges the scanner for twelve seconds a carton, past the scanner's own
    six-second limit, so the guard it is supposed to be muting is simply off."""
    await _gc_body(bus, stop, latch_the_trip=True, write_the_motor=False,
                   mute_window=12.0)


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
             "codes": set()}

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


#: `{scene: {name: controller}}`, plus the two shared ones. Every scene has a
#: `good` that must pass and at least one wrong answer that must fail for that
#: scene's own reason -- the rubric is only known to work when both have been
#: watched (AGENTS.md gotcha 24).
REFERENCES: dict[str, dict] = {
    "sorting-by-height": {"good": _good, "blind": _blind, "greedy": _greedy},
    "start-stop-station": {"good": _ss_good, "noestop": _ss_noestop,
                           "runon": _ss_runon},
    "tank-level-control": {"good": _tank_good, "bangbang": _tank_bangbang,
                           "fixedsp": _tank_fixedsp},
    "heat-treat-station": {"good": _oven_good, "ponly": _oven_ponly,
                           "thermostat": _oven_thermostat},
    "light-curtain-sorting": {"good": _lc_good, "fixed": _lc_fixed,
                              "everyother": _lc_everyother},
    "roller-line-weighing": {"good": _rw_good, "metalonly": _rw_metalonly,
                             "fastfeed": _rw_fastfeed},
    "accumulation-buffer": {"good": _ab_good, "timed": _ab_timed},
    "batch-dosing": {"good": _bd_good, "timed": _bd_timed,
                     "noreset": _bd_noreset},
    "guarded-cell": {"good": _gc_good, "autostart": _gc_autostart,
                     "writesbelt": _gc_writesbelt, "tapedmute": _gc_tapedmute},
    "pick-and-place-cell": {"good": _pp_good, "timed": _pp_timed},
}

_SHARED = {"idle": _idle, "forcer": _forcer}


def reference_for(scene: str, kind: str):
    return REFERENCES.get(scene, {}).get(kind) or _SHARED.get(kind)


def reference_choices() -> tuple[str, ...]:
    names = set(SHARED_REFERENCES)
    for table in REFERENCES.values():
        names |= set(table)
    return tuple(sorted(names))


async def start_reference(kind: str, url: str, scene: str):
    """Connect a reference controller. Returns an awaitable that stops it."""
    from factoryforge_sidecar.tagbus import TagBusClient       # noqa: PLC0415

    controller = reference_for(scene, kind)
    if controller is None:
        raise RuntimeError(f"{scene} has no {kind!r} reference controller")

    bus = TagBusClient(url)
    stop = asyncio.Event()
    runner = asyncio.create_task(bus.run(stop))
    await asyncio.wait_for(bus.connected.wait(), timeout=15)
    deadline = time.perf_counter() + 15
    while bus.scene is None or len(bus.table) == 0:
        if time.perf_counter() > deadline:
            raise RuntimeError("reference controller never received a describe")
        await asyncio.sleep(0.05)

    task = asyncio.create_task(controller(bus, stop))

    async def shutdown() -> None:
        stop.set()
        task.cancel()
        for pending in (task, runner):
            try:
                await asyncio.wait_for(pending, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pending.cancel()
    return shutdown


# --- the run -----------------------------------------------------------

async def run_grading(args) -> Report:
    rubric = RUBRICS[args.scene]
    seed = args.seed if args.seed is not None else random.randrange(1, 2 ** 31)
    report = Report(scene=args.scene)
    report.evidence["seed"] = seed

    sim = rubric["build"](seed)
    watched = Watched(sim, observe=rubric["observe"])
    engine = GradedEngine(watched, port=args.bus_port)
    await engine.start()

    _announce(args, engine, rubric, seed)

    ticker = asyncio.create_task(engine._tick_loop())
    reference = None
    try:
        if args.reference:
            reference = await start_reference(args.reference, engine.url, args.scene)

        try:
            await asyncio.wait_for(engine.arrived.wait(), timeout=args.wait)
        except asyncio.TimeoutError:
            report.verdict = ERROR
            report.headline = (f"no controller connected within {args.wait:g}s")
            report.evidence["sessions"] = []
            return report

        if not args.quiet:
            print(f"controller connected after {engine.sessions[0]['connected_at']:.1f}s; "
                  f"grading for {args.duration:g}s\n", flush=True)

        # The PLANT's clock, not the wall's and not a tick count.
        #
        # Counting iterations would be wrong for the reason gotcha 2 gives: a
        # 500-step loop can finish in 10 ms on Windows. But wall-clock is wrong
        # too, and more quietly. The engine paces itself with a real-time
        # accumulator, so on a loaded machine it falls behind and a 78-second
        # wall-clock window buys less than 78 seconds of plant. A batch that
        # needed the tail of that window simply never completes, and the mark
        # changes because the marking machine was busy -- which for a grader an
        # instructor runs in a CI job is the worst property it could have.
        # Observed: this is exactly how the re-rated-pump case failed inside a
        # full suite run and passed alone.
        #
        # watched.sim_time advances inside tick(), on the fixed step, so it is
        # the same number the rubric already reasons about. The wall-clock
        # ceiling is a liveness bound, not the criterion: an engine that has
        # stopped stepping must not hang the run forever (HP-54), and it says
        # which of the two ended the window.
        ceiling = time.perf_counter() + args.duration * 4 + 30
        while watched.sim_time < args.duration:
            if time.perf_counter() > ceiling:
                report.feedback.append(
                    f"the plant only advanced {watched.sim_time:.1f}s of the "
                    f"{args.duration:g}s asked for before the wall-clock ceiling; "
                    f"the engine was not stepping, so this mark is not trustworthy")
                break
            await asyncio.sleep(0.05)

        if check_integrity(watched, engine, report, args.duration):
            rubric["grade"](watched, engine, report, args.duration)
            failed = [c for c in report.checks if not c.ok]
            report.verdict = FAIL if failed else PASS
            report.headline = (
                "every check met" if not failed
                else f"{len(failed)} of {len(report.checks)} checks failed")
    finally:
        ticker.cancel()
        if reference is not None:
            await reference()
        await engine.stop()
    return report


def _announce(args, engine: GradedEngine, rubric: dict, seed: int) -> None:
    if args.quiet:
        return
    print(f"FactoryForge grading — {rubric['title']}")
    print(f"  {rubric['task']}")
    print(f"  {rubric['tags']}")
    print()
    print(f"  tag bus   {engine.url}")
    # Not only the feed: the seed picks every number this run's exam chooses --
    # the batch size, the setpoints, the thresholds, the limits -- so quoting
    # it is what makes a disputed mark re-runnable exactly.
    print(f"  exam seed {seed}   window {args.duration:g}s")
    print()
    print("  Connect your controller with:")
    print(f"    python -m factoryforge_sidecar connect --driver <yours> "
          f"--port {engine.actual_port} -o <options>")
    print()
    print(f"  Waiting up to {args.wait:g}s ...", flush=True)


# --- output ------------------------------------------------------------

def to_json(report: Report, args, started: str) -> dict:
    return {
        "tool": "factoryforge-grade",
        "format": 1,
        "scene": report.scene,
        "student": args.student,
        "started": started,
        "duration_s": args.duration,
        "verdict": report.verdict,
        "exit_code": EXIT[report.verdict],
        "headline": report.headline,
        "checks": [c.to_json() for c in report.checks],
        "feedback": report.feedback,
        "evidence": report.evidence,
    }


def print_summary(report: Report, args) -> None:
    print()
    print("=" * 68)
    who = f" — {args.student}" if args.student else ""
    print(f"{report.verdict}{who}   {report.headline}")
    print("=" * 68)

    for check in report.checks:
        print(f"  [{'ok' if check.ok else 'XX'}] {check.id:<30} {check.detail}")

    evidence = report.evidence
    summary = RUBRICS.get(report.scene, {}).get("summary")
    # Only once the run reached a verdict about the plant. A disqualified or
    # aborted run has no plant evidence to print, and a summary that assumed
    # otherwise would raise on the one path a student most needs to read.
    if summary is not None and any(not c.id.startswith("integrity.")
                                   for c in report.checks):
        print()
        try:
            summary(evidence, lambda line: print("  " + line))
        except (KeyError, TypeError, ValueError):
            pass

    if report.feedback:
        print()
        for line in report.feedback:
            print(_wrap("  - " + line))

    failed = [c.id for c in report.checks if not c.ok]
    print()
    print(f"RESULT grade={report.verdict} scene={report.scene} "
          f"checks={len(report.checks) - len(failed)}/{len(report.checks)} "
          f"failed={','.join(failed) or 'none'} "
          f"forced={len(evidence.get('forced_tags', {}))}")


def _wrap(text: str, width: int = 76) -> str:
    import textwrap
    return textwrap.fill(text, width=width, subsequent_indent="    ")


# --- entry point -------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grade.py",
        description="Grade a PLC program against a FactoryForge scene, unattended.")
    parser.add_argument("--list", action="store_true", help="scenes this can mark")
    parser.add_argument("--scene", default="sorting-by-height",
                        help="scene id (see --list)")
    parser.add_argument("--student", default=None, help="name for the report")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to watch once the controller connects "
                             "(default: the scene's own, since an exercise that "
                             "heats a plate needs longer than one that pushes a "
                             "carton)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT,
                        help="seconds to wait for a controller before giving up")
    parser.add_argument("--seed", type=int, default=None,
                        help="feed pattern seed; reported, so a mark is reproducible")
    parser.add_argument("--bus-port", type=int, default=0,
                        help="tag bus port; 0 (the default) lets the OS pick, so "
                             f"runs never collide. Fixed: {SUGGESTED_PORTS.start}-"
                             f"{SUGGESTED_PORTS.stop - 1}")
    parser.add_argument("--json", dest="json_path",
                        help="write the full report here (- for stdout)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress everything but the RESULT line")
    parser.add_argument("--reference", choices=reference_choices(), default=None,
                        help="grade a built-in controller instead of waiting for "
                             "one, to check the grader itself. Which ones a scene "
                             "has is in --list")
    args = parser.parse_args(argv)

    if args.list:
        for scene_id, rubric in sorted(RUBRICS.items()):
            refs = ", ".join(tuple(rubric["references"]) + SHARED_REFERENCES)
            print(f"{scene_id}\n    {rubric['title']} — {rubric['task']}")
            print(f"    {rubric['duration']:g}s window; references: {refs}")
        return 0

    if args.scene not in RUBRICS:
        print(f"RESULT grade=ERROR no rubric for {args.scene!r}; "
              f"known: {', '.join(sorted(RUBRICS))}", file=sys.stderr)
        return EXIT[ERROR]
    if args.duration is None:
        args.duration = RUBRICS[args.scene]["duration"]
    if args.reference and reference_for(args.scene, args.reference) is None:
        print(f"RESULT grade=ERROR {args.scene} has no {args.reference!r} "
              f"reference; it has: "
              f"{', '.join(tuple(RUBRICS[args.scene]['references']) + SHARED_REFERENCES)}",
              file=sys.stderr)
        return EXIT[ERROR]
    if args.bus_port and args.bus_port not in SUGGESTED_PORTS:
        print(f"note: --bus-port {args.bus_port} is outside "
              f"{SUGGESTED_PORTS.start}-{SUGGESTED_PORTS.stop - 1}, which is the "
              f"range reserved for graded runs.", file=sys.stderr)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = asyncio.run(run_grading(args))

    if args.json_path:
        payload = json.dumps(to_json(report, args, started), indent=2)
        if args.json_path == "-":
            print(payload)
        else:
            Path(args.json_path).write_text(payload + "\n", encoding="utf-8")

    if args.quiet:
        print(f"RESULT grade={report.verdict} scene={report.scene} "
              f"{report.headline}")
    else:
        print_summary(report, args)
    return EXIT[report.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
