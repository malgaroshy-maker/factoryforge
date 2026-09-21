"""Force an int tag and a float tag and confirm each value reaches the bus.

    python tools/check_force_types.py

Requires an engine with at least one int **input** tag and one float
**input** tag -- the light-curtain template has both
(`tall_count.count`/`short_count.count` int, `height_gauge.height` float):

    godot --headless --path engine -- --scene=res://templates/light_curtain_sorting.json

Deliberately targets input tags, not output: TagBusServer.SendUpdates only
ever echoes TagKind.Input tags ("the client already knows its own output
values"), so forcing an output would never produce an `update` regardless of
whether Force itself works -- the first version of this script picked an
output tag and failed for exactly that (correct) reason, not because Force
was broken.

Regression check for UX-35: the Tag Inspector's Force button used to only
handle bit tags (§2.8) -- pressing it on an int or float tag did nothing, no
value forced, no message, silently. UX-35 fixed the UI; this checks the wire
protocol itself carries a non-bool forced value correctly end to end,
independent of any UI, the way check_force_while_paused.py already does for
bits and check_protocol.py does for message shape.
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


async def force_and_confirm(bus: TagBusClient, tag_id: str, value) -> str | None:
    """Force *tag_id* to *value* and wait for it to come back on an
    `update`. Returns None on success, or a problem string."""
    seen = asyncio.Event()
    got: dict = {}

    async def on_update(values: dict) -> None:
        if tag_id in values:
            got["value"] = values[tag_id]
            seen.set()

    bus.on_update(on_update)
    await bus.force({tag_id: value})

    try:
        await asyncio.wait_for(seen.wait(), timeout=5)
    except asyncio.TimeoutError:
        return f"{tag_id}: forced to {value!r} but no update arrived within 5s"

    reported = got["value"]
    close_enough = (isinstance(value, float) and abs(reported - value) < 1e-6) or reported == value
    if not close_enough:
        return f"{tag_id}: forced to {value!r}, update reported {reported!r}"
    return None


async def main() -> int:
    bus = TagBusClient(BUS_URL)
    runner = asyncio.create_task(bus.run())
    try:
        await asyncio.wait_for(bus.connected.wait(), timeout=10)
        for _ in range(200):
            if bus.scene is not None and len(bus.table) > 0:
                break
            await asyncio.sleep(0.05)

        # Input, not output: SendUpdates only ever echoes input tags, so an
        # output would never produce an `update` regardless of whether Force
        # itself works (see the module docstring).
        int_target = next((t.id for t in bus.table if t.type == "int" and t.kind == "input"), None)
        float_target = next((t.id for t in bus.table if t.type == "float" and t.kind == "input"), None)

        if int_target is None:
            print("RESULT no int-typed input tag on this scene -- load a scene that has one")
            return 1
        if float_target is None:
            print("RESULT no float-typed input tag on this scene -- load a scene that has one "
                  "(the light-curtain template has both)")
            return 1

        problems = []
        for problem in await asyncio.gather(
            force_and_confirm(bus, int_target, 42),
            force_and_confirm(bus, float_target, 12.5),
        ):
            if problem:
                problems.append(problem)

        if problems:
            print("RESULT " + "; ".join(problems))
            return 1
        print(f"RESULT OK ({int_target}=42, {float_target}=12.5)")
        return 0
    finally:
        runner.cancel()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
