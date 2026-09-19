"""Stage 0: make sure the master data the Order will reference already exists.

Why this stage exists
---------------------
The spec creates missing master data *while the document editor is open* - the
payment method from inside the Debtor editor (2.10), the VAT rate from inside
the Order (3.4-3.6). That ordering assumes the editors refresh their dropdowns
when new records appear.

This build does not. Its dropdowns are filled once, when the editor opens, and
a record created afterwards is simply not offered. That was verified directly:
after creating "Bank Transfer", expanding the Debtor's Payment dropdown still
listed only the one pre-existing entry; and a Product created after "VAT 19%"
existed still saved against "Tax-free", because the Order editor - opened before
the rate existed - had no other choice to give it.

So the same checks the spec describes, with the same matching and the same
manual-review gates, are run first, before any editor is opened. Nothing is
created that the spec would not have created; only the moment differs, and the
run report records that a prerequisite was made here rather than mid-flow.
"""
from __future__ import annotations

from ..logging_setup import get
from .context import Ctx
from .payment import ensure_payment_method
from .vat import ensure_vat

log = get("flow.prerequisites")


def run(ctx: Ctx) -> None:
    doc = ctx.doc

    with ctx.step("0.2", "Ensure the payment method exists before any editor opens") as rec:
        if ctx.dry_run:
            ctx.art.end(rec, "skipped", "dry run")
        else:
            ensure_payment_method(ctx, doc.payment_method)
            ctx.art.end(rec, "ok", "payment method %r available" % doc.payment_method)

    with ctx.step("0.3", "Ensure every VAT rate on the order exists") as rec:
        if ctx.dry_run:
            ctx.art.end(rec, "skipped", "dry run")
            return
        names = []
        for item in _first_item_per_rate(doc):
            ensure_vat(ctx, item)
            names.append(item.vat_name)
        ctx.art.end(rec, "ok", "VAT rates available: %s" % ", ".join(names))


def _first_item_per_rate(doc):
    """One item per distinct VAT percentage - the rate is what matters, not the line."""
    seen = set()
    out = []
    for item in doc.items:
        if item.vat_pct in seen:
            continue
        seen.add(item.vat_pct)
        out.append(item)
    return out
