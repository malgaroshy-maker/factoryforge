"""Tag model shared by the engine stub and the sidecar.

`kind` is always from the controller's point of view:

    output -- PLC writes it, simulator reads it   (motor, valve, lamp)
    input  -- simulator writes it, PLC reads it   (sensor, button, counter)

See docs/tag-bus.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Union

TagType = Literal["bit", "int", "float"]
TagKind = Literal["input", "output"]
TagValue = Union[bool, int, float]

#: Floats closer than this are treated as equal, so physics jitter in the low
#: bits does not emit an update every tick.
FLOAT_EPSILON = 1e-6

#: An `int` tag is a signed 32-bit integer, and the range is part of the
#: protocol rather than an artefact of one implementation. Python would hold
#: any integer you like; the C# engine holds an `int`, and a bus whose two
#: engines disagree about what `2147483648` means is not a contract. Documented
#: in docs/tag-bus.md, pinned by engine/fixtures/tag_cases.json. It also matches
#: what a PLC has: an S7 DInt is exactly this.
INT_MIN = -2147483648
INT_MAX = 2147483647

_DEFAULTS: dict[str, TagValue] = {"bit": False, "int": 0, "float": 0.0}


class TagError(ValueError):
    """Raised when a tag is misdeclared or given a value it cannot hold."""


@dataclass(frozen=True, slots=True)
class Tag:
    id: str
    name: str
    type: TagType
    kind: TagKind
    value: TagValue = None  # type: ignore[assignment]  # filled in __post_init__

    def __post_init__(self) -> None:
        if self.type not in _DEFAULTS:
            raise TagError(f"{self.id}: bad type {self.type!r}")
        if self.kind not in ("input", "output"):
            raise TagError(f"{self.id}: bad kind {self.kind!r}")
        if self.value is None:
            object.__setattr__(self, "value", _DEFAULTS[self.type])
        else:
            object.__setattr__(self, "value", self.coerce(self.value))

    def coerce(self, value: TagValue) -> TagValue:
        """Convert *value* to this tag's type, rejecting what cannot represent it."""
        if self.type == "bit":
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            raise TagError(f"{self.id}: {value!r} is not a bit")
        if self.type == "int":
            # bool is an int subclass in Python; accepting it here would silently
            # turn a mis-typed bit write into 0/1 and hide the bug.
            if isinstance(value, bool) or not isinstance(value, int):
                raise TagError(f"{self.id}: {value!r} is not an int")
            if not INT_MIN <= value <= INT_MAX:
                raise TagError(
                    f"{self.id}: {value!r} is outside the 32-bit range "
                    f"[{INT_MIN}, {INT_MAX}]")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TagError(f"{self.id}: {value!r} is not a float")
        return float(value)

    def differs(self, value: TagValue) -> bool:
        """True if *value* is meaningfully different from the current one.

        Coerces first, so a value this tag cannot hold is rejected here rather
        than compared. Without that, `True` against a float tag holding 1.0
        compared equal and was then quietly accepted by anything that only
        stores when `differs` says so -- and the two engines disagreed about it,
        because Python compares `1.0 != True` as False while C# ran the same
        value through `Convert.ToDouble`.
        """
        coerced = self.coerce(value)
        if self.type == "float":
            return abs(float(self.value) - coerced) > FLOAT_EPSILON
        return self.value != coerced

    def with_value(self, value: TagValue) -> "Tag":
        return replace(self, value=self.coerce(value))

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "value": self.value,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Tag":
        try:
            return cls(
                id=d["id"], name=d["name"], type=d["type"], kind=d["kind"],
                value=d.get("value"),
            )
        except KeyError as exc:
            raise TagError(f"tag is missing field {exc}") from exc


class TagTable:
    """An ordered collection of tags, keyed by id.

    The engine holds the authoritative instance; the sidecar holds a cache so
    drivers can serve reads without a bus round-trip.
    """

    __slots__ = ("_tags", "_forced")

    def __init__(self, tags: list[Tag] | None = None) -> None:
        self._tags: dict[str, Tag] = {}
        self._forced: dict[str, TagValue] = {}
        for tag in tags or ():
            self.add(tag)

    def add(self, tag: Tag) -> None:
        if tag.id in self._tags:
            raise TagError(f"duplicate tag id {tag.id!r}")
        self._tags[tag.id] = tag

    def __contains__(self, tag_id: object) -> bool:
        return tag_id in self._tags

    def __len__(self) -> int:
        return len(self._tags)

    def __iter__(self):
        return iter(self._tags.values())

    def __getitem__(self, tag_id: str) -> Tag:
        return self._tags[tag_id]

    def get(self, tag_id: str) -> Tag | None:
        return self._tags.get(tag_id)

    def value(self, tag_id: str) -> TagValue:
        return self._tags[tag_id].value

    def by_kind(self, kind: TagKind) -> list[Tag]:
        return [t for t in self._tags.values() if t.kind == kind]

    def set(self, tag_id: str, value: TagValue) -> bool:
        """Set a tag's value. Returns True if it actually changed.

        A forced tag absorbs the write silently: the underlying value updates so
        that clearing the force reveals something sensible, but the observable
        value stays pinned and no change is reported.

        A value that does not meaningfully differ is **not stored** (HP-18.3).
        Saying "nothing changed" and then storing the new value anyway let the
        reference creep by a hair a scan, so a float drifting 1e-9 per scan
        crossed the epsilon on the very next comparison and published on every
        single scan -- which is the traffic the epsilon exists to prevent.
        Coercion still happens first, so a value this tag cannot hold is
        rejected whether or not it would have changed anything.
        """
        tag = self._tags[tag_id]
        coerced = tag.coerce(value)
        changed = tag.differs(coerced)
        if changed:
            self._tags[tag_id] = tag.with_value(coerced)
        if tag_id in self._forced:
            return False
        return changed

    def observe(self, tag_id: str, value: TagValue) -> bool:
        """Record a value the *authority* says is already in effect.

        Only the sidecar's cache uses this. The engine reports the value it can
        see, which is the value after its own forces have been applied, so a
        reported value must land on top of a local force pin rather than be
        absorbed underneath it the way a simulator write is. Without it a tag
        whose forced value the engine changed would read as whatever the pin
        said when the sidecar first heard about it.
        """
        if tag_id in self._forced:
            return self.force(tag_id, value)
        return self.set(tag_id, value)

    def forced_ids(self) -> list[str]:
        return list(self._forced)

    def force(self, tag_id: str, value: TagValue) -> bool:
        tag = self._tags[tag_id]
        coerced = tag.coerce(value)
        was_visible = self.visible(tag_id)
        self._forced[tag_id] = coerced
        return coerced != was_visible

    def clear_force(self, tag_id: str) -> bool:
        if tag_id not in self._forced:
            return False
        pinned = self._forced.pop(tag_id)
        return self._tags[tag_id].differs(pinned)

    def is_forced(self, tag_id: str) -> bool:
        return tag_id in self._forced

    def visible(self, tag_id: str) -> TagValue:
        """The value the outside world sees, honouring any force."""
        if tag_id in self._forced:
            return self._forced[tag_id]
        return self._tags[tag_id].value

    def snapshot(self) -> dict[str, TagValue]:
        return {tid: self.visible(tid) for tid in self._tags}

    def to_json(self) -> list[dict]:
        out = []
        for tid, tag in self._tags.items():
            d = tag.to_json()
            d["value"] = self.visible(tid)
            if tid in self._forced:
                d["forced"] = True
            out.append(d)
        return out
