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
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from factoryforge_sidecar import drivers
from factoryforge_sidecar.drivers import s7_snap7
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
