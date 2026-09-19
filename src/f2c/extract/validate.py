"""Deterministic reconciliation of an extracted OrderDoc.

This runs *before* any UI is touched. If the numbers on the image do not add
up the way the spec defines them, the safest thing is to stop: writing a wrong
order into an accounting system is worse than not writing one.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List

from ..errors import ReconciliationError
from ..models import CENTS, OrderDoc, money

#: Allowed absolute deviation. Zero by default: the arithmetic is exact.
#: One cent of slack is permitted for totals because some source documents
#: round per line and some round on the sum.
LINE_TOLERANCE = Decimal("0.00")
TOTAL_TOLERANCE = Decimal("0.01")


def reconcile(doc: OrderDoc, strict: bool = True) -> List[str]:
    """Return a list of human-readable problems. Raise if `strict` and non-empty."""
    problems: List[str] = []

    if not doc.items:
        problems.append("no item rows were extracted")

    # --- per line: qty x unit_net x (1 - disc/100) == source line total ------
    for i, it in enumerate(doc.items, start=1):
        expected = it.computed_line_net
        if abs(expected - money(it.line_net)) > LINE_TOLERANCE:
            problems.append(
                "line %d (%s): computed %s but image says %s"
                % (i, it.sku, expected, money(it.line_net))
            )
        if it.qty <= 0:
            problems.append("line %d (%s): non-positive quantity %s" % (i, it.sku, it.qty))
        if it.unit_net < 0:
            problems.append("line %d (%s): negative unit price %s" % (i, it.sku, it.unit_net))
        if not (Decimal(0) <= it.discount_pct < Decimal(100)):
            problems.append(
                "line %d (%s): discount %s%% out of range" % (i, it.sku, it.discount_pct)
            )
        if not (Decimal(0) <= it.vat_pct <= Decimal(100)):
            problems.append("line %d (%s): VAT %s%% out of range" % (i, it.sku, it.vat_pct))
        if not it.sku:
            problems.append("line %d: empty SKU" % i)

    # --- sum of lines == net total -----------------------------------------
    net = money(sum((it.computed_line_net for it in doc.items), Decimal(0)))
    if abs(net - money(doc.net_total)) > TOTAL_TOLERANCE:
        problems.append("net total: computed %s but image says %s" % (net, money(doc.net_total)))

    # --- VAT per line, summed ----------------------------------------------
    vat = money(
        sum(
            (it.computed_line_net * it.vat_pct / Decimal(100) for it in doc.items),
            Decimal(0),
        )
    )
    if abs(vat - money(doc.vat_total)) > TOTAL_TOLERANCE:
        problems.append("VAT total: computed %s but image says %s" % (vat, money(doc.vat_total)))

    # --- net + vat == gross -------------------------------------------------
    gross = money(net + vat)
    if abs(gross - money(doc.gross_total)) > TOTAL_TOLERANCE:
        problems.append(
            "gross total: computed %s but image says %s" % (gross, money(doc.gross_total))
        )

    # --- payment consistency (spec 5.3) ------------------------------------
    if doc.is_paid and doc.payment_date is None:
        problems.append("paid status is PAID but no payment date was extracted")
    if not doc.is_paid and doc.payment_date is not None:
        problems.append("paid status is not PAID but a payment date was extracted")
    if not doc.payment_code:
        problems.append(
            "payment method %r has no Fakturama payment-code mapping" % doc.payment_method
        )

    # --- debtor minimums ----------------------------------------------------
    if not doc.company:
        problems.append("no company / customer name extracted")
    if doc.billing.is_empty():
        problems.append("billing address is incomplete")
    if doc.delivery.is_empty():
        problems.append("delivery address is incomplete")
    if not doc.external_ref:
        problems.append("no external reference extracted")

    if problems and strict:
        raise ReconciliationError(
            "extraction failed reconciliation:\n  - " + "\n  - ".join(problems)
        )
    return problems


def summary(doc: OrderDoc) -> str:
    """One-screen human summary, printed before the UI run starts."""
    lines = [
        "Order        %s   date %s" % (doc.external_ref, doc.order_date),
        "Debtor       %s / %s  (alias %s)" % (doc.company, doc.contact_name, doc.alias or "-"),
        "Billing      %s, %s %s, %s"
        % (doc.billing.street, doc.billing.zip, doc.billing.city, doc.billing.country),
        "Delivery     %s, %s %s, %s%s"
        % (
            doc.delivery.street,
            doc.delivery.zip,
            doc.delivery.city,
            doc.delivery.country,
            "  (same as billing)" if doc.delivery_same_as_billing else "",
        ),
        "Payment      %s -> code %r | %s%s"
        % (
            doc.payment_method,
            doc.payment_code,
            doc.paid_status,
            (" on %s" % doc.payment_date) if doc.payment_date else "",
        ),
        "Items:",
    ]
    for i, it in enumerate(doc.items, 1):
        lines.append(
            "  %d. %-14s %-26s qty %s x %s  -%s%%  VAT %s%%  = %s   (product gross %s)"
            % (
                i,
                it.sku,
                it.description[:26],
                it.qty,
                it.unit_net,
                it.discount_pct,
                it.vat_pct,
                it.computed_line_net,
                it.product_gross_price,
            )
        )
    lines.append(
        "Totals       net %s | VAT %s | gross %s"
        % (money(doc.net_total), money(doc.vat_total), money(doc.gross_total))
    )
    return "\n".join(lines)
