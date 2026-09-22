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
        #: Set once every describe hook has finished rebuilding for the current
        #: epoch. Writes are held back -- discarded, not queued -- while it is
        #: clear: see `_flush_loop`, and HP-33.
        self.rebuilt = asyncio.Event()

        self._ws: websockets.WebSocketClientProtocol | None = None
        #: tag id -> (value, generation it was derived under). The generation
        #: is what makes HP-33's "dropped, not held back" true in fact and not
        #: only in intent: the drop used to be decided at FLUSH time, so a
        #: write queued while the gate was shut but flushed after it reopened
        #: was sent, carrying an address map older than the epoch stamped on
        #: it. Windows hid this -- the flush landed inside the window -- and
        #: Linux did not, failing 2 runs in 3. Judged at flush against the
        #: generation in force when the value was computed, the timing stops
        #: mattering.
        self._pending: dict[str, tuple[TagValue, int, bool]] = {}
        #: Bumped by every describe, so a write cannot outlive its map.
        self._write_gen = 0
        self._pending_lock = asyncio.Lock()
        self._tick_ms = proto.DEFAULT_TICK_MS
        self._on_describe: list[DescribeHook] = []
        self._on_update: list[UpdateHook] = []
        self._on_observe: list[ObserveHook] = []
        self._on_disconnect: list[DisconnectHook] = []

        # Work handed to the driver hooks, and the task that runs it. The
        # receive loop only ever fills these in; it never awaits a driver.
        # See `_dispatch_loop` (HP-31).
        self._next_describe: tuple[str, int, TagTable] | None = None
        self._next_updates: dict[str, TagValue] = {}
        self._next_observe: tuple[dict[str, TagValue], list[str]] = ({}, [])
        self._dispatch_wake = asyncio.Event()

    # --- hooks ---

    def on_describe(self, hook: DescribeHook) -> None:
        self._on_describe.append(hook)
        # Drivers are usually constructed after the engine's initial describe
        # has already been processed. Replay it so registration order never
        # matters.
        #
        # Through the same queue a real describe goes through, and therefore
        # re-running every hook rather than only the new one. Two reasons.
        # `rebuild` is contracted to derive everything fresh, so re-running it
        # is a cost and not a hazard -- and the new hook has no address map at
        # all yet, which is the same state a scene change leaves every hook in,
        # so it must hold writes back the same way (HP-33). The old path
        # created a bare task for the one new hook and gated nothing.
        if self.epoch >= 0 and self.scene is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self._queue_describe()

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
            # Stamped with BOTH the generation and whether the gate was open
            # when this value was computed. The generation catches a write
            # queued before the describe; the gate flag catches one computed
            # during the window, which shares the new generation but came from
            # a poller still holding the old address map. Judging either at
            # flush time alone lets the other through whenever the flush lands
            # on the far side of the gate reopening.
            self._pending[tag_id] = (coerced, self._write_gen, self.rebuilt.is_set())

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
                    self._reset_dispatch()

                    log.info("%s to %s (tick %dms)",
                             "reconnected" if was_connected else "connected",
                             hello.get("engine"), self._tick_ms)
                    self.connected.set()
                    was_connected = True
                    delay = _RECONNECT_MIN_DELAY   # a live connection earns a fresh start

                    flusher = asyncio.create_task(self._flush_loop())
                    dispatcher = asyncio.create_task(self._dispatch_loop())
                    try:
                        stopped_by_caller = await self._recv_loop(ws, stop)
                    finally:
                        flusher.cancel()
                        dispatcher.cancel()
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
            self._queue_describe()
        elif kind == "update":
            values = proto.parse_values(msg)
            # The cache is the bus client's own business and is updated here,
            # on the receive loop, so `read()` is current the instant a frame
            # arrives however busy the drivers are. Only the *hooks* are handed
            # off (HP-31).
            for tag_id, value in values.items():
                if tag_id in self.table:
                    self.table.observe(tag_id, value)
            self._next_updates.update(values)
            self._dispatch_wake.set()
        elif kind == "observe":
            forced_values, cleared = proto.parse_observe(msg)
            # Applied before any `update` in the same tick, because the engine
            # sends `observe` first: a release has to be in the table before
            # the value it reveals arrives.
            for tag_id, value in forced_values.items():
                if tag_id in self.table:
                    self.table.force(tag_id, value)
            for tag_id in cleared:
                if tag_id in self.table:
                    self.table.clear_force(tag_id)
            # Coalescing has to respect the order the frames arrived in. Merged
            # independently, a tag forced, released and forced again while a
            # hook was busy reached that hook in *both* collections -- and
            # whoever applies the forced values and then the releases, which is
            # the order the engine sends them in and the order this method
            # itself applies them, ends up having dropped a pin that is still
            # in effect. The cache above never had it, because the receive loop
            # applies each frame as it lands; only the hooks saw it, and
            # nothing subscribes to `observe` by default (HP-31).
            queued_forced, queued_cleared = self._next_observe
            for tag_id, value in forced_values.items():
                queued_forced[tag_id] = value
                if tag_id in queued_cleared:
                    queued_cleared.remove(tag_id)
            for tag_id in cleared:
                queued_forced.pop(tag_id, None)
                if tag_id not in queued_cleared:
                    queued_cleared.append(tag_id)
            self._dispatch_wake.set()
        elif kind == "status":
            log.log(
                {"info": logging.INFO, "warn": logging.WARNING}.get(
                    msg.get("level"), logging.ERROR
                ),
                "engine: %s", msg.get("message"),
            )
        else:
            log.warning("ignoring unexpected message %r", kind)

    # --- dispatch: driver work, off the receive loop (HP-31, HP-33) ---

    def _reset_dispatch(self) -> None:
        self._next_describe = None
        self._next_updates = {}
        self._next_observe = ({}, [])
        self._dispatch_wake.clear()
        self.rebuilt.clear()
        # Anything already queued was derived under the old map, and
        # anything queued from here until the hooks return is too.
        self._write_gen += 1

    def _queue_describe(self) -> None:
        """Hand the current scene to the describe hooks, off the receive loop.

        Clears `rebuilt`, which holds writes back until every hook has
        finished. Updates already queued are dropped with it: they are deltas
        against a tag set nobody has any more, and the describe carries the
        current value of every tag in the new one.

        This runs on the receive loop, so the gate closes when the describe
        *arrives* rather than when the dispatcher reaches it. That distinction
        is the whole of HP-33 once hooks moved off the receive loop: a driver
        wedged in `push()` is holding the previous epoch's address map exactly
        as one that has not rebuilt yet is, and the dispatcher cannot get to
        the describe to say so (HP-31, HP-33).
        """
        self._next_describe = (self.scene or "", self.epoch, self.table)
        self._next_updates = {}
        self._next_observe = ({}, [])
        self.rebuilt.clear()
        # Anything already queued was derived under the old map, and
        # anything queued from here until the hooks return is too.
        self._write_gen += 1
        self._dispatch_wake.set()

    async def _dispatch_loop(self) -> None:
        """Run the driver hooks, off the receive loop.

        The receive loop used to await every hook inline (HP-31), so one slow
        PLC write held up the next sensor update *and* the next scene
        description -- for every driver, not only the slow one, and for the
        client's own cache too. Hooks run here instead: still in order, still
        one at a time, because a driver wants its own work serialised, but
        nothing a driver does is between a frame and the socket any more.

        Updates coalesce while a hook is busy. They are deltas, so merging two
        of them produces the message the engine would have sent had it batched
        them, and a driver that has fallen behind wants the current state
        rather than a queue of history. `docs/tag-bus.md`: "A driver that
        stalls gets its writes dropped, not the whole simulation."
        """
        while True:
            await self._dispatch_wake.wait()
            self._dispatch_wake.clear()

            describe, self._next_describe = self._next_describe, None
            forced, cleared = self._next_observe
            self._next_observe = ({}, [])
            values, self._next_updates = self._next_updates, {}

            if describe is not None:
                scene, epoch, table = describe
                for hook in list(self._on_describe):
                    await self._run_hook(hook, scene, epoch, table)
                # Only now, and only if nothing has queued another describe
                # behind this one. Everything between adopting an epoch and
                # finishing the rebuild is a window in which a driver is still
                # holding the previous epoch's address map (HP-33).
                if self._next_describe is None:
                    self.rebuilt.set()
            if forced or cleared:
                for hook in list(self._on_observe):
                    await self._run_hook(hook, forced, cleared)
            if values:
                for hook in list(self._on_update):
                    # A describe that arrived while these were being handed out
                    # has already emptied the queue behind them, for the reason
                    # that applies to these too: they are deltas against a tag
                    # set nobody has any more. Stop delivering them rather than
                    # finish the round -- `rebuild` re-reads the PLC, so there
                    # is nothing here worth arriving late, and one of these ids
                    # belonging to a different tag in the new scene is the whole
                    # reason the epoch is stamped on a write.
                    if self._next_describe is not None:
                        break
                    await self._run_hook(hook, values)

    @staticmethod
    async def _run_hook(hook, *args) -> None:
        """One hook failing must not take the dispatcher down with it.

        `Driver` already guards its own three, but a hook registered by an app
        or a test is not obliged to, and a dispatcher that dies leaves every
        driver deaf while the bus goes on looking perfectly healthy.
        """
        try:
            await hook(*args)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("a tag bus hook failed: %r", hook)

    async def _flush_loop(self) -> None:
        """Coalesce queued writes into at most one message per tick."""
        interval = self._tick_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            async with self._pending_lock:
                if not self._pending:
                    continue
                batch, self._pending = self._pending, {}
            # Dropped, not held back (HP-33), and judged per value against
            # the generation it was computed under rather than against the
            # gate's state right now. A write derived before the current
            # describe came out of an address map older than the epoch it
            # would be stamped with, so the engine would accept it onto
            # whichever tag inherited that id. Every driver re-derives its map
            # and re-reads the PLC inside `rebuild`, which is what brings the
            # value back.
            gen = self._write_gen
            fresh = {tag_id: value
                     for tag_id, (value, at, ready) in batch.items()
                     if at == gen and ready}
            stale = len(batch) - len(fresh)
            if stale:
                log.debug("dropping %d write(s) derived before this epoch's "
                          "rebuild finished (epoch %s)", stale, self.epoch)
            if not fresh or not self.rebuilt.is_set():
                continue
            await self._send(proto.write(self.epoch, fresh))

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            log.debug("dropping %s: not connected", msg.get("t"))
            return
        try:
            await self._ws.send(proto.encode(msg))
        except websockets.ConnectionClosed:
            log.debug("dropping %s: connection closed", msg.get("t"))
