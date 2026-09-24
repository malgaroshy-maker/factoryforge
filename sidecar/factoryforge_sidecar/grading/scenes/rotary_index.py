"""`rotary-index`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/rotary_index.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (CARTON_LENGTH, CARTON_WIDTH, EngineFeed, Item, PlantScene, Script,
                     declare_stack_light, fault_input, pot_start)
from ..templates import TemplateError, template


SCENE = "rotary-index"


# --- rotary index station ---------------------------------------------------
#
# Observable fact: the deck's angle and the ram's extension, every tick, and
# where each carton was. The plant knows the angle the deck stood at when the
# pusher's plate met each carton, whether the deck turned while the plate was
# out over it, and whether two cartons were ever on the deck at once -- and a
# program can report none of those, only cause them.
#
# The lesson is a rotary index: two motions that share one space, each of
# which has to finish before the other may start. The deck turns a quarter
# and the pusher sweeps the carton off it onto the outfeed; push before the
# deck is square at its index and the carton leaves skewed, turn the deck
# while the plate is still over it and the plate is in the way of whatever
# the deck carries. Two limit switches on the deck and two reeds on the
# cylinder are the whole interlock, and neither `!extended` nor a timer is a
# substitute for them.
#
# How a program fakes it: by timing the index. A quarter turn at the deck's
# rated 55 deg/s takes 1.64 s, and a stopwatch set to that pushes square --
# once. So the exam slows the deck mid-run (the template's `index_speed`,
# cut to 40-60 % by the examiner's hand, as the inspector's slider does in the
# engine), and a pusher on a timer meets the carton at half a turn.
#
# Why a pusher at all: the deck has no rollers (`TurnTable.cs`), so a carton
# fed onto it by a belt stops at the joint, and one on it stays on it. The
# emitter drops each carton onto the deck, and the cylinder sweeps it off --
# which is also how the engine runs this scene, checked in 3-D (IP-14).
#
# Parts: `TurnTable.cs` (`UpdateIndex`, `IsHome`, `IsAtIndex`),
# `PneumaticCylinder.cs`, the retroreflective `PhotoelectricSensor.cs`, a
# `ConveyorBelt` turned to run along +Z, and a `Remover`. Every position and
# rate is the template's.
_PLANT = template(SCENE)
_TABLE = _PLANT.part("table", "TurnTable")
_PUSHER = _PLANT.part("pusher", "PneumaticCylinder")
_EYE = _PLANT.part("deck_eye", "RetroreflectiveSensor")
_BELT = _PLANT.part("outfeed", "ConveyorBelt")
_DONE = _PLANT.part("done", "Remover")
_EMITTER = _PLANT.part("emitter", "Emitter")

RI_RADIUS = _TABLE.number("deck_radius")
RI_INDEX = _TABLE.number("index_angle")
RI_SPEED_FIRST = _TABLE.number("index_speed")
RI_STROKE = _PUSHER.number("stroke")
RI_ROD_SPEED = _PUSHER.number("rod_speed")
RI_REED_BAND = _PUSHER.number("reed_band")
RI_PLATE_HALF = _PUSHER.number("plate_width") / 2
RI_BELT_SPEED = _BELT.number("speed")
RI_POT_START = pot_start(_PLANT)
#: `TurnTable.cs` (`IsHome`, `IsAtIndex`): within half a degree of either end.
RI_LIMIT_BAND = 0.5
#: `PneumaticCylinder.cs`: the rod's 0.02 m stub and the plate's 0.03 m.
RI_ROD_STUB = 0.02
RI_PLATE_THICKNESS = 0.03
#: The exam slows the deck to one of these fractions of its rating, from the
#: seed.
RI_SLOWED = (0.4, 0.5, 0.6)
RI_SLOW_AT = 24.0
#: A carton leaves "square" if the deck was within this of its index angle
#: when the plate met it, in degrees.
RI_SQUARE = 2.0

# The layout the model assumes, checked rather than trusted: the emitter over
# the deck's centre, the pusher behind the deck stroking +Z across it, the eye
# a beam along -X through the deck's centre, the outfeed turned to run +Z
# from the deck's far rim, and the remover past its end.
_X0, _Z0 = _TABLE.x, _TABLE.position[2]
_QUARTER = math.pi / 2


def _turned(part, radians: float) -> bool:
    return (abs(part.rotation[0]) < 1e-6 and abs(part.rotation[2]) < 1e-6
            and abs(part.rotation[1] - radians) < 1e-4)


if not (abs(_EMITTER.x - _X0) < 1e-6 and abs(_EMITTER.position[2] - _Z0) < 1e-6
        and abs(_PUSHER.x - _X0) < RI_PLATE_HALF and _turned(_PUSHER, 0.0)
        and _PUSHER.position[2] < _Z0 - RI_RADIUS
        and _turned(_EYE, _QUARTER) and abs(_EYE.position[2] - _Z0) < 1e-6
        and _EYE.x - _EYE.number("range") < _X0 < _EYE.x
        and _turned(_BELT, -_QUARTER) and abs(_BELT.x - _X0) < 1e-6):
    raise TemplateError(f"{_PLANT.path.name}: the rotary index's layout is not the one "
                        f"the grader models")
#: The outfeed's two ends along Z (turned -90 deg, its length runs along +Z).
RI_BELT_FROM = _BELT.position[2] - _BELT.number("size_x") / 2
RI_BELT_TO = _BELT.position[2] + _BELT.number("size_x") / 2
#: Where the remover takes a carton riding +Z: its near face, less half a carton.
RI_TAKEN_AT = _DONE.position[2] - _DONE.number("zone_z") / 2 - CARTON_LENGTH / 2
#: The plate is over the deck once its front passes the deck's near rim.
RI_PLATE_OVER_DECK = (_Z0 - RI_RADIUS) - (_PUSHER.position[2] + RI_ROD_STUB + RI_PLATE_THICKNESS)
if RI_PLATE_OVER_DECK <= 0.0:
    raise TemplateError(f"{_PLANT.path.name}: the pusher's retracted plate is already over "
                        f"the deck")


def half_depth(turn_degrees: float) -> float:
    """How far a carton reaches along Z from its centre, turned `turn_degrees`
    from how the emitter dropped it (length along X, width along Z)."""
    a = math.radians(turn_degrees)
    return CARTON_LENGTH / 2 * abs(math.sin(a)) + CARTON_WIDTH / 2 * abs(math.cos(a))


class RotaryIndexScene(PlantScene):
    name = "rotary-index"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=RI_POT_START)
        self._declare(
            Tag("emitter.emit", "Emitter 0 (Emit)", "bit", "output"),
            Tag("table.index", "Turntable 0 (Index)", "bit", "output"),
            # The deck drive (IP-33), declared because the engine declares it:
            # the grader offers exactly the engine's tags. This scene drops
            # cartons on and pushes them off, so the model does not move a
            # carton for it -- a program that runs the rollers here is marked as
            # if it had not.
            Tag("table.deck", "Turntable 0 (Deck Drive)", "bit", "output"),
            Tag("table.athome", "Turntable 0 (At Home)", "bit", "input", value=True),
            Tag("table.atindex", "Turntable 0 (At Index)", "bit", "input"),
            fault_input("table", "Turntable 0 Drive Fault"),
            Tag("pusher.extend", "Cylinder 0 Extend Coil", "bit", "output"),
            Tag("pusher.retract", "Cylinder 0 Retract Coil", "bit", "output"),
            Tag("pusher.extended", "Cylinder 0 Extended Reed", "bit", "input"),
            Tag("pusher.retracted", "Cylinder 0 Retracted Reed", "bit", "input", value=True),
            fault_input("pusher", "Cylinder 0 Seized"),
            Tag("deck_eye.detect", "Sensor 0 (Detect)", "bit", "input"),
            Tag("outfeed.rotate", "Conveyor 0 (Rotate)", "bit", "output"),
            fault_input("outfeed", "Conveyor 0 Drive Fault"),
            Tag("done.count", "Remover 0 (Count)", "int", "input"),
        )
        declare_stack_light(self.tags)
        self.angle = 0.0
        self.index_speed = RI_SPEED_FIRST
        self.slowed = float(self.rng.choice(RI_SLOWED))
        self.extension = 0.0
        self.spool_extends = False
        self.feed = EngineFeed(_EMITTER)
        self._emit_edge = False
        self.items: list[Item] = []
        self._next_id = 1

        # --- ground truth ---
        #: One entry per carton: dropped at (deck angle), met by the plate at
        #: (deck angle, time), delivered at.
        self.ledger: dict[int, dict] = {}
        self.delivered: list[Item] = []
        #: Degrees the deck turned while the plate was out over it.
        self.turned_under_the_plate = 0.0
        self.turned_under_the_plate_at: list[float] = []
        #: Times two cartons were on the deck at once.
        self.crowded_at: list[float] = []
        self._crowded = False

        self.script = Script([
            (1.0, self.panel.press("start")),
            (RI_SLOW_AT, self._slow_the_deck),
        ])

    def _slow_the_deck(self) -> None:
        """What the turntable's "Index Speed" slider does in the engine. No
        tag reports it; `atindex` does, when the deck gets there."""
        self.index_speed = RI_SPEED_FIRST * self.slowed

    def _on_deck(self, item: Item) -> bool:
        return item.position - _Z0 < RI_RADIUS

    def step(self, dt: float) -> None:
        # The emitter: a carton per rising edge, dropped on the deck's centre.
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            height, metal = self.feed.next()
            item = Item(height=height, metal=metal, position=_Z0, id=self._next_id)
            self._next_id += 1
            self.items.append(item)
            self.ledger[item.id] = {"dropped_at": round(self.t, 2),
                                    "deck_at_drop": round(self.angle, 1),
                                    "met_at": None, "deck_at_push": None,
                                    "delivered_at": None}
        self._emit_edge = emit

        # The deck (`UpdateIndex`).
        was_angle = self.angle
        target = RI_INDEX if self.bit("table.index") else 0.0
        step = self.index_speed * dt
        self.angle += max(min(target - self.angle, step), -step)
        self.tags.set("table.athome", self.angle <= RI_LIMIT_BAND)
        self.tags.set("table.atindex", self.angle >= RI_INDEX - RI_LIMIT_BAND)

        # The pusher (5/2 valve, no spring).
        extend, retract = self.bit("pusher.extend"), self.bit("pusher.retract")
        if extend and not retract:
            self.spool_extends = True
        elif retract and not extend:
            self.spool_extends = False
        target = RI_STROKE if self.spool_extends else 0.0
        step = RI_ROD_SPEED * dt
        self.extension += max(min(target - self.extension, step), -step)
        self.tags.set("pusher.extended", self.extension >= RI_STROKE - RI_REED_BAND)
        self.tags.set("pusher.retracted", self.extension <= RI_REED_BAND)
        front = _PUSHER.position[2] + self.extension + RI_ROD_STUB + RI_PLATE_THICKNESS

        if self.extension > RI_PLATE_OVER_DECK and abs(self.angle - was_angle) > 1e-9:
            self.turned_under_the_plate += abs(self.angle - was_angle)
            if not self.turned_under_the_plate_at or \
                    self.t - self.turned_under_the_plate_at[-1] > 1.0:
                self.turned_under_the_plate_at.append(round(self.t, 2))

        # The cartons: on the deck they turn with it until the plate meets
        # them; the plate sweeps them to +Z; on the outfeed they ride it.
        belt = self.bit("outfeed.rotate") and not self.bit("outfeed.fault")
        still: list[Item] = []
        for item in self.items:
            record = self.ledger[item.id]
            turn = (self.angle - record["deck_at_drop"] if record["met_at"] is None
                    else record["deck_at_push"] - record["deck_at_drop"])
            reach = half_depth(turn)
            if front >= item.position - reach:
                if record["met_at"] is None:
                    record["met_at"] = round(self.t, 2)
                    record["deck_at_push"] = round(self.angle, 1)
                item.position = front + reach
            if belt and RI_BELT_FROM <= item.position <= RI_BELT_TO + CARTON_LENGTH:
                item.position += RI_BELT_SPEED * dt
            if item.position >= RI_TAKEN_AT:
                record["delivered_at"] = round(self.t, 2)
                item.lane = "outfeed"
                self.delivered.append(item)
            else:
                still.append(item)
        self.items = still

        on_deck = [i for i in self.items if self._on_deck(i)]
        crowded = len(on_deck) > 1
        if crowded and not self._crowded:
            self.crowded_at.append(round(self.t, 2))
        self._crowded = crowded

        def in_beam(item: Item) -> bool:
            record = self.ledger[item.id]
            turn = (self.angle if record["met_at"] is None else record["deck_at_push"]) \
                - record["deck_at_drop"]
            return abs(item.position - _Z0) <= half_depth(turn)

        plate = front - RI_PLATE_THICKNESS <= _Z0 <= front
        self.tags.set("deck_eye.detect", plate or any(in_beam(i) for i in self.items))
        self.tags.set("done.count", len(self.delivered))


def grade_rotary_index(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: RotaryIndexScene = watched.inner
    pushed = [dict(id=k, **v) for k, v in sim.ledger.items() if v["met_at"] is not None]
    skewed = [p for p in pushed
              if abs(p["deck_at_push"] - p["deck_at_drop"] - RI_INDEX) > RI_SQUARE]
    delivered = len(sim.delivered)
    after_slow = [p for p in pushed if p["met_at"] >= RI_SLOW_AT]

    report.evidence.update({
        "index_speed_first": RI_SPEED_FIRST,
        "index_speed_then": round(RI_SPEED_FIRST * sim.slowed, 2),
        "slowed_at": RI_SLOW_AT,
        "cartons": [dict(id=k, **v) for k, v in sorted(sim.ledger.items())],
        "delivered": delivered,
        "pushed_after_the_slowdown": len(after_slow),
        "skewed": [{"id": p["id"], "turned_deg": round(p["deck_at_push"] - p["deck_at_drop"], 1),
                    "at": p["met_at"]} for p in skewed],
        "turned_under_the_plate_deg": round(sim.turned_under_the_plate, 1),
        "turned_under_the_plate_at": sim.turned_under_the_plate_at[:10],
        "crowded_at": sim.crowded_at[:10],
    })

    report.add("index.transferred",
               delivered >= 4 and len(after_slow) >= 1,
               f"{delivered} carton(s) reached the outfeed (at least 4), "
               f"{len(after_slow)} of them pushed after the deck was slowed")
    report.add("index.turned_square",
               bool(pushed) and not skewed,
               f"every carton left the deck turned {RI_INDEX:g} deg, within "
               f"{RI_SQUARE:g} deg" if pushed and not skewed else
               ("no carton was ever pushed" if not pushed else
                f"{len(skewed)} carton(s) left skewed: "
                + ", ".join(f"{s['deck_at_push'] - s['deck_at_drop']:.0f} deg at "
                            f"{s['met_at']:g}s" for s in skewed[:4])))
    report.add("index.plate_clear_while_turning",
               sim.turned_under_the_plate <= 1.0,
               f"the deck turned {sim.turned_under_the_plate:.0f} deg while the pusher's "
               f"plate was out over it (at most 1)")
    report.add("index.one_at_a_time",
               not sim.crowded_at,
               "never two cartons on the deck at once" if not sim.crowded_at else
               f"two cartons were on the deck together at {sim.crowded_at[:3]}s")

    say = report.feedback.append
    if not pushed:
        say("No carton was ever pushed off the deck. The emitter drops one on the "
            "deck's centre; `table.index` turns it a quarter, and the cylinder's "
            "plate sweeps it onto the outfeed -- `pusher.extend` and "
            "`pusher.retract` are two coils, and the two reeds say where the rod is.")
    if skewed:
        s = skewed[0]
        say(f"Carton {s['id']} was pushed with the deck at "
            f"{s['deck_at_push'] - s['deck_at_drop']:.0f} deg of its {RI_INDEX:g}. The "
            f"deck was slowed to {sim.slowed:.0%} of its speed at {RI_SLOW_AT:g}s -- nothing "
            f"on the bus says so -- and a quarter turn then takes "
            f"{RI_INDEX / (RI_SPEED_FIRST * sim.slowed):.1f}s, not "
            f"{RI_INDEX / RI_SPEED_FIRST:.1f}s. Push on `table.atindex`, which is the "
            f"deck telling you it has arrived.")
    if sim.turned_under_the_plate > 1.0:
        say(f"The deck turned {sim.turned_under_the_plate:.0f} deg while the pusher's "
            f"plate was still out over it, first at {sim.turned_under_the_plate_at[0]:g}s. "
            f"Between its two reeds the rod is neither out nor back: `not extended` is "
            f"not `retracted`. Turn the deck on `pusher.retracted`.")
    if sim.crowded_at:
        say("Two cartons were on the deck at once. Drop the next one only once the "
            "deck is home and `deck_eye.detect` says the last one has gone.")


def _summary_rotary(evidence: dict, out) -> None:
    out(f"{evidence['delivered']} delivered; deck slowed from "
        f"{evidence['index_speed_first']:g} to {evidence['index_speed_then']:g} deg/s at "
        f"{evidence['slowed_at']:g}s")
    turns = ", ".join(f"{c['deck_at_push'] - c['deck_at_drop']:.0f}"
                      for c in evidence["cartons"] if c["deck_at_push"] is not None)
    out(f"deck angle each carton was pushed at (deg): {turns or 'none'}")
    out(f"turned with the plate over the deck: {evidence['turned_under_the_plate_deg']:g} deg; "
        f"two on the deck: {evidence['crowded_at'] or 'never'}")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Rotary index station",
    "task": ("Drop a carton on the deck, turn it a quarter, sweep it onto the "
             "outfeed, and turn back for the next -- each motion only when the "
             "other has finished, on the limit switches and the reeds. This run "
             "slows the deck."),
    "build": RotaryIndexScene,
    "observe": None,
    "grade": grade_rotary_index,
    "summary": _summary_rotary,
    "duration": 60.0,
    "references": ("good", "timed", "notretracted"),
    "tags": ("emitter.emit, table.index, pusher.extend, pusher.retract, "
             "outfeed.rotate, tower.green/yellow/red, panel.green, panel.red "
             "are yours to write; table.athome, table.atindex, table.fault, "
             "pusher.extended, pusher.retracted, pusher.fault, "
             "deck_eye.detect, outfeed.fault, done.count, panel.setpoint and "
             "the buttons are the plant's."),
}
