"""Exception hierarchy.

The important distinction is between *recoverable* automation problems
(retry / fall back to another locator tier) and *manual review* gates,
which the spec demands we stop on rather than guess.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class F2CError(Exception):
    """Base for everything this package raises."""


class ExtractionError(F2CError):
    """The order image could not be turned into a trustworthy OrderDoc."""


class ReconciliationError(ExtractionError):
    """Extracted numbers do not add up; we refuse to touch the UI."""


class LocatorError(F2CError):
    """No locator tier managed to resolve a logical control."""

    def __init__(self, logical_name: str, tried: Any = None):
        self.logical_name = logical_name
        self.tried = tried or []
        super().__init__(
            "could not resolve control %r; tried %d strategies: %s"
            % (logical_name, len(self.tried), self.tried)
        )


class TimeoutError_(F2CError):
    """A wait condition never became true."""


class VerificationError(F2CError):
    """A post-condition read back a value that does not match the source."""

    def __init__(self, what: str, expected: Any, actual: Any):
        self.what, self.expected, self.actual = what, expected, actual
        super().__init__("%s: expected %r, got %r" % (what, expected, actual))


class ManualReviewRequired(F2CError):
    """An ambiguity gate from the spec fired.

    Raised (never swallowed) for: conflicting/ambiguous Debtor rows, multiple
    or conflicting payment methods, conflicting VAT definitions, a product that
    does not reappear after saving, an unavailable invoice payment method, and
    any totals mismatch.
    """

    def __init__(self, reason: str, context: Optional[Dict[str, Any]] = None):
        self.reason = reason
        self.context = context or {}
        super().__init__(reason)
