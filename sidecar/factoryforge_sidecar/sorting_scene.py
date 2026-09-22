"""Headless 'sorting by height' scene.

A 1D kinematic model of the v1 milestone scene -- no physics, no 3D. Boxes are
points travelling along a belt; a pusher deflects whichever one is in front of
it when extended.

Two jobs:

1. Prove the tag bus round-trip before any of the 3D work exists.
2. Serve as the CI regression scene, so physics tuning in the real engine can be
   checked against known-good behaviour.

Layout (metres along the belt):

    0.0        1.5           2.0          2.5        3.0
    |          |             |            |          |
    emitter    sensor_low    sensor_high  pusher     remover
                                          |
                                          v  chute (tall boxes)

Sensor semantics match a real diffuse photoelectric sensor: `sensor_low` sits
low enough to see every box, `sensor_high` is mounted above short-box height so
only tall boxes break it. Both are true while a box occupies their window.

It lives in the sidecar package rather than at `harness/scene.py`, where it
started, because the grader marks this scene and reads its geometry, and the
grader has to import from an installed or frozen sidecar with no checkout
beside it (IP-18, IP-08). `harness/scene.py` is an alias for this module, not
a copy: `import scene` returns this very module object, so setting
`scene.BELT_SPEED` changes the constant this code reads.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from factoryforge_sidecar.tags import Tag, TagTable

# Geometry
EMITTER_POS = 0.0
SENSOR_LOW_POS = 1.5
SENSOR_HIGH_POS = 2.0
PUSHER_POS = 2.5
REMOVER_POS = 3.0

BOX_LENGTH = 0.2
SENSOR_WINDOW = BOX_LENGTH        # a sensor sees a box within half this either side
BELT_SPEED = 0.5                  # m/s when running
PUSHER_TRAVEL_TIME = 0.3          # s to fully extend or retract
PUSHER_CATCH = 0.15               # m either side of the pusher it can deflect

SHORT_HEIGHT = 0.1
TALL_HEIGHT = 0.3

_ids = itertools.count(1)


@dataclass
class Box:
    height: float
    position: float = EMITTER_POS
    id: int = field(default_factory=lambda: next(_ids))
    diverted: bool = False

    @property
    def is_tall(self) -> bool:
        return self.height > (SHORT_HEIGHT + TALL_HEIGHT) / 2


class SortingScene:
    """The scene. Owns the authoritative tag table."""

    name = "sorting-by-height"

    def __init__(self, emit_pattern: list[bool] | None = None) -> None:
        #: True for a tall box. Cycled. Default alternates so a single pass
        #: exercises both branches.
        self.emit_pattern = emit_pattern if emit_pattern is not None else [False, True]
        self._emit_index = 0

        self.boxes: list[Box] = []
        self.sorted_tall: list[Box] = []
        self.sorted_short: list[Box] = []

        self.pusher_extension = 0.0   # 0.0 retracted, 1.0 extended
        self._emit_edge = False

        self.tags = TagTable([
            # --- PLC outputs: the program writes these ---
            Tag("conveyor.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("pusher.extend", "Pusher (Extend)", "bit", "output"),
            Tag("stack_light.green", "Stack Light (Green)", "bit", "output"),
            # --- PLC inputs: the simulator writes these ---
            Tag("sensor_low.detect", "Diffuse Sensor Low (Detect)", "bit", "input"),
            Tag("sensor_high.detect", "Diffuse Sensor High (Detect)", "bit", "input"),
            Tag("pusher.extended", "Pusher (Extended)", "bit", "input"),
            Tag("pusher.retracted", "Pusher (Retracted)", "bit", "input"),
            Tag("counter.tall", "Counter (Tall)", "int", "input"),
            Tag("counter.short", "Counter (Short)", "int", "input"),
        ])
        self.tags.set("pusher.retracted", True)

    # --- simulation ---

    def tick(self, dt: float) -> None:
        self._step_emitter()
        self._step_belt(dt)
        self._step_pusher(dt)
        self._step_sensors()
        self._step_counters()

    def _step_emitter(self) -> None:
        """Emit one box on the rising edge of emitter.emit."""
        emit = bool(self.tags.visible("emitter.emit"))
        if emit and not self._emit_edge:
            tall = self.emit_pattern[self._emit_index % len(self.emit_pattern)]
            self._emit_index += 1
            self.boxes.append(Box(height=TALL_HEIGHT if tall else SHORT_HEIGHT))
        self._emit_edge = emit

    def _step_belt(self, dt: float) -> None:
        if not self.tags.visible("conveyor.rotate"):
            return
        for box in self.boxes:
            box.position += BELT_SPEED * dt

        remaining = []
        for box in self.boxes:
            if box.position >= REMOVER_POS:
                self.sorted_short.append(box)
            else:
                remaining.append(box)
        self.boxes = remaining

    def _step_pusher(self, dt: float) -> None:
        target = 1.0 if self.tags.visible("pusher.extend") else 0.0
        step = dt / PUSHER_TRAVEL_TIME
        if self.pusher_extension < target:
            self.pusher_extension = min(target, self.pusher_extension + step)
        elif self.pusher_extension > target:
            self.pusher_extension = max(target, self.pusher_extension - step)

        self.tags.set("pusher.extended", self.pusher_extension >= 1.0)
        self.tags.set("pusher.retracted", self.pusher_extension <= 0.0)

        # A fully extended pusher deflects whatever is in front of it.
        if self.pusher_extension >= 1.0:
            for box in list(self.boxes):
                if abs(box.position - PUSHER_POS) <= PUSHER_CATCH:
                    box.diverted = True
                    self.boxes.remove(box)
                    self.sorted_tall.append(box)

    def _step_sensors(self) -> None:
        self.tags.set("sensor_low.detect", self._occupied(SENSOR_LOW_POS))
        self.tags.set(
            "sensor_high.detect",
            self._occupied(SENSOR_HIGH_POS, min_height=TALL_HEIGHT),
        )

    def _occupied(self, position: float, min_height: float = 0.0) -> bool:
        half = SENSOR_WINDOW / 2
        return any(
            abs(box.position - position) <= half and box.height >= min_height
            for box in self.boxes
        )

    def _step_counters(self) -> None:
        self.tags.set("counter.tall", len(self.sorted_tall))
        self.tags.set("counter.short", len(self.sorted_short))

    # --- helpers ---

    def reset(self) -> None:
        self.boxes.clear()
        self.sorted_tall.clear()
        self.sorted_short.clear()
        self.pusher_extension = 0.0
        self._emit_index = 0
        self._emit_edge = False
        for tag in list(self.tags):
            self.tags.set(tag.id, {"bit": False, "int": 0, "float": 0.0}[tag.type])
        self.tags.set("pusher.retracted", True)

    def __repr__(self) -> str:
        return (f"<SortingScene {len(self.boxes)} on belt, "
                f"{len(self.sorted_tall)} tall, {len(self.sorted_short)} short>")
