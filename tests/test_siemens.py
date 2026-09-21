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


async def test_plcsim_never_touches_the_power_state(sim_instance, fake_bus):
    """Gotcha 18: attaching to a CPU somebody else started and downloaded a
    program to is not owning it. stop() used to PowerOff(), so a 40-second run
    switched off the user's PLC."""
    driver = make_plcsim(fake_bus)
    await driver.start()
    await driver.rebuild("sorting", 1, sorting_table())
    await driver.stop()
    assert sim_instance.power_calls == []
