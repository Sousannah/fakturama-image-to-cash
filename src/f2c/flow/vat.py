"""spec 3.4 - 3.6: resolve or create the VAT rate a Product will need.

Always run *before* New product, so the rate is present in the Product editor's
VAT dropdown (3.7).
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence

from ..logging_setup import get
from ..models import Item, VatRow
from ..ui.grid import read_uia_rows, row_signature
from .context import Ctx
from .formats import parse_amount

log = get("flow.vat")

STANDARD_RATE_CODE = "S (Standard rate)"


def parse_vat_rows(rows: Sequence[Sequence[str]]) -> List[VatRow]:
    """Turn raw cell lists into VatRow, without needing to know column order."""
    out: List[VatRow] = []
    for i, cells in enumerate(rows):
        name = ""
        value = ""
        code = ""
        for cell in cells:
            c = cell.strip()
            if not c:
                continue
            low = c.casefold()
            if low.startswith("vat ") and c.endswith("%") and not name:
                name = c
            elif ("standard" in low or low in ("s",)) and not code:
                code = c
            elif parse_amount(c) is not None and not value:
                value = c
        if not name and cells:
            name = cells[0].strip()
        out.append(VatRow(name=name, value=value, code=code, row_index=i, raw=" | ".join(cells)))
    return out


def ensure_vat(ctx: Ctx, item: Item) -> None:
    """Guarantee an exact, reusable VAT row exists for this item's rate."""
    wanted = item.vat_name  # e.g. "VAT 19%"

    rows = []
    for attempt in ctx.retried_step("3.4", "Open Data > VATs and search %r" % wanted):
        with attempt:
            # Replayable: the list part is opened if it is not already, and the
            # search box is overwritten rather than appended to.
            ctx.click("nav.data_vats")
            table = ctx.wait_for("vat.list_table", timeout=20)
            ctx.type_into("vat.list_search", wanted, blank_readback_ok=True)
            ctx.wait_stable(lambda: row_signature(table), what="VAT rows")
            rows = read_uia_rows(table)

    parsed = parse_vat_rows(rows)
    reusable = [v for v in parsed if v.is_reusable_for(item)]
    same_name = [v for v in parsed if v.name.strip().casefold() == wanted.casefold()]

    with ctx.step("3.5", "Decide whether an existing VAT row can be reused") as rec:
        if len(reusable) > 1:
            ctx.stop_for_review(
                "%d VAT rows match %r exactly" % (len(reusable), wanted),
                rows=[v.raw for v in reusable],
            )
        if len(reusable) == 1:
            ctx.state.vats_created.append(wanted)   # guaranteed present
            ctx.art.end(rec, "skipped", "reusing existing %s" % reusable[0].raw)
            return
        if same_name:
            # same name but a conflicting value or code - exactly the case the
            # spec says to stop on rather than silently correct
            ctx.stop_for_review(
                "a VAT row named %r exists but its value or E-Invoice code conflicts" % wanted,
                rows=[v.raw for v in same_name],
                expected_value=str(item.vat_pct),
                expected_code=STANDARD_RATE_CODE,
            )
        ctx.art.end(rec, "ok", "no %r row; creating it" % wanted)

    _create(ctx, item)


def _create(ctx: Ctx, item: Item) -> None:
    wanted = item.vat_name

    # Filling the form and saving it are separate steps, because only one of
    # them is safe to repeat. Every field here is overwritten rather than
    # appended to, and the editor is opened only if it is not already open, so
    # the form can be filled again after a transient failure. The save cannot:
    # a second one would mean a second VAT rate.
    for attempt in ctx.retried_step(
        "3.6", "Fill a new %s with the Standard rate code" % wanted, shoot=True
    ):
        with attempt:
            ctx.open_editor("vat.list_new", "vat.name", timeout=20)
            ctx.type_into("vat.name", wanted)
            ctx.type_into("vat.description", wanted)

            combo = ctx.maybe("vat.code_combo")
            if combo is not None and not ctx.dry_run:
                current = combo.text().strip()
                if "standard" not in current.casefold():
                    if not combo.select_item(STANDARD_RATE_CODE):
                        combo.select_item("S (Standard rate)")

            value_field = ctx.el("vat.value")
            sample = value_field.text().strip()
            from .formats import format_percent

            ctx.type_into("vat.value", format_percent(item.vat_pct, sample))
            # "Standard VAT" is left exactly as displayed (spec 3.6).

    with ctx.step("3.6", "Save the new VAT rate once"):
        ctx.save()
        ctx.state.vats_created.append(wanted)
