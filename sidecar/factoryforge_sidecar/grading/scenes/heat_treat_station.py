"""`heat-treat-station`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/heat_treat_station.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..plant import Script
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
# Numbers from `engine/src/Parts/HeatingStation.cs` and the template: a 90
# degC/s element, a loss of 0.30 per degC above a 20 degC room, a thermal mass
# of 6. That is a first-order lag with a 20-second time constant.
OVEN_POWER = 90.0
OVEN_LOSS = 0.30
OVEN_MASS = 6.0
OVEN_AMBIENT = 20.0
#: The part's own at-temperature window, which is about its configured target
#: and not about the pot -- exactly as the engine has it.
OVEN_TARGET = 180.0
OVEN_TOLERANCE = 3.0


class OvenScene(Regulator):
    name = "heat-treat-station"
    measured = "the plate"
    unit = "C"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
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
        self.temperature = OVEN_AMBIENT
        self.tags.set("oven.temperature", self.temperature)

        first = self.rng.choice([115.0, 125.0, 135.0])
        second = self.rng.choice([190.0, 200.0, 210.0])
        self.script = Script([
            (0.2, self._phase(first, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(second, until=10_000.0)),
        ])

    def measure(self) -> float:
        return self.temperature

    def step(self, dt: float) -> None:
        power = min(max(self.num("oven.heater"), 0.0), 100.0)
        heat = OVEN_POWER * power / 100.0
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


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Heat treat station",
    "task": ("Hold the plate at the temperature on the pot. The plant is a "
             "first-order lag losing heat to the room, so holding a "
             "temperature needs a standing output -- and proportional "
             "action can only make one out of a standing error."),
    "build": OvenScene,
    "observe": None,
    "grade": grade_oven,
    "summary": _summary_regulator,
    "duration": 65.0,
    "references": ("good", "ponly", "thermostat"),
    "tags": ("oven.heater, temp_gauge.value, temp_readout.value, "
             "alarm.beacon, alarm.horn, panel.green, panel.red are yours to "
             "write; oven.temperature, oven.attemp, oven.fault, "
             "panel.setpoint and the buttons are the plant's."),
}
