"""``semantic.temporal``: bounds the model resolved, checked rather than trusted (W3.5, ``V2-D88``).

The engine never parses a date phrase -- "last spring", "الأسبوع الماضي", "先週" have no shared grammar -- so the model
resolves it against the turn's own instant and this module decides whether the answer is legal. Each check closes a
failure mode that costs correctness if it is left out, and each refusal is a static code rather than a repair.
"""

from datetime import UTC, datetime

import pytest

from custom_memory_engine.semantic.contracts import TemporalBounds
from custom_memory_engine.semantic.temporal import TemporalError, resolve, resolve_claim_bounds

T0 = datetime(2026, 3, 10, tzinfo=UTC)


def test_an_absent_bound_is_no_claim_and_uses_the_observation_instant() -> None:
    bounds = resolve(TemporalBounds(), occurred_at=T0)
    assert (bounds.start, bounds.end, bounds.basis) == (None, None, "inferred")


def test_an_explicit_bound_is_recorded_as_explicit() -> None:
    bounds = resolve(TemporalBounds(start="2026-01-01T00:00:00+00:00"), occurred_at=T0)
    assert bounds.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert bounds.basis == "explicit"


def test_a_naive_instant_is_refused_rather_than_reinterpreted_in_the_servers_zone() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(start="2026-01-01T00:00:00"), occurred_at=T0)
    assert refused.value.code == "event_start_must_carry_a_timezone"


def test_an_offset_is_converted_rather_than_stored_as_written() -> None:
    bounds = resolve(TemporalBounds(start="2026-01-01T02:00:00+02:00"), occurred_at=T0)
    assert bounds.start == datetime(2026, 1, 1, tzinfo=UTC)


def test_an_inverted_range_is_refused() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(start="2026-02-01T00:00:00+00:00", end="2026-01-01T00:00:00+00:00"), occurred_at=T0)
    assert refused.value.code == "event_range_inverted"


def test_an_empty_range_is_refused_because_the_schema_refuses_it_too() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(start="2026-01-01T00:00:00+00:00", end="2026-01-01T00:00:00+00:00"), occurred_at=T0)
    assert refused.value.code == "event_range_empty"


def test_an_asserted_claim_cannot_begin_after_it_was_uttered() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(start="2026-04-01T00:00:00+00:00"), occurred_at=T0)
    assert refused.value.code == "event_start_after_occurrence"


def test_a_planned_claim_may_begin_after_it_was_uttered() -> None:
    """ "I will move in June" is the one mode that legitimately points into the future."""
    bounds = resolve_claim_bounds(
        TemporalBounds(start="2026-04-01T00:00:00+00:00"), occurred_at=T0, assertion_mode="planned"
    )
    assert bounds.start == datetime(2026, 4, 1, tzinfo=UTC)
    # and every other mode still refuses it
    with pytest.raises(TemporalError):
        resolve_claim_bounds(
            TemporalBounds(start="2026-04-01T00:00:00+00:00"), occurred_at=T0, assertion_mode="asserted"
        )


def test_a_naive_anchor_is_refused() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(), occurred_at=datetime(2026, 3, 10))  # noqa: DTZ001 - the point of the test
    assert refused.value.code == "occurred_at_must_carry_a_timezone"


def test_a_malformed_instant_names_the_field_it_belongs_to() -> None:
    with pytest.raises(TemporalError) as refused:
        resolve(TemporalBounds(end="not a date"), occurred_at=T0)
    assert refused.value.code == "event_end_invalid"
