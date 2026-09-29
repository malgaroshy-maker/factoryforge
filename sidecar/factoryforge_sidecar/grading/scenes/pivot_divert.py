"""`pivot-divert`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/pivot_divert.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (CARTON_LENGTH, SHORT_HEIGHT, TALL_HEIGHT, Item, OperatorExam,
                     PlantScene, Script, fault_input, operator_exam_ends_by, pot_start,
                     remover_catch, shuffled_cycle)
from ..templates import TemplateError, template
from ._contract import mark_contract, summary_contract


SCENE = "pivot-divert"


# --- pivot diverter line ------------------------------------------------------
#
# Observable fact: each carton's true height, which lane it ended in, and the
# blade's angle at the moment the carton reached it. The plant made the
# carton and turns the blade, so it knows all three; no tag carries any of
# them.
#
# The lesson is a deflector on a line that never stops. A pusher strikes a
# carton that is beside it; a blade turns a carton that runs into it, so it
# has to be across *before* the carton arrives and stay across until the
# carton has slid along it and off the belt -- which, driven only by the belt
# under it, takes about two metres of belt travel past the eyes. Home it
# early and the carton is let go half turned and rides on to the end; swing
# it late and it lands in the chute anyway, because the blade hit it.
#
# How a program fakes it: by timing the hold. A blade held for as long as a
# carton took at the rated speed holds long enough -- once. So the exam slows
# the belt mid-run (its speed, cut to 60-70 % by the examiner's hand, as the
# inspector's slider does in the engine), and a hold on a stopwatch lets the
# carton go before it is off. The feed is shuffled, not the engine's
# alternation, so "divert every second carton" never reads the eyes and
# fails (docs/GRADING.md, "The feed patterns are shuffled").
#
# Why a model of a 3-D contact can be one-dimensional: everything the belt
# drives scales with the belt. How far a carton has run along the lane --
# and how far along the blade, since the blade turns the belt's own push --
# is the belt travel since it was dropped, at any speed. So a carton's
# `position` here is its belt travel from the emitter, added to the
# emitter's x: its world x while it runs straight, and a progress coordinate
# once the blade has it. The four points on that coordinate where the blade
# matters were MEASURED in the engine, not derived (IP-32, 2026-09-24; a
# probe drove one carton at a time through this template over the tag bus
# at 0.5 and at 0.3 m/s and swept the moment the blade went out and home):
#
#   PD_MEET     the blade must be across (`diverted`) before a carton's
#               centre gets here, or the blade meets it moving. Out 1.4 s
#               after entry_eye fell (centre at 2.30): clean; out 1.7 s
#               after (2.45, across at 2.55): struck.
#   PD_MISS     a blade swung out before a carton gets here strikes it into
#               the chute; after, the carton has gone by. Out 3.0 s after
#               the fall (3.10): struck; 3.5 s (3.35): went by. Both heights.
#   PD_RELEASE  held until here the carton is off the belt; homed before,
#               it is let go and rides on. Tall: 1.75-2.0 m past the fall at
#               0.5 m/s, 1.8-1.95 m at 0.3. Short, lighter: 2.0-2.25 m and
#               2.1-2.25 m. The midpoints of where the two speeds agree.
#   PD_COUNTED  where the chute's remover counts a turned carton, fitted to
#               the counts at both speeds (tall 3.97 / 3.89, short 4.14 /
#               4.20) to within 0.1 m.
#
# Those offsets belong to this geometry -- a 45 deg blade 0.75 m long on a
# post one cell off a 0.5 m belt, and a chute with its lip dropped -- so the
# layout is checked below rather than trusted, and a template that moves
# any of it is refused until somebody measures again.
#
# Parts: `PivotDiverter.cs` (`UpdateSwing`, `IsDiverted`, `IsHome`), two
# retroreflective `PhotoelectricSensor.cs` beams at two heights, a
# `ConveyorBelt`, a `Chute`, and two `Remover`s. Every position and rate is
# the template's.
_PLANT = template(SCENE)
_BELT = _PLANT.part("belt", "ConveyorBelt")
_EMITTER = _PLANT.part("emitter", "Emitter")
_ENTRY = _PLANT.part("entry_eye", "RetroreflectiveSensor")
_TALL = _PLANT.part("tall_eye", "RetroreflectiveSensor")
_GATE = _PLANT.part("gate", "PivotDiverter")
_CHUTE = _PLANT.part("chute", "Chute")
_FAR = _PLANT.part("short_count", "Remover")
_PLANT.part("tall_count", "Remover")

PD_BELT_SPEED = _BELT.number("speed")
PD_EMIT_AT = _EMITTER.x
PD_EYE_AT = _ENTRY.x
#: The two beams' heights above the belt: a carton breaks one only if it is
#: taller than that (`PhotoelectricSensor.cs`, `BeamY`).
PD_ENTRY_HEIGHT = _ENTRY.number("height")
PD_TALL_HEIGHT = _TALL.number("height")
PD_GATE_AT = _GATE.x
PD_DIVERT_ANGLE = _GATE.number("divert_angle")
PD_SWING_SPEED = _GATE.number("swing_speed")
PD_POT_START = pot_start(_PLANT)
#: `PivotDiverter.cs` (`IsDiverted`, `IsHome`): within half a degree of
#: either end.
PD_LIMIT_BAND = 0.5
#: Where the far-end remover takes a carton running straight.
PD_FAR_AT = remover_catch(_FAR, _BELT.span()[1])

#: Measured in the engine; see above. Offsets from the gate's x.
PD_MEET = PD_GATE_AT - 0.05
PD_MISS = PD_GATE_AT + 0.70
PD_RELEASE = {True: PD_GATE_AT + 0.975, False: PD_GATE_AT + 1.275}
PD_COUNTED = {True: PD_GATE_AT + 1.43, False: PD_GATE_AT + 1.67}
#: A struck carton is flung, not slid: the chute counted it 0.4-0.7 s after
#: the blade reached it, and 1.8-2.0 s when it was struck early and rode the
#: blade in.
PD_STRUCK_COUNT = 0.6
#: A carton let go half turned has been slowed on the blade and rides on
#: sideways: the far end counted it this much of its time on the blade late,
#: in belt travel (0.69 m late after 0.9 m on it at 0.5 m/s; 0.95 m after
#: 1.25 m at 0.3).
PD_RELEASE_LAG = 0.8

#: The exam slows the belt to one of these fractions of its rating, from the
#: seed, at PD_SLOW_AT.
PD_SLOWED = (0.6, 0.7)
PD_SLOW_AT = 24.0

# The layout the offsets were measured on, checked rather than trusted.
_Z0 = _BELT.position[2]


def _turned(part, radians: float) -> bool:
    return (abs(part.rotation[0]) < 1e-6 and abs(part.rotation[2]) < 1e-6
            and abs(part.rotation[1] - radians) < 1e-4)


def _beam_across(eye) -> bool:
    """A retroreflective beam runs along the sensor's -Z; turned 180 deg, +Z.
    Either way it has to cross the whole belt at the eyes."""
    z, reach = eye.position[2], eye.number("range")
    if _turned(eye, 0.0):
        return z - reach <= _Z0 - 0.25 and z >= _Z0 + 0.25
    if _turned(eye, math.pi):
        return z + reach >= _Z0 + 0.25 and z <= _Z0 - 0.25
    return False


if not (_BELT.span()[0] <= PD_EMIT_AT < PD_EYE_AT < PD_GATE_AT
        and abs(_BELT.number("size_z") - 0.5) < 1e-6
        and abs(_TALL.x - PD_EYE_AT) < 1e-6
        and _beam_across(_ENTRY) and _beam_across(_TALL)
        and PD_ENTRY_HEIGHT < SHORT_HEIGHT < PD_TALL_HEIGHT < TALL_HEIGHT
        and _turned(_GATE, 0.0) and abs(_GATE.position[2] - (_Z0 - 0.5)) < 1e-6
        and abs(_GATE.number("blade_length") - 0.75) < 1e-6
        and abs(PD_DIVERT_ANGLE - 45.0) < 1e-6
        and _turned(_CHUTE, 0.0) and abs(_CHUTE.position[2] - (_Z0 + 0.5)) < 1e-6
        # World x, not the progress coordinate: a turned carton crosses the
        # far edge within a metre of the post.
        and _CHUTE.x - _CHUTE.number("ramp_width") / 2 <= PD_GATE_AT
        and _CHUTE.x + _CHUTE.number("ramp_width") / 2 >= PD_GATE_AT + 1.0
        and "lip_drop" not in _CHUTE.properties):
    raise TemplateError(f"{_PLANT.path.name}: the pivot diverter line's layout is not "
                        f"the one the grader's measured offsets belong to")


# --- the operator contract (IP-12) ------------------------------------------
#
# After the diverting exam, the E-stop sheet (`plant.OperatorExam`).
# "Stopped" is the belt. The blade is not part of it: a swing already begun
# finishes on its own drive, and a blade held across a stopped belt holds the
# carton on it, which slides on along the blade when the belt restarts --
# where a carton is on the blade scales with belt travel, not with time, so no
# carton is the E-stop's to missort, and the examiner strikes whenever the
# belt is running.

#: Where the window used to end.
PD_TESTS_END = 75.0
PD_ESTOP_AT = PD_TESTS_END + 0.5
PD_EXAM_ENDS_BY = operator_exam_ends_by(PD_ESTOP_AT)


class PivotDivertScene(PlantScene):
    name = "pivot-divert"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=PD_POT_START)
        self._declare(
            Tag("belt.rotate", "Conveyor 0 (Rotate)", "bit", "output"),
            fault_input("belt", "Conveyor 0 Drive Fault"),
            Tag("emitter.emit", "Emitter 0 (Emit)", "bit", "output"),
            Tag("entry_eye.detect", "Sensor 0 (Detect)", "bit", "input"),
            Tag("tall_eye.detect", "Sensor 0 (Detect)", "bit", "input"),
            Tag("gate.divert", "Diverter 0 (Divert)", "bit", "output"),
            Tag("gate.diverted", "Diverter 0 (Diverted)", "bit", "input"),
            Tag("gate.home", "Diverter 0 (Home)", "bit", "input", value=True),
            fault_input("gate", "Diverter 0 Drive Fault"),
            Tag("tall_count.count", "Remover 0 (Count)", "int", "input"),
            Tag("short_count.count", "Remover 0 (Count)", "int", "input"),
        )
        self.belt_speed = PD_BELT_SPEED
        self.slowed = float(self.rng.choice(PD_SLOWED))
        #: Tall or not, carton by carton: two of each, shuffled in blocks.
        self.feed = shuffled_cycle(self.rng, [True, True, False, False], 8)
        self._fed = 0
        self.angle = 0.0
        self.items: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        # --- ground truth ---
        #: One entry per carton: tall, lane, and what the blade did to it.
        self.ledger: dict[int, dict] = {}
        self.done: list[Item] = []

        self.script = Script([
            (1.0, self.panel.press("start")),
            (PD_SLOW_AT, self._slow_the_belt),
        ])
        self.operator = OperatorExam(self, PD_ESTOP_AT, noun="belt", what="the belt")

    def _slow_the_belt(self) -> None:
        """What the belt's "Belt Speed" slider does in the engine. No tag
        reports it; the eyes do, in how long a carton now takes."""
        self.belt_speed = PD_BELT_SPEED * self.slowed

    def _finish(self, item: Item, lane: str) -> None:
        item.lane = lane
        record = self.ledger[item.id]
        record["lane"] = lane
        record["at"] = round(self.t, 2)
        self.done.append(item)

    def step(self, dt: float) -> None:
        # The emitter: a carton per rising edge, of the height the feed says.
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            tall = self.feed[self._fed % len(self.feed)]
            self._fed += 1
            item = Item(height=TALL_HEIGHT if tall else SHORT_HEIGHT,
                        position=PD_EMIT_AT, id=self._next_id)
            self._next_id += 1
            self.items.append(item)
            self.ledger[item.id] = {"tall": tall, "fed_at": round(self.t, 2),
                                    "state": "belt", "blade_at_meet": None,
                                    "struck_at": None, "released_at": None,
                                    "lane": None, "at": None}
        self._emit_edge = emit

        # The blade (`UpdateSwing`): a fault freezes it where it is.
        was_angle = self.angle
        if not self.bit("gate.fault"):
            target = PD_DIVERT_ANGLE if self.bit("gate.divert") else 0.0
            step = PD_SWING_SPEED * dt
            self.angle += max(min(target - self.angle, step), -step)
        diverted = self.angle >= PD_DIVERT_ANGLE - PD_LIMIT_BAND
        self.tags.set("gate.diverted", diverted)
        self.tags.set("gate.home", self.angle <= PD_LIMIT_BAND)
        swinging_out = self.angle > was_angle + 1e-9

        # The cartons. Everything the belt drives advances by the belt's
        # travel; what the blade has done to a carton decides where that
        # travel takes it.
        running = self.bit("belt.rotate") and not self.bit("belt.fault")
        self.operator.driven = running
        travel = self.belt_speed * dt if running else 0.0
        still: list[Item] = []
        for item in self.items:
            record = self.ledger[item.id]
            tall = record["tall"]
            before = item.position
            item.position += travel
            state = record["state"]

            if state == "belt" and before < PD_MEET <= item.position:
                record["blade_at_meet"] = round(self.angle, 1)
                state = "captured" if diverted else "unguarded"
            if state == "unguarded":
                if item.position >= PD_MISS:
                    state = "passed"
                elif swinging_out:
                    state = "struck"
                    record["struck_at"] = round(self.t, 2)
                    record["struck_counted"] = self.t + PD_STRUCK_COUNT
            if state == "captured":
                if not diverted and item.position < PD_RELEASE[tall]:
                    state = "released"
                    record["released_at"] = round(self.t, 2)
                    record["released_lag"] = PD_RELEASE_LAG * (item.position - PD_MEET)

            record["state"] = state
            if state == "captured" and item.position >= PD_COUNTED[tall]:
                self._finish(item, "chute")
            elif state == "struck" and self.t >= record["struck_counted"]:
                self._finish(item, "chute")
            elif state in ("belt", "unguarded", "passed", "released") and \
                    item.position >= PD_FAR_AT + record.get("released_lag", 0.0):
                self._finish(item, "far")
            else:
                still.append(item)
        self.items = still

        # The eyes, upstream of anything the blade touches: a carton breaks a
        # beam while its length covers it, if it is taller than the beam.
        def in_beam(item: Item, height: float) -> bool:
            return (abs(item.position - PD_EYE_AT) <= CARTON_LENGTH / 2
                    and item.height > height)

        self.tags.set("entry_eye.detect", any(in_beam(i, PD_ENTRY_HEIGHT) for i in self.items))
        self.tags.set("tall_eye.detect", any(in_beam(i, PD_TALL_HEIGHT) for i in self.items))
        self.tags.set("tall_count.count", sum(1 for i in self.done if i.lane == "chute"))
        self.tags.set("short_count.count", sum(1 for i in self.done if i.lane == "far"))


def grade_pivot_divert(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: PivotDivertScene = watched.inner
    cartons = [dict(id=k, **v) for k, v in sorted(sim.ledger.items())]
    done = [c for c in cartons if c["lane"] is not None]
    chute = [c for c in done if c["lane"] == "chute"]
    far = [c for c in done if c["lane"] == "far"]
    wrong = [c for c in done if (c["lane"] == "chute") != c["tall"]]
    struck = [c for c in cartons if c["struck_at"] is not None]
    released = [c for c in cartons if c["released_at"] is not None]
    after_slow = [c for c in done if c["fed_at"] >= PD_SLOW_AT]

    report.evidence.update({
        "belt_speed_first": PD_BELT_SPEED,
        "belt_speed_then": round(PD_BELT_SPEED * sim.slowed, 3),
        "slowed_at": PD_SLOW_AT,
        "fed": sim._fed,
        "reached_a_lane": len(done),
        "chute": len(chute),
        "far_end": len(far),
        "judged_after_the_slowdown": len(after_slow),
        "misrouted": [{"carton": c["id"], "tall": c["tall"], "lane": c["lane"],
                       "fed_at": c["fed_at"], "released_at": c["released_at"]}
                      for c in wrong][:20],
        "struck": [{"carton": c["id"], "tall": c["tall"], "at": c["struck_at"],
                    "blade_at_meet_deg": c["blade_at_meet"]} for c in struck][:20],
        "released": [{"carton": c["id"], "tall": c["tall"], "at": c["released_at"]}
                     for c in released][:20],
        "cartons": cartons[:40],
    })

    report.add("divert.ran",
               len(done) >= 8 and len(chute) >= 2 and len(far) >= 2 and len(after_slow) >= 2,
               f"{len(done)} cartons reached a lane (at least 8): chute {len(chute)}, far end "
               f"{len(far)} (at least 2 each), {len(after_slow)} fed after the belt was slowed "
               f"(at least 2)")
    report.add("divert.sorted",
               bool(done) and not wrong,
               "every carton went to the lane its height asked for" if done and not wrong else
               ("no carton reached a lane" if not done else
                f"{len(wrong)} carton(s) in the wrong lane: "
                + ", ".join(f"{'tall' if c['tall'] else 'short'} #{c['id']} -> {c['lane']}"
                            for c in wrong[:6])))
    report.add("divert.blade_ready",
               not struck,
               "every carton the blade turned met it already across" if not struck else
               f"the blade was still swinging when it met {len(struck)} carton(s), first at "
               f"{struck[0]['struck_at']:g}s")

    say = report.feedback.append
    if watched.held_true("belt.rotate") == 0.0:
        say("The belt never ran. `belt.rotate` is yours to write.")
        return
    if sim._fed == 0:
        say("No cartons were fed. `emitter.emit` makes one on each RISING edge.")
        return
    tall_far = [c for c in wrong if c["tall"]]
    short_chute = [c for c in wrong if not c["tall"]]
    let_go = [c for c in tall_far if c["released_at"] is not None]
    never = [c for c in tall_far if c["released_at"] is None]
    if let_go:
        c = let_go[0]
        say(f"Tall carton {c['id']} was on the blade and was let go at {c['released_at']:g}s, "
            f"before it was off the belt, so it rode on to the end. The belt was slowed to "
            f"{sim.slowed:.0%} of its speed at {PD_SLOW_AT:g}s -- nothing on the bus says so -- "
            f"and a carton then takes longer to slide off. Hold the blade until "
            f"`tall_count.count` says the carton has arrived, not for a time.")
    if never:
        c = never[0]
        say(f"Tall carton {c['id']} ran past a blade that was not across when it got there "
            f"(the blade stood at {c['blade_at_meet'] or 0:g} deg). `tall_eye.detect` is "
            f"true for the moment the carton is in the beam, a metre before the blade: "
            f"latch the decision and hold the blade until the carton is in the chute.")
    if short_chute:
        c = short_chute[0]
        say(f"Short carton {c['id']} went into the chute: the blade was still across when "
            f"it arrived. Home it once the tall carton ahead has been counted.")
    if struck:
        c = struck[0]
        say(f"The blade was still swinging out when it met carton {c['id']} at "
            f"{c['struck_at']:g}s, so it went into the chute by being hit. A pusher is fired "
            f"when the carton is beside it; a blade has to be across before the carton "
            f"reaches it -- swing it out when `tall_eye` sees the carton, and it is waiting "
            f"with time to spare.")
    mark_contract(sim.operator, report, watched.sim_time)


def _summary_pivot(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, {evidence['reached_a_lane']} reached a lane: chute "
        f"{evidence['chute']}, far end {evidence['far_end']}; belt slowed from "
        f"{evidence['belt_speed_first']:g} to {evidence['belt_speed_then']:g} m/s at "
        f"{evidence['slowed_at']:g}s")
    for entry in evidence["misrouted"][:8]:
        out(f"  {'tall' if entry['tall'] else 'short'} carton {entry['carton']:>3} -> "
            f"{entry['lane']}" + (f" (let go at {entry['released_at']:g}s)"
                                  if entry["released_at"] is not None else ""))
    if evidence["struck"]:
        out(f"struck by a moving blade: {[s['carton'] for s in evidence['struck']]}")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Pivot diverter line",
    "task": ("Turn every tall carton off a running belt into the chute and let "
             "every short one run on: the blade across before a tall carton "
             "reaches it, held until the chute has it, home for the short one "
             "behind. This run slows the belt. The mushroom is normally "
             "closed, stops the belt within 200 ms and latches -- only Reset, "
             "then Start, runs it again."),
    "build": PivotDivertScene,
    "observe": None,
    "grade": grade_pivot_divert,
    "summary": _summary_pivot,
    "duration": PD_EXAM_ENDS_BY,
    "references": ("good", "timed", "unlatched", "late", "everyother", "noestop",
                   "startalone"),
    "tags": ("belt.rotate, emitter.emit, gate.divert, panel.green, panel.red are "
             "yours to write; entry_eye.detect, tall_eye.detect, gate.diverted, "
             "gate.home, gate.fault, belt.fault, tall_count.count, "
             "short_count.count, panel.setpoint and the buttons are the line's."),
}
