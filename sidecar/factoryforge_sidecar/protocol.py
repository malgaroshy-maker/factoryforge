"""Tag bus wire protocol. See docs/tag-bus.md.

Message construction and parsing only -- no I/O, no asyncio. Both the engine and
the sidecar import this so the two can never drift apart on message shape.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .tags import Tag, TagValue

PROTOCOL_VERSION = 0
DEFAULT_URL = "ws://127.0.0.1:7411/tagbus"
DEFAULT_TICK_MS = 10


class ProtocolError(ValueError):
    """Malformed or unexpected message."""


# --- construction -----------------------------------------------------------


def hello(engine: str, tick_ms: int = DEFAULT_TICK_MS) -> dict:
    return {"t": "hello", "protocol": PROTOCOL_VERSION, "engine": engine, "tick_ms": tick_ms}


def describe(scene: str, epoch: int, tags: Iterable[dict]) -> dict:
    return {"t": "describe", "scene": scene, "epoch": epoch, "tags": list(tags)}


def write(epoch: int, values: dict[str, TagValue]) -> dict:
    return {"t": "write", "epoch": epoch, "values": values}


def update(tick: int, values: dict[str, TagValue]) -> dict:
    return {"t": "update", "tick": tick, "values": values}


def observe(tick: int, forced: dict[str, TagValue],
            cleared: Iterable[str] = ()) -> dict:
    """Forced state the engine currently has in effect (HP-13).

    Deliberately *not* carried on `update`. `update` is simulator-input
    delivery and every driver's `push()` hook hangs off it; an output whose
    value the simulator is forcing must never reach that hook, or a driver that
    writes whatever it is handed would push a simulator-invented value back
    into a PLC-owned node. See docs/tag-bus.md.
    """
    return {"t": "observe", "tick": tick, "forced": forced, "cleared": list(cleared)}


def force(epoch: int, values: dict[str, TagValue] | None = None,
          clear: Iterable[str] = ()) -> dict:
    return {"t": "force", "epoch": epoch, "values": values or {}, "clear": list(clear)}


def status(level: str, code: str, message: str) -> dict:
    if level not in ("info", "warn", "error"):
        raise ProtocolError(f"bad status level {level!r}")
    return {"t": "status", "level": level, "code": code, "message": message}


def controller(ready: bool, driver: str, message: str) -> dict:
    """Whether the sidecar's driver has reached the controller (IP-30).

    Sidecar -> engine, and optional: an engine that does not know it ignores
    it, and a sidecar that never sends it still works. It is a message of its
    own rather than a `status` code because something acts on it -- the grader
    opens its window on it -- and `status` is free text for a person to read,
    whose codes drivers have always chosen for themselves. `driver_connected`,
    tag-bus.md's own example code, meant "a Modbus server is listening", which
    is not this at all. See docs/tag-bus.md.
    """
    if not isinstance(ready, bool):
        raise ProtocolError(f"controller: ready must be a bool, got {ready!r}")
    return {"t": "controller", "ready": ready, "driver": driver, "message": message}


# --- parsing ----------------------------------------------------------------


def encode(msg: dict) -> str:
    """Serialise a message. Refuses to emit anything that is not JSON.

    HP-23: Python's json module writes `NaN` and `Infinity` by default, which
    are not JSON and which the C# engine's parser rejects outright -- so a
    non-finite value that reached a tag left as a frame the other side could
    not read, and the sender was never told. `allow_nan=False` turns that into
    a ValueError here, where the sender can still see it.
    """
    try:
        return json.dumps(msg, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        raise ProtocolError(f"cannot encode {msg.get('t')!r}: {exc}") from exc


def _reject_constant(name: str):
    """HP-23: `NaN`, `Infinity` and `-Infinity` are not JSON.

    Python's decoder reads them anyway and hands back a float, which is how a
    non-finite value got into a float tag in the first place. The C# engine's
    parser refuses the same frame, so accepting it here is also a parity
    divergence: one engine took the message and the other did not.
    """
    raise ProtocolError(f"{name} is not valid JSON")


def decode(raw: str | bytes) -> dict:
    try:
        msg = json.loads(raw, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    if "t" not in msg:
        raise ProtocolError("message has no 't' field")
    return msg


def require(msg: dict, kind: str) -> dict:
    if msg.get("t") != kind:
        raise ProtocolError(f"expected {kind!r}, got {msg.get('t')!r}")
    return msg


def check_hello(msg: dict) -> dict:
    """Validate a hello and its protocol version.

    Version mismatch must fail loudly here rather than surfacing later as a
    baffling KeyError three messages deep.
    """
    require(msg, "hello")
    got = msg.get("protocol")
    if got != PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol mismatch: engine speaks v{got}, sidecar speaks v{PROTOCOL_VERSION}"
        )
    return msg


def parse_describe(msg: dict) -> tuple[str, int, list[Tag], set[str]]:
    """Scene, epoch, tags, and the ids the engine reports as forced.

    The forced set used to be dropped here, which is half of HP-13: a sidecar
    connecting to a scene somebody had already forced was told about it in the
    very first message and threw the fact away.
    """
    require(msg, "describe")
    try:
        scene = msg["scene"]
        epoch = int(msg["epoch"])
        tags = [Tag.from_json(t) for t in msg["tags"]]
        forced = {t["id"] for t in msg["tags"] if t.get("forced")}
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"bad describe: {exc}") from exc
    return scene, epoch, tags, forced


def parse_observe(msg: dict) -> tuple[dict[str, Any], list[str]]:
    require(msg, "observe")
    forced = msg.get("forced", {})
    if not isinstance(forced, dict):
        raise ProtocolError("'forced' must be an object")
    cleared = msg.get("cleared", [])
    if not isinstance(cleared, list):
        raise ProtocolError("'cleared' must be an array")
    return forced, cleared


def parse_controller(msg: dict) -> tuple[bool, str, str]:
    """`ready`, `driver` and `message` from a `controller` frame.

    Strict about types, for the reason `parse_epoch` is: the engine acts on
    this, and a frame it cannot read unambiguously draws `bad_message` rather
    than a guess. `"ready": 1` is refused although a `bit` tag would take it --
    this is a field of the protocol, not a tag value, and JSON has a boolean.
    Fields the engine does not know are ignored, so a later revision can add
    one without an older engine refusing the frame.
    """
    require(msg, "controller")
    ready = msg.get("ready")
    # bool first: it is the only type allowed, and `isinstance(1, bool)` is
    # False, so an integer cannot slip through as it would for an epoch.
    if not isinstance(ready, bool):
        raise ProtocolError(f"controller: 'ready' must be true or false, got {ready!r}")
    for key in ("driver", "message"):
        if not isinstance(msg.get(key), str):
            raise ProtocolError(
                f"controller: {key!r} must be a string, got {msg.get(key)!r}")
    return ready, msg["driver"], msg["message"]


def parse_epoch(msg: dict) -> int:
    """The `epoch` a `write` or `force` is stamped with. Must be an integer.

    The epoch stamp is the engine's only defence against a frame in flight
    across a scene change, so a frame that does not carry a usable one is not a
    frame the engine can act on -- it cannot tell current from stale. It draws
    `bad_message` rather than being dropped in silence, and this is where the
    two engines used to part company three ways: C# threw out of
    `GetValue<int>()` into its catch-all, Python dropped a string epoch
    silently, and Python *applied* a fractional one because `3.0 == 3`. An
    epoch that is an integer but not the current one is a different thing
    entirely and is still dropped without comment. See HP-19.
    """
    epoch = msg.get("epoch")
    # bool is an int subclass in Python, and `"epoch": true` is not an epoch.
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise ProtocolError(
            f"{msg.get('t')}: epoch must be an integer, got {epoch!r}")
    return epoch


def parse_values(msg: dict) -> dict[str, Any]:
    values = msg.get("values", {})
    if not isinstance(values, dict):
        raise ProtocolError("'values' must be an object")
    return values
