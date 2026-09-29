"""Grade a student's PLC program against a scene, unattended.

    python tools/grade.py --list
    python tools/grade.py --scene sorting-by-height --student "a.patel"
    python tools/grade.py --scene sorting-by-height --json marks/a.patel.json

This is the *other* side of `tools/try_scene.py`. That one drives a scene the
way a PLC would, to prove the scene works. This one runs the scene and lets
somebody else's controller drive it, to find out whether *they* work. It starts
a tag bus, prints the one command a student has to run, waits for their sidecar
to connect, watches for a fixed window and reports.

    grade.py  ── tag bus ──  factoryforge-sidecar connect ── OPC UA / S7 / MQTT ── PLC
    (the exam)                 (the student's own driver)          (their program)

So the thing under test is the whole chain a real line has, over the same seam
a real PLC uses. Nothing here reaches into the controller and nothing here
drives the scene.

**The verdict comes from the plant, not from the tags.** The scene knows how
tall every carton it made was and which lane it ended in, how many litres the
pump really moved, and the tick the contactor pulled in -- and no controller
can reach any of it. Counters, sensor values and actuator commands are
*evidence*, the part a student needs in order to fix anything, but they are
never the criterion, because every one of them is reachable from the bus and a
criterion you can reach is a criterion you can fake.

**And the exam changes the plant while the program runs.** Six of the ten
scenes are gradeable only because of this: the pot moves to a second value, the
drive's top speed doubles, the gantry slows down, the pump is re-rated. None of
those is a value on the bus, so the only way to notice is to measure -- which
is exactly the difference between a program written on feedback and one written
on a stopwatch. A rubric that never moved anything would mark both the same.

**Forcing is refused, not ignored.** A forced tag is a value that disagrees
with the simulation on purpose. It is the right tool for fault injection and
the wrong tool for a graded run, and a grader that did not look would be beaten
by four lines of Node-RED. Every `force` message is recorded, every tick is
checked for a pinned tag, and either one ends the run as DISQUALIFIED rather
than FAIL -- an instructor wants to tell "got it wrong" apart from "tried it on".

Exit codes, for a marking script:

    0  PASS           every check met
    1  FAIL           the program ran and got it wrong
    2  ERROR          nothing to grade -- nobody connected, or the run broke
    3  DISQUALIFIED   tags were forced

`--json` writes the whole run: checks, per-carton ledger, measured timings,
every force. That file is the appeal record.

Honest limits are in docs/GRADING.md, and they grew rather than shrank when
this went from one scene to ten. The short version: headless Python models of
the plants rather than the 3D engine, so nothing here can jam or tip; faults
are injected and marked on five scenes -- the servo drive, the air receiver's
valve, and since IP-12 the sorting conveyor's drive, the dosing pump and the
oven's element -- while the other fault tags are declared and never raised;
and the operator contract itself -- the latching E-stop, Start that will not
clear it, Reset that starts nothing -- is marked on every scene, all eighteen
of which have a panel: the sorting line, the start / stop station and the
guarded cell in code of their own, and the other fifteen through one shared
sheet since IP-12 (`plant.OperatorExam`, `scenes/_contract.py`).

Where things are (IP-18):

    core.py        the run, the engine it owns, integrity, the report, the CLI
    lockstep.py    the reference controllers' scan loop, and `--lockstep`
    registry.py    finds the scenes and their reference controllers
    plant.py       what every plant model is built from
    scenes/        one module per scene: plant model, rubric, feedback, summary
    reference/     one module per scene: its built-in controllers

A graded scene is `scenes/<scene>.py` plus `reference/<scene>.py`, and nothing
else has to change for the grader to mark it. `core.py` names no scene.

`main(argv)` returns the exit code instead of exiting; `tools/grade.py` is a
shim over it, so that path keeps working for the docs, the tests and CI.
"""

from .core import main

__all__ = ["main"]
