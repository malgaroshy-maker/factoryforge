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

Addresses are assigned in sorted tag-id order on the first description, so a
fresh run of a given scene always produces the same map and a student can write
it down once. After that they are *stable*: editing the scene never moves an
address that already exists, new tags go above everything already handed out,
and a deleted tag's address is reserved rather than reused. The PLC's copy of
the map is a hand-written list of numbers with no tag names on the wire, so
there is nothing on the Modbus side that could notice a shift. Changes are
announced on the tag bus instead. Call `address_table()` for a printable map.
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


#: The conventional Modbus prefixes, as a student would write them next to a
#: PLC symbol table.
_LABELS = {"coils": "0x", "discrete_inputs": "1x",
           "holding_registers": "4x", "input_registers": "3x"}

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
        # Every address this driver has ever handed out, and the high-water
        # mark per block. Both outlive any single rebuild: see _assign.
        self._known: dict[str, Mapping] = {}
        self._next = {"coils": 0, "discrete_inputs": 0,
                      "holding_registers": 0, "input_registers": 0}
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

    @staticmethod
    def _placement(tag) -> tuple[str, int]:
        """Which block a tag belongs in, and how many addresses it takes."""
        if tag.type == "bit":
            return ("coils" if tag.kind == "output" else "discrete_inputs"), 1
        block = "holding_registers" if tag.kind == "output" else "input_registers"
        return block, (2 if tag.type == "float" else 1)

    def _assign(self, tag) -> Mapping | None:
        """The address for *tag* — the one it already has, wherever possible.

        Addresses used to be rebuilt from zero in sorted-tag-id order on every
        describe, and the engine republishes the description on every scene
        edit. So adding a conveyor called `belt_a` to a scene that already had
        `pusher_1` slid every address after it by one, while the PLC's
        configuration -- a hand-written list of numbers -- did not move at all.
        The symptom is a program that was working and now drives the wrong
        device, with nothing on screen to explain it, and Modbus has no tag
        names on the wire to notice the mismatch with.

        So a tag keeps its address for the life of the process, and a new one
        goes above everything ever handed out rather than into a gap. Reusing a
        deleted tag's slot would be the same bug wearing a different hat: the
        PLC still has that number written down, and it would now reach a
        different device.
        """
        block, width = self._placement(tag)
        existing = self._known.get(tag.id)
        if existing is not None and existing.block == block and existing.width == width:
            return existing

        address = self._next[block]
        if address + width > self.store.size:
            log.error(
                "%s cannot be mapped: %s is full (%d addresses). Restart the "
                "sidecar to compact the map — addresses are deliberately never "
                "reused while it runs.", tag.id, block, self.store.size)
            return None
        mapping = Mapping(tag.id, block, address, width, tag.type)
        self._next[block] = address + width
        self._known[tag.id] = mapping
        return mapping

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        """Map the tag set onto stable addresses and seed the datastore."""
        previous = dict(self._by_tag)
        self._by_tag = {}
        self._by_address = {}

        for tag in sorted(table, key=lambda t: t.id):
            mapping = self._assign(tag)
            if mapping is None:
                continue
            self._by_tag[tag.id] = mapping
            for offset in range(mapping.width):
                self._by_address[(mapping.block, mapping.address + offset)] = mapping

        # Seed the datastore so a master polling before the first update reads
        # the scene's actual initial state rather than all-zeros.
        for tag in table:
            mapping = self._by_tag.get(tag.id)
            if mapping is not None:
                self._write_store(mapping, table.visible(tag.id))

        log.info("mapped %d tags for scene %r (epoch %d)", len(self._by_tag), scene, epoch)
        await self._announce_changes(previous)

    async def _announce_changes(self, previous: dict[str, Mapping]) -> None:
        """Say what moved, because nothing on the Modbus wire will.

        The first map is not news. A later one is: somebody edited the scene
        while a PLC was reading it, and the addresses they wrote down by hand
        may no longer cover the whole scene.
        """
        if not previous:
            return
        added = [m for tag_id, m in self._by_tag.items() if tag_id not in previous]
        removed = sorted(set(previous) - set(self._by_tag))
        if not added and not removed:
            return

        parts = ["Modbus address map updated; every existing address is unchanged."]
        if added:
            parts.append("new: " + ", ".join(
                f"{m.tag_id}={_LABELS[m.block]}{m.address}"
                for m in sorted(added, key=lambda m: (m.block, m.address))))
        if removed:
            parts.append("gone: " + ", ".join(
                f"{tag_id} (was {_LABELS[previous[tag_id].block]}"
                f"{previous[tag_id].address}, reserved)" for tag_id in removed))
        message = " ".join(parts)
        log.info("%s", message)
        try:
            await self.bus.status("info", "modbus_map_changed", message)
        except Exception:
            log.debug("could not report the map change upstream", exc_info=True)

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
            rows.append(
                f"  {_LABELS[mapping.block]}{mapping.address:<14} "
                f"{mapping.type:<6} {mapping.tag_id}"
            )
        return "\n".join(rows)
