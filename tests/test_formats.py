"""Locale mimicry: we copy the format the field is already using."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from f2c.flow.formats import (
    detect_date_format,
    detect_decimal_separator,
    format_date,
    format_decimal,
    format_percent,
    parse_amount,
)


@pytest.mark.parametrize(
    "sample,expected",
    [
        ("01.02.2026", "%d.%m.%Y"),
        ("2026-02-01", "%Y-%m-%d"),
        ("02/01/2026", "%m/%d/%Y"),
        ("", None),
        ("not a date", None),
    ],
)
def test_date_format_detection(sample, expected):
    assert detect_date_format(sample) == expected


def test_date_formatting_follows_the_field():
    d = date(2026, 7, 14)
    assert format_date(d, "01.02.2026") == "14.07.2026"
    assert format_date(d, "2026-02-01") == "2026-07-14"
    # Fakturama 2.2.0 under an English locale renders "Sep 18, 2026"
    assert format_date(d, "Sep 18, 2026") == "Jul 14, 2026"
    assert format_date(d, "") == "Jul 14, 2026"


def test_day_is_not_zero_padded_in_the_month_name_format():
    """The widget shows "Sep 8, 2026", not "Sep 08, 2026"."""
    assert format_date(date(2026, 9, 8), "Sep 18, 2026") == "Sep 8, 2026"


def test_decimal_separator_detection():
    assert detect_decimal_separator("1.234,56") == ","
    assert detect_decimal_separator("0,00") == ","
    assert detect_decimal_separator("1,234.56") == "."
    assert detect_decimal_separator("") == "."


def test_decimal_formatting_follows_the_field():
    assert format_decimal(Decimal("297.5"), "0,00") == "297,50"
    assert format_decimal(Decimal("297.5"), "0.00") == "297.50"
    assert format_decimal(Decimal("0"), "") == "0.00"


def test_percent_formatting_drops_trailing_zeros():
    assert format_percent(Decimal("19")) == "19"
    assert format_percent(Decimal("19.00")) == "19"
    assert format_percent(Decimal("7.5")) == "7.5"
    assert format_percent(Decimal("7.5"), "0,00") == "7,5"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("450,00 €", Decimal("450.00")),
        ("EUR 678.30", Decimal("678.30")),
        ("1.234,56", Decimal("1234.56")),
        ("19 %", Decimal("19")),
        ("", None),
        ("   ", None),
        ("n/a", None),
    ],
)
def test_amount_parsing(text, expected):
    assert parse_amount(text) == expected
