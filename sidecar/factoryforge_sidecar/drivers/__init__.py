"""Driver interface and registry.

A driver translates between the tag bus and one industrial protocol. Adding one
means subclassing `Driver`, implementing three methods, and decorating it with
`@register`. Nothing else in the codebase needs to change.

Drivers run on their own asyncio task. A driver that stalls gets its writes
dropped; it must never block the bus.
"""

from __future__ import annotations

import abc
import inspect
import logging
from typing import Callable, Type

from ..tagbus import TagBusClient
from ..tags import TagTable, TagValue

log = logging.getLogger(__name__)

_REGISTRY: dict[str, Type["Driver"]] = {}


def register(name: str) -> Callable[[Type["Driver"]], Type["Driver"]]:
    def wrap(cls: Type["Driver"]) -> Type["Driver"]:
        if name in _REGISTRY:
            raise ValueError(f"driver {name!r} already registered")
        _REGISTRY[name] = cls
        cls.driver_name = name
        return cls
    return wrap


def available() -> list[str]:
    return sorted(_REGISTRY)


def usable() -> dict[str, bool]:
    """Every registered driver, and whether it can actually run here.

    Registration is not the same question. Each protocol driver guards its own
    third-party import and registers regardless, so it can explain itself at
    connect time rather than vanishing from the CLI -- which means
    `available()` lists drivers that will fail the moment they are used.

    A frozen release makes that distinction expensive: PyInstaller bundles what
    was importable at build time, so a release built without an extra ships a
    driver that is present, listed, and dead. `tools/packaging/check_release.py`
    asks this before letting a release out.
    """
    from . import plcsim_advanced, s7_snap7

    needs = {
        "s7-snap7": s7_snap7.HAS_SNAP7,
        "plcsim-advanced": plcsim_advanced.HAS_PYTHONNET,
        "opcua-client": "opcua-client" in _REGISTRY,
        "opcua-server": "opcua-server" in _REGISTRY,
    }
    return {name: needs.get(name, True) for name in available()}


def option_types(name: str) -> dict[str, str]:
    """Each keyword the named driver's constructor takes, and its annotation.

    The CLI uses this to convert `-o` values, because argparse hands over
    strings and a type hint does not make one an int. Reading the signature
    keeps the two in step: a driver that gains an option gets it coerced
    without anyone remembering to add it to a table somewhere else.

    Annotations come back as written -- driver modules use
    `from __future__ import annotations`, so they are already strings, and the
    caller only needs to recognise the leading type name.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        return {}
    hints: dict[str, str] = {}
    for param in inspect.signature(cls.__init__).parameters.values():
        if param.name in ("self", "bus") or param.kind is param.VAR_KEYWORD:
            continue
        if param.annotation is inspect.Parameter.empty:
            continue
        hints[param.name] = (
            param.annotation if isinstance(param.annotation, str)
            else getattr(param.annotation, "__name__", str(param.annotation)))
    return hints


def create(name: str, bus: TagBusClient, **config) -> "Driver":
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown driver {name!r}; available: {', '.join(available())}")
    return cls(bus, **config)


class Driver(abc.ABC):
    """Base class for all drivers."""

    driver_name: str = "unnamed"

    def __init__(self, bus: TagBusClient, **config) -> None:
        self.bus = bus
        self.config = config
        bus.on_describe(self._on_describe)
        bus.on_update(self._on_update)
        bus.on_disconnect(self._on_bus_disconnect)

    # --- to implement ---

    @abc.abstractmethod
    async def start(self) -> None:
        """Open sockets, bind ports, connect to the PLC."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Release everything acquired in start(). Must be idempotent."""

    @abc.abstractmethod
    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        """Rebuild the address map for a new tag set.

        Called on every `describe`. Tag ids are stable but their membership and
        ordering are not, so any address mapping must be derived fresh here
        rather than cached across epochs.
        """

    async def push(self, values: dict[str, TagValue]) -> None:
        """Handle changed simulator inputs (sensors) heading to the PLC.

        Default is a no-op, which is right for drivers where the PLC polls --
        a Modbus server just serves whatever the datastore holds. Override when
        the driver must actively push, as an OPC UA server does for subscriptions.
        """

    async def bus_disconnected(self) -> None:
        """The tag bus connection to the engine dropped.

        Default is a no-op: most drivers already recompute from the next
        describe/update once the bus reconnects. Override when the driver
        exposes points a PLC polls independent of this process's own state — a
        Modbus or OPC UA *server* driver — so it can mark them bad-quality
        instead of silently continuing to serve the last value it ever read
        from a bus that is no longer there. See FF-03.
        """

    # --- internal ---

    async def _on_describe(self, scene: str, epoch: int, table: TagTable) -> None:
        try:
            await self.rebuild(scene, epoch, table)
        except Exception:
            log.exception("%s: rebuild failed", self.driver_name)

    async def _on_update(self, values: dict[str, TagValue]) -> None:
        try:
            await self.push(values)
        except Exception:
            log.exception("%s: push failed", self.driver_name)

    async def _on_bus_disconnect(self) -> None:
        try:
            await self.bus_disconnected()
        except Exception:
            log.exception("%s: bus_disconnected failed", self.driver_name)


# Importing the modules is what runs their @register decorators.
from . import mock, modbus_tcp, plcsim_advanced, s7_snap7  # noqa: E402,F401

# OPC UA is the primary integration path but pulls in a sizeable dependency.
# A Modbus-only or CI-only install should not fail because it is absent.
try:
    from . import opcua_client, opcua_server  # noqa: E402,F401
except ImportError:  # pragma: no cover
    log.info("asyncua not installed; OPC UA drivers unavailable "
             "(pip install factoryforge-sidecar[opcua])")
