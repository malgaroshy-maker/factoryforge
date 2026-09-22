"""`pick-and-place-cell`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/pick_and_place_cell.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import Item, PlantScene, Script, shuffled_cycle


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
# Numbers from `engine/templates/pick_and_place_cell.json`: a 2.4 m rail at
# 80 %/s with a 1.5 % in-position window, a 0.52 m stroke at 1.3 m/s, and an
# infeed behind a VFD ramping at 35 %/s to 0.8 m/s.
PP_INFEED_TO = 2.0
PP_SCANNER_POS = 1.4
PP_STATION_POS = 2.9
PP_STATION_END = 3.0
PP_STATION_SPEED = 0.5
PP_INFEED_MAX = 0.8
PP_INFEED_RAMP = 35.0
PP_TOLERANCE = 1.5
PP_LOWER_TIME = 0.52 / 1.3
PP_TRAVEL_FIRST = 80.0
PP_TRAVEL_THEN = 32.0
PP_TRAVEL_CHANGES_AT = 35.0
#: Where on the rail the outfeed is. Let go anywhere else and the carton falls.
PP_PLACE_AT = 100.0
PP_PLACE_WINDOW = 8.0
PP_CODES = (101, 102, 201)


class PickPlaceScene(PlantScene):
    name = "pick-and-place-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
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
        )

        self.travel_speed = PP_TRAVEL_FIRST
        self.actual_percent = 0.0
        self.position = 0.0
        self.held_target = 0.0
        self.lowered_frac = 0.0
        self.carried: Item | None = None
        self.items: list[Item] = []
        self._scanned: set[int] = set()
        self._next_id = 1
        self._emit_edge = False
        self.codes = shuffled_cycle(self.rng, list(PP_CODES), 12)

        # --- ground truth ---
        #: Cartons the gantry carried to the outfeed, with the sim time.
        self.placed: list[dict] = []
        #: Cartons let go of anywhere else, with the rail position it happened
        #: at. A cycle that releases over the middle of the rail has not been
        #: slow, it has dropped a carton.
        self.dropped: list[dict] = []
        #: Times the gantry reached the place end with the vacuum on and
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
            item = Item(id=self._next_id)
            item.measured = float(self.codes[(self._next_id - 1) % len(self.codes)])
            self.items.append(item)
            self._next_id += 1
        self._emit_edge = emit

        run = self.bit("infeed.run")
        reference = min(max(self.num("infeed.speed"), 0.0), 100.0) if run else 0.0
        self.actual_percent += max(min(reference - self.actual_percent,
                                       PP_INFEED_RAMP * dt), -PP_INFEED_RAMP * dt)
        self.max_ramp_gap = max(self.max_ramp_gap,
                                abs(reference - self.actual_percent))
        self.tags.set("infeed.actual", self.actual_percent)
        infeed_speed = PP_INFEED_MAX * self.actual_percent / 100.0 if run else 0.0

        station = self.bit("pickstation.rotate")
        for item in self.items:
            if item is self.carried:
                continue
            if item.position < PP_INFEED_TO:
                item.position = min(item.position + infeed_speed * dt, PP_INFEED_TO)
            elif station:
                item.position = min(item.position + PP_STATION_SPEED * dt,
                                    PP_STATION_END)

    def _step_gantry(self, dt: float) -> None:
        # The target the *machine* holds, taken before this tick's writes are
        # read, so `inposition` is stale on the scan that commands a move --
        # exactly as `PickPlaceArm.InPosition` is, and exactly the trap the
        # scene's own brief warns about.
        target = min(max(self.num("gantry.target"), 0.0), 100.0)
        step = self.travel_speed * dt
        if not self.bit("gantry.fault"):
            self.position += max(min(self.held_target - self.position, step), -step)
        self.tags.set("gantry.position", self.position)
        self.tags.set("gantry.inposition",
                      abs(self.position - self.held_target) <= PP_TOLERANCE)
        self.held_target = target

        want_down = self.bit("gantry.lower")
        rate = dt / PP_LOWER_TIME
        self.lowered_frac = (min(self.lowered_frac + rate, 1.0) if want_down
                             else max(self.lowered_frac - rate, 0.0))
        lowered = self.lowered_frac >= 0.999
        self.tags.set("gantry.lowered", lowered)
        self.tags.set("gantry.raised", self.lowered_frac <= 0.001)

        grip = self.bit("gantry.grip")
        if grip and self.carried is None and lowered and self.position <= PP_TOLERANCE * 2:
            under = [i for i in self.items
                     if i is not self.carried
                     and abs(i.position - PP_STATION_POS) <= 0.14]
            if under:
                self.carried = under[0]
                self.carried.carried = True
                self._carrying_since = self.t
        elif not grip and self.carried is not None:
            item = self.carried
            self.carried = None
            item.carried = False
            where = round(self.position, 1)
            if abs(self.position - PP_PLACE_AT) <= PP_PLACE_WINDOW:
                item.lane = "outfeed"
                self.placed.append({"carton": item.id, "at": round(self.t, 2),
                                    "code": int(item.measured or 0),
                                    "position": where, "phase": self.phase})
                self.items.remove(item)
                self.tags.set("outfeed.count", len(self.placed))
            else:
                # Let go anywhere but over the outfeed and it is on the floor.
                item.lane = "floor"
                self.dropped.append({"carton": item.id, "at": round(self.t, 2),
                                     "position": where, "phase": self.phase})
                self.items.remove(item)

        self.tags.set("gantry.holding", self.carried is not None and grip)

        at_place = abs(self.position - PP_PLACE_AT) <= PP_PLACE_WINDOW
        if at_place and not self._was_at_place and grip and self.carried is None:
            self.empty_carries.append(round(self.t, 2))
        self._was_at_place = at_place

    def _step_sensors(self) -> None:
        waiting = [i for i in self.items if i is not self.carried]
        in_window = [i for i in waiting
                     if abs(i.position - PP_SCANNER_POS) <= 0.13]
        self.tags.set("scanner.present", bool(in_window))
        fresh = [i for i in in_window if i.id not in self._scanned]
        if fresh and self.bit("scanner.enable"):
            self._scanned.add(fresh[0].id)
            self.tags.set("scanner.code", int(fresh[0].measured or 0))
            self.tags.set("scanner.read", True)
        else:
            # One scan wide, exactly like a panel button's pulse, which is why
            # a program has to latch it rather than poll it.
            self.tags.set("scanner.read", False)
        self.tags.set("atstation.detect",
                      any(abs(i.position - PP_STATION_POS) <= 0.14
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
                f"at {spots[:5]} % of the rail instead of at "
                f"{PP_PLACE_AT:g} %. That is a sequence written on timers. "
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
