"""`heat-treat-station`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/heat_treat_station.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..plant import (OperatorExam, Script, declare_stack_light,
                     operator_exam_ends_by, pot_start)
from ..templates import template
from ._contract import mark_contract, summary_contract
from ._regulator import Regulator, _summary_regulator, grade_regulator


SCENE = "heat-treat-station"


# --- heat treat station -------------------------------------------------
#
# Observable fact: the temperature trace.
#
# How a program fakes it: the same three ways as the tank, plus one this plant
# alone can catch. The plate loses heat to the room in proportion to how far
# above it the plate is, so *holding* a temperature needs a standing heater
# output -- and a proportional controller can only make a standing output out
# of a standing error. Grade "did it reach the setpoint" and P-only passes on
# the way past. Grade the settled error and it parks, measurably, exactly the
# offset the plant's own numbers predict: at 120 degC the plate loses
# (120-20)*0.30 = 30 degC/s of heat, which is 33 % of a 90 degC/s element, and
# a gain of 3.5 can only produce 33 % from an error of 9.5 degC.
#
# The equation is `engine/src/Parts/HeatingStation.cs`'s; the numbers are the
# template's, read from it (IP-19). As shipped: a 90 degC/s element, a loss of
# 0.30 per degC above a 20 degC room, a thermal mass of 6 -- a first-order lag
# with a 20-second time constant. The worked numbers in the comment above and
# in `grade_oven` are for those values.
_PLANT = template(SCENE)
_OVEN = _PLANT.part("oven", "HeatingStation").engineering_units()
#: Where the pot sits before the exam turns it (IP-29).
OVEN_POT_START = pot_start(_PLANT)
OVEN_POWER = _OVEN.number("heater_power")
OVEN_LOSS = _OVEN.number("loss_rate")
OVEN_MASS = _OVEN.number("thermal_mass")
OVEN_AMBIENT = _OVEN.number("ambient")
#: The part's own at-temperature window, which is about its configured target
#: and not about the pot -- exactly as the engine has it.
OVEN_TARGET = _OVEN.number("target_temp")
OVEN_TOLERANCE = _OVEN.number("tolerance")


# --- the element failure (IP-12) ------------------------------------------
#
# The brief ends on it: fail the element and `oven.heater` keeps reading what
# was commanded while the temperature falls -- "the output tells you nothing,
# only the measurement does". The least a program has to do with that is say
# so: light `alarm.beacon` within a few seconds of the element failing, and
# keep it lit, since nothing on the plate is going to clear it. And not light
# it while the element is healthy, or a beacon wired on would pass.
#
# The examiner fails the element once the second setpoint has had its whole
# phase to settle, and the E-stop test after it (below) has run -- the phase
# ends at 65 s, so the settling checks read exactly the trace they read before
# either existed -- and the window runs on long enough to see the alarm come
# and stay. The plant obeys the failure as `HeatingStation.cs` does: no heat,
# whatever the command.
#
# The beacon is an output, not a plant fact, and there is no plant fact to
# read instead: a dead element heats nothing whatever it is told. It is marked
# the way the air receiver marks its seized valve (docs/GRADING.md).

#: The second phase ends here, at the end of the old 65-second window, so it
#: is as long as it always was.
OVEN_HOLDS_END = 65.0


# --- the operator contract (IP-12) ------------------------------------------
#
# Between the second hold and the element failure, the E-stop sheet
# (`plant.OperatorExam`): the plate is holding its second setpoint on a
# standing heater output, and "stopped" is that output at zero -- no power to
# the element while the mushroom is in or its trip is latched. It comes before
# the failure and not after, because a Reset is part of the sheet, and Reset
# is what clears a latched alarm.

OVEN_ESTOP_AT = OVEN_HOLDS_END + 0.5
OVEN_ESTOP_ENDS_BY = operator_exam_ends_by(OVEN_ESTOP_AT)

#: When the element fails: once the E-stop test is over and the plate has had
#: a few seconds back on its loop. It failed at 65 s until the E-stop test
#: took the time in front of it.
OVEN_FAULT_AT = 80.0
#: How long after the failure the beacon has to be lit. The plate at 200 C
#: loses 9 C a second with no element; a loop saturates and sees the
#: temperature falling under full output inside three.
OVEN_ALARM_WITHIN = 5.0
#: How long the window watches after that, for the beacon staying lit.
OVEN_ALARM_HELD_FOR = 5.0
OVEN_EXAM_ENDS_BY = OVEN_FAULT_AT + OVEN_ALARM_WITHIN + OVEN_ALARM_HELD_FOR


class OvenScene(Regulator):
    name = "heat-treat-station"
    measured = "the plate"
    unit = "C"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=OVEN_POT_START)
        self._declare(
            Tag("oven.heater", "Heating Station Heater (%)", "float", "output"),
            Tag("temp_gauge.value", "Temperature Gauge", "float", "output"),
            Tag("temp_readout.value", "Temperature Readout", "int", "output"),
            Tag("alarm.beacon", "Alarm Beacon", "bit", "output"),
            Tag("alarm.horn", "Alarm Horn", "bit", "output"),
            Tag("oven.temperature", "Heating Station Temperature (C)",
                "float", "input"),
            Tag("oven.attemp", "Heating Station At Temperature", "bit", "input"),
            Tag("oven.fault", "Heating Station Element Fault", "bit", "input"),
        )
        declare_stack_light(self.tags)
        self.temperature = OVEN_AMBIENT
        self.tags.set("oven.temperature", self.temperature)

        first = self.rng.choice([115.0, 125.0, 135.0])
        second = self.rng.choice([190.0, 200.0, 210.0])
        #: The element failure's ground truth, and the beacon against it.
        self.element_failed = False
        self.fault: dict = {"failed_at": None, "false_alarm_at": None,
                            "alarmed_at": None, "alarm_dropped_at": None,
                            "temperature_at_failure": None}
        self.script = Script([
            (0.2, self._phase(first, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(second, until=OVEN_HOLDS_END)),
            (OVEN_FAULT_AT, self._fail_the_element),
        ])
        self.operator = OperatorExam(self, OVEN_ESTOP_AT, noun="heater",
                                     what="the heater")

    def _fail_the_element(self) -> None:
        """`HeatingStation.cs`: a faulted station makes no heat, and its
        fault contact reads true -- written by the plant, not forced."""
        self.element_failed = True
        self.tags.set("oven.fault", True)
        self.fault["failed_at"] = round(self.t, 2)
        self.fault["temperature_at_failure"] = round(self.temperature, 2)

    def measure(self) -> float:
        return self.temperature

    def _watch_the_beacon(self) -> None:
        beacon = self.bit("alarm.beacon")
        fault = self.fault
        if not self.element_failed:
            if beacon and fault["false_alarm_at"] is None:
                fault["false_alarm_at"] = round(self.t, 2)
        elif beacon and fault["alarmed_at"] is None:
            fault["alarmed_at"] = round(self.t, 2)
        elif not beacon and fault["alarmed_at"] is not None \
                and fault["alarm_dropped_at"] is None:
            fault["alarm_dropped_at"] = round(self.t, 2)

    def step(self, dt: float) -> None:
        self._watch_the_beacon()
        power = min(max(self.num("oven.heater"), 0.0), 100.0)
        heat = 0.0 if self.element_failed else OVEN_POWER * power / 100.0
        self.operator.driven = power > 0.0
        loss = (self.temperature - OVEN_AMBIENT) * OVEN_LOSS
        self.temperature = max(self.temperature + (heat - loss) / OVEN_MASS * dt,
                               OVEN_AMBIENT)
        self.tags.set("oven.temperature", self.temperature)
        self.tags.set("oven.attemp",
                      abs(self.temperature - OVEN_TARGET) <= OVEN_TOLERANCE)
        self.record()


def grade_oven(watched, engine, report, duration) -> None:
    # 3 degC settled, against a P-only offset of 9.5 degC at the first setpoint
    # and 16 degC at the second: the margin is wide enough that a well-tuned
    # proportional-only loop still fails, which is the point of the scene.
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=5.0, overshoot=12.0, moved=80.0)
    _grade_element_fault(watched.inner, report, watched.sim_time)
    mark_contract(watched.inner.operator, report, watched.sim_time)


def _grade_element_fault(sim: OvenScene, report, window: float) -> None:
    """The element failure (IP-12): the beacon lit within OVEN_ALARM_WITHIN
    of it and still lit when the window ends, and never lit before it."""
    fault = sim.fault
    failed = fault["failed_at"]
    report.evidence["element_fault"] = {
        **fault, "alarm_within_s": OVEN_ALARM_WITHIN,
        "alarmed_after_s": (None if failed is None or fault["alarmed_at"] is None
                            else round(fault["alarmed_at"] - failed, 2)),
    }
    short = (f"the {window:g}s window ended before the examiner finished the "
             f"element-failure test, which needs {OVEN_EXAM_ENDS_BY:g}s")
    say = report.feedback.append

    false_alarm = fault["false_alarm_at"]
    report.add("fault.no_false_alarm",
               false_alarm is None,
               "alarm.beacon stayed dark while the element was healthy"
               if false_alarm is None else
               f"alarm.beacon lit at {false_alarm:g}s, with the element healthy")
    if false_alarm is not None:
        say(f"`alarm.beacon` lit at {false_alarm:g}s, while the element was "
            f"still working. An alarm that is on when nothing is wrong teaches "
            f"the operator to ignore it -- light it on the failure, not before.")

    if failed is None or window < OVEN_EXAM_ENDS_BY - 1e-6:
        report.add("fault.alarmed", False, short)
        return
    alarmed, dropped = fault["alarmed_at"], fault["alarm_dropped_at"]
    lag = None if alarmed is None else alarmed - failed
    ok = lag is not None and lag <= OVEN_ALARM_WITHIN + 1e-9 and dropped is None
    if alarmed is None:
        detail = (f"alarm.beacon never lit after the element failed at "
                  f"{failed:g}s (within {OVEN_ALARM_WITHIN:g}s)")
    elif lag > OVEN_ALARM_WITHIN + 1e-9:
        detail = (f"alarm.beacon lit {lag:.2f}s after the element failed at "
                  f"{failed:g}s (within {OVEN_ALARM_WITHIN:g}s)")
    elif dropped is not None:
        detail = (f"alarm.beacon lit {lag:.2f}s after the element failed at "
                  f"{failed:g}s and went out again at {dropped:g}s, with the "
                  f"element still dead")
    else:
        detail = (f"alarm.beacon lit {lag:.2f}s after the element failed at "
                  f"{failed:g}s (within {OVEN_ALARM_WITHIN:g}s) and stayed lit")
    report.add("fault.alarmed", ok, detail)
    if alarmed is None or lag > OVEN_ALARM_WITHIN + 1e-9:
        say(f"The element failed at {failed:g}s, with the plate at "
            f"{fault['temperature_at_failure']:g} C, and "
            + ("nothing said so. " if alarmed is None else
               f"`alarm.beacon` took {lag:.1f}s to say so. ")
            + "`oven.heater` goes on reading what you commanded -- it is your "
              "own output -- so it cannot tell you. The measurement can: full "
              "heater output and a temperature that is falling is an element "
              "that is not heating. Light `alarm.beacon` on that.")
    elif dropped is not None:
        say(f"`alarm.beacon` went out at {dropped:g}s while the element was "
            f"still dead -- once the plate has cooled, a falling temperature "
            f"stops falling. Latch the alarm, and clear it on Reset.")


def _summary_oven(evidence: dict, out) -> None:
    _summary_regulator(evidence, out)
    fault = evidence.get("element_fault", {})
    if fault.get("failed_at") is not None:
        after = fault["alarmed_after_s"]
        out(f"element failed at {fault['failed_at']:g}s: beacon "
            + ("never lit" if after is None else f"lit {after:.2f}s later")
            + ("" if fault["alarm_dropped_at"] is None
               else f", out again at {fault['alarm_dropped_at']:g}s"))
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Heat treat station",
    "task": ("Hold the plate at the temperature on the pot. The plant is a "
             "first-order lag losing heat to the room, so holding a "
             "temperature needs a standing output -- and proportional "
             "action can only make one out of a standing error. The "
             "mushroom is normally closed, cuts the heater within 200 ms and "
             "latches -- only Reset, then Start, brings it back. Then the "
             "element fails: light alarm.beacon within five seconds and "
             "keep it lit -- and never while the element is healthy."),
    "build": OvenScene,
    "observe": None,
    "grade": grade_oven,
    "summary": _summary_oven,
    "duration": OVEN_EXAM_ENDS_BY,
    "references": ("good", "ponly", "thermostat", "ignorefault", "noestop",
                   "startalone"),
    "tags": ("oven.heater, temp_gauge.value, temp_readout.value, "
             "alarm.beacon, alarm.horn, panel.green, panel.red are yours to "
             "write; oven.temperature, oven.attemp, oven.fault, "
             "panel.setpoint and the buttons are the plant's."),
}
