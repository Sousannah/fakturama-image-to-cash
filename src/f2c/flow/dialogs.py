"""The two selector dialogs, which are the flow's existence checks.

The spec is deliberate about this: the Order's own Debtor and Product selectors
*are* the existence check (never a side query against the database). So both
dialogs get the same careful treatment - type the query, wait for the list to
stop changing, read the rows, decide.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..logging_setup import get
from ..ui.grid import click_canvas_row, read_rows, read_uia_row_elements, row_signature
from ..ui.uia import Element
from .context import Ctx

log = get("flow.dialogs")

#: Column headings of the "Select the address" list, read off the live dialog.
#: Used to reconstruct the custom-drawn result grid (see ui/grid.py).
ADDRESS_HEADERS = ("No.", "First Name", "Name", "Company", "ZIP", "City")

#: Column headings of the "Select a product" list.
PRODUCT_HEADERS = ("No.", "Item No.", "Name", "Description", "Price")


def search(
    ctx: Ctx,
    search_field: str,
    table: str,
    query: str,
    headers: Sequence[str] = (),
) -> List[List[str]]:
    """Type `query`, wait for the result list to stabilise, return its rows."""
    ctx.type_into(search_field, query, blank_readback_ok=True)
    table_el = ctx.el(table)
    ctx.wait_stable(
        lambda: row_signature(table_el),
        what="results for %r" % query,
    )
    rows = read_rows(table_el, headers)
    log.info("search %r -> %d row(s)", query, len(rows))
    for r in rows[:12]:
        log.debug("   %s", r)
    return rows


def row_elements(ctx: Ctx, table: str) -> List[Element]:
    return read_uia_row_elements(ctx.el(table))


def select_row(ctx: Ctx, table: str, index: int, headers: Sequence[str] = ()) -> None:
    """Select result row `index`, whether the list is a real table or a canvas."""
    table_el = ctx.el(table)
    els = read_uia_row_elements(table_el)

    if els:
        if index >= len(els):
            ctx.stop_for_review(
                "row %d is no longer present in the result list" % index, rows=len(els)
            )
        if not ctx.dry_run:
            els[index].select()
        return

    # custom-drawn list: click inside the row band
    if ctx.dry_run:
        return
    if not click_canvas_row(table_el, index, headers):
        ctx.stop_for_review(
            "could not select row %d: the result list could not be reconstructed" % index
        )


#: A displayed cell shorter than this is never treated as a clipped prefix.
MIN_PREFIX_CHARS = 6

#: Glyph pairs OCR routinely confuses in UI text. These lists are read off a
#: canvas by OCR, not from the accessibility tree, so "CHR-ERG-01" comes back as
#: "CHR-ERG-O1" and "CUST000001" as "CUSTOOOOO1". Folding both sides through
#: this table before comparing recovers the match.
#:
#: This is deliberately applied only as a last resort, after exact and prefix
#: comparison have failed, and the flow still refuses to act on more than one
#: candidate - so the worst case of an over-eager fold is a stop for manual
#: review, not a wrong record.
_OCR_CONFUSABLES = str.maketrans(
    {"o": "0", "O": "0", "i": "1", "I": "1", "l": "1",
     "s": "5", "S": "5", "b": "8", "B": "8", "z": "2", "Z": "2"}
)


def _fold_confusables(text: str) -> str:
    return text.translate(_OCR_CONFUSABLES)


def cell_matches(cell: str, expected: str, allow_clipped: bool = False) -> bool:
    """Does one displayed cell represent `expected`?

    Whole-cell equality by default: "Berlin" must not satisfy a requirement of
    "Berlin-Mitte", and a ZIP of "10117" must not be satisfied by "101170".

    `allow_clipped` additionally accepts a long-enough prefix, because these
    lists clip to the column width - the Company column shows "Northstar
    Office" for "Northstar Office GmbH", so equality alone can never match a
    real debtor.

    It is opt-in per field rather than global on purpose. A first attempt
    applied the prefix rule everywhere and immediately reintroduced the very
    false positive the whole-cell rule exists to prevent: "Berlin" is a
    six-character prefix of "Berlin-Mitte". Only fields that genuinely get
    clipped (long free-text ones like Company) are allowed the tolerance.
    """
    c = (cell or "").strip().casefold()
    e = (expected or "").strip().casefold()
    if c == e:
        return True
    if allow_clipped and len(c) >= MIN_PREFIX_CHARS and len(c) < len(e) and e.startswith(c):
        return True

    # last resort: same string once OCR-confusable glyphs are folded together
    cf, ef = _fold_confusables(c), _fold_confusables(e)
    if cf == ef:
        return True
    if allow_clipped and len(cf) >= MIN_PREFIX_CHARS and len(cf) < len(ef) and ef.startswith(cf):
        return True
    return False


def cells_match_all(
    row: Sequence[str],
    required: Sequence[str],
    clippable: Sequence[str] = (),
) -> bool:
    """Every required value is represented by some cell of this row.

    `clippable` lists the values whose column may be visually truncated.
    """
    clip = {(v or "").strip().casefold() for v in clippable}
    for value in required:
        v = (value or "").strip()
        if not v:
            continue  # an unspecified field cannot disqualify a row
        allow = v.casefold() in clip
        if not any(cell_matches(cell, v, allow) for cell in row):
            return False
    return True


def cells_contain_any(
    row: Sequence[str], values: Sequence[str], clippable: Sequence[str] = ()
) -> bool:
    clip = {(v or "").strip().casefold() for v in clippable}
    return any(
        any(cell_matches(cell, v, v.strip().casefold() in clip) for cell in row)
        for v in values
        if (v or "").strip()
    )


def classify(
    rows: Sequence[Sequence[str]],
    required: Sequence[str],
    identifying: Sequence[str],
    clippable: Sequence[str] = (),
) -> Tuple[List[int], List[int]]:
    """Split result rows into (exact, partial).

    * exact   - every value in `required` appears as a whole cell
    * partial - at least one `identifying` value matches but not the full set;
                these are the rows the spec calls "conflicting or ambiguous"
    """
    exact, partial = [], []
    for i, row in enumerate(rows):
        if cells_match_all(row, required, clippable):
            exact.append(i)
        elif cells_contain_any(row, identifying, clippable):
            partial.append(i)
    return exact, partial


def close_with(ctx: Ctx, button_logical: str, window_logical: str) -> None:
    ctx.click(button_logical)
    try:
        ctx.wait_gone(window_logical, timeout=10.0)
    except Exception:
        log.debug("dialog %s did not report as gone; continuing", window_logical)
    ctx.resolver.invalidate_scopes()


def close_if_open(ctx: Ctx, window_logical: str, cancel_logical: str) -> bool:
    """Cancel a selector dialog if one is still up. Returns whether it was.

    This is the recovery a replayable selection step runs before its next
    attempt: Cancel commits nothing, so the retry starts from the same state the
    first attempt did rather than from whatever the failure left behind.
    """
    if ctx.maybe(window_logical, timeout=1.0) is None:
        return False
    log.info("closing a stray %s before trying again", window_logical)
    try:
        close_with(ctx, cancel_logical, window_logical)
    except Exception as exc:
        log.warning("could not close %s: %s", window_logical, exc)
    return True


def open_dialog(
    ctx: Ctx,
    opener_logical: str,
    window_logical: str,
    timeout: float = 20.0,
    attempts: int = 3,
) -> Element:
    """Click the opener and wait for the dialog shell.

    Retried, because a click on an SWT image control is occasionally swallowed
    (the only evidence is the tooltip appearing). Opening a selector dialog is
    idempotent, so a repeat click is safe: if the dialog did open, the second
    click never happens.
    """
    last = None
    for attempt in range(1, attempts + 1):
        existing = ctx.maybe(window_logical, timeout=1.0)
        if existing is not None:
            return existing

        ctx.click(opener_logical)
        # The FULL timeout on every attempt, not a share of it. Dividing it
        # meant a dialog that was merely slow ran out of time on each try, and
        # every expiry clicked the icon again - so the retry made things worse
        # rather than better.
        try:
            return ctx.wait_for(window_logical, timeout=timeout)
        except Exception as exc:
            last = exc
            log.warning("%s did not appear (attempt %d/%d)", window_logical, attempt, attempts)
            ctx.app.ensure_foreground()
    raise last


def guard_not_the_new_button(ctx: Ctx, select_logical: str, new_logical: str) -> None:
    """spec 2.1 / 3.2: assert we resolved the upper selector, not the green +.

    Both icons live in the same composite. When both resolve, the selector must
    be strictly above the creation control; if it is not, the templates have
    matched the wrong thing and we stop rather than silently starting a new
    master record.
    """
    selector = ctx.maybe(select_logical, timeout=2.0)
    creator = ctx.maybe(new_logical, timeout=2.0)
    if selector is None or creator is None:
        return  # nothing to compare; the resolver's own ordering stands
    if selector.rect.top >= creator.rect.top:
        ctx.stop_for_review(
            "the resolved record selector is not above the create control - "
            "refusing to click in case it is the green + that starts a new record",
            selector=str(selector),
            creator=str(creator),
        )
