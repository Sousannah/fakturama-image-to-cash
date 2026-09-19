"""Stage 5: complete and verify the linked Invoice (spec 5.1 - 5.7)."""
from __future__ import annotations

from decimal import Decimal

from ..config import SETTINGS
from ..logging_setup import get
from ..ui.grid import DOCUMENT_HEADERS, read_rows, row_signature
from .context import Ctx
from .formats import format_date, format_decimal, parse_amount

log = get("flow.invoice")

TOLERANCE = Decimal("0.01")


def run(ctx: Ctx) -> None:
    doc = ctx.doc

    # 5.1 - the proposed numbers and dates are left exactly as generated
    # Read-only. The Invoice is populated from the Order asynchronously, so the
    # first look can legitimately be too early.
    for attempt in ctx.retried_step(
        "5.1", "Confirm the Invoice was populated from the Order",
        shoot=True, recover=ctx.activate_invoice,
    ):
        with attempt as rec:
            number = ""
            el = ctx.maybe("invoice.no_field")
            if el is not None:
                number = el.text().strip()
            ctx.state.invoice_number = number
            ctx.remember("invoice_number", number)   # renamed on save, as the Order is

            custref = ""
            el = ctx.maybe("invoice.custref_field")
            if el is not None:
                custref = el.text().strip()
            if custref:
                ctx.expect("Invoice Cust.Ref.", doc.external_ref, custref)

            total = None
            # full-length: same reason as the Order totals in finalize_order.py
            el = ctx.maybe("invoice.total_gross_label", timeout=SETTINGS.default_timeout)
            if el is not None:
                total = parse_amount(el.text())
            if total is not None and abs(total - Decimal(doc.gross_total)) > TOLERANCE:
                ctx.stop_for_review(
                    "invoice total %s does not match the order total %s"
                    % (total, doc.gross_total)
                )
            ctx.art.end(
                rec,
                "ok",
                "invoice No. %r (unchanged), Cust.Ref. %r, total %s"
                % (number, custref, total),
            )

    # 5.2 - payment method
    for attempt in ctx.retried_step(
        "5.2", "Set or confirm the Invoice payment method", recover=ctx.activate_invoice
    ):
        with attempt as rec:
            combo = ctx.maybe("invoice.payment_combo", timeout=6.0)
            if combo is None:
                ctx.stop_for_review("the Invoice payment-method control could not be resolved")
            current = combo.text().strip()
            if current.casefold() == doc.payment_method.casefold():
                ctx.art.end(rec, "ok", "already %r" % current)
            else:
                if not (ctx.dry_run or combo.select_item(doc.payment_method)):
                    ctx.stop_for_review(
                        "payment method %r is not available on the Invoice"
                        % doc.payment_method,
                        current=current,
                    )
                ctx.art.end(rec, "ok", "changed %r -> %r" % (current, doc.payment_method))

    # 5.3 - paid status
    _apply_paid_status(ctx)

    # 5.4 - save once
    with ctx.step("5.4", "Click the toolbar Save control once", shoot=True):
        ctx.save()
        ctx.state.invoice_saved = True
        ctx.resolver.invalidate_scopes()

    # 5.5 - final verification in Data > Documents
    _verify_documents(ctx)


def _apply_paid_status(ctx: Ctx) -> None:
    doc = ctx.doc
    # Replayable: the paid box is toggled to a state rather than flipped, and
    # the date and value are overwritten.
    for attempt in ctx.retried_step(
        "5.3", "Apply the extracted payment status (%s)" % doc.paid_status,
        shoot=True, recover=ctx.activate_invoice,
    ):
        with attempt as rec:
            check = ctx.maybe("invoice.paid_check", timeout=6.0)
            if check is None:
                ctx.stop_for_review("the Invoice paid control could not be resolved")

            if not doc.is_paid:
                if not ctx.dry_run:
                    check.toggle_off()
                ctx.art.end(
                    rec, "ok",
                    "status is not PAID: paid left clear, no date or value invented",
                )
                return

            if not ctx.dry_run:
                check.toggle_on()

            date_field = ctx.maybe("invoice.payment_date", timeout=6.0)
            if date_field is None:
                ctx.stop_for_review("the Invoice payment-date field could not be resolved")
            sample = date_field.text().strip()
            expected_date = format_date(doc.payment_date, sample)
            actual_date = ctx.set_date("invoice.payment_date", doc.payment_date)
            ctx.expect("invoice payment date", expected_date, actual_date)

            value_field = ctx.maybe("invoice.paid_value", timeout=6.0)
            if value_field is None:
                ctx.stop_for_review("the Invoice paid-value field could not be resolved")
            ctx.type_into(
                "invoice.paid_value",
                format_decimal(Decimal(doc.gross_total), value_field.text().strip()),
            )
            ctx.art.end(
                rec,
                "ok",
                "paid on %s for the full invoice total %s"
                % (doc.payment_date, doc.gross_total),
            )


def _verify_documents(ctx: Ctx) -> None:
    """spec 5.5: the Invoice row is correct and the source Order is still open."""
    doc = ctx.doc
    for attempt in ctx.retried_step(
        "5.5", "Verify Invoice and Order rows in Data > Documents", shoot=True
    ):
        with attempt as rec:
            # see finalize_order.py 4.5
            table = ctx.open_editor(
                "nav.data_documents", "documents.table",
                timeout=30, tab_logical="documents.tab",
            )
            search = ctx.maybe("documents.search", timeout=3.0)
            if search is not None:
                ctx.type_into("documents.search", doc.external_ref, blank_readback_ok=True)
            ctx.wait_stable(lambda: row_signature(table), what="documents list")
            rows = read_rows(table, DOCUMENT_HEADERS)
            ctx.art.extra["documents_final"] = rows[:30]

            related = [r for r in rows if _has_cell(r, doc.external_ref)]
            if len(related) < 2:
                ctx.stop_for_review(
                    "expected an Order row and an Invoice row for Cust.Ref. %r, found %d"
                    % (doc.external_ref, len(related)),
                    rows=related,
                )

            # Identify the two rows by the document NUMBERS the flow already
            # knows, not by the word "invoice" appearing in a cell. This list is
            # a custom-drawn canvas read by OCR, and it renders the number
            # "INV000001" - which OCR returns as "INVOOOOO1". The word never
            # appears; the number does, and `cell_matches` folds the confusable
            # glyphs. The word check stays as a fallback for a build that
            # numbers its documents differently.
            invoice_rows = _rows_for(related, ctx.state.invoice_number, "invoice")
            order_rows = _rows_for(related, ctx.state.order_number, "order")
            if not invoice_rows:
                ctx.stop_for_review("no Invoice row found after saving", rows=related)
            if not order_rows:
                ctx.stop_for_review(
                    "the source Order row is missing after saving the Invoice",
                    rows=related,
                )

            totals_ok = any(
                _has_amount(r, Decimal(doc.gross_total)) for r in invoice_rows
            )
            if not totals_ok:
                ctx.stop_for_review(
                    "the Invoice row does not show the expected total %s" % doc.gross_total,
                    rows=invoice_rows,
                )

            ctx.art.end(
                rec,
                "ok",
                "invoice row %s | order row %s" % (invoice_rows[0], order_rows[0]),
            )


def _has_cell(row, value: str) -> bool:
    """The documents list is read by OCR, so allow for look-alike glyphs."""
    from .dialogs import cell_matches

    return any(cell_matches(c, value) for c in row)


def _looks_like(row, kind: str) -> bool:
    return any(kind in str(c).casefold() for c in row)


def _rows_for(rows, number: str, kind: str):
    """Rows belonging to one document: by its number first, by the word second."""
    if number:
        matched = [r for r in rows if _has_cell(r, number)]
        if matched:
            return matched
    return [r for r in rows if _looks_like(r, kind)]


def _has_amount(row, expected: Decimal) -> bool:
    """Is `expected` shown anywhere in this row?

    Uses the same currency-glyph tolerance as the item grid: this list renders
    "$678.30" and OCR returns the symbol as a digit.
    """
    from .items import _money_candidates

    for cell in row:
        for value in _money_candidates(cell):
            if abs(value - expected) <= TOLERANCE:
                return True
    return False
