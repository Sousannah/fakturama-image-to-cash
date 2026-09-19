"""The decision rules the spec is strict about: exact match and reuse."""
from __future__ import annotations

from f2c.flow.dialogs import cells_contain_any, cells_match_all, classify
from f2c.flow.vat import parse_vat_rows
from f2c.models import DebtorMatch, ProductMatch


# --- spec 2.3: a Debtor is exact only on all five visible fields ----------- #
def test_debtor_exact_requires_every_field(sample_doc):
    row = DebtorMatch(
        company="Northstar Office GmbH",
        first_name="Marta",
        name="Klein",
        zip="10117",
        city="Berlin",
    )
    assert row.is_exact_for(sample_doc)


def test_debtor_with_a_different_zip_is_not_exact(sample_doc):
    row = DebtorMatch(
        company="Northstar Office GmbH",
        first_name="Marta",
        name="Klein",
        zip="10553",
        city="Berlin",
    )
    assert not row.is_exact_for(sample_doc)


def test_debtor_match_is_case_insensitive(sample_doc):
    row = DebtorMatch(
        company="northstar office gmbh",
        first_name="marta",
        name="KLEIN",
        zip="10117",
        city="berlin",
    )
    assert row.is_exact_for(sample_doc)


# --- whole-cell matching --------------------------------------------------- #
def test_cells_match_all_is_whole_cell_not_substring():
    row = ["Northstar Office GmbH", "Marta", "Klein", "101170", "Berlin"]
    assert not cells_match_all(row, ["Northstar Office GmbH", "10117"])
    row2 = ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
    assert cells_match_all(row2, ["Northstar Office GmbH", "10117"])


def test_empty_required_values_never_disqualify():
    row = ["Acme", "", "Smith", "12345", "Berlin"]
    assert cells_match_all(row, ["Acme", "", "Smith"])


def test_classify_splits_exact_and_partial(sample_doc):
    rows = [
        ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"],
        ["Northstar Office GmbH", "Jonas", "Weber", "10553", "Berlin"],
        ["Unrelated AG", "Ann", "Meier", "99999", "Hamburg"],
    ]
    required = [
        sample_doc.company,
        sample_doc.contact_first,
        sample_doc.contact_last,
        sample_doc.billing.zip,
        sample_doc.billing.city,
    ]
    exact, partial = classify(rows, required, [sample_doc.company])
    assert exact == [0]
    assert partial == [1]


def test_classify_reports_two_exact_rows_as_ambiguous(sample_doc):
    row = ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
    required = [
        sample_doc.company,
        sample_doc.contact_first,
        sample_doc.contact_last,
        sample_doc.billing.zip,
        sample_doc.billing.city,
    ]
    exact, _ = classify([row, list(row)], required, [sample_doc.company])
    assert len(exact) == 2      # the flow stops for manual review on this


# --- spec 3.3: Products match on exact SKU --------------------------------- #
def test_product_matches_on_exact_sku(sample_doc):
    item = sample_doc.items[0]
    assert ProductMatch(item_number="CHR-ERG-01").is_exact_for(item)
    assert not ProductMatch(item_number="CHR-ERG-010").is_exact_for(item)
    assert not ProductMatch(item_number="CHR-ERG").is_exact_for(item)


# --- spec 3.5: a VAT row is reusable only when name, value and code agree --- #
def test_vat_row_reuse_rules(sample_doc):
    item = sample_doc.items[0]           # 19%
    rows = parse_vat_rows([["VAT 19%", "19.00 %", "S (Standard rate)"]])
    assert rows[0].is_reusable_for(item)


def test_vat_row_with_the_wrong_value_is_not_reusable(sample_doc):
    item = sample_doc.items[0]
    rows = parse_vat_rows([["VAT 19%", "7.00 %", "S (Standard rate)"]])
    assert not rows[0].is_reusable_for(item)


def test_vat_row_with_a_reduced_rate_code_is_not_reusable(sample_doc):
    item = sample_doc.items[0]
    rows = parse_vat_rows([["VAT 19%", "19.00 %", "AA (Lower rate)"]])
    assert not rows[0].is_reusable_for(item)


def test_vat_row_with_a_different_name_is_not_reusable(sample_doc):
    item = sample_doc.items[0]
    rows = parse_vat_rows([["Umsatzsteuer 19%", "19.00 %", "S (Standard rate)"]])
    assert not rows[0].is_reusable_for(item)


# --- clipped columns ------------------------------------------------------- #
def test_a_clipped_company_cell_still_matches():
    """The selector dialogs clip to the column width: "Northstar Office GmbH"
    is displayed as "Northstar Office"."""
    from f2c.flow.dialogs import cell_matches

    assert cell_matches("Northstar Office", "Northstar Office GmbH", allow_clipped=True)
    assert cell_matches("Northstar Office GmbH", "Northstar Office GmbH")
    # not tolerated unless the field is declared clippable
    assert not cell_matches("Northstar Office", "Northstar Office GmbH")


def test_a_short_prefix_is_not_treated_as_clipped():
    from f2c.flow.dialogs import cell_matches

    assert not cell_matches("North", "Northstar Office GmbH", allow_clipped=True)
    # even opted in, a city must not match a longer district name
    assert not cells_match_all(["Berlin"], ["Berlin-Mitte"])


def test_a_longer_cell_never_matches_a_shorter_expectation():
    from f2c.flow.dialogs import cell_matches

    assert not cell_matches("101170", "10117")
    assert not cell_matches("Berlin-Mitte", "Berlin")


def test_classify_accepts_a_row_with_a_clipped_company(sample_doc):
    rows = [["1 CUST000001", "Marta", "Klein", "Northstar Office", "10117", "Berlin"]]
    required = [
        sample_doc.company,
        sample_doc.contact_first,
        sample_doc.contact_last,
        sample_doc.billing.zip,
        sample_doc.billing.city,
    ]
    exact, partial = classify(
        rows, required, [sample_doc.company], clippable=[sample_doc.company]
    )
    assert exact == [0] and partial == []


# --- OCR confusables ------------------------------------------------------- #
def test_ocr_confusable_sku_still_matches():
    """Canvas lists are read by OCR: "CHR-ERG-01" comes back as "CHR-ERG-O1"."""
    from f2c.flow.dialogs import cell_matches

    assert cell_matches("CHR-ERG-O1", "CHR-ERG-01")
    assert cell_matches("CUSTOOOOO1", "CUST000001")


def test_confusable_folding_does_not_merge_genuinely_different_values():
    from f2c.flow.dialogs import cell_matches

    assert not cell_matches("CHR-ERG-02", "CHR-ERG-01")
    assert not cell_matches("MAT-DESK-02", "CHR-ERG-01")


# --- product row fallback --------------------------------------------------- #
def test_row_mentions_tolerates_truncated_ocr_cells():
    from f2c.flow.product import _row_mentions

    assert _row_mentions(["Ergonomic Des_", "", "297.50"], "Ergonomic Desk Chair")
    assert not _row_mentions(["Anti-Fatigue D_", "", "47.60"], "Ergonomic Desk Chair")


def test_row_mentions_ignores_cells_too_short_to_identify():
    from f2c.flow.product import _row_mentions

    assert not _row_mentions(["Erg", "1"], "Ergonomic Desk Chair")


# --- read-only money cells -------------------------------------------------- #
def test_money_candidates_offers_the_currency_glyph_reading():
    """The line Price has no editor, so it is read by OCR, and the dollar sign
    in "$450.00" comes back as a digit."""
    from decimal import Decimal

    from f2c.flow.items import _money_candidates

    assert Decimal("450.00") in _money_candidates("5450.00")
    assert _money_candidates("$450.00") == [Decimal("450.00")]
    assert _money_candidates("450.00") == [Decimal("450.00")]


def test_money_candidates_keeps_the_literal_reading_first():
    """A genuine 5450.00 must still be offered - the caller decides."""
    from decimal import Decimal

    from f2c.flow.items import _money_candidates

    assert _money_candidates("5450.00")[0] == Decimal("5450.00")


def test_money_candidates_on_unreadable_text():
    from f2c.flow.items import _money_candidates

    assert _money_candidates("") == []
    assert _money_candidates(None) == []


# --------------------------------------------------------------------------- #
# identifying the Order and Invoice rows in the final Documents list (spec 5.5)
# --------------------------------------------------------------------------- #
def test_document_rows_are_found_by_number_not_by_the_word():
    """The Documents list is a canvas read by OCR. It renders "INV000001",
    which comes back as "INVOOOOO1" - and the word "invoice" never appears in
    the row at all. A run that created both documents correctly was reported as
    manual-review because the check looked for the word."""
    from f2c.flow.invoice import _rows_for

    rows = [
        ["INVOOOOO1", "Sep 19, 2026", "Northstar Office Gm.", "WEB-2026-0714-A17", "paid", "678.30"],
        ["POOOOOO1", "Jul 14, 2026", "Northstar Office Gm.", "WEB-2026-0714-A17", "open", "678.30"],
    ]
    assert _rows_for(rows, "INV000001", "invoice") == [rows[0]]
    assert _rows_for(rows, "PO000001", "order") == [rows[1]]


def test_document_rows_fall_back_to_the_word_when_no_number_is_known():
    from f2c.flow.invoice import _rows_for

    rows = [["Invoice 7", "paid"], ["Order 7", "open"]]
    assert _rows_for(rows, "", "invoice") == [rows[0]]
    assert _rows_for(rows, "", "order") == [rows[1]]
