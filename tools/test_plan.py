"""Run the whole FactoryForge test plan and report.

    python tools/test_plan.py              # everything that needs no display
    python tools/test_plan.py --gui        # add the checks that need one
    python tools/test_plan.py --only C,E   # just those sections
    python tools/test_plan.py --only H     # all five scene exercises, headless

Exits non-zero if anything failed. See docs/TEST_PLAN.md for what each check is
for; the short reasons here are so a failure explains itself without the doc.

Why a runner rather than a CI yaml: the interesting failures in this project are
at the seam between the Python sidecar and the Godot engine, and checking that
means starting an engine, driving it, and reading what came back. That is
awkward to express as a list of shell steps and easy to express here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
SIDECAR = ROOT / "sidecar"

#: Where Godot writes user:// on this platform, for tests that read exports.
USER_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Godot" / "app_userdata" / "FactoryForge"


def find_godot() -> str | None:
    """$GODOT, then PATH, then the usual download locations.

    Shared with run.py rather than copied: the runner and the launcher have to
    agree about which Godot a machine has, or the suite tests a different build
    from the one a person just launched.
    """
    sys.path.insert(0, str(ROOT))
    from run import find_godot as locate     # noqa: PLC0415 — avoids a cycle at import time
    return locate()


GODOT = find_godot()


@dataclass
class Result:
    ident: str
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


RESULTS: list[Result] = []


def record(ident: str, name: str, ok: bool, detail: str = "", skipped: bool = False) -> bool:
    RESULTS.append(Result(ident, name, ok, detail, skipped))
    mark = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    print(f"  [{mark}] {ident} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return ok


def run(cmd: list[str], cwd: Path = ROOT, timeout: float = 180) -> tuple[int, str]:
    """Run a command, returning (exit code, stdout+stderr)."""
    try:
        # encoding is explicit: the sidecar prints an em dash, and letting
        # Windows decode as cp1252 turned "— 16 tags" into "â€” 16 tags" and
        # broke a regex that was looking for it.
        proc = subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return 124, (out if isinstance(out, str) else out.decode("utf-8", "replace")) + "\n[TIMEOUT]"


def engine(args: list[str], headless: bool = True, timeout: float = 90) -> tuple[int, str]:
    cmd = [GODOT or "godot"]
    if headless:
        cmd.append("--headless")
    cmd += ["--path", str(ENGINE), "--", f"--bus-port={BUS_PORT}"] + args
    return run(cmd, timeout=timeout)


def _free_port() -> int:
    """An unused port, so two runs of the plan can share a machine.

    Every engine this file starts binds the tag bus, and the bus port used to
    be 7411 in six places here, three more in the tools it calls and one in
    try_scene.py. Two runs therefore could not coexist: the second engine bound
    nothing, reported [NO TAG BUS], and the check that was really failing was
    never the one that got blamed. FF_BUS_PORT pins it when something outside
    needs to know the number; otherwise the OS picks one.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


BUS_PORT = int(os.environ.get("FF_BUS_PORT") or _free_port())
#: Inherited by check_protocol.py, check_force_*.py, drive_engine.py and
#: try_scene.py, each of which starts or connects to the same engine.
os.environ["FF_BUS_PORT"] = str(BUS_PORT)
os.environ["FF_BUS_URL"] = f"ws://127.0.0.1:{BUS_PORT}/tagbus"


def port_in_use(port: int | None = None) -> bool:
    port = BUS_PORT if port is None else port
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port_free(timeout: float = 20.0) -> bool:
    """The previous engine's socket outlives its process by a moment, so back-to-back
    checks must wait rather than assume."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not port_in_use():
            return True
        time.sleep(0.4)
    return False


class EngineProcess:
    """A running engine, for the checks that need something to connect to.

    Output goes to a file, never to ``subprocess.PIPE``. Godot blocks on a full
    stdout pipe that nobody is draining, and it fills that buffer *before* it
    binds the tag bus — so a piped engine starts, prints its banner, and then
    hangs forever without ever listening. Measured: with inherited stdout or a
    file the port opens in 0.25s; with a pipe it never opens at all.
    """

    def __init__(self, *args: str) -> None:
        self.args = list(args)
        self.proc: subprocess.Popen | None = None
        self.log = ROOT / ".test_plan_engine.log"
        self._handle = None

    def __enter__(self) -> "EngineProcess":
        # A leftover engine holds the port, and the new one then binds nothing
        # while looking healthy. Fail here with the real reason rather than
        # 30 seconds later with "never opened port".
        if not wait_for_port_free():
            raise RuntimeError(
                f"port {BUS_PORT} is still held after 20s — another engine is running. "
                "Close it before running the plan.")

        cmd = ([GODOT or "godot", "--headless", "--path", str(ENGINE), "--",
                f"--bus-port={BUS_PORT}"] + self.args)
        self._handle = open(self.log, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=self._handle, stderr=subprocess.STDOUT)

        # __exit__ does not run when __enter__ raises, so everything below has to
        # clean up after itself (HP-30). Without this, an engine that came up but
        # never bound the port was left running -- and it is still holding the bus
        # so the *next* check waits 20 seconds for a free port and fails too. One
        # orphan poisons the rest of the run and the reported failure is never
        # the real one.
        try:
            # Wait for the port rather than sleeping a guessed amount: on a cold
            # start the .NET runtime can take several seconds to come up.
            for _ in range(120):
                with socket.socket() as probe:
                    probe.settimeout(0.25)
                    if probe.connect_ex(("127.0.0.1", BUS_PORT)) == 0:
                        time.sleep(0.5)      # let the describe go out
                        return self
                # Ask whether it is still alive, not just whether the port is
                # shut. Without this the loop spends its full thirty seconds
                # and then blames the port for an engine that died on startup
                # -- the port is the one thing that is not wrong (HP-54).
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        f"engine exited with code {self.proc.returncode} before "
                        f"opening port {BUS_PORT}; see {self.log}")
                time.sleep(0.25)
            raise RuntimeError(f"engine never opened port {BUS_PORT} within 30s")
        except BaseException:
            # BaseException, not Exception: Ctrl-C during that minute of waiting
            # is the most likely way to get here and leaks the same process.
            self.__exit__(None, None, None)
            raise

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    def __exit__(self, *exc: object) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._handle:
            self._handle.close()


def sidecar(args: list[str], timeout: float = 60) -> tuple[int, str]:
    # `connect` talks to the engine this file started, which is not on the
    # default port unless FF_BUS_PORT said so.
    if args and args[0] == "connect" and "--port" not in args:
        args = args + ["--port", str(BUS_PORT)]
    return run([sys.executable, "-m", "factoryforge_sidecar"] + args, cwd=SIDECAR, timeout=timeout)


#: Types declared and used only inside their own file. Nested helper records and
#: the Godot entry point are the honest cases; anything else here means dead
#: code, so the list is short on purpose and every addition needs a reason.
SELF_CONTAINED_TYPES = {
    "OperableHit",   # SceneEditor's own hit record
    "PlacedPart",    # SceneEditor's own placed-part record
}


def dead_types() -> tuple[bool, str]:
    """Find every type in engine/src referenced nowhere outside its own file.

    A whole feature once shipped this way: FloatingTagBadge3D was a complete,
    working billboard label that README advertised and nothing ever constructed
    (LE-02/LE-12). Nothing in A1-A5 could see it -- dead code builds cleanly.
    """
    sources = {path: path.read_text(encoding="utf-8", errors="replace")
               for path in (ENGINE / "src").rglob("*.cs")}
    declaration = re.compile(
        r"\b(?:public|internal)\s+(?:sealed\s+|abstract\s+|static\s+|partial\s+)*"
        r"(?:class|enum|interface|record(?:\s+struct)?)\s+(\w+)")

    declared: dict[str, Path] = {}
    for path, text in sources.items():
        for name in declaration.findall(text):
            declared.setdefault(name, path)

    dead = []
    for name, home in declared.items():
        if name in SELF_CONTAINED_TYPES:
            continue
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        if not any(word.search(text) for path, text in sources.items() if path != home):
            dead.append(f"{name} ({home.name})")
    return not dead, ", ".join(sorted(dead))


# --- A. build and static ----------------------------------------------------

def section_a() -> None:
    print("\nA. Build and static")

    code, out = run(["dotnet", "build", "-v", "q", "--nologo"], cwd=ENGINE, timeout=300)
    warnings = re.findall(r"warning [A-Z]+\d+", out)
    record("A1", "engine builds with zero warnings", code == 0 and not warnings,
           "build failed" if code else (f"{len(warnings)} warnings" if warnings else ""))

    code, out = run([sys.executable, "-c", "import factoryforge_sidecar"], cwd=SIDECAR, timeout=60)
    record("A2", "sidecar package imports", code == 0, out.strip().splitlines()[-1] if code else "")

    # Temporary verification hooks have escaped into commits before.
    stray = []
    for path in (ENGINE / "src").rglob("*.cs"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "TempClickProbe" in text or "TempUiProbe" in text or "TempInspectorProbe" in text:
            stray.append(path.name)
        if re.search(r"\b(TODO|FIXME|HACK)\b", text):
            stray.append(f"{path.name}:marker")
    record("A3", "no temporary probes or TODO markers left in engine/src",
           not stray, ", ".join(sorted(set(stray))))

    # A4/A5: the C# and Python tag models, and the wire messages between them,
    # must agree with docs/tag-bus.md and with each other. See FF-29. The
    # Python side of the parity fixture runs as part of B1 (tests/test_tag_parity.py);
    # this is the C# side plus the wire-level conformance check.
    code, out = engine(["--self-test=parity", "--duration=20"], timeout=90)
    fails = [line.strip() for line in out.splitlines() if "FAIL" in line]
    passed = code == 0 and "self-test parity: PASS" in out
    record("A4", "C# tag model matches the shared parity fixture", passed,
           "; ".join(fails[:3]) if fails else ("no PASS line" if not passed else ""))

    with EngineProcess("--duration=30"):
        code, out = run([sys.executable, str(ROOT / "tools" / "check_protocol.py")], timeout=30)
    record("A5", "hello/describe/update carry exactly the fields docs/tag-bus.md names",
           code == 0 and "RESULT OK" in out,
           out.strip().splitlines()[-1] if out.strip() else "no output")

    record("A6", "no type in engine/src is referenced nowhere outside its own file",
           *dead_types())


# --- B. python suite --------------------------------------------------------

def section_b() -> None:
    print("\nB. Python unit and integration")
    code, out = run([sys.executable, "-m", "pytest", "-q", "tests"], timeout=600)
    match = re.search(r"(\d+) passed", out)
    failed = re.search(r"(\d+) failed", out)
    # Skips too. A skip is neither a pass nor a failure, so counting only the
    # first two makes a test that quietly stops running anywhere invisible in
    # this line -- which is exactly how CI spent weeks measuring FakeUtil
    # against itself, because snap7 was not installed and
    # test_the_fake_agrees_with_real_snap7 hit its importorskip. The count was
    # accurate and incomplete, and that is how a suite shrinks (HP-56).
    skipped = re.search(r"(\d+) skipped", out)
    # Name the tests that failed, not just how many. A bare count sends you back
    # to run pytest yourself to find out what broke -- and if it was a flake,
    # the second run tells you nothing at all.
    names = re.findall(r"^(?:FAILED\s+)?(tests[/\][\w./\]+::[\w\[\]-]+)", out, re.M)
    detail = (f"{match.group(1) if match else '?'} passed"
              + (f", {failed.group(1)} FAILED" if failed else "")
              + (f", {skipped.group(1)} skipped" if skipped else ""))
    if names:
        detail += ": " + ", ".join(dict.fromkeys(names))
    record("B1", "pytest suite", code == 0, detail)


# --- C/D. engine self-tests -------------------------------------------------

def _self_test(ident: str, name: str, which: str, headless: bool = True) -> None:
    code, out = engine([f"--self-test={which}", "--duration=40"], headless=headless, timeout=180)
    fails = [line.strip() for line in out.splitlines() if "FAIL" in line]
    passed = code == 0 and f"self-test {which}: PASS" in out
    record(ident, name, passed, "; ".join(fails[:3]) if fails else ("no PASS line" if not passed else ""))


def section_c() -> None:
    print("\nC. Engine self-tests (headless)")
    _self_test("C1", "panel buttons: one-scan pulse, latched E-stop, cap picking", "buttons")
    _self_test("C2", "rename and I/O export", "io")
    _self_test("C3", "scene save/load round-trip, every part type", "scene")
    _self_test("C4", "every shipped start-screen template loads and registers I/O", "templates")
    # The F5 dialog's status label had no autowrap, so a long auto-detect
    # message stretched the modal to 1594px of a 1600px screen and clipped its
    # own buttons off the frame. Nothing in A-E could see it: no tag is wrong
    # when a dialog is unusable.
    _self_test("C5", "F5 driver modal still fits on screen with a long status message", "layout")
    # The Tag Inspector's Force button only ever handled bit tags; pressing it
    # on an int or float tag did nothing, silently, and the tank template (all
    # float I/O) could not be operated by hand at all.
    _self_test("C6", "Tag Inspector forces int and float tags, not just bits", "force")
    # The nine parts added in CP-01..CP-09, asserted by effect rather than by
    # existence: a ramping drive whose actual lags its reference, a diverter
    # that reports neither limit mid-sweep and freezes there when seized, a
    # gantry that will not claim a hold it does not have, a scanner whose read
    # pulse does not repeat for the same carton, a needle that moves, and a
    # heater with a real time constant whose failed element cools while its
    # command still reads 100 %. Given 90s: the thermal ramp is hand-turned
    # ticks rather than wall clock, but the run still has to build the scene.

    # --scene= used to only work windowed; BuildHeadlessPhysicsParts always
    # called RegisterDefaultSceneParts regardless of what was asked for.
    code, out = engine(["--scene=res://templates/tank_level_control.json", "--print-tags"], timeout=30)
    match = re.search(r'\{"t":"describe".*\}', out)
    payload = json.loads(match.group(0)) if match else {}
    record("C7", "--scene= loads a template headless and --print-tags dumps its I/O",
           code == 0 and payload.get("scene") == "tank-level-control" and len(payload.get("tags", [])) == 15,   # 13, plus the setpoint pot (OP-01)
                                                 # and the tank's valve-fault contact (FI-01)
           f"scene={payload.get('scene')!r} tags={len(payload.get('tags', []))}" if payload else "no describe line")

    # A hybrid of the fixed regression scene and a template is worse than
    # refusing outright.
    code, out = engine(["--deterministic", "--scene=res://templates/tank_level_control.json"], timeout=20)
    record("C8", "--deterministic --scene= is rejected, not silently hybridized",
           code != 0 and "cannot combine" in out, f"exit={code}")

    _self_test("C9", "DemoDriver picks the right profile per scene, refuses honestly otherwise", "demo")

    _self_test("C10", "start-stop-station profile: momentary buttons, E-stop latch, reset", "startstop")
    _self_test("C11", "tank-level-control profile: controller settles within ±5% of setpoint", "tank")
    _self_test("C12", "light-curtain-sorting profile: both tall and short cartons routed correctly", "lightcurtain")
    _self_test("C13", "roller-line-weighing profile: outfeed counts, metal detected", "roller")
    _self_test("C14", "Demo's refusal reaches the UI, not just the console", "refusal")
    _self_test("C15", "part property panel drives live I/O (bit/int/float x output/input)", "proppanel")
    # Run mode's click only ever pressed a Control Panel cap; every other part
    # was dead in Run mode however loud the toolbar's Operate label claimed
    # otherwise.
    _self_test("C16", "Run mode's click operates the part it lands on, not just the panel", "operate")
    # Run mode used to be silent about whether anything on screen would
    # respond to a click at all.
    _self_test("C17", "entering Run mode says what's clickable, or says plainly nothing is", "modehint")
    # "Try this scene" (UX-31) resolves the wrong template, or claims to run
    # one on a custom scene that has none.
    _self_test("C18", "\"Try this scene\" finds the right exercise, refuses honestly otherwise", "tryscene")
    # Nothing else catches a template edit that quietly renames or retypes a
    # tag out from under a mapping file.
    _self_test("C19", "every shipped scene's tag set (id/type/kind) matches engine/fixtures/scene_tag_sets.json", "scenes")
    # =click and =buttons each cover one half of the Edit/Run contract; this
    # is the only check that a click means one thing in one mode and nothing
    # in the other, as a pair.
    _self_test("C20", "the Edit/Run contract holds as a pair: select only in Edit, operate only in Run", "modes")
    # C15 covers the property panel's I/O half. Its *settings* half was
    # uncovered, and that is where "Curtain Height" sat: a slider moving a value
    # LightArray only read while building itself, so it moved and nothing
    # happened (LE-01). This drives every settings row and asserts a named
    # observable per control, and fails on a row it does not know how to drive.
    _self_test("C21", "every settings control in the part panel reaches the simulation", "partsettings")
    # F5's only job is starting the sidecar, and when the search misses it
    # falls back to "command copied, run it yourself" -- which looks like a
    # feature rather than a failure. In a packaged build that fallback used to
    # be the only path (UX-04).
    _self_test("C22", "the engine can find a sidecar to launch, and knows how to start it", "sidecar")
    # The panel's pot is the first control a mouse drags rather than clicks,
    # and the first tag written in both directions -- the knob drives it, a
    # forced tag drives the knob. Either half can be missing while the panel
    # still looks right in a screenshot (OP-01, OP-02).
    _self_test("C23", "the panel's setpoint pot turns, publishes, and follows a forced tag", "setpoint")
    # Dragging a placed part is what everyone tries first and what the editor
    # did not have: moving one needed the M key, which nothing on screen
    # mentioned (OP-08). One drag has to be one undoable step, and a press
    # that never travels has to stay a plain click.
    _self_test("C24", "a placed part can be dragged to a new cell, as one undoable step", "drag")
    # Until drives could fail, every actuator in the library did exactly what
    # it was told, so a command and reality could never disagree -- and an
    # interlock exists precisely because the plant does not always obey. The
    # assertion that matters is not "the belt stopped" but "the belt stopped
    # while the command was still on" (FI-01).
    _self_test("C25", "a drive can fail: it stops while still commanded, and a jam freezes mid-stroke", "fault")
    # The seven parts added in CP-01..CP-07, asserted by effect rather than by
    # existence: a ramping drive whose actual lags its reference, a diverter
    # that reports neither limit mid-sweep and freezes there when seized, a
    # gantry that will not claim a hold it does not have, a scanner whose read
    # pulse does not repeat for the same carton, a needle that moves, and a
    # heater with a real time constant whose failed element cools while its
    # command still reads 100 %.
    _self_test("C26", "the nine CP-01..CP-09 parts do what their tags claim", "newparts")
    # The five parts added in LP-01..LP-05. Two of their claims are statements
    # about the solver rather than about the dispatch -- a blade that stops a
    # carton on a running belt, and a deck that carries its load round by
    # friction -- so that test runs those on real engine ticks and hand-turns
    # the rest.
    _self_test("C27", "the five LP-01..LP-05 parts do what their tags claim", "lineparts")
    # The loop a person is in while building a line, asserted as a loop: a
    # second click after a placement makes a second part, Escape stops that
    # happening, two duplicates land in two cells rather than one, and a nudge
    # is one undo step.
    _self_test("C28", "the build loop: repeat placement, duplicate walks, arrow nudge", "buildflow")

    # The six parts added after HP-34, asserted by what they do rather than by
    # what tags they declare: a contactor's aux contact lagging its coil by a
    # scan, an overload that stays out, a permissive that closes and starts
    # nothing, a mute that expires, a cylinder mid-stroke making neither reed.
    _self_test("C29", "the six control parts do what they do, not what they declare", "controlparts")

    # Keystrokes pushed through the viewport, because "does this key stop here?"
    # is a question only the viewport can answer -- Ctrl+C used to copy AND flip
    # the camera, and the arrow-key nudge was inverted in every view.
    _self_test("C30", "one keystroke does one thing, and the nudge follows the screen", "editorkeys")

    # Deliberately NOT in tools/packaging/check_release.py: this scans
    # engine/src/Editor/ for part type names, and an exported build has no .cs
    # files to scan, so it reports SKIPPED there. A release-gate entry that
    # always skips is a pass that proves nothing -- the exact shape this plan
    # keeps finding. It belongs here, where the source exists.
    _self_test("C31", "the editor still names no part type (HP-34 has not decayed)", "partcontract")


def section_d(enabled: bool) -> None:
    print("\nD. Engine self-tests (need a display)")
    if not enabled:
        record("D1", "whole click path, synthesized mouse event to tag", True,
               "not run (pass --gui)", skipped=True)
        record("D2", "whole drag path, synthesized press/motion/release to a moved part", True,
               "not run (pass --gui)", skipped=True)
        return
    _self_test("D1", "whole click path, synthesized mouse event to tag", "click", headless=False)
    # C24's headless half enters at the ray seam, which skips the two things
    # that can kill dragging outright while every headless assertion passes:
    # the pixel threshold, and selecting the part under the *press* rather
    # than under wherever the cursor is when the event is processed (OP-08).
    _self_test("D2", "whole drag path, synthesized press/motion/release to a moved part",
               "dragpath", headless=False)


# --- E. determinism and the regression contract -----------------------------

def _driven_run(deterministic: bool) -> tuple[int, int] | None:
    """Start an engine and drive it with the real control logic, returning the
    sorted counts. Undriven runs are useless as a determinism check — nothing
    moves, so every run trivially agrees on zero."""
    args = ["--duration=70"] + (["--deterministic"] if deterministic else [])
    with EngineProcess(*args):
        code, out = run([sys.executable, str(ROOT / "tools" / "drive_engine.py")], timeout=150)
    got = re.search(r"RESULT tall=(\d+) short=(\d+)", out)
    return (int(got.group(1)), int(got.group(2))) if got else None


def section_e() -> None:
    print("\nE. Determinism and the regression contract")

    first = _driven_run(deterministic=True)
    record("E1", "drive_engine.py sorts 5 tall and 5 short", first == (5, 5),
           f"got {first}" if first else "no RESULT line")

    second = _driven_run(deterministic=True)
    record("E2", "a second driven run agrees exactly, and did real work",
           first is not None and first == second and sum(first) > 0,
           f"{first} vs {second}")

    code, slow = engine(["--deterministic", "--duration=8", "--time-scale=1"], timeout=90)
    code, fast = engine(["--deterministic", "--duration=8", "--time-scale=4"], timeout=90)
    slow_ticks = re.search(r"tick=(\d+)", slow)
    fast_ticks = re.search(r"tick=(\d+)", fast)
    ok = bool(slow_ticks and fast_ticks) and int(fast_ticks.group(1)) > int(slow_ticks.group(1)) * 1.5
    record("E3", "time-scale=4 advances the sim faster than 1x", ok,
           f"1x={slow_ticks.group(1) if slow_ticks else '?'} "
           f"4x={fast_ticks.group(1) if fast_ticks else '?'} ticks")


# --- F. the engine/sidecar seam ---------------------------------------------

def section_f() -> None:
    print("\nF. Engine and sidecar seam")

    with EngineProcess("--duration=90"):
        code, out = sidecar(["connect", "--driver", "mock", "--duration", "6"], timeout=90)
        # Deliberately not matching the em dash between them: what matters is
        # that it found the scene and its tags, not the punctuation.
        match = re.search(r"connected to scene '([^']+)'.*?(\d+) tags", out, re.S)
        record("F1", "connect attaches a driver to a running engine",
               bool(match) and match.group(1) != "None" and int(match.group(2)) > 0,
               f"scene={match.group(1)} tags={match.group(2)}" if match else "no connect line")

        # Its own free port, not 502. Proving the bus port was shareable turned
        # up two more fixed ones underneath it: two concurrent runs of this
        # section failed here and in F3 on "only one usage of each socket
        # address", while every other check passed. A driver's listening port
        # is as much a shared resource as the bus is.
        code, out = sidecar(["connect", "--driver", "modbus-tcp", "--duration", "6",
                             "-o", "port", str(_free_port())], timeout=90)
        record("F2", "modbus-tcp server starts and reports its address map",
               "Modbus address map" in out, "" if "Modbus address map" in out else out.strip()[-120:])

        code, out = sidecar(["connect", "--driver", "opcua-server", "--duration", "8",
                             "-o", "endpoint",
                             f"opc.tcp://127.0.0.1:{_free_port()}/factoryforge/"],
                            timeout=120)
        record("F3", "opcua-server starts and reports an endpoint",
               "OPC UA server:" in out, "" if "OPC UA server:" in out else out.strip()[-120:])

    # No engine running now.
    started = time.perf_counter()
    code, out = sidecar(["connect", "--driver", "mock", "--timeout", "3"], timeout=60)
    elapsed = time.perf_counter() - started
    record("F4", "connect with no engine fails fast and says why",
           code != 0 and "no engine listening" in out and elapsed < 25,
           f"exit={code} after {elapsed:.1f}s")

    # The physics scene, driven by real control logic. This is the check that
    # proves emitter, sensors, pusher, chute and removers work together under
    # Jolt — the deterministic scene in E1 simulates all of that in code, so it
    # would pass even if every rigid body were broken.
    #
    # `--driver mock` alone would not do: the mock is a passthrough with no
    # logic of its own, so an engine driven by it correctly does nothing.
    # Counts are not asserted exactly — Jolt does not promise reproducible
    # numbers, which is the entire reason --deterministic exists.
    counts = _driven_run(deterministic=False)
    record("F5", "the rigid-body scene sorts cartons when driven for real",
           counts is not None and sum(counts) > 0, f"got {counts}")


# --- G. robustness ----------------------------------------------------------

def section_g() -> None:
    print("\nG. Robustness")

    # G1 used to write this file and then launch the engine with no `--scene=`
    # pointing at it, so the corrupt file was never opened and the check asserted
    # nothing but that an ordinary startup starts (HP-08). It passed, and it
    # would have passed just as well with the loader destroying the open scene
    # before parsing -- which is exactly what it was doing.
    #
    # Three claims now, because "does not stop startup" was never the whole of
    # it: the engine comes up, it says which file it refused and why, and it does
    # *not* claim to have loaded it.
    bad = USER_DIR / "testplan_corrupt.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not json at all ]", encoding="utf-8")
    code, out = engine(["--scene=user://testplan_corrupt.json", "--duration=6"], timeout=90)
    refused = "Could not open scene" in out and "testplan_corrupt.json" in out
    record("G1", "a corrupt scene file is refused by name, and startup survives it",
           code == 0 and "engine ready" in out and refused
           and "Loaded scene from user://testplan_corrupt.json" not in out,
           f"exit={code}; refused={refused}")

    code, out = engine(["--duration=6", "--nonsense-flag=1"], timeout=90)
    record("G2", "an unknown CLI flag is ignored rather than fatal",
           code == 0 and "engine ready" in out, f"exit={code}")

    # G3 is covered inside --self-test=scene, which plants an unknown property
    # key; recorded here so the plan's table matches what actually ran.
    code, out = engine(["--self-test=scene", "--duration=40"], timeout=180)
    record("G3", "a scene with unknown property keys still loads",
           code == 0 and "self-test scene: PASS" in out)

    # A second instance cannot bind the bus. It must say so rather than
    # reporting "ready" and leaving someone to debug their PLC.
    with EngineProcess("--duration=40"):
        code, out = engine(["--duration=6"], timeout=90)
    record("G4", "a second engine says its tag bus is dead instead of claiming ready",
           "NO TAG BUS" in out, "still reports a healthy start" if "NO TAG BUS" not in out else "")

    # G5: kill the engine out from under a connected sidecar and confirm it
    # notices and starts retrying, rather than serving the last-known values
    # forever as if the line were still running. See FF-03.
    g5_log = ROOT / ".test_plan_sidecar_g5.log"
    with EngineProcess("--duration=30") as eng:
        handle = open(g5_log, "w", encoding="utf-8", errors="replace")
        sidecar_proc = subprocess.Popen(
            # --port explicitly: this is the one sidecar launch that does not go
            # through sidecar(), so it does not inherit that helper's injection
            # and would otherwise look for the engine on the default port while
            # EngineProcess put it on a free one. That failure reads as "the
            # sidecar did not notice the engine die", which is this check's real
            # subject -- so it fails for a reason that looks exactly like the
            # thing being tested.
            [sys.executable, "-m", "factoryforge_sidecar", "connect", "--driver", "mock",
             "--duration", "20", "--port", str(BUS_PORT)],
            cwd=SIDECAR, stdout=handle, stderr=subprocess.STDOUT)
        # Wait for evidence that it connected, not a guessed two seconds. The
        # printer loop only runs once the describe has arrived, so an "OUT "
        # line is proof. Under load -- another engine building, a parallel
        # plan -- two seconds was not always enough, and the sidecar was then
        # killed before it had connected at all: nothing to lose the
        # connection, nothing logged, and a FAIL reading "did not notice the
        # engine die". That is this check's own subject, so the failure
        # impersonated the bug (gotcha 2: wait on events, never on a sleep).
        connected = False
        for _ in range(100):                      # 20s ceiling
            handle.flush()
            if "OUT " in g5_log.read_text(encoding="utf-8", errors="replace"):
                connected = True
                break
            if sidecar_proc.poll() is not None:   # it died; stop waiting on it
                break
            time.sleep(0.2)
        eng.proc.terminate()
        try:
            eng.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            eng.proc.kill()
        time.sleep(3.0)   # give the sidecar a moment to notice and log it
        sidecar_proc.terminate()
        try:
            sidecar_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sidecar_proc.kill()
        handle.close()
    g5_out = g5_log.read_text(encoding="utf-8", errors="replace") if g5_log.exists() else ""
    record("G5", "the sidecar notices when the engine dies mid-run and retries",
           connected and "connection lost" in g5_out and "retrying" in g5_out,
           "" if connected and "connection lost" in g5_out
           else ("the sidecar never connected, so there was nothing to lose"
                 if not connected else g5_out.strip()[-160:]))

    # G6: forcing an input tag while paused must still reach the bus. Pausing
    # used to zero the fixed-timestep accumulator that gated SendUpdates(), so
    # a forced sensor sat in the tag table and never left the engine until you
    # un-paused it — exactly the workflow pause exists for. See FF-14.
    with EngineProcess("--duration=30", "--paused"):
        code, out = run([sys.executable, str(ROOT / "tools" / "check_force_while_paused.py")], timeout=30)
    record("G6", "forcing a tag while paused still reaches the bus",
           code == 0 and "RESULT update received" in out,
           out.strip().splitlines()[-1] if out.strip() else "no output")

    # G7: the Tag Inspector's Force button used to only handle bit tags
    # (§2.8) — this checks the wire protocol itself, independent of any UI,
    # the way G6 already does for bits (UX-45).
    with EngineProcess("--duration=30", "--scene=res://templates/light_curtain_sorting.json"):
        code, out = run([sys.executable, str(ROOT / "tools" / "check_force_types.py")], timeout=30)
    record("G7", "forcing an int tag and a float tag both reach the bus",
           code == 0 and "RESULT OK" in out,
           out.strip().splitlines()[-1] if out.strip() else "no output")


# --- H. Every scene exercise, driven end to end -----------------------------

def section_h() -> None:
    print("\nH. Scene exercises (tools/try_scene.py)")

    # The spike behind UX-10 proved templates simulate headless with no
    # renderer, so this needs no display -- unlike D, which is gated behind
    # --gui.
    manifest = json.loads((ENGINE / "templates" / "manifest.json").read_text(encoding="utf-8"))
    for i, entry in enumerate(manifest, start=1):
        scene_id = entry["id"]
        # try_scene.py tears its own engine down before exiting, but the OS can
        # take a moment to release the port after that -- the same gap
        # EngineProcess.__enter__ waits out between checks.
        if not wait_for_port_free():
            record(f"H{i}", f"{scene_id}: try_scene.py drives it to a real PASS", False,
                   f"port {BUS_PORT} still held from a previous check")
            continue
        # 210s, not 90: the longest exercises in the set are long because the
        # plant is, not because the harness is slow. The heat-treat run holds a
        # first-order plant steady twice -- once with the integral term off to
        # measure the offset, once with it on to show it close -- and the
        # accumulation buffer spends most of its run waiting for released
        # cartons to travel two metres to the counter, twice.
        code, out = run([sys.executable, str(ROOT / "tools" / "try_scene.py"),
                         "--scene", scene_id], timeout=210)
        # try_scene.py's own PASS/FAIL line carries an em dash, which a
        # subprocess piped on Windows can mangle in transit -- exit code
        # alone is the authoritative pass/fail signal (that convention is
        # the whole point of UX-21's "exit 0/1" contract), and the RESULT
        # line this pulls for detail is plain ASCII.
        match = re.search(r"^RESULT .+$", out, re.M)
        detail = match.group(0)[len("RESULT "):] if match else (out.strip().splitlines()[-1] if out.strip() else "no output")
        record(f"H{i}", f"{scene_id}: try_scene.py drives it to a real PASS", code == 0, detail)


SECTIONS = {
    "A": section_a, "B": section_b, "C": section_c,
    "E": section_e, "F": section_f, "G": section_g, "H": section_h,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", action="store_true", help="also run checks needing a display")
    parser.add_argument("--only", help="comma-separated section letters, e.g. C,E")
    args = parser.parse_args()

    if GODOT is None:
        print("Godot not found. Set $GODOT to the .NET build's executable.", file=sys.stderr)
        return 2

    wanted = [s.strip().upper() for s in args.only.split(",")] if args.only else None
    print(f"FactoryForge test plan\n  godot:  {GODOT}\n  python: {sys.executable}")

    started = time.perf_counter()
    for letter, fn in SECTIONS.items():
        if wanted and letter not in wanted:
            continue
        fn()
    if not wanted or "D" in wanted:
        section_d(args.gui)

    failed = [r for r in RESULTS if not r.ok and not r.skipped]
    skipped = [r for r in RESULTS if r.skipped]
    print(f"\n{'=' * 62}")
    print(f"{len(RESULTS) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped "
          f"in {time.perf_counter() - started:.0f}s")
    for r in failed:
        print(f"  FAILED  {r.ident} {r.name}" + (f"  — {r.detail}" if r.detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
