"""OPC UA drivers, tested with no Siemens software involved.

The client driver is exercised against a local `asyncua` server standing in for
a PLC; the server driver is exercised by a real `asyncua` client standing in for
Node-RED or a SCADA package. CI must never need TIA Portal or PLCSIM.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
import pytest_asyncio
from asyncua import Client, Server, ua

from factoryforge_sidecar import drivers

def _free_port() -> int:
    """A port nothing else is on, so two suites can share a machine.

    These endpoints were 48400 and 48410 flat. Two pytest runs at once -- one
    checking a branch while another checks master, or a developer's run
    overlapping CI's -- then fought over the same two sockets, and the loser
    failed in whichever fake_plc test happened to be first. That reads as a
    driver bug and is not one.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PLC_ENDPOINT = f"opc.tcp://127.0.0.1:{_free_port()}/fakeplc/"
SIM_ENDPOINT = f"opc.tcp://127.0.0.1:{_free_port()}/factoryforge/"

#: The four PLC-written tags plus the sensors we care about.
OUTPUTS = ["conveyor.rotate", "emitter.emit", "pusher.extend", "stack_light.green"]
#: `pusher.retracted` is deliberately included: it starts True, so it is the one
#: tag that can prove initial state is pushed rather than left at a default.
INPUTS = ["sensor_low.detect", "sensor_high.detect", "counter.tall",
          "pusher.retracted"]


async def _settle(check, timeout: float = 5.0, interval: float = 0.05):
    """Poll until *check* returns something truthy.

    `interval` stays above Windows' 15.6ms asyncio clock resolution -- below it,
    sleeps return immediately and the loop spins without real time passing.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = check()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(interval)
    return None


# --- client driver, against a fake PLC ---

@pytest_asyncio.fixture
async def fake_plc():
    """A local OPC UA server standing in for an S7-1500."""
    async for plc in _fake_plc(ua.VariantType.Int32):
        yield plc


@pytest_asyncio.fixture
async def fake_s7_int_plc():
    """The same PLC with its counter declared `Int` -- Int16, the type of a
    real `%IW` analog channel -- rather than `DInt` (IP-28)."""
    async for plc in _fake_plc(ua.VariantType.Int16):
        yield plc


async def _fake_plc(int_variant: ua.VariantType):
    server = Server()
    await server.init()
    server.set_endpoint(PLC_ENDPOINT)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace("urn:fakeplc")
    folder = await server.nodes.objects.add_folder(ua.NodeId("plc", idx), "PLC")

    nodes = {}
    for tag_id in OUTPUTS:
        node = await folder.add_variable(
            ua.NodeId(tag_id, idx), tag_id, False, ua.VariantType.Boolean)
        await node.set_writable()
        nodes[tag_id] = node
    for tag_id in INPUTS:
        variant = (int_variant if tag_id.startswith("counter")
                   else ua.VariantType.Boolean)
        initial = False if variant is ua.VariantType.Boolean else 0
        node = await folder.add_variable(ua.NodeId(tag_id, idx), tag_id, initial, variant)
        await node.set_writable()
        nodes[tag_id] = node

    await server.start()
    try:
        yield server, idx, nodes
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def opcua_client(bus, fake_plc):
    _, idx, _ = fake_plc
    mapping = {t: f"ns={idx};s={t}" for t in OUTPUTS + INPUTS}
    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT,
                            mapping=mapping, publish_interval=20)
    await driver.start()
    assert await _settle(lambda: driver.connected.is_set()), "driver never connected"
    # Every mapped tag, not merely the first one to land. `connected` is set
    # before _bind is even called (opcua_client.py:171 vs :174), so waiting on
    # the event alone hands the test a bind that has not started; this used to
    # wait on "_nodes is non-empty", which handed it one still in progress and
    # failed roughly one run in seven on whichever tag had not arrived yet.
    # _bind now swaps a finished map in, so the count is exact rather than a
    # sampling of a map being filled -- and it still says how far it got.
    assert await _settle(lambda: len(driver._nodes) == len(mapping)), \
        f"only {len(driver._nodes)}/{len(mapping)} tags bound"
    try:
        yield driver
    finally:
        await driver.stop()


async def test_client_binds_mapped_tags(opcua_client):
    for tag_id in OUTPUTS:
        assert tag_id in opcua_client._nodes


async def test_plc_write_reaches_the_engine(engine, fake_plc, opcua_client):
    """PLC writes a coil-equivalent; the simulation must see it."""
    _, _, nodes = fake_plc
    await nodes["conveyor.rotate"].write_value(
        ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))

    assert await _settle(lambda: engine.scene.tags.visible("conveyor.rotate")), \
        "PLC write never reached the engine"


async def test_sensor_reaches_the_plc(engine, fake_plc, opcua_client):
    """Engine raises a sensor; the driver must write it into the PLC."""
    from scene import SENSOR_LOW_POS, SHORT_HEIGHT, Box

    _, _, nodes = fake_plc
    engine.scene.boxes.append(Box(height=SHORT_HEIGHT, position=SENSOR_LOW_POS))

    assert await _settle(lambda: nodes["sensor_low.detect"].read_value()), \
        "sensor never reached the PLC"


async def test_initial_sensor_state_is_pushed_on_bind(fake_plc, opcua_client):
    """The PLC must start from real scene state, not from node defaults.

    `pusher.retracted` starts True in the scene but False on the fake PLC, so
    only an explicit push on bind can make them agree.
    """
    _, _, nodes = fake_plc
    assert "pusher.retracted" in opcua_client._nodes
    assert await _settle(lambda: nodes["pusher.retracted"].read_value()), \
        "initial state was never pushed to the PLC"


async def _bound_client(bus, idx, **kwargs):
    mapping = {t: f"ns={idx};s={t}" for t in OUTPUTS + INPUTS}
    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT, mapping=mapping, **kwargs)
    await driver.start()
    assert await _settle(lambda: len(driver._nodes) == len(mapping)), "never bound"
    # The bind seeds every input from the scene; wait until it has, so the
    # pushes below are not raced by it.
    assert await _settle(lambda: set(INPUTS) <= set(driver._node_types)), "types never read"
    return driver


async def test_a_raw_count_lands_in_an_int16_node(bus, fake_s7_int_plc):
    """IP-28. A real `%IW` analog channel is an S7 `Int`, an Int16 node over
    OPC UA, and an S7 refuses an Int32 written into it (BadTypeMismatch) --
    as the asyncua server does. The driver used to write every `int` tag as
    Int32, so a raw count could only ever land in a `DInt`. It now writes the
    node's own DataType, read at bind.

    Every S7 analog code is a 16-bit value, so all of them must land: full
    scale, 7FFFh (overflow, wire break) and 8000h (underflow).
    """
    _, idx, nodes = fake_s7_int_plc
    driver = await _bound_client(bus, idx)
    try:
        assert driver._node_types["counter.tall"] is ua.VariantType.Int16

        for count in (27648, 32767, -32768, -4864, 0, 13824):
            await driver.push({"counter.tall": count})
            assert await _settle(lambda: _equals(nodes["counter.tall"], count), timeout=2.0), \
                f"{count} never reached the Int16 node"
            value = await nodes["counter.tall"].read_data_value()
            assert value.Value.VariantType is ua.VariantType.Int16
        assert "counter.tall" not in driver._unacked
    finally:
        await driver.stop()


async def test_a_count_an_int_cannot_hold_is_refused_and_said_so(monkeypatch, bus, fake_s7_int_plc):
    """40000 is not an S7 `Int`. Wrapping it would hand the PLC -25536; the
    driver refuses it, says why on the bus, once, and does not queue it for a
    retry that could only ever be refused again. The next value that fits
    goes through as normal."""
    _, idx, nodes = fake_s7_int_plc
    driver = await _bound_client(bus, idx, input_retry_interval=0.05)
    reports: list[tuple[str, str]] = []
    real_report = driver._report

    async def spy(level, code, message):
        reports.append((code, message))
        await real_report(level, code, message)

    monkeypatch.setattr(driver, "_report", spy)
    try:
        await driver.push({"counter.tall": 1234})
        assert await _settle(lambda: _equals(nodes["counter.tall"], 1234)), "1234 never landed"

        await driver.push({"counter.tall": 40000})
        await driver.push({"counter.tall": 40001})
        assert "counter.tall" not in driver._unacked, "an unwritable value was queued for retry"
        await asyncio.sleep(0.3)          # several reconciler periods
        assert await nodes["counter.tall"].read_value() == 1234, \
            "an out-of-range count was written anyway"
        refused = [m for c, m in reports if c == "value_out_of_range"]
        assert len(refused) == 1, f"reported {len(refused)} times: {refused}"
        assert "40000" in refused[0] and "Int16" in refused[0] and "counter.tall" in refused[0]

        await driver.push({"counter.tall": 27648})
        assert await _settle(lambda: _equals(nodes["counter.tall"], 27648)), \
            "a value that fits was not written after a refusal"
    finally:
        await driver.stop()


async def _equals(node, want) -> bool:
    return await node.read_value() == want


def test_encode_for_node_matches_the_node_not_the_tag():
    """The encoding rules without a server: the node's type wins where the
    value fits it, an unknown type falls back to the pre-IP-28 variant, and
    anything that would need rounding or wrapping is refused."""
    from factoryforge_sidecar.drivers.opcua_client import Unwritable, encode_for_node

    VT = ua.VariantType
    assert encode_for_node("int", 27648, VT.Int16).VariantType is VT.Int16
    assert encode_for_node("int", 27648, VT.Int32).VariantType is VT.Int32
    assert encode_for_node("int", 5, VT.Double) == ua.Variant(5.0, VT.Double)
    assert encode_for_node("float", 1.5, VT.Double) == ua.Variant(1.5, VT.Double)
    assert encode_for_node("bit", True, VT.Boolean) == ua.Variant(True, VT.Boolean)
    # Unknown DataType: exactly what every write was before.
    assert encode_for_node("int", 7, None) == ua.Variant(7, VT.Int32)
    assert encode_for_node("float", 0.5, None) == ua.Variant(0.5, VT.Float)

    for tag_type, value, node_type, code in [
        ("int", 32768, VT.Int16, "value_out_of_range"),
        ("int", -32769, VT.Int16, "value_out_of_range"),
        ("int", -1, VT.UInt16, "value_out_of_range"),
        ("float", 42.5, VT.Int16, "node_type_mismatch"),
        ("bit", True, VT.Int16, "node_type_mismatch"),
        ("int", 1, VT.String, "node_type_mismatch"),
    ]:
        with pytest.raises(Unwritable) as refused:
            encode_for_node(tag_type, value, node_type)
        assert refused.value.code == code, (tag_type, value, node_type)


async def test_unmapped_tags_are_reported_not_fatal(bus, fake_plc):
    """A partial mapping must warn and keep running, not crash."""
    _, idx, _ = fake_plc
    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT,
                            mapping={"conveyor.rotate": f"ns={idx};s=conveyor.rotate"})
    await driver.start()
    try:
        assert await _settle(lambda: driver._nodes)
        assert "conveyor.rotate" in driver._nodes
        assert "pusher.extend" not in driver._nodes
    finally:
        await driver.stop()


async def test_a_scene_edit_does_not_drop_a_pending_retry(monkeypatch, bus, fake_plc):
    """A rebuild must not discard a write that is still waiting to land.

    This is HP-50's own failure arriving through a different door. Every
    placement, deletion and rename in the editor raises TagsChanged, which
    republishes the tag set and calls rebuild() -- with the connection still
    up, so `_unacked` is not cleared the way a disconnect clears it. _bind used
    to empty `self._nodes` and refill it one network round trip at a time, and
    the reconciler read a tag's absence from that half-built map as "this tag
    is unmapped, stop retrying it".

    The sampling loop is the test: the drop is only visible *during* the bind,
    because seeding at the end of the same bind would put an entry back and
    hide it.
    """
    from asyncua import Node

    _, idx, _ = fake_plc
    mapping = {t: f"ns={idx};s={t}" for t in OUTPUTS + INPUTS}

    allowed = asyncio.Event()
    written: list[str] = []
    real_write = Node.write_value

    async def gated(self, *args, **kwargs):
        written.append(self.nodeid.Identifier)
        if self.nodeid.Identifier == "sensor_high.detect" and not allowed.is_set():
            raise RuntimeError("BadCommunicationError")
        return await real_write(self, *args, **kwargs)

    monkeypatch.setattr(Node, "write_value", gated)

    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT, mapping=mapping,
                            input_retry_interval=0.05)
    await driver.start()
    try:
        assert await _settle(lambda: driver.connected.is_set())
        assert await _settle(lambda: set(INPUTS) <= set(written))

        await driver.push({"sensor_high.detect": True})
        assert await _settle(lambda: "sensor_high.detect" in driver._unacked), \
            "the refused write was not held in the first place"

        # Give each node resolution a visible round trip, so the rebuild has a
        # window the reconciler can wake up inside. A real network supplies
        # this; a loopback server does not.
        real_browse = Node.read_browse_name

        async def slow_browse(self):
            await asyncio.sleep(0.05)
            return await real_browse(self)

        monkeypatch.setattr(Node, "read_browse_name", slow_browse)

        attempts = written.count("sensor_high.detect")
        rebuild = asyncio.create_task(driver.rebuild("sorting", 2, bus.table))
        held = True
        while not rebuild.done():
            if "sensor_high.detect" not in driver._unacked:
                held = False
                break
            await asyncio.sleep(0.02)
        await rebuild

        assert written.count("sensor_high.detect") > attempts, \
            "the reconciler never ran during the rebuild, so nothing was proven"
        assert held, "a scene edit discarded a write that was still pending"

        # And it is still a live entry afterwards, not a corpse: opening the
        # gate drains it.
        allowed.set()
        assert await _settle(lambda: "sensor_high.detect" not in driver._unacked), \
            "the retained write was never retried to completion"
    finally:
        await driver.stop()


async def test_a_transient_read_error_does_not_strand_the_poller(
        monkeypatch, engine, fake_plc, opcua_client):
    """A failed read used to end `_poll_loop` for the rest of the run.

    The reasoning was that the connect loop owns reconnection -- and it does,
    but all it checks is whether the session is alive, and a session that
    survived one failed read looks perfectly healthy to it. So one timeout
    against a busy CPU took the driver silent, on a connection still reporting
    good, with a scene that went on running and a PLC that went on being
    ignored.
    """
    from asyncua import Client as AsyncuaClient

    real_read = AsyncuaClient.read_values
    refused = []

    async def flaky(self, nodes):
        if len(refused) < 3:
            refused.append(1)
            raise RuntimeError("BadTimeout")
        return await real_read(self, nodes)

    monkeypatch.setattr(AsyncuaClient, "read_values", flaky)
    assert await _settle(lambda: len(refused) >= 3), "the poller stopped on the first error"

    _, _, nodes = fake_plc
    await nodes["conveyor.rotate"].write_value(
        ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
    assert await _settle(lambda: engine.scene.tags.visible("conveyor.rotate")), \
        "polling never resumed, and nothing said so"


async def test_a_dropped_input_write_is_retried(monkeypatch, bus, fake_plc):
    """A failed write used to be logged and discarded, which is not something
    the system recovers from on its own: the engine publishes *deltas*, so a
    sensor whose write fails and which then holds steady is never sent again.
    The PLC keeps the wrong value indefinitely, on a connection that reports
    healthy.

    So this pushes the value exactly once and then leaves the driver alone.
    Nothing else will ever send it; only the reconciler can put it right.

    Two things are deliberately not left to the clock. The refusal is a gate,
    not a count of attempts -- `_bind` seeds every input node at the end of a
    connect, this node included, so counting refusals lets whichever write
    happens to land first decide the arithmetic. And the test waits until that
    seeding has touched every input before it pushes anything, because
    `connected` is set *before* `_bind` runs (opcua_client.py:171 vs :174), so
    a bind can still be in flight when the connection looks established. Left
    unguarded, the seeded `False` overwrites the pushed `True` in `_unacked`
    and the reconciler faithfully converges on the wrong value.
    """
    from asyncua import Node

    _, idx, nodes = fake_plc
    mapping = {t: f"ns={idx};s={t}" for t in OUTPUTS + INPUTS}

    allowed = asyncio.Event()
    written: list[str] = []
    real_write = Node.write_value

    async def gated(self, *args, **kwargs):
        written.append(self.nodeid.Identifier)
        if self.nodeid.Identifier == "sensor_high.detect" and not allowed.is_set():
            raise RuntimeError("BadCommunicationError")
        return await real_write(self, *args, **kwargs)

    # Installed before the driver exists, so its own seeding is refused too and
    # cannot race the push below.
    monkeypatch.setattr(Node, "write_value", gated)

    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT, mapping=mapping,
                            input_retry_interval=0.1)
    await driver.start()
    try:
        assert await _settle(lambda: driver.connected.is_set()), "never connected"
        assert await _settle(lambda: set(INPUTS) <= set(written)), \
            "bind-time seeding never finished, so the push below would race it"

        await driver.push({"sensor_high.detect": True})
        assert await _settle(lambda: driver._unacked.get("sensor_high.detect") is True), \
            "the refused write was dropped instead of kept"
        assert await nodes["sensor_high.detect"].read_value() is False

        # Nothing else will ever send this value. Open the gate and the only
        # thing that can put the PLC right is the reconciler.
        allowed.set()
        assert await _settle(lambda: nodes["sensor_high.detect"].read_value()), \
            "a dropped input write was never retried; the PLC kept the wrong value"
        assert await _settle(lambda: "sensor_high.detect" not in driver._unacked), \
            "an acknowledged write is still queued for retry"
    finally:
        await driver.stop()


async def test_rebuilding_does_not_accumulate_subscriptions(bus, fake_plc):
    """Every scene edit republishes the description, and each rebuild created
    a subscription without deleting the one it replaced.

    Counted on the *server*, because that is where the resource actually runs
    out: against a real S7 these are scarce (gotcha 7, where one extra client
    session was enough to destabilise it), and the nine stale ones were still
    delivering into a handler whose node->tag map had moved on.
    """
    server, idx, _ = fake_plc
    live = server.iserver.subscription_service.subscriptions
    before = len(live)

    mapping = {t: f"ns={idx};s={t}" for t in OUTPUTS + INPUTS}
    driver = drivers.create("opcua-client", bus, url=PLC_ENDPOINT, mapping=mapping,
                            mode="subscribe", publish_interval=100)
    await driver.start()
    try:
        assert await _settle(lambda: driver._subscription is not None), \
            "no subscription was ever created"
        assert len(live) == before + 1

        for epoch in range(2, 6):
            await driver.rebuild("sorting", epoch, bus.table)
        assert await _settle(lambda: len(live) == before + 1), \
            f"{len(live) - before} subscriptions are live after five binds, not 1"
    finally:
        await driver.stop()

    # Belt and braces: closing the session tears these down server-side anyway,
    # so this holds with or without _disconnect()'s explicit delete. It is here
    # as a standing invariant, not as proof of that line.
    assert await _settle(lambda: len(live) == before), \
        "stop() left a subscription on the server"


DEAD_ENDPOINT = "opc.tcp://127.0.0.1:48499/nothing-here/"


async def test_missing_plc_does_not_block_startup(bus):
    """If the PLC is off, start() must still return promptly."""
    driver = drivers.create("opcua-client", bus, url=DEAD_ENDPOINT)
    await asyncio.wait_for(driver.start(), timeout=2)
    try:
        assert not driver.connected.is_set()
    finally:
        await driver.stop()


async def test_the_connect_timeout_is_explicit_and_configurable(monkeypatch, bus):
    """AGENTS.md gotcha 8: "asyncua's default 4 s connect timeout is too short
    for a real S7. Use timeout=10." The lesson was learned and written into the
    handoff document, and the code went on constructing `Client(url=...)` with
    no timeout at all -- so a CPU that was merely busy looked like a CPU that
    was not there.
    """
    from factoryforge_sidecar.drivers import opcua_client as mod

    seen: list[dict] = []
    real_client = mod.Client

    class Recording(real_client):
        def __init__(self, url, **kwargs):
            seen.append({"url": url, **kwargs})
            super().__init__(url, **kwargs)

    monkeypatch.setattr(mod, "Client", Recording)

    driver = drivers.create("opcua-client", bus, url=DEAD_ENDPOINT)
    assert driver.timeout == 10.0, "the default must be the one AGENTS.md prescribes"
    await driver.start()
    try:
        assert await _settle(lambda: seen), "no client was ever constructed"
        assert seen[0]["timeout"] == 10.0, "asyncua's 4s default was left in place"
    finally:
        await driver.stop()

    seen.clear()
    # `-o timeout 20` arrives from the CLI as a string.
    slow = drivers.create("opcua-client", bus, url=DEAD_ENDPOINT, timeout="20")
    assert slow.timeout == 20.0
    await slow.start()
    try:
        assert await _settle(lambda: seen)
        assert seen[0]["timeout"] == 20.0, "a configured timeout did not reach asyncua"
    finally:
        await slow.stop()


# --- server driver, driven by a real client ---

@pytest_asyncio.fixture
async def opcua_server(bus):
    driver = drivers.create("opcua-server", bus, endpoint=SIM_ENDPOINT,
                            publish_interval=20)
    await driver.start()
    assert await _settle(lambda: driver._nodes), "server never published its tags"
    try:
        yield driver
    finally:
        await driver.stop()


@pytest_asyncio.fixture
async def scada(opcua_server):
    """A plain OPC UA client, standing in for Node-RED or Ignition."""
    client = Client(url=SIM_ENDPOINT)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def test_server_publishes_every_tag(engine, opcua_server):
    assert len(opcua_server._nodes) == len(engine.scene.tags)


async def test_node_ids_are_derived_from_tag_ids(opcua_server):
    """ns=2;s=<tag_id> -- stable and readable, so no mapping is needed."""
    node = opcua_server._nodes["conveyor.rotate"]
    assert node.nodeid.Identifier == "conveyor.rotate"


async def test_scada_client_write_drives_the_simulation(engine, opcua_server, scada):
    """A Node-RED-style client writes a tag; the simulation must react."""
    idx = opcua_server.idx
    node = scada.get_node(ua.NodeId("conveyor.rotate", idx))
    await node.write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))

    assert await _settle(lambda: engine.scene.tags.visible("conveyor.rotate")), \
        "client write never reached the engine"


async def test_scada_client_reads_a_sensor(engine, opcua_server, scada):
    from scene import SENSOR_LOW_POS, SHORT_HEIGHT, Box

    engine.scene.boxes.append(Box(height=SHORT_HEIGHT, position=SENSOR_LOW_POS))
    node = scada.get_node(ua.NodeId("sensor_low.detect", opcua_server.idx))

    assert await _settle(lambda: node.read_value()), "sensor never reached the client"


async def test_simulator_owned_inputs_are_read_only(opcua_server, scada):
    """A client must not be able to fake a sensor by writing to it."""
    node = scada.get_node(ua.NodeId("sensor_low.detect", opcua_server.idx))
    with pytest.raises(ua.UaStatusCodeError):
        await node.write_value(
            ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))


async def test_bus_disconnect_marks_nodes_bad_quality(opcua_server, scada):
    """FF-03: a client that checks quality (as any serious SCADA package
    does) must see a fault when the sidecar loses the engine, not a
    plausible-looking frozen reading with a Good status code."""
    node = scada.get_node(ua.NodeId("sensor_low.detect", opcua_server.idx))
    assert (await node.read_data_value(raise_on_bad_status=False)).StatusCode.is_good()

    await opcua_server.bus_disconnected()

    assert (await node.read_data_value(raise_on_bad_status=False)).StatusCode.is_bad()
