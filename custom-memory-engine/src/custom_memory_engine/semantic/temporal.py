"""Deterministic validation of a temporal bound the model resolved (``V2-D88``).

The engine does not parse dates. It cannot: "last spring", "الأسبوع الماضي", "先週" and "end of Q3" have no shared
grammar, and every engine in the 2026 audit that tried -- Hindsight's English-only ``DateparserQueryAnalyzer``,
Cognee's ``languages=["en"]`` hints -- ends up silently dropping the queries it cannot parse, which is worse than
refusing them. The model resolves the phrase against the turn's own instant; this module checks that the answer is a
legal one.

Four checks, each a static code, each closing a failure mode that costs correctness if left out:

* **Aware.** A naive instant is a bug, not a preference: the engine's whole temporal model is UTC instants, and a
  naive one would be silently reinterpreted in whatever zone the server happens to have.
* **Ordered.** ``start <= end`` when both are present.
* **Anchored.** ``start`` is not *after* the turn's ``occurred_at``. A claim cannot begin in the future relative to the
  statement that asserted it; a model that reports so has mis-resolved the phrase, and storing it would put an event on
  the timeline before it was ever mentioned.
* **Non-degenerate.** When both are present the range is not empty. An empty ``[t, t)`` is what a naive "same day"
  resolution produces, and the schema refuses it (``valid_wellformed``); refusing it here keeps the error a typed code.

An **absent** bound is not a failure -- it means "no temporal claim", which is the common case, and the ingest path
then uses the observation instant exactly as it always has.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from custom_memory_engine.semantic.contracts import TemporalBounds


class TemporalError(ValueError):
    """A static code; never carries an instant the caller supplied."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedBounds:
    """The validated pair, as aware UTC instants (``None`` where the claim asserted no bound)."""

    start: datetime | None
    end: datetime | None

    @property
    def basis(self) -> Literal["explicit", "inferred"]:
        """The ``valid_from_basis`` the schema records: ``explicit`` only when the claim named a start itself."""
        return "explicit" if self.start is not None else "inferred"


def _parse(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise TemporalError(f"{field}_invalid") from None
    if parsed.tzinfo is None:
        raise TemporalError(f"{field}_must_carry_a_timezone")
    return parsed.astimezone(UTC)


def resolve(bounds: TemporalBounds, *, occurred_at: datetime) -> ResolvedBounds:
    """Validate ``bounds`` against the turn's instant and return them as UTC instants.

    ``occurred_at`` must itself be aware; a caller that cannot say when something was said cannot ask when it was true.
    """
    if not isinstance(bounds, TemporalBounds):
        raise TemporalError("temporal_bounds_invalid")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise TemporalError("occurred_at_must_carry_a_timezone")
    anchor = occurred_at.astimezone(UTC)
    start = None if bounds.start is None else _parse(bounds.start, "event_start")
    end = None if bounds.end is None else _parse(bounds.end, "event_end")
    if start is not None and end is not None and start > end:
        raise TemporalError("event_range_inverted")
    if start is not None and start > anchor:
        # "I will move in June" begins after the statement that planned it, and that is correct. Exactly one assertion
        # mode may point into the future, and it is `planned`; :func:`resolve_claim_bounds` is that path, so a reader
        # of either function can see which modes allow what.
        raise TemporalError("event_start_after_occurrence")
    if start is not None and end is not None and start == end:
        raise TemporalError("event_range_empty")
    return ResolvedBounds(start, end)


def resolve_claim_bounds(bounds: TemporalBounds, *, occurred_at: datetime, assertion_mode: str) -> ResolvedBounds:
    """As :func:`resolve`, but a ``planned`` claim may legitimately begin after the statement that planned it.

    Separate entry point rather than a boolean flag so the exception is visible at every call site: exactly one
    assertion mode is allowed to point into the future, and a reader can see which.
    """
    if assertion_mode == "planned":
        if not isinstance(bounds, TemporalBounds):
            raise TemporalError("temporal_bounds_invalid")
        if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
            raise TemporalError("occurred_at_must_carry_a_timezone")
        start = None if bounds.start is None else _parse(bounds.start, "event_start")
        end = None if bounds.end is None else _parse(bounds.end, "event_end")
        if start is not None and end is not None and start > end:
            raise TemporalError("event_range_inverted")
        if start is not None and end is not None and start == end:
            raise TemporalError("event_range_empty")
        return ResolvedBounds(start, end)
    return resolve(bounds, occurred_at=occurred_at)


__all__ = ["ResolvedBounds", "TemporalError", "resolve", "resolve_claim_bounds"]
