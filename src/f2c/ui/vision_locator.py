"""Tier 3 / Tier 4 grounding: pixels, but never *fixed* pixels.

Every function here takes a `Rect` that came from a UIA element and returns
`Rect`s in screen coordinates. Nothing in this module knows a constant screen
position: the search region is always resolved at runtime from the accessibility
tree, and the offsets are always relative to that region.

That is what satisfies the brief's "no hardcoded coordinates, no fixed UI
layout" requirement while still being able to drive a custom-drawn canvas
(Fakturama's item grid) that exposes no accessibility children at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..config import SETTINGS
from ..logging_setup import get
from .uia import Rect

log = get("ui.vision")


# --------------------------------------------------------------------------- #
# screenshots
# --------------------------------------------------------------------------- #
def grab(region: Optional[Rect] = None):
    """Return a PIL.Image of the screen or of `region` (screen coordinates)."""
    from PIL import Image

    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            if region is None:
                shot = sct.grab(sct.monitors[0])
            else:
                shot = sct.grab(
                    {
                        "left": region.left,
                        "top": region.top,
                        "width": max(1, region.width),
                        "height": max(1, region.height),
                    }
                )
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as exc:
        log.debug("mss unavailable (%s); falling back to PIL.ImageGrab", exc)

    from PIL import ImageGrab

    if region is None:
        return ImageGrab.grab()
    return ImageGrab.grab(bbox=region.as_tuple())


def save_screenshot(path: Path, region: Optional[Rect] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    grab(region).save(path)
    return path


# --------------------------------------------------------------------------- #
# Tier 3: icon template matching, scoped to a UIA rect
# --------------------------------------------------------------------------- #
@dataclass
class Match:
    rect: Rect
    score: float


def match_template(
    template_path: Path,
    region: Rect,
    threshold: Optional[float] = None,
    scales: Sequence[float] = (1.0, 0.9, 1.1, 0.8, 1.25),
) -> List[Match]:
    """Find every occurrence of a small icon inside `region`.

    Multi-scale, because Fakturama renders toolbar icons at different sizes
    under different display scaling. Returns screen-coordinate rects, sorted by
    score descending.
    """
    import cv2
    import numpy as np

    threshold = SETTINGS.template_threshold if threshold is None else threshold

    haystack = np.array(grab(region))
    haystack = cv2.cvtColor(haystack, cv2.COLOR_RGB2GRAY)

    template0 = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
    if template0 is None:
        raise FileNotFoundError("icon template not found: %s" % template_path)

    found: List[Match] = []
    for scale in scales:
        if scale == 1.0:
            tmpl = template0
        else:
            w = max(4, int(template0.shape[1] * scale))
            h = max(4, int(template0.shape[0] * scale))
            tmpl = cv2.resize(template0, (w, h), interpolation=cv2.INTER_AREA)
        if tmpl.shape[0] > haystack.shape[0] or tmpl.shape[1] > haystack.shape[1]:
            continue

        res = cv2.matchTemplate(haystack, tmpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for x, y in zip(xs, ys):
            found.append(
                Match(
                    rect=Rect(
                        region.left + int(x),
                        region.top + int(y),
                        region.left + int(x) + tmpl.shape[1],
                        region.top + int(y) + tmpl.shape[0],
                    ),
                    score=float(res[y, x]),
                )
            )

    return _dedupe(found)


def _dedupe(matches: List[Match], iou: float = 0.3) -> List[Match]:
    """Simple non-maximum suppression across scales."""
    out: List[Match] = []
    for m in sorted(matches, key=lambda m: -m.score):
        if all(not _overlaps(m.rect, k.rect, iou) for k in out):
            out.append(m)
    return out


def _overlaps(a: Rect, b: Rect, iou_threshold: float) -> bool:
    ix = max(0, min(a.right, b.right) - max(a.left, b.left))
    iy = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
    inter = ix * iy
    if inter == 0:
        return False
    union = a.area + b.area - inter
    return union > 0 and (inter / union) >= iou_threshold


def pick_match(matches: List[Match], strategy: str = "best") -> Optional[Match]:
    """`best` | `topmost` | `bottommost` | `leftmost` | `rightmost`.

    `topmost` is what disambiguates spec 2.1 and 3.2: the *upper* existing-record
    selector icon versus the *lower* green + that starts a new record.
    """
    if not matches:
        return None
    if strategy == "topmost":
        return min(matches, key=lambda m: (m.rect.top, -m.score))
    if strategy == "bottommost":
        return max(matches, key=lambda m: (m.rect.top, m.score))
    if strategy == "leftmost":
        return min(matches, key=lambda m: (m.rect.left, -m.score))
    if strategy == "rightmost":
        return max(matches, key=lambda m: (m.rect.left, m.score))
    return max(matches, key=lambda m: m.score)


# --------------------------------------------------------------------------- #
# Tier 4: OCR words inside a UIA rect
# --------------------------------------------------------------------------- #
@dataclass
class Word:
    text: str
    rect: Rect
    conf: float = 0.0


def ocr_words(region: Rect, upscale: int = 2) -> List[Word]:
    """OCR `region` and return words with screen-coordinate boxes.

    Upscaling matters: UI text is small and both engines do noticeably better on
    a 2x image than on native pixels.
    """
    img = grab(region)
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale))

    words = _ocr_tesseract(img, region, upscale)
    if words is None:
        words = _ocr_easyocr(img, region, upscale)
    if words is None:
        log.warning("no OCR backend available; tier-4 grounding is disabled")
        return []
    return words


def _ocr_tesseract(img, region: Rect, upscale: int) -> Optional[List[Word]]:
    try:
        import pytesseract  # type: ignore
        from pytesseract import Output  # type: ignore
    except Exception:
        return None
    try:
        data = pytesseract.image_to_data(img, output_type=Output.DICT)
    except Exception as exc:
        log.debug("tesseract failed: %s", exc)
        return None

    words: List[Word] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        words.append(
            Word(
                text=text,
                rect=Rect(
                    region.left + x // upscale,
                    region.top + y // upscale,
                    region.left + (x + w) // upscale,
                    region.top + (y + h) // upscale,
                ),
                conf=conf,
            )
        )
    return words


def _ocr_easyocr(img, region: Rect, upscale: int) -> Optional[List[Word]]:
    try:
        import easyocr  # type: ignore
        import numpy as np
    except Exception:
        return None
    try:
        reader = _easyocr_reader()
        results = reader.readtext(np.array(img))
    except Exception as exc:
        log.debug("easyocr failed: %s", exc)
        return None

    words: List[Word] = []
    for box, text, conf in results:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        words.append(
            Word(
                text=str(text).strip(),
                rect=Rect(
                    region.left + int(min(xs)) // upscale,
                    region.top + int(min(ys)) // upscale,
                    region.left + int(max(xs)) // upscale,
                    region.top + int(max(ys)) // upscale,
                ),
                conf=float(conf) * 100.0,
            )
        )
    return words


_READER = None


def _easyocr_reader():
    global _READER
    if _READER is None:
        import easyocr  # type: ignore

        _READER = easyocr.Reader(["en", "de"], gpu=False, verbose=False)
    return _READER


def find_text(region: Rect, needle: str, exact: bool = False) -> List[Word]:
    """Locate a text run inside a region, merging words on the same line."""
    needle_cf = needle.strip().casefold()
    words = ocr_words(region)
    hits = []

    for w in words:
        t = w.text.strip().casefold()
        if (exact and t == needle_cf) or (not exact and needle_cf in t):
            hits.append(w)

    if hits or " " not in needle_cf:
        return hits

    # multi-word needle: merge words that share a line, then search the line
    for line_words in group_lines(words):
        joined = " ".join(w.text for w in line_words).casefold()
        if needle_cf in joined:
            hits.append(
                Word(
                    text=" ".join(w.text for w in line_words),
                    rect=Rect(
                        min(w.rect.left for w in line_words),
                        min(w.rect.top for w in line_words),
                        max(w.rect.right for w in line_words),
                        max(w.rect.bottom for w in line_words),
                    ),
                    conf=min(w.conf for w in line_words),
                )
            )
    return hits


def group_lines(words: Sequence[Word], tolerance_ratio: float = 0.6) -> List[List[Word]]:
    """Cluster words into visual lines by vertical overlap."""
    remaining = sorted(words, key=lambda w: (w.rect.top, w.rect.left))
    lines: List[List[Word]] = []
    for w in remaining:
        placed = False
        for line in lines:
            ref = line[0].rect
            tol = max(2, int(ref.height * tolerance_ratio))
            if abs(w.rect.center[1] - ref.center[1]) <= tol:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.rect.left)
    lines.sort(key=lambda ln: ln[0].rect.top)
    return lines


def detect_grid_lines(region: Rect, min_ratio: float = 0.6) -> Tuple[List[int], List[int]]:
    """Detect a custom-drawn table's rulings inside `region`.

    Returns (column_x, row_y) in *screen* coordinates. Used by `grid.py` as the
    preferred way to reconstruct Fakturama's item table, because ruling lines
    are far more reliable than clustering OCR text when cells are empty.
    """
    import cv2
    import numpy as np

    img = np.array(grab(region))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, -2
    )
    h, w = binary.shape

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(4, int(h * min_ratio))))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(4, int(w * min_ratio)), 1))
    verticals = cv2.dilate(cv2.erode(binary, v_kernel), v_kernel)
    horizontals = cv2.dilate(cv2.erode(binary, h_kernel), h_kernel)

    col_x = _peaks(verticals.sum(axis=0), min_gap=6)
    row_y = _peaks(horizontals.sum(axis=1), min_gap=6)
    return (
        [region.left + int(x) for x in col_x],
        [region.top + int(y) for y in row_y],
    )


def _peaks(projection, min_gap: int = 6) -> List[int]:
    import numpy as np

    threshold = projection.max() * 0.5 if projection.max() else 0
    idx = [int(i) for i, v in enumerate(projection) if v >= threshold and v > 0]
    if not idx:
        return []
    groups, current = [], [idx[0]]
    for i in idx[1:]:
        if i - current[-1] <= min_gap:
            current.append(i)
        else:
            groups.append(current)
            current = [i]
    groups.append(current)
    return [int(np.mean(g)) for g in groups]


def rulings(region: Rect, min_ratio: float = 0.35, threshold_ratio: float = 0.5):
    """Detect a table's drawn separator lines inside `region`.

    Returns (column_x, row_y) in SCREEN coordinates.

    Separate from `detect_grid_lines` because the item table needs a much lower
    `min_ratio`: its column rulings only span the rows that exist, so demanding
    a line across most of the control's height finds nothing once the grid has
    empty space below the data.
    """
    import cv2
    import numpy as np

    image = np.array(grab(region))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, -2
    )

    def peaks(mask, axis):
        projection = mask.sum(axis=axis)
        top = projection.max()
        if not top:
            return []
        hits = [i for i, v in enumerate(projection) if v >= top * threshold_ratio and v > 0]
        if not hits:
            return []
        groups, current = [], [hits[0]]
        for i in hits[1:]:
            if i - current[-1] <= 6:
                current.append(i)
            else:
                groups.append(current)
                current = [i]
        groups.append(current)
        return [int(np.mean(g)) for g in groups]

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, int(height * min_ratio))))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(width * min_ratio)), 1))
    vertical = cv2.dilate(cv2.erode(binary, v_kernel), v_kernel)
    horizontal = cv2.dilate(cv2.erode(binary, h_kernel), h_kernel)

    return (
        [region.left + x for x in peaks(vertical, 0)],
        [region.top + y for y in peaks(horizontal, 1)],
    )
