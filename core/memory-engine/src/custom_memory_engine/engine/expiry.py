"""Abstraction expiry: the pure rule and its content-free receipt (``A-014``, ``V2-D14``, ``V2-D97``).

``API_V2.md`` §4.7 says expiry "applies retention expiry; recorded as a scope event, not a deletion of knowledge". The
schema agrees with the document: ``abstraction_stats.expires_at`` is what :func:`~.staleness.staleness_inputs` reads to
derive the ``expired`` cause, so *stamping that column* is the whole effect — the belief becomes stale, stops rendering
as a fresh fact, and stays re-derivable. Nothing is deleted.

Two consequences follow, and both are improvements on the declaration they replace:

* the contract's ``deletes`` flag was wrong for this operation and is corrected to ``false``. A declared deletion that
  never happens is worse than no declaration: it invites a reviewer to assume a guarantee, and ``TB1``'s protection is
  aimed at an operation that never destroys anything;
* the *numbers* stay where ``V2-D14`` put them — in the profile (``abstraction_expiry.days_by_facet``). This module
  supplies the arithmetic (``valid_from + days``) and refuses to invent a default, so a deployment without a policy
  expires nothing and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class ExpiryError(ValueError):
    """A static code; never a date, a scope or a facet name."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExpiryReceipt:
    """Content-free outcome of ``expire``: how many were stamped, how many were candidates, and the belief time.

    ``PurgeReceipt``'s sibling and for the same reason (``API_V2.md`` §1.1): an expiry touches no caller-named
    artifact, so there is no id to return and an id would be exactly what a maintenance receipt may not carry.
    ``considered`` makes ``expired=0`` informative — it separates "nothing was due" from "nothing was there".
    """

    expired: int
    considered: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        for name in ("expired", "considered"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExpiryError("counts_must_be_non_negative")
        if self.expired > self.considered:
            raise ExpiryError("expired_cannot_exceed_considered")


def expiry_instant(believed_from: datetime, days: int) -> datetime:
    """When a belief first becomes expired: the beginning of its validity, plus the policy's window.

    Anchored on the artifact's own ``valid_from`` rather than on "now" or on the request's ``as_of``, so the stored
    value is a function of the row and the policy. A window restarted on every expiry run would expire nothing twice
    and everything never — the opposite of retention.
    """
    if not isinstance(believed_from, datetime) or believed_from.tzinfo is None:
        raise ExpiryError("believed_from_must_carry_a_timezone")
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ExpiryError("expiry_window_must_be_positive")
    return believed_from + timedelta(days=days)


def due(believed_from: datetime, days: int, *, as_of: datetime) -> bool:
    """Whether a belief with this window is expired as of ``as_of``. The rule the SQL statement mirrors."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ExpiryError("as_of_must_carry_a_timezone")
    return expiry_instant(believed_from, days) <= as_of


__all__ = ["ExpiryError", "ExpiryReceipt", "due", "expiry_instant"]
