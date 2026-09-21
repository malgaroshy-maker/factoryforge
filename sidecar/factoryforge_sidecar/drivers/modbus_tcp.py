"""Modbus TCP server driver.

The simulator acts as a Modbus **slave/server**; the PLC (or OpenPLC, or a test
script) connects as master. This is the zero-friction path: it needs no Siemens
software at all, which makes it the right driver to develop and test against.

Address mapping, derived from `kind` (which is from the controller's POV):

    sim output, bit    -> coil              (master writes)
    sim input,  bit    -> discrete input    (master reads)
    sim output, int    -> holding register  (master writes)
    sim input,  int    -> input register    (master reads)
    sim output, float  -> 2 holding registers, IEEE-754 big-endian
    sim input,  float  -> 2 input registers, IEEE-754 big-endian

Addresses are assigned deterministically in sorted tag-id order, so the map is
reproducible across runs and a student can write it down once. Call
`address_table()` for a printable map.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

from ..modbus import DataStore, ModbusTcpServer
from ..modbus.server import DEFAULT_MAX_CONNECTIONS, DEFAULT_READ_TIMEOUT
from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Mapping:
    tag_id: str
    block: str          # "coils" | "discrete_inputs" | "holding_registers" | "input_registers"
    address: int
    width: int          # registers consumed; 1 for bits
    type: str           # "bit" | "int" | "float"


def _to_registers(type_: str, value: TagValue) -> list[int]:
    if type_ == "float":
        return list(struct.unpack(">HH", struct.pack(">f", float(value))))
    # Modbus registers are unsigned on the wire; two's complement for negatives.
    return [int(value) & 0xFFFF]


def _from_registers(type_: str, regs: list[int]) -> TagValue:
    if type_ == "float":
        return struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]
    value = regs[0] & 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


#: Addresses that reach only this machine. Binding anywhere else publishes a
#: writable, unauthenticated simulator to everyone who can route to the host.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@register("modbus-tcp")
class ModbusTcpDriver(Driver):
    def __init__(self, bus, host: str = "127.0.0.1", port: int = 502,
                 max_connections: int = DEFAULT_MAX_CONNECTIONS,
                 read_timeout: float = DEFAULT_READ_TIMEOUT, **config) -> None:
        # Loopback by default, and binding wider is something you have to ask
        # for. This driver used to default to "0.0.0.0" while the server class
        # it wraps defaulted to "127.0.0.1" -- and the driver's default is the
        # one that ships. Modbus has no authentication of any kind, so the
        # wider bind hands anyone on the subnet write access to the machine:
        # they can run the conveyor, fire the pusher and rewrite the counters,
        # on a classroom network, without so much as a username.
        super().__init__(bus, host=host, port=port, **config)
        self.store = DataStore()
        self.store.on_write = self._on_master_write
        self.server = ModbusTcpServer(self.store, host, port,
                                      max_connections=int(max_connections),
                                      read_timeout=float(read_timeout))
        if host not in _LOOPBACK:
            log.warning(
                "Modbus TCP will bind %s, which is reachable from outside this "
                "machine. Modbus has no authentication: anyone who can reach "
                "port %s can write every coil and register in the scene. Use "
                "-o host 127.0.0.1 unless a PLC on the network has to reach it.",
                host, port,
            )
        self._by_tag: dict[str, Mapping] = {}
        self._by_address: dict[tuple[str, int], Mapping] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def port(self) -> int:
        """The bound port, resolved if 0 was requested."""
        return self.server.actual_port

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.server.start()
        await self.bus.status(
            "info", "driver_connected",
            f"Modbus TCP server listening on {self.server.host}:{self.port}",
        )

    async def stop(self) -> None:
        await self.server.stop()

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        """Assign addresses deterministically and seed the datastore."""
        self._by_tag.clear()
        self._by_address.clear()
        next_addr = {"coils": 0, "discrete_inputs": 0,
                     "holding_registers": 0, "input_registers": 0}

        for tag in sorted(table, key=lambda t: t.id):
            if tag.type == "bit":
                block = "coils" if tag.kind == "output" else "discrete_inputs"
                width = 1
            else:
                block = "holding_registers" if tag.kind == "output" else "input_registers"
                width = 2 if tag.type == "float" else 1

            mapping = Mapping(tag.id, block, next_addr[block], width, tag.type)
            next_addr[block] += width
            self._by_tag[tag.id] = mapping
            for offset in range(width):
                self._by_address[(block, mapping.address + offset)] = mapping

        # Seed the datastore so a master polling before the first update reads
        # the scene's actual initial state rather than all-zeros.
        for tag in table:
            self._write_store(self._by_tag[tag.id], table.visible(tag.id))

        log.info("mapped %d tags for scene %r (epoch %d)", len(self._by_tag), scene, epoch)

    async def push(self, values: dict[str, TagValue]) -> None:
        """Sensor changes from the engine -> datastore, for the master to read."""
        for tag_id, value in values.items():
            mapping = self._by_tag.get(tag_id)
            if mapping is not None:
                self._write_store(mapping, value)

    async def bus_disconnected(self) -> None:
        """Modbus has no wire-level "bad quality" flag the way OPC UA does —
        a coil or register is just a value — so there is no honest way to mark
        individual points stale without a custom convention the master would
        also have to know about. The achievable half: say so loudly here,
        since nothing on the wire will. See FF-03."""
        log.warning(
            "tag bus disconnected — Modbus TCP at %s:%d is now serving values "
            "from before the drop; the master has no way to tell from the wire",
            self.server.host, self.port,
        )

    def _write_store(self, mapping: Mapping, value: TagValue) -> None:
        if mapping.type == "bit":
            block = (self.store.coils if mapping.block == "coils"
                     else self.store.discrete_inputs)
            block[mapping.address] = bool(value)
            return
        regs = _to_registers(mapping.type, value)
        block = (self.store.holding_registers if mapping.block == "holding_registers"
                 else self.store.input_registers)
        block[mapping.address:mapping.address + len(regs)] = regs

    def _on_master_write(self, block: str, address: int, values: list) -> None:
        """Called from the server's task when the master writes.

        Resolves affected tags and queues bus writes. Runs synchronously inside
        the server coroutine, so it must not await -- it schedules instead.
        """
        touched: dict[str, TagValue] = {}
        for offset in range(len(values)):
            mapping = self._by_address.get((block, address + offset))
            if mapping is None or mapping.tag_id in touched:
                continue
            if mapping.type == "bit":
                touched[mapping.tag_id] = bool(values[offset])
            else:
                start = mapping.address - address
                if start < 0 or start + mapping.width > len(values):
                    # A partial write across a float's two registers. Read the
                    # whole value back from the store, which the server has
                    # already updated, so both halves are consistent.
                    regs = self.store.holding_registers[
                        mapping.address:mapping.address + mapping.width]
                else:
                    regs = list(values[start:start + mapping.width])
                touched[mapping.tag_id] = _from_registers(mapping.type, regs)

        if touched and self._loop is not None:
            self._loop.create_task(self.bus.write_many(touched))

    def address_table(self) -> str:
        """A printable map, for pasting next to a student's PLC symbol table."""
        rows = ["  ADDRESS          TYPE   TAG",
                "  ---------------- ------ ----------------------------------"]
        for mapping in sorted(self._by_tag.values(), key=lambda m: (m.block, m.address)):
            label = {"coils": "0x", "discrete_inputs": "1x",
                     "holding_registers": "4x", "input_registers": "3x"}[mapping.block]
            rows.append(
                f"  {label}{mapping.address:<14} {mapping.type:<6} {mapping.tag_id}"
            )
        return "\n".join(rows)
