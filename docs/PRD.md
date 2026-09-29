# FactoryForge — Product Requirements

*Status: draft · Last updated: 2026-08-07*

## Problem

Learning PLC programming needs something to program *against*. A ladder program
that toggles a bit teaches nothing; a ladder program that starts a conveyor,
watches a sensor, and fires a pusher teaches automation.

The tool that does this best is **Factory I/O** — good graphics, easy to use,
real driver support. But:

- It is **stagnant**. Still 2.5.x as of August 2026; no 2.6. Maintenance releases only.
- It is **closed**. Custom parts are impossible. Real Games said in June 2025 that
  import "will come later"; a preview promised for autumn 2025 never shipped.
- It is **paid per seat** — €278/year or €765 perpetual for Ultimate. For a
  classroom of thirty, or a self-taught student in a country where that is a
  month's wages, this is the barrier.

Alternatives fail on different axes: Emulate3D and Visual Components are
professional-grade but expensive; Simumatik has a component editor but is a
hosted platform; the open-source options have no 3D worth looking at.

**There is no free, open, good-looking 3D factory simulator that a student can
download and point a real PLC at.**

## Users

| User | Need | Success looks like |
|---|---|---|
| **Self-taught student** (primary) | Practise ladder/SCL against something visual, free, on their own PC | Downloads it, writes a program in TIA Portal, watches boxes sort |
| **Instructor** | Hand out a scene as an assignment; no per-seat licence | Distributes a scene file; thirty students run it with no procurement |
| **Contributor** | Add the part or driver their course needs | Writes a part without touching the driver layer, or a driver without touching 3D |
| **Hobbyist / maker** | Test automation logic without buying hardware | Points OpenPLC or a Pi at it over Modbus |

Explicit non-user for v1: **industrial virtual commissioning.** Real digital-twin
work needs validated physics, determinism guarantees, and support contracts.
Competing there would compromise the teaching goal.

## Goals

1. A student with no licence and no hardware can run a working factory scene
   and drive it from a simulated PLC.
2. Adding a part is a documented, achievable afternoon's work.
3. Adding a driver requires only Python and no knowledge of the 3D engine.
4. The look is good enough that a student *wants* to use it — this is not
   incidental; it is the main reason Factory I/O won.

## Non-goals (v1)

- **Not** a Factory I/O clone. No attempt to match its ~71 parts or ~20 drivers.
- **Not** a PLC. We simulate the *plant*; the controller is external.
- **No** web version. Godot's C# does not export to web; revisit later.
- **No** multi-user or cloud. Local, single-user, offline-capable.
- **No** `.factoryio` scene import in v1. Possible later; not a launch blocker.
- **No** validated physics accuracy claims. It must look and behave plausibly,
  not certify a real line.

## Requirements

### Must have (v1)

| # | Requirement |
|---|---|
| R1 | Runs offline on Windows and Linux, no account, no licence key |
| R2 | Seven parts: emitter, belt conveyor, two diffuse sensors, pusher, remover, button panel |
| R3 | Scene editor: place, rotate, delete, save, load on a grid |
| R4 | **OPC UA** driver — the primary integration path. Reference CPU: **S7-1500** |
| R5 | Tag list UI showing live values, with manual force for testing |
| R6 | The "sorting by height" scene works end to end against S7-PLCSIM Advanced |
| R7 | Written guide: install → build scene → connect PLC → run |

### Should have

| # | Requirement |
|---|---|
| R8 | PLCSIM Advanced driver (no hardware needed — the best student path) |
| R9 | S7 driver via snap7 (older CPUs, no OPC UA licence) |
| R10 | Modbus TCP driver *(already implemented)* |
| R11 | Fault injection: force a tag, simulate a stuck sensor |
| R12 | Headless mode for automated testing of student programs |

### Could have

Analog parts (tank, level meter, PID scenarios) · scene screenshots ·
part authoring template · Linux packaging beyond a tarball · MQTT driver for
IIoT/Industry 4.0 scenarios (Factory I/O has none — a genuine differentiator)

## Success criteria

**Launch (v1):**
- A student who has never seen the project goes from download to a sorting
  scene driven by PLCSIM in **under 30 minutes**, using only the written guide.
- The same TIA Portal program drives S7-PLCSIM Advanced through both the OPC UA
  driver and the Modbus driver with identical behaviour.
- An unmodified OpenPLC program drives the same scene over Modbus, proving the
  project is not Siemens-only.
- One person outside the project contributes a part.

**Not verifiable in-house:** nobody on the project has physical PLC hardware.
PLCSIM Advanced runs genuine S7-1500 firmware and is a strong proxy, but real
hardware timing and network latency remain untested. See Risks.

**Twelve months:**
- 25+ parts, of which a meaningful share are community-contributed
- Used in at least one published course or curriculum
- Contributors have added at least one driver we did not write

**Anti-goals — signs we drifted:**
- Chasing Factory I/O feature parity instead of the teaching goal
- A part library only the maintainer can extend
- Requiring an account, a licence server, or an internet connection

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Engine | Godot 4.6 + C#/.NET 8 | MIT-licensed, Jolt physics built in, matches the open-source goal |
| Architecture | Godot engine + Python driver sidecar over a tag bus | Contributors add drivers in Python; drivers can crash without killing the sim |
| First driver | **OPC UA** | Widely compatible — Siemens, Codesys, Beckhoff, WAGO, Ignition |
| Scene format | Fresh (JSON) | Avoids inheriting Factory I/O's grid and the legal exposure of cloning it |
| Licence | MIT or Apache-2.0 | Removes the barrier that motivates the project |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Conveyor physics is the classic time sink | High | Budget 3-4 weeks; use a dedicated conveyor constraint, not friction |
| Art is the hidden cost | High | Programmer-art for v1; lighting matters more than models |
| Real Games ships their own parts SDK | Medium | Would not remove the price barrier, which is the core motivation |
| Solo maintainer burnout | Medium | Ship independently useful milestones; do not gate value on a distant v1 |
| S7-1500 OPC UA server needs a paid licence | **Low** | The unlicensed trial allows 100 variables; v1's scene uses 10 |
| **No physical PLC available for validation** | Medium | PLCSIM Advanced runs real S7-1500 firmware, so protocol behaviour is faithful. Timing under real network latency is not. Mark hardware validation as help-wanted; keep the tag bus tolerant of jitter (it already is — it is explicitly not hard real-time) |
| PLCSIM Advanced licence expires mid-project | Medium | OpenPLC + the existing Modbus driver is the everyday development loop and needs no licence at all |

---

*FactoryForge — developed by Mahamed Algaroshy (محمد الجروشي).*
