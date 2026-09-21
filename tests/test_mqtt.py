"""MQTT driver, exercised by a real broker and a real MQTT controller.

The broker is in this file. That is deliberate and it follows the precedent
`factoryforge_sidecar/modbus/` already set: CI must not need mosquitto
installed, and a test that skips when a service is missing is a test that
silently never runs. It speaks enough MQTT 3.1.1 for a paho client -- connect,
subscribe, publish at QoS 0 and 1, retained messages, wills, keepalive -- and
nothing more.

It binds port 0 and reports what it got (HP-53): two of these run side by side
without knowing about each other.

The acceptance test is at the bottom: a controller that speaks *only* MQTT,
with no idea the tag bus exists, sorts cartons by height. Same shape as the
Modbus one, and the point of milestone M1.5 -- if that passes with the `Driver`
ABC untouched, the abstraction is real.
"""

from __future__ import annotations

import asyncio
import json
import struct

import pytest
import pytest_asyncio

from factoryforge_sidecar import drivers
from factoryforge_sidecar.drivers import mqtt as mqtt_driver
from factoryforge_sidecar.drivers.mqtt import MqttDriver, decode, encode, sanitise
from factoryforge_sidecar.tags import Tag

paho = pytest.importorskip("paho.mqtt.client")


# --- a minimal MQTT 3.1.1 broker ---------------------------------------

CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK = 8, 9, 10, 11
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14


def _encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n:
            byte |= 0x80
        out.append(byte)
        if not n:
            return bytes(out)


def _packet(kind: int, flags: int, body: bytes) -> bytes:
    return bytes([kind << 4 | flags]) + _encode_length(len(body)) + body


def _string(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("!H", data, pos)
    pos += 2
    return data[pos:pos + length].decode("utf-8"), pos + length


def topic_matches(filt: str, topic: str) -> bool:
    """MQTT 3.1.1 topic filter matching, `+` and `#` included."""
    f, t = filt.split("/"), topic.split("/")
    for i, level in enumerate(f):
        if level == "#":
            # `#` matches the parent level too, but never a $-prefixed topic.
            return i <= len(t)
        if i >= len(t):
            return False
        if level != "+" and level != t[i]:
            return False
    return len(f) == len(t)


class _Session:
    def __init__(self, writer) -> None:
        self.writer = writer
        self.subscriptions: list[tuple[str, int]] = []
        self.will: tuple[str, bytes, int, bool] | None = None
        self.client_id = ""

    def send(self, data: bytes) -> None:
        try:
            self.writer.write(data)
        except Exception:                                  # pragma: no cover
            pass


class Broker:
    """Just enough MQTT to be a broker, on a port the OS picks."""

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._sessions: set[_Session] = set()
        self.retained: dict[str, tuple[bytes, int]] = {}
        self._next_pid = 1
        #: Every PUBLISH the broker accepted, in order: (topic, payload, retain).
        self.log: list[tuple[str, bytes, bool]] = []

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)

    async def stop(self) -> None:
        if self._server is None:
            return
        for session in list(self._sessions):
            session.writer.close()
        self._sessions.clear()
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _serve(self, reader, writer) -> None:
        session = _Session(writer)
        self._sessions.add(session)
        try:
            while True:
                header = await reader.readexactly(1)
                length, shift = 0, 0
                while True:
                    (byte,) = await reader.readexactly(1)
                    length += (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                body = await reader.readexactly(length) if length else b""
                if not await self._handle(session, header[0] >> 4, header[0] & 0x0F, body):
                    session.will = None          # a clean DISCONNECT cancels the will
                    break
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._sessions.discard(session)
            if session.will is not None:
                topic, payload, qos, retain = session.will
                self._distribute(topic, payload, qos, retain)
            try:
                writer.close()
            except Exception:                              # pragma: no cover
                pass

    async def _handle(self, session: _Session, kind: int, flags: int, body: bytes) -> bool:
        if kind == CONNECT:
            pos = 0
            _, pos = _string(body, pos)                     # "MQTT"
            pos += 1                                        # protocol level
            connect_flags = body[pos]
            pos += 3                                        # flags + keepalive
            session.client_id, pos = _string(body, pos)
            if connect_flags & 0x04:                        # will flag
                will_topic, pos = _string(body, pos)
                (will_len,) = struct.unpack_from("!H", body, pos)
                pos += 2
                will_payload = body[pos:pos + will_len]
                pos += will_len
                session.will = (will_topic, will_payload,
                                (connect_flags >> 3) & 0x03, bool(connect_flags & 0x20))
            session.send(_packet(CONNACK, 0, bytes([0, 0])))
            return True

        if kind == PUBLISH:
            qos = (flags >> 1) & 0x03
            retain = bool(flags & 0x01)
            topic, pos = _string(body, 0)
            pid = None
            if qos:
                (pid,) = struct.unpack_from("!H", body, pos)
                pos += 2
            payload = body[pos:]
            self.log.append((topic, payload, retain))
            if qos == 1:
                session.send(_packet(PUBACK, 0, struct.pack("!H", pid)))
            self._distribute(topic, payload, qos, retain)
            return True

        if kind == SUBSCRIBE:
            (pid,) = struct.unpack_from("!H", body, 0)
            pos, codes = 2, []
            while pos < len(body):
                filt, pos = _string(body, pos)
                qos = body[pos]
                pos += 1
                session.subscriptions.append((filt, qos))
                codes.append(qos)
                for topic, (payload, retained_qos) in list(self.retained.items()):
                    if topic_matches(filt, topic):
                        self._deliver(session, topic, payload,
                                      min(qos, retained_qos), retain=True)
            session.send(_packet(SUBACK, 0, struct.pack("!H", pid) + bytes(codes)))
            return True

        if kind == UNSUBSCRIBE:
            (pid,) = struct.unpack_from("!H", body, 0)
            pos = 2
            while pos < len(body):
                filt, pos = _string(body, pos)
                session.subscriptions = [s for s in session.subscriptions if s[0] != filt]
            session.send(_packet(UNSUBACK, 0, struct.pack("!H", pid)))
            return True

        if kind == PINGREQ:
            session.send(_packet(PINGRESP, 0, b""))
            return True

        if kind in (PUBACK,):
            return True

        return kind != DISCONNECT

    def _distribute(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        if retain:
            # A zero-length retained payload is MQTT's delete.
            if payload:
                self.retained[topic] = (payload, qos)
            else:
                self.retained.pop(topic, None)
        for session in list(self._sessions):
            for filt, sub_qos in session.subscriptions:
                if topic_matches(filt, topic):
                    self._deliver(session, topic, payload, min(qos, sub_qos), retain=False)
                    break

    def _deliver(self, session: _Session, topic: str, payload: bytes,
                 qos: int, retain: bool) -> None:
        encoded = topic.encode("utf-8")
        body = struct.pack("!H", len(encoded)) + encoded
        if qos:
            body += struct.pack("!H", self._next_pid)
            self._next_pid = self._next_pid % 65535 + 1
        session.send(_packet(PUBLISH, qos << 1 | int(retain), body + payload))


# --- fixtures ----------------------------------------------------------

@pytest_asyncio.fixture
async def broker():
    b = Broker()
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


@pytest_asyncio.fixture
async def mqtt(bus, broker):
    """A started MQTT driver, connected to the broker and subscribed."""
    driver = drivers.create("mqtt", bus, host="127.0.0.1", port=broker.port)
    await driver.start()
    await driver.ready(timeout=10)
    # start() only says the broker answered. The topic map comes from the
    # engine's describe, which arrives on its own schedule.
    await _wait_for(lambda: bool(driver._output_topics))
    await _wait_for(lambda: driver.status_topic in broker.retained)
    try:
        yield driver
    finally:
        await driver.stop()


async def _wait_for(predicate, timeout: float = 10.0, what: str = "condition"):
    """Wait on a predicate with a deadline.

    Polls at 25ms, which is above Windows' 15.6ms asyncio clock floor
    (AGENTS.md gotcha 2) -- these are conditions reached by a broker on
    another socket, so there is no event on this side to wait on. Every
    *timing* assertion in this file waits on an event instead.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


class Client:
    """A small async MQTT client for the tests, on the same paho + queue
    bridge the driver uses. Stands in for Node-RED, or for a PLC."""

    def __init__(self, port: int, identifier: str) -> None:
        self.port = port
        self.identifier = identifier
        self.seen: list[tuple[str, bytes, bool]] = []
        self.latest: dict[str, bytes] = {}
        self.inbox: asyncio.Queue = asyncio.Queue()
        self._connected = asyncio.Event()
        self._client = None

    async def __aenter__(self) -> "Client":
        loop = asyncio.get_running_loop()
        client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                             client_id=self.identifier, protocol=paho.MQTTv311)

        def on_connect(*_args, **_kwargs):
            loop.call_soon_threadsafe(self._connected.set)

        def on_message(_client, _userdata, message):
            loop.call_soon_threadsafe(self._record, str(message.topic),
                                      message.payload, bool(message.retain))

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect_async("127.0.0.1", self.port)
        client.loop_start()
        self._client = client
        await asyncio.wait_for(self._connected.wait(), timeout=10)
        return self

    async def __aexit__(self, *exc) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()
            await asyncio.get_running_loop().run_in_executor(None, client.loop_stop)

    def _record(self, topic: str, payload: bytes, retained: bool) -> None:
        self.seen.append((topic, payload, retained))
        self.latest[topic] = payload
        self.inbox.put_nowait((topic, payload))

    async def subscribe(self, *filters: str) -> None:
        self._client.subscribe([(f, 1) for f in filters])
        # The broker answers SUBACK and any retained messages on the same
        # socket, in order, so one short settle is enough for both.
        await asyncio.sleep(0.2)

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        self._client.publish(topic, payload, qos=qos)


# --- payloads (no sockets) ---------------------------------------------

def test_a_payload_is_a_bare_json_scalar():
    assert encode(True) == b"true"
    assert encode(False) == b"false"
    assert encode(7) == b"7"
    assert json.loads(encode(3.5)) == 3.5


@pytest.mark.parametrize("payload", [b"true", b"1", b"on", b"ON", b" yes ",
                                     b'{"value": true}', b'{"value": 1}'])
def test_every_spelling_of_true_a_student_will_publish_is_accepted(payload):
    tag = Tag("conveyor.rotate", "Belt", "bit", "output")
    assert decode(tag, payload) is True


@pytest.mark.parametrize("payload", [b"false", b"0", b"off", b"OFF", b'{"value": false}'])
def test_every_spelling_of_false_is_accepted(payload):
    tag = Tag("conveyor.rotate", "Belt", "bit", "output")
    assert decode(tag, payload) is False


def test_a_payload_that_is_not_the_tags_type_is_refused():
    bit = Tag("conveyor.rotate", "Belt", "bit", "output")
    count = Tag("counter.tall", "Tall", "int", "input")
    with pytest.raises(ValueError):
        decode(bit, b"42")
    with pytest.raises(ValueError):
        decode(bit, b"")
    with pytest.raises(ValueError):
        decode(count, b"true")
    with pytest.raises(ValueError):
        decode(count, b"1.5")


def test_an_int_tag_accepts_an_integral_float_because_javascript_has_no_ints():
    count = Tag("counter.tall", "Tall", "int", "input")
    assert decode(count, b"4.0") == 4


def test_topic_levels_never_contain_mqtt_wildcards():
    assert sanitise("sorting/by height") == "sorting_by_height"
    assert sanitise("a+b#c") == "a_b_c"


def test_topic_matching_handles_plus_and_hash():
    assert topic_matches("a/+/c", "a/b/c")
    assert not topic_matches("a/+/c", "a/b/d")
    assert topic_matches("a/#", "a/b/c")
    assert not topic_matches("a/b", "a/b/c")


# --- the driver against a broker ---------------------------------------

async def test_the_scene_appears_on_the_broker_as_retained_topics(bus, broker, mqtt):
    prefix = f"factoryforge/sorting-by-height/tag"
    retained = {t: p for t, (p, _) in broker.retained.items()}
    assert retained[f"{prefix}/sensor_low.detect"] == b"false"
    assert retained[f"{prefix}/pusher.retracted"] == b"true"
    assert retained[f"{prefix}/counter.tall"] == b"0"
    assert broker.retained["factoryforge/sorting-by-height/status"][0] == b"online"


async def test_only_simulator_inputs_are_published(bus, broker, mqtt):
    """`kind` is from the controller's point of view: the controller writes the
    outputs, so publishing them would be this process telling the controller
    what it just said."""
    published = set(broker.retained)
    for tag in bus.table.by_kind("output"):
        assert f"factoryforge/sorting-by-height/tag/{tag.id}" not in published
    for tag in bus.table.by_kind("input"):
        assert f"factoryforge/sorting-by-height/tag/{tag.id}" in published


async def test_only_controller_outputs_are_subscribed(bus, broker, mqtt):
    assert set(mqtt._output_topics) == {t.id for t in bus.table.by_kind("output")}
    assert set(mqtt._input_topics) == {t.id for t in bus.table.by_kind("input")}
    assert not (set(mqtt._by_topic.values()) & set(mqtt._input_topics))


async def test_a_publish_on_an_output_topic_reaches_the_simulation(bus, broker, mqtt, scene):
    async with Client(broker.port, "plc") as plc:
        plc.publish("factoryforge/sorting-by-height/tag/conveyor.rotate", b"true")
        await _wait_for(lambda: scene.tags.visible("conveyor.rotate") is True,
                        what="the belt to start")

        plc.publish("factoryforge/sorting-by-height/tag/conveyor.rotate", b"off")
        await _wait_for(lambda: scene.tags.visible("conveyor.rotate") is False,
                        what="the belt to stop")


async def test_a_sensor_change_is_published_and_nothing_else_is(bus, broker, mqtt, scene):
    """Delta-only, as the bus is. An idle scene must be silent on the broker --
    otherwise the retained-message story is carrying a firehose."""
    async with Client(broker.port, "listener") as listener:
        await listener.subscribe("factoryforge/sorting-by-height/tag/#")
        await _wait_for(lambda: len(listener.latest) >= 6, what="the retained baseline")
        baseline = len(listener.seen)

        await asyncio.sleep(0.5)
        assert len(listener.seen) == baseline, (
            "an idle scene published "
            f"{[t for t, _, _ in listener.seen[baseline:]]}")

        # Forced rather than set: the scene recomputes every sensor and counter
        # on each 5ms tick, so a plain set is overwritten before it can be
        # published. A force is what the engine shows the outside world.
        scene.tags.force("sensor_low.detect", True)
        await _wait_for(
            lambda: listener.latest.get(
                "factoryforge/sorting-by-height/tag/sensor_low.detect") == b"true",
            what="the sensor edge")
        assert len(listener.seen) == baseline + 1


async def test_a_late_subscriber_learns_the_line_from_retained_messages(
        bus, broker, mqtt, scene):
    """The reason every input publish is retained. A controller that connects
    after the line started has no history to replay, and MQTT's only answer is
    the retained message."""
    scene.tags.force("counter.tall", 4)          # see the delta-only test
    await _wait_for(
        lambda: broker.retained.get(
            "factoryforge/sorting-by-height/tag/counter.tall", (b"",))[0] == b"4")

    async with Client(broker.port, "late") as late:
        await late.subscribe("factoryforge/sorting-by-height/tag/#")
        await _wait_for(
            lambda: late.latest.get(
                "factoryforge/sorting-by-height/tag/counter.tall") == b"4",
            what="a retained counter")
        assert all(retained for _, _, retained in late.seen)


async def test_a_payload_the_tag_cannot_hold_is_refused_not_coerced(
        bus, broker, mqtt, scene):
    async with Client(broker.port, "plc") as plc:
        plc.publish("factoryforge/sorting-by-height/tag/conveyor.rotate", b"banana")
        await asyncio.sleep(0.3)
        assert scene.tags.visible("conveyor.rotate") is False


async def test_a_lost_bus_is_announced_rather_than_left_looking_healthy(
        bus, broker, mqtt):
    """MQTT has no quality flag, so retained sensor values from a dead
    simulator are indistinguishable from live ones. The status topic is the
    only place to say otherwise. See FF-03."""
    assert broker.retained["factoryforge/sorting-by-height/status"][0] == b"online"
    await mqtt.bus_disconnected()
    await _wait_for(
        lambda: broker.retained["factoryforge/sorting-by-height/status"][0] == b"stale",
        what="the scene to be marked stale")


async def test_a_killed_sidecar_says_so_through_its_will(bus, broker, mqtt):
    """The will is on the sidecar topic, not the scene topic: a will is fixed
    at CONNECT and `start()` runs before the first `rebuild()`, so the scene
    name is not known yet."""
    will_topic = "factoryforge/sidecar/factoryforge-sidecar/status"
    assert broker.retained[will_topic][0] == b"online"
    # Drop the socket without a DISCONNECT, which is what a killed process does.
    for session in list(broker._sessions):
        if session.client_id == "factoryforge-sidecar":
            session.writer.close()
    await _wait_for(lambda: broker.retained[will_topic][0] == b"offline",
                    what="the last will")


async def test_retained_messages_for_tags_a_scene_edit_removed_are_cleared(
        bus, broker, mqtt, engine, scene):
    """An uncleared retained message is a sensor reading that outlives its
    sensor: it sits on the broker for ever and reads exactly like a live one."""
    topic = "factoryforge/sorting-by-height/tag/counter.short"
    assert topic in broker.retained

    trimmed = [t for t in scene.tags if t.id != "counter.short"]
    scene.tags = type(scene.tags)(
        [type(t)(t.id, t.name, t.type, t.kind, t.value) for t in trimmed])
    await engine.send_describe()

    await _wait_for(lambda: topic not in broker.retained,
                    what="the orphaned retained message to be cleared")


async def test_the_driver_comes_back_when_the_broker_does(bus, broker, scene):
    port = broker.port          # captured before the broker releases it
    driver = drivers.create("mqtt", bus, host="127.0.0.1", port=port)
    await driver.start()
    await driver.ready(timeout=10)
    await _wait_for(lambda: bool(driver._output_topics))
    try:
        await broker.stop()
        await _wait_for(lambda: not driver.online.is_set(), what="the driver to notice")

        revived = Broker()
        # Same port, now that the old broker has released it. Nothing here
        # hard-codes a port number; the OS chose it and the driver was told.
        revived._server = await asyncio.start_server(revived._serve, "127.0.0.1", port)
        try:
            await _wait_for(lambda: driver.online.is_set(), timeout=15,
                            what="the driver to reconnect")
            await _wait_for(
                lambda: "factoryforge/sorting-by-height/tag/sensor_low.detect"
                in revived.retained,
                what="the retained baseline to be republished")
        finally:
            await revived.stop()
    finally:
        await driver.stop()


# --- acceptance: a controller that speaks only MQTT --------------------

async def test_a_controller_that_only_speaks_mqtt_sorts_cartons(bus, broker, mqtt, scene):
    """The milestone. Nothing in this controller knows the tag bus exists: it
    subscribes to one sensor topic and publishes three actuator topics, and
    cartons come out sorted by height.

    Counts are a band, not a number -- this is a controller racing a
    simulation across a broker, and the exact total depends on when the run
    starts. What it *can* promise is that nothing landed in the wrong lane.
    """
    base = "factoryforge/sorting-by-height/tag"

    async with Client(broker.port, "mqtt-plc") as plc:
        await plc.subscribe(f"{base}/sensor_high.detect")
        plc.publish(f"{base}/conveyor.rotate", b"true")
        emitter = asyncio.create_task(_emit_loop(plc, base))
        pusher = asyncio.create_task(_pusher_loop(plc, base))
        try:
            await _wait_for(
                lambda: len(scene.sorted_tall) >= 3 and len(scene.sorted_short) >= 3,
                timeout=45, what="cartons to reach both lanes")
        finally:
            for task in (emitter, pusher):
                task.cancel()
            for task in (emitter, pusher):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    misrouted_tall = [b.id for b in scene.sorted_short if b.is_tall]
    misrouted_short = [b.id for b in scene.sorted_tall if not b.is_tall]
    assert not misrouted_tall, f"tall cartons ran off the end: {misrouted_tall}"
    assert not misrouted_short, f"short cartons were diverted: {misrouted_short}"
    assert scene.tags.visible("conveyor.rotate") is True


async def _emit_loop(plc: "Client", base: str) -> None:
    """One carton every 1.8s, as a rising edge on emitter.emit."""
    while True:
        plc.publish(f"{base}/emitter.emit", b"true", qos=0)
        await asyncio.sleep(0.2)
        plc.publish(f"{base}/emitter.emit", b"false", qos=0)
        await asyncio.sleep(1.6)


async def _pusher_loop(plc: "Client", base: str) -> None:
    """Fire the pusher on the rising edge of the tall beam.

    Waits on the message queue rather than polling: the delay that matters is
    measured from the edge, and a poll loop under Windows' 15.6ms clock floor
    would not measure it at all (AGENTS.md gotcha 2).
    """
    high_was = False
    while True:
        topic, payload = await plc.inbox.get()
        if not topic.endswith("sensor_high.detect"):
            continue
        high = payload == b"true"
        if high and not high_was:
            # The carton is 0.6m upstream of the pusher at 0.5m/s, and the
            # plate takes 0.3s to come out: command it 0.7s after the beam.
            await asyncio.sleep(0.7)
            plc.publish(f"{base}/pusher.extend", b"true", qos=0)
            await asyncio.sleep(0.5)
            plc.publish(f"{base}/pusher.extend", b"false", qos=0)
            high_was = False
            continue
        high_was = high


# --- registry ----------------------------------------------------------

def test_the_cli_can_coerce_the_options_the_driver_declares():
    """`-o port 1884` arrives as a string, and a type hint does not make it an
    int (AGENTS.md gotcha 19b). The CLI reads these annotations."""
    types = drivers.option_types("mqtt")
    assert types["port"] == "int"
    assert types["qos"] == "int"
    assert types["host"] == "str"
    assert types["prefix"] == "str"


def test_the_driver_is_registered_under_its_own_name():
    assert drivers._REGISTRY["mqtt"] is MqttDriver
