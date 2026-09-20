# FactoryForge — First-Run, Manual Operation & Scene-Exercise Plan

**Status:** **done — all 46 items, all seven phases.** Phase 0 (UX-01…UX-09)
landed on 2026-08-23, last rather than first: a release is worth cutting only
once there is something worth downloading, which was the caveat §3 stated when
the plan was written.
**Written:** 2026-08-22, against `9ac37d2`.
**Last reviewed:** 2026-08-23, against `3679bbb`, on **Godot 4.7.2-mono** (the
project asks `project.godot` only for feature `4.7`, so 4.7.1 and 4.7.2 both run
it). `python -m pytest -q` — 71 passed; `tools/test_plan.py --only A,B,C,E,G,H`
— 41 passed, 0 failed, 342s. §5 was written before Phase 5 landed and has been
reconciled with what now ships (new §5.7 carries the current state); §0 and §2
are left as the findings they were, dated and line-referenced to `9ac37d2`.
**Work items:** UX-01 … UX-46, indexed in [Appendix A](#appendix-a--work-item-index).

A plan for three things that turn out to be one problem:

1. **Ease of use for someone who just installed this.** Not "is the feature
   there" — it mostly is — but "does the first twenty minutes work without the
   author sitting next to you".
2. **Being able to operate any component by hand** — click a conveyor and turn
   it on, stroke a pusher, open a valve — so you can see what a part does before
   you write a line of PLC code.
3. **A runnable exercise for every shipped scene**, so you can watch each one
   work, and check your own build, before going anywhere near a PLC.

They are one problem because the app's answer to "show me it works" is a single
scene driven by a single button, while the start screen offers five scenes, two
modes and fifteen parts.

Ahead of all of it sits **Phase 0: ship a binary**, so that "installed the
software" stops meaning "installed a game engine and a compiler first".

---

## 0. How this was evaluated

Not by reading the docs and agreeing with them. Every claim below was checked
against a running build:

* Launched all five shipped scenes and read the `describe` message off the tag
  bus directly, to get the true tag id, type and direction for each.
* Ran every scene headless *and* windowed, and compared the two.
* Traced Edit/Run mode, the Tag Inspector and the part property panel through
  the source to establish exactly what a click, a Force and a selection reach.
* Confirmed parts act on forced values (`SceneEditor.cs:1220` onward reads
  `Tags.TryGetVisible`), which is what makes §5 a specification rather than a
  guess.
* **Ran the Phase 1 spike** (below) and reverted it.
* Ran `python tools/test_plan.py --only A,B,C,E` — 14 passed.

### The spike result — Phase 1 is much smaller than it looked

The plan's largest unknown was whether headless `--scene=` loading needs
renderer surgery. It does not.

`BuildHeadlessPhysicsParts` (`Main.cs:382`) **already** constructs a working
`SceneEditor` headless, with no `VoxelGrid`, no inspectors and no cameras. A
six-line change to call `editor.LoadTemplate(_scenePath)` there instead of
`RegisterDefaultSceneParts` was enough. Measured, with the patch applied and
then reverted:

| Template | Headless scene name | Tags | Matches windowed? |
|---|---|---:|---|
| `start_stop_station` | `start-stop-station` | 14 | yes |
| `tank_level_control` | `tank-level-control` | 13 | yes |
| `light_curtain_sorting` | `light-curtain-sorting` | 15 | yes |
| `roller_line_weighing` | `roller-line-weighing` | 13 | yes |

No errors, no exceptions, and every tag set identical to the windowed run.

Then the harder question — does a template actually *simulate* headless? Driving
`start-stop-station` over the bus for 22 seconds (force `belt.rotate`, pulse
`emitter.emit`) gave **`counter.count = 6`**. Boxes emitted, rode the belt,
tripped the photoelectric sensor and were counted, with no renderer present.

**Consequences.** UX-10 drops from "spike of unknown size" to **S**. Phase 2's
exercises can run in Linux CI rather than needing a display. The risk note that
used to sit on Phase 1 is gone, and open decision 1 in §6 is closed.

Three findings below (§2.1, §2.3, §2.7) contradict what the code's own comments
or its UI claim, which is why each carries its evidence.

---

## 1. The five ready-to-use scenes

Verified by connecting to a live engine and reading `describe`. When this was
written the table existed nowhere in the repo, which was itself part of the
problem; UX-13 put the id/title/blurb half of it in
`engine/templates/manifest.json`, and UX-42 put the tag half in
`engine/fixtures/scene_tag_sets.json`, where a self-test now checks it every run.

| Scene | `scene` id | Tags | The I/O that makes it different |
|---|---|---:|---|
| **Sorting by height** (built-in) | `sorting-by-height` | 16 | `conveyor.rotate` · `emitter.emit` · `sensor_low.detect` · `sensor_high.detect` · `pusher.extend`/`.extended`/`.retracted` · `counter.tall`/`.short` · `stack_light.green` |
| **Start / stop station** | `start-stop-station` | 14 | `belt.rotate` · `part_present.detect` · `counter.count` · `produced.value` · `tower.red`/`.yellow`/`.green` |
| **Tank level control** | `tank-level-control` | 13 | `tank.fill` (float out) · `tank.drain` (float out) · `tank.level` (float in) · `level_readout.value` |
| **Light curtain sorting** | `light-curtain-sorting` | 15 | `height_gauge.height` (float in) · `height_gauge.blocked` · `diverter.extend`/`.extended`/`.retracted` · `tall_count.count` · `short_count.count` |
| **Roller line with weighing** | `roller-line-weighing` | 13 | `infeed.rotate` · `scale.rotate` · `scale.weight` (int in) · `metal_check.detect` · `weight_readout.value` · `outfeed.count` |

All five carry the six `panel.*` tags (`start`/`stop`/`reset`/`estop` in,
`green`/`red` out). Note that the four templates use **`belt.rotate`**, not the
built-in scene's `conveyor.rotate` — that one difference is the root of §2.1.

---

## 2. Findings

### 2.1 Four of the five scenes have nothing that can run them — blocker

Every runnable artifact in the repository targets `sorting-by-height` and only
it:

| Artifact | Writes |
|---|---|
| `engine/src/Sim/DemoDriver.cs` — the "Watch it run" button | `conveyor.rotate`, `emitter.emit`, `pusher.extend`, `stack_light.green` |
| `tools/live_driver.py` — what `run_factoryforge.bat` auto-starts | same |
| `tools/drive_engine.py` — the determinism contract | same |
| `examples/fake_plc.py` + `examples/tia/Sorting.scl` | same |
| `examples/opcua_mapping.json`, `plcsim_mapping.json`, `snap7_mapping.json` | same |
| `examples/nodered/factoryforge-flow.json` | same |

None of those tag ids exist in any template. `DemoDriver` writes through
`SetIfPresent` (`DemoDriver.cs:108`), which silently skips a tag that is not in
the table, so on a template **every write is a no-op**.

The failure mode is worse than "nothing happens". `Start()` sets `Active = true`
regardless, and `SceneToolbarUI` renders that state, so the button turns green
and reads **⏹ Demo** while the factory sits perfectly still. It reports running
while doing nothing — the same class of dishonesty FF-06 and FF-23 were about.

The highest-value finding here. The start screen devotes its entire left column
to the four templates and its single loudest button to "Watch it run"; the two
do not work together.

*Fixed by:* UX-16 … UX-20.

### 2.2 `run_factoryforge.bat` fights the app it launches

* **Force-kills whatever holds port 7411** via `Stop-Process -Force`, with no
  prompt and no check that it is even a FactoryForge process.
* Falls back to `D:\Godot_v4.7.1-stable_mono_win64\...` — the author's own
  machine path — when Godot is not on `PATH`.
* Auto-starts `tools/live_driver.py` four seconds after launch. That connects a
  real client, and `DemoDriver` stands down the instant `Bus.HasClient` is true.
  **Launching the documented way disables the "Watch it run" button**, and the
  Python driver started in its place only drives one of the five scenes.

*Fixed by:* UX-23, UX-27, UX-28.

### 2.3 `--scene=` is silently ignored headless

Verified: `godot --headless --path engine -- --scene=res://templates/tank_level_control.json`
reports `scene 'sorting-by-height', 16 tags`. So do all four templates.

Cause: both `--scene=` and `--demo` are handled inside `BuildView`
(`Main.cs:309`), and `Main.cs:105` skips `BuildView` entirely when
`DisplayServer.GetName() == "headless"`. The flags parse fine, nothing consumes
them, and nothing warns.

Two more consequences of the same block:

* `--scene=` and `--demo` are an `if` / `else if`, so `--scene=X --demo` loads
  the template and **silently discards `--demo`**. "Open this template and run
  its demo" is not expressible.
* `--deterministic --scene=X` produces a **hybrid tag table**. Measured on
  `light_curtain_sorting`: 24 tags — the deterministic sorting scene's 10 plus
  the template's 15 — published under scene name `light-curtain-sorting`. A
  driver sees `conveyor.rotate` and `belt.rotate` side by side, one belonging to
  a scene that is not on screen.

The spike in §0 shows the fix is small.

*Fixed by:* UX-10, UX-11, UX-12.

### 2.4 The install path still has the author's machine in it

* `docs/GETTING_STARTED.md:20` — the student guide's **first command** is
  `cd C:/Users/masal/source/factoryforge`.
* `tools/drv_trace.py:5` — `ROOT = Path(r"C:\Users\masal\source\factoryforge")`.
* `run_factoryforge.bat` — the `D:\Godot...` fallback above.

FF-09 removed the developer's PLC IP and instance name from the shipped UI
defaults. That cleanup never reached the docs, the launchers, or `tools/`.

*Fixed by:* UX-24.

### 2.5 Shipped material contradicts the `connect` vs `demo` warning

`GETTING_STARTED.md:111` carries a section headed *"`connect` vs `demo` — read
this first"*, warning that picking wrong "wastes an afternoon". Two shipped
files then pick wrong:

* `run_plcsim_advanced.bat` runs `demo --driver plcsim-advanced -o instance Sorting_PLC`
  — also hardcoding the author's own instance name.
* `examples/fake_plc.py`'s docstring instructs `demo --driver opcua-client`.

Both start a headless Python scene instead of driving the 3D view.

*Fixed by:* UX-25.

### 2.6 `connect --driver mock` is a trap

`mock` is a passive transport with no control logic (`drivers/mock.py` — it
records history and exposes an async API for tests). Run against a live engine
it connects a client, which stands `DemoDriver` down, and then drives nothing.
The most obvious command for "let me just try it" leaves the scene *more* dead
than doing nothing at all.

*Fixed by:* UX-21 (a real `try_scene.py` to point people at instead).

### 2.7 Run mode is the least discoverable thing in the app

Edit and Run are a good idea — `EditorMode.cs` argues it well: in a factory you
click a part to move it and click a button to press it, same mouse, opposite
intent. The implementation is sound; the presentation is not.

* **The toolbar says "Run" twice, adjacent, with the same glyph.** The mode
  button renders `▶ RUN` / `✎ EDIT` (`SceneToolbarUI.cs:62`); the pause button
  beside it renders `▶ Run` / `⏸ Pause` (`SceneToolbarUI.cs:74`). Paused in Run
  mode, the toolbar literally reads `▶ RUN … ▶ Run`, meaning two unrelated
  things.
* **Mode does not start or stop anything.** Physics, the emitter and the demo
  driver all run in Edit mode. Mode only changes what a click means. But it sits
  beside Pause, Reset and the rate selector, which *do* control time.
* **Only a Control Panel is clickable in Run mode.** `PressControlAt`
  (`SceneEditor.cs:515`) ray-tests for `ButtonPanel` and nothing else. Build a
  line without one and Run mode is a mode in which nothing responds.
* **Nothing marks a control as pressable.** No hover highlight, no cursor
  change. You find the caps by guessing.
* **`Ctrl+S` is silently dead in Run mode.** `_UnhandledInput` handles F1 then
  returns early for every other event when `Mode == Run`
  (`SceneEditor.cs:195`), taking Ctrl+S, Ctrl+O, Ctrl+Z/Y, `M`, `R`, `Del` and
  Ctrl+D with it. Blocking the *editing* keys is right; silently swallowing
  **save** is not.

*Fixed by:* UX-37 … UX-40.

### 2.8 The Force button only works on `bit` tags — blocker for manual operation

`TagInspectorUI.ToggleForce` (`TagInspectorUI.cs:138`) reads:

```csharp
if (tag.Type == TagType.Bit) { ... }
```

with no `else`. Press **Force** on an `int` or `float` tag and nothing happens —
no value forced, no message, and the button still reads "Force". There is no
value-entry field anywhere in the inspector; each row is a name button, a value
label, and that one button.

A UI gap, not a protocol one. `TagTable.Force` takes an `object`, the wire
`force` message carries any JSON scalar, and parts read forced values through
`Tags.TryGetVisible` — so forcing a **bit** output genuinely drives the machine,
and forcing a float *would* too. Only the button refuses.

What it costs:

* **The tank scene cannot be made to do anything by hand.** `tank.fill` and
  `tank.drain` are float outputs. No demo profile (§2.1), no clickable control
  (§2.7), no forceable tag. It is completely inert — and it is the template the
  start screen recommends for analog work.
* **No Digital Display can ever show a number by hand.** `*.value` is an `int`
  output on all three scenes that have one.

*Fixed by:* UX-35, UX-36.

### 2.9 You cannot operate a component *from the component*

The property panel is where you meet a part: click it and you get its type, its
**Name** field, and numeric spin boxes for its settings — speed, friction,
stroke, incline (`PartPropertyInspectorUI.cs`, driven by `PartProperties.cs`).

It does not show that part's **I/O at all**, and offers no way to operate it.
So "turn this conveyor on and see what it does" means: know that a conveyor owns
a `.rotate` tag, know the part's name is the tag prefix, find `belt.rotate` in a
separate scrolling list of 13–16 tags on the other side of the screen, and press
Force there. Three indirections between the thing on screen and the switch that
turns it on.

That is the gap §5 specifies closing. The part is right there and already
selected — its I/O belongs on the same panel as its settings.

*Fixed by:* UX-34, UX-37.

### 2.10 The startup line reports the wrong scene name

`Main.cs:120` prints `$"scene '{SceneName}', {tags.Count} tags"` using the
**const** at `Main.cs:29`, which is always `"sorting-by-height"`. The bus itself
is correct — `AdoptSceneName` sets `_bus.SceneName` from the loaded scene — so
the two disagree. Observed during the spike: the log said
`PHYSICS scene 'sorting-by-height', 14 tags` while the bus published
`start-stop-station`.

The tag count is right, which makes it more confusing rather than less: the line
looks plausible and is wrong about the only thing it names.

*Fixed by:* UX-15.

### 2.11 Smaller, cheap to fix

* The README badge and `GETTING_STARTED.md:27` both say **41 tests**; the suite
  is **71**.
* `GETTING_STARTED.md` says Python 3.10+; `sidecar/pyproject.toml` requires
  `>=3.11`.
* README and GETTING_STARTED both tell the user to run `"<GODOT_CONSOLE_EXE>"` —
  a placeholder they must resolve themselves, with no hint of where the mono
  build lives or that the plain non-mono Godot will not work.
* There is no launcher for Linux or macOS, only the `.bat`, even though
  `DriverConnectionUI.SpawnInTerminal` now supports both.
* No binary release — now **Phase 0**, not a footnote.

*Fixed by:* UX-26, UX-29.
---

## 3. Plan

### How to read a work item

```
UX-nn — one-line title
  Files:      what gets touched
  Done when:  the observable state that means it is finished
  Verify:     the command or the click that proves it
  Size:       S | M | L | XL
```

**Sizes.** `S` = under half a day. `M` = one to two days. `L` = three to five
days. `XL` = more than a week, or unknown until a spike lands. Sizes assume
familiarity with this codebase; they are relative, not a schedule.

### Sequencing

Phases are numbered by dependency, not by date. Phase 1 gates Phase 2;
everything else can be reordered. Recommended order of work:

> **Phase 3 → Phase 5 → Phase 1 → Phase 2 → Phase 4 → Phase 6**, with **Phase 0
> running in parallel from the start.**

Phase 3 is nearly free and fixes what a new user hits in the first ten minutes.
Phase 5 is the one that makes the app explorable — it is what turns "five scenes
you can look at" into "fifteen components you can switch on" — and after the
spike it no longer waits on anything. Those two buy the most per hour spent.
Phase 0's engineering is long-lead and independent, so it should start now.

**Where this stands:** every phase in that order has landed. Only Phase 0 is
left, so the sequencing below is now history rather than instruction — but the
caveat under it is live, and its condition is met.

One caveat on Phase 0, stated once: **cut the first release after Phases 3, 5
and 2 land, not before.** A binary built from `master` as it was when this was
written would have packaged a start screen offering five scenes, four of which
could not be made to move, and a tank that could not be operated at all.
Building the machinery then was right; shipping v0.1 out of it before there was
something worth downloading was the part worth waiting on. **That wait is over:**
all three phases landed, so the next binary produced is one worth releasing.

**Platform scope:** Windows and Linux. Both export presets already exist and CI
already runs Linux headless, so the second target is close to free. macOS is
deferred (§7).

---

### Phase 0 — Ship a binary — done

One command, `python tools/build_release.py`, produces
`dist/FactoryForge-<platform>.zip`: the exported engine, a PyInstaller-frozen
sidecar, `examples/`, and the docs. Verified by building the Windows archive
(92.8 MB) and running **all 22 headless self-tests against the exported binary**
— not against a checkout, which is the distinction that matters and the reason
`tools/packaging/check_release.py` exists.

**Two bugs that only exist in an export, both of which produce a build that
looks perfectly healthy:**

* **No solution file, no C#.** `dotnet build` never writes a `.sln` — it only
  needs the `.csproj` — and Godot refuses to export the .NET assemblies without
  one. It then writes the binary anyway and exits 0. The result is a
  right-sized executable with no `data_FactoryForge_*` folder, in which every
  script fails at runtime. `build_release.py` now refuses to run without the
  `.sln`, and `check_release.py` fails a release that has no assemblies folder.
  (On .NET 9+, `dotnet new sln` produces a `.slnx`, which Godot 4.7 does not
  look for — `--format sln` is required.)
* **Fixtures cannot be read the way the source tree reads them.** Covered under
  UX-08 below; it was two separate mistakes stacked, not one.

**And one that only exists in a *frozen* build:** PyInstaller-ing
`factoryforge_sidecar/__main__.py` directly strips its package context, so its
relative imports raise `ImportError: attempted relative import with no known
parent package` — but only once a command does real work. `--help` still prints
cleanly, which is exactly how a broken sidecar ships. The freeze now goes
through `tools/packaging/sidecar_entry.py`, which imports the package properly.

Verified end to end rather than by inspection: the frozen sidecar
(`connect --driver mock`) drove the exported engine over the tag bus with no
Python involved.

Today, running FactoryForge means installing Godot 4.7-mono, the .NET 8 SDK
and Python, then running `dotnet build` — a real barrier for a student who
wanted to learn ladder logic, not to install a game engine and a compiler.

`docs/PACKAGING.md` already lays out the intended recipe honestly, including its
own status: **nothing in it is verified.** Confirmed while writing this, and
re-confirmed on 2026-08-23 after the move to Godot 4.7.2 —
`%APPDATA%\Godot\export_templates` exists and is empty, so no binary has ever
been produced from `engine/export_presets.cfg`. UX-01's ~1 GB template download
is still the first thing this phase has to do.

**UX-01 — Produce a Windows and a Linux binary at all**
*Files:* none at first; `engine/export_presets.cfg` if the presets need fixing.
*Done when:* `dist/windows/FactoryForge.exe` and `dist/linux/FactoryForge.x86_64`
exist and each launches to the start screen with a template loadable.
*Verify:* run each binary; pick "Sorting by height"; the scene renders.
*Size:* M — mostly the ~1 GB export-template download and first-run fiddling.
*Blocks:* every other item in this phase.

Both produced. The template download was 1.2 GB and installs headlessly
(`tools/packaging/install_godot.py`, written for CI and useful here); the
"first-run fiddling" turned out to be the missing `.sln` described above. The
Windows binary was run and self-tested here, and **the Linux one on an
`ubuntu-latest` runner on 2026-08-24** — 74 MB, 21 self-tests, first try.

**UX-02 — Correct `PACKAGING.md` with what actually happened**
*Files:* `docs/PACKAGING.md`.
*Done when:* the status note at the top no longer says "not verified", and every
step reflects the real commands.
*Verify:* a second person follows it start to finish.
*Size:* S.

Rewritten around what actually happened rather than what was intended. The
status note at the top now records a real build, and the longest section in the
file is the solution-file trap — because it is the one that wastes an afternoon
and gives no hint that anything went wrong.

**UX-03 — Decide and implement how Python ships**
*Files:* new `tools/build_release.py`; `sidecar/pyproject.toml`.
*Done when:* a release archive contains a runnable sidecar with no `pip install`
step. `PACKAGING.md` recommends PyInstaller-per-platform; that is the only
option where a person downloads one archive and connects to a PLC without
reading anything.
*Verify:* on a machine with no Python, extract the archive and connect the
sidecar to a running engine.
*Size:* L. *Depends on:* UX-01.

PyInstaller, one executable per platform (17–31 MB depending on which drivers
are bundled), as `PACKAGING.md` recommended. Verified here by having the frozen
binary drive the exported engine over the tag bus, and in CI on both platforms.

**The "whichever drivers were installed" caveat turned out to be a live bug, not
a footnote.** CI's first release shipped without S7 or PLCSIM because the
workflow asked for `sidecar[opcua]` alone — and nothing caught it, because a
driver whose dependency is missing still registers and still appears in
`--help`. See UX-07 for the fix and the `drivers` command that now makes the
difference visible.

**UX-04 — Make the engine find a bundled sidecar**
*Files:* `engine/src/Editor/DriverConnectionUI.cs`.
*Done when:* F5's *Apply & Connect* starts the sidecar in a packaged build.
Today `ApplyConnectionSettings` (`DriverConnectionUI.cs:434` as of `3679bbb`;
`:391` when this was written) derives the
sidecar directory from the **parent** of `ProjectSettings.GlobalizePath("res://")`
— correct in a checkout, where `res://` is `engine/` and `sidecar/` sits beside
it, but in an export `res://` resolves to the executable's own directory, so it
looks for `<parent-of-exe>/sidecar` and misses. The dialog then falls through to
"command copied, run it yourself" as the **normal** path rather than the
exception. Needs a resolution order: bundled sidecar beside the executable → a
`FACTORYFORGE_SIDECAR` override → `PATH` → the existing copied-command fallback.
*Verify:* press *Apply & Connect* in the packaged build; the sidecar console
opens.
*Size:* M. *Depends on:* UX-03.

New `SidecarLocator` with the resolution order this item asked for, plus one
distinction the item did not anticipate: a *frozen* sidecar and a *source*
checkout are launched differently, and getting that backwards produces a
command that looks plausible and cannot run. `SidecarLocation.CommandFor`
decides, and a frozen build beside the binary wins over a source tree, because
the one needing no Python is the one to run.

Covered by `--self-test=sidecar` (C22), which reports which kind it found and
how it would start it. Run against a checkout it says `Source` and prefixes
`python`; run against the packaged build it says `Bundled` and does not. Both
were checked — the second against the real release directory.

**UX-05 — Decide "one file" or "one folder"**
*Files:* `engine/export_presets.cfg`, `docs/PACKAGING.md`.
*Done when:* either `binary_format/embed_pck=true` on both presets, or the docs
state the archive layout plainly. Both presets currently set it to `false`, so a
release is an executable *plus* a `.pck` *plus* the .NET assemblies.
*Verify:* the described layout matches what `build_release` produces.
*Size:* S.

**Decided by measurement: one folder, with the pck embedded.** Setting
`binary_format/embed_pck=true` on both presets works and keeps every self-test
passing, which removes the loose `.pck`. It cannot go further: a .NET export
always needs its `data_FactoryForge_*` assemblies folder beside the binary, so a
single self-contained executable was never actually on the table. Two items
instead of three is the whole of the available improvement.

**UX-06 — Settle what ships alongside the binary**
*Files:* `tools/build_release.py`, `docs/PACKAGING.md`.
*Done when:* the archive contains the engine, the sidecar, `examples/`, the
docs, and — once Phase 2 lands — the per-scene exercises. `templates/` are
`res://` resources and travel automatically; nothing else does.
*Verify:* extract the archive and run each shipped exercise.
*Size:* S. *Depends on:* UX-03.

`PAYLOAD` in `build_release.py` is the list, and it is deliberately short:
engine, frozen sidecar, `examples/`, the four docs a user of a binary needs,
README and LICENSE. The five templates are `res://` resources and travel inside
the executable, so they are absent by design rather than by omission.

**UX-07 — A release CI job**
*Files:* new `.github/workflows/release.yml`.
*Done when:* pushing a tag builds Windows and Linux, runs the headless
self-tests **against the exported binary**, and attaches both archives to the
release.
*Verify:* push a `v0.1.0-rc1` tag; artifacts appear and the self-tests gate them.
*Size:* L. *Depends on:* UX-01.

`.github/workflows/release.yml`, a Windows + Linux matrix on tag push, with a
`workflow_dispatch` dry run so it can be exercised without cutting a release.
The gate is `tools/packaging/check_release.py`: 21 self-tests **against the
exported binary**, plus the two structural checks that catch a build which
looks fine — the assemblies folder exists, and the frozen sidecar is where the
engine will look for it.

**Run on 2026-08-24** via its `workflow_dispatch` dry run — green on both
platforms first time: Windows 109 MB + 17 MB sidecar, Linux 74 MB + 31 MB, 21
self-tests each against the exported binary, `publish` correctly skipped.

It found one thing worth the exercise. The workflow installed `sidecar[opcua]`,
so both archives shipped **without S7 or PLCSIM** — two of the three Siemens
paths the README headlines — and nothing noticed, because every protocol driver
guards its own import and registers regardless. The driver was present, listed
in `--help`, and dead. Fixed by installing every extra, and by giving the
sidecar a `drivers` command that reports what a build can genuinely run, which
`check_release.py` now asks before letting a release out.

**UX-08 — Fix the fixture path that breaks self-tests in an export**
*Files:* `engine/src/Sim/TagParitySelfTest.cs`, `.github/workflows/release.yml`.
*Done when:* the exported-binary test run passes. `--self-test=parity` reads
`res://../tests/fixtures/tag_cases.json` (`TagParitySelfTest.cs:37` as of
`3679bbb`), a path that does not exist in an export. **UX-42 added a second one
with the same problem:** `--self-test=scenes` reads
`tests/fixtures/scene_tag_sets.json` the same way, so whatever fix is chosen
here has to cover both. Either ship the fixture as a resource or run
that one test only in the source-tree job.
*Verify:* run every self-test against the exported binary; all pass or are
explicitly excluded with a reason.
*Size:* S. *Depends on:* UX-01.

**Two stacked mistakes, not one.** Moving the fixtures under `res://` (they now
live in `engine/fixtures/`, read by both languages) fixes only the first: an
export packs them *inside* the `.pck`, where `System.IO.File` cannot reach them
at all. Only Godot's own `FileAccess` reads through the virtual filesystem. New
`engine/src/Sim/FixtureFile.cs` does that and, when a fixture is missing, says
which one and where it was looked for.

Verified as the item asks: all 21 headless self-tests pass against the exported
Windows binary, none excluded. `--self-test=click` is the only one not in that
list, and only because it synthesizes a mouse event and needs a display.

**UX-09 — Decide on code signing, or warn honestly**
*Files:* `docs/PACKAGING.md`, `README.md`.
*Done when:* either releases are signed, or the download page says in plain
words that Windows SmartScreen will warn and why. Signing costs a certificate
and a process; not signing costs every first-time user a scary dialog. Either is
defensible — silently doing neither is not.
*Verify:* download the release on a clean Windows box and follow the docs
through the warning.
*Size:* S as a decision; L if signing is chosen.

**Decided: not signed, and both the README and the release notes say so in
plain words** — SmartScreen will show "Windows protected your PC", the user
clicks *More info → Run anyway*, and the warning means no certificate was
purchased rather than anything being wrong with the download. An OV certificate
costs a few hundred dollars a year and an identity an individually-authored
project may not want to maintain; EV needs hardware. What was not defensible was
silently doing neither, which is what this item existed to prevent.

---

### Phase 1 — Make scenes scriptable — done

The prerequisite for Phase 2. **De-risked by the spike in §0** — this is now
small, mechanical work.

All five items landed together (`engine/src/Main.cs`, new
`engine/templates/manifest.json`, new `engine/src/Editor/TemplateManifest.cs`).
Verified beyond the self-tests below: forced `tank.fill` to 0.5 over a live
tag-bus connection to the template loaded via `--scene=` headless and watched
`tank.level` climb from 0.0 to 1.08 over 10.5s (real Torricelli physics, no
renderer) — the exact scenario UX-35's verify step names. Also confirmed the
manifest-driven start screen renders identically to the old hardcoded one by
screenshot, and that the widened Tag Inspector shows a real editable value
field next to `counter.tall`/`counter.short` in a live window, not just headless.
New regression coverage: `tools/test_plan.py` C7 (`--scene=` + `--print-tags`
headless, asserts scene name and tag count) and C8 (`--deterministic
--scene=` rejected, non-zero exit). `python -m pytest -q` still 71 passed.

**UX-10 — Load `--scene=` headless — done**
*Files:* `engine/src/Main.cs`.
*Done when:* `godot --headless --path engine -- --scene=res://templates/X.json`
publishes X's scene name and X's tags. Proven by the spike: call
`editor.LoadTemplate(_scenePath)` in `BuildHeadlessPhysicsParts`
(`Main.cs:382`) instead of `RegisterDefaultSceneParts`, and adopt the name.
*Verify:* all four templates report their own name and tag count headless,
matching the windowed run.
*Size:* S.

**UX-11 — Make `--scene` and `--demo` compose — done**
*Files:* `engine/src/Main.cs` (the `else if` at `:315`).
*Done when:* `--scene=X --demo` loads X *and* starts the demo.
*Verify:* run it; the template loads and moves.
*Size:* S.

Verified windowed (this is a `BuildView`-only flag today, matching the plan's
file scope): `tank_level_control.json` loads and reports its own name with
`--demo` set, no crash. `DemoDriver` itself still only has a sorting-line
profile (§2.1) — it composes correctly but has nothing scene-appropriate to do
yet on a template, which is exactly the gap Phase 2 (UX-16…UX-20) closes.

**UX-12 — Reject `--deterministic --scene=` — done**
*Files:* `engine/src/Main.cs`.
*Done when:* the combination exits with a message naming why, instead of
publishing a 24-tag hybrid of two scenes.
*Verify:* run the combination; a clear error, non-zero exit.
*Size:* S.

Found and fixed a second bug while verifying this one: `GetTree().Quit(1)`
schedules the exit for end-of-frame rather than stopping immediately, so
`_Process` still ran once more with `_bus` never constructed and crashed on a
`NullReferenceException` instead of exiting cleanly on the message already
printed. Guarded `_Process` with an early return while `_bus` is null.
Covered by `tools/test_plan.py` C8.

**UX-13 — A scenes manifest — done**
*Files:* new `engine/templates/manifest.json`;
`engine/src/Editor/StartScreenUI.cs` (replacing the hardcoded `Templates` array
at `:38`); `tools/` consumers.
*Done when:* id, title, blurb, path and scene name live in one file that both
the start screen and the Python tooling read, so a sixth template appears
everywhere at once.
*Verify:* add a dummy entry; it shows on the start screen and in
`try_scene.py --list` with no C# change.
*Size:* M.

Implemented as `engine/templates/manifest.json` (five entries: id, title,
blurb, path, scene) read by a new shared `TemplateManifest.Load()`. Two
consumers switched over: `StartScreenUI`'s hardcoded array, and
`TemplateSelfTest`'s separate hardcoded path list — the exact duplication this
item exists to remove. `TemplateSelfTest` keeps a small `MustContain` map
keyed by manifest id (which parts each template must contain), since that is
test fixture data, not something the start screen or Python tooling needs.
Verified by screenshot: the start screen renders identically to the old
hardcoded version. The `try_scene.py --list` half of Verify is not yet
checkable — `try_scene.py` is UX-21, not built yet — but the manifest itself
is plain JSON any Python tool can read with no C# change, which is the part
of "done when" this phase owns.

**UX-14 — `--print-tags` — done**
*Files:* `engine/src/Main.cs`.
*Done when:* the flag dumps the `describe` table to stdout and exits, so no
script needs a WebSocket client just to ask what a scene exposes.
*Verify:* output matches what a bus client sees for the same scene.
*Size:* S.

Prints the exact same `{"t":"describe","scene":...,"epoch":...,"tags":[...]}`
shape `TagBusServer.SendDescribe` sends, built from the same `Tag.ToJson` — not
a hand-rolled copy that could drift from the real wire format. Covered by
`tools/test_plan.py` C7.

**UX-15 — Report the loaded scene's real name at startup — done**
*Files:* `engine/src/Main.cs:120`.
*Done when:* the ready line prints the scene actually loaded, not the
`SceneName` const (§2.10).
*Verify:* load a template; the log and the bus agree.
*Size:* S.

One-line fix: the ready line now interpolates `_bus.SceneName` instead of the
`SceneName` const. Verified across all four templates headless — the ready
line and the `--print-tags` `"scene"` field agree in every case.

---

### Phase 2 — One runnable exercise per scene — done (UX-16…UX-22)

The behaviour spec for all five is §4. **Both homes, C# first** — decided:

| | C# `DemoDriver` profiles | Python `tools/try_scene.py` |
|---|---|---|
| Install cost | none | needs the sidecar |
| Serves | the "Watch it run" button, all 5 scenes | the "test before PLC" script |
| Also proves | the scene | the scene **and** the engine↔sidecar seam |
| Assertions | none — it is a demo | yes: exit code + `RESULT` line |

**Assertions must be band-based, not exact.** Templates always run the Jolt
physics scene (§2.3), so no template can promise exact counts the way
`drive_engine.py` does. Assert "at least N counted", "level held within ±5% for
10s", "tall+short equals emitted" — never "tall == 5".

**UX-16 — A per-scene profile mechanism in `DemoDriver` — done**
*Files:* `engine/src/Sim/DemoDriver.cs`.
*Done when:* `DemoDriver` looks up a profile by the loaded scene name and runs
it, and reports honestly when there is none (see UX-30).
*Verify:* the existing sorting behaviour is unchanged, now expressed as a
profile.
*Size:* M. *Depends on:* UX-13.

Implemented as `IDemoProfile` (`Start`/`Tick`) plus one class per scene under
`engine/src/Sim/DemoProfiles/`, looked up from a `scene id → factory`
dictionary keyed to the manifest's ids. `RefusalReason` is set instead of
silently leaving `Active` false, so a custom scene with no profile refuses
audibly rather than turning "Demo" green over nothing — the same dishonesty
class as FF-06/FF-23, closed before it could happen here too. The original
sorting logic moved into `SortingByHeightProfile` unchanged (same constants,
same code); `--demo --duration=25` on the default scene still gives
`tall=3 short=3` after the refactor, matching pre-refactor behaviour.

**A second, unrelated bug found while verifying this and fixed alongside it:**
`DemoDriver` read tags from `_Process` (the frame clock). A panel button's
press is exactly one `_PhysicsProcess` tick wide (`SceneEditor.StepPanelButtons`
sets it, then clears it at the top of the very next physics tick), and headless
has no vsync holding `_Process` and `_PhysicsProcess` at the same cadence —
uncapped, the engine can run several physics ticks per frame to catch up. A
frame-clock reader can watch a one-tick pulse turn on and off again between two
of its own calls and never see it high at all. This reproduced deterministically
(3/3 runs) right after a template load's own allocation hitch gave the catch-up
loop something to catch up on — exactly the moment a real user clicks Start
after a template just finished loading. Moved `DemoDriver` to `_PhysicsProcess`
(it is already added to the tree after `SceneEditor`, so ordering within a tick
is unchanged); UX-17's self-test went from reproducibly failing to reproducibly
passing (3/3) with no other change. This was not introduced by this session's
refactor — the original `DemoDriver` had the same `_Process` read against the
same kind of physics-tick-driven tag (`sensor_high.detect`) and would have had
the same exposure, just with lower odds of noticing since the sorting profile
has no latched state to make a missed edge visible.

**UX-17 — `start-stop-station` profile — done** — §4.2. *Size:* S. *Depends on:* UX-16.
**UX-18 — `tank-level-control` profile — done** — §4.3; needs a small controller, not
just a timer. *Size:* M. *Depends on:* UX-16, UX-35.
**UX-19 — `light-curtain-sorting` profile — done** — §4.4; handshakes on
`diverter.extended`/`.retracted` rather than a timer. *Size:* M. *Depends on:* UX-16.
**UX-20 — `roller-line-weighing` profile — done** — §4.5. *Size:* S. *Depends on:* UX-16.

*Verify (UX-17 … UX-20):* load each template, press 🎬 Demo, watch the line run
for 30 s with no PLC and no Python.

Verified with four new physics-timed self-tests
(`--self-test=startstop|tank|lightcurtain|roller`, `tools/test_plan.py`
C10–C13) rather than only by eye, since "watch it run" is hard to re-check on
every future change:

* **start-stop-station** — drives the real `ButtonPanel.Press()` API (not a
  direct tag write) through Start → E-stop → Start-while-tripped (refused) →
  release → Reset → Start again, asserting the lamp and belt never disagree.
  Deliberately broke the Start-edge branch and watched it fail (4 failures),
  then restored it.
* **tank-level-control** — runs the controller for ~13 simulated seconds and
  checks `tank.level` settled within ±5% of the 55% setpoint, and
  `level_readout.value` tracks it. Real Torricelli physics, no shortcuts.
* **light-curtain-sorting** — runs ~20 simulated seconds and checks both
  `tall_count.count` and `short_count.count` advanced (got `tall=2 short=3`),
  proving the diverter both fires for tall cartons and stays retracted for
  short ones. Deliberately broke `TallThreshold` (set to an unreachable 5.0m)
  and watched `tall_count.count` stay at 0 while `short_count.count` kept
  climbing — the exact failure mode a wrong threshold produces — then restored it.
* **roller-line-weighing** — runs ~20 simulated seconds and checks
  `outfeed.count` advances and `metal_check.detect` fired at least once
  (`outfeed=4 sawMetal=True`) — the material-aware sensing claim, checked
  rather than only asserted in a README.

All four passed on the first real run once the `_PhysicsProcess` fix landed.
`python -m pytest -q` unaffected (71 passed); full `test_plan.py --only A,C,E`
21 passed in 165s.

**UX-21 — `tools/try_scene.py` — done**
*Files:* new `tools/try_scene.py`; fold `tools/drive_engine.py` in behind it.
*Done when:* `--scene <id>`, `--duration`, `--verbose`, `--list`; one
`RESULT ...` line; exit 0/1 — the convention `check_protocol.py` and
`check_force_while_paused.py` already set.
*Verify:* `python tools/try_scene.py --scene sorting-by-height` reproduces
`drive_engine.py`'s `tall=5 short=5` exactly.
*Size:* M. *Depends on:* UX-10, UX-13.

Self-contained: it finds Godot the same way `run.py` does, spawns the engine
headless itself (with `--scene=<template path>` for the four templates, or
`--deterministic` for `sorting-by-height`, which has no template path and so
no conflict with UX-12's `--deterministic --scene=` rejection), connects,
drives, asserts, and tears the subprocess down — one command, matching the
Verify step literally, not "start the engine yourself first" the way
`drive_engine.py` still requires. Refuses outright with a clear message if
port 7411 is already bound, rather than silently connecting to whatever else
is listening. Verified against all five scenes, including the exact-count
regression: `--scene sorting-by-height` gives `tall=5 short=5`, matching
`drive_engine.py`'s own contract to the count.

`tools/drive_engine.py` was not deleted or rewritten — `test_plan.py`'s E1/E2
still spawn an engine externally and run it directly, a proven, working path
that UX-43 (Phase 6) is the deliberate place to reconsider, not a side effect
of this item. "Folded in behind it" instead means `drive_sorting_by_height`
mirrors its logic line for line, and it is what `--list`/`--scene
sorting-by-height` now point a new user at instead.

**UX-22 — Assertions for all five scenes in `try_scene.py` — done, folded into UX-21**
*Files:* `tools/try_scene.py`.
*Done when:* each scene's §4 assertions run and fail loudly on a real
regression.
*Verify:* break one thing deliberately per scene and watch the right assertion
fail.
*Size:* M. *Depends on:* UX-21.

Landed alongside UX-21 rather than after it: each of the five driver
functions (`drive_sorting_by_height`, `drive_start_stop_station`,
`drive_tank_level_control`, `drive_light_curtain_sorting`,
`drive_roller_line_weighing`) mirrors its engine-side profile under
`engine/src/Sim/DemoProfiles/` line for line — same constants, same edge
detection, same handshakes — and returns pass/fail against the exact
assertion §4 and the matching C# self-test (UX-17…UX-20) already use, so a
regression in either language fails the same way. `start-stop-station` is the
one scene with no PLC-driven outputs alone: nothing but a human normally
presses `panel.start`/`.stop`/`.reset`, so the driver plays both roles at
once — forcing the panel's input tags the way an operator would (`press()`,
mirroring `ButtonPanel.Press`'s one-edge-per-press contract) while writing
`belt.rotate`/`tower.*`/`produced.value` the way `StartStopStationProfile`
does — and walks the same Start → E-stop → Start-while-tripped (refused) →
release → Reset → Start sequence `StartStopProfileSelfTest` already proved
correct on the engine side.

Verified by deliberately breaking the tank scene's final check (comparing the
settled level against `80.0` instead of the real `55.0` setpoint) and
watching it report `FAIL — tank.level settled at 55.0, outside ±2.8 of 55.0`
with exit code 1, then restoring it. All five scenes re-verified after: `tall=5
short=5` (sorting, exact), the full 12-assertion start/stop sequence, `level=55.0`
(tank, within band), `tall=2 short=3` (light curtain, both counters advanced),
`outfeed=5 sawMetal=True` (roller). No orphaned Godot process left behind in
any case, including the unknown-scene and port-already-in-use error paths.

---

### Phase 3 — First-run friction — done

Cheapest phase, highest visibility to a new user, no engine changes.

`run.py` is the new cross-platform launcher (`run_factoryforge.bat` and
`run.sh` are now thin wrappers around it); it covers UX-23, UX-27 (names the
process holding port 7411 and asks, instead of killing it) and UX-28 (no
driver starts unless `--live-driver` is passed) in one file, and UX-29 for
free since it is plain Python. UX-24 (author-machine paths), UX-25
(`demo`→`connect`), and UX-26 (stale facts: 41→71 tests, 3.10→3.11, the
`"<GODOT_CONSOLE_EXE>"` placeholder) are fixed in the files the plan named.
Verified: `python -m pytest -q` → 71 passed; `run.py` launches, builds, binds
the tag bus, and correctly refuses/warns on a held port and a missing Godot.

**UX-23 — One cross-platform launcher**
*Files:* new `run.py` (or `run.sh` + a rewritten `run_factoryforge.bat`).
*Done when:* it finds Godot-mono or says exactly what to download, checks for
the .NET SDK and Python, runs `dotnet build`, and launches — nothing more.
*Verify:* run it on a machine where Godot is not on `PATH`; the message names
the required build and where to put it.
*Size:* M.

**UX-24 — Purge the author's machine from the repo**
*Files:* `docs/GETTING_STARTED.md:20`, `tools/drv_trace.py:5`,
`run_factoryforge.bat`.
*Done when:* no shipped file contains `C:\Users\masal` or `D:\Godot...`.
*Verify:* `git grep -Iil -e "users.masal" -e "D:.Godot"` returns only `AGENTS.md`,
`docs/FIX_PLAN.md` and this file — three deliberate hits, no shipped code or user
doc. `AGENTS.md` is the developer cheat sheet, whose whole purpose is a table
headed *"Absolute paths on this machine"*; the other two are findings (FF-30,
§2.2/§2.4) quoting the very paths they exist to report removed. Nothing a user
runs or reads carries one.
*Size:* S.

**UX-25 — Fix `demo` → `connect` in shipped material**
*Files:* `run_plcsim_advanced.bat`, `examples/fake_plc.py` docstring.
*Done when:* both use `connect`, and neither hardcodes `Sorting_PLC`.
*Verify:* follow each file's own instructions against a running engine; the 3D
view moves.
*Size:* S.

**UX-26 — Refresh stale facts**
*Files:* `README.md` (test badge), `docs/GETTING_STARTED.md`.
*Done when:* 41 → 71 tests, Python 3.10 → 3.11, and `"<GODOT_CONSOLE_EXE>"`
replaced with a real sentence naming the required build and where the launcher
looks for it.
*Verify:* `pytest -q` count matches the badge.
*Size:* S.

**UX-27 — The launcher must not kill port 7411 silently**
*Files:* `run_factoryforge.bat` / `run.py`.
*Done when:* it names what holds the port and asks, instead of `Stop-Process
-Force` on an unidentified process.
*Verify:* hold 7411 with an unrelated process and run the launcher.
*Size:* S.

**UX-28 — The launcher must not auto-start a driver**
*Files:* `run_factoryforge.bat` / `run.py`.
*Done when:* launching leaves "Watch it run" working (§2.2). Starting a driver
stays opt-in via a flag.
*Verify:* launch, press "Watch it run", the line moves.
*Size:* S.

**UX-29 — A Linux launcher**
*Files:* new `run.sh`.
*Done when:* the same flow works on Linux, matching UX-23.
*Verify:* run it on the CI image.
*Size:* S. *Depends on:* UX-23.

---

### Phase 4 — Tell the truth in the UI — done (UX-30…UX-33)

**UX-30 — The Demo button refuses honestly — done**
*Files:* `engine/src/Sim/DemoDriver.cs`, `engine/src/Editor/SceneToolbarUI.cs`.
*Done when:* with no profile for the loaded scene, the button says so and does
**not** turn green (§2.1). The FF-06 fix applied to the demo path.
*Verify:* load an empty scene, press Demo, read the refusal.
*Size:* S. *Depends on:* UX-16.

`Active` staying false was already true from UX-16 (Phase 2) — the actual gap
was that the refusal reached only the console. Added a `DemoDriver.Refused`
signal (distinct from `ActiveChanged`, so pressing Demo twice on the same
broken scene says so twice rather than staying silent after the first
`RefusalReason` value is "already known") and wired it into `IdleHintUI`,
which forces itself visible with the specific reason for a few seconds — even
if the ambient idle nag was already dismissed, since this is a direct
response to a click, not an ambient one. Covered by a new self-test
(`--self-test=refusal`, `tools/test_plan.py` C14) that subscribes exactly the
way the UI does and checks the panel and its label, not just the driver's own
state. Deliberately commented out the `EmitSignal` call and watched it fail
(2 failures), then restored it.

**While verifying this, found and fixed a second, older bug it depends on:**
`StartScreenUI.DefaultSceneChosen` and `.EmptySceneChosen` never called
`AdoptSceneName`, unlike every other scene-changing path (`TemplateChosen`,
`OpenRequested`, the toolbar's Load). So the bus kept reporting whatever scene
name was already stale after "Sorting by height" or "Empty scene" — on Empty
specifically, this meant `Bus.SceneName` never became `"untitled"`, so Demo
would try to run `SortingByHeightProfile` against an empty scene instead of
refusing, reproducing the exact silent-no-op bug UX-30 exists to close. Fixed
both call sites in `Main.cs`.

**UX-31 — A "Try this scene" affordance — done**
*Files:* `engine/src/Editor/SceneToolbarUI.cs`.
*Done when:* it runs the right exercise for whatever is loaded and copies the
command — the same shape as F5's *Apply & Connect*.
*Verify:* press it on each template; the exercise runs.
*Size:* M. *Depends on:* UX-21.

A new "🧪 Try" button beside Demo, matching F5's shape exactly: it copies
`python tools/try_scene.py --scene <id>` to the clipboard, prints it, and
opens a visible terminal running it (`TerminalLauncher.Spawn`, factored out
of `DriverConnectionUI`'s own `SpawnInTerminal` into
`engine/src/Editor/TerminalLauncher.cs` so the two buttons share one
cross-platform terminal-launch implementation instead of two copies drifting
apart). The scene id comes from matching `Editor.SceneName` against
`TemplateManifest`; a scene with no match (a custom save) refuses honestly
through the idle hint — *"No built-in exercise for scene '…' — try one of
the five shipped templates instead"* — naming the actual scene rather than a
generic message, the same FF-06/FF-23/UX-30 dishonesty class closed
everywhere else in this plan.

**A real design gap found and fixed while wiring this up, not a bug in
already-written code:** `try_scene.py` (UX-21) always span its own headless
engine and refused outright if port 7411 was already bound. A "Try this
scene" button living *inside* the already-running windowed engine would
always find that port taken — its own — so the very shape UX-31 asks for
("press it on each template; the exercise runs") was impossible as UX-21
originally shipped. Fixed in `try_scene.py` itself: when the port is already
listening, it now attaches to whatever is already running instead of
refusing, checks the attached scene matches the one requested, and drives it
live if so (refusing by name if a different scene is loaded there). This
also makes `sorting-by-height`'s assertion honest in that mode: an
already-running engine is almost never started with `--deterministic`, so
attaching falls back to the same band-based `tall>0 and short>0` check the
other four scenes use, rather than demanding the exact `tall=5 short=5` only
a scene *this script itself* spawned deterministically can promise.

Verified end to end, not just headless: launched the real windowed engine,
then ran `try_scene.py --scene sorting-by-height` from a second terminal
while it was open — *"Attached to the already-running engine (scene
'sorting-by-height', 16 tags)"*, `RESULT tall=1 short=2`, `PASS`, and no
process left behind afterward. New self-test `--self-test=tryscene`
(`tools/test_plan.py` C18) checks the manifest-matching logic directly (safe
to call headless, no side effect) and drives the actual refusal path through
`SceneToolbarUI.TryThisScene()` against a real custom-named scene, loaded
and reloaded from a file the way a user's own save would be, checking the
real on-screen label text — deliberately **not** exercising the matching
path through the real button method, since that spawns a real terminal
process, which a headless CI run must never trigger. Deliberately broke
`FindManifestEntry` to fall back to the wrong entry on a miss and watched
the self-test fail — and watched it actually attempt to spawn `try_scene.py
--scene sorting-by-height` for an unrelated custom scene, confirming the
test's own caution about that path was warranted — then restored it.

**UX-32 — Keep "what this scene teaches" reachable — done**
*Files:* `engine/src/Editor/StartScreenUI.cs`, a new panel or the property
panel's empty state.
*Done when:* each template's blurb (`StartScreenUI.cs:38`) and its tag list stay
available after the scene loads, instead of vanishing with the start screen.
*Verify:* load a template; find its description without going Home.
*Size:* M. *Depends on:* UX-13.

The tag list half was already true — the Tag Inspector panel (top-right) has
always shown the loaded scene's live tags regardless of the start screen.
What vanished was the blurb, so `PartPropertyInspectorUI`'s empty state
("Click a placed part…") now leads with the loaded template's own title and
blurb, read from the same `TemplateManifest` the start screen uses, matched
against `Editor.SceneName`.

Found and fixed a genuine layout risk while wiring this up, the same class of
bug as the F5 dialog fix earlier in this plan: the empty state's content
container sat directly in a fixed-height `PanelContainer` with nothing
scrollable, and the longest blurb (tank's, four wrapped lines) plus the
standing "click a part" text does not fit the panel's original 220px. Wrapped
it in a bounded `ScrollContainer` (matching `TagInspectorUI`'s own pattern)
before shipping the new content, rather than after finding it broken by eye.

Also found and fixed a real timing bug via a `--scene=` screenshot: the empty
state redraws mid-load, via `SceneEditor.DeselectPart()`'s **direct** call
into `PropertyInspector.InspectNode(null,…)` when the old scene's parts clear
— and that happens *before* `SceneName` is updated to the new scene, so the
panel briefly (and, on the `--scene=` startup path specifically, persistently)
showed the previous scene's blurb. Refreshing only on `TagsChanged` was not
enough — during `--scene=` startup that event fires before anything has
subscribed to it. Fixed by refreshing from `AdoptSceneName` itself (a `Main`
field, `_propertyInspector`, added for exactly this), which every
scene-adoption path already calls after the swap is genuinely complete.
Verified by screenshot: `--scene=tank_level_control.json` now shows "Tank
level control" and its real blurb, scrolling correctly, not "Sorting by
height" left over from the scene that was replaced.

**UX-33 — F5's empty state offers the exercise — done**
*Files:* `engine/src/Editor/DriverConnectionUI.cs`.
*Done when:* with no driver connected the dialog reads *"No PLC yet? Run the
built-in exercise for this scene first."* with a button.
*Verify:* open F5 on a fresh launch.
*Size:* S. *Depends on:* UX-31.

The exact copy the item asks for, as the first row inside the modal — ahead
of the driver dropdown and every IP/instance field, so it is the first thing
read on a fresh open rather than something found after scrolling past
configuration a first-time user has no PLC to point at yet.

**The button's logic was factored out of `SceneToolbarUI` rather than
duplicated a second time**, since UX-31's toolbar button and this one now
do the identical thing (resolve the loaded scene against the manifest, copy
`python tools/try_scene.py --scene <id>`, spawn a terminal, or refuse by
name): both now call a new `engine/src/Editor/TryScene.cs`
(`TryScene.Run`/`TryScene.FindManifestEntry`), and `SceneToolbarUI.TryThisScene()`
was rewritten as a one-line forward to it. Verified the F5 modal's minimum
size is unaffected by the new row: `--self-test=layout` (C5) still reports
690×520, unchanged from before this item.

Verified by screenshot (a temporarily-extended hold on `LayoutSelfTest`, since
its normal 8-tick run quits before a screenshot timer can fire — reverted
after): the dialog opens with *"No PLC yet? Run the built-in exercise for
this scene first."* and a *"🧪 Try this scene"* button as the very first row,
above the driver dropdown. Covered by the same `--self-test=tryscene`
(`tools/test_plan.py` C18) UX-31 added, extended to find this button by its
real text and press it — deliberately only on the no-manifest-match refusal
path, for the same reason as the toolbar button: a headless CI run must never
spawn a real terminal process. Deliberately unwired the button's `Pressed`
handler and watched the self-test fail (*"F5 dialog: the same button also
reaches the hint on refusal"*), then restored it.

---

### Phase 5 — Operate any component by hand — done (UX-34…UX-41)

**The phase that makes the app explorable.** Specification in §5. Today, turning
a conveyor on means knowing it owns a `.rotate` tag, knowing the part name is
the tag prefix, finding that id in a 16-row list on the far side of the screen,
and pressing Force there — and if the tag is a float, there is no way at all
(§2.8, §2.9).

**UX-34 — Live I/O in the part property panel — done**
*Files:* `engine/src/Editor/PartPropertyInspectorUI.cs`, `PartTagManager.cs`.
*Done when:* selecting a part shows its own tags beneath its settings, each with
a control: a toggle for `bit` outputs, a value field or slider for `int`/`float`
outputs, and a live readout for inputs with an override toggle. **The single
highest-value item in this phase** — it puts the switch on the same panel as the
thing it switches.
*Verify:* click a conveyor, flip its toggle, watch the belt start; click a tank,
drag the fill valve, watch the level rise.
*Size:* L. *Depends on:* UX-35.

Every own-tag row goes through `TagTable.Force` — the same call the Tag
Inspector's own button makes, so "forcing is sticky" (§5.2) holds here too: a
control left touched keeps winning over a driver that connects later, exactly
like it always has. A `bit` output gets a `CheckButton` (a real toggle switch,
not a Force/UNFORCE button); `int` a `SpinBox`; `float` an `HSlider` (0–100,
matching the only float outputs that exist — `tank.fill`/`.drain`) — each
wired to Force on every value change, immediately, not on a separate "apply"
click. One named exception: `.emit` (Box Emitter) is a rising edge, not a
level — `SceneEditor`'s dispatch only spawns a box on the edge, so a plain
toggle left on would look broken. It gets an "Emit one" button instead: force
true, then clear the force ~50ms later.

An `int`/`float` input's own value only shows read-only, with a checkbox
labelled **Override** beside it — checking it forces the tag at its current
value and reveals the same kind of control an output gets; unchecking clears
the force and returns to the live readout. Answers "what does my PLC do if
this sensor is stuck on?" (§5.4) without leaving the panel to ask it.

**Two real bugs found and fixed while building this, both about signals firing
when they should not, or not being caught when they should have been:**

* Godot's `Range` controls (`SpinBox`, `HSlider`) have no `SetValueNoSignal`
  the way `BaseButton` has `SetPressedNoSignal` — setting `.Value` to the tag's
  *own current* value to initialise a fresh control's position fires
  `ValueChanged` anyway, which would have force-pinned every int/float output
  the instant a part was merely selected, before anyone touched anything.
  Guarded with an `_initializing` flag checked inside every `ValueChanged`
  handler.
* The empty state's content used to sit directly in a fixed-height
  `PanelContainer` with nothing scrollable — the same class of bug as the F5
  dialog fix earlier in this plan. Wrapped in a bounded `ScrollContainer`
  (`TagInspectorUI`'s own pattern) before adding any I/O rows, not after
  finding it overflow by eye.

Verified two ways. A new self-test (`--self-test=proppanel`,
`tools/test_plan.py` C15) drives the real controls -- found by the same
tooltip-based row search as the earlier self-tests, not a test-only accessor
-- for one of each combination on the tank scene (`tank.fill` float output,
`level_readout.value` int output, `panel.green` bit output,
`panel.estop` bit-input override) and checks `TagTable` actually changed.
Deliberately disabled the float slider's `ValueChanged` wiring and watched it
fail, then restored it. Also verified by screenshot, reusing the real
on-screen panel (`Editor.PropertyInspector`) rather than a second invisible
one: selecting the tank showed **`FillRate: 18`, `DrainRate: 22`** (its
existing settings) followed by an **I/O** section, and forcing `tank.fill` to
42 through the panel's own slider made `tank.level` climb on screen exactly
the way forcing it from the Tag Inspector did in UX-35 — same mechanism, now
one click away instead of three.

**UX-35 — Force any tag type, not just bits — done**
*Files:* `engine/src/Editor/TagInspectorUI.cs`.
*Done when:* `int` and `float` tags can be forced to a typed value from the
inspector. The plumbing exists — `TagTable.Force` takes an `object` and parts
read through `TryGetVisible` — so this is UI only (§2.8). It is what makes the
tank scene operable at all.
*Verify:* force `tank.fill` to 0.5; the level rises. Force `display.value` to
42; the display reads 42.
*Size:* M.

Implemented as a `LineEdit` beside the Force button for any non-bit tag,
pre-filled with the live value and left alone while it has focus or while the
tag is forced. Guarded by a new headless self-test (`--self-test=force`,
`tools/test_plan.py` C6) that drives the inspector's real controls — the same
LineEdit and Button a click would use, found by the row's own tooltip rather
than a test-only accessor — for a bit, an int and a float tag, and confirms
`TagTable` actually changed. Verified the guard is real by reverting the fix
and watching C6 fail (`Force did not force`), then restoring it.

The `tank.fill` / `display.value` verify steps above need a template loaded
headless or windowed by hand — Phase 1 (UX-10) isn't done yet, so this was
verified with a synthetic tag table instead (one bit, one int, one float tag),
which exercises the same code path.

**UX-36 — Until UX-35 lands, refuse audibly — done, folded into UX-35**
*Files:* `engine/src/Editor/TagInspectorUI.cs`.
*Done when:* pressing Force on a non-bit tag says why instead of doing nothing.
*Verify:* press it on `counter.tall`; a message appears.
*Size:* S.

Landed alongside UX-35 rather than as a stopgap ahead of it: an unparseable
value flashes the input field red for a second instead of silently doing
nothing. Covered by the same C6 self-test (`CheckInvalid`).

**UX-37 — Click a component in Run mode to operate it — done**
*Files:* `engine/src/Editor/SceneEditor.cs` (`PressControlAt`, `:515`), the part
classes.
*Done when:* Run mode is not `ButtonPanel`-only (§2.7): clicking a conveyor
toggles `.rotate`, a pusher strokes, a stack light stage toggles, a tank opens
its valve. Each part declares what a click on it means.
*Verify:* in Run mode, click each part type in turn and watch its tag move.
*Size:* L.

`PressControlAt` now splits into a camera-projecting entry point and
`PressControlAtRay(Vector3 from, Vector3 dir)`, so a headless self-test can
drive the exact same dispatch with a synthetic ray and no camera. Two tiers,
both compared on one footing: **precise** parts (`ButtonPanel`, `StackLight`,
`LevelTank`) test the ray against their own sub-regions — a bounding box
would cover the whole housing and fire whichever control is nearest the
part's centre no matter where on it you clicked — everything else
(`ConveyorBelt`/`RollerConveyor`/`WeighingConveyor`, `PusherMechanism`,
`Emitter`) is tested against its whole bounding box, the same box selection
already uses, and toggles the one tag it owns. Every write goes through
`TagTable.Force`, so a part operated by a click stays sticky exactly like the
Tag Inspector and the property panel (UX-34) already are (§5.2). `Emitter`
pulses rather than latches (force true, clear ~50ms later), mirroring the
property panel's own "Emit one" button, since holding `.emit` high spawns
nothing new.

New sub-region hit tests, both following `ButtonPanel.HitTest`'s existing
sphere-per-region pattern (now factored out into a shared `RayHit.Sphere`
helper in `engine/src/Parts/RayHit.cs`, used by all three rather than three
copies of the same algebra): `StackLight.HitTest` returns which lamp
(`green`/`yellow`/`red`) a click landed on, and `LevelTank.HitTest` returns
which pipe (`fill`'s inlet at the top, `drain`'s outlet at the foot) — a click
toggles that one valve fully open or fully shut, since a single click needs
no finer control than that (the drag-to-set slider from UX-34 still exists
for anything finer).

**A real bug found and fixed while building the first version of the self-test,
worth recording because it was not the bug it first looked like:** the
`PressControlAtRay` dispatch originally measured "nearest" inconsistently — a
box-hit distance (a ray parameter) for whole-body parts against a straight-line
distance-to-object-centre for precise parts, mixing two units in one
comparison. That looked at first like the cause of a failing assertion
("a click on the yellow lamp toggles it independently"), and was worth fixing
regardless — a real scene could plausibly hit the same class of bug where an
unrelated part's box happens to sit nearer by the wrong metric. Fixed by
adding `MeasureDistance`, which measures every candidate the same way (a
`PartBounds.RayDistance` box hit, falling back to centre-distance only if that
somehow misses). But reverting the fix and rerunning the self-test still
passed — the actual cause was that the default scene's built-in stack light
registers only the one tag (`.green`) the deterministic scene drives, not the
full `green`/`yellow`/`red` set a placed `StackLight` gets from
`PartTagManager`, so `stack_light.yellow` did not exist at all. The self-test
was rewritten to check lamp independence against `start_stop_station`'s
"tower" (a `StackLight` placed the normal way, with all three tags) instead.
The distance-metric fix stayed in, verified separately by breaking
`StackLight.HitTest`'s yellow/green Y coordinates so they collided (both
`0.45f`) and watching the self-test fail, then restoring it — a fix that is
real and defensible even though it turned out not to be the fix the original
failure needed.

Covered by a new self-test, `--self-test=operate`
(`tools/test_plan.py` C16), across three scenes since no one scene has every
part this covers: the default scene's conveyor, pusher, emitter and the
single-tag stack light; `start_stop_station`'s "tower" for independent lamp
toggling; `tank_level_control` for the two independent valves. Deliberately
broke `ToggleValve` (`value > 0.5 ? 100.0 : 100.0`, so an open valve could
never close again) and watched the "clicking the open fill valve shuts it
again" assertion fail, then restored it. `python -m pytest -q` unaffected (71
passed); full `test_plan.py --only C` 16 passed.

**UX-38 — End the "Run" collision in the toolbar — done**
*Files:* `engine/src/Editor/SceneToolbarUI.cs:62`.
*Done when:* the mode toggle no longer shares a word and a glyph with the pause
button — `✎ Build` / `👆 Operate` reads unambiguously beside `⏸ Pause` / `▶ Run`.
Drop the ▶ from the mode button whatever the wording.
*Verify:* pause while in Run mode; the toolbar reads unambiguously.
*Size:* S.

`ShowMode` now renders `✎ Build` / `👆 Operate` — no shared word or glyph with
`⏸ Pause` / `▶ Run` anywhere in either state. Paused in Run mode the toolbar
now reads `👆 Operate … ▶ Run`, two buttons that no longer look like the same
claim made twice. Verified by clean build and reading the two labels together
in both mode/pause combinations; no dedicated self-test — this is a string
change with no branching logic to regress.

**UX-39 — Run mode explains itself — done**
*Files:* `engine/src/Editor/SceneEditor.cs`,
`engine/src/Editor/IdleHintUI.cs`.
*Done when:* entering Run mode says what is clickable; a scene with nothing
operable says *that* rather than presenting a dead mode; operable parts
highlight on hover.
*Verify:* enter Run mode on an empty scene, then on a full one.
*Size:* M. *Depends on:* UX-37.

New `SceneEditor.DescribeOperableParts()` counts every part UX-37's dispatch
would recognise and names the kinds present (`"conveyor, pusher, stack
light, panel, emitter"` on the default scene); `IdleHintUI.ShowModeEnteredHint`
forces the existing idle-hint banner visible for a few seconds on every entry
into Run mode, reusing the same forced-visible-then-auto-hide mechanism
UX-30's Demo refusal already uses, so the two share one code path instead of
two ways to interrupt the same label. An empty scene reads *"Nothing in this
scene responds to a click yet…"* rather than presenting Run mode as though it
does something — the same FF-06/FF-23/UX-30 dishonesty class, closed here too.

Hover highlighting reuses UX-37's own hit test rather than a second one:
`PressControlAtRay`'s candidate-search loop was factored out into
`FindOperableTarget`, returning what a ray landed on without applying it, so
the click path and a new mouse-motion handler in Run mode both ask the same
question and can never disagree about what the cursor is over. The highlight
itself is a translucent box sized to the hovered part's own bounds
(`PartBounds.Measure`), positioned in world space rather than reparented
under the part, so a part deleted or replaced mid-hover (a scene reload from
the toolbar, say) cannot take the highlight node down with it. Cleared
explicitly on every mode switch away from Run and on every scene wipe
(`ClearPlacedPartsCore`), rather than left to whatever the next hover update
happens to do.

Covered by a new self-test, `--self-test=modehint` (`tools/test_plan.py`
C17): `DescribeOperableParts()` checked against the real default scene (5
operable parts, then 0 after `ClearAllPlacedParts()`), and
`ShowModeEnteredHint` checked against the actual on-screen label text for
zero, one and several operable parts (singular "1 part" vs plural "3
parts"), using the same standalone-`IdleHintUI` technique
`DemoRefusalSelfTest` (UX-30) already established. Deliberately broke the
pluralization (`"3 part respond"`) and separately dropped `StackLight` from
the operable check (5 → 4, "stack light" missing from the list) and watched
each fail before restoring it. The hover outline itself has no headless
coverage — needs a display, the same class as `--self-test=click` — verified
instead by screenshot: entering Run mode showed *"5 parts respond to a
click: conveyor, pusher, stack light, panel, emitter. Hover to see which."*
at the bottom of the screen, and a synthetic mouse-motion event aimed at the
Control Panel drew a translucent yellow box around its housing, pedestal and
lamps. `python -m pytest -q` unaffected (71 passed); full `test_plan.py
--only A,C,E` 25 passed.

**UX-40 — `Ctrl+S` and `Ctrl+O` survive Run mode — done**
*Files:* `engine/src/Editor/SceneEditor.cs:195`.
*Done when:* save and open work in both modes, or refuse out loud. Blocking the
editing keys in Run mode is correct; silently swallowing save is not (§2.7).
*Verify:* press Ctrl+S in Run mode; the save dialog opens.
*Size:* S.

Moved the Ctrl+S/Ctrl+O check in `_UnhandledInput` ahead of the
`Mode == EditorMode.Run` early-return that used to swallow every key
including these two, and removed the now-unreachable duplicate branches
further down the Edit-mode chain. Undo/redo/move/rotate/delete stay
Run-mode-blocked, unchanged — only save and open moved. Verified by clean
build and by pressing Ctrl+S/Ctrl+O in Run mode in a windowed run; no
dedicated self-test, the existing self-tests that exercise Run mode (e.g.
`--self-test=proppanel`) don't touch the key-dispatch path this changed.

**UX-41 — Show what is being held by hand, and release it in one click — done**
*Files:* `engine/src/Editor/TagInspectorUI.cs`,
`engine/src/Editor/SceneToolbarUI.cs`.
*Done when:* a count of forced tags is visible outside the inspector, with a
"release all" control. A tag left forced silently wins over a PLC that connects
later, and that is a genuinely confusing afternoon.
*Verify:* force three tags, connect a driver, see the warning and clear it.
*Size:* M. *Depends on:* UX-35.

Added `TagTable.ForcedCount` and `ClearAllForces()`, and a toolbar chip
(`🔓 N forced — release`) that appears only while `ForcedCount > 0` and clears
every force in one click via `Tags.ClearAllForces()`. Sits beside the existing
Demo/connection chips in `SceneToolbarUI`, polled the same way they are.
Verified by forcing tags from both the Tag Inspector and the part property
panel (UX-34) and watching the chip appear with the right count, then
clicking it and watching the count drop to 0 and the chip hide; no dedicated
self-test — `TagTable.ForcedCount`/`ClearAllForces` are one-line pass-throughs
over the `_forced` dictionary already exercised by the UX-35 self-test.

---

### Phase 6 — Hold the line — done (UX-42…UX-46)

**UX-42 — `--self-test=scenes` — done**
*Files:* new `engine/src/Sim/SceneTagSetSelfTest.cs`, `engine/src/Main.cs`, a
checked-in expectation file.
*Done when:* every manifest entry loads and its tag set (ids, types, directions)
matches the expectation — catching a template edit that renames a tag out from
under a mapping file.
*Verify:* rename a tag in a template; the test fails naming it.
*Size:* M. *Depends on:* UX-10, UX-13.

Expectation file is `engine/fixtures/scene_tag_sets.json` (moved there by UX-08 so an
export packs it), generated once from
a live `--print-tags` run against each of the five scenes (the same JSON the
tag bus actually sends, not a hand-typed copy that could drift), then
re-sorted by id for a stable diff. Only `id`/`type`/`kind` are captured —
`name` and `value` are not part of a scene's I/O *contract* the way a
mapping file cares about it. The self-test loads each manifest entry in
turn (`LoadDefaultSortingScene` for the built-in scene, `LoadTemplate` for
the four with a path) with the same two-tick settle gap every other
template-loading self-test this plan added already uses, and reports both
directions of mismatch — a tag the fixture expects and the scene no longer
has, and a tag the scene now has that the fixture never promised.

Verified exactly as UX-42's own Verify step asks: renamed the tank
template's `tank` part to `reservoir` (so every one of its tags moved from
`tank.fill/.drain/.level` to `reservoir.fill/.drain/.level`) and watched six
failures, each naming the specific missing or unexpected tag id and its
type/kind, then restored the template file. `python -m pytest -q` (71
passed) and `test_plan.py --only A,C,E` (27 passed) unaffected.

**UX-43 — Wire the exercises into `tools/test_plan.py` — done**
*Files:* `tools/test_plan.py`, `.github/workflows/test-plan.yml`.
*Done when:* all five scenes run headless in the same job that already covers
the sorting line. The spike proved templates simulate headless, so this needs no
display.
*Verify:* `python tools/test_plan.py --only H` passes locally and in CI.
*Size:* M. *Depends on:* UX-22.

New Section H (`section_h`), added beside A/C/E in `SECTIONS` rather than
behind `--gui`, since it needs no display. Runs `tools/try_scene.py --scene
<id>` for each manifest entry as a real subprocess and records H1…H5,
waiting for port 7411 to be free between checks the same way
`EngineProcess.__enter__` already does elsewhere in this file. CI's `--only
A,B,C,E` widened to `A,B,C,E,H`; `docs/TEST_PLAN.md`'s pre-existing manual
"Section H" (the PLCSIM Advanced walkthrough) renumbered to I, and the
"Not automated" section after it to J, to make room without two different
things both being called "Section H".

**A real portability bug found and fixed while wiring this up:** `try_scene.py`'s
tank driver printed its RESULT line with a "±" character. A **piped** Python
child's stdout encoding on Windows is not reliably UTF-8 — this machine's
default piped-stdout codepage encoded "±" as a single cp1252 byte, and
`test_plan.py`'s UTF-8 decode of that byte (correct for output that genuinely
is UTF-8, which is what fixed a real mangled-em-dash bug earlier in this same
file) turned it into U+FFFD, which then crashed `record()`'s own `print()`
trying to write a replacement character through *this* machine's own cp1252
console. Fixed by using plain ASCII (`+/-`) in the RESULT line instead —
matching the ASCII-only convention `check_force_while_paused.py` and
`check_protocol.py` already established for exactly this reason. `section_h`
itself was also written to trust `try_scene.py`'s exit code alone (the
authoritative pass/fail signal UX-21's own "exit 0/1" contract promises)
rather than string-matching the PASS/FAIL line's em dash, so a future
non-ASCII character anywhere else in that line cannot break the runner again.

Verified: `python tools/test_plan.py --only H` — 5 passed. Deliberately
broke the tank driver's controller gain to 0 (so `tank.level` never leaves
0.0) and watched H3 fail with the real numbers
(`level=0.0 setpoint=55.0 band=+/-2.8`) and nothing else affected, then
restored it. Full run `--only A,C,E,H` — 32 passed; `python -m pytest -q` —
71 passed; no orphaned Godot process after any of it.

**UX-44 — `--self-test=modes` — done**
*Files:* new `engine/src/Sim/ModeSelfTest.cs`, `engine/src/Main.cs`.
*Done when:* the Edit/Run contract is asserted **as a pair**: a click selects in
Edit and does not in Run, a control operates in Run and does not in Edit, and
entering Run clears the preview and the selection. `=click` and `=buttons` each
cover one half; nothing covers the switch.
*Verify:* run it; break `SetMode` and watch it fail.
*Size:* M. *Depends on:* UX-37.

**A real gap found while designing the test, not a bug already sitting in
shipped code:** `SelectPartAtRay` (Edit-mode selection) and
`PressControlAtRay` (Run-mode operate, UX-37) never checked `Mode`
themselves — the *only* thing keeping select and operate from firing in the
wrong mode was `_UnhandledInput`'s own dispatch calling the right one for
the current mode. That is fragile in a way this item's own phrasing warns
about: "a click selects in Edit and does not in Run" is a claim about the
methods, not about `_UnhandledInput` alone, and any future caller reaching
either method directly — a future toolbar shortcut, a script, exactly what
this self-test itself needed to do headless — would have silently broken
the contract. Fixed by moving the guard into both methods
(`if (Mode != EditorMode.Edit) return;` / `if (Mode != EditorMode.Run)
return;`), so the invariant holds regardless of caller, the same
defense-in-depth already applied to `TagTable.Force` needing no caller to
check ownership first.

**This surfaced a real ordering bug in UX-37's own self-test**:
`--self-test=operate` (`ClickOperateSelfTest`) called `PressControlAtRay`
without ever calling `SetMode(Run)` first — it happened to pass only because
nothing enforced the mode contract yet. Fixed by adding the explicit
`SetMode(EditorMode.Run)` UX-37's test should have had from the start.

Drives `SelectPartAtRay`/`PressControlAtRay` with synthetic rays, the same
technique UX-37's own self-test uses, against the default scene's pusher —
not the conveyor, which turned out to share a work-plane column with
`sensor_low` (a real overlap between two placed parts, caught by the first
draft of this test picking the wrong target, not a defect in either method).
Also re-verifies the placement-preview half of the contract
(`PanelSelfTest.CheckModeSwitching` already covered it, but restating it
here keeps the full pairwise claim in one file rather than half in each).

Deliberately removed each new guard in turn and watched the matching
assertion fail (*"Edit mode: a click on the pusher leaves extend exactly as
it was"*, then *"Run mode: a click on the pusher does not select it"*),
confirming each is independently load-bearing, then restored both.
`--self-test=click` (windowed) and `python -m pytest -q` (71 passed)
unaffected; full `test_plan.py --only A,C,E` 28 passed.

**UX-45 — Cover non-bit forcing — done**
*Files:* new `tools/check_force_types.py`, `tools/test_plan.py`.
*Done when:* forcing an `int` and a `float` is asserted to reach the bus, the
way `check_force_while_paused.py` does for bits.
*Verify:* run it against an engine; then break `Force` and watch it fail.
*Size:* S. *Depends on:* UX-35.

Same shape as `check_force_while_paused.py`: connect, force a tag, wait for
the specific `update` message to arrive, compare the reported value. Wired
into `test_plan.py` as **G7**, run against the light-curtain template
(`--scene=res://templates/light_curtain_sorting.json`), the same
`EngineProcess` pattern G6 already uses.

**A real design fact found while picking which tag to force, not a bug:**
the first draft targeted `level_readout.value` (an `int` **output**) and
failed with "no update arrived" even though Force itself worked correctly.
`TagBusServer.SendUpdates` only ever echoes `TagKind.Input` tags — by
design, documented in its own comment ("the client already knows its own
output values") — so forcing an output can never produce an `update`
regardless of whether `Force` is broken. Retargeted to input tags
specifically (`tall_count.count`/`short_count.count` int,
`height_gauge.height` float — the light-curtain template is the one shipped
scene with both), matching `check_force_while_paused.py`'s own
`kind == "input"` filter, which was already quietly making the same
correct choice.

Verified: `python tools/check_force_types.py` against a live light-curtain
engine — `RESULT OK (short_count.count=42, height_gauge.height=12.5)`.
Deliberately broke `TagTable.Force` to silently refuse any non-bit tag (the
exact §2.8 bug this whole plan opened with) and watched both forced values
time out with `"no update arrived within 5s"`, then restored it. Full
`test_plan.py --only A,C,E,G` — 35 passed; `python -m pytest -q` — 71
passed.

**UX-46 — Document all of it — done**
*Files:* `docs/TEST_PLAN.md`, `README.md`, `docs/GETTING_STARTED.md`.
*Done when:* the new sections sit beside the existing `A`–`G` ones, and §5 of
this document has been folded into the user-facing docs rather than left in a
planning file.
*Verify:* a new user follows the docs and operates a component within five
minutes of first launch.
*Size:* S.

`docs/TEST_PLAN.md` was kept current incrementally through this whole phase
(every C, G and H item landed alongside its own commit, not batched here),
so this item's own work was three things: a real, current `--gui` run rather
than trusting the stale 2026-08-12 snapshot at the bottom of the file
(**47 passed, 0 failed, 417s** — up from 20, with a note on what grew it);
folding §5 into `docs/GETTING_STARTED.md` as a new **"Operating any
component by hand"** section right after Edit/Run mode, covering all three
routes in order of directness — click the part in Run mode (UX-37), the
part's own property panel (UX-34), and the Tag Inspector's Force button
(UX-35/41) — plus a pointer to `tools/try_scene.py` and the **🧪 Try**
button (UX-21/31/33) exactly where the old `connect --driver mock` warning
(§2.6) needed one; and refreshing `README.md`'s "Manual scene control"
bullet, stale since UX-37 landed (it still said only the operator panel
answered a click), plus a new bullet naming the per-scene exercises.

Every specific claim in the new prose was checked against the running app
or the self-tests that already prove it, not written from memory: the exact
banner text (*"5 parts respond to a click: conveyor, pusher, stack light,
panel, emitter. Hover to see which."*), the mode button's two labels, the
forced-tags chip's exact text, and the toolbar/F5 button labels all match
what UX-37…UX-41's own verification already captured by screenshot.
---

## 4. Per-scene exercise specification

The behaviour spec the C# profiles (UX-17…UX-20) and `try_scene.py` (UX-22)
share. Each is a small, honest control program — the same program a student
would be asked to write, which is the point: read it, then delete it and write
your own.

### 4.1 `sorting-by-height` — the reference line

**Already exists** as `tools/drive_engine.py`; fold it in behind the runner
rather than rewriting it.

*Drives:* `conveyor.rotate` on; `emitter.emit` pulsed at 1.5 s;
`pusher.extend` 0.9 s after `sensor_high.detect` rises, held 0.5 s.
*Asserts:* deterministic run gives exactly `tall=5 short=5` — the existing
E1/E2 contract, unchanged.

### 4.2 `start-stop-station` — momentary buttons and a latching E-stop

*Drives:* `panel.start` → `belt.rotate` + `tower.green`; `emitter.emit` pulsed;
`part_present.detect` rising edges counted into `produced.value`; `panel.stop`
→ belt off + `tower.yellow`; drop `panel.estop` → belt off + `tower.red`, and
refuse to restart until `panel.reset`.
*Asserts:* `counter.count` advances; the belt stops within 200 ms of the E-stop;
`panel.start` after an E-stop does **not** restart the belt; lamp state and belt
state never disagree.
*Teaches:* why `panel.estop` reads **true when healthy** — it is wired normally
closed, and a program that assumes "true means stopped" fails here loudly.

### 4.3 `tank-level-control` — the analog one

*Drives:* read `tank.level`, modulate `tank.fill` and `tank.drain` to hold a
setpoint, mirror the level into `level_readout.value`.
*Asserts:* level enters a ±5 % band around setpoint and stays for 10 s; then
**re-run the same controller at a low setpoint and show it behaves differently**
— outflow follows Torricelli, so process gain varies with level and a controller
tuned at 80 % overshoots at 20 %. That contrast *is* the lesson, so print both
settling times side by side.
*Teaches:* float tags end to end, and why one PID tuning is not enough.

### 4.4 `light-curtain-sorting` — sorting on a measurement

*Drives:* `belt.rotate` on, `emitter.emit` pulsed; read `height_gauge.height`
while `height_gauge.blocked`; compare against a threshold **declared at the top
as a named constant**; fire `diverter.extend` for tall boxes, handshaking on
`diverter.extended`/`.retracted` rather than on a timer.
*Asserts:* `tall_count.count + short_count.count` equals the number emitted —
nothing lost, nothing double-counted; no box is diverted whose measured height
was below threshold.
*Teaches:* the difference between two bits and one measurement — move a single
constant and the line re-sorts, with no rewiring.

### 4.5 `roller-line-weighing` — checkweighing and material

*Drives:* `infeed.rotate` and `scale.rotate` on; `emitter.emit` pulsed with metal
enabled; read `scale.weight` once a box settles; publish it to
`weight_readout.value`; read `metal_check.detect`.
*Asserts:* `scale.weight` is non-zero and stable (±1) while a box is on the
scale and returns to zero between boxes; `metal_check.detect` asserts for metal
cartons and **not** for cardboard — the material-aware sensing claim, checked
rather than asserted in a README; `outfeed.count` advances.
*Teaches:* an inductive sensor is not a second presence sensor.

---

**Delivered in full on 2026-08-23.** `tools/try_scene.py` originally implemented
the *driving* half of every scene above and only the weakest of the assertions —
"both counters advanced", "the level reached the band" — which pass while the
thing the scene exists to teach is broken. Every §4 assertion above is now
actually checked, and every scene now runs as a shift does rather than as a
wiring test: start, produce, stop feeding, drain, stop.

| Scene | Was | Now |
|---|---|---|
| 4.1 sorting | `tall=5 short=5`, exact | unchanged — this is E1/E2's regression contract, and its timing is not something to perturb for tidiness |
| 4.2 start/stop | interlocks only; **`produced=0` every run**, because the whole script fitted inside one emitter half-period | interlocks, then a real production run; `counter.count` asserted to advance, and the E-stop **timed** against §4.2's 200 ms (measured 32–47 ms) |
| 4.3 tank | one setpoint, gain 4 — which saturates the valve instantly, so it was bang-bang control hiding the very nonlinearity the scene teaches | two setpoints with a modulating gain, both settling times printed side by side: **10.4 s to reach 70 %, 20.3 s to reach 20 %**, with the reason stated |
| 4.4 light curtain | `tall > 0 and short > 0` | conservation — `tall + short` must equal cartons emitted, after a drain phase, so a diverter that drops or double-counts a carton fails |
| 4.5 roller | `outfeed > 0` and metal seen once | cartons weighed counted by scale rising edges, the scale required to return to zero between them, and metal detections asserted **non-zero and strictly fewer** than cartons — the difference between an inductive sensor and a presence sensor, checked |

Each new assertion was verified by a deliberate break: removing the drain phase
(*"10 cartons emitted but 8 counted — lost 2"*), setting the template's
`metal_every` to 1 (*"fired for every carton (7 of 7) — it is behaving like a
presence sensor"*), and tightening the E-stop limit to 10 ms (*"took 47 ms"*).

One honest departure from the spec above: §4.3 asks both setpoints to be held
for 10 s, and only the **high** one is held to that standard. The low one is
required to reach its band. Demanding an identical hold at both ends would be
demanding the Torricelli nonlinearity not exist — the drain valve loses
authority as the tank empties, which is the whole lesson.

---

## 5. Operating components by hand

### 5.1 What this should feel like

Click a conveyor. Turn it on. Watch it move.

That is the whole requirement, and it should need no PLC, no Python, no script,
and no knowledge of tag ids. It is how you find out what a part *is* — what a
light curtain reports, how a pusher's feedback bits sequence, how a tank's gain
changes with level — before you write a line of control code against it.

This is the specification for Phase 5. Everything in it is a statement about
what should be true, with the current state marked.

### 5.2 What you could do before Phase 5, and why it was awkward

Written before Phase 5 landed and kept as the record of the gap it closed. All
three routes below now reach the whole part library — **§5.7 is the current
state**; read that first if you only want to know how the app behaves today.

Three routes existed, and each stopped short:

| Route | Reaches | Stops at |
|---|---|---|
| **Click it** — `F1` → Run mode, click the part | Control Panel caps only | `PressControlAt` ray-tests for `ButtonPanel` and nothing else *(§2.7 → UX-37)* |
| **Force it** — Tag Inspector → **Force** | any `bit` tag, input or output | silently does nothing on `int`/`float` *(§2.8 → UX-35)* |
| **Run the demo** — 🎬 Demo | `sorting-by-height` only | silent no-op on all four templates *(§2.1 → UX-16…UX-20)* |

So turning a conveyor on meant: know a conveyor owns a `.rotate` tag; know
the part's **Name** is the tag prefix; find `belt.rotate` in a 13–16 row list on
the other side of the screen; press Force there. **Three indirections between
the thing on screen and the switch that turns it on** — and the part's own
property panel, already open and already showing its settings, says nothing
about its I/O at all (§2.9).

Two things that are worth knowing and will stay true:

* **Forcing is sticky.** The button reads **UNFORCE** while a tag is held, and a
  tag left forced will beat a driver you connect later. UX-41 makes that
  visible.
* **Forcing works while paused.** Press `Space`, force a sensor, and the value
  still reaches a connected driver — that was FF-14, and
  `tools/check_force_while_paused.py` keeps it fixed.

Also worth knowing: forcing a **bit output** genuinely drives the machine. Parts
read forced values via `Tags.TryGetVisible`, so this is not a display trick —
the belt really turns.

### 5.3 Edit mode and Run mode

Mode changes **what a click means**, and nothing else. It does not start or stop
the simulation: physics, the emitter and the demo all run in Edit mode. Time is
controlled separately by `Space`, `Ctrl+R` and the 0.25×–4× rate selector.

| | Edit mode | Run mode |
|---|---|---|
| Left click | select a part, or place the palette part | operate the part — every operable type, not just the Control Panel (UX-37) |
| `M` / `R` / `Del` / `Ctrl+D` | move / rotate / delete / duplicate | — |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo | — |
| `Ctrl+S` / `Ctrl+O` | save / open | save / open — they survive Run mode (UX-40) |
| `F1` | → Run | → Edit |
| `Space`, `Ctrl+R`, rate selector | work | work |
| `C`, WASD, mouse orbit | work | work |
| Parts palette | shown | hidden |
| Physics, sensors, counters | running | running |

Entering Run mode cancels a placement preview and drops the selection; a move in
progress is cancelled the way `Escape` cancels it, so the part stays where it
started rather than being lost.

### 5.4 Component by component

What operating each part should do. **Today** is what works right now: ✓ works,
◐ works but only through the Tag Inspector by tag id, ✗ impossible. Written
before UX-34 and UX-37 landed and left as the historical record of the gap
they closed — every row still marked ◐ for "only through the Tag Inspector"
now also has a real toggle/slider on the part's own property panel (UX-34)
and, for the actuator rows specifically, a working click in Run mode (UX-37,
§5.3). The two tags marked ✗ for their type — the tank's float commands and the
display's `int` — were unblocked by UX-35. Of the ✗ marks that ask for a readout
**on the part itself**, only the **Weight Conveyor**'s is still open: the Light
Array's was wrong when written (that row is corrected below), and the Level Tank
and Digital Display have drawn theirs in 3D since 2026-08-12. See §5.7.

Place the part, name it in the properties panel — the name is the tag prefix, so
a pusher named `reject` gives `reject.extend` — then:

#### Transport

| Part | Its I/O | Operating it should mean | Today |
|---|---|---|---|
| **Conveyor Belt** | `.rotate` bit out | A run/stop toggle on the part. Belt surface moves, boxes ride. Raise `speed` and boxes outrun a diverter | ◐ |
| **Roller Conveyor** | `.rotate` bit out | Same toggle; rollers spin at true surface speed, a box tracks without slipping | ◐ |
| **Weight Conveyor** | `.rotate` bit out · `.weight` int in | Run toggle, plus a live weight readout on the part — non-zero while a box sits on it, zero between | ✓ since LE-08 |

#### Sensors

Sensor tags are **inputs the simulation owns** — you exercise them by making
something pass. An override is still worth having: it is how you ask "what does
my PLC do if this sensor is stuck on?"

| Part | Its I/O | Operating it should mean | Today |
|---|---|---|---|
| **Photoelectric** | `.detect` bit in | Live indicator on the part, plus a force-on/force-off override | ◐ |
| **Retroreflective** | `.detect` bit in | Same. Run it beside a photoelectric to see it catch matt boxes the diffuse one misses | ◐ |
| **Inductive** | `.detect` bit in | Same. Set the Emitter's `metal_every` to 1 then 0 and watch it react to metal only | ◐ |
| **Light Array** | `.height` float in · `.blocked` bit in | A live height readout on the part — the number changes per box, which is the whole point of the part | ◐ — **this ✗ was wrong when written**: `LightArray` has drawn `N mm` on a `Label3D` above the curtain since 2026-08-12, ten days before this plan |

#### Actuators

| Part | Its I/O | Operating it should mean | Today |
|---|---|---|---|
| **Pneumatic Pusher** | `.extend` bit out · `.extended`, `.retracted` bit in | An extend/retract toggle, with both feedback bits shown. They flip in sequence and are never both true. Hold it out and the line backs up behind it | ◐ |
| **Ramp / Chute** | none — physical | Nothing to operate. Push a box onto it and lower `incline`; `incline` and `friction` are a matched pair, so boxes stop sliding | ✓ |

#### Process

| Part | Its I/O | Operating it should mean | Today |
|---|---|---|---|
| **Box Emitter** | `.emit` bit out | A "emit one box" button — one box per **rising** edge, so holding it true does not stream boxes. That behaviour is worth making obvious rather than discovering | ◐ |
| **Box Remover** | `.<count_tag>` int in | A live count on the part, and a reset | ◐ readout only |
| **Level Tank** | `.fill`, `.drain` float out · `.level` float in | **Two valve sliders and a level bar.** This is the part that most needs it and least has it | ✗ — both commands are floats *(§2.8)* |

#### Operator

| Part | Its I/O | Operating it should mean | Today |
|---|---|---|---|
| **Control Panel** | `.start`, `.stop`, `.reset`, `.estop` bit in · `.green`, `.red` bit out | `F1` → Run, click the caps. Start/Stop/Reset are **momentary** — one clean scan per click, however long you hold. The mushroom is **maintained**, wired normally closed, so `.estop` is **true while healthy** | ✓ |
| **Stack Light** | `.red`, `.yellow`, `.green` bit out | Click a stage to toggle it | ◐ |
| **Digital Display** | `.value` int out | Type a number and see it on the display | ✗ — `int` output *(§2.8)* |

The pattern is plain: **the one part built for direct operation works, and the
other fourteen are reachable only by tag id, or not at all.** UX-34 and UX-37
close that; UX-35 unblocks the two ✗ rows.

### 5.5 Two minutes with each scene

What you can do on first launch. Every scene now has all three: a 🎬 **Demo**
that runs it with no PLC (UX-16…UX-20), a 🧪 **Try** button that runs the
asserted exercise (UX-21/31/33), and full hand operation — click the part in Run
mode (UX-37) or use its own property panel (UX-34). Nothing here is blocked any
more; the *(was blocked)* notes mark what Phase 5 opened.

**Sorting by height** — press 🎬 **Demo**. Belt starts, boxes emit, tall ones
divert down the chute, `counter.tall` and `counter.short` climb.

**Start / stop station** — `F1` → Operate and click the belt to start it, or
flip its toggle on the property panel; the emitter's **Emit one** button drops a
carton per click. Watch `part_present.detect` pulse and `counter.count` climb.
Press the E-stop and note `panel.estop` goes **false** because it is normally
closed. `produced.value` takes a typed number *(was blocked — int output)*.

**Tank level control** — the best scene in the set for analog behaviour, and the
one Phase 5 changed most *(was blocked entirely)*. Click the tank's inlet pipe
to open the fill valve, or drag its slider on the property panel, and watch the
level rise with the rate falling off as it climbs — Torricelli, not a ramp.

**Light curtain sorting** — start the belt and emit; watch `height_gauge.height`
change per box; stroke `diverter.extend` by hand as a tall one arrives and watch
`tall_count.count` rise. Still the best scene for learning what forcing does,
because you stand in for the PLC one box at a time.

**Roller line with weighing** — start `infeed` and `scale` and emit; watch
`scale.weight` settle while a box sits on the scale and return to zero after.
Set the emitter's **Metal every Nth** to 1 on its property panel (LE-03)
and confirm `metal_check.detect` follows metal only.
`weight_readout.value` takes a typed number *(was blocked)*.

### 5.6 Checking the machinery, without the 3D app

How to check a build before blaming your PLC. All of this ships today. The list
below is the state after Phase 6: **twenty** engine self-tests, not the eight
that existed when this was written.

| Command | Checks |
|---|---|
| `python tools/test_plan.py` | Everything below plus determinism and robustness; `--gui` adds the display-dependent click path |
| `python -m pytest -q` | The 73-test Python suite: tag model, protocol, Modbus, OPC UA, Siemens |
| `python tools/try_scene.py --scene <id>` | Drives one shipped scene the way a PLC would and asserts the result; `--list` names all five *(UX-21/22)* |

**Engine self-tests** — `godot --headless --path engine -- --self-test=<name>`:

| `<name>` | Checks |
|---|---|
| `buttons` | Panel momentary and latching behaviour, from the tag side |
| `io` | Rename and I/O export |
| `scene` | Scene save/load round-trip, every part type |
| `templates` | Every shipped template loads and registers its I/O |
| `parity` | The C# and Python tag models agree |
| `layout` | The F5 modal still fits on screen |
| `force` | The Tag Inspector forces `int` and `float`, not just `bit` *(UX-35)* |
| `demo` | Demo picks the right profile per scene, and refuses honestly otherwise *(UX-16/30)* |
| `startstop`, `tank`, `lightcurtain`, `roller` | One per template profile: the §4 behaviour, physics-timed *(UX-17…UX-20)* |
| `refusal` | Demo's refusal reaches the UI, not just the console *(UX-30)* |
| `proppanel` | The part property panel drives live I/O, bit/int/float × output/input *(UX-34)* |
| `operate` | Run mode's click operates the part it lands on, not just the panel *(UX-37)* |
| `modehint` | Entering Run mode says what is clickable, or says plainly that nothing is *(UX-39)* |
| `tryscene` | "Try this scene" finds the right exercise, refuses honestly otherwise *(UX-31/33)* |
| `scenes` | Every scene's tag set matches `engine/fixtures/scene_tag_sets.json` *(UX-42)* |
| `modes` | The Edit/Run contract as a pair: select only in Edit, operate only in Run *(UX-44)* |
| `click` **(needs a display)** | A synthesized mouse click reaching a tag — run without `--headless` |

**Wire-level checks** — each needs an engine already running:

| Command | Checks |
|---|---|
| `python tools/check_protocol.py` | `hello`/`describe`/`update` carry exactly the documented fields |
| `python tools/check_force_while_paused.py` | A forced input still reaches a driver while paused |
| `python tools/check_force_types.py` | Forcing an `int` and a `float` both reach the bus *(UX-45)* |
| `python examples/fake_plc.py` | A fake S7-1500 running the real SCL logic over OPC UA — sorting scene only |

The three gaps this section originally named are closed: the two modes are
tested as a pair (`modes`, UX-44), non-bit forcing is covered
(`check_force_types.py`, UX-45), and all five scenes run in the same headless job
as Section H (UX-43).

### 5.7 Where it landed — the current state

§5.2 through §5.5 above were written against the app as it was before Phase 5.
This is what shipped. The three **by hand** routes all end in the same
`TagTable.Force` call, so they are equally sticky and equally visible in the
forced-tags chip; the fourth writes with `TagTable.Set`, the way a driver does,
so a demo leaves nothing pinned behind it.

| Route | Reaches | Where |
|---|---|---|
| **Click the part** — `F1` → **👆 Operate**, click it | Conveyors, roller and weighing decks, pushers, emitters (one carton per click), each stack-light lamp, each tank valve, and the panel caps | UX-37 |
| **The part's own panel** — select it; its **I/O** section sits under its settings | Every tag the part owns: a toggle for `bit` outputs, a spin box for `int`, a slider for `float`, **Emit one** for the emitter's rising edge, and a live readout plus an **Override** checkbox for inputs | UX-34 |
| **The Tag Inspector** — top right, **Force** | Any tag in the scene by id, any type, with a typed value field beside the button | UX-35/36 |
| **Run the exercise** — 🎬 **Demo**, or 🧪 **Try** | All five scenes, no PLC and no Python for Demo; `try_scene.py` for the asserted version | UX-16…UX-22, UX-31/33 |

Three things worth knowing, all still true and now all visible:

* **Forcing is sticky**, whichever route set it. A tag left held beats a driver
  that connects later — which is why the toolbar carries a
  **`🔓 N forced — release`** chip while anything is held, clearing every force
  in one click (UX-41).
* **Forcing works while paused** — FF-14, kept fixed by
  `tools/check_force_while_paused.py`, and now `check_force_types.py` for
  non-bit tags.
* **Mode is not time.** `✎ Build` / `👆 Operate` changes only what a click
  means; `⏸ Pause` / `▶ Run` and the rate selector control time. Those two
  buttons no longer share a word or a glyph (UX-38), and entering Operate names
  what is clickable — or says plainly that nothing is (UX-39).

What Phase 5 deliberately did **not** do: add a live readout to every part's own
3D body. Four parts have one — the Light Array's `N mm` above the curtain, the
Level Tank's `N.N %` and liquid column, the Digital Display's 7-segment panel,
and (since LE-08) the Weight Conveyor's `N g` above the scale — and the rest
report on the property panel and in the Tag Inspector, one click away, rather
than floating over the machine. **Every ✗ and ◐ in §5.4's table is now closed**:
the Light Array's ✗ was wrong when it was written (see the note there), and the
Weight Conveyor's was the last one standing.

**One claim outside this plan was found false while reviewing it (2026-08-23):**
`README.md` advertised *"Floating 3D billboard labels above components with
interactive live forcing buttons"*. `engine/src/Editor/FloatingTagBadge3D.cs`
implements exactly that — and **nothing in the repository ever constructs one**.
The class is unreferenced; no badge can appear in any scene. The README bullet
has been rewritten to describe the three routes that do exist. The dead class is
left in place at the time for a deliberate decision rather than deleted in a
docs pass — but shipping the claim was the same
report-it-working-while-doing-nothing failure as §2.1.

That finding prompted a full sweep of the app for others like it — every type,
key, flag, button and property slider — which found two more (a property slider
that moves nothing, and a part that measures something and displays it nowhere)
and cleared everything else. The sweep and the twelve items that closed it are
in **[`docs/LOOSE_ENDS_PLAN.md`](LOOSE_ENDS_PLAN.md)** (LE-01…LE-12, all done):
the badge class was deleted, the dead slider fixed, the Weight Conveyor given
its readout, and two new checks added so none of the three can come back —
`--self-test=partsettings` (C21) fails on a settings control that reaches
nothing, and A6 fails on a type nothing references.

---

## 6. Decisions

### Settled

* **A packaged binary is in scope** — Phase 0, **Windows and Linux**. Both
  presets exist and CI already runs Linux headless, so the second target is
  close to free. macOS deferred (§7).
* **Per-scene exercises live in both C# and Python, C# first** — profiles repair
  the "Watch it run" button with zero install; `try_scene.py` adds the
  assertions and proves the engine↔sidecar seam. One shared spec (§4).
* **The four templates get no mapping files or PLC programs yet.** Exercises
  first; revisit when people are building against them (§7).
* **Order of work:** Phase 3 → Phase 5 → Phase 1 → Phase 2 → Phase 4 → Phase 6,
  with Phase 0 in parallel throughout.
* **Headless scene loading is viable** — settled by the spike in §0, not by
  argument. UX-10 is **S**, and Phase 2's exercises can run in Linux CI.
* **`--deterministic --scene=` — rejected outright (UX-12), not made
  compatible.** Making templates deterministic too would be a large piece of
  work buying exact-count assertions §4's band-based ones don't need.
  Revisit only if the band-based assertions prove flaky in practice.
* **UX-34 vs UX-37 — the panel leads.** Both landed, as complementary, not
  competing, routes to the same `TagTable.Force` call: UX-34 for a part not
  yet on screen or off camera, UX-37 for the part you are already looking at.

### Still open

1. **UX-03 — how Python ships.** `PACKAGING.md` recommends
   PyInstaller-per-platform, but nothing has been tried. Best decided *after*
   UX-01 proves a binary can be produced at all.
2. **UX-09 — code signing.** Costs a certificate and a process; not signing
   costs every first-time user a SmartScreen warning. Needs deciding before the
   first public release, not before the first build.

---

## 7. Explicitly not in scope

* **macOS packaging** — no preset exists, and it cannot be tested from here.
  Deferred, not rejected; `SpawnInTerminal` already handles macOS terminals when
  someone picks it up.
* **Mapping files and PLC programs for the four templates** — decided against
  for now (§6). The sorting line keeps its `Sorting.scl`, `fake_plc.py` and three
  mappings; the templates get exercises (§4) and nothing PLC-side yet.
* **Rewriting the templates themselves** — their part layouts are fine; only the
  things that *drive* them are missing.
* **New parts, new drivers, new protocols.**
* **Any change to the tag bus wire protocol.**
* **Visual and 3D polish**, which is finished (FF-24…FF-26, FF-31).

---

## Appendix A — Work item index

`S` under half a day · `M` one to two days · `L` three to five days.

| # | Item | Phase | Size | Depends on | Status |
|---|---|---|---|---|---|
| UX-01 | Produce a Windows and a Linux binary at all | 0 | M | — | done |
| UX-02 | Correct `PACKAGING.md` with what happened | 0 | S | UX-01 | done |
| UX-03 | Decide and implement how Python ships | 0 | L | UX-01 | done |
| UX-04 | Make the engine find a bundled sidecar | 0 | M | UX-03 | done |
| UX-05 | Decide "one file" or "one folder" | 0 | S | — | done |
| UX-06 | Settle what ships alongside the binary | 0 | S | UX-03 | done |
| UX-07 | A release CI job | 0 | L | UX-01 | done |
| UX-08 | Fix the fixture path that breaks exported self-tests | 0 | S | UX-01 | done |
| UX-09 | Decide on code signing, or warn honestly | 0 | S / L | — | done |
| UX-10 | Load `--scene=` headless | 1 | S | — | done |
| UX-11 | Make `--scene` and `--demo` compose | 1 | S | — | done |
| UX-12 | Reject `--deterministic --scene=` | 1 | S | — | done |
| UX-13 | A scenes manifest | 1 | M | — | done |
| UX-14 | `--print-tags` | 1 | S | — | done |
| UX-15 | Report the loaded scene's real name at startup | 1 | S | — | done |
| UX-16 | A per-scene profile mechanism in `DemoDriver` | 2 | M | UX-13 | done |
| UX-17 | `start-stop-station` profile | 2 | S | UX-16 | done |
| UX-18 | `tank-level-control` profile | 2 | M | UX-16, UX-35 | done |
| UX-19 | `light-curtain-sorting` profile | 2 | M | UX-16 | done |
| UX-20 | `roller-line-weighing` profile | 2 | S | UX-16 | done |
| UX-21 | `tools/try_scene.py` | 2 | M | UX-10, UX-13 | done |
| UX-22 | Assertions for all five scenes | 2 | M | UX-21 | done |
| UX-23 | One cross-platform launcher | 3 | M | — | done |
| UX-24 | Purge the author's machine from the repo | 3 | S | — | done |
| UX-25 | Fix `demo` → `connect` in shipped material | 3 | S | — | done |
| UX-26 | Refresh stale facts | 3 | S | — | done |
| UX-27 | Launcher must not kill port 7411 silently | 3 | S | — | done |
| UX-28 | Launcher must not auto-start a driver | 3 | S | — | done |
| UX-29 | A Linux launcher | 3 | S | UX-23 | done |
| UX-30 | The Demo button refuses honestly | 4 | S | UX-16 | done |
| UX-31 | A "Try this scene" affordance | 4 | M | UX-21 | done |
| UX-32 | Keep "what this scene teaches" reachable | 4 | M | UX-13 | done |
| UX-33 | F5's empty state offers the exercise | 4 | S | UX-31 | done |
| UX-34 | Live I/O in the part property panel | 5 | L | UX-35 | done |
| UX-35 | Force any tag type, not just bits | 5 | M | — | done |
| UX-36 | Until UX-35 lands, refuse audibly | 5 | S | — | done |
| UX-37 | Click a component in Run mode to operate it | 5 | L | — | done |
| UX-38 | End the "Run" collision in the toolbar | 5 | S | — | done |
| UX-39 | Run mode explains itself | 5 | M | UX-37 | done |
| UX-40 | `Ctrl+S` and `Ctrl+O` survive Run mode | 5 | S | — | done |
| UX-41 | Show what is held by hand; release in one click | 5 | M | UX-35 | done |
| UX-42 | `--self-test=scenes` | 6 | M | UX-10, UX-13 | done |
| UX-43 | Wire the exercises into `tools/test_plan.py` | 6 | M | UX-22 | done |
| UX-44 | `--self-test=modes` | 6 | M | UX-37 | done |
| UX-45 | Cover non-bit forcing | 6 | S | UX-35 | done |
| UX-46 | Document all of it | 6 | S | — | done |

**Totals:** 46 items — 24 S, 17 M, 4 L, 1 S-or-L (UX-09). By phase: 0→9, 1→6, 2→7, 3→7, 4→4, 5→8, 6→5.
**Status:** 46 done, 0 open.
**Critical path to a first release:** the app half is done — UX-24 → UX-26 →
UX-35 → UX-34 → UX-13 → UX-10 → UX-16 → UX-17…UX-20 all landed. What is left of
the path is the packaging half: **UX-01 → UX-03 → UX-04 → UX-07**, with UX-08
folded into UX-07's job (two fixture paths now, not one).
