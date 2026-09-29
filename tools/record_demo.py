"""Record the README's demo GIF from the running engine.

Replaces the old tools/create_video.py, which stitched four hand-taken stills
into a slideshow that went stale the moment the UI changed. This drives the
real engine through Godot's Movie Maker mode (``--write-movie``), which writes
one PNG per frame on the *game* clock, so the machines move at their true
speed however slow the encoding is, and then assembles the clips, an end card
and the GIF.

    python tools/record_demo.py                 # record everything, write the GIF
    python tools/record_demo.py --keep          # keep the per-frame PNGs
    python tools/record_demo.py --reuse DIR     # skip recording, reuse DIR's clips
    python tools/record_demo.py --godot PATH    # or set the GODOT environment variable

To change the film, edit STORYBOARD below. Each scene clip is a template
(``--scene=``) run under its built-in demo controller (``--demo``, the same
thing the start screen's "Watch it run" starts). ``skip`` drops the first
seconds (the scene is still settling, nothing has reached the machine yet) and
``take`` is how much is kept.

Needs Pillow, plus arabic-reshaper and python-bidi for the end card (Pillow's
own Arabic shaping needs libraqm, which most wheels lack). Every recording is
Godot's wall-clock-slow (each frame is PNG-encoded synchronously, about
half a second at 1280x720), so a full run takes several minutes.

AGENTS.md gotcha 15: Godot's stdout goes to a file, never an undrained pipe.
Gotcha 14: build first; a failed build leaves the last binary in place.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
OUT = ROOT / "docs" / "images" / "demo_video.gif"
DEFAULT_GODOT = r"D:\Godot_v4.7.2-stable_mono_win64\Godot_v4.7.2-stable_mono_win64_console.exe"

# ---------------------------------------------------------------- the film --
REC_SIZE = (1280, 720)      # what Godot renders
GIF_SIZE = (1280, 720)      # what the GIF holds
FPS = 12                    # recorded frames per game-second
FRAME_MS = 80               # GIF frame time (12.5 fps; GIF times are 10 ms steps)

# kind "start": the start screen (no arguments), held still.
# kind "scene": --scene=<template> --demo. skip/take are game-seconds.
# kind "card":  the end card, rendered here.
STORYBOARD = [
    # The credit line at the foot of the start screen is ~9 px tall at 1440 wide, so this clip
    # is recorded at twice the size and, after `hold` seconds, eased in on it over `over`
    # seconds. box is (centre x, centre y, width) as fractions of the frame.
    {"kind": "start", "skip": 1.0, "take": 3.5, "res": (2560, 1440),
     "zoom": {"hold": 1.2, "over": 1.3, "box": (0.5, 0.82, 0.30)}},
    {"kind": "scene", "template": None, "skip": 1.0, "take": 7.0,
     "label": "sorting-by-height"},                    # None: --demo alone loads it
    {"kind": "scene", "template": "pick_and_place_cell", "skip": 2.0, "take": 4.0},
    {"kind": "scene", "template": "palletising_cell", "skip": 2.0, "take": 3.5},
    {"kind": "scene", "template": "guarded_cell", "skip": 2.0, "take": 5.0},
    {"kind": "card", "take": 2.5},
]

CARD_TITLE = "FactoryForge"
CARD_TAGLINE = "free, open PLC training simulator"
CARD_AUTHOR_EN = "Developed by Mahamed Algaroshy"
CARD_AUTHOR_AR = "محمد الجروشي"
CARD_URL = "github.com/malgaroshy-maker/factoryforge"
# ------------------------------------------------------------------------------


def find_font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/noto"),
            Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    for name in names:
        for d in dirs:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    sys.exit(f"no usable font among {names}; install one or edit find_font()")


def shape_arabic(text: str) -> str:
    """Join and reorder Arabic for a renderer that only draws left to right.
    Pillow needs libraqm to do this itself; without it Arabic comes out as
    disconnected letters in the wrong order."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(text))


def render_card(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, (24, 27, 33))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 8], fill=(255, 140, 40))
    bold = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
    regular = ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    arabic = ["segoeui.ttf", "tahoma.ttf", "arial.ttf", "DejaVuSans.ttf"]
    f_title = find_font(bold, 110)
    f_tag = find_font(regular, 44)
    f_auth = find_font(regular, 38)
    f_url = find_font(["consola.ttf", "DejaVuSansMono.ttf"] + regular, 36)

    def centred(y: int, text: str, font, fill) -> None:
        box = d.textbbox((0, 0), text, font=font)
        d.text(((w - (box[2] - box[0])) / 2 - box[0], y), text, font=font, fill=fill)

    centred(int(h * 0.22), CARD_TITLE, f_title, (255, 255, 255))
    centred(int(h * 0.22) + 140, CARD_TAGLINE, f_tag, (255, 170, 90))
    d.line([w * 0.3, h * 0.60, w * 0.7, h * 0.60], fill=(70, 76, 88), width=2)
    # English and Arabic on their own lines, so neither has to embed the other.
    centred(int(h * 0.65), CARD_AUTHOR_EN, f_auth, (220, 224, 230))
    centred(int(h * 0.65) + 56, shape_arabic(CARD_AUTHOR_AR), find_font(arabic, 40), (220, 224, 230))
    centred(int(h * 0.65) + 130, CARD_URL, f_url, (120, 190, 255))
    return img


def encode_gif(segments: list[list[Image.Image]], out: Path, palette: str,
               dither: bool) -> None:
    """Quantize and write the GIF. ``segments`` is the film clip by clip.

    The first version quantized the whole film to one 128-colour palette with
    no dithering. Five scenes shared it, and the grey floor and the dark UI
    took nearly all of it: orange cartons came out beige, the red and blue
    panel buttons lost their colour, the end card's white/orange/blue turned
    cream/tan/green, and the stack light's soft glow collapsed into a flat
    blotch.

    ``per-clip`` (the default) builds one 256-colour palette per clip from a
    spread of that clip's own frames, so each scene keeps its own colours,
    and a frame's unchanged areas stay byte-identical to the last frame's, so
    GIF's frame differencing still compresses them. ``per-frame`` gives every
    frame its own palette (truest colour, several times the size). Dithering
    smooths gradients but its noise defeats the differencing: measured on
    this film, per-frame with dithering was 128 MB and a single dithered
    palette 55 MB, against a few MB without.
    """
    mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE

    def palette_from(frames: list[Image.Image]) -> Image.Image:
        sample = frames[:: max(1, len(frames) // 12)]
        strip = Image.new("RGB", (GIF_SIZE[0], GIF_SIZE[1] * len(sample)))
        for i, im in enumerate(sample):
            strip.paste(im, (0, i * GIF_SIZE[1]))
        # FASTOCTREE, not MEDIANCUT: median cut splits by pixel count, so the
        # acres of grey floor and dark UI won the palette and orange cartons,
        # red buttons and the yellow bumper came out washed-out beige. Octree
        # kept them indistinguishable from the raw frame (compared side by
        # side on the sorting clip).
        return strip.quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                              dither=Image.Dither.NONE)

    frames_p = []
    for seg in segments:
        if palette == "per-frame":
            frames_p += [im.quantize(palette=palette_from([im]), dither=mode) for im in seg]
        else:
            shared = palette_from(seg)
            frames_p += [im.quantize(palette=shared, dither=mode) for im in seg]
    frames_p[0].save(out, save_all=True, append_images=frames_p[1:], duration=FRAME_MS,
                     loop=0, optimize=True, disposal=1)


def run_godot(godot: str, args: list[str], log: Path) -> None:
    """Run Godot to completion with stdout/stderr in a file (gotcha 15)."""
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        r = subprocess.run([godot, *args], cwd=ENGINE, stdout=fh, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        sys.exit(f"godot exited {r.returncode}; see {log}")


def record(godot: str, clip: dict, out_dir: Path, log_dir: Path) -> list[Path]:
    frames_dir = out_dir / clip["name"]
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    total = int(round((clip["skip"] + clip["take"]) * FPS))
    args = ["--path", ".", "--resolution", "x".join(map(str, clip.get("res", REC_SIZE))),
            "--write-movie", str(frames_dir / "f.png"), "--fixed-fps", str(FPS),
            "--quit-after", str(total)]
    if clip["kind"] == "scene":
        args.append("--")
        if clip.get("template"):
            args.append(f"--scene=res://templates/{clip['template']}.json")
        args.append("--demo")
    print(f"recording {clip['name']}: {total} frames ...", flush=True)
    run_godot(godot, args, log_dir / f"{clip['name']}.log")
    frames = sorted(frames_dir.glob("f*.png"))
    if len(frames) < total:
        sys.exit(f"{clip['name']}: expected {total} frames, got {len(frames)}")
    skip = int(round(clip["skip"] * FPS))
    return frames[skip:total]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--godot", default=os.environ.get("GODOT", DEFAULT_GODOT))
    ap.add_argument("--reuse", type=Path, help="directory of clips from an earlier --keep run "
                    "(a clip whose folder is missing is recorded again)")
    ap.add_argument("--keep", action="store_true", help="keep the per-frame PNGs and print where")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--palette", choices=["per-clip", "per-frame"], default="per-clip",
                    help="one 256-colour palette per clip (default) or per frame (much larger)")
    ap.add_argument("--dither", action="store_true",
                    help="Floyd-Steinberg dithering: smoother gradients, many times the size")
    opts = ap.parse_args()

    work = opts.reuse or Path(tempfile.mkdtemp(prefix="ff_demo_"))
    work.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, step in enumerate(STORYBOARD):
        clip = dict(step)
        clip["name"] = f"{i:02d}_{step.get('label') or step.get('template') or step['kind']}"
        clips.append(clip)

    if not opts.reuse:
        print("building the engine (a failed build leaves a stale binary, gotcha 14) ...")
        if subprocess.run(["dotnet", "build", "-v", "q", "--nologo"], cwd=ENGINE).returncode != 0:
            sys.exit("dotnet build failed")
        if not (ENGINE / ".godot").exists():
            run_godot(opts.godot, ["--headless", "--path", ".", "--import"], work / "import.log")

    segments: list[list[Image.Image]] = []
    for clip in clips:
        if clip["kind"] == "card":
            card = render_card(GIF_SIZE)
            card.save(work / "card.png")
            segments.append([card] * int(round(clip["take"] * 1000 / FRAME_MS)))
            continue
        if opts.reuse and (work / clip["name"]).exists():
            frames = sorted((work / clip["name"]).glob("f*.png"))[int(round(clip["skip"] * FPS)):]
        else:
            frames = record(opts.godot, clip, work, work)
        keep = int(round(clip["take"] * FPS))
        # A start screen does not move; frames are frames all the same.
        zoom = clip.get("zoom")
        segments.append([])
        for n, f in enumerate(frames[:keep]):
            with Image.open(f) as im:
                im = im.convert("RGB")
                if zoom:
                    k = min(1.0, max(0.0, (n / FPS - zoom["hold"]) / zoom["over"]))
                    k = k * k * (3 - 2 * k)                       # ease in and out
                    cx, cy, bw = zoom["box"]
                    w = 1 + (bw - 1) * k
                    x0 = (0.5 + (cx - 0.5) * k) - w / 2
                    y0 = (0.5 + (cy - 0.5) * k) - w / 2
                    im = im.crop((round(x0 * im.width), round(y0 * im.height),
                                  round((x0 + w) * im.width), round((y0 + w) * im.height)))
                segments[-1].append(im.resize(GIF_SIZE, Image.LANCZOS))

    opts.out.parent.mkdir(parents=True, exist_ok=True)
    encode_gif(segments, opts.out, opts.palette, opts.dither)
    sequence = [im for seg in segments for im in seg]
    mb = opts.out.stat().st_size / 1e6
    print(f"wrote {opts.out}: {len(sequence)} frames, {len(sequence) * FRAME_MS / 1000:.1f} s, "
          f"{GIF_SIZE[0]}x{GIF_SIZE[1]}, {mb:.1f} MB")
    if opts.keep or opts.reuse:
        print(f"clips kept in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
