"""`press-station`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/press_station.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (BELT_THICKNESS, PlantScene, Script, declare_stack_light,
                     fault_input, pot_start)
from ..templates import TemplateError, template


SCENE = "press-station"


# --- press station --------------------------------------------------------
#
# Observable fact: where the ram was, every tick, against the position of the
# mode selector and the verdict of the two-hand relay at that tick. The plant
# owns all three, so it knows whether the ram moved down while the selector
# said OFF, or in MANUAL without the relay's permissive -- and a program can
# arrange none of that from the bus.
#
# The lesson is the mode interlock. The selector decides who may move the ram:
# the sequencer (AUTO), the operator's two hands (MANUAL), or nobody (OFF).
# Two ways a program gets it wrong, both of which the exam provokes:
#
# * it ANDs the two palm buttons itself. `hands.left AND hands.right` is not
#   the permissive -- `hands.valid` is, and the relay only gives it when the
#   two presses arrived within its sync window. The examiner ties one button
#   down and presses the other a second later: both bits read true, `valid`
#   does not, and a ram that moves has been worked one-handed.
# * it lets the sequencer ignore the selector. Turn to OFF mid-cycle and an
#   automatic cycle that carries on is a press nobody can stop by switching it
#   off.
#
# And in MANUAL the ram moves only while `valid` is made -- hold-to-run: a
# press held shorter than the stroke has to bring the ram back up.
#
# The parts are `PneumaticCylinder.cs` (`Step`), `LimitSwitch.cs`
# (`MeasureDeflection` :261, `StepContacts` :287), `TwoHandControl.cs`
# (`Press` :225, `Step` :252) and `SelectorSwitch.cs`. The stroke, rod speed,
# lever, heights, sync window and hold time are the template's, and the
# bottom-dead-centre switch's trip point is worked out from where the
# template puts it against the ram's face plate.
_PLANT = template(SCENE)
_RAM = _PLANT.part("ram", "PneumaticCylinder")
_BDC = _PLANT.part("bdc", "LimitSwitch")
_HANDS = _PLANT.part("hands", "TwoHandControl")
_MODE = _PLANT.part("mode", "SelectorSwitch")

PS_STROKE = _RAM.number("stroke")
PS_ROD_SPEED = _RAM.number("rod_speed")
PS_REED_BAND = _RAM.number("reed_band")
PS_PLATE_HALF = _RAM.number("plate_width") / 2
PS_LEVER = _BDC.number("lever_length")
PS_LEVER_HEIGHT = _BDC.number("height")
PS_SYNC = _HANDS.number("sync_window")
PS_HOLD = _HANDS.number("hold_time")
PS_POT_START = pot_start(_PLANT)
if _BDC.number("bounce_ms") != 0.0:
    raise TemplateError(f"{_BDC.source}: the grader's model of the limit switch "
                        f"assumes clean contacts (bounce_ms 0)")
#: The selector's detents: MAN, OFF, AUTO, and where it rests.
PS_LABELS = [label.strip() for label in _MODE.properties["labels"].split(",")]
if PS_LABELS != ["MAN", "OFF", "AUTO"] or _MODE.number("positions") != 3:
    raise TemplateError(f"{_MODE.source}: the grader models a MAN/OFF/AUTO selector")
PS_MAN, PS_OFF, PS_AUTO = 0, 1, 2
PS_DETENT_START = int(_MODE.number("detent"))

#: `PneumaticCylinder.cs` (`AxisY` :66, `PlateThickness` :69, `PlateHeight`
#: :70) and the rod's 0.02 m stub in `Apply`; `LimitSwitch.cs` (:72-:87);
#: `PartLayout.cs` (`StandardBeltWidth`).
PS_AXIS_Y = 0.12
PS_PLATE_THICKNESS = 0.03
PS_PLATE_HEIGHT = 0.16
PS_ROD_STUB = 0.02
PS_TRIP_ANGLE = 20.0
PS_DIFFERENTIAL = 6.0
PS_MAX_ANGLE = 75.0
PS_ROLLER = 0.02
PS_STANDARD_BELT_WIDTH = 0.5
PS_SHAFT = PS_STANDARD_BELT_WIDTH / 2 + 0.07

# The lever's probe runs from the shaft towards -Z at the lever's height; the
# ram strokes towards +Z. Both unrotated, the probe must cross the plate.
if any(abs(r) > 1e-9 for r in _RAM.rotation + _BDC.rotation):
    raise TemplateError("press-station: the grader models an unrotated ram and switch")
_LEVER_Y = BELT_THICKNESS / 2 + PS_LEVER_HEIGHT
if not (abs(_BDC.x - _RAM.x) < PS_PLATE_HALF
        and abs((_BDC.position[1] + _LEVER_Y) - (_RAM.position[1] + PS_AXIS_Y))
        < PS_PLATE_HEIGHT / 2):
    raise TemplateError(f"{_BDC.source}: the bottom-dead-centre switch's lever does "
                        f"not cross the ram's face plate")
PS_SHAFT_Z = _BDC.position[2] + PS_SHAFT


def lever_angle(extension: float) -> float:
    """`LimitSwitch.MeasureDeflection`: the angle the ram's face plate pushes
    the lever to, degrees. The plate's front face stands `extension + stub +
    thickness` in front of the ram's origin."""
    front = _RAM.position[2] + extension + PS_ROD_STUB + PS_PLATE_THICKNESS
    hit = PS_SHAFT_Z - front
    if hit < 0.0 or hit > PS_LEVER + PS_ROLLER:
        return 0.0
    reach = hit - PS_ROLLER
    if reach >= PS_LEVER:
        return 0.0
    cos = min(max(reach / max(PS_LEVER, 0.01), -1.0), 1.0)
    return min(math.degrees(math.acos(cos)), PS_MAX_ANGLE)


#: Where the switch makes and breaks along the stroke, for the report.
PS_TRIP_AT = next((e / 1000 for e in range(int(PS_STROKE * 1000) + 1)
                   if lever_angle(e / 1000) >= PS_TRIP_ANGLE), None)
if PS_TRIP_AT is None:
    raise TemplateError(f"{_BDC.source}: the ram's full stroke never trips the "
                        f"bottom-dead-centre switch")

#: The exam. The selector and both palms are the examiner's hand.
PS_OFF_AT, PS_MAN_AT = 19.0, 24.0
PS_TIE_LEFT, PS_TIE_RIGHT = 26.0, 27.0
PS_TWO_HANDS_AT = 31.0
PS_SHORT_AT = 38.0
#: The AUTO dwell the examiner turns the pot to before Start, from the seed.
PS_DWELLS = (0.8, 1.2, 1.5)
#: How long after `valid` drops the ram may still be driven down: two scans.
PS_GRACE = 0.1
#: Metres of down-stroke that count as "the ram moved".
PS_MOVED = 0.01


class Palm:
    """One palm button of `TwoHandControl.cs`: a click holds it `hold_time`;
    a click on one already held renews the hold and keeps its timestamp."""

    def __init__(self) -> None:
        self.held = 0.0
        self.pressed_at = 0.0


class PressScene(PlantScene):
    name = "press-station"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=PS_POT_START)
        self._declare(
            Tag("ram.extend", "Cylinder 0 Extend Coil", "bit", "output"),
            Tag("ram.retract", "Cylinder 0 Retract Coil", "bit", "output"),
            Tag("ram.extended", "Cylinder 0 Extended Reed", "bit", "input"),
            Tag("ram.retracted", "Cylinder 0 Retracted Reed", "bit", "input", value=True),
            fault_input("ram", "Cylinder 0 Seized"),
            Tag("bdc.no", "Limit Switch 0 NO Contact", "bit", "input"),
            Tag("bdc.nc", "Limit Switch 0 NC Contact", "bit", "input", value=True),
            Tag("hands.left", "Two-Hand 0 Left Held", "bit", "input"),
            Tag("hands.right", "Two-Hand 0 Right Held", "bit", "input"),
            Tag("hands.valid", "Two-Hand 0 Permissive", "bit", "input"),
            Tag("mode.position", "Selector 0 Position", "int", "input",
                value=PS_DETENT_START),
        )
        declare_stack_light(self.tags)
        self.extension = 0.0
        self.spool_extends = False
        self.actuated = False
        self.clock = 0.0
        self.palms = {"left": Palm(), "right": Palm()}
        self.valid = False
        self.detent = PS_DETENT_START
        self._invalid_for = 0.0

        # --- ground truth ---
        self.strokes: list[dict] = []          # every time BDC was made
        self.off_travel = 0.0                   # metres down while OFF
        self.unpermitted_travel = 0.0           # metres down in MAN without valid
        self.unpermitted_at: list[float] = []
        self.tie_down_travel = 0.0              # metres down during the tie-down
        self.two_hand_bdc = False               # BDC made during the long press
        self.short_press: dict = {}             # the hold-to-run press
        self._stroke: dict | None = None

        self.script = Script([
            (0.3, self.panel.set_setpoint(float(self.rng.choice(PS_DWELLS)))),
            (0.5, self._turn(PS_AUTO)),
            (1.0, self.panel.press("start")),
            (PS_OFF_AT, self._turn(PS_OFF)),
            (PS_MAN_AT, self._turn(PS_MAN)),
            (PS_TIE_LEFT, self._press("left")),
            (PS_TIE_RIGHT, self._press("right")),
            (PS_TWO_HANDS_AT, self._press("left", "right")),
            (PS_TWO_HANDS_AT + 1.5, self._press("left", "right")),
            (PS_SHORT_AT, self._press("left", "right")),
        ])

    # --- the examiner's hand ---

    def _turn(self, detent: int):
        def do() -> None:
            self.detent = detent
        do.__name__ = f"selector to {PS_LABELS[detent]}"
        return do

    def _press(self, *which: str):
        def do() -> None:
            # `TwoHandControl.Press`: a fresh press is timestamped on the
            # station's clock; re-pressing a held palm renews it and keeps
            # the timestamp, so a renewal is not a new, simultaneous press.
            for name in which:
                palm = self.palms[name]
                if palm.held <= 0.0:
                    palm.pressed_at = self.clock
                palm.held = PS_HOLD
        do.__name__ = f"press {' and '.join(which)}"
        return do

    # --- the plant ---

    def step(self, dt: float) -> None:
        # The two-hand station (`Step` :252).
        self.clock += dt
        for palm in self.palms.values():
            if palm.held > 0.0:
                palm.held = max(palm.held - dt, 0.0)
        left, right = self.palms["left"], self.palms["right"]
        self.valid = (left.held > 0.0 and right.held > 0.0
                      and abs(left.pressed_at - right.pressed_at) <= PS_SYNC)
        self.tags.set("hands.left", left.held > 0.0)
        self.tags.set("hands.right", right.held > 0.0)
        self.tags.set("hands.valid", self.valid)
        self.tags.set("mode.position", self.detent)

        # The ram: a 5/2 double-solenoid valve and its reeds.
        extend, retract = self.bit("ram.extend"), self.bit("ram.retract")
        if extend and not retract:
            self.spool_extends = True
        elif retract and not extend:
            self.spool_extends = False
        was = self.extension
        target = PS_STROKE if self.spool_extends else 0.0
        step = PS_ROD_SPEED * dt
        self.extension += max(min(target - self.extension, step), -step)
        self.tags.set("ram.extended", self.extension >= PS_STROKE - PS_REED_BAND)
        self.tags.set("ram.retracted", self.extension <= PS_REED_BAND)

        # The bottom-dead-centre switch, clean contacts.
        angle = lever_angle(self.extension)
        self.actuated = (angle > PS_TRIP_ANGLE - PS_DIFFERENTIAL if self.actuated
                         else angle >= PS_TRIP_ANGLE)
        self.tags.set("bdc.no", self.actuated)
        self.tags.set("bdc.nc", not self.actuated)

        self._record(max(self.extension - was, 0.0), dt)

    def _record(self, down: float, dt: float) -> None:
        t = self.t
        self._invalid_for = 0.0 if self.valid else self._invalid_for + dt
        if self.actuated:
            if self._stroke is None:
                self._stroke = {"at": round(t, 2), "mode": PS_LABELS[self.detent],
                                "held_s": 0.0}
                self.strokes.append(self._stroke)
            self._stroke["held_s"] = round(self._stroke["held_s"] + dt, 2)
            if PS_TWO_HANDS_AT <= t < PS_SHORT_AT:
                self.two_hand_bdc = True
        else:
            self._stroke = None
        if self.detent == PS_OFF:
            self.off_travel += down
        if self.detent == PS_MAN and self._invalid_for > PS_GRACE and down > 0.0:
            self.unpermitted_travel += down
            if not self.unpermitted_at or t - self.unpermitted_at[-1] > 1.0:
                self.unpermitted_at.append(round(t, 2))
        if PS_TIE_RIGHT <= t < PS_TIE_RIGHT + PS_HOLD:
            self.tie_down_travel += down
        if PS_SHORT_AT <= t:
            short = self.short_press
            short.setdefault("deepest_m", 0.0)
            short["deepest_m"] = round(max(short["deepest_m"], self.extension), 3)
            if not self.valid and "released_at" not in short and t > PS_SHORT_AT + 0.1:
                short["released_at"] = round(t, 2)
                short["at_release_m"] = round(self.extension, 3)
            if "released_at" in short and self.extension <= PS_REED_BAND \
                    and "home_at" not in short:
                short["home_at"] = round(t, 2)


def grade_press(watched: Watched, engine: GradedEngine, report: Report,
                duration: float) -> None:
    sim: PressScene = watched.inner
    pot = sim.panel.setpoint_value
    auto = [s for s in sim.strokes if s["mode"] == "AUTO"]
    short_dwell = [s for s in auto if s["held_s"] < pot - 0.1]
    short = sim.short_press

    report.evidence.update({
        "pot_dwell_s": pot,
        "bdc_trips_at_m": PS_TRIP_AT,
        "strokes": sim.strokes,
        "off_travel_m": round(sim.off_travel, 3),
        "unpermitted_travel_m": round(sim.unpermitted_travel, 3),
        "unpermitted_at": sim.unpermitted_at[:10],
        "tie_down_travel_m": round(sim.tie_down_travel, 3),
        "two_hand_press_reached_bdc": sim.two_hand_bdc,
        "short_press": short,
    })

    report.add("auto.cycled",
               len(auto) >= 2,
               f"{len(auto)} stroke(s) reached bottom dead centre in AUTO (at least 2)")
    report.add("auto.dwelled",
               bool(auto) and not short_dwell,
               f"every AUTO stroke held bottom dead centre for the pot's {pot:g}s"
               if auto and not short_dwell else
               f"{len(short_dwell)} AUTO stroke(s) came back early: "
               + ", ".join(f"{s['held_s']:g}s" for s in short_dwell[:4]))
    report.add("mode.off_is_off",
               sim.off_travel <= PS_MOVED,
               f"the ram moved {sim.off_travel * 1000:.0f} mm down with the selector at "
               f"OFF (at most {PS_MOVED * 1000:.0f})")
    report.add("mode.manual_needs_both_hands",
               sim.unpermitted_travel <= PS_MOVED,
               f"in MANUAL the ram moved {sim.unpermitted_travel * 1000:.0f} mm down "
               f"without the two-hand permissive (at most {PS_MOVED * 1000:.0f})"
               + (f", from {sim.unpermitted_at[:3]}s" if sim.unpermitted_at else ""))
    report.add("manual.two_hands_stroke",
               sim.two_hand_bdc,
               "a two-hand press held through the stroke reached bottom dead centre"
               if sim.two_hand_bdc else
               "the examiner held both palms through a whole stroke and the ram never "
               "reached bottom dead centre")

    _press_feedback(report, sim, auto, short_dwell, pot)


def _press_feedback(report, sim, auto, short_dwell, pot) -> None:
    say = report.feedback.append
    if not sim.strokes and sim.extension == 0.0 and sim.off_travel == 0.0:
        say("The ram never moved. `ram.extend` and `ram.retract` are two coils on a "
            "valve with no spring; the selector (`mode.position`: 0 MAN, 1 OFF, "
            "2 AUTO) decides which of your sequences may drive them.")
    if sim.tie_down_travel > PS_MOVED:
        say(f"The examiner held the left palm down and pressed the right one a second "
            f"later, and the ram moved {sim.tie_down_travel * 1000:.0f} mm. Both "
            f"`hands.left` and `hands.right` read true then -- and `hands.valid` did "
            f"not, because the presses were more than {PS_SYNC:g}s apart. That is a "
            f"taped-down button, and the relay's verdict exists to refuse it: drive "
            f"the ram on `hands.valid`, never on left AND right.")
    elif sim.unpermitted_travel > PS_MOVED:
        say(f"In MANUAL the ram went down {sim.unpermitted_travel * 1000:.0f} mm while "
            f"`hands.valid` was false. MANUAL is hold-to-run: the ram moves only "
            f"while both hands are on, and comes back up the moment they leave.")
    if sim.off_travel > PS_MOVED:
        say(f"With the selector at OFF the ram went down {sim.off_travel * 1000:.0f} mm. "
            f"The selector is read every scan, not only at Start: an automatic cycle "
            f"that carries on after somebody turned the press off is a press nobody "
            f"can switch off.")
    if short_dwell:
        say(f"In AUTO the ram came back up before the pot's {pot:g}s at bottom dead "
            f"centre. Time the dwell from `bdc.no`, the switch the ram makes at the "
            f"bottom of its stroke.")


def _summary_press(evidence: dict, out) -> None:
    strokes = evidence["strokes"]
    out(f"{len(strokes)} stroke(s) to bottom dead centre: "
        + ", ".join(f"{s['mode']} at {s['at']:g}s ({s['held_s']:g}s)" for s in strokes[:8]))
    out(f"down-stroke at OFF {evidence['off_travel_m'] * 1000:.0f} mm; in MANUAL without "
        f"the permissive {evidence['unpermitted_travel_m'] * 1000:.0f} mm, of it during "
        f"the tie-down {evidence['tie_down_travel_m'] * 1000:.0f} mm")
    out(f"two-hand press reached bottom dead centre: "
        f"{'yes' if evidence['two_hand_press_reached_bdc'] else 'no'}")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Press station",
    "task": ("Run the press in the mode the selector says. AUTO: cycle on "
             "Start, dwelling the pot's seconds at the limit switch. OFF: "
             "nothing moves. MANUAL: the ram goes down only while the two-hand "
             "relay's `valid` is made, and comes back when it drops or at the "
             "bottom."),
    "build": PressScene,
    "observe": None,
    "grade": grade_press,
    "summary": _summary_press,
    "duration": 46.0,
    "references": ("good", "andhands", "ignoresmode"),
    "tags": ("ram.extend, ram.retract, tower.green/yellow/red, panel.green, "
             "panel.red are yours to write; ram.extended, ram.retracted, "
             "ram.fault, bdc.no, bdc.nc, hands.left, hands.right, hands.valid, "
             "mode.position, panel.setpoint and the buttons are the plant's."),
}
