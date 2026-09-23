"""Mock driver: a programmable stand-in for a PLC.

Used by CI and by anyone developing a part who does not want to start TIA Portal
to see whether their conveyor turns. Exposes the tag bus directly as an async
API and records everything the simulator reported.
"""

from __future__ import annotations

import asyncio
import logging

from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)


@register("mock")
class MockDriver(Driver):
    def __init__(self, bus, connect_delay: float = 0.0, **config) -> None:
        super().__init__(bus, **config)
        self.running = False
        #: Seconds this stand-in PLC takes to be reached (IP-30). 0, the
        #: default, is reached the moment `start()` runs. Anything else makes
        #: this behave like a driver still connecting: `start()` returns at
        #: once, sensor updates are dropped as `opcua_client.push` drops them
        #: while disconnected, and when the delay is up the current inputs are
        #: seeded, as every real driver's bind does, and it reports ready. That
        #: is what a slow OPC UA or S7 connection looks like from the engine,
        #: without a PLC: `connect --driver mock -o connect_delay 3`.
        self.connect_delay = float(connect_delay)
        #: Every value the engine has reported, in order. Test assertions read this.
        self.history: list[tuple[str, TagValue]] = []
        self.tags: TagTable | None = None
        self._ready = asyncio.Event()
        self._waiters: list[tuple[str, TagValue, asyncio.Future]] = []
        self._connecting = False
        self._connector: asyncio.Task | None = None

    async def start(self) -> None:
        self.running = True
        if self.connect_delay <= 0:
            await self.link(True, "mock controller: whatever script drives it")
            return
        self._connecting = True
        await self.link(False, f"mock controller: connecting "
                               f"(a simulated {self.connect_delay:g}s delay)")
        self._connector = asyncio.create_task(self._connect_later())

    async def _connect_later(self) -> None:
        await asyncio.sleep(self.connect_delay)
        if not self.running:
            return
        self._connecting = False
        # What a PLC is handed on connecting: the current value of every
        # input, not the edges it missed while nobody was there.
        table = self.bus.table
        for tag in table.by_kind("input"):
            self.history.append((tag.id, table.visible(tag.id)))
        await self.link(True, f"mock controller: reached after "
                              f"{self.connect_delay:g}s")

    async def stop(self) -> None:
        self.running = False
        connector, self._connector = self._connector, None
        if connector is not None:
            connector.cancel()
        for _, _, fut in self._waiters:
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        await self.link(False, "mock controller: stopped")

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        self.tags = table
        self._ready.set()

    async def push(self, values: dict[str, TagValue]) -> None:
        if self._connecting:
            return          # nobody there yet to hear it
        for tag_id, value in values.items():
            self.history.append((tag_id, value))
        for waiter in list(self._waiters):
            tag_id, expected, fut = waiter
            if tag_id in values and values[tag_id] == expected and not fut.done():
                fut.set_result(value := values[tag_id])
                self._waiters.remove(waiter)

    # --- test-facing API ---

    async def ready(self, timeout: float = 5.0) -> TagTable:
        """Block until the engine has sent a describe."""
        await asyncio.wait_for(self._ready.wait(), timeout)
        assert self.tags is not None
        return self.tags

    async def set(self, tag_id: str, value: TagValue) -> None:
        """Write a PLC output, as a real PLC would."""
        await self.bus.write(tag_id, value)

    def get(self, tag_id: str) -> TagValue:
        """Read the sidecar's cached value."""
        return self.bus.read(tag_id)

    async def wait_for(self, tag_id: str, value: TagValue, timeout: float = 5.0) -> TagValue:
        """Block until *tag_id* reaches *value*.

        Returns immediately if it is already there, which avoids a race where
        the change lands between the caller's check and this call.
        """
        if self.bus.read(tag_id) == value:
            return value
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.append((tag_id, value, fut))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise AssertionError(
                f"{tag_id} did not reach {value!r} within {timeout}s "
                f"(last seen {self.bus.read(tag_id)!r})"
            ) from None
