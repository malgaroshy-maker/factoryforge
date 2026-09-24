"""OPC UA **client** driver: the sidecar connects to a PLC's OPC UA server.

This is the setup most Siemens users already have -- an S7-1500 with its OPC UA
server enabled, and something connecting to it. Reference CPU is S7-1500,
because PLCSIM Advanced simulates that family and not the S7-1200.

Direction, which is the part that trips people up:

    sim `output` tag  (PLC writes it) -> we **subscribe** to the PLC node
    sim `input`  tag  (PLC reads it)  -> we **write** the PLC node

Mapping tags to nodes cannot be derived automatically: a PLC's address space
looks like `ns=3;s="DB1"."Motor"` and has no relationship to our tag ids. So a
mapping is required, given either inline or as a JSON file. `auto_map` is
offered as a convenience for quick starts, matching node browse names against
tag ids.

Licensing note: an S7-1500's OPC UA *server* needs a paid SIMATIC runtime
licence; unlicensed it runs a 100-variable trial. The client side -- this code
-- is always free.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from asyncua import Client, ua

from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5.0

#: How often to re-attempt input writes the PLC did not accept. See
#: `_reconcile_loop` for why dropping one is not survivable.
INPUT_RETRY_INTERVAL = 1.0

#: AGENTS.md gotcha 8: "asyncua's default 4 s connect timeout is too short for
#: a real S7. Use timeout=10." Stated as a number here rather than left to
#: asyncua's default, so that changing it is a decision somebody takes rather
#: than a dependency's default quietly applying.
CONNECT_TIMEOUT = 10.0

#: Consecutive failed polls before saying so on the bus. One is a blip; a run
#: of them is something whoever is watching the scene should be told about.
POLL_FAILURES_BEFORE_REPORTING = 20

#: What a tag is written as when the node's DataType could not be read, or is
#: not a scalar built-in type: the variant every write used before IP-28. An
#: `int` here is an Int32, which only a `DInt` accepts.
_DEFAULT_VARIANT = {
    "bit": ua.VariantType.Boolean,
    "int": ua.VariantType.Int32,
    "float": ua.VariantType.Float,
}

#: The integer DataTypes an `int` tag can be written into, with what each can
#: hold. The S7 names, for reading an error against a TIA project: SInt,
#: USInt/Byte, Int, UInt/Word, DInt, UDInt/DWord, LInt, ULInt/LWord.
_INT_RANGES = {
    ua.VariantType.SByte: (-(2 ** 7), 2 ** 7 - 1),
    ua.VariantType.Byte: (0, 2 ** 8 - 1),
    ua.VariantType.Int16: (-(2 ** 15), 2 ** 15 - 1),
    ua.VariantType.UInt16: (0, 2 ** 16 - 1),
    ua.VariantType.Int32: (-(2 ** 31), 2 ** 31 - 1),
    ua.VariantType.UInt32: (0, 2 ** 32 - 1),
    ua.VariantType.Int64: (-(2 ** 63), 2 ** 63 - 1),
    ua.VariantType.UInt64: (0, 2 ** 64 - 1),
}

#: Real and LReal.
_FLOATS = {ua.VariantType.Float, ua.VariantType.Double}


class Unwritable(Exception):
    """A value this node cannot hold, known before anything is sent.

    Not a transient failure, so never retried: a retry would send the same
    value to the same node and be refused the same way, once a second, for
    ever. *code* is the status code it is reported under.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def encode_for_node(tag_type: str, value, node_type: ua.VariantType | None) -> ua.Variant:
    """The variant that writes *value* into a node of DataType *node_type*.

    A real S7 refuses a write whose variant type is not the variable's own
    (BadTypeMismatch), so an Int32 never lands in an `Int` -- the 16-bit type
    of a real `%IW` analog channel (IP-28). Written as the node's own type, a
    raw analog count lands in an `Int`, a `DInt` counter in a `DInt`, and a
    float in a `Real` or an `LReal`. What cannot be represented is refused here,
    with a message that names both sides, rather than truncated: 40000 is not
    an `Int`, and wrapping it to -25536 would hand the program a lie.

    *node_type* None means the DataType is unknown, and the tag is written as it
    always was.
    """
    if node_type is None:
        return ua.Variant(value, _DEFAULT_VARIANT[tag_type])

    if tag_type == "bit":
        if node_type is ua.VariantType.Boolean:
            return ua.Variant(bool(value), node_type)
    elif tag_type == "int":
        if node_type in _INT_RANGES:
            low, high = _INT_RANGES[node_type]
            count = int(value)
            if not low <= count <= high:
                raise Unwritable(
                    "value_out_of_range",
                    f"{count} does not fit the node's DataType {node_type.name} "
                    f"({low}..{high}); not written. A raw analog count always fits "
                    f"an Int; a larger number needs a DInt on the PLC side")
            return ua.Variant(count, node_type)
        if node_type in _FLOATS:
            return ua.Variant(float(value), node_type)
    elif tag_type == "float":
        if node_type in _FLOATS:
            return ua.Variant(float(value), node_type)
        if node_type in _INT_RANGES:
            raise Unwritable(
                "node_type_mismatch",
                f"a float tag cannot be written into a {node_type.name} node without "
                f"rounding it; not written. Switch the part's signal to raw counts, "
                f"or declare the PLC variable Real")

    raise Unwritable(
        "node_type_mismatch",
        f"a {tag_type} tag cannot be written into a {node_type.name} node; not written")


class _SubHandler:
    """Receives data changes for PLC-written (sim output) nodes."""

    def __init__(self, driver: "OpcUaClientDriver") -> None:
        self._driver = driver

    def datachange_notification(self, node, val, data) -> None:
        tag_id = self._driver._by_node.get(node.nodeid.to_string())
        if tag_id is None:
            return
        # Called from asyncua's task; schedule rather than await.
        self._driver._queue_write(tag_id, val)


@register("opcua-client")
class OpcUaClientDriver(Driver):
    def __init__(self, bus, url: str = "opc.tcp://127.0.0.1:4840/",
                 mapping: dict[str, str] | None = None,
                 mapping_file: str | None = None,
                 auto_map: bool = False,
                 mode: str = "poll",
                 poll_interval: float = 0.05,
                 publish_interval: int = 50,
                 input_retry_interval: float = INPUT_RETRY_INTERVAL,
                 timeout: float = CONNECT_TIMEOUT,
                 **config) -> None:
        super().__init__(bus, url=url, **config)
        self.url = url
        self.auto_map = auto_map
        # "poll" reads in a tight loop; "subscribe" uses an OPC UA subscription.
        #
        # Polling is the default because a real S7-1500 silently revises a
        # requested 50ms publishing interval to 1000ms, and with the default
        # monitored-item queue size any signal that rises and falls inside that
        # second is dropped. A 500ms pusher pulse vanished this way. Batched
        # reads against the same CPU sustain ~1100/s, so a 50ms poll is roughly
        # 20x more responsive than the subscription the server will actually
        # give you. Larger queuesize / faster sampling_interval were tried and
        # made it worse -- the S7 rejects them and then reports nothing at all.
        if mode not in ("poll", "subscribe"):
            raise ValueError(f"mode must be 'poll' or 'subscribe', not {mode!r}")
        self.mode = mode
        self.poll_interval = poll_interval
        self.publish_interval = publish_interval
        self.input_retry_interval = float(input_retry_interval)
        # Each request to the server, the connect handshake included, must be
        # answered inside this. A real S7-1500 does not always manage asyncua's
        # 4s default, which is how a CPU that was simply busy came to look like
        # a CPU that was not there. `-o timeout 20` for an unusually slow one.
        self.timeout = float(timeout)

        self.mapping: dict[str, str] = dict(mapping or {})
        if mapping_file:
            loaded = json.loads(Path(mapping_file).read_text())
            # JSON has no comments, so mapping files use leading-underscore keys
            # for notes. Skip them rather than treating them as tag ids.
            self.mapping.update(
                {k: v for k, v in loaded.items() if not k.startswith("_")})

        self.client: Client | None = None
        self.connected = asyncio.Event()
        self._by_node: dict[str, str] = {}      # nodeid string -> tag_id
        self._nodes: dict[str, object] = {}     # tag_id -> asyncua Node
        #: tag_id -> the DataType of the node it writes, None where unknown.
        #: Only for the tags we write (inputs); read at bind, swapped in with
        #: the node map.
        self._node_types: dict[str, ua.VariantType | None] = {}
        #: Tags whose value the node refused before sending, by status code,
        #: so a sensor sitting at an unrepresentable value is reported once
        #: rather than on every change.
        self._refused: dict[str, str] = {}
        self._subscription = None
        self._runner: asyncio.Task | None = None
        self._poller: asyncio.Task | None = None
        self._poll_nodes: list[tuple[str, object]] = []
        self._last_read: dict[str, TagValue] = {}
        self._stopping = False
        self._table: TagTable | None = None
        #: Input writes the PLC has not accepted, kept until it does.
        self._unacked: dict[str, TagValue] = {}
        self._reconciler: asyncio.Task | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        """Begin connecting. Returns immediately.

        Connecting must not block: if the PLC is off, the simulation should
        still run and the UI should say why, rather than the whole sidecar
        hanging on a socket.
        """
        self._stopping = False
        await self.link(False, f"connecting to OPC UA server {self.url}")
        self._runner = asyncio.create_task(self._connect_loop())
        self._reconciler = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._runner, self._reconciler):
            if task is not None:
                task.cancel()
        self._runner = self._reconciler = None
        await self._disconnect()
        await self.link(False, f"stopped; disconnected from {self.url}")

    async def _connect_loop(self) -> None:
        while not self._stopping:
            try:
                await self._connect()
                # Hold the connection open until it drops.
                while not self._stopping and self.client is not None:
                    await asyncio.sleep(1.0)
                    await self.client.check_connection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._report("warn", "plc_disconnected",
                                   f"OPC UA connection to {self.url} lost: {exc}")
                await self._disconnect()
                await self.link(False, f"not connected to {self.url}: {exc} -- "
                                       f"retrying in {RECONNECT_DELAY:g}s")
                if self._stopping:
                    return
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect(self) -> None:
        log.info("connecting to %s (timeout %gs)", self.url, self.timeout)
        self.client = Client(url=self.url, timeout=self.timeout)
        await self.client.connect()
        self.connected.set()
        await self._report("info", "plc_connected", f"connected to OPC UA server {self.url}")
        if self._table is not None:
            await self._bind(self._table)

    async def _disconnect(self) -> None:
        self.connected.clear()
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
        await self._drop_subscription()
        self._nodes.clear()
        self._by_node.clear()
        self._node_types.clear()
        self._refused.clear()
        self._last_read.clear()
        # Not carried across: the next _bind() rewrites every input from the
        # table, which is a better source of truth than a value that failed
        # against nodes this session no longer has.
        self._unacked.clear()
        client, self.client = self.client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                log.debug("error while disconnecting", exc_info=True)

    # --- mapping ---

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        self._table = table
        if self.client is not None and self.connected.is_set():
            await self._bind(table)

    async def _bind(self, table: TagTable) -> None:
        """Resolve nodes and (re)create the subscription for this tag set.

        Stop the old data flow, build the new map in full, then swap it in and
        start the new flow. The order is the point of the method.
        """
        assert self.client is not None

        # Stop first. An old poller left running through node resolution would
        # otherwise deliver a batch read under the old tag set into a driver
        # that has already adopted the new one. Cancelling here also means an
        # in-flight read_values is cancelled at its await rather than landing
        # afterwards.
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
        # And the subscription this bind replaces, which nothing used to
        # delete. Every scene edit republishes the description, so a scene
        # edited ten times left ten live subscriptions on the server: nine of
        # them still delivering into a handler whose node->tag map had moved
        # on. Against a real S7 they are also a resource the CPU has very
        # little of -- see gotcha 7, where one extra client session was enough
        # to destabilise it.
        await self._drop_subscription()
        self._last_read.clear()

        mapping = dict(self.mapping)
        if self.auto_map:
            mapping.update(await self._browse_for_tags(table, skip=set(mapping)))

        # Built locally, swapped in below. This used to clear self._nodes and
        # then refill it one network round trip at a time, which left the map
        # visibly half-built for the whole of a rebuild -- and a half-built map
        # is indistinguishable from a finished one. "Not in _nodes" has two
        # meanings, "this tag is genuinely unmapped" and "binding has not got
        # to it yet", and only the first is actionable. The reconciler read the
        # second as the first and discarded a write it was holding precisely so
        # it would not be lost, every time somebody edited the scene -- and the
        # editor republishes the tag set on every placement, deletion and
        # rename. Keeping the old map intact until the new one is complete
        # means the question is never asked during the window.
        nodes: dict[str, object] = {}
        by_node: dict[str, str] = {}

        missing = []
        for tag in table:
            node_id = mapping.get(tag.id)
            if node_id is None:
                missing.append(tag.id)
                continue
            try:
                node = self.client.get_node(node_id)
                await node.read_browse_name()      # fail fast on a bad id
            except Exception as exc:
                await self._report("warn", "bad_node",
                                   f"{tag.id} -> {node_id} could not be read: {exc}")
                continue
            nodes[tag.id] = node
            by_node[node.nodeid.to_string()] = tag.id

        # What each node we write actually is, so a count lands in an `Int`
        # as an Int16 (IP-28). Read before the swap, so the types always
        # belong to the map they are swapped in with.
        written = [t.id for t in table.by_kind("input") if t.id in nodes]
        node_types = await self._read_node_types(written, nodes)

        # No await between these: the swap is atomic to everything else on
        # the loop, so no reader ever sees one map with the other's contents.
        self._nodes = nodes
        self._by_node = by_node
        self._node_types = node_types
        self._refused.clear()

        if missing:
            await self._report("warn", "unmapped_tags",
                               f"no OPC UA node mapped for: {', '.join(sorted(missing))}")

        # Watch the tags the PLC writes; we read those.
        plc_written = [(t.id, self._nodes[t.id]) for t in table.by_kind("output")
                       if t.id in self._nodes]

        if plc_written:
            if self.mode == "subscribe":
                self._subscription = await self.client.create_subscription(
                    self.publish_interval, _SubHandler(self))
                await self._subscription.subscribe_data_change(
                    [node for _, node in plc_written])
            else:
                self._poll_nodes = plc_written
                self._poller = asyncio.create_task(self._poll_loop())

        # Push current sensor values so the PLC starts from the real state.
        for tag in table.by_kind("input"):
            if tag.id in self._nodes:
                await self._write_node(tag.id, table.visible(tag.id))

        log.info("bound %d/%d tags on %s", len(self._nodes), len(table), self.url)
        # Only now, and not at connect: until the map is in, push() has no
        # node to write a sensor edge to, so a Start press in that gap would
        # never reach the PLC -- which is the gap IP-30 is about.
        await self.link(True, f"connected to {self.url}; "
                              f"{len(self._nodes)} of {len(table)} tags bound")

    async def _read_node_types(self, tag_ids: list[str],
                               nodes: dict[str, object]) -> dict[str, ua.VariantType | None]:
        """Each node's DataType as a variant type, one round trip for all.

        A built-in scalar type (ns=0, i=1..25) maps straight onto its variant
        type: the numbering is the same by design of the standard. Anything
        else -- an enumeration, a subtype -- is asked of the server, which walks
        the type hierarchy. What cannot be resolved is None, and that tag is
        written as it was before IP-28 rather than not at all.
        """
        types: dict[str, ua.VariantType | None] = {t: None for t in tag_ids}
        if not tag_ids:
            return types
        assert self.client is not None
        try:
            values = await self.client.read_attributes(
                [nodes[t] for t in tag_ids], ua.AttributeIds.DataType)
        except Exception as exc:
            log.warning("could not read the DataType of the nodes we write (%s); "
                        "writing each tag as its default type", exc)
            return types

        for tag_id, value in zip(tag_ids, values):
            data_type = getattr(getattr(value, "Value", None), "Value", None)
            if not isinstance(data_type, ua.NodeId):
                continue
            if (data_type.NamespaceIndex == 0 and isinstance(data_type.Identifier, int)
                    and 1 <= data_type.Identifier <= 25):
                types[tag_id] = ua.VariantType(data_type.Identifier)
                continue
            try:
                types[tag_id] = await nodes[tag_id].read_data_type_as_variant_type()
            except Exception:
                log.debug("%s: DataType %s is not a built-in type", tag_id, data_type)
        return types

    async def _drop_subscription(self) -> None:
        """Delete the current subscription, if there is one, and forget it."""
        subscription, self._subscription = self._subscription, None
        if subscription is None:
            return
        try:
            await subscription.delete()
        except Exception:
            # A server that has already gone will refuse; the point was to ask.
            log.debug("could not delete the previous subscription", exc_info=True)

    async def _browse_for_tags(self, table: TagTable, skip: set[str]) -> dict[str, str]:
        """Best-effort: match node browse names against tag ids.

        A convenience for getting started, not a substitute for an explicit
        mapping. Only matches exact browse names.
        """
        assert self.client is not None
        wanted = {t.id for t in table} - skip
        found: dict[str, str] = {}
        try:
            objects = self.client.nodes.objects
            for node in await objects.get_children():
                name = (await node.read_browse_name()).Name
                if name in wanted:
                    found[name] = node.nodeid.to_string()
        except Exception as exc:
            log.warning("auto-map browse failed: %s", exc)
        return found

    # --- data flow ---

    async def _poll_loop(self) -> None:
        """Batch-read the PLC-written tags and forward changes to the bus.

        One `read_values` call covers every tag, so this is a single round trip
        per interval regardless of tag count -- not one per tag.
        """
        ids = [tag_id for tag_id, _ in self._poll_nodes]
        nodes = [node for _, node in self._poll_nodes]
        failures = 0
        while not self._stopping:
            await asyncio.sleep(self.poll_interval)
            client = self.client
            if client is None:
                return
            try:
                values = await client.read_values(nodes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not stand down. This used to `return`, which ended polling
                # for the rest of the run. The reasoning was that the connect
                # loop owns reconnection -- and it does, but all it ever checks
                # is whether the *session* is alive, and a session that has
                # survived one failed read looks perfectly healthy to it. So
                # one timeout against a busy CPU took the driver silent, on a
                # connection still reporting good, with a scene that went on
                # running and a PLC that went on being ignored.
                failures += 1
                if failures == 1:
                    log.debug("poll read failed: %s", exc)
                elif failures % POLL_FAILURES_BEFORE_REPORTING == 0:
                    # A poller retrying forever in silence is its own kind of
                    # lie. Say so, periodically, without flooding.
                    await self._report(
                        "warn", "plc_read_failed",
                        f"{failures} consecutive failed reads from {self.url}: {exc}")
                continue

            if failures:
                log.info("polling %s recovered after %d failed read(s)",
                         self.url, failures)
                failures = 0

            changed: dict[str, TagValue] = {}
            for tag_id, raw in zip(ids, values):
                if self._last_read.get(tag_id, object()) == raw:
                    continue
                self._last_read[tag_id] = raw
                coerced = self._coerce(tag_id, raw)
                if coerced is not None:
                    changed[tag_id] = coerced
            if changed:
                await self.bus.write_many(changed)

    def _coerce(self, tag_id: str, value) -> TagValue | None:
        tag = self._table.get(tag_id) if self._table else None
        if tag is None:
            return None
        try:
            return tag.coerce(value)
        except Exception:
            log.warning("PLC sent %r for %s, which is not a %s",
                        value, tag_id, tag.type)
            return None

    async def push(self, values: dict[str, TagValue]) -> None:
        """Sensor changes from the engine -> write into the PLC."""
        if not self.connected.is_set():
            return
        for tag_id, value in values.items():
            if tag_id in self._nodes:
                await self._write_node(tag_id, value)

    async def _write_node(self, tag_id: str, value: TagValue) -> bool:
        node = self._nodes.get(tag_id)
        if node is None or self._table is None:
            return False
        tag = self._table.get(tag_id)
        if tag is None:
            return False
        try:
            variant = encode_for_node(tag.type, value, self._node_types.get(tag_id))
        except Unwritable as exc:
            # Not held: see Unwritable. And an older value still held for
            # retry is dropped too, or the reconciler would land a stale
            # reading after the PLC had been told the current one cannot be
            # written.
            self._unacked.pop(tag_id, None)
            if self._refused.get(tag_id) != exc.code:
                self._refused[tag_id] = exc.code
                node_id = getattr(node, "nodeid", None)
                await self._report("warn", exc.code,
                                   f"{tag_id} -> {node_id.to_string() if node_id else node}: {exc}")
            else:
                log.debug("%s still unwritable: %s", tag_id, exc)
            return False
        try:
            await node.write_value(ua.DataValue(variant))
        except Exception as exc:
            # Held, not dropped. See _reconcile_loop.
            self._unacked[tag_id] = value
            log.warning("write %s failed: %s — will retry", tag_id, exc)
            return False
        self._unacked.pop(tag_id, None)
        self._refused.pop(tag_id, None)
        return True

    async def _reconcile_loop(self) -> None:
        """Re-attempt input writes the PLC did not accept.

        A failed write used to be logged and discarded, and that is not
        recoverable on its own: the engine publishes *deltas*, so a sensor
        whose write fails and which then holds steady is never sent again. The
        PLC keeps the wrong value indefinitely -- on a connection that reports
        healthy, against a scene that looks right on screen, with one line in
        a log to say why.
        """
        while not self._stopping:
            await asyncio.sleep(self.input_retry_interval)
            if not self._unacked or not self.connected.is_set():
                continue
            try:
                for tag_id, value in list(self._unacked.items()):
                    if tag_id not in self._nodes:
                        # Genuinely unmapped: the scene no longer has this tag,
                        # or nothing maps it to a node. Safe only because
                        # _bind() swaps a finished map in rather than filling
                        # one in place -- while it did the latter, this line
                        # discarded pending writes for tags that were merely
                        # waiting their turn to be re-resolved.
                        self._unacked.pop(tag_id, None)
                        continue
                    await self._write_node(tag_id, value)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("input reconcile failed", exc_info=True)

    def _queue_write(self, tag_id: str, value) -> None:
        """Called from the subscription handler; schedules a bus write."""
        tag = self._table.get(tag_id) if self._table else None
        if tag is None:
            return
        try:
            coerced = tag.coerce(value)
        except Exception:
            log.warning("PLC sent %r for %s, which is not a %s", value, tag_id, tag.type)
            return
        asyncio.get_event_loop().create_task(self.bus.write(tag_id, coerced))

    async def _report(self, level: str, code: str, message: str) -> None:
        log.log({"info": logging.INFO, "warn": logging.WARNING}.get(level, logging.ERROR),
                "%s", message)
        try:
            await self.bus.status(level, code, message)
        except Exception:
            log.debug("could not report status upstream", exc_info=True)
