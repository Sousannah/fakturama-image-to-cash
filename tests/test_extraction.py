"""Normalisation and reconciliation - the part that must never be wrong."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from f2c.errors import ReconciliationError
from f2c.extract.pipeline import normalise, parse_date, _num
from f2c.extract.validate import reconcile, summary


def test_sample_normalises(sample_doc):
    d = sample_doc
    assert d.order_date == date(2026, 7, 14)
    assert d.external_ref == "WEB-2026-0714-A17"
    assert d.company == "Northstar Office GmbH"
    assert d.contact_first == "Marta"
    assert d.contact_last == "Klein"
    assert d.alias == "NORTHSTAR-BERLIN"
    assert d.billing.zip == "10117"
    assert d.delivery.zip == "10553"
    assert d.is_paid
    assert d.payment_date == date(2026, 7, 18)
    assert len(d.items) == 2


def test_sample_reconciles(sample_doc):
    assert reconcile(sample_doc) == []


def test_line_arithmetic(sample_doc):
    chair, mat = sample_doc.items
    # 2 x 250.00 x (1 - 10/100) = 450.00
    assert chair.computed_line_net == Decimal("450.00")
    # 3 x 40.00 = 120.00
    assert mat.computed_line_net == Decimal("120.00")


def test_product_gross_price_excludes_line_discount(sample_doc):
    """spec 3.9: gross = unit net x (1 + VAT/100); the line discount is NOT applied."""
    chair, mat = sample_doc.items
    assert chair.product_gross_price == Decimal("297.50")   # 250.00 x 1.19
    assert mat.product_gross_price == Decimal("47.60")      # 40.00 x 1.19


def test_vat_names(sample_doc):
    assert sample_doc.items[0].vat_name == "VAT 19%"


def test_payment_code_mapping(sample_doc):
    assert sample_doc.payment_code == "Credit transfer"


def test_delivery_differs_from_billing(sample_doc):
    assert not sample_doc.delivery_same_as_billing


def test_a_wrong_line_total_is_rejected(raw_sample):
    raw_sample["items"][0]["line_net"] = "500.00"
    doc = normalise(raw_sample)
    with pytest.raises(ReconciliationError) as exc:
        reconcile(doc)
    assert "line 1" in str(exc.value)


def test_a_wrong_grand_total_is_rejected(raw_sample):
    raw_sample["gross_total"] = "700.00"
    doc = normalise(raw_sample)
    with pytest.raises(ReconciliationError):
        reconcile(doc)


def test_paid_without_a_date_is_rejected(raw_sample):
    raw_sample["payment_date"] = ""
    doc = normalise(raw_sample)
    with pytest.raises(ReconciliationError) as exc:
        reconcile(doc)
    assert "payment date" in str(exc.value)


def test_unpaid_with_a_date_is_rejected(raw_sample):
    raw_sample["paid_status"] = "UNPAID"
    doc = normalise(raw_sample)
    with pytest.raises(ReconciliationError):
        reconcile(doc)


def test_blank_filler_rows_are_dropped(raw_sample):
    raw_sample["items"].append(
        {"sku": "", "description": "", "qty": "", "unit": "", "unit_net": "",
         "discount_pct": "", "vat_pct": "", "line_net": ""}
    )
    doc = normalise(raw_sample)
    assert len(doc.items) == 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("250.00", "250.00"),
        ("EUR 678.30", "678.30"),
        ("19%", "19"),
        ("", "0"),
        ("-", "0"),
    ],
)
def test_number_normalisation(raw, expected):
    assert _num(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-14", date(2026, 7, 14)),
        ("14.07.2026", date(2026, 7, 14)),
        ("", None),
    ],
)
def test_date_parsing(raw, expected):
    assert parse_date(raw) == expected


def test_summary_mentions_every_sku(sample_doc):
    text = summary(sample_doc)
    for item in sample_doc.items:
        assert item.sku in text
