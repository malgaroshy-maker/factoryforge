"""Siemens native protocol drivers (PLCSIM Advanced API & snap7), against fakes.

There is no PLC in CI and no Siemens software either, so the PLC side is a
bytearray behind an object shaped like a snap7 `Client`. That fake is not a
convenience: it is the only way to ask for the three behaviours a real PLC on a
desk will not perform on demand -- refusing a connection, failing a read, and
taking half a second over a call while you watch what the event loop does.

The fake supplies its own `snap7.util`, so these tests run identically whether
or not python-snap7 is installed. `test_the_fake_agrees_with_real_snap7`
keeps that substitution honest wherever the real library is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from factoryforge_sidecar import drivers
from factoryforge_sidecar.drivers import plcsim_advanced, s7_snap7
from factoryforge_sidecar.tags import Tag, TagTable, TagValue


# --- the fake PLC -------------------------------------------------------------

class FakeUtil:
    """S7 memory layout: big-endian words, bit 0 is the byte's LSB.

    Hand-written rather than imported so the tests do not need python-snap7.
    """

    @staticmethod
    def get_bool(data, byte: int, bit: int) -> bool:
        return bool(data[byte] & (1 << bit))

    @staticmethod
    def set_bool(data, byte: int, bit: int, value: bool) -> None:
        if value:
            data[byte] |= 1 << bit
        else:
            data[byte] &= ~(1 << bit) & 0xFF

    @staticmethod
    def get_dint(data, byte: int) -> int:
        return struct.unpack_from(">i", data, byte)[0]

    @staticmethod
    def set_dint(data, byte: int, value: int) -> None:
        struct.pack_into(">i", data, byte, int(value))

    @staticmethod
    def get_real(data, byte: int) -> float:
        return struct.unpack_from(">f", data, byte)[0]

    @staticmethod
    def set_real(data, byte: int, value: float) -> None:
        struct.pack_into(">f", data, byte, float(value))


class FakePlc:
    """One DB, and the knobs a test needs to misbehave on purpose."""

    def __init__(self, size: int = 10, db_number: int = 1) -> None:
        self.db_number = db_number
        self.memory = bytearray(size)
        self.refuse = False
        self.read_error: Exception | None = None
        #: Seconds each call blocks its *thread* -- exactly as a real ctypes
        #: call into snap7 does. A driver that runs one on the event loop stops
        #: the event loop for this long.
        self.call_delay = 0.0
        self.connect_attempts = 0
        self.clients: list["FakeClient"] = []
        self.writes: list[tuple[int, bytes]] = []
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def client(self) -> "FakeClient":
        client = FakeClient(self)
        self.clients.append(client)
        return client

    @contextlib.contextmanager
    def _call(self):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.call_delay:
                time.sleep(self.call_delay)
            yield
        finally:
            with self._lock:
                self.in_flight -= 1


class FakeClient:
    def __init__(self, plc: FakePlc) -> None:
        self._plc = plc
        self._connected = False
        self.destroyed = False

    def connect(self, host, rack, slot) -> None:
        with self._plc._call():
            self._plc.connect_attempts += 1
            if self._plc.refuse:
                raise RuntimeError(f"connection to {host} refused")
            self._connected = True

    def disconnect(self) -> None:
        with self._plc._call():
            self._connected = False

    def get_connected(self) -> bool:
        return self._connected

    def destroy(self) -> None:
        self.destroyed = True

    def db_read(self, db_number: int, start: int, size: int) -> bytes:
        with self._plc._call():
            if self._plc.read_error is not None:
                raise self._plc.read_error
            if not self._connected:
                raise RuntimeError("not connected")
            if db_number != self._plc.db_number:
                raise RuntimeError(f"DB{db_number} does not exist on this CPU")
            if start + size > len(self._plc.memory):
                # Exactly how a real overrun fails -- see AGENTS.md gotcha 19c.
                raise RuntimeError("Invalid address (0x05)")
            return bytes(self._plc.memory[start:start + size])

    def db_write(self, db_number: int, start: int, data) -> None:
        with self._plc._call():
            if not self._connected:
                raise RuntimeError("not connected")
            if start + len(data) > len(self._plc.memory):
                raise RuntimeError("Invalid address (0x05)")
            self._plc.memory[start:start + len(data)] = data
            self._plc.writes.append((start, bytes(data)))


class FakeBus:
    """Just enough `TagBusClient` for a driver to run against."""

    def __init__(self) -> None:
        self.written: dict[str, TagValue] = {}
        self.statuses: list[tuple[str, str, str]] = []

    def on_describe(self, hook) -> None: ...
    def on_update(self, hook) -> None: ...
    def on_disconnect(self, hook) -> None: ...

    async def write(self, tag_id: str, value: TagValue) -> None:
        self.written[tag_id] = value

    async def write_many(self, values: dict[str, TagValue]) -> None:
        self.written.update(values)

    async def status(self, level: str, code: str, message: str) -> None:
        self.statuses.append((level, code, message))


#: A mapping shaped like the real FF_IO: 10 bytes, with the two directions in
#: separate bytes (gotcha 19d) and the DInt counters on doubleword boundaries.
FF_IO = {
    "conveyor.rotate": "DBX0.0",
    "emitter.emit": "DBX0.1",
    "pusher.extend": "DBX0.2",
    "stack_light.green": "DBX0.3",
    "sensor_low.detect": "DBX1.0",
    "sensor_high.detect": "DBX1.1",
    "pusher.extended": "DBX1.2",
    "pusher.retracted": "DBX1.3",
    "counter.tall": "DBD2",
    "counter.short": "DBD6",
}


def sorting_table() -> TagTable:
    """The sorting demo's ten tags, with `pusher.retracted` true as it really
    is at rest -- the one tag that can prove initial state was transferred."""
    return TagTable([
        Tag("conveyor.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
        Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
        Tag("pusher.extend", "Pusher (Extend)", "bit", "output"),
        Tag("stack_light.green", "Stack Light (Green)", "bit", "output"),
        Tag("sensor_low.detect", "Diffuse Sensor Low (Detect)", "bit", "input"),
        Tag("sensor_high.detect", "Diffuse Sensor High (Detect)", "bit", "input"),
        Tag("pusher.extended", "Pusher (Extended)", "bit", "input"),
        Tag("pusher.retracted", "Pusher (Retracted)", "bit", "input", True),
        Tag("counter.tall", "Counter (Tall)", "int", "input"),
        Tag("counter.short", "Counter (Short)", "int", "input"),
    ])


async def settle(check, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll until *check* is truthy. `interval` stays above Windows' 15.6ms
    asyncio clock resolution -- below it, sleeps return immediately and the
    loop spins without real time passing (gotcha 2)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if check():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.fixture
def plc(monkeypatch) -> FakePlc:
    """A fake S7 in place of the snap7 module, for the whole of one test."""
    fake = FakePlc()
    monkeypatch.setattr(
        s7_snap7, "snap7",
        SimpleNamespace(client=SimpleNamespace(Client=fake.client), util=FakeUtil),
        raising=False,
    )
    monkeypatch.setattr(s7_snap7, "HAS_SNAP7", True)
    monkeypatch.setattr(s7_snap7, "RECONNECT_DELAY", 0.2)
    return fake


@pytest.fixture
def fake_bus() -> FakeBus:
    return FakeBus()


def make_snap7(fake_bus, **kwargs):
    kwargs.setdefault("mapping", dict(FF_IO))
    return s7_snap7.S7Snap7Driver(fake_bus, **kwargs)


# --- the fake PLCSIM Advanced instance ----------------------------------------

#: What a TIA symbol map looks like: quotes are part of the identifier.
PLCSIM_SYMBOLS = {tag_id: f'"FF_IO".{tag_id}' for tag_id in FF_IO}


class FakeSimInstance:
    """The .NET Simulation Runtime API surface this driver actually touches."""

    def __init__(self) -> None:
        self.values: dict[str, TagValue] = {}
        self.tag_list_updated = False
        #: Gotcha 18: attaching to a CPU is not owning it. Nothing here should
        #: ever be called, and a test asserts so.
        self.power_calls: list[str] = []

    def UpdateTagList(self) -> None:
        self.tag_list_updated = True

    def PowerOn(self) -> None: self.power_calls.append("PowerOn")
    def PowerOff(self) -> None: self.power_calls.append("PowerOff")
    def Run(self) -> None: self.power_calls.append("Run")

    def WriteBool(self, symbol, value) -> None: self.values[symbol] = bool(value)
    def WriteInt32(self, symbol, value) -> None: self.values[symbol] = int(value)
    def WriteDouble(self, symbol, value) -> None: self.values[symbol] = float(value)

    def ReadBool(self, symbol) -> bool: return bool(self.values.get(symbol, False))
    def ReadInt32(self, symbol) -> int: return int(self.values.get(symbol, 0))
    def ReadDouble(self, symbol) -> float: return float(self.values.get(symbol, 0.0))


@pytest.fixture
def sim_instance(monkeypatch) -> FakeSimInstance:
    """Stand in for the Siemens assembly `start()` imports from."""
    instance = FakeSimInstance()
    module = SimpleNamespace(
        SimulationRuntimeManager=SimpleNamespace(
            CreateInterface=lambda name: instance))
    for name in ("Siemens", "Siemens.Simatic", "Siemens.Simatic.Simulation",
                 "Siemens.Simatic.Simulation.Runtime"):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(plcsim_advanced, "HAS_PYTHONNET", True)
    monkeypatch.setattr(plcsim_advanced, "_load_runtime_api", lambda: None)
    return instance


def make_plcsim(fake_bus, **kwargs):
    kwargs.setdefault("mapping", dict(PLCSIM_SYMBOLS))
    return plcsim_advanced.PLCSIMAdvancedDriver(fake_bus, **kwargs)


def test_the_fake_agrees_with_real_snap7():
    """The fake's memory layout stands in for snap7.util everywhere these tests
    run. Where the real library is installed, check the substitution holds --
    otherwise every offset assertion below is measuring the fake."""
    real = pytest.importorskip("snap7.util")
    data = bytearray(12)
    FakeUtil.set_bool(data, 1, 3, True)
    FakeUtil.set_dint(data, 4, -70000)
    FakeUtil.set_real(data, 8, 3.5)
    assert real.get_bool(data, 1, 3) is True
    assert real.get_dint(data, 4) == -70000
    assert real.get_real(data, 8) == pytest.approx(3.5)

    other = bytearray(12)
    real.set_bool(other, 1, 3, True)
    real.set_dint(other, 4, -70000)
    real.set_real(other, 8, 3.5)
    assert bytes(other) == bytes(data)


def test_siemens_drivers_registered() -> None:
    available = drivers.available()
    assert "plcsim-advanced" in available
    assert "s7-snap7" in available
    assert "mock" in available
    assert "modbus-tcp" in available


@pytest.mark.asyncio
async def test_plcsim_advanced_driver_lifecycle() -> None:
    mock_bus = MagicMock()
    drv = drivers.create("plcsim-advanced", mock_bus)
    assert drv.driver_name == "plcsim-advanced"
    assert hasattr(drv, "instance_name")


@pytest.mark.asyncio
async def test_s7_snap7_driver_lifecycle() -> None:
    mock_bus = MagicMock()
    drv = drivers.create("s7-snap7", mock_bus)
    assert drv.driver_name == "s7-snap7"
    assert hasattr(drv, "host")
    assert hasattr(drv, "db_number")


# --- what a frozen release can actually run -----------------------------------

def test_usable_distinguishes_registered_from_runnable():
    """A driver can be registered and still unable to run.

    Every protocol driver guards its own third-party import and registers
    regardless, so it can explain itself at connect time rather than vanishing
    from the CLI. That is the right behaviour and it makes `available()` a
    misleading answer to "what does this build support" -- which matters for a
    PyInstaller release, because it bundles whatever was importable when it was
    built. A release built without an extra ships a driver that is present,
    listed, and dead (see tools/packaging/check_release.py).
    """
    from factoryforge_sidecar import drivers

    report = drivers.usable()
    assert set(report) == set(drivers.available()), "every registered driver is reported"
    assert all(isinstance(ok, bool) for ok in report.values())

    # mock and modbus-tcp have no third-party dependency at all, so they are
    # usable in any build -- including a CI one with no extras installed.
    assert report["mock"] is True
    assert report["modbus-tcp"] is True


def test_usable_tracks_the_dependency_flag_each_driver_sets():
    from factoryforge_sidecar import drivers
    from factoryforge_sidecar.drivers import plcsim_advanced, s7_snap7

    report = drivers.usable()
    assert report["s7-snap7"] == s7_snap7.HAS_SNAP7
    assert report["plcsim-advanced"] == plcsim_advanced.HAS_PYTHONNET


# --- snap7 off the event loop, and a connection that comes back ---------------

async def test_start_returns_promptly_when_the_plc_will_not_answer(plc, fake_bus):
    """A PLC that is off must leave the simulation running and the CLI honest
    about why nothing is moving, not park the sidecar on a socket."""
    plc.refuse = True
    driver = make_snap7(fake_bus)
    try:
        await asyncio.wait_for(driver.start(), timeout=2)
        assert not driver.connected.is_set()
        assert await settle(lambda: any(code == "plc_disconnected"
                                        for _, code, _ in fake_bus.statuses)), \
            "a driver that never connected reported nothing"
    finally:
        await driver.stop()


async def test_a_refused_connection_is_retried(plc, fake_bus):
    """It used to be caught, logged, and that was the end of it: no poll task,
    no second attempt, and a CLI that printed 'driver started'."""
    plc.refuse = True
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: plc.connect_attempts >= 2), \
            f"only {plc.connect_attempts} attempt(s); the driver gave up"
    finally:
        await driver.stop()


async def test_the_plc_coming_up_connects_the_driver(plc, fake_bus):
    driver = make_snap7(fake_bus)
    plc.refuse = True
    try:
        await driver.start()
        assert await settle(lambda: plc.connect_attempts >= 1)
        assert not driver.connected.is_set()

        plc.refuse = False
        assert await settle(lambda: driver.connected.is_set()), \
            "the driver never noticed the PLC come up"
    finally:
        await driver.stop()


async def test_a_snap7_call_does_not_stop_the_event_loop(plc, fake_bus):
    """The failure this covers is invisible from inside the driver: connect, DB
    read and DB write were synchronous ctypes calls awaited on the loop, so for
    their whole duration the tag bus, the write flusher and every other driver
    stopped -- and for a PLC that has just gone away that duration is a socket
    timeout, not a millisecond."""
    plc.call_delay = 0.4
    driver = make_snap7(fake_bus)
    loop = asyncio.get_running_loop()
    try:
        await driver.start()
        assert await settle(lambda: plc.in_flight > 0, timeout=2), \
            "no snap7 call was ever observed in flight -- it ran to completion " \
            "without the loop getting a turn, which is the bug"

        before = loop.time()
        await asyncio.sleep(0.05)
        lag = loop.time() - before
        assert plc.in_flight > 0, "the call finished first; nothing was proven"
        assert lag < 0.2, f"the event loop was blocked for {lag:.2f}s by a snap7 call"
    finally:
        await driver.stop()


async def test_one_call_is_in_the_client_at_a_time(plc, fake_bus):
    """Reads and writes share one snap7 Client, which is one socket and one
    ctypes handle. Offloading them to threads without serializing them is the
    same data race with a thread pool in front of it."""
    driver = make_snap7(fake_bus)
    table = sorting_table()
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, table)

        plc.call_delay = 0.05
        plc.max_in_flight = 0
        await asyncio.gather(*[
            driver.push({"counter.tall": n, "sensor_low.detect": bool(n % 2)})
            for n in range(6)
        ])
        assert plc.max_in_flight == 1, \
            f"{plc.max_in_flight} snap7 calls were in the same client at once"
    finally:
        await driver.stop()


async def test_stop_leaves_no_session_open(plc, fake_bus):
    driver = make_snap7(fake_bus)
    await driver.start()
    assert await settle(lambda: driver.connected.is_set())
    await driver.stop()
    assert not driver.connected.is_set()
    assert all(not c.get_connected() for c in plc.clients)


async def test_a_dead_plc_is_dropped_and_reconnected(plc, fake_bus):
    """A read can fail because the mapping is wrong or because the PLC has
    gone. Only the second wants a new session, and snap7 is what knows."""
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())
        first = plc.connect_attempts

        # The CPU drops the line: reads fail and the client knows it is down.
        for client in plc.clients:
            client._connected = False
        plc.read_error = RuntimeError("connection reset")

        assert await settle(lambda: not driver.connected.is_set()), \
            "a dead session was never let go of"
        plc.read_error = None
        assert await settle(lambda: plc.connect_attempts > first), \
            "the driver never tried to rebuild the session"
    finally:
        await driver.stop()


# --- initial simulator inputs reach the PLC -----------------------------------
#
# The engine publishes deltas after the description, so an input that never
# changes is never sent. `pusher.retracted` starts true and stays true, which
# makes it the tag that can prove seeding happened: nothing else would ever
# write it.

def retracted(plc: FakePlc) -> bool:
    return FakeUtil.get_bool(plc.memory, 1, 3)      # pusher.retracted, DBX1.3


async def test_an_input_that_never_changes_is_established_on_connect(plc, fake_bus):
    """The description arriving first is the ordinary case: the bus replays it
    to a driver constructed after it, long before the PLC answers."""
    plc.refuse = True
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: plc.connect_attempts >= 1)
        assert not retracted(plc), "nothing should have reached an unreachable PLC"

        plc.refuse = False
        assert await settle(lambda: driver.connected.is_set())
        assert await settle(lambda: retracted(plc)), \
            "a stable-true input never reached the PLC"
    finally:
        await driver.stop()


async def test_a_description_arriving_after_the_connection_also_seeds(plc, fake_bus):
    """The other order, which is what a scene edit looks like."""
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        assert not retracted(plc)

        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: retracted(plc)), \
            "rebuild against a live connection never transferred initial state"
    finally:
        await driver.stop()


async def test_a_reconnect_seeds_again_without_a_new_description(plc, fake_bus):
    """A reconnect does not necessarily bring a new description with it, so
    seeding cannot live only inside rebuild(). The CPU on the other side may
    have been restarted, or downloaded to, while we were away."""
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: retracted(plc))

        # The line drops, and the CPU comes back with that address cleared.
        for client in plc.clients:
            client._connected = False
        plc.read_error = RuntimeError("connection reset")
        assert await settle(lambda: not driver.connected.is_set())
        plc.memory[:] = bytearray(len(plc.memory))
        plc.read_error = None

        assert await settle(lambda: driver.connected.is_set())
        assert await settle(lambda: retracted(plc)), \
            "the new session was left with whatever that address happened to hold"
    finally:
        await driver.stop()


async def test_plcsim_seeds_initial_inputs_when_it_attaches(sim_instance, fake_bus):
    driver = make_plcsim(fake_bus)
    await driver.rebuild("sorting", 1, sorting_table())
    try:
        await driver.start()
        assert sim_instance.values.get('"FF_IO".pusher.retracted') is True, \
            "a stable-true input never reached the virtual CPU"
    finally:
        await driver.stop()


async def test_plcsim_seeds_when_the_description_arrives_after_the_attach(
        sim_instance, fake_bus):
    driver = make_plcsim(fake_bus)
    try:
        await driver.start()
        assert '"FF_IO".pusher.retracted' not in sim_instance.values

        await driver.rebuild("sorting", 1, sorting_table())
        assert sim_instance.values.get('"FF_IO".pusher.retracted') is True
    finally:
        await driver.stop()


async def test_a_read_past_the_end_of_the_db_blames_the_length_first(
        plc, fake_bus, caplog):
    """Gotcha 19c: snap7's "Invalid address (0x05)" usually means the read
    overran the DB, not that the block is optimized. The driver asserted the
    latter and handed out TIA instructions for unchecking it -- the exact
    misdiagnosis the handoff document records as having cost real time.
    """
    # A mapping that reaches byte 12 of a DB that is 10 bytes long, which is
    # gotcha 19c's own example.
    mapping = dict(FF_IO, over_the_end="DBD8")
    driver = make_snap7(fake_bus, mapping=mapping)
    try:
        with caplog.at_level(logging.ERROR, logger=s7_snap7.__name__):
            await driver.start()
            assert await settle(lambda: driver.connected.is_set())
            await driver.rebuild("sorting", 1, sorting_table())
            assert await settle(
                lambda: any("Invalid address" in r.getMessage()
                            for r in caplog.records)), "the failure was never explained"
    finally:
        await driver.stop()

    message = next(r.getMessage() for r in caplog.records
                   if "Invalid address" in r.getMessage())
    assert "12-byte read" in message, "the message does not say what was asked for"
    assert "over_the_end" in message and "at least 12 bytes" in message, \
        "the message does not say which address is out of bounds, or by how much"
    assert "optimized" in message, "optimized access is still worth mentioning second"
    assert message.index("past the end") < message.index("optimized"), \
        "optimized block access is still being given as the diagnosis"


# --- offsets, directions, and the byte range a write is allowed to touch ------
#
# AGENTS.md records that all three Siemens drivers once shipped calling
# `TagTable.outputs()` and `tag.kind.value`, neither of which exists, and that
# nothing in the suite would have caught it. Everything below starts, rebuilds,
# polls, pushes and stops a driver, so either mistake raises here.

async def test_the_poller_reads_each_tag_from_its_own_offset(plc, fake_bus):
    """Bit 2 of byte 0 is the pusher, not "the third tag in declaration order".
    The scheme this replaced packed every bit into byte 0 in tag order and
    wrapped at 8 with `% 8`, so the ninth silently overwrote the first."""
    plc.memory[0] = 0b0000_0101         # conveyor.rotate and pusher.extend
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: "conveyor.rotate" in fake_bus.written)
    finally:
        await driver.stop()

    assert fake_bus.written["conveyor.rotate"] is True
    assert fake_bus.written["emitter.emit"] is False
    assert fake_bus.written["pusher.extend"] is True
    assert fake_bus.written["stack_light.green"] is False


async def test_doublewords_are_read_from_their_own_boundaries(plc, fake_bus):
    """DBD2 and DBD6 are four bytes each, big-endian -- the DInt counters used
    to be ignored by the addressing scheme entirely."""
    table = TagTable([
        Tag("setpoint.speed", "Speed", "int", "output"),
        Tag("temp.actual", "Temperature", "float", "output"),
    ])
    struct.pack_into(">i", plc.memory, 2, 70_000)     # past 16 bits on purpose
    struct.pack_into(">f", plc.memory, 6, 21.5)

    driver = make_snap7(fake_bus, mapping={"setpoint.speed": "DBD2",
                                           "temp.actual": "DBD6"})
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("mixed", 1, table)
        assert await settle(lambda: "setpoint.speed" in fake_bus.written)
    finally:
        await driver.stop()

    assert fake_bus.written["setpoint.speed"] == 70_000
    assert fake_bus.written["temp.actual"] == pytest.approx(21.5)


async def test_a_write_lands_on_the_tag_s_own_bit(plc, fake_bus):
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())
        await driver.push({"sensor_high.detect": True})
        struct_before = bytes(plc.memory)

        assert FakeUtil.get_bool(plc.memory, 1, 1) is True, "DBX1.1 was not set"
        assert FakeUtil.get_bool(plc.memory, 1, 0) is False, "it set a neighbour too"
        await driver.push({"counter.tall": 5})
        assert struct.unpack_from(">i", plc.memory, 2)[0] == 5
        assert plc.memory[1] == struct_before[1], "the DInt write reached byte 1"
    finally:
        await driver.stop()


async def test_a_write_does_not_stomp_the_bytes_the_plc_owns(plc, fake_bus):
    """Gotcha 19d: bits are not individually addressable on the wire, so
    setting a simulator bit means read-modify-write of a whole byte. The driver
    must write the narrowest range it can -- here, byte 1 only, leaving the
    PLC's own byte 0 exactly as the program left it."""
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())

        plc.memory[0] = 0xFF                # every PLC-owned output asserted
        await driver.push({"sensor_low.detect": True, "pusher.extended": True})
        assert plc.memory[0] == 0xFF, "a simulator write rewrote PLC-owned bits"
        assert all(start != 0 for start, _ in plc.writes), \
            "byte 0 was written at all, which is the window gotcha 19d is about"
    finally:
        await driver.stop()


async def test_direction_decides_who_writes_what(plc, fake_bus):
    """`kind` is from the controller's point of view, and it is the whole
    contract: an `output` is the PLC's to write and ours to read, an `input`
    the other way round. Getting it backwards is how a driver ends up
    overwriting the program's own outputs with a copy read a moment earlier."""
    driver = make_snap7(fake_bus)
    try:
        await driver.start()
        assert await settle(lambda: driver.connected.is_set())
        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: "conveyor.rotate" in fake_bus.written)

        # Nothing simulator-owned is ever reported back to the bus as if the
        # PLC had produced it.
        assert set(fake_bus.written) == {
            "conveyor.rotate", "emitter.emit", "pusher.extend", "stack_light.green"}

        # And a PLC-owned tag offered to push() is refused, not written.
        plc.memory[0] = 0x00
        plc.writes.clear()
        await driver.push({"conveyor.rotate": True, "pusher.extend": True})
        assert plc.memory[0] == 0x00, "the driver wrote a tag the PLC owns"
        assert plc.writes == []
    finally:
        await driver.stop()


async def test_plcsim_reads_outputs_and_writes_inputs_by_symbol(sim_instance, fake_bus):
    """The PLCSIM driver used to hand tag ids straight to ReadBool, asking the
    CPU for a variable called "conveyor.rotate" that no PLC has ever had, and
    swallowed every resulting exception while reporting itself started."""
    driver = make_plcsim(fake_bus)
    sim_instance.values['"FF_IO".conveyor.rotate'] = True
    try:
        await driver.start()
        await driver.rebuild("sorting", 1, sorting_table())
        assert await settle(lambda: "conveyor.rotate" in fake_bus.written)
        assert fake_bus.written["conveyor.rotate"] is True
        assert set(fake_bus.written) == {
            "conveyor.rotate", "emitter.emit", "pusher.extend", "stack_light.green"}

        await driver.push({"counter.tall": 7, "conveyor.rotate": False})
        assert sim_instance.values['"FF_IO".counter.tall'] == 7
        assert sim_instance.values['"FF_IO".conveyor.rotate'] is True, \
            "the driver wrote a tag the PLC owns"
    finally:
        await driver.stop()


async def test_plcsim_says_which_tags_have_no_symbol(sim_instance, fake_bus, caplog):
    """A partial mapping must be named, not silently skipped: 'connected but
    driving nothing' is the state this driver shipped in for months."""
    partial = {"conveyor.rotate": '"FF_IO".conveyor.rotate'}
    driver = make_plcsim(fake_bus, mapping=partial)
    try:
        with caplog.at_level(logging.WARNING, logger=plcsim_advanced.__name__):
            await driver.start()
            await driver.rebuild("sorting", 1, sorting_table())
            assert await settle(lambda: "conveyor.rotate" in fake_bus.written)
    finally:
        await driver.stop()

    complaints = " ".join(r.getMessage() for r in caplog.records)
    assert "pusher.extend" in complaints and "no PLC symbol" in complaints


async def test_plcsim_updates_the_tag_list_but_nothing_else(sim_instance, fake_bus):
    driver = make_plcsim(fake_bus)
    await driver.start()
    try:
        assert sim_instance.tag_list_updated, "the CPU's symbols were never loaded"
    finally:
        await driver.stop()


async def test_plcsim_never_touches_the_power_state(sim_instance, fake_bus):
    """Gotcha 18: attaching to a CPU somebody else started and downloaded a
    program to is not owning it. stop() used to PowerOff(), so a 40-second run
    switched off the user's PLC."""
    driver = make_plcsim(fake_bus)
    await driver.start()
    await driver.rebuild("sorting", 1, sorting_table())
    await driver.stop()
    assert sim_instance.power_calls == []
