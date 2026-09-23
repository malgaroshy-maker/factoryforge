"""`guarded-cell`: the plant model, the rubric, the feedback and the
summary.

`grading.registry` finds it by `SCENE` and `RUBRIC`. Its reference
controllers are in `grading/reference/guarded_cell.py`.
"""

from __future__ import annotations

import math

from factoryforge_sidecar.tags import Tag

from ..core import GradedEngine, Report, Watched
from ..plant import (CARTON_WIDTH, ESTOP_LIMIT, EngineFeed, Item, PlantScene, Script,
                     declare_stack_light, fault_input, pot_start, remover_catch)
from ..templates import TemplateError, template


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
# **The gate has a solenoid lock, and since IP-29 the exam obeys it.** In the
# engine a leaf the program holds locked shut cannot be opened
# (`SafetyGate.cs`, `Toggle`), and the brief never mentions the locks, so a
# program that leaves them alone -- every one written to the brief -- has its
# gate opened while the cell runs, exactly as before. A program that does lock
# the guard is answered the way an operator answers a locked guard: they press
# Stop, which is how anybody asks a machine to let them in, and keep trying the
# handle. Guard locking done properly -- locked while the motor can run,
# released once it has stopped -- lets them in within a scan or two, and the
# rest of the exam runs from the moment the gate really opened. Done badly --
# locked for good -- it never lets them in, and that fails a check of its own,
# `cell.guard_released_for_access`: a guard that stays locked after the
# machine has stopped keeps the people out who have to clear it. What a locking
# program is not asked is the automatic-restart question, and it does not need
# to be: its gate cannot be opened while it wants to run, which is the hazard
# guard locking exists to remove. Until IP-29 the model reported the lock and
# opened the gate through it.
#
# Two more the plant can see and the bus cannot. `belt.rotate` is the motor's
# own tag and this exercise is the one where the program must never write it --
# the contactor runs the motor -- so the grader records every tag the
# controller ever wrote and marks that directly. And a mute held longer than
# the scanner's own limit stops being a mute: the plant notes the moment the
# scanner withdraws one, which is what a mute somebody has taped on looks like
# from inside the machine.
#
# Read from the template (IP-19), with the behaviour from the parts: a relay
# that energises on a RISING reset edge only, within a channel-sync window; a
# contactor with a pull-in delay; a scanner with a mute limit; a cylinder with a
# stroke and a rod speed; gate leaves that slide. As shipped: 0.5 s, 0.06 s,
# 6 s, 0.7 m at 1.4 m/s, and 0.72 m at 1.1 m/s. Positions are world X along the
# belt, and since IP-29 they are the template's.
_PLANT = template(SCENE)
_BELT = _PLANT.part("belt", "ConveyorBelt")
_SCANNER = _PLANT.part("scanner", "AreaScanner")
_CYLINDER = _PLANT.part("cylinder", "PneumaticCylinder")
_STARTER = _PLANT.part("starter", "MotorStarter").engineering_units()
_RELAY = _PLANT.part("relay", "SafetyRelay")
_GATES = {leaf: _PLANT.part(leaf, "SafetyGate") for leaf in ("guard_a", "guard_b")}

GC_BELT_SPEED = _BELT.number("speed")
GC_START_POS = _PLANT.part("emitter", "Emitter").x
#: The eyes and the cylinder. Until IP-29 this model had them at 1.35, 2.8 and
#: 3.2 -- a layout of its own, 0.35 to 0.8 m downstream of the template's.
GC_MUTE_EYE_POS = _PLANT.part("mute_eye", "PhotoelectricSensor").x
GC_PUSH_EYE_POS = _PLANT.part("push_eye", "PhotoelectricSensor").x
GC_STATION_POS = _CYLINDER.x
#: Where the `line_end` remover takes a carton (`plant.remover_catch`). This
#: model used 3.9, past the end of a belt that ends at 3.0, until IP-29.
GC_LINE_END_POS = remover_catch(_PLANT.part("line_end", "Remover"), _BELT.span()[1])


def _field(radius: float) -> tuple[float, float]:
    """Where a scanner field crosses the belt's centreline. `AreaScanner.cs`
    measures a carton's centre, flat, from the scanner, and the template's
    scanner stands off to the side of the belt."""
    if _SCANNER.number("scan_arc") < 360.0:
        raise TemplateError(f"{_SCANNER.source}: the scanner's scan_arc is "
                            f"{_SCANNER.number('scan_arc'):g}; the grader's model "
                            f"of it assumes the full 360")
    off = _SCANNER.position[2] - _BELT.position[2]
    reach = math.sqrt(max(radius ** 2 - off ** 2, 0.0))
    return _SCANNER.x - reach, _SCANNER.x + reach


#: The protective field and the warning field, along the belt. Until IP-29 this
#: model had the protective field at 1.6 to 2.4 and used it for the warning
#: field too, where the template's scanner warns from 0.25 to 2.75.
GC_FIELD_FROM, GC_FIELD_TO = _field(_SCANNER.number("stop_radius"))
GC_WARN_FROM, GC_WARN_TO = _field(_SCANNER.number("warn_radius"))
GC_SYNC_WINDOW = _RELAY.number("sync_window")
GC_PULL_IN = _STARTER.number("pull_in")
GC_MUTE_LIMIT = _SCANNER.number("mute_limit")
GC_STROKE = _CYLINDER.number("stroke")
GC_ROD_SPEED = _CYLINDER.number("rod_speed")
GC_ROD_TIME = GC_STROKE / GC_ROD_SPEED
GC_REED_BAND = _CYLINDER.number("reed_band")
#: `PneumaticCylinder.cs`: the face plate sits `Extension + 0.02` (the rod's
#: stub) plus its own 0.03 thickness in front of the cylinder, so it meets a
#: carton centred on the belt once the rod is out far enough to cross the gap
#: to the carton's near side. 0.33 m of the 0.7 m stroke, as shipped. A carton
#: whose centre is in front of the plate's width then goes into the chute.
GC_ROD_STUB = 0.02
GC_PLATE_THICKNESS = 0.03
GC_CONTACT_EXTENSION = (abs(_BELT.position[2] - _CYLINDER.position[2])
                        - CARTON_WIDTH / 2 - GC_ROD_STUB - GC_PLATE_THICKNESS)
GC_PLATE_HALF = _CYLINDER.number("plate_width") / 2
#: How long a leaf takes to slide its whole travel (`SafetyGate.cs`, `Step`),
#: and how far open it may be and still read shut (`IsClosed`, 2 %).
GC_GATE_TRAVEL_TIME = {leaf: part.number("travel") / part.number("slide_speed")
                       for leaf, part in _GATES.items()}
GC_GATE_CLOSED_BELOW = 0.02
#: How long the examiner keeps trying a locked handle after pressing Stop.
#: A program that releases its lock once the machine has stopped does so
#: within a scan or two; this is generous for any scan time a PLC has.
GC_ACCESS_PATIENCE = 5.0
#: The exam sheet, relative to the moment the gate actually opened: shut it
#: four seconds later, Reset two seconds after that, Start six after that.
GC_SHUT_AFTER, GC_RESET_AFTER, GC_START_AFTER = 4.0, 6.0, 12.0
GC_POT_START = pot_start(_PLANT)
#: The motor behind the contactor, for `starter.current`: its full-load amps
#: and how loaded it runs, from the template.
GC_FLA = _STARTER.number("fla")
GC_LOAD_PERCENT = _STARTER.number("load_percent")
#: No template sets these: `MotorStarter.cs` owns them (`InrushFactor` :100,
#: `InrushDecay` :101). Locked-rotor current is six times full load, decaying
#: to the running current with a 0.45 s time constant.
GC_INRUSH_FACTOR = 6.0
GC_INRUSH_DECAY = 0.45


class GateLeaf:
    """One leaf of the cell gate, as `SafetyGate.cs` runs it.

    It slides open and shut at the template's speed and reads shut while it
    is within 2 % of shut. Its solenoid lock is the program's (`.lock`): a
    leaf locked while shut refuses the handle, and locking it stops a leaf
    that has only just started to open. A lock cannot shut a leaf that is
    already open.
    """

    def __init__(self, travel_time: float) -> None:
        self.rate = 1.0 / max(travel_time, 1e-6)
        self.opening = 0.0
        self.want_open = False
        self.locked = False

    @property
    def closed(self) -> bool:
        return self.opening <= GC_GATE_CLOSED_BELOW

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        if locked and self.closed:
            self.want_open = False

    def toggle(self) -> bool:
        """Pull the handle. False, and nothing moves, if it is locked shut."""
        if self.locked and self.closed:
            return False
        self.want_open = not self.want_open
        return True

    def step(self, dt: float) -> None:
        target = 1.0 if self.want_open else 0.0
        step = self.rate * dt
        self.opening += max(min(target - self.opening, step), -step)


class GuardedCellScene(PlantScene):
    name = "guarded-cell"

    def __init__(self, seed: int) -> None:
        super().__init__(seed, setpoint=GC_POT_START)
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
            Tag("starter.current", "Starter Motor Current (A)", "float", "input"),
            Tag("guard_a.lock", "Gate Leaf A Solenoid Lock", "bit", "output"),
            Tag("guard_b.lock", "Gate Leaf B Solenoid Lock", "bit", "output"),
            Tag("guard_a.locked", "Gate Leaf A Locked Shut", "bit", "input"),
            Tag("guard_b.locked", "Gate Leaf B Locked Shut", "bit", "input"),
            fault_input("belt", "Conveyor Drive Fault"),
            fault_input("cylinder", "Cylinder Seized"),
        )
        declare_stack_light(self.tags)

        #: The relay powers up open, so the cell powers up unable to move. That
        #: is not a fault, it is what every safety relay does.
        self.energised = False
        self.relay_fault = False
        self._discrepancy = 0.0
        self._last_reset = False
        self.leaves = {leaf: GateLeaf(GC_GATE_TRAVEL_TIME[leaf]) for leaf in _GATES}

        self.contactor = False
        self._coil_timer = 0.0
        self._start_timer = 0.0
        self.mute_held = 0.0
        self.muted = False
        self.extension = 0.0
        self.spool_extends = False
        self.items: list[Item] = []
        self.transferred: list[Item] = []
        self.line_end: list[Item] = []
        self._next_id = 1
        self._emit_edge = False
        self._feed = EngineFeed(_PLANT.part("emitter", "Emitter"))

        # --- ground truth ---
        #: Sim times the contactor pulled in with no Start press since the cell
        #: last stopped. The headline of the whole scene.
        self.started_without_a_press: list[float] = []
        self._stopped_at: float | None = 0.0
        #: Metres the belt ran after a gate leaf opened.
        self.travel_after_the_gate = 0.0
        self._gate_opened_at: float | None = None
        #: Times the gate went from shut to open.
        self.gate_openings = 0
        #: Times the scanner withdrew a mute because it had been held past its
        #: own limit.
        self.mute_withdrawn: list[float] = []
        self.both_coils = 0
        self.max_mute_held = 0.0
        #: The examiner going in: when they asked, which leaves refused the
        #: handle, when they pressed Stop to be let in, when the gate opened,
        #: and whether they gave up. None until the exam asks.
        self.access: dict | None = None

        # The examiner's test sheet. Reset closes the relay and Start starts
        # the cell; at 22 s the examiner goes in, and the rest of the sheet
        # (`_go_in`) is timed from when the gate really opened. Those three
        # steps are the whole exam: the gate opening must stop the motor,
        # Reset must close the relay and start nothing, and only the Start
        # after it may.
        self.script = Script([
            (2.0, self.panel.press("reset")),
            (6.0, self.panel.press("start")),
            (22.0, self._ask_for_access),
        ])

    # --- the examiner going in ---

    def _ask_for_access(self) -> None:
        """Pull both handles, and if the guard is locked, ask to be let in.

        `SafetyGate.Toggle` refuses silently while the solenoid holds the leaf
        shut. An operator who meets that presses Stop -- the one request every
        machine understands -- and keeps trying the handle.
        """
        self.access = {"asked_at": round(self.t, 2), "refused": [],
                       "stop_pressed_at": None, "opened_at": None,
                       "gave_up_at": None,
                       "deadline": self.t + GC_ACCESS_PATIENCE}
        self._pull_the_handles()

    def _pull_the_handles(self) -> None:
        access = self.access
        refused = False
        for name, leaf in self.leaves.items():
            if leaf.want_open or not leaf.closed:
                continue
            if not leaf.toggle():
                refused = True
                if name not in access["refused"]:
                    access["refused"].append(name)
        if refused and access["stop_pressed_at"] is None:
            self.panel.press("stop")()
            access["stop_pressed_at"] = round(self.t, 2)

    def _step_access(self) -> None:
        access = self.access
        if access is None or access["gave_up_at"] is not None:
            return
        if access["opened_at"] is None and self.t > access["deadline"]:
            access["gave_up_at"] = round(self.t, 2)
            return
        if not all(leaf.want_open or not leaf.closed for leaf in self.leaves.values()) \
                and self.t <= access["deadline"] and not access.get("shut"):
            self._pull_the_handles()

    def _went_in(self) -> None:
        """The gate is open: somebody is going in.

        They take the part out of the field as they go. That is not
        decoration. A carton standing in the protective field when the cell
        stops leaves the field un-clear, and a stopped belt cannot clear it --
        so the scanner refuses to let the cell start, for ever, and a correct
        program is marked as one that could not restart. That deadlock is real
        and `tools/try_scene.py` demonstrates it deliberately, but it is not
        this rubric's question. Somebody opening a guard is somebody going in,
        and going in is how the part comes out.
        """
        self.access["opened_at"] = round(self.t, 2)
        self.items = [i for i in self.items
                      if not GC_FIELD_FROM - 0.2 <= i.position <= GC_FIELD_TO + 0.2]
        self.script.at(self.t + GC_SHUT_AFTER, self._shut_the_gate)
        self.script.at(self.t + GC_RESET_AFTER, self.panel.press("reset"))
        self.script.at(self.t + GC_START_AFTER, self.panel.press("start"))

    def _shut_the_gate(self) -> None:
        self.access["shut"] = True
        for leaf in self.leaves.values():
            if leaf.want_open:
                leaf.toggle()

    # --- the plant ---

    def step(self, dt: float) -> None:
        self._step_access()
        self._step_gates(dt)
        self._step_relay(dt)
        self._step_starter(dt)
        self._step_scanner(dt)
        self._step_line(dt)

    def _step_gates(self, dt: float) -> None:
        # `SafetyGate.StepPart`: the lock first, then the slide. A leaf locked
        # while it is still shut stops opening -- and one already open cannot
        # be shut by it.
        was_shut = all(leaf.closed for leaf in self.leaves.values())
        for name, leaf in self.leaves.items():
            leaf.set_locked(self.bit(f"{name}.lock"))
            leaf.step(dt)
        shut = all(leaf.closed for leaf in self.leaves.values())
        if was_shut and not shut:
            self._gate_opened_at = self.t
            self.gate_openings += 1
            if self.access is not None and self.access["opened_at"] is None:
                self._went_in()
        elif shut:
            self._gate_opened_at = None

    def _step_relay(self, dt: float) -> None:
        a, b = self.leaves["guard_a"].closed, self.leaves["guard_b"].closed
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
        for name, leaf in self.leaves.items():
            self.tags.set(f"{name}.closed", leaf.closed)
            # `SafetyGate.cs`: `locked` is the solenoid energised AND the
            # leaf shut.
            self.tags.set(f"{name}.locked", leaf.locked and leaf.closed)

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

        # `MotorStarter.cs` (`Step`): inrush from the moment the contactor
        # closes, decaying to the running current. The thermal overload is
        # not modelled -- `starter.overload` reads healthy throughout, and
        # at the template's load a running motor never reaches its trip.
        if self.contactor:
            if not was:
                self._start_timer = 0.0
            self._start_timer += dt
            running = GC_FLA * max(GC_LOAD_PERCENT, 0.0) / 100.0
            locked_rotor = GC_FLA * GC_INRUSH_FACTOR
            current = running + max(locked_rotor - running, 0.0) * math.exp(
                -self._start_timer / GC_INRUSH_DECAY)
        else:
            current = 0.0
        self.tags.set("starter.current", current)
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
        warned = any(GC_WARN_FROM <= i.position <= GC_WARN_TO for i in self.items)
        self.tags.set("scanner.warn", warned)
        self.tags.set("scanner.muted", self.muted)
        self.tags.set("scanner.stop", (not broken) or self.muted)

    def _step_line(self, dt: float) -> None:
        emit = self.bit("emitter.emit")
        if emit and not self._emit_edge:
            height, metal = self._feed.next()
            self.items.append(Item(height=height, metal=metal,
                                   position=GC_START_POS, id=self._next_id))
            self._next_id += 1
        self._emit_edge = emit

        extend = self.bit("cylinder.extend")
        retract = self.bit("cylinder.retract")
        if extend and retract:
            self.both_coils += 1
        # `PneumaticCylinder.Step`: a 5/2 double-solenoid valve. One coil
        # moves the spool; both, or neither, leave it where it was, and the
        # rod goes on to whichever end the spool points at. Until IP-29 this
        # model stopped the rod wherever it was when the coil dropped.
        if extend and not retract:
            self.spool_extends = True
        elif retract and not extend:
            self.spool_extends = False
        target = GC_STROKE if self.spool_extends else 0.0
        step = GC_ROD_SPEED * dt
        self.extension += max(min(target - self.extension, step), -step)
        self.tags.set("cylinder.extended", self.extension >= GC_STROKE - GC_REED_BAND)
        self.tags.set("cylinder.retracted", self.extension <= GC_REED_BAND)

        if self.contactor:
            for item in self.items:
                item.position += GC_BELT_SPEED * dt

        still: list[Item] = []
        for item in self.items:
            if (self.extension >= GC_CONTACT_EXTENSION
                    and abs(item.position - GC_STATION_POS) <= GC_PLATE_HALF):
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
        "gate_openings": sim.gate_openings,
        "longest_mute_s": round(sim.max_mute_held, 2),
        "scanner_mute_limit_s": GC_MUTE_LIMIT,
        "mute_withdrawn_at": sim.mute_withdrawn[:10],
        "both_cylinder_coils_ticks": sim.both_coils,
        "contactor_fraction": round(watched.held_true("starter.aux"), 3),
        "presses": sim.panel.presses,
        "access": None if sim.access is None else
        {k: v for k, v in sim.access.items() if k not in ("deadline", "shut")},
    })

    access = sim.access
    if access is not None:
        opened, stop = access["opened_at"], access["stop_pressed_at"]
        if opened is not None and not access["refused"]:
            detail = f"the gate opened when the examiner pulled it at {opened:g}s"
        elif opened is not None:
            detail = (f"the guard was locked at {access['asked_at']:g}s; Stop was "
                      f"pressed at {stop:g}s and the gate opened at {opened:g}s")
        else:
            asked = (f"after Stop was pressed at {stop:g}s" if stop is not None
                     else f"after the examiner first pulled it")
            detail = (f"the guard was locked at {access['asked_at']:g}s and stayed "
                      f"shut for {GC_ACCESS_PATIENCE:g}s {asked} -- nobody could "
                      f"get in")
        report.add("cell.guard_released_for_access", opened is not None, detail)

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
    # Only once a leaf has actually opened: a gate that never opened stopped
    # nothing, and 0 mm of belt after it is not a pass. A run whose gate the
    # program kept locked fails `cell.guard_released_for_access` instead.
    if sim.gate_openings:
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

    access = sim.access
    if access is not None and access["opened_at"] is None:
        say(f"The examiner went to open the gate at {access['asked_at']:g}s and "
            f"found it locked, pressed Stop to be let in, and waited "
            f"{GC_ACCESS_PATIENCE:g}s -- and `guard_a.lock` / `guard_b.lock` "
            f"never let go. A guard lock holds the door while the machine can "
            f"still hurt somebody, and releases it once the machine has "
            f"stopped: lock while `starter.aux` is made, release when it is not. "
            f"A guard that stays locked on a stopped cell keeps out the people "
            f"who have to clear it -- and the half of this exam that opens the "
            f"gate on a running cell never ran.")

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
            and not wrote_the_motor and not sim.mute_withdrawn
            and sim.gate_openings):
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
    access = evidence.get("access")
    if access:
        out(f"examiner went in at {access['asked_at']:g}s: "
            + (f"gate opened {access['opened_at']:g}s" if access["opened_at"] is not None
               else "the guard stayed locked")
            + (f", after Stop at {access['stop_pressed_at']:g}s"
               if access["stop_pressed_at"] is not None else ""))


#: What this scene marks, and what it says it marks. `grading.registry`
#: files it under `SCENE`.
RUBRIC = {
    "title": "Guarded cell",
    "task": ("Run the transfer cell WITHOUT ever writing belt.rotate. You "
             "command starter.coil and the contactor runs the motor. The "
             "safety relay holds your coil off until both gate leaves are "
             "shut and somebody resets it -- and the relay closing hands "
             "the coil back, it does not start anything. Bridge the "
             "scanner for each carton, within its own mute limit. If you "
             "lock the gate, let the operator in once the cell has stopped."),
    "build": GuardedCellScene,
    "observe": None,
    "grade": grade_guarded_cell,
    "summary": _summary_guarded,
    "duration": 70.0,
    "references": ("good", "autostart", "writesbelt", "tapedmute", "guardlock",
                   "lockedshut"),
    "tags": ("relay.reset, starter.coil, scanner.mute, emitter.emit, "
             "cylinder.extend, cylinder.retract, guard_a.lock, guard_b.lock, "
             "panel.green, panel.red are "
             "yours to write; relay.k1/k2/cha/chb/fault, guard_a.closed, "
             "guard_b.closed, guard_a.locked, guard_b.locked, starter.aux, starter.overload, scanner.stop, "
             "scanner.warn, scanner.muted, mute_eye.detect, "
             "push_eye.detect, cylinder.extended, cylinder.retracted, "
             "transferred.count, line_end.count are the cell's. "
             "belt.rotate is the motor's, and writing it fails the "
             "exercise."),
}
