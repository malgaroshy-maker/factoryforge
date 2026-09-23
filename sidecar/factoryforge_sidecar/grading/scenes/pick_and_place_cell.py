"""`pick-and-place-cell`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/pick_and_place_cell.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (BELT_SURFACE_Y, CARTON_LENGTH, SHORT_HEIGHT, WORK_PLANE_Y,
                     EngineFeed, Item, PlantScene, Script, Vfd, declare_stack_light,
                     fault_input, pot_start)
from ..templates import template


SCENE = "pick-and-place-cell"


# --- pick and place cell -------------------------------------------------
#
# Observable fact: which cartons the gantry actually carried to the outfeed,
# and where it let go of the ones it did not. The plant owns the cup, so it
# knows whether there was anything on it and it knows the rail position at the
# moment the vacuum dropped.
#
# How a program fakes it: by sequencing on timers. Travel, lower, grip, raise,
# travel, release is six waits, and six waits of the right length look exactly
# like a controller written on feedback -- at one travel speed. So the exam
# reaches into the axis halfway through the run and slows it down. The
# feedback-driven sequence gets slower and keeps working; the timed one starts
# releasing the carton somewhere over the middle of the rail, and the plant
# records every one of those as a carton dropped on the floor rather than as a
# cycle that merely took too long.
#
# The other half is the vacuum. `gantry.holding` is true only when the cup
# really caught something, so a cycle that travels with the grip on and nothing
# held carried air -- which a timed sequencer does the moment it grips before a
# carton has been indexed.
#
# Read from the template (IP-19), and since IP-29 laid out where the template
# lays it out. As shipped: a 2.4 m rail centred at x = 3.6 at 80 %/s with a
# 1.5 % in-position window, a 0.52 m stroke at 1.3 m/s, and an infeed behind a
# VFD ramping at 35 %/s to 0.8 m/s. Positions along the line are world X, and
# the gantry's are percent of its rail: 0 % is x = 2.4, over the pick station
# just short of its eye, and 100 % is x = 4.8, past the outfeed's centre.
_PLANT = template(SCENE)
_INFEED = _PLANT.part("infeed", "VariableConveyor")
_SCANNER = _PLANT.part("scanner", "BarcodeScanner")
_STATION = _PLANT.part("pickstation", "ConveyorBelt")
_GANTRY = _PLANT.part("gantry", "PickPlaceArm")
_OUTFEED = _PLANT.part("outfeed", "Remover")
_EMITTER = _PLANT.part("emitter", "Emitter")

PP_START_POS = _EMITTER.x
PP_INFEED_TO = _INFEED.span()[1]
#: The scanner. Until IP-29 this model had it at 1.4.
PP_SCANNER_POS = _SCANNER.x
#: A carton is in front of the head while its collider overlaps the read
#: window (`BarcodeScanner.cs`, an `Area3D` `window` long): its centre within
#: half the window plus half a carton. Until IP-29 this model used half the
#: window alone.
PP_SCANNER_REACH = _SCANNER.number("window") / 2 + CARTON_LENGTH / 2
PP_STATION_FROM, PP_STATION_END = _STATION.span()
PP_STATION_SPEED = _STATION.number("speed")
#: The station's eye. Until IP-29 this model detected -- and picked -- at
#: x = 2.9, the far end of the deck, and called that 0 % of the rail.
PP_EYE_POS = _PLANT.part("atstation", "PhotoelectricSensor").x
#: `PickPlaceArm.cs`: 0 % of the rail is its centre less half its length.
PP_RAIL_LENGTH = _GANTRY.number("rail_length")
PP_RAIL_FROM = _GANTRY.x - PP_RAIL_LENGTH / 2
PP_INFEED_MAX = _INFEED.number("max_speed")
PP_INFEED_RAMP = _INFEED.number("accel_rate")
PP_TOLERANCE = _GANTRY.number("tolerance")
PP_STROKE = _GANTRY.number("stroke")
PP_LOWER_SPEED = _GANTRY.number("lower_speed")
PP_LOWER_TIME = PP_STROKE / PP_LOWER_SPEED
PP_TRAVEL_FIRST = _GANTRY.number("travel_speed")
#: The exam's slow-down, and not a number from anywhere else: after
#: PP_TRAVEL_CHANGES_AT the axis runs at two fifths of the template's speed.
PP_SLOWDOWN = 0.4
PP_TRAVEL_THEN = PP_TRAVEL_FIRST * PP_SLOWDOWN
PP_TRAVEL_CHANGES_AT = 35.0
PP_POT_START = pot_start(_PLANT)

#: Numbers `PickPlaceArm.cs` owns and no template sets: the cup's pick zone,
#: 0.28 m square (`_pickZone`), the rail's height above the part origin
#: (`RailY`), the carriage underside below it (`ColumnTopY`) and the
#: column's rest stub below that (`restStub`). The cup hangs
#: `RailY - PP_COLUMN_DROP - restStub - extension` above the work plane.
PP_PICK_ZONE = 0.28
PP_RAIL_Y = 0.88
PP_COLUMN_DROP = 0.10
PP_REST_STUB = 0.10
#: A carton is under the cup while its collider overlaps the pick zone.
PP_PICK_REACH = PP_PICK_ZONE / 2 + CARTON_LENGTH / 2
#: Godot's default gravity, which the project does not override. A released
#: carton falls under it, carrying the cup's velocity (`PickPlaceArm.Release`).
GRAVITY = 9.8

#: Where the carton has to land. The `outfeed` remover is a zone under the far
#: end of the rail -- x = 4.25 to 4.95 and up to 0.4 m off the floor, as
#: shipped -- and a released carton counts as placed if its collider passes
#: through it on the way down. Until IP-29 this model accepted any release
#: between 92 % and 100 % of the rail and no other; the zone reaches back to
#: 77 % (73 % for a carton's leading edge) and the carton does not fall
#: straight down if the axis is still moving.
PP_OUTFEED_X = _OUTFEED.x
PP_OUTFEED_HALF = _OUTFEED.number("zone_x") / 2
PP_OUTFEED_TOP = _OUTFEED.position[1] + _OUTFEED.number("zone_y") / 2
#: The rail position over the outfeed's centre: where a program aims to
#: release. Derived, not chosen.
PP_PLACE_AT = (PP_OUTFEED_X - PP_RAIL_FROM) / PP_RAIL_LENGTH * 100.0
#: What `BarcodeScanner.cs` reads off a carton (`CodeShortCarton`,
#: `CodeTallCarton`, `CodeMetal`, :33-35), from what the carton is. The
#: cartons are the template emitter's (`plant.EngineFeed`): short, tall, short,
#: tall, and every `metal_every`-th steel. Until IP-29 this model dealt the
#: codes out shuffled, independent of the carton.
PP_CODES = (101, 102, 201)


def pp_rail_x(percent: float) -> float:
    """World X of the cup at a rail position."""
    return PP_RAIL_FROM + PP_RAIL_LENGTH * percent / 100.0


def pp_code(item: Item) -> int:
    short, tall, metal = PP_CODES
    return metal if item.metal else (tall if item.height > SHORT_HEIGHT else short)


class PickPlaceScene(PlantScene):
    name = "pick-and-place-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=PP_POT_START)
        self._declare(
            Tag("infeed.run", "VFD Conveyor (Run)", "bit", "output"),
            Tag("infeed.speed", "VFD Conveyor Speed Ref (%)", "float", "output"),
            Tag("pickstation.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("scanner.enable", "Scanner Enable", "bit", "output", value=True),
            Tag("gantry.target", "Gantry Target (%)", "float", "output"),
            Tag("gantry.lower", "Gantry Lower", "bit", "output"),
            Tag("gantry.grip", "Gantry Vacuum", "bit", "output"),
            Tag("rate.value", "Rate Gauge", "float", "output"),
            Tag("alarm.beacon", "Alarm Beacon", "bit", "output"),
            Tag("alarm.horn", "Alarm Horn", "bit", "output"),
            Tag("infeed.actual", "VFD Conveyor Actual Speed (%)", "float", "input"),
            Tag("scanner.code", "Scanner Code", "int", "input"),
            Tag("scanner.read", "Scanner Read Pulse", "bit", "input"),
            Tag("scanner.present", "Scanner Item Present", "bit", "input"),
            Tag("atstation.detect", "Diffuse Sensor (Detect)", "bit", "input"),
            Tag("gantry.position", "Gantry Position (%)", "float", "input"),
            Tag("gantry.inposition", "Gantry In Position", "bit", "input",
                value=True),
            Tag("gantry.lowered", "Gantry Lowered", "bit", "input"),
            Tag("gantry.raised", "Gantry Raised", "bit", "input", value=True),
            Tag("gantry.holding", "Gantry Holding", "bit", "input"),
            Tag("gantry.fault", "Gantry Drive Fault", "bit", "input"),
            Tag("outfeed.count", "Remover (Count)", "int", "input"),
            fault_input("infeed", "VFD Conveyor Drive Fault"),
            fault_input("pickstation", "Conveyor Drive Fault"),
        )
        declare_stack_light(self.tags)

        self.travel_speed = PP_TRAVEL_FIRST
        self.drive = Vfd(PP_INFEED_MAX, PP_INFEED_RAMP)
        self.position = 0.0
        self.extension = 0.0
        self.carried: Item | None = None
        self.items: list[Item] = []
        self._last_read: int | None = None
        self._next_id = 1
        self._emit_edge = False
        self._feed = EngineFeed(_EMITTER)

        # --- ground truth ---
        #: Cartons the gantry carried to the outfeed, with the sim time.
        self.placed: list[dict] = []
        #: Cartons let go of anywhere else, with the rail position it happened
        #: at and where they came down. A cycle that releases over the middle
        #: of the rail has not been slow, it has dropped a carton.
        self.dropped: list[dict] = []
        #: Cartons let go of over a deck, which fall back onto it and are
        #: carried on from there -- not placed, not lost.
        self.put_back: list[dict] = []
        #: Cartons the pick station's belt carried off its own far end.
        self.ran_off_the_station: list[int] = []
        #: Times the gantry reached the outfeed with the vacuum on and
        #: nothing on the cup.
        self.empty_carries: list[float] = []
        self._carrying_since: float | None = None
        self._was_at_place = False
        self.max_ramp_gap = 0.0

        # The pot on this cell is the infeed drive's speed reference, in
        # percent, which the template graduates 10-100.
        self.script = Script([
            (0.2, self.panel.set_setpoint(self.rng.choice([60.0, 70.0, 80.0]))),
            (1.0, self.panel.press("start")),
            (PP_TRAVEL_CHANGES_AT, self._slow_the_axis),
        ])

    def _slow_the_axis(self) -> None:
        """Turn the rail's travel speed down, mid-run.

        It is a slider in the property panel in the real editor, so a scene a
        student is handed may have any value in it. Nothing on the bus reports
        it: `gantry.position` still counts percent and `gantry.inposition`
        still says whether the axis got there. A sequence written on those two
        does not notice. A sequence written on a stopwatch lets go of the
        carton over the middle of the rail.
        """
        self.travel_speed = PP_TRAVEL_THEN

    @property
    def phase(self) -> str:
        return "fast" if self.t < PP_TRAVEL_CHANGES_AT else "slow"

    def step(self, dt: float) -> None:
        self._step_infeed(dt)
        self._step_gantry(dt)
        self._step_sensors()

    def _step_infeed(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            height, metal = self._feed.next()
            item = Item(height=height, metal=metal, position=PP_START_POS,
                        id=self._next_id)
            item.measured = float(pp_code(item))
            self.items.append(item)
            self._next_id += 1
        self._emit_edge = emit

        # `VariableConveyor.cs`: the belt runs at the drive's ACTUAL speed,
        # which lags the reference and coasts down when `run` drops.
        infeed_speed = self.drive.step(self.bit("infeed.run"),
                                       self.num("infeed.speed"), dt)
        self.max_ramp_gap = max(self.max_ramp_gap,
                                abs((self.drive.reference if self.bit("infeed.run")
                                     else 0.0) - self.drive.actual))
        self.tags.set("infeed.actual", self.drive.actual)

        station = self.bit("pickstation.rotate")
        still: list[Item] = []
        for item in self.items:
            if item is not self.carried:
                if item.position < PP_INFEED_TO:
                    item.position = min(item.position + infeed_speed * dt, PP_INFEED_TO)
                elif station:
                    item.position += PP_STATION_SPEED * dt
                if item.position > PP_STATION_END:
                    # Off the end of a belt that kept running: onto the floor.
                    item.lane = "floor"
                    self.ran_off_the_station.append(item.id)
                    continue
            still.append(item)
        self.items = still

    def _step_gantry(self, dt: float) -> None:
        # `PickPlaceArm.Step`: travel, stroke, then gripper, all on this
        # tick's command. `inposition` is stale on the scan that commands a
        # move because the controller reads it before its own write has
        # reached the machine -- which the bus does by itself. Until IP-29
        # this model held the target back a further tick as well.
        target = min(max(self.num("gantry.target"), 0.0), 100.0)
        was = self.position
        if not self.bit("gantry.fault"):
            step = self.travel_speed * dt
            self.position += max(min(target - self.position, step), -step)
            reach = PP_LOWER_SPEED * dt
            want = PP_STROKE if self.bit("gantry.lower") else 0.0
            self.extension += max(min(want - self.extension, reach), -reach)
        #: The cup's own velocity along the rail, measured the way
        #: `TrackCupVelocity` does it: this tick's movement over this tick.
        velocity = (pp_rail_x(self.position) - pp_rail_x(was)) / dt if dt > 0 else 0.0
        self.tags.set("gantry.position", self.position)
        self.tags.set("gantry.inposition",
                      abs(self.position - target) <= PP_TOLERANCE)
        lowered = self.extension >= PP_STROKE - 0.01
        self.tags.set("gantry.lowered", lowered)
        self.tags.set("gantry.raised", self.extension <= 0.01)

        cup_x = pp_rail_x(self.position)
        grip = self.bit("gantry.grip")
        if grip and self.carried is None and lowered:
            # `TryPick`: the nearest carton whose collider overlaps the cup's
            # zone. The model asks for the column to be down, where the zone
            # reaches a carton of either height; the engine's zone can catch a
            # tall one a little before that.
            under = [i for i in self.items if abs(i.position - cup_x) < PP_PICK_REACH]
            if under:
                self.carried = min(under, key=lambda i: abs(i.position - cup_x))
                self.carried.carried = True
                self._carrying_since = self.t
        elif not grip and self.carried is not None:
            self._release(self.carried, cup_x, velocity)

        if self.carried is not None:
            self.carried.position = cup_x
        self.tags.set("gantry.holding", self.carried is not None and grip)

        at_place = abs(cup_x - PP_OUTFEED_X) <= PP_OUTFEED_HALF
        if at_place and not self._was_at_place and grip and self.carried is None:
            self.empty_carries.append(round(self.t, 2))
        self._was_at_place = at_place

    def _release(self, item: Item, cup_x: float, velocity: float) -> None:
        """`PickPlaceArm.Release`: the carton is handed back to physics with
        the cup's velocity, so one let go while the axis is still moving is
        thrown along the line. Where it comes down decides what it was."""
        self.carried = None
        item.carried = False
        where = round(self.position, 1)
        cup_y = (WORK_PLANE_Y + PP_RAIL_Y - PP_COLUMN_DROP - PP_REST_STUB
                 - self.extension)
        bottom = cup_y - item.height

        def landing(surface: float) -> float:
            fall = (2.0 * max(bottom - surface, 0.0) / GRAVITY) ** 0.5
            return cup_x + velocity * fall

        record = {"carton": item.id, "at": round(self.t, 2), "position": where,
                  "phase": self.phase}
        on_deck = landing(BELT_SURFACE_Y)
        for low, high in ((0.0, PP_INFEED_TO), (PP_STATION_FROM, PP_STATION_END)):
            if low <= on_deck <= high:
                # Back onto a deck, which carries it on from wherever it fell.
                item.position = on_deck
                self.put_back.append({**record, "landed_x": round(on_deck, 2)})
                return

        self.items.remove(item)
        # Through the remover's zone on the way to the floor: from where it
        # crosses the zone's top to where it lands.
        high, low = landing(PP_OUTFEED_TOP), landing(0.0)
        near = min(high, low) - CARTON_LENGTH / 2
        far = max(high, low) + CARTON_LENGTH / 2
        if far > PP_OUTFEED_X - PP_OUTFEED_HALF and near < PP_OUTFEED_X + PP_OUTFEED_HALF:
            item.lane = "outfeed"
            self.placed.append({**record, "code": int(item.measured or 0)})
            self.tags.set("outfeed.count", len(self.placed))
        else:
            # Let go anywhere but over the outfeed and it is on the floor.
            item.lane = "floor"
            self.dropped.append({**record, "landed_x": round(low, 2)})

    def _step_sensors(self) -> None:
        waiting = [i for i in self.items if i is not self.carried]
        # `BarcodeScanner.Scan`: armed by `scanner.enable`, it looks at the
        # first carton in its window, reads it once, and re-arms only when the
        # window has been empty. Disarmed, it sees nothing at all.
        if self.bit("scanner.enable"):
            in_window = [i for i in waiting
                         if abs(i.position - PP_SCANNER_POS) < PP_SCANNER_REACH]
        else:
            in_window = []
        self.tags.set("scanner.present", bool(in_window))
        read = False
        if not in_window:
            self._last_read = None
        else:
            first = max(in_window, key=lambda i: i.position)
            if first.id != self._last_read:
                self._last_read = first.id
                self.tags.set("scanner.code", int(first.measured or 0))
                read = True
        # One tick wide, exactly like a panel button's pulse, which is why a
        # program has to latch it rather than poll it.
        self.tags.set("scanner.read", read)
        self.tags.set("atstation.detect",
                      any(abs(i.position - PP_EYE_POS) <= CARTON_LENGTH / 2
                          for i in waiting))


def grade_pick_place(watched: Watched, engine: GradedEngine, report: Report,
                     duration: float) -> None:
    sim: PickPlaceScene = watched.inner
    fast = [p for p in sim.placed if p["phase"] == "fast"]
    slow = [p for p in sim.placed if p["phase"] == "slow"]

    report.evidence.update({
        "fed": sim._next_id - 1,
        "placed": len(sim.placed),
        "placed_before_the_axis_slowed": len(fast),
        "placed_after_the_axis_slowed": len(slow),
        "dropped": sim.dropped[:20],
        "put_back_on_a_deck": sim.put_back[:20],
        "ran_off_the_station": sim.ran_off_the_station[:20],
        "empty_carries_at": sim.empty_carries[:10],
        "travel_speed_before": PP_TRAVEL_FIRST,
        "travel_speed_after": PP_TRAVEL_THEN,
        "codes_read": sorted({p["code"] for p in sim.placed}),
        "max_ramp_gap_percent": round(sim.max_ramp_gap, 1),
        "carried": [p["carton"] for p in sim.placed][:20],
    })

    report.add("cell.cycled",
               len(sim.placed) >= 3,
               f"{len(sim.placed)} carton(s) were carried to the outfeed "
               f"(at least 3 -- one is a cell that happened to work once)")
    report.add("cell.nothing_dropped",
               not sim.dropped,
               "the vacuum was never released away from the outfeed"
               if not sim.dropped else
               f"{len(sim.dropped)} carton(s) were let go somewhere else, at "
               f"rail positions "
               f"{sorted({d['position'] for d in sim.dropped})[:6]} %")
    report.add("cell.survived_the_slower_axis",
               len(slow) >= 1,
               f"{len(fast)} placed at {PP_TRAVEL_FIRST:g} %/s and {len(slow)} "
               f"after the axis was slowed to {PP_TRAVEL_THEN:g} %/s")
    report.add("cell.never_carried_air",
               not sim.empty_carries,
               "the gantry never travelled to the outfeed with the vacuum on "
               "and nothing on the cup" if not sim.empty_carries else
               f"{len(sim.empty_carries)} cycle(s) carried air, at "
               f"{sim.empty_carries[:5]}s")

    _pick_place_feedback(report, watched, sim, fast, slow)


def _pick_place_feedback(report, watched, sim, fast, slow) -> None:
    say = report.feedback.append

    if not sim.placed and not sim.dropped:
        if watched.held_true("infeed.run") == 0.0:
            say("The infeed never ran. `infeed.run` is a bit and `infeed.speed` "
                "is a percentage -- the drive needs both, and it ramps, so "
                "`infeed.actual` will lag whatever you ask for.")
        else:
            say("Nothing was ever picked up. `gantry.grip` only catches "
                "something when the arm is down (`gantry.lowered`) over a "
                "carton the pick station has indexed (`atstation.detect`), and "
                "`gantry.holding` tells you whether it did.")
        return

    if sim.dropped:
        spots = sorted({d["position"] for d in sim.dropped})
        in_slow = [d for d in sim.dropped if d["phase"] == "slow"]
        if len(in_slow) >= len(sim.dropped) * 0.7:
            say(f"{len(sim.dropped)} carton(s) hit the floor, "
                f"{len(in_slow)} of them after the axis was slowed from "
                f"{PP_TRAVEL_FIRST:g} %/s to {PP_TRAVEL_THEN:g} %/s -- released "
                f"at {spots[:5]} % of the rail instead of over the outfeed, "
                f"about {PP_PLACE_AT:.0f} %. That is a sequence written on timers. "
                f"Wait on `gantry.position` reaching the destination this step "
                f"wants, and the same code works at any travel speed.")
        else:
            say(f"{len(sim.dropped)} carton(s) were released at {spots[:5]} % of "
                f"the rail rather than over the outfeed. Check the position "
                f"feedback against the destination the step wants -- not "
                f"`gantry.inposition` alone, which compares the axis to the "
                f"target the machine currently holds, and a target written this "
                f"scan has not reached the machine yet. On the scan that issues "
                f"a move it still reports 'arrived', at the place you are "
                f"trying to leave.")

    if sim.empty_carries:
        say(f"{len(sim.empty_carries)} cycle(s) travelled to the outfeed with "
            f"the vacuum on and nothing on the cup. `gantry.holding` is true "
            f"only when the cup really caught something -- check it after you "
            f"grip, and go back and wait if it did not.")

    if not slow and fast:
        say(f"Nothing was placed after the axis slowed down {PP_TRAVEL_CHANGES_AT:g}s "
            f"in. The cell either stalled or is still waiting for a motion that "
            f"now takes longer than it used to.")

    if sim.placed and not sim.dropped and not sim.empty_carries:
        say(f"{len(sim.placed)} cartons placed -- {len(fast)} at "
            f"{PP_TRAVEL_FIRST:g} %/s and {len(slow)} after the axis was slowed "
            f"to {PP_TRAVEL_THEN:g} %/s, with nothing dropped and every carry "
            f"holding something. That is a sequence on feedback.")


def _summary_pick_place(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, placed {evidence['placed']} "
        f"({evidence['placed_before_the_axis_slowed']} at "
        f"{evidence['travel_speed_before']:g} %/s, "
        f"{evidence['placed_after_the_axis_slowed']} at "
        f"{evidence['travel_speed_after']:g} %/s)")
    out(f"codes carried: {evidence['codes_read']}")
    out(f"largest gap between the drive's reference and its actual speed: "
        f"{evidence['max_ramp_gap_percent']:.0f} %")
    for entry in evidence["dropped"][:8]:
        out(f"  carton {entry['carton']:>3} dropped at {entry['position']:.0f} % "
            f"of the rail, {entry['at']:.1f}s ({entry['phase']})")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Pick & place cell",
    "task": ("Sequence three motions against feedback, not timers: ramp "
             "the infeed, index each carton, then travel, lower, grip, "
             "raise, travel, release onto the outfeed. This run slows the "
             "rail down halfway through."),
    "build": PickPlaceScene,
    "observe": None,
    "grade": grade_pick_place,
    "summary": _summary_pick_place,
    "duration": 75.0,
    "references": ("good", "timed"),
    "tags": ("infeed.run, infeed.speed, pickstation.rotate, emitter.emit, "
             "scanner.enable, gantry.target, gantry.lower, gantry.grip, "
             "rate.value, alarm.beacon, alarm.horn, panel.green, panel.red "
             "are yours to write; infeed.actual, scanner.code, "
             "scanner.read, scanner.present, atstation.detect, "
             "gantry.position, gantry.inposition, gantry.lowered, "
             "gantry.raised, gantry.holding, gantry.fault, outfeed.count "
             "and the buttons are the cell's."),
}
