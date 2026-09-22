"""`tank-level-control`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/tank_level_control.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..plant import Script
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
# Numbers from `engine/src/Parts/LevelTank.cs` and the template that configures
# it: 18 %/s at a fully open fill valve, 22 %/s draining a full tank.
TANK_FILL_RATE = 18.0
TANK_DRAIN_RATE = 22.0


class TankScene(Regulator):
    name = "tank-level-control"
    measured = "the level"
    unit = "%"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("tank.fill", "Tank Fill Valve (%)", "float", "output"),
            Tag("tank.drain", "Tank Drain Valve (%)", "float", "output"),
            Tag("level_readout.value", "Level Readout", "int", "output"),
            Tag("tank.level", "Tank Level (%)", "float", "input"),
            Tag("tank.fault", "Tank Valve Fault", "bit", "input"),
        )
        self.level = 0.0

        high = self.rng.choice([65.0, 70.0, 75.0])
        low = self.rng.choice([18.0, 22.0, 26.0])
        self.script = Script([
            (0.2, self._phase(high, until=30.0)),
            (1.0, self.panel.press("start")),
            (30.0, self._phase(low, until=10_000.0)),
        ])

    def measure(self) -> float:
        return self.level

    def step(self, dt: float) -> None:
        fill = min(max(self.num("tank.fill"), 0.0), 100.0)
        drain = min(max(self.num("tank.drain"), 0.0), 100.0)
        inflow = TANK_FILL_RATE * fill / 100.0
        outflow = (TANK_DRAIN_RATE * drain / 100.0
                   * (max(self.level, 0.0) / 100.0) ** 0.5)
        self.level = min(max(self.level + (inflow - outflow) * dt, 0.0), 100.0)
        self.tags.set("tank.level", self.level)
        self.record()


def grade_tank(watched, engine, report, duration) -> None:
    # A few percent, which is what the scene's own brief asks for. Ripple is
    # tighter than settled error on purpose: a controller 3 % off is mistuned,
    # while one swinging 3 % peak to peak is cycling a valve that has to last.
    grade_regulator(watched, engine, report, duration,
                    settled=3.0, ripple=3.0, overshoot=6.0, moved=40.0)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Tank level control",
    "task": ("Hold the tank at the level the pot asks for, with the fill "
             "and drain valves. Outflow follows Torricelli, so the process "
             "gain falls with level: this run asks for a high level and "
             "then a low one."),
    "build": TankScene,
    "observe": None,
    "grade": grade_tank,
    "summary": _summary_regulator,
    "duration": 65.0,
    "references": ("good", "bangbang", "fixedsp"),
    "tags": ("tank.fill, tank.drain, level_readout.value, panel.green, "
             "panel.red are yours to write; tank.level, tank.fault, "
             "panel.setpoint and the buttons are the plant's."),
}
