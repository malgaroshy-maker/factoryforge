"""What every plant model in `grading/scenes/` is built from: a carton, the
examiner's script, the operator panel and the scene base class.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from factoryforge_sidecar.tags import Tag, TagTable


# =======================================================================
#  The other nine scenes
# =======================================================================
#
# `factoryforge_sidecar/sorting_scene.py` (once `harness/scene.py`) models one
# line, and it is owned by the tag-bus stream. The nine models in
# `grading/scenes/` are built from this file instead, and they are the same
# *kind* of thing: 1-D kinematic plants with no physics, faithful about tag
# semantics, sensor windows and -- where the lesson is analog -- about the
# engine's own dynamics, which are copied from the C# part and the template
# that configures it rather than invented. Every constant that matters carries
# the file it came from, so a change to a part shows up there as a number that
# no longer matches rather than as a rubric that is quietly wrong.
#
# Three rules each of them follows.
#
# **The plant keeps its own ledger.** Every model records what physically
# happened -- which carton went where, how many litres really left the pump,
# whether the contactor pulled in before anybody pressed Start -- in attributes
# no bus message reaches. That ledger is the verdict. Tags are evidence.
#
# **The plant runs the exam.** Each model owns a `Script`: a list of things an
# examiner does on the plant side, on simulation time. Pressing Start, striking
# the mushroom, turning the pot to a number chosen from the seed, and -- the
# part that makes several of these rubrics work at all -- reaching into the
# machinery mid-run and changing something physical that no tag reports. A belt
# that is suddenly half as fast. A pump rated for half the flow. A gantry that
# travels slower than it did. Those are the changes a program written on a
# stopwatch cannot survive and a program written on feedback does not notice.
#
# **Nothing here forces a tag.** The engine's own parts do -- a safety relay
# holds the starter coil down by forcing it, and a motor starter drives the belt
# the same way -- but a forced tag is this tool's disqualification signal, so
# the models reproduce the *behaviour* and never the mechanism: the plant simply
# ignores a command it is not obeying. See docs/GRADING.md, which says so.


@dataclass
class Item:
    """A carton, in one dimension. Height and mass are the plant's secret.

    `height` is metres, `mass` kilograms -- both from `BoxPhysics.cs`, where a
    carton is 0.20 x H x 0.24 at 150 kg/m3 and a steel one at 900.
    """
    height: float = 0.10
    metal: bool = False
    position: float = 0.0
    id: int = 0
    lane: str | None = None          #: set once it leaves the line
    measured: float | None = None    #: what an instrument said about it
    threshold: float | None = None   #: the rule in force when it was measured
    carried: bool = False

    @property
    def mass(self) -> float:
        density = 900.0 if self.metal else 150.0
        return 0.20 * self.height * 0.24 * density

    @property
    def grams(self) -> float:
        return self.mass * 1000.0


class Script:
    """The examiner, on simulation time.

    A list of `(seconds, what)` run in order, once each, from inside `tick`.
    Simulation time and not wall clock, so a slow machine sits an exam in the
    same order as a fast one, and nothing here sleeps -- on Windows a sleep
    under 15.6 ms does not sleep at all (AGENTS.md gotcha 2) and the tick is the
    one place with an exact clock.
    """

    def __init__(self, steps: list[tuple[float, object]]) -> None:
        self._steps = sorted(steps, key=lambda s: s[0])
        self._next = 0
        self.done: list[tuple[float, str]] = []

    def run(self, now: float) -> None:
        while self._next < len(self._steps) and now >= self._steps[self._next][0]:
            when, what = self._steps[self._next]
            self._next += 1
            what()                                         # type: ignore[operator]
            self.done.append((round(now, 2), getattr(what, "__name__", "step")))


class Panel:
    """The operator station, from the operator's side.

    Every template places one and every scene here has one, because half of
    what these exercises teach is the operator contract: momentary buttons, a
    normally-closed mushroom, a latch that Start cannot clear. The grader is
    the hand on those buttons -- it presses them from the plant side, which is
    what makes "Start while tripped did nothing" a fact about the machine
    rather than a fact about a tag somebody could have written.

    `estop` is inverted on purpose, exactly as `ButtonPanel.cs` declares it:
    normally closed, true = healthy.
    """

    #: Long enough that a controller scanning at 50 ms cannot miss the edge,
    #: short enough to still be one edge. `tools/try_scene.py` holds 0.15 s
    #: against the real engine for the same reason.
    PRESS = 0.15

    def __init__(self, tags: TagTable, prefix: str = "panel",
                 setpoint: float = 0.0) -> None:
        self.tags = tags
        self.prefix = prefix
        self._held: dict[str, float] = {}
        self.setpoint_value = setpoint
        self.healthy = True
        #: Sim time of the last Start press. Ground truth for "did the machine
        #: start because somebody started it".
        self.last_start: float | None = None
        self.presses: list[tuple[float, str]] = []
        self._t = 0.0

    def declare(self, tags: TagTable) -> None:
        p = self.prefix
        for name, title in (("start", "Start (momentary)"), ("stop", "Stop (momentary)"),
                            ("reset", "Reset (momentary)")):
            tags.add(Tag(f"{p}.{name}", f"Panel {title}", "bit", "input"))
        tags.add(Tag(f"{p}.estop", "Panel E-Stop OK (NC)", "bit", "input", value=True))
        tags.add(Tag(f"{p}.setpoint", "Panel Setpoint", "float", "input",
                     value=self.setpoint_value))
        tags.add(Tag(f"{p}.green", "Panel Green Lamp", "bit", "output"))
        tags.add(Tag(f"{p}.red", "Panel Red Lamp", "bit", "output"))

    # --- the examiner's hand ---

    def press(self, name: str):
        def do() -> None:
            self._held[name] = self._t + self.PRESS
            self.presses.append((round(self._t, 2), name))
            if name == "start":
                self.last_start = self._t
        do.__name__ = f"press {name}"
        return do

    def set_setpoint(self, value: float):
        def do() -> None:
            self.setpoint_value = float(value)
        do.__name__ = f"pot to {value}"
        return do

    def strike(self):
        def do() -> None:
            self.healthy = False
        do.__name__ = "strike the mushroom"
        return do

    def release(self):
        def do() -> None:
            self.healthy = True
        do.__name__ = "release the mushroom"
        return do

    # --- the plant side ---

    def tick(self, dt: float, now: float) -> None:
        self._t = now
        p = self.prefix
        for name in ("start", "stop", "reset"):
            self.tags.set(f"{p}.{name}", now < self._held.get(name, -1.0))
        self.tags.set(f"{p}.estop", self.healthy)
        self.tags.set(f"{p}.setpoint", float(self.setpoint_value))

    def started_since(self, when: float) -> bool:
        return self.last_start is not None and self.last_start >= when


class PlantScene:
    """What every model in `grading/scenes/` has in common.

    Owns the tag table, the panel, the clock and the exam script, and offers
    the two things a rubric always wants: a place to put ground truth, and a
    `bit`/`num` pair that reads the *visible* value -- the one a force would
    change -- so a scene never accidentally grades the value underneath a pin.
    """

    name = "unnamed"

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.t = 0.0
        self.tags = TagTable([])
        self.panel = Panel(self.tags)
        self.panel.declare(self.tags)
        self.script = Script([])
        self.notes: dict = {}

    # --- reading what the controller wrote ---

    def bit(self, tag_id: str) -> bool:
        return bool(self.tags.visible(tag_id))

    def num(self, tag_id: str) -> float:
        return float(self.tags.visible(tag_id))

    # --- the loop ---

    def tick(self, dt: float) -> None:
        self.t += dt
        self.script.run(self.t)
        self.panel.tick(dt, self.t)
        self.step(dt)

    def step(self, dt: float) -> None:            # pragma: no cover - overridden
        raise NotImplementedError

    # --- helpers the models share ---

    def _declare(self, *tags: Tag) -> None:
        for tag in tags:
            self.tags.add(tag)

    def _eye(self, items: list[Item], position: float, window: float = 0.20) -> bool:
        """A diffuse photoelectric sensor: true while an item is in its window.

        Matches `sorting_scene.py`, which is in turn the semantics
        `PhotoelectricSensor.cs` gives a diffuse head.
        """
        half = window / 2
        return any(abs(item.position - position) <= half for item in items)


#: A shuffled, seeded feed, the same argument as `feed_pattern` makes for the
#: sorting line: an order a controller can guess is an order it can be written
#: against. Every scene that feeds more than one kind of carton draws from one
#: of these rather than from an alternation.
def shuffled_cycle(rng: random.Random, values: list, repeats: int) -> list:
    out: list = []
    for _ in range(repeats):
        block = list(values)
        rng.shuffle(block)
        out.extend(block)
    return out


#: `docs/tag-bus.md` §4.2 and `tools/try_scene.py`: strike to stopped. Here
#: rather than with the start / stop station, because the guarded cell allows
#: the same 200 ms of belt after its gate opens.
ESTOP_LIMIT = 0.200
