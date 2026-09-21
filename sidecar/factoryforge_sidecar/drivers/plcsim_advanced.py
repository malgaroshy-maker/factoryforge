"""Siemens PLCSIM Advanced Simulation Runtime API driver.

Connects directly to S7-PLCSIM Advanced virtual CPUs via Siemens' native C#/.NET
Simulation Runtime API DLL (Siemens.Simatic.Simulation.Runtime.Api.x64).
Direct shared memory I/O access — lowest latency, zero network overhead, no OPC UA licence needed.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
from pathlib import Path
from typing import Any

from ..tagbus import TagBusClient
from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)

try:
    import clr  # pythonnet
    HAS_PYTHONNET = True
except ImportError:
    HAS_PYTHONNET = False


#: Where the PLCSIM Advanced installer puts its API assembly. It is not in the
#: GAC and not on .NET's probing path, so `clr.AddReference("Siemens.Simatic.
#: Simulation.Runtime.Api.x64")` by bare name fails on a normal install — which
#: used to surface as "could not connect (fallback mode)" and a driver that
#: silently did nothing.
_API_DLL = "Siemens.Simatic.Simulation.Runtime.Api.x64.dll"
_API_GLOBS = (
    rf"C:\Program Files\Common Files\Siemens\PLCSIMADV\API\*\{_API_DLL}",
    rf"C:\Program Files (x86)\Common Files\Siemens\PLCSIMADV\API\*\{_API_DLL}",
    rf"C:\Program Files\Siemens\**\{_API_DLL}",
    rf"C:\Program Files (x86)\Siemens\**\{_API_DLL}",
)


def _load_runtime_api() -> None:
    """Reference the Simulation Runtime API assembly, by name if .NET can find
    it and by path otherwise, preferring the newest API version installed."""
    try:
        clr.AddReference("Siemens.Simatic.Simulation.Runtime.Api.x64")
        return
    except Exception:
        pass

    found: list[str] = []
    for pattern in _API_GLOBS:
        found += glob.glob(pattern, recursive=True)
    if not found:
        raise RuntimeError(
            "could not find Siemens.Simatic.Simulation.Runtime.Api.x64.dll. "
            "Is S7-PLCSIM Advanced installed?"
        )

    # Paths sort lexically, so API\6.0 beats API\2.1 — good enough here and it
    # avoids parsing Siemens' version directories.
    clr.AddReference(sorted(found)[-1])
    log.debug("loaded Simulation Runtime API from %s", sorted(found)[-1])


@register("plcsim-advanced")
class PLCSIMAdvancedDriver(Driver):
    """Siemens PLCSIM Advanced native API driver."""

    def __init__(self, bus: TagBusClient, instance: str = "Sorting_PLC",
                 mapping: dict[str, str] | None = None, mapping_file: str | None = None,
                 **config: Any) -> None:
        super().__init__(bus, **config)
        self.instance_name = instance
        self._instance: Any = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._table: TagTable | None = None

        # FactoryForge tag id -> PLC symbol, e.g.
        #   "conveyor.rotate": '"FF_IO".ConveyorRotate'
        # Required, and it was missing entirely: the driver used to hand tag ids
        # straight to ReadBool, so it asked the CPU for a variable called
        # "conveyor.rotate" that no PLC has ever had. Every read raised, every
        # exception was swallowed, and the driver reported itself started.
        self.mapping: dict[str, str] = dict(mapping or {})
        if mapping_file:
            loaded = json.loads(Path(mapping_file).read_text())
            self.mapping.update(
                {k: v for k, v in loaded.items() if not k.startswith("_") and v}
            )
        self._warned: set[str] = set()

    async def start(self) -> None:
        self._running = True

        # Fail loudly. Both of these used to log a warning and return, leaving a
        # driver that reported "started", drove nothing, and sent whoever ran it
        # to go and look at their PLC.
        if not HAS_PYTHONNET:
            raise RuntimeError(
                "the plcsim-advanced driver needs pythonnet: pip install pythonnet. "
                "It talks to Siemens' .NET Simulation Runtime API, which has no "
                "pure-Python equivalent."
            )

        if not self.mapping:
            raise RuntimeError(
                "plcsim-advanced needs a tag -> PLC symbol map. Export one from the "
                "engine (F4 -> Export) and pass --mapping <file>; the values are PLC "
                'symbols such as "FF_IO".ConveyorRotate.'
            )

        try:
            _load_runtime_api()
            from Siemens.Simatic.Simulation.Runtime import SimulationRuntimeManager  # type: ignore

            self._instance = SimulationRuntimeManager.CreateInterface(self.instance_name)
            # Do NOT PowerOn/Run here. The instance is normally already running
            # with a program downloaded from TIA, and cycling it would wipe that
            # — the sidecar attaches to a CPU, it does not own it.
            self._instance.UpdateTagList()
        except Exception as err:
            raise RuntimeError(
                f"could not attach to PLCSIM Advanced instance {self.instance_name!r}: {err}. "
                "Check the instance is started in the PLCSIM Advanced control panel and "
                "that its name matches -o instance."
            ) from err

        log.info("Connected to PLCSIM Advanced instance '%s'", self.instance_name)
        await self._seed_inputs()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Deliberately does NOT PowerOff. The sidecar attaches to a CPU somebody
        # else started and downloaded a program to; switching it off when a
        # 40-second run ends is not cleanup, it is destroying their setup. This
        # driver used to do exactly that, and it is the reason the pairing with
        # PowerOn/Run in start() had to go too.
        self._instance = None

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        self._table = table
        await self._seed_inputs()

    async def _seed_inputs(self) -> None:
        """Write the current value of every mapped simulator input once.

        The engine publishes *deltas* after the description, so an input that
        never changes is never sent: a healthy E-stop, a pusher that starts
        retracted, a counter still at zero. Nothing ever establishes them, and
        the CPU keeps whatever those symbols already held -- false, stale, or
        left over from the last time somebody ran something against this
        instance, which for an attached PLCSIM CPU is a very live possibility.

        Hung off both ends deliberately: a connection and a table can arrive in
        either order, and whichever is second is the one that has to fire.
        rebuild() can precede the attach, and push() simply returns while there
        is no instance.
        """
        table = self._table
        if table is None or self._instance is None:
            return
        seed = {tag.id: table.visible(tag.id) for tag in table.by_kind("input")
                if tag.id in self.mapping}
        if not seed:
            return
        await self.push(seed)
        log.info("seeded %d simulator inputs into '%s'", len(seed), self.instance_name)

    async def push(self, values: dict[str, TagValue]) -> None:
        if not self._instance or not self._table:
            return

        for tag_id, val in values.items():
            symbol = self.mapping.get(tag_id)
            if symbol is None:
                self._warn_once(tag_id, f"{tag_id} has no PLC symbol; not written")
                continue

            try:
                tag = self._table.get(tag_id)
                # kind and type are plain strings in tags.py. This used to test
                # `tag.kind.value`, which is an AttributeError on every tag — one
                # of three API mistakes that show the driver had never once run.
                if tag and tag.kind == "input":
                    if tag.type == "bit":
                        self._instance.WriteBool(symbol, bool(val))
                    elif tag.type == "int":
                        self._instance.WriteInt32(symbol, int(val))
                    else:
                        self._instance.WriteDouble(symbol, float(val))
            except Exception as err:
                log.error("writing %s (%s): %s", tag_id, symbol, err)

    def _warn_once(self, key: str, message: str) -> None:
        """A polling driver would otherwise repeat the same complaint 20x a
        second and bury everything else."""
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    async def _poll_loop(self) -> None:
        read_ok = False
        while self._running:
            if self._instance and self._table:
                changes: dict[str, TagValue] = {}
                # by_kind("output"): the PLC writes these, the simulator reads
                # them. The old code called table.outputs(), which does not
                # exist — the driver crashed on its first poll.
                for tag in self._table.by_kind("output"):
                    symbol = self.mapping.get(tag.id)
                    if symbol is None:
                        self._warn_once(tag.id, f"{tag.id} has no PLC symbol; not read")
                        continue
                    try:
                        if tag.type == "bit":
                            changes[tag.id] = bool(self._instance.ReadBool(symbol))
                        elif tag.type == "int":
                            changes[tag.id] = int(self._instance.ReadInt32(symbol))
                        else:
                            changes[tag.id] = float(self._instance.ReadDouble(symbol))
                        read_ok = True
                    except Exception as err:
                        # Once per tag, not per scan. Silently swallowing these
                        # is what let a driver that could read nothing at all
                        # look like a driver that was working.
                        self._warn_once(f"read:{tag.id}", f"reading {tag.id} ({symbol}): {err}")
                if changes:
                    await self.bus.write_many(changes)
                elif not read_ok:
                    self._warn_once(
                        "nothing", "connected to PLCSIM but not one tag could be read — "
                                   "check the symbols in your mapping file match the DB")
            await asyncio.sleep(0.05)
