"""The step-retry harness, and the parked-control rule it depends on.

These are the two mechanisms that turn "a different step stumbles on every run"
into a run that finishes: the resolver refuses to hand back a control that SWT
has parked off-screen, and a step whose body is safe to repeat is repeated.
"""
from __future__ import annotations

import pytest

from f2c.errors import ManualReviewRequired, VerificationError
from f2c.ui.uia import PARKED_COORDINATE, Rect


# --------------------------------------------------------------------------- #
# geometry: what counts as "on screen"
# --------------------------------------------------------------------------- #
def test_a_normal_control_is_on_screen():
    assert Rect(100, 200, 260, 224).is_onscreen()


def test_a_maximized_shell_origin_is_still_on_screen():
    """A maximized Eclipse window reports (-9, -9) - that is not parked."""
    assert Rect(-9, -9, 1929, 1029).is_onscreen()


def test_a_parked_editor_is_not_on_screen():
    """The real rectangle of the Product editor in the run that could not set
    its VAT combo."""
    assert not Rect(-31610, -31896, -30083, -31420).is_onscreen()
    assert not Rect(-31464, -31554, -31360, -31526).is_onscreen()


def test_the_parking_threshold_sits_between_the_two():
    assert Rect(-9, -9, 100, 100).left > PARKED_COORDINATE
    assert Rect(-31464, -31554, -31360, -31526).left < PARKED_COORDINATE


def test_an_empty_rectangle_is_not_on_screen():
    assert not Rect(0, 0, 0, 0).is_onscreen()


# --------------------------------------------------------------------------- #
# reading a control's VALUE rather than its caption
#
# SWT names a Text widget after its caption, so an EMPTY input reports the
# caption when asked for its text. That illusion cost two separate failures: a
# blank "Account" box looked like it held the word "Account" and was cleared
# over and over, and a good write looked like a failed one - which sent
# verification down the programmatic path that updates the display without
# telling the application.
# --------------------------------------------------------------------------- #
class _FakePattern:
    def __init__(self, value):
        self.Value = value


class _FakeControl:
    """Enough of a uiautomation control for text() / value_text()."""

    def __init__(self, name, value=None):
        self.Name = name
        self._value = value

    def GetValuePattern(self):
        if self._value is None:
            raise RuntimeError("no value pattern")
        return _FakePattern(self._value)

    def GetLegacyIAccessiblePattern(self):
        raise RuntimeError("none")

    def GetTextPattern(self):
        raise RuntimeError("none")


def test_an_empty_field_reports_no_value_even_though_it_has_a_caption():
    from f2c.ui.uia import Element

    el = Element(_FakeControl(name="Account", value=""))
    assert el.value_text() == ""          # what verification must see
    assert el.text() == "Account"         # what the caption-aware reader sees


def test_a_filled_field_reports_its_value():
    from f2c.ui.uia import Element

    el = Element(_FakeControl(name="Item Number", value="CHR-ERG-01"))
    assert el.value_text() == "CHR-ERG-01"


# --------------------------------------------------------------------------- #
# the retry harness
# --------------------------------------------------------------------------- #
class FakeApp:
    def __init__(self):
        self.foreground_calls = 0

    def ensure_foreground(self):
        self.foreground_calls += 1
        return True

    def window(self):
        return None


class FakeResolver:
    def __init__(self):
        self.invalidations = 0

    def invalidate_scopes(self):
        self.invalidations += 1


class FakeArtifacts:
    """Just enough of RunArtifacts for the harness."""

    def __init__(self):
        from f2c.artifacts import StepRecord

        self._record = StepRecord
        self.steps = []
        self.failures = []
        self.screenshots = []
        self.extra = {}

    def begin(self, spec_ref, title):
        rec = self._record(index=len(self.steps) + 1, spec_ref=spec_ref, title=title)
        self.steps.append(rec)
        return rec

    def end(self, rec, status="ok", detail=""):
        rec.status = status
        rec.detail = detail

    def screenshot(self, name, region=None, rec=None):
        self.screenshots.append(name)
        return None

    def record_failure(self, exc, element=None, name="failure"):
        self.failures.append(name)


@pytest.fixture
def ctx(sample_doc):
    from f2c.flow.context import Ctx

    # every test passes settle=0.0, so the harness never sleeps here
    return Ctx(doc=sample_doc, app=FakeApp(), resolver=FakeResolver(), art=FakeArtifacts())


def _run(ctx, body, attempts=3, recover=None, spec="X", title="step"):
    """Drive a step body through the harness the way a stage does."""
    for attempt in ctx.retried_step(
        spec, title, attempts=attempts, recover=recover, settle=0.0
    ):
        with attempt as rec:
            body(rec)


def test_a_body_that_works_runs_once(ctx):
    calls = []
    _run(ctx, lambda rec: calls.append(1))
    assert len(calls) == 1
    assert ctx.art.steps[0].status == "ok"
    assert ctx.art.steps[0].attempts == 1


def test_a_transient_failure_is_retried_and_the_step_passes(ctx):
    calls = []

    def body(rec):
        calls.append(1)
        if len(calls) < 3:
            raise VerificationError("value written to x", "19%", "Tax-free")

    _run(ctx, body)

    assert len(calls) == 3
    assert ctx.art.steps[0].status == "ok"
    assert ctx.art.steps[0].attempts == 3
    # the recovered failures are kept, so a run that limped still says so
    assert len(ctx.art.steps[0].recovered) == 2
    assert "Tax-free" in ctx.art.steps[0].recovered[0]
    # nothing is reported as a failure of the run
    assert ctx.art.failures == []


def test_the_last_failure_still_ends_the_run(ctx):
    def body(rec):
        raise VerificationError("value written to x", "19%", "Tax-free")

    with pytest.raises(VerificationError):
        _run(ctx, body, attempts=2)

    rec = ctx.art.steps[0]
    assert rec.status == "failed"
    assert rec.attempts == 2
    assert "after 2 attempts" in rec.detail
    assert ctx.art.failures  # a traceback and screenshot were captured


def test_a_manual_review_gate_still_stops_the_run(ctx):
    """A real ambiguity must not be retried into silence - it is re-checked,
    and then it still stops the run, as a manual-review rather than a crash."""

    def body(rec):
        raise ManualReviewRequired("2 debtors match the source exactly")

    with pytest.raises(ManualReviewRequired):
        _run(ctx, body, attempts=2)

    assert ctx.art.steps[0].status == "manual-review"


def test_recovery_runs_between_attempts_but_not_before_the_first(ctx):
    recoveries = []
    calls = []

    def body(rec):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("swallowed keystroke")

    _run(ctx, body, recover=lambda: recoveries.append(1))

    assert len(calls) == 3
    assert len(recoveries) == 2                  # before attempts 2 and 3 only
    assert ctx.app.foreground_calls == 2         # same
    assert ctx.resolver.invalidations == 2


def test_a_broken_recovery_does_not_mask_the_retry(ctx):
    calls = []

    def body(rec):
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("swallowed keystroke")

    def recover():
        raise RuntimeError("the editor would not come forward")

    _run(ctx, body, recover=recover)
    assert len(calls) == 2
    assert ctx.art.steps[0].status == "ok"


def test_a_status_set_by_a_failed_attempt_does_not_stick(ctx):
    """A body that records a status and then fails must not leave that status
    behind when the retry succeeds."""
    calls = []

    def body(rec):
        calls.append(1)
        if len(calls) < 2:
            ctx.art.end(rec, "manual-review", "could not read the footer")
            raise ManualReviewRequired("could not read the footer")
        ctx.art.end(rec, "ok", "totals match the source")

    _run(ctx, body)
    assert ctx.art.steps[0].status == "ok"
    assert ctx.art.steps[0].detail == "totals match the source"


def test_a_body_may_return_out_of_the_step(ctx):
    """Several stages return from inside the step - the loop must not run the
    body a second time when it does."""
    calls = []

    def stage():
        for attempt in ctx.retried_step("X", "step", settle=0.0):
            with attempt:
                calls.append(1)
                return "done"
        return "fell through"

    assert stage() == "done"
    assert len(calls) == 1


def test_attempts_of_one_behaves_like_a_plain_step(ctx):
    calls = []

    def body(rec):
        calls.append(1)
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        _run(ctx, body, attempts=1)
    assert len(calls) == 1
    assert "attempts" not in ctx.art.steps[0].detail


# --------------------------------------------------------------------------- #
# the precondition guard
#
# This exists because retrying a step blindly produced a WRONG ORDER: the
# Order's product picker appends an item line, so an attempt that selected the
# product and then failed on the way out had already done its job, and the
# retry put the same product on the order again. Total Net came out as 650.00
# instead of 570.00.
# --------------------------------------------------------------------------- #
def test_the_goal_being_reached_by_a_failed_attempt_stops_the_replay(ctx):
    calls = []
    on_the_line = []

    def body(rec):
        calls.append(1)
        on_the_line.append(1)          # the side effect landed...
        raise RuntimeError("...and then the dialog vanished")

    for attempt in ctx.retried_step(
        "3.12", "select the product", settle=0.0,
        precondition=lambda: bool(on_the_line),
    ):
        with attempt as rec:
            body(rec)

    assert len(calls) == 1, "the body must not run again once the goal is met"
    assert len(on_the_line) == 1, "the product was added to the order twice"
    assert ctx.art.steps[0].status == "ok"
    assert "reached the goal" in ctx.art.steps[0].recovered[-1]


def test_an_unmet_precondition_still_allows_the_retry(ctx):
    calls = []

    def body():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("the dialog never opened")

    for attempt in ctx.retried_step(
        "3.12", "select the product", settle=0.0, precondition=lambda: False
    ):
        with attempt:
            body()

    assert len(calls) == 2
    assert ctx.art.steps[0].status == "ok"


def test_a_precondition_that_raises_is_treated_as_not_met(ctx):
    calls = []

    def body():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("the dialog never opened")

    def precondition():
        raise RuntimeError("could not read the line")

    for attempt in ctx.retried_step(
        "3.12", "select the product", settle=0.0, precondition=precondition
    ):
        with attempt:
            body()

    assert len(calls) == 2
    assert ctx.art.steps[0].status == "ok"


def test_a_short_circuited_step_hands_control_back_after_the_loop(ctx):
    """The shape `flow/product.py` uses: the caller needs a return value, and
    the harness may finish the step WITHOUT running the body again. Falling off
    the end then returns None, which a caller reads as "it did not work" - and
    that sent a perfectly good Order to manual review."""
    done = []

    def select():
        for attempt in ctx.retried_step(
            "3.12", "select the product", settle=0.0, precondition=lambda: bool(done)
        ):
            with attempt:
                done.append(1)
                raise RuntimeError("the dialog vanished on the way out")
        return True          # reached only via the precondition short-circuit

    assert select() is True


def test_the_precondition_is_never_consulted_before_the_first_attempt(ctx):
    """A step whose goal looks met at the start must still run once - the
    caller decides whether to skip it, not the harness."""
    checks = []
    calls = []

    for attempt in ctx.retried_step(
        "3.12", "select the product", settle=0.0,
        precondition=lambda: checks.append(1) or True,
    ):
        with attempt:
            calls.append(1)

    assert calls == [1]
    assert checks == []


def test_a_screenshot_is_taken_once_the_step_finally_passes(ctx):
    calls = []

    def stage():
        for attempt in ctx.retried_step("X", "step", shoot=True, settle=0.0):
            with attempt:
                calls.append(1)
                if len(calls) < 2:
                    raise RuntimeError("no")

    stage()
    assert len(ctx.art.screenshots) == 1
