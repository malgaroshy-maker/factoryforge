# 🚀 FactoryForge Student Getting Started Guide

Welcome to **FactoryForge**, a free, open 3D factory simulator for learning PLC programming.

---

## 📋 System Requirements

* **Operating System:** Windows 10/11 or Linux (x86_64)
* **Python:** Python 3.11+ (Python 3.12 recommended)
* **Godot:** Godot 4.7 Mono / C# (4.7.2 recommended — any 4.7.x works)
* **PLC Target (Optional):** Siemens S7-PLCSIM Advanced v3.0+, TIA Portal V14-V19, or Node-RED

---

## ⚡ Quick Start (5 Minutes)

**Building from source is currently the only way in.** There is no published
release to download yet — the build and freeze machinery exists and is tested,
but no version has ever been tagged and uploaded, so the Releases page is empty.
See [PACKAGING.md](PACKAGING.md). That is why this guide starts with `git clone`
and not with a download link.

### 1. Clone & Install Sidecar

```bash
git clone https://github.com/malgaroshy-maker/factoryforge.git
cd factoryforge
pip install -e "sidecar[dev,opcua]"
```

### 2. Run the Test Suite

This checks the sidecar installed cleanly. It needs no PLC, no Godot and no
Siemens software:

```bash
python -m pytest -q
```

73 pass as of 2026-09-21. The count is whatever `pytest` prints — it has grown
steadily and every place that wrote it down went stale. What matters is that
nothing **fails**, and that pytest *collects* the suite at all: a collection
error rather than a test failure usually means an optional extra did not
install.

### 3. Launch the 3D Engine

```bash
python run.py
```

`run.py` locates a Godot 4.7 .NET build, builds the C# engine, and launches it.
Works the same on Windows and Linux; Windows users can also double-click
`run_factoryforge.bat`.

**You should not have to configure anything.** Godot ships as a zip with no
installer, so it looks in the `GODOT` environment variable, then `PATH`, then
the places an extracted download actually sits — your drive roots, Downloads,
Desktop, and the usual program directories. Only if all of that misses does it
print what to download. To point it at a specific build, set `GODOT` to that
executable's full path.

FactoryForge opens on a **start screen**: pick a template, open a scene you
saved, or start empty. The key list is on that screen too, and once a scene is
open **`F12`** (or the toolbar's **`?`**) brings the same list back over it.

The templates each teach one thing:

| Template | What it is for |
|---|---|
| **Sorting by height** | The reference line — sensors, a diverter, two counters |
| **Start / stop station** | Momentary buttons, a latching E-stop, a lamp that must track real state |
| **Tank level control** | Analog end to end; outflow varies with level, so a PID tuned full overshoots empty |
| **Light curtain sorting** | Sorting on a measurement rather than two bits |
| **Roller line with weighing** | A checkweigher and an inductive sensor that sees metal only |
| **Pick & place cell** | A gantry with three motions to sequence, on feedback rather than timers, and a grip that reports honestly when it caught nothing |
| **Accumulation buffer** | Product piles up behind a blade stop on a belt that never stops, and is released a batch at a time — timed by encoder pulses, so the batch stays the same size when somebody turns the drive up. A timer would not. |
| **Heat treat station** | A thermal plant with real inertia — proportional control alone visibly parks short of setpoint, and you can measure by how much |

To skip the start screen — scripting a run, or grabbing a screenshot:

```bash
godot --path engine/ -- --scene=res://templates/tank_level_control.json
```

The default scene is the **physics** one: rigid-body cartons on a real conveyor.
Useful keys straight away:

| Key | Action |
|---|---|
| `F1` | Switch between **Edit** and **Run** mode |
| `Space` | Pause / resume — freeze the line and read every sensor at that instant |
| `Ctrl+R` | Reset the run (machines stay where they are) |
| `C` | Switch between the orbit and fly cameras |
| `F4` / `F5` | I/O wiring panel · driver connection |

The toolbar also carries a **0.25×–4× rate selector**: slow a fast interlock
down to watch it, or fast-forward a long cycle. Your PLC stays connected while
the scene is paused.

### Edit mode and Run mode

A click has to mean one thing at a time. In **Edit** mode it selects a part —
and a click that keeps going is a **drag**, which moves it, snapped to the same
grid a fresh placement uses and undone by a single `Ctrl+Z`. The property panel
lists what else the selection answers to. In **Run** mode every operable part
answers a click by doing whatever it does — a conveyor toggles on, a pusher strokes, a
stack light lamp switches, a tank valve opens. The toolbar's mode button reads
`✎ Build` / `👆 Operate` so the two are never mistaken for one another, and the
parts palette hides itself while the line is running.

Press `F1` to enter Run mode. A banner along the bottom names what's clickable
— *"5 parts respond to a click: conveyor, pusher, stack light, panel,
emitter. Hover to see which."* — or says plainly that nothing is, on a scene
you have not built anything into yet. Hovering over an operable part outlines
it, so you never have to click blind to find out what responds.

Click the **control panel** beside the belt:

| Control | Tag | Behaviour |
|---|---|---|
| Start (green) | `panel.start` | Momentary — high for exactly one scan per click |
| Stop (black) | `panel.stop` | Momentary |
| Reset (blue) | `panel.reset` | Momentary |
| E-Stop (red mushroom) | `panel.estop` | Maintained — click to strike, click again to release |
| Setpoint pot | `panel.setpoint` | **Dragged**, not clicked — pull up to raise it. Reads out on the scale plate above it |

Momentary means what it does on a real panel: one click is one clean rising
edge, however long you hold the mouse down. Write your logic against the edge,
not the level.

The **E-Stop is wired normally closed**, like the real thing: `panel.estop` is
**true while the circuit is healthy** and goes false when the mushroom is
struck. If your program runs happily with that tag false, it would also run with
the wire to the E-stop cut — which is the exact failure NC wiring exists to
catch. This is the cheapest place to learn that.

The pot is the one control on the panel that answers to a drag. It carries the
scene's own units rather than a percent — the level to hold, the height that
counts as tall, the weight that counts as a reject — so a controller reads a
number that already means what it says, with no range to agree on separately.
Its tag works both ways: turn the knob and it publishes, force it from a PLC
and the pointer turns to match, so the panel never disagrees with the number
your program is using.

### Breaking it on purpose

A line that always works teaches half the job. In **Operate** mode the toolbar
has a **`⚠ Fault`** button: arm it, then click a conveyor or a pusher.

The drive stops **while its command is still on**. That is the whole point —
`belt.rotate` stays true, the belt does not move, its fault beacon lights, and
`belt.fault` goes true for your program to read. A command and reality have
disagreed, which is the situation every interlock in every real plant exists
for, and which nothing in this library could produce until the drives could
fail. A jammed cylinder is worse and more instructive still: it freezes
mid-stroke rather than returning home, so `pusher.extended` and
`pusher.retracted` are both false and the limit switches are the only honest
thing to read.

The tank's valves can seize too, and that one is nastier. A modulating valve
stuck at 60% keeps filling while your controller's own output reads zero, so
nothing *looks* broken — the level just will not do what you asked. A PID
chasing a valve that no longer answers is one of the first real diagnoses an
instrument technician learns, and it is the one failure here you cannot spot
from the command side at all.

Click the same part again to clear it. The fault is held as a *force*, so the
Tag Inspector shows it held and the `🔓 N forced` chip releases it too.

A controller worth the name should then refuse to restart. The shipped
exercises check exactly that: Reset while the fault stands does nothing, and
clearing the fault alone does not restart the line either — the trip is still
latched, and only Reset then Start bring it back.

**Every shipped scene answers to this panel.** Start runs the line, Stop stops
it, the mushroom latches a trip that only Reset clears, and the pot changes
what the line is aiming at while it runs. `python tools/try_scene.py --scene
<id>` presses the same buttons and reports pass/fail, so the exercise and the
regression test are the same sequence.

If you need repeatable results — grading an exercise, or comparing two runs — add
`-- --deterministic` for the fixed-timestep scene. Both expose the same tags,
with or without a window, so your program does not change.

---

### Operating any component by hand

Click a conveyor. Turn it on. Watch it move. That should need no PLC, no
Python and no knowledge of tag ids — it is how you find out what a part *is*
before you write a line of control code against it. Three ways in, from
fastest to most precise:

1. **Click the part itself, in Run mode (`F1`).** A conveyor or roller
   toggles its `.rotate` bit; a pusher strokes out and back; a stack light
   lamp switches independently of its neighbours (click the green dome, then
   the yellow one — each answers on its own); a level tank's inlet or outlet
   pipe opens or shuts that one valve fully. A box emitter pulses one carton
   per click rather than streaming them, since holding the tag high would not
   spawn a second one.
2. **Select the part (Edit mode) and use its own property panel.** Every
   part's panel shows its own I/O beneath its settings — a toggle for a bit
   output, a slider for a float, a live readout with an **Override**
   checkbox for an input. This is the one place a tank's fill and drain
   valves each get a real slider, and the only way to drive a value the part
   itself does not expose to a click (a digital display's number, for
   instance).
3. **The Tag Inspector**, top-right, lists every tag on the bus regardless of
   which part owns it, each with a **Force** button — works on bit, int and
   float tags alike. A tag left forced stays pinned (the button reads
   **UNFORCE**) even after a real driver connects later, which is exactly
   the point when you are overriding a stuck sensor to see how your program
   reacts — and exactly the thing to remember to release again afterward.
   The toolbar shows a **🔓 N forced — release** chip whenever anything is,
   so a forced tag is never invisible for long.

Forcing still works while the simulation is **paused** (`Space`) — freeze the
line, force a sensor, and read your PLC's response at that exact instant.

Not sure a scene is wired the way you expect before you write a real PLC
program against it? Run its built-in exercise:

```bash
python tools/try_scene.py --scene tank-level-control
```

It spawns the engine, drives the scene the way a PLC would, and reports
`PASS`/`FAIL` — or press the toolbar's **🧪 Try** button (or **🧪 Try this
scene**, the same action offered inside the F5 dialog) to run it against the
window already open, so you watch it happen rather than reading a log.
`python tools/try_scene.py --list` shows every scene id. This is what to
reach for instead of `connect --driver mock` — the mock driver only records
what it is told, so connecting it to a scene you have not written a program
for yet leaves the line more dead than doing nothing at all.

---

## 🔌 `connect` vs `demo` — read this first

The engine speaks one protocol: its own tag bus. **Every PLC protocol lives in
the Python sidecar**, so connecting a PLC always means starting the sidecar
alongside the running engine.

There are two subcommands and picking the wrong one wastes an afternoon:

| | |
|---|---|
| **`connect`** | Attaches to an engine that is **already running**. This is the one you want with the 3D engine. |
| **`demo`** | Starts its **own** headless Python scene on the bus port. For a quick driver check with no Godot involved. |

Run `demo` while the 3D engine is up and it finds port 7411 taken — or worse,
binds first and drives a scene you cannot see while the 3D one sits still.

```bash
# Terminal 1
godot --path engine/

# Terminal 2
cd sidecar
python -m factoryforge_sidecar connect --driver plcsim-advanced -o instance Sorting_PLC
```

The **F5 Driver dialog** does exactly this for you: pick a driver, fill in the
address, and *Apply & Connect* starts the sidecar and copies the command to your
clipboard in case you would rather run it yourself.

---

## 🔌 Connecting to Siemens S7-PLCSIM Advanced

FactoryForge supports **two direct connection methods** to Siemens S7-1500 PLCs:

### Method A: Direct PLCSIM Advanced Native API Driver (Recommended — Zero Licence Cost)

**Verified end to end against a real PLCSIM Advanced CPU driving the 3D scene.**

```bash
pip install -e "sidecar[plcsim]"     # needs pythonnet; Windows only
```

1. Open **S7-PLCSIM Advanced Control Panel** and start a virtual CPU. Either
   communication mode works for this driver — it talks to the API directly, so
   the default local **Softbus** mode is fine and no IP is needed.
2. Download your TIA Portal program and the `FF_IO` datablock
   ([`examples/tia/`](../examples/tia/)) to it.
3. With the engine running, attach the driver:

```bash
cd sidecar
python -m factoryforge_sidecar connect --driver plcsim-advanced \
    -o instance <instance name> --mapping ../examples/plcsim_mapping.json
```

Two things that will cost you time otherwise:

- **The instance name is the one in the PLCSIM control panel**, which is not
  necessarily the CPU's name in TIA. If `connect` reports `InstanceNotRunning`,
  that mismatch is the usual reason.
- **The mapping is required and holds PLC symbols, not NodeIds** — `FF_IO.ConveyorRotate`,
  dotted and unquoted. That is the form the API's own tag list reports, and it
  differs from the OPC UA spelling (`ns=3;s="FF_IO"."ConveyorRotate"`).

---

### Method B: OPC UA Server Connection

> **PLCSIM Advanced must be on the Virtual Ethernet Adapter for this.** In the
> default local *Softbus* mode the virtual CPU has no IP at all, so nothing
> listens on 4840 no matter what address the TIA project shows. Switch the
> instance's communication interface in the PLCSIM control panel, then start it
> and download again.

1. Enable **OPC UA Server** in TIA Portal CPU properties under *Protection & Security -> OPC UA*.
2. Compile and download to PLCSIM Advanced (`192.168.1.20:4840`).
3. Discover NodeIds using the sidecar browse tool:

```bash
python -m factoryforge_sidecar browse opc.tcp://192.168.1.20:4840
```

4. Attach the sidecar to the running engine with your OPC UA mapping:

```bash
cd sidecar
python -m factoryforge_sidecar connect --driver opcua-client \
    -o url opc.tcp://192.168.1.20:4840 --mapping ../examples/opcua_mapping.json
```

**Verified end to end against a virtual S7-1500 driving the 3D scene.**

---

### Method C: Snap7 (ISO-on-TCP, no OPC UA licence)

Also verified. Needs two things beyond Method B:

```bash
pip install -e "sidecar[siemens]"
cd sidecar
python -m factoryforge_sidecar connect --driver s7-snap7 \
    -o host 192.168.1.20 -o db 1 --mapping ../examples/snap7_mapping.json
```

1. **`FF_IO` must not be an "optimized block access" DB.** An optimized block
   has no absolute byte addresses, and snap7 addresses bytes. Right-click the DB
   → *Properties* → *Attributes*, uncheck it, recompile, download.
2. **The mapping holds byte addresses** — `DBX0.0` for a Bool, `DBD2` for a
   DInt — which are the values in TIA's *Offset* column. They move if you
   reorder the DB.

If every read fails with `Invalid address (0x05)`, the usual cause is the read
running past the end of the DB, not the optimized-access setting.

---

## 🏭 Connecting a scene you built yourself

The sorting demo ships with a mapping file. A line you build in the editor does
not — it has its own parts and its own tag ids, and nothing on the PLC side
knows them yet. The path is:

**1. Name your parts.** Click a part and edit **Name** in the properties panel.
The name is the tag prefix, so a pusher called `reject_pusher` gives you
`reject_pusher.extend`. Do this before writing any PLC code: the default ids
(`pushermechanism_2`) are unreadable, and worse, unstable — delete a part and
re-place it and the number moves, silently breaking a mapping that points at the
old one.

Renaming is refused, with a reason, for parts that only mirror tags the
simulation owns — the sorting demo's belt and sensors.

**2. Export the I/O.** Open **F4 Wiring** and press **Export & Copy Command**.
You get two files, and the absolute path to both:

* `io_mapping.json` — every tag id with a blank address, ready to fill in.
  Re-exporting after adding a part keeps the addresses you already typed and
  only adds new blanks.
* `io_tags.csv` — the same list with descriptions, direction, and a suggested
  IEC address. Open it in a spreadsheet and build your PLC symbol table from it.

The direction column is worth reading carefully. It is written from the
**controller's** point of view: an *Input* is something the PLC reads (a sensor,
a button) and the simulation writes.

**3. Find your NodeIds** (OPC UA only) and paste them into `io_mapping.json`:

```bash
cd sidecar
python -m factoryforge_sidecar browse opc.tcp://192.168.1.20:4840
```

**4. Connect**, via the F5 dialog or the command it copies.

Add or delete a part while a driver is connected and the engine republishes the
I/O list automatically, so the driver sees the new tags without a reconnect.

---

## 🛠️ Using the 3D Scene Editor

* **`Left-Click`**: Select part in 3D or place active palette component on the voxel grid floor.
* **`Left-Drag` on a placed part**: Move it. One drag is one `Ctrl+Z`; a click that does not travel only selects.
* **`R`**: Rotate the placement preview, or the selected part, 90°.
* **`Ctrl+D`**: Duplicate the selected component.
* **`M`**: Move the selected component with the cursor, committing on the next click — the keyboard route to the same thing the drag does.
* **`Delete` / `Backspace`**: Delete selected component.
* **`Ctrl+Z` / `Ctrl+Y`**: Undo / Redo placement or deletion.
* **`F1`**: Switch between **Edit** and **Run** mode.
* **`Esc`** (Operate mode): Disarm the `⚠ Fault` tool.
* **`F4`**: **I/O Wiring** — map PLC addresses to tags, and export `io_mapping.json` / `io_tags.csv`.
* **`F5`**: **Driver** — pick a protocol and start the sidecar against this engine.
* **Save / Load**: choose a file, so you can keep more than one line and share it. The filename becomes the scene name reported on the tag bus.
* **`C`**: Toggle between **Orbit Camera** and **Free-Look Fly Camera** (WASD + Right-Click drag).
