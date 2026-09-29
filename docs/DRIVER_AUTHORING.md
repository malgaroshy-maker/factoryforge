# 🔌 FactoryForge Driver Authoring Guide

This guide explains how to add custom protocol drivers to the **FactoryForge Python sidecar**.

---

## 🏗️ Driver Architecture Overview

The sidecar decouples the 3D physics engine from industrial communication protocols. A driver translates between the WebSocket **Tag Bus** and an industrial protocol (OPC UA, Modbus, Siemens S7, EtherNet/IP, BACnet).

All drivers inherit from `Driver` in `factoryforge_sidecar/drivers/__init__.py`.

---

## 📝 Step-by-Step Driver Creation

### Step 1: Create the Driver Module

Create `sidecar/factoryforge_sidecar/drivers/custom_driver.py`:

```python
"""Custom industrial protocol driver for FactoryForge."""

from __future__ import annotations
import asyncio
import logging
from typing import Any

from ..tagbus import TagBusClient
from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)


@register("custom-protocol")
class CustomProtocolDriver(Driver):
    """Custom protocol driver implementation."""

    def __init__(self, bus: TagBusClient, host: str = "127.0.0.1", port: int = 502, **config: Any) -> None:
        super().__init__(bus, **config)
        self.host = host
        self.port = port
        self._running = False
        self._table: TagTable | None = None

    async def start(self) -> None:
        """Connect to hardware/software controller."""
        self._running = True
        log.info("Connecting to custom controller at %s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Idempotently close sockets/sessions."""
        self._running = False

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        """Rebuild tag address mapping for a new scene/epoch."""
        self._table = table

    async def push(self, values: dict[str, TagValue]) -> None:
        """Active push of updated simulator input values (sensors) to controller."""
        pass
```

---

### Step 2: Register in `drivers/__init__.py`

Import your driver module in `sidecar/factoryforge_sidecar/drivers/__init__.py`:

```python
from . import mock, modbus_tcp, plcsim_advanced, s7_snap7, custom_driver
```

---

### Step 3: Test Driver via CLI

Run the sidecar with your custom driver:

```bash
python -m factoryforge_sidecar connect --driver custom-protocol
```

---

*FactoryForge — developed by Mahamed Algaroshy (محمد الجروشي).*
