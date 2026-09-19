"""Stage 3 (master data half): select or create each Product (spec 3.1 - 3.12)."""
from __future__ import annotations

from typing import List

from ..logging_setup import get
from ..models import Item
from .context import Ctx
from .dialogs import (
    PRODUCT_HEADERS,
    classify,
    close_if_open,
    close_with,
    guard_not_the_new_button,
    open_dialog,
    search,
    select_row,
)
from .formats import format_decimal
from .vat import ensure_vat

log = get("flow.product")


def _vat_shown_matches(shown: str, item) -> bool:
    """Does the Product editor's VAT box represent this item's rate?

    Compared by percentage, not by label: the list decorates entries, and the
    same rate appears as "VAT 19%" here and "VAT 19% (19.0%)" elsewhere.
    """
    from decimal import Decimal

    from .formats import parse_amount

    value = parse_amount(shown)
    if value is None:
        return False
    return abs(value - Decimal(item.vat_pct)) <= Decimal("0.01")


def _row_mentions(row, value: str) -> bool:
    """Is `value` recognisable in any cell of an OCR-read row?

    Deliberately loose: these cells come from OCR of a canvas and are often
    truncated with a trailing glyph ("Ergonomic Des_").
    """
    needle = (value or "").strip().casefold()
    if len(needle) < 6:
        return False
    for cell in row:
        c = (cell or "").strip().casefold().rstrip("_. ")
        if c and (c in needle or needle.startswith(c)) and len(c) >= 6:
            return True
    return False


def read_line_identity(writer, line_index: int):
    """(item number, name) of a line, read from its own cell editors."""
    try:
        number = writer.read_cell(line_index, "Item No.")
    except Exception:
        number = None
    try:
        name = writer.read_cell(line_index, "Name")
    except Exception:
        name = None
    return number, name


def line_has_product(writer, item: Item, line_index: int, attempts: int = 2) -> bool:
    """Is this item's product already sitting on that line?

    Read from the line's own cell editors, so it is an exact comparison rather
    than an OCR guess, and checked on BOTH the item number and the name - the
    number is the authority, but a cell that answers late or empty should not be
    read as "the line is wrong".

    Worth checking before opening the selector at all: the dialog is the
    slowest and least reliable step in the whole flow, and after a product is
    created the application has frequently already placed it on the line. When
    that happens, re-selecting is not merely unnecessary - its failure is
    actively misleading, because the line is in fact correct.
    """
    from .dialogs import cell_matches

    for attempt in range(1, attempts + 1):
        number, name = read_line_identity(writer, line_index)
        if number and cell_matches(number, item.sku):
            return True
        if name and item.description and cell_matches(name, item.description):
            return True
        log.debug(
            "line %d reads number=%r name=%r (attempt %d)",
            line_index + 1, number, name, attempt,
        )
    return False


def ensure_selected(ctx: Ctx, item: Item, line_index: int, writer=None) -> None:
    """Leave the Order with this item's Product selected on line `line_index`."""
    if writer is not None and line_has_product(writer, item, line_index):
        log.info("%s is already on line %d", item.sku, line_index + 1)
        return

    if _try_select(ctx, item, first_pass=True, writer=writer, line_index=line_index):
        return

    # spec 3.4 - 3.6 already ran, in stage 0, before the Order editor was
    # opened - see flow/prerequisites.py. Running the check again here would
    # search a list that is drawn as a picture, and a missed read would create a
    # SECOND "VAT 19%" (which is exactly what happened before this guard).
    if item.vat_name not in ctx.state.vats_created:
        ensure_vat(ctx, item)
        ctx.activate_order()
    else:
        log.debug("%s already ensured in stage 0", item.vat_name)

    _create(ctx, item)             # 3.7 - 3.11

    for attempt in ctx.retried_step("3.12", "Return to the still-open Order tab"):
        with attempt:
            ctx.activate_order()

    if writer is not None and line_has_product(writer, item, line_index):
        with ctx.step("3.12", "Confirm %s is on line %d" % (item.sku, line_index + 1)) as rec:
            ctx.art.end(rec, "ok", "the saved product is already on the line")
        return

    try:
        selected = _try_select(
            ctx, item, first_pass=False, writer=writer, line_index=line_index
        )   # 3.12
    except Exception as exc:
        # The selector would not open. Before treating that as a failure, look
        # at the line itself: if the product is on it, the goal is met and the
        # dialog was never needed.
        if writer is not None and line_has_product(writer, item, line_index):
            with ctx.step("3.12", "Confirm %s is on line %d" % (item.sku, line_index + 1)) as rec:
                ctx.art.end(
                    rec, "ok",
                    "the selector would not reopen, but the line already carries the product",
                )
            return
        raise exc

    if not selected:
        ctx.stop_for_review(
            "the newly saved Product %s does not appear in the Order's product selector"
            % item.sku,
            sku=item.sku,
        )


# --------------------------------------------------------------------------- #
def _try_select(
    ctx: Ctx, item: Item, first_pass: bool, writer=None, line_index: int = 0
) -> bool:
    spec = "3.2-3.3" if first_pass else "3.12"
    label = (
        "Try to select %s from the Order" % item.sku
        if first_pass
        else "Re-open the product selector and select the newly saved %s" % item.sku
    )

    # Replayable, but ONLY while the product is not yet on the line.
    #
    # The Order's product picker APPENDS an item line; it does not fill one in.
    # So an attempt that selected the product and then failed on the way out -
    # which is exactly what a swallowed dialog looks like - has already done the
    # job, and repeating it puts the same product on the Order twice. That is
    # not a hypothetical: retrying this step blindly produced an Order carrying
    # MAT-DESK-02 three times, and a Total Net of 650.00 instead of 570.00.
    #
    # Hence the precondition: before every retry, look at the line itself. If it
    # carries the product, the step is finished, whatever the failure said.
    # Between attempts a stray dialog is cancelled - Cancel commits nothing -
    # and the Order is brought back in front.
    def _recover() -> None:
        close_if_open(ctx, "dlg.product.window", "dlg.product.cancel")
        ctx.activate_order()

    def _already_on_the_line() -> bool:
        return writer is not None and line_has_product(writer, item, line_index)

    for attempt in ctx.retried_step(
        spec, label, shoot=True, recover=_recover, precondition=_already_on_the_line
    ):
        with attempt as rec:
            # 3.2 - the UPPER product-selection icon, never the green +
            guard_not_the_new_button(ctx, "order.item_select_icon", "order.item_new_icon")
            open_dialog(ctx, "order.item_select_icon", "dlg.product.window")

            rows = search(
                ctx, "dlg.product.search_field", "dlg.product.table", item.sku, PRODUCT_HEADERS
            )
            exact, partial = classify(rows, required=[item.sku], identifying=[item.sku])
            fallback_note = ""

            if not exact and len(rows) == 1 and _row_mentions(rows[0], item.description):
                # The product list is a custom-drawn canvas, and on this build its
                # Item No. column does not survive reconstruction - OCR returns the
                # name twice and no SKU cell at all. The search box was filtered by
                # the exact SKU, so a single returned row whose name matches the
                # extracted description is accepted as that product.
                #
                # This is a weaker check than the spec's "exact SKU" rule, so it is
                # recorded in the run report rather than applied silently, and it
                # only ever fires when exactly one row came back.
                fallback_note = (
                    "SKU column unreadable; accepted the single row returned by an "
                    "exact-SKU search because its name matches the extracted description"
                )
                log.warning("%s: %s", item.sku, fallback_note)
                exact = [0]
            ctx.art.extra.setdefault("product_search", []).append(
                {"sku": item.sku, "pass": "first" if first_pass else "after-create",
                 "rows": rows, "exact": exact, "fallback": fallback_note}
            )

            if len(exact) > 1:
                close_with(ctx, "dlg.product.cancel", "dlg.product.window")
                ctx.stop_for_review(
                    "%d products share the SKU %s" % (len(exact), item.sku),
                    rows=[rows[i] for i in exact],
                )

            if len(exact) == 1:
                select_row(ctx, "dlg.product.table", exact[0], PRODUCT_HEADERS)
                close_with(ctx, "dlg.product.ok", "dlg.product.window")
                detail = "selected existing product: %s" % rows[exact[0]]
                if fallback_note:
                    detail += "  [%s]" % fallback_note
                ctx.art.end(rec, "ok", detail)
                return True

            close_with(ctx, "dlg.product.cancel", "dlg.product.window")
            ctx.art.end(rec, "ok", "no exact SKU %s (%d rows)" % (item.sku, len(rows)))
            return False

    # Falling out of the loop means the harness finished the step without
    # running the body again, which it only does when the precondition above
    # says the product is already on the line. That IS a successful selection -
    # the goal of this function - so it must not be reported as a failure to
    # select, which would send a perfectly good Order to manual review.
    return True


def _create(ctx: Ctx, item: Item) -> None:
    for attempt in ctx.retried_step("3.7", "Open New product (the required VAT now exists)"):
        with attempt:
            # An editor that opened BEHIND the Order is parked off-screen by
            # Eclipse and every write into it silently does nothing - which is
            # exactly how a run reached 3.10 and could not select the VAT. So
            # the tab is brought forward rather than the opener clicked twice.
            ctx.open_editor(
                "nav.new_product", "product.item_number", tab_logical="product.tab"
            )
            ctx.resolver.invalidate_scopes()

    for attempt in ctx.retried_step(
        "3.8", "Set Item Number, Name and Description", recover=ctx.activate_product
    ):
        with attempt:
            ctx.type_into("product.item_number", item.sku)
            ctx.type_into("product.name", item.description)
            ctx.type_into("product.description", item.description)

    for attempt in ctx.retried_step(
        "3.9",
        "Price (gross) = %s x (1 + %s/100) = %s"
        % (item.unit_net, item.vat_pct, item.product_gross_price),
        recover=ctx.activate_product,
    ):
        with attempt as rec:
            field = ctx.el("product.price_gross")
            sample = field.text().strip()
            text = format_decimal(item.product_gross_price, sample)
            ctx.type_into("product.price_gross", text)
            ctx.art.end(
                rec,
                "ok",
                "wrote %s (the transaction-line discount of %s%% is NOT applied here)"
                % (text, item.discount_pct),
            )

    # The most-retried step in the flow, and the one that most needs it: the
    # VAT dropdown has to have the foreground, keyboard focus and a settled
    # layout at the instant the selection keys land, and when it does not, the
    # combo simply keeps its old value. Every attempt re-asserts all three and
    # brings the Product editor back in front first.
    for attempt in ctx.retried_step(
        "3.10", "cost price 0.00, exact VAT, Stock 0.00",
        shoot=True, recover=ctx.activate_product,
    ):
        with attempt as rec:
            # The VAT goes in FIRST and is re-confirmed immediately before the
            # save. This is the single most important field on the product: the
            # order line inherits its tax rate from here, and the Order's own
            # VAT dropdown is populated before this rate exists, so it cannot be
            # corrected later.
            if not ctx.choose("product.vat_combo", item.vat_name):
                ctx.stop_for_review(
                    "VAT %r could not be selected in the Product editor" % item.vat_name,
                    sku=item.sku,
                    shows=ctx.read("product.vat_combo"),
                )

            cost = ctx.maybe("product.cost_price")
            if cost is not None:
                ctx.type_into("product.cost_price", format_decimal(0, cost.text()))
            stock = ctx.maybe("product.stock")
            if stock is not None:
                ctx.type_into("product.stock", format_decimal(0, stock.text()))
            # Category, GTIN, supplier code, allowance, picture and user defined
            # field 1 are deliberately left untouched (spec 3.10).

            shown = ctx.read("product.vat_combo")
            if not _vat_shown_matches(shown, item):
                log.warning("VAT drifted to %r while filling the form; re-selecting", shown)
                ctx.choose("product.vat_combo", item.vat_name)
                shown = ctx.read("product.vat_combo")
            ctx.art.end(rec, "ok", "VAT %r, cost 0.00, stock 0.00" % shown)

    with ctx.step("3.11", "Save the Product once") as rec:
        before_save = ctx.read("product.vat_combo")
        if not _vat_shown_matches(before_save, item):
            ctx.stop_for_review(
                "the Product's VAT reads %r immediately before saving, not %s"
                % (before_save, item.vat_name),
                sku=item.sku,
            )
        ctx.save()
        ctx.state.products_created.append(item.sku)
        ctx.resolver.invalidate_scopes()
        ctx.art.end(rec, "ok", "saved with VAT %r" % before_save)
