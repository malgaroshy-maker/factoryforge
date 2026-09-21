"""Tag bus round-trip: the integration this whole milestone exists to de-risk."""

from __future__ import annotations

import asyncio

import pytest

from factoryforge_sidecar.tags import Tag, TagError, TagTable


# --- tag model ---

def test_kind_and_type_are_validated():
    with pytest.raises(TagError):
        Tag("x", "X", "bit", "sideways")       # type: ignore[arg-type]
    with pytest.raises(TagError):
        Tag("x", "X", "quantum", "input")      # type: ignore[arg-type]


def test_bool_is_not_accepted_as_int():
    """bool subclasses int in Python; silently coercing would hide a real bug."""
    tag = Tag("c", "Counter", "int", "input")
    with pytest.raises(TagError):
        tag.coerce(True)


def test_float_epsilon_suppresses_jitter():
    tag = Tag("l", "Level", "float", "input", 1.0)
    assert not tag.differs(1.0 + 1e-9)
    assert tag.differs(1.5)


def test_forcing_pins_the_visible_value():
    table = TagTable([Tag("s", "Sensor", "bit", "input")])
    table.force("s", True)
    assert table.visible("s") is True
    # A simulator write is absorbed, not observed...
    assert table.set("s", False) is False
    assert table.visible("s") is True
    # ...but is revealed once the force is cleared.
    table.clear_force("s")
    assert table.visible("s") is False


# --- round-trip ---

async def test_engine_describes_scene_on_connect(mock):
    table = await mock.ready()
    assert "conveyor.rotate" in table
    assert table["conveyor.rotate"].kind == "output"
    assert table["sensor_low.detect"].kind == "input"


async def test_write_reaches_the_engine(engine, mock):
    await mock.set("conveyor.rotate", True)
    await asyncio.sleep(0.1)
    assert engine.scene.tags.visible("conveyor.rotate") is True


async def test_sensor_change_reaches_the_sidecar(engine, mock):
    """Engine raises an input; the sidecar must observe it."""
    from scene import SENSOR_LOW_POS, SHORT_HEIGHT, Box

    engine.scene.boxes.append(Box(height=SHORT_HEIGHT, position=SENSOR_LOW_POS))
    await mock.wait_for("sensor_low.detect", True, timeout=2)


async def test_writing_an_input_tag_is_rejected(bus, mock):
    """Direction discipline: only force() may override a simulator-owned input."""
    with pytest.raises(ValueError, match="force"):
        await bus.write("sensor_low.detect", True)


async def test_force_overrides_a_simulator_input(engine, mock):
    await mock.bus.force({"sensor_high.detect": True})
    await mock.wait_for("sensor_high.detect", True, timeout=2)
    assert engine.scene.tags.is_forced("sensor_high.detect")

    await mock.bus.force(clear=["sensor_high.detect"])
    await mock.wait_for("sensor_high.detect", False, timeout=2)


async def test_updates_are_delta_only(engine, mock):
    """An idle scene must produce no traffic at all."""
    await asyncio.sleep(0.05)
    before = len(mock.history)
    await asyncio.sleep(0.15)
    assert len(mock.history) == before


async def test_bus_notices_when_the_engine_drops(engine, bus):
    """FF-03: before this, closing the engine left the sidecar's runner task
    quietly completing with nothing anywhere saying the connection was gone —
    the worst failure shape for a simulator, indistinguishable from a line
    that legitimately stopped. Full reconnect-after-restart is a Phase 6
    end-to-end check (test_plan.py); this is the unit-level half: the drop
    must be detected and reported, not silently absorbed.
    """
    disconnects = 0

    async def on_disconnect():
        nonlocal disconnects
        disconnects += 1

    bus.on_disconnect(on_disconnect)
    assert bus.connected.is_set()

    await engine.stop()

    for _ in range(100):
        if not bus.connected.is_set():
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("bus never noticed the engine going away")

    assert disconnects == 1


async def test_stale_epoch_writes_are_dropped(engine, bus, mock):
    await mock.set("conveyor.rotate", True)
    await asyncio.sleep(0.05)

    # Simulate a scene reload landing between a write being queued and delivered.
    stale_epoch = bus.epoch
    await engine.send_describe()
    await asyncio.sleep(0.05)

    from factoryforge_sidecar import protocol as proto
    await bus._send(proto.write(stale_epoch, {"conveyor.rotate": False}))
    await asyncio.sleep(0.1)

    assert engine.scene.tags.visible("conveyor.rotate") is True


# --- HP-13: a forced output must read as forced from the sidecar ---

async def _until(predicate, timeout: float = 2.0, what: str = "condition"):
    """Wait until *predicate* holds, against a real deadline.

    Gotcha 2: on Windows an `asyncio.sleep` under ~15.6 ms returns immediately,
    so counting iterations of a short sleep measures nothing at all. The tick
    here is deliberately above that floor and the deadline is read off the
    loop's own clock, so this waits for real time to pass rather than for a
    number of laps.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if predicate():
            return
        if loop.time() >= deadline:
            raise AssertionError(f"{what} not reached within {timeout}s")
        await asyncio.sleep(0.02)


async def test_forcing_an_output_is_visible_to_the_sidecar(engine, bus, mock):
    """HP-13. The engine updates *inputs* only, and the sidecar fills its own
    outputs in optimistically from what it wrote -- so a motor forced off while
    the PLC commands it on used to read as on, which defeats the exact
    diagnostic forcing exists for.
    """
    await mock.set("conveyor.rotate", True)
    await _until(lambda: engine.scene.tags.visible("conveyor.rotate") is True,
                 what="the engine accepting the write")
    assert bus.read("conveyor.rotate") is True

    engine.scene.tags.force("conveyor.rotate", False)
    await _until(lambda: bus.read("conveyor.rotate") is False,
                 what="the sidecar observing the force")
    assert bus.table.is_forced("conveyor.rotate")

    # And it survives the next local write. The client reflects its own output
    # writes locally; that must not paper over a force in effect.
    await mock.set("conveyor.rotate", True)
    await _until(lambda: engine.scene.tags.value("conveyor.rotate") is True,
                 what="the engine storing the write underneath the force")
    assert bus.read("conveyor.rotate") is False

    engine.scene.tags.clear_force("conveyor.rotate")
    await _until(lambda: not bus.table.is_forced("conveyor.rotate"),
                 what="the sidecar observing the release")
    assert bus.read("conveyor.rotate") is True


async def test_describe_carries_forced_state(engine, bus, mock):
    """The `forced` flag docs/tag-bus.md puts in `describe` was parsed and
    thrown away, so a sidecar connecting to an already-forced scene had no way
    to know."""
    engine.scene.tags.force("sensor_high.detect", True)
    await engine.send_describe()
    await _until(lambda: bus.table.is_forced("sensor_high.detect"),
                 what="the forced flag surviving describe")
    assert bus.read("sensor_high.detect") is True
