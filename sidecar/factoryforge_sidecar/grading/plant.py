"""What every plant model in `grading/scenes/` is built from: a carton, the
examiner's script, the operator panel and the scene base class.
"""

from __future__ import annotations

import math
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
# that configures it rather than invented. A number the template sets is read
# from the template (`grading/templates.py`, IP-19), so retuning the scene
# retunes the model. A number the C# part owns is a named constant carrying
# the file it came from, and `tests/test_grade_templates.py` reads that file
# and fails when the two stop matching -- so a change to a part shows up as a
# failed test rather than as a rubric that is quietly wrong.
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


#: A carton, from `engine/src/Parts/BoxPhysics.cs`, which owns these and which
#: no template configures: `Length` and `Width` (:22, :23), the two heights
#: (`Height`, :24), and the two densities (`CartonDensity` :29,
#: `MetalDensity` :33). Length is along the belt, so it is also how long a
#: carton holds a beam broken as it passes.
CARTON_LENGTH = 0.20
CARTON_WIDTH = 0.24
SHORT_HEIGHT = 0.10
TALL_HEIGHT = 0.30
CARDBOARD_DENSITY = 150.0
STEEL_DENSITY = 900.0

#: `engine/src/Parts/PartLayout.cs`: every part's origin sits on the work
#: plane, and a belt deck is this thick, centred on it -- so a carton rests on
#: the deck at WORK_PLANE_Y + BELT_THICKNESS / 2. No template sets either.
WORK_PLANE_Y = 0.5
BELT_THICKNESS = 0.12
BELT_SURFACE_Y = WORK_PLANE_Y + BELT_THICKNESS / 2


def pot_start(plant, panel_id: str = "panel") -> float:
    """Where the template's pot sits when the scene opens (IP-29).

    `ButtonPanel.cs` applies the template's `setpoint` through `ApplySetpoint`,
    which clamps it to the plate's `setpoint_min` .. `setpoint_max`, and
    publishes it from the first tick. The models used to start every pot at 0
    until the exam turned it -- a pot the engine never shows.
    """
    panel = plant.part(panel_id, "ButtonPanel")
    low, high = panel.number("setpoint_min"), panel.number("setpoint_max")
    value = panel.number("setpoint")
    return min(max(value, low), high) if high > low else low


def remover_catch(remover, deck_end: float) -> float:
    """Where along the line a `Remover` takes a carton travelling along +X.

    `Remover.cs` is an `Area3D`: it takes a carton on `BodyEntered`, the moment
    the two colliders first overlap. When the zone reaches up past the deck's
    carrying surface -- as every graded template's end-of-line remover does,
    0.0 to 0.6 m high against a deck at 0.56 m -- the carton is taken while it
    is still riding the belt, as its leading face crosses the zone's near
    face: centre at `near face - CARTON_LENGTH / 2`, a tenth of a metre before
    the belt ends. The models used to retire a carton at the belt's end, or
    further on.

    A zone wholly below the deck catches what falls off its end instead, and
    this model has no fall: it says so by taking the carton at `deck_end`.
    """
    near = remover.x - remover.number("zone_x") / 2
    top = remover.position[1] + remover.number("zone_y") / 2
    if top > BELT_SURFACE_Y:
        return near - CARTON_LENGTH / 2
    return max(deck_end, near - CARTON_LENGTH / 2)


class EngineFeed:
    """What the engine's `Emitter` makes, carton by carton (IP-29).

    `Emitter.cs`: every `metal_every`-th carton is steel (0 = never), and with
    `tall_every` at its default of -1 the height comes from the host's shared
    alternation -- `SceneEditor.NextAlternate`, which starts short after a
    reset -- so the stream is short, tall, short, tall. A template can set
    `tall_every` N instead: every Nth is tall, 0 never, 1 always.

    Two scenes deliberately do not use this: the light curtain and the
    checkweigher feed a shuffled mix, because their exams are about the mix
    (docs/GRADING.md, "The feed patterns are shuffled").
    """

    def __init__(self, emitter) -> None:
        self.metal_every = int(emitter.number("metal_every"))
        tall_every = emitter.properties.get("tall_every")
        self.tall_every = -1 if tall_every is None else int(float(tall_every))
        self._emitted = 0
        self._shaped = 0
        self._alternate = False

    def next(self) -> tuple[float, bool]:
        """`(height, metal)` of the next carton."""
        self._emitted += 1
        metal = self.metal_every > 0 and self._emitted % self.metal_every == 0
        if self.tall_every < 0:
            tall, self._alternate = self._alternate, not self._alternate
        else:
            self._shaped += 1
            tall = self.tall_every > 0 and self._shaped % self.tall_every == 0
        return (TALL_HEIGHT if tall else SHORT_HEIGHT), metal


@dataclass
class Item:
    """A carton, in one dimension. Height and mass are the plant's secret.

    `height` is metres, `mass` kilograms -- both from `BoxPhysics.cs`, where a
    carton is 0.20 x H x 0.24 at 150 kg/m3 and a steel one at 900.
    """
    height: float = SHORT_HEIGHT
    metal: bool = False
    position: float = 0.0
    id: int = 0
    lane: str | None = None          #: set once it leaves the line
    measured: float | None = None    #: what an instrument said about it
    threshold: float | None = None   #: the rule in force when it was measured
    carried: bool = False

    @property
    def mass(self) -> float:
        density = STEEL_DENSITY if self.metal else CARDBOARD_DENSITY
        return CARTON_LENGTH * self.height * CARTON_WIDTH * density

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

    def at(self, when: float, what) -> None:
        """Add a step during the run, for an exam whose next move waits on
        what the plant did -- a guard the program keeps locked opens when it
        opens, not when the sheet said. A step already due runs on the next
        tick; the order among steps still to come is by time."""
        pending = self._steps[self._next:] + [(when, what)]
        self._steps = self._steps[:self._next] + sorted(pending, key=lambda s: s[0])

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

    #: How long a press holds a momentary contact closed, and then the least
    #: time it stays open before the next press on the same button can close
    #: it again: `ButtonPanel.DefaultPressHold` (`ButtonPanel.cs`, IP-31),
    #: which no template overrides (`tests/test_grade_templates.py` holds both
    #: to that). The grader used to hold 0.15 s against the engine's one
    #: physics tick; IP-31 raised the engine to 0.2 s and IP-34 brought the
    #: examiner's hand to the same number, so a program is marked against the
    #: press it will meet in the 3D scene.
    PRESS = 0.2

    def __init__(self, tags: TagTable, prefix: str = "panel",
                 setpoint: float = 0.0) -> None:
        self.tags = tags
        self.prefix = prefix
        #: One momentary contact per button, as `ButtonPanel.StepContact`
        #: moves it: `[phase, ticks left, requested]`, phase "idle", "high"
        #: (closed) or "low" (open, and not yet open for long enough).
        self._contacts: dict[str, list] = {
            name: ["idle", 0, False] for name in ("start", "stop", "reset")}
        #: Presses that landed while their button was already down, and so
        #: were the press in progress rather than a new edge -- what the
        #: engine does with a double-click. No exam does this on purpose.
        self.absorbed: list[tuple[float, str]] = []
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
        """A press, as a click on the engine's cap is one: a request the
        contact acts on at its next tick. While the button is still down it
        is the same press (`absorbed`); while it is re-opening it waits out
        the gap and then makes its own edge."""
        def do() -> None:
            contact = self._contacts[name]
            if contact[0] == "high":
                self.absorbed.append((round(self._t, 2), name))
                return
            contact[2] = True
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
        hold = self.hold_ticks(dt)
        for name, contact in self._contacts.items():
            self._step_contact(contact, hold)
            self.tags.set(f"{p}.{name}", contact[0] == "high")
        self.tags.set(f"{p}.estop", self.healthy)
        self.tags.set(f"{p}.setpoint", float(self.setpoint_value))

    @classmethod
    def hold_ticks(cls, dt: float) -> int:
        """`PRESS` in whole ticks, rounded up and never less than one --
        `ButtonPanel.PressHoldTicks`, on this clock's tick."""
        return max(1, math.ceil(cls.PRESS / dt - 1e-6))

    @staticmethod
    def _step_contact(contact: list, hold: int) -> None:
        """`ButtonPanel.StepContact`, line for line: closed for `hold` ticks,
        then open for at least `hold` ticks, and a request waits for that."""
        phase, left, requested = contact
        if phase == "high":
            contact[2] = False                 # already down: the same press
            if left > 0:
                contact[1] = left - 1
                return
            contact[0], contact[1] = "low", hold - 1
            return
        if phase == "low":
            if left > 0:
                contact[1] = left - 1
                return
            contact[0] = "idle"
        if not contact[2]:
            return
        contact[0], contact[1], contact[2] = "high", hold - 1, False

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

    def __init__(self, seed: int, setpoint: float = 0.0) -> None:
        """`setpoint` is where the pot sits before the exam touches it: a
        templated scene passes `pot_start(template)`, which is what the
        engine's panel shows from its first tick."""
        self.rng = random.Random(seed)
        self.seed = seed
        self.t = 0.0
        self.tags = TagTable([])
        self.panel = Panel(self.tags, setpoint=setpoint)
        self.panel.declare(self.tags)
        self.script = Script([])
        self.notes: dict = {}
        #: The E-stop / Start / Reset sheet, on a scene that marks it through
        #: `OperatorExam` (IP-12). The scene sets `operator.driven` in its
        #: `step`; `tick` does the rest.
        self.operator: OperatorExam | None = None

    # --- reading what the controller wrote ---

    def bit(self, tag_id: str) -> bool:
        return bool(self.tags.visible(tag_id))

    def num(self, tag_id: str) -> float:
        return float(self.tags.visible(tag_id))

    # --- the loop ---

    def tick(self, dt: float) -> None:
        self.t += dt
        self.script.run(self.t)
        operator = self.operator
        if operator is not None:
            operator.poll(self.t)       # the strike lands before the panel ticks
        self.panel.tick(dt, self.t)
        self.step(dt)
        if operator is not None:
            operator.ledger.step(self.t, dt if operator.driven else 0.0)

    def step(self, dt: float) -> None:            # pragma: no cover - overridden
        raise NotImplementedError

    # --- helpers the models share ---

    def _declare(self, *tags: Tag) -> None:
        for tag in tags:
            self.tags.add(tag)

    def _eye(self, items: list[Item], position: float,
             window: float = CARTON_LENGTH) -> bool:
        """A diffuse photoelectric sensor: true while an item is in its window.

        Matches `sorting_scene.py`, which is in turn the semantics
        `PhotoelectricSensor.cs` gives a diffuse head: a beam across the belt
        is broken for as long as a carton's length covers it.
        """
        half = window / 2
        return any(abs(item.position - position) <= half for item in items)


#: `VariableConveyor.cs` (`DeadBand`): below this actual speed, in percent,
#: the drive counts as stopped and the belt does not move. No template sets it.
VFD_DEAD_BAND = 0.5


class Vfd:
    """A belt behind a variable-frequency drive, as `VariableConveyor.cs`
    (`StepDrive`) runs it: the actual speed chases the reference at the
    template's `accel_rate`, in both directions, and dropping `run` aims the
    ramp at zero rather than cutting the speed -- the belt coasts down. The
    models used to stop the belt dead the tick `run` fell (IP-29)."""

    def __init__(self, max_speed: float, accel_rate: float) -> None:
        self.max_speed = max_speed
        self.accel_rate = accel_rate
        self.actual = 0.0
        self.reference = 0.0
        #: What the ramp is aiming at this tick, in percent: zero once `run`
        #: drops. `driven` is the operator contract's reading of the drive.
        self.target = 0.0

    def step(self, run: bool, reference: float, dt: float,
             faulted: bool = False) -> float:
        """One tick; returns the belt's surface speed in m/s."""
        self.reference = min(max(reference, 0.0), 100.0)
        target = self.target = 0.0 if faulted or not run else self.reference
        step = self.accel_rate * dt
        self.actual += max(min(target - self.actual, step), -step)
        return self.speed

    @property
    def speed(self) -> float:
        return (self.max_speed * self.actual / 100.0
                if self.actual > VFD_DEAD_BAND else 0.0)

    @property
    def driven(self) -> bool:
        """The drive is being told to turn the belt. A stopped drive still
        coasts down its own ramp, as `VariableConveyor.cs` does -- a category
        1 stop -- so the E-stop is marked on this, not on the belt's speed."""
        return self.target > VFD_DEAD_BAND


#: `TurnTable.cs` (`DeckSpeed`): the deck rollers' surface speed while `deck`
#: is on, in m/s. A template may set `deck_speed`; this is the part's default.
DECK_SPEED = 0.5


def deck_lane_velocity(running: bool, angle_deg: float, speed: float = DECK_SPEED,
                       direction: tuple[float, float] = (1.0, 0.0)) -> tuple[float, float]:
    """The surface velocity `(x, z)`, in m/s, a turntable's deck hands a
    carton on it, as `TurnTable.SetDeckRunning` computes it (IP-33).

    A carton on the deck moves only while the deck runs: off, the deck hands
    the solver nothing. On, it is `speed` along the deck's own `direction`
    (`deck_dir`, +X by default) carried round by the deck's angle -- Godot
    turns a body about +Y by a positive angle from +X towards -Z -- so the lane
    points where the lane *is*, not where it was when the drive started. The
    drive has a motor of its own: the index drive's fault does not stop it,
    which is why there is no fault argument.

    No graded scene calls this yet. `rotary-index` drops its cartons on the
    deck and sweeps them off, and models one axis; a belt-fed turntable scene
    (open, IP-33) would step its cartons with this."""
    length = math.hypot(*direction)
    if not running or length < 1e-3:
        return (0.0, 0.0)
    a = math.radians(angle_deg)
    dx, dz = direction[0] / length, direction[1] / length
    return (speed * (dx * math.cos(a) + dz * math.sin(a)),
            speed * (dz * math.cos(a) - dx * math.sin(a)))


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


class TripLedger:
    """The operator contract, measured as belt travel (IP-35).

    One ledger for every scene whose panel stops a belt, so "the mushroom
    stops it, only Reset *then* Start restarts it" is measured the same way
    wherever it is marked. Ticked once per plant step, after the panel, with
    the metres the belt really moved that tick. It reads the panel's contacts
    -- the edges a program sees -- not the examiner's intentions.

    A trip runs through three phases, and the travel in each is kept apart
    because each is a different mistake:

    * **struck** -- mushroom in. Belt here beyond `ESTOP_LIMIT` of travel is a
      program that did not stop, or stopped too slowly.
    * **latched** -- mushroom out, no Reset yet; then **reset** -- Reset seen,
      no Start yet. Any belt here is a program whose trip did not latch: it
      restarted on the release, on Start alone, or on Reset alone.
    * cleared by the first Start edge after a Reset edge after the release.

    It also keeps every moment the belt began moving with no Start edge since
    it last stopped -- the guarded cell's "started without a press", for a
    belt rather than a contactor.

    Until IP-35 the start / stop station ended its trip on *any* Start after
    the strike, so a program that restarted on Start alone, with no Reset,
    passed it: 5 mm of belt, all of it the stop lag.
    """

    def __init__(self, panel: "Panel", struck=None) -> None:
        """`struck` says whether the trip's cause is present this tick: by
        default the mushroom, pressed in. A scene that also marks a fault
        passes the fault instead and keeps a second ledger, so the sorting
        line's drive fault is measured in the same phases as its E-stop."""
        self.panel = panel
        self._struck = struck or (lambda: not panel.healthy)
        self.phase = "clear"
        #: One record per trip: when it was struck, released, reset and
        #: cleared, how far the belt moved in each phase, how long it took
        #: to stop, and whether the belt was moving at the strike.
        self.trips: list[dict] = []
        self.started_without_a_press: list[float] = []
        #: Every moment the plant began to move while nobody had started it:
        #: before the first Start edge, or after a Stop edge with no Start
        #: since, and never inside a trip, which has checks of its own. The
        #: reading of "started without a press" for a plant that stops and
        #: starts by itself while it runs -- a valve that closes at setpoint,
        #: an axis dwelling at a station -- where "moving began with no Start
        #: since it last stopped" would flag every normal cycle (IP-12).
        self.began_unstarted: list[float] = []
        self._moving = False
        self._start_since_stop = False
        self._operator_stopped = True
        self._edge = {"start": False, "reset": False, "stop": False}

    @property
    def moving(self) -> bool:
        """Whether the plant moved on the last tick."""
        return self._moving

    def _rising(self, name: str) -> bool:
        now = bool(self.panel.tags.visible(f"{self.panel.prefix}.{name}"))
        rose = now and not self._edge[name]
        self._edge[name] = now
        return rose

    def step(self, now: float, moved: float) -> None:
        start, reset = self._rising("start"), self._rising("reset")
        stopped = self._rising("stop")
        healthy = not self._struck()
        moving = moved > 0.0
        if start:
            self._start_since_stop = True

        trip = self.trips[-1] if self.trips else None
        if not healthy and self.phase != "struck":
            trip = {"struck_at": round(now, 2), "moving_at_strike": self._moving,
                    "released_at": None, "reset_at": None, "cleared_at": None,
                    "stop_lag_s": None, "struck_travel_m": 0.0,
                    "latched_travel_m": 0.0, "latched_moved_at": None,
                    "restarted_at": None, "last_moved_after_s": None}
            self.trips.append(trip)
            self.phase = "struck"
        elif self.phase == "struck" and healthy:
            trip["released_at"] = round(now, 2)
            self.phase = "latched"
        elif self.phase == "latched" and reset:
            trip["reset_at"] = round(now, 2)
            self.phase = "reset"
        elif self.phase == "reset" and start:
            trip["cleared_at"] = round(now, 2)
            self.phase = "cleared"

        if self.phase == "struck":
            trip["struck_travel_m"] += moved
            if trip["stop_lag_s"] is None and not moving:
                trip["stop_lag_s"] = round(now - trip["struck_at"], 3)
            if moving:
                trip["last_moved_after_s"] = round(now - trip["struck_at"], 3)
        elif self.phase in ("latched", "reset"):
            trip["latched_travel_m"] += moved
            if moving and trip["latched_moved_at"] is None:
                trip["latched_moved_at"] = round(now, 2)
        elif self.phase == "cleared" and moving and trip["restarted_at"] is None:
            trip["restarted_at"] = round(now, 2)

        in_trip = self.phase in ("struck", "latched", "reset")
        if stopped:
            self._operator_stopped = True
        elif start and not in_trip:
            self._operator_stopped = False
        if moving and not self._moving and self._operator_stopped and not in_trip:
            self.began_unstarted.append(round(now, 2))

        if moving and not self._moving and not self._start_since_stop:
            self.started_without_a_press.append(round(now, 2))
        if self._moving and not moving:
            self._start_since_stop = start      # it stopped: a new start is owed
        self._moving = moving

    @property
    def tripped_travel(self) -> float:
        """Metres of belt while any trip was struck or latched."""
        return sum(t["struck_travel_m"] + t["latched_travel_m"] for t in self.trips)


# --- the operator contract, on any plant (IP-12) ---------------------------
#
# The sorting line's sheet (IP-35), timed from the strike: the mushroom in,
# released, Start alone (must do nothing), Reset (must do nothing either), and
# Start (must bring the plant back). Written once here, so the fifteen scenes
# that put it after their own exam put the same sheet, and `scenes/_contract.py`
# marks it the same way on all of them.

#: How long the examiner waits, from reaching for the mushroom, for a moment
#: the scene calls fair to strike -- the plant running and nothing in a state
#: an E-stop would spoil for reasons that are not the program's.
ESTOP_WAIT = 4.0
ESTOP_RELEASE_AFTER = 2.0
ESTOP_START_ALONE_AFTER = 3.0
ESTOP_RESET_AFTER = 4.5
ESTOP_RESTART_AFTER = 6.0
#: How soon after the last Start the plant has to be moving again.
ESTOP_RESTART_WITHIN = 1.0


def operator_exam_ends_by(reach_at: float, wait: float = ESTOP_WAIT,
                          restart_within: float = ESTOP_RESTART_WITHIN) -> float:
    """The shortest window a sheet that reaches for the mushroom at
    `reach_at` fits in, whatever the wait turns out to be -- plus a tenth of
    a second, because a strike at the end of the wait lands on the first tick
    past it, not on the deadline itself."""
    return reach_at + wait + ESTOP_RESTART_AFTER + restart_within + 0.1


class OperatorExam:
    """The E-stop / Start / Reset sheet, put to any plant (IP-12).

    The scene says what "stopped" means for it by setting `driven` every
    `step` -- the belt turning, the pump being run, the heater on, the axis
    driven -- and, if it needs one, a `ready` test for a moment that is fair
    to strike in. `PlantScene.tick` polls the sheet before the panel and
    ticks the `TripLedger` after the step, so the ledger measures seconds of
    the plant being driven in each phase of the trip.

    `driven` is always the plant obeying a command the program could have
    dropped, never a motion the program cannot recall: a drive coasting down
    its own ramp, a quick-stopping servo, a turntable finishing an index it
    has no way to abandon. Where a scene has such a motion, its `ready` waits
    for a moment it is not happening, and says so in its own file.
    """

    def __init__(self, scene: "PlantScene", reach_at: float, *, what: str,
                 noun: str, ready=None, wait: float = ESTOP_WAIT,
                 restart_within: float = ESTOP_RESTART_WITHIN,
                 on_strike=None) -> None:
        """`what` is the thing that stops, in words ("the belt", "the
        pump"), and `noun` the word the check id ends in
        (`estop.stopped_the_belt`)."""
        self.scene = scene
        self.panel = scene.panel
        self.ledger = TripLedger(scene.panel)
        self.reach_at = reach_at
        self.what = what
        self.noun = noun
        self.wait = wait
        self.restart_within = restart_within
        self.ends_by = operator_exam_ends_by(reach_at, wait, restart_within)
        self._ready = ready
        self._on_strike = on_strike
        #: Set by the scene every step: is the plant being driven this tick.
        self.driven = False
        #: What the examiner did and when. None until it happens.
        self.sheet: dict = {"reached_for_the_mushroom_at": None, "struck_at": None,
                            "waited_for_a_fair_moment": None,
                            "released_at": None, "start_alone_at": None,
                            "reset_at": None, "restart_at": None}
        self._deadline: float | None = None
        scene.script.at(reach_at, self._reach)

    def _reach(self) -> None:
        self.sheet["reached_for_the_mushroom_at"] = round(self.scene.t, 2)
        self._deadline = self.scene.t + self.wait

    _reach.__name__ = "reach for the mushroom"

    def poll(self, now: float) -> None:
        if self._deadline is None:
            return
        fair = self.ledger.moving and (self._ready is None or bool(self._ready()))
        if not fair and now < self._deadline:
            return
        self._deadline = None
        self.sheet["struck_at"] = round(now, 2)
        self.sheet["waited_for_a_fair_moment"] = fair
        if self._on_strike is not None:
            self._on_strike()
        self.panel.strike()()
        script = self.scene.script
        script.at(now + ESTOP_RELEASE_AFTER, self._note("released_at", self.panel.release()))
        script.at(now + ESTOP_START_ALONE_AFTER,
                  self._note("start_alone_at", self.panel.press("start")))
        script.at(now + ESTOP_RESET_AFTER, self._note("reset_at", self.panel.press("reset")))
        script.at(now + ESTOP_RESTART_AFTER,
                  self._note("restart_at", self.panel.press("start")))

    def _note(self, key: str, action):
        def do() -> None:
            action()
            self.sheet[key] = round(self.scene.t, 2)
        do.__name__ = action.__name__
        return do

    @property
    def trip(self) -> dict | None:
        return self.ledger.trips[0] if self.ledger.trips else None

    def finished(self, window: float) -> bool:
        """The sheet ran to its end, and the window lasted long enough after
        the last Start to see whether the plant came back."""
        restart = self.sheet["restart_at"]
        return restart is not None and window >= restart + self.restart_within - 1e-6


# --- tags the engine declares and no rubric reads -------------------------
#
# A student is handed the scene's tag list and writes a mapping against it, so
# the grader has to offer the same list the engine does -- ids, types and
# kinds -- or a mapping that works on the 3D scene fails to connect to the
# exam. `tests/test_grade_templates.py` holds every graded scene to the tag set
# the engine registers. Some of those tags are the plant's business and are
# modelled; these two kinds are declared and nothing more.

def declare_stack_light(tags: TagTable, prefix: str = "tower") -> None:
    """A `StackLight`'s three lamps: outputs the program may write, and which
    nothing on the line reads -- `StackLight.cs` only lights them."""
    for colour in ("green", "yellow", "red"):
        tags.add(Tag(f"{prefix}.{colour}", f"Stack Light {colour.title()}",
                     "bit", "output"))


def fault_input(prefix: str, title: str) -> Tag:
    """A part's `.fault` input. Declared so the tag list matches the scene a
    student is handed. Most exams never raise it, and then the plant never
    reads it back either. The ones that do (the sorting line's drive, the
    dosing pump, the oven's element -- docs/GRADING.md, "Fault injection")
    raise it from the plant side with `tags.set`, never a force, and make the
    model obey it: a rubric that starts injecting one must do both."""
    return Tag(f"{prefix}.fault", title, "bit", "input")
