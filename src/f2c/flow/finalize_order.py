"""Stage 4: confirm, save and verify the Order, then branch to the Invoice."""
from __future__ import annotations

from decimal import Decimal

from ..config import SETTINGS
from ..logging_setup import get
from ..ui.grid import DOCUMENT_HEADERS, read_rows, row_signature
from .context import Ctx
from .formats import parse_amount

log = get("flow.finalize")

TOLERANCE = Decimal("0.01")


def run(ctx: Ctx) -> None:
    doc = ctx.doc

    for attempt in ctx.retried_step(
        "4.1", "Confirm the Debtor addresses and every Product line",
        shoot=True, recover=ctx.activate_order,
    ):
        with attempt as rec:
            el = ctx.maybe("order.invoice_address_text", timeout=4.0)
            invoice_text = el.text() if el is not None else ""
            if invoice_text:
                ctx.expect_contains("Order invoice address", doc.billing.zip, invoice_text)
            ctx.art.end(
                rec, "ok", "%d item line(s) entered" % ctx.state.lines_entered
            )

    for attempt in ctx.retried_step(
        "4.2", "Keep order Discount at 0%% and Shipping free", recover=ctx.activate_order
    ):
        with attempt as rec:
            discount = ctx.maybe("order.discount_field")
            detail = []
            if discount is not None:
                current = parse_amount(discount.text())
                if current is None or current != 0:
                    ctx.type_into("order.discount_field", "0")
                    detail.append("discount reset to 0")
                else:
                    detail.append("discount already 0")
            shipping = ctx.maybe("order.shipping_combo")
            if shipping is not None:
                detail.append("shipping left as %r" % shipping.text().strip())
            ctx.art.end(rec, "ok", "; ".join(detail) or "no order-level controls resolved")

    _confirm_totals(ctx)

    with ctx.step("4.4", "Click the toolbar Save control once", shoot=True):
        ctx.save()
        ctx.state.order_saved = True
        ctx.resolver.invalidate_scopes()

    _verify_in_documents(ctx)
    _open_followup_invoice(ctx)


def _confirm_totals(ctx: Ctx) -> None:
    """spec 4.3: Total Net, VAT and Total must match the source."""
    doc = ctx.doc
    # Read-only, and the footer is recomputed asynchronously after the last
    # line commits - so a first reading that says "not yet" is not a mismatch.
    for attempt in ctx.retried_step(
        "4.3", "Confirm Total Net / VAT / Total against the source",
        recover=ctx.activate_order,
    ):
        with attempt as rec:
            if ctx.dry_run:
                ctx.art.end(rec, "skipped", "dry run")
                return

            readings = {}
            for logical, expected, label in (
                ("order.total_net_label", doc.net_total, "Total Net"),
                ("order.total_vat_label", doc.vat_total, "VAT"),
                ("order.total_gross_label", doc.gross_total, "Total"),
            ):
                # A full-length wait, not the short one used for optional
                # controls. By the time this runs the Order editor holds both
                # item lines and their in-cell editors, and a single walk of
                # that subtree takes about ten seconds - so a four-second budget
                # expired after ONE look and reported the totals as unreadable
                # while they sat on screen the whole time.
                el = ctx.maybe(logical, timeout=SETTINGS.default_timeout)
                actual = parse_amount(el.text()) if el is not None else None
                readings[label] = {"expected": str(expected), "actual": str(actual)}
                if actual is None:
                    log.warning("could not read %s from the Order footer", label)
                    continue
                if abs(actual - Decimal(expected)) > TOLERANCE:
                    ctx.stop_for_review(
                        "order %s is %s but the image says %s" % (label, actual, expected),
                        readings=readings,
                    )

            unread = [k for k, v in readings.items() if v["actual"] in ("None", None)]
            ctx.art.extra["order_totals"] = readings
            if unread:
                ctx.art.end(
                    rec,
                    "manual-review",
                    "could not read %s from the Order footer" % ", ".join(unread),
                )
                ctx.stop_for_review(
                    "the Order totals could not be read back, so they cannot be confirmed",
                    unread=unread,
                    readings=readings,
                )
            ctx.art.end(rec, "ok", "totals match the source")


def _verify_in_documents(ctx: Ctx) -> None:
    """spec 4.5: one Order row with the right number, date, Cust.Ref., state, total."""
    doc = ctx.doc
    # Navigation plus a read: nothing here changes a record, and the list is
    # populated asynchronously, so this is exactly the shape that benefits from
    # being tried again.
    for attempt in ctx.retried_step(
        "4.5", "Verify the saved Order in Data > Documents", shoot=True
    ):
        with attempt as rec:
            # Not a plain click: the nav entry does not always bring the
            # Documents list to the front, and when it does not, this step ends
            # up reading whichever list IS in front.
            table = ctx.open_editor(
                "nav.data_documents", "documents.table",
                timeout=30, tab_logical="documents.tab",
            )
            search = ctx.maybe("documents.search", timeout=3.0)
            if search is not None:
                ctx.type_into("documents.search", doc.external_ref, blank_readback_ok=True)
            ctx.wait_stable(lambda: row_signature(table), what="documents list")
            rows = read_rows(table, DOCUMENT_HEADERS)

            from .dialogs import cell_matches

            matching = [r for r in rows if any(cell_matches(c, doc.external_ref) for c in r)]
            ctx.art.extra["documents_after_order"] = rows[:30]

            if not matching:
                ctx.stop_for_review(
                    "no document row carries the Cust.Ref. %r after saving the Order"
                    % doc.external_ref,
                    rows=rows[:30],
                )
            if len(matching) > 1:
                ctx.stop_for_review(
                    "%d document rows carry the Cust.Ref. %r" % (len(matching), doc.external_ref),
                    rows=matching,
                )

            row = matching[0]
            joined = " ".join(row)
            # The same OCR-confusable tolerance the row match uses: this list
            # renders "PO000001" and OCR returns "POOOOOO1".
            if ctx.state.order_number and not any(
                cell_matches(c, ctx.state.order_number) for c in row
            ):
                log.warning(
                    "the saved row does not show the proposed number %r: %s",
                    ctx.state.order_number,
                    joined,
                )
            ctx.art.end(rec, "ok", "order row: %s" % joined)


def _open_followup_invoice(ctx: Ctx) -> None:
    """spec 4.6 / 4.7: use the Order's follow-up action, never the toolbar."""
    # Clicking the follow-up action twice would create two Invoices, so the
    # click goes through `open_editor`, which looks for the Invoice editor
    # first and never clicks when it is already there. That is what makes this
    # step replayable - and it needs to be, because this is an unnamed link in
    # a composite the Order draws itself, exactly the kind of click SWT drops.
    for attempt in ctx.retried_step(
        "4.6", "Create the linked Invoice from the Order's follow-up area",
        shoot=True, recover=ctx.activate_order,
    ):
        with attempt as rec:
            if ctx.maybe("invoice.payment_combo", timeout=1.0) is None:
                # 4.5 verified the Order in Data > Documents, which leaves the
                # Documents list in front of the editor area. The follow-up
                # action lives INSIDE the Order editor, so the Order has to be
                # brought back first - on the first attempt as much as on a
                # retry, which is why this is in the body and not only in the
                # step's `recover`.
                ctx.activate_order()
                area = ctx.maybe("order_followup_area", timeout=SETTINGS.default_timeout)
                if area is None:
                    ctx.art.end(
                        rec,
                        "manual-review",
                        "the 'Create a follow-up document' area could not be resolved",
                    )
                    ctx.stop_for_review(
                        "the Order's follow-up area was not found; the toolbar Invoice "
                        "button is deliberately not used because it would not preserve "
                        "the Order link",
                        order_number=ctx.state.order_number,
                    )
            ctx.open_editor(
                "order.followup_invoice",
                "invoice.payment_combo",
                timeout=30,
                tab_logical="invoice.tab",
            )

    for attempt in ctx.retried_step("4.7", "Wait for the linked New Invoice editor"):
        with attempt:
            ctx.wait_for("invoice.payment_combo", timeout=30)
            ctx.resolver.invalidate_scopes()
