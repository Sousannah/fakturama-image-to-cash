"""Locale-tolerant formatting of values we push into Fakturama fields.

Fakturama renders dates and decimals according to the JVM locale, which is not
knowable ahead of time. Rather than assume, we read what the field already
contains and mimic it. That is why `order.date_field` is read before it is
written.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from ..logging_setup import get

log = get("flow.formats")

_PATTERNS = (
    # "Sep 18, 2026" - what Fakturama 2.2.0 renders under an English locale.
    # Found the hard way: writing "18.09.2026" into this field is silently
    # rejected and the field snaps back to today, which is exactly the kind of
    # failure that would otherwise reach a saved invoice.
    (re.compile(r"^[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}$"), "%b %d, %Y"),
    (re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$"), "%d %b %Y"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "%d.%m.%Y"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$"), "%d.%m.%Y"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y"),
)

DEFAULT_DATE_FORMAT = "%b %d, %Y"


def detect_date_format(sample: str) -> Optional[str]:
    """Infer a strftime pattern from whatever the field currently shows."""
    sample = (sample or "").strip()
    for pattern, fmt in _PATTERNS:
        if pattern.match(sample):
            return fmt
    return None


def format_date(value: date, sample: str = "") -> str:
    fmt = detect_date_format(sample) or DEFAULT_DATE_FORMAT
    text = value.strftime(fmt)
    # strftime zero-pads %d on Windows; the widget renders "Sep 8, 2026"
    if fmt == "%b %d, %Y":
        text = re.sub(r"\s0(\d),", r" \1,", text)
    return text


def detect_decimal_separator(sample: str) -> str:
    """',' for a German-style field, '.' otherwise."""
    sample = (sample or "").strip()
    if re.search(r"\d,\d{1,2}\b", sample):
        return ","
    return "."


def format_decimal(value: Decimal, sample: str = "", places: int = 2) -> str:
    text = ("%%.%df" % places) % Decimal(value)
    if detect_decimal_separator(sample) == ",":
        text = text.replace(".", ",")
    return text


def format_percent(value: Decimal, sample: str = "") -> str:
    """Percentages are written without the sign; Fakturama appends it."""
    normalised = Decimal(value).normalize()
    text = format(normalised, "f")
    if detect_decimal_separator(sample) == ",":
        text = text.replace(".", ",")
    return text


def parse_amount(text: str) -> Optional[Decimal]:
    """Read a currency/percentage label back into a Decimal, or None."""
    if text is None:
        return None
    s = str(text)
    # cells render amounts with a currency prefix - "USD 297.5", "$595.00",
    # "450,00 EUR" - and percentages with a trailing sign
    for junk in ("€", "£", "$", "EUR", "USD", "GBP", "CHF", "%", " ", " "):
        s = s.replace(junk, "")
    s = s.strip()
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rindex(",") > s.rindex(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except Exception:
        return None


def date_digits(value: date, fmt: str) -> str:
    """The digits to type into a segmented date widget, in ITS segment order.

    Fakturama's date control is a segmented date editor, not a text box.
    Writing a formatted string into it - by ValuePattern or by keystrokes -
    scrambles the segments ("Jul 14, 2026" came back as "Sep 20, 0026").
    What it does accept is a continuous run of digits typed from the first
    segment: each segment fills up and hands over to the next automatically.

    So we emit month/day/year digits in whatever order the widget displays
    them, which `fmt` (detected from the field's current contents) tells us.
    """
    parts = []
    for token in re.findall(r"%[a-zA-Z]", fmt):
        if token in ("%b", "%B", "%m"):
            parts.append("%02d" % value.month)
        elif token == "%d":
            parts.append("%02d" % value.day)
        elif token == "%Y":
            parts.append("%04d" % value.year)
        elif token == "%y":
            parts.append("%02d" % (value.year % 100))
    return "".join(parts)
