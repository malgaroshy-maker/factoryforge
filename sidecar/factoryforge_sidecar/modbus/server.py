"""A minimal asyncio Modbus TCP server.

Why not pymodbus's server? As of pymodbus 3.14 the `ModbusDeviceContext` /
`ModbusSequentialDataBlock` datastore is deprecated and slated for removal in
v4, and its replacement (`SimData`/`SimDevice`) stores coils as packed 16-bit
registers, which makes per-bit read/write mapping awkward and couples us to
internals that are visibly still in flux. Implementing the eight function codes
a simulator needs is less code than that adapter layer, has no moving
dependency, and gives exact control over the tag/address mapping.

pymodbus is still used as the *test master* -- that half of its API is stable.

Supports FC 1, 2, 3, 4, 5, 6, 15, 16.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Callable

log = logging.getLogger(__name__)

# Exception codes
ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02
ILLEGAL_VALUE = 0x03
SERVER_FAILURE = 0x04

MAX_BITS = 2000
MAX_REGS = 125

#: A Modbus TCP ADU is at most 260 bytes: a 7-byte MBAP header and a 253-byte
#: PDU. The MBAP `length` field counts the unit id plus the PDU, so it is
#: between 2 (unit id + function code) and 254. Anything outside that is not a
#: short read to wait on, it is a frame that will never arrive.
MIN_MBAP_LENGTH = 2
MAX_MBAP_LENGTH = 254

#: How long a *partially delivered* frame may stay partial. There is no deadline
#: on the gap between frames -- a master that polls once a minute is normal --
#: but once the first byte of a header has landed, the rest of that frame is
#: expected promptly. Without this, one peer that sends a byte and stops pins a
#: coroutine and a connection slot for as long as the process runs.
DEFAULT_READ_TIMEOUT = 10.0

#: Nothing legitimate needs many masters at once, and the cost of a connection
#: is a coroutine that can be made to wait. Cap it so a stranger cannot make the
#: sidecar hold an unbounded number of them.
DEFAULT_MAX_CONNECTIONS = 8


class ModbusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"modbus exception {code}")
        self.code = code


class DataStore:
    """Coils, discrete inputs, holding and input registers.

    Naming is from the *master's* point of view, per the Modbus spec:
      coils            -- read/write bits   (master writes these)
      discrete_inputs  -- read-only bits    (master reads these)
      holding_registers-- read/write words  (master writes these)
      input_registers  -- read-only words   (master reads these)
    """

    def __init__(self, size: int = 1024) -> None:
        self.size = size
        self.coils = [False] * size
        self.discrete_inputs = [False] * size
        self.holding_registers = [0] * size
        self.input_registers = [0] * size
        #: Called with (kind, address, values) after a master write.
        #: kind is "coils" or "holding_registers".
        self.on_write: Callable[[str, int, list], None] | None = None

    def _check(self, block: list, address: int, count: int, limit: int) -> None:
        if count < 1 or count > limit:
            raise ModbusError(ILLEGAL_VALUE)
        if address < 0 or address + count > len(block):
            raise ModbusError(ILLEGAL_ADDRESS)

    def read_bits(self, block: list[bool], address: int, count: int) -> list[bool]:
        self._check(block, address, count, MAX_BITS)
        return block[address:address + count]

    def read_regs(self, block: list[int], address: int, count: int) -> list[int]:
        self._check(block, address, count, MAX_REGS)
        return block[address:address + count]

    def write_coils(self, address: int, values: list[bool]) -> None:
        self._check(self.coils, address, len(values), MAX_BITS)
        self.coils[address:address + len(values)] = values
        if self.on_write:
            self.on_write("coils", address, values)

    def write_registers(self, address: int, values: list[int]) -> None:
        self._check(self.holding_registers, address, len(values), MAX_REGS)
        self.holding_registers[address:address + len(values)] = values
        if self.on_write:
            self.on_write("holding_registers", address, values)


def _pack_bits(bits: list[bool]) -> bytes:
    """Pack bits LSB-first into bytes, as the Modbus spec requires."""
    out = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def _unpack_bits(data: bytes, count: int) -> list[bool]:
    return [bool(data[i // 8] & (1 << (i % 8))) for i in range(count)]


def handle_pdu(store: DataStore, pdu: bytes) -> bytes:
    """Process one request PDU and return the response PDU."""
    if not pdu:
        raise ModbusError(ILLEGAL_FUNCTION)
    fc = pdu[0]
    body = pdu[1:]

    try:
        if fc in (1, 2, 3, 4):
            address, count = struct.unpack(">HH", body[:4])
            if fc in (1, 2):
                block = store.coils if fc == 1 else store.discrete_inputs
                data = _pack_bits(store.read_bits(block, address, count))
                return bytes([fc, len(data)]) + data
            block = store.holding_registers if fc == 3 else store.input_registers
            regs = store.read_regs(block, address, count)
            return bytes([fc, len(regs) * 2]) + struct.pack(f">{len(regs)}H", *regs)

        if fc == 5:
            address, raw = struct.unpack(">HH", body[:4])
            if raw not in (0x0000, 0xFF00):
                raise ModbusError(ILLEGAL_VALUE)
            store.write_coils(address, [raw == 0xFF00])
            return bytes([fc]) + struct.pack(">HH", address, raw)

        if fc == 6:
            address, value = struct.unpack(">HH", body[:4])
            store.write_registers(address, [value])
            return bytes([fc]) + struct.pack(">HH", address, value)

        if fc == 15:
            address, count, nbytes = struct.unpack(">HHB", body[:5])
            data = body[5:5 + nbytes]
            if len(data) != nbytes or nbytes != (count + 7) // 8:
                raise ModbusError(ILLEGAL_VALUE)
            store.write_coils(address, _unpack_bits(data, count))
            return bytes([fc]) + struct.pack(">HH", address, count)

        if fc == 16:
            address, count, nbytes = struct.unpack(">HHB", body[:5])
            if nbytes != count * 2 or len(body) < 5 + nbytes:
                raise ModbusError(ILLEGAL_VALUE)
            values = list(struct.unpack(f">{count}H", body[5:5 + nbytes]))
            store.write_registers(address, values)
            return bytes([fc]) + struct.pack(">HH", address, count)

    except struct.error as exc:
        raise ModbusError(ILLEGAL_VALUE) from exc

    raise ModbusError(ILLEGAL_FUNCTION)


def error_response(pdu: bytes, code: int) -> bytes:
    """The exception response for *pdu*, which may be empty.

    Written out rather than inlined because the obvious spelling, `pdu[0] |
    0x80`, indexes off the end of a zero-length PDU: a malformed frame then
    raises IndexError inside the connection handler instead of producing the
    reply the spec asks for, and takes the session down with it. A frame with no
    function code has no function code to echo, so echo 0.
    """
    fc = pdu[0] if pdu else 0
    return bytes([fc | 0x80, code])


class ModbusTcpServer:
    def __init__(self, store: DataStore, host: str = "127.0.0.1", port: int = 502,
                 max_connections: int = DEFAULT_MAX_CONNECTIONS,
                 read_timeout: float = DEFAULT_READ_TIMEOUT) -> None:
        self.store = store
        self.host = host
        self.port = port
        self.max_connections = int(max_connections)
        self.read_timeout = float(read_timeout)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    @property
    def actual_port(self) -> int:
        """The bound port. Differs from `port` when 0 was requested."""
        if not self._server:
            raise RuntimeError("server is not running")
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        log.info("Modbus TCP listening on %s:%d", self.host, self.actual_port)

    async def stop(self) -> None:
        """Stop listening *and* end the sessions already established.

        Closing only the listener leaves every connected master attached to a
        datastore nothing updates any more, which is indistinguishable on the
        wire from a running simulation that has stopped moving.
        """
        # Order matters, and not only for tidiness: since Python 3.12,
        # `Server.wait_closed()` also waits for every connection handler to
        # finish. A handler parked in readexactly() never finishes on its own,
        # so closing the listener *first* and awaiting it deadlocks stop() for
        # as long as one master stays connected. Close the sessions, then wait.
        clients, self._clients = self._clients, set()
        for writer in clients:
            writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in clients:
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:
                log.debug("error closing a master session", exc_info=True)

    async def _client(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if len(self._clients) >= self.max_connections:
            log.warning("refusing master from %s: %d connections already open",
                        peer, self.max_connections)
            writer.close()
            return
        self._clients.add(writer)
        log.info("master connected from %s", peer)
        try:
            while True:
                # No deadline on the first byte: the gap between one master's
                # polls is its own business. The deadline starts once a frame
                # has, so the rest of *this* frame has to turn up.
                first = await reader.readexactly(1)
                rest = await asyncio.wait_for(
                    reader.readexactly(6), self.read_timeout)
                txn, proto_id, length, unit = struct.unpack(">HHHB", first + rest)
                if proto_id != 0:
                    log.warning("bad protocol id %d from %s", proto_id, peer)
                    return
                if not MIN_MBAP_LENGTH <= length <= MAX_MBAP_LENGTH:
                    log.warning("frame length %d from %s is out of range; closing",
                                length, peer)
                    return
                pdu = await asyncio.wait_for(
                    reader.readexactly(length - 1), self.read_timeout)

                try:
                    response = handle_pdu(self.store, pdu)
                except ModbusError as exc:
                    response = error_response(pdu, exc.code)
                except Exception:
                    log.exception("handler failed")
                    response = error_response(pdu, SERVER_FAILURE)

                writer.write(
                    struct.pack(">HHHB", txn, 0, len(response) + 1, unit) + response
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except asyncio.TimeoutError:
            log.warning("master %s left a frame half-delivered for %gs; closing",
                        peer, self.read_timeout)
        finally:
            self._clients.discard(writer)
            log.info("master disconnected from %s", peer)
            writer.close()
