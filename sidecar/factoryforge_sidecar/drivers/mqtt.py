"""MQTT driver: the scene's tags as pub/sub topics.

Modbus and OPC UA are both request/response over an address space, and the
`Driver` ABC was written with those two in front of it. MQTT is neither: there
is no address space, no polling, and no connection to the controller at all --
a broker sits in the middle and neither end knows who is on the other side.
That is the whole point of this driver (roadmap M1.5). It is a test of the
abstraction as much as it is a feature.

    sim `output` tag  (PLC writes it) -> we **subscribe**; a publish reaches the bus
    sim `input`  tag  (PLC reads it)  -> we **publish**, retained, delta-only

Topics are `factoryforge/<scene>/tag/<tag_id>` and need no mapping file --
unlike OPC UA and S7, where the PLC's address space has no relationship to our
tag ids and a mapping has to be written by hand. Anything that speaks MQTT
finds a tag by its name.

Payloads are a bare JSON scalar: `true`, `false`, `42`, `3.14`. Readable from
`mosquitto_sub` without a decoder ring, and one line to parse anywhere.
Incoming payloads are read liberally -- `1`, `on`, `ON`, `true` and
`{"value": true}` all set a bit -- because what publishes them is a student's
Node-RED flow or a shell one-liner, and rejecting `on` teaches nothing about
control.

**Retained is the load-bearing word.** A subscriber that connects after the
line started still has to learn the sensor states, and MQTT's only answer to
that is the retained message. So every input publish is retained, the whole
input table is republished on connect and on every scene change, and the
retained messages of tags a scene edit removed are actively cleared -- an
uncleared retained message is a sensor reading that outlives its sensor.

Two retained status topics, and the split is not decoration:

    factoryforge/<scene>/status           online | stale | offline
    factoryforge/sidecar/<client>/status  online | offline   <- the last will

`stale` is what `bus_disconnected()` publishes: the equivalent of the OPC UA
server driver marking its nodes Bad. MQTT has no quality channel, so a retained
`true` from a sensor whose simulator died reads exactly like a retained `true`
from one that is working, and the status topic is the only place to say
otherwise (FF-03).

The will cannot live on the scene topic, and that is a finding rather than a
preference: a will is fixed at CONNECT, and `Driver.start()` runs before the
first `rebuild()`, so when the connection is made this driver does not yet know
the scene name its topics will use. The honest consequence is that killing this
process leaves `<scene>/status` saying `online` -- anything that wants the
truth has to watch the sidecar topic too.

## This driver is not wired into the CLI yet

`drivers/__init__.py` ends with an explicit `from . import mock, modbus_tcp,
...`, and importing a module is what runs its `@register`. So until `mqtt` is
added to that list, `factoryforge-sidecar drivers` will not list it and
`--driver mqtt` will not find it, however complete this file is. Two one-line
changes are needed, both in a file this driver deliberately does not touch:

    from . import mock, modbus_tcp, mqtt, plcsim_advanced, s7_snap7
    # and in usable():  "mqtt": mqtt.HAS_PAHO,

The second matters as much as the first. `usable()` keys a hard-coded `needs`
dict and defaults anything missing from it to `True`, so without that entry a
release frozen without the `mqtt` extra would list this driver as OK while it
is dead on arrival -- the exact failure `usable()` was written to prevent.

Worth noting while somebody is in there: the `Driver` docstring says adding a
driver means "subclassing `Driver`, implementing three methods, and decorating
it with `@register`. Nothing else in the codebase needs to change."
`docs/DRIVER_AUTHORING.md` step 2 says otherwise, and step 2 is right.

## Why paho and not aiomqtt

The roadmap asked for `aiomqtt`, and on Linux and macOS it would be the better
choice -- a nicer API and less of this file. It cannot be used here. aiomqtt
drives paho's socket through `loop.add_reader()`/`add_writer()`, and Windows'
default `ProactorEventLoop` implements neither; every call raises
`NotImplementedError` from inside an asyncio callback, so the client connects,
reports success, and then silently never receives a byte. FactoryForge's
reference machine is Windows and the sidecar is launched by the engine, which
chooses neither the event loop policy nor the driver, so "set
`WindowsSelectorEventLoopPolicy` first" is not an instruction this driver is in
a position to give.

paho is aiomqtt's own transport, so this is one layer down rather than a
different stack. Its `loop_start()` puts the socket on a thread paho owns,
which also satisfies the ABC's rule that a driver must never block the bus. The
only thing that crosses the thread boundary is a `call_soon_threadsafe` onto a
queue: every piece of driver state is touched from the asyncio side alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..tagbus import TagBusClient
from ..tags import Tag, TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)

try:
    import paho.mqtt.client as paho
    HAS_PAHO = True
except ImportError:                                   # pragma: no cover
    paho = None                                       # type: ignore[assignment]
    HAS_PAHO = False

#: Reconnect backoff bounds, in seconds. paho does the retrying; these only
#: say how fast. Both are far above Windows' 15.6 ms asyncio clock floor.
RECONNECT_MIN_DELAY = 0.5
RECONNECT_MAX_DELAY = 5.0

DEFAULT_PREFIX = "factoryforge"
DEFAULT_PORT = 1883

#: Most inbound messages to hold before dropping. A controller in a publish
#: loop must not be able to grow this process's memory without bound; dropping
#: the newest is right for control values, where the next one supersedes it.
INBOX_LIMIT = 1000

#: Characters MQTT gives its own meaning to, plus the ones that make a topic
#: unreadable. A tag id containing one of these cannot be addressed, so the
#: driver says so and skips it rather than publishing to a topic that means
#: something else -- a tag called `a/b` would otherwise create a level.
_UNSAFE = re.compile(r"[+#/\x00-\x20\x7f]")

_TRUEISH = {"1", "true", "on", "yes", "high", "t"}
_FALSEISH = {"0", "false", "off", "no", "low", "f"}

#: Inbox sentinels. Distinct objects rather than strings, so a topic can never
#: be mistaken for one.
_CONNECTED = object()
_DISCONNECTED = object()


def sanitise(text: str) -> str:
    """A topic level it is safe to build a topic out of."""
    return _UNSAFE.sub("_", text) or "_"


def encode(value: TagValue) -> bytes:
    """A bare JSON scalar. `true`, `false`, `42`, `3.14`."""
    return json.dumps(value).encode("utf-8")


def decode(tag: Tag, payload: bytes) -> TagValue:
    """Read a payload as this tag's type, or raise ValueError.

    Deliberately liberal about what it accepts. The publisher is a Node-RED
    node, an `mosquitto_pub` out of somebody's notes, or a broker bridge, and
    every one of them has its own idea of what "true" looks like on the wire.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("empty payload")
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError:
        value = text
    # `{"value": ...}` is what a Node-RED function node emits when somebody
    # forgets to reach into msg.payload.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]

    if tag.type == "bit":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUEISH:
            return True
        if isinstance(value, str) and value.strip().lower() in _FALSEISH:
            return False
        raise ValueError(f"{text!r} is not a bit")

    if tag.type == "int":
        if isinstance(value, bool):
            raise ValueError(f"{text!r} is a bit, not an int")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"{text!r} is not a whole number")
            value = int(value)
        if isinstance(value, str):
            value = int(value, 10)
        if not isinstance(value, int):
            raise ValueError(f"{text!r} is not an int")
        return value

    if isinstance(value, bool):
        raise ValueError(f"{text!r} is a bit, not a float")
    if isinstance(value, str):
        value = float(value)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{text!r} is not a float")
    return float(value)


@register("mqtt")
class MqttDriver(Driver):
    """Publish simulator inputs, subscribe to controller outputs."""

    def __init__(self, bus: TagBusClient, host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT, prefix: str = DEFAULT_PREFIX,
                 username: str | None = None, password: str | None = None,
                 qos: int = 1, client_id: str | None = None,
                 keepalive: int = 30, **config: Any) -> None:
        super().__init__(bus, host=host, port=port, **config)
        self.host = host
        # `-o port 1883` arrives as a string; the CLI coerces from the
        # annotations above, but nothing stops a caller constructing this
        # directly with whatever it has (AGENTS.md gotcha 19b).
        self.port = int(port)
        self.prefix = sanitise(prefix)
        self.username = username
        self.password = password
        self.qos = int(qos)
        self.keepalive = int(keepalive)
        self.client_id = client_id or "factoryforge-sidecar"

        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbox: asyncio.Queue | None = None
        self._pump: asyncio.Task | None = None

        self._table: TagTable | None = None
        self._scene: str | None = None
        #: tag id -> topic, for the tags this scene can actually address.
        self._input_topics: dict[str, str] = {}
        self._output_topics: dict[str, str] = {}
        #: topic -> tag id, for incoming messages.
        self._by_topic: dict[str, str] = {}
        #: Topics currently subscribed, so a scene change can undo them.
        self._subscribed: set[str] = set()
        #: Topics holding a retained value no tag owns any more.
        self._orphaned: set[str] = set()
        self.unaddressable: list[str] = []

        #: Set while the broker connection is up *and* its subscriptions are
        #: live -- not merely while the socket exists.
        self.online = asyncio.Event()
        self._dropped = 0

    # --- topics ---

    @property
    def scene_prefix(self) -> str:
        return f"{self.prefix}/{sanitise(self._scene or 'unknown')}"

    @property
    def status_topic(self) -> str:
        return f"{self.scene_prefix}/status"

    @property
    def sidecar_status_topic(self) -> str:
        """Scene-independent, because a will is chosen at CONNECT -- before
        `rebuild()` has said what the scene is called."""
        return f"{self.prefix}/sidecar/{sanitise(self.client_id)}/status"

    def topic_for(self, tag_id: str) -> str:
        return f"{self.scene_prefix}/tag/{tag_id}"

    # --- lifecycle ---

    async def start(self) -> None:
        if not HAS_PAHO:
            raise RuntimeError(
                "paho-mqtt is not installed. pip install 'factoryforge-sidecar[mqtt]'")
        self._loop = asyncio.get_running_loop()
        self._inbox = asyncio.Queue(maxsize=INBOX_LIMIT)
        self._pump = asyncio.create_task(self._pump_inbox())

        client = paho.Client(
            paho.CallbackAPIVersion.VERSION2,
            client_id=self.client_id, protocol=paho.MQTTv311, clean_session=True)
        if self.username is not None:
            client.username_pw_set(self.username, self.password)
        client.will_set(self.sidecar_status_topic, b"offline",
                        qos=self.qos, retain=True)
        client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY,
                                   max_delay=RECONNECT_MAX_DELAY)
        client.on_connect = self._paho_on_connect
        client.on_disconnect = self._paho_on_disconnect
        client.on_message = self._paho_on_message
        self._client = client

        # connect_async + loop_start never blocks and retries the first
        # connection as well as later ones, so a broker that is not up yet is
        # a delay rather than a failure -- which is the MQTT expectation.
        client.connect_async(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()

    async def stop(self) -> None:
        """Idempotent: `connect` calls it on every exit path, including ones
        where `start` never ran."""
        client, self._client = self._client, None
        if client is not None and self.online.is_set():
            # A clean shutdown says so itself; the will only covers the ones
            # that are not clean, and DISCONNECT cancels the will.
            self._raw_publish(client, self.status_topic, b"offline", retain=True)
            info = self._raw_publish(client, self.sidecar_status_topic, b"offline",
                                     retain=True)
            await self._flush(info)
        self.online.clear()

        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass

        if client is not None:
            client.disconnect()
            # loop_stop() joins paho's thread, so it must not run on the loop.
            await asyncio.get_running_loop().run_in_executor(None, client.loop_stop)
        self._subscribed.clear()

    async def ready(self, timeout: float = 10.0) -> None:
        """Block until the broker connection is up and subscribed.

        `start()` returning means the client exists, not that a broker
        answered. A driver that refused to start without its broker would be
        the wrong shape for MQTT, where everything is expected to come and go,
        so callers that need the distinction ask for it.
        """
        await asyncio.wait_for(self.online.wait(), timeout)

    # --- address map ---

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        """Recompute topics for a new tag set.

        There is no mapping to reconcile -- a topic is the scene name and the
        tag id -- but there *is* retained state on the broker, and a tag this
        scene no longer has must have its retained message cleared or it sits
        there for ever reading like a live sensor.
        """
        previous_inputs = set(self._input_topics.values())
        previous_prefix = self.scene_prefix if self._scene else None

        self._scene = scene
        self._table = table
        self._input_topics.clear()
        self._output_topics.clear()
        self._by_topic.clear()
        self.unaddressable = []

        for tag in table:
            if _UNSAFE.search(tag.id):
                self.unaddressable.append(tag.id)
                continue
            topic = self.topic_for(tag.id)
            if tag.kind == "input":
                self._input_topics[tag.id] = topic
            else:
                self._output_topics[tag.id] = topic
                self._by_topic[topic] = tag.id

        if self.unaddressable:
            await self._report(
                "warn", "unaddressable_tags",
                "tag ids MQTT cannot address (they contain +, # or /): "
                + ", ".join(sorted(self.unaddressable)))

        live = set(self._input_topics.values())
        if previous_prefix and previous_prefix != self.scene_prefix:
            self._orphaned |= previous_inputs        # the whole prefix moved
        else:
            self._orphaned |= previous_inputs - live
        self._orphaned -= live

        if self.online.is_set():
            self._resubscribe()
            self._publish_all()

    # --- data flow ---

    async def push(self, values: dict[str, TagValue]) -> None:
        """Changed simulator inputs -> one retained publish each.

        Delta-only, exactly as the bus delivers them. A publish lost while the
        broker is away is deliberately not queued for later: the whole input
        table is republished on reconnect, so what a subscriber eventually
        reads is the line as it is now rather than a backlog of sensor edges
        from a line that has since moved on.
        """
        if not self.online.is_set() or self._table is None:
            return
        for tag_id, value in values.items():
            topic = self._input_topics.get(tag_id)
            if topic is not None:
                self._publish(topic, encode(value), retain=True)

    async def bus_disconnected(self) -> None:
        """Say so on the broker rather than leave retained values that look
        like a running line. See the module docstring, and FF-03."""
        if self.online.is_set():
            self._publish(self.status_topic, b"stale", retain=True)

    # --- paho callbacks. These run on paho's thread: they may do nothing but
    # --- hand the event to the loop.

    def _paho_on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.warning("MQTT broker refused the connection: %s", reason_code)
            return
        self._offer(_CONNECTED, None)

    def _paho_on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._offer(_DISCONNECTED, reason_code)

    def _paho_on_message(self, client, userdata, message):
        self._offer(message.topic, message.payload)

    def _offer(self, kind, payload) -> None:
        loop, inbox = self._loop, self._inbox
        if loop is None or inbox is None:
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, inbox, kind, payload)
        except RuntimeError:
            pass          # the loop is closing; there is nothing to deliver to

    def _enqueue(self, inbox: asyncio.Queue, kind, payload) -> None:
        try:
            inbox.put_nowait((kind, payload))
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("MQTT inbox full; dropped %d messages", self._dropped)

    # --- the asyncio side ---

    async def _pump_inbox(self) -> None:
        assert self._inbox is not None
        while True:
            kind, payload = await self._inbox.get()
            try:
                if kind is _CONNECTED:
                    await self._on_connected()
                elif kind is _DISCONNECTED:
                    await self._on_disconnected(payload)
                else:
                    await self._receive(kind, payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mqtt: handling %r failed", kind)

    async def _on_connected(self) -> None:
        self.online.set()
        self._subscribed.clear()
        self._resubscribe()
        self._publish_all()
        await self._report("info", "broker_connected",
                           f"MQTT connected to {self.host}:{self.port}; "
                           f"topics under {self.scene_prefix}/tag/")

    async def _on_disconnected(self, reason) -> None:
        if not self.online.is_set():
            return
        self.online.clear()
        self._subscribed.clear()
        log.warning("MQTT broker %s:%d disconnected (%s) -- retrying",
                    self.host, self.port, reason)

    def _resubscribe(self) -> None:
        """Subscribe to exactly the controller-owned topics.

        Explicitly, one per output tag, rather than a `.../tag/+` wildcard: the
        wildcard would also deliver back every input this driver publishes, so
        each sensor edge would arrive as an inbound message to identify and
        discard. Filtering at the broker costs one SUBSCRIBE and saves all of
        that.
        """
        client = self._client
        if client is None:
            return
        wanted = set(self._output_topics.values())
        gone = self._subscribed - wanted
        if gone:
            client.unsubscribe(sorted(gone))
        new = wanted - self._subscribed
        if new:
            client.subscribe([(topic, self.qos) for topic in sorted(new)])
        self._subscribed = wanted
        log.info("subscribed to %d controller topics under %s",
                 len(wanted), self.scene_prefix)

    def _publish_all(self) -> None:
        """Retained baseline: clear what is orphaned, then publish every input.

        Not delta-only, and that is the point -- a subscriber joining now has
        no history, and retained messages are the only thing that will tell it
        what the line looks like.
        """
        if self._client is None or self._table is None:
            return
        for topic in sorted(self._orphaned):
            # A zero-length retained payload is MQTT's delete.
            self._publish(topic, b"", retain=True)
        self._orphaned.clear()

        self._publish(self.sidecar_status_topic, b"online", retain=True)
        self._publish(self.status_topic, b"online", retain=True)
        for tag_id, topic in self._input_topics.items():
            self._publish(topic, encode(self._table.visible(tag_id)), retain=True)

    def _publish(self, topic: str, payload: bytes, retain: bool = False):
        return self._raw_publish(self._client, topic, payload, retain)

    def _raw_publish(self, client, topic: str, payload: bytes, retain: bool = False):
        if client is None:
            return None
        try:
            return client.publish(topic, payload, qos=self.qos, retain=retain)
        except Exception as exc:
            # A publish failing is the connection going away; paho will
            # reconnect and _on_connected republishes. Losing this one is
            # fine -- see push().
            log.debug("publish to %s failed: %s", topic, exc)
            return None

    @staticmethod
    async def _flush(info, timeout: float = 2.0) -> None:
        """Wait for one publish to leave the socket, off the event loop.

        Used only on the way out. `wait_for_publish` blocks, which is exactly
        what an executor is for and exactly what the bus loop must never do.
        """
        if info is None:
            return
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, info.wait_for_publish, timeout)
        except Exception:
            log.debug("final publish did not flush", exc_info=True)

    async def _receive(self, topic: str, payload: bytes) -> None:
        """A controller published to an output topic."""
        tag_id = self._by_topic.get(topic)
        if tag_id is None or self._table is None:
            log.debug("ignoring message on unmapped topic %s", topic)
            return
        tag = self._table.get(tag_id)
        if tag is None:
            return
        if tag.kind != "output":
            # Unreachable through _by_topic, but the rule is worth stating
            # where somebody reading this file will find it: publishing to a
            # sensor's topic is not how you override a sensor. `force` is.
            await self._report("warn", "wrong_kind",
                               f"{tag_id} is a simulator-owned input; "
                               f"publishing to it does nothing")
            return
        try:
            value = decode(tag, payload)
        except (ValueError, UnicodeDecodeError) as exc:
            await self._report("warn", "bad_payload",
                               f"{topic}: {exc} (expected a {tag.type})")
            return
        await self.bus.write(tag_id, value)

    async def _report(self, level: str, code: str, message: str) -> None:
        log.log({"info": logging.INFO, "warn": logging.WARNING}.get(level, logging.ERROR),
                "%s", message)
        try:
            await self.bus.status(level, code, message)
        except Exception:
            log.debug("could not report status upstream", exc_info=True)
