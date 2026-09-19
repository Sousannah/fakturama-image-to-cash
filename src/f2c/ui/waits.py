"""Condition-based waiting.

There is no `sleep(2)` anywhere in this package. Every wait is a predicate with
a ceiling, which is what makes the flow survive a slow machine without being
needlessly slow on a fast one.
"""
from __future__ import annotations

import time
from typing import Any, Callable, List, Optional, TypeVar

from ..config import SETTINGS
from ..errors import TimeoutError_
from ..logging_setup import get

log = get("ui.waits")

T = TypeVar("T")


def wait_until(
    predicate: Callable[[], Optional[T]],
    timeout: Optional[float] = None,
    interval: Optional[float] = None,
    what: str = "condition",
) -> T:
    """Poll `predicate` until it returns something truthy; return it."""
    timeout = SETTINGS.default_timeout if timeout is None else timeout
    interval = SETTINGS.poll_interval if interval is None else interval
    deadline = time.monotonic() + timeout
    last_exc = None
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # transient UIA COM errors are normal
            last_exc = exc
        time.sleep(interval)
    raise TimeoutError_(
        "timed out after %.1fs waiting for %s (%d polls)%s"
        % (timeout, what, attempts, ("; last error: %s" % last_exc) if last_exc else "")
    )


def wait_gone(
    predicate: Callable[[], Any],
    timeout: Optional[float] = None,
    interval: Optional[float] = None,
    what: str = "element to disappear",
) -> None:
    timeout = SETTINGS.default_timeout if timeout is None else timeout
    interval = SETTINGS.poll_interval if interval is None else interval
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not predicate():
                return
        except Exception:
            return
        time.sleep(interval)
    raise TimeoutError_("timed out after %.1fs waiting for %s" % (timeout, what))


def wait_stable(
    snapshot: Callable[[], Any],
    stable_polls: Optional[int] = None,
    timeout: Optional[float] = None,
    interval: Optional[float] = None,
    what: str = "content",
):
    """Wait until `snapshot()` returns the same value N polls in a row.

    This implements the spec's "wait for the list to stabilize" (2.2): search
    results in Fakturama arrive asynchronously, so a result count of 0 a few
    milliseconds after typing means nothing.
    """
    stable_polls = SETTINGS.list_stable_polls if stable_polls is None else stable_polls
    timeout = SETTINGS.default_timeout if timeout is None else timeout
    interval = SETTINGS.poll_interval if interval is None else interval

    deadline = time.monotonic() + timeout
    previous = object()
    same = 0
    current = None
    while time.monotonic() < deadline:
        try:
            current = snapshot()
        except Exception:
            current = None
        if current == previous:
            same += 1
            if same >= stable_polls:
                log.debug("%s stabilised after %d identical polls", what, same)
                return current
        else:
            same = 0
            previous = current
        time.sleep(interval)
    log.warning("%s never fully stabilised within %.1fs; using last snapshot", what, timeout)
    return current


def retry(
    fn: Callable[[], T],
    attempts: int = 3,
    interval: float = 0.4,
    what: str = "action",
) -> T:
    """Retry a flaky UI action. Only for idempotent operations."""
    last: List[Exception] = []
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last.append(exc)
            log.debug("%s failed (attempt %d/%d): %s", what, i, attempts, exc)
            time.sleep(interval)
    raise last[-1]
