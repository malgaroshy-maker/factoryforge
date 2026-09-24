#!/usr/bin/env python3
"""Run every headless self-test against a built release, not a checkout.

    python tools/packaging/check_release.py --target windows

This is the gate a release has to pass. The distinction from
`tools/test_plan.py` matters: that runs the same self-tests against the source
tree, and a checkout can pass every one of them while the exported binary fails.
Two of these read checked-in fixtures, which used to live outside res:// at a
path that does not exist beside a binary -- and even in the right place cannot
be read with System.IO, because an export packs them inside the .pck where only
Godot's own FileAccess reaches.

Also checks the two things that make a release a release rather than a folder of
files: the .NET assemblies are present (an export missing them still produces a
binary and exits 0), and the frozen sidecar is where the engine looks for it.

And it runs the frozen sidecar the way someone with only the zip would (IP-08):
`grade --list` must offer every scene the checkout's grader has, one scene is
graded with a built-in controller that must PASS and one that must FAIL, and
`demo` must run a scene. Each from a scratch directory, with no
`FACTORYFORGE_TEMPLATES` and no `PYTHONPATH`, so the only templates and the
only modules it can find are the ones inside it. Listing scenes alone would
not do: a freeze that collected the scene modules but not the templates, or
the other way round, fails only once something is actually marked.

    python tools/packaging/check_release.py --target windows --sidecar-only

checks the frozen sidecar and nothing else, for iterating on the freeze. It is
not a release verdict and says so.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# This script's own folder, put on the path explicitly: test_plan.py loads the
# file by location, and then nothing else puts it there.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
DIST = ROOT / "dist"

BINARIES = {"windows": "FactoryForge.exe", "linux": "FactoryForge.x86_64"}
SIDECARS = {"windows": "factoryforge-sidecar.exe", "linux": "factoryforge-sidecar"}

#: Every self-test that runs without a display. `click` is excluded on purpose
#: -- it synthesizes a mouse event and needs a window.
SELF_TESTS = [
    "buttons", "templates", "scene", "io", "parity", "layout", "force", "demo",
    "startstop", "tank", "lightcurtain", "roller", "refusal", "proppanel",
    "operate", "modehint", "tryscene", "scenes", "modes", "partsettings",
    "sidecar", "setpoint", "drag", "fault", "newparts", "lineparts",
    "buildflow",
    # Behaviour of the six control parts, and the editor key contract.
    # `partcontract` is deliberately absent: it scans .cs sources, which a
    # .pck does not contain, so here it would skip forever and look green.
    "controlparts", "editorkeys", "handlingparts",
    # Raw analog (IP-16): every analog input at 0/50/100 % in each signal
    # mode, the wire break, and the setting across a save and a load.
    "analog",
    # The five industrial parts of IP-17, asserted by their effects: a lever
    # pushed by a real carton, a valve's feedback that does not follow when
    # stuck, a star-delta short that trips and latches, raw counts that track
    # a receiver's pressure, a servo that holds on a fault until acknowledged.
    "industrialparts",
]


def run_self_test(binary: Path, name: str, timeout: float = 180) -> tuple[bool, str]:
    result = subprocess.run(
        [str(binary), "--headless", "--", f"--self-test={name}", "--duration=90"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    output = (result.stdout or "") + (result.stderr or "")
    passed = result.returncode == 0 and f"self-test {name}: PASS" in output
    detail = ""
    if not passed:
        fails = [line.strip() for line in output.splitlines() if "FAIL" in line]
        detail = fails[0] if fails else f"exit={result.returncode}, no PASS line"
    return passed, detail


#: Drivers a release is expected to carry. The frozen sidecar contains whatever
#: was importable when PyInstaller ran, so a missing extra at build time is a
#: driver the user finds missing at connect time -- and nothing else notices.
EXPECTED_DRIVERS = ["mock", "modbus-tcp", "mqtt", "opcua-client", "opcua-server", "s7-snap7"]


def check_drivers(sidecar: Path, target: str) -> list[str]:
    """Ask the frozen sidecar which drivers it can actually run.

    `connect --help` is not the question: its driver list is a hardcoded string
    and prints identically on a build with no snap7 in it. Nor is the registry
    -- every protocol driver guards its own import and registers regardless, so
    it can explain itself at connect time. `drivers` reports the HAS_* flag each
    module sets, which is the only one of the three that knows.
    """
    try:
        result = subprocess.run([str(sidecar), "drivers"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"the frozen sidecar would not run: {exc}"]

    listed = (result.stdout or "") + (result.stderr or "")
    if "OK" not in listed:
        return [f"`{sidecar.name} drivers` reported nothing usable: {listed.strip()[:200]}"]

    usable = {line.split()[-1] for line in listed.splitlines() if line.startswith("OK")}
    expected = list(EXPECTED_DRIVERS)
    # PLCSIM Advanced reaches its .NET API through pythonnet, which is
    # Windows-only by construction, so its absence elsewhere is not a fault.
    if target == "windows":
        expected.append("plcsim-advanced")

    missing = [name for name in expected if name not in usable]
    return ([f"the frozen sidecar cannot run: {', '.join(missing)} "
             f"-- build with pip install -e \"sidecar[opcua,siemens,plcsim,mqtt]\""]
            if missing else [])


#: The graded run the gate makes against the frozen sidecar. One scene, two
#: built-in controllers: `good` must PASS, and `blind` -- a stopwatch program
#: that never reads a sensor -- must FAIL on the sorting checks, not on the
#: run. Lockstep and a fixed seed, so the verdict cannot depend on how busy the
#: runner is.
GATE_SCENE = "sorting-by-height"
GATE_GOOD = "good"
GATE_WRONG = "blind"
GATE_SEED = "1"

SCENES_DIR = ROOT / "sidecar" / "factoryforge_sidecar" / "grading" / "scenes"


def expected_scenes() -> set[str]:
    """Every scene the checkout's grader can mark, read from the scene
    modules' `SCENE = "..."` lines rather than by importing them, so the gate
    needs nothing installed to know what the frozen grader should offer."""
    found = set()
    for path in SCENES_DIR.glob("*.py"):
        if path.name.startswith("_"):
            continue
        match = re.search(r'^SCENE\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"),
                          re.MULTILINE)
        if match:
            found.add(match.group(1))
    return found


def _sidecar_env() -> dict[str, str]:
    """The caller's environment, minus everything that could let the frozen
    sidecar find templates or modules anywhere but inside itself."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("FACTORYFORGE_TEMPLATES", "PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_sidecar(sidecar: Path, args: list[str], cwd: Path,
                 timeout: float = 240) -> tuple[int, str]:
    # subprocess.run drains both pipes as it goes (AGENTS.md gotcha 15).
    result = subprocess.run([str(sidecar), *args], cwd=cwd, env=_sidecar_env(),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _last_line(text: str) -> str:
    # The bootloader's own "[PYI-...] Failed to execute script" follows every
    # uncaught exception; the exception itself is the line worth reporting.
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.startswith("[PYI-")]
    return lines[-1][:400] if lines else "(no output)"


def check_grader(sidecar: Path) -> list[str]:
    """Grade with the frozen sidecar alone. Returns the problems, and prints
    each check as it goes."""
    problems: list[str] = []

    def fail(message: str) -> None:
        problems.append(message)
        print(f"  [FAIL] {message}", file=sys.stderr, flush=True)

    with tempfile.TemporaryDirectory(prefix="ff-gate-") as scratch:
        cwd = Path(scratch)

        # 1. Every scene the checkout marks is offered.
        try:
            code, out = _run_sidecar(sidecar, ["grade", "--list"], cwd)
        except (subprocess.TimeoutExpired, OSError) as exc:
            fail(f"grade --list did not run: {exc}")
            return problems
        # --list prints each scene id flush left, its details indented.
        listed = {line.strip() for line in out.splitlines()
                  if line.strip() and not line[:1].isspace() and " " not in line.strip()}
        expected = expected_scenes()
        if code != 0:
            fail(f"grade --list exited {code}: {_last_line(out)}")
        elif not expected:
            fail(f"found no scene modules in {SCENES_DIR} to compare against")
        elif listed != expected:
            fail(f"grade --list offers {len(listed)} scene(s), the checkout has "
                 f"{len(expected)}; missing {sorted(expected - listed) or 'none'}, "
                 f"extra {sorted(listed - expected) or 'none'}")
        else:
            print(f"  [ok]   grade --list: all {len(expected)} graded scenes", flush=True)

        # 2. A graded run each way, read back from the JSON report.
        for reference, want_code, want in ((GATE_GOOD, 0, "PASS"), (GATE_WRONG, 1, "FAIL")):
            report_path = cwd / f"{reference}.json"
            args = ["grade", "--scene", GATE_SCENE, "--reference", reference,
                    "--lockstep", "--seed", GATE_SEED, "--quiet",
                    "--json", str(report_path)]
            label = f"grade --scene {GATE_SCENE} --reference {reference}"
            try:
                code, out = _run_sidecar(sidecar, args, cwd)
            except (subprocess.TimeoutExpired, OSError) as exc:
                fail(f"{label} did not finish: {exc}")
                continue
            if code != want_code:
                fail(f"{label} exited {code}, expected {want_code} ({want}): "
                     f"{_last_line(out)}")
                continue
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                fail(f"{label} wrote no readable JSON report: {exc}")
                continue
            failed = [c["id"] for c in report.get("checks", []) if not c.get("ok")]
            if report.get("verdict") != want or report.get("exit_code") != want_code:
                fail(f"{label}: the report says {report.get('verdict')} "
                     f"(exit_code {report.get('exit_code')}), expected {want}")
                continue
            if want == "FAIL":
                # Wrong for the scene's own reason -- not because the run broke.
                broken = [c for c in failed if c.startswith(("controller.", "integrity."))]
                if broken or not any(c.startswith("sort.") for c in failed):
                    fail(f"{label} failed for the wrong reason: {', '.join(failed)}")
                    continue
            print(f"  [ok]   {label}: {want}, exit {code}"
                  + (f" ({', '.join(failed)})" if failed else ""), flush=True)

        # 3. `demo` runs its scene. Until IP-08 it imported that scene from a
        # path that exists only in a checkout, and the frozen build shipped it
        # failing on its first line.
        try:
            code, out = _run_sidecar(sidecar, ["demo", "--driver", "mock", "--port", "0",
                                               "--duration", "3"], cwd, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            fail(f"demo did not finish: {exc}")
            return problems
        if code != 0 or "engine listening on ws://" not in out or "t=" not in out:
            fail(f"demo --driver mock exited {code}: {_last_line(out)}")
        elif "Traceback" in out:
            fail(f"demo --driver mock exited 0 but printed a traceback: {_last_line(out)}")
        else:
            print("  [ok]   demo --driver mock: the scene ran and shut down", flush=True)
    return problems


def release_contents(target: str) -> tuple[str, dict[str, bytes]]:
    """What the release holds, `{path inside the extracted folder: bytes}`,
    read from the archive a user downloads when there is one -- that is what
    ships -- and from the staged folder when the build skipped the archive.
    Only text is read; every other file maps to b""."""
    archive = DIST / f"FactoryForge-{target}.zip"
    files: dict[str, bytes] = {}
    if archive.is_file():
        prefix = f"{target}/"
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.endswith("/") or not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
                files[rel] = z.read(name) if release_text.is_text(rel) else b""
        return f"{archive.name}", files
    staging = DIST / target
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            rel = path.relative_to(staging).as_posix()
            files[rel] = path.read_bytes() if release_text.is_text(rel) else b""
    return f"{staging} (no archive: built with --skip-archive?)", files


def check_release_text(target: str, show: int = 40) -> list[str]:
    """IP-36 and IP-38 against what ships: no command the extracted folder
    cannot run, outside a marked from-source region, and no relative link to
    a file the folder does not have. Returns one problem per check that
    failed; prints every offending file:line (up to *show* of each)."""
    where, files = release_contents(target)
    if not files:
        message = f"nothing to read in {where}"
        print(f"  [FAIL] {message}", file=sys.stderr)
        return [message]
    texts: dict[str, str] = {}
    undecodable = []
    for rel, data in files.items():
        if release_text.is_text(rel):
            try:
                texts[rel] = data.decode("utf-8")
            except UnicodeDecodeError:
                undecodable.append(rel)
    problems: list[str] = []

    commands = [f"{rel}:{line}: {message}"
                for rel in sorted(texts)
                for line, message in release_text.command_problems(rel, texts[rel])]
    commands += [f"{rel}: not UTF-8, so it could not be checked" for rel in undecodable]
    if commands:
        problems.append(f"{len(commands)} line(s) of shipped text tell the reader to run "
                        f"something the release cannot, outside a from-source region")
        print(f"  [FAIL] {problems[-1]}:", file=sys.stderr)
        for line in commands[:show]:
            print(f"           {line}", file=sys.stderr)
        if len(commands) > show:
            print(f"           ... and {len(commands) - show} more", file=sys.stderr)
    else:
        print(f"  [ok]   every command in {len(texts)} shipped text files runs from the "
              f"release, or is marked from-source", flush=True)

    dead = release_text.dead_links(
        {rel: text for rel, text in texts.items() if rel.endswith(".md")}, set(files))
    linked = sum(len(release_text.relative_links(rel, text))
                 for rel, text in texts.items() if rel.endswith(".md"))
    if dead:
        problems.append(f"{len(dead)} relative link(s) in shipped pages lead nowhere "
                        f"in the release")
        print(f"  [FAIL] {problems[-1]}:", file=sys.stderr)
        for line in dead[:show]:
            print(f"           {line}", file=sys.stderr)
        if len(dead) > show:
            print(f"           ... and {len(dead) - show} more", file=sys.stderr)
    elif not linked:
        # A link checker that found no links has checked nothing (gotcha 16).
        problems.append("found no relative links at all in the shipped pages")
        print(f"  [FAIL] {problems[-1]}", file=sys.stderr)
    else:
        print(f"  [ok]   all {linked} relative links in the shipped pages resolve inside "
              f"{where}", flush=True)
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=sorted(BINARIES), required=True)
    parser.add_argument("--sidecar-only", action="store_true",
                        help="check the frozen sidecar alone; not a release verdict")
    parser.add_argument("--text-only", action="store_true",
                        help="check the shipped docs' commands and links alone; not a "
                             "release verdict")
    args = parser.parse_args(argv)

    staging = DIST / args.target
    binary = staging / BINARIES[args.target]
    failures: list[str] = []

    print(f"Checking the {args.target} release in {staging}", flush=True)

    if args.text_only:
        failures = check_release_text(args.target)
        print()
        if failures:
            print(f"{len(failures)} problem(s) in the shipped text", file=sys.stderr)
            return 1
        print("Shipped text OK. The engine and the sidecar were NOT checked: "
              "this is not a release verdict.")
        return 0

    if args.sidecar_only:
        sidecar = staging / SIDECARS[args.target]
        if not sidecar.exists():
            print(f"  [FAIL] no {sidecar.name}", file=sys.stderr)
            return 1
        for problem in check_drivers(sidecar, args.target):
            failures.append(problem)
            print(f"  [FAIL] {problem}", file=sys.stderr)
        if not failures:
            print("  [ok]   drivers usable", flush=True)
        failures.extend(check_grader(sidecar))
        print()
        if failures:
            print(f"{len(failures)} problem(s) in the frozen sidecar", file=sys.stderr)
            return 1
        print("Sidecar OK. The engine and its self-tests were NOT checked: "
              "this is not a release verdict.")
        return 0

    if not binary.exists():
        print(f"  [FAIL] no {binary.name} -- the export did not run", file=sys.stderr)
        return 1
    print(f"  [ok]   {binary.name} ({binary.stat().st_size / 1e6:.0f} MB)")

    # An export with no solution file writes the binary, skips the assemblies,
    # and exits 0. Every C# script then fails at runtime in a build that looks
    # perfectly healthy from the outside.
    assemblies = list(staging.glob("data_FactoryForge_*"))
    if not assemblies:
        failures.append("no data_FactoryForge_* folder -- the .NET assemblies were not exported "
                        "(engine/FactoryForge.sln missing at export time?)")
        print(f"  [FAIL] {failures[-1]}", file=sys.stderr)
    else:
        print(f"  [ok]   {assemblies[0].name}")

    sidecar = staging / SIDECARS[args.target]
    if not sidecar.exists():
        failures.append(f"no {sidecar.name} beside the binary -- F5 would fall back to "
                        "copying a command the user has no Python to run")
        print(f"  [FAIL] {failures[-1]}", file=sys.stderr)
    else:
        print(f"  [ok]   {sidecar.name} ({sidecar.stat().st_size / 1e6:.0f} MB)")
        problems = check_drivers(sidecar, args.target)
        for problem in problems:
            failures.append(problem)
            print(f"  [FAIL] {problem}", file=sys.stderr)
        if not problems:
            # Name what was actually verified, which on Windows includes PLCSIM.
            verified = list(EXPECTED_DRIVERS)
            if args.target == "windows":
                verified.append("plcsim-advanced")
            print(f"  [ok]   drivers usable: {', '.join(verified)}")
        failures.extend(check_grader(sidecar))

    # What the release tells its reader (IP-36, IP-38).
    failures.extend(check_release_text(args.target))

    print(f"\n  {len(SELF_TESTS)} self-tests against the exported binary:")
    for name in SELF_TESTS:
        try:
            passed, detail = run_self_test(binary, name)
        except subprocess.TimeoutExpired:
            passed, detail = False, "timed out"
        print(f"    [{'PASS' if passed else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""),
              flush=True)
        if not passed:
            failures.append(f"self-test {name}: {detail}")

    print()
    if failures:
        print(f"{len(failures)} problem(s) -- this release is not shippable", file=sys.stderr)
        return 1
    print(f"Release OK: {len(SELF_TESTS)} self-tests passed against {binary.name}; "
          f"the frozen sidecar graded {GATE_SCENE} to PASS and FAIL and ran demo; "
          f"every shipped command runs from the release and every link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
