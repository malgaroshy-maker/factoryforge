# FactoryForge — What v1.1 Needs

**Status:** IP-01 … IP-41. IP-02, IP-03, IP-06, IP-16, IP-18, IP-19, IP-21, IP-25, IP-07, IP-08 (Windows), IP-10, IP-26, IP-27, IP-09, IP-12, IP-14, IP-15, IP-20, IP-28, IP-29, IP-30, IP-31, IP-32, IP-33, IP-34, IP-35, IP-36, IP-37, IP-38, IP-40 and IP-41 done, IP-04 verified on CI, IP-17's parts done; IP-01 done; the rest open. Work paused on 2026-09-28: see [Where we stopped](#where-we-stopped--2026-09-28).
**Written:** 2026-09-22, against `5c2f26a`, after `HARDENING_PLAN.md` closed 53 of
its 56 items.
**Horizon:** the next release, v1.1. Nothing here is a v2 idea.

v1.0.0 shipped on 2026-09-21. The hardening plan did what it set out to do: the
editor no longer silently destroys work, the tag bus has server parity, the
drivers survive a rude peer, and a part costs one file. There are 38 parts, 11
scenes, and a grader that marks all ten graded scenes headlessly.

This plan comes from one review of the whole project on 2026-09-22 (no Codex
pass this time; see Appendix B). It found four things, and each of them sets the
shape of one phase:

**CI cannot be trusted to tell you what failed.** Eight of the last thirty runs
were red. Every run takes about 47 minutes because the 51 graded tests are
almost all of it. On the most recent red run (`35694688972`), B1 reported
`285 passed, 9 FAILED` and named one test. The "9" comes from
`re.search(r"(\d+) failed", out)` at `tools/test_plan.py:371`, which returns
the *first* match anywhere in pytest's output, not the summary line. That
output includes captured grader reports. So the nine failures this project spent
two CI cycles chasing were probably never nine. There was one real failure, a
test that failed on `master` and passed on the PR branch *with the same code*,
which makes it a flake.

**A student cannot get from the download to a graded program.** The release
zip contains the engine, the sidecar, docs and examples, but not the grader
(`dist/FactoryForge-windows.zip` has no `tools/`). `GETTING_STARTED.md`
mentions OpenPLC zero times, even though it is the only free, real IEC 61131-3
target and it is verified (HP-45). Only the sorting scene has a PLC program in
`examples/`; the other ten have Python reference controllers and nothing a
student can open in their own IDE. `GRADING.md` says it outright: *"Nobody has
graded a real student's program."*

**The grader and the engine are two simulations of one plant, and nothing
checks that they agree.** `tools/grade.py` (4631 lines) holds ten hand-written
Python plant models. Their constants are copied by hand from the templates,
and the comments say so: *"Numbers from `engine/templates/batch_dosing.json`"*
(`:2139`, also `:751`, `:1329`, `:1857`, `:2379`, `:2776`). Retune a pump in
the template and the grader keeps marking against the old pump. No test would
notice. This is the tag-bus parity problem again, one level up, and the tag
bus solved it with a shared fixture.

**Seven of the 38 parts appear in no scene, and one palette group has a single
part.** `TurnTable`, `VerticalLift`, `RetroreflectiveSensor`, `PivotDiverter`,
`CoolingFan`, `SelectorSwitch` and `TwoHandControl` ship in the palette and in
no template. `ELECTRICAL` has only `MotorStarter`. Every analog value reaches
the PLC as a float in engineering units, so scaling, the first thing a real
S7 or 4–20 mA loop teaches, cannot be practised at all.

---

## How to read a work item

```
IP-nn — one-line title
  Files:      what gets touched
  Done when:  the observable state that means it is finished
  Verify:     the command or the click that proves it
  Size:       S | M | L | XL
```

Sizes are as in `HARDENING_PLAN.md`: `S` under half a day, `M` one to two days,
`L` three to five, `XL` more than a week. As there, **phases are thematic**;
priority is the v1.1 gate in the Sequencing section.

---

## Phase 0 — Make the record true

The hardening plan's index said 54 of its 56 items were open, and its status
line said 25 were done. Git says 53 are done. A plan whose index is wrong
sends the next agent to redo finished work. That is the "silently wrong"
defect again, in the one document that exists to prevent it.

**IP-01 — Close the hardening plan honestly**
*Files:* `docs/HARDENING_PLAN.md`.
*Done when:* Appendix A marks each item done with its commit, the status line
agrees with the index, and the three items still open carry forward here:
HP-08's tautology (`SceneSelfTest.cs:416`, `Expect(true, "part dispatch
survives every output being driven")`) is IP-07, HP-44 is IP-05, and HP-46 is
IP-13.
*Verify:* every `done` row names a commit whose subject is that item's fix.
*Size:* S. Done alongside this plan, pending review.

**IP-02 — Take the counts out of the prose**
*Files:* `AGENTS.md`, `docs/GETTING_STARTED.md`, `docs/ROADMAP.md`,
`.github/workflows/test-plan.yml`, `tools/test_plan.py` (A6b).
*Done when:* no document states a test count, a part count or a scene count
that a check does not verify. Known stale claims today: `GETTING_STARTED.md:42`
and `AGENTS.md` ("73 pass"); `ROADMAP.md:67` and `:94–98` (OpenPLC cross-check
and MQTT, both shipped, still unchecked, and MQTT uses paho, not the
`aiomqtt` the roadmap names); `AGENTS.md`'s *Current state* and *Next steps*,
which end at M6 and say nothing is published; and the CI workflow's header,
which cites "five shipped scenes", an unverified Godot download that has run
thirty times, and the retired `FIX_PLAN.md`.
*Verify:* A6b fails when a count is edited to a wrong number in any of these
files.
*Size:* S.

---

## Phase 1 — CI that tells you what broke, fast

**IP-03 — B1 reads pytest's result, not pytest's prose** · gate
*Files:* `tools/test_plan.py` (`section_b`).
*Done when:* B1 runs pytest with `--junitxml` and takes passed, failed,
skipped and the failing test ids from the XML. It does not regex the console
output. If the XML is missing, B1 fails and says so; it never falls back to a
count.
*Verify:* add a throwaway test that fails with the message `"9 failed"`, and
confirm B1 reports **1** failure and names it. Today it would report 9.
Remove the test afterwards. (This line first said the old code would also
name nothing. It names the test in that case; its name regex failed on
classes and parametrized ids instead, which the new code handles.)
*Size:* S.

**IP-04 — Graded tests get their own job** · gate
*Files:* `.github/workflows/test-plan.yml`, `pytest.ini`,
`tests/test_grade.py`, `tools/test_plan.py`.
*Done when:* the graded tests carry a `graded` marker. The `test-plan` job
runs `-m "not graded"` and a parallel `grader` job runs `-m graded`, so the
fast signal (build, bus, drivers, self-tests) arrives in minutes rather than
47. Both jobs are required checks. (Decided on 2026-09-22: a separate job
running in parallel.)
*Verify:* a PR shows two checks; the non-graded one finishes in under 15
minutes; deliberately breaking a tag-bus test fails only the fast job.
*Size:* S.

**IP-05 — Run F and G on Linux** (was HP-44)
*Files:* `.github/workflows/test-plan.yml`, `tools/test_plan.py`.
*Done when:* `--only A,B,C,E,F,G,H` runs in CI. F is the engine/sidecar seam
and G covers robustness: engine kill, pause-force, corrupt file. HP-53 and
HP-54 removed the two known reasons these could not run on a shared runner
(fixed ports, and waits that could not tell a dead engine from a slow one).
*Verify:* a green run with F and G in the log, plus one run with a
deliberately killed sidecar that G reports.
*Size:* M.

**IP-06 — A graded test must not depend on how busy the machine is** · gate
*Files:* `tools/grade.py` (`run_scan`, `GradedEngine`), `tests/test_grade.py`.
*Done when:* graded tests that use *reference* controllers run the plant and
the controller in lockstep on the plant's clock. Each scan advances the plant
by exactly the scan period, and nothing reads the wall clock. Grading a real
student stays on wall-clock time, because a real PLC does not wait for us.
Today both halves run on their own wall-clock timers. Under load the scans
coarsen and the batch-dosing lesson moves, which is why
`test_a_batch_timed_in_seconds_delivers_half_when_the_pump_is_re_rated`
failed on `master` (`35694688972`) and passed on its PR run with the same
code.
*Verify:* run the graded suite 5× in WSL under `stress-ng --cpu $(nproc)` with
zero failures. Then reintroduce the old wall-clock scan and confirm the same
test fails under the same load (gotcha 24).
*Size:* M.

**IP-07 — The all-parts dispatch check asserts something** (was HP-08's
remainder)
*Files:* `engine/src/Sim/SceneSelfTest.cs:416`.
*Done when:* driving every output of every catalog part asserts a per-part
observable effect read from its own input tags or state, not
`Expect(true, …)`. `IPart` can carry an optional `ProbeEffect` so the test stays
generic and no part type is named outside its own file, which keeps HP-36's
check green.
*Verify:* stub out one part's `Tick` and confirm `--self-test=scene` fails
naming that part.
*Size:* L.

---

## Phase 2 — From download to graded program, with no licence

**IP-08 — Ship the grader in the release** · gate
*Files:* `sidecar/factoryforge_sidecar/__main__.py`, `tools/grade.py` (moved;
see IP-18), `tools/build_release.py`, `tools/packaging/check_release.py`,
`docs/GRADING.md`.
*Done when:* the frozen sidecar has a `grade` subcommand, so someone with only
the zip can run `factoryforge-sidecar grade --scene sorting-by-height`. The
release gate runs one graded scene, good and bad reference controllers,
against the frozen binary.
*Verify:* on a machine with no Python, unzip the release and grade the
sorting scene to PASS and FAIL.
*Size:* M.
*Found while doing IP-18:* two traps for this item. (1) `sidecar/__main__.py`'s
`demo` still reaches `harness/` through a checkout-relative path, so `demo` is
probably broken in the frozen sidecar today. That is from reading the code,
not from a run. It can import `factoryforge_sidecar.engine_stub` directly now.
(2) PyInstaller's `--collect-submodules factoryforge_sidecar` collects
*nothing* when the Python running it cannot import the package. That gives a
frozen grader with no scenes and a sidecar with no drivers. The registry now
raises "not collected" rather than listing zero scenes, and this item's gate
must grade a scene against the frozen binary, not only list scenes.
*Found while doing IP-19:* the grader now reads each scene's plant from
`engine/templates/` (`grading/templates.py`). It looks in `FACTORYFORGE_TEMPLATES`,
then `engine/templates` inside a PyInstaller bundle, then the checkout. The
exported engine keeps its templates inside the `.pck`, where Python cannot read
them, so the freeze needs `--add-data` for `engine/templates`. Outside a
checkout, without that, the grader's scene modules refuse to import and say
where they looked. The sidecar's drivers and CLI never import them.

**IP-09 — A no-licence first hour** · gate
*Files:* `docs/GETTING_STARTED.md`, `README.md`, `examples/openplc/`.
*Done when:* the guide's first path is *download → OpenPLC → a sorted
carton*. Siemens becomes the second path, not the only one, since
Getting Started never mentions OpenPLC today. The OpenPLC path uses the
release zip, not a clone.
*Verify:* follow it, word for word, in a clean WSL or VM with nothing
pre-installed, and note every step that had to be improvised. The item is done
when that list is empty.
*Size:* M.

**IP-10 — A starter program for every graded scene**
*Files:* `examples/openplc/<scene>/`, `examples/tia/<scene>/`, one mapping file
per scene and driver.
*Done when:* each of the ten graded scenes ships two things: an IEC ST
skeleton for OpenPLC and an SCL skeleton for TIA, with I/O declared and logic
empty; and the Modbus and OPC UA mapping files that wire them. No solutions:
the reference controllers stay Python, so the answer is not one `cp` away.
*Verify:* a new pytest parses each skeleton's variable list and asserts it
equals the tag set the scene declares, so a renamed tag breaks the skeleton
loudly.
*Size:* L.

**IP-11 — Grade a program nobody on the project wrote**
*Files:* `docs/GRADING.md`, `examples/graded/`.
*Done when:* `Sorting.st` on OpenPLC and `Sorting.scl` on PLCSIM Advanced are
each graded end to end through a real driver, and the JSON reports are
committed as examples. `GRADING.md`'s "nobody has graded a real student's
program" becomes a narrower, true sentence.
*Verify:* the committed reports. **Needs the user's PLCSIM: ask before
connecting to it.**
*Size:* M.

**IP-12 — Mark the operator contract and the fault, not only the product**
*Files:* `tools/grade.py` (rubrics), `tests/test_grade.py`, `docs/GRADING.md`.
*Done when:* at least three rubrics inject a fault mid-run and mark the
response: the sorting conveyor's drive fault, the dosing pump's fault, and the
oven's element failure. The E-stop / Start / Reset contract is marked on
every scene that has a `ButtonPanel`, not two of ten. The grader's own
docstring (`grade.py:55–58`) lists both gaps today.
*Verify:* each new check has a reference controller that ignores the fault and
fails for exactly that reason.
*Size:* L.

**IP-13 — The same TIA program over both drivers** (was HP-46)
*Files:* `examples/tia/`, `docs/GETTING_STARTED.md`.
*Done when:* unchanged `Sorting.scl` drives the scene through the OPC UA
driver and through the PLCSIM native driver, with the same split both times.
It is the last unmet PRD launch criterion.
*Verify:* two logged runs. **Needs the user's PLCSIM: ask first.**
*Size:* M.

---

## Phase 3 — Parts people will meet on a real line

**IP-14 — Three scenes for the seven parts with none** · gate for the parts
it covers
*Files:* `engine/templates/` (three new), `manifest.json`, `tools/grade.py`,
`tests/test_grade.py`.
*Done when:* every catalog part except `VerticalLift` (IP-15) is in a shipped,
graded scene:
- **Merge and divert**: `PivotDiverter`, `TurnTable` and
  `RetroreflectiveSensor`. Two lanes merge through a turntable and a pivot
  diverter splits them again, with retroreflective beams at the merge.
- **Press station**: `TwoHandControl`, `SelectorSwitch` and
  `PneumaticCylinder`. The selector chooses auto or manual, and manual needs
  two hands within 0.5 s. The lesson is the mode interlock.
- **Cooling tunnel**: `CoolingFan` with `HeatingStation`. Hold a product
  temperature against a varying load.

*Verify:* each scene's good controller passes and one wrong controller fails
for that scene's lesson; `--self-test=templates` loads all fourteen.
*Size:* L.

**IP-15 — A second level, so the lift has somewhere to go**
*Files:* `engine/src/Parts/PartLayout.cs`, `engine/src/Editor/SceneEditor.cs`,
scene format (`level` per part, version bump handled by HP-07's refusal),
`engine/src/Parts/VerticalLift.cs`, one new template.
*Done when:* a part can sit on an elevated work plane. This is a per-part
`level` whose origin is `WorkPlaneY + level × LevelHeight`, so AGENTS.md's
"origin on the work plane" still holds per level. A lift carries cartons
between level 0 and level 1 in a shipped scene.
*Verify:* `--self-test=scene` round-trips a level-1 part; an older build
refuses the new file rather than flattening it.
*Size:* L.

**IP-16 — Raw analog, the way a PLC actually sees it**
*Files:* `engine/src/Parts/PartTagBuilder.cs`, analog parts (`LevelTank`,
`HeatingStation`, `FlowMeter`, `WeighingConveyor`, `MotorStarter.current`),
`PartSettings`, `docs/tag-bus.md`.
*Done when:* each analog input has a per-part `signal` setting:
`engineering` (today's float, the default), `s7_raw` (INT 0–27648), or
`ma_4_20` (INT raw with an underrange below 3.6 mA, set by an injectable wire
break). A student practises `NORM_X` / `SCALE_X` against a tag that looks
like the real card.
*Verify:* a self-test sets each mode and asserts the raw value at 0 %, 50 %,
100 % and on wire break; `check_protocol.py` covers the INT on both engines.
*Size:* M.

**IP-17 — Five industrial parts, electrical and mechanical**
*Files:* one file per part under `engine/src/Parts/`, `PartCatalog.cs`,
`--self-test=newparts`-style behaviour self-test.
*Done when:* each part lands in one file plus one catalog entry (HP-34's
contract), each is placed in at least one scene, and each is asserted by its
effect:
- **Limit switch** (roller lever, NO/NC contacts, mechanical bounce as a
  setting). The most common input on any real machine, and the palette has none.
- **Solenoid valve** with open and closed position feedback and a *stuck*
  fault. It teaches discrepancy monitoring, command versus feedback.
- **Star-delta starter**: three contactors with the changeover timer in the
  PLC. This is the classic exam circuit, and energising star and delta
  together trips the supply.
- **Pressure transmitter** on a pneumatic header, raw analog only (uses
  IP-16).
- **Servo axis**: enable / ready / fault / ack / position / in-position, a
  PLCopen-shaped subset. This also unblocks the palletiser's `AlternateLayers`
  pattern, which needs a fourth axis.

*Verify:* each part's self-test fails when its core behaviour is stubbed out
(gotcha 24).
*Size:* XL total, M per part. The five are independent of one another, so
this item parallelises well.

---

## Phase 4 — One plant, one place

**IP-18 — `grade.py` becomes a package, one file per scene**
*Files:* `tools/grade.py` → `sidecar/factoryforge_sidecar/grading/`
(`core.py`, `scenes/<scene>.py`, `reference/<scene>.py`), `tests/test_grade.py`.
*Done when:* adding a graded scene is one new scene file plus one reference
file, with no edits to a shared file, which is the same bar HP-34 set for
parts. Moving it into the sidecar package is also what lets IP-08 freeze it.
*Verify:* the graded suite passes unchanged, and a test fails if `core.py`
names a scene.
*Size:* L. Do it before IP-12 and IP-14 add three more scenes to a 4631-line
file.

**IP-19 — The grader's plant is read from the template, and checked against
the engine** · gate
*Files:* `grading/scenes/*.py`, `engine/src/Main.cs` (a `--dump-tags` flag),
`tests/test_grade.py`.
*Done when:* every constant a grader scene takes from a template (belt speed,
deck length, pump rating, tank area and so on) is loaded from that template's
JSON, not retyped. Each grader scene's tag table (ids, types, kinds) must
equal the tag set the headless engine registers for the same template, and a
test asserts it.
*Verify:* change a pump's rating in `batch_dosing.json` and confirm the
grader's expectation moves with it. Rename a tag in a template and confirm the
test fails.
*Size:* M.

**IP-20 — The grader's reference controllers pass on the 3D engine too**
*Files:* `tools/try_scene.py`, `grading/reference/`.
*Done when:* `try_scene.py` drives the headless Godot engine with the grader's
reference controllers rather than keeping its own. Today it has a separate
3281-line set of scene solvers. Each `good` controller must complete its scene
on the real engine. This is the behavioural half of IP-19, since the tag sets
can agree while the physics does not.
*Verify:* section H runs all graded scenes this way; reintroducing a
deliberate mismatch, such as a slower belt in the template only, fails H.
*Size:* L.

**IP-21 — Split `SceneEditor.cs` along the lines it already has**
*Files:* `engine/src/Editor/SceneEditor.cs` (3047 lines, 148 members) →
`Editor/Commands/*.cs`: the seven nested command classes
(`PartCommand :660`, `CompositeCommand :1031`, `MoveCommand :1064`,
`DuplicateGroupCommand :2411`, `DuplicateCommand :2552`,
`RotateCommand :2608`, `ClearSceneCommand :2637`), plus the box select
(`:891`) and the clipboard.
*Done when:* a pure move with no behaviour change, and `SceneEditor.cs` under
1500 lines.
*Verify:* all 30 release self-tests pass, and the build is clean at zero
warnings.
*Size:* M.

---

## Phase 5 — Ship it

**IP-22 — Every release self-test meets the exported binary**
*Files:* `tools/packaging/check_release.py`.
*Done when:* all 30 `SELF_TESTS` plus IP-08's graded run pass against both
exported platforms. `controlparts`, `editorkeys` and `handlingparts` were
added after v1.0.0's gate ran and have never met a binary.
*Size:* S.

**IP-23 — v1.1.0**
*Done when:* the gate below is closed, `release.yml` publishes v1.1.0 from the
tag, and the release notes list the new scenes, parts and the grader.
*Size:* S.

**IP-24 — Hand-over**
*Files:* `AGENTS.md`, this file.
*Done when:* `AGENTS.md`'s *Current state* describes v1.1 as it is, not M6
as it was, and this plan's index is closed the way IP-01 closed the last one.
*Size:* S.

## Added by implementing it — IP-25 … IP-27

Found by IP-06's agent while it reproduced the batch-dosing flake.

**IP-25 — The graded plant starts when the controller connects** · gate
*Files:* `tools/grade.py` (`GradedEngine`, the run's start), `tests/test_grade.py`.
*Done when:* on the wall-clock path a real student uses, the plant and the
graded window start when the controller's sidecar has connected and been
described to. Today they start when the engine starts. In IP-06's experiment
a controller that connected 8 s late into a 20 s window was graded on 12 s,
and its first batch scored 0.0 L. Nothing in the report said why.
*Verify:* a test that connects a `good` reference controller several seconds
after the grader starts, on the wall clock, gets PASS. Moving the start back
to engine start makes that test fail.
*Size:* S.

**IP-26 — A tag-bus coalescing test that flakes on its own**
*Files:* `tests/test_tagbus.py::test_updates_coalesce_behind_a_slow_push`.
*Done when:* the cause is known and fixed, or the test waits on an event
rather than on elapsed time (gotcha 2). It failed 1 of 30 runs on unmodified
`5c2f26a` on Windows, so it is not caused by IP-06.
*Verify:* 100 consecutive passes, and the fixed flaw reintroduced fails.
*Size:* S.

**IP-27 — Re-measure `GRADING.md`'s wrong-controller table**
*Files:* `docs/GRADING.md`.
*Done when:* every number in the table (for example, "22.1 L and then 11.0 L")
is from a lockstep run, which is now reproducible, with the command that
produced it. Ideally a test regenerates it or asserts it (the IP-02 approach).
*Size:* S.

**IP-28 — Raw analog for outputs and the remaining inputs**
*Files:* the parts below, `engine/src/Parts/AnalogSignal.cs`, `docs/tag-bus.md`.
*Done when:* analog *outputs* can take raw counts too: tank fill and drain,
heater power, pump and fan speed, VFD speed reference, gantry target and gauge
value. So can the inputs IP-16 left in engineering units: `DosingPump.flow`,
`CoolingFan.airflow`, `VariableConveyor.actual`, `PickPlaceArm.position`,
`RotaryEncoder.rate`, the height readings on `LightArray`, `VerticalLift` and
`ArticulatedArm`, and possibly `ButtonPanel.setpoint`. Also settle the OPC UA
client writing int tags as Int32: a real `%IW` channel is an S7 `Int`, and a
student's PLC variable should be able to be one. `tag-bus.md` documents that
mismatch today; it does not fix it.
*Verify:* `--self-test=analog` extended to every channel added. The OPC UA
test writes a raw count into an `Int` node on the test server.
*Size:* M.

**IP-29 — The grader marks the plant the engine runs** · gate
*Files:* `sidecar/factoryforge_sidecar/grading/**`, `tests/test_grade.py`,
`tests/test_grade_templates.py`, `docs/GRADING.md`.
*Found by IP-19:* 16 part positions in 7 of the 9 templated scenes disagree
between the grader's model and the template. For example, the guarded cell's
mute eye is at 1.35 m in the model and 1.0 m in the template, and the
accumulation blade is at 3.0 against 2.6. There are also six behavioural
differences. The batch-dosing model ignores `tank.fill`. The guarded cell's
examiner opens a gate the program has locked, which the engine forbids. The
pots start at 0 rather than at the template's value. Pick-and-place barcode
codes are shuffled, where the engine derives them from the carton. The
accumulation outfeed ignores its own drive. And the docs say the gantry
"halves" when it drops to 0.4×. The engine uses the template value in every
case. **Decided on 2026-09-23: the grader adopts the engine's values
everywhere.**
*Done when:* `tests/test_grade_templates.py` pins no disagreement. Every
scene's `good` reference passes, and every wrong reference fails for its own
lesson. Exam steps are retuned or redesigned within the engine's rules where
geometry or behaviour moved. `GRADING.md`'s table is re-measured.
*Verify:* before/after tables of verdicts and failed check ids per reference.
Reverting one position, the locked guard and the fill valve each fails a test.
*Size:* L.

**IP-30 — The window opens when the student's driver is up**
*Files:* `sidecar/factoryforge_sidecar/__main__.py` (`connect`), `tagbus.py`,
`docs/tag-bus.md`, `grading/core.py`, `engine_stub.py` and
`TagBusServer.cs` if the protocol gains a message.
*Found by IP-25:* the graded window now opens on `describe`, but `connect`
starts its driver only after `describe` arrives. However long an OPC UA or S7
driver takes to reach the PLC therefore comes out of the window. Eight of the
ten scenes press Start at 1.0 s, for 0.15 s, so a driver that needs more than
about a second could miss the first Start. The engine cannot see this through
today's protocol.
*Done when:* the sidecar tells the engine when its driver is connected (a
status message, added to the tag-bus contract on both engines), and the grader
opens the window on it, falling back to `describe` for a controller that never
sends it.
*Verify:* a test with a driver that takes 3 s to connect still sees the first
Start. Opening on `describe` makes that test fail. Parity passes on both
engines.
*Size:* M.

**IP-31 — A click on a momentary button must outlive the slowest poll** · gate
*Files:* `engine/src/Parts/ButtonPanel.cs`, `SceneEditor.StepPanelButtons`,
possibly `BarcodeScanner.cs`, AGENTS.md ("Momentary means one scan").
*Found by IP-10, from reading the code (ButtonPanel.cs, BarcodeScanner.cs,
grading/plant.py), not from a run:* a Start/Stop/Reset click in the 3D engine
raises the tag for exactly one physics tick, about 17 ms at 60 Hz. OpenPLC's
Modbus master polls every 50 ms, and the OPC UA driver polls too (gotcha 1),
so a click can come and go between two polls, and the PLC never sees it. The
grader holds each press for 0.15 s and does not have this problem. So a
program can pass the grader and still ignore a real click in the 3D scene.
`scanner.read` has the same shape.
*Done when:* a press stays high for at least a minimum hold. The value should
be long enough for the slowest supported poll, for example 200 ms, and a
panel setting. It must still give exactly one clean rising edge per click,
however long the mouse is held or however fast it is clicked. AGENTS.md's
"one scan, not one mouse-down" rule is restated as "one edge, held long
enough to be seen".
*Verify:* reproduce first. Drive the engine through the Modbus driver with a
50 ms poller and count missed clicks before the fix. Then `--self-test=buttons`
and `click` assert the hold and the single edge, and shortening the hold back
to one tick fails them.
*Size:* M.

## Added by IP-14 — IP-32 … IP-34

**IP-32 — The pivot diverter's blade must physically divert** · gate
*Files:* `engine/src/Parts/PivotDiverter.cs`, `--self-test=newparts`, the
IP-07 probe for it.
*Found by IP-14 in the real engine:* held at `divert`, the part reports
`diverted` true, but its blade's collider stays parked. No carton was
diverted in any mounting tried, and beams either side of the post never see
the blade. The suspected cause is a rotation applied to the parent of an
`AnimatableBody3D` with `SyncToPhysics`; that is not confirmed. The IP-07 probe
passes because it asserts the contact, not the blade. That is the gotcha-16
shape again: the part reports work it did not do.
*Done when:* a carton on a running belt is deflected into the side lane with
the blade at `divert` and carried past with it at `home`, on real physics. The
probe asserts the blade's collider moved, not only the contact. A graded scene
uses the part (the merge-and-divert scene IP-14 could not build).
*Verify:* the new check fails on today's code before the fix.
*Size:* S (M if the scene is included).

**IP-33 — A turntable that can take a carton off a belt**
*Files:* `engine/src/Parts/TurnTable.cs`.
*Found by IP-14:* the deck has no drive of its own. A belt-fed carton stops at
the belt/deck joint, and the queue behind it jams. IP-14's rotary-index scene
works around this by dropping cartons onto the deck and pushing them off. A
real transfer turntable has rollers or a belt on its deck. Add a deck-drive
output, and a scene where a belt feeds the table.
*Size:* M.

**IP-34 — The grader's panel press matches the engine's hold**
*Files:* `grading/plant.py` (`Panel.PRESS`, 0.15 s).
*Why:* IP-31 made the engine hold a press for 0.2 s. The grader's examiner
still presses for 0.15 s. Both are well above a 50 ms poll, but the grader
marks the plant the engine runs (IP-29). Derive it from
`ButtonPanel.DefaultPressHold` via `CSHARP_MIRRORS`, and re-run the verdict
matrix and the IP-27 table.
*Size:* S.

## Added by IP-09 — IP-35 … IP-39

Found by following the new first-hour guide from the release zip alone.

**IP-35 — The sorting grader presses the Start its brief promises** · gate
The sorting-by-height brief says the mushroom stops the line and Start alone
will not restart it, but its grader never presses Start. A program written to
the brief, which waits for Start, never runs its belt under the grader. The
guide's program ignores Start and explains why, and that is teaching the
wrong habit to fit the grader. The rubric should press Start and mark the
E-stop / Reset / Start contract, as IP-12 asks for every panel scene.

**IP-36 — Every command a release user is told to run exists in the release** · gate
`tools/gen_starters.py` writes `python tools/grade.py` into `examples/README.md`
and every starter README. The grader prints `factoryforge-sidecar connect`
without the `.\` that PowerShell needs to run a program from the current
folder. Print and generate the form that runs from the extracted folder on
each OS. Add a test that searches the release's text for `python ` and
`tools/` commands.

**IP-37 — The F5 dialog can serve Modbus to a PLC that is not on loopback**
The F5 dialog's Modbus option has no host or port fields and always serves
`127.0.0.1:502`, which OpenPLC in WSL cannot reach. Add both fields (the
HP-22 default stays loopback, and binding wider is an explicit choice).

**IP-38 — The release carries the docs its guide links to**
`build_release.py` does not ship `docs/OPENPLC.md` or `docs/GRADING.md`, so
links to them inside the zip are dead. Ship every doc the shipped docs link
to, and have the release gate check that no link inside the zip is dead.

**IP-39 — Verify OpenPLC in WSL reaching the Windows sidecar** · gate
Steps 4–6 of the first hour were not run end to end, because allowing the
sidecar through the Windows firewall is the user's decision. The OpenPLC runs
used the Linux layout on loopback instead. **Needs the user:** allow the
firewall prompt once, then follow the guide exactly.

---

## Sequencing

### The v1.1 gate

| Item | Why it gates |
|---|---|
| IP-03 | CI misreports how many tests failed |
| IP-04 | a 47-minute signal is a signal nobody waits for |
| IP-06 | a load-dependent flake trains everyone to re-run red CI |
| IP-08, IP-09 | a release a student cannot get graded with, or cannot use without a Siemens licence, misses the PRD's audience |
| IP-19 | the grader can mark against a plant that is not the one in the scene |
| IP-14 | seven parts in the palette that no scene uses (gates only the parts it ships) |
| IP-22 | three self-tests have never met a binary |
| IP-25 | a student who connects late is graded on a shorter run, and can score zero |
| IP-29 | the grader marks a plant laid out differently from the one the student sees |
| IP-31 | a student's Start click in the 3D engine can be lost before their PLC sees it |
| IP-32 | a shipped part whose `diverted` contact says it diverted while nothing moved |

### Order

1. **IP-01, IP-02, IP-03**: small, and they make everything after them
   legible.
2. **IP-04, IP-06**: fast and trustworthy CI before anything adds more graded
   tests.
3. **IP-18, then IP-19**: restructure the grader before it grows. IP-12 and
   IP-14 add scenes and both depend on this.
4. **In parallel, by file ownership** (the same partitioning that let this
   session's subagents merge without conflict):
   - parts: IP-16 → IP-17
   - scenes: IP-14, then IP-15
   - student path: IP-08, IP-09, IP-10
   - editor: IP-21 (touches only `SceneEditor.cs`; schedule it apart from
     IP-15, which edits the same file)
5. **IP-05, IP-07, IP-12, IP-20**: widening coverage once the ground is
   stable.
6. **IP-11, IP-13**: need the user's PLCSIM, so they run whenever the user is
   available, not on the critical path.
7. **IP-22 → IP-23 → IP-24.**

### Deliberately not in v1.1

- **Grading against the live 3D engine.** It is the obvious next step and the
  real fix for the two-simulations problem. But several rubrics read plant
  state that has no tag (carton ledgers, escape positions), and Jolt is not
  reproducible. IP-19 and IP-20 close most of the risk for a fraction of the
  cost. Revisit it with their results in hand.
- **In-engine "check my program" button.** It depends on the item above.
- **New drivers.** Seven exist; none has had an outside user yet.

---

## Where we stopped — 2026-09-28

Work paused here at the user's request. `master` is at the handover commit;
everything below was verified on this machine (Windows, Godot 4.7.2-mono) on
that day.

### Done in this session

IP-02, 03, 06, 07, 08 (Windows), 09, 10, 14, 15, 16, 17 (parts), 18, 19, 20,
21, 25–36, 38 and 40 — commits in Appendix A. IP-04 is verified: the
first push of this work (CI run 36447686787) ran the graded tests as their
own job, and both jobs passed. The last full evidence: section H **19 passed, 0 failed** (1214 s, every scene
driven by the grader's own reference, mezzanine-lift included), sections A
and B **9 passed** (pytest 782). The OpenPLC first-hour program was run on a
real OpenPLC runtime and found gotcha 25 (`AGENTS.md`).

### Needs the user

- **IP-39** — allow the sidecar through the Windows firewall once, then
  follow the first-hour guide from WSL exactly.
- **IP-11** — the Siemens half was done on 2026-09-29 with the user's
  permission (TIA project `FactoryForge_Sorting`, PLCSIM instance `test`).
  Left: IP-11's OpenPLC half, and a program a student wrote. IP-13 is done.

### Left, no one else needed

- **IP-05** — run sections F and G on Linux CI.
- **IP-22 → IP-23 → IP-24** — every release self-test against the exported
  binary (Windows and Linux), tag `v1.1.0`, hand over.
- Follow-ups found on the way: the grader does not model the turntable's
  `table.deck` output, and no belt-fed turntable scene exists (IP-33); the
  pivot-divert scene still carries its `lip_drop` 0.04 workaround (IP-40);
  IP-08's Linux archive has not been built.

---

## Appendix A — work item index

| Item | Title | Phase | Size | Gate | Status |
|---|---|---|---|---|---|
| IP-01 | Close the hardening plan honestly | 0 | S |  | **done** 2026-09-28: all 53 done rows name a commit that exists and fixes that item (shared commits name it in the body; HP-09 names the `v1.0.0` tag) |
| IP-02 | Take the counts out of the prose | 0 | S |  | **done** ee4ede9 |
| IP-03 | B1 reads pytest's result, not its prose | 1 | S | ● | **done** 609e07b |
| IP-04 | Graded tests get their own job | 1 | S | ● | **done** d1ac12d; CI run 36447686787: test-plan 27 min and grader 23 min in parallel, both green |
| IP-05 | Run F and G on Linux (HP-44) | 1 | M |  | code done (see git log); CI unverified until a green run shows F and G in the log |
| IP-06 | A graded test must not depend on machine load | 1 | M | ● | **done** e64157b, 01bd35c |
| IP-07 | The all-parts dispatch check asserts something (HP-08) | 1 | L |  | **done** e85df0c, 7dd30bc |
| IP-08 | Ship the grader in the release | 2 | M | ● | **done** on Windows (9e8d0f8, 77991fb); Linux archive unverified until release.yml runs |
| IP-09 | A no-licence first hour | 2 | M | ● | **done** 0078282, except the WSL-to-Windows firewall crossing (IP-39) |
| IP-10 | A starter program for every graded scene | 2 | L |  | **done** (see git log for gen_starters) |
| IP-11 | Grade a program nobody on the project wrote | 2 | M |  | partly done 2026-09-29: Siemens half -- `Sorting.scl` FAIL 10/15 (panel and fault checks only), the first-hour program in SCL PASS 15/15 at seeds 1-3, on PLCSIM through the native driver (`examples/graded/`); OpenPLC half open; no student-written program yet |
| IP-12 | Mark the operator contract and the fault | 2 | L |  | **done** ae3c44e (faults on sorting, dosing, heat-treat), 3a6a494 (E-stop / Start / Reset on all 18 panel scenes) |
| IP-13 | The same TIA program over both drivers (HP-46) | 2 | M |  | **done** 2026-09-29: unchanged `Sorting.scl` v0.4 on an S7-1500 in PLCSIM Advanced, 300 s each, `demo`: native driver (Softbus) **48 tall / 48 short**, OPC UA client (TCP/IP, `opc.tcp://192.168.0.20:4840`) **48 tall / 48 short**, no misroutes either way. A shorter native run, 120 s, gave 18 / 20: one tall carton counted short at 81 s |
| IP-14 | Three scenes for the seven parts with none | 3 | L | ● | **done** 1c3f031..bac0b06: six scenes, 10 of 11 parts; PivotDiverter is IP-32 |
| IP-15 | A second level, so the lift has somewhere to go | 3 | L |  | **done** ba6a72a, 728d65c |
| IP-16 | Raw analog, the way a PLC sees it | 3 | M |  | **done** ca39193, ffe1aba (inputs; outputs are IP-28) |
| IP-17 | Five industrial parts | 3 | XL |  | parts **done** 07fe152..67fc1f0; placing them in scenes is with IP-14 |
| IP-18 | `grade.py` becomes a package | 4 | L |  | **done** 8d4c395, 38660a3, 48f2691 |
| IP-19 | The grader's plant is read from the template | 4 | M | ● | **done** 02be83a, 568aaa0; its findings are IP-29 |
| IP-20 | Reference controllers pass on the 3D engine | 4 | L |  | **done** 061bb11, d79f09a; H 19/19 |
| IP-21 | Split `SceneEditor.cs` | 4 | M |  | **done** f032379, fc7fe89 |
| IP-22 | Every release self-test meets the binary | 5 | S | ● | open |
| IP-23 | v1.1.0 | 5 | S |  | open |
| IP-24 | Hand-over | 5 | S |  | open |
| IP-25 | The graded plant starts when the controller connects | 2 | S | ● | **done** 184422a |
| IP-26 | A tag-bus coalescing test that flakes on its own | 1 | S |  | **done** 6023e1f (a test bug) |
| IP-27 | Re-measure GRADING.md's wrong-controller table | 2 | S |  | **done** 963d7d3 |
| IP-28 | Raw analog for outputs and the remaining inputs | 3 | M |  | **done** 950230a, 6fb594c; one unexplained OPC UA test failure seen once by its agent, 0 in 15 later file runs |
| IP-29 | The grader marks the plant the engine runs | 4 | L | ● | **done** 326248e..c740af5 |
| IP-30 | The window opens when the student's driver is up | 2 | M |  | **done** ddc74dd, 6a0887b |
| IP-31 | A click on a momentary button must outlive the slowest poll | 3 | M | ● | **done** b808bdd, 15c57b1 (76 % of clicks missed at a 50 ms poll before, 0 after) |
| IP-32 | The pivot diverter's blade must physically divert | 3 | S | ● | **done** 885df29, 85c0bc6 (plus the pivot-divert scene) |
| IP-33 | A turntable that can take a carton off a belt | 3 | M |  | **done** e132303 (part and real-physics test; no belt-fed scene yet, and the grader model ignores table.deck) |
| IP-34 | The grader's panel press matches the engine's hold | 2 | S |  | **done** 89b184a |
| IP-35 | The sorting grader presses the Start its brief promises | 2 | S | ● | **done** 2066cbd..814ad98, 0668cfc; run on a real OpenPLC: step 8 PASS 12/12 (found gotcha 25) |
| IP-36 | Every command a release user is told to run exists in the release | 2 | S | ● | **done** (gate checks every shipped text file) |
| IP-37 | The F5 dialog can serve Modbus to a PLC that is not on loopback | 5 | M |  | **done** 1afd2c4 (host and port fields, 127.0.0.1:502 by default; `--self-test=sidecar` fails if the chosen bind does not reach the command or a bad port is taken) |
| IP-38 | The release carries the docs its guide links to | 5 | S |  | **done** (gate checks 322 relative links) |
| IP-39 | Verify OpenPLC in WSL reaching the Windows sidecar | 2 | S | ● | open (needs the user's firewall decision) |
| IP-41 | Starters warn that OpenPLC's first scans read every input FALSE | 2 | S |  | **done** dda69f5 (every OpenPLC starter's README and `.st` carry the warning and the `EstopSeen` pattern; `test_examples.py` fails without them) |
| IP-40 | The chute's lip must not stand proud of the belt | 3 | S |  | **done** 768fce8 (pivot-divert still carries its lip_drop 0.04 workaround; its grader requires it) |

## Appendix B — where the findings came from

One review on 2026-09-22 against `5c2f26a`, by reading code, commit history,
CI logs (`gh run view 35694688972`, `35698579275`) and the v1.0.0 archive.
The user chose a single reviewer this time rather than a Codex pass. The
first plan's Appendix C recorded four wrong claims that a second reviewer
caught, so treat the claims here as one reviewer's findings. Each claim cites
the line, run or file it rests on, so it can be checked rather than trusted.

Two claims here are inferences, not observations, and are labelled as such
where they appear:

- *"The nine failures were probably never nine."* The regex takes the first
  match, and the run named one test. What text actually produced "9" cannot be
  recovered, because CI does not keep pytest's captured output. IP-03 makes
  the question unnecessary rather than answering it.
- *"IP-06's flake is scan coarsening under load."* That is consistent with the
  earlier `dt` fix and with the failure following load rather than code, but
  it is not reproduced. IP-06's *Verify* is written to reproduce it first.
