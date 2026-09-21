"""Sidecar-side tag bus client.

Connects to the engine, tracks the tag table, coalesces driver writes into one
message per tick, and fans engine updates out to drivers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import websockets

from . import protocol as proto
from .tags import Tag, TagTable, TagValue

log = logging.getLogger(__name__)

#: Called with (scene, epoch, table) whenever the engine sends a describe.
DescribeHook = Callable[[str, int, TagTable], Awaitable[None]]
#: Called with the changed input values whenever the engine sends an update.
UpdateHook = Callable[[dict[str, TagValue]], Awaitable[None]]
#: Called with (forced values, released ids) whenever the engine's forced state
#: changes. Deliberately separate from `UpdateHook`: `Driver` subscribes to
#: updates and a driver's `push()` writes what it is handed straight into the
#: PLC, so routing observed output state through that hook would send a
#: simulator-invented value into a PLC-owned node (HP-13).
ObserveHook = Callable[[dict[str, TagValue], list[str]], Awaitable[None]]
#: Called with no arguments when the bus connection drops.
DisconnectHook = Callable[[], Awaitable[None]]

#: Reconnect backoff bounds. A drop retries almost immediately — most drops
#: are the engine restarting, which takes a couple of seconds — and backs off
#: to a slow poll rather than hammering a socket nobody is listening on.
_RECONNECT_MIN_DELAY = 0.5
_RECONNECT_MAX_DELAY = 5.0


class TagBusClient:
    def __init__(self, url: str = proto.DEFAULT_URL) -> None:
        self.url = url
        self.table = TagTable()
        self.scene: str | None = None
        self.epoch: int = -1
        self.connected = asyncio.Event()

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._pending: dict[str, TagValue] = {}
        self._pending_lock = asyncio.Lock()
        self._tick_ms = proto.DEFAULT_TICK_MS
        self._on_describe: list[DescribeHook] = []
        self._on_update: list[UpdateHook] = []
        self._on_observe: list[ObserveHook] = []
        self._on_disconnect: list[DisconnectHook] = []

    # --- hooks ---

    def on_describe(self, hook: DescribeHook) -> None:
        self._on_describe.append(hook)
        # Drivers are usually constructed after the engine's initial describe has
        # already been processed. Replay it so registration order never matters.
        if self.epoch >= 0 and self.scene is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(hook(self.scene, self.epoch, self.table))

    def on_update(self, hook: UpdateHook) -> None:
        self._on_update.append(hook)

    def on_observe(self, hook: ObserveHook) -> None:
        """Watch the engine's forced state. Not something `Driver` subscribes
        to — see `ObserveHook` for why that would be a worse bug than HP-13."""
        self._on_observe.append(hook)

    def on_disconnect(self, hook: DisconnectHook) -> None:
        self._on_disconnect.append(hook)

    # --- driver-facing API ---

    def read(self, tag_id: str) -> TagValue:
        """Read from the local cache. Never blocks, never hits the bus."""
        return self.table.visible(tag_id)

    async def write(self, tag_id: str, value: TagValue) -> None:
        """Queue a write to a PLC-output tag. Flushed on the next tick."""
        tag = self.table.get(tag_id)
        if tag is None:
            log.warning("write to unknown tag %s (scene may have changed)", tag_id)
            return
        if tag.kind != "output":
            raise ValueError(
                f"{tag_id} is an input (simulator-owned); use force() to override it"
            )
        coerced = tag.coerce(value)
        async with self._pending_lock:
            self._pending[tag_id] = coerced

        # Reflect it locally too. For an output tag the client is ordinarily
        # the authority — the engine never echoes one back, because the
        # controller is what drives it — so without this, read() on an output
        # returns its default forever. That made a live status display show
        # `rotate=0` while the belt it commands was visibly running.
        #
        # `set` and not `observe`: a forced output absorbs this silently, so
        # the pin the engine reported on the observe channel keeps winning and
        # read() goes on telling the truth about what the motor is doing
        # (HP-13). The underlying value still updates, so releasing the force
        # reveals the command the PLC is actually holding.
        self.table.set(tag_id, coerced)

    async def write_many(self, values: dict[str, TagValue]) -> None:
        for tag_id, value in values.items():
            await self.write(tag_id, value)

    async def force(self, values: dict[str, TagValue] | None = None,
                    clear: list[str] | None = None) -> None:
        await self._send(proto.force(self.epoch, values, clear or []))

    async def status(self, level: str, code: str, message: str) -> None:
        await self._send(proto.status(level, code, message))

    # --- lifecycle ---

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Connect and pump until *stop* is set, reconnecting with backoff on
        every drop in between.

        A sidecar that gives up after the first disconnect is worse than one
        that never connected at all: it keeps a Modbus or OPC UA *server*
        driver up, serving whatever it last knew as if the line were still
        running, with nothing anywhere saying otherwise. See FF-03.
        """
        was_connected = False
        delay = _RECONNECT_MIN_DELAY
        while stop is None or not stop.is_set():
            try:
                async with websockets.connect(self.url, max_queue=64) as ws:
                    self._ws = ws
                    msg = proto.decode(await ws.recv())
                    if msg.get("t") == "status":
                        log.warning("status message before hello: %s", msg.get("message"))
                        msg = proto.decode(await ws.recv())
                    hello = proto.check_hello(msg)
                    self._tick_ms = hello.get("tick_ms", proto.DEFAULT_TICK_MS)

                    # A fresh connection — first time or a reconnect — starts
                    # from a clean table and waits for the real describe. The
                    # epoch may have changed under us while we were gone.
                    self.table = TagTable()
                    self.scene = None
                    self.epoch = -1

                    log.info("%s to %s (tick %dms)",
                             "reconnected" if was_connected else "connected",
                             hello.get("engine"), self._tick_ms)
                    self.connected.set()
                    was_connected = True
                    delay = _RECONNECT_MIN_DELAY   # a live connection earns a fresh start

                    flusher = asyncio.create_task(self._flush_loop())
                    try:
                        stopped_by_caller = await self._recv_loop(ws, stop)
                    finally:
                        flusher.cancel()
                        self.connected.clear()
                        self._ws = None

                if stopped_by_caller:
                    return
                # _recv_loop swallows ConnectionClosed itself (so a clean
                # engine-side close never looks like an error), which means
                # this is the common case — closing the engine while `connect`
                # is running — not the exceptional one below.
                await self._report_disconnect("engine closed the connection")
            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                if was_connected:
                    await self._report_disconnect(str(e))
                else:
                    log.debug("tag bus not reachable yet: %s — retrying", e)

            if stop is not None and stop.is_set():
                return
            await self._wait_or_stop(delay, stop)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _report_disconnect(self, reason: str) -> None:
        log.warning("tag bus connection lost: %s — retrying", reason)
        for hook in self._on_disconnect:
            await hook()

    @staticmethod
    async def _wait_or_stop(delay: float, stop: asyncio.Event | None) -> None:
        """Sleep for *delay*, but wake immediately if *stop* is set —
        otherwise the last backoff of a deliberate shutdown blocks it."""
        if stop is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _recv_loop(self, ws, stop: asyncio.Event | None) -> bool:
        """Pump incoming messages until *stop* is set or the connection
        closes. Returns True if *stop* caused the return, False if the
        connection dropped on its own — the caller needs to tell a deliberate
        shutdown apart from a drop worth retrying."""
        stopper = asyncio.create_task(stop.wait()) if stop else None
        try:
            while True:
                recv = asyncio.create_task(ws.recv())
                waits = {recv} | ({stopper} if stopper else set())
                done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                if stopper in done:
                    recv.cancel()
                    return True
                try:
                    raw = recv.result()
                except websockets.ConnectionClosed:
                    log.info("engine closed the connection")
                    return False
                await self._handle(proto.decode(raw))
        finally:
            if stopper:
                stopper.cancel()

    async def _handle(self, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "describe":
            scene, epoch, tags, forced = proto.parse_describe(msg)
            self.scene, self.epoch = scene, epoch
            table = TagTable(tags)
            # The values in a describe are already the *visible* ones, so
            # pinning each forced tag to the value it arrived with reproduces
            # the engine's view exactly.
            for tag_id in forced:
                table.force(tag_id, table.value(tag_id))
            self.table = table
            # A new epoch invalidates anything queued against the old tag set.
            async with self._pending_lock:
                self._pending.clear()
            log.info("scene %r epoch %d, %d tags (%d forced)",
                     scene, epoch, len(tags), len(forced))
            for hook in self._on_describe:
                await hook(scene, epoch, self.table)
        elif kind == "update":
            values = proto.parse_values(msg)
            for tag_id, value in values.items():
                if tag_id in self.table:
                    self.table.observe(tag_id, value)
            for hook in self._on_update:
                await hook(values)
        elif kind == "observe":
            forced_values, cleared = proto.parse_observe(msg)
            # Applied before the hooks, and before any `update` in the same
            # tick, because the engine sends `observe` first: a release has to
            # be in the table before the value it reveals arrives.
            for tag_id, value in forced_values.items():
                if tag_id in self.table:
                    self.table.force(tag_id, value)
            for tag_id in cleared:
                if tag_id in self.table:
                    self.table.clear_force(tag_id)
            for hook in self._on_observe:
                await hook(forced_values, cleared)
        elif kind == "status":
            log.log(
                {"info": logging.INFO, "warn": logging.WARNING}.get(
                    msg.get("level"), logging.ERROR
                ),
                "engine: %s", msg.get("message"),
            )
        else:
            log.warning("ignoring unexpected message %r", kind)

    async def _flush_loop(self) -> None:
        """Coalesce queued writes into at most one message per tick."""
        interval = self._tick_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            async with self._pending_lock:
                if not self._pending:
                    continue
                batch, self._pending = self._pending, {}
            await self._send(proto.write(self.epoch, batch))

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            log.debug("dropping %s: not connected", msg.get("t"))
            return
        try:
            await self._ws.send(proto.encode(msg))
        except websockets.ConnectionClosed:
            log.debug("dropping %s: connection closed", msg.get("t"))
