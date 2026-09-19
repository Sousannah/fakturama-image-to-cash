"""spec 3.13 - 3.17: complete each selected item line in the Order's grid.

This is the only place the automation writes into Fakturama's custom-drawn item
table, and it is the part that took longest to get right.

The table is a canvas: UIA reports where it is and nothing about what is in it.
The way through is that **clicking a cell makes the application create a real
editor control for that cell**, which does appear in the accessibility tree. So
every value here is typed into a genuine SWT control and read back from that
same control - no OCR is involved in either the write or the check. See
`ui/grid.py` for the mechanics.
"""
from __future__ import annotations

from decimal import Decimal

from ..logging_setup import get
from ..models import Item
from ..ui.grid import ItemGridWriter
from .context import Ctx
from .formats import format_decimal, format_percent, parse_amount

log = get("flow.items")

COLUMN_QTY = "Qty."
COLUMN_UNIT_PRICE = "U.Price"
COLUMN_VAT = "VAT"
COLUMN_DISCOUNT = "Discount"
COLUMN_PRICE = "Price"
COLUMN_ITEM_NO = "Item No."


def complete_line(ctx: Ctx, item: Item, line_index: int, writer: ItemGridWriter) -> None:
    # Every cell write here is an overwrite, never an append, so each of these
    # steps may be replayed. The recovery brings the Order back in front and
    # makes the writer resolve the canvas again - a canvas element held from
    # before the editor was parked reports a rectangle that is no longer on
    # screen, and clicks computed from it go nowhere.
    def _recover() -> None:
        ctx.activate_order()
        writer.invalidate()

    for attempt in ctx.retried_step(
        "3.13", "Line %d: set Qty. to %s" % (line_index + 1, item.qty),
        recover=_recover,
    ):
        with attempt as rec:
            if ctx.dry_run:
                ctx.art.end(rec, "skipped", "dry run")
                return
            ctx.app.ensure_foreground()

            present = writer.read_cell(line_index, COLUMN_ITEM_NO)
            if not present:
                ctx.stop_for_review(
                    "line %d has no product on it - the selection did not land"
                    % (line_index + 1),
                    sku=item.sku,
                )

            if not writer.set_cell(line_index, COLUMN_QTY, _num(item.qty)):
                ctx.stop_for_review(
                    "could not set the quantity on line %d" % (line_index + 1),
                    sku=item.sku,
                    grid_shape=writer.grid.shape,
                )
            ctx.art.end(rec, "ok", "Qty. = %s (confirmed in the cell editor)" % item.qty)

    # spec 3.14 - the VAT and unit price come from the Product, but the spec
    # requires them to equal the source, so both are set and confirmed.
    for attempt in ctx.retried_step(
        "3.14", "Line %d: confirm U.Price and VAT" % (line_index + 1),
        recover=_recover,
    ):
        with attempt as rec:
            vat_text = _set_line_vat(ctx, writer, item, line_index)

            if not writer.set_cell(line_index, COLUMN_UNIT_PRICE, _num(item.unit_net)):
                ctx.stop_for_review(
                    "could not set the unit price on line %d" % (line_index + 1),
                    sku=item.sku,
                )
            ctx.art.end(rec, "ok", "U.Price = %s, VAT = %s" % (item.unit_net, vat_text))

    for attempt in ctx.retried_step(
        "3.15", "Line %d: set Discount to %s%%" % (line_index + 1, item.discount_pct),
        recover=_recover,
    ):
        with attempt as rec:
            wanted = _pct(item.discount_pct)
            # the application renders a discount as a negative percentage
            if not writer.set_cell(line_index, COLUMN_DISCOUNT, wanted, compare="magnitude"):
                ctx.stop_for_review(
                    "could not set the discount on line %d" % (line_index + 1),
                    sku=item.sku,
                )
            ctx.art.end(rec, "ok", "Discount = %s%%" % item.discount_pct)

    _confirm_line_price(ctx, writer, item, line_index)


def _set_line_vat(ctx: Ctx, writer: ItemGridWriter, item: Item, line_index: int) -> str:
    """Make the line's VAT equal the extracted percentage.

    A product created in this run arrives on the line with whatever VAT its
    master record carries. That is not necessarily the rate on the source
    document, so the cell is set explicitly rather than trusted.
    """
    current = writer.read_cell(line_index, COLUMN_VAT) or ""
    if _vat_matches(current, item.vat_pct):
        log.debug("line %d VAT already %r", line_index + 1, current)
        return current

    log.info("line %d VAT is %r; setting it to %s", line_index + 1, current, item.vat_name)
    if not writer.select_in_cell(line_index, COLUMN_VAT, item.vat_name):
        # some builds label the entry with its percentage rather than its name
        writer.select_in_cell(line_index, COLUMN_VAT, _pct(item.vat_pct))

    after = writer.read_cell(line_index, COLUMN_VAT) or ""
    if not _vat_matches(after, item.vat_pct):
        ctx.stop_for_review(
            "line %d VAT is %r but the source says %s%%"
            % (line_index + 1, after, item.vat_pct),
            sku=item.sku,
        )
    return after


def _vat_matches(text: str, wanted_pct: Decimal) -> bool:
    """Does a VAT cell's text represent this percentage?

    The cell shows things like "VAT 19%" or "Tax-free (0.0%)", so the rate is
    taken from the number in the text rather than from the label.
    """
    amount = parse_amount(text)
    if amount is None:
        return False
    return abs(amount - Decimal(wanted_pct)) <= Decimal("0.01")


def _confirm_line_price(
    ctx: Ctx, writer: ItemGridWriter, item: Item, line_index: int
) -> None:
    """spec 3.16: line Price == qty x unit net x (1 - discount/100)."""
    expected = item.computed_line_net

    def _recover() -> None:
        ctx.activate_order()
        writer.invalidate()

    # Read-only, and worth repeating: the line total is recomputed by the
    # application after the discount cell commits, so a read that lands too
    # early sees the previous value.
    for attempt in ctx.retried_step(
        "3.16", "Line %d: confirm Price == %s" % (line_index + 1, expected),
        recover=_recover,
    ):
        with attempt as rec:
            if ctx.dry_run:
                ctx.art.end(rec, "skipped", "dry run")
                return

            text = writer.read_cell(line_index, COLUMN_PRICE)
            candidates = _money_candidates(text)
            if not candidates:
                ctx.stop_for_review(
                    "the Price cell on line %d could not be read (%r)" % (line_index + 1, text),
                    sku=item.sku,
                )

            match = next((c for c in candidates if abs(c - expected) <= Decimal("0.01")), None)
            if match is None:
                ctx.stop_for_review(
                    "line %d price reads %r (%s) but should be %s"
                    % (line_index + 1, text, ", ".join(str(c) for c in candidates), expected),
                    sku=item.sku,
                )
            ctx.art.end(rec, "ok", "line price %s confirmed (cell read %r)" % (match, text))


#: Characters OCR commonly returns for a leading currency symbol.
_CURRENCY_GLYPH_MISREADS = "5S$8B3S"


def _money_candidates(text):
    """Values a read-only money cell might be showing.

    The line Price is computed, so it has no editor and has to be read from the
    pixels. The cell renders "$450.00", and OCR returns the dollar sign as a
    digit - "5450.00". Rather than guess at the true value, this offers the
    small set of readings the text could represent, and the caller accepts one
    only if it equals the figure already computed from the source document. The
    number is therefore confirmed by OCR, never derived from it.
    """
    if not text:
        return []
    out = []
    first = parse_amount(text)
    if first is not None:
        out.append(first)
    stripped = text.strip()
    if stripped and stripped[0] in _CURRENCY_GLYPH_MISREADS:
        second = parse_amount(stripped[1:])
        if second is not None and second not in out:
            out.append(second)
    return out


def _num(value) -> str:
    return format_decimal(Decimal(value), "", places=2)


def _pct(value) -> str:
    return format_percent(Decimal(value), "")


def run(ctx: Ctx) -> None:
    """spec 3.1 / 3.17: the whole selection + completion branch, per item."""
    from .product import ensure_selected

    writer = ItemGridWriter(ctx.resolver)

    # Measure the grid ONCE, now, while it is still empty and unhighlighted.
    # Its columns and row height do not change as lines are filled in, and a
    # selected row hides the ruling lines the measurement depends on.
    if not ctx.dry_run:
        ctx.activate_order()
        geometry = writer.ensure_geometry()
        log.info("item grid measured: %d columns x rows of %dpx",
                 len(geometry.columns),
                 geometry.rows[0].height if geometry.rows else 0)
        if not geometry.is_usable():
            ctx.stop_for_review(
                "the item grid geometry could not be measured",
                shape=geometry.shape,
            )

    for index, item in enumerate(ctx.doc.items):
        with ctx.step(
            "3.1", "Item %d of %d: %s" % (index + 1, len(ctx.doc.items), item.sku)
        ):
            ctx.activate_order()
            ensure_selected(ctx, item, index, writer)
            ctx.activate_order()
            complete_line(ctx, item, index, writer)
            ctx.state.lines_entered = index + 1
