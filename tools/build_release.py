#!/usr/bin/env python3
"""Build a downloadable FactoryForge release: engine, sidecar, examples, docs.

    python tools/build_release.py                 # this platform
    python tools/build_release.py --no-sidecar    # engine only, much faster
    python tools/build_release.py --skip-archive  # leave the staged tree

The point of a release is that someone can extract one archive and connect to a
PLC without installing Godot, the .NET SDK, or Python. That means three things
have to be true, and each is checked here rather than assumed:

* the engine is exported with its .NET assemblies (an export with no solution
  file produces a binary whose every script silently fails -- it looks fine),
* the sidecar is frozen, so no Python is needed on the target machine,
* the engine can find that frozen sidecar (SidecarLocator looks beside the
  binary first, which is exactly where this puts it),
* the frozen sidecar carries the grader whole -- every scene module, which
  nothing imports by name, and the engine's scene templates, which the grader
  reads from disk and the exported engine keeps inside its .pck (IP-08).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
DIST = ROOT / "dist"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "packaging"))
from run import find_godot  # noqa: E402
import release_text  # noqa: E402

#: Preset name in engine/export_presets.cfg -> (staging dir, binary name).
TARGETS = {
    "windows": ("Windows Desktop", "FactoryForge.exe"),
    "linux": ("Linux", "FactoryForge.x86_64"),
}

#: Everything that is not the engine itself. templates/ are res:// resources
#: and travel inside the binary, so they are deliberately absent here; the
#: grader's copy travels inside the frozen sidecar (`freeze_sidecar`). These
#: are the seeds: every document they link to ships too (`payload_files`,
#: IP-38), so a page linked from here needs no entry of its own. OPENPLC.md
#: and GRADING.md are named anyway -- the first hour depends on both.
PAYLOAD = [
    ("examples", "examples"),
    ("docs/GETTING_STARTED.md", "docs/GETTING_STARTED.md"),
    ("docs/OPENPLC.md", "docs/OPENPLC.md"),
    ("docs/GRADING.md", "docs/GRADING.md"),
    ("docs/tag-bus.md", "docs/tag-bus.md"),
    ("docs/DRIVER_AUTHORING.md", "docs/DRIVER_AUTHORING.md"),
    ("docs/PART_AUTHORING.md", "docs/PART_AUTHORING.md"),
    ("docs/TEST_PLAN.md", "docs/TEST_PLAN.md"),
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
]


def payload_files(root: Path = ROOT, payload: list[tuple[str, str]] | None = None
                  ) -> dict[str, Path]:
    """`{path in the release: source file}` for everything but the engine and
    the sidecar: PAYLOAD, plus every document a shipped page links to, followed
    through the pages that adds (IP-38). A link the build cannot honour -- to
    nothing, out of the tree, or to source code -- stops the build here rather
    than shipping a dead link.

    Every entry keeps its repository path in the release, which is what lets a
    relative link resolve the same in both.
    """
    files: dict[str, Path] = {}
    for src_rel, dest_rel in (PAYLOAD if payload is None else payload):
        if src_rel != dest_rel:
            raise SystemExit(f"[payload] {src_rel} -> {dest_rel}: a moved file breaks "
                             f"every relative link to and from it")
        src = root / src_rel
        if not src.exists():
            raise SystemExit(f"[payload] missing: {src_rel}")
        if src.is_dir():
            for path in sorted(src.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    files[path.relative_to(root).as_posix()] = path
        else:
            files[dest_rel] = src
    added, problems = release_text.doc_closure(root, set(files))
    if problems:
        raise SystemExit("[payload] shipped pages link to what a release cannot carry:\n  "
                         + "\n  ".join(problems))
    for rel in sorted(added):
        files[rel] = root / rel
    return dict(sorted(files.items()))


def payload_bytes(rel: str, src: Path) -> bytes:
    """What a payload file ships as. A page in `release_text.SOURCE_ONLY_DOCS`
    is wrapped in one from-source region under a visible note (IP-36);
    everything else is copied byte for byte."""
    data = src.read_bytes()
    if not release_text.is_source_only(rel):
        return data
    banner = release_text.source_only_banner(rel)
    if banner is None:
        raise SystemExit(f"[payload] {rel} is listed as source-only, but a "
                         f"{Path(rel).suffix or 'suffix-less'} file cannot carry the note")
    head, tail = banner
    return (head + data.decode("utf-8") + tail).encode("utf-8")


def platform_key() -> str:
    return "windows" if sys.platform.startswith("win") else "linux"


def export_engine(godot: str, target: str, staging: Path) -> Path:
    preset, binary = TARGETS[target]
    out = staging / binary
    staging.mkdir(parents=True, exist_ok=True)

    if not (ENGINE / "FactoryForge.sln").exists():
        raise SystemExit(
            "engine/FactoryForge.sln is missing. Godot refuses to export the .NET\n"
            "assemblies without it and still writes a binary, so the export looks\n"
            "like it worked and every C# script in it fails at runtime. Create it\n"
            "with:  dotnet new sln -n FactoryForge --format sln && dotnet sln add FactoryForge.csproj")

    print(f"[engine] exporting {preset} -> {out}")
    result = subprocess.run(
        [godot, "--headless", "--path", str(ENGINE), "--export-release", preset, str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    # Godot exits 0 on an export that "completed with warnings", and a missing
    # .NET solution is one of those warnings, so the exit code alone is not
    # enough to trust.
    for line in (result.stdout + result.stderr).splitlines():
        if "ERROR:" in line and "Export" in line:
            print("  " + line.strip())
    if not out.exists():
        raise SystemExit(f"export produced no {out}")
    return out


SIDECAR_SRC = ROOT / "sidecar"
PACKAGE = SIDECAR_SRC / "factoryforge_sidecar"
TEMPLATES = ENGINE / "templates"

#: Where the templates go inside the frozen bundle. It must be exactly the path
#: `grading/templates.py` looks for under `sys._MEIPASS` (`_RELATIVE` there),
#: or the frozen grader finds nothing and says so on every command.
TEMPLATES_IN_BUNDLE = "engine/templates"


def freeze_env() -> dict[str, str]:
    """The environment PyInstaller runs in: this checkout's sidecar first on
    PYTHONPATH.

    `--collect-submodules factoryforge_sidecar` is evaluated when the spec is
    read, by importing the package in the Python running PyInstaller -- before
    `--paths` has any effect. If that Python cannot import the package it
    collects *nothing*, silently, and the modules nothing imports by name go
    missing: every grader scene and every reference controller, which the
    registry finds at runtime. If it imports a *different* copy -- an editable
    install pointing at another checkout -- it collects that one's module
    list. Putting this checkout first settles both.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SIDECAR_SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def check_collectable(env: dict[str, str]) -> None:
    """Refuse to freeze unless PyInstaller will collect every module in the
    package -- asked the way PyInstaller itself will ask, and compared with
    the files on disk rather than with a count that could be stale."""
    probe = (
        "import json, factoryforge_sidecar as p\n"
        "from PyInstaller.utils.hooks import collect_submodules\n"
        "print(json.dumps([p.__file__, collect_submodules('factoryforge_sidecar')]))\n")
    result = subprocess.run([sys.executable, "-c", probe], env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
    if result.returncode != 0:
        raise SystemExit("[sidecar] the Python running PyInstaller cannot import "
                         "factoryforge_sidecar, so --collect-submodules would collect "
                         "nothing:\n" + (result.stderr or result.stdout)[-2000:])
    where, collected = json.loads(result.stdout.strip().splitlines()[-1])
    if Path(where).resolve().parent != PACKAGE.resolve():
        raise SystemExit(f"[sidecar] PyInstaller would collect factoryforge_sidecar from "
                         f"{where}, not from this checkout's {PACKAGE}")
    on_disk = set()
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(SIDECAR_SRC).with_suffix("").parts
        if "__pycache__" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        on_disk.add(".".join(parts))
    missing = sorted(on_disk - set(collected) - {"factoryforge_sidecar.__main__"})
    if missing:
        raise SystemExit(f"[sidecar] PyInstaller would not collect: {', '.join(missing)}")
    print(f"[sidecar] {len(collected)} modules collectable from {PACKAGE}")


def freeze_sidecar(staging: Path) -> Path | None:
    """PyInstaller the sidecar into one executable beside the engine.

    Carries the grader (IP-08): every module of the package, and the engine's
    scene templates as data, because the exported engine keeps its own copy
    inside the .pck where Python cannot read it.
    """
    entry = ROOT / "tools" / "packaging" / "sidecar_entry.py"
    work = DIST / "_pyinstaller"
    if not (TEMPLATES / "manifest.json").is_file():
        raise SystemExit(f"[sidecar] no {TEMPLATES / 'manifest.json'}; the grader "
                         f"would ship with no plant to mark against")
    env = freeze_env()
    check_collectable(env)
    print("[sidecar] freezing with PyInstaller (a few minutes)")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
         "--name", "factoryforge-sidecar",
         "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
         "--specpath", str(work),
         "--paths", str(SIDECAR_SRC),
         "--collect-submodules", "factoryforge_sidecar",
         # SOURCE<sep>DEST, and the separator is os.pathsep: ';' on Windows,
         # ':' elsewhere. Absolute source, because --specpath moves the
         # directory a relative one would be resolved against.
         "--add-data", f"{TEMPLATES}{os.pathsep}{TEMPLATES_IN_BUNDLE}",
         str(entry)],
        env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit("PyInstaller failed")

    built = next((work / "dist").glob("factoryforge-sidecar*"), None)
    if built is None:
        raise SystemExit("PyInstaller reported success but produced no executable")
    dest = staging / built.name
    shutil.copy2(built, dest)
    print(f"[sidecar] {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def copy_payload(staging: Path) -> None:
    files = payload_files()
    seeds = [dest for _, dest in PAYLOAD]
    linked = [rel for rel in files
              if not any(rel == s or rel.startswith(s + "/") for s in seeds)]
    marked = 0
    for rel, src in files.items():
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = payload_bytes(rel, src)
        dest.write_bytes(data)
        marked += len(data) != src.stat().st_size
    print(f"[payload] {len(files)} files from {len(PAYLOAD)} entries, {len(linked)} of them "
          f"only because a shipped page links to them; {marked} marked source-only")


def make_archive(staging: Path, target: str) -> Path:
    archive = DIST / f"FactoryForge-{target}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(staging.parent))
    print(f"[archive] {archive.name} ({archive.stat().st_size / 1e6:.1f} MB)")
    return archive


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=sorted(TARGETS), default=platform_key(),
                        help="which preset to export (default: this platform)")
    parser.add_argument("--no-sidecar", action="store_true",
                        help="skip the PyInstaller step")
    parser.add_argument("--skip-archive", action="store_true",
                        help="leave the staged tree without zipping it")
    args = parser.parse_args(argv)

    godot = find_godot()
    if godot is None:
        print("[ERROR] no Godot .NET build found; see run.py's message.", file=sys.stderr)
        return 1

    staging = DIST / args.target
    if staging.exists():
        shutil.rmtree(staging)

    export_engine(godot, args.target, staging)
    if not args.no_sidecar:
        freeze_sidecar(staging)
    copy_payload(staging)

    if not args.skip_archive:
        make_archive(staging, args.target)

    print(f"\nStaged in {staging}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
