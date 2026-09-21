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
from factoryforge_sidecar.tags import TagTable, TagValue  # noqa: E402
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


#: Every scene this tool can mark. One, and the tool says so rather than
#: pretending: see docs/GRADING.md.
RUBRICS = {
    "sorting-by-height": {
        "title": "Sorting by height",
        "task": ("Run the belt, feed cartons, and push the tall ones down the "
                 "chute while the short ones carry on."),
        "build": build_sorting_scene,
        "observe": observe_sorting,
        "grade": grade_sorting,
        "tags": ("conveyor.rotate, emitter.emit, pusher.extend are yours to write; "
                 "sensor_low.detect, sensor_high.detect, pusher.extended, "
                 "pusher.retracted, counter.tall, counter.short are the line's."),
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

REFERENCE_CHOICES = ("good", "blind", "greedy", "idle", "forcer")

#: The emitter makes one carton per rising edge. 1.8s apart is comfortably
#: more than the 1.2s a carton takes to clear the pusher.
EMIT_PULSE = 0.2
EMIT_GAP = 1.6


async def _feed(bus, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await bus.write("emitter.emit", True)
        await asyncio.sleep(EMIT_PULSE)
        await bus.write("emitter.emit", False)
        await asyncio.sleep(EMIT_GAP)


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
    """Runs the line, never sorts, and forces the counters to look right."""
    await bus.write("conveyor.rotate", True)
    feeder = asyncio.create_task(_feed(bus, stop))
    try:
        while not stop.is_set():
            await bus.force({"counter.tall": 20, "counter.short": 20})
            await asyncio.sleep(0.5)
    finally:
        feeder.cancel()


_REFERENCE = {"good": _good, "blind": _blind, "greedy": _greedy,
              "idle": _idle, "forcer": _forcer}


async def start_reference(kind: str, url: str):
    """Connect a reference controller. Returns an awaitable that stops it."""
    from factoryforge_sidecar.tagbus import TagBusClient       # noqa: PLC0415

    bus = TagBusClient(url)
    stop = asyncio.Event()
    runner = asyncio.create_task(bus.run(stop))
    await asyncio.wait_for(bus.connected.wait(), timeout=15)
    deadline = time.perf_counter() + 15
    while bus.scene is None or len(bus.table) == 0:
        if time.perf_counter() > deadline:
            raise RuntimeError("reference controller never received a describe")
        await asyncio.sleep(0.05)

    task = asyncio.create_task(_REFERENCE[kind](bus, stop))

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
            reference = await start_reference(args.reference, engine.url)

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
    if "pusher" in evidence:
        pusher = evidence["pusher"]
        print()
        print(f"  fed {evidence['emitted']}, sorted {evidence['sorted']}, "
              f"{evidence['still_on_belt']} still on the belt")
        print(f"  chute   {evidence['chute']['total']:>3}  "
              f"({evidence['chute']['tall']} tall, {evidence['chute']['short']} short)")
        print(f"  far end {evidence['far_end']['total']:>3}  "
              f"({evidence['far_end']['short']} short, {evidence['far_end']['tall']} tall)")
        window = pusher["required_window_s"]
        measured = pusher["mean_delay_after_beam_s"]
        print(f"  pusher fired {pusher['fired']}x, "
              f"{'never' if measured is None else f'{measured:.2f}s'} after the beam "
              f"(needs {window[0]:.2f}–{window[1]:.2f}s)")
        if evidence["misrouted"]:
            print("  misrouted:")
            for entry in evidence["misrouted"][:8]:
                print(f"    carton {entry['carton']:>3} ({entry['height']}) "
                      f"-> {entry['lane']} at {entry['at']:.1f}s")

    if report.feedback:
        print()
        for line in report.feedback:
            print(_wrap("  - " + line))

    print()
    print(f"RESULT grade={report.verdict} scene={report.scene} "
          f"sorted={evidence.get('sorted', 0)} "
          f"misrouted={len(evidence.get('misrouted', []))} "
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
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help="seconds to watch once the controller connects")
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
    parser.add_argument("--reference", choices=REFERENCE_CHOICES, default=None,
                        help="grade a built-in controller instead of waiting for "
                             "one, to check the grader itself")
    args = parser.parse_args(argv)

    if args.list:
        for scene_id, rubric in sorted(RUBRICS.items()):
            print(f"{scene_id}\n    {rubric['title']} — {rubric['task']}")
        return 0

    if args.scene not in RUBRICS:
        print(f"RESULT grade=ERROR no rubric for {args.scene!r}; "
              f"known: {', '.join(sorted(RUBRICS))}", file=sys.stderr)
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
