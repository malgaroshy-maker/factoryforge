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


# --- HP-33: who owns the epoch ---

async def test_a_write_during_a_rebuild_does_not_get_the_new_epoch(engine, bus, mock):
    """HP-33's epoch half, reproduced.

    The client adopted the new scene, table and epoch and only then awaited the
    describe hooks. Any driver that had not yet rebuilt was still holding the
    previous epoch's address map -- and a value it read through that map and
    published in the window was stamped with the *new* epoch, so the engine
    accepted it onto whichever tag had inherited the id.

    The blocking hook here is not a contrivance: it is what a second driver
    looks like while the first one's `rebuild` is awaiting the PLC, and what
    any single driver looks like once HP-31 moves hook dispatch off the receive
    loop.
    """
    await mock.set("conveyor.rotate", False)
    await _until(lambda: engine.scene.tags.visible("conveyor.rotate") is False,
                 what="a known starting value")

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_rebuild(scene, epoch, table):
        # Registering a hook replays the describe already in hand, so the first
        # call is that replay and is not what this test is about. Let it
        # through and block on the real one.
        nonlocal calls
        calls += 1
        if calls == 1:
            return
        entered.set()
        await release.wait()

    bus.on_describe(slow_rebuild)
    await _until(lambda: calls == 1, what="the registration replay")
    await engine.send_describe()
    await asyncio.wait_for(entered.wait(), 2)

    try:
        # A poller still working from the old map publishes here.
        await bus.write("conveyor.rotate", True)
        await asyncio.sleep(0.1)          # several flush ticks at 5ms
        assert engine.scene.tags.visible("conveyor.rotate") is False, (
            "a write derived from a map older than the current epoch reached "
            "the engine"
        )
    finally:
        release.set()

    # And once every hook has rebuilt, writes land again.
    await _until(lambda: bus.rebuilt.is_set(), what="the rebuild finishing")
    await bus.write("conveyor.rotate", True)
    await _until(lambda: engine.scene.tags.visible("conveyor.rotate") is True,
                 what="writes resuming after the rebuild")


# --- HP-31: driver I/O must not run on the bus receive loop ---

async def test_updates_coalesce_behind_a_slow_push(engine, bus, mock):
    """HP-31's second claim: a driver that fell behind gets the current state.

    Updates are deltas, so the merge of two of them is the message the engine
    would have sent had it batched them -- and a driver half a second behind a
    PLC wants that, not a queue of history it has to replay. This is the claim
    d3b6145 made and did not check: its test proved a stalled driver does not
    stall anything else, which is the *first* claim.

    The wedge is an event rather than a long sleep. A `push` that takes hundreds
    of milliseconds is exactly a `push` that has not returned yet, and an event
    says so without anybody guessing how long to wait (gotcha 2).
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[dict] = []

    async def slow_push(values):
        seen.append(dict(values))
        if len(seen) == 1:
            entered.set()
            await release.wait()

    bus.on_update(slow_push)
    engine.scene.tags.force("sensor_high.detect", True)
    await asyncio.wait_for(entered.wait(), 2)

    # Two more input changes, on two separate engine ticks, while the hook is
    # wedged. Waiting on the *cache* is what puts a tick between them: it is
    # filled on the receive loop, so it moving proves the frame landed.
    engine.scene.tags.force("sensor_low.detect", True)
    await _until(lambda: bus.read("sensor_low.detect") is True,
                 what="the first frame landing past the stalled driver")
    engine.scene.tags.force("counter.tall", 7)
    await _until(lambda: bus.read("counter.tall") == 7,
                 what="the second frame landing past the stalled driver")

    release.set()
    await _until(lambda: len(seen) >= 2, what="the queued updates reaching the hook")
    assert seen[1] == {"sensor_low.detect": True, "counter.tall": 7}, (
        "two frames behind a busy hook must arrive as one merged delta")

    # And nothing is trailing them: the queue holds current state, not history.
    engine.scene.tags.force("counter.short", 3)
    await _until(lambda: len(seen) >= 3, what="a later update reaching the hook")
    assert seen[2] == {"counter.short": 3}
    assert len(seen) == 3


async def test_a_describe_behind_a_slow_push_stops_the_stale_updates(engine, bus, mock):
    """A describe means the tag set those deltas were against is gone.

    `_queue_describe` already empties the queue for that reason. What it could
    not reach was an update *already being handed out* when the describe
    arrived: the hooks after the slow one went on receiving deltas against a
    scene nobody had any more. Every driver re-derives its map and re-reads the
    PLC inside `rebuild`, so there is nothing in those values worth delivering
    late -- and one of them naming a tag the new scene reuses is the whole
    reason the epoch gate exists.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    later: list[dict] = []
    calls = 0

    async def slow_push(values):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()

    async def behind_it(values):
        later.append(dict(values))

    bus.on_update(slow_push)
    bus.on_update(behind_it)

    engine.scene.tags.force("sensor_high.detect", True)
    await asyncio.wait_for(entered.wait(), 2)

    engine.scene.tags.force("sensor_low.detect", True)
    await _until(lambda: bus.read("sensor_low.detect") is True,
                 what="a second update queueing behind the stalled driver")
    before = bus.epoch
    await engine.send_describe()
    await _until(lambda: bus.epoch > before, what="the describe reaching the client")

    release.set()
    await _until(lambda: bus.rebuilt.is_set(), what="the rebuild finishing")
    assert later == [], (
        f"a hook was handed {later} -- deltas against a tag set the describe "
        f"has already replaced")

    # The hook is not broken, only skipped: the next real change reaches it.
    engine.scene.tags.force("counter.tall", 4)
    await _until(lambda: later == [{"counter.tall": 4}],
                 what="the next update after the rebuild")


async def test_a_release_and_a_re_force_coalesce_in_the_order_they_arrived(
        engine, bus, mock):
    """Coalescing the observe channel has to respect arrival order.

    `forced` and `cleared` were merged independently, so a tag forced, released
    and forced again while a hook was busy arrived in *both* collections -- and
    a hook applying the forced values and then the releases, which is the order
    the engine sends them in and the order the client itself applies them, ends
    up having released a pin that is still in effect. The client's own cache
    never had the bug, because the receive loop applies each frame as it lands;
    only the hooks saw it. Nothing subscribes to `observe` by default, which is
    why it survived HP-31 unnoticed.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[tuple[dict, list]] = []

    async def slow_observe(forced, cleared):
        seen.append((dict(forced), list(cleared)))
        if len(seen) == 1:
            entered.set()
            await release.wait()

    bus.on_observe(slow_observe)

    engine.scene.tags.force("sensor_high.detect", True)
    await asyncio.wait_for(entered.wait(), 2)

    # Released and re-applied while the hook is wedged, each waited for on the
    # cache so the two land on separate ticks -- a force and a release inside
    # one tick correctly net out to nothing at all, both channels being deltas.
    engine.scene.tags.clear_force("sensor_high.detect")
    await _until(lambda: not bus.table.is_forced("sensor_high.detect"),
                 what="the release reaching the client")
    engine.scene.tags.force("sensor_high.detect", True)
    await _until(lambda: bus.table.is_forced("sensor_high.detect"),
                 what="the second force reaching the client")

    release.set()
    await _until(lambda: len(seen) >= 2, what="the coalesced observe reaching the hook")
    forced, cleared = seen[1]
    assert forced.get("sensor_high.detect") is True
    assert "sensor_high.detect" not in cleared, (
        "the tag is currently forced; reporting it as released in the same "
        "breath leaves whoever applies both with the pin gone")


async def test_a_stalled_driver_does_not_stall_the_bus(engine, bus, mock):
    """HP-31. The receive loop awaited every driver hook inline, so one slow
    PLC write held up the next sensor update *and* the next scene description
    -- for everything, not just for the driver that was slow.
    """
    stuck = asyncio.Event()
    release = asyncio.Event()

    async def slow_push(values):
        stuck.set()
        await release.wait()

    bus.on_update(slow_push)
    try:
        engine.scene.tags.force("sensor_high.detect", True)
        await asyncio.wait_for(stuck.wait(), 2)

        # The driver is now wedged. Everything else must carry on: a second
        # sensor change has to reach the table...
        engine.scene.tags.force("sensor_low.detect", True)
        await _until(lambda: bus.read("sensor_low.detect") is True,
                     what="a sensor update arriving past a stalled driver")

        # ...and so does a scene description.
        before = bus.epoch
        await engine.send_describe()
        await _until(lambda: bus.epoch > before,
                     what="a describe arriving past a stalled driver")
    finally:
        release.set()
