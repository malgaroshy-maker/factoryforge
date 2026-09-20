# FactoryForge 🏭

[![Godot 4.7](https://img.shields.io/badge/Godot-v4.7.2--mono-blue?logo=godotengine)](https://godotengine.org/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green?logo=python)](https://www.python.org/)
![.NET 8.0](https://img.shields.io/badge/.NET-8.0-purple?logo=dotnet)
[![Tests](https://img.shields.io/badge/Tests-73%20Passed-brightgreen)](tests/)
[![Siemens S7-1500](https://img.shields.io/badge/Siemens-S7--1500%20Verified-009999?logo=siemens)](examples/tia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*A free, open 3D factory simulator for learning PLC programming — a modern, customizable open replacement for Factory I/O.*

**Author & Creator:** Mahamed Algaroshy (محمد الجروشي)  
**Repository:** [github.com/malgaroshy-maker/factory](https://github.com/malgaroshy-maker/factory)

---

## 🌟 Overview

**FactoryForge** allows students, automation engineers, and software developers to write PLC logic (Ladder Diagram, SCL, Function Block Diagram) in TIA Portal, OpenPLC, Node-RED, or Ignition SCADA, and watch it drive a real-time 3D physics-based factory in Godot 4.7.

No accounts, no per-seat subscription fees, and 100% open for custom part & driver creation.

![FactoryForge 3D Engine & Scene Editor Demo Video](docs/images/demo_video.gif)

```
┌────────────────────────────┐          ┌──────────────────────────┐
│  SIM ENGINE (Godot 4.7/C#) │          │  DRIVER SIDECAR (Python) │
│                            │          │                          │
│  3D render + Jolt physics  │  tag bus │  asyncua      (OPC UA)   │
│  scene editor / voxel grid │ ◄──────► │  pythonnet    (PLCSIM)   │
│  29-part library           │    WS    │  python-snap7 (S7)       │
│  tag registry (authority)  │   JSON   │  built-in     (Modbus)   │
└────────────────────────────┘          └──────────────────────────┘
```

---

## ✨ Key Features

* 🧲 **Material-aware sensing**: items carry a material, so an inductive sensor sorts metal from cardboard instead of being a second presence sensor.
* ⚠️ **Break the machine on purpose (`⚠ Fault`)**: arm the fault tool and click a conveyor or a pusher. The drive stops **while its command is still on** — the belt disobeys, its fault beacon lights, and `<part>.fault` goes true for the PLC to read. A jammed cylinder freezes mid-stroke rather than returning home, so the limit switches are the only honest thing to read, and a seized tank valve holds its opening — the nastier failure, because the process keeps moving and the controller's own output cannot tell you. Until this existed every actuator did exactly what it was told, which made half of real PLC work unteachable: an interlock exists precisely because the plant does not always obey.
* 🎚️ **Analog I/O**: Float tags end to end — a modulating valve, a level transmitter, a heater against a first-order thermal plant, a variable-speed drive whose *actual* speed lags the reference you gave it, and a needle gauge to read any of them in the scene. Enough to write a real PID against a nonlinear process, and to measure why the integral term exists rather than being told.
* ⏯️ **Run / Pause / Reset & time scale (0.25×–4×)**: freeze the line mid-cycle to read every sensor and actuator at that instant, or slow a fast sequence down to watch an interlock. The PLC stays connected while paused.
* 🎮 **Godot 4.7 C# 3D Engine & Jolt Physics**: 60 FPS 3D rendering with 4× MSAA, soft shadows, SSAO, glow on the parts that are meant to be lights, and continuous collision detection. Conveyor drums turn at true surface speed, cylinders cushion into their end stops, stack-light lamps cast real light onto what is beside them, and the camera frames the scene you just opened instead of leaving you looking at a control panel.
* 📦 **Real rigid-body cartons**: mass from carton density, friction tuned per material pair (rubber belt, cardboard, steel chute), boxes that accumulate behind a blocked diverter instead of passing through it.
* 🏠 **Start screen with eight templates**: open on a chooser rather than cold into one demo. Each template teaches a different thing — momentary buttons and a latching E-stop, analog level control with a nonlinear process, sorting on a measurement instead of two bits, a checkweigher with metal detection, a pick-and-place gantry you sequence on feedback rather than timers, a thermal loop where proportional control alone visibly parks short of setpoint, and a buffer where product accumulates behind a blade stop and is released by belt travel rather than by a timer — plus recent scenes and the full key list. **`F12`** brings that key list back once a scene is open.
* 🕹️ **Operate any component by hand (`F1`)**: switch the toolbar from **`✎ Build`** to **`👆 Operate`** and click a conveyor, a pusher, a stack light lamp or a tank valve directly — not just the operator panel's Start/Stop/Reset/E-stop. **Every shipped scene answers to its panel**: Start runs the line, Stop stops it, the mushroom latches a trip that only Reset clears, and the panel's setpoint pot is the one number that scene is about — the level to hold, the height that counts as tall, the weight that counts as a reject, the batch to make. Drag the knob mid-run and the line changes what it does, with no code edited. A banner names what's clickable, hovering outlines it, and every part's own property panel carries a live toggle or slider for its I/O too, so you can see what a part does before writing a line of PLC code against it.
* 🏭 **A shop to build it in**: the line stands on poured concrete with a control joint around every two-metre bay, inside clad walls five metres to the eaves. Both are generated in code — this project ships no image assets — and both are there for a reason beyond looking better: with nothing around it, a conveyor could be two metres long or twenty, and a floor bay is a ruler lying under the machine.
* 📋 **Every template tells you what to build**: press **`T`** and the scene says what it is asking of you — the task, the tags your program drives and reads, and how you know it works. The lesson used to live only in the Python test harness, which is the last place somebody learning PLC programming will look.
* 🔎 **A tag list you can find things in**: search it, collapse it by machine, or show just the half the PLC writes. A tag you have **forced** by hand marks its own name and raises a count with a one-click release — because a forgotten force is a value that disagrees with the simulation on purpose, and it explains more "why is my program not working" than anything else.
* 🛠️ **3D Scene Editor Suite**: a searchable palette that tells you what each part does and which tags it will register. **The part stays in your hand after you place it**, so a line of six conveyors is six clicks rather than six trips back to the palette — the lit palette button says what you are holding, and **`Esc`** puts it down. **`Ctrl+D`** lands its copy clear of the original and selects it, so pressing it again walks a line across the grid; the **arrow keys** nudge the selection one cell at a time. Click a placed part and **drag it** to a new cell — one gesture, one **`Ctrl+Z`** — with grid snapping, rotation (**`R`**), a selection wireframe gizmo, and undo/redo throughout. **A selection can be several parts**: **`Shift`**-click to add, **`Ctrl`**-drag a box over the floor to take everything inside it, **`Ctrl+A`** for all of them, and then move, nudge, rotate, duplicate or delete the whole group as *one* undo step. **`Ctrl+C` / `Ctrl+V`** carries a section into another scene. **`N`** floats every part's name over it — and a part's name *is* its tag prefix, so that is the list your PLC program is written against. **`1`-`4`** snap the camera to iso, top, front and side without losing what you were looking at. The property panel names what the selected part responds to, so none of it has to be guessed.
* 🔌 **Visual I/O Driver Wiring Panel (`F4`)**: Centered split-screen modal — click a PLC address (`%I0.0`, `%Q0.0`), then click the component tag to map it to. **Auto-map** suggests an address for every tag in the loaded scene, and **Export** writes `io_mapping.json` and `io_tags.csv` for the sidecar and for whoever is building the PLC side.
* 🏷️ **Live tag inspection and forcing**: the Tag Inspector lists every tag the loaded scene owns with its live value, and forces any of them — bit, int and float alike — with a typed value. A `🔓 N forced` chip in the toolbar shows what is being held by hand and releases it all in one click. The parts that measure something — the light curtain, the level tank, the digital display — also read out in 3D on the part itself.
* 🧪 **A built-in exercise per scene**: `python tools/try_scene.py --scene <id>` (or the toolbar's **🧪 Try** button) spawns or attaches to the engine and drives the scene the way a PLC would — pressing the panel's own buttons, timing the E-stop against a 200 ms limit, turning the setpoint pot mid-run to prove the line follows it, and failing a drive under it to check the controller trips and refuses to reset while the fault stands — then reports pass/fail. The thing to run before writing a real program against it.
* 🏭 **Native Siemens Integration**: **all three Siemens paths verified driving the 3D scene from a virtual S7-1500** — PLCSIM Advanced Simulation Runtime API (shared memory, no network, no OPC UA licence), OPC UA client, and Snap7 ISO-on-TCP. Belt, emitter, sensors, diverter and counters all run off the CPU's own program.
* 📊 **Multi-Protocol SCADA Support**: Built-in OPC UA client/server, Modbus TCP server, and Node-RED integration.

---

## 📦 29-Part Industrial Component Library

The tag ids below are the built-in scene's names. **A part's Name is its tag
prefix** — rename a pusher to `reject` in the property panel and its tags become
`reject.extend`, `reject.extended`, `reject.retracted`. That is the whole naming
rule, and it is what makes a scene you build addressable from a PLC.

| Component | Description | Tag Bus Interface |
|---|---|---|
| **Conveyor Belt** | Surface-velocity belt with side rails and legs, plus a drive-fault beacon | `conveyor.rotate` (Bit, Output) · `conveyor.fault` (Bit, Input) |
| **Photoelectric Sensor** | Diffuse beam sensor, reflects off the item itself | `sensor.detect` (Bit, Input) |
| **Retroreflective Sensor** | Beams to a reflector post across the lane; sees matt and dark items a diffuse sensor misses | `sensor.detect` (Bit, Input) |
| **Inductive Sensor** | Responds to metal only — cardboard passes it as if the lane were empty | `sensor.detect` (Bit, Input) |
| **Light Array** | Light curtain of 12 beams; reports the height of the tallest blocked beam, so one part replaces a low/high sensor pair | `lightarray.height` (Float, Input), `.blocked` (Bit, Input) |
| **Pneumatic Pusher** | Cylinder housing, chrome shaft & orange face plate; a jam freezes it mid-stroke | `pusher.extend`, `pusher.extended`, `pusher.retracted`, `pusher.fault` |
| **Inclined Ramp (Chute)** | 30° gravity chute with guide rails; incline and friction are a matched pair so cartons actually slide | Physical static body |
| **Stack Light** | 3-stage industrial tower light (Green, Yellow, Red) | `stacklight.green`, `yellow`, `red` |
| **Digital Display** | 3D 7-segment LED panel displaying live integer counts | `display.value` (Int, Output) |
| **Roller Conveyor** | Driven roller deck for pallets and totes that would scuff a belt; rollers spin at the true surface speed | `rollerconveyor.rotate` (Bit, Output) · `.fault` (Bit, Input) |
| **Weight Scale Conveyor**| Integrated load cell scale reading the carton's mass **in grams** — 720 g for a short carton, 2160 g for a tall one, 12960 g for a metal one — and showing it on the scale | `weighconveyor.weight` (Int, Input) |
| **Box Emitter** | Spawner emitting tall & short rigid cartons, optionally every Nth in metal | `emitter.emit` (Bit, Output) |
| **Box Remover** | Area3D zone despawning items & incrementing a counter; the counted tag is pickable, so two removers can feed one total | `remover.count` (Int, Input) |
| **Control Panel** | Operator station you can actually press. Start/Stop/Reset are momentary — one clean scan per click, however long you hold the mouse — and the mushroom is a maintained E-stop wired **normally closed**, so its tag is true while the circuit is healthy. The setpoint pot is **dragged**, reads out in the scene's own units on its scale plate, and turns itself to match a tag driven from a PLC | `panel.start`, `.stop`, `.reset`, `.estop` (Bit, Input) · `panel.setpoint` (Float, Input) · `panel.green`, `.red` (Bit, Output) |
| **Level Tank** | Analog process tank; outflow follows Torricelli, so process gain varies with level and a PID tuned full overshoots when empty. A seized valve holds its opening — the process keeps moving while the command reads zero | `tank.fill`, `tank.drain` (Float, Output), `tank.level`, `tank.fault` (Input) |
| **VFD Conveyor** | A belt behind a variable-frequency drive. The reference ramps, so *commanded* and *actual* speed genuinely disagree while the drive is moving between them — a controller that treats the reference as instantly true is wrong here in a way you can measure | `vfd.run`, `vfd.speed` (Float, Output) · `vfd.actual` (Float, Input), `.fault` |
| **Pivot Diverter** | A blade on a rotary actuator that deflects a *moving* carton across the lane without stopping the line. Two limit switches and a swing time are the whole exercise; a seizure freezes it mid-sweep, across a running lane | `div.divert` (Output) · `div.diverted`, `div.home`, `div.fault` (Input) |
| **Pick & Place Gantry** | A portal with a travelling carriage, a telescoping Z column and a vacuum cup that really picks a carton up and drops it with the carriage's own velocity. Three motions to sequence, and a grip that reports honestly when it caught nothing | `arm.target` (Float, Output), `.lower`, `.grip` (Output) · `.position` (Float, Input), `.inposition`, `.lowered`, `.raised`, `.holding`, `.fault` |
| **Barcode Scanner** | Overhead reader that says what an item *is*, not just that it is there — 101 short carton, 102 tall, 201 metal — as a code plus a one-scan read pulse a program has to latch | `scan.enable` (Output) · `scan.code` (Int), `scan.read`, `scan.present` (Input) |
| **Heating Station** | First-order thermal plant with ambient loss: heat fast, cool only as fast as the room allows. Pure proportional control leaves a standing offset you can measure. A failed element still reports full power while the plate cools | `oven.heater` (Float, Output) · `oven.temperature` (Float), `oven.attemp`, `oven.fault` (Input) |
| **Analog Gauge** | Needle instrument with a graduated plate, a red band and a digital sub-readout — somewhere for a float to be read in the scene rather than only in the tag list | `gauge.value` (Float, Output) |
| **Alarm Beacon** | Rotating beacon that sweeps a real light across the machines near it, plus a horn with a visible diaphragm. What you notice from the other end of the building | `beacon.beacon`, `beacon.horn` (Bit, Output) |
| **Selector Switch** | The third kind of operator input: not a pulse and not a latch, but a knob that **stays where it is put**. The controller reads a position, not an edge — which is what an Auto/Manual program is built around | `selector.position` (Int, Input) |
| **Guard Door** | An interlocked guard whose switch is closed while the door is shut (normally closed, like the mushroom). The solenoid lock makes it a two-way contract: the controller decides whether the door may be opened at all, and until it releases the lock the handle does nothing | `guard.lock` (Bit, Output) · `guard.closed`, `guard.locked` (Bit, Input) |
| **Blade Stop** | A blade that rises through the lane to hold cartons on a belt that *keeps running*. The only way to build an accumulation buffer here: the queue behind it builds because the solver says so, and releasing one lets the rest close up on their own. A seizure is the nasty one — frozen halfway, it stops short cartons and lets tall ones ride over | `stop.raise` (Bit, Output) · `stop.up`, `stop.down`, `stop.fault` (Bit, Input) |
| **Transfer Turntable** | A rotary index that turns a carton into a new lane. The load is held on by **friction**, not by being parented to the deck, so a deck told to index too fast genuinely throws it — which is the real constraint on how fast a transfer can run | `xfer.index` (Bit, Output) · `xfer.athome`, `xfer.atindex`, `xfer.fault` (Bit, Input) |
| **Measuring Encoder** | A wheel riding the belt it is placed over, counting pulses per metre of travel. Product tracked by *distance* instead of by a timer, so the logic survives anybody turning the drive up. Place it away from a conveyor and it counts nothing and does not turn — the honest failure, and a visible one | `enc.reset` (Bit, Output) · `enc.count` (Int), `enc.rate` (Float, Input) |
| **Cooling Fan** | A ducted fan that adds to the loss term of any heating station within reach, giving the thermal plant a second actuator pulling the other way. One plant, two actuators — which is split-range control, and the first place a deadband exists for a reason | `fan.run`, `fan.speed` (Float, Output) · `fan.airflow` (Float), `fan.fault` (Bit, Input) |
| **Two-Hand Control** | Two palm buttons whose permissive is **not** `left AND right`: the relay also requires that the two presses arrived within half a second of each other, so taping one button down defeats nothing. A program that ANDs the two bits itself passes its own test and fails the real device | `hands.left`, `hands.right`, `hands.valid` (Bit, Input) |

---

## ⚡ Quick Start

**You build it from source today.** That is what the steps below do, and it is
the same path [GETTING_STARTED.md](docs/GETTING_STARTED.md) walks in detail. You
need Godot 4.7 mono, the .NET 8 SDK and Python 3.11+.

### Downloading a release — not yet

There are **no published releases**. The
[Releases page](https://github.com/malgaroshy-maker/factory/releases) is empty,
and saying so here is cheaper than letting you click through to find out.

The machinery is built and tested — `python tools/build_release.py` exports the
engine and freezes the sidecar, and on Windows **`build_windows.bat`** does the
same double-clickably and then runs the release gate: 25 headless self-tests
against the *exported binary*, not against the checkout. It has been run end to
end on Windows and Linux. What has never happened is a tag and a publish. See
[PACKAGING.md](docs/PACKAGING.md).

When the first tagged release lands, the block below becomes true and this
heading comes off:

> Grab the archive for your platform from Releases, extract it, and run
> `FactoryForge`. **No Godot, no .NET SDK and no Python needed** — the sidecar
> that speaks every PLC protocol ships frozen alongside the engine, and F5's
> *Apply & Connect* finds it automatically.
>
> **Windows will warn you on first run.** These builds are not code-signed, so
> SmartScreen shows "Windows protected your PC" — click *More info → Run
> anyway*. That warning means the binary has no purchased certificate attached,
> not that anything is wrong with it. See
> [PACKAGING.md](docs/PACKAGING.md#code-signing--not-signed-and-the-download-page-says-so)
> for why this project does not buy one.

### 1. Installation

```bash
git clone https://github.com/malgaroshy-maker/factory.git
cd factory
pip install -e "sidecar[dev,opcua]"
```

### 2. Run the Test Suite

```bash
python -m pytest -q
```

73 pass as of 2026-09-21. Treat the command's output as the count, not this
line — four files used to quote three different numbers between them.

Or the full plan — build, the Python suite, the engine's own self-tests,
determinism, the engine↔sidecar seam and robustness. No PLC needed:

```bash
python tools/test_plan.py          # --gui adds the two display-dependent checks
```

### 3. Launch 3D Simulation Engine

```bash
python run.py
```

`run.py` finds Godot (or tells you exactly what to install and where), builds
the C# engine, and launches it — no separate `dotnet build` step, and it works
the same on Windows and Linux. (Windows users can also double-click
`run_factoryforge.bat`, which just calls `run.py`.)

This runs the **physics scene**: Jolt rigid-body cartons, real collisions, and
components whose properties genuinely change how the line behaves — speed up the
belt and boxes outrun the diverter; hold the pusher out and the line backs up
behind it.

```bash
# Fixed-timestep scene instead: reproducible, and the regression contract.
python run.py -- --deterministic
```

Both scenes expose the **same 16 tags** and report the same scene name, so a PLC
program, Node-RED flow or SCADA client drives either one unchanged. Use
`--deterministic` whenever you need repeatable counts — CI and
`tools/drive_engine.py` rely on it.

---

## 🔌 Driver Execution Commands

The engine speaks only its own tag bus; every PLC protocol lives in the Python
sidecar. **`connect` attaches to a running engine — that is the one to use with
the 3D view.** (`demo` starts its own headless Python scene instead, which is
for checking a driver with no Godot in the picture.) The **F5 Driver dialog**
runs these for you and copies the command.

```bash
cd sidecar

# Siemens PLCSIM Advanced (Shared Memory API — Zero Licence Cost)
python -m factoryforge_sidecar connect --driver plcsim-advanced -o instance Sorting_PLC

# Siemens S7 ISO-on-TCP (Snap7)
python -m factoryforge_sidecar connect --driver s7-snap7 -o host 192.168.1.20 -o db 1

# OPC UA Client (connecting to an S7-1500 @ 192.168.1.20)
python -m factoryforge_sidecar connect --driver opcua-client \
    -o url opc.tcp://192.168.1.20:4840 --mapping io_mapping.json

# OPC UA Server (exposing the scene to Node-RED / SCADA)
python -m factoryforge_sidecar connect --driver opcua-server
```

Building your own scene? Name your parts in the inspector, then **F4 → Export**
writes `io_mapping.json` and `io_tags.csv` for the tags that scene actually has.
See [Getting Started](docs/GETTING_STARTED.md#-connecting-a-scene-you-built-yourself).

---

## 📚 Documentation Sitemap

| Document | Description |
|---|---|
| 🚀 **[GETTING_STARTED.md](docs/GETTING_STARTED.md)** | Step-by-step setup for PLCSIM Advanced, TIA Portal & Node-RED |
| 🛠️ **[PART_AUTHORING.md](docs/PART_AUTHORING.md)** | Guide & template for building custom 3D factory components |
| 🔌 **[DRIVER_AUTHORING.md](docs/DRIVER_AUTHORING.md)** | Guide for adding custom Python protocol drivers |
| ✅ **[TEST_PLAN.md](docs/TEST_PLAN.md)** | What is tested, what is not, and the last run's results |
| 📦 **[PACKAGING.md](docs/PACKAGING.md)** | Building a distributable release — verified end to end, never published |
| 🔨 **[HARDENING_PLAN.md](docs/HARDENING_PLAN.md)** | The open work list, HP-01…HP-52, and the release gate inside it |
| 🗺️ **[ROADMAP.md](docs/ROADMAP.md)** | Milestone completion tracking |
| 📑 **[PRD.md](docs/PRD.md)** | Problem statement, target audience, and success criteria |
| ⚡ **[tag-bus.md](docs/tag-bus.md)** | WebSocket tag bus protocol specification |
| 🤖 **[AGENTS.md](AGENTS.md)** | Developer cheat sheet, paths, and hardware gotchas |
| 📚 **[docs/history/](docs/history/)** | Thirteen completed plan documents — why each piece was built the way it was. History, not instructions |

---

## 🤝 Contributing

Nobody outside the project has contributed yet, so
[CONTRIBUTING.md](CONTRIBUTING.md) is written to tell you what it actually costs
rather than to sell you on it. The short version: **a driver is cheap** — one
Python module, no Godot and no C# — and **a part is not**, because it still
touches eight shared files besides its own class. That is a design problem, it is
written down, and HP-34 in the [hardening plan](docs/HARDENING_PLAN.md) is the
work to reduce it to one file plus a catalog entry.

Issue templates for a [bug](.github/ISSUE_TEMPLATE/bug_report.yml), a
[part](.github/ISSUE_TEMPLATE/part_request.yml) and a
[driver](.github/ISSUE_TEMPLATE/driver_request.yml). Security issues go through
[SECURITY.md](SECURITY.md), not the issue tracker — it also lists what is already
known, including a Modbus bind default that is wrong today and being fixed under
HP-22. Behaviour here: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## ⚖️ License

Distributed under the **MIT License**. See `LICENSE` for more information.
