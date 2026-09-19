"""Stage 2: select or create the Debtor, from inside the open Order (spec 2.x).

The Order tab is never closed. If the Debtor has to be created we detour to the
Contact editor (and, if necessary, from there to terms of payment), save, and
then come back and re-run the *same* selection that failed - because a
successful selection from the Order is the only proof the spec accepts that the
new Debtor was really saved (2.13).
"""
from __future__ import annotations

from typing import List

from ..logging_setup import get
from .context import Ctx
from .dialogs import (
    ADDRESS_HEADERS,
    classify,
    close_if_open,
    close_with,
    guard_not_the_new_button,
    open_dialog,
    search,
    select_row,
)

log = get("flow.debtor")


def run(ctx: Ctx) -> None:
    if _try_select(ctx, first_pass=True):
        _confirm_addresses(ctx)
        return
    _create(ctx)
    with ctx.step("2.12", "Return to the still-open Order tab"):
        ctx.activate_order()
    if not _try_select(ctx, first_pass=False):
        ctx.stop_for_review(
            "the newly saved Debtor still does not appear in the Order's address selector",
            company=ctx.doc.company,
        )
    _confirm_addresses(ctx)


# --------------------------------------------------------------------------- #
# selection branch (2.1 - 2.4)
# --------------------------------------------------------------------------- #
def _try_select(ctx: Ctx, first_pass: bool) -> bool:
    doc = ctx.doc
    label = "Try to select the Debtor from the Order" if first_pass else (
        "Re-open the address selector and select the newly saved Debtor"
    )
    spec = "2.1-2.3" if first_pass else "2.12"

    # Replayable in full. Opening the selector is idempotent (the dialog is
    # checked for before the icon is clicked), the search box is overwritten
    # rather than appended to, and selecting the same row twice leaves the same
    # Debtor on the Order. Between attempts a stray dialog is cancelled, which
    # commits nothing, and the Order is brought back to the front.
    def _recover() -> None:
        close_if_open(ctx, "dlg.address.window", "dlg.address.cancel")
        ctx.activate_order()

    for attempt in ctx.retried_step(spec, label, shoot=True, recover=_recover):
        with attempt as rec:
            # 2.1 - the UPPER existing-contact icon, never the lower green +
            guard_not_the_new_button(ctx, "order.address_select_icon", "order.address_new_icon")
            open_dialog(ctx, "order.address_select_icon", "dlg.address.window")

            # 2.2 - search by company/customer name and wait for the list to settle
            rows = search(
                ctx, "dlg.address.search_field", "dlg.address.table", doc.company, ADDRESS_HEADERS
            )

            # 2.3 - exact means Company, First Name, Name, ZIP and City all match
            required = [
                doc.company,
                doc.contact_first,
                doc.contact_last,
                doc.billing.zip,
                doc.billing.city,
            ]
            exact, partial = classify(
                rows,
                required=required,
                identifying=[doc.company],
                clippable=[doc.company],   # the Company column is truncated to fit
            )
            ctx.art.extra.setdefault("debtor_search", []).append(
                {"pass": "first" if first_pass else "after-create", "rows": rows,
                 "exact": exact, "partial": partial}
            )

            if len(exact) > 1:
                close_with(ctx, "dlg.address.cancel", "dlg.address.window")
                ctx.stop_for_review(
                    "%d debtors match the source exactly" % len(exact),
                    rows=[rows[i] for i in exact],
                )

            if len(exact) == 1:
                select_row(ctx, "dlg.address.table", exact[0], ADDRESS_HEADERS)
                close_with(ctx, "dlg.address.ok", "dlg.address.window")
                ctx.art.end(rec, "ok", "selected existing debtor: %s" % rows[exact[0]])
                return True

            if partial and first_pass:
                close_with(ctx, "dlg.address.cancel", "dlg.address.window")
                ctx.stop_for_review(
                    "no exact debtor, but %d similar row(s) were returned - creating a "
                    "duplicate would be worse than stopping" % len(partial),
                    rows=[rows[i] for i in partial],
                    required=required,
                )

            # 2.3 - no exact row: Cancel and fall through to the creation branch
            close_with(ctx, "dlg.address.cancel", "dlg.address.window")
            ctx.art.end(rec, "ok", "no exact debtor found (%d rows)" % len(rows))
            return False

    # Unreachable while this step has no precondition - the body always runs and
    # always returns - but stated rather than left to fall off the end, because
    # a selection step that returns None reads as "not selected".
    return False


def _confirm_addresses(ctx: Ctx) -> None:
    """spec 2.4 / 2.13: the populated addresses must match the image."""
    doc = ctx.doc
    # Read-only, so always safe to repeat - and worth repeating: the address
    # panels are populated asynchronously once the selector closes.
    for attempt in ctx.retried_step(
        "2.4", "Confirm the populated Invoice and Delivery addresses",
        shoot=True, recover=ctx.activate_order,
    ):
        with attempt as rec:
            invoice_text = ""
            delivery_text = ""
            el = ctx.maybe("order.invoice_address_text", timeout=4.0)
            if el is not None:
                invoice_text = el.text()
            el = ctx.maybe("order.delivery_address_text", timeout=4.0)
            if el is not None:
                delivery_text = el.text()

            if not invoice_text:
                ctx.art.end(rec, "manual-review", "invoice address field could not be read")
                ctx.stop_for_review("the Order's Invoice address could not be read back")

            for needle in (doc.billing.zip, doc.billing.city):
                ctx.expect_contains("Order invoice address", needle, invoice_text)
            if doc.billing.street:
                ctx.expect_contains("Order invoice address", doc.billing.street, invoice_text)

            if delivery_text:
                for needle in (doc.delivery.zip, doc.delivery.city):
                    ctx.expect_contains("Order delivery address", needle, delivery_text)
            ctx.art.end(rec, "ok", "invoice/delivery addresses match the source")


def _assign_roles(ctx: Ctx, roles: list) -> bool:
    """Try to give the Main address the requested roles. True when it stuck.

    Two shapes are attempted: the pair of checkboxes the spec describes, and
    the "address type" selector this version actually ships.
    """
    checkbox_logicals = {
        "Invoice address": "contact.addr.role_invoice",
        "Delivery address": "contact.addr.role_delivery",
    }
    applied = False
    for role in roles:
        el = ctx.maybe(checkbox_logicals[role], timeout=3.0)
        if el is not None and el.control_type == "CheckBox":
            if not ctx.dry_run:
                el.toggle_on()
            applied = True

    if applied:
        return True

    selector = ctx.maybe("contact.addr.type_control", timeout=3.0)
    if selector is None:
        return False
    if ctx.dry_run:
        return True
    return any(selector.select_item(role) for role in roles)


# --------------------------------------------------------------------------- #
# creation branch (2.5 - 2.11)
# --------------------------------------------------------------------------- #
def _create(ctx: Ctx) -> None:
    doc = ctx.doc

    # The payment method was ensured in stage 0, before any editor opened -
    # see flow/prerequisites.py for why it cannot be done from in here.
    for attempt in ctx.retried_step("2.5", "Keep the Order open and click New Contact"):
        with attempt:
            # `open_editor` looks for the editor before clicking, so a replay
            # cannot open a second Contact; `tab_logical` covers the case where
            # it opened behind the Order and was parked off-screen.
            ctx.open_editor(
                "nav.new_contact", "contact.company", tab_logical="contact.tab"
            )
            ctx.resolver.invalidate_scopes()

    for attempt in ctx.retried_step(
        "2.6", "Enter Company / First Name / Last Name; leave the ID and Salutation",
        recover=ctx.activate_contact,
    ):
        with attempt as rec:
            proposed_id = ""
            el = ctx.maybe("contact.customer_id")
            if el is not None:
                proposed_id = el.text().strip()
            ctx.type_into("contact.company", doc.company)
            ctx.type_into("contact.first_name", doc.contact_first)
            ctx.type_into("contact.last_name", doc.contact_last)
            if doc.salutation:
                ctx.choose("contact.salutation_combo", doc.salutation)
            ctx.art.end(
                rec,
                "ok",
                "proposed Customer ID %r left unchanged; salutation %s"
                % (proposed_id, doc.salutation or "left as ---"),
            )

    for attempt in ctx.retried_step(
        "2.7", "Fill Addresses > Main address from the billing address",
        recover=ctx.activate_contact,
    ):
        with attempt:
            ctx.click("contact.tab_addresses")
            ctx.type_into("contact.addr.street", doc.billing.street)
            ctx.type_into("contact.addr.zip", doc.billing.zip)
            ctx.type_into("contact.addr.city", doc.billing.city)
            if doc.billing.country:
                country = ctx.maybe("contact.addr.country")
                if country is not None:
                    if not country.select_item(doc.billing.country):
                        ctx.type_into("contact.addr.country", doc.billing.country)
            if doc.email:
                ctx.type_into("contact.addr.email", doc.email)
            if doc.phone:
                ctx.type_into("contact.addr.phone", doc.phone)
        # additional name / address specification / district are only filled
        # when the source supplies them - this source does not.

    for attempt in ctx.retried_step(
        "2.8", "Assign address roles", shoot=True, recover=ctx.activate_contact
    ):
        with attempt as rec:
            roles = ["Invoice address"]
            if doc.delivery_same_as_billing:
                roles.append("Delivery address")

            applied = _assign_roles(ctx, roles)

            if applied:
                detail = "assigned %s" % ", ".join(roles)
            else:
                # Fakturama 2.2.0 exposes address roles through an "address
                # type" control rather than the two checkboxes the spec
                # describes, and it already treats a contact's Main address as
                # both the invoice and the delivery address by default. Rather
                # than stop the flow on a difference that does not change the
                # saved record, this is recorded as a deviation and the run
                # continues. See README, known gaps.
                detail = (
                    "address-role control not drivable in this Fakturama version; "
                    "Main address keeps its default roles (recorded as a deviation)"
                )
            if not doc.delivery_same_as_billing:
                detail += (
                    " | billing != delivery: the separate delivery address is NOT "
                    "created (see README, known gaps)"
                )
            ctx.art.end(rec, "ok" if applied else "skipped", detail)

    for attempt in ctx.retried_step(
        "2.9", "Miscellaneous: alias, 0%% discount, Net", recover=ctx.activate_contact
    ):
        with attempt:
            ctx.click("contact.tab_misc")
            if doc.alias:
                ctx.type_into("contact.misc.alias", doc.alias)
            ctx.type_into("contact.misc.discount", "0")
            net = ctx.maybe("contact.misc.net_gross")
            if net is not None and not ctx.dry_run:
                if not net.select_item("Net"):
                    net.invoke()

    for attempt in ctx.retried_step(
        "2.10", "Select the Payment Method %r" % doc.payment_method,
        recover=ctx.activate_contact,
    ):
        with attempt as rec:
            ctx.click("contact.tab_payment")
            if not ctx.choose("contact.payment.method_combo", doc.payment_method):
                ctx.stop_for_review(
                    "payment method %r could not be selected on the Debtor"
                    % doc.payment_method,
                    shows=ctx.read("contact.payment.method_combo"),
                )
            ctx.art.end(
                rec, "ok",
                "payment method set to %r" % ctx.read("contact.payment.method_combo"),
            )

    with ctx.step("2.11", "Save the Debtor once", shoot=True):
        ctx.save()
        ctx.state.debtor_created = True
        ctx.resolver.invalidate_scopes()
