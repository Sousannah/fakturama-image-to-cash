"""Stage 1: open a New Order and fill its header (spec 1.3 - 1.8)."""
from __future__ import annotations

from ..logging_setup import get
from .context import Ctx
from .formats import format_date

log = get("flow.order_header")


def open_new_order(ctx: Ctx) -> None:
    """spec 1.3: click Order in the top toolbar and wait for the editor."""
    for attempt in ctx.retried_step(
        "1.3", "Open a New Order from the top toolbar", shoot=True
    ):
        with attempt:
            # `open_editor` looks for the editor before it clicks anything, so
            # replaying this step can never open a second Order; and if the
            # editor opened behind another one, its tab is brought forward
            # rather than the toolbar being clicked again.
            ctx.open_editor(
                "toolbar.order_new",
                "order.custref_field",
                timeout=30,
                tab_logical="order.tab",
            )
            ctx.resolver.invalidate_scopes()


def fill_header(ctx: Ctx) -> None:
    doc = ctx.doc

    # spec 1.4 - the proposed No. is left exactly as Fakturama generated it.
    for attempt in ctx.retried_step(
        "1.4", "Leave the proposed order No. unchanged", recover=ctx.activate_order
    ):
        with attempt as rec:
            number = ""
            el = ctx.maybe("order.no_field")
            if el is not None:
                number = el.text().strip()
            ctx.state.order_number = number
            # the editor will be renamed to this the moment it is saved
            ctx.remember("order_number", number)
            ctx.art.end(rec, "ok", "proposed No. = %r (not modified)" % number)

    # spec 1.5 - Date
    for attempt in ctx.retried_step(
        "1.5", "Set Date to the extracted Order Date", recover=ctx.activate_order
    ):
        with attempt:
            field = ctx.el("order.date_field")
            sample = field.text().strip()
            expected = format_date(doc.order_date, sample)
            log.debug("date field currently %r -> want %r", sample, expected)
            actual = ctx.set_date("order.date_field", doc.order_date)
            ctx.expect("order date", expected, actual)

    # spec 1.6 - External reference into Cust.Ref.
    for attempt in ctx.retried_step(
        "1.6", "Enter the External Reference in Cust.Ref.", recover=ctx.activate_order
    ):
        with attempt:
            ctx.type_into("order.custref_field", doc.external_ref)
            ctx.expect("Cust.Ref.", doc.external_ref, ctx.read("order.custref_field"))

    # spec 1.7 - price mode Net, VAT mode "With VAT"
    for attempt in ctx.retried_step(
        "1.7", "Set price mode to Net and keep VAT as With VAT",
        shoot=True, recover=ctx.activate_order,
    ):
        with attempt as rec:
            mode = ctx.maybe("order.price_mode_net")
            if mode is None:
                ctx.art.end(
                    rec, "manual-review", "the price-mode control could not be resolved"
                )
                ctx.stop_for_review(
                    "document price mode control not found; refusing to guess the price basis"
                )

            before = mode.text().strip()
            if before.casefold() != "net":
                if not ctx.dry_run and not mode.select_item("Net"):
                    ctx.stop_for_review(
                        "the document price mode could not be set to Net",
                        current=before,
                    )
                after = ctx.read("order.price_mode_net")
                ctx.expect("document price mode", "Net", after)
            else:
                after = before

            # "With VAT" is the default and the spec says to keep it - we
            # confirm, never set, so an unexpected value surfaces instead of
            # being overwritten.
            vat = ctx.maybe("order.vat_mode_with")
            vat_state = vat.text().strip() if vat is not None else "<unresolved>"
            if vat_state and "with vat" not in vat_state.casefold():
                ctx.stop_for_review(
                    "VAT mode is %r, expected it to already be 'With VAT'" % vat_state
                )
            ctx.art.end(
                rec, "ok", "price mode %r -> %r; VAT mode %r" % (before, after, vat_state)
            )


def run(ctx: Ctx) -> None:
    open_new_order(ctx)
    fill_header(ctx)
