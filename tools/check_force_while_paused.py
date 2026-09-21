"""Force an input tag while the simulation is paused and confirm it reaches the bus.

    python tools/check_force_while_paused.py

Requires an engine started with --paused. Regression check for FF-14: pausing
was implemented as Engine.TimeScale = 0, which also zeroed the fixed-timestep
accumulator, so SendUpdates() only ran when steps > 0 -- meaning a forced
sensor value sat in the tag table but never reached a connected driver until
the sim was un-paused. That is exactly the workflow pause exists for: freeze
the line, force a sensor, read the PLC's response.
"""
from __future__ import annotations

import os
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))

from factoryforge_sidecar.tagbus import TagBusClient  # noqa: E402


#: The engine's tag bus. FF_BUS_URL lets this run against an engine
#: started with --bus-port=N, so two runs can coexist on one machine.
BUS_URL = os.environ.get("FF_BUS_URL", "ws://127.0.0.1:7411/tagbus")


async def main() -> int:
    bus = TagBusClient(BUS_URL)
    runner = asyncio.create_task(bus.run())
    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=10)
        # Wait for the real describe, not just the socket -- forcing against
        # an empty table finds nothing to force.
        for _ in range(200):
            if bus.scene is not None and len(bus.table) > 0:
                break
            await asyncio.sleep(0.05)

        target = next((t.id for t in bus.table if t.kind == "input" and t.type == "bit"), None)
        if target is None:
            print("RESULT no bit-typed input tag to force")
            return 1

        seen = asyncio.Event()

        async def on_update(values: dict) -> None:
            if target in values:
                seen.set()

        bus.on_update(on_update)
        await bus.force({target: not bus.read(target)})

        try:
            await asyncio.wait_for(seen.wait(), timeout=5)
        except asyncio.TimeoutError:
            print(f"RESULT no update for {target} within 5s -- forced value never reached the bus")
            return 1

        print(f"RESULT update received for {target}")
        return 0
    finally:
        runner.cancel()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
