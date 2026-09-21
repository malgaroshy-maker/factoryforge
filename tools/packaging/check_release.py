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
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

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
    "controlparts", "editorkeys",
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
EXPECTED_DRIVERS = ["mock", "modbus-tcp", "opcua-client", "opcua-server", "s7-snap7"]


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
             f"-- build with pip install -e \"sidecar[opcua,siemens,plcsim]\""]
            if missing else [])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=sorted(BINARIES), required=True)
    args = parser.parse_args(argv)

    staging = DIST / args.target
    binary = staging / BINARIES[args.target]
    failures: list[str] = []

    print(f"Checking the {args.target} release in {staging}")

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
    print(f"Release OK: {len(SELF_TESTS)} self-tests passed against {binary.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
