# Driving FactoryForge from OpenPLC over Modbus TCP

End-to-end walkthrough: an ordinary IEC 61131-3 Structured Text program, running
on the OpenPLC Runtime, sorts boxes by height in FactoryForge — no Siemens
software anywhere, and nothing in the program that FactoryForge put there.

This is the launch criterion in [`PRD.md`](PRD.md): *"an unmodified OpenPLC
program drives the same scene over Modbus, proving the project is not
Siemens-only."* The program is [`examples/openplc/Sorting.st`](../examples/openplc/Sorting.st),
the line-for-line counterpart of [`examples/tia/Sorting.scl`](../examples/tia/Sorting.scl).

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

The sidecar prints its half of the map, and it is the authority:

```bash
python -m factoryforge_sidecar connect --driver modbus-tcp -o port 5502
```

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

```bash
python -m factoryforge_sidecar demo --driver modbus-tcp -o port 5502
```

`demo` runs the headless Python scene, which is the right thing for a first
run: no Godot, no GPU, and the box counts print on stdout. Against the 3D
engine use `connect` instead, with the engine already running — see AGENTS.md.

Port 5502 rather than 502 because 502 is privileged on Linux and frequently
already taken on Windows.

**The driver binds `127.0.0.1` and nothing else unless you say so.** Modbus has
no authentication of any kind; a wider bind hands anyone who can route to the
machine the ability to run the conveyor and rewrite the counters. If OpenPLC is
on the same machine, leave it alone. If OpenPLC is in a VM, a container or WSL
while the simulator is on the host, it is a different machine as far as the
socket is concerned and you have to ask:

```bash
python -m factoryforge_sidecar demo --driver modbus-tcp -o host 0.0.0.0 -o port 5502
```

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

`sudo` because the runtime asks for a real-time scheduling priority and locks
its memory; it runs without, printing two warnings and a fatter jitter
distribution.

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
| Everything stays 0; the sidecar never logs `master connected from ...` | `mbconfig.cfg` is not in `webserver/core/`. The runtime reads it from its working directory and says `Skipping configuration of Slave Devices` when it is missing — in its internal log, which is not stdout. |
| `invalid located variable declaration` from matiec | matiec will not mix located and unlocated variables in one `VAR` block. Put `AT %...` declarations in their own block. The errors that follow it — "invalid variable before ':='" on perfectly good statements — are fallout from the declarations that got dropped. |
| `rotate=0`, nothing moves | Coils are not being written. Check `Coils_Size` is not 0 and that the program really is the one that got compiled (`webserver/active_program`). |
| `tall` climbs, `short` stays 0 | The pusher is firing on every box: `SensorHigh` is wired to the low sensor. `%IX100.2` is the *high* one. |
| Boxes emitted, nothing ever diverted | `PusherExtend` is not mapped, or `PUSH_DELAY` is wrong for your geometry. |
| Occasional tall box slips through | Timing margin — see above. Lower the polling period or raise `PUSH_HOLD`. |
| Counters climb in jumps of 65536, or go negative | One half of an Int is being read as the whole thing, or the two halves are swapped. `3x0` is the **high** word of `counter.short` and `3x1` the low. |
| The compile links but `openplc` exits immediately | Another instance is holding port 43628, or the S7 server could not bind port 102 (needs root). |

---

## Limits of what this proves

Worth stating plainly, because the point of the exercise was to stop assuming.

- **The web UI was not used.** The program was compiled with OpenPLC's own
  `compile_program.sh` and the slave device configured by writing the same
  `mbconfig.cfg` the UI would have written. The runtime, the compiler and the
  Modbus master are all OpenPLC's; the click path around them is not covered.
  Its dependencies do install on Python 3.14 — that was checked — so this is a
  gap in coverage, not a blocked path.
- **Against the headless Python scene, not the 3D engine.** Same tag bus, same
  driver, same ten tags — `connect` in place of `demo` and the addresses do not
  move — but this run did not have Godot in it.
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
