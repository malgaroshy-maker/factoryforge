"""Cut the FactoryForge trailer: real engine footage with two generated shots.

    python tools/make_trailer.py --seedance DIR --out trailer.mp4
    python tools/make_trailer.py --seedance DIR --reuse CLIPS --out trailer.mp4

The middle of the film is the engine itself, recorded with the same machinery
as the README's GIF (tools/record_demo.py: Movie Maker on the game clock, the
filming flags, clips cut on the logged event that makes the action) but at
30 fps and encoded as H.264 rather than a GIF. It opens on the real sorting
line with the camera gliding in (--camera-to). Only the PLC shot and the logo
outro are generated (Seedance 2.5; provenance in engine/assets/ASSETS.md), and
neither shows a machine at work: a generated factory opened the first cut, and
its cartons merged, changed size and passed through the pusher, which is the
one thing a trailer for a simulator of how machines behave cannot show.

DIR holds the generated clips: 2_plc.mp4 and 3_outro.mp4. Their sound is the
only audio; the engine clips carry the PLC shot's hum, quietly, underneath. Needs Pillow and a recent ffmpeg, on PATH or in FFMPEG_DIR.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import record_demo as rd  # noqa: E402  (the recorder and the event cutter)

import os

# An old ffmpeg on PATH (this machine's is a 2013 build without a native AAC
# encoder) fails here; FFMPEG_DIR points at a newer one.
_BIN = Path(os.environ["FFMPEG_DIR"]) if os.environ.get("FFMPEG_DIR") else None
FFMPEG = str(_BIN / "ffmpeg") if _BIN else "ffmpeg"
FFPROBE = str(_BIN / "ffprobe") if _BIN else "ffprobe"

SIZE = (1280, 720)
FPS = 30
NAVY = (4, 25, 72)
ORANGE = (255, 138, 20)
TEAL = (22, 190, 190)
FADE = 0.35                 # seconds of fade at each cut

# The opening: the real sorting line, the camera gliding from a wide view down
# to the pusher as it fires, with the title over it. `title` is centred text,
# (lines, first second, last second) as in BOOKENDS below.
OPENING = {"kind": "scene", "template": None, "label": "opening",
           "camera": "-70,-38,3.6:1.8,0.4,0.1", "camera_to": "-25,-35,2.3:2.55,0.3,0.25@3.8-9.0",
           "watch": ["pusher.extend"], "record": 12.0,
           "cut": {"event": "pusher.extend=true", "before": 5.8, "after": 1.4},
           "title": ([("Learn PLC programming", 64, (255, 255, 255)), ("in a 3D factory", 40, ORANGE)], 0.6, 4.6)}

# Real footage. Same fields as record_demo.STORYBOARD; `sub` is a second,
# smaller caption line. The sorting line is the opening, so it is not here.
REAL = [
    {"kind": "start", "skip": 1.5, "take": 3.5,
     "caption": "Pick a scene. Each one comes with a task.",
     "sub": "19 scenes, from a start/stop station to a guarded robot cell"},
    {"kind": "scene", "template": "pick_and_place_cell",
     "camera": "-20,-38,3.6:3.7,0.5,0.0", "watch": ["gantry.grip", "gantry.lower"], "record": 12.0,
     "cut": {"event": "gantry.grip=true", "before": 1.5, "after": 3.5},
     "caption": "Sequence a gantry pick-and-place", "sub": "Axes, a vacuum cup and a barcode scanner"},
    {"kind": "scene", "template": "palletising_cell",
     "camera": "-8,-30,2.7:-0.1,0.55,-0.6", "watch": ["arm.grip", "pallet.count"], "record": 14.0,
     "cut": {"event": "arm.grip=true", "before": 1.0, "after": 4.0},
     "caption": "Stack a pallet with a robot arm", "sub": "Pattern, count and a full-pallet signal"},
    {"kind": "scene", "template": "guarded_cell",
     "camera": "-24,-32,2.8:1.6,0.5,0.5", "at": ["9.0:operate:guard_a"],
     "watch": ["belt.rotate", "guard_a.closed"], "record": 13.0,
     "cut": {"event": "action operate:guard_a", "before": 2.0, "after": 3.0},
     "caption": "Safety that really stops the machine", "sub": "Open the guard: the relay drops the belt"},
    {"kind": "scene", "template": "tank_level_control", "record": 12.0, "skip": 7.0, "take": 4.0,
     "caption": "Tune a PID loop on a real process", "sub": "Analog in, analog out, real inertia"},
]

# Text over the generated shots: (clip, lines, first second, last second).
BOOKENDS = {
    "2_plc": ([("Write the program. Watch it run.", 54, (255, 255, 255)),
               ("Real PLCs: S7-1500 · OpenPLC · Modbus · OPC UA", 28, TEAL)], 1.0, 8.6),
    "3_outro": ([("FactoryForge", 50, (255, 255, 255)),
                 ("github.com/malgaroshy-maker/factoryforge", 26, (170, 200, 235))], 1.5, 6.0),
}
ORDER = ["OPENING", "2_plc", "REAL", "3_outro"]


def ff(*args: str) -> None:
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args])
    if r.returncode != 0:
        sys.exit("ffmpeg failed: " + " ".join(args))


def font(size: int, bold: bool = True):
    return rd.find_font(["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"], size)


def lower_third(im: Image.Image, title: str, sub: str | None, alpha: float) -> Image.Image:
    """A navy card at the lower left with an orange edge: the logo's colours."""
    if alpha <= 0:
        return im
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    ft, fs = font(34), font(22, bold=False)
    w = max(d.textlength(title, font=ft), d.textlength(sub or "", font=fs)) + 56
    h = 96 if sub else 64
    x, y = 48, im.height - h - 44
    a = int(255 * alpha)
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(*NAVY, int(215 * alpha)))
    d.rectangle((x, y + 10, x + 6, y + h - 10), fill=(*ORANGE, a))
    d.text((x + 28, y + 12), title, font=ft, fill=(255, 255, 255, a))
    if sub:
        d.text((x + 28, y + 58), sub, font=fs, fill=(190, 205, 230, a))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def text_overlay(lines, path: Path) -> None:
    """Centred title lines on a transparent frame, for the generated shots."""
    ov = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    fonts = [font(size) for _, size, _ in lines]
    heights = [f.getbbox("Ag")[3] for f in fonts]
    total = sum(heights) + 14 * (len(lines) - 1)
    outro = path.stem.startswith("3_")
    # The outro sits under its logo; the opening's title sits high, off the
    # line the camera is gliding down to; the rest are centred.
    centre = 0.80 if outro else 0.22 if path.stem.endswith("_title") else 0.5
    y = SIZE[1] * centre - total / 2
    # A soft band behind the text so it reads over any frame.
    d.rounded_rectangle((SIZE[0] * 0.14, y - 26, SIZE[0] * 0.86, y + total + 26), radius=18,
                        fill=(*NAVY, 0 if outro else 150))
    for (text, _, colour), f, h in zip(lines, fonts, heights):
        d.text(((SIZE[0] - d.textlength(text, font=f)) / 2, y), text, font=f, fill=(*colour, 255))
        y += h + 14
    ov.save(path)


def norm(src: Path, dst: Path, overlay: Path | None = None, t0: float = 0, t1: float = 0) -> None:
    """Scale to 1280x720 at 30 fps, stereo 48 kHz, fade in and out; optionally a
    text overlay that fades in at t0 and out at t1."""
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout)
    v = (f"[0:v]scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio=increase,crop={SIZE[0]}:{SIZE[1]},"
         f"fps={FPS},format=yuv420p")
    args = ["-i", str(src)]
    if overlay:
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(overlay)]
        graph = (f"{v}[b];[1:v]format=rgba,fade=in:st={t0}:d=0.5:alpha=1,"
                 f"fade=out:st={t1 - 0.5}:d=0.5:alpha=1[o];[b][o]overlay=0:0:shortest=1,")
    else:
        graph = v + ","
    graph += f"fade=in:st=0:d={FADE},fade=out:st={dur - FADE:.3f}:d={FADE}[v]"
    audio = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,afade=in:d={FADE},afade=out:st={dur - FADE:.3f}:d={FADE}[a]"
    ff(*args, "-filter_complex", graph + ";" + audio, "-map", "[v]", "-map", "[a]",
       "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac", "-b:a", "192k", str(dst))


def real_segment(clip: dict, work: Path, ambience: Path, dst: Path) -> None:
    """Captioned frames -> H.264, with the generated shot's hum looped quietly
    under it. A clip with a `title` gets centred title text instead of a
    lower-third caption."""
    frames, how = rd.select_frames(clip, work)
    print(f"  {clip['name']}: {len(frames)} frames, cut {how}")
    out = work / f"{clip['name']}_cap"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    n = len(frames)
    title = None
    if "title" in clip:
        lines, t0, t1 = clip["title"]
        text_overlay(lines, work / f"{clip['name']}_title.png")
        title = Image.open(work / f"{clip['name']}_title.png")
    for i, f in enumerate(frames):
        with Image.open(f) as im:
            im = im.convert("RGB").resize(SIZE, Image.LANCZOS)
        t = i / FPS
        if title is not None:
            a = min(1.0, max(0.0, (t - t0) / 0.5), max(0.0, (t1 - t) / 0.5))
            ov = title.copy()
            ov.putalpha(ov.getchannel("A").point(lambda v: int(v * a)))
            Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB").save(out / f"c{i:05d}.png")
            continue
        alpha = min(1.0, max(0.0, (t - 0.3) / 0.4), max(0.0, (n / FPS - 0.3 - t) / 0.4))
        lower_third(im, clip["caption"], clip.get("sub"), alpha).save(out / f"c{i:05d}.png")
    dur = n / FPS
    ff("-framerate", str(FPS), "-i", str(out / "c%05d.png"), "-stream_loop", "-1", "-i", str(ambience),
       "-filter_complex",
       f"[0:v]format=yuv420p,fade=in:st=0:d={FADE},fade=out:st={dur - FADE:.3f}:d={FADE}[v];"
       f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.3,atrim=0:{dur:.3f},"
       f"afade=in:d={FADE},afade=out:st={dur - FADE:.3f}:d={FADE}[a]",
       "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
       "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac", "-b:a", "192k", str(dst))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seedance", type=Path, required=True, help="folder with 2_plc.mp4 and 3_outro.mp4")
    ap.add_argument("--reuse", type=Path, help="folder of engine recordings from an earlier run")
    ap.add_argument("--godot", default=rd.DEFAULT_GODOT)
    ap.add_argument("--out", type=Path, required=True)
    opts = ap.parse_args()

    rd.FPS = FPS                        # record and cut at 30 fps, not the GIF's 12
    work = opts.reuse or Path(tempfile.mkdtemp(prefix="ff_trailer_"))
    work.mkdir(parents=True, exist_ok=True)
    seg_dir = work / "segments"
    seg_dir.mkdir(exist_ok=True)

    if not opts.reuse:
        if subprocess.run(["dotnet", "build", "-v", "q", "--nologo"], cwd=rd.ENGINE).returncode != 0:
            sys.exit("dotnet build failed (gotcha 14: a stale binary would be filmed)")

    ambience = opts.seedance / "2_plc.mp4"
    segments = []
    for key in ORDER:
        if key == "OPENING":
            clip = dict(OPENING, name="00_opening")
            if not (work / clip["name"]).exists():
                rd.record(opts.godot, clip, work, work)
            dst = seg_dir / "real_00_opening.mp4"
            real_segment(clip, work, ambience, dst)
            segments.append(dst)
            continue
        if key != "REAL":
            lines, t0, t1 = BOOKENDS[key]
            text_overlay(lines, work / f"{key}_text.png")
            dst = seg_dir / f"{key}.mp4"
            norm(opts.seedance / f"{key}.mp4", dst, work / f"{key}_text.png", t0, t1)
            segments.append(dst)
            continue
        for i, step in enumerate(REAL):
            clip = dict(step, name=f"{i + 1:02d}_{step.get('label') or step.get('template') or step['kind']}")
            if not (work / clip["name"]).exists():
                rd.record(opts.godot, clip, work, work)
            dst = seg_dir / f"real_{clip['name']}.mp4"
            real_segment(clip, work, ambience, dst)
            segments.append(dst)

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(opts.out))
    dur = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(opts.out)], capture_output=True, text=True).stdout.strip()
    print(f"wrote {opts.out}: {float(dur):.1f} s, {opts.out.stat().st_size / 1e6:.1f} MB; clips in {work}")


if __name__ == "__main__":
    main()
