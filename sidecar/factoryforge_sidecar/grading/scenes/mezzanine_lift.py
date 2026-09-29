"""`mezzanine-lift`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/mezzanine_lift.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (BELT_THICKNESS, CARTON_LENGTH, SHORT_HEIGHT, EngineFeed, Item,
                     OperatorExam, PlantScene, Script, fault_input,
                     operator_exam_ends_by, pot_start)
from ..templates import LEVEL_HEIGHT, TemplateError, template
from ._contract import mark_contract, summary_contract


SCENE = "mezzanine-lift"


# --- mezzanine lift ----------------------------------------------------------
#
# Observable fact: the carriage's height every tick, and for every carton
# whether it came to rest on the carriage and the height the carriage stood at
# when the carton left it. A program can report none of those, only cause them.
#
# The lesson is the lift's handshake, which is the first thing in this library
# about *where* a thing is rather than what a machine is doing: a carriage that
# holds one carton, a deck that has to be run to take it on and stopped the
# moment it is on, a call to a level that has to be *answered* -- `atlevel`
# with `level` = 1 -- before the deck may run again, and an outfeed eye that
# says the carton really has left before the carriage goes back down.
#
# How a program fakes it: by timing the climb. 0.9 m at the rated 0.75 m/s
# takes 1.2 s, and a stopwatch set to that discharges at the top -- once. So
# the exam slows the hoist mid-run (the template's `hoist_speed`, cut by the
# examiner's hand as the inspector's Hoist Speed slider does in the engine),
# and a discharge on the stopwatch runs the carton off a carriage that is still
# between floors. Nothing on the bus says the hoist was slowed; `atlevel` says
# when the carriage arrives.
#
# And by leaving the deck running. The deck is how a carton gets on and how it
# gets off, so a program that runs it to load and does not stop it on
# `occupied` carries the carton straight across the carriage and off the far
# side at the floor.
#
# The gate is the lift's own (`VerticalLift.cs`, `GateShouldOpen`): a blade
# across the infeed mouth, down only while the carriage stands empty at the
# floor. A program cannot put a second carton on the carriage and the model
# does not let it either -- a carton that arrives early waits on the belt.
#
# Parts: `VerticalLift.cs` (`Step`, `DeckHasCarton`, `GateShouldOpen`,
# `IsReady`), two `ConveyorBelt`s -- one on level 1, on a `Mezzanine` --
# two retroreflective `PhotoelectricSensor`s and two `Remover`s. Every position
# and rate is the template's; the carriage's own dimensions are the C# part's,
# held equal to it by `tests/test_grade_templates.py`.
_PLANT = template(SCENE)
_LIFT = _PLANT.part("lift", "VerticalLift")
_INFEED = _PLANT.part("infeed", "ConveyorBelt")
_OUTFEED = _PLANT.part("outfeed", "ConveyorBelt")
_DECK = _PLANT.part("deck", "Mezzanine")
_GATE_EYE = _PLANT.part("gate_eye", "RetroreflectiveSensor")
_OUT_EYE = _PLANT.part("out_eye", "RetroreflectiveSensor")
_DONE = _PLANT.part("done", "Remover")
_SPILL = _PLANT.part("spill", "Remover")
_EMITTER = _PLANT.part("emitter", "Emitter")

ML_LX = _LIFT.x
ML_LEVELS = int(_LIFT.number("levels"))
ML_SPACING = _LIFT.number("spacing")
ML_HOIST_FIRST = _LIFT.number("hoist_speed")
ML_TRANSFER = _LIFT.number("transfer_speed")
ML_TOLERANCE = _LIFT.number("tolerance")
ML_INFEED_SPEED = _INFEED.number("speed")
ML_OUTFEED_SPEED = _OUTFEED.number("speed")
ML_POT_START = pot_start(_PLANT)

#: `VerticalLift.cs`: the carriage deck's length, the fraction of it a
#: carton's centre has to be over to count as aboard (`ZoneFraction`), the
#: entry blade's height, how far down the open blade tucks below the belt
#: surface, how much of its height it gives up when closed, how fast it
#: moves, its thickness, how far beyond the deck's end it stands, and how
#: nearly down counts as open.
ML_DECK_LENGTH = 0.46
ML_ZONE_FRACTION = 0.62
ML_BLADE_HEIGHT = 0.26
ML_BLADE_TUCK = 0.03
ML_BLADE_SHORT = 0.04
ML_BLADE_SPEED = 0.9
ML_BLADE_THICKNESS = 0.028
ML_BLADE_SETBACK = 0.11
ML_GATE_OPEN = 0.02

#: The exam slows the hoist to one of these fractions of its rating, from the
#: seed. Both were chosen from the engine's geometry, not only the model's: a
#: discharge timed on the rated climb then starts with the carriage low enough
#: that the carton slides off onto the mezzanine floor *under* the outfeed,
#: which is what the engine does too. Much slower and the carton meets the
#: mezzanine's edge instead; much faster and its top catches the outfeed's
#: frame -- both of which the engine resolves unpredictably.
ML_SLOWED = (0.30, 0.35)
ML_SLOW_AT = 30.0

# --- the operator contract (IP-12) ------------------------------------------
#
# After the lifting exam, the E-stop sheet (`plant.OperatorExam`). "Stopped"
# is the infeed, the outfeed and the carriage's deck, and the carriage sent
# nowhere new. The hoist itself has no stop a program can give it: `lift.target`
# is a level, and a carriage already travelling to one arrives, as
# `VerticalLift.cs` has it. So for the carriage what counts is a new level
# called -- the tick `lift.target` changes -- and not the travel after it. The
# examiner strikes with the carriage standing at the level it was last called
# to, so nothing is in flight; a call already made inside the 200 ms is within
# the limit, and one made after it is the lift sent somewhere while the trip
# stands.

#: Where the window used to end.
ML_TESTS_END = 75.0
ML_ESTOP_AT = ML_TESTS_END + 0.5
ML_EXAM_ENDS_BY = operator_exam_ends_by(ML_ESTOP_AT)

# The layout the model assumes, checked rather than trusted: a line along +X
# at z = 0; the infeed on level 0 running up to the lift's mouth, the outfeed on
# level 1 running away from its far side, standing on the mezzanine; the lift
# serving exactly levels 0 and 1, its infeed at 0, at the editor's level pitch
# (so level 1 of the lift is level 1 of the scene); the gate eye before the
# mouth, the out eye past the joint.
_IN_FROM, _IN_TO = _INFEED.span()
_OUT_FROM, _OUT_TO = _OUTFEED.span()
_DECK_FROM, _DECK_TO = _DECK.x - _DECK.number("size_x") / 2, _DECK.x + _DECK.number("size_x") / 2
if not (all(abs(p.position[2]) < 1e-6 for p in (_LIFT, _INFEED, _OUTFEED, _EMITTER, _DECK))
        and _INFEED.level == 0 and _LIFT.level == 0 and _EMITTER.level == 0
        and _OUTFEED.level == 1 and _DECK.level == 1 and _OUT_EYE.level == 1
        and _DONE.level == 1 and _SPILL.level == 1
        and ML_LEVELS == 2 and abs(ML_SPACING - LEVEL_HEIGHT) < 1e-6
        and int(_LIFT.number("infeed_level")) == 0
        and _IN_FROM < _EMITTER.x < _IN_TO and abs(_IN_TO - (ML_LX - 0.25)) < 1e-6
        and abs(_OUT_FROM - (ML_LX + 0.25)) < 1e-6
        and _DECK_FROM <= _OUT_FROM + 0.1 and _DECK_TO >= _OUT_TO - 0.1
        and _IN_FROM < _GATE_EYE.x < ML_LX and _OUT_FROM < _OUT_EYE.x < _OUT_TO
        and abs(_LIFT.rotation[1]) < 1e-9):
    raise TemplateError(f"{_PLANT.path.name}: the mezzanine lift's layout is not the one "
                        f"the grader models")

#: The rated climb, and a carton's centre where the deck ends at either side.
ML_RATED_CLIMB = ML_SPACING / ML_HOIST_FIRST
ML_DECK_START = ML_LX - ML_DECK_LENGTH / 2
ML_DECK_END = ML_LX + ML_DECK_LENGTH / 2
#: `DeckHasCarton`: the carton's centre within this of the shaft's axis.
ML_ABOARD = ML_DECK_LENGTH * ML_ZONE_FRACTION / 2
#: The face of the entry blade a carton on the infeed stops against.
ML_BLADE_FACE = ML_LX - (ML_DECK_LENGTH / 2 + ML_BLADE_SETBACK) - ML_BLADE_THICKNESS / 2
#: The blade stands proud of the belt, and so stops a carton, once it has
#: risen this far of its travel (its top clears the belt surface).
ML_BLADE_BLOCKS = ML_BLADE_TUCK / (ML_BLADE_HEIGHT - ML_BLADE_SHORT)
#: Where the remover at the outfeed's end takes a carton riding it: its near
#: face, less half a carton -- the zone reaches above the level-1 belt.
if _DONE.world_y + _DONE.number("zone_y") / 2 <= _OUTFEED.world_y + BELT_THICKNESS / 2:
    raise TemplateError(f"{_PLANT.path.name}: the outfeed's remover does not reach up to "
                        f"the belt it is meant to empty")
ML_TAKEN_AT = _DONE.x - _DONE.number("zone_x") / 2 - CARTON_LENGTH / 2
#: A carton that leaves the carriage between floors lands on the mezzanine,
#: in the spill remover's zone, once its top clears the zone's floor; below
#: that it falls through the shaft's gap to the ground. The carriage's deck
#: is flush with a level-0 belt at height 0.
_BELT_SURFACE_0 = _INFEED.world_y + BELT_THICKNESS / 2
ML_SPILL_ABOVE = (_SPILL.world_y - _SPILL.number("zone_y") / 2) - _BELT_SURFACE_0 - SHORT_HEIGHT


class MezzanineLiftScene(PlantScene):
    name = "mezzanine-lift"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=ML_POT_START)
        self._declare(
            Tag("emitter.emit", "Emitter 0 (Emit)", "bit", "output"),
            Tag("infeed.rotate", "Conveyor 0 (Rotate)", "bit", "output"),
            fault_input("infeed", "Conveyor 0 Drive Fault"),
            Tag("gate_eye.detect", "Sensor 0 (Detect)", "bit", "input"),
            Tag("lift.target", "Lift 0 Call Level", "int", "output"),
            Tag("lift.transfer", "Lift 0 Deck Transfer", "bit", "output"),
            Tag("lift.level", "Lift 0 At Level", "int", "input"),
            Tag("lift.atlevel", "Lift 0 In Position", "bit", "input", value=True),
            Tag("lift.height", "Lift 0 Height", "float", "input"),
            Tag("lift.occupied", "Lift 0 Carriage Occupied", "bit", "input"),
            Tag("lift.ready", "Lift 0 Ready To Accept", "bit", "input"),
            fault_input("lift", "Lift 0 Drive Fault"),
            Tag("outfeed.rotate", "Conveyor 1 (Rotate)", "bit", "output"),
            fault_input("outfeed", "Conveyor 1 Drive Fault"),
            Tag("out_eye.detect", "Sensor 1 (Detect)", "bit", "input"),
            Tag("done.count", "Remover 0 (Count)", "int", "input"),
            Tag("spill.count", "Remover 1 (Count)", "int", "input"),
        )
        self.height = 0.0
        self.hoist_speed = ML_HOIST_FIRST
        self.slowed = float(self.rng.choice(ML_SLOWED))
        self.rise = 1.0                       # the entry blade, 1 = fully up
        self.feed = EngineFeed(_EMITTER)
        self._emit_edge = False
        self._next_id = 1
        #: Cartons by where they are: on the infeed, on the carriage, on the
        #: outfeed. A carton is in exactly one of these until it is gone.
        self.infeed: list[Item] = []
        self.carriage: list[Item] = []
        self.outfeed: list[Item] = []

        # --- ground truth ---
        #: One entry per carton: when it was made, boarded, came to rest on the
        #: carriage, was first lifted, left the carriage (and at what carriage
        #: height), and where it ended up.
        self.ledger: dict[int, dict] = {}
        self.delivered: list[Item] = []
        self.spilled = 0

        self.script = Script([
            (1.0, self.panel.press("start")),
            (ML_SLOW_AT, self._slow_the_hoist),
        ])
        #: Whether the carriage moved on the last tick, and the level it was
        #: last called to.
        self.hoisting = False
        self._called = 0
        self.operator = OperatorExam(
            self, ML_ESTOP_AT, noun="lift", what="the lift",
            ready=lambda: not self.hoisting and self._at(self._called))

    def _slow_the_hoist(self) -> None:
        """What the lift's Hoist Speed slider does in the engine. No tag reports
        it; `atlevel` does, when the carriage gets there."""
        self.hoist_speed = ML_HOIST_FIRST * self.slowed

    # --- the lift, as `VerticalLift.Step` runs it ---

    def _at(self, level: int) -> bool:
        return abs(self.height - level * ML_SPACING) <= ML_TOLERANCE

    def _occupied(self) -> bool:
        return any(abs(item.position - ML_LX) <= ML_ABOARD for item in self.carriage)

    def step(self, dt: float) -> None:
        # The emitter: a carton per rising edge, on the infeed at its own x.
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            height, metal = self.feed.next()
            item = Item(height=height, metal=metal, position=_EMITTER.x, id=self._next_id)
            self._next_id += 1
            self.infeed.append(item)
            self.ledger[item.id] = {"made_at": round(self.t, 2), "boarded_at": None,
                                    "rested": False, "lifted_at": None, "left_at": None,
                                    "left_height": None, "lane": None}
        self._emit_edge = emit

        faulted = self.bit("lift.fault")
        target = min(max(int(self.num("lift.target")), 0), ML_LEVELS - 1)

        # `Step`: occupancy first, then the hoist, then the gate, then the deck.
        occupied = self._occupied()
        was_height = self.height
        if not faulted:
            goal = target * ML_SPACING
            step = self.hoist_speed * dt
            self.height += max(min(goal - self.height, step), -step)
        self.hoisting = abs(self.height - was_height) > 1e-9
        called = target != self._called
        self._called = target
        gate_should_open = not faulted and self._at(0) and not occupied
        want = 0.0 if gate_should_open else 1.0
        step = ML_BLADE_SPEED * dt
        self.rise += max(min(want - self.rise, step), -step)
        deck = self.bit("lift.transfer") and not faulted

        # The carriage: its cartons ride it up and down, move along it only
        # while the deck is driven, and leave it past the far end of the deck.
        still: list[Item] = []
        for item in self.carriage:
            record = self.ledger[item.id]
            if deck:
                item.position += ML_TRANSFER * dt
            else:
                record["rested"] = True
            if self.height > 0.05 and record["lifted_at"] is None:
                record["lifted_at"] = round(self.t, 2)
            if item.position <= ML_DECK_END:
                still.append(item)
                continue
            record["left_at"] = round(self.t, 2)
            record["left_height"] = round(self.height, 3)
            if self._at(1):
                self.outfeed.append(item)
            elif self.height > ML_SPILL_ABOVE:
                record["lane"] = "spill"
                self.spilled += 1
            else:
                record["lane"] = "floor"
        self.carriage = still

        # The infeed: cartons queue behind each other and behind the blade
        # while it stands proud of the belt; a carton whose centre passes the
        # deck's start is on the carriage, which the open gate means is here.
        running = self.bit("infeed.rotate") and not self.bit("infeed.fault")
        ahead = None
        blade_blocks = self.rise > ML_BLADE_BLOCKS
        for item in sorted(self.infeed, key=lambda i: -i.position):
            if running:
                limit = item.position + ML_INFEED_SPEED * dt
                if ahead is not None:
                    limit = min(limit, ahead - CARTON_LENGTH)
                if blade_blocks and item.position + CARTON_LENGTH / 2 <= ML_BLADE_FACE + 1e-9:
                    limit = min(limit, ML_BLADE_FACE - CARTON_LENGTH / 2)
                item.position = max(item.position, limit)
            ahead = item.position
        boarding = [i for i in self.infeed if i.position >= ML_DECK_START]
        for item in boarding:
            self.infeed.remove(item)
            record = self.ledger[item.id]
            if self._at(0):
                self.carriage.append(item)
                record["boarded_at"] = round(self.t, 2)
            else:                              # the shaft, with no carriage in it
                record["lane"] = "floor"
                record["left_at"] = round(self.t, 2)
                record["left_height"] = round(self.height, 3)

        # The outfeed, to the remover at its end.
        belt = self.bit("outfeed.rotate") and not self.bit("outfeed.fault")
        self.operator.driven = running or belt or deck or called
        kept: list[Item] = []
        for item in self.outfeed:
            if belt:
                item.position += ML_OUTFEED_SPEED * dt
            if item.position >= ML_TAKEN_AT:
                self.ledger[item.id]["lane"] = "delivered"
                self.ledger[item.id]["delivered_at"] = round(self.t, 2)
                self.delivered.append(item)
            else:
                kept.append(item)
        self.outfeed = kept

        occupied_now = self._occupied()
        level = min(max(round(self.height / ML_SPACING), 0), ML_LEVELS - 1)
        self.tags.set("lift.level", int(level))
        self.tags.set("lift.atlevel", self._at(target))
        self.tags.set("lift.height", float(self.height))
        self.tags.set("lift.occupied", occupied_now)
        self.tags.set("lift.ready", gate_should_open and self.rise <= ML_GATE_OPEN)
        self.tags.set("gate_eye.detect",
                      any(abs(i.position - _GATE_EYE.x) <= CARTON_LENGTH / 2 for i in self.infeed))
        self.tags.set("out_eye.detect",
                      any(abs(i.position - _OUT_EYE.x) <= CARTON_LENGTH / 2 for i in self.outfeed))
        self.tags.set("done.count", len(self.delivered))
        self.tags.set("spill.count", self.spilled)


def grade_mezzanine_lift(watched: Watched, engine: GradedEngine, report: Report,
                         duration: float) -> None:
    sim: MezzanineLiftScene = watched.inner
    cartons = [dict(id=k, **v) for k, v in sorted(sim.ledger.items())]
    boarded = [c for c in cartons if c["boarded_at"] is not None]
    left = [c for c in boarded if c["left_at"] is not None]
    ran_through = [c for c in left if not c["rested"]]
    between = [c for c in left if c["rested"] and c["lane"] not in ("delivered", None)
               and not (abs(c["left_height"] - ML_SPACING) <= ML_TOLERANCE)]
    delivered = len(sim.delivered)
    lifted_after = [c for c in cartons if c["lane"] == "delivered"
                    and c["lifted_at"] is not None and c["lifted_at"] >= ML_SLOW_AT]

    report.evidence.update({
        "hoist_speed_first": ML_HOIST_FIRST,
        "hoist_speed_then": round(ML_HOIST_FIRST * sim.slowed, 4),
        "slowed_at": ML_SLOW_AT,
        "cartons": cartons,
        "delivered": delivered,
        "delivered_lifted_after_the_slowdown": len(lifted_after),
        "spilled": sim.spilled,
        "ran_through": [{"id": c["id"], "left_height_m": c["left_height"], "at": c["left_at"],
                         "lane": c["lane"]} for c in ran_through],
        "left_between_floors": [{"id": c["id"], "left_height_m": c["left_height"],
                                 "at": c["left_at"], "lane": c["lane"]} for c in between],
    })

    report.add("lift.delivered",
               delivered >= ML_MIN_DELIVERED and len(lifted_after) >= 1,
               f"{delivered} carton(s) reached the outfeed's end (at least "
               f"{ML_MIN_DELIVERED}), {len(lifted_after)} of them lifted after the "
               f"hoist was slowed (at least 1)")
    report.add("lift.held_aboard",
               not ran_through,
               "every carton came to rest on the carriage before it left it"
               if not ran_through else
               f"{len(ran_through)} carton(s) ran across the carriage without stopping, "
               f"leaving it at " + ", ".join(f"{c['left_height']:.2f} m" for c in ran_through[:4]))
    report.add("lift.discharged_at_level",
               not between,
               "every carton left the carriage at level 1, onto the outfeed"
               if not between else
               f"{len(between)} carton(s) left the carriage between floors: "
               + ", ".join(f"at {c['left_height']:.2f} m ({c['left_at']:g}s)" for c in between[:4]))

    say = report.feedback.append
    if not boarded:
        say("No carton ever boarded the lift. The gate is down, and lift.ready made, only "
            "while the carriage stands empty at the floor; run the infeed and the carriage's "
            "deck (lift.transfer) then, and a carton rides aboard.")
    if ran_through:
        c = ran_through[0]
        say(f"Carton {c['id']} ran straight across the carriage and off its far side at "
            f"{c['left_height']:.2f} m: the deck was still running when it was aboard. Stop "
            f"lift.transfer on lift.occupied -- the deck is how a carton gets on, and it is "
            f"also how it gets off.")
    if between:
        c = between[0]
        say(f"Carton {c['id']} was run off the carriage at {c['left_height']:.2f} m, with "
            f"level 1 at {ML_SPACING:g} m. The hoist was slowed to {sim.slowed:.0%} of its "
            f"speed at {ML_SLOW_AT:g}s -- nothing on the bus says so -- and a climb then "
            f"takes {ML_SPACING / (ML_HOIST_FIRST * sim.slowed):.1f}s, not "
            f"{ML_RATED_CLIMB:.1f}s. Discharge on lift.atlevel with lift.level = 1: that "
            f"is the carriage telling you it has arrived.")
    if delivered and not lifted_after:
        say(f"No carton was lifted after {ML_SLOW_AT:g}s. A program has to go on working "
            f"once the hoist is slower, not only until it is.")
    mark_contract(sim.operator, report, watched.sim_time)


#: The fewest cartons a working program delivers in the window. The `good`
#: reference delivers 9 on seeds 5 and 11 (docs/GRADING.md), one carton
#: every trip: 5 on the rated hoist and 4 on the slowed one.
ML_MIN_DELIVERED = 6


def _summary_mezzanine(evidence: dict, out) -> None:
    out(f"{evidence['delivered']} delivered, {evidence['spilled']} on the mezzanine floor; "
        f"hoist slowed from {evidence['hoist_speed_first']:g} to "
        f"{evidence['hoist_speed_then']:g} m/s at {evidence['slowed_at']:g}s")
    heights = ", ".join(f"{c['left_height']:.2f}" for c in evidence["cartons"]
                        if c["left_height"] is not None)
    out(f"carriage height each carton left it at (m): {heights or 'none'}")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Mezzanine lift",
    "task": ("Take cartons from the floor conveyor up to the mezzanine one at a time: "
             "draw each aboard and stop the deck on lift.occupied, call level 1, "
             "discharge only once lift.atlevel says the carriage is there, and send it "
             "back when out_eye sees the carton on the outfeed. This run slows the hoist. "
             "The mushroom is normally closed, stops both belts and the deck within "
             "200 ms and sends the carriage nowhere new, and latches -- only Reset, "
             "then Start, runs the lift again."),
    "build": MezzanineLiftScene,
    "observe": None,
    "grade": grade_mezzanine_lift,
    "summary": _summary_mezzanine,
    "duration": ML_EXAM_ENDS_BY,
    "references": ("good", "timed", "nostop", "noestop", "startalone"),
    "tags": ("emitter.emit, infeed.rotate, lift.target, lift.transfer, outfeed.rotate, "
             "panel.green, panel.red are yours to write; lift.level, lift.atlevel, "
             "lift.height, lift.occupied, lift.ready, lift.fault, gate_eye.detect, "
             "out_eye.detect, done.count, spill.count, infeed.fault, outfeed.fault, "
             "panel.setpoint and the buttons are the plant's."),
}
