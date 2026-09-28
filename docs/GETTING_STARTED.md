# 🚀 FactoryForge Student Getting Started Guide

Welcome to **FactoryForge**, a free, open 3D factory simulator for learning PLC programming.

| Where to go | |
|---|---|
| [Your first hour](#your-first-hour-no-licence-needed) | Download, a free PLC runtime (OpenPLC), sorted cartons and a graded program. You don't need a licence for any of it. **Start here.** |
| [Second path: Siemens](#second-path-siemens-tia-portal-and-plcsim-advanced) | TIA Portal and S7-PLCSIM Advanced, if you have them |
| [Using FactoryForge](#using-factoryforge) | The start screen, the keys, the operator panel and faults |
| [Building from source](#building-from-source-for-contributors) | For changing the engine, adding a part or adding a driver |

---

## 📋 System Requirements

* **Operating System:** Windows 10/11 or Linux (x86_64)
* **To use FactoryForge:** nothing. The download carries the engine and the
  sidecar, which speaks every PLC protocol and grades your program. You don't
  need Godot, the .NET SDK or Python.
* **A PLC to program:** [OpenPLC Runtime v3](https://github.com/thiagoralves/OpenPLC_v3),
  which is free. It runs on Linux, and on Windows it runs inside WSL, which
  comes with Windows 10 and 11. The other option is Siemens TIA Portal V14–V19
  with S7-PLCSIM Advanced v3.0+.
* **Only to build from source:** Python 3.11+, Godot 4.7 Mono / C# (4.7.2
  recommended) and the .NET 8 SDK.

---

## Your first hour: no licence needed

By the end of this section OpenPLC, a free IEC 61131-3 PLC runtime, is running
a program you wrote in Structured Text. FactoryForge's sorting line is sorting
cartons by height under that program, and FactoryForge's grader has marked it
**PASS**. Nothing costs money and nothing needs Siemens software.

Follow the steps in order, exactly as written. Every command below was run as
printed; [what that run covered](#what-was-checked-and-what-was-not) is listed
at the end of this section.

This is how the pieces connect:

```
FactoryForge ── tag bus ── factoryforge-sidecar ─── Modbus TCP ─── OpenPLC
(the factory)              (a Modbus slave, port 5502)           (the master: your program)
```

The **sidecar** is the translator. FactoryForge speaks only its own tag bus;
the sidecar turns that into Modbus. OpenPLC is the Modbus master and polls it
every 50 ms: it reads the sensors and writes the motors and valves.

> **Windows and WSL.** On Windows, FactoryForge runs on Windows and OpenPLC
> runs inside WSL, which counts as a separate machine on a small virtual
> network. Two steps below exist only because of that, and they are marked
> **(Windows)**. On Linux everything runs on one machine: skip those two
> steps and keep `127.0.0.1` wherever it appears.

### Step 1: Download FactoryForge

Download `FactoryForge-windows.zip` from
[Releases](https://github.com/malgaroshy-maker/factoryforge/releases). You
need a release that has the `grade` command, so **v1.1 or later**. v1.0.0
has no grader.

Right-click the zip, choose **Extract All…**, change the destination to
`C:\FactoryForge`, and click **Extract**. You now have
`C:\FactoryForge\windows\FactoryForge.exe`, with `factoryforge-sidecar.exe`
and an `examples` folder beside it.

*(Linux: take `FactoryForge-linux.zip` and unzip it to `~/FactoryForge`. The
same files are in `~/FactoryForge/linux/`.)*

### Step 2: Install OpenPLC (once, about 20 minutes)

**(Windows) Install WSL first.** Open the Start menu, type `PowerShell`,
right-click **Windows PowerShell** and choose **Run as administrator**. Then
type:

```
wsl --install
```

Restart when it asks you to. After the restart an **Ubuntu** window opens and
asks you for a new UNIX username and password. They can be anything, and they
are not your Windows ones. Use that Ubuntu window from now on whenever this
guide says *Ubuntu*. To reopen it later, type `Ubuntu` in the Start menu.

**In Ubuntu** (or in a terminal on Linux), download and build OpenPLC:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/thiagoralves/OpenPLC_v3.git ~/OpenPLC_v3
cd ~/OpenPLC_v3
CMAKE_POLICY_VERSION_MINIMUM=3.5 ./install.sh linux
```

It asks for the password you just chose, then builds for a while. The
`CMAKE_POLICY_VERSION_MINIMUM=3.5` at the front is not a typo. OpenPLC bundles
an old library whose build script CMake 4 refuses to configure, and newer
Ubuntu releases ship CMake 4. On an older CMake that setting does nothing.

The installer also sets up OpenPLC's web interface as a background service.
This guide does not use it: it compiles and runs the program from the command
line, which takes fewer clicks and shows you what happens. You never need to
open the web page.

### Step 3: Open the sorting line

Double-click `C:\FactoryForge\windows\FactoryForge.exe`. If Windows says
*"Windows protected your PC"*, click **More info**, then **Run anyway**. The
build is not code-signed, and that is all the warning means.

On the start screen, click **Sorting by height**, the first template. Do
**not** click *Watch it run*: that starts a built-in demo controller, which
would fight your PLC for the same outputs.

The line appears and nothing moves. That is correct: the belt, the carton
feeder and the pusher are all PLC outputs, and no PLC is connected yet.

*(Linux: run `~/FactoryForge/linux/FactoryForge.x86_64`.)*

### Step 4: (Windows) Find the address WSL uses to reach Windows

In Ubuntu:

```bash
ip route show default | cut -d' ' -f3
```

It prints one address, for example `172.28.80.1`. Yours will probably be
different. **Wherever this guide shows `172.28.80.1`, type your own address
instead.** WSL can pick a new one after Windows restarts, so run this command
again if a later step stops connecting.

### Step 5: Start the sidecar as a Modbus slave

Open `C:\FactoryForge\windows` in File Explorer, click the address bar, type
`cmd` and press Enter. A Command Prompt opens in that folder. Type:

```
.\factoryforge-sidecar connect --driver modbus-tcp -o host 172.28.80.1 -o port 5502
```

The `.\` at the front matters: PowerShell and some Command Prompt setups will
not run a program from the current folder without it.

The first time, **Windows Defender Firewall** asks whether
`factoryforge-sidecar` may communicate on networks. Tick **Public networks**
as well as Private and click **Allow access**. The link to WSL counts as a
public network, and if you only allow Private, OpenPLC never gets through.

The sidecar prints a warning, which is expected. Modbus has no password, and
binding anything other than `127.0.0.1` makes the slave reachable from outside
this computer. The address you gave it is the one only WSL uses. After the
warning it prints the address map:

```
connected to scene 'sorting-by-height' — 19 tags
driver 'modbus-tcp' started: {'host': '172.28.80.1', 'port': 5502}

Modbus address map:
  ADDRESS          TYPE   REGS     TAG
  ---------------- ------ -------- ------------------------
  0x0              bit    1        conveyor.rotate
  0x1              bit    1        emitter.emit
  0x2              bit    1        panel.green
  ...
  1x8              bit    1        sensor_high.detect
  1x9              bit    1        sensor_low.detect
  3x0              int    2 (0-1)  counter.short
  3x2              int    2 (2-3)  counter.tall
  3x4              float  2 (4-5)  panel.setpoint
```

**It must say 19 tags.** If it does not, FactoryForge is not showing the
sorting line: go back to step 3. Leave this window open. The sidecar runs
until you press Ctrl+C.

*(Linux: in a terminal in `~/FactoryForge/linux`, run
`./factoryforge-sidecar connect --driver modbus-tcp -o port 5502`. It stays
on `127.0.0.1`, there is no firewall question and no warning.)*

### Step 6: Put the starter program into OpenPLC

Every graded scene ships a **starter**: a Structured Text program with all of
its I/O already declared at the right Modbus addresses and an empty space for
your logic, plus the `mbconfig.cfg` that tells OpenPLC where the slave is. In
Ubuntu:

```bash
cd ~/OpenPLC_v3/webserver
cp /mnt/c/FactoryForge/windows/examples/openplc/sorting-by-height/sorting_by_height.st st_files/
cp /mnt/c/FactoryForge/windows/examples/openplc/sorting-by-height/mbconfig.cfg core/
sed -i 's/address = "127.0.0.1"/address = "172.28.80.1"/' core/mbconfig.cfg
```

The last line **(Windows only)** points OpenPLC at Windows instead of at
itself; use your own address from step 4. *(Linux: the zip is at
`~/FactoryForge/linux/`, so the two paths start with that, and you skip the
`sed`.)*

The starter's table of signals is
`C:\FactoryForge\windows\examples\openplc\sorting-by-height\README.md`. Every
tag, its direction, its Modbus address and its name in the program are listed
there. It must match the map the sidecar printed in step 5, line for line.

### Step 7: Make it move

Open the program in Ubuntu's text editor:

```bash
nano st_files/sorting_by_height.st
```

You make two changes. To paste into nano, right-click in the Ubuntu window.

**First change: seven new variables.** Use the arrow keys to find the line that
starts `PanelSetpoint : REAL;`. Put the cursor at the end of that line, press
Enter, and paste:

```
    Running   : BOOL;    (* the line is running *)
    Tripped   : BOOL;    (* the E-stop has tripped; only Reset clears it *)
    EstopSeen : BOOL;    (* the E-stop has read healthy since OpenPLC started *)
    rStart    : R_TRIG;  (* the moment Start goes down *)
    rReset    : R_TRIG;  (* the moment Reset goes down *)
    tEmit     : TON;     (* times the feed: one carton every 3 s *)
    EmitFlag  : BOOL;
```

They must end up above the `END_VAR` that follows. OpenPLC's compiler will
not accept your own variables in the first `VAR` block, which holds the
addresses, and that is why the starter has a second one.

**Second change: the logic.** Find the line that starts
`(* ===== 3. Outputs`. Put the cursor at the very start of that line and
paste:

```
  (* The operator panel. Start and Reset are momentary: act on the scan
     the button goes down, not for as long as it is held. *)
  rStart(CLK := PanelStart);
  rReset(CLK := PanelReset);

  (* Until OpenPLC's first poll reaches the slave, every input reads
     FALSE, and a normally closed E-stop that reads FALSE looks struck.
     So a trip only counts once the E-stop has been seen healthy. *)
  IF PanelEstop THEN
    EstopSeen := TRUE;
  END_IF;

  (* The E-stop trips the line, and the trip latches: releasing the
     mushroom does not clear it, and neither does Start. Only Reset does. *)
  IF EstopSeen AND NOT PanelEstop THEN
    Tripped := TRUE;
  ELSIF rReset.Q THEN
    Tripped := FALSE;
  END_IF;

  (* Start runs the line, and only while the E-stop reads healthy. Stop or
     a trip stops it. Reset alone does not start anything: after a trip it
     takes Reset, then Start. *)
  IF Tripped OR PanelStop OR NOT PanelEstop THEN
    Running := FALSE;
  ELSIF rStart.Q THEN
    Running := TRUE;
  END_IF;

  ConveyorRotate  := Running;
  StackLightGreen := Running;
  PanelGreen      := Running;
  PanelRed        := Tripped;

  (* Feed while the line runs: a square wave, 1.5 s high and 1.5 s low,
     one carton per rising edge. A short pulse would be missed: OpenPLC
     polls every 50 ms. *)
  IF Running THEN
    tEmit(IN := TRUE, PT := T#1500ms);
    IF tEmit.Q THEN
      EmitFlag := NOT EmitFlag;
      tEmit(IN := FALSE, PT := T#1500ms);
    END_IF;
  ELSE
    tEmit(IN := FALSE, PT := T#1500ms);
    EmitFlag := FALSE;
  END_IF;
  EmitterEmit := EmitFlag;

```

Save with **Ctrl+O**, then **Enter**, and leave nano with **Ctrl+X**. Then
compile:

```bash
./scripts/compile_program.sh sorting_by_height.st
```

It lists every located variable it found and ends with
`Compilation finished successfully!`. If it stops earlier, look at the lines
just above the end of its output: the compiler names the line it could not
read. Now start the PLC:

```bash
cd core
./openplc
```

It keeps running in that window. First it prints the slave device it read
from `mbconfig.cfg`: check that `Address:` is the address from step 4
(`127.0.0.1` on Linux). Two warnings about real-time priority and locking
memory are harmless. After that it prints nothing while all is well. If it
repeats `Connection failed on MB device FactoryForge`, it cannot reach the
sidecar: see the table at the end of this section.

Within a second the sidecar's window shows `driver READY`. Nothing moves yet,
and that is correct: the line waits for its operator. In FactoryForge press
**F1** for Run mode and click the green **Start** button on the panel. The
sidecar's `OUT` line shows `rotate=1`, the belt runs and a carton appears
every 3 s. Every carton runs to the far end, tall or short: nothing tells the
pusher to move yet.

Now try the E-stop, because this is the part that matters on a real line:

1. Click the red mushroom. The belt stops at once and the red lamp lights.
2. Click it again to release it. The belt stays stopped.
3. Click **Start**. Still nothing: the trip is latched.
4. Click the blue **Reset**. The red lamp goes out, and the belt stays
   stopped, because Reset clears the trip and starts nothing.
5. Click **Start**. The line runs again.

*Why `NOT PanelEstop`?* The E-stop is wired **normally closed**: the tag is
TRUE while the circuit is healthy and FALSE when the mushroom is struck, so a
cut wire stops the line too. *Why the latch?* A machine that restarted by
itself the moment somebody released the mushroom, or on one press of Start,
could start while a person is still reaching into it. That is why a trip needs
Reset first, and a separate Start after it. *Why `EstopSeen`?* OpenPLC runs
its first scans before its first Modbus poll has come back, and until then
every input reads FALSE, the E-stop included. Without `EstopSeen` the program
would trip on its own every time OpenPLC starts, and the red lamp would be lit
before anybody touched anything. The line still cannot run while the E-stop
reads FALSE: that is the `NOT PanelEstop` beside `Tripped` and `PanelStop`, so
a cut wire still stops it. *Why `R_TRIG`?* A click holds
Start down for 0.2 s, which is about ten of this program's 20 ms scans. The
program acts on the rising edge, once, however long the button is held. The
grader checks all of this. It presses Start itself and strikes the mushroom
halfway through the run, then presses the buttons in the order above.

### Step 8: Make it sort

Press **Ctrl+C** in the Ubuntu window to stop OpenPLC, then:

```bash
cd ..
nano st_files/sorting_by_height.st
```

**Four more variables**, on a new line after `EmitFlag  : BOOL;`, still above
`END_VAR`:

```
    HighMem   : BOOL;    (* the high beam, one scan ago *)
    PushReq   : BOOL;    (* a tall carton is on its way to the pusher *)
    tDelay    : TON;     (* high beam -> carton in front of the pusher *)
    tPush     : TON;     (* how long the pusher stays out *)
```

**The sorting logic**, at the very start of the line that starts
`(* ===== 3. Outputs` again, so it lands below what you added in step 7:

```
  (* A tall carton breaks the high beam 1.2 s before it reaches the pusher,
     and the pusher takes 0.3 s to come out: wait 0.9 s, then push. *)
  IF SensorHighDetect AND NOT HighMem THEN
    PushReq := TRUE;
  END_IF;
  HighMem := SensorHighDetect;
  tDelay(IN := PushReq, PT := T#900ms);
  tPush(IN := tDelay.Q, PT := T#1500ms);
  PusherExtend := tDelay.Q AND NOT tPush.Q;
  IF tPush.Q THEN
    PushReq := FALSE;
  END_IF;

```

Save, compile and run as before:

```bash
./scripts/compile_program.sh sorting_by_height.st
cd core
./openplc
```

A restarted program starts with the line stopped, so click **Start** in
FactoryForge again. Tall cartons now go down the chute and short ones carry
on to the end. The high beam sees only tall cartons, so its rising edge is
the whole decision.
Because the edge is gone long before the carton reaches the pusher, the
program latches it in `PushReq`, then waits and strokes the pusher.

This is the only scene whose answer this guide writes out, because it is the
tutorial. The starters for every other scene are empty.

### Step 9: Get it graded

The grader replaces FactoryForge's window with a copy of the same line that it
watches and marks. It sets the order of tall and short cartons itself and
shuffles it, so a program that pushes every second carton without reading a
sensor fails. It is also the operator: it presses Start 1 s in, and about
16 s in it strikes the mushroom, releases it, presses Start alone, then Reset,
then Start, exactly as you did in step 7. It offers the same 19 tags, so the
same program and the same addresses work unchanged.

In the Command Prompt from step 5, press **Ctrl+C** to stop the sidecar.

Then **restart OpenPLC**, so that the grader meets your program fresh, with
the line stopped. In Ubuntu, press **Ctrl+C** and start it again:

```bash
./openplc
```

This matters. A PLC keeps its variables while it runs, and OpenPLC keeps
writing the outputs it last had. If the line was running when you stopped the
sidecar, it is still running when the grader connects, before anybody has
pressed Start, and the grader fails `line.started_by_start`. Until the grader's
sidecar is up, OpenPLC's window repeats `Connection failed on MB device
FactoryForge`, which is expected: it finds the new slave on its own.

Now start the grader:

```
.\factoryforge-sidecar grade --scene sorting-by-height
```

It prints the port it is listening on:

```
  Connect your controller with:
    .\factoryforge-sidecar connect --driver <yours> --port 61507 -o <options>
```

Open a **second** Command Prompt in `C:\FactoryForge\windows` (address bar,
`cmd`, Enter) and connect the sidecar to the grader instead of to
FactoryForge. Add `--port` and **the number your grader printed**:

```
.\factoryforge-sidecar connect --driver modbus-tcp -o host 172.28.80.1 -o port 5502 --port 61507
```

The grader starts its 60-second window once OpenPLC has polled, then prints
the verdict:

```
====================================================================
PASS   every check met
====================================================================
  ...
  [ok] controller.stayed_connected    1 session(s), 0 of them dropped before the end
  [ok] integrity.no_forced_tags       no tag was forced
  [ok] integrity.no_input_writes      no writes aimed at simulator-owned tags
  [ok] line.ran                       16 cartons reached a lane (at least 8 needed in the 60s window)
  [ok] line.both_lanes                chute 8, far end 8 (at least 3 each)
  [ok] sort.tall_diverted             no tall carton ran off the far end
  [ok] sort.short_passed              no short carton was diverted
  [ok] line.conservation              17 fed = 16 sorted + 1 still on the belt
  [ok] line.started_by_start          the belt never started without somebody pressing Start
  [ok] estop.stopped_the_belt         the belt moved 55 mm after the mushroom was struck (at most 100 mm, which is 200 ms of belt)
  [ok] estop.latched_until_reset      the belt stayed still from the release until Reset and then Start
  [ok] estop.restarted_after_reset    the belt was running again 0.10s after Start at 22.83s (within 1s)
  ...
RESULT grade=PASS scene=sorting-by-height checks=12/12 failed=none forced=0
```

Your counts can differ by a carton or two. The verdict is what matters. Press
Ctrl+C in the second window to stop that sidecar, and Ctrl+C in Ubuntu to stop
OpenPLC.

To see what a FAIL looks like, take the sorting logic from step 8 back out,
compile, run and grade again. The report names the failed checks and says
*"The pusher never came out"*. Every report ends by saying what went wrong in
terms of the line. Add `--json mark.json` to the grade command for the whole
run as JSON, and `--student <name>` to put a name on it. `.\factoryforge-sidecar
grade --list` shows every scene the grader can mark.

### Where to go next

- **Another scene.** Every graded scene has a starter in
  `examples\openplc\<scene>\`, and its README gives the task, the I/O table and
  the `mbconfig.cfg`. The pattern is always the same: open the template in
  FactoryForge, **then** start the sidecar, copy the starter and its
  `mbconfig.cfg` into OpenPLC, write the logic, compile, run and grade. Restart
  the sidecar whenever you change scene, because it hands out Modbus addresses
  once, for the scene that was open when it started. `examples\README.md` lists
  the scenes.
- **The operator contract.** This program already honours Start, Stop, the
  latching E-stop and Reset. The sorting grader marks all of it except Stop,
  which it never presses. The *Start / stop station* scene adds a batch
  counted against the pot, which the line has to stop by itself.
- **32-bit values.** An Int or Float tag is two Modbus registers, high word
  first. The starter's first and last sections join and split them for you, so
  your logic only ever sees plain `DINT` and `REAL` variables.

### If something does not work

| What you see | Why, and what to do |
|---|---|
| The sidecar says `no engine listening on ws://127.0.0.1:7411` | FactoryForge is not running. Step 3. |
| The sidecar says a number other than 19 tags | Another scene is open. Open *Sorting by height* and restart the sidecar. |
| `rotate=0` forever even after you press Start, the sidecar never says `driver READY`, and OpenPLC repeats `Connection failed on MB device FactoryForge` | OpenPLC is not reaching the slave. On Windows: `core/mbconfig.cfg` still says `127.0.0.1`, or your WSL address changed (step 4 again, then the `sed` in step 6 with the new one, and restart both), or the firewall question was answered without **Public networks**. Everywhere: `mbconfig.cfg` must be in `webserver/core/`, the folder you start `./openplc` from. |
| `./openplc` exits at once | Another OpenPLC runtime is already running. If you pressed *Start PLC* in OpenPLC's web interface, press *Stop PLC* there first. |
| The compiler reports `invalid located variable declaration` | A variable of yours ended up in the first `VAR` block, among the `AT %…` lines. Move it to the second one. |
| No cartons appear, or only one does | `EmitterEmit` makes one carton per **rising** edge, and the level has to last longer than the 50 ms poll. |
| Some tall cartons get past the pusher | Timing. OpenPLC's polling period is paid twice, once for the sensor and once for the pusher. Keep `Polling_Period = "50"` in `mbconfig.cfg`. |
| The red lamp lights the moment OpenPLC starts, and Start does nothing until you press Reset | Your trip logic has no `EstopSeen`. Until OpenPLC's first poll comes back every input reads FALSE, and a normally closed E-stop that reads FALSE looks struck. The grader presses Start without Reset first, so this fails `estop.stopped_the_belt`: the belt was never running to be stopped. |
| The grader fails `line.started_by_start`, *"the belt started … at [0.01]s"* | OpenPLC was not restarted before grading, and the line was still running from before (step 9). |
| The grader says `ERROR` and *no controller connected* | The second sidecar was not started within the grader's wait (120 s), or its `--port` is not the number the grader printed. |

More on OpenPLC itself (addressing, the 32-bit register format, timing, and
how the numbers above were derived) is in
[`docs/OPENPLC.md`](https://github.com/malgaroshy-maker/factoryforge/blob/master/docs/OPENPLC.md)
in the repository. That file is not in the download.

### What was checked, and what was not

On 2026-09-24 every FactoryForge command above ran from a release zip built
from this repository and unpacked into an empty folder, with no Python on
`PATH`. OpenPLC was OpenPLC_v3 `b5d4135` in WSL (Ubuntu 26.04). The starter
from that zip compiled with OpenPLC's own `compile_program.sh`. The step 7
program graded **FAIL**: 9 tall cartons past the pusher, *"The pusher never
came out"*. The step 8 program graded **PASS**, 8/8 checks, with 9 tall down
the chute and 9 short past the end. OpenPLC was the controller in both runs.

That run was of the program as it stood then, which ignored Start because the
grader never pressed it. IP-35 made the grader press Start and mark the
E-stop, and step 7 was rewritten for it. On 2026-09-25 the rewritten program
was run on OpenPLC too, and the first version of it did not work there:

- **It tripped by itself at every start.** OpenPLC runs its first scans before
  its first poll returns, so the E-stop read FALSE, the trip latched, and the
  grader's Start 1 s in did nothing. It graded **FAIL** on
  `estop.stopped_the_belt`, even with the sorting logic in (11/12), and it did
  so whether OpenPLC started before the slave or 3 s after it. That is why
  step 7 now has `EstopSeen`.
- **Step 9 said to leave OpenPLC running.** A line still running from step 8
  was still running when the grader connected, and the grader failed
  `line.started_by_start` at 0.01 s (11/12). That is why step 9 now restarts
  OpenPLC.

The program as it now stands was then run on OpenPLC. The four code blocks in
steps 7 and 8 were taken out of this file by a script and inserted into the
starter where those steps say; the script checked that each block is in the
program byte for byte and that the program minus the blocks is the starter.
OpenPLC's `compile_program.sh` built it and `./openplc` ran it, with the
starter's `mbconfig.cfg`. The grader and the second sidecar were the commands
in step 9, run from source inside WSL, with OpenPLC restarted first:

- **Step 7:** **FAIL**, 10/12. 8 tall cartons past the pusher, *"The pusher
  never came out"*, and all four panel checks met: `line.started_by_start`,
  `estop.stopped_the_belt` (60 mm), `estop.latched_until_reset`,
  `estop.restarted_after_reset` (0.08 s).
- **Step 8:** **PASS**, 12/12, on seeds 1, 2 and 3. Every tall carton down
  the chute, every short one past the end, the belt 30 to 55 mm past the
  mushroom and running again 0.10 to 0.11 s after Start. The output in step 9
  is from seed 2. It also passed 12/12 with OpenPLC started 3 s after the
  grader's sidecar (seed 4).

Five things were **not** covered by those runs, and they are listed here so
that nobody mistakes them for tested:

- **Your clicks in step 7.** The E-stop, Reset and Start sequence was pressed
  by the grader, not in the 3D window, and the program was not run against the
  3D window at all (see below).
- **The crossing from WSL to Windows.** In that run OpenPLC and the grader
  shared a loopback, as on Linux, and the grader and sidecar there were the
  same code run from source inside WSL. The firewall question in step 5 was not
  answered on the test machine, so the `-o host` address and the `sed` in
  step 6 are checked only as far as this: WSL reaches a Windows program that
  the firewall allows, at the step 4 address.
- **OpenPLC driving the 3D window.** The 3D line was checked separately: the
  release's sidecar connected to it and printed the same 19-tag map as the
  starter. It was not driven by OpenPLC. The 0.9 s push delay is the same one
  the scene's own exercise uses against the 3D line.
- **A fresh `install.sh`.** OpenPLC had been built earlier on the same machine
  (see `docs/OPENPLC.md`). The CMake workaround was checked against the CMake
  version it is for (4.2), but not inside a full install.
- **The Linux archive.** Not built with the grader yet.

---

## Second path: Siemens TIA Portal and PLCSIM Advanced

If you have TIA Portal and S7-PLCSIM Advanced, the sidecar reaches a virtual
S7-1500 three ways. Each is a `connect` command run from the FactoryForge
folder while FactoryForge is running, just like step 5 above. The TIA
programs are in `examples\tia\`: `Sorting.scl` with its `FF_IO` data block
(walkthrough in `examples\tia\README.md`), and an empty starter for every
graded scene in `examples\tia\<scene>\`, each with a mapping file for each
driver below.

Each method below was checked end to end against a virtual S7-1500 driving
the 3D scene. That was done from a source checkout. The release carries the
same drivers, and `.\factoryforge-sidecar drivers` lists them, but they have
not been run against a CPU from the release.

### Method A: Direct PLCSIM Advanced Native API Driver (Recommended — Zero Licence Cost)

**Verified end to end against a real PLCSIM Advanced CPU driving the 3D scene.**
Windows only.

1. Open **S7-PLCSIM Advanced Control Panel** and start a virtual CPU. Either
   communication mode works for this driver — it talks to the API directly, so
   the default local **Softbus** mode is fine and no IP is needed.
2. Download your TIA Portal program and the `FF_IO` datablock
   (`examples\tia\`) to it.
3. With the engine running, attach the driver:

```
.\factoryforge-sidecar connect --driver plcsim-advanced -o instance <instance name> --mapping examples\plcsim_mapping.json
```

Two things that will cost you time otherwise:

- **The instance name is the one in the PLCSIM control panel**, which is not
  necessarily the CPU's name in TIA. If `connect` reports `InstanceNotRunning`,
  that mismatch is the usual reason.
- **The mapping is required and holds PLC symbols, not NodeIds** — `FF_IO.ConveyorRotate`,
  dotted and unquoted. That is the form the API's own tag list reports, and it
  differs from the OPC UA spelling (`ns=3;s="FF_IO"."ConveyorRotate"`).

`examples\plcsim_mapping.json` maps the ten members of `Sorting.scl`'s
`FF_IO`. A starter's own `plcsim_mapping.json` in `examples\tia\<scene>\` maps
all of that scene's tags.

---

### Method B: OPC UA Server Connection

> **PLCSIM Advanced must be on the Virtual Ethernet Adapter for this.** In the
> default local *Softbus* mode the virtual CPU has no IP at all, so nothing
> listens on 4840 no matter what address the TIA project shows. Switch the
> instance's communication interface in the PLCSIM control panel, then start it
> and download again.

1. Enable **OPC UA Server** in TIA Portal CPU properties under *Protection & Security -> OPC UA*.
2. Compile and download to PLCSIM Advanced (`192.168.1.20:4840` here; use your CPU's address).
3. Discover NodeIds using the sidecar browse tool:

```
.\factoryforge-sidecar browse opc.tcp://192.168.1.20:4840
```

4. Attach the sidecar to the running engine with your OPC UA mapping:

```
.\factoryforge-sidecar connect --driver opcua-client -o url opc.tcp://192.168.1.20:4840 --mapping examples\opcua_mapping.json
```

**Verified end to end against a virtual S7-1500 driving the 3D scene.**

---

### Method C: Snap7 (ISO-on-TCP, no OPC UA licence)

Also verified. Needs two things beyond Method B:

```
.\factoryforge-sidecar connect --driver s7-snap7 -o host 192.168.1.20 -o db 1 --mapping examples\snap7_mapping.json
```

1. **`FF_IO` must not be an "optimized block access" DB.** An optimized block
   has no absolute byte addresses, and snap7 addresses bytes. Right-click the DB
   → *Properties* → *Attributes*, uncheck it, recompile, download.
2. **The mapping holds byte addresses** — `DBX0.0` for a Bool, `DBD2` for a
   DInt — which are the values in TIA's *Offset* column. They move if you
   reorder the DB.

If every read fails with `Invalid address (0x05)`, the usual cause is the read
running past the end of the DB, not the optimized-access setting.

To have a TIA program graded, follow [step 9](#step-9-get-it-graded) with your
Siemens `connect` command in place of the Modbus one.

---

## Using FactoryForge

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
| **Guarded cell** | The guarding chain, and the first scene where the controller does not command the motor: your program energises a contactor coil and the contactor runs the belt. A dual-channel safety relay and an area scanner whose muting expires decide whether the coil may be energised at all — a permissive is not a command, and closing the relay must start nothing |
| **Batch dosing** | A cascade: an outer loop watching a totaliser sets the setpoint of an inner loop trimming a pump. The batch ends on a quantity rather than a timer, which is why the same recipe takes twice as long at half the flow and still delivers the same litres |
| **Star-delta starter** | The classic exam circuit: main, star and delta contactors on one motor. Change over on the motor's speed rather than a timer, and never let star and delta conduct together — the contacts open slower than they close, so that needs a dead time |
| **Cooling tunnel** | The heat treat plate with a fan on it: one plant, two actuators pulling opposite ways. Split-range control — heater above zero, fan below it, a deadband between, and never both at once — through a recipe that drops sharply and climbs again |
| **Air receiver** | A compressed-air receiver on a 4-20 mA transmitter and an isolation valve with limit switches. The PLC gets raw counts and contacts: scale the one (`NORM_X`, `SCALE_X`) to hold a pressure band, and prove the other by its feedback within the valve's travel time |
| **Press station** | A pneumatic press with a MAN/OFF/AUTO selector, a two-hand station and a limit switch at the bottom of the stroke. The mode interlock: the selector decides who may move the ram, and in MAN only the two-hand relay's permissive may — not the two buttons ANDed in your program |
| **Rotary index station** | A turntable that turns each carton a quarter, a cylinder that sweeps it onto the outfeed, and a retroreflective eye across the deck. Two motions in one space: each waits for the other's limit switches, never for a timer |
| **Pivot diverter line** | A blade on a post that turns tall cartons off a running belt into a chute, sorted by two retroreflective beams at two heights. The blade has to be across before a carton reaches it and held until the chute has counted it, so latch the decision instead of timing the hold |
| **Mezzanine lift** | A vertical lift that takes cartons from a floor conveyor up to an outfeed on a mezzanine, the first scene on two levels. The lift's handshake: stop the carriage's deck the moment `lift.occupied` says a carton is aboard, and discharge only once `lift.atlevel` says the carriage has arrived -- never after the time the climb took yesterday |
| **Servo positioning** | A servo axis with a PLCopen-shaped interface — enable and ready, an absolute move, and an error the drive latches by itself. Shuttle between two stations, and when the drive faults, hold until the operator's Reset rather than acknowledging on your own |
| **Palletising cell** | An arm placing cartons onto a pattern the pallet station generates, layer by layer. The program does the inverse kinematics; the station says where the next one goes and counts what landed. Teaches multi-axis coordination against a pattern rather than against a fixed position |

To skip the start screen — scripting a run, or grabbing a screenshot:

```
.\FactoryForge.exe -- --scene=res://templates/tank_level_control.json
```

<!-- from-source -->
(From a source checkout: `godot --path engine/ -- --scene=…`.)
<!-- /from-source -->

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
| Start (green) | `panel.start` | Momentary — one rising edge per click, held 0.2 s so a polled link sees it (the panel's *Press Hold* setting). Act on the edge, not the level |
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

A controller worth the name should then refuse to restart: Reset while the
fault stands does nothing, and clearing the fault alone does not restart the
line either — the trip is still latched, and only Reset then Start bring it
back.

**Every shipped scene answers to this panel.** Start runs the line, Stop stops
it, the mushroom latches a trip that only Reset clears, and the pot changes
what the line is aiming at while it runs. The toolbar's **🧪 Try** button
drives the scene with the grader's own reference controller, pressing the same
buttons the grader's exam presses, and reports whether the scene completed
(<!-- from-source -->from a source checkout, so does
`python tools/try_scene.py --scene <id>`<!-- /from-source -->), so the program the grader is checked
against is also checked against the scene you see.

If you need repeatable results, for example to compare two runs, start it as
`.\FactoryForge.exe -- --deterministic` for the fixed-timestep scene. (To mark
a program, use the grader from [step 9](#step-9-get-it-graded) instead.) Both expose the same tags,
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
program against it? Press the toolbar's **🧪 Try** button (or **🧪 Try this
scene**, the same action offered inside the F5 dialog). It drives the open
scene the way a PLC would and reports `PASS`/`FAIL`, and you watch it happen.<!-- from-source -->
From a source checkout the same exercise runs headless:

```bash
python tools/try_scene.py --scene tank-level-control
```

It spawns the engine and reports in the terminal;
`python tools/try_scene.py --list` shows every scene id.<!-- /from-source --> This is what to
reach for instead of `connect --driver mock` — the mock driver only records
what it is told, so connecting it to a scene you have not written a program
for yet leaves the line more dead than doing nothing at all.

---

## 🔌 `connect` vs `demo` — read this first

The engine speaks one protocol: its own tag bus. **Every PLC protocol lives in
the sidecar**, so connecting a PLC always means starting the sidecar
alongside the running engine, as in [step 5](#step-5-start-the-sidecar-as-a-modbus-slave).

There are two subcommands and picking the wrong one wastes an afternoon:

| | |
|---|---|
| **`connect`** | Attaches to an engine that is **already running**. This is the one you want with the 3D engine. |
| **`demo`** | Starts its **own** headless Python scene on the bus port. For a quick driver check with no FactoryForge window involved. |

Run `demo` while the 3D engine is up and it finds port 7411 taken — or worse,
binds first and drives a scene you cannot see while the 3D one sits still.
`demo`'s scene is also a different, smaller one: ten tags, not the 3D sorting
line's nineteen, so its Modbus addresses are not the starter's.
`examples/openplc/Sorting.st` is the finished program for *that* map.

The **F5 Driver dialog** starts the sidecar for you: pick a driver, fill in the
address, and *Apply & Connect* starts it and copies the command to your
clipboard in case you would rather run it yourself. For Modbus it serves
`127.0.0.1:502` unless you change its **Modbus Server Bind Host** and **Port**
fields; loopback suits a PLC on this same machine only. For OpenPLC in WSL,
enter the address from step 4 and `5502`, the same values as step 5's
command.

<!-- from-source -->
From a source checkout, `python -m factoryforge_sidecar` (run in `sidecar/`)
stands in for `factoryforge-sidecar` in every command in this guide.
<!-- /from-source -->

---

## 📦 Starter programs for every graded scene

Every graded scene has a starter for your own PLC IDE in `examples/`:
`examples/openplc/<scene>/` (Structured Text plus `mbconfig.cfg`) and
`examples/tia/<scene>/` (an SCL source with the global DB `FF_IO`, plus a
mapping file for each Siemens driver). The I/O is declared and commented; the
logic is yours. Open the scene first, then start the sidecar, and restart the
sidecar if you change scene: Modbus addresses are handed out once, in tag-id
order. The index with the connect commands is `examples/README.md`, and
[the first hour](#your-first-hour-no-licence-needed) walks one scene from
starter to grade.

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

```
.\factoryforge-sidecar browse opc.tcp://192.168.1.20:4840
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

---

<!-- from-source -->
## Building from source (for contributors)

Everything above uses the download. Build it yourself if you intend to change
the engine, add a part or add a driver. You need Python 3.11+, Godot 4.7 mono
and the .NET 8 SDK.

### 1. Clone and install the sidecar

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

How many pass is whatever `pytest` prints. This guide does not write the number
down: it has grown steadily and every place that did went stale. What matters is that
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
<!-- /from-source -->
