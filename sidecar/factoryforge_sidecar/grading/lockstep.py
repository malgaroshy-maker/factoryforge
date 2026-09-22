"""A reference controller's scan loop, and the lockstep that can put it on
the plant's clock (IP-06).

`run_scan` is what every reference controller calls; `Lockstep` is what takes
a scan over when the grader runs with `--lockstep`. They live together
because the second only works if the first hands it the body.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from factoryforge_sidecar.tagbus import TagBusClient

if TYPE_CHECKING:                                 # annotations only
    from .core import GradedEngine


#: A reference controller's scan. Comfortably above Windows' 15.6 ms timer
#: floor, where a shorter sleep does not sleep at all (AGENTS.md gotcha 2).
SCAN = 0.05


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

    Real elapsed time fixed the controller's arithmetic and not the race: the
    plant and the controller were still two wall clocks, and on a loaded
    machine both coarsen. So when the bus is a `LockstepClient` this loop does
    not run at all. The body is handed to `Lockstep`, which calls it once per
    scan with `dt` equal to exactly the plant time that passed, and nothing on
    that path reads the wall clock (IP-06).
    """
    lockstep = getattr(bus, "lockstep", None)
    if lockstep is not None:
        lockstep.attach(body, period)
        await stop.wait()
        return

    last = time.perf_counter()
    while not stop.is_set():
        now = time.perf_counter()
        dt, last = now - last, now
        await body(dt)
        await asyncio.sleep(period)


# --- lockstep: a reference controller on the plant's clock ---------------

class LockstepStalled(RuntimeError):
    """One side of a lockstep run stopped answering."""


class Lockstep:
    """A plant and a reference controller on one clock: the plant's.

    A graded run against a real PLC is two machines on two wall clocks -- the
    engine paces itself with a real-time accumulator and the PLC scans when it
    scans -- and that is right, because a real PLC does not wait for anybody.
    A *reference* controller runs in this process, though, so it can be made
    to wait, and a test that marks one has to: on a loaded machine both clocks
    coarsen, each in its own way, and a quantity the rubric measures moves with
    how busy the machine was. The stopwatch reference for batch-dosing passed
    its PR run and failed the same code on master that way (IP-06).

    So each scan is one cycle, in a fixed order:

    1. the controller scans -- reads the inputs it has been sent and writes its
       outputs -- and is told `dt`, which is exactly the plant time since its
       previous scan (0 on the first);
    2. the plant applies those writes;
    3. the plant advances by exactly one scan period, in its own fixed steps;
    4. every update those steps published reaches the controller.

    Nothing in that loop reads the wall clock. Steps 2 and 4 *wait* on real
    I/O -- the controller still talks to the plant over a real websocket
    through the real `TagBusClient`, so it crosses the seam a student's
    sidecar crosses -- but the plant is not moving while they wait, so however
    long they take changes nothing. The wall clock appears only as a liveness
    bound: a side that stops answering ends the run as ERROR rather than
    hanging it.

    It runs faster than real time, by however much the machine allows.
    """

    #: Wall-clock seconds one barrier may wait before the run is declared
    #: broken. Liveness only: it decides whether the run ends, never what the
    #: plant or the controller sees.
    STALL = 20.0
    #: How often a barrier re-checks when nothing has poked it. It only
    #: matters when a flush drops every write it held (HP-33's gate), which
    #: sends nothing and so pokes nothing.
    RECHECK = 0.01

    def __init__(self, engine: GradedEngine, period: float = SCAN) -> None:
        tick = engine.tick_ms / 1000.0
        steps = round(period / tick)
        if steps < 1 or abs(steps * tick - period) > 1e-9:
            raise ValueError(f"a {period:g}s scan is not a whole number of "
                             f"{engine.tick_ms} ms plant steps")
        self.engine = engine
        self.period = period
        self.steps_per_scan = steps
        self.bus: LockstepClient | None = None
        self.scan = None
        self.scans = 0
        self.started = False
        self.error: BaseException | None = None
        #: Frames the plant published, and how many the controller handled.
        self.engine_sent = 0
        self.bus_read = 0
        #: Frames the controller sent, and how many the plant applied.
        self.bus_sent = 0
        self.engine_read = 0
        self._progress = asyncio.Event()

    def poke(self) -> None:
        self._progress.set()

    def attach(self, body, period: float) -> None:
        """Take over a reference controller's scan. Called by `run_scan`."""
        if self.started:
            problem = ("a scan attached after the plant had started moving, so "
                       "lockstep cannot say which plant time it began at")
        elif self.scan is not None:
            problem = "a second scan attached; lockstep steps exactly one"
        elif abs(period - self.period) > 1e-9:
            problem = (f"a {period:g}s scan attached to a {self.period:g}s "
                       f"lockstep")
        else:
            self.scan = body
            return
        # Raised here, inside the controller's task, where nobody is looking --
        # so it is also kept for `run` to raise where somebody is.
        self.error = RuntimeError(problem)
        raise self.error

    def _connected(self) -> bool:
        return self.engine._client is not None and self.bus is not None

    def _writes_applied(self) -> bool:
        if not self._connected():
            return True                       # nobody left to wait for
        return self.bus.flushed and self.engine_read >= self.bus_sent

    def _updates_delivered(self) -> bool:
        if not self._connected():
            return True
        return self.bus_read >= self.engine_sent

    async def _until(self, ready, what: str) -> None:
        deadline = time.perf_counter() + self.STALL
        while not ready():
            if time.perf_counter() > deadline:
                raise LockstepStalled(
                    f"{what} had not arrived {self.STALL:g}s later (wall clock), "
                    f"at plant time {self.engine.tick_count * self.engine.tick_ms / 1000:.2f}s")
            self._progress.clear()
            try:
                await asyncio.wait_for(self._progress.wait(), self.RECHECK)
            except asyncio.TimeoutError:
                pass

    async def run(self, duration: float) -> None:
        """Advance the plant exactly `duration` seconds, a scan at a time."""
        engine = self.engine
        target = round(duration * 1000.0 / engine.tick_ms)
        done = 0
        dt = 0.0
        self.started = True
        while done < target:
            if self.error is not None:
                raise self.error
            if self.scan is not None:
                await self.scan(dt)
                self.scans += 1
            await self._until(self._writes_applied, "the controller's writes")
            steps = min(self.steps_per_scan, target - done)
            for _ in range(steps):
                await engine.step()
            done += steps
            await self._until(self._updates_delivered, "the plant's updates")
            dt = steps * engine.tick_ms / 1000.0


class LockstepClient(TagBusClient):
    """The real bus client, counting frames so `Lockstep` can tell when each
    direction has gone quiet. Every frame still crosses the websocket, and the
    writes still go out through the client's own flush loop -- including its
    HP-33 gate -- so nothing a reference does in lockstep takes a path a
    student's sidecar would not."""

    def __init__(self, url: str, lockstep: Lockstep) -> None:
        super().__init__(url)
        self.lockstep = lockstep

    @property
    def flushed(self) -> bool:
        """Nothing written is still waiting for the flush loop."""
        return not self._pending

    async def _handle(self, msg: dict) -> None:
        try:
            await super()._handle(msg)
        finally:
            self.lockstep.bus_read += 1
            self.lockstep.poke()

    async def _send(self, msg: dict) -> None:
        # Counted before the send and with no await between the flush loop
        # emptying `_pending` and this line, so a barrier can never see the
        # writes gone from the queue and not yet in flight.
        if self._ws is not None:
            self.lockstep.bus_sent += 1
            self.lockstep.poke()
        await super()._send(msg)
