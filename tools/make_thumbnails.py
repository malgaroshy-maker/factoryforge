"""Render a start-screen thumbnail for every template (V12-07).

    python tools/make_thumbnails.py            # every template in the manifest
    python tools/make_thumbnails.py rotary-index pick-and-place-cell

Each one is the real engine, not a drawing: the template loaded with its demo
running (--demo, a no-op for a scene without a profile), every panel hidden
(--film), a frame taken after the demo has had time to move something, then
cropped to the middle and saved as engine/templates/thumbnails/<id>.jpg. So a
thumbnail shows what you will get, and a template whose look changes is
refreshed by re-running this rather than by redrawing anything.

Needs a display (it renders) and Pillow.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
OUT = ENGINE / "templates" / "thumbnails"
GODOT = os.environ.get(
    "GODOT", r"D:\Godot_v4.7.2-stable_mono_win64\Godot_v4.7.2-stable_mono_win64_console.exe")
SIZE = (480, 270)
CROP = 0.62          # keep the middle 62%: the scene's own framing leaves a lot of floor and wall
DROP = 0.05          # and a little below centre, where the machinery sits


def render(entry: dict, png: Path) -> bool:
    # Everything after "--" is the engine's; a --scene before it is Godot's and
    # is silently ignored, which rendered the default line nineteen times.
    user = ["--demo", "--film", "--duration=10", f"--screenshot={png.as_posix()}", "--screenshot-at=8"]
    if entry["path"]:
        user.append(f"--scene={entry['path']}")
    args = [GODOT, "--path", str(ENGINE), "--resolution", "1280x720", "--", *user]
    # To a file, never a pipe nobody drains: AGENTS.md gotcha 15.
    with tempfile.TemporaryFile() as log:
        subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    return png.is_file()


def main() -> int:
    manifest = json.loads((ENGINE / "templates" / "manifest.json").read_text(encoding="utf-8"))
    wanted = set(sys.argv[1:])
    OUT.mkdir(exist_ok=True)
    failed = []
    with tempfile.TemporaryDirectory() as tmp:
        for entry in manifest:
            if wanted and entry["id"] not in wanted:
                continue
            png = Path(tmp) / f"{entry['id']}.png"
            if not render(entry, png):
                failed.append(entry["id"])
                print("FAIL", entry["id"])
                continue
            im = Image.open(png).convert("RGB")
            w, h = im.size
            cw, ch = int(w * CROP), int(h * CROP)
            top = (h - ch) // 2 + int(h * DROP)
            im = im.crop(((w - cw) // 2, top, (w + cw) // 2, top + ch))
            im.resize(SIZE, Image.LANCZOS).save(OUT / f"{entry['id']}.jpg", quality=86, optimize=True)
            print("ok  ", entry["id"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
