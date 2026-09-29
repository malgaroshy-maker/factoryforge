"""`tank-level-control`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/tank_level_control.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..plant import (OperatorExam, Script, declare_stack_light,
                     operator_exam_ends_by, pot_start)
from ..templates import template
from ._contract import mark_contract, summary_contract
from ._regulator import Regulator, _summary_regulator, grade_regulator


SCENE = "tank-level-control"


# --- tank level control -------------------------------------------------
#
# Observable fact: the level trace, which the plant integrates and no bus
# message reaches.
#
# How a program fakes it: "the level reached the setpoint" is true of a pair of
# float switches, of a valve slammed fully open until the number arrives, and
# of a program with 70 written into it. All three are graded out -- by the
# ripple, by the overshoot, and by the pot moving to a second level drawn from
# the seed.
#
# Outflow follows Torricelli, so the drain valve's authority grows with the
# square root of the head and a controller tuned at the top of the tank behaves
# differently at the bottom. That is why the second setpoint is a low one.
#
# The equation is `engine/src/Parts/LevelTank.cs`'s; the rates are the
# template's, read from it (IP-19): percent per second at a fully open fill
# valve, and draining a full tank.
_PLANT = template(SCENE)
_TANK = _PLANT.part("tank", "LevelTank").engineering_units()
#: Where the pot sits before the exam turns it (IP-29).
TANK_POT_START = pot_start(_PLANT)
TANK_FILL_RATE = _TANK.number("fill_rate")
TANK_DRAIN_RATE = _TANK.number("drain_rate")

# --- the operator contract (IP-12) ------------------------------------------
#
# Once the second level has had its whole phase, the examiner turns the pot to
# a level well above it, so the fill valve is open, and puts the E-stop sheet
# (`plant.OperatorExam`). "Stopped" is both valves shut: nothing flows in or
# out while the mushroom is in or its trip is latched. The two holds are marked
# on the trace they always were; what follows them is no hold's.

#: The second hold ends here, where the window used to.
TANK_HOLDS_END = 65.0
#: Where the examiner turns the pot for the E-stop test, and when it reaches
#: for the mushroom.
TANK_ESTOP_POT = 50.0
TANK_ESTOP_AT = TANK_HOLDS_END + 1.0
TANK_EXAM_ENDS_BY = operator_exam_ends_by(TANK_ESTOP_AT)


class TankScene(Regulator):
    name = "tank-level-control"
    measured = "the level"
    unit = "%"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=TANK_POT_START)
        self._declare(
            Tag("tank.fill", "Tank Fill Valve (%)", "float", "output"),
            Tag("tank.drain", "Tank Drain Valve (%)", "float", "output"),
            Tag("level_readout.value", "Level Readout", "int", "output"),
            Tag("tank.level", "Tank Level (%)", "float", "input"),
            Tag("tank.fault", "Tank Valve Fault", "bit", "input"),
        )
        declare_stack_light(self.tags)
        self.level = 0.0

        high = self.rng.choice([65.0, 70.0, 75.0])
        low = self.rng.choice([18.0, 22.0, 26.0])
        self.script = Script([
            (0.2, self._phase(high, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(low, until=TANK_HOLDS_END)),
            (TANK_HOLDS_END, self.panel.set_setpoint(TANK_ESTOP_POT)),
        ])
        self.operator = OperatorExam(self, TANK_ESTOP_AT, noun="flow",
                                     what="the flow through the valves")

    def measure(self) -> float:
        return self.level

    def step(self, dt: float) -> None:
        fill = min(max(self.num("tank.fill"), 0.0), 100.0)
        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        inflow = TANK_FILL_RATE * fill / 100.0
        outflow = (TANK_DRAIN_RATE * drain / 100.0
                   * (max(self.level, 0.0) / 100.0) ** 0.5)
        self.level = min(max(self.level + (inflow - outflow) * dt, 0.0), 100.0)
        self.operator.driven = fill > 0.0 or drain > 0.0
        self.tags.set("tank.level", self.level)
        self.record()


def grade_tank(watched, engine, report, duration) -> None:
    # A few percent, which is what the scene's own brief asks for. Ripple is
    # tighter than settled error on purpose: a controller 3 % off is mistuned,
    # while one swinging 3 % peak to peak is cycling a valve that has to last.
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=3.0, overshoot=6.0, moved=40.0)
    mark_contract(watched.inner.operator, report, watched.sim_time)


def _summary_tank(evidence: dict, out) -> None:
    _summary_regulator(evidence, out)
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Tank level control",
    "task": ("Hold the tank at the level the pot asks for, with the fill "
             "and drain valves. Outflow follows Torricelli, so the process "
             "gain falls with level: this run asks for a high level and "
             "then a low one. Then the E-stop: the mushroom is normally "
             "closed, shuts both valves within 200 ms and latches -- only "
             "Reset, then Start, opens them again."),
    "build": TankScene,
    "observe": None,
    "grade": grade_tank,
    "summary": _summary_tank,
    "duration": TANK_EXAM_ENDS_BY,
    "references": ("good", "bangbang", "fixedsp", "noestop", "startalone"),
    "tags": ("tank.fill, tank.drain, level_readout.value, panel.green, "
             "panel.red are yours to write; tank.level, tank.fault, "
             "panel.setpoint and the buttons are the plant's."),
}
