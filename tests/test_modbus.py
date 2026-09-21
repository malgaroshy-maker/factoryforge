"""Modbus TCP driver, exercised by a real pymodbus master.

This is the acceptance test for the milestone: a Modbus master (standing in for
OpenPLC or a PLC) writes a coil, the simulation reacts, and the master reads the
result back from a discrete input -- with no 3D and no Siemens software.
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from pymodbus.client import AsyncModbusTcpClient

from factoryforge_sidecar import drivers
from factoryforge_sidecar.modbus import DataStore, ModbusTcpServer, error_response, handle_pdu
from factoryforge_sidecar.modbus.server import ILLEGAL_ADDRESS, ILLEGAL_FUNCTION


# --- protocol unit tests (no sockets) ---

def test_read_coils_packs_bits_lsb_first():
    store = DataStore()
    store.coils[0:3] = [True, False, True]
    # FC1, address 0, count 3
    resp = handle_pdu(store, bytes([1]) + (0).to_bytes(2, "big") + (3).to_bytes(2, "big"))
    assert resp == bytes([1, 1, 0b101])


def test_write_single_coil_round_trips():
    store = DataStore()
    resp = handle_pdu(store, bytes([5]) + (7).to_bytes(2, "big") + (0xFF00).to_bytes(2, "big"))
    assert store.coils[7] is True
    assert resp[0] == 5


def test_write_multiple_coils():
    store = DataStore()
    # FC15, address 0, count 3, 1 byte, 0b011
    pdu = bytes([15]) + (0).to_bytes(2, "big") + (3).to_bytes(2, "big") + bytes([1, 0b011])
    handle_pdu(store, pdu)
    assert store.coils[0:3] == [True, True, False]


def test_out_of_range_read_is_an_exception():
    store = DataStore(size=10)
    with pytest.raises(Exception) as exc:
        handle_pdu(store, bytes([1]) + (5).to_bytes(2, "big") + (100).to_bytes(2, "big"))
    assert exc.value.code == ILLEGAL_ADDRESS


def test_unknown_function_code_is_rejected():
    with pytest.raises(Exception) as exc:
        handle_pdu(DataStore(), bytes([99, 0, 0, 0, 1]))
    assert exc.value.code == ILLEGAL_FUNCTION


def test_write_callback_reports_the_master_write():
    store = DataStore()
    seen = []
    store.on_write = lambda kind, addr, values: seen.append((kind, addr, values))
    handle_pdu(store, bytes([5]) + (2).to_bytes(2, "big") + (0xFF00).to_bytes(2, "big"))
    assert seen == [("coils", 2, [True])]


# --- driver integration ---

@pytest_asyncio.fixture
async def modbus(bus):
    """A Modbus TCP driver on an ephemeral port, mapped to the scene."""
    driver = drivers.create("modbus-tcp", bus, host="127.0.0.1", port=0)
    await driver.start()
    for _ in range(100):                      # wait for the describe to map tags
        if driver._by_tag:
            break
        await asyncio.sleep(0.05)
    assert driver._by_tag, "driver never received a describe"
    try:
        yield driver
    finally:
        await driver.stop()


@pytest_asyncio.fixture
async def master(modbus):
    client = AsyncModbusTcpClient("127.0.0.1", port=modbus.port)
    await client.connect()
    assert client.connected
    try:
        yield client
    finally:
        client.close()


def _addr(driver, tag_id: str) -> int:
    return driver._by_tag[tag_id].address


async def test_address_map_is_deterministic(modbus):
    """Sorted by tag id, so a student can write the map down once."""
    coils = sorted(
        (m.address, m.tag_id) for m in modbus._by_tag.values() if m.block == "coils"
    )
    assert [t for _, t in coils] == [
        "conveyor.rotate", "emitter.emit", "pusher.extend", "stack_light.green",
    ]


async def test_master_write_drives_the_simulation(engine, modbus, master):
    """The core round-trip: coil write -> tag bus -> engine."""
    await master.write_coil(_addr(modbus, "conveyor.rotate"), True)
    for _ in range(100):
        if engine.scene.tags.visible("conveyor.rotate"):
            break
        await asyncio.sleep(0.05)
    assert engine.scene.tags.visible("conveyor.rotate") is True


async def test_master_reads_a_sensor(engine, modbus, master):
    """The other direction: engine input -> tag bus -> discrete input."""
    from scene import SENSOR_LOW_POS, SHORT_HEIGHT, Box

    engine.scene.boxes.append(Box(height=SHORT_HEIGHT, position=SENSOR_LOW_POS))
    address = _addr(modbus, "sensor_low.detect")
    for _ in range(100):
        result = await master.read_discrete_inputs(address, count=1)
        if result.bits[0]:
            break
        await asyncio.sleep(0.05)
    assert result.bits[0] is True


async def test_int_tags_map_to_input_registers(engine, modbus, master):
    engine.scene.sorted_tall.extend([object()] * 3)
    address = _addr(modbus, "counter.tall")
    for _ in range(100):
        result = await master.read_input_registers(address, count=1)
        if result.registers[0] == 3:
            break
        await asyncio.sleep(0.05)
    assert result.registers[0] == 3


async def test_initial_state_is_seeded_before_any_update(modbus, master):
    """A master polling before the first delta must not read all-zeros."""
    result = await master.read_discrete_inputs(_addr(modbus, "pusher.retracted"), count=1)
    assert result.bits[0] is True


# --- the server under contact -------------------------------------------------
#
# Everything above talks to a well-behaved master. These talk to one that is
# not: a frame that stops halfway, a length field that cannot be Modbus, more
# connections than anyone needs, and a shutdown with a session still open.

async def _settle(check, timeout: float = 5.0, interval: float = 0.05):
    """Poll until *check* is truthy. `interval` stays above Windows' 15.6ms
    asyncio clock resolution -- below it, sleeps return immediately."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if check():
            return True
        await asyncio.sleep(interval)
    return False


async def _ended(reader, timeout: float = 5.0) -> bool:
    """True if the server ended the session: EOF, or a reset, which is what
    Windows often reports for a socket closed with bytes still unread."""
    try:
        return await asyncio.wait_for(reader.read(), timeout=timeout) == b""
    except (ConnectionResetError, asyncio.IncompleteReadError):
        return True


def _mbap(length: int, txn: int = 1, unit: int = 1) -> bytes:
    return struct.pack(">HHHB", txn, 0, length, unit)


@pytest_asyncio.fixture
async def server_factory():
    """Builds bare `ModbusTcpServer`s on ephemeral ports and stops them after."""
    made: list[ModbusTcpServer] = []

    async def make(**kwargs) -> ModbusTcpServer:
        server = ModbusTcpServer(DataStore(), "127.0.0.1", 0, **kwargs)
        await server.start()
        made.append(server)
        return server

    try:
        yield make
    finally:
        for server in made:
            await server.stop()


def test_an_error_reply_survives_a_pdu_with_no_function_code():
    """A zero-length PDU has no function code to echo, and `pdu[0] | 0x80`
    indexes off the end of it -- an IndexError inside the connection handler
    instead of the exception response the spec asks for."""
    assert error_response(b"", ILLEGAL_FUNCTION) == bytes([0x80, ILLEGAL_FUNCTION])
    assert error_response(bytes([3, 0, 0]), ILLEGAL_ADDRESS) == bytes([0x83, ILLEGAL_ADDRESS])


async def test_a_half_delivered_frame_does_not_pin_the_connection(server_factory):
    """A header promising a PDU that never arrives must not hold a coroutine
    and a connection slot for the life of the process."""
    server = await server_factory(read_timeout=0.3)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.actual_port)
    writer.write(_mbap(7))          # "a 6-byte PDU follows" -- then silence
    await writer.drain()
    try:
        assert await _ended(reader), "the server waited forever for the rest"
    finally:
        writer.close()


async def test_a_length_field_that_cannot_be_modbus_is_refused_at_once(server_factory):
    """The deadline is a backstop; an impossible length is knowable immediately.

    The read timeout here is far longer than the assertion window, so only the
    length check can be what closed the session.
    """
    server = await server_factory(read_timeout=30.0)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.actual_port)
    writer.write(_mbap(9999))
    await writer.drain()
    try:
        assert await _ended(reader, timeout=2.0), "an absurd frame length was waited on"
    finally:
        writer.close()


async def test_connections_are_capped(server_factory):
    server = await server_factory(max_connections=2)
    held = []
    try:
        for _ in range(2):
            held.append(await asyncio.open_connection("127.0.0.1", server.actual_port))
        assert await _settle(lambda: len(server._clients) == 2), "sessions never registered"

        reader, writer = await asyncio.open_connection("127.0.0.1", server.actual_port)
        held.append((reader, writer))
        assert await _ended(reader), "a third master was accepted over the cap"
    finally:
        for _, writer in held:
            writer.close()


async def test_stop_ends_sessions_that_are_already_established(server_factory):
    """Closing the listener alone leaves a master attached to a datastore
    nothing updates any more -- which on the wire looks exactly like a running
    simulation that has stopped moving."""
    server = await server_factory()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.actual_port)
    writer.write(_mbap(6) + bytes([1]) + (0).to_bytes(2, "big") + (1).to_bytes(2, "big"))
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(10), timeout=5)

    try:
        await server.stop()
        assert await _ended(reader), "stop() left an established session open"
    finally:
        writer.close()


def test_the_driver_binds_loopback_unless_told_otherwise():
    """The driver's default is what ships, and it used to be 0.0.0.0 while the
    server class it wraps defaulted to loopback. Modbus has no authentication,
    so the wider bind hands write access to the whole subnet."""
    driver = drivers.create("modbus-tcp", MagicMock(), port=0)
    assert driver.server.host == "127.0.0.1"

    explicit = drivers.create("modbus-tcp", MagicMock(), host="0.0.0.0", port=0)
    assert explicit.server.host == "0.0.0.0", "a wider bind must still be possible"
