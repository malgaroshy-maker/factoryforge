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

**The verdict comes from the cartons, not from the tags.** The scene knows how
tall every carton it made was and which lane it ended in, and no controller can
reach that. Counters, sensor values and actuator commands are *evidence* -- the
part a student needs in order to fix anything -- but they are never the
criterion, because every one of them is reachable from the bus and a criterion
you can reach is a criterion you can fake.

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

Honest limits are in docs/GRADING.md. The short version: one scene, the
headless Python model of it rather than the 3D engine, and no marks for the
operator panel.
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

#: How long the grader keeps the scene running after the controller connects.
DEFAULT_DURATION = 60.0
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


def _quantise(value: float, step: float) -> float:
    return round(value / step) * step


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
    """Call `body(dt)` on a fixed scan until told to stop."""
    while not stop.is_set():
        await body(period)
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

        # Wall-clock, and deliberately not a tick count: the engine's own
        # accumulator decides how many fixed steps fit, and counting
        # iterations to measure time is how a 500-step loop finishes in 10 ms
        # on Windows (AGENTS.md gotcha 2).
        await asyncio.sleep(args.duration)

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
    print(f"  feed seed {seed}   window {args.duration:g}s")
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
