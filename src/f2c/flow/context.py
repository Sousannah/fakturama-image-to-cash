"""Shared state and the small verb vocabulary every flow stage uses."""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..artifacts import RunArtifacts, StepRecord
from ..config import SETTINGS
from ..errors import ManualReviewRequired, TimeoutError_, VerificationError
from ..logging_setup import get
from ..models import OrderDoc
from ..ui.app import FakturamaApp
from ..ui.resolver import Resolver
from ..ui.uia import Element
from ..ui.waits import wait_stable, wait_until

log = get("flow")


@dataclass
class FlowState:
    """What the run has done so far - also the resume key."""

    debtor_created: bool = False
    payment_method_created: bool = False
    vats_created: List[str] = field(default_factory=list)
    products_created: List[str] = field(default_factory=list)
    order_number: str = ""
    invoice_number: str = ""
    order_saved: bool = False
    invoice_saved: bool = False
    lines_entered: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "debtor_created": self.debtor_created,
            "payment_method_created": self.payment_method_created,
            "vats_created": self.vats_created,
            "products_created": self.products_created,
            "order_number": self.order_number,
            "invoice_number": self.invoice_number,
            "order_saved": self.order_saved,
            "invoice_saved": self.invoice_saved,
            "lines_entered": self.lines_entered,
        }


class _Attempt:
    """One run of a replayable step body.

    Swallows a failure so the enclosing loop can try again; lets the *last*
    failure through so the run ends with a real traceback rather than a
    manufactured one.
    """

    def __init__(self, loop: "StepAttempts"):
        self._loop = loop

    def __enter__(self) -> StepRecord:
        return self._loop.rec

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._loop._finish(exc)


class StepAttempts:
    """Runs one step body until it works, or until the attempts are used up.

    Why this exists
    ---------------
    Almost every failure seen against the live application has the same shape: a
    control needs the foreground, keyboard focus and a settled layout, and if
    any of the three is not true at the instant a keystroke lands, the operation
    does nothing and says nothing. Each one is transient - the step that stumbles
    is different on every run, and any of them passes when tried again.

    Chasing them one at a time was the wrong approach; this recovers the whole
    class of them. Between attempts the harness re-asserts the foreground,
    throws away cached container elements (a parked editor must not keep serving
    its children - see ui.uia.PARKED_COORDINATE) and runs the caller's own
    recovery, which is usually "bring the document editor back to the front".

    It is deliberately opt-in per step. A body may only be replayed if running
    it twice is indistinguishable from running it once: setting a field,
    choosing from a dropdown, opening a dialog that is checked for first,
    reading a value back. Steps that CREATE a record or press Save use the
    plain `Ctx.step` and get exactly one attempt, because a repeat would mean a
    second saved record - the one failure this automation must never produce.

    `precondition` is for the harder case in between: a body whose goal may
    ALREADY have been reached by the attempt that failed. Selecting a product
    from the Order is the example that taught this - the picker APPENDS an item
    line rather than setting one, so an attempt that selected the product and
    then failed on the way out had already done its job, and running it again
    put the same product on the Order a second time. The check runs before
    every retry, never before the first attempt, and a true answer finishes the
    step instead of repeating it.
    """

    def __init__(
        self,
        ctx: "Ctx",
        rec: StepRecord,
        attempts: int,
        recover: Optional[Callable[[], Any]],
        shoot: bool,
        settle: float,
        precondition: Optional[Callable[[], bool]] = None,
    ):
        self.ctx = ctx
        self.rec = rec
        self.attempts = max(1, int(attempts))
        self.recover = recover
        self.shoot = shoot
        self.settle = settle
        self.precondition = precondition
        self._n = 0
        self._done = False

    # -- iteration ------------------------------------------------------- #
    def __iter__(self) -> Iterator[_Attempt]:
        return self

    def __next__(self) -> _Attempt:
        if self._done:
            raise StopIteration
        if self._n >= self.attempts:
            # unreachable in practice: the final failure propagates out of
            # _finish, which leaves the loop before it can ask for another try
            raise StopIteration
        self._n += 1
        if self._n > 1:
            self._recover()
            if self._satisfied():
                self._done = True
                raise StopIteration
        self.rec.attempts = self._n
        return _Attempt(self)

    def _satisfied(self) -> bool:
        """Has the failed attempt already achieved what the step wanted?"""
        if self.precondition is None:
            return False
        try:
            if not self.precondition():
                return False
        except Exception as exc:
            log.debug("precondition check for [%s] failed: %s", self.rec.spec_ref, exc)
            return False
        note = "attempt %d reached the goal before it failed; not repeating it" % (
            self._n - 1
        )
        self.rec.recovered.append(note)
        log.info("[%s] %s", self.rec.spec_ref, note)
        self.ctx.art.end(self.rec, "ok", note)
        if self.shoot:
            self.ctx.art.screenshot(
                "%s-%s" % (self.rec.spec_ref, self.rec.title), rec=self.rec
            )
        return True

    # -- outcome of one attempt ------------------------------------------ #
    def _finish(self, exc: Optional[BaseException]) -> bool:
        if exc is None:
            self._done = True
            if self.rec.status == "running":
                self.ctx.art.end(self.rec, "ok")
            if self.shoot:
                self.ctx.art.screenshot(
                    "%s-%s" % (self.rec.spec_ref, self.rec.title), rec=self.rec
                )
            return False

        if self._n >= self.attempts:
            self._done = True
            status = (
                "manual-review" if isinstance(exc, ManualReviewRequired) else "failed"
            )
            detail = (
                str(exc)
                if isinstance(exc, ManualReviewRequired)
                else "%s: %s" % (type(exc).__name__, exc)
            )
            if self.attempts > 1:
                detail += " (after %d attempts)" % self.attempts
            self.ctx.art.end(self.rec, status, detail)
            self.ctx.art.record_failure(
                exc, self.ctx._safe_window(), "%s-%s" % (status, self.rec.spec_ref)
            )
            return False  # propagate

        note = "attempt %d/%d: %s: %s" % (
            self._n, self.attempts, type(exc).__name__, exc,
        )
        self.rec.recovered.append(note)
        log.warning("[%s] %s - retrying", self.rec.spec_ref, note)
        # The status may have been set by the body before it failed (a step that
        # calls art.end and then stops for review); reset it so the record
        # reflects the attempt that finally counts.
        self.rec.status = "running"
        return True  # swallowed: go round again

    def _recover(self) -> None:
        """Put the application back into a state the body can be replayed from."""
        self.ctx.resolver.invalidate_scopes()
        try:
            self.ctx.app.ensure_foreground()
        except Exception as exc:
            log.debug("could not re-assert the foreground before a retry: %s", exc)
        if self.recover is not None:
            try:
                self.recover()
            except Exception as exc:
                log.warning("recovery before a retry failed: %s", exc)
        if self.settle:
            time.sleep(self.settle)


class Ctx:
    """Everything a flow stage needs, plus the verbs it is allowed to use.

    Keeping the verbs here (rather than letting stages talk to the resolver
    directly) means every UI interaction is logged, screenshotted and, where it
    writes a value, read back.
    """

    def __init__(
        self,
        doc: OrderDoc,
        app: FakturamaApp,
        resolver: Resolver,
        art: RunArtifacts,
        dry_run: bool = False,
    ):
        self.doc = doc
        self.app = app
        self.resolver = resolver
        self.art = art
        self.dry_run = dry_run
        self.state = FlowState()

    # ------------------------------------------------------------------ #
    # step bookkeeping
    # ------------------------------------------------------------------ #
    @contextmanager
    def step(self, spec_ref: str, title: str, shoot: bool = False):
        """One attempt, no replay. For bodies that must not run twice.

        Use this for anything that creates a record or presses Save. Everything
        else should use `retried_step`, which recovers the transient
        foreground/focus/layout failures that make a long run flaky.
        """
        rec = self.art.begin(spec_ref, title)
        try:
            yield rec
        except ManualReviewRequired as exc:
            self.art.end(rec, "manual-review", str(exc))
            self.art.record_failure(exc, self._safe_window(), "manual-review-%s" % spec_ref)
            raise
        except Exception as exc:
            self.art.end(rec, "failed", "%s: %s" % (type(exc).__name__, exc))
            self.art.record_failure(exc, self._safe_window(), "failed-%s" % spec_ref)
            raise
        else:
            if rec.status == "running":
                self.art.end(rec, "ok")
            if shoot:
                self.art.screenshot("%s-%s" % (spec_ref, title), rec=rec)

    def retried_step(
        self,
        spec_ref: str,
        title: str,
        shoot: bool = False,
        attempts: Optional[int] = None,
        recover: Optional[Callable[[], Any]] = None,
        settle: Optional[float] = None,
        precondition: Optional[Callable[[], bool]] = None,
    ) -> StepAttempts:
        """A step whose body may be replayed after a transient failure.

            for attempt in ctx.retried_step("3.10", "set the VAT"):
                with attempt as rec:
                    ...

        The body runs inside `with attempt`. If it raises, the harness recovers
        (foreground, cached scopes, the caller's `recover` hook) and runs it
        again; the last failure propagates exactly as it would from `step`, so
        the manual-review gates still stop the run for a real ambiguity - they
        just stop it after proving the ambiguity is not a timing artefact.

        Only pass a body that is safe to run twice. Where "twice" is only safe
        when the goal has not already been met, pass `precondition` - see
        `StepAttempts`.
        """
        rec = self.art.begin(spec_ref, title)
        return StepAttempts(
            self,
            rec,
            attempts=SETTINGS.step_attempts if attempts is None else attempts,
            recover=recover,
            shoot=shoot,
            settle=SETTINGS.step_settle if settle is None else settle,
            precondition=precondition,
        )

    def _safe_window(self) -> Optional[Element]:
        try:
            return self.app.window()
        except Exception:
            return None

    def remember(self, key: str, value: str) -> None:
        """Let selectors refer to something the flow has just learned.

        Used for the document numbers. Fakturama renames a document editor the
        moment it is saved - "New Order" becomes "PO000001" - so the selectors
        that find the Order by name stop matching precisely when the Order
        starts to exist, and every step after the save loses the editor.
        """
        value = (value or "").strip()
        if value:
            self.resolver.values[key] = value
            log.debug("selectors may now use {%s} = %r", key, value)

    def shoot(self, name: str, rec: Optional[StepRecord] = None):
        return self.art.screenshot(name, rec=rec)

    # ------------------------------------------------------------------ #
    # verbs
    # ------------------------------------------------------------------ #
    def el(self, logical: str, timeout: Optional[float] = None) -> Element:
        return self.resolver.resolve(logical, timeout=timeout)

    def maybe(self, logical: str, timeout: float = 1.5) -> Optional[Element]:
        return self.resolver.try_resolve(logical, timeout=timeout)

    def click(self, logical: str, timeout: Optional[float] = None) -> Element:
        el = self.el(logical, timeout=timeout)
        log.debug("click %s -> %r", logical, el)
        if not self.dry_run:
            # a click on an unnamed SWT image is a *physical* click, so the
            # application must own the foreground or it lands elsewhere
            self.app.ensure_foreground()
            was_tab = el.control_type == "TabItem"
            el.invoke()
            if was_tab:
                # give the newly revealed page time to lay out before anything
                # in it is focused or typed into
                import time as _time

                _time.sleep(0.5)
        self.resolver.invalidate_scopes()
        return el

    def type_into(
        self,
        logical: str,
        value: Any,
        timeout: Optional[float] = None,
        method: str = "keys",
        required: bool = True,
        blank_readback_ok: bool = False,
    ) -> Element:
        el = self.el(logical, timeout=timeout)
        text = "" if value is None else str(value)
        log.debug("type %r into %s (%r)", text, logical, el)
        if self.dry_run:
            return el

        self.app.ensure_foreground()
        el.set_text(text, method=method)

        # Verify, and retry once with the other write path. Keystroke entry into
        # SWT fields occasionally drops a character (an e-mail address arrived as
        # "martaklein@..." instead of "marta.klein@..."), and a silently mangled
        # value is exactly the kind of defect that survives all the way into a
        # saved invoice. Writes are checked, not hoped for.
        # value_text(), not text(): an empty SWT input reports its own caption,
        # so comparing against text() makes a blank field look like it already
        # holds the word "Account" and a good write look like a failed one.
        first = el.value_text().strip()
        if blank_readback_ok and not first and text:
            # Some SWT filter boxes never report their contents through the
            # accessibility tree - they answer with an empty string however they
            # are asked. An empty answer is therefore "no opinion", not "the
            # write failed"; a WRONG answer is still a failure.
            log.debug("%s reports no value; accepting the write", logical)
            return el

        if not _equal(text, first, loose=False):
            other = "value" if method == "keys" else "keys"
            log.warning(
                "%s read back %r after writing %r; retrying via %s",
                logical, first, text, other,
            )
            el = self.el(logical, timeout=timeout)
            el.set_text(text, method=other)
            got = el.value_text().strip()
            if blank_readback_ok and not got and text:
                return el
            if not _equal(text, got, loose=False):
                if not required:
                    # an optional field the spec only asks us to leave alone -
                    # worth recording, not worth failing the whole order over
                    log.warning(
                        "%s still reads %r after writing %r; continuing", logical, got, text
                    )
                    return el
                raise VerificationError("value written to %s" % logical, text, got)
        return el

    def read(self, logical: str, timeout: Optional[float] = None) -> str:
        return self.el(logical, timeout=timeout).text().strip()

    def set_date(self, logical: str, value, timeout: Optional[float] = None) -> str:
        """Write a date into a segmented date control and return what it shows.

        See `formats.date_digits` for why this cannot be a plain set_text.
        """
        from .formats import DEFAULT_DATE_FORMAT, date_digits, detect_date_format, format_date

        el = self.el(logical, timeout=timeout)
        sample = el.text().strip()
        fmt = detect_date_format(sample) or DEFAULT_DATE_FORMAT
        expected = format_date(value, sample)

        if self.dry_run:
            return expected

        self.app.ensure_foreground()
        el.focus()
        el.send_keys("{Home}", wait=0.12)
        el.send_keys(date_digits(value, fmt), wait=0.25)
        el.send_keys("{Tab}", wait=0.2)

        actual = self.read(logical)
        log.debug("date field %s: wanted %r, shows %r", logical, expected, actual)
        return actual

    def choose(self, logical: str, value: str, timeout: Optional[float] = None) -> bool:
        """Select `value` in a combo and confirm it actually took.

        Verified for the same reason writes are: a combo that quietly keeps its
        old selection saved a Debtor with country "United States" when the
        source said Germany.
        """
        log.debug("choose %r in %s", value, logical)
        if self.dry_run:
            return True

        # Retried, because a dropdown that has only just been revealed by a tab
        # switch is not yet focusable: the keystrokes that drive the selection
        # go nowhere and the attempt fails for a reason that has gone away by
        # the time it is tried again.
        import time as _time

        for attempt in range(1, 4):
            self.app.ensure_foreground()
            # a combo that has just been revealed may still be served from a
            # container element resolved before the layout settled
            self.resolver.invalidate_scopes()
            el = self.el(logical, timeout=timeout)

            already = el.text().strip()
            if _selection_agrees(value, already):
                return True

            el.select_item(value)
            actual = self.el(logical, timeout=timeout).text().strip()
            if _selection_agrees(value, actual):
                return True
            log.warning(
                "%s shows %r after selecting %r (attempt %d)", logical, actual, value, attempt
            )
            _time.sleep(1.0)
        return False

    def check(self, logical: str, on: bool = True, timeout: Optional[float] = None) -> Element:
        el = self.el(logical, timeout=timeout)
        if not self.dry_run:
            self.app.ensure_foreground()
            el.toggle_on() if on else el.toggle_off()
        return el

    # ------------------------------------------------------------------ #
    # verification
    # ------------------------------------------------------------------ #
    def expect(self, what: str, expected: Any, actual: Any, loose: bool = True) -> None:
        if self.dry_run:
            return
        if not _equal(expected, actual, loose):
            raise VerificationError(what, expected, actual)
        log.debug("verified %s == %r", what, expected)

    def expect_contains(self, what: str, needle: str, haystack: str) -> None:
        if self.dry_run:
            return
        if _norm(needle) not in _norm(haystack):
            raise VerificationError(what, "contains %r" % needle, haystack)
        log.debug("verified %s contains %r", what, needle)

    def stop_for_review(self, reason: str, **context) -> None:
        raise ManualReviewRequired(reason, context)

    # ------------------------------------------------------------------ #
    # waits, re-exported so stages do not import them individually
    # ------------------------------------------------------------------ #
    def wait_until(self, predicate, timeout: Optional[float] = None, what: str = "condition"):
        return wait_until(predicate, timeout=timeout, what=what)

    def wait_stable(self, snapshot, what: str = "list", timeout: Optional[float] = None):
        return wait_stable(snapshot, what=what, timeout=timeout)

    def wait_for(self, logical: str, timeout: Optional[float] = None) -> Element:
        return self.resolver.resolve(logical, timeout=timeout)

    def wait_gone(self, logical: str, timeout: float = 10.0) -> None:
        from ..ui.waits import wait_gone

        wait_gone(
            lambda: self.resolver.try_resolve(logical, timeout=0.4) is not None,
            timeout=timeout,
            what="%s to close" % logical,
        )

    def activate_order(self) -> bool:
        """Bring the still-open Order editor back to the front (spec 2.12 / 3.12).

        Every master-data detour opens its own editor on top of the Order. The
        spec is explicit that the Order tab stays open and is returned to, so
        this is a tab click, never a re-open.
        """
        return self._activate("order.tab", "order_editor")

    def activate_invoice(self) -> bool:
        return self._activate("invoice.tab", "invoice_editor")

    def activate_contact(self) -> bool:
        """Back to the Debtor editor after the terms-of-payment detour."""
        return self._activate("contact.tab", "contact_editor")

    def activate_product(self) -> bool:
        """Bring the Product editor back in front of whatever covered it."""
        return self._activate("product.tab", "product_editor")

    def _activate(self, tab_logical: str, editor_logical: str, attempts: int = 3) -> bool:
        """Bring an editor tab to the front and prove that it got there.

        "Proving it" is the whole point. Since the resolver refuses to return a
        parked control (ui.uia.PARKED_COORDINATE), resolving anything inside the
        editor is now evidence that the editor really is the visible one - and a
        tab click that was swallowed shows up here, immediately, instead of as a
        silent write into an editor nobody can see.
        """
        if self.dry_run:
            return True

        for attempt in range(1, attempts + 1):
            editor = self.maybe(editor_logical, timeout=1.0)
            if editor is not None:
                if attempt > 1:
                    log.debug("%s came to the front on attempt %d", editor_logical, attempt)
                # let the editor finish laying out before anything is clicked in it
                time.sleep(0.5)
                return True

            tab = self.maybe(tab_logical, timeout=5.0)
            if tab is None:
                log.warning("could not find the %s tab to return to", tab_logical)
                time.sleep(0.5)
                continue

            self.app.ensure_foreground()
            tab.invoke()
            self.resolver.invalidate_scopes()
            if self.maybe(editor_logical, timeout=10.0) is not None:
                time.sleep(0.5)
                log.debug("returned to %s", editor_logical)
                return True
            log.warning(
                "%s did not come to the front (attempt %d/%d)",
                editor_logical, attempt, attempts,
            )

        raise TimeoutError_(
            "%s would not come to the front after %d attempts; refusing to work "
            "in an editor that is not on screen" % (editor_logical, attempts)
        )

    def open_editor(
        self,
        opener_logical: str,
        wait_logical: str,
        attempts: int = 3,
        timeout: float = 25.0,
        tab_logical: Optional[str] = None,
    ) -> Element:
        """Click something that opens an editor, and wait for that editor.

        Retried: the navigation links are plain labels, and a click that arrives
        while a dialog is still closing is occasionally dropped by the platform -
        which surfaces as an error panel rather than an exception, so the only
        way to notice is that the editor never appeared.

        `tab_logical` covers the other half of that problem, which cost a whole
        run: the editor DID open, but it opened behind the document that was
        already in front, and Eclipse parks the widgets of a background editor
        off-screen. The automation went on to type into it and every write
        silently did nothing. So before concluding the opener was not clicked,
        look for the editor's tab and bring it forward - that is the difference
        between "it never opened" and "it opened where I could not see it".

        Never clicks the opener twice without first checking whether the editor
        is already there, which is what makes it safe to call from a replayable
        step: a second click would mean a second editor, and eventually a second
        record.
        """
        last = None
        for attempt in range(1, attempts + 1):
            existing = self.maybe(wait_logical, timeout=1.0)
            if existing is not None:
                return existing

            if tab_logical is not None:
                tab = self.maybe(tab_logical, timeout=1.0)
                if tab is not None:
                    log.info(
                        "%s already exists but is not in front; activating its tab",
                        wait_logical,
                    )
                    if not self.dry_run:
                        self.app.ensure_foreground()
                        tab.invoke()
                    self.resolver.invalidate_scopes()
                    existing = self.maybe(wait_logical, timeout=8.0)
                    if existing is not None:
                        return existing

            try:
                self.click(opener_logical)
                return self.wait_for(wait_logical, timeout=timeout / attempts)
            except Exception as exc:
                last = exc
                log.warning(
                    "%s did not open via %s (attempt %d/%d)",
                    wait_logical, opener_logical, attempt, attempts,
                )
                self.app.ensure_foreground()
        raise last

    def save(self) -> None:
        """Click the toolbar Save control exactly once (spec 3.11 / 4.4 / 5.4)."""
        self.click("toolbar.save")


def _selection_agrees(wanted: str, shown: str) -> bool:
    """Did a dropdown end up on the entry we asked for?

    Entries carry decoration the caller does not supply - a VAT named
    "VAT 19%" is displayed as "VAT 19% (19.0%)" - so the shown text is allowed
    to be the wanted text plus a suffix, but never something unrelated.
    """
    a, b = _norm(wanted), _norm(shown)
    return bool(a) and (a == b or b.startswith(a) or a in b)


def _norm(value: Any) -> str:
    s = str(value).strip().casefold()
    for junk in (" ", "€", "eur", " "):
        s = s.replace(junk, "")
    return s.replace(",", ".")


def _equal(expected: Any, actual: Any, loose: bool) -> bool:
    if not loose:
        return str(expected) == str(actual)
    return _norm(expected) == _norm(actual)
