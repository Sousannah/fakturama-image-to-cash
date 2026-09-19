"""OCR: the order image -> positioned text.

This is the *primary* extractor in this build. The Groq account available here
serves no vision model, so the pipeline is OCR-first: Python OCR reads the
pixels, and a text LLM turns the reading into structured fields
(`f2c.extract.llm`). That split is arguably the better engineering anyway - the
transcription step is reproducible and inspectable, and the model never sees
pixels it could hallucinate over.

Two lessons are baked into the defaults below, both found on the sample order:

* **Upscale before recognising.** At native resolution EasyOCR silently dropped
  both single-digit quantities ("2" and "3"). At 2x with lowered detection
  thresholds it reads them at confidence 1.00. A dropped quantity is a wrong
  invoice, so this is not a tuning nicety.
* **Keep coordinates.** Feeding the LLM a flat string loses the table. We
  reconstruct visual lines and columns and hand over a layout-preserving
  rendering, so "which number is the discount" is a reading question rather
  than a guessing one.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..logging_setup import get

log = get("extract.ocr")

#: Where the Tesseract binary usually lives on Windows when it is not on PATH.
_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

#: Detection is run on an upscaled copy; see the module docstring.
DEFAULT_UPSCALE = 2
DEFAULT_TEXT_THRESHOLD = 0.5
DEFAULT_LOW_TEXT = 0.3
DEFAULT_MIN_SIZE = 5


@dataclass
class OcrWord:
    text: str
    x: int
    y: int
    right: int
    bottom: int
    conf: float

    @property
    def cx(self) -> int:
        return (self.x + self.right) // 2

    @property
    def cy(self) -> int:
        return (self.y + self.bottom) // 2

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.y)


@dataclass
class OcrResult:
    words: List[OcrWord]
    engine: str
    width: int
    height: int

    def lines(self, tolerance_ratio: float = 0.6) -> List[List[OcrWord]]:
        return group_lines(self.words, tolerance_ratio)

    def plain_text(self) -> str:
        return "\n".join(" ".join(w.text for w in line) for line in self.lines())

    def layout_text(self) -> str:
        return render_layout(self.lines())

    def mean_confidence(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else 0.0


# --------------------------------------------------------------------------- #
# engines
# --------------------------------------------------------------------------- #
_READER = None


def _easyocr_reader(languages: Sequence[str] = ("en",)):
    global _READER
    if _READER is None:
        import easyocr  # type: ignore

        log.info("loading EasyOCR models (first run downloads them)")
        _READER = easyocr.Reader(list(languages), gpu=False, verbose=False)
    return _READER


def read_easyocr(
    image_path: Path,
    upscale: int = DEFAULT_UPSCALE,
    languages: Sequence[str] = ("en",),
) -> Optional[OcrResult]:
    try:
        import numpy as np
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        log.debug("Pillow/numpy unavailable: %s", exc)
        return None

    try:
        reader = _easyocr_reader(languages)
    except Exception as exc:
        log.debug("EasyOCR unavailable: %s", exc)
        return None

    img = Image.open(image_path).convert("RGB")
    scaled = (
        img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
        if upscale > 1
        else img
    )

    try:
        raw = reader.readtext(
            np.array(scaled),
            detail=1,
            paragraph=False,
            text_threshold=DEFAULT_TEXT_THRESHOLD,
            low_text=DEFAULT_LOW_TEXT,
            mag_ratio=1.5,
            min_size=DEFAULT_MIN_SIZE,
        )
    except Exception as exc:
        log.warning("EasyOCR failed: %s", exc)
        return None

    words: List[OcrWord] = []
    for box, text, conf in raw:
        text = str(text).strip()
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        words.append(
            OcrWord(
                text=text,
                x=int(min(xs)) // upscale,
                y=int(min(ys)) // upscale,
                right=int(max(xs)) // upscale,
                bottom=int(max(ys)) // upscale,
                conf=float(conf),
            )
        )
    log.info("EasyOCR read %d words (mean conf %.2f)", len(words),
             sum(w.conf for w in words) / len(words) if words else 0.0)
    return OcrResult(words=words, engine="easyocr", width=img.width, height=img.height)


def _tesseract_cmd() -> Optional[str]:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _TESSERACT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def read_tesseract(image_path: Path, upscale: int = DEFAULT_UPSCALE) -> Optional[OcrResult]:
    try:
        import pytesseract  # type: ignore
        from PIL import Image
        from pytesseract import Output  # type: ignore
    except Exception as exc:
        log.debug("pytesseract unavailable: %s", exc)
        return None

    cmd = _tesseract_cmd()
    if not cmd:
        log.debug("no tesseract binary found")
        return None
    pytesseract.pytesseract.tesseract_cmd = cmd

    img = Image.open(image_path).convert("RGB")
    scaled = (
        img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
        if upscale > 1
        else img
    )
    try:
        data = pytesseract.image_to_data(scaled, output_type=Output.DICT)
    except Exception as exc:
        log.debug("tesseract failed: %s", exc)
        return None

    words: List[OcrWord] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i]) / 100.0
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0:
            continue
        x, y = int(data["left"][i]), int(data["top"][i])
        w, h = int(data["width"][i]), int(data["height"][i])
        words.append(
            OcrWord(
                text=text,
                x=x // upscale,
                y=y // upscale,
                right=(x + w) // upscale,
                bottom=(y + h) // upscale,
                conf=conf,
            )
        )
    log.info("Tesseract read %d words", len(words))
    return OcrResult(words=words, engine="tesseract", width=img.width, height=img.height)


def read_image(image_path: Path, engine: str = "auto") -> OcrResult:
    """Run OCR. `engine` is auto | easyocr | tesseract."""
    image_path = Path(image_path)
    result = None
    if engine in ("auto", "easyocr"):
        result = read_easyocr(image_path)
    if result is None and engine in ("auto", "tesseract"):
        result = read_tesseract(image_path)
    if result is None:
        raise RuntimeError(
            "no OCR engine available - install easyocr, or Tesseract plus pytesseract"
        )
    return result


# --------------------------------------------------------------------------- #
# layout reconstruction
# --------------------------------------------------------------------------- #
def group_lines(words: Sequence[OcrWord], tolerance_ratio: float = 0.6) -> List[List[OcrWord]]:
    """Cluster words into visual lines by vertical proximity."""
    lines: List[List[OcrWord]] = []
    for w in sorted(words, key=lambda w: (w.y, w.x)):
        placed = False
        for line in lines:
            ref = line[0]
            tolerance = max(4, int(ref.height * tolerance_ratio))
            if abs(w.cy - ref.cy) <= tolerance:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.x)
    lines.sort(key=lambda line: min(w.y for w in line))
    return lines


def render_layout(lines: Sequence[Sequence[OcrWord]], column_width: int = 12) -> str:
    """Render lines with their horizontal positions preserved.

    The LLM reads a monospace approximation of the page, so a value that sits
    under the "Disc." heading still looks like it sits under the "Disc."
    heading. Without this the item table collapses into an ambiguous run of
    numbers.
    """
    if not lines:
        return ""
    all_words = [w for line in lines for w in line]
    max_x = max(w.right for w in all_words) or 1
    scale = 110.0 / max_x  # target ~110 characters wide

    out: List[str] = []
    for line in lines:
        row = ""
        for w in line:
            column = int(w.x * scale)
            if column < len(row):
                column = len(row) + 1
            row += " " * (column - len(row)) + w.text
        out.append(row.rstrip())
    return "\n".join(out)


def low_confidence_words(result: OcrResult, threshold: float = 0.55) -> List[OcrWord]:
    return [w for w in result.words if w.conf < threshold]


# --------------------------------------------------------------------------- #
# cross-check (used when a vision model did the extraction instead)
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).replace(",", "").casefold()


def cross_check(doc, image_path: Path) -> Tuple[bool, List[str]]:
    """Assert every extracted value literally appears in the OCR text."""
    try:
        result = read_image(Path(image_path))
    except Exception as exc:
        log.info("OCR cross-check skipped: %s", exc)
        return False, []

    hay = _norm(result.plain_text())
    misses: List[str] = []

    def check(label: str, value) -> None:
        if value in (None, ""):
            return
        if _norm(value) not in hay:
            misses.append("%s=%r not found in OCR text" % (label, value))

    check("external_ref", doc.external_ref)
    check("company", doc.company)
    check("billing_zip", doc.billing.zip)
    check("delivery_zip", doc.delivery.zip)
    check("net_total", doc.net_total)
    check("vat_total", doc.vat_total)
    check("gross_total", doc.gross_total)
    for item in doc.items:
        check("sku", item.sku)
        check("unit_net", item.unit_net)
        check("line_net", item.line_net)

    if misses:
        log.warning("OCR cross-check: %d unconfirmed value(s)", len(misses))
        for m in misses:
            log.warning("  %s", m)
    else:
        log.info("OCR cross-check: every extracted value appears in the OCR text")
    return True, misses
