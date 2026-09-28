#!/usr/bin/env python3
"""Run a shipped scene headless, drive it with the grader's own `good`
controller, and check the scene completes on the real engine.

    python tools/try_scene.py --list
    python tools/try_scene.py --scene sorting-by-height
    python tools/try_scene.py --scene tank-level-control --verbose
    python tools/try_scene.py --scene batch-dosing --reference timed
    python tools/try_scene.py --scene pivot-divert --scene-file my_copy.json

Spawns the engine itself (headless, the scene's own template if it has one)
and tears it down when done -- no separate `godot --headless ...` command to
remember, and no risk of `connect --driver mock` (§2.6), which connects a
client and then drives nothing: the most obvious "let me just try it" command
leaving the scene more dead than doing nothing at all.

One RESULT line, exit 0 on a real pass and 1 otherwise -- the same convention
tools/check_protocol.py and tools/check_force_while_paused.py already set.

**One reference controller per scene (IP-20).** Every graded scene has a
`good` reference controller in `sidecar/factoryforge_sidecar/grading/
reference/`, and this is the program that drives it here: the same Python,
through the same `TagBusClient` a student's sidecar uses, over the real tag
bus, against the real rigid-body engine. This file used to carry a second
controller per scene, written separately from the grader's -- 3281 lines of
them -- so the two could disagree about a scene and nothing would say so. The
grader's plant model is read from the template (IP-19), which makes its tag
set and its numbers agree with the engine's; this is the behavioural half. If
`good` passes the grader's model and cannot complete the scene in the 3D
engine, the model and the engine disagree about how the plant behaves, and
this fails.

**The examiner.** The grader's plant model runs its own exam: it presses
Start, turns the pot, strikes the mushroom (`grading/plant.py`, `Script`).
The engine has nobody at its panel, so each scene's `Trial` below carries the
examiner's steps, done over the bus the only way a client can touch an input:
a force, held for `PRESS` -- the engine's own momentary hold, 0.2 s
(`ButtonPanel.DefaultPressHold`, IP-31), which is also what the grader's hand
holds (IP-34) -- and then released. Only the panel steps are copied. The
grader's exams also reach into the machinery (a slower belt, a re-rated
pump), which nothing on the bus can do; those are the grader's business and
are not repeated here.

**What "completes" means.** Each `Trial` has its own engine-side measure,
built only from what the engine reports -- counters, sensors, analog inputs --
never from what the controller claims about itself. It is not the grader's
rubric: several rubrics read plant ledgers that have no tag (which carton went
where), and Jolt is not reproducible (§4), so every measure is a band, not an
exact count. What each one has to show is that real work happened (AGENTS.md
gotcha 16) and that the work is the scene's own: cartons in the lane their
height says, a batch on its number, a temperature held. Each Trial's
docstring says why that measure and not another.

`--reference` runs another of the scene's references instead of `good`. A
wrong one should not complete: that is how a measure is shown to have teeth
(gotcha 24). `--scene-file` opens a scene file in the engine in place of the
template, while the reference keeps reading the shipped template -- the way to
show a mismatch between the two fails here.

Scenes with no grader reference keep a controller of their own at the end of
this file (`SOLVERS`); today that is only the palletising cell, which has no
graded exam.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
MANIFEST = ENGINE / "templates" / "manifest.json"
sys.path.insert(0, str(ROOT / "sidecar"))
logging.disable(logging.WARNING)

from factoryforge_sidecar import protocol as proto  # noqa: E402
from factoryforge_sidecar.tagbus import TagBusClient  # noqa: E402

#: The engine's tag bus. FF_BUS_PORT lets this run against a port other
#: than the default, so two exercises can run at once on one machine --
#: this spawns its own engine, so it has to agree with it about the port.
PORT = int(os.environ.get("FF_BUS_PORT", "7411"))


def find_godot() -> str | None:
    """$GODOT, then PATH, then the usual download locations.

    One implementation, in run.py, rather than a third copy drifting from the
    launcher's: they all have to agree about which Godot a machine has, or
    `run.py` opens one build while this script spawns another.
    """
    sys.path.insert(0, str(ROOT))
    from run import find_godot as locate     # noqa: PLC0415 — avoids a cycle at import time
    return locate()


def load_manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def port_listening() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


# --- engine lifecycle -------------------------------------------------

class Engine:
    """The headless subprocess. Output goes to a temp file rather than a
    pipe: an unread PIPE can deadlock the child once its buffer fills
    (AGENTS.md gotcha 15), and a file gives something to show the user if
    the connect step times out."""

    def __init__(self, godot: str, entry: dict, scene_file: str | None = None) -> None:
        args = [godot, "--headless", "--path", str(ENGINE), "--",
                f"--bus-port={PORT}"]
        scene = scene_file or entry["path"]
        if scene:
            args.append(f"--scene={scene}")

        self._log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
        self.proc = subprocess.Popen(args, stdout=self._log, stderr=subprocess.STDOUT, text=True)

    def tail(self, lines: int = 20) -> str:
        self._log.flush()
        with open(self._log.name, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._log.close()
        try:
            os.unlink(self._log.name)
        except OSError:
            pass


async def wait_for_port(timeout: float, proc: subprocess.Popen | None = None) -> str | None:
    """None once the port is open, otherwise why it never opened.

    `proc` is the engine we are waiting on. Without it this loop cannot tell
    "still starting" from "died ten seconds ago": it burns the whole timeout
    either way and then blames the port, which is the one thing that is not
    wrong. A wait that cannot observe its own failure mode is the same defect
    as a test that cannot -- see AGENTS.md gotcha 16, and the 52-minute poll
    this project already paid for once (HP-54).
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if port_listening():
            return None
        if proc is not None and proc.poll() is not None:
            return f"the engine exited with code {proc.returncode} before opening the port"
        await asyncio.sleep(0.1)
    return f"the engine did not open the port within {timeout:g}s"


async def connect(bus: TagBusClient | None = None,
                  timeout: float = 15.0) -> tuple[TagBusClient, asyncio.Task]:
    """Connect `bus` (a plain client if None) and wait for its describe."""
    if bus is None:
        bus = TagBusClient(f"ws://127.0.0.1:{PORT}/tagbus")
    runner = asyncio.create_task(bus.run())
    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=timeout)
        deadline = time.perf_counter() + timeout
        while bus.scene is None or len(bus.table) == 0:
            if time.perf_counter() > deadline:
                raise RuntimeError("connected but never received a describe")
            await asyncio.sleep(0.05)
    except (asyncio.TimeoutError, RuntimeError):
        runner.cancel()
        raise
    return bus, runner


# =====================================================================
#  The grader's reference controllers, on the engine (IP-20)
# =====================================================================

class Recorder(TagBusClient):
    """The bus client a reference controller drives the engine through, with a
    timeline of every value it saw.

    Recorded where frames arrive (`_handle`) and where the controller writes
    (`write`), not by polling: a poll every few tens of milliseconds can miss
    a pulse, and on Windows a short sleep does not sleep at all (AGENTS.md
    gotcha 2). The cache `read()` returns is updated in the same place, so the
    timeline is exactly what the controller could have seen.

    Every time here is seconds since `open()` -- the moment the reference was
    attached and the examiner's clock started, the engine-side twin of the
    grader's window opening.
    """

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.opened: float | None = None
        #: tag id -> [(perf_counter, visible value)], one entry per change.
        self._timeline: dict[str, list[tuple[float, object]]] = {}
        #: What the examiner did, and when (seconds since open).
        self.events: list[tuple[float, str]] = []
        #: (perf_counter, engine tick) for every update, to report the
        #: engine's pace: a slow machine is a reason, not a verdict.
        self.ticks: list[tuple[float, int]] = []
        self.tick_ms = proto.DEFAULT_TICK_MS

    # --- recording ---

    def _note(self, tag_id: str) -> None:
        if tag_id not in self.table:
            return
        value = self.read(tag_id)
        series = self._timeline.setdefault(tag_id, [])
        if not series or series[-1][1] != value:
            series.append((time.perf_counter(), value))

    async def _handle(self, msg: dict) -> None:
        await super()._handle(msg)
        kind = msg.get("t")
        if kind == "hello":
            self.tick_ms = int(msg.get("tick_ms") or self.tick_ms)
        elif kind == "describe":
            for tag in self.table:
                self._note(tag.id)
        elif kind == "update":
            if isinstance(msg.get("tick"), int):
                self.ticks.append((time.perf_counter(), msg["tick"]))
            for tag_id in proto.parse_values(msg):
                self._note(tag_id)
        elif kind == "observe":
            forced, cleared = proto.parse_observe(msg)
            for tag_id in [*forced, *cleared]:
                self._note(tag_id)

    async def write(self, tag_id: str, value) -> None:
        await super().write(tag_id, value)
        self._note(tag_id)

    def open(self) -> None:
        self.opened = time.perf_counter()
        for tag in self.table:
            self._note(tag.id)

    def now(self) -> float:
        if self.ended is not None:
            return self.ended
        return time.perf_counter() - (self.opened or time.perf_counter())

    #: Set when the run is over (or loaded from a file), so every measure
    #: reads the same end however long it takes to compute.
    ended: float | None = None

    def close(self) -> None:
        self.ended = self.now()

    # --- keeping a run, to re-measure it without an engine ---

    def dump(self, path: str) -> None:
        """Write the timeline, so a measure can be re-run on it (`--replay`):
        how a measure is tuned without re-running Jolt, and how a failing run
        is kept for somebody to read."""
        data = {"scene": self.scene, "ended": self.now(), "events": self.events,
                "tags": [[t.id, t.type, t.kind] for t in self.table],
                "series": {tag_id: self.series(tag_id) for tag_id in self._timeline}}
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "Recorder":
        from factoryforge_sidecar.tags import Tag, TagTable   # noqa: PLC0415
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        bus = cls("ws://replay")
        bus.scene = data["scene"]
        bus.table = TagTable([Tag(i, i, ty, kind) for i, ty, kind in data["tags"]])
        bus.opened, bus.ended = 0.0, data["ended"]
        bus.events = [tuple(e) for e in data["events"]]
        bus._timeline = {k: [tuple(p) for p in v] for k, v in data["series"].items()}
        return bus

    def event(self, what: str) -> None:
        self.events.append((round(self.now(), 2), what))

    # --- reading the timeline back ---

    def series(self, tag_id: str) -> list[tuple[float, object]]:
        base = self.opened or 0.0
        return [(at - base, value) for at, value in self._timeline.get(tag_id, [])]

    def value(self, tag_id: str, at: float | None = None):
        """The value `tag_id` had at `at` (seconds since open), or at the end."""
        found = None
        for when, value in self.series(tag_id):
            if at is not None and when > at:
                break
            found = value
        return found

    def num(self, tag_id: str, at: float | None = None) -> float:
        value = self.value(tag_id, at)
        return float(value) if value is not None else 0.0

    def rises(self, tag_id: str, start: float = 0.0, end: float = math.inf) -> list[float]:
        """When `tag_id` went from false to true, inside [start, end]."""
        out, before = [], None
        for when, value in self.series(tag_id):
            if start <= when <= end and value and not before and before is not None:
                out.append(round(when, 2))
            before = value
        return out

    def gain(self, tag_id: str, start: float = 0.0, end: float | None = None) -> float:
        """How much a counter advanced between two moments."""
        return self.num(tag_id, end) - self.num(tag_id, start)

    def extreme(self, tag_id: str, start: float = 0.0, end: float = math.inf,
                pick=max) -> float:
        values = [float(v) for when, v in self.series(tag_id) if start <= when <= end]
        values.append(self.num(tag_id, start))
        return pick(values)

    def seconds_where(self, tag_id: str, test, start: float = 0.0,
                      end: float | None = None) -> float:
        """Seconds of [start, end] during which `test(value)` held."""
        end = self.now() if end is None else end
        total, value, since = 0.0, self.value(tag_id, start), start
        for when, new in self.series(tag_id):
            if when <= start:
                continue
            if when >= end:
                break
            if value is not None and test(value):
                total += when - since
            value, since = new, when
        if value is not None and test(value):
            total += end - since
        return total

    def pace(self) -> float | None:
        """Engine seconds per wall second since open, from the update ticks;
        None when too few updates arrived to say."""
        base = self.opened or 0.0
        seen = [(at, tick) for at, tick in self.ticks if at >= base]
        if len(seen) < 2 or seen[-1][0] - seen[0][0] < 1.0:
            return None
        return ((seen[-1][1] - seen[0][1]) * self.tick_ms / 1000.0
                / (seen[-1][0] - seen[0][0]))


#: How long the examiner holds a momentary button: the engine's own press,
#: `ButtonPanel.DefaultPressHold` (IP-31), which the grader's examiner holds
#: too (IP-34) and `tests/test_grade_templates.py` pins to the C#.
from factoryforge_sidecar.grading.plant import Panel  # noqa: E402
PRESS = Panel.PRESS


class Examiner:
    """The grader's examiner, done over the bus.

    Forcing is the only way a bus client can move an input, and it is what a
    hand on the panel looks like from the controller's side: `ButtonPanel`
    publishes the forced value and, for the pot, turns its pointer to match.
    Every force it makes it also clears, at the end if not before, so an
    attached engine is left as it was found.
    """

    def __init__(self, bus: Recorder) -> None:
        self.bus = bus
        self.held: set[str] = set()

    async def press(self, tag_id: str) -> None:
        self.bus.event(f"press {tag_id}")
        await self.force(tag_id, True, note=False)
        await asyncio.sleep(PRESS)
        await self.release(tag_id, note=False)

    async def force(self, tag_id: str, value, note: bool = True) -> None:
        if note:
            self.bus.event(f"{tag_id} := {value}")
        self.held.add(tag_id)
        await self.bus.force({tag_id: value})

    async def release(self, tag_id: str, note: bool = True) -> None:
        if note:
            self.bus.event(f"release {tag_id}")
        self.held.discard(tag_id)
        await self.bus.force(clear=[tag_id])

    async def run(self, steps: list[tuple[float, Callable]]) -> None:
        """Each step at its time since open, each in a task of its own so a
        press being held does not delay the step after it."""
        started = []
        for at, step in sorted(steps, key=lambda s: s[0]):
            wait = at - self.bus.now()
            if wait > 0:
                await asyncio.sleep(wait)
            started.append(asyncio.create_task(step(self)))
        if started:
            await asyncio.gather(*started)

    async def let_go(self) -> None:
        if self.held:
            await self.bus.force(clear=sorted(self.held))
            self.held.clear()


# --- the examiner's steps ------------------------------------------------

Step = Callable[[Examiner], Awaitable[None]]


def press(button: str, prefix: str = "panel") -> Step:
    async def step(ex: Examiner) -> None:
        await ex.press(f"{prefix}.{button}")
    return step


def strike() -> Step:
    """The mushroom, struck: `panel.estop` is normally closed, true = healthy."""
    async def step(ex: Examiner) -> None:
        await ex.force("panel.estop", False)
    return step


def release_estop() -> Step:
    async def step(ex: Examiner) -> None:
        await ex.release("panel.estop")
    return step


def turn(tag_id: str, value) -> Step:
    """Set an input and leave it set: a pot, or a selector's detent."""
    async def step(ex: Examiner) -> None:
        await ex.force(tag_id, value)
    return step


#: Start at 1.0 s, as eight of the graded exams do, and the grader's
#: `SORT_START_AT`: late enough that the controller has scanned its inputs.
START = [(1.0, press("start"))]


Check = tuple[bool, str]


@dataclass
class Trial:
    """One scene's run on the engine: how long, what the examiner does, and
    what the engine has to report for the scene to have completed."""
    duration: float
    measure: Callable[[Recorder], list[Check]]
    steps: list[tuple[float, Step]] = field(default_factory=lambda: list(START))



# =====================================================================
#  Per scene: what "completes" means on the engine
# =====================================================================
#
# Every measure reads inputs -- what the engine reports -- and never takes
# the controller's word for anything. Where a measure compares the controller
# with the plant, it is to ask whether the plant did what was commanded.
#
# Counters are compared with the sensors upstream of them rather than with a
# fixed number: "every carton the tall beam saw reached the tall lane" is true
# of a working line at any belt speed and false of a mistimed one, where "at
# least five in the chute" is true of both. A carton still between its sensor
# and its counter when the run ends is not a missorted one, so a lane may be
# short by the cartons seen in the last `lag` seconds and by nothing else.

def lane(bus: Recorder, name: str, seen: list[float], counter: str,
         lag: float, end: float | None = None) -> Check:
    """The cartons counted into a lane against the cartons that should have
    gone there, allowing only for those still on their way."""
    end = bus.now() if end is None else end
    counted = int(bus.gain(counter, 0.0, end))
    settled = sum(1 for t in seen if t <= end - lag)
    return (settled <= counted <= len(seen),
            f"{name}: {counted} counted against {len(seen)} that should go there "
            f"({settled} of them more than {lag:g}s before the end)")


def unpaired(every: list[float], tall: list[float]) -> list[float]:
    """The short cartons, from a beam every carton breaks and a later one only
    tall cartons reach: each tall edge claims the latest unclaimed edge of the
    first beam before it, and what nobody claims is short. By order, not by a
    travel time, so a belt stopped between the two beams (an E-stop) does not
    turn a tall carton into a short one. Cartons cannot overtake on a belt,
    and they are further apart than the two beams, so the order is enough."""
    claimed: set[int] = set()
    for h in tall:
        before = [i for i, t in enumerate(every) if t < h and i not in claimed]
        if before:
            claimed.add(before[-1])
    return [t for i, t in enumerate(every) if i not in claimed]


def at_least(value: float, floor: float, what: str) -> Check:
    return value >= floor, f"{what}: {value:g} (at least {floor:g})"


def quiet(bus: Recorder, tags: list[str], start: float, end: float, what: str) -> Check:
    """Nothing moved on the line between two moments: the sensors along it
    never changed. How an E-stop is seen from the plant's side."""
    moved = [(t, tag) for tag in tags for t, _ in bus.series(tag) if start < t < end]
    return (not moved,
            f"{what}: no sensor changed between {start:.1f}s and {end:.1f}s"
            if not moved else
            f"{what}: {tag_list(moved)} changed between {start:.1f}s and {end:.1f}s")


def tag_list(moved) -> str:
    return ", ".join(sorted({f"{tag} at {t:.1f}s" for t, tag in moved})[:4])


def resample(bus: Recorder, tag_id: str, step: float = 0.05) -> list[tuple[float, float]]:
    """A tag as a uniform trace, the way the grader's plant records one every
    tick. The bus publishes a value only when it changes, so a measurement
    that sits still would otherwise count once however long it sat."""
    series = [(t, float(v)) for t, v in bus.series(tag_id)]
    out, i, value = [], 0, float(bus.value(tag_id, 0.0) or 0.0)
    t = 0.0
    while t <= bus.now():
        while i < len(series) and series[i][0] <= t:
            value = series[i][1]
            i += 1
        out.append((round(t, 3), value))
        t += step
    return out


# --- the operator sheet -------------------------------------------------
#
# The two scenes whose grader exams mark the E-stop contract (IP-35) get the
# same sheet here: Start, the mushroom struck mid-run, released, Start alone,
# Reset, then Start. The engine side of it is that the line really stood
# still from the strike until the last Start -- no sensor along it changed --
# and ran again afterwards.

def operator_sheet(reach_at: float, clear=None, wait: float = 4.0) -> list[tuple[float, Step]]:
    """The sheet as one step, because its times run from the strike and the
    strike can wait: `clear(bus)` is the examiner looking for a moment when
    stopping the belt cannot missort a carton -- the grader's sorting exam
    waits for exactly that (`SortingExam._plate_is_clear`), for at most
    `wait` seconds, and then strikes anyway."""
    async def sheet(ex: Examiner) -> None:
        deadline = ex.bus.now() + wait
        while clear is not None and not clear(ex.bus) and ex.bus.now() < deadline:
            await asyncio.sleep(0.05)
        struck = ex.bus.now()
        await strike()(ex)
        for after, step in ((2.0, release_estop()),
                            (3.0, press("start")),      # must not restart: latched
                            (4.5, press("reset")),      # must not restart either
                            (6.0, press("start"))):     # this one must
            await asyncio.sleep(max(struck + after - ex.bus.now(), 0.0))
            await step(ex)
    return [(reach_at, sheet)]


def struck_at(bus: Recorder) -> float | None:
    return next((t for t, what in bus.events if what == "panel.estop := False"), None)


def sheet_checks(bus: Recorder, sensors: list[str]) -> list[Check]:
    # Half a second of grace after the strike: the controller has to see it,
    # and a carton already crossing a beam finishes crossing it -- the belt
    # stops, the carton's edge does not move back out.
    strike_at = struck_at(bus)
    if strike_at is None:
        return [(False, "the examiner never struck the mushroom")]
    restart = strike_at + 6.0
    stood = quiet(bus, sensors, strike_at + 0.5, restart,
                  "the mushroom stopped the line, and only Reset then Start restarted it")
    ran = any(t > restart for tag in sensors for t, _ in bus.series(tag))
    return [stood, (ran, "the line ran again after Reset then Start"
                    if ran else "the line never ran again after Reset then Start")]


# --- sorting by height ----------------------------------------------------

#: The examiner reaches for the mushroom here, as the grader's does
#: (`SORT_STRIKE_FROM`), and strikes once no tall carton is committed to the
#: plate: none has broken the high beam in the time a carton takes from the
#: beam to past the far edge of the pusher's catch.
SORT_STRIKE_FROM = 16.0


def sorting_plate_clear(bus: Recorder) -> bool:
    from factoryforge_sidecar import sorting_scene as line   # noqa: PLC0415
    beam = line.SENSOR_HIGH_POS - line.SENSOR_WINDOW / 2
    committed = (line.PUSHER_POS + line.PUSHER_CATCH - beam) / line.BELT_SPEED
    last = bus.rises("sensor_high.detect", -1.0)
    return not last or bus.now() - last[-1] > committed + 0.2


def measure_sorting_by_height(bus: Recorder) -> list[Check]:
    """Every carton goes to the lane its height says, on real physics.

    `sensor_low` sees every carton and `sensor_high` only the tall ones, so
    the engine itself says which carton was which. `counter.tall` is the
    chute's remover and `counter.short` the end of the belt's. The reference
    fires the pusher on a delay the grader works out from `sorting_scene.py`
    (`_beam_to_pusher_window`); if the engine's belt, beam or pusher is not
    where that model says, tall cartons sail past the plate and this lane
    count comes up short -- which is what a mismatch looks like here.
    """
    tall = bus.rises("sensor_high.detect")
    every = bus.rises("sensor_low.detect")
    short = unpaired(every, tall)
    # From the high beam at 2.0 m, 3.5 m of belt at 0.5 m/s to the far
    # remover; the chute is nearer. Seven seconds covers both with room.
    return [
        (len(tall) >= 6 and len(short) >= 6,
         f"tall={int(bus.gain('counter.tall'))} short={int(bus.gain('counter.short'))}, "
         f"of {len(tall)} tall and {len(short)} short seen"),
        lane(bus, "the chute", tall, "counter.tall", 7.0),
        lane(bus, "the end of the belt", short, "counter.short", 7.0),
        *sheet_checks(bus, ["sensor_low.detect", "sensor_high.detect"]),
    ]


# --- start / stop station -------------------------------------------------

SS_BATCH = 4
SS_BATCH_AT = 27.0


def measure_start_stop_station(bus: Recorder) -> list[Check]:
    """The contract, then a batch the line stops itself at.

    The grader's sheet, as it presses it: a batch too big to reach while the
    mushroom is tried, then Stop, Reset, the pot to four, and Start. On the
    engine, "made exactly four" is four cartons crossing `part_present`
    after that last Start -- the engine's own eye, not the count the
    controller displays -- and then nothing crossing it again, because the
    belt stopped with nobody pressing anything.
    """
    batch = bus.rises("part_present.detect", SS_BATCH_AT)
    last = batch[-1] if batch else None
    stopped = last is not None and bus.now() - last >= 8.0
    return [
        (len(batch) == SS_BATCH,
         f"batch={len(batch)} of {SS_BATCH} crossed the eye after its Start"
         + (f", the last at {last:.1f}s" if last else "")),
        (stopped, "then the line stood still for the rest of the run"
         if stopped else "the line did not stand still after the batch"),
        at_least(len(bus.rises("part_present.detect", 1.0, SS_STRIKE_AT)), 4,
                 "cartons made before the mushroom"),
        at_least(bus.gain("counter.count"), SS_BATCH + 4,
                 "cartons the remover took over the run"),
        *sheet_checks(bus, ["part_present.detect"]),
    ]


SS_STRIKE_AT = 12.0
SS_STEPS = [(0.5, turn("panel.setpoint", 40.0)), (1.0, press("start")),
            *operator_sheet(SS_STRIKE_AT),
            (24.0, press("stop")), (26.0, press("reset")),
            (26.5, turn("panel.setpoint", float(SS_BATCH))),
            (SS_BATCH_AT, press("start"))]


# --- the regulators -------------------------------------------------------
#
# The tank, the oven and the cooling tunnel are marked on their trace, and the
# grader's own statistics are the right ones to apply to it: settled error,
# ripple and overshoot per phase of the pot (`grading/scenes/_regulator.py`),
# with the grader's own limits, on the engine's trace instead of the model's.
# The pot moves as the grader's exam moves it -- one of its seeded values.

def regulator_checks(bus: Recorder, tag_id: str, phases: list[tuple[float, float]],
                     *, settled: float, ripple: float, overshoot: float,
                     moved: float, unit: str) -> list[Check]:
    from factoryforge_sidecar.grading.scenes._regulator import _phase_stats  # noqa: PLC0415
    trace = resample(bus, tag_id)
    values = [v for _, v in trace]
    span = max(values) - min(values) if values else 0.0
    checks: list[Check] = []
    stats = []
    for index, (start, setpoint) in enumerate(phases):
        until = phases[index + 1][0] if index + 1 < len(phases) else bus.now()
        stats.append(_phase_stats(trace, {"setpoint": setpoint, "from": start, "to": until}))
    headline = " | ".join(
        f"{s['setpoint']:g}{unit}: off {s['settled_error']}, ripple {s['ripple']}, "
        f"over {s['overshoot']}" for s in stats)
    checks.append((span >= moved, f"{headline} (travelled {span:.1f}{unit})"))
    for s in stats:
        sp = s["setpoint"]
        if s["settled_error"] is None:
            checks.append((False, f"no trace in the phase at {sp:g}{unit}"))
            continue
        checks.append((s["settled_error"] <= settled,
                       f"at {sp:g}{unit}: settled {s['settled_error']}{unit} off "
                       f"(at most {settled:g})"))
        checks.append((s["ripple"] <= ripple,
                       f"at {sp:g}{unit}: {s['ripple']}{unit} peak to peak (at most {ripple:g})"))
        checks.append((s["overshoot"] <= overshoot,
                       f"at {sp:g}{unit}: {s['overshoot']}{unit} past the setpoint "
                       f"(at most {overshoot:g})"))
    return checks


def pot_phases(phases: list[tuple[float, float]]) -> list[tuple[float, Step]]:
    return [(at, turn("panel.setpoint", value)) for at, value in phases]


TANK_PHASES = [(0.2, 70.0), (30.0, 22.0)]


def measure_tank_level_control(bus: Recorder) -> list[Check]:
    """Hold the level the pot asks for, high and then low: the grader's
    `grade_tank` limits (3 % settled, 3 % ripple, 6 % overshoot, 40 % of
    travel) on `tank.level` as the engine reports it."""
    return regulator_checks(bus, "tank.level", TANK_PHASES, settled=3.0, ripple=3.0,
                            overshoot=6.0, moved=40.0, unit="%")


OVEN_PHASES = [(0.2, 125.0), (30.0, 200.0)]


def measure_heat_treat_station(bus: Recorder) -> list[Check]:
    """Hold the plate at the pot's temperature, twice: `grade_oven`'s limits
    (3 C settled, 5 C ripple, 12 C overshoot, 80 C of travel) on
    `oven.temperature`. A P-only loop parks 9.5 C short of 125 on the
    template's plate, so passing the settled check here is the integral term
    working against the engine's heater, not the model's."""
    return regulator_checks(bus, "oven.temperature", OVEN_PHASES, settled=3.0,
                            ripple=5.0, overshoot=12.0, moved=80.0, unit="C")


CT_PHASES = [(0.2, 160.0), (30.0, 75.0), (55.0, 120.0)]


def measure_cooling_tunnel(bus: Recorder) -> list[Check]:
    """The oven's three numbers per phase, and the split range's own lesson:
    after the recipe drops the product is within 3 C of the new setpoint in
    10 s (`CT_ARRIVE_WITHIN`), which the room alone cannot do -- so the fan
    really did cool it, on the engine's fan and the engine's plate."""
    checks = regulator_checks(bus, "oven.temperature", CT_PHASES, settled=3.0,
                              ripple=5.0, overshoot=12.0, moved=80.0, unit="C")
    drop, low = CT_PHASES[1]
    arrived = next((t for t, v in resample(bus, "oven.temperature")
                    if t >= drop and v <= low + 3.0), None)
    took = None if arrived is None else arrived - drop
    checks.append((took is not None and took <= 10.0,
                   f"after the drop to {low:g} C it was within 3 C in "
                   f"{took:.1f}s (at most 10s)" if took is not None
                   else f"it never came within 3 C of {low:g} C"))
    checks.append(at_least(round(bus.extreme("fan.airflow", drop), 1), 50.0,
                           "peak airflow after the drop, %"))
    return checks


# --- light curtain sorting ------------------------------------------------

LC_POT_THEN_AT = 32.0
#: Above the tallest carton the template's emitter makes (0.30 m), so once the
#: pot is here nothing may be diverted: the line has to follow the knob.
LC_POT_THEN = 0.40


def measure_light_curtain_sorting(bus: Recorder) -> list[Check]:
    """Divert on the measured height against the pot as it was when the
    carton was measured. `height_gauge.height` is the light array's own
    reading, so the lane every carton should take comes from the engine; the
    pot moves above every carton at 32 s, and from then on the diverter must
    let everything through. `tall_count` is the chute's remover."""
    blocked = bus.rises("height_gauge.blocked")
    divert, through = [], []
    for t in blocked:
        # The array reports the highest beam blocked; read it a moment after
        # the edge, once the whole carton is in the curtain.
        height = bus.num("height_gauge.height", t + 0.3)
        (divert if height >= bus.num("panel.setpoint", t) else through).append(t)
    return [
        (len(divert) >= 4 and len(through) >= 4,
         f"diverted={int(bus.gain('tall_count.count'))} "
         f"through={int(bus.gain('short_count.count'))}, of {len(divert)} and "
         f"{len(through)} the curtain and the pot said"),
        lane(bus, "the chute", divert, "tall_count.count", 7.0),
        lane(bus, "the end of the belt", through, "short_count.count", 7.0),
        (any(t > LC_POT_THEN_AT + 1.0 for t in through),
         "cartons ran past after the pot went above them all"),
    ]


# --- roller line with weighing --------------------------------------------

def measure_roller_line_weighing(bus: Recorder) -> list[Check]:
    """Every carton weighed on its own, and every one weighed reaching the
    outfeed. The reference spaces its feed from the infeed's speed and the
    deck's length as the grader's model has them; on an engine where those
    differ, two cartons share the deck and read as one load -- and then the
    outfeed counts more cartons than the scale saw loads."""
    loaded = [t for t, v in threshold_rises(bus, "scale.weight", 20.0)]
    # The verdict, against the engine's own weights: every load whose peak was
    # over the pot is rejected (`panel.red` rises once it leaves the deck) and
    # no other is. The pot drops to 1500 g at 30 s, as the grader's exam
    # drops it, so a tall cardboard carton changes sides.
    wrong = []
    for t in loaded:
        off = next((u for u, v in bus.series("scale.weight") if u > t and float(v) <= 20.0),
                   None)
        if off is None:
            continue
        peak = bus.extreme("scale.weight", t, off)
        over = peak > bus.num("panel.setpoint", off)
        rejected = bool(bus.rises("panel.red", off - 0.1, off + 0.6))
        if over != rejected:
            wrong.append(f"{peak:.0f} g at {off:.1f}s")
    return [
        (len(loaded) >= 8,
         f"weighed={len(loaded)} outfeed={int(bus.gain('outfeed.count'))} "
         f"rejected={len(bus.rises('panel.red'))}"),
        lane(bus, "the outfeed", loaded, "outfeed.count", 5.0),
        (not wrong, "every load over the pot was rejected and no other"
         if not wrong else f"judged wrongly: {wrong[:4]}"),
    ]


def threshold_rises(bus: Recorder, tag_id: str, level: float) -> list[tuple[float, float]]:
    out, above = [], None
    for t, v in bus.series(tag_id):
        now = float(v) > level
        if now and above is False and t >= 0.0:
            out.append((t, float(v)))
        above = now
    return out




# --- pick and place cell --------------------------------------------------

def measure_pick_and_place_cell(bus: Recorder) -> list[Check]:
    """Cartons picked off the station and put down at the outfeed.

    `gantry.holding` is the vacuum's own feedback: a rise is a carton in the
    cup. Every carton let go must be let go over the outfeed -- the rail
    within reach of the place position the grader's model uses
    (`PP_PLACE_AT`) -- and reach the outfeed's remover. A cup that lets go
    anywhere else, or cartons the remover never sees, is the engine's gantry
    and the model's disagreeing about where the outfeed is.
    """
    from factoryforge_sidecar.grading.scenes.pick_and_place_cell import PP_PLACE_AT  # noqa: PLC0415
    held = [t for t, v in bus.series("gantry.holding") if v and t >= 0.0]
    released = [t for t, v in bus.series("gantry.holding") if not v and t > 0.0
                and any(h < t for h in held)]
    wild = [round(bus.num("gantry.position", t), 1) for t in released
            if abs(bus.num("gantry.position", t) - PP_PLACE_AT) > 5.0]
    return [
        (len(released) >= 4,
         f"placed={len(released)} outfeed={int(bus.gain('outfeed.count'))}"),
        (not wild, "every carton was let go over the outfeed"
         if not wild else f"cartons let go at {wild} % of the rail, not {PP_PLACE_AT:.0f}"),
        lane(bus, "the outfeed", released, "outfeed.count", 3.0),
    ]


# --- accumulation buffer --------------------------------------------------

def measure_accumulation_buffer(bus: Recorder) -> list[Check]:
    """A queue held against a running belt, then let out a pot's worth at a
    time.

    The blade is `stop.up` / `stop.down`: the engine's own position switches.
    Each time it drops, the encoder's count while it is down is the window
    the pot asks for, and the cartons the remover takes in the seconds after
    are the ones that got out. Real work is several releases, each letting
    out cartons -- not none (the blade never drops) and not the whole buffer
    (it never comes back up).

    The window is a length of belt, so it is about a pot's worth of cartons
    nose to tail: 120 pulses at 100 a metre is 1.2 m, six 0.2 m cartons. On
    the engine a release lets out six to eight (the queue is compressed
    against the blade, and a carton already on its way through counts too),
    so the band is one to twice that -- what it has to catch is a release of
    nothing, or a blade that never comes back up and lets out everything.
    The last release is left out: the run can end in the middle of it.
    """
    from factoryforge_sidecar.grading.scenes.accumulation_buffer import (  # noqa: PLC0415
        AB_PITCH, AB_PULSES_PER_METRE)
    drops = bus.rises("stop.down")
    counts = []
    for i, t in enumerate(drops):
        until = drops[i + 1] if i + 1 < len(drops) else bus.now()
        counts.append(int(bus.gain("released.count", t, until)))
    window = bus.num("panel.setpoint")
    expect = window / (AB_PITCH * AB_PULSES_PER_METRE)
    judged = counts[:-1] if len(counts) > 1 else counts
    return [
        (len(drops) >= 3,
         f"releases={len(drops)} released={int(bus.gain('released.count'))} "
         f"per release={counts}"),
        (bool(judged) and all(1 <= c <= 2 * expect for c in judged),
         f"each release let out 1..{2 * expect:.0f} cartons for a {window:g}-pulse "
         f"window of about {expect:.0f} (got {judged})"),
        (bus.seconds_where("stop.up", bool, 5.0) >= 0.5 * (bus.now() - 5.0),
         f"the blade was up for {bus.seconds_where('stop.up', bool, 5.0):.0f}s of "
         f"{bus.now() - 5.0:.0f}s: a buffer, not an open belt"),
    ]


# --- guarded cell ---------------------------------------------------------

GC_START_AT = 6.0


def measure_guarded_cell(bus: Recorder) -> list[Check]:
    """Reset closes the relay, Start runs the cell, and cartons are pushed
    across the scanner's field into the transfer chute without it stopping
    the cell.

    All of it is the engine's: `relay.k1`/`k2` are the relay's contacts,
    `starter.aux` the contactor's, `scanner.stop` the scanner's own verdict.
    The reference mutes the scanner on the pot's window and times the push
    from `GC_PUSH_DELAY`, both worked out from the grader's model; a scanner
    field or a cylinder that is not where the model has it stops the cell or
    misses the carton, and the transfer count stays short.
    """
    pushed = bus.rises("push_eye.detect", GC_START_AT)
    stops = [t for t, v in bus.series("scanner.stop") if not v and t > GC_START_AT]
    ran = bus.seconds_where("starter.aux", bool, GC_START_AT)
    return [
        (int(bus.gain("transferred.count")) >= 4,
         f"transferred={int(bus.gain('transferred.count'))} of {len(pushed)} at the "
         f"push eye, line_end={int(bus.gain('line_end.count'))}"),
        (bool(bus.value("relay.k1", GC_START_AT)) and bool(bus.value("relay.k2", GC_START_AT)),
         "Reset closed both relay channels before Start"),
        (ran >= 0.9 * (bus.now() - GC_START_AT - 1.0),
         f"the contactor was in for {ran:.0f}s of the {bus.now() - GC_START_AT:.0f}s "
         f"after Start" + (f"; the scanner stopped the cell at {stops[:3]}" if stops else "")),
        lane(bus, "the transfer chute", pushed, "transferred.count", 5.0),
    ]


# --- batch dosing ---------------------------------------------------------

BD_SECOND_AT = 35.0


def measure_batch_dosing(bus: Recorder) -> list[Check]:
    """Two batches on the pot's number, as the grader's exam runs them:
    Reset and Start, the batch, Stop at 32 s, and Reset and Start again.

    `meter.total` is the flow meter's totaliser and `tank.level` the tank the
    pump feeds, both the engine's. Each batch must leave the totaliser on the
    pot within the grader's 1.5 L and raise the tank by what those litres
    are, and the pump must have stopped by itself before the next Start.
    What the engine cannot do is the grader's re-rated pump -- that stays
    the grader's -- so both batches here run on the same pump.
    """
    from factoryforge_sidecar.grading.scenes.batch_dosing import BD_CAPACITY  # noqa: PLC0415
    pot = bus.num("panel.setpoint")
    checks: list[Check] = []
    heads = []
    for name, start, end in (("first", 1.0, 32.0), ("second", BD_SECOND_AT, bus.now())):
        total = bus.num("meter.total", end)
        rise = bus.num("tank.level", end) - bus.num("tank.level", start)
        flow_end = bus.num("pump.flow", end - 1.0)
        heads.append(f"{name} {total:g} L")
        checks.append((abs(total - pot) <= 1.5,
                       f"{name} batch: the meter read {total:g} L against a {pot:g} L pot "
                       f"(within 1.5)"))
        expect = pot / BD_CAPACITY * 100.0
        checks.append((abs(rise - expect) <= 1.5,
                       f"{name} batch: the tank rose {rise:.1f} % for {expect:.1f} %"))
        checks.append((flow_end < 1.0,
                       f"{name} batch: the pump had stopped by its end ({flow_end:.1f} L/min)"))
    return [(all(ok for ok, _ in checks), ", ".join(heads) + f" on a {pot:g} L pot"), *checks]


BD_STEPS = [(1.0, press("reset")), (1.0, press("start")), (32.0, press("stop")),
            (BD_SECOND_AT, press("reset")), (BD_SECOND_AT, press("start"))]


# --- star-delta start -----------------------------------------------------

SD_STOP_AT = 12.0


def measure_star_delta_start(bus: Recorder) -> list[Check]:
    """A start in star, a changeover at the pot's speed with the star
    contacts open first, and a run in delta -- on the engine's motor.

    Every number here is the starter's feedback: `motor.speed`, the three
    auxiliary contacts, `motor.breaker`. The breaker never trips (star and
    delta never together), delta pulls in only once `staraux` has dropped,
    and the speed at the changeover is the pot's. Stop drops the main.
    """
    pot = bus.num("panel.setpoint")
    delta = bus.rises("motor.deltaaux")
    star_off = [t for t, v in bus.series("motor.staraux") if not v and t > 0.0]
    at = delta[0] if delta else None
    speed = bus.num("motor.speed", at - 0.05) if at is not None else 0.0
    top = bus.extreme("motor.speed", 1.0, SD_STOP_AT)
    breaker_fell = [t for t, v in bus.series("motor.breaker") if not v]
    main_open = [t for t, v in bus.series("motor.mainaux") if not v and t > SD_STOP_AT]
    return [
        (at is not None and not breaker_fell and top >= 95.0,
         f"changeover at {speed:.1f} % (pot {pot:g} %), ran at {top:.1f} %"
         + (f"; breaker tripped at {breaker_fell[0]:.1f}s" if breaker_fell else "")),
        (at is not None and speed >= pot - 3.0,
         f"delta came in at {speed:.1f} % of speed, the pot says {pot:g} %"),
        (at is not None and any(t <= at for t in star_off),
         "the star contacts opened before delta closed"),
        (not breaker_fell, "the breaker never tripped"),
        (bool(main_open) and main_open[0] - SD_STOP_AT <= 0.5,
         "Stop dropped the main contactor" if main_open else "Stop never dropped the main"),
    ]


# --- servo positioning ----------------------------------------------------

SV_POT_AT = 11.0
SV_POT_THEN = 550.0


def measure_servo_positioning(bus: Recorder) -> list[Check]:
    """The carriage shuttles between station A and the pot's station B, and
    follows the pot when it moves (to 550 mm at 11 s, one of the grader's
    values). Arrivals are `axis.position` inside the axis's own window of
    each station -- the engine's encoder -- counted once per visit."""
    from factoryforge_sidecar.grading.scenes.servo_positioning import (  # noqa: PLC0415
        SV_STATION_A, SV_WINDOW)

    def visits(where: float, start: float, end: float) -> int:
        n, inside = 0, False
        for t, v in bus.series("axis.position"):
            now = abs(float(v) - where) <= SV_WINDOW
            if now and not inside and start <= t <= end:
                n += 1
            inside = now
        return n

    first_b = bus.num("panel.setpoint", SV_POT_AT - 0.5)
    a = visits(SV_STATION_A, 0.0, bus.now())
    b1 = visits(first_b, 0.0, SV_POT_AT)
    b2 = visits(SV_POT_THEN, SV_POT_AT, bus.now())
    errors = bus.rises("axis.error", -1.0)
    return [
        (a >= 3 and b1 >= 1 and b2 >= 2,
         f"visits: A={a}, B at {first_b:g} mm={b1}, B at {SV_POT_THEN:g} mm={b2}"),
        (not errors, "the drive never raised an error" if not errors
         else f"the drive raised an error at {errors[:3]}"),
    ]


# --- air receiver ---------------------------------------------------------

AR_POT_AT = 25.0
AR_POT_THEN = 5.0


def measure_air_receiver(bus: Recorder) -> list[Check]:
    """The receiver loaded into the band the pot sets, held there, and moved
    with the pot (6.0 bar, then 5.0 at 25 s, one of the grader's values).

    `receiver.pressure` is the transmitter's raw count, scaled here the way
    the card is: 27648 at the top of the template's range. The band is the
    grader's: pot - 0.5 .. pot, with 0.25 bar of slack either side. It is
    marked once each phase has had time to get there.
    """
    from factoryforge_sidecar.grading.scenes.air_receiver import (  # noqa: PLC0415
        AR_BAND, AR_BAND_SLACK, AR_RANGE_MAX, AR_RANGE_MIN)

    def bar(raw: float) -> float:
        return AR_RANGE_MIN + raw / 27648.0 * (AR_RANGE_MAX - AR_RANGE_MIN)

    trace = [(t, bar(v)) for t, v in resample(bus, "receiver.pressure")]
    checks: list[Check] = []
    marked = []
    for start, end in ((12.0, AR_POT_AT), (AR_POT_AT + 12.0, bus.now())):
        pot = bus.num("panel.setpoint", end - 0.1)
        inside = [p for t, p in trace if start <= t <= end]
        lo, hi = (min(inside), max(inside)) if inside else (0.0, 0.0)
        marked.append(f"{pot:g} bar: {lo:.2f}..{hi:.2f}")
        checks.append((bool(inside) and lo >= pot - AR_BAND - AR_BAND_SLACK
                       and hi <= pot + AR_BAND_SLACK,
                       f"with the pot at {pot:g} bar the receiver held {lo:.2f}..{hi:.2f} "
                       f"bar ({start:g}s to {end:.0f}s)"))
    opened = bool(bus.value("valve.opened", 5.0))
    checks.append((opened, "the isolation valve opened when commanded"))
    return [(all(ok for ok, _ in checks), "; ".join(marked)), *checks]


# --- press station --------------------------------------------------------

PS_OFF_AT = 19.0


def measure_press_station(bus: Recorder) -> list[Check]:
    """AUTO on Start cycles the ram down to bottom dead centre, dwells for the
    pot's time and returns; OFF stops it. `bdc.no` is the limit switch the
    ram's plate trips at the bottom and `ram.retracted` the cylinder's reed,
    both the engine's -- so the ram really travelled the stroke the grader's
    model gives it, and dwelled where the switch says it is.

    The switch closes a little before bottom dead centre and opens a little
    after the ram leaves it, so it reads closed for the pot's dwell plus the
    travel either side: about 0.45 s more on the template's ram. The pot is
    turned to 1.2 s first, one of the grader's own dwells, so the dwell is the
    pot's and not the template's default."""
    pot = bus.num("panel.setpoint")
    bdc = bus.rises("bdc.no", 1.0, PS_OFF_AT)
    dwell = [bus.seconds_where("bdc.no", bool, t, t + pot + 3.0) for t in bdc]
    back = bus.rises("ram.retracted", 1.0, PS_OFF_AT + 4.0)
    later = bus.rises("bdc.no", PS_OFF_AT + 1.5)
    return [
        (len(bdc) >= 3 and len(back) >= len(bdc) - 1,
         f"strokes={len(bdc)} returns={len(back)} dwells={[round(d, 1) for d in dwell]}s "
         f"for a {pot:g}s pot"),
        (bool(dwell) and all(pot - 0.3 <= d <= pot + 1.0 for d in dwell),
         f"each stroke dwelled at bottom for the pot's {pot:g}s (the switch closed "
         f"{pot - 0.3:g}..{pot + 1.0:g}s)"),
        (not later, "OFF stopped the ram" if not later else f"the ram stroked after OFF at {later}"),
    ]


PS_STEPS = [(0.3, turn("panel.setpoint", 1.2)), (0.5, turn("mode.position", 2)),
            (1.0, press("start")), (PS_OFF_AT, turn("mode.position", 1))]


# --- rotary index ---------------------------------------------------------

def measure_rotary_index(bus: Recorder) -> list[Check]:
    """Drop a carton on the deck, turn a quarter, push it off onto the
    outfeed, turn home. `table.atindex`/`athome` are the deck's limit
    switches and `pusher.extended` the cylinder's reed; the rod is only out
    while the deck is at index, and every carton pushed reaches `done`."""
    pushes = bus.rises("pusher.extended")
    bad = [t for t in pushes if not bus.value("table.atindex", t)]
    return [
        (len(pushes) >= 3, f"pushed={len(pushes)} done={int(bus.gain('done.count'))} "
                           f"indexes={len(bus.rises('table.atindex'))}"),
        (not bad, "the rod only came out with the deck at index"
         if not bad else f"the rod came out off index at {bad[:3]}"),
        lane(bus, "the outfeed", pushes, "done.count", 6.0),
    ]


# --- pivot divert ---------------------------------------------------------

def measure_pivot_divert(bus: Recorder) -> list[Check]:
    """Tall cartons into the chute, short ones on to the end, by a blade that
    swings across a running belt. `entry_eye` sees every carton and
    `tall_eye` only the tall ones, so the engine says which is which; the
    counts are the two removers'. The blade's hold is ended by the chute's
    count, so a blade that does not divert on the engine holds forever and
    every carton after it goes into the chute."""
    tall = bus.rises("tall_eye.detect")
    every = bus.rises("entry_eye.detect")
    short = [t for t in every if not any(abs(h - t) <= 0.4 for h in tall)]
    return [
        (len(tall) >= 3 and len(short) >= 3,
         f"chute={int(bus.gain('tall_count.count'))} far={int(bus.gain('short_count.count'))}, "
         f"of {len(tall)} tall and {len(short)} short seen"),
        lane(bus, "the chute", tall, "tall_count.count", 8.0),
        lane(bus, "the far end", short, "short_count.count", 8.0),
    ]


# --- mezzanine lift -------------------------------------------------------

def measure_mezzanine_lift(bus: Recorder) -> list[Check]:
    """Cartons carried up one floor, one per trip, and none spilled.

    A trip is `lift.occupied` rising: the carriage's own sensor says a carton
    is aboard. Each one must be discharged with the carriage at level 1 --
    `out_eye` on the mezzanine sees it only once it is off the carriage, and
    `lift.level`/`lift.atlevel` say where the carriage was when that
    happened -- and reach `done`. `spill` is the remover under the gap
    between floors: a carton run off a carriage that is not there. Its count
    must stay at zero. The reference discharges on `atlevel` at level 1,
    which the grader's model works out from the template's spacing and hoist
    speed; an engine whose carriage stops elsewhere spills, or never
    discharges at all."""
    trips = bus.rises("lift.occupied")
    arrived = bus.rises("out_eye.detect")
    off_level = [t for t in arrived
                 if not (bus.num("lift.level", t) == 1 and bus.value("lift.atlevel", t))]
    spilled = int(bus.gain("spill.count"))
    return [
        (len(arrived) >= 4 and not spilled,
         f"delivered={int(bus.gain('done.count'))} trips={len(trips)} "
         f"up={len(arrived)} spilled={spilled}"),
        (not off_level, "every carton left the carriage at level 1"
         if not off_level else f"cartons left the carriage off level 1 at {off_level[:3]}"),
        (len(arrived) >= len(trips) - 1,
         f"{len(arrived)} of {len(trips)} cartons aboard reached the mezzanine "
         f"(the last may still be on its way)"),
        lane(bus, "done", arrived, "done.count", 6.0),
    ]


TRIALS: dict[str, Trial] = {
    "sorting-by-height": Trial(60.0, measure_sorting_by_height,
                               [*START, *operator_sheet(SORT_STRIKE_FROM, sorting_plate_clear)]),
    "start-stop-station": Trial(60.0, measure_start_stop_station, SS_STEPS),
    "tank-level-control": Trial(65.0, measure_tank_level_control,
                                [*pot_phases(TANK_PHASES), *START]),
    "heat-treat-station": Trial(65.0, measure_heat_treat_station,
                                [*pot_phases(OVEN_PHASES), *START]),
    "cooling-tunnel": Trial(80.0, measure_cooling_tunnel,
                            [*pot_phases(CT_PHASES), *START]),
    "light-curtain-sorting": Trial(65.0, measure_light_curtain_sorting,
                                   [*START, (LC_POT_THEN_AT, turn("panel.setpoint", LC_POT_THEN))]),
    "roller-line-weighing": Trial(70.0, measure_roller_line_weighing,
                                  [*START, (30.0, turn("panel.setpoint", 1500.0))]),
    "pick-and-place-cell": Trial(75.0, measure_pick_and_place_cell),
    "accumulation-buffer": Trial(80.0, measure_accumulation_buffer),
    "guarded-cell": Trial(70.0, measure_guarded_cell,
                          [(2.0, press("reset")), (GC_START_AT, press("start"))]),
    "batch-dosing": Trial(70.0, measure_batch_dosing, BD_STEPS),
    "star-delta-start": Trial(16.0, measure_star_delta_start,
                              [*START, (SD_STOP_AT, press("stop"))]),
    "servo-positioning": Trial(30.0, measure_servo_positioning,
                               [*START, (SV_POT_AT, turn("panel.setpoint", SV_POT_THEN))]),
    "air-receiver": Trial(45.0, measure_air_receiver,
                          [*START, (AR_POT_AT, turn("panel.setpoint", AR_POT_THEN))]),
    "press-station": Trial(24.0, measure_press_station, PS_STEPS),
    "rotary-index": Trial(60.0, measure_rotary_index),
    "pivot-divert": Trial(60.0, measure_pivot_divert),
    "mezzanine-lift": Trial(75.0, measure_mezzanine_lift),
}


# =====================================================================
#  Scenes with no grader reference: a controller of their own
# =====================================================================
#
# Only the palletising cell, which has no graded exam and so no reference to
# borrow. Everything below is its controller and the operator-station helpers
# it is built from -- what this whole file used to be for every scene.

def bit(bus: TagBusClient, tag_id: str) -> bool:
    return bool(bus.read(tag_id)) if bus.table.get(tag_id) is not None else False


def num(bus: TagBusClient, tag_id: str) -> float:
    return float(bus.read(tag_id)) if bus.table.get(tag_id) is not None else 0.0


async def operator_press(bus: TagBusClient, tag_id: str, hold: float = PRESS) -> None:
    """A momentary operator press: force the input high for as long as the
    engine's own panel holds a click (`PRESS`, IP-31), then release it."""
    await bus.force({tag_id: True})
    await asyncio.sleep(hold)
    await bus.force(clear=[tag_id])


# --- the operator station -----------------------------------------------
#
# Four of the five scenes used to ignore the control panel completely. Every
# template placed one, every panel published `start`, `stop`, `reset` and
# `estop`, and exactly one scene read them -- so pressing Start on the roller
# line did nothing, and striking the E-stop on the tank did not stop it
# filling. The buttons were wired to the tag bus and the tag bus was wired to
# nobody (OP-02). One implementation, here, means every scene behaves the same
# way under the operator's hand: a student who learns the E-stop on one line
# finds the same E-stop on the next.


async def write_present(bus: TagBusClient, values: dict) -> None:
    """Write only the tags this scene actually has.

    Scenes differ in what indicators they carry -- the sorting line has a
    single green lamp where the start/stop station has a three-stage tower --
    and a controller that crashes on a missing lamp is a worse controller than
    one that simply does not light it.
    """
    present = {k: v for k, v in values.items() if bus.table.get(k) is not None}
    if present:
        await bus.write_many(present)


async def turn_pot(bus: TagBusClient, value: float, prefix: str = "panel") -> None:
    """Turn the setpoint pot from here.

    Forced rather than written, because the setpoint is an *Input*: the panel
    republishes the knob's own position every tick, so a plain write would be
    overwritten within one scan. A force is how the engine models a hand on
    the knob -- and the engine turns the pointer to match it, so the panel on
    screen never disagrees with the number the controller is using (OP-02).
    """
    await bus.force({f"{prefix}.setpoint": float(value)})


class Station:
    """One control panel, scanned the way a PLC scans it."""

    def __init__(self, bus: TagBusClient, prefix: str = "panel",
                 faults: tuple[str, ...] = ()) -> None:
        self.bus = bus
        self.prefix = prefix
        #: Drive fault contacts this line watches (FI-01). A standing fault
        #: trips the line exactly like the mushroom does -- and, the part
        #: students get wrong, Reset cannot clear a fault that is still there.
        #: A controller that lets you reset a live fault is one that lets you
        #: restart into it. Mirrors OperatorStation in the engine.
        self.faults = faults
        self.running = False
        self.tripped = False
        self.drive_faulted = False
        self._prev = {"start": False, "stop": False, "reset": False}

    @property
    def setpoint(self) -> float:
        """Where the pot is, in the scene's own units. The template owns the
        range, so this is already metres, grams or seconds -- there is no
        percent to rescale here, and therefore no second copy of the range to
        drift out of step with the plate on the panel."""
        return num(self.bus, f"{self.prefix}.setpoint")

    def scan(self) -> dict[str, bool]:
        """One controller scan: read the buttons, resolve the interlocks,
        return the edges in case the caller wants them too."""
        now = {k: bit(self.bus, f"{self.prefix}.{k}") for k in ("start", "stop", "reset")}
        edges = {k: now[k] and not self._prev[k] for k in now}
        self._prev = now

        healthy = bit(self.bus, f"{self.prefix}.estop")
        self.drive_faulted = any(bit(self.bus, tag) for tag in self.faults)

        # Latching, and only Reset clears it. A trip that cleared itself when
        # the mushroom popped back out would restart the line under whoever
        # was still working on it -- the exact thing a latch exists to stop.
        # A live drive fault re-asserts the latch every scan, so Reset while
        # the fault stands achieves nothing, which is the point.
        if not healthy or self.drive_faulted:
            self.tripped = True
        elif edges["reset"]:
            self.tripped = False

        if self.tripped or edges["stop"]:
            self.running = False
        elif edges["start"] and healthy and not self.drive_faulted:
            self.running = True

        edges["healthy"] = healthy
        return edges

    def lamps(self) -> dict:
        """Panel lamps and, where the scene has one, the stack light. Yellow
        is stopped-but-healthy: a tower with nothing lit says "no power", not
        "idle"."""
        return {
            f"{self.prefix}.green": self.running,
            f"{self.prefix}.red": self.tripped,
            "tower.green": self.running,
            "tower.red": self.tripped,
            "tower.yellow": (not self.running) and (not self.tripped),
            "stack_light.green": self.running,
        }


class Checks:
    """Collects assertions so a run reports every problem it found rather than
    dying on the first one -- a driver that stops at the first failure hides
    how much else is broken."""

    def __init__(self, verbose: bool) -> None:
        self.problems: list[str] = []
        self._verbose = verbose

    def __call__(self, ok: bool, what: str) -> bool:
        if not ok:
            self.problems.append(what)
        if self._verbose:
            print(f"  {'ok' if ok else 'FAIL'}  {what}")
        return ok

    def note(self, text: str) -> None:
        if self._verbose:
            print(f"  --    {text}")


def controller(tick, period: float = 0.02):
    """Run `tick` on a fixed scan, in the background, until stopped.

    Every scene now has a controller task rather than a single loop that both
    controls the line and asserts things about it: an interlock check has to
    press a button and watch what the *controller* does about it, which is not
    possible when the checking code is the controller.
    """
    stop = asyncio.Event()

    async def loop() -> None:
        while not stop.is_set():
            await tick(period)
            await asyncio.sleep(period)

    return stop, asyncio.create_task(loop())


#: §4.2's number, applied to every scene rather than only the one that used to
#: check it: strike the mushroom and the line is off within this long.
ESTOP_LIMIT = 0.200

#: Wall-clock the shared interlock sequence needs, on top of a scene's own
#: production window.
INTERLOCK_BUDGET = 7.0


async def exercise_interlocks(bus: TagBusClient, station: Station, check: Checks,
                              is_moving, what_moves: str) -> float:
    """The operator contract every line shares, checked identically on all
    five (OP-02). Returns how long the E-stop took to stop the line.

    `is_moving` is the one thing that differs between scenes: what "the line is
    running" looks like from the bus. Everything else -- momentary buttons, a
    normally-closed E-stop, a latch Start cannot clear, Reset that clears the
    fault but does not restart anything -- is the same contract, and five
    scenes agreeing on it is worth more than five dialects of it.
    """
    await asyncio.sleep(0.3)
    check(not is_moving(), f"initial: {what_moves} is off before anyone presses Start")
    check(bit(bus, "panel.estop"), "initial: the E-stop circuit reads healthy (NC)")

    await operator_press(bus, "panel.start")
    await asyncio.sleep(0.4)
    check(is_moving(), f"after Start: {what_moves} runs")
    check(bit(bus, "panel.green"), "after Start: the panel's green lamp is lit")

    # Timed, not merely observed: "it stopped eventually" is a different claim
    # from the one §4.2 makes.
    struck = time.perf_counter()
    await bus.force({"panel.estop": False})       # the mushroom, struck
    while time.perf_counter() - struck < 1.0:
        if not is_moving():
            break
        await asyncio.sleep(0.005)
    estop_ms = (time.perf_counter() - struck) * 1000.0
    check(not is_moving(), f"after E-stop: {what_moves} is off")
    check(estop_ms <= ESTOP_LIMIT * 1000.0,
          f"E-stop stops the line within {ESTOP_LIMIT * 1000:.0f}ms (took {estop_ms:.0f}ms)")
    await asyncio.sleep(0.2)
    check(bit(bus, "panel.red"), "after E-stop: the panel's red lamp is lit")

    await operator_press(bus, "panel.start")
    await asyncio.sleep(0.3)
    check(not is_moving(), "Start while tripped: does NOT restart the line")

    await bus.force({"panel.estop": True})        # twisted back out
    await asyncio.sleep(0.2)
    check(not is_moving(), "releasing the mushroom alone does not restart the line")

    await operator_press(bus, "panel.reset")
    await asyncio.sleep(0.3)
    check(not bit(bus, "panel.red"), "after Reset: the fault is cleared")
    check(not is_moving(), "after Reset: still stopped until someone presses Start")

    await operator_press(bus, "panel.start")
    await asyncio.sleep(0.4)
    check(is_moving(), "Start after Reset: the line runs again")

    return estop_ms


async def drive_palletising_cell(bus: TagBusClient, duration: float,
                                 verbose: bool) -> tuple[bool, str]:
    """Solve for joint angles, and index through a pattern (HA-04).

    Four things are checked here that no other scene can check.

    **The controller does the kinematics, and the machine agrees.** The arm
    takes three angles, not a position, so `solve` below turns each target into
    a pose -- and then the run compares the tool position the machine *reports*
    against the point that pose was computed for. That single check is worth the
    whole exercise: arithmetic done here, verified over the wire against
    geometry built there, with no shared code between the two.

    **The pattern repeats and the layers interlock.** Six cartons make a layer,
    the seventh starts the next one a layer height higher, and slot 0 of layer 1
    is deliberately not above slot 0 of layer 0. The run reads both and checks
    they differ.

    **A full pallet refuses.** Indexed past its capacity, `pallet.count` stops
    moving and the published position stops with it -- so a program that ignores
    `full` places on the same slot rather than stacking into the air.

    **The moves are staged.** A joint-space move bows a long way off the chord
    between its endpoints, so a carton flown straight from the pick to a slot
    sweeps through the stack. This lifts, swings on the waist alone -- one joint
    moves the tool on an exact horizontal circle and cannot dip -- aligns the
    radius, and only then descends. Mirrors PalletisingCellProfile.
    """
    #: Where the cell's furniture is, in the arm's own frame. Template
    #: knowledge, exactly as PICK_AT/PLACE_AT are for the gantry cell: arm at
    #: (-0.57, -0.85), pick stop at (-0.57, 0), pallet centre at (0.10, -0.85).
    PICK_LOCAL_Z = 0.85
    PALLET_LOCAL_X = 0.67
    #: Tool height for a pick off the belt, and for transit above a full stack.
    PICK_Y, TRANSIT_Y = 0.16, 0.72
    #: Tool above the drop surface so the carton's underside lands level with
    #: it: the arm holds a carton half its height plus 20 mm below the tool.
    CARTON_DROP = 0.13
    #: Degrees of axis error counted as arrived, wider than the machine's own
    #: in-position window so the two never disagree in a way that stalls it.
    ARRIVAL = 1.8
    #: Shortest link `solve` will divide by.
    MIN_LINK = 0.05
    FEED_INTERVAL = 1.6

    check = Checks(verbose)
    state = {
        "step": "measure_stretch",
        "settle": 0.0,
        "feed": 1.0,
        "placed": 0,
        "attempts": 0,
        "empty": 0,
        "unreachable": 0,
        "worst_ik_error": 0.0,
        "layer_seen": 0,
        "layerdone_seen": False,
        "cmd": (0.0, 0.0, 0.0),
        "slot": (0.0, 0.0, 0.0),
        "geom": None,          # (shoulder height, upper arm, forearm), measured
        "slot0": {},           # layer -> (x, z) of its first slot
        "index_hold": 0,
    }

    def solve(target: tuple[float, float, float]):
        """Inverse kinematics for a waist and two links.

        The elbow comes from the law of cosines on the triangle
        shoulder-elbow-tool; the shoulder is the angle up to the target plus the
        angle the upper arm stands off that line. Elbow-up of the two solutions,
        which keeps the forearm out of whatever the arm is reaching over.

        Three guards, in the order they bite, because every one is reachable
        from a scene somebody edited: a zero-length link divides the elbow term
        by zero, a target on the shoulder axis makes `d` zero and divides the
        shoulder term by it, and a target further off than the links reach has
        no solution at all. The clamps on the cosines would turn that last one
        into a silently wrong pose, so the reach is checked *before* them -- and
        it is the case that actually happens.
        """
        x, y, z = target
        waist = math.degrees(math.atan2(-z, x))
        height, upper, fore = state["geom"]
        if upper < MIN_LINK or fore < MIN_LINK:
            return None

        radius = math.hypot(x, z)
        rise = y - height
        distance = math.hypot(radius, rise)
        if distance < MIN_LINK or distance > upper + fore:
            return None

        cos_elbow = (distance ** 2 - upper ** 2 - fore ** 2) / (2.0 * upper * fore)
        cos_offset = (distance ** 2 + upper ** 2 - fore ** 2) / (2.0 * distance * upper)
        elbow = math.degrees(math.acos(max(-1.0, min(1.0, cos_elbow))))
        offset = math.acos(max(-1.0, min(1.0, cos_offset)))
        return waist, math.degrees(math.atan2(rise, radius) + offset), elbow

    def command(writes: dict, pose: tuple[float, float, float]) -> None:
        state["cmd"] = pose
        writes["arm.waist"], writes["arm.shoulder"], writes["arm.elbow"] = pose

    def move_to(writes: dict, target: tuple[float, float, float]) -> bool:
        pose = solve(target)
        if pose is None:
            state["unreachable"] += 1
            return False
        command(writes, pose)
        return True

    def arrived() -> bool:
        """Every axis has reached the pose *this step* commanded.

        Deliberately not `arm.inposition` on its own: that bit compares the axes
        to the pose the machine currently holds, and a pose written this scan
        has not reached it yet -- so on the scan that issues a move it still
        reads "arrived", at the pose we are trying to leave. The gantry cell's
        driver trusted the equivalent bit and released every carton straight
        back onto the pick station.
        """
        want = state["cmd"]
        return (abs(num(bus, "arm.atwaist") - want[0]) <= ARRIVAL
                and abs(num(bus, "arm.atshoulder") - want[1]) <= ARRIVAL
                and abs(num(bus, "arm.atelbow") - want[2]) <= ARRIVAL)

    def slot_target() -> tuple[float, float, float]:
        return (PALLET_LOCAL_X + num(bus, "pallet.nextx"),
                num(bus, "pallet.nexty") + CARTON_DROP,
                num(bus, "pallet.nextz"))

    async def tick(dt: float) -> None:
        station.scan()
        running = station.running

        at_pick = bit(bus, "atpick.detect")
        writes = {
            "stop.raise": running,
            "infeed.run": running,
            "infeed.speed": station.setpoint if running else 0.0,
            # Runs even with a carton indexed: it is what holds the queue
            # against the blade. Stopping it is the accumulation interlock that
            # wedges a carton on the joint between two decks.
            "pickstation.rotate": running,
            # An Int tag, and the bus refuses a float for one -- which is the
            # whole point of the tag model having types at all.
            "onpallet.value": int(num(bus, "pallet.count")),
            "emitter.emit": False,
            **station.lamps(),
        }

        # Hold the index high for a few scans rather than one.
        #
        # The station takes a rising edge, so a longer pulse still indexes
        # exactly once. But a 20 ms controller scan and a 16.7 ms physics tick
        # are close enough that a one-scan pulse sent over the wire is
        # sometimes never sampled high at all -- the first version of this
        # driver placed six cartons while the pallet counted one, and the cell
        # looked perfect from every other angle. The engine-side profile has no
        # such problem because it runs *on* the physics clock; a real PLC does
        # not, which is exactly why this is the wire-level exercise.
        #
        # Only this controller's own pulse is driven here, so a pulse written
        # by the checks below, once the line is stopped, is left alone.
        if state["index_hold"] > 0:
            state["index_hold"] -= 1
            writes["pallet.index"] = state["index_hold"] > 0

        if running and not at_pick:
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["feed"] = FEED_INTERVAL
                writes["emitter.emit"] = True

        if bit(bus, "pallet.layerdone"):
            state["layerdone_seen"] = True
        state["layer_seen"] = max(state["layer_seen"], int(num(bus, "pallet.layer")))
        layer = int(num(bus, "pallet.layer"))
        if int(num(bus, "pallet.slot")) == 0 and layer not in state["slot0"]:
            state["slot0"][layer] = (num(bus, "pallet.nextx"), num(bus, "pallet.nextz"))

        if not running:
            # Stopped means stopped. The jaws stay as they are, so a trip does
            # not drop a carried carton, and no pose is commanded at all -- which
            # is what leaves the arm tags free for the checks below.
            await write_present(bus, writes)
            return

        holding = bit(bus, "arm.holding")
        step = state["step"]

        if step == "measure_stretch":
            # Straight out and level: the tool sits at shoulder height, at a
            # radius of both links end to end. Measured rather than copied from
            # the template, because the link lengths are sliders on the part and
            # a hardcoded copy would place into thin air the day one moved.
            command(writes, (0.0, 0.0, 0.0))
            if arrived():
                state["geom"] = (num(bus, "arm.height"), 0.0, num(bus, "arm.reach"))
                command(writes, (0.0, 0.0, 90.0))
                state["step"] = "measure_fold"
        elif step == "measure_fold":
            if arrived():
                height, _, total = state["geom"]
                upper = num(bus, "arm.reach")
                state["geom"] = (height, upper, total - upper)
                state["step"] = "topick"
        elif step == "topick":
            writes["arm.grip"] = False
            if move_to(writes, (0.0, TRANSIT_Y, PICK_LOCAL_Z)) and arrived() and at_pick:
                state["settle"] = 0.35
                state["step"] = "descend"
        elif step == "descend":
            state["settle"] -= dt
            if state["settle"] <= 0.0 and move_to(writes, (0.0, PICK_Y, PICK_LOCAL_Z)):
                if arrived():
                    state["settle"] = 0.2
                    state["step"] = "grip"
        elif step == "grip":
            writes["arm.grip"] = True
            state["settle"] -= dt
            if state["settle"] <= 0.0:
                state["attempts"] += 1
                if holding:
                    state["slot"] = slot_target()
                    state["step"] = "lift"
                else:
                    # Jaws that closed on nothing go back to waiting rather than
                    # flying an empty cycle and indexing the pattern past a slot
                    # that never got a carton.
                    state["empty"] += 1
                    writes["arm.grip"] = False
                    state["step"] = "topick"
        elif step == "lift":
            writes["arm.grip"] = True
            if move_to(writes, (0.0, TRANSIT_Y, PICK_LOCAL_Z)) and arrived():
                state["step"] = "swing"
        elif step == "swing":
            # Waist only: one joint moves the tool on an exact horizontal
            # circle, so a carried carton cannot dip into the stack on the way
            # across -- which a two-joint move to the same endpoint genuinely
            # can, by about 175 mm on this arm.
            writes["arm.grip"] = True
            target = state["slot"]
            command(writes, (math.degrees(math.atan2(-target[2], target[0])),
                             state["cmd"][1], state["cmd"][2]))
            if arrived():
                state["step"] = "align"
        elif step == "align":
            writes["arm.grip"] = True
            x, _, z = state["slot"]
            if move_to(writes, (x, TRANSIT_Y, z)) and arrived():
                state["step"] = "place"
        elif step == "place":
            writes["arm.grip"] = True
            if move_to(writes, state["slot"]) and arrived():
                # The assertion this scene exists for: the pose was solved here
                # and the tool position is reported by the machine, so the two
                # agreeing means the kinematics are right rather than merely
                # self-consistent.
                x, y, z = state["slot"]
                error = math.hypot(num(bus, "arm.reach") - math.hypot(x, z),
                                   num(bus, "arm.height") - y)
                state["worst_ik_error"] = max(state["worst_ik_error"], error)
                state["step"] = "release"
        elif step == "release":
            writes["arm.grip"] = False
            if not holding:
                writes["pallet.index"] = True
                state["index_hold"] = 4
                state["placed"] += 1
                state["step"] = "retreat"
        elif step == "retreat":
            x, _, z = state["slot"]
            if move_to(writes, (x, TRANSIT_Y, z)) and arrived():
                state["step"] = "change" if bit(bus, "pallet.full") else "topick"
        elif step == "change":
            if not bit(bus, "pallet.change"):
                writes["pallet.change"] = True
            else:
                writes["pallet.change"] = False
                if not bit(bus, "pallet.full"):
                    state["step"] = "topick"

        await write_present(bus, writes)

    station = Station(bus, faults=("arm.fault", "infeed.fault", "stop.fault"))
    stop_event, task = controller(tick)
    estop_ms = -1.0

    try:
        await turn_pot(bus, 70.0)
        await asyncio.sleep(0.2)
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "infeed.run"),
                                             "the infeed drive command")

        await operator_press(bus, "panel.start")
        await asyncio.sleep(1.5)
        check.note(f"production begins: running={station.running} "
                   f"step={state['step']} geom={state['geom']}")

        await asyncio.sleep(max(duration, 55.0))
        # Captured here, not read at the end: the fill-and-refuse checks below
        # drive the pattern to the top of the pallet by hand, and the controller
        # is still watching, so `layer_seen` afterwards is the fill's number and
        # not production's.
        produced_layers = state["layer_seen"] + 1
        check.note(f"production ends: step={state['step']} "
                   f"count={num(bus, 'pallet.count'):.0f} "
                   f"layer={num(bus, 'pallet.layer'):.0f} slot0={state['slot0']}")

        check(state["geom"] is not None and state["geom"][1] > 0.1,
              f"the controller measured the arm before using it "
              f"(shoulder {state['geom'][0]:.3f} m, links {state['geom'][1]:.3f} + "
              f"{state['geom'][2]:.3f} m)" if state["geom"] else
              "the controller measured the arm before using it (it never finished)")
        check(state["placed"] >= 6,
              f"the cell stacked at least a full layer ({state['placed']} placed) -- "
              f"one carton is a cell that happens to work once, not a pattern")
        # Exactly, not "at least": a rising edge sent over the wire can be
        # missed and it can be double-counted, and the two failures look
        # identical from a count that is merely large enough.
        check(num(bus, "pallet.count") == state["placed"],
              f"and the station counted exactly what the sequence placed "
              f"({num(bus, 'pallet.count'):.0f} against {state['placed']})")
        check(state["layer_seen"] >= 1,
              f"the pattern wrapped onto a second layer (reached layer "
              f"{state['layer_seen']})")
        check(state["layerdone_seen"],
              "and said so on pallet.layerdone as it went -- a level, so a slow "
              "scan cannot miss it")
        check(state["unreachable"] == 0,
              f"every pose the sequence asked for was reachable "
              f"({state['unreachable']} refusals)")
        # 50 mm, and the number is not arbitrary: this driver calls an axis
        # arrived within 1.8 degrees, which at a metre of reach is already
        # about 30 mm of arc. Anything tighter asserts how long the axes were
        # given to settle rather than whether the arithmetic was right -- and
        # a genuinely wrong solve misses by tens of centimetres, not by two.
        check(state["worst_ik_error"] < 0.05,
              f"and the tool went where the kinematics said it would -- worst "
              f"disagreement between the solved point and the position the "
              f"machine reported was {state['worst_ik_error'] * 1000:.0f} mm")

        first = state["slot0"].get(0)
        second = state["slot0"].get(1)
        if check(first is not None and second is not None,
                 f"both layers published a first slot ({state['slot0']})"):
            check(abs(first[0] - second[0]) > 0.05 or abs(first[1] - second[1]) > 0.05,
                  f"and slot 0 of layer 1 is not above slot 0 of layer 0 -- the "
                  f"layers interlock ({first} against {second})")

        # Stop the line and take the pattern the rest of the way by hand. Filling
        # a three-layer pallet a carton at a time would be a two-minute run; what
        # is being checked is the station's refusal, not the arm's patience.
        await operator_press(bus, "panel.stop")
        await asyncio.sleep(0.3)
        for _ in range(24):
            await bus.write_many({"pallet.index": True})
            await asyncio.sleep(0.06)
            await bus.write_many({"pallet.index": False})
            await asyncio.sleep(0.04)

        filled = num(bus, "pallet.count")
        held_x, held_y = num(bus, "pallet.nextx"), num(bus, "pallet.nexty")
        check(bit(bus, "pallet.full"),
              f"indexed past its capacity, the pallet reports itself full "
              f"({filled:.0f} cartons)")
        for _ in range(3):
            await bus.write_many({"pallet.index": True})
            await asyncio.sleep(0.06)
            await bus.write_many({"pallet.index": False})
            await asyncio.sleep(0.04)
        check(num(bus, "pallet.count") == filled,
              f"and refuses further index pulses outright "
              f"({num(bus, 'pallet.count'):.0f} against {filled:.0f})")
        check(abs(num(bus, "pallet.nextx") - held_x) < 1e-4
              and abs(num(bus, "pallet.nexty") - held_y) < 1e-4,
              "with the published position held at the last slot -- a full "
              "pallet does not invite a program to stack into the air")

        await bus.write_many({"pallet.change": True})
        await asyncio.sleep(0.15)
        await bus.write_many({"pallet.change": False})
        await asyncio.sleep(0.2)
        check(num(bus, "pallet.count") == 0 and not bit(bus, "pallet.full"),
              f"a pallet change starts an empty one "
              f"({num(bus, 'pallet.count'):.0f} cartons)")

        # A command past a mechanical stop is refused, and said so -- the thing
        # a travel axis can never demonstrate, because every position it can be
        # commanded to is one it can reach.
        await bus.write_many({"arm.shoulder": 200.0})
        await asyncio.sleep(1.8)
        check(bit(bus, "arm.limit"),
              "a command past the shoulder stop raises arm.limit")
        check(abs(num(bus, "arm.atshoulder") - 105.0) < 1.0,
              f"and the axis stops on the stop rather than going where it was "
              f"told ({num(bus, 'arm.atshoulder'):.1f} deg of 105)")
        await bus.write_many({"arm.shoulder": 40.0})
        await asyncio.sleep(0.4)
        check(not bit(bus, "arm.limit"),
              "a command back inside the envelope clears it -- arm.limit "
              "describes this scan, not a latched history")

        # Seize the arm mid-move: every axis stops where it stands.
        await bus.write_many({"arm.shoulder": 90.0, "arm.elbow": 20.0})
        await asyncio.sleep(0.3)
        await bus.force({"arm.fault": True})
        await asyncio.sleep(0.3)
        frozen = (num(bus, "arm.atshoulder"), num(bus, "arm.atelbow"))
        await asyncio.sleep(1.5)
        now = (num(bus, "arm.atshoulder"), num(bus, "arm.atelbow"))
        check(abs(now[0] - frozen[0]) < 0.5 and abs(now[1] - frozen[1]) < 0.5,
              f"a seized arm freezes where it stands (shoulder {frozen[0]:.1f} -> "
              f"{now[0]:.1f}, elbow {frozen[1]:.1f} -> {now[1]:.1f} deg)")
        check(not bit(bus, "arm.inposition"),
              "and does not claim to have arrived, which is the only honest "
              "thing it can say")
        await bus.force(clear=["arm.fault"])
    finally:
        stop_event.set()
        await task

    print(f"RESULT pattern={'PASS' if not check.problems else 'FAIL'} "
          f"placed={state['placed']} layers={produced_layers} "
          f"ik_error={state['worst_ik_error'] * 1000:.0f}mm "
          f"empty_picks={state['empty']}/{state['attempts']} estop={estop_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


SOLVERS = {"palletising-cell": drive_palletising_cell}

#: Its production window: a layer is six cartons and an arm cycle is about
#: seven seconds -- pick, lift, swing, align, place, retreat, all waiting on
#: axis feedback rather than on a clock. Long enough for a layer to complete
#: and the pattern to wrap onto the next one, which is the thing being checked.
DEFAULT_DURATION = {"palletising-cell": 60.0}


# =====================================================================
#  Coverage: every scene in the manifest is one of three things
# =====================================================================
#
# A graded scene has a `TRIALS` entry: the grader's `good` reference on the
# engine, and what "completes" means there. An ungraded scene has a controller
# of its own in `SOLVERS`. A scene that can have neither yet is named here,
# with the reason, and `tools/test_plan.py` section H reports it as SKIP with
# that reason -- visibly, and only for as long as the entry stays. A manifest
# scene in none of the three fails H: a new scene cannot be skipped by being
# forgotten.
#
# Today this is empty. A graded scene belongs in TRIALS, not here; this is for
# a scene whose engine side cannot run headless at all, and the entry should
# say what would have to change for it to leave.
EXEMPT: dict[str, str] = {}

#: The exit code for an EXEMPT scene: neither a pass (0) nor a failure (1).
EXIT_EXEMPT = 3


def trace(bus: Recorder) -> None:
    """Every tag's story in one line each, for `--trace`: how a measure is
    designed, and how a failing one is read."""
    print("  examiner: " + (", ".join(f"{at:.1f}s {what}" for at, what in bus.events) or "nothing"))
    for tag in sorted(bus.table, key=lambda t: (t.kind, t.id)):
        series = bus.series(tag.id)
        if not series:
            continue
        values = [v for _, v in series]
        if tag.type == "bit":
            detail = (f"rises={len(bus.rises(tag.id, -math.inf))} "
                      f"true={bus.seconds_where(tag.id, bool, 0.0):.1f}s")
        else:
            numbers = [float(v) for v in values]
            detail = (f"first={numbers[0]:.4g} last={numbers[-1]:.4g} "
                      f"min={min(numbers):.4g} max={max(numbers):.4g}")
        print(f"  {tag.kind:6s} {tag.id:26s} changes={len(series) - 1:<5d} {detail}")


async def run_reference(bus: Recorder, scene_id: str, kind: str, trial: Trial,
                        duration: float, verbose: bool, show_trace: bool,
                        save: str | None = None) -> tuple[bool, str]:
    """Drive the connected engine with the grader's `kind` reference for
    `duration` seconds, the examiner at the panel, and measure the result.

    The controller is attached the way `grading.core.start_reference` attaches
    one -- the same function, the same client, and the same `controller`
    report saying it has reached its PLC (IP-30) -- except that the bus is this
    file's `Recorder`, because the examiner and the measure need it too, and
    the engine serves one sidecar at a time.
    """
    from factoryforge_sidecar.grading import registry     # noqa: PLC0415 — reads templates

    controller = registry.reference_for(scene_id, kind)
    if controller is None:
        return False, f"{scene_id} has no {kind!r} reference controller"

    # A write computed before the describe hooks finish is dropped by design
    # (HP-33), which is harmless on the wall clock -- the next scan writes it
    # again -- but waiting costs nothing and makes the first scan count.
    await asyncio.wait_for(bus.rebuilt.wait(), timeout=15)

    stop = asyncio.Event()
    task = asyncio.create_task(controller(bus, stop))
    await bus.controller_link(f"reference:{kind}", True,
                              f"built-in {kind!r} reference controller (try_scene.py)")
    bus.open()
    examiner = Examiner(bus)
    exam = asyncio.create_task(examiner.run(trial.steps))
    try:
        # The controller is somebody else's code: if it dies, say so rather
        # than measure a plant nobody was driving.
        deadline = time.perf_counter() + duration
        while time.perf_counter() < deadline:
            if task.done():
                problem = task.exception() if not task.cancelled() else "cancelled"
                return False, f"the {kind!r} reference stopped at {bus.now():.1f}s: {problem!r}"
            if exam.done() and exam.exception() is not None:
                return False, f"the examiner failed at {bus.now():.1f}s: {exam.exception()!r}"
            await asyncio.sleep(0.25)
        bus.close()
    finally:
        exam.cancel()
        stop.set()
        task.cancel()
        for pending in (exam, task):
            try:
                await asyncio.wait_for(pending, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        await examiner.let_go()

    if save:
        bus.dump(save)
    return report(bus, scene_id, kind, trial, verbose, show_trace)


def report(bus: Recorder, scene_id: str, kind: str, trial: Trial,
           verbose: bool, show_trace: bool) -> tuple[bool, str]:
    """Measure a finished run and print its RESULT line."""
    checks = trial.measure(bus)
    if verbose or show_trace:
        for ok, what in checks:
            print(f"  {'ok' if ok else 'FAIL'}  {what}")
    if show_trace:
        trace(bus)
    pace = bus.pace()
    failed = [what for ok, what in checks if not ok]
    # One line, so test_plan's H can quote it: what was measured, not a count
    # of checks. Each measure puts its headline number first.
    print(f"RESULT {'PASS' if not failed else 'FAIL'} {scene_id} reference={kind} "
          f"{checks[0][1] if checks else 'no checks'}"
          + (f" | engine pace {pace:.2f}x" if pace is not None else ""))
    if pace is not None and pace < 0.9:
        print(f"       note: the engine ran at {pace:.2f}x real time on this machine, "
              f"so the controller's clock and the plant's disagreed; a failure "
              f"here may be the machine, not the scene")
    return not failed, "; ".join(failed)


def _quiet_close(loop, context) -> None:
    """A websocket closed at teardown leaves a finished receive task nobody
    awaits, which asyncio reports as a traceback on the way out -- noise on
    top of the RESULT line a reader is looking for. Everything else is
    reported as usual."""
    import websockets     # noqa: PLC0415 — the sidecar already depends on it
    if isinstance(context.get("exception"), websockets.exceptions.ConnectionClosed):
        return
    loop.default_exception_handler(context)


async def run_scene(godot: str, entry: dict, args) -> int:
    asyncio.get_running_loop().set_exception_handler(_quiet_close)
    scene_id = entry["id"]
    trial = TRIALS.get(scene_id)
    solver = SOLVERS.get(scene_id)
    reference = args.reference or "good"
    if scene_id in EXEMPT:
        print(f"RESULT EXEMPT {scene_id}: {EXEMPT[scene_id]}")
        return EXIT_EXEMPT
    if trial is None and solver is None:
        print(f"RESULT no trial for {scene_id!r}: a graded scene needs an entry in "
              f"TRIALS (what 'completes' means on the engine), an ungraded one a "
              f"controller in SOLVERS, and a scene that can have neither yet a "
              f"reason in EXEMPT")
        return 1
    if solver is not None and args.reference:
        print(f"RESULT {scene_id!r} has no grader reference; --reference does not apply")
        return 1
    duration = args.duration if args.duration is not None else (
        trial.duration if trial is not None else DEFAULT_DURATION[scene_id])

    bus_url = f"ws://127.0.0.1:{PORT}/tagbus"
    eng: Engine | None = None

    def fresh_bus() -> TagBusClient:
        return Recorder(bus_url) if trial is not None else TagBusClient(bus_url)

    if port_listening():
        # Something is already on the port -- most likely the windowed engine
        # a "Try this scene" button in the editor itself would be talking to
        # (UX-31). Attach to it rather than refusing outright: a real PLC
        # test tool that insists on starting its own engine could never be
        # pressed from inside the one already open on screen. Every measure
        # below is taken from the moment of attaching, so a scene that has
        # been running for a while is measured on what happens from now.
        try:
            bus, runner = await connect(fresh_bus(), timeout=5)
        except (asyncio.TimeoutError, RuntimeError) as exc:
            print(f"RESULT port {PORT} is already in use, and connecting to it failed too: {exc}")
            return 1

        if bus.scene != scene_id:
            print(f"RESULT an engine is already running scene {bus.scene!r}, not {scene_id!r} -- "
                  f"load \"{entry['title']}\" there first, or close it and this will start its own")
            runner.cancel()
            return 1
        print(f"Attached to the already-running engine (scene {bus.scene!r}, {len(bus.table)} tags)")
    else:
        # Deliberately *not* --deterministic, even for sorting-by-height: this
        # runs the line the way a user opens it, on real physics, which is the
        # whole point of checking the grader's controller against it.
        # tall=5/short=5 stays in tools/drive_engine.py, the tool written for
        # that (OP-03).
        print(f"Starting engine for '{entry['title']}'"
              + (f" from {args.scene_file}" if args.scene_file else "") + "...")
        eng = Engine(godot, entry, args.scene_file)

        why = await wait_for_port(20.0, eng.proc)
        if why is not None:
            print(f"RESULT {why}")
            print(eng.tail())
            eng.stop()
            return 1

        try:
            bus, runner = await connect(fresh_bus())
        except (asyncio.TimeoutError, RuntimeError) as exc:
            print(f"RESULT could not connect: {exc}")
            print(eng.tail())
            eng.stop()
            return 1

        if bus.scene != scene_id:
            print(f"RESULT connected, but scene is {bus.scene!r}, expected {scene_id!r}")
            runner.cancel()
            eng.stop()
            return 1
        print(f"connected to scene {bus.scene!r}, {len(bus.table)} tags")

    try:
        if trial is not None:
            print(f"driving it with the grader's {reference!r} reference for {duration:g}s")
            ok, problem = await run_reference(bus, scene_id, reference, trial, duration,
                                              args.verbose, args.trace, args.save)
        else:
            ok, problem = await solver(bus, duration, args.verbose)
        if ok:
            print(f"PASS — {entry['title']}")
        else:
            print(f"FAIL — {entry['title']}: {problem}")
        return 0 if ok else 1
    finally:
        runner.cancel()
        try:
            await asyncio.wait_for(runner, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        if eng is not None:
            eng.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", help="manifest id, e.g. sorting-by-height (see --list)")
    parser.add_argument("--reference", default=None,
                        help="which of the scene's grader references to drive it with "
                             "(default: good). A wrong one should FAIL here")
    parser.add_argument("--scene-file", default=None,
                        help="open this scene file in the engine instead of the "
                             "scene's template; the reference still reads the template")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to run before measuring (default: the scene's own)")
    parser.add_argument("--verbose", action="store_true", help="print every check")
    parser.add_argument("--trace", action="store_true",
                        help="also print every tag's timeline summary and the examiner's steps")
    parser.add_argument("--save", metavar="FILE", default=None,
                        help="keep the run's timeline in FILE (JSON)")
    parser.add_argument("--replay", metavar="FILE", default=None,
                        help="measure a timeline kept with --save again, with no engine")
    parser.add_argument("--list", action="store_true", help="list scene ids and exit")
    args = parser.parse_args(argv)

    if args.replay:
        bus = Recorder.load(args.replay)
        trial = TRIALS.get(bus.scene)
        if trial is None:
            print(f"RESULT no trial for {bus.scene!r}")
            return 1
        ok, problem = report(bus, bus.scene, args.reference or "(replayed)", trial,
                             args.verbose, args.trace)
        return 0 if ok else 1

    manifest = load_manifest()

    if args.list:
        for entry in manifest:
            how = ("grader reference" if entry["id"] in TRIALS
                   else "own controller" if entry["id"] in SOLVERS
                   else "exempt" if entry["id"] in EXEMPT else "NO TRIAL")
            print(f"{entry['id']:24s} {entry['title']}  [{how}]")
        return 0

    if not args.scene:
        parser.error("--scene is required (or pass --list to see the ids)")

    entry = next((e for e in manifest if e["id"] == args.scene), None)
    if entry is None:
        ids = ", ".join(e["id"] for e in manifest)
        print(f"RESULT unknown scene {args.scene!r} -- known ids: {ids}")
        return 1

    if args.scene_file and not Path(args.scene_file).is_file():
        print(f"RESULT no such scene file: {args.scene_file}")
        return 1

    godot = find_godot()
    if not godot:
        print("RESULT could not find a Godot .NET binary -- set $GODOT or put it on PATH")
        return 1

    return asyncio.run(run_scene(godot, entry, args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
