"""`servo-positioning`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/servo_positioning.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import PlantScene, Script, declare_stack_light, pot_start
from ..templates import template


SCENE = "servo-positioning"


# --- servo positioning ---------------------------------------------------
#
# Observable fact: where the carriage was, tick by tick, and when it moved.
# The plant runs the drive, so it knows the instant the drive stopped itself
# on a fault, whether the carriage moved again before anybody pressed Reset,
# and every place it came to rest -- none of which a controller can arrange
# from the bus.
#
# How a program fakes it: a servo exercise is easy to pass on a drive that
# never faults, and that is the one thing a real drive does not promise. The
# lesson here is the handshake around an error: the drive stops itself and
# latches `error`; the program has to notice, hold its sequence, and leave the
# acknowledgement to a person -- `ack` on the operator's Reset, once the cause
# has gone. A program that pulses `ack` whenever it sees an error with no
# fault behind it is automatic restart by another name: the carriage moves off
# on its own the moment the cause clears, with nobody having asked. So the
# exam raises the drive fault mid-move, clears it three seconds later, and
# presses Reset three seconds after that, and the plant records whether the
# carriage moved in between.
#
# And, as every scene with a pot does, the pot moves mid-run: it is station B,
# so a station written into the program goes to the wrong place.
#
# The drive is `engine/src/Parts/ServoAxis.cs` (`Step` :267, `Track` :322),
# its stroke, speed limit, acceleration and window read from the template.
_PLANT = template(SCENE)
_AXIS = _PLANT.part("axis", "ServoAxis")

SV_STROKE = _AXIS.number("stroke_mm")
SV_MAX_VELOCITY = _AXIS.number("max_velocity")
SV_ACCELERATION = _AXIS.number("acceleration")
SV_WINDOW = _AXIS.number("window")
SV_POT_START = pot_start(_PLANT)
#: `ServoAxis.cs` owns these (`EnableDelay` :90, `QuickStopFactor` :94).
SV_ENABLE_DELAY = 0.10
SV_QUICK_STOP = 4.0
#: Station A, fixed; the brief names it. Station B is the pot.
SV_STATION_A = 100.0
#: Where the exam turns the pot to, part way through, from the seed.
SV_POT_THEN = (450.0, 550.0, 650.0)
#: The exam's clock: the pot moves, and after that the drive is faulted on
#: the first tick it is moving, cleared FAULT_FOR seconds later, and Reset is
#: pressed RESET_AFTER seconds after the fault cleared.
SV_POT_AT = 11.0
SV_FAULT_FROM = 19.0
SV_FAULT_FOR = 3.0
SV_RESET_AFTER = 3.0
#: Moving, for "the drive is mid-move", in mm/s.
SV_MOVING = 200.0
#: How far the carriage may creep and still count as held, in mm. A
#: quick-stopped axis stops within a few ticks and holds on its brake.
SV_HELD_WITHIN = 1.0
#: How long after the pot moved before an arrival at the old station B counts
#: against the program: one move of the whole stroke.
SV_POT_GRACE = 3.0


class ServoDrive:
    """`ServoAxis.Step` (:267) and `Track` (:322), in doubles."""

    def __init__(self) -> None:
        self.position = 0.0
        self.velocity = 0.0
        self.target = 0.0
        self.ready = False
        self.error = False
        self.error_text = ""
        self._enable_timer = 0.0
        self._last_ack = False

    def latch(self, why: str) -> None:
        if self.error:
            return
        self.error = True
        self.error_text = why

    def step(self, enable: bool, ack: bool, fault: bool, target: float,
             velocity: float, dt: float) -> bool:
        """One tick. Returns True on the tick an ack clears an error."""
        if fault:
            self.latch("drive fault")
        edge = ack and not self._last_ack
        self._last_ack = ack
        cleared = False
        if edge and self.error and not fault:
            self.error = False
            self.error_text = ""
            cleared = True

        self._enable_timer = self._enable_timer + dt if enable else 0.0
        powered = enable and self._enable_timer >= SV_ENABLE_DELAY
        if powered and not self.error:
            needs_move = abs(target - self.position) > SV_WINDOW
            if target < 0.0 or target > SV_STROKE:
                self.latch(f"target {target:.1f} mm is outside 0..{SV_STROKE:.0f} mm")
            elif needs_move and velocity <= 0.0:
                self.latch("move requested at a velocity of zero or less")

        self.ready = powered and not self.error
        if self.ready:
            self.target = target
            self._track(min(velocity, max(SV_MAX_VELOCITY, 1.0)), dt)
        else:
            decel = max(SV_ACCELERATION, 1.0) * SV_QUICK_STOP
            step = decel * dt
            self.velocity += max(min(0.0 - self.velocity, step), -step)
            self.position += self.velocity * dt

        if self.position < 0.0 or self.position > SV_STROKE:
            self.position = min(max(self.position, 0.0), SV_STROKE)
            self.velocity = 0.0
        return cleared

    def _track(self, cap: float, dt: float) -> None:
        accel = max(SV_ACCELERATION, 1.0)
        error = self.target - self.position
        if abs(error) < 1e-4 and abs(self.velocity) <= accel * dt:
            self.position, self.velocity = self.target, 0.0
            return
        slew = accel * dt
        stoppable = (math.sqrt(slew * slew + 8.0 * accel * abs(error)) - slew) / 2.0
        desired = math.copysign(min(max(cap, 0.0), stoppable), error)
        self.velocity += max(min(desired - self.velocity, slew), -slew)
        step = self.velocity * dt
        if (math.copysign(1.0, step) == math.copysign(1.0, error) and abs(step) >= abs(error)
                and abs(self.velocity) <= 2.0 * accel * dt):
            self.position, self.velocity = self.target, 0.0
            return
        self.position += step

    @property
    def in_position(self) -> bool:
        return (self.ready and abs(self.velocity) < 1e-3
                and abs(self.target - self.position) <= SV_WINDOW)


class ServoScene(PlantScene):
    name = "servo-positioning"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=SV_POT_START)
        self._declare(
            Tag("axis.enable", "Axis 0 Enable", "bit", "output"),
            Tag("axis.ack", "Axis 0 Error Acknowledge", "bit", "output"),
            Tag("axis.target", "Axis 0 Target Position (mm)", "float", "output"),
            Tag("axis.velocity", "Axis 0 Velocity (mm/s)", "float", "output"),
            Tag("axis.ready", "Axis 0 Ready", "bit", "input"),
            Tag("axis.error", "Axis 0 Error (latched)", "bit", "input"),
            Tag("axis.position", "Axis 0 Actual Position (mm)", "float", "input"),
            Tag("axis.inposition", "Axis 0 In Position", "bit", "input"),
            Tag("axis.fault", "Axis 0 Drive Fault", "bit", "input"),
            Tag("position_display.value", "Display 0 Value", "int", "output"),
        )
        declare_stack_light(self.tags)
        self.drive = ServoDrive()
        self.pot_then = float(self.rng.choice(SV_POT_THEN))

        # --- ground truth ---
        #: Every time the carriage came to rest after moving: when, where.
        self.arrivals: list[dict] = []
        self._moving = False
        #: Errors the drive latched, with why. The examiner's fault is one;
        #: anything else is a command the drive refused.
        self.errors: list[dict] = []
        #: The injected fault: when raised, cleared, Reset pressed; where the
        #: carriage stopped; how far it went before Reset; when the error
        #: was acknowledged.
        self.fault: dict | None = None
        self.pot_moved_at: float | None = None

        self.script = Script([
            (1.0, self.panel.press("start")),
            (SV_POT_AT, self._move_the_pot),
        ])

    def _move_the_pot(self) -> None:
        self.panel.set_setpoint(self.pot_then)()
        self.pot_moved_at = self.t

    def _clear_the_fault(self) -> None:
        self.fault["cleared_at"] = round(self.t, 2)

    def _press_reset(self) -> None:
        self.panel.press("reset")()
        self.fault["reset_at"] = round(self.t, 2)

    def step(self, dt: float) -> None:
        drive = self.drive
        if (self.fault is None and self.t >= SV_FAULT_FROM
                and abs(drive.velocity) > SV_MOVING):
            self.fault = {"raised_at": round(self.t, 2), "moving_at_mm_s": round(drive.velocity, 1),
                          "cleared_at": None, "reset_at": None, "stopped_at_mm": None,
                          "moved_before_reset_mm": 0.0, "acknowledged_at": None}
            self.script.at(self.t + SV_FAULT_FOR, self._clear_the_fault)
            self.script.at(self.t + SV_FAULT_FOR + SV_RESET_AFTER, self._press_reset)
        faulted = (self.fault is not None and self.fault["cleared_at"] is None)
        self.tags.set("axis.fault", faulted)

        was_error = drive.error
        cleared = drive.step(self.bit("axis.enable"), self.bit("axis.ack"), faulted,
                             self.num("axis.target"), self.num("axis.velocity"), dt)
        if drive.error and not was_error:
            self.errors.append({"at": round(self.t, 2), "why": drive.error_text,
                                "position_mm": round(drive.position, 1)})
        if cleared and self.fault is not None and self.fault["acknowledged_at"] is None \
                and self.fault["raised_at"] <= self.t:
            self.fault["acknowledged_at"] = round(self.t, 2)

        f = self.fault
        if f is not None and f["reset_at"] is None:
            # From the tick the fault was raised to the Reset press: record
            # where the quick stop ended and anything the carriage did after.
            if f["stopped_at_mm"] is None and abs(drive.velocity) < 1e-6:
                f["stopped_at_mm"] = round(drive.position, 2)
            elif f["stopped_at_mm"] is not None:
                f["moved_before_reset_mm"] = round(
                    max(f["moved_before_reset_mm"], abs(drive.position - f["stopped_at_mm"])), 2)

        moving = abs(drive.velocity) > 1e-6
        if self._moving and not moving:
            self.arrivals.append({"at": round(self.t, 2), "position_mm": round(drive.position, 2),
                                  "pot": self.panel.setpoint_value})
        self._moving = moving

        self.tags.set("axis.ready", drive.ready)
        self.tags.set("axis.error", drive.error)
        self.tags.set("axis.position", drive.position)
        self.tags.set("axis.inposition", drive.in_position)


def _at(position: float, station: float) -> bool:
    return abs(position - station) <= SV_WINDOW + 0.5


def grade_servo(watched: Watched, engine: GradedEngine, report: Report,
                duration: float) -> None:
    sim: ServoScene = watched.inner
    f = sim.fault
    first_pot = SV_POT_START
    arrivals = sim.arrivals
    at_a = [a for a in arrivals if _at(a["position_mm"], SV_STATION_A)]
    at_b = [a for a in arrivals if _at(a["position_mm"], a["pot"])]
    moved = sim.pot_moved_at
    at_old_b = [a for a in arrivals
                if moved is not None and a["at"] > moved + SV_POT_GRACE
                and _at(a["position_mm"], first_pot)]
    at_new_b = [a for a in arrivals if moved is not None and a["at"] > moved
                and _at(a["position_mm"], sim.pot_then)]
    reset_at = f["reset_at"] if f else None
    after_reset = [a for a in arrivals if reset_at is not None and a["at"] > reset_at
                   and (_at(a["position_mm"], SV_STATION_A) or _at(a["position_mm"], a["pot"]))]
    refused = [e for e in sim.errors if e["why"] != "drive fault"]

    report.evidence.update({
        "station_a_mm": SV_STATION_A,
        "pot_first_mm": first_pot,
        "pot_then_mm": sim.pot_then,
        "pot_moved_at": moved,
        "arrivals": arrivals,
        "errors": sim.errors,
        "fault": f,
    })

    report.add("axis.cycled",
               len(at_a) >= 2 and len(at_b) >= 2,
               f"the carriage came to rest at station A {len(at_a)} time(s) and at "
               f"station B {len(at_b)} time(s) (at least 2 of each)")
    report.add("axis.followed_the_pot",
               bool(at_new_b) and not at_old_b,
               f"after the pot moved to {sim.pot_then:g} mm the carriage stopped there "
               f"{len(at_new_b)} time(s)"
               + (f", and at the old {first_pot:g} mm {len(at_old_b)} time(s)" if at_old_b else ""))
    report.add("axis.no_refused_commands",
               not refused,
               "the drive never refused a command" if not refused else
               f"the drive refused {len(refused)} command(s) and stopped itself: "
               + "; ".join(f"{e['why']} at {e['at']:g}s" for e in refused[:3]))
    if f is None:
        report.add("error.held_until_reset", False,
                   "the carriage never moved after the fault was due, so the fault "
                   "was never raised")
    else:
        held = f["moved_before_reset_mm"] <= SV_HELD_WITHIN and (
            f["acknowledged_at"] is None or reset_at is None or f["acknowledged_at"] >= reset_at)
        report.add("error.held_until_reset",
                   held,
                   f"after the fault at {f['raised_at']:g}s the carriage stayed where it "
                   f"stopped until the Reset at {reset_at}s"
                   if held else
                   f"the fault cleared at {f['cleared_at']}s and the error was "
                   f"acknowledged at {f['acknowledged_at']}s, before anybody pressed "
                   f"Reset at {reset_at}s; the carriage moved "
                   f"{f['moved_before_reset_mm']:.0f} mm on its own")
        report.add("error.recovered",
                   f["acknowledged_at"] is not None and bool(after_reset),
                   f"acknowledged at {f['acknowledged_at']}s and "
                   f"{len(after_reset)} station(s) reached after the Reset"
                   if f["acknowledged_at"] is not None else
                   "the error was never acknowledged, so the axis never moved again")

    _servo_feedback(report, sim, at_a, at_b, at_old_b, refused)


def _servo_feedback(report, sim, at_a, at_b, at_old_b, refused) -> None:
    say = report.feedback.append
    f = sim.fault
    if not sim.arrivals:
        say("The carriage never moved. The drive does nothing until `axis.enable` "
            "has been on for 0.1 s and `axis.ready` answers -- and a move needs a "
            "`axis.velocity` above zero as well as a `axis.target`.")
        return
    if refused:
        say(f"The drive refused a command and stopped itself: {refused[0]['why']}. "
            f"A real drive does not guess what you meant. Keep `axis.target` inside "
            f"0..{SV_STROKE:g} mm and write a velocity with every move.")
    if f is not None and f["acknowledged_at"] is not None and f["reset_at"] is not None \
            and f["acknowledged_at"] < f["reset_at"]:
        say(f"The drive stopped itself at {f['raised_at']:g}s and the fault behind it "
            f"went away at {f['cleared_at']:g}s -- and {f['acknowledged_at'] - f['cleared_at']:.2f}s "
            f"later your program acknowledged it, and the carriage moved off with "
            f"nobody having asked. That is automatic restart. `axis.ack` is the "
            f"operator's decision, passed on: acknowledge on the Reset button, "
            f"once `axis.fault` has gone.")
    if f is not None and f["acknowledged_at"] is None:
        say("The drive stopped itself on a fault and stayed stopped: `axis.error` "
            "latches, and only a RISING edge of `axis.ack` clears it -- after the "
            "cause has gone. Holding `axis.ack` true is one edge, at the start, "
            "and none after. Cycling `axis.enable` does not clear it either.")
    if at_old_b:
        say(f"After the pot moved to {sim.pot_then:g} mm the carriage still went to "
            f"{SV_POT_START:g} mm. Station B is `panel.setpoint`: read it every "
            f"time you start a move to B.")
    if (not refused and at_a and at_b and not at_old_b and f is not None
            and f["acknowledged_at"] is not None and f["reset_at"] is not None
            and f["acknowledged_at"] >= f["reset_at"]):
        say(f"Clean: the drive stopped on the fault at {f['raised_at']:g}s, stayed "
            f"stopped until the Reset at {f['reset_at']:g}s, and carried on from there.")


def _summary_servo(evidence: dict, out) -> None:
    out(f"station A {evidence['station_a_mm']:g} mm; station B {evidence['pot_first_mm']:g} mm, "
        f"then {evidence['pot_then_mm']:g} mm from {evidence['pot_moved_at']}s")
    stops = ", ".join(f"{a['position_mm']:.0f}" for a in evidence["arrivals"][:14])
    out(f"came to rest at (mm): {stops}")
    f = evidence["fault"]
    if f:
        out(f"fault at {f['raised_at']:g}s, cleared {f['cleared_at']}s, Reset {f['reset_at']}s, "
            f"acknowledged {f['acknowledged_at']}s, moved {f['moved_before_reset_mm']:g} mm "
            f"before Reset")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Servo positioning",
    "task": ("Shuttle the axis between station A (100 mm) and station B (the "
             "pot), dwelling a second at each. The drive is faulted mid-move: "
             "hold the sequence, acknowledge only on the operator's Reset once "
             "the fault has gone, and carry on."),
    "build": ServoScene,
    "observe": None,
    "grade": grade_servo,
    "summary": _summary_servo,
    "duration": 40.0,
    "references": ("good", "autoack", "noack"),
    "tags": ("axis.enable, axis.ack, axis.target, axis.velocity, "
             "position_display.value, tower.green/yellow/red, panel.green, "
             "panel.red are yours to write; axis.ready, axis.error, "
             "axis.position, axis.inposition, axis.fault, panel.setpoint and "
             "the buttons are the plant's."),
}
