"""The grader itself: the run, the engine it owns, the integrity checks and
the report. Nothing in this file names a scene -- every scene arrives
through `grading.registry` -- and `tests/test_grade.py` fails if one does.

See the package docstring for what grading is and why it works this way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from factoryforge_sidecar.engine_stub import EngineStub
from factoryforge_sidecar.tags import TagTable, TagValue
from factoryforge_sidecar.tagbus import TagBusClient

from . import registry
from .lockstep import Lockstep, LockstepClient, LockstepStalled


#: Ports a graded run may bind when the instructor asks for a fixed one. The
#: default is 0 -- the OS picks, and two runs on one machine cannot collide.
#: Fixed ports were removed project-wide under HP-53 for exactly that reason.
SUGGESTED_PORTS = range(7500, 7511)

#: How long it waits for a controller before giving up with ERROR.
DEFAULT_WAIT = 120.0

#: How long, after a controller is described to, the window waits for its
#: sidecar to say its driver has reached the PLC before opening anyway (IP-30).
#:
#: 30 s covers one failed attempt and a slow success of the slowest driver
#: there is: the OPC UA client gives a connect 10 s (AGENTS.md gotcha 8),
#: waits 5 s (RECONNECT_DELAY) and tries again for up to 10 s more -- 25 s --
#: and snap7 retries on the same 5 s. A driver that needs longer than that is
#: not connecting, and the exam should not wait on it forever.
#:
#: What the wait costs is wall-clock time and nothing else. The plant is
#: frozen until the window opens, so a controller that never says it is ready
#: -- a hand-written client, a sidecar from before IP-30 -- is graded on the
#: same full window, 30 s later, with the report saying why.
DEFAULT_READY_WAIT = 30.0

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
    `sorting_scene.py` needs no changes to be gradable.

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
    message arrive, and `engine_stub.py` is owned by the tag-bus
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
        #: Set the first time a controller has been sent its `describe`: the
        #: end of the handshake, and the moment the graded window opens. See
        #: `send_describe` for why this event and not an earlier or later one.
        self.described = asyncio.Event()
        #: Set the first time a sidecar reports its driver has reached the
        #: controller -- the event the window opens on (IP-30). `ready_at` is
        #: when, in the same wall seconds as the sessions.
        self.ready = asyncio.Event()
        self.ready_at: float | None = None
        #: Set when a reference controller is stepped in lockstep with this
        #: plant rather than racing it on the wall clock. See `Lockstep`.
        self.lockstep: Lockstep | None = None
        self._session: dict | None = None
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
            session = {"connected_at": self.elapsed, "described_at": None,
                       "ready_at": None, "disconnected_at": None,
                       # Every `controller` report, in order: what the
                       # sidecar said about its driver, and when.
                       "controller": []}
            self.sessions.append(session)
            self._session = session
        try:
            await super()._handle(ws)
        finally:
            if session is not None:
                session["disconnected_at"] = self.elapsed
                if self._session is session:
                    self._session = None

    async def send_describe(self) -> None:
        """The last step of the handshake, and the event the window opens on.

        `EngineStub._handle` greets a session with `hello` and then this, and
        the protocol has nothing after it: a sidecar does not acknowledge a
        describe. So "the controller has connected and been described to" is
        the latest moment the engine can know a controller is there without
        waiting on something the controller chooses to do. Waiting for its
        first write instead would hand a program the start of its own exam,
        and never start one for a program that writes nothing -- which is a
        program that has to be graded, as a FAIL. The socket being accepted,
        a line earlier, is before the controller has a tag list to scan with.

        What this cannot see is the sidecar's own driver. `factoryforge_sidecar
        connect` starts its driver after the describe arrives, so a driver
        that takes seconds to reach its PLC would spend them inside the window.
        That is what `controller_reported` is for (IP-30): the window waits
        for it, and falls back to this only when it does not come.
        """
        await super().send_describe()
        session = self._session
        if session is not None and session["described_at"] is None:
            session["described_at"] = self.elapsed
            self.described.set()

    async def controller_reported(self, ready: bool, driver: str, message: str) -> None:
        """The sidecar said whether its driver has reached the controller.

        The first `ready: true` opens the window. Every report is kept, in the
        session that made it: a driver that reaches its PLC late, or loses it
        halfway, is something the report has to be able to say.
        """
        session = self._session
        if session is None:
            return
        session["controller"].append({"at": self.elapsed, "ready": ready,
                                      "driver": driver, "message": message})
        if ready and session["ready_at"] is None:
            session["ready_at"] = self.elapsed
        if ready and not self.ready.is_set():
            self.ready_at = self.elapsed
            self.ready.set()

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
        try:
            await super()._on_message(msg)
        finally:
            # Counted once the message has been *applied*, not when it
            # arrived: lockstep's promise is that a scan's writes are in the
            # plant before the plant moves.
            if self.lockstep is not None:
                self.lockstep.engine_read += 1
                self.lockstep.poke()

    async def _send(self, msg: dict) -> None:
        if self.lockstep is not None and self._client is not None:
            self.lockstep.engine_sent += 1
            self.lockstep.poke()
        await super()._send(msg)

    async def step(self) -> None:
        """One fixed step and its publication, and nothing else.

        What `EngineStub._tick_loop` does for each step it runs, without the
        wall-clock accumulator that decides how many to run. Only `Lockstep`
        calls this; a graded run against a real controller never does, because
        a real PLC does not wait for anybody.
        """
        self.scene.tick(self.tick_ms / 1000.0)
        self.tick_count += 1
        await self._send_observations()
        await self._send_updates()


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


def window_feedback(engine: GradedEngine, window: dict) -> list[str]:
    """What the report has to say about the driver and the window (IP-30).

    Nothing, when the driver was ready before the window opened and stayed
    so. Otherwise the reason the window opened without it, and anything the
    driver said afterwards: a late ready or a lost PLC is exactly the kind of
    thing that makes a correct program fail, and a student reading only the
    verdict would never know.
    """
    opened = window["opened_at"]
    reports = [r for s in engine.sessions for r in s["controller"]]
    lines: list[str] = []
    if window["opened_on"] != "controller ready":
        before = [r for r in reports if r["at"] <= opened]
        if not before:
            lines.append(
                f"The window opened {window['ready_wait']:g}s after your controller "
                f"connected, without its sidecar saying its driver had reached "
                f"the PLC: it never sent a `controller` report, which a "
                f"hand-written client or a sidecar from before IP-30 does not. If "
                f"your driver needed longer than that to connect, your PLC missed "
                f"whatever the examiner did before it did.")
        else:
            last = before[-1]
            lines.append(
                f"The window opened without your driver: after "
                f"{window['ready_wait']:g}s your sidecar was still saying it was not "
                f"ready ({last['driver']}: {last['message']}).")
    was_ready = any(r["ready"] for r in reports if r["at"] <= opened)
    for r in (r for r in reports if r["at"] > opened):
        into = r["at"] - opened
        if r["ready"] and not was_ready:
            lines.append(
                f"Your driver reported ready {into:.1f}s into the window "
                f"({r['driver']}: {r['message']}). Your PLC could not see "
                f"anything the examiner did before then.")
        elif not r["ready"] and was_ready:
            lines.append(
                f"Your driver reported losing its controller {into:.1f}s into the "
                f"window ({r['driver']}: {r['message']}).")
        elif not r["ready"]:
            lines.append(
                f"{into:.1f}s into the window your sidecar said its driver was "
                f"not ready yet ({r['driver']}: {r['message']}).")
        was_ready = r["ready"]
    return lines


async def start_reference(kind: str, url: str, scene: str,
                          lockstep: Lockstep | None = None):
    """Connect a reference controller. Returns an awaitable that stops it.

    With `lockstep`, the controller's scan is handed to it before this
    returns, while the plant is still at t = 0.
    """
    controller = registry.reference_for(scene, kind)
    if controller is None:
        raise RuntimeError(f"{scene} has no {kind!r} reference controller")

    bus = TagBusClient(url) if lockstep is None else LockstepClient(url, lockstep)
    stop = asyncio.Event()
    runner = asyncio.create_task(bus.run(stop))
    await asyncio.wait_for(bus.connected.wait(), timeout=15)
    deadline = time.perf_counter() + 15
    while bus.scene is None or len(bus.table) == 0:
        if time.perf_counter() > deadline:
            raise RuntimeError("reference controller never received a describe")
        await asyncio.sleep(0.05)

    if lockstep is not None:
        # A write computed before the describe hooks finish is dropped by
        # design (HP-33). On the wall clock the next scan writes it again; in
        # lockstep the first scan is at t = 0 and has to count, so wait here,
        # while the plant is not moving and waiting costs nothing.
        await asyncio.wait_for(bus.rebuilt.wait(), timeout=15)
        lockstep.bus = bus

    task = asyncio.create_task(controller(bus, stop))
    if lockstep is not None:
        # Run the controller up to its first suspension, which for one that
        # scans is `run_scan` handing its body over. `Lockstep.attach` refuses
        # a body that turns up after the plant has started, so a controller
        # that awaited something first fails loudly rather than joining late.
        await asyncio.sleep(0)
        if task.done():
            task.result()                     # re-raise if it died starting

    # A reference runs in this process and has no driver and no PLC: it has
    # reached its controller once its scan exists, because it is one. Saying
    # so through the bus, like any sidecar, puts it through the same window
    # a student's goes through (IP-30).
    await bus.controller_link(f"reference:{kind}", True,
                              f"built-in {kind!r} reference controller")

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

async def run_grading(args, on_listening=None) -> Report:
    """Grade one run. `on_listening`, if given, is called with the engine once
    its tag bus is bound, for a caller that connects a controller of its own
    rather than through `--reference` -- which is how the tests connect one
    late."""
    rubric = registry.rubrics()[args.scene]
    seed = args.seed if args.seed is not None else random.randrange(1, 2 ** 31)
    report = Report(scene=args.scene)
    report.evidence["seed"] = seed

    sim = rubric["build"](seed)
    watched = Watched(sim, observe=rubric["observe"])
    engine = GradedEngine(watched, port=args.bus_port)
    # Before the engine starts, so the frames of the very first connection
    # are counted. Only a reference controller can be stepped: `main` refuses
    # --lockstep without --reference, because a real PLC does not wait.
    lockstep = Lockstep(engine) if getattr(args, "lockstep", False) else None
    engine.lockstep = lockstep
    await engine.start()

    _announce(args, engine, rubric, seed)
    if on_listening is not None:
        on_listening(engine)

    ticker = None
    reference = None
    try:
        if args.reference:
            reference = await start_reference(args.reference, engine.url,
                                              args.scene, lockstep)

        # The plant does not move until a controller is there to drive it
        # (IP-25). It used to start with the engine, so the window was
        # measured from when the grader started listening: a controller that
        # connected 8 s into a 20 s batch-dosing window was graded on 12 s,
        # missed the examiner pressing Start at 1 s, and scored 0.0 L on its
        # first batch -- with nothing in the report to say why. `--wait` is
        # still the ceiling on nobody connecting at all.
        try:
            await asyncio.wait_for(engine.described.wait(), timeout=args.wait)
        except asyncio.TimeoutError:
            report.verdict = ERROR
            report.headline = (f"no controller connected within {args.wait:g}s")
            report.evidence["sessions"] = engine.sessions
            return report

        # Then, on the wall clock, for the driver behind it (IP-30). Being
        # described means the sidecar has its tag list; it does not mean the
        # PLC behind it can see anything yet. `connect` starts its driver only
        # after the describe arrives, and an OPC UA or S7 driver can take
        # seconds to reach its PLC -- seconds that used to be the plant's
        # first, which is when eight scenes press Start. So the window waits
        # for the sidecar to say its driver is there, and falls back to the
        # describe after `ready_wait` for one that never says: a hand-written
        # client, or a sidecar from before the message existed.
        #
        # Lockstep does not wait. Its controller is a reference in this
        # process, attached before the plant takes its first step, and the
        # plant does not move until `Lockstep.run` moves it.
        ready_wait = getattr(args, "ready_wait", None)
        if ready_wait is None:
            ready_wait = DEFAULT_READY_WAIT
        if lockstep is None and not engine.ready.is_set():
            try:
                await asyncio.wait_for(engine.ready.wait(), timeout=ready_wait)
            except asyncio.TimeoutError:
                pass

        if lockstep is None:
            ticker = asyncio.create_task(engine._tick_loop())
        # Wall seconds since the grader started listening. The window and the
        # plant open together, so a student's plant time 0 is the moment their
        # driver could see the plant -- and the report says when that was.
        #
        # It opens once. A controller that drops and reconnects finds the
        # plant still running, as a real line would be, and fails
        # `controller.stayed_connected`; pausing the plant for it instead would
        # let a program stop the exam's clock by hanging up.
        first = next(s for s in engine.sessions if s["described_at"] is not None)
        opened_on_ready = lockstep is None and engine.ready.is_set()
        driver_names = [r["driver"] for s in engine.sessions for r in s["controller"]]
        report.evidence["window"] = {
            "opened_on": ("controller described; stepped in lockstep"
                          if lockstep is not None
                          else "controller ready" if opened_on_ready
                          else f"controller described; no ready within {ready_wait:g}s"),
            "controller_connected_at": first["connected_at"],
            "controller_described_at": first["described_at"],
            # When the sidecar said its driver had reached the PLC, if it had
            # by the time the window opened. None on the fallback.
            "controller_ready_at": engine.ready_at,
            "driver": driver_names[-1] if driver_names else None,
            "ready_wait": ready_wait,
            "opened_at": engine.elapsed,
            # Plant seconds that had already passed when the window opened.
            # Zero by construction; recorded so a report can show it rather
            # than ask to be believed.
            "plant_seconds_before": round(watched.sim_time, 3),
        }

        if not args.quiet:
            if opened_on_ready:
                how = (f"controller connected after {first['described_at']:.1f}s "
                       f"and its driver was ready at {engine.ready_at:.1f}s")
            elif lockstep is None:
                how = (f"controller connected after {first['described_at']:.1f}s; "
                       f"its sidecar did not say its driver was ready within "
                       f"{ready_wait:g}s")
            else:
                how = f"controller connected after {first['described_at']:.1f}s"
            print(f"{how}; the plant starts now and is graded for "
                  f"{args.duration:g}s\n", flush=True)

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
        #
        # That fixes the window's length and not what happens inside it: the
        # plant and the controller are still two wall clocks racing, and a
        # stopwatch controller's batch still moved with the load. For a
        # reference controller, `Lockstep` removes the race instead (IP-06).
        if lockstep is not None:
            report.evidence["clock"] = "lockstep"
            try:
                await lockstep.run(args.duration)
            except LockstepStalled as exc:
                report.verdict = ERROR
                report.headline = f"the lockstep run stalled: {exc}"
                report.evidence["sim_seconds"] = round(watched.sim_time, 2)
                return report
        else:
            ceiling = time.perf_counter() + args.duration * 4 + 30
            while watched.sim_time < args.duration:
                if time.perf_counter() > ceiling:
                    report.feedback.append(
                        f"the plant only advanced {watched.sim_time:.1f}s of the "
                        f"{args.duration:g}s asked for before the wall-clock ceiling; "
                        f"the engine was not stepping, so this mark is not trustworthy")
                    break
                await asyncio.sleep(0.05)

        if lockstep is None:
            report.feedback.extend(window_feedback(engine, report.evidence["window"]))

        if check_integrity(watched, engine, report, args.duration):
            rubric["grade"](watched, engine, report, args.duration)
            failed = [c for c in report.checks if not c.ok]
            report.verdict = FAIL if failed else PASS
            report.headline = (
                "every check met" if not failed
                else f"{len(failed)} of {len(report.checks)} checks failed")
    finally:
        if ticker is not None:
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
    print(f"    {sidecar_launcher()} connect --driver <yours> "
          f"--port {engine.actual_port} -o <options>")
    print()
    print(f"  Waiting up to {args.wait:g}s ...", flush=True)


def sidecar_launcher(frozen: bool | None = None, platform: str | None = None) -> str:
    """How to start the sidecar, typed as it must be typed to run.

    The release has no Python: its sidecar is one frozen program, and telling
    a student to type `python -m ...` there hands them a command that cannot
    run (found in IP-08). Nor does a bare `factoryforge-sidecar` run from the
    folder the download extracted to: PowerShell and every Linux shell run a
    program in the current directory only when the path says so (IP-36).
    `.\\` is the Windows form because it is the one that works in both
    PowerShell and cmd.exe; `./` does not work in cmd.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return "python -m factoryforge_sidecar"
    platform = sys.platform if platform is None else platform
    return (".\\factoryforge-sidecar" if platform.startswith("win")
            else "./factoryforge-sidecar")


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

    evidence = report.evidence
    window = evidence.get("window")
    if window:
        connected = (f"The controller connected {window['controller_described_at']:.1f}s "
                     f"after the grader started listening")
        opened_on = window.get("opened_on", "")
        if opened_on == "controller ready":
            line = (f"{connected}, and its driver ({window['driver']}) reported "
                    f"ready at {window['controller_ready_at']:.1f}s; the plant and "
                    f"the {args.duration:g}s window started then.")
        elif opened_on.startswith("controller described; no ready"):
            line = (f"{connected}. Its sidecar did not say its driver was ready "
                    f"within {window['ready_wait']:g}s, so the plant and the "
                    f"{args.duration:g}s window started at "
                    f"{window['opened_at']:.1f}s without it.")
        else:
            line = (f"{connected}; the plant and the {args.duration:g}s window "
                    f"started then.")
        print(textwrap.fill(line, width=76, initial_indent="  ",
                            subsequent_indent="  "))
        print()

    for check in report.checks:
        print(f"  [{'ok' if check.ok else 'XX'}] {check.id:<30} {check.detail}")

    summary = registry.rubrics().get(report.scene, {}).get("summary")
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
    return textwrap.fill(text, width=width, subsequent_indent="    ")


# --- entry point -------------------------------------------------------

def main(argv: list[str] | None = None, prog: str = "grade.py") -> int:
    """The whole command line. Returns the exit code rather than exiting, so
    `tools/grade.py` and any other front end (IP-08's sidecar subcommand) can
    call it and decide what to do with the number. `prog` is only the name
    `--help` prints."""
    rubrics = registry.rubrics()
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Grade a PLC program against a FactoryForge scene, unattended.")
    parser.add_argument("--list", action="store_true", help="scenes this can mark")
    parser.add_argument("--scene", default=registry.default_scene(),
                        help="scene id (see --list)")
    parser.add_argument("--student", default=None, help="name for the report")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to watch once the controller connects "
                             "(default: the scene's own, since an exercise that "
                             "heats a plate needs longer than one that pushes a "
                             "carton)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT,
                        help="seconds to wait for a controller before giving up")
    parser.add_argument("--ready-wait", type=float, default=DEFAULT_READY_WAIT,
                        help="seconds to wait, once a controller is connected, for "
                             "its sidecar to say its driver has reached the PLC "
                             "before starting the plant anyway (default "
                             f"{DEFAULT_READY_WAIT:g}); the report says which it was")
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
    parser.add_argument("--reference", choices=registry.reference_choices(), default=None,
                        help="grade a built-in controller instead of waiting for "
                             "one, to check the grader itself. Which ones a scene "
                             "has is in --list")
    parser.add_argument("--lockstep", action="store_true",
                        help="with --reference: step the plant and the built-in "
                             "controller together on the plant's clock, one scan "
                             "at a time, so the mark cannot depend on how busy "
                             "this machine is. Faster than real time. Not for "
                             "students: a real PLC cannot be made to wait")
    args = parser.parse_args(argv)

    if args.list:
        for scene_id, rubric in sorted(rubrics.items()):
            refs = ", ".join(tuple(rubric["references"]) + registry.SHARED_REFERENCES)
            print(f"{scene_id}\n    {rubric['title']} — {rubric['task']}")
            print(f"    {rubric['duration']:g}s window; references: {refs}")
        return 0

    if args.scene not in rubrics:
        print(f"RESULT grade=ERROR no rubric for {args.scene!r}; "
              f"known: {', '.join(sorted(rubrics))}", file=sys.stderr)
        return EXIT[ERROR]
    if args.duration is None:
        args.duration = rubrics[args.scene]["duration"]
    if args.lockstep and not args.reference:
        print("RESULT grade=ERROR --lockstep needs --reference: only a built-in "
              "controller can be made to wait for the plant, and a real PLC "
              "scans on its own clock", file=sys.stderr)
        return EXIT[ERROR]
    if args.reference and registry.reference_for(args.scene, args.reference) is None:
        print(f"RESULT grade=ERROR {args.scene} has no {args.reference!r} "
              f"reference; it has: "
              f"{', '.join(tuple(rubrics[args.scene]['references']) + registry.SHARED_REFERENCES)}",
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
