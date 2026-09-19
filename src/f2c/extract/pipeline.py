"""image -> OrderDoc, with caching, normalisation, reconciliation."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import SETTINGS
from ..errors import ExtractionError
from ..logging_setup import get
from ..models import Address, Item, OrderDoc
from . import validate as _validate

log = get("extract")

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y")


def image_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def cache_path(path: Path) -> Path:
    return SETTINGS.out_dir / "extraction-cache" / ("%s.json" % image_hash(path))


def parse_date(raw: Any) -> Optional[date]:
    if raw in (None, "", "-"):
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ExtractionError("unparseable date: %r" % raw)


def _num(raw: Any, default: str = "0") -> str:
    """Normalise a transcribed number string: strip currency, %, spaces, commas."""
    if raw in (None, "", "-"):
        return default
    s = str(raw).strip()
    for junk in ("EUR", "USD", "GBP", "%", " ", " "):
        s = s.replace(junk, "")
    # 1.234,56 (de) -> 1234.56 ; 1,234.56 (en) -> 1234.56
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rindex(",") > s.rindex(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) in (1, 2) else s.replace(",", "")
    return s or default


def _address(d: Dict[str, Any]) -> Address:
    d = d or {}
    return Address(
        name=d.get("name", "") or "",
        street=d.get("street", "") or "",
        zip=str(d.get("zip", "") or ""),
        city=d.get("city", "") or "",
        country=d.get("country", "") or "",
    )


def normalise(raw: Dict[str, Any]) -> OrderDoc:
    """Raw tool output -> typed, normalised OrderDoc (no reconciliation yet)."""
    items = []
    for row in raw.get("items", []) or []:
        if not (row.get("sku") or "").strip():
            continue  # filler row
        items.append(
            Item(
                sku=(row.get("sku") or "").strip(),
                description=(row.get("description") or "").strip(),
                qty=_num(row.get("qty"), "0"),
                unit=(row.get("unit") or "pcs").strip(),
                unit_net=_num(row.get("unit_net"), "0"),
                discount_pct=_num(row.get("discount_pct"), "0"),
                vat_pct=_num(row.get("vat_pct"), "0"),
                line_net=_num(row.get("line_net"), "0"),
            )
        )

    return OrderDoc(
        order_date=parse_date(raw.get("order_date")),
        external_ref=(raw.get("external_ref") or "").strip(),
        customer_id_source=(raw.get("customer_id_source") or "").strip(),
        currency=(raw.get("currency") or "EUR").strip(),
        company=(raw.get("company") or "").strip(),
        contact_first=(raw.get("contact_first") or "").strip(),
        contact_last=(raw.get("contact_last") or "").strip(),
        salutation=(raw.get("salutation") or "").strip(),
        alias=(raw.get("alias") or "").strip(),
        email=(raw.get("email") or "").strip(),
        phone=(raw.get("phone") or "").strip(),
        billing=_address(raw.get("billing")),
        delivery=_address(raw.get("delivery")),
        payment_method=(raw.get("payment_method") or "").strip(),
        paid_status=(raw.get("paid_status") or "UNPAID").strip(),
        payment_date=parse_date(raw.get("payment_date")),
        items=items,
        net_total=_num(raw.get("net_total"), "0"),
        vat_total=_num(raw.get("vat_total"), "0"),
        gross_total=_num(raw.get("gross_total"), "0"),
    )


def extract_raw(image_path: Path, mode: Optional[str] = None) -> Dict[str, Any]:
    """Run the configured extraction mode and return the raw tool payload.

    ``ocr``    - Python OCR reads the image, a text model structures the result.
                 Default here: the Groq account in use has no vision model, and
                 the OCR transcription is saved alongside the extraction so a
                 disputed field can be traced back to what was actually read.
    ``vision`` - the image goes straight to a multimodal model.
    """
    from . import llm

    mode = (mode or SETTINGS.extraction_mode).lower()

    if mode == "vision":
        log.info("extraction mode: vision (%s/%s)", SETTINGS.provider, SETTINGS.model)
        return llm.structure_from_image(image_path)

    from .ocr import read_image

    log.info("extraction mode: ocr -> %s/%s", SETTINGS.provider, SETTINGS.model)
    result = read_image(image_path, engine=SETTINGS.ocr_engine)
    layout = result.layout_text()

    debug_dir = SETTINGS.out_dir / "extraction-cache"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / ("%s.ocr.txt" % image_hash(image_path))).write_text(layout, encoding="utf-8")
    log.info(
        "OCR (%s): %d words, mean confidence %.2f",
        result.engine,
        len(result.words),
        result.mean_confidence(),
    )

    raw = llm.structure(layout)
    raw["_ocr_engine"] = result.engine
    raw["_ocr_words"] = len(result.words)
    return raw


def extract_order(
    image_path: Path,
    use_cache: bool = True,
    ocr_check: bool = False,
    strict: bool = True,
    from_json: Optional[Path] = None,
    mode: Optional[str] = None,
) -> OrderDoc:
    """The public entry point. Returns a reconciled OrderDoc or raises."""
    if from_json is not None:
        raw = json.loads(Path(from_json).read_text(encoding="utf-8"))
        log.info("loaded extraction from %s", from_json)
    else:
        cp = cache_path(image_path)
        if use_cache and cp.exists():
            raw = json.loads(cp.read_text(encoding="utf-8"))
            log.info("reusing cached extraction %s", cp)
        else:
            raw = extract_raw(image_path, mode=mode)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            log.info("cached extraction -> %s", cp)

    doc = normalise(raw)

    if ocr_check:
        from .ocr import cross_check

        cross_check(doc, image_path)

    _validate.reconcile(doc, strict=strict)
    log.info("extraction reconciled\n%s", _validate.summary(doc))
    return doc
