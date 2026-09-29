"""`roller-line-weighing`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/roller_line_weighing.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (CARTON_LENGTH, SHORT_HEIGHT, TALL_HEIGHT, Item, OperatorExam,
                     PlantScene, Script, fault_input, operator_exam_ends_by, pot_start,
                     remover_catch, shuffled_cycle)
from ..templates import template
from ._contract import mark_contract, summary_contract


SCENE = "roller-line-weighing"


# --- roller line with weighing ------------------------------------------
#
# Observable fact: what each carton actually weighs, and whether it ever shared
# the deck with another one. The scene made them, so it knows both.
#
# How a program fakes it: by rejecting on the inductive sensor instead of the
# scale. The brief says the steel cartons are also the heavy ones, so metal and
# over-limit agree -- at one limit. They stop agreeing the moment the limit
# drops below a tall cardboard carton, which weighs 2160 g where a short steel
# one weighs 4320, and the exam moves it there. A program that flags metal
# passes the first half of the run and misses every tall carton in the second.
#
# The other half is the deck itself. Two cartons on a checkweigher read as one
# peak, and that is a real property of real checkweighers rather than a quirk
# here: the plant records whether each carton was ever weighed alongside
# another, and a line that feeds without holding fails on that regardless of
# what it did with the number.
#
# Masses from `BoxPhysics.cs`: 0.20 x H x 0.24 at 150 kg/m3 for cardboard and
# 900 for steel, so the four classes are 720 g, 2160 g, 4320 g and 12960 g.
#
# The line is read from the template (IP-19): two decks, each with its own
# speed, the scale's deck the second of them. Positions are world X; a carton
# starts at the emitter's.
#
# What is NOT the engine's here, on purpose: the feed, for the reason given
# above `self.feed` below. The template's emitter makes every third carton
# steel and alternates the heights.
_PLANT = template(SCENE)
_INFEED = _PLANT.part("infeed", "RollerConveyor")
_SCALE = _PLANT.part("scale", "WeighingConveyor").engineering_units()

RW_INFEED_SPEED = _INFEED.number("speed")
RW_SCALE_SPEED = _SCALE.number("speed")
RW_START_POS = _PLANT.part("emitter", "Emitter").x
#: The inductive eye. Until IP-29 this model had it at 1.2, 0.3 m upstream of
#: where the template puts it.
RW_METAL_EYE_POS = _PLANT.part("metal_check", "InductiveSensor").x
RW_DECK_FROM, RW_DECK_TO = _SCALE.span()
#: The load cell. `WeighingConveyor.cs` weighs every carton whose collider
#: overlaps an area `RW_SCALE_AREA` of the deck long, centred on it -- so a
#: carton weighs while its centre is within that half-length plus half a
#: carton of the deck's centre. On the template's 1 m deck that happens to be
#: the deck's own span, 2.0 to 3.0, which is what this model used before
#: anybody checked.
RW_SCALE_AREA = 0.8
_WEIGH_REACH = RW_SCALE_AREA * _SCALE.number("size_x") / 2 + CARTON_LENGTH / 2
RW_WEIGH_FROM, RW_WEIGH_TO = _SCALE.x - _WEIGH_REACH, _SCALE.x + _WEIGH_REACH
#: Where the `outfeed` remover takes a carton (`plant.remover_catch`): as its
#: nose crosses x = 3.0, still on the scale's deck. Until IP-29 this model
#: retired one at 3.3, past the end of the line.
RW_REMOVER_POS = remover_catch(_PLANT.part("outfeed", "Remover"), RW_DECK_TO)
RW_POT_START = pot_start(_PLANT)
#: How long after a carton rolls off the deck the controller has to have made
#: its mind up. Generous: a scan plus a network round trip.
RW_VERDICT_WINDOW = 0.6

# --- the operator contract (IP-12) ------------------------------------------
#
# After the checkweighing exam, the E-stop sheet (`plant.OperatorExam`).
# "Stopped" is both decks: the roller infeed and the scale's own belt. A carton
# stopped on the scale is still weighed on its peak when it rolls off, so no
# carton is the E-stop's to misjudge, and the examiner strikes whenever the
# line is running.

#: Where the window used to end.
RW_TESTS_END = 70.0
RW_ESTOP_AT = RW_TESTS_END + 0.5
RW_EXAM_ENDS_BY = operator_exam_ends_by(RW_ESTOP_AT)


class RollerWeighScene(PlantScene):
    name = "roller-line-weighing"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=RW_POT_START)
        self._declare(
            Tag("infeed.rotate", "Roller Conveyor (Rotate)", "bit", "output"),
            Tag("scale.rotate", "Weighing Conveyor (Rotate)", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("weight_readout.value", "Weight Readout", "int", "output"),
            Tag("scale.weight", "Weighing Conveyor Weight (g)", "int", "input"),
            Tag("metal_check.detect", "Inductive Sensor (Detect)", "bit", "input"),
            Tag("outfeed.count", "Remover (Count)", "int", "input"),
            fault_input("infeed", "Roller Conveyor Drive Fault"),
            fault_input("scale", "Weighing Conveyor Drive Fault"),
        )

        #: All four mass classes, shuffled in blocks of four rather than
        #: emitted on the template's `metal_every: 3` cadence. Two reasons, and
        #: the first is the same one the sorting line's feed has: an order a
        #: program can guess is an order it can be written against. The second
        #: is fairness the other way. The carton the two instruments disagree
        #: about is the tall cardboard one -- 2160 g, over a 1500 g limit and
        #: invisible to an inductive sensor -- and a block of four guarantees
        #: one, so `metalonly` cannot pass on a lucky draw.
        self.feed = shuffled_cycle(
            self.rng, [(True, False), (False, False), (True, True), (False, True)], 8)
        self._fed = 0

        # 3000 g is above every cardboard carton and below every steel one, so
        # metal and over-limit agree. 1500 g is below the tall cardboard one,
        # so they stop agreeing. That is the whole exam.
        self.limits = [3000.0, 1500.0]
        self.script = Script([
            (0.2, self.panel.set_setpoint(self.limits[0])),
            (1.0, self.panel.press("start")),
            (30.0, self.panel.set_setpoint(self.limits[1])),
        ])
        self.operator = OperatorExam(self, RW_ESTOP_AT, noun="belts",
                                     what="the line")

        self.items: list[Item] = []
        self.weighed: list[dict] = []
        self._watching: list[dict] = []
        self._next_id = 1
        self._emit_edge = False
        self._on_deck: dict[int, dict] = {}

    def step(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            tall, metal = self.feed[self._fed % len(self.feed)]
            self.items.append(Item(height=TALL_HEIGHT if tall else SHORT_HEIGHT,
                                   metal=metal, position=RW_START_POS,
                                   id=self._next_id))
            self._fed += 1
            self._next_id += 1
        self._emit_edge = emit

        infeed = self.bit("infeed.rotate")
        deck = self.bit("scale.rotate")
        self.operator.driven = infeed or deck
        for item in self.items:
            on_scale = RW_DECK_FROM <= item.position < RW_DECK_TO
            running = deck if on_scale else infeed
            if running:
                item.position += (RW_SCALE_SPEED if on_scale else RW_INFEED_SPEED) * dt

        on_deck = [i for i in self.items
                   if RW_WEIGH_FROM <= i.position <= RW_WEIGH_TO]
        self.tags.set("scale.weight", int(round(sum(i.grams for i in on_deck))))
        self.tags.set("metal_check.detect",
                      any(i.metal for i in self.items
                          if abs(i.position - RW_METAL_EYE_POS) <= CARTON_LENGTH / 2))

        # A carton's record opens when it reaches the deck and closes when it
        # leaves. `shared` is the plant's own answer to "was this weighed on
        # its own", which no instrument on the line reports.
        for item in on_deck:
            record = self._on_deck.get(item.id)
            if record is None:
                record = self._on_deck[item.id] = {
                    "carton": item.id, "grams": round(item.grams),
                    "metal": item.metal, "shared": False, "limit": None,
                    "flagged": False, "at": round(self.t, 2)}
            record["shared"] = record["shared"] or len(on_deck) > 1

        for item_id, record in list(self._on_deck.items()):
            if any(i.id == item_id for i in on_deck):
                continue
            del self._on_deck[item_id]
            record["limit"] = float(self.panel.setpoint_value)
            record["until"] = self.t + RW_VERDICT_WINDOW
            self.weighed.append(record)
            self._watching.append(record)

        # The controller's verdict: the red lamp, at any point in the window
        # after the carton rolls off. Held permanently it flags everything and
        # fails on the light ones; never lit it flags nothing and fails on the
        # heavy ones, so the rule is symmetric and neither shortcut survives.
        red = self.bit("panel.red")
        for record in list(self._watching):
            if red:
                record["flagged"] = True
            if self.t > record["until"]:
                self._watching.remove(record)

        still = []
        for item in self.items:
            if item.position >= RW_REMOVER_POS:
                item.lane = "outfeed"
            else:
                still.append(item)
        removed = len(self.items) - len(still)
        if removed:
            self.tags.set("outfeed.count",
                          int(self.tags.visible("outfeed.count")) + removed)
        self.items = still


def grade_roller_weighing(watched: Watched, engine: GradedEngine, report: Report,
                          duration: float) -> None:
    sim: RollerWeighScene = watched.inner
    judged = [r for r in sim.weighed if r["limit"] is not None]
    shared = [r for r in judged if r["shared"]]
    alone = [r for r in judged if not r["shared"]]
    # Judged against what the carton really weighs, and every carton, including
    # the ones that shared the deck. Restricting this to cartons weighed alone
    # made it vacuous for exactly the runs it most needed to catch: a line that
    # never singulates has no cartons weighed alone, so "every carton was
    # judged correctly" passed while nothing had been judged at all.
    wrong = [r for r in judged if (r["grams"] > r["limit"]) != r["flagged"]]
    limits = sorted({r["limit"] for r in judged})
    per_limit = {f"{int(l)}g": sum(1 for r in judged if r["limit"] == l)
                 for l in limits}
    # The cartons the two instruments disagree about: heavy cardboard. They are
    # the ones a metal-sensing program gets wrong, so they are worth naming.
    split = [r for r in judged if (r["grams"] > r["limit"]) != r["metal"]]

    report.evidence.update({
        "fed": sim._fed,
        "weighed": len(judged),
        "weighed_alone": len(alone),
        "shared_the_deck": len(shared),
        "limits_seen": per_limit,
        "misjudged": [{"carton": r["carton"], "grams": r["grams"],
                       "limit": int(r["limit"]), "flagged": r["flagged"],
                       "metal": r["metal"]} for r in wrong][:20],
        "metal_and_weight_disagree": len(split),
        "outfeed": int(sim.tags.visible("outfeed.count")),
        "red_held_fraction": round(watched.held_true("panel.red"), 3),
    })

    report.add("line.ran",
               len(judged) >= 8 and int(sim.tags.visible("outfeed.count")) >= 6,
               f"{len(judged)} cartons crossed the scale and "
               f"{int(sim.tags.visible('outfeed.count'))} reached the outfeed "
               f"(at least 8 and 6)")
    report.add("scale.singulated",
               not shared,
               "every carton was weighed on its own" if not shared else
               f"{len(shared)} carton(s) shared the deck with another, so the "
               f"scale read the pair as one peak: "
               f"{[r['carton'] for r in shared][:10]}")
    report.add("reject.judged_them_all",
               len(judged) >= 8,
               f"{len(judged)} cartons got a verdict (at least 8, so the check "
               f"below is about the judging and not about the sample size)")
    report.add("limit.both_settings_tested",
               len(per_limit) >= 2 and min(per_limit.values()) >= 2,
               f"cartons weighed under each limit: "
               f"{', '.join(f'{k} x{v}' for k, v in per_limit.items())}")
    report.add("reject.matched_the_weight",
               not wrong,
               "every carton over the limit was flagged and no other was"
               if not wrong else
               f"{len(wrong)} carton(s) judged wrong: "
               f"{[r['carton'] for r in wrong][:10]}")

    _roller_feedback(report, watched, sim, judged, alone, shared, wrong, split,
                     per_limit)
    mark_contract(sim.operator, report, watched.sim_time)


def _roller_feedback(report, watched, sim, judged, alone, shared, wrong, split,
                     per_limit) -> None:
    say = report.feedback.append
    red = watched.held_true("panel.red")

    if not judged:
        say("Nothing crossed the scale. `infeed.rotate` and `scale.rotate` are "
            "both yours, and `emitter.emit` makes one carton per RISING edge.")
        return

    if shared:
        say(f"{len(shared)} carton(s) were on the deck together. A checkweigher "
            f"weighs what is on it, so two cartons read as one peak and both "
            f"verdicts are guesses. Hold the feed while `scale.weight` is above "
            f"zero -- a real line singulates before it weighs.")

    if red > 0.9:
        say("The red lamp was lit for essentially the whole run, which flags "
            "every carton including the light ones.")
    elif red == 0.0:
        say("The red lamp never lit, so nothing was flagged. `panel.red` is how "
            "this exercise reports a reject.")

    if wrong:
        metal_shaped = [r for r in wrong if r["flagged"] == r["metal"]]
        if split and len(metal_shaped) >= len(wrong) * 0.8:
            say(f"Every carton you got wrong is one where the scale and the "
                f"inductive sensor disagree -- {len(split)} of them in this run. "
                f"A tall cardboard carton weighs 2160 g and a short steel one "
                f"4320 g, so at a limit of 1500 g the heavy ones are no longer "
                f"only the metal ones. Judge on `scale.weight`, and use "
                f"`metal_check.detect` as the second opinion it is.")
        else:
            worst = wrong[0]
            say(f"Carton {worst['carton']} weighed {worst['grams']} g against a "
                f"limit of {int(worst['limit'])} g and was "
                f"{'flagged' if worst['flagged'] else 'passed'}. Judge each "
                f"carton on the PEAK it showed while it was on the deck, not on "
                f"whatever the cell reads as it rolls off.")

    if len(per_limit) < 2:
        say("The run turned the pot to a second limit part way through and not "
            "enough cartons were weighed afterwards to mark it.")
    elif not wrong and not shared:
        say(f"Checkweighing was clean at both limits "
            f"({', '.join(per_limit)}), every carton weighed on its own, and the "
            f"{len(split)} carton(s) where mass and material disagree were "
            f"judged on the mass.")


def _summary_roller(evidence: dict, out) -> None:
    out(f"fed {evidence['fed']}, weighed {evidence['weighed']} "
        f"({evidence['weighed_alone']} alone, {evidence['shared_the_deck']} sharing "
        f"the deck), {evidence['outfeed']} reached the outfeed")
    out("limits the run used: "
        + ", ".join(f"{k} for {v} cartons" for k, v in evidence["limits_seen"].items()))
    out(f"{evidence['metal_and_weight_disagree']} carton(s) where the scale and "
        f"the inductive sensor disagree")
    for entry in evidence["misjudged"][:8]:
        out(f"  carton {entry['carton']:>3} {entry['grams']:>6} g against "
            f"{entry['limit']} g: {'flagged' if entry['flagged'] else 'passed'}"
            f"{', metal' if entry['metal'] else ''}")
    summary_contract(evidence, out)


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Roller line with weighing",
    "task": ("Checkweigh. Judge each carton on the peak weight it shows "
             "crossing the deck and light panel.red for anything over the "
             "limit on the pot. Two cartons on the deck read as one peak, "
             "so hold the feed while the scale is loaded. The mushroom is "
             "normally closed, stops both decks within 200 ms and latches -- "
             "only Reset, then Start, runs them again."),
    "build": RollerWeighScene,
    "observe": None,
    "grade": grade_roller_weighing,
    "summary": _summary_roller,
    "duration": RW_EXAM_ENDS_BY,
    "references": ("good", "metalonly", "fastfeed", "noestop", "startalone"),
    "tags": ("infeed.rotate, scale.rotate, emitter.emit, "
             "weight_readout.value, panel.green, panel.red are yours to "
             "write; scale.weight, metal_check.detect, outfeed.count, "
             "panel.setpoint and the buttons are the line's."),
}
