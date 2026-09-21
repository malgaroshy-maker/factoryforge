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
        self._runner = asyncio.create_task(self._connect_loop())
        self._reconciler = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._runner, self._reconciler):
            if task is not None:
                task.cancel()
        self._runner = self._reconciler = None
        await self._disconnect()

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
                if self._stopping:
                    return
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect(self) -> None:
        log.info("connecting to %s", self.url)
        self.client = Client(url=self.url)
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
        self._subscription = None
        self._nodes.clear()
        self._by_node.clear()
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
        """Resolve nodes and (re)create the subscription for this tag set."""
        assert self.client is not None
        self._nodes.clear()
        self._by_node.clear()

        mapping = dict(self.mapping)
        if self.auto_map:
            mapping.update(await self._browse_for_tags(table, skip=set(mapping)))

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
            self._nodes[tag.id] = node
            self._by_node[node.nodeid.to_string()] = tag.id

        if missing:
            await self._report("warn", "unmapped_tags",
                               f"no OPC UA node mapped for: {', '.join(sorted(missing))}")

        # Watch the tags the PLC writes; we read those.
        plc_written = [(t.id, self._nodes[t.id]) for t in table.by_kind("output")
                       if t.id in self._nodes]
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
        self._last_read.clear()

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
                # Let the connect loop own reconnection; just stand down.
                log.debug("poll read failed: %s", exc)
                return

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
        variant_type = {
            "bit": ua.VariantType.Boolean,
            "int": ua.VariantType.Int32,
            "float": ua.VariantType.Float,
        }[tag.type]
        try:
            await node.write_value(ua.DataValue(ua.Variant(value, variant_type)))
        except Exception as exc:
            # Held, not dropped. See _reconcile_loop.
            self._unacked[tag_id] = value
            log.warning("write %s failed: %s — will retry", tag_id, exc)
            return False
        self._unacked.pop(tag_id, None)
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
