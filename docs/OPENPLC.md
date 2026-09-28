# Driving FactoryForge from OpenPLC over Modbus TCP

End-to-end walkthrough: an ordinary IEC 61131-3 Structured Text program, running
on the OpenPLC Runtime, sorts boxes by height in FactoryForge — no Siemens
software anywhere, and nothing in the program that FactoryForge put there.

This is the launch criterion in [`PRD.md`](PRD.md): *"an unmodified OpenPLC
program drives the same scene over Modbus, proving the project is not
Siemens-only."* The program is [`examples/openplc/Sorting.st`](../examples/openplc/Sorting.st),
the line-for-line counterpart of [`examples/tia/Sorting.scl`](../examples/tia/Sorting.scl).

> **Two sorting lines, two Modbus maps.** `Sorting.st` is written for the
> ten-tag Python scene that `factoryforge-sidecar demo` runs. The line the 3D
> engine opens, which is also the one the grader marks, has nineteen tags: an
> operator panel and two fault contacts on top of the ten. The sidecar hands out
> Modbus addresses in sorted tag-id order, so the extra tags **move the
> addresses**. `pusher.extend` is coil 2 in `demo` and coil 4 on the 3D line,
> where coil 2 is `panel.green`. `Sorting.st` against the 3D line lights a lamp
> instead of firing the pusher. For the 3D line and the grader, start from
> [`examples/openplc/sorting-by-height/`](../examples/openplc/sorting-by-height/).
> The student's walk from the download to a graded program is
> [*Your first hour*](GETTING_STARTED.md#your-first-hour-no-licence-needed) in
> Getting Started. This file is the reference behind it.

## What was actually run

| | |
|---|---|
| **OpenPLC** | OpenPLC_v3, `b5d4135` (4 Apr 2026), from `github.com/thiagoralves/OpenPLC_v3` |
| **Built** | 21 Sep 2026, from source, with OpenPLC's own `matiec`, `libmodbus`, `snap7` and `opendnp3` |
| **On** | Ubuntu 26.04 LTS under WSL2 (kernel 6.18.33.1-microsoft-standard-WSL2) on Windows 11, gcc 15.2.0 |
| **Simulator** | `factoryforge-sidecar`'s `modbus-tcp` driver over the Python harness scene (`harness/scene.py`), same process, same loopback |
| **Result** | **103 tall / 103 short** over a 630 s run — a perfect split, every tall box diverted and no short box pushed |

A shorter 60 s run on the same build gave 9 tall / 9 short, also a perfect
split. The Siemens path reports 99 tall / 99 short on a real S7-1500 and
Node-RED reports 9 tall / 9 short; this is the third controller to produce that
shape and the first that is neither Siemens nor a flow engine.

Both counts are the simulator's. OpenPLC's own view of them, read back out of
the PLC over a second Modbus connection, agreed exactly: `CountShort=103
CountTall=103`.

### The 3D line's map, graded (2026-09-24, IP-09)

The first-hour path in Getting Started was run on the same OpenPLC build. The
FactoryForge side came from a release zip built from this repository.

| | |
|---|---|
| **Program** | `examples/openplc/sorting-by-height/sorting_by_height.st`, the nineteen-tag starter, copied out of the unpacked zip together with its `mbconfig.cfg` and completed with the two blocks the guide gives |
| **Compiled** | with `compile_program.sh`, as an ordinary user in a user-owned copy of the OpenPLC tree. The starter carries CRLF line endings from the Windows zip, and neither matiec nor the `mbconfig.cfg` parser minded |
| **Run** | `./openplc` without `sudo`: the two real-time warnings, nothing else |
| **Marked by** | `factoryforge_sidecar grade --scene sorting-by-height`, OpenPLC as the controller over `connect --driver modbus-tcp`, all on loopback inside WSL |
| **Result** | the step 7 program (belt and feed only): **FAIL**, 9 tall cartons off the far end, *"The pusher never came out"*. The step 8 program: **PASS**, 8/8, 9 tall down the chute and 9 short past the end, the pusher firing 0.95 s after the beam (window 0.60–1.20 s). *That was the program before IP-35, which ignored Start. The exam now presses Start and runs the E-stop sequence (12 checks); the program written for it was run on OpenPLC on 2026-09-25, below.* |

The release's own sidecar, unpacked with no Python on `PATH`, connected to the
release's engine and printed the nineteen-tag map exactly as the starter
declares it. It graded the built-in `good` reference controller to PASS.

Two parts of that path were not run. The grader and sidecar in the OpenPLC
run were the same code from source inside WSL, rather than the frozen Windows
binaries across the WSL boundary. Crossing that boundary needs Windows
Firewall to allow the sidecar, and that was not done on the test machine. And
OpenPLC did not drive the 3D window itself.

### The IP-35 program, run (2026-09-25)

IP-35 made the exam press Start 1 s in, strike the mushroom, release it, press
Start alone, then Reset, then Start, and rewrote the guide's step 7 to latch the
E-stop and wait for Start. That program had only been compiled. It was then run
on the same OpenPLC build, and it did not survive the run as written.

| | |
|---|---|
| **Program** | the starter plus the four code blocks of the guide's steps 7 and 8, taken out of `GETTING_STARTED.md` by a script and inserted where the guide says. The script checked that each block is in the program byte for byte and that the program minus the blocks is the starter. The files that were compiled have the same SHA-256 as the ones the final guide produces |
| **Compiled** | with `compile_program.sh`, in a user-owned copy of `/opt/OpenPLC_v3`, with the starter's `mbconfig.cfg` unchanged in `webserver/core/` |
| **Run** | `./openplc` without `sudo`, a fresh process for every graded run unless the table says otherwise |
| **Marked by** | the sidecar's `grade --scene sorting-by-height` (wall clock, not `--lockstep`) and a second sidecar's `connect --driver modbus-tcp -o port 5502 --port <printed>`, the commands of the guide's step 9, run from source in a WSL venv, all on loopback inside WSL |

| Program | OpenPLC | Seed | Result |
|---|---|---|---|
| step 7, first version | started 2 s before the grader | random | **FAIL** 9/12: `line.both_lanes`, `sort.tall_diverted`, `estop.stopped_the_belt` |
| step 7, first version | started 3 s after the grader's sidecar | 1 | **FAIL** 9/12, the same three |
| step 8, first version | started 2 s before the grader | 1 | **FAIL** 11/12: `estop.stopped_the_belt` |
| step 8, first version | left running from that run, a second grader | 2 | the run was cut short, but the sidecar's first `OUT` line already said `rotate=1` before any Start |
| step 7, as now | started 2 s before the grader | 11 | **FAIL** 10/12: `line.both_lanes`, `sort.tall_diverted`, *"The pusher never came out"*. All four panel checks met |
| step 8, as now | started 2 s before the grader | 1, 2, 3 | **PASS** 12/12 each time: 8/7, 8/8 and 8/7 tall/short, none misrouted, the pusher 0.98–0.99 s after the beam, 30–55 mm of belt after the mushroom, running again 0.10–0.11 s after Start |
| step 8, as now | started 3 s after the grader's sidecar | 4 | **PASS** 12/12 |
| step 8, as now | left running from that run, a second grader | 5, 6 | first **PASS** 12/12; second **FAIL** 11/12, `line.started_by_start`, the belt started at 0.01 s |

Two things were wrong, and neither shows in a compile or in a Python
translation of the program:

- **OpenPLC's inputs read FALSE until its first poll returns.** The runtime
  scans before the Modbus master's first read lands, and the master's input
  buffer in `modbus_master.cpp` starts at zero. The E-stop is normally closed,
  so FALSE means struck: the first version tripped on its first scan, the red
  lamp was lit before anything happened (the sidecar's first `OUT` line
  already said `red=1`), and the exam's Start, which comes without a Reset,
  did nothing. It did so even with OpenPLC started after the slave was up. The
  guide's program now counts a trip only once the E-stop has read healthy
  (`EstopSeen`), and still refuses Start while it reads FALSE.
- **OpenPLC keeps its last inputs when the slave goes away.** On a failed
  connection the master skips the device and leaves its buffer alone, so a
  running program keeps running and writes `rotate=1` to whatever slave it
  finds next. The guide said to leave OpenPLC running between the 3D window
  and the grader. A line left running then started without a Start press, and
  the exam failed it. Step 9 now restarts OpenPLC before grading.

Not covered: the grader pressed the buttons, not a person in the 3D window;
OpenPLC did not drive the 3D window; and the WSL-to-Windows crossing is still
IP-39's.

---

## How the two halves meet

FactoryForge is the Modbus **slave**. OpenPLC is the **master** and polls it.
That is the opposite of the usual student setup, where OpenPLC is the slave and
a SCADA package polls *it*, and it is worth saying out loud because it decides
every address in the program.

OpenPLC's Modbus master ("Slave Devices" in the web UI) lays a polled device's
points into a block of its own address space that always starts at 100:

| The sidecar serves | OpenPLC's master calls it |
|---|---|
| discrete input *n* | `%IX(100 + n/8).(n mod 8)` |
| coil *n* | `%QX(100 + n/8).(n mod 8)` |
| input register *n* | `%IW(100 + n)` |
| holding register *n* (write) | `%QW(100 + n)` |

That is `updateBuffersIn_MB` / `updateBuffersOut_MB` in OpenPLC v3's
`webserver/core/modbus_master.cpp`, not a convention this project invented.

The sidecar prints its half of the map, and it is the authority. In the
folder FactoryForge was extracted to:

```
./factoryforge-sidecar connect --driver modbus-tcp -o port 5502
```

(`.\factoryforge-sidecar` on Windows.)

<!-- from-source -->
From a source checkout:

```bash
python -m factoryforge_sidecar connect --driver modbus-tcp -o port 5502
```
<!-- /from-source -->

```
  ADDRESS          TYPE   TAG
  ---------------- ------ ----------------------------------
  0x0              bit    conveyor.rotate
  0x1              bit    emitter.emit
  0x2              bit    pusher.extend
  0x3              bit    stack_light.green
  1x0              bit    pusher.extended
  1x1              bit    pusher.retracted
  1x2              bit    sensor_high.detect
  1x3              bit    sensor_low.detect
  3x0              int    counter.short
  3x2              int    counter.tall
```

Put those two tables together and you get the declarations at the top of
`Sorting.st`. `tests/test_openplc.py` does exactly that arithmetic and compares
the result against the file, so the example cannot drift away from the scene
without something saying so.

### An Int is two registers

Look at the gap in that map. `counter.short` is at `3x0`, `counter.tall` at
`3x2`, and `3x1` is not a typo or an omission — **a FactoryForge Int tag is a
signed 32-bit number and takes two consecutive registers, big-endian, high word
first.** `counter.short` is `3x0` *and* `3x1`.

This is the wire format as of HP-49. Before it, an Int was one register
reinterpreted as signed 16-bit, and a counter passing 32,767 came back as
−32,768 with nothing on the wire to say so.

The printed map does not say any of that. Each row is a start address, a type
and a tag, with no width column, so the only hint that an `int` is wider than a
`bit` is the gap in the numbering — and a gap is easy to read as a reserved
address rather than as the rest of the value above it. Two `int` rows invite
`Input_Registers_Size = "2"`. Do that and OpenPLC reads registers 0 and 1,
which are both halves of `counter.short`, calls them `%IW100` and `%IW101`, and
never reads `counter.tall` at all. A program that maps one register to each
counter then shows `counter.short` stuck at 0 (its high word) and
`counter.tall` displaying `counter.short`'s value (its low word) — two wrong
numbers, one of which climbs convincingly. Nothing errors, on either side.

This walkthrough is currently the only place that is written down, which is a
thin place for it to live given the map is what everyone starts from.

OpenPLC's Modbus master has no 32-bit point type: it reads *n* registers and
drops them into *n* consecutive `%IW`s. So the reassembly happens in the
program, which is what you would have to do on any PLC polling a generic Modbus
device:

```
CountShort := DWORD_TO_DINT(SHL(IN := WORD_TO_DWORD(ShortHi), N := 16)
                            OR WORD_TO_DWORD(ShortLo));
```

`DWORD_TO_DINT` reinterprets the bit pattern rather than clamping, so this is
correct for the whole signed range and not only for counters. That was checked
rather than assumed — see [Verifying the 32-bit path](#verifying-the-32-bit-path).

---

## Installing OpenPLC

The runtime is open source and builds from source. On a Debian-family Linux:

```bash
git clone https://github.com/thiagoralves/OpenPLC_v3.git
cd OpenPLC_v3
./install.sh linux
```

That is the supported path and the one to try first. Two things about it are
worth knowing before you spend an afternoon on them.

**The bundled opendnp3 does not configure under CMake 4.** Its
`cmake_minimum_required` names a version CMake 4 has dropped support for, and
`install.sh` stops there with `Configuring incomplete, errors occurred!`.
Ubuntu 26.04 ships CMake 4, so this is not an edge case. The build-environment
override is enough; nothing in OpenPLC needs changing:

```bash
cd utils/dnp3_src && cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 . && make && sudo make install
```

DNP3 is not optional here even though this example never uses it: OpenPLC's
`compile_program.sh` links `-lopendnp3 -lasiodnp3 -lasiopal -lopenpal`
unconditionally on Linux.

**This walkthrough does not use the web UI**, and the steps below are
correspondingly lower-level than OpenPLC's own documentation. The runtime does
not need the web UI: it is a front end that compiles programs and writes config
files, and both can be done directly, which keeps the moving parts down to the
ones this document is actually about.

That was originally written here as "its pinned Flask and pymodbus versions do
not install on Python 3.14", which was a guess from reading `install.sh`'s
`flask==2.3.3` / `pymodbus==2.5.3` pins. Checked afterwards, on Ubuntu 26.04
with Python 3.14, and it is **wrong**: the whole set installs cleanly. So if
you want the UI, use it — OpenPLC's ordinary "Programs → Upload → Compile" and
"Slave Devices → Add new device" flow replaces steps 2 and 3 and the rest of
this document still applies.

One thing to know if you mix the two, because it cost an hour here: compiling
from the command line writes `webserver/active_program` but no row in
`webserver/openplc.db`, and `webserver.py` then dies in its HTTP thread with
`TypeError: 'NoneType' object is not subscriptable` while its REST API on 8443
comes up as though nothing were wrong. Put `blank_program.st` back in
`active_program` and it starts. That is self-inflicted, not an OpenPLC bug.

---

## Running it

### 1. Start the simulator as a Modbus slave

In the folder FactoryForge was extracted to (`.\factoryforge-sidecar` on
Windows):

```
./factoryforge-sidecar demo --driver modbus-tcp -o port 5502
```

<!-- from-source -->
From a source checkout, as the runs below were made:

```bash
python -m factoryforge_sidecar demo --driver modbus-tcp -o port 5502
```
<!-- /from-source -->

`demo` runs the headless Python scene, which is the right thing for a first
run: no Godot, no GPU, and the box counts print on stdout. Against the 3D engine use `connect` instead, with the engine
already running, and the nineteen-tag starter rather than `Sorting.st`. See
the note at the top of this file.

Port 5502 rather than 502 because 502 is privileged on Linux and frequently
already taken on Windows.

**The driver binds `127.0.0.1` and nothing else unless you say so.** Modbus has
no authentication of any kind; a wider bind hands anyone who can route to the
machine the ability to run the conveyor and rewrite the counters. If OpenPLC is
on the same machine, leave it alone. If OpenPLC is in a VM, a container or WSL
while the simulator is on the host, it is a different machine as far as the
socket is concerned and you have to ask:

```
./factoryforge-sidecar demo --driver modbus-tcp -o host 0.0.0.0 -o port 5502
```

<!-- from-source -->
(From a source checkout: `python -m factoryforge_sidecar demo ...` with the same
options.)
<!-- /from-source -->

Against the 3D engine, the F5 dialog's Modbus host and port fields pass the
same two options.

The driver logs a warning when you do, and you will also need to let the port
through the host firewall. The simplest way to avoid all of it is to put both
halves on the same side of the boundary, which is what was done here: the
sidecar runs *inside* WSL alongside OpenPLC, and both use loopback.

### 2. Compile the program

```bash
cp examples/openplc/Sorting.st OpenPLC_v3/webserver/st_files/
cd OpenPLC_v3/webserver
./scripts/compile_program.sh Sorting.st
```

That runs matiec over the ST, generates the glue for the located variables, and
builds `webserver/core/openplc`. It should end with

```
Compilation finished successfully!
```

and, just above, a list naming every located variable it found:
`__QX100_0 … __IW103 … __MD0, __MD1`. If that list is short, a declaration did
not parse — see the troubleshooting table.

### 3. Tell OpenPLC where the slave is

Copy [`examples/openplc/mbconfig.cfg`](../examples/openplc/mbconfig.cfg) to
`OpenPLC_v3/webserver/core/mbconfig.cfg`. The runtime reads it from its own
working directory at startup. (The web UI writes this file for you from the
"Slave Devices" page; the copy here exists so you can skip the web UI.)

The sizes in it are the sorting scene's: 4 discrete inputs, 4 coils, **4** input
registers. Four registers for two counters, because each counter is two.

The polling period is set to 50 ms rather than OpenPLC's default 100 ms. That
matters and is explained under [Timing](#timing).

### 4. Run

```bash
cd OpenPLC_v3/webserver/core
sudo ./openplc
```

`sudo` because the runtime asks for a real-time scheduling priority. It runs
perfectly well without: checked, and all that changes is two lines —
`WARNING: Failed to set main thread to real-time priority` and
`WARNING: Failed to lock memory` — and a fatter jitter distribution. The
first-hour path in Getting Started runs it without `sudo`, from a clone the
student owns, and was graded PASS that way. The 0.3 s of slack in the catch window is wide enough that
this run would not have noticed either way.

Within a second or two the sidecar's status line should come alive:

```
t=2434    belt=2 tall=3 short=3 | rotate=1 emit=1 extend=0 green=1
```

- `rotate=1` and `green=1` immediately — those are unconditional in the program,
  so if they stay 0 the master is not writing coils at all.
- `tall` and `short` climbing together, one box every 3 s alternating.
- `belt` low and steady. Boxes are leaving, not piling up.
- `extend` blipping to 1 shortly after each tall box passes the high sensor.

---

## Verifying the 32-bit path

A sorting run is a weak test of a 32-bit transport: over ten minutes the
counters reach about a hundred, so the high register of every Int is zero from
start to finish. Everything HP-49 added would still work if that half of the
wire were dropped on the floor.

`Sorting.st` publishes its reassembled counters at `%MD0` and `%MD1` for this
reason. OpenPLC's own Modbus slave server exposes `%MD`*n* as holding registers
`2048 + 2n` and `2049 + 2n`, so a master can ask the *PLC* what it thinks the
counter is, rather than asking the simulator what it sent. Start that server by
sending `start_modbus(5020)` to the runtime's interactive port 43628 — which is
all the web UI's "Start PLC" button does — and read it:

```python
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient("127.0.0.1", port=5020); c.connect()
print(c.read_holding_registers(address=2048, count=4, device_id=0).registers)
```

[`examples/openplc/verify_int32.py`](../examples/openplc/verify_int32.py) does
that: it stands up the same engine-stub-plus-driver stack `demo` builds, pins
both counters with a tag-bus `force`, and reads OpenPLC back. OpenPLC stays the
real runtime throughout — the script replaces the engine, not the PLC.

```
        forced  3x0..3x3 on the wire           %MD0 / %MD1 in OpenPLC       verdict
             0  [0, 0, 0, 0]                   0 / 0                        ok
             9  [0, 9, 0, 9]                   9 / 9                        ok
         70000  [1, 4464, 1, 4464]             70000 / 70000                ok
       1000000  [15, 16960, 15, 16960]         1000000 / 1000000            ok
    2147483647  [32767, 65535, 32767, 65535]   2147483647 / 2147483647      ok
            -1  [65535, 65535, 65535, 65535]   -1 / -1                      ok
   -2147483648  [32768, 0, 32768, 0]           -2147483648 / -2147483648    ok
```

Both extremes of the signed range, and the negative cases that only exist
because the tag type is signed, came back exactly. That is the evidence that
matiec's `DWORD_TO_DINT` reinterprets rather than clamps, and that the driver's
`struct.pack(">i")` layout is the one a third-party master reads. Note the
second row: **that is all a sorting run ever tested.**

---

## Timing

The numbers in `Sorting.st` are the scene's, and they are the same numbers as
the SCL:

```
belt 0.5 m/s, sensor_high at 2.0 m (edge at 1.9 m), pusher at 2.5 m,
pusher catches within +/-0.15 m, stroke 0.3 s

travel 1.9 -> 2.5 = 0.6 m / 0.5 m/s = 1.2 s
lead   1.2 s - 0.3 s stroke        = 0.9 s   -> PUSH_DELAY
```

The catch window is 0.9 s to 1.5 s after the sensor edge, and the pusher is
fully out at 1.2 s — 0.3 s of slack. Everything between the sensor and the coil
comes out of that 0.3 s, and on a polled transport the polling period is paid
**twice**: once for the sensor state to reach the PLC, once for the coil to
reach the simulator, with a task interval in between. At OpenPLC's default
100 ms that is about 230 ms of the 300 available. It fits, and it leaves
nothing.

Hence `Polling_Period = "50"` in `mbconfig.cfg` and `T#20ms` for the task —
about 130 ms worst case, and the margin holds. If you raise the polling period and
tall boxes start slipping past, this is why — and the same fix the SCL used
works here: a longer `PUSH_HOLD`.

The emitter is a **1.5 s square wave**, not a pulse, for the same reason it is
one in the SCL: a polled transport samples, and a level held for one scan is a
level the simulator will simply never see. Nothing about Modbus makes this
better than OPC UA — it is a different flavour of the same sampling problem.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Connection failed on MB device FactoryForge: Connection refused` in the runtime log | The sidecar is not up, or is on another port, or is bound to loopback while OpenPLC is in a VM/container/WSL. See the `-o host` note in step 1. |
| Everything stays 0; the sidecar never says `driver READY` | `mbconfig.cfg` is not in `webserver/core/`. The runtime reads it from its working directory and says `Skipping configuration of Slave Devices` when it is missing. Run from a terminal, `./openplc` prints that line, and every `Connection failed on MB device` retry, on its own stdout; the web UI shows the same lines in its log. |
| `invalid located variable declaration` from matiec | matiec will not mix located and unlocated variables in one `VAR` block. Put `AT %...` declarations in their own block. The errors that follow it — "invalid variable before ':='" on perfectly good statements — are fallout from the declarations that got dropped. |
| The red lamp is lit the moment OpenPLC starts, and Start does nothing until Reset | Your trip latches on `NOT PanelEstop` from the first scan. OpenPLC scans before its first Modbus poll returns, every input reads FALSE until then, and a normally closed E-stop that reads FALSE looks struck. Arm the trip only once the E-stop has read healthy (`EstopSeen`; the first-hour program in `GETTING_STARTED.md` step 7 has it), and keep refusing Start while it reads FALSE. The same goes for any normally closed input: guard switches, overloads, a breaker. |
| `rotate=0`, nothing moves | Coils are not being written. Check `Coils_Size` is not 0 and that the program really is the one that got compiled (`webserver/active_program`). |
| `tall` climbs, `short` stays 0 | The pusher is firing on every box: `SensorHigh` is wired to the low sensor. `%IX100.2` is the *high* one. |
| Boxes emitted, nothing ever diverted | `PusherExtend` is not mapped, or `PUSH_DELAY` is wrong for your geometry. |
| Occasional tall box slips through | Timing margin — see above. Lower the polling period or raise `PUSH_HOLD`. |
| A REAL reads as a huge number, e.g. 1065353216.0 for 1.0 | `DWORD_TO_REAL` converts the number, not the bits. Rebuild it with `FF_WORDS_TO_REAL` from any starter in `examples/openplc/<scene>/`. |
| Counters climb in jumps of 65536, or go negative | One half of an Int is being read as the whole thing, or the two halves are swapped. `3x0` is the **high** word of `counter.short` and `3x1` the low. |
| The compile links but `openplc` exits immediately | Another instance is holding port 43628. The runtime binds it on startup and one is already yours if a previous run did not shut down. |

---

## Limits of what this proves

Worth stating plainly, because the point of the exercise was to stop assuming.

- **The web UI was not used.** The program was compiled with OpenPLC's own
  `compile_program.sh` and the slave device configured by writing the same
  `mbconfig.cfg` the UI would have written. The runtime, the compiler and the
  Modbus master are all OpenPLC's; the click path around them is not covered.
  Its dependencies do install on Python 3.14 — that was checked — so this is a
  gap in coverage, not a blocked path.
- **Against the headless Python scene, not the 3D engine.** Same tag bus and
  same driver, but **not** the same tags. This file used to say that
  `connect` in place of `demo` left the addresses where they were. It does
  not: the 3D line has nineteen tags to `demo`'s ten, and the addresses move
  (see the note at the top). The nineteen-tag starter was graded against the
  grader's model of that line on 2026-09-24. No run has had OpenPLC driving
  the Godot window.
- **One platform.** Linux under WSL2. A native Linux install should behave
  identically; the OpenPLC Windows (Cygwin/MSYS2) build was not tried.
- **Loopback.** No switch, no real network, no latency beyond the loopback
  stack. The timing margin is real but it was measured under friendly
  conditions.
- **Only one direction of the register map was exercised.** The sorting scene
  has no Int or Float *outputs*, so OpenPLC never wrote a holding register:
  `%QW100` upward, and the driver's master-write path for multi-register
  values, are untested from this side.
- **The 32-bit cases were forced, not counted to.** Nothing sorted two billion
  cartons. `verify_int32.py` pins the tag and checks what comes out the other
  end, which proves the transport and the reassembly and says nothing about a
  counter that gets there on its own.
