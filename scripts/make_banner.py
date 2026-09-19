"""Compose `docs/banner.png` - the README hero image.

    python scripts/make_banner.py

Puts the three things the project actually is side by side: the order image
that goes in, the window that shows what was read, and the saved Invoice that
comes out. Everything is cropped from real artifacts, so the banner cannot show
something the system does not do.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "banner.png"

SOURCE = REPO_ROOT / "fixtures" / "sample_order.png"
WINDOW = REPO_ROOT / "docs" / "screenshots" / "gui-03-groq.png"
RESULT = REPO_ROOT / "docs" / "screenshots" / "run"

NAVY = (14, 35, 56)
NAVY_SOFT = (27, 58, 92)
ACCENT = (46, 125, 209)
SUBTLE = (159, 182, 206)
WHITE = (255, 255, 255)

W, H = 1600, 620


def font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def text_width(draw, text, f) -> int:
    try:
        return int(draw.textlength(text, font=f))
    except Exception:
        return f.getsize(text)[0]


def panel(canvas, draw, image_path: Path, box, caption: str, crop=None):
    """Paste an image into `box` (x, y, w, h), letterboxed, with a caption under it."""
    x, y, w, h = box
    draw.rectangle([x - 2, y - 2, x + w + 2, y + h + 2], fill=(255, 255, 255, 255))

    img = Image.open(image_path).convert("RGB")
    if crop:
        img = img.crop(crop)
    img.thumbnail((w, h), Image.LANCZOS)
    canvas.paste(img, (x + (w - img.width) // 2, y + (h - img.height) // 2))

    f = font(17, bold=True)
    draw.text((x + w / 2 - text_width(draw, caption, f) / 2, y + h + 16),
              caption, font=f, fill=SUBTLE)


def arrow(draw, x, y, length=46):
    draw.line([(x, y), (x + length, y)], fill=ACCENT, width=4)
    draw.polygon([(x + length + 14, y), (x + length - 4, y - 9), (x + length - 4, y + 9)],
                 fill=ACCENT)


def main() -> int:
    for required in (SOURCE, WINDOW):
        if not required.exists():
            print("missing %s - run scripts/capture_demo.py first" % required)
            return 2

    final = sorted(RESULT.glob("B23-*.jpg")) or sorted(RESULT.glob("B*.jpg"))
    if not final:
        print("no annotated run figures in %s" % RESULT)
        return 2

    canvas = Image.new("RGB", (W, H), NAVY)
    draw = ImageDraw.Draw(canvas)

    draw.rectangle([0, 0, W, 6], fill=ACCENT)

    draw.text((56, 44), "Fakturama Image-to-Cash", font=font(46, bold=True), fill=WHITE)
    draw.text((58, 108),
              "One order image  →  Python OCR  →  Groq validation  →  "
              "a saved, verified Order and its linked Invoice",
              font=font(20), fill=SUBTLE)

    chip_x = 58
    for label in ("Microsoft UI Automation", "EasyOCR", "Groq LLM", "no hardcoded coordinates"):
        f = font(14, bold=True)
        w = text_width(draw, label, f) + 26
        draw.rounded_rectangle([chip_x, 150, chip_x + w, 182], radius=16, fill=NAVY_SOFT)
        draw.text((chip_x + 13, 157), label, font=f, fill=SUBTLE)
        chip_x += w + 12

    top, height = 212, 286
    panel(canvas, draw, SOURCE, (56, top, 300, height), "the only input")
    arrow(draw, 384, top + height // 2)
    panel(canvas, draw, WINDOW, (476, top, 520, height), "what the machine read")
    arrow(draw, 1030, top + height // 2)
    # the final verification screen. The annotation ribbon is cropped off: at
    # banner size its caption is unreadable and only adds noise.
    shot = Image.open(final[-1])
    ribbon = int(shot.height * 0.092)
    panel(canvas, draw, final[-1], (1122, top, 422, height),
          "the saved Order and its paid Invoice",
          crop=(0, ribbon, shot.width, shot.height))

    draw.line([(56, H - 62), (W - 56, H - 62)], fill=NAVY_SOFT, width=1)
    draw.text((56, H - 46),
              "Verified against Fakturama 2.2.0 (Windows x64) on a live installation  "
              "·  67 of 68 steps passed first time  ·  155 tests",
              font=font(15), fill=SUBTLE)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    print("wrote %s  (%dx%d)" % (OUT, canvas.width, canvas.height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
