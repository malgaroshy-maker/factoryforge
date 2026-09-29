"""`cooling-tunnel`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/cooling_tunnel.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..plant import (OperatorExam, Script, declare_stack_light,
                     operator_exam_ends_by, pot_start)
from ..templates import TemplateError, template
from ._contract import mark_contract, summary_contract
from ._regulator import Regulator, _summary_regulator, grade_regulator


SCENE = "cooling-tunnel"


# --- cooling tunnel -------------------------------------------------------
#
# Observable fact: the temperature trace, and what the two actuators were
# really doing at every tick of it -- the heater's power and the airflow the
# fan actually delivered, which lags its reference.
#
# The plant is the heat treat station's plate with a fan blowing on it: the
# fan adds to the same loss term the room does (`HeatingStation.cs`, `Step`
# :213, and `CoolingFan.cs`, `Step` :282), so it is one plant with two
# actuators pulling opposite ways. That is split-range control, and the recipe
# on the pot is what makes it necessary: the exam drops the setpoint by 60 C
# or more, and a plate that can only lose heat to the room takes 15 s and more
# to get there -- the fan does it in well under 10.
#
# How a program fakes it -- or rather, the two ways it gets split range wrong:
#
# * it never uses the fan. Every hold is fine, and the recipe change arrives
#   late, so `cool.arrived_in_time` marks the time to reach the new setpoint;
# * it runs both at once. A fan left blowing while a PI trims the heater holds
#   every setpoint beautifully -- by heating the air the fan is blowing away.
#   The plant integrates the overlap of heater power and delivered airflow,
#   and `split.no_fighting` marks it.
#
# The regulator checks (settled, steady, overshoot) are the heat treat
# station's, per phase, so a thermostat still fails here too.
#
# The numbers are the template's (IP-19), and so is the fan's reach: a fan
# that the template moves out of range of the plate cools nothing, in the
# engine and here.
_PLANT = template(SCENE)
_OVEN = _PLANT.part("oven", "HeatingStation").engineering_units()
_FAN = _PLANT.part("fan", "CoolingFan")

CT_POT_START = pot_start(_PLANT)
CT_POWER = _OVEN.number("heater_power")
CT_LOSS = _OVEN.number("loss_rate")
CT_MASS = _OVEN.number("thermal_mass")
CT_AMBIENT = _OVEN.number("ambient")
CT_TARGET = _OVEN.number("target_temp")
CT_TOLERANCE = _OVEN.number("tolerance")
CT_COOLING = _FAN.number("cooling_rate")
CT_SPIN_UP = _FAN.number("spin_up_rate")
CT_REACH = _FAN.number("reach")
#: `CoolingFan.FindStations`: a station counts if its origin is within
#: `reach` of the fan's, measured in 3-D between the two part origins.
CT_DISTANCE = math.dist(_FAN.position, _OVEN.position)
if CT_DISTANCE > CT_REACH:
    raise TemplateError(f"{_FAN.source}: the fan is {CT_DISTANCE:.2f} m from the plate "
                        f"and reaches {CT_REACH:g} m, so it cools nothing -- no split "
                        f"range to grade")
#: `HeatingStation.AddCooling` (:250) clamps the offered loss at this.
CT_COOLING_CEILING = 10.0
#: `CoolingFan.Step` (:298): below this airflow the fan offers nothing.
CT_AIRFLOW_FLOOR = 0.01

#: The recipe, from the seed: a hot hold, a drop of at least 60 C, and a
#: middle hold the heater has to take back over.
CT_FIRST = (150.0, 160.0, 170.0)
CT_LOW = (70.0, 80.0)
CT_MID = (110.0, 120.0, 130.0)
CT_DROP_AT = 30.0
CT_MID_AT = 55.0
#: How long after the drop the plate has to be within `CT_ARRIVED` of the new
#: setpoint. From the plant's own numbers: losing heat to the room alone takes
#: 14.5 s for the smallest drop (150 -> 80: e-folding 20 s, from 130 C above
#: ambient to 63 C); with the fan full on the time constant is 6.7 s and even
#: the biggest drop (170 -> 70) arrives in about 8.5 s, spin-up included.
CT_ARRIVE_WITHIN = 10.0
CT_ARRIVED = 3.0
#: Heater and fan together, as percent-seconds of min(heater %, airflow %) / 100:
#: one second of both flat out. A deadband crossing costs a fraction of that.
CT_FIGHT_LIMIT = 1.0

# --- the operator contract (IP-12) ------------------------------------------
#
# Once the middle hold has had its phase, the E-stop sheet
# (`plant.OperatorExam`). The plate is holding on a standing heater output, and
# "stopped" is the heater at zero and the fan not run: neither actuator driven
# while the mushroom is in or its trip is latched. A fan still spinning down on
# its own ramp is not driven -- `fan.airflow` lags the command, as the brief
# says. The holds, the arrival and the overlap are measured over the recipe
# alone, up to `CT_HOLDS_END`, exactly as they were before the sheet existed.

#: The middle hold ends here, where the window used to.
CT_HOLDS_END = 80.0
CT_ESTOP_AT = CT_HOLDS_END + 0.5
CT_EXAM_ENDS_BY = operator_exam_ends_by(CT_ESTOP_AT)


class CoolingTunnelScene(Regulator):
    name = "cooling-tunnel"
    measured = "the product"
    unit = "C"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=CT_POT_START)
        self._declare(
            Tag("fan.run", "Fan 0 Run", "bit", "output"),
            Tag("fan.speed", "Fan 0 Speed Ref (%)", "float", "output"),
            Tag("fan.airflow", "Fan 0 Airflow (%)", "float", "input"),
            Tag("fan.fault", "Fan 0 Motor Fault", "bit", "input"),
            Tag("oven.heater", "Heater 0 Power (%)", "float", "output"),
            Tag("oven.temperature", "Heater 0 Temperature (C)", "float", "input"),
            Tag("oven.attemp", "Heater 0 At Temperature", "bit", "input"),
            Tag("oven.fault", "Heater 0 Element Fault", "bit", "input"),
            Tag("temp_gauge.value", "Gauge 0 Value", "float", "output"),
        )
        declare_stack_light(self.tags)
        self.temperature = CT_AMBIENT
        self.airflow = 0.0
        self.tags.set("oven.temperature", self.temperature)
        #: Ground truth for `split.no_fighting`: the overlap of heater power
        #: and delivered airflow, integrated, and when it happened.
        self.fight = 0.0
        self.fight_seconds = 0.0
        self.heater_seconds = 0.0
        self.fan_seconds = 0.0
        #: When the plate first came within CT_ARRIVED of the dropped setpoint.
        self.arrived_at: float | None = None

        self.first = float(self.rng.choice(CT_FIRST))
        self.low = float(self.rng.choice(CT_LOW))
        self.mid = float(self.rng.choice(CT_MID))
        self.script = Script([
            (0.2, self._phase(self.first, until=CT_DROP_AT)),
            (1.0, self.panel.press("start")),
            (CT_DROP_AT, self._phase(self.low, until=CT_MID_AT)),
            (CT_MID_AT, self._phase(self.mid, until=CT_HOLDS_END)),
        ])
        self.operator = OperatorExam(self, CT_ESTOP_AT, noun="heater_and_fan",
                                     what="the heater and the fan")

    def measure(self) -> float:
        return self.temperature

    def step(self, dt: float) -> None:
        # The fan first, as the template places it first: it offers cooling
        # this tick and the station consumes it in its own step.
        run = self.bit("fan.run") and not self.bit("fan.fault")
        command = min(max(self.num("fan.speed"), 0.0), 100.0)
        target = command if run else 0.0
        self.airflow += max(min(target - self.airflow, CT_SPIN_UP * dt), -CT_SPIN_UP * dt)
        offered = (CT_COOLING * self.airflow / 100.0 if self.airflow > CT_AIRFLOW_FLOOR
                   else 0.0)
        offered = min(offered, CT_COOLING_CEILING)

        power = min(max(self.num("oven.heater"), 0.0), 100.0)
        applied = 0.0 if self.bit("oven.fault") else power
        heat = CT_POWER * applied / 100.0
        loss = (self.temperature - CT_AMBIENT) * (CT_LOSS + offered)
        self.temperature = max(self.temperature + (heat - loss) / max(CT_MASS, 0.01) * dt,
                               CT_AMBIENT)

        self.operator.driven = power > 0.0 or target > 0.0
        both = min(power, self.airflow)
        recipe = self.t <= CT_HOLDS_END
        if both > 1.0 and recipe:
            self.fight += both / 100.0 * dt
            self.fight_seconds += dt
        if power > 1.0 and recipe:
            self.heater_seconds += dt
        if self.airflow > 1.0 and recipe:
            self.fan_seconds += dt
        if (self.arrived_at is None and self.t >= CT_DROP_AT
                and self.temperature <= self.low + CT_ARRIVED):
            self.arrived_at = self.t

        self.tags.set("fan.airflow", self.airflow)
        self.tags.set("oven.temperature", self.temperature)
        self.tags.set("oven.attemp", abs(self.temperature - CT_TARGET) <= CT_TOLERANCE)
        self.record()


def grade_cooling_tunnel(watched, engine, report, duration) -> None:
    sim: CoolingTunnelScene = watched.inner
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=5.0, overshoot=12.0, moved=80.0)

    took = None if sim.arrived_at is None else sim.arrived_at - CT_DROP_AT
    report.evidence.update({
        "recipe": [sim.first, sim.low, sim.mid],
        "arrived_after_the_drop_s": None if took is None else round(took, 2),
        "arrive_within_s": CT_ARRIVE_WITHIN,
        "fight_percent_seconds": round(sim.fight * 100.0, 1),
        "fight_limit_percent_seconds": CT_FIGHT_LIMIT * 100.0,
        "both_on_s": round(sim.fight_seconds, 2),
        "heater_on_s": round(sim.heater_seconds, 2),
        "fan_on_s": round(sim.fan_seconds, 2),
    })
    report.add("cool.arrived_in_time",
               took is not None and took <= CT_ARRIVE_WITHIN,
               (f"after the recipe dropped from {sim.first:g} C to {sim.low:g} C the "
                f"product was within {CT_ARRIVED:g} C of it in {took:.1f}s "
                f"(at most {CT_ARRIVE_WITHIN:g}s)") if took is not None else
               f"the product never came within {CT_ARRIVED:g} C of {sim.low:g} C")
    report.add("split.no_fighting",
               sim.fight <= CT_FIGHT_LIMIT,
               f"heater and fan were on together for {sim.fight_seconds:.1f}s, "
               f"{sim.fight * 100:.0f} %-seconds of overlap (at most "
               f"{CT_FIGHT_LIMIT * 100:.0f})")

    say = report.feedback.append
    if sim.fan_seconds < 0.5 and (took is None or took > CT_ARRIVE_WITHIN):
        say(f"The fan never ran, so when the recipe dropped to {sim.low:g} C the "
            f"product could only lose heat to the room -- "
            + (f"{took:.1f}s" if took is not None else "longer than the run")
            + f" to get there. The heater can only push the temperature up; "
              f"coming down is the fan's job. Split the controller's output: "
              f"above zero it drives `oven.heater`, below zero `fan.speed`.")
    elif took is not None and took > CT_ARRIVE_WITHIN:
        say(f"The product took {took:.1f}s to come down to {sim.low:g} C. Run the "
            f"fan hard while the error is large and negative, and remember it "
            f"spins up at {CT_SPIN_UP:g} %/s: `fan.airflow` is what it is really "
            f"delivering.")
    if sim.fight > CT_FIGHT_LIMIT:
        say(f"The heater and the fan were on together for {sim.fight_seconds:.0f}s. "
            f"The temperature holds -- by heating the air the fan is blowing "
            f"away, which is a plant burning energy against itself. Split range "
            f"means one actuator at a time, with a deadband between them so the "
            f"handover does not chatter.")

    mark_contract(sim.operator, report, watched.sim_time)


def _summary_cooling_tunnel(evidence: dict, out) -> None:
    _summary_regulator(evidence, out)
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Cooling tunnel",
    "task": ("Hold the product at the temperature on the pot with two "
             "actuators pulling opposite ways: a heater and a fan. The recipe "
             "drops sharply and then climbs again. Never run both at once. "
             "The mushroom is normally closed, stops both within 200 ms and "
             "latches -- only Reset, then Start, brings them back."),
    "build": CoolingTunnelScene,
    "observe": None,
    "grade": grade_cooling_tunnel,
    "summary": _summary_cooling_tunnel,
    "duration": CT_EXAM_ENDS_BY,
    "references": ("good", "heatonly", "fight", "noestop", "startalone"),
    "tags": ("oven.heater, fan.run, fan.speed, temp_gauge.value, "
             "tower.green/yellow/red, panel.green, panel.red are yours to "
             "write; oven.temperature, oven.attemp, oven.fault, fan.airflow, "
             "fan.fault, panel.setpoint and the buttons are the plant's."),
}
