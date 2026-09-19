"""spec 2.10.x: resolve or create the Payment Method (terms of payment).

Entered only while the Debtor editor is open and the required method is not in
its dropdown. The Order tab and the Debtor editor both stay open throughout.
"""
from __future__ import annotations

from ..logging_setup import get
from ..ui.grid import read_uia_rows, row_signature
from .context import Ctx
from .dialogs import classify

log = get("flow.payment")


def ensure_payment_method(ctx: Ctx, method: str) -> None:
    """Make sure `method` exists in Data > terms of payment."""
    rows = []
    for attempt in ctx.retried_step(
        "2.10.1", "Open Data > terms of payment and search %r" % method
    ):
        with attempt:
            # Replayable: opening a list part that is already open is a no-op,
            # and the search box is overwritten rather than appended to.
            ctx.click("nav.data_terms_of_payment")
            table = ctx.wait_for("payment.list_table", timeout=20)
            ctx.type_into("payment.list_search", method, blank_readback_ok=True)
            ctx.wait_stable(lambda: row_signature(table), what="payment methods")
            rows = read_uia_rows(table)

    exact, partial = classify(rows, required=[method], identifying=[method])

    with ctx.step("2.10.2", "Decide whether an exact Payment Method already exists") as rec:
        if len(exact) > 1:
            ctx.stop_for_review(
                "%d payment methods match %r exactly" % (len(exact), method),
                rows=[rows[i] for i in exact],
            )
        if len(exact) == 1:
            ctx.art.end(rec, "skipped", "exact method already exists: %s" % rows[exact[0]])
            return
        if partial:
            ctx.stop_for_review(
                "payment method %r is not present but %d conflicting row(s) look similar"
                % (method, len(partial)),
                rows=[rows[i] for i in partial],
            )
        ctx.art.end(rec, "ok", "no exact row; creating %r" % method)

    _create(ctx, method)


def _create(ctx: Ctx, method: str) -> None:
    doc = ctx.doc
    code = doc.payment_code
    if not code:
        ctx.stop_for_review("no payment-code mapping for %r" % method)

    for attempt in ctx.retried_step(
        "2.10.2", "Click the green + in the terms-of-payment list"
    ):
        with attempt:
            # `open_editor` checks for the editor before clicking, so a replay
            # cannot open a second one.
            ctx.open_editor("payment.list_new", "payment.name", timeout=20)

    for attempt in ctx.retried_step("2.10.3", "Set Name and Description; leave Account blank"):
        with attempt:
            ctx.type_into("payment.name", method)
            ctx.type_into("payment.description", method)
            account = ctx.maybe("payment.account")
            if account is not None and account.value_text().strip():
                # spec 2.10.3 says to leave Account blank; if the field will not
                # clear, that is worth a note rather than abandoning the order
                ctx.type_into("payment.account", "", required=False)

    for attempt in ctx.retried_step("2.10.4", "Set the payment code to %r" % code):
        with attempt:
            if not ctx.choose("payment.code_combo", code):
                ctx.stop_for_review(
                    "payment code %r is not offered by the dropdown" % code,
                    method=method,
                )

    for attempt in ctx.retried_step(
        "2.10.5", "Zero the discount/day fields; leave the texts blank"
    ):
        with attempt:
            for logical in ("payment.cash_discount", "payment.discount_days", "payment.net_days"):
                if ctx.maybe(logical) is not None:
                    ctx.type_into(logical, "0")
            # "Set as standard" is deliberately never touched.
            log.debug("Set-as-standard intentionally not clicked")

    with ctx.step("2.10.6", "Save the new Payment Method once", shoot=True):
        ctx.save()
        ctx.state.payment_method_created = True
