"""Drive the running Godot 3D engine live for testing."""
import os
import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))
logging.disable(logging.WARNING)

from factoryforge_sidecar import drivers
from factoryforge_sidecar.tagbus import TagBusClient

EMIT_HALF_PERIOD = 1.5
PUSH_DELAY = 0.9
PUSH_HOLD = 0.5
RUN_FOR = 300.0  # 5 minutes continuous live test

#: The engine's tag bus. FF_BUS_URL lets this run against an engine
#: started with --bus-port=N, so two runs can coexist on one machine.
BUS_URL = os.environ.get("FF_BUS_URL", "ws://127.0.0.1:7411/tagbus")


async def main():
    bus = TagBusClient(BUS_URL)
    runner = asyncio.create_task(bus.run())
    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("Could not connect — is Godot engine running?")
        return 1

    mock = drivers.create("mock", bus)
    await mock.start()
    table = await mock.ready(timeout=10)
    print(f"\n[LIVE DEMO STARTED]")
    print(f"Connected to scene '{bus.scene}' epoch {bus.epoch}, {len(table)} tags")
    print("Controls in Godot Window:")
    print("  - Drag Left Mouse: Orbit camera around factory floor")
    print("  - Scroll Mouse Wheel: Zoom in / out")
    print("  - Drag Middle Mouse: Pan camera")
    print("  - Press WASD / Space / Shift / Right-Click Drag: Fly Camera")
    print("\nRunning for 5 minutes... Press Ctrl+C or close Godot to end.\n")

    await mock.set("conveyor.rotate", True)
    await mock.set("stack_light.green", True)

    emit_flag = False
    next_toggle = time.perf_counter()
    high_mem = False
    extend_at = None
    retract_at = None
    start = time.perf_counter()

    try:
        while time.perf_counter() - start < RUN_FOR:
            await asyncio.sleep(0.02)
            now = time.perf_counter()

            if now >= next_toggle:
                emit_flag = not emit_flag
                next_toggle = now + EMIT_HALF_PERIOD
                await mock.set("emitter.emit", emit_flag)

            high = bool(mock.get("sensor_high.detect"))
            if high and not high_mem:
                extend_at = now + PUSH_DELAY
            high_mem = high

            if extend_at is not None and now >= extend_at:
                await mock.set("pusher.extend", True)
                retract_at = extend_at + PUSH_HOLD
                extend_at = None
            if retract_at is not None and now >= retract_at:
                await mock.set("pusher.extend", False)
                retract_at = None

    except asyncio.CancelledError:
        pass
    finally:
        print(f"FINAL RESULT: tall={mock.get('counter.tall')} short={mock.get('counter.short')}")
        await mock.stop()
        runner.cancel()

if __name__ == "__main__":
    asyncio.run(main())
