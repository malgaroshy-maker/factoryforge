"""Drive the Godot C# engine with the unchanged Python sidecar.

This is the M2 parity check: the same TagBusClient and the same driver stack
that talk to harness/engine_stub.py must talk to the Godot engine with no
changes at all. If this passes, the engine can be swapped in underneath the
drivers without touching them.

    godot --headless --path engine/ -- --deterministic --duration=60 &
    python tools/drive_engine.py

--deterministic is required. Without it the engine runs the rigid-body scene,
which is Jolt solving real contacts and cannot reproduce exact counts, so the
tall=5 short=5 assertion below is meaningless against it.
"""
from __future__ import annotations

import os
import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))
logging.disable(logging.WARNING)

from factoryforge_sidecar import drivers          # noqa: E402
from factoryforge_sidecar.tagbus import TagBusClient  # noqa: E402

EMIT_HALF_PERIOD = 1.5
PUSH_DELAY = 0.9
PUSH_HOLD = 0.5
RUN_FOR = 35.0


#: The engine's tag bus. FF_BUS_URL lets this run against an engine
#: started with --bus-port=N, so two runs can coexist on one machine.
BUS_URL = os.environ.get("FF_BUS_URL", "ws://127.0.0.1:7411/tagbus")


async def main() -> int:
    bus = TagBusClient(BUS_URL)
    runner = asyncio.create_task(bus.run())
    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("could not connect — is the engine running?")
        return 1

    mock = drivers.create("mock", bus)
    await mock.start()
    table = await mock.ready(timeout=10)
    print(f"connected to scene {bus.scene!r} epoch {bus.epoch}, {len(table)} tags")

    await mock.set("conveyor.rotate", True)
    await mock.set("stack_light.green", True)

    emit_flag = False
    next_toggle = time.perf_counter()
    high_mem = False
    extend_at: float | None = None
    retract_at: float | None = None
    start = time.perf_counter()

    while time.perf_counter() - start < RUN_FOR:
        await asyncio.sleep(0.02)
        now = time.perf_counter()

        # Square wave emitter, same shape as Sorting.scl v0.3.
        if now >= next_toggle:
            emit_flag = not emit_flag
            next_toggle = now + EMIT_HALF_PERIOD
            await mock.set("emitter.emit", emit_flag)

        # Latch on the rising edge; the box clears the sensor long before it
        # reaches the pusher.
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

    tall = mock.get("counter.tall")
    short = mock.get("counter.short")
    print(f"\nRESULT tall={tall} short={short}")
    ok = tall > 0 and short > 0
    if ok:
        print("PASS — the Python sidecar drives the Godot engine")
    else:
        print("FAIL — nothing sorted")
        print("       was the engine started with --deterministic, and did it "
              "stay up for the whole 35 s run?")

    await mock.stop()
    runner.cancel()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
