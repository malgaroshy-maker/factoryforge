#!/usr/bin/env python3
"""Run a shipped scene headless, drive it the way a PLC would, and check it
actually works -- before you write a real one against it.

    python tools/try_scene.py --list
    python tools/try_scene.py --scene sorting-by-height
    python tools/try_scene.py --scene tank-level-control --duration 20 --verbose

Spawns the engine itself (headless, the scene's own template if it has one)
and tears it down when done -- no separate `godot --headless ...` command to
remember, and no risk of `connect --driver mock` (§2.6), which connects a
client and then drives nothing: the most obvious "let me just try it" command
leaving the scene more dead than doing nothing at all.

One RESULT line, exit 0 on a real pass and 1 otherwise -- the same convention
tools/check_protocol.py and tools/check_force_while_paused.py already set.

Every scene is driven **from its control panel** (OP-02): the run starts
stopped, presses Start, checks that Stop and a normally-closed E-stop really
stop the line, and reads the scene's one setpoint off the panel's pot rather
than a constant in this file. That is not decoration -- it is the only way to
check that pressing a button in the running app does anything, and four of the
five scenes used to ignore their panel completely.

Assertions are band-based rather than exact counts: these run real rigid-body
physics, and no such run can promise a number (§4). What each scene *can*
promise is conservation, timing, and that turning the knob changes what the
line does. The exact tall=5/short=5 regression contract lives in
tools/drive_engine.py, against `--deterministic` -- a count like that needs a
belt that runs for a fixed length of time, which is the one thing an operator
sequence deliberately does not give it.

Each scene's driving logic mirrors its engine-side demo profile under
engine/src/Sim/DemoProfiles/, so a regression in either one fails the same
way, and this doubles as a check that the engine behaves correctly when driven
purely over the wire -- the same seam a real PLC uses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
MANIFEST = ENGINE / "templates" / "manifest.json"
sys.path.insert(0, str(ROOT / "sidecar"))
logging.disable(logging.WARNING)

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
    pipe: an unread PIPE can deadlock the child once its buffer fills, and a
    file gives something to show the user if the connect step times out."""

    def __init__(self, godot: str, entry: dict) -> None:
        args = [godot, "--headless", "--path", str(ENGINE), "--",
                f"--bus-port={PORT}"]
        if entry["path"]:
            args.append(f"--scene={entry['path']}")

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


async def wait_for_port(timeout: float) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if port_listening():
            return True
        await asyncio.sleep(0.1)
    return False


async def connect(timeout: float = 15.0) -> tuple[TagBusClient, asyncio.Task]:
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


def bit(bus: TagBusClient, tag_id: str) -> bool:
    return bool(bus.read(tag_id)) if bus.table.get(tag_id) is not None else False


def num(bus: TagBusClient, tag_id: str) -> float:
    return float(bus.read(tag_id)) if bus.table.get(tag_id) is not None else 0.0


async def press(bus: TagBusClient, tag_id: str, hold: float = 0.15) -> None:
    """A momentary operator press: force the input high just long enough for
    the engine to see a rising edge, then release it. Real panel presses stay
    high for exactly one physics tick (ButtonPanel.Press); this holds it for
    several, which still reads as one edge, since nothing here is watching
    for it to drop and come back."""
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

    await press(bus, "panel.start")
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

    await press(bus, "panel.start")
    await asyncio.sleep(0.3)
    check(not is_moving(), "Start while tripped: does NOT restart the line")

    await bus.force({"panel.estop": True})        # twisted back out
    await asyncio.sleep(0.2)
    check(not is_moving(), "releasing the mushroom alone does not restart the line")

    await press(bus, "panel.reset")
    await asyncio.sleep(0.3)
    check(not bit(bus, "panel.red"), "after Reset: the fault is cleared")
    check(not is_moving(), "after Reset: still stopped until someone presses Start")

    await press(bus, "panel.start")
    await asyncio.sleep(0.4)
    check(is_moving(), "Start after Reset: the line runs again")

    return estop_ms


async def exercise_fault(bus: TagBusClient, station: Station, check: Checks,
                         is_moving, fault_tag: str, what_moves: str) -> float:
    """Fail a drive under a running line, and check the line notices (FI-01).

    Until drives could fail, every actuator in the library did exactly what it
    was told, so a command and reality could never disagree -- and an interlock
    exists precisely because the plant does not always obey. This is the other
    half of the operator contract, and it is checked the same way on every
    scene that has a drive to fail.

    Assumes the line is running on entry, and leaves it running.
    """
    check(not bit(bus, fault_tag), f"before the fault: {fault_tag} is clear")
    check(is_moving(), f"before the fault: {what_moves} is running")

    raised = time.perf_counter()
    await bus.force({fault_tag: True})
    while time.perf_counter() - raised < 1.0:
        if not is_moving():
            break
        await asyncio.sleep(0.005)
    trip_ms = (time.perf_counter() - raised) * 1000.0

    check(not is_moving(), f"a faulted drive stops {what_moves}")
    check(trip_ms <= ESTOP_LIMIT * 1000.0,
          f"the controller trips within {ESTOP_LIMIT * 1000:.0f}ms of the fault "
          f"(took {trip_ms:.0f}ms)")
    await asyncio.sleep(0.2)
    check(bit(bus, "panel.red"), "a faulted drive lights the panel's red lamp")

    # The one students get wrong. Resetting a live fault must do nothing, and
    # Start after that must do nothing either.
    await press(bus, "panel.reset")
    await asyncio.sleep(0.3)
    check(bit(bus, "panel.red"), "Reset while the fault stands does not clear it")
    await press(bus, "panel.start")
    await asyncio.sleep(0.3)
    check(not is_moving(), "and Start while the fault stands does not restart the line")

    await bus.force(clear=[fault_tag])
    await asyncio.sleep(0.3)
    check(not is_moving(),
          "clearing the fault alone does not restart the line -- the trip is still latched")

    await press(bus, "panel.reset")
    await asyncio.sleep(0.3)
    check(not bit(bus, "panel.red"), "Reset after the fault is gone clears it")

    await press(bus, "panel.start")
    await asyncio.sleep(0.4)
    check(is_moving(), f"Start then brings {what_moves} back")

    return trip_ms


async def check_quiet_after_stop(bus: TagBusClient, station: Station, check: Checks,
                                 is_moving, counter_tag: str) -> None:
    """Press Stop and prove the line really stopped -- not just that a lamp
    went out. A counter that keeps advancing after Stop is the failure this
    catches, and it is invisible from the indicators."""
    await press(bus, "panel.stop")
    await asyncio.sleep(0.3)
    check(not is_moving(), "after Stop: the line is off")
    check(not bit(bus, "panel.green"), "after Stop: green is out")

    settled = num(bus, counter_tag)
    await asyncio.sleep(0.8)
    check(num(bus, counter_tag) == settled,
          f"after Stop: {counter_tag} stops advancing (was {settled:.0f}, "
          f"now {num(bus, counter_tag):.0f})")


# --- per-scene drivers --------------------------------------------------
# Each mirrors its engine-side profile under engine/src/Sim/DemoProfiles/.


async def drive_sorting_by_height(bus: TagBusClient, duration: float, verbose: bool) -> tuple[bool, str]:
    """The reference line, now driven from the panel (OP-03).

    Its analog knob is the timing pot every real diverter has: how long after
    the tall beam breaks the pusher fires. Too short and the plate hits the
    carton on the nose; too long and it sails past. The pot is checked the way
    that matters -- by measuring what the controller actually does with it,
    not by reading the tag back -- because a driver that reads the setpoint and
    then ignores it looks identical from the bus.
    """
    EMIT_HALF_PERIOD = 1.5
    PUSH_HOLD = 0.5

    check = Checks(verbose)
    state = {"high_mem": False, "extend_at": None, "retract_at": None,
             "broke_at": None, "delays": [], "emit_flag": False,
             "elapsed": 0.0, "next_toggle": 0.0, "feeding": False}

    async def tick(dt: float) -> None:
        s = state
        station.scan()
        now = time.perf_counter()
        s["elapsed"] += dt

        # A checkweigher can only weigh one carton at a time, and this line has
        # no spacing control of its own -- so the controller provides it, by
        # holding the feed while the scale is loaded. Without this, two cartons
        # occasionally share the deck: they read as one peak, so the run counts
        # fewer cartons than it fed and one metal carton's reject merges into
        # its neighbour's. That is what "2 over limit, 3 metal" meant when this
        # exercise failed under load, and it is a real requirement of real
        # checkweighers rather than a workaround for this one.
        scale_loaded = num(bus, "scale.weight") > 20.0

        if station.running and s["feeding"] and not scale_loaded:
            if s["elapsed"] >= s["next_toggle"]:
                s["emit_flag"] = not s["emit_flag"]
                s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD
        else:
            s["emit_flag"] = False
            s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD

        high = bit(bus, "sensor_high.detect")
        if high and not s["high_mem"] and station.running:
            # The pot, read fresh every scan rather than latched at startup:
            # turning it must change the next carton, not the next run.
            s["broke_at"] = now
            s["extend_at"] = now + station.setpoint
        s["high_mem"] = high

        extend = bit(bus, "pusher.extend")
        if s["extend_at"] is not None and now >= s["extend_at"] and station.running:
            extend = True
            if s["broke_at"] is not None:
                s["delays"].append(now - s["broke_at"])
            s["retract_at"] = now + PUSH_HOLD
            s["extend_at"] = None
        if s["retract_at"] is not None and now >= s["retract_at"]:
            extend = False
            s["retract_at"] = None
        if not station.running:
            extend = False
            s["extend_at"] = None

        await write_present(bus, {
            "conveyor.rotate": station.running,
            "emitter.emit": s["emit_flag"],
            "pusher.extend": extend,
            **station.lamps(),
        })

    station = Station(bus, faults=("conveyor.fault", "pusher.fault"))
    stop_event, task = controller(tick)
    estop_ms = fault_ms = -1.0
    tall = short = 0
    nominal_delays: list[float] = []

    try:
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "conveyor.rotate"), "the belt")
        fault_ms = await exercise_fault(bus, station, check,
                                        lambda: bit(bus, "conveyor.rotate"),
                                        "conveyor.fault", "the belt")

        # --- production, at the pot's own setting --------------------------
        tall_before, short_before = num(bus, "counter.tall"), num(bus, "counter.short")
        nominal = station.setpoint
        check.note(f"diverter pot reads {nominal:.2f}s")
        state["delays"].clear()
        state["feeding"] = True
        await asyncio.sleep(max(duration - 6.0, 4.0))
        state["feeding"] = False
        await asyncio.sleep(6.0)                 # drain: let the lane clear

        tall = int(num(bus, "counter.tall") - tall_before)
        short = int(num(bus, "counter.short") - short_before)
        nominal_delays = list(state["delays"])   # noqa: F841 — reused below

        if nominal_delays:
            mean = sum(nominal_delays) / len(nominal_delays)
            check(abs(mean - nominal) < 0.15,
                  f"the pusher fires {nominal:.2f}s after the beam, as the pot says "
                  f"(measured {mean:.2f}s over {len(nominal_delays)} cartons)")

        # A band, not an exact count: this is real Jolt physics, and no
        # rigid-body run can promise a number the way the deterministic scene
        # can. Conservation is the assertion that actually catches a broken
        # diverter -- every carton the emitter made has to end up in one of
        # the two counters.
        check(tall > 0 and short > 0,
              f"production: both counters advance (got tall={tall} short={short})")

        # --- turn the pot, and prove the line follows it -------------------
        # Timing, not counts: physics variance makes "how many were missed"
        # a poor assertion, while "the pusher fired later" is exactly what
        # turning the knob is supposed to mean and is not noisy at all.
        await turn_pot(bus, 1.80)
        await asyncio.sleep(0.3)
        check(abs(station.setpoint - 1.80) < 0.01,
              f"turning the pot to its stop reads back 1.80s (got {station.setpoint:.2f})")
        state["delays"].clear()
        state["feeding"] = True
        await asyncio.sleep(8.0)
        state["feeding"] = False
        await asyncio.sleep(3.0)

        slow_delays = list(state["delays"])
        if check(bool(slow_delays), "the line kept running after the pot was turned"):
            slow_mean = sum(slow_delays) / len(slow_delays)
            check(abs(slow_mean - 1.80) < 0.15,
                  f"the pusher now fires 1.80s after the beam (measured {slow_mean:.2f}s)")
            if nominal_delays:
                print(f"       pot at {nominal:.2f}s -> pusher fired at "
                      f"{sum(nominal_delays) / len(nominal_delays):.2f}s; "
                      f"pot at 1.80s -> {slow_mean:.2f}s. The knob is the timing, "
                      f"and a diverter mistimed by a second misses the carton.")

        await check_quiet_after_stop(bus, station, check,
                                     lambda: bit(bus, "conveyor.rotate"), "counter.short")
    finally:
        stop_event.set()
        await task
        await bus.force(clear=["panel.setpoint", *station.faults])

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"tall={tall} short={short} estop={estop_ms:.0f}ms fault={fault_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_start_stop_station(bus: TagBusClient, duration: float, verbose: bool) -> tuple[bool, str]:
    """Operator interlocks, then a real batch (§4.2, OP-04).

    The interlock half checks the contract a student gets wrong: momentary
    buttons, an E-stop wired normally closed, and a latch Start cannot clear.
    The batch half is what the pot is for -- it is a *count*, and the line
    stops itself when it has made that many. That is a far better exercise
    than "run until the clock runs out": it has a right answer the run either
    hits exactly or does not.
    """
    EMIT_HALF_PERIOD = 1.5

    check = Checks(verbose)
    state = {"produced": 0, "prev_present": False, "emit_flag": False,
             "feeding": False, "elapsed": 0.0, "next_toggle": EMIT_HALF_PERIOD,
             "batch_done": False}

    async def tick(dt: float) -> None:
        s = state
        target = int(round(station.setpoint))
        was_done = target > 0 and s["produced"] >= target

        edges = station.scan()

        # Start on a finished batch starts the next one, the way a real batch
        # controller works. Without this the panel would have a Start button
        # that does nothing until someone found a way to zero the count, which
        # is not a control system. Mirrors StartStopStationProfile.
        if edges["start"] and was_done:
            s["produced"] = 0

        present = bit(bus, "part_present.detect")
        if present and not s["prev_present"] and station.running:
            s["produced"] += 1
        s["prev_present"] = present

        # The batch interlock: at target, the line stops itself.
        s["batch_done"] = target > 0 and s["produced"] >= target
        if s["batch_done"] and station.running:
            station.running = False

        s["elapsed"] += dt
        if station.running and s["feeding"]:
            if s["elapsed"] >= s["next_toggle"]:
                s["emit_flag"] = not s["emit_flag"]
                s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD
        else:
            s["emit_flag"] = False
            s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD

        await write_present(bus, {
            "belt.rotate": station.running,
            "emitter.emit": s["emit_flag"],
            "produced.value": s["produced"],
            **station.lamps(),
        })

    station = Station(bus, faults=("belt.fault",))
    stop_event, task = controller(tick)
    estop_ms = fault_ms = -1.0
    batch = 4

    try:
        # A batch big enough that the interlock sequence cannot accidentally
        # complete it before the production phase starts.
        await turn_pot(bus, 40)
        await asyncio.sleep(0.2)

        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "belt.rotate"), "the belt")
        check(bit(bus, "tower.green") and not bit(bus, "tower.yellow"),
              "running: the tower shows green only")
        fault_ms = await exercise_fault(bus, station, check,
                                        lambda: bit(bus, "belt.rotate"), "belt.fault", "the belt")

        # --- the batch ------------------------------------------------------
        await press(bus, "panel.stop")
        await asyncio.sleep(0.3)
        state["produced"] = 0
        state["batch_done"] = False
        await turn_pot(bus, batch)
        await asyncio.sleep(0.3)
        check(abs(station.setpoint - batch) < 0.01,
              f"the batch pot reads {batch} pcs (got {station.setpoint:.0f})")

        before = num(bus, "counter.count")
        await press(bus, "panel.start")
        state["feeding"] = True
        check.note(f"running a batch of {batch}")

        deadline = time.perf_counter() + max(duration, 20.0)
        while time.perf_counter() < deadline and not state["batch_done"]:
            await asyncio.sleep(0.05)
        finished_in = max(duration, 20.0) - (deadline - time.perf_counter())
        state["feeding"] = False

        check(state["batch_done"],
              f"the batch completed within its budget (made {state['produced']} of {batch})")
        check(state["produced"] == batch,
              f"the line made exactly the batch it was set to (want {batch}, "
              f"got {state['produced']})")
        await asyncio.sleep(0.4)
        check(not bit(bus, "belt.rotate"),
              "at target the line stops itself -- nobody had to press Stop")
        check(bit(bus, "tower.yellow") and not bit(bus, "tower.green"),
              "batch complete: the tower goes yellow, not green")

        # It really stopped: nothing else comes through after the target.
        settled = state["produced"]
        await asyncio.sleep(1.5)
        check(state["produced"] == settled,
              f"nothing is made past the target (still {state['produced']})")

        counted = int(num(bus, "counter.count") - before)
        check(counted > 0, f"production: the remover counted cartons too (got {counted})")
        check(int(num(bus, "produced.value")) == state["produced"],
              "the display mirrors the sensor edges the controller counted")

        print(f"       batch of {batch} finished in {finished_in:.1f}s; the pot is a "
              f"count, so the line has a target it either hits exactly or does not.")

        await check_quiet_after_stop(bus, station, check,
                                     lambda: bit(bus, "belt.rotate"), "counter.count")
    finally:
        stop_event.set()
        await task
        await bus.force(clear=["panel.setpoint", *station.faults])

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"batch={batch} produced={state['produced']} "
          f"counted={int(num(bus, 'counter.count'))} estop={estop_ms:.0f}ms "
          f"fault={fault_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_tank_level_control(bus: TagBusClient, duration: float, verbose: bool) -> tuple[bool, str]:
    """Hold whatever the pot says, at two very different levels (§4.3, OP-05).

    The two setpoints used to be constants in this file. Now they are one
    knob, turned mid-run, which is the same experiment with the interesting
    part put where a student can reach it: outflow follows Torricelli, so the
    drain valve's authority grows with the square root of level, and a
    controller tuned near the top of the tank behaves differently near the
    bottom. Printing both settling times side by side is the point of the
    exercise.

    A proportional controller with a modest gain, not a saturating one: a gain
    that pins the valve at 100% until the setpoint arrives is bang-bang
    control, and bang-bang hides exactly the nonlinearity this scene exists to
    show.
    """
    GAIN = 1.6                # %valve per % of error -- modulates, not saturates
    BAND_PERCENT = 5.0
    HOLD = 6.0                # stay inside the band this long to count as settled
    HIGH, LOW = 70.0, 20.0
    START_LEVEL = 8.0         # where the setpoint experiment starts from

    check = Checks(verbose)
    state = {"fill": 0.0, "drain": 0.0}

    async def tick(dt: float) -> None:
        station.scan()
        level = num(bus, "tank.level")

        if station.running:
            error = station.setpoint - level
            fill = min(max(error * GAIN, 0.0), 100.0)
            drain = min(max(-error * GAIN, 0.0), 100.0)
        else:
            # Stopped means stopped: both valves shut, so a tripped tank holds
            # its level instead of quietly carrying on filling. This is the
            # whole reason the scene needed a panel -- an E-stop that leaves
            # the fill valve open is not an E-stop.
            fill = drain = 0.0

        state["fill"], state["drain"] = fill, drain
        await write_present(bus, {"tank.fill": fill, "tank.drain": drain,
                                  "level_readout.value": round(level),
                                  **station.lamps()})

    async def settle(setpoint: float, budget: float) -> tuple[float, float, float]:
        """Turn the pot to `setpoint` and watch. Returns (seconds to reach the
        band, seconds held continuously inside it at the end, final level)."""
        await turn_pot(bus, setpoint)
        await asyncio.sleep(0.2)
        band = setpoint * BAND_PERCENT / 100.0
        start = time.perf_counter()
        reached = -1.0
        entered: float | None = None
        level = num(bus, "tank.level")

        while time.perf_counter() - start < budget:
            await asyncio.sleep(0.05)
            level = num(bus, "tank.level")
            inside = abs(setpoint - level) <= band
            if inside:
                if entered is None:
                    entered = time.perf_counter()
                    if reached < 0:
                        reached = entered - start
            else:
                entered = None            # left the band; the hold clock restarts

            if verbose and int((time.perf_counter() - start) * 2) % 8 == 0:
                print(f"  sp={setpoint:4.0f} t={time.perf_counter() - start:5.1f}s "
                      f"level={level:5.1f} fill={state['fill']:5.1f} drain={state['drain']:5.1f}")

        held = time.perf_counter() - entered if entered is not None else 0.0
        return reached, held, level

    station = Station(bus, faults=("tank.fault",))
    stop_event, task = controller(tick)
    estop_ms = fault_ms = -1.0
    high_reach = low_reach = -1.0
    high_held = low_held = high_level = low_level = 0.0

    try:
        # The tank's "is it running" is the fill valve: an E-stop that stops
        # the *controller* while leaving a valve cracked open has not stopped
        # anything, and only watching the valve itself catches that.
        await turn_pot(bus, 100.0)     # so the controller wants fill, not drain
        await asyncio.sleep(0.2)
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: num(bus, "tank.fill") > 0.5,
                                             "the fill valve")

        fault_ms = await exercise_valve_fault(bus, check)

        # Both the interlock and the fault legs leave the tank part full, and
        # "how long to reach 70%" means nothing measured from an unknown
        # starting level -- the run that prompted this reported `reached=0.0s`
        # because the tank was already there. Drain to a known low mark first,
        # so the two settling times below are a fair comparison of the same
        # controller against the same process at two ends of its range, and so
        # they mean the same thing from one run to the next.
        await settle(START_LEVEL, 25.0)
        check(num(bus, "tank.level") <= START_LEVEL + 5.0,
              f"drained to a known starting level before the experiment "
              f"(at {num(bus, 'tank.level'):.1f}%, wanted {START_LEVEL:.0f}%)")

        budget = max(duration, 40.0)
        high_reach, high_held, high_level = await settle(HIGH, budget * 0.4)
        low_reach, low_held, low_level = await settle(LOW, budget * 0.6)

        # The high run gets the strict test -- reach the band and hold it --
        # because that is the tuning point. The low run is only required to
        # *reach* it, and the reason is the lesson itself: the same controller
        # that settles at 70% in ten seconds is still creeping toward 20%
        # twenty seconds later. Demanding an identical hold at both ends would
        # be demanding the nonlinearity not exist.
        check(high_reach >= 0, f"reached {HIGH:.0f}% (got to {high_level:.1f})")
        if high_reach >= 0:
            check(high_held >= HOLD,
                  f"held {HIGH:.0f}% for {HOLD:.0f}s (held {high_held:.1f}s, "
                  f"level {high_level:.1f})")
        check(low_reach >= 0,
              f"reached {LOW:.0f}% within {budget * 0.6:.0f}s (got to {low_level:.1f})")

        await press(bus, "panel.stop")
        await asyncio.sleep(0.4)
        check(num(bus, "tank.fill") < 0.5 and num(bus, "tank.drain") < 0.5,
              "after Stop: both valves are shut, not left where the controller had them")
        held_level = num(bus, "tank.level")
        await asyncio.sleep(1.5)
        check(abs(num(bus, "tank.level") - held_level) < 1.0,
              f"after Stop: the level holds (was {held_level:.1f}, "
              f"now {num(bus, 'tank.level'):.1f})")
    finally:
        stop_event.set()
        await task
        await bus.write_many({"tank.fill": 0.0, "tank.drain": 0.0})
        await bus.force(clear=["panel.setpoint", *station.faults])

    print(f"RESULT high sp={HIGH:.0f} reached={high_reach:.1f}s held={high_held:.1f}s "
          f"level={high_level:.1f} | low sp={LOW:.0f} reached={low_reach:.1f}s "
          f"held={low_held:.1f}s level={low_level:.1f} | estop={estop_ms:.0f}ms "
          f"fault={fault_ms:.0f}ms")
    if high_reach >= 0 and low_reach >= 0:
        print(f"       same controller, same gain, one knob: {high_reach:.1f}s to reach "
              f"{HIGH:.0f}% but {low_reach:.1f}s to reach {LOW:.0f}% -- outflow follows "
              f"Torricelli, so process gain falls with level. One PID tuning is not enough.")
    return not check.problems, "; ".join(check.problems)


async def exercise_valve_fault(bus: TagBusClient, check: Checks) -> float:
    """Seize the fill valve under a running controller (FI-01).

    The tank does not get the shared `exercise_fault`, and the reason is the
    whole lesson. A stopped drive is obviously stopped. A modulating valve
    stuck open keeps the process moving while the controller's own output
    reads zero -- so the command and the plant disagree *and the command looks
    fine*. Asserting "the commanded tag went to 0" would pass here while the
    tank overflowed, which is exactly the mistake this scene should teach a
    student not to make.

    So the observable is the level, not the valve command. Returns how long the
    controller took to trip.
    """
    check(num(bus, "tank.fill") > 5.0,
          f"before the fault: the fill valve is open (at {num(bus, 'tank.fill'):.0f}%)")

    before = num(bus, "tank.level")
    raised = time.perf_counter()
    await bus.force({"tank.fault": True})
    while time.perf_counter() - raised < 1.0:
        if bit(bus, "panel.red"):
            break
        await asyncio.sleep(0.005)
    trip_ms = (time.perf_counter() - raised) * 1000.0

    check(bit(bus, "panel.red"), "a seized valve trips the controller")
    check(trip_ms <= ESTOP_LIMIT * 1000.0,
          f"the controller trips within {ESTOP_LIMIT * 1000:.0f}ms of the fault "
          f"(took {trip_ms:.0f}ms)")

    await asyncio.sleep(0.3)
    check(num(bus, "tank.fill") < 0.5,
          f"the controller commands the valve shut (writing {num(bus, 'tank.fill'):.0f}%)")

    # The point. The command says shut and the tank keeps filling anyway.
    await asyncio.sleep(2.0)
    climbed = num(bus, "tank.level") - before
    check(climbed > 1.0,
          f"and the tank keeps filling regardless -- level rose {climbed:.1f}% while "
          f"the fill command read zero. The valve is not obeying, and the "
          f"controller's own output cannot tell you that")

    await press(bus, "panel.reset")
    await asyncio.sleep(0.3)
    check(bit(bus, "panel.red"), "Reset while the valve is still seized does not clear it")

    await bus.force(clear=["tank.fault"])
    await asyncio.sleep(0.4)
    held = num(bus, "tank.level")
    await asyncio.sleep(1.5)
    check(abs(num(bus, "tank.level") - held) < 0.5,
          f"freeing the valve lets the standing shut command take effect "
          f"(level {held:.1f} -> {num(bus, 'tank.level'):.1f})")

    await press(bus, "panel.reset")
    await press(bus, "panel.start")
    await asyncio.sleep(0.4)
    check(not bit(bus, "panel.red"), "Reset then clears the fault")

    return trip_ms


async def drive_light_curtain_sorting(bus: TagBusClient, duration: float, verbose: bool) -> tuple[bool, str]:
    """Sort on the measurement, with the threshold on the panel (§4.4, OP-06).

    The threshold used to be a constant in this file, which made the scene's
    whole point -- that you sort on a *number*, not on two bits -- something
    you could only exercise by editing Python. It is the pot now, so the line
    can be re-sorted mid-shift, and the run proves it by turning the knob past
    every carton and watching the diverter go quiet.

    The assertion that matters is still conservation: tall + short must equal
    what was emitted. "Both counters advanced" passes while the diverter drops
    cartons on the floor or double-counts them.
    """
    EMIT_HALF_PERIOD = 1.5
    PUSH_DELAY = 2.0
    CLEAR_DWELL = 0.4
    DRAIN = 6.0

    check = Checks(verbose)
    state = {"emitted": 0, "emit_flag": False, "elapsed": 0.0, "next_toggle": 0.0,
             "prev_blocked": False, "extend_at": None, "extending": False,
             "retract_at": None, "measured": [], "diverted": [], "feeding": False}

    async def tick(dt: float) -> None:
        s = state
        station.scan()
        now = time.perf_counter()
        s["elapsed"] += dt

        if station.running and s["feeding"]:
            if s["elapsed"] >= s["next_toggle"]:
                s["emit_flag"] = not s["emit_flag"]
                s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD
                if s["emit_flag"]:
                    s["emitted"] += 1          # one carton per rising edge
        else:
            s["emit_flag"] = False
            s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD

        blocked = bit(bus, "height_gauge.blocked")
        if blocked and not s["prev_blocked"] and station.running:
            height = num(bus, "height_gauge.height")
            s["measured"].append(height)
            # The pot, read at the moment of the measurement -- turning it
            # re-sorts the next carton, not the next run.
            if height >= station.setpoint:
                s["extend_at"] = now + PUSH_DELAY
                s["diverted"].append((height, station.setpoint))
        s["prev_blocked"] = blocked

        extend = bit(bus, "diverter.extend")
        if not s["extending"] and s["extend_at"] is not None and now >= s["extend_at"]:
            extend = True
            s["extending"] = True
            s["extend_at"] = None
        if s["extending"] and s["retract_at"] is None and bit(bus, "diverter.extended"):
            s["retract_at"] = now + CLEAR_DWELL
        if s["retract_at"] is not None and now >= s["retract_at"]:
            extend = False
            s["extending"] = False
            s["retract_at"] = None
        if not station.running:
            extend = False
            s["extending"] = False
            s["extend_at"] = s["retract_at"] = None

        await write_present(bus, {"belt.rotate": station.running,
                                  "emitter.emit": s["emit_flag"],
                                  "diverter.extend": extend,
                                  **station.lamps()})

    station = Station(bus, faults=("belt.fault", "diverter.fault"))
    stop_event, task = controller(tick)
    estop_ms = fault_ms = -1.0
    tall = short = 0

    try:
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "belt.rotate"), "the belt")
        fault_ms = await exercise_fault(bus, station, check,
                                        lambda: bit(bus, "belt.rotate"), "belt.fault", "the belt")

        threshold = station.setpoint
        check.note(f"height threshold pot reads {threshold:.2f}m")
        state["emitted"] = 0
        state["measured"].clear()
        state["diverted"].clear()
        state["feeding"] = True
        await asyncio.sleep(max(duration - DRAIN, 4.0))
        state["feeding"] = False
        await asyncio.sleep(DRAIN)

        tall, short = int(num(bus, "tall_count.count")), int(num(bus, "short_count.count"))
        counted = tall + short
        emitted = state["emitted"]

        check(tall > 0 and short > 0,
              f"both counters advance at {threshold:.2f}m (got tall={tall} short={short})")
        check(counted == emitted,
              f"{emitted} cartons emitted and {counted} counted -- "
              f"{'lost' if counted < emitted else 'double-counted'} {abs(emitted - counted)}")
        below = [h for h, t in state["diverted"] if h < t]
        check(not below,
              f"nothing below the threshold was diverted (diverted {len(below)} that were)")

        # --- turn the pot past every carton --------------------------------
        # A threshold above the tallest carton must divert nothing. This is
        # the assertion that separates "reads the setpoint" from "uses it":
        # a driver that latched the pot at startup passes everything above and
        # fails here.
        await turn_pot(bus, 0.50)
        await asyncio.sleep(0.3)
        check(abs(station.setpoint - 0.50) < 0.01,
              f"the pot reads 0.50m at its stop (got {station.setpoint:.2f})")
        diverted_before = len(state["diverted"])
        seen_before = len(state["measured"])
        state["feeding"] = True
        await asyncio.sleep(9.0)
        state["feeding"] = False
        await asyncio.sleep(DRAIN)

        seen = len(state["measured"]) - seen_before
        newly_diverted = len(state["diverted"]) - diverted_before
        check(seen > 0, f"the curtain kept measuring after the pot was turned (saw {seen})")
        check(newly_diverted == 0,
              f"with the threshold above every carton, nothing is diverted "
              f"(diverted {newly_diverted} of {seen})")
        if seen:
            print(f"       threshold {threshold:.2f}m -> tall={tall} short={short}; "
                  f"threshold 0.50m -> {seen} measured, none diverted. The knob is "
                  f"the sorting rule, and no code changed between the two.")

        await check_quiet_after_stop(bus, station, check,
                                     lambda: bit(bus, "belt.rotate"), "short_count.count")
    finally:
        stop_event.set()
        await task
        await bus.force(clear=["panel.setpoint", *station.faults])

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"tall={tall} short={short} measured={len(state['measured'])} "
          f"estop={estop_ms:.0f}ms fault={fault_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_roller_line_weighing(bus: TagBusClient, duration: float, verbose: bool) -> tuple[bool, str]:
    """Checkweigh against the pot, and cross-check it (§4.5, OP-07).

    The pot is the reject limit in grams. That turns the scale from a readout
    into a decision, and gives the scene a second, independent opinion about
    which cartons are steel: the inductive sensor sees metal, the checkweigher
    sees mass, and on this line those are the same cartons. Two instruments
    agreeing is a far stronger check than either one being non-zero -- and
    "metal was seen at least once" passes for a sensor wired to fire on
    everything, which is the exact confusion this scene exists to clear up.
    """
    EMIT_HALF_PERIOD = 1.5
    ZERO = 0.5                # below this the scale reads empty
    DRAIN = 5.0

    check = Checks(verbose)
    state = {"emit_flag": False, "elapsed": 0.0, "next_toggle": 0.0, "feeding": False,
             "on_scale": False, "peak": 0.0, "peaks": [], "returned_to_zero": 0,
             "rejects": 0, "metal_hits": 0, "prev_metal": False, "metal_peaks": []}

    async def tick(dt: float) -> None:
        s = state
        station.scan()
        s["elapsed"] += dt

        if station.running and s["feeding"]:
            if s["elapsed"] >= s["next_toggle"]:
                s["emit_flag"] = not s["emit_flag"]
                s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD
        else:
            s["emit_flag"] = False
            s["next_toggle"] = s["elapsed"] + EMIT_HALF_PERIOD

        metal = bit(bus, "metal_check.detect")
        if metal and not s["prev_metal"] and station.running:
            s["metal_hits"] += 1
        s["prev_metal"] = metal

        weight = num(bus, "scale.weight")
        if weight > ZERO:
            if not s["on_scale"]:
                s["peak"] = 0.0
            s["on_scale"] = True
            s["peak"] = max(s["peak"], weight)
        elif s["on_scale"]:
            # The carton has left: judge it on its peak, not on whatever the
            # cell happened to read as it rolled off.
            s["on_scale"] = False
            s["returned_to_zero"] += 1
            s["peaks"].append(s["peak"])
            over = s["peak"] > station.setpoint
            if over:
                s["rejects"] += 1
                s["metal_peaks"].append(s["peak"])
            if verbose:
                print(f"  carton: peak {s['peak']:.0f}g "
                      f"{'REJECT' if over else 'pass'} (limit {station.setpoint:.0f}g)")

        await write_present(bus, {
            "infeed.rotate": station.running,
            "scale.rotate": station.running,
            "emitter.emit": s["emit_flag"],
            "weight_readout.value": round(weight),
            # Red on the panel now means "the last carton was over limit" as
            # well as "tripped": a reject the operator cannot see is a reject
            # nobody acts on.
            "panel.red": station.tripped or bool(s["rejects"]),
            **{k: v for k, v in station.lamps().items() if k != "panel.red"},
        })

    station = Station(bus, faults=("infeed.fault", "scale.fault"))
    stop_event, task = controller(tick)
    estop_ms = fault_ms = -1.0

    try:
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "scale.rotate"), "the rollers")
        fault_ms = await exercise_fault(bus, station, check,
                                        lambda: bit(bus, "scale.rotate"), "scale.fault",
                                        "the rollers")

        limit = station.setpoint
        check.note(f"reject limit pot reads {limit:.0f}g")
        for key in ("peaks", "metal_peaks"):
            state[key].clear()
        state["rejects"] = state["metal_hits"] = state["returned_to_zero"] = 0
        state["feeding"] = True
        await asyncio.sleep(max(duration - DRAIN, 6.0))
        state["feeding"] = False
        await asyncio.sleep(DRAIN)

        weighed = len(state["peaks"])
        rejects = state["rejects"]
        metal_hits = state["metal_hits"]

        check(weighed > 0, "the scale weighed something -- scale.weight never left zero")
        check(state["returned_to_zero"] >= weighed,
              f"the scale returns to zero between cartons "
              f"({state['returned_to_zero']} clears for {weighed} cartons)")
        check(int(num(bus, "outfeed.count")) > 0,
              f"outfeed.count advanced (got {int(num(bus, 'outfeed.count'))})")
        check(metal_hits > 0, "metal_check.detect fired for the steel cartons")
        check(not (weighed > 0 and metal_hits >= weighed),
              f"metal_check.detect is selective, not a presence sensor "
              f"({metal_hits} hits for {weighed} cartons)")
        check(0 < rejects < weighed,
              f"the checkweigher rejected some cartons and passed others "
              f"({rejects} of {weighed} over {limit:.0f}g)")

        # Two instruments, one answer. This is the assertion the scene was
        # missing: mass and material are independent measurements of the same
        # cartons, so a wiring or threshold mistake in either one shows up as
        # them disagreeing.
        check(rejects == metal_hits,
              f"the checkweigher and the inductive sensor flag the same cartons "
              f"({rejects} over limit, {metal_hits} metal)")

        # --- turn the pot below every carton -------------------------------
        await turn_pot(bus, 100)
        await asyncio.sleep(0.3)
        check(abs(station.setpoint - 100) < 1.0,
              f"the pot reads 100g (got {station.setpoint:.0f})")
        weighed_before, rejects_before = len(state["peaks"]), state["rejects"]
        state["feeding"] = True
        await asyncio.sleep(8.0)
        state["feeding"] = False
        await asyncio.sleep(DRAIN)

        now_weighed = len(state["peaks"]) - weighed_before
        now_rejects = state["rejects"] - rejects_before
        check(now_weighed > 0, f"the scale kept weighing after the pot moved ({now_weighed})")
        check(now_rejects == now_weighed,
              f"below every carton, the limit rejects all of them "
              f"({now_rejects} of {now_weighed})")
        if now_weighed:
            print(f"       limit {limit:.0f}g -> {rejects} of {weighed} rejected; "
                  f"limit 100g -> {now_rejects} of {now_weighed}. Peaks seen: "
                  f"{sorted({round(p / 100) * 100 for p in state['peaks']})}g.")

        await check_quiet_after_stop(bus, station, check,
                                     lambda: bit(bus, "scale.rotate"), "outfeed.count")
    finally:
        stop_event.set()
        await task
        await bus.force(clear=["panel.setpoint", *station.faults])

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"weighed={len(state['peaks'])} rejects={state['rejects']} "
          f"metal={state['metal_hits']} outfeed={int(num(bus, 'outfeed.count'))} "
          f"estop={estop_ms:.0f}ms fault={fault_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_pick_and_place_cell(bus: TagBusClient, duration: float,
                                    verbose: bool) -> tuple[bool, str]:
    """Sequence a gantry: index, lower, grip, lift, traverse, release (CP-30).

    Three things are checked here that no other scene can check.

    **The drive really is analog.** The infeed is behind a VFD, so commanding a
    speed and reading it back must not agree instantly. The run watches the gap
    between `infeed.speed` and `infeed.actual` while the ramp is moving; a drive
    that reported its own reference would pass every other assertion in this
    file and still be a bit output wearing a float's clothes.

    **The grip reports the truth.** After the vacuum closes, `gantry.holding`
    is true only when a carton was really there. The controller below checks it
    and goes back to waiting if the cup caught nothing, which is the interlock
    the scene exists to teach.

    **The sequence is written on feedback, not on timers.** Every transition
    waits on `inposition`, `lowered`, `raised` or `holding`. A timer-based
    version passes at one travel speed and fails at another, and the travel
    speed is a slider in the property panel.
    """
    PICK_AT, PLACE_AT = 0.0, 96.0
    #: Short, because the *queue* bounds the feed here, not the clock: the
    #: infeed holds while a carton is indexed, so cartons accumulate and the
    #: next is already waiting when the station clears. A six-second cadence
    #: added its own dead time on top of the travel and the cycle, and the cell
    #: managed one carton in thirty seconds.
    FEED_INTERVAL = 2.0
    #: Percent of the rail counted as arrived. Wider than the machine's own
    #: in-position window so the two never disagree in a way that stalls it.
    ARRIVAL_WINDOW = 2.5

    check = Checks(verbose)
    state = {
        "step": "topick",
        "settle": 0.0,
        "feed": 1.0,
        "placed": 0,
        "attempts": 0,
        "empty": 0,
        "max_ramp_gap": 0.0,
        "codes": set(),
    }

    async def tick(dt: float) -> None:
        station.scan()
        running = station.running

        reference = station.setpoint if running else 0.0
        actual = num(bus, "infeed.actual")
        if running and abs(reference - actual) > state["max_ramp_gap"]:
            state["max_ramp_gap"] = abs(reference - actual)

        at_station = bit(bus, "atstation.detect")
        writes = {
            # The infeed runs whenever the line does, and deliberately does
            # *not* hold while a carton is indexed. Holding it looks like the
            # obvious accumulation interlock and it jams this line solid: a
            # carton stopped exactly on the joint between two conveyors rests
            # against the downstream deck's leading face, and contact slop
            # means it cannot climb back onto it when the belt restarts.
            # Carried across at speed it never touches that face.
            "infeed.run": running,
            "infeed.speed": reference,
            # Index the carton to the stop rather than coasting it onto a dead
            # plate: a repeatable pick needs the carton put under the cup on
            # purpose, not left wherever friction happened to stop it.
            "pickstation.rotate": running and not at_station,
            "scanner.enable": True,
            "rate.value": actual,
            "alarm.beacon": station.tripped or station.drive_faulted,
            "alarm.horn": station.drive_faulted,
            "emitter.emit": False,
            **station.lamps(),
        }

        # Feed only into space: not while a carton is indexed at the station,
        # and not while one is still under the scanner head. Together those
        # bound the queue to what the infeed can hold without needing a count.
        if running and not at_station and not bit(bus, "scanner.present"):
            state["feed"] -= dt
            if state["feed"] <= 0.0:
                state["feed"] = FEED_INTERVAL
                writes["emitter.emit"] = True

        if bit(bus, "scanner.read"):
            state["codes"].add(int(num(bus, "scanner.code")))

        if not running:
            writes["gantry.lower"] = False
            await write_present(bus, writes)
            return

        in_position = bit(bus, "gantry.inposition")
        lowered = bit(bus, "gantry.lowered")
        raised = bit(bus, "gantry.raised")
        holding = bit(bus, "gantry.holding")
        step = state["step"]

        def arrived(destination: float) -> bool:
            """Has the axis reached `destination`?

            Deliberately not `gantry.inposition` on its own. That bit compares
            the axis to the target the *machine* currently holds, and the
            target this controller just wrote has not reached the machine yet
            -- so on the scan that issues a move, `inposition` still reports
            "arrived", at the place we are trying to leave. The first version
            of this driver trusted it and released every carton straight back
            onto the pick station, having never travelled at all. Checking the
            position feedback against the destination this step wants has no
            such window.
            """
            return abs(num(bus, "gantry.position") - destination) <= ARRIVAL_WINDOW

        if step == "topick":
            writes["gantry.target"] = PICK_AT
            writes["gantry.lower"] = False
            if in_position and arrived(PICK_AT) and raised and at_station:
                state["settle"] = 0.4
                state["step"] = "lower"
        elif step == "lower":
            state["settle"] -= dt
            if state["settle"] <= 0.0:
                writes["gantry.lower"] = True
                if lowered:
                    state["settle"] = 0.25
                    state["step"] = "grip"
        elif step == "grip":
            writes["gantry.lower"] = True
            writes["gantry.grip"] = True
            state["settle"] -= dt
            if state["settle"] <= 0.0:
                state["attempts"] += 1
                if holding:
                    state["step"] = "raise"
                else:
                    state["empty"] += 1
                    writes["gantry.grip"] = False
                    state["step"] = "topick"
        elif step == "raise":
            writes["gantry.grip"] = True
            writes["gantry.lower"] = False
            if raised:
                state["step"] = "toplace"
        elif step == "toplace":
            writes["gantry.grip"] = True
            writes["gantry.target"] = PLACE_AT
            if in_position and arrived(PLACE_AT):
                state["step"] = "release"
        elif step == "release":
            writes["gantry.grip"] = False
            if not holding:
                state["placed"] += 1
                state["step"] = "home"
        elif step == "home":
            writes["gantry.target"] = PICK_AT
            if in_position and arrived(PICK_AT):
                state["step"] = "topick"

        await write_present(bus, writes)

    station = Station(bus, faults=("gantry.fault", "infeed.fault"))
    stop_event, task = controller(tick)
    estop_ms = -1.0

    try:
        await turn_pot(bus, 70.0)
        await asyncio.sleep(0.2)
        # What must be off within 200 ms is the drive *command*, not the
        # belt's last revolution. This is the one scene where those differ:
        # the infeed is behind a VFD, and a VFD asked to stop ramps down over
        # a couple of seconds. That is not a bug to hide -- it is why a real
        # E-stop circuit removes power or uses safe torque off rather than
        # politely asking the drive to decelerate. The interlock is measured
        # against the command; the coast-down is asserted separately below.
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "infeed.run"),
                                             "the infeed drive command")

        # ...and the drive really does wind down afterwards, rather than the
        # command being dropped while the belt keeps running forever. Measured
        # on its own Stop press: exercise_interlocks deliberately leaves the
        # line running, so timing a coast straight after it would be timing a
        # drive that is already ramping back up.
        await press(bus, "panel.stop")
        coast_start = time.perf_counter()
        while num(bus, "infeed.actual") > 0.5 and time.perf_counter() - coast_start < 10.0:
            await asyncio.sleep(0.05)
        coast = time.perf_counter() - coast_start
        check(num(bus, "infeed.actual") <= 0.5,
              f"and the drive itself coasts to a stop, in its own time "
              f"({coast:.1f}s after the command dropped)")
        check.note(f"ramp-down took {coast:.1f}s -- an E-stop that only asked the "
                   f"drive to decelerate would leave the belt moving that long")

        await press(bus, "panel.start")
        await asyncio.sleep(1.5)   # let the ramp come back up before timing production
        check.note(f"production begins: running={station.running} tripped={station.tripped} "
                   f"healthy={bit(bus, 'panel.estop')} actual={num(bus, 'infeed.actual'):.1f} "
                   f"at_station={bit(bus, 'atstation.detect')} "
                   f"present={bit(bus, 'scanner.present')}")

        before = num(bus, "outfeed.count")
        await asyncio.sleep(max(duration, 30.0))
        moved = num(bus, "outfeed.count") - before
        check.note(f"production ends: running={station.running} tripped={station.tripped} "
                   f"step={state['step']} actual={num(bus, 'infeed.actual'):.1f} "
                   f"at_station={bit(bus, 'atstation.detect')} "
                   f"present={bit(bus, 'scanner.present')} pos={num(bus, 'gantry.position'):.1f}")

        check(state["placed"] >= 2,
              f"the sequencer completed at least two full cycles "
              f"({state['placed']} releases) -- one is a scene that happens to "
              f"work once, not one that runs")
        check(moved >= 1,
              f"and the carton it placed reached the outfeed "
              f"(count rose by {moved:.0f})")
        # The gap only has to exist. How large it gets depends on where in the
        # ramp the sample lands, and asserting a size would be asserting a
        # sampling accident.
        check(state["max_ramp_gap"] > 1.0,
              f"the drive's actual speed lagged its reference during the ramp "
              f"(largest gap seen {state['max_ramp_gap']:.1f} %)")
        check(len(state["codes"]) >= 1 and state["codes"].issubset({101, 102, 201}),
              f"the scanner read real item codes {sorted(state['codes'])}")

        # Seize the gantry mid-run: the axis must stop where it is, even though
        # the target still says somewhere else.
        await bus.force({"gantry.fault": True})
        await asyncio.sleep(0.6)
        frozen = num(bus, "gantry.position")
        await asyncio.sleep(1.5)
        check(abs(num(bus, "gantry.position") - frozen) < 0.5,
              f"a seized gantry freezes where it is (was {frozen:.1f} %, "
              f"now {num(bus, 'gantry.position'):.1f} %)")
        await bus.force(clear=["gantry.fault"])
    finally:
        stop_event.set()
        await task

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"placed={state['placed']} outfeed={int(num(bus, 'outfeed.count'))} "
          f"codes={sorted(state['codes'])} rampgap={state['max_ramp_gap']:.1f}% "
          f"empty_picks={state['empty']}/{state['attempts']} estop={estop_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_heat_treat_station(bus: TagBusClient, duration: float,
                                   verbose: bool) -> tuple[bool, str]:
    """PI control of a first-order thermal plant (CP-30).

    What this scene is for, and what is measured here, is the standing offset.
    The plate loses heat in proportion to how far above ambient it is, so
    holding a temperature needs a standing heater output -- and a proportional
    controller can only produce one from a standing error. The run measures that
    error with the integral term switched off, then switches it on and checks
    the error actually closes. Nothing else in the project demonstrates why
    integral action exists rather than merely asserting that it does.

    The element fault is the other half: `oven.heater` still reads whatever was
    commanded while the temperature falls, so a controller watching only its own
    output learns nothing.
    """
    GAIN = 3.5
    #: Enough authority that the integral term can supply the whole standing
    #: output on its own. At 180 degC the plate loses (180-20)*0.30 = 48 degC/s
    #: of heat, which is about 53 % of the element -- an integral that can only
    #: contribute 11 % (the first tuning here) cannot close the offset it was
    #: added to close, and reads as "integral action does not work".
    INTEGRAL_GAIN = 0.6
    INTEGRAL_LIMIT = 140.0

    check = Checks(verbose)
    #: `manual` overrides the controller's own output when it is not None. Used
    #: only by the element-fault leg, which has to command full power while the
    #: station is tripped -- something the interlocks correctly refuse to do on
    #: their own, and the only way to show that the output tells you nothing.
    state = {"integral": 0.0, "use_integral": False, "power": 0.0, "manual": None}

    async def tick(dt: float) -> None:
        station.scan()
        temperature = num(bus, "oven.temperature")
        setpoint = station.setpoint
        power = 0.0

        if state["manual"] is not None:
            power = state["manual"]
        elif station.running:
            error = setpoint - temperature
            proportional = error * GAIN
            if state["use_integral"]:
                # Only while the output is not saturated: integrating through a
                # cold start's flat-out heating is textbook windup.
                if -100.0 < proportional < 100.0:
                    state["integral"] = max(-INTEGRAL_LIMIT,
                                            min(INTEGRAL_LIMIT,
                                                state["integral"] + error * dt))
            else:
                state["integral"] = 0.0
            power = max(0.0, min(100.0, proportional + state["integral"] * INTEGRAL_GAIN))
        else:
            state["integral"] = 0.0

        state["power"] = power
        await write_present(bus, {
            "oven.heater": power,
            "temp_gauge.value": temperature,
            "temp_readout.value": round(temperature),
            "alarm.beacon": temperature > setpoint + 25.0 or station.drive_faulted,
            "alarm.horn": station.drive_faulted,
            **station.lamps(),
        })

    async def hold(seconds: float) -> float:
        """Run for a while and return the mean error over the last third -- the
        steady-state error, rather than a single sample that could land anywhere
        on a still-moving ramp."""
        start = time.perf_counter()
        samples: list[float] = []
        while time.perf_counter() - start < seconds:
            await asyncio.sleep(0.1)
            if time.perf_counter() - start > seconds * 2 / 3:
                samples.append(station.setpoint - num(bus, "oven.temperature"))
            if verbose and int((time.perf_counter() - start) * 2) % 10 == 0:
                print(f"  t={time.perf_counter() - start:5.1f}s "
                      f"T={num(bus, 'oven.temperature'):6.1f} "
                      f"power={state['power']:5.1f} I={state['integral']:6.1f}")
        return sum(samples) / len(samples) if samples else 0.0

    station = Station(bus, faults=("oven.fault",))
    stop_event, task = controller(tick)
    estop_ms = -1.0
    p_error = pi_error = 0.0

    try:
        await turn_pot(bus, 180.0)
        await asyncio.sleep(0.2)
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: num(bus, "oven.heater") > 0.5,
                                             "the heater output")

        budget = max(duration, 50.0)
        state["use_integral"] = False
        p_error = await hold(budget * 0.45)
        check(p_error > 0.5,
              f"proportional control alone parks below setpoint "
              f"({p_error:.1f} degC short) -- the offset this scene exists to show")

        state["use_integral"] = True
        pi_error = await hold(budget * 0.55)
        check(pi_error < p_error,
              f"adding integral action closes that offset "
              f"({p_error:.1f} degC -> {pi_error:.1f} degC)")
        check(abs(pi_error) < 8.0,
              f"and holds the setpoint within 8 degC ({pi_error:.1f} degC off)")

        # A failed element, in two parts.
        #
        # First: the controller does the right thing and trips. A drive fault
        # latches the station exactly as the mushroom does, so the heater
        # command goes to zero -- which is correct, and is also why this leg
        # cannot end there. A controller that trips proves nothing about
        # whether the *output* could have told it anything.
        hot = num(bus, "oven.temperature")
        await bus.force({"oven.fault": True})
        await asyncio.sleep(1.0)
        check(bit(bus, "panel.red"),
              "a failed element trips the station, exactly like the mushroom does")

        # Second, and this is the lesson: hold the element at full power by
        # hand, with the fault standing, and watch the temperature fall anyway.
        # The command reads 100 % throughout. Nothing on the output side of
        # this controller could distinguish that from a working heater --
        # only the measurement can.
        state["manual"] = 100.0
        await asyncio.sleep(6.0)
        cooled = num(bus, "oven.temperature")
        check(cooled < hot - 2.0,
              f"and cools while commanded flat out ({hot:.1f} degC -> {cooled:.1f} degC)")
        check(num(bus, "oven.heater") > 99.0,
              f"with the heater output still reading "
              f"{num(bus, 'oven.heater'):.0f} % the whole time")
        state["manual"] = None
        await bus.force(clear=["oven.fault"])
    finally:
        stop_event.set()
        await task

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"p_offset={p_error:.1f}degC pi_offset={pi_error:.1f}degC "
          f"final={num(bus, 'oven.temperature'):.1f}degC estop={estop_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


async def drive_accumulation_buffer(bus: TagBusClient, duration: float,
                                    verbose: bool) -> tuple[bool, str]:
    """Accumulation, and a release measured in distance rather than seconds
    (LP-20).

    Two claims, and the second is the one worth the scene:

    1. A raised blade stop holds product on a belt that never stops. Nothing
       gets past it, and the encoder keeps counting the whole time -- so "the
       line stopped" is ruled out as the explanation.
    2. A release window of N encoder pulses lets the same amount of product
       through at any line speed. The run does the same release at 40 % and at
       80 % of the drive and compares the counts; the wall-clock times differ by
       about half, which is exactly what a controller written with a timer would
       have got wrong.

    Cartons are counted by the remover at the end of the lane, not by the
    photo-eye. Accumulated product travels touching, and an eye cannot separate
    two cartons with no gap between them -- which is true of a real eye too, and
    is why a real line counts at a point where the product has been singulated.

    Mirrors AccumulationBufferProfile.
    """
    #: Pulses of belt travel the blade stays down for. 100 pulses is one metre.
    WINDOW = 120.0
    SLOW = 40.0
    FAST = 80.0
    EMIT_HALF_PERIOD = 0.6

    check = Checks(verbose)
    #: `manual_run` overrides the station's own verdict when it is not None.
    #: Used only by the seized-stop leg, which has to keep the line moving with
    #: a drive fault standing -- something the interlocks correctly refuse to do
    #: on their own, and the only way to show that the raise command tells you
    #: nothing. Same device the heat-treat exercise uses, for the same reason.
    state = {
        "emit": False, "next_toggle": 0.0, "feeding": False,
        "raise_blade": True, "reference": SLOW, "manual_run": None,
    }

    async def tick(dt: float) -> None:
        station.scan()
        running = station.running if state["manual_run"] is None else state["manual_run"]

        now = time.perf_counter()
        if state["feeding"] and running:
            if now >= state["next_toggle"]:
                state["emit"] = not state["emit"]
                state["next_toggle"] = now + EMIT_HALF_PERIOD
        else:
            state["emit"] = False
            state["next_toggle"] = now + EMIT_HALF_PERIOD

        # The eye is watched, not counted on. Accumulated product travels
        # touching, so a batch coming past the blade breaks the beam once and
        # clears it once however many cartons are in it. What it can honestly
        # say is whether anything is moving past the stop at all -- which is how
        # a blade seized *down* becomes visible while the controller is still
        # commanding `raise`. Mirrors AccumulationBufferProfile.
        flowing = bit(bus, "exit_eye.detect")
        holding = state["raise_blade"] or not running

        lamps = station.lamps()
        # The one scene that lights two tower stages at once, deliberately:
        # green is "running", amber is "running and holding".
        if running and state["raise_blade"]:
            lamps["tower.yellow"] = True
        if flowing and holding:
            lamps["panel.red"] = True

        await write_present(bus, {
            "buffer.run": running,
            "buffer.speed": state["reference"] if running else 0.0,
            "outfeed.rotate": running,
            "emitter.emit": state["emit"],
            # A stopped line holds what it has: dropping the blade while the
            # belt is off would spill the whole buffer the moment it restarted.
            "stop.raise": holding,
            "count_display.value": int(num(bus, "released.count")),
            **lamps,
        })

    async def accumulate(seconds: float) -> None:
        state["raise_blade"] = True
        state["feeding"] = True
        await asyncio.sleep(seconds)
        state["feeding"] = False
        await asyncio.sleep(1.0)

    async def release(reference: float, drain: float) -> tuple[int, float]:
        """Drop the blade for WINDOW pulses of belt travel, then wait for
        whatever escaped to reach the remover. Returns how many cartons came
        out and how long the window itself took."""
        state["reference"] = reference
        await asyncio.sleep(1.5)            # let the drive finish its ramp

        before = int(num(bus, "released.count"))
        start_count = num(bus, "enc.count")
        started = time.perf_counter()

        state["raise_blade"] = False
        while num(bus, "enc.count") - start_count < WINDOW:
            if time.perf_counter() - started > 30.0:
                break
            await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - started
        state["raise_blade"] = True

        await asyncio.sleep(drain)
        return int(num(bus, "released.count")) - before, elapsed

    station = Station(bus, faults=("buffer.fault", "stop.fault"))
    stop_event, task = controller(tick)
    estop_ms = -1.0
    slow_out = fast_out = 0
    slow_secs = fast_secs = 0.0

    try:
        await turn_pot(bus, WINDOW)
        await asyncio.sleep(0.2)
        # The *command*, not the belt's last revolution: this drive ramps down,
        # and a VFD that coasts is not an E-stop failure -- it is why a real
        # E-stop circuit removes power. Same measurement the pick-and-place
        # cell makes, for the same reason.
        estop_ms = await exercise_interlocks(bus, station, check,
                                             lambda: bit(bus, "buffer.run"),
                                             "the buffer drive")

        # --- 1. a raised blade holds the lot
        state["reference"] = SLOW
        held_before = int(num(bus, "released.count"))
        pulses_before = num(bus, "enc.count")
        await accumulate(12.0)
        held_after = int(num(bus, "released.count"))
        pulses_after = num(bus, "enc.count")

        check(held_after == held_before,
              f"nothing gets past a raised blade stop "
              f"({held_after - held_before} carton(s) escaped)")
        check(pulses_after - pulses_before > 100.0,
              f"and the belt ran the whole time, so that is the blade and not a "
              f"stopped line ({pulses_after - pulses_before:.0f} pulses of travel)")
        check(bit(bus, "stop.up") and not bit(bus, "stop.down"),
              "the blade reports its raised limit while it is holding")

        # --- 2. the same window at two line speeds
        slow_out, slow_secs = await release(SLOW, 14.0)
        check(slow_out >= 2,
              f"a release at {SLOW:.0f} % lets product through ({slow_out} cartons)")

        await accumulate(12.0)
        fast_out, fast_secs = await release(FAST, 9.0)

        check(abs(slow_out - fast_out) <= 1,
              f"the same pulse window releases the same amount at twice the speed "
              f"({slow_out} at {SLOW:.0f} % vs {fast_out} at {FAST:.0f} %)")
        check(fast_secs < slow_secs * 0.75,
              f"while taking about half as long ({slow_secs:.1f}s vs {fast_secs:.1f}s) "
              f"-- which is what a controller timed in seconds would have got wrong")

        # --- 3. a seized blade, with the command still on
        #
        # Seized *down*, which is the failure that matters: a stop frozen in
        # its raised position is merely a line that will not run, and everybody
        # notices that within a minute.
        await accumulate(10.0)
        state["reference"] = FAST
        state["raise_blade"] = False
        await asyncio.sleep(1.4)
        await bus.force({"stop.fault": True})
        await asyncio.sleep(1.0)
        check(bit(bus, "panel.red"),
              "a seized stop trips the station, exactly like the mushroom does")

        # Which is correct, and is also why this leg cannot end there: a
        # controller that trips proves nothing about whether its own *output*
        # could have told it anything. So hold the line running by hand, with
        # the fault standing and the raise command on, and watch.
        state["manual_run"] = True
        state["raise_blade"] = True
        await asyncio.sleep(2.0)
        check(not bit(bus, "stop.up"),
              "a seized blade never reaches its raised limit, however long the "
              "raise command is held")
        escaped_before = int(num(bus, "released.count"))
        await asyncio.sleep(10.0)
        check(int(num(bus, "released.count")) > escaped_before,
              "and product keeps escaping past it while the controller believes "
              "it is holding -- the limit switch is the only honest thing to read")
        state["manual_run"] = None
        await bus.force(clear=["stop.fault"])
        await asyncio.sleep(2.5)
        check(bit(bus, "stop.up"), "clearing the fault lets the blade finish rising")
    finally:
        stop_event.set()
        await task

    print(f"RESULT sequence={'PASS' if not check.problems else 'FAIL'} "
          f"window={WINDOW:.0f}p slow={slow_out}@{slow_secs:.1f}s "
          f"fast={fast_out}@{fast_secs:.1f}s "
          f"outfeed={int(num(bus, 'released.count'))} estop={estop_ms:.0f}ms")
    return not check.problems, "; ".join(check.problems)


DRIVERS = {
    "sorting-by-height": drive_sorting_by_height,
    "start-stop-station": drive_start_stop_station,
    "tank-level-control": drive_tank_level_control,
    "light-curtain-sorting": drive_light_curtain_sorting,
    "roller-line-weighing": drive_roller_line_weighing,
    "pick-and-place-cell": drive_pick_and_place_cell,
    "heat-treat-station": drive_heat_treat_station,
    "accumulation-buffer": drive_accumulation_buffer,
}

#: The *production* window, not the whole run: every scene now runs the shared
#: operator sequence first (~7s) and a setpoint demonstration after, so the wall
#: clock is longer than the number here. Long enough for a real cycle, not just
#: to prove the line is wired, and every one ends with a drain phase -- feeding
#: stops and the lane clears -- so counters can be checked for conservation
#: rather than just for having moved.
DEFAULT_DURATION = {
    "sorting-by-height": 35.0,    # long enough for both pot settings to sort
    "start-stop-station": 30.0,   # budget for the batch, not a fixed run length
    "tank-level-control": 50.0,   # two setpoints, half the budget each
    "light-curtain-sorting": 28.0,
    "roller-line-weighing": 30.0,
    "pick-and-place-cell": 34.0,   # several full gantry cycles, not just one
    "heat-treat-station": 50.0,    # the P-only offset, then PI closing it
    # Two accumulate-and-release cycles plus their drains, and the drains are
    # most of it: a released carton has two metres to travel before the remover
    # can count it.
    "accumulation-buffer": 95.0,
}


async def run_scene(godot: str, entry: dict, duration: float | None, verbose: bool) -> int:
    scene_id = entry["id"]
    run_duration = duration if duration is not None else DEFAULT_DURATION[scene_id]

    eng: Engine | None = None

    if port_listening():
        # Something is already on the port -- most likely the windowed engine
        # a "Try this scene" button in the editor itself would be talking to
        # (UX-31). Attach to it rather than refusing outright: a real PLC
        # test tool that insists on starting its own engine could never be
        # pressed from inside the one already open on screen.
        try:
            bus, runner = await connect(timeout=5)
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
        # Deliberately *not* --deterministic, even for sorting-by-height.
        # Two reasons, and only the second one survives now that the
        # deterministic scene has a panel of its own: this runs the line the
        # way a user actually opens it, and an exact count needs a belt that
        # runs for a fixed length of time -- which is precisely what pressing
        # Stop and striking an E-stop mid-run takes away. tall=5/short=5 stays
        # in tools/drive_engine.py, the tool written for it (OP-03).
        print(f"Starting engine for '{entry['title']}'...")
        eng = Engine(godot, entry)

        if not await wait_for_port(20.0):
            print("RESULT engine never opened the tag bus port")
            print(eng.tail())
            eng.stop()
            return 1

        try:
            bus, runner = await connect()
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
        ok, problem = await DRIVERS[scene_id](bus, run_duration, verbose)
        if ok:
            print(f"PASS — {entry['title']}")
        else:
            print(f"FAIL — {entry['title']}: {problem}")
        return 0 if ok else 1
    finally:
        runner.cancel()
        if eng is not None:
            eng.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", help="manifest id, e.g. sorting-by-height (see --list)")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to run before checking (default varies per scene)")
    parser.add_argument("--verbose", action="store_true", help="print progress while running")
    parser.add_argument("--list", action="store_true", help="list scene ids and exit")
    args = parser.parse_args(argv)

    manifest = load_manifest()

    if args.list:
        for entry in manifest:
            print(f"{entry['id']:24s} {entry['title']}")
        return 0

    if not args.scene:
        parser.error("--scene is required (or pass --list to see the ids)")

    entry = next((e for e in manifest if e["id"] == args.scene), None)
    if entry is None:
        ids = ", ".join(e["id"] for e in manifest)
        print(f"RESULT unknown scene {args.scene!r} -- known ids: {ids}")
        return 1

    if entry["id"] not in DRIVERS:
        print(f"RESULT no driver for {entry['id']!r} yet")
        return 1

    godot = find_godot()
    if not godot:
        print("RESULT could not find a Godot .NET binary -- set $GODOT or put it on PATH")
        return 1

    return asyncio.run(run_scene(godot, entry, args.duration, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
