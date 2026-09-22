"""`light-curtain-sorting`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/light_curtain_sorting.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import Item, PlantScene, Script, shuffled_cycle


SCENE = "light-curtain-sorting"


# --- light curtain sorting ----------------------------------------------
#
# Observable fact: each carton's true height, and which lane it ended in. The
# scene made the carton, so it knows; nothing on the bus carries either.
#
# How a program fakes it: two ways, and both are shut.
#
# A program that never reads the curtain can sort an alternating feed by
# pushing every second carton, so the feed is drawn from a shuffled cycle of
# eight different heights rather than from two.
#
# A program with the threshold written into it sorts perfectly at one setting,
# which is the whole difference between this scene and `sorting-by-height` --
# there the rule is two bits of wiring, here it is a number on the pot. So the
# pot is set to one value, and then to another, and every carton is marked
# against the rule that was in force when the curtain measured *it*. A carton
# in flight when the knob turned is judged by the old rule, which is what a
# real line does and what makes the check fair.
#
# Geometry and the curtain from `engine/templates/light_curtain_sorting.json`:
# a 12-beam array over 0.48 m, a 0.55 m diverter stroke at 1.83 m/s.
LC_BELT_SPEED = 0.5
LC_CURTAIN_POS = 1.2
LC_DIVERTER_POS = 2.2
LC_REMOVER_POS = 3.0
LC_CATCH = 0.15
LC_TRAVEL_TIME = 0.55 / 1.83
LC_BEAMS = 12
LC_CURTAIN_HEIGHT = 0.48


def lc_beam_ladder() -> list[float]:
    """Where each beam sits above the belt.

    `LightArray.cs`: the lowest beam is 20 mm up so the shortest carton still
    breaks one, and the rest are spread to the top of the curtain. The array
    reports the height of the *highest blocked beam*, so a carton's measured
    height is a rung of this ladder and never its true height -- which is why
    the thresholds below sit between rungs and never on one.
    """
    return [0.02 + (LC_CURTAIN_HEIGHT - 0.02) * i / (LC_BEAMS - 1)
            for i in range(LC_BEAMS)]


class LightCurtainScene(PlantScene):
    name = "light-curtain-sorting"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("diverter.extend", "Diverter (Extend)", "bit", "output"),
            Tag("height_gauge.height", "Light Array Height (m)", "float", "input"),
            Tag("height_gauge.blocked", "Light Array Blocked", "bit", "input"),
            Tag("diverter.extended", "Diverter (Extended)", "bit", "input"),
            Tag("diverter.retracted", "Diverter (Retracted)", "bit", "input",
                value=True),
            Tag("tall_count.count", "Chute Remover (Count)", "int", "input"),
            Tag("short_count.count", "Far End Remover (Count)", "int", "input"),
        )

        ladder = lc_beam_ladder()
        #: Eight heights, each sitting just above a rung so the curtain reports
        #: that rung exactly. Shuffled in blocks, so no eight consecutive
        #: cartons are an order a program could have been written against.
        self._rungs = list(range(1, 9))
        self.feed = shuffled_cycle(self.rng, self._rungs, 6)
        self._fed = 0
        self.ladder = ladder

        # Two thresholds, each halfway between two rungs, so no carton is ever
        # a tie and a boundary case is never the reason for a mark.
        first, second = self.rng.sample([3, 4, 5, 6], 2)
        self.thresholds = [(ladder[first] + ladder[first - 1]) / 2,
                           (ladder[second] + ladder[second - 1]) / 2]
        self.script = Script([
            (0.2, self.panel.set_setpoint(round(self.thresholds[0], 4))),
            (1.0, self.panel.press("start")),
            (32.0, self.panel.set_setpoint(round(self.thresholds[1], 4))),
        ])

        self.items: list[Item] = []
        self.sorted_items: list[Item] = []
        self._next_id = 1
        self._emit_edge = False
        self.extension = 0.0

    # --- the plant ---

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            rung = self.feed[self._fed % len(self.feed)]
            self._fed += 1
            self.items.append(Item(height=self.ladder[rung] + 0.005,
                                   id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        if self.bit("belt.rotate"):
            for item in self.items:
                item.position += LC_BELT_SPEED * dt

        target = 1.0 if self.bit("diverter.extend") else 0.0
        rate = dt / LC_TRAVEL_TIME
        self.extension = min(self.extension + rate, target) if target > self.extension \
            else max(self.extension - rate, target)
        self.tags.set("diverter.extended", self.extension >= 0.999)
        self.tags.set("diverter.retracted", self.extension <= 0.001)

        # The curtain. Measured once, on the beam break, and stamped with the
        # rule that was in force at that moment.
        in_curtain = [i for i in self.items
                      if abs(i.position - LC_CURTAIN_POS) <= 0.10]
        if in_curtain:
            tallest = max(in_curtain, key=lambda i: i.height)
            rung = max(y for y in self.ladder if y <= tallest.height)
            self.tags.set("height_gauge.height", rung)
            self.tags.set("height_gauge.blocked", True)
            for item in in_curtain:
                if item.measured is None:
                    item.measured = max(y for y in self.ladder if y <= item.height)
                    item.threshold = float(self.panel.setpoint_value)
        else:
            self.tags.set("height_gauge.blocked", False)
            self.tags.set("height_gauge.height", 0.0)

        still: list[Item] = []
        for item in self.items:
            if (self.extension > 0.5
                    and abs(item.position - LC_DIVERTER_POS) <= LC_CATCH):
                item.lane = "chute"
                item.carried = True
                self.sorted_items.append(item)
            elif item.position >= LC_REMOVER_POS:
                item.lane = "far-end"
                self.sorted_items.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("tall_count.count",
                      sum(1 for i in self.sorted_items if i.lane == "chute"))
        self.tags.set("short_count.count",
                      sum(1 for i in self.sorted_items if i.lane == "far-end"))


def grade_light_curtain(watched: Watched, engine: GradedEngine, report: Report,
                        duration: float) -> None:
    sim: LightCurtainScene = watched.inner
    judged = [i for i in sim.sorted_items if i.measured is not None]
    unmeasured = [i for i in sim.sorted_items if i.measured is None]
    chute = [i for i in sim.sorted_items if i.lane == "chute"]
    far = [i for i in sim.sorted_items if i.lane == "far-end"]

    wrong = [i for i in judged
             if (i.measured >= i.threshold) != (i.lane == "chute")]
    rules = sorted({round(i.threshold, 4) for i in judged})
    per_rule = {f"{r:.3f}": sum(1 for i in judged if abs(i.threshold - r) < 1e-6)
                for r in rules}

    report.evidence.update({
        "fed": sim._fed,
        "sorted": len(sim.sorted_items),
        "still_on_belt": len(sim.items),
        "chute": len(chute),
        "far_end": len(far),
        "thresholds_seen": per_rule,
        "misrouted": [{"carton": i.id, "measured_m": round(i.measured, 3),
                       "threshold_m": round(i.threshold, 3), "lane": i.lane}
                      for i in wrong][:20],
        "unmeasured": len(unmeasured),
        "diverter_out_fraction": round(watched.held_true("diverter.extend"), 3),
    })

    report.add("line.ran",
               len(sim.sorted_items) >= 8,
               f"{len(sim.sorted_items)} cartons reached a lane (at least 8)")
    report.add("line.both_lanes",
               len(chute) >= 2 and len(far) >= 2,
               f"chute {len(chute)}, far end {len(far)} (at least 2 each)")
    report.add("rule.both_settings_tested",
               len(per_rule) >= 2 and min(per_rule.values()) >= 2,
               f"cartons measured under each threshold: "
               f"{', '.join(f'{k}m x{v}' for k, v in per_rule.items())}")
    report.add("sort.followed_the_measurement",
               not wrong,
               "every carton went to the lane its measured height asked for"
               if not wrong else
               f"{len(wrong)} carton(s) went the wrong way: "
               f"{[i.id for i in wrong][:10]}")
    report.add("line.conservation",
               sim._fed == len(sim.sorted_items) + len(sim.items),
               f"{sim._fed} fed = {len(sim.sorted_items)} sorted + "
               f"{len(sim.items)} still on the belt")

    _light_curtain_feedback(report, watched, sim, wrong, per_rule)


def _light_curtain_feedback(report, watched, sim, wrong, per_rule) -> None:
    say = report.feedback.append
    belt = watched.held_true("belt.rotate")
    out = watched.held_true("diverter.extend")

    if belt == 0.0:
        say("The belt never ran. `belt.rotate` is yours to write.")
        return
    if sim._fed == 0:
        say("No cartons were fed. `emitter.emit` makes one on each RISING edge.")
        return
    if out == 0.0:
        say("The diverter never came out, so everything went past. "
            "`height_gauge.height` is a measurement in metres and "
            "`panel.setpoint` is the threshold to compare it against.")
    elif out > 0.85:
        say("The diverter was held out for nearly the whole run, so it swept "
            "everything into the chute. It has to come back for the short ones.")

    if len(per_rule) < 2:
        say("The run turned the pot to a second threshold part way through and "
            "not enough cartons were measured afterwards to mark it. If the line "
            "stopped or the feed stopped, that is why.")

    if wrong:
        by_rule: dict[float, int] = {}
        for item in wrong:
            by_rule[round(item.threshold, 3)] = by_rule.get(round(item.threshold, 3), 0) + 1
        if len(by_rule) == 1 and len(per_rule) > 1:
            only = next(iter(by_rule))
            say(f"Every misrouted carton was measured while the pot read "
                f"{only:.3f} m, and the ones under the other setting were all "
                f"correct. That is a threshold written into the program: read "
                f"`panel.setpoint` at the moment you measure each carton, not "
                f"once at startup.")
        else:
            worst = wrong[0]
            say(f"Carton {worst.id} measured {worst.measured:.3f} m against a "
                f"threshold of {worst.threshold:.3f} m and went to the "
                f"{worst.lane}. The curtain reports the highest beam it lost, so "
                f"the measurement is a rung of a 12-beam ladder over "
                f"{LC_CURTAIN_HEIGHT:g} m -- compare that number, not the bit.")
    elif len(sim.sorted_items) >= 8:
        say(f"Sorting was clean at both thresholds: "
            f"{', '.join(f'{k} m for {v} cartons' for k, v in per_rule.items())}, "
            f"none misrouted.")


def _summary_light_curtain(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, sorted {evidence['sorted']}, "
        f"{evidence['still_on_belt']} still on the belt")
    out(f"chute {evidence['chute']}, far end {evidence['far_end']}")
    out("thresholds the run used: "
        + ", ".join(f"{k} m for {v} cartons"
                    for k, v in evidence["thresholds_seen"].items()))
    for entry in evidence["misrouted"][:8]:
        out(f"  carton {entry['carton']:>3} measured {entry['measured_m']:.3f} m "
            f"against {entry['threshold_m']:.3f} m -> {entry['lane']}")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Light curtain sorting",
    "task": ("Sort on a measurement rather than on two bits. The curtain "
             "reports how tall each carton is, in metres, and the pot is "
             "the threshold -- read it when you measure each carton, "
             "because this run turns it."),
    "build": LightCurtainScene,
    "observe": None,
    "grade": grade_light_curtain,
    "summary": _summary_light_curtain,
    "duration": 65.0,
    "references": ("good", "fixed", "everyother"),
    "tags": ("belt.rotate, emitter.emit, diverter.extend, panel.green, "
             "panel.red are yours to write; height_gauge.height, "
             "height_gauge.blocked, diverter.extended, diverter.retracted, "
             "tall_count.count, short_count.count, panel.setpoint and the "
             "buttons are the line's."),
}
