"""Engine-side reference implementation of the tag bus.

Stands in for the Godot engine: owns the authoritative tag table, ticks a scene,
and serves exactly one sidecar. When the real engine is written in C#, this
stays as the protocol reference and as CI's scene runner.

See docs/tag-bus.md.
"""

from __future__ import annotations

import asyncio
import logging
import time

import websockets

from factoryforge_sidecar import protocol as proto
from factoryforge_sidecar.tags import TagValue

log = logging.getLogger(__name__)

ENGINE_ID = "factoryforge-engine-stub/0.1.0"


class EngineStub:
    def __init__(self, scene, host: str = "127.0.0.1", port: int = 7411,
                 tick_ms: int = proto.DEFAULT_TICK_MS) -> None:
        self.scene = scene
        self.host = host
        self.port = port
        self.tick_ms = tick_ms
        self.epoch = 0
        self.tick_count = 0

        self._server: websockets.WebSocketServer | None = None
        self._client: websockets.WebSocketServerProtocol | None = None
        self._last_sent: dict[str, TagValue] = {}
        self._last_forced: dict[str, TagValue] = {}
        self._stop = asyncio.Event()

    @property
    def actual_port(self) -> int:
        if not self._server:
            raise RuntimeError("engine is not running")
        return next(iter(self._server.sockets)).getsockname()[1]

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.actual_port}/tagbus"

    async def start(self) -> None:
        self._server = await websockets.serve(self._handle, self.host, self.port)
        log.info("engine stub listening on %s", self.url)

    async def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def run(self) -> None:
        """Serve and tick until stopped."""
        await self.start()
        try:
            await self._tick_loop()
        finally:
            await self.stop()

    # --- connection ---

    async def _handle(self, ws) -> None:
        if self._client is not None:
            # Two sidecars writing the same output tag is a bug, not a feature.
            await ws.send(proto.encode(proto.status(
                "error", "already_connected", "another sidecar is already connected")))
            await ws.close()
            return

        self._client = ws
        peer = ws.remote_address
        log.info("sidecar connected from %s", peer)
        try:
            await ws.send(proto.encode(proto.hello(ENGINE_ID, self.tick_ms)))
            await self.send_describe()
            async for raw in ws:
                await self._on_message(proto.decode(raw))
        except websockets.ConnectionClosed:
            pass
        finally:
            log.info("sidecar disconnected from %s", peer)
            self._client = None

    async def send_describe(self) -> None:
        """Publish the current tag set and bump the epoch."""
        self.epoch += 1
        self._last_sent = self.scene.tags.snapshot()
        # `describe` already carries every force, so the observe channel starts
        # from that baseline rather than repeating it on the next tick.
        self._last_forced = {tid: self.scene.tags.visible(tid)
                             for tid in self.scene.tags.forced_ids()}
        await self._send(proto.describe(
            self.scene.name, self.epoch, self.scene.tags.to_json()))

    async def _on_message(self, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "write":
            if msg.get("epoch") != self.epoch:
                # A write in flight across a scene change would otherwise land on
                # whatever tag inherited that id.
                log.debug("dropping stale write (epoch %s, now %s)",
                          msg.get("epoch"), self.epoch)
                return
            await self._apply_writes(proto.parse_values(msg))
        elif kind == "force":
            if msg.get("epoch") != self.epoch:
                return
            for tag_id, value in proto.parse_values(msg).items():
                if tag_id in self.scene.tags:
                    self.scene.tags.force(tag_id, value)
            for tag_id in msg.get("clear", []):
                if tag_id in self.scene.tags:
                    self.scene.tags.clear_force(tag_id)
        elif kind == "status":
            log.info("sidecar: %s", msg.get("message"))
        else:
            log.warning("ignoring unexpected message %r", kind)

    async def _apply_writes(self, values: dict[str, TagValue]) -> None:
        unknown = []
        for tag_id, value in values.items():
            tag = self.scene.tags.get(tag_id)
            if tag is None:
                unknown.append(tag_id)
                continue
            if tag.kind != "output":
                await self._send(proto.status(
                    "warn", "wrong_kind",
                    f"{tag_id} is a simulator-owned input; use force to override it"))
                continue
            self.scene.tags.set(tag_id, value)
        if unknown:
            await self._send(proto.status(
                "warn", "unknown_tags", f"ignored unknown tags: {', '.join(unknown)}"))

    # --- tick ---

    #: Most fixed steps to run in one iteration when catching up. Beyond this we
    #: drop the backlog rather than spiral: a simulation that can never catch up
    #: should run slow visibly, not freeze.
    MAX_CATCH_UP_STEPS = 5

    async def _tick_loop(self) -> None:
        """Fixed-timestep loop with a wall-clock accumulator.

        The scene is always advanced by exactly `tick_ms`, never by measured
        elapsed time -- a variable dt would make runs non-reproducible and
        defeat the point of the regression scene. But sleeping for `tick_ms` and
        stepping once per wake makes the simulation run slow, because timer
        granularity (~15ms on Windows) means each wake takes longer than asked.
        So we accumulate real elapsed time and run however many whole fixed
        steps fit.
        """
        interval = self.tick_ms / 1000.0
        last = time.perf_counter()
        accumulator = 0.0

        while not self._stop.is_set():
            await asyncio.sleep(interval)
            now = time.perf_counter()
            accumulator += now - last
            last = now

            steps = 0
            while accumulator >= interval and steps < self.MAX_CATCH_UP_STEPS:
                self.scene.tick(interval)
                self.tick_count += 1
                accumulator -= interval
                steps += 1
            if accumulator > interval * self.MAX_CATCH_UP_STEPS:
                accumulator = 0.0

            if steps:
                await self._send_observations()
                await self._send_updates()

    async def _send_observations(self) -> None:
        """Publish changes to the forced state, delta-only (HP-13).

        Sent *before* `_send_updates` on the same tick: a release has to reach
        the sidecar before the value it reveals, or the sidecar absorbs the
        revealed value underneath a pin it is about to drop.

        This is a separate message from `update` on purpose. `update` is
        simulator-input delivery, drivers hang `push()` off it, and a driver
        that writes what it is handed would push a simulator-forced output
        straight back into the PLC node that owns it.
        """
        if self._client is None:
            return
        current = {tid: self.scene.tags.visible(tid)
                   for tid in self.scene.tags.forced_ids()}
        forced = {tid: v for tid, v in current.items()
                  if tid not in self._last_forced or self._last_forced[tid] != v}
        cleared = [tid for tid in self._last_forced if tid not in current]
        if not forced and not cleared:
            return
        self._last_forced = current
        await self._send(proto.observe(self.tick_count, forced, cleared))

    async def _send_updates(self) -> None:
        """Send only the input tags that actually changed."""
        if self._client is None:
            return
        changed: dict[str, TagValue] = {}
        for tag in self.scene.tags:
            if tag.kind != "input":
                continue
            current = self.scene.tags.visible(tag.id)
            if self._last_sent.get(tag.id) != current:
                changed[tag.id] = current
                self._last_sent[tag.id] = current
        if changed:
            await self._send(proto.update(self.tick_count, changed))

    async def _send(self, msg: dict) -> None:
        if self._client is None:
            return
        try:
            await self._client.send(proto.encode(msg))
        except websockets.ConnectionClosed:
            pass
