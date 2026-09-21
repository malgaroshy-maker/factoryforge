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


# --- parsing ----------------------------------------------------------------


def encode(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


def decode(raw: str | bytes) -> dict:
    try:
        msg = json.loads(raw)
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


def parse_values(msg: dict) -> dict[str, Any]:
    values = msg.get("values", {})
    if not isinstance(values, dict):
        raise ProtocolError("'values' must be an object")
    return values
