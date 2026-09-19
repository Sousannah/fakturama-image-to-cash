"""First verification layer: re-read what the UI shows and compare.

Used inside the flow (each stage verifies its own post-condition) and available
standalone via `f2c verify --image ...`, which re-opens Data > Documents and
confirms an already-completed run without repeating it.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from ..logging_setup import get
from ..models import OrderDoc
from ..ui.app import FakturamaApp
from ..ui.grid import read_uia_rows, row_signature
from ..ui.resolver import Resolver
from ..ui.waits import wait_stable
from ..flow.formats import parse_amount

log = get("verify.ui")

TOLERANCE = Decimal("0.01")


def verify_documents(doc: OrderDoc) -> Dict[str, Any]:
    """Confirm an Order row and an Invoice row exist with the expected totals."""
    app = FakturamaApp()
    app.attach()
    app.normalise_window()
    resolver = Resolver(app.window)

    problems: List[str] = []

    nav = resolver.try_resolve("nav.data_documents", timeout=8.0)
    if nav is not None:
        nav.invoke()
    table = resolver.resolve("documents.table", timeout=20)

    search = resolver.try_resolve("documents.search", timeout=3.0)
    if search is not None:
        search.set_text(doc.external_ref)

    wait_stable(lambda: row_signature(table), what="documents list")
    rows = read_uia_rows(table)

    related = [
        r for r in rows
        if any(str(c).strip().casefold() == doc.external_ref.casefold() for c in r)
    ]
    if not related:
        problems.append("no document row with Cust.Ref. %r" % doc.external_ref)

    orders = [r for r in related if any("order" in str(c).casefold() for c in r)]
    invoices = [r for r in related if any("invoice" in str(c).casefold() for c in r)]
    if not orders:
        problems.append("no Order row")
    if not invoices:
        problems.append("no Invoice row")

    for label, group in (("Order", orders), ("Invoice", invoices)):
        for row in group:
            if not _has_amount(row, Decimal(doc.gross_total)):
                problems.append(
                    "%s row does not show the expected total %s: %s"
                    % (label, doc.gross_total, row)
                )

    return {
        "ok": not problems,
        "problems": problems,
        "rows": related,
        "tier_usage": resolver.stats.as_dict(),
    }


def _has_amount(row, expected: Decimal) -> bool:
    for cell in row:
        value = parse_amount(cell)
        if value is not None and abs(value - expected) <= TOLERANCE:
            return True
    return False
