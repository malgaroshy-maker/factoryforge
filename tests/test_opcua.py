"""OPC UA drivers, tested with no Siemens software involved.

The client driver is exercised against a local `asyncua` server standing in for
a PLC; the server driver is exercised by a real `asyncua` client standing in for
Node-RED or a SCADA package. CI must never need TIA Portal or PLCSIM.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from asyncua import Client, Server, ua

from factoryforge_sidecar import drivers

PLC_ENDPOINT = "opc.tcp://127.0.0.1:48400/fakeplc/"
SIM_ENDPOINT = "opc.tcp://127.0.0.1:48410/factoryforge/"

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
        variant = (ua.VariantType.Int32 if tag_id.startswith("counter")
                   else ua.VariantType.Boolean)
        initial = 0 if variant is ua.VariantType.Int32 else False
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
    assert await _settle(lambda: driver._nodes), "driver never bound its tags"
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


async def test_a_dropped_input_write_is_retried(monkeypatch, fake_plc, opcua_client):
    """A failed write used to be logged and discarded, which is not something
    the system recovers from on its own: the engine publishes *deltas*, so a
    sensor whose write fails and which then holds steady is never sent again.
    The PLC keeps the wrong value indefinitely, on a connection that reports
    healthy.

    So this pushes the value exactly once, fails the first two attempts, and
    then leaves the driver alone. Nothing else will ever send it.
    """
    from asyncua import Node

    _, _, nodes = fake_plc
    real_write = Node.write_value
    refused = []

    async def flaky(self, *args, **kwargs):
        if self.nodeid.Identifier == "sensor_high.detect" and len(refused) < 2:
            refused.append(1)
            raise RuntimeError("BadCommunicationError")
        return await real_write(self, *args, **kwargs)

    monkeypatch.setattr(Node, "write_value", flaky)
    opcua_client.input_retry_interval = 0.2

    await opcua_client.push({"sensor_high.detect": True})
    assert len(refused) == 1, "the write was not attempted once"
    assert await nodes["sensor_high.detect"].read_value() is False
    assert "sensor_high.detect" in opcua_client._unacked, "the value was dropped"

    assert await _settle(lambda: nodes["sensor_high.detect"].read_value(), timeout=10), \
        "a dropped input write was never retried; the PLC kept the wrong value"
    assert "sensor_high.detect" not in opcua_client._unacked, \
        "an acknowledged write is still queued for retry"


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
