"""Build every logo file from one master (V12-06).

    python tools/make_branding.py                      # derivatives from the master
    python tools/make_branding.py --from-raw <png>     # re-cut the master first

The master is engine/assets/branding/logo_1024.png: the picked Higgsfield
concept with its rounded square re-masked, because the generated image sits on
a white page and its corners are white, not transparent. Everything else is
derived from it, so a new logo means replacing one file and running this:

    engine/icon.png                   256 px, the window and Linux icon
    engine/icon.ico                   16-256 px, the Windows executable's icon
    docs/images/banner.png            the README header
    docs/images/social_preview.png    1280x640, GitHub's social preview

Needs Pillow. The text uses Segoe UI, so the banner is built on Windows.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "engine" / "assets" / "branding" / "logo_1024.png"
NAVY = (4, 25, 72)            # the generated square's own background
ORANGE = (255, 138, 20)
TEAL = (22, 190, 190)
WHITE = (240, 244, 250)
RADIUS = 0.197                # corner radius / side, measured on the raw image
FONT_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
FONT = r"C:\Windows\Fonts\segoeui.ttf"


def rounded_mask(size: int, inset: int, radius: float, ss: int = 4) -> Image.Image:
    """An anti-aliased rounded-square alpha mask, drawn at ss times and shrunk."""
    big = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (inset * ss, inset * ss, (size - inset) * ss - 1, (size - inset) * ss - 1),
        radius=int(radius * size * ss), fill=255)
    return big.resize((size, size), Image.LANCZOS)


def cut_master(raw: Path) -> None:
    im = Image.open(raw).convert("RGB")
    side = im.width
    # Inset a few pixels so none of the white page's anti-aliased fringe survives.
    rgba = im.convert("RGBA")
    rgba.putalpha(rounded_mask(side, inset=max(2, side // 400), radius=RADIUS))
    MASTER.parent.mkdir(parents=True, exist_ok=True)
    rgba.resize((1024, 1024), Image.LANCZOS).save(MASTER, optimize=True)
    print("master", MASTER.relative_to(ROOT))


def fit_text(draw: ImageDraw.ImageDraw, text: str, path: str, size: int, max_w: int) -> ImageFont.FreeTypeFont:
    while size > 8:
        font = ImageFont.truetype(path, size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return ImageFont.truetype(path, size)


def lockup(w: int, h: int, logo_px: int, title_px: int, tag_px: int, credit: bool) -> Image.Image:
    """Logo left, name and one line of purpose right, on the logo's own navy."""
    canvas = Image.new("RGB", (w, h), NAVY)
    logo = Image.open(MASTER).resize((logo_px, logo_px), Image.LANCZOS)
    pad = (h - logo_px) // 2
    canvas.paste(logo, (pad, pad), logo)
    d = ImageDraw.Draw(canvas)
    x = pad + logo_px + max(24, pad // 2)
    room = w - x - pad
    title = fit_text(d, "FactoryForge", FONT_BOLD, title_px, room)
    tag = fit_text(d, "A free, open 3D factory simulator for learning PLC programming", FONT, tag_px, room)
    lines = [("Factory", ORANGE, title), ("Forge", TEAL, title)]
    th = title.getbbox("FactoryForge")[3]
    gh = tag.getbbox("Ag")[3]
    block = th + gh // 2 + gh + (int(gh * 1.4) if credit else 0)
    y = (h - block) // 2
    cx = x
    for word, colour, font in lines:
        d.text((cx, y), word, font=font, fill=colour)
        cx += d.textlength(word, font=font)
    y += th + gh // 2
    d.text((x, y), "A free, open 3D factory simulator for learning PLC programming", font=tag, fill=WHITE)
    if credit:
        small = ImageFont.truetype(FONT, int(tag.size * 0.72))
        d.text((x, y + int(gh * 1.4)), "by Mahamed Algaroshy", font=small, fill=(150, 165, 195))
    return canvas


def derivatives() -> None:
    master = Image.open(MASTER)
    master.resize((256, 256), Image.LANCZOS).save(ROOT / "engine" / "icon.png", optimize=True)
    master.save(ROOT / "engine" / "icon.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    images = ROOT / "docs" / "images"
    lockup(1280, 320, 224, 104, 34, credit=False).save(images / "banner.png", optimize=True)
    lockup(1280, 640, 400, 120, 36, credit=True).save(images / "social_preview.png", optimize=True)
    for p in ("engine/icon.png", "engine/icon.ico", "docs/images/banner.png", "docs/images/social_preview.png"):
        print(p, (ROOT / p).stat().st_size, "bytes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-raw", type=Path, help="re-cut the master from a generated image first")
    args = ap.parse_args()
    if args.from_raw:
        cut_master(args.from_raw)
    derivatives()
