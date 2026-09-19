"""Turn a raw run screenshot into an annotated figure.

A caption ribbon across the top says which clause of the written procedure the
screen belongs to and what to look at; highlight boxes ring the part of the
screen that matters, each with its own numbered badge and label.

Boxes are given as **fractions** of the image, not pixels, so an annotation
written against a 1920x1080 capture still lands correctly on a capture taken at
another resolution.

    from annotate import annotate
    annotate(src, dst, index=3, spec="2.1-2.3",
             title="Try to select the Debtor from the Order",
             note="The picker is the existence check - no row means create.",
             boxes=[(0.31, 0.22, 0.69, 0.55, "exact match on Company, Name, ZIP, City")])
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

NAVY = (14, 35, 56)
ACCENT = (46, 125, 209)
ACCENT_SOFT = (120, 175, 235)
WHITE = (255, 255, 255)
SUBTLE = (159, 182, 206)
AMBER = (217, 119, 6)

Box = Tuple[float, float, float, float, str]


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:  # very old Pillow
        return font.getsize(text)[0]


def _wrap(draw, text: str, font, max_width: int) -> Sequence[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if _text_width(draw, trial, font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def annotate(
    src: Path,
    dst: Path,
    index: Optional[int] = None,
    spec: str = "",
    title: str = "",
    note: str = "",
    boxes: Iterable[Box] = (),
    status: str = "",
    max_width: int = 1600,
) -> Path:
    src, dst = Path(src), Path(dst)
    image = Image.open(src).convert("RGB")

    if max_width and image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)

    width, height = image.size
    scale = width / 1600.0

    ribbon_h = int(58 * scale)
    note_font = _font(max(11, int(15 * scale)))
    title_font = _font(max(13, int(20 * scale)), bold=True)
    spec_font = _font(max(10, int(13 * scale)), bold=True)
    badge_font = _font(max(11, int(16 * scale)), bold=True)
    label_font = _font(max(10, int(14 * scale)), bold=True)

    pad = int(18 * scale)
    line_h = int(21 * scale)
    note_lines = []
    if note:
        probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        note_lines = list(_wrap(probe, note, note_font, width - 2 * pad))[:2]
    note_h = (len(note_lines) * line_h + int(12 * scale)) if note_lines else 0

    canvas = Image.new("RGB", (width, height + ribbon_h + note_h), NAVY)
    draw = ImageDraw.Draw(canvas)
    # --- ribbon ---------------------------------------------------------- #
    left = pad
    if index is not None:
        size = int(30 * scale)
        top = (ribbon_h - size) // 2
        draw.rectangle([left, top, left + size, top + size], fill=ACCENT)
        label = str(index)
        draw.text(
            (left + size / 2 - _text_width(draw, label, badge_font) / 2, top + size * 0.18),
            label, font=badge_font, fill=WHITE,
        )
        left += size + int(14 * scale)

    if spec:
        chip_w = _text_width(draw, spec, spec_font) + int(16 * scale)
        chip_h = int(20 * scale)
        top = (ribbon_h - chip_h) // 2
        draw.rectangle([left, top, left + chip_w, top + chip_h], outline=ACCENT_SOFT, width=max(1, int(scale)))
        draw.text((left + int(8 * scale), top + int(3 * scale)), spec, font=spec_font, fill=ACCENT_SOFT)
        left += chip_w + int(14 * scale)

    if title:
        draw.text((left, (ribbon_h - int(22 * scale)) / 2), title, font=title_font, fill=WHITE)

    if status:
        colour = {"ok": (60, 190, 120), "manual-review": AMBER}.get(status, SUBTLE)
        w = _text_width(draw, status.upper(), spec_font)
        draw.text((width - pad - w, (ribbon_h - int(14 * scale)) / 2), status.upper(),
                  font=spec_font, fill=colour)

    for i, line in enumerate(note_lines):
        draw.text((pad, ribbon_h + int(2 * scale) + i * line_h), line, font=note_font, fill=SUBTLE)

    # --- the screenshot itself -------------------------------------------- #
    top_of_image = ribbon_h + note_h
    canvas.paste(image, (0, top_of_image))

    # --- highlight boxes --------------------------------------------------- #
    for n, (x0, y0, x1, y1, label) in enumerate(boxes, start=1):
        px0, py0 = int(x0 * width), int(y0 * height) + top_of_image
        px1, py1 = int(x1 * width), int(y1 * height) + top_of_image
        thickness = max(2, int(3 * scale))
        draw.rectangle([px0, py0, px1, py1], outline=ACCENT, width=thickness)

        if label:
            badge = int(22 * scale)
            bx, by = px0, max(top_of_image, py0 - badge - int(4 * scale))
            text_w = _text_width(draw, label, label_font)
            draw.rectangle([bx, by, bx + badge + text_w + int(14 * scale), by + badge], fill=ACCENT)
            draw.text((bx + badge / 2 - _text_width(draw, str(n), label_font) / 2, by + int(3 * scale)),
                      str(n), font=label_font, fill=WHITE)
            draw.text((bx + badge + int(6 * scale), by + int(3 * scale)), label, font=label_font, fill=WHITE)

    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, quality=92)
    return dst
