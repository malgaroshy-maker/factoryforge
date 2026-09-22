"""`guarded-cell`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/guarded_cell.py`.
"""

from __future__ import annotations

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import ESTOP_LIMIT, Item, PlantScene, Script


SCENE = "guarded-cell"


# --- guarded cell --------------------------------------------------------
#
# Observable fact: whether the contactor ever pulled in without somebody having
# pressed Start. The plant runs the relay and the contactor, so it knows the
# tick the motor started and it knows every press, and neither is something a
# controller can arrange from the bus.
#
# How a program fakes it: it cannot, and that is the point of the scene. The
# failure this rubric exists to catch is not a shortcut, it is the thing a
# student writes by accident -- holding `starter.coil` true for as long as the
# cell "should be running", so that when the guard is shut and the relay hands
# the coil back the machine starts by itself, with somebody still inside it.
# The exam opens both gate leaves mid-run and closes them again, and watches
# what the motor does between the relay re-energising and the next Start press.
# A program that got it right does nothing at all there.
#
# Two more the plant can see and the bus cannot. `belt.rotate` is the motor's
# own tag and this exercise is the one where the program must never write it --
# the contactor runs the motor -- so the grader records every tag the
# controller ever wrote and marks that directly. And a mute held longer than
# the scanner's own limit stops being a mute: the plant notes the moment the
# scanner withdraws one, which is what a mute somebody has taped on looks like
# from inside the machine.
#
# Numbers from `engine/templates/guarded_cell.json` and the parts: a relay with
# a 0.5 s channel-sync window that energises on a RISING reset edge only, a
# contactor with a 0.06 s pull-in, a scanner with a 6 s mute limit, and a 0.7 m
# cylinder stroke at 1.4 m/s.
GC_BELT_SPEED = 0.5
GC_MUTE_EYE_POS = 1.35
GC_FIELD_FROM = 1.6
GC_FIELD_TO = 2.4
GC_PUSH_EYE_POS = 2.8
GC_STATION_POS = 3.2
GC_LINE_END_POS = 3.9
GC_SYNC_WINDOW = 0.5
GC_PULL_IN = 0.06
GC_MUTE_LIMIT = 6.0
GC_ROD_TIME = 0.7 / 1.4


class GuardedCellScene(PlantScene):
    name = "guarded-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self._declare(
            Tag("relay.reset", "Safety Relay Reset", "bit", "output"),
            Tag("starter.coil", "Starter Contactor Coil", "bit", "output"),
            Tag("scanner.mute", "Scanner Mute Request", "bit", "output"),
            Tag("emitter.emit", "Emitter (Emit)", "bit", "output"),
            Tag("cylinder.extend", "Cylinder Extend Coil", "bit", "output"),
            Tag("cylinder.retract", "Cylinder Retract Coil", "bit", "output"),
            # The motor's own tag. Declared exactly as the engine declares it,
            # an Output the controller *could* write -- and the one thing this
            # exercise says not to. A tag the grader hid would teach nothing.
            Tag("belt.rotate", "Belt Conveyor (Rotate)", "bit", "output"),
            Tag("relay.k1", "Safety Relay Contact K1", "bit", "input"),
            Tag("relay.k2", "Safety Relay Contact K2", "bit", "input"),
            Tag("relay.cha", "Safety Relay Channel A", "bit", "input", value=True),
            Tag("relay.chb", "Safety Relay Channel B", "bit", "input", value=True),
            Tag("relay.fault", "Safety Relay Channel Fault", "bit", "input"),
            Tag("guard_a.closed", "Gate Leaf A Closed", "bit", "input", value=True),
            Tag("guard_b.closed", "Gate Leaf B Closed", "bit", "input", value=True),
            Tag("starter.aux", "Starter Auxiliary Contact", "bit", "input"),
            Tag("starter.overload", "Starter Overload OK (NC)", "bit", "input",
                value=True),
            Tag("scanner.stop", "Scanner Protective Field Clear (NC)", "bit",
                "input", value=True),
            Tag("scanner.warn", "Scanner Warning Field Broken", "bit", "input"),
            Tag("scanner.muted", "Scanner Muting Active", "bit", "input"),
            Tag("mute_eye.detect", "Mute Eye (Detect)", "bit", "input"),
            Tag("push_eye.detect", "Push Eye (Detect)", "bit", "input"),
            Tag("cylinder.extended", "Cylinder Extended Reed", "bit", "input"),
            Tag("cylinder.retracted", "Cylinder Retracted Reed", "bit", "input",
                value=True),
            Tag("transferred.count", "Chute Remover (Count)", "int", "input"),
            Tag("line_end.count", "Line End Remover (Count)", "int", "input"),
        )

        #: The relay powers up open, so the cell powers up unable to move. That
        #: is not a fault, it is what every safety relay does.
        self.energised = False
        self.relay_fault = False
        self._discrepancy = 0.0
        self._last_reset = False
        self.guard_a = self.guard_b = True

        self.contactor = False
        self._coil_timer = 0.0
        self.mute_held = 0.0
        self.muted = False
        self.rod = 0.0
        self.items: list[Item] = []
        self.transferred: list[Item] = []
        self.line_end: list[Item] = []
        self._next_id = 1
        self._emit_edge = False

        # --- ground truth ---
        #: Sim times the contactor pulled in with no Start press since the cell
        #: last stopped. The headline of the whole scene.
        self.started_without_a_press: list[float] = []
        self._stopped_at: float | None = 0.0
        #: Metres the belt ran after a gate leaf opened.
        self.travel_after_the_gate = 0.0
        self._gate_opened_at: float | None = None
        #: Times the scanner withdrew a mute because it had been held past its
        #: own limit.
        self.mute_withdrawn: list[float] = []
        self.both_coils = 0
        self.max_mute_held = 0.0

        # The examiner's test sheet. The two presses after the gate shuts are
        # the whole exam: Reset closes the relay, and the motor must not move
        # until the Start six seconds later.
        self.script = Script([
            (2.0, self.panel.press("reset")),
            (6.0, self.panel.press("start")),
            (22.0, self._open_the_gate),
            (26.0, self._shut_the_gate),
            (28.0, self.panel.press("reset")),
            (34.0, self.panel.press("start")),
        ])

    def _open_the_gate(self) -> None:
        """Both leaves at once, and the part in the field taken out.

        The second half is not decoration. A carton standing in the protective
        field when the cell stops leaves the field un-clear, and a stopped belt
        cannot clear it -- so the scanner refuses to let the cell start, for
        ever, and a correct program is marked as one that could not restart.
        That deadlock is real and `tools/try_scene.py` demonstrates it
        deliberately, but it is not this rubric's question. Somebody opening a
        guard is somebody going in, and going in is how the part comes out.
        """
        self.guard_a = self.guard_b = False
        self._gate_opened_at = self.t
        self.items = [i for i in self.items
                      if not GC_FIELD_FROM - 0.2 <= i.position <= GC_FIELD_TO + 0.2]

    def _shut_the_gate(self) -> None:
        self.guard_a = self.guard_b = True
        self._gate_opened_at = None

    # --- the plant ---

    def step(self, dt: float) -> None:
        self._step_relay(dt)
        self._step_starter(dt)
        self._step_scanner(dt)
        self._step_line(dt)

    def _step_relay(self, dt: float) -> None:
        a, b = self.guard_a, self.guard_b
        if a != b:
            self._discrepancy += dt
            if self._discrepancy >= GC_SYNC_WINDOW:
                self.relay_fault = True
        else:
            self._discrepancy = 0.0

        healthy = a and b and not self.relay_fault
        if not healthy:
            # Immediate and unconditional. A safety output that waited for
            # anything would not be a safety output.
            self.energised = False

        reset = self.bit("relay.reset")
        if reset and not self._last_reset:
            if self.relay_fault and a == b:
                self.relay_fault = False
                self._discrepancy = 0.0
            if a and b and not self.relay_fault:
                self.energised = True
        self._last_reset = reset

        self.tags.set("relay.cha", a)
        self.tags.set("relay.chb", b)
        self.tags.set("relay.k1", self.energised)
        self.tags.set("relay.k2", self.energised)
        self.tags.set("relay.fault", self.relay_fault)
        self.tags.set("guard_a.closed", a)
        self.tags.set("guard_b.closed", b)

    def _step_starter(self, dt: float) -> None:
        # The relay holds the coil circuit open. In the engine it does that by
        # forcing the tag; here the plant simply does not obey a command the
        # relay is not passing, because a forced tag is this tool's
        # disqualification signal and the grader must not trip its own wire.
        wants = self.bit("starter.coil") and self.energised
        was = self.contactor
        if wants:
            self._coil_timer += dt
            if not self.contactor and self._coil_timer >= GC_PULL_IN:
                self.contactor = True
        else:
            self._coil_timer = 0.0
            self.contactor = False

        if self.contactor and not was:
            # The motor just started. Was it started by anybody?
            since = self._stopped_at if self._stopped_at is not None else 0.0
            if not self.panel.started_since(since):
                self.started_without_a_press.append(round(self.t, 2))
        elif was and not self.contactor:
            self._stopped_at = self.t

        self.tags.set("starter.aux", self.contactor)
        self.tags.set("belt.rotate", self.contactor)

        if self.contactor and self._gate_opened_at is not None:
            self.travel_after_the_gate += GC_BELT_SPEED * dt

    def _step_scanner(self, dt: float) -> None:
        wants_mute = self.bit("scanner.mute")
        if wants_mute:
            self.mute_held += dt
            self.max_mute_held = max(self.max_mute_held, self.mute_held)
            allowed = self.mute_held <= GC_MUTE_LIMIT
            if self.muted and not allowed:
                # The scanner taking the guard back under a standing request.
                self.mute_withdrawn.append(round(self.t, 2))
            self.muted = allowed
        else:
            self.mute_held = 0.0
            self.muted = False

        broken = any(GC_FIELD_FROM <= i.position <= GC_FIELD_TO for i in self.items)
        self.tags.set("scanner.warn", broken)
        self.tags.set("scanner.muted", self.muted)
        self.tags.set("scanner.stop", (not broken) or self.muted)

    def _step_line(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            self.items.append(Item(id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        extend = self.bit("cylinder.extend")
        retract = self.bit("cylinder.retract")
        if extend and retract:
            self.both_coils += 1
        # Energising both is not a way to hold a double-solenoid valve still,
        # it is a way to leave the rod where the last scan put it.
        rate = dt / GC_ROD_TIME
        if extend and not retract:
            self.rod = min(self.rod + rate, 1.0)
        elif retract and not extend:
            self.rod = max(self.rod - rate, 0.0)
        self.tags.set("cylinder.extended", self.rod >= 0.999)
        self.tags.set("cylinder.retracted", self.rod <= 0.001)

        if self.contactor:
            for item in self.items:
                item.position += GC_BELT_SPEED * dt

        still: list[Item] = []
        for item in self.items:
            if self.rod > 0.5 and abs(item.position - GC_STATION_POS) <= 0.22:
                item.lane = "chute"
                self.transferred.append(item)
            elif item.position >= GC_LINE_END_POS:
                item.lane = "line-end"
                self.line_end.append(item)
            else:
                still.append(item)
        self.items = still

        self.tags.set("mute_eye.detect", self._eye(self.items, GC_MUTE_EYE_POS))
        self.tags.set("push_eye.detect", self._eye(self.items, GC_PUSH_EYE_POS))
        self.tags.set("transferred.count", len(self.transferred))
        self.tags.set("line_end.count", len(self.line_end))


def grade_guarded_cell(watched: Watched, engine: GradedEngine, report: Report,
                       duration: float) -> None:
    sim: GuardedCellScene = watched.inner
    wrote_the_motor = engine is not None and "belt.rotate" in engine.written_tags
    allowed = GC_BELT_SPEED * ESTOP_LIMIT

    report.evidence.update({
        "transferred": len(sim.transferred),
        "past_the_station": len(sim.line_end),
        "started_without_a_press_at": sim.started_without_a_press[:10],
        "wrote_belt_rotate": wrote_the_motor,
        "belt_travel_after_the_gate_m": round(sim.travel_after_the_gate, 3),
        "allowed_after_the_gate_m": round(allowed, 3),
        "longest_mute_s": round(sim.max_mute_held, 2),
        "scanner_mute_limit_s": GC_MUTE_LIMIT,
        "mute_withdrawn_at": sim.mute_withdrawn[:10],
        "both_cylinder_coils_ticks": sim.both_coils,
        "contactor_fraction": round(watched.held_true("starter.aux"), 3),
        "presses": sim.panel.presses,
    })

    report.add("cell.transferred_cartons",
               len(sim.transferred) >= 3,
               f"{len(sim.transferred)} carton(s) reached the chute "
               f"(at least 3), {len(sim.line_end)} went past the station")
    report.add("cell.never_wrote_the_motor",
               not wrote_the_motor,
               "the program never wrote belt.rotate -- the contactor runs the "
               "motor" if not wrote_the_motor else
               "the program wrote belt.rotate, which is the motor's own tag and "
               "the one thing this exercise says not to touch")
    report.add("cell.no_start_on_the_permissive",
               not sim.started_without_a_press,
               "the motor never started without somebody pressing Start"
               if not sim.started_without_a_press else
               f"the motor started {len(sim.started_without_a_press)} time(s) "
               f"with no Start press since it last stopped, at "
               f"{sim.started_without_a_press[:5]}s")
    report.add("cell.gate_stopped_it",
               sim.travel_after_the_gate <= allowed,
               f"the belt moved {sim.travel_after_the_gate * 1000:.0f} mm after a "
               f"gate leaf opened (at most {allowed * 1000:.0f} mm)")
    report.add("cell.mute_within_the_limit",
               not sim.mute_withdrawn,
               f"the longest mute was {sim.max_mute_held:.1f}s, inside the "
               f"scanner's {GC_MUTE_LIMIT:g}s limit"
               if not sim.mute_withdrawn else
               f"the scanner withdrew the mute {len(sim.mute_withdrawn)} time(s) "
               f"after it was held {sim.max_mute_held:.1f}s, past its "
               f"{GC_MUTE_LIMIT:g}s limit")
    report.add("cell.one_coil_at_a_time",
               sim.both_coils == 0,
               "both cylinder solenoids were never energised at once"
               if not sim.both_coils else
               f"both solenoids were energised together on {sim.both_coils} ticks")

    _guarded_feedback(report, watched, sim, wrote_the_motor, allowed)


def _guarded_feedback(report, watched, sim, wrote_the_motor, allowed) -> None:
    say = report.feedback.append

    if sim.started_without_a_press:
        say(f"The motor started at {sim.started_without_a_press[0]}s with nobody "
            f"having pressed Start since it last stopped. That is automatic "
            f"restart, and it is the failure this whole cell exists to prevent: "
            f"the relay closing hands `starter.coil` back to your program, it "
            f"does not command it. Shutting a gate and pressing the relay's "
            f"Reset must start nothing at all -- Start is what starts it, and "
            f"the run has to be latched off by the trip until then.")

    if wrote_the_motor:
        say("The program wrote `belt.rotate`. On this cell the contactor runs "
            "the motor: you command `starter.coil` and read `starter.aux` to "
            "find out whether it pulled in. Writing the motor's tag directly "
            "works right up until the day a guard is open and the relay is "
            "holding the coil -- and then it drives a motor the safety circuit "
            "believes it has stopped.")

    if watched.held_true("starter.aux") == 0.0:
        say("The contactor never pulled in. The relay powers up open, so the "
            "cell powers up unable to move: write `relay.reset` to close it "
            "(a RISING edge -- a level is automatic restart), then hold "
            "`starter.coil` once somebody has pressed Start.")
    elif not sim.transferred:
        say("The motor ran and nothing reached the chute. `cylinder.extend` and "
            "`cylinder.retract` are two coils, not one, and the two reeds are "
            "how you know where the rod is -- between them neither is made, so "
            "`not extended` is not `retracted`.")

    if sim.mute_withdrawn:
        say(f"The scanner stopped honouring a mute your program was still "
            f"asking for, after {sim.max_mute_held:.1f}s. Its own limit is "
            f"{GC_MUTE_LIMIT:g}s, because muting held longer than a pallet "
            f"takes to cross is muting somebody has taped on. Bridge the field "
            f"for each carton and let it go again.")

    if sim.travel_after_the_gate > allowed:
        say(f"The belt ran {sim.travel_after_the_gate * 1000:.0f} mm after a gate "
            f"leaf opened. The relay drops the coil circuit immediately; if the "
            f"motor kept turning, something other than the contactor is driving "
            f"it.")

    if (sim.transferred and not sim.started_without_a_press
            and not wrote_the_motor and not sim.mute_withdrawn):
        say(f"Clean: {len(sim.transferred)} cartons transferred, the gate "
            f"stopped the cell, the relay closing on Reset started nothing, and "
            f"`belt.rotate` was never written -- the contactor ran the motor.")


def _summary_guarded(evidence: dict, out) -> None:
    out(f"{evidence['transferred']} transferred, "
        f"{evidence['past_the_station']} past the station; the contactor was in "
        f"for {evidence['contactor_fraction'] * 100:.0f}% of the run")
    started = evidence["started_without_a_press_at"]
    out(f"motor starts with no Start press: {started if started else 'none'}")
    out(f"belt.rotate written by the program: "
        f"{'YES' if evidence['wrote_belt_rotate'] else 'no'}")
    out(f"belt after the gate opened {evidence['belt_travel_after_the_gate_m'] * 1000:.0f} mm "
        f"(at most {evidence['allowed_after_the_gate_m'] * 1000:.0f} mm)")
    out(f"longest mute {evidence['longest_mute_s']:.1f}s against a "
        f"{evidence['scanner_mute_limit_s']:g}s limit")


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Guarded cell",
    "task": ("Run the transfer cell WITHOUT ever writing belt.rotate. You "
             "command starter.coil and the contactor runs the motor. The "
             "safety relay holds your coil off until both gate leaves are "
             "shut and somebody resets it -- and the relay closing hands "
             "the coil back, it does not start anything. Bridge the "
             "scanner for each carton, within its own mute limit."),
    "build": GuardedCellScene,
    "observe": None,
    "grade": grade_guarded_cell,
    "summary": _summary_guarded,
    "duration": 70.0,
    "references": ("good", "autostart", "writesbelt", "tapedmute"),
    "tags": ("relay.reset, starter.coil, scanner.mute, emitter.emit, "
             "cylinder.extend, cylinder.retract, panel.green, panel.red are "
             "yours to write; relay.k1/k2/cha/chb/fault, guard_a.closed, "
             "guard_b.closed, starter.aux, starter.overload, scanner.stop, "
             "scanner.warn, scanner.muted, mute_eye.detect, "
             "push_eye.detect, cylinder.extended, cylinder.retracted, "
             "transferred.count, line_end.count are the cell's. "
             "belt.rotate is the motor's, and writing it fails the "
             "exercise."),
}
