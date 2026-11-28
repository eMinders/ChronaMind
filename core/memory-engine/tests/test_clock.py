"""Clock allocation arithmetic, instant validation and pre-I/O boundaries."""

from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from psycopg import AsyncConnection
from psycopg.errors import InvalidParameterValue
from pydantic import ValidationError

from custom_memory_engine.clock import ClockError, ClockTick, advance_instant, allocate_clock

NOW = datetime(2026, 10, 1, tzinfo=UTC)
TICK = timedelta(microseconds=1)


@pytest.mark.parametrize(
    ("raw", "previous", "expected"),
    [
        (NOW, None, NOW),
        (NOW, NOW - TICK, NOW),
        (NOW, NOW, NOW + TICK),
        (NOW - timedelta(days=10), NOW, NOW + TICK),
        (NOW + timedelta(days=10), NOW, NOW + timedelta(days=10)),
    ],
)
def test_strict_tick_follows_raw_time_except_tie_or_out_of_order(raw, previous, expected) -> None:
    assert advance_instant(raw, previous) == expected


def test_repeated_and_out_of_order_inputs_produce_exact_microsecond_ticks() -> None:
    previous = None
    observed = []
    for raw in (NOW, NOW, NOW - TICK, NOW - timedelta(days=1), NOW + timedelta(days=1)):
        previous = advance_instant(raw, previous)
        observed.append(previous)
    assert observed == [NOW, NOW + TICK, NOW + 2 * TICK, NOW + 3 * TICK, NOW + timedelta(days=1)]


def test_normalization_compares_instants_not_wall_times_or_dst_fold() -> None:
    raw = NOW.astimezone(timezone(timedelta(hours=5, minutes=45)))
    assert advance_instant(raw) == NOW
    assert advance_instant(raw).tzinfo is UTC
    zone = ZoneInfo("America/New_York")
    early = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    late = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    assert advance_instant(early, late) == late.astimezone(UTC) + TICK
    assert advance_instant(late, early) == late.astimezone(UTC)


@pytest.mark.parametrize("invalid", [datetime(2026, 1, 1), "2026-10-01T00:00:00Z", None, True, 123])
def test_only_aware_python_instants_are_accepted(invalid: object) -> None:
    with pytest.raises(ClockError) as raw:
        advance_instant(cast(datetime, invalid))
    assert raw.value.sqlstate == "22023"
    assert str(raw.value) == "clock_aware_instant_required"
    if invalid is not None:
        with pytest.raises(ClockError, match="clock_aware_instant_required"):
            advance_instant(NOW, cast(datetime, invalid))


def test_no_wraparound_at_the_largest_representable_instant() -> None:
    maximum = datetime.max.replace(tzinfo=UTC)
    assert advance_instant(maximum, maximum - TICK) == maximum
    with pytest.raises(InvalidParameterValue) as exhausted:
        advance_instant(maximum, maximum)
    assert exhausted.value.sqlstate == "22023"
    assert str(exhausted.value) == "clock_instant_exhausted"


def test_utc_conversion_overflow_is_a_static_validation_error() -> None:
    too_early = datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(ClockError, match="clock_instant_out_of_range"):
        advance_instant(too_early)


def test_tick_is_immutable_normalized_and_serializable() -> None:
    tick = ClockTick(instant=NOW + TICK, raw_instant=NOW, mode="replay", advanced=True)
    assert ClockTick.model_validate_json(tick.model_dump_json()) == tick
    with pytest.raises(ValidationError):
        tick.advanced = False
    offset = timezone(timedelta(hours=-3))
    normalized = ClockTick(
        instant=NOW.astimezone(offset), raw_instant=NOW.astimezone(offset), mode="live", advanced=False
    )
    assert normalized.instant.tzinfo is UTC and normalized.raw_instant.tzinfo is UTC


@pytest.mark.parametrize(
    "changes",
    [
        {"instant": NOW - TICK},
        {"advanced": True},
        {"instant": NOW + TICK, "advanced": False},
        {"mode": "unknown"},
        {"raw_instant": datetime(2026, 1, 1)},
    ],
)
def test_tick_cannot_claim_inconsistent_or_naive_allocation(changes) -> None:
    values = dict(instant=NOW, raw_instant=NOW, mode="live", advanced=False)
    values.update(changes)
    with pytest.raises((ValidationError, ClockError)):
        ClockTick.model_validate(values)


@pytest.mark.parametrize("user", ["", " ", None, True, 12])
async def test_invalid_tenant_is_rejected_before_connection_use(user: object) -> None:
    with pytest.raises(ClockError, match="clock_user_id_required"):
        await allocate_clock(cast(AsyncConnection, None), cast(str, user))


@pytest.mark.parametrize("schema", ["", "public", "cme_" + "a" * 64, "cme_engine;DROP SCHEMA public", "cme_e\0"])
async def test_invalid_schema_is_rejected_before_connection_use(schema: str) -> None:
    with pytest.raises(ClockError, match="isolated_engine_schema_identifier_required"):
        await allocate_clock(cast(AsyncConnection, None), "u", schema_name=schema)


async def test_invalid_replay_instant_is_rejected_before_connection_use() -> None:
    with pytest.raises(ClockError, match="clock_aware_instant_required"):
        await allocate_clock(cast(AsyncConnection, None), "u", replay_time=datetime(2026, 1, 1))
