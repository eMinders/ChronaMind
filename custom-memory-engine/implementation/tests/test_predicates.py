"""Public query contracts, exact guards and SQL/data separation."""

from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from psycopg import AsyncConnection
from psycopg.errors import InvalidParameterValue
from pydantic import ValidationError

from implementation.engine.predicates import (
    TemporalQuery,
    TemporalQueryError,
    TemporalResult,
    TemporalVersion,
    TimeRange,
    audit_as_of,
    compile_predicate,
    current_state,
    fetch_versions,
    historical_as_of,
    historical_window,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
PAST = datetime(2026, 4, 1, tzinfo=UTC)
TICK = timedelta(microseconds=1)


@pytest.mark.parametrize(
    "query",
    [current_state("u", NOW), historical_as_of("u", PAST, NOW), historical_window("u", PAST, NOW, NOW)],
)
def test_ordinary_queries_bind_the_reference_belief_not_just_open_upper(query: TemporalQuery) -> None:
    compiled = compile_predicate(query)
    statement = compiled.statement.as_string()
    assert '"recorded_range" @> %(reference_time)s::timestamptz' in statement
    assert "upper_inf" not in statement
    assert "\"status\" = 'active'" in statement
    assert compiled.parameters["reference_time"] == NOW
    assert compiled.parameters["user_id"] == "u"
    assert TemporalQuery.model_validate_json(query.model_dump_json()) == query


def test_window_is_explicit_nonempty_half_open_overlap() -> None:
    compiled = compile_predicate(historical_window("u", PAST, NOW, NOW))
    assert (
        "&& tstzrange(%(window_start)s::timestamptz, %(window_end)s::timestamptz, '[)')"
        in compiled.statement.as_string()
    )
    assert compiled.parameters["window_start"] == PAST
    assert compiled.parameters["window_end"] == NOW


def test_guard_equality_and_microsecond_failure_have_sqlstate_22023() -> None:
    assert historical_as_of("u", NOW, NOW).target_time == NOW
    assert historical_window("u", PAST, NOW, NOW).window_end == NOW
    with pytest.raises(InvalidParameterValue) as future:
        historical_as_of("u", NOW + TICK, NOW)
    assert future.value.sqlstate == "22023"
    assert str(future.value) == "historical_target_after_reference"
    for start, end, code in (
        (NOW, NOW, "historical_window_must_be_nonempty"),
        (NOW, PAST, "historical_window_must_be_nonempty"),
        (PAST, NOW + TICK, "historical_window_after_reference"),
    ):
        with pytest.raises(TemporalQueryError) as invalid:
            historical_window("u", start, end, NOW)
        assert invalid.value.sqlstate == "22023" and invalid.value.code == code


def test_audit_uses_explicit_database_clock_and_has_unrestricted_valid_target() -> None:
    target = datetime(2099, 1, 1, tzinfo=UTC)
    query = audit_as_of("u", target, NOW)
    with pytest.raises(TemporalQueryError, match="audit_database_clock_required"):
        compile_predicate(query)
    compiled = compile_predicate(query, database_clock=NOW)
    assert compiled.parameters["target_time"] == target
    assert compiled.parameters["belief_time"] == NOW
    assert "\"status\" IN ('active', 'archived')" in compiled.statement.as_string()
    with pytest.raises(TemporalQueryError) as future:
        compile_predicate(audit_as_of("u", PAST, NOW + TICK), database_clock=NOW)
    assert future.value.sqlstate == "22023"
    assert future.value.code == "audit_belief_after_database_clock"


def test_timezone_normalization_compares_instants_including_dst_fold() -> None:
    offset_now = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert current_state("u", offset_now).reference_time == NOW
    reference = current_state("u", offset_now).reference_time
    assert reference is not None and reference.tzinfo is UTC
    east = ZoneInfo("America/New_York")
    early = datetime(2026, 11, 1, 1, 30, tzinfo=east, fold=0)
    late = datetime(2026, 11, 1, 1, 30, tzinfo=east, fold=1)
    assert early.astimezone(UTC) < late.astimezone(UTC)
    with pytest.raises(TemporalQueryError, match="historical_target_after_reference"):
        historical_as_of("u", late, early)
    assert historical_as_of("u", early, late).target_time == early.astimezone(UTC)


@pytest.mark.parametrize("user_id", ["", " ", None, True, 12])
def test_nonempty_explicit_user_identity_required(user_id: object) -> None:
    with pytest.raises(ValidationError):
        current_state(cast(str, user_id), NOW)


@pytest.mark.parametrize("instant", [datetime(2026, 1, 1), "2026-01-01", None, True, 12])
def test_query_instants_are_aware_datetimes(instant: object) -> None:
    with pytest.raises(ValidationError):
        current_state("u", cast(datetime, instant))


def test_deserialization_cannot_bypass_mode_or_future_guards() -> None:
    data = current_state("u", NOW).model_dump()
    data["target_time"] = PAST
    with pytest.raises(ValidationError, match="mode_parameter_mismatch"):
        TemporalQuery.model_validate(data)
    data = historical_as_of("u", PAST, NOW).model_dump()
    data["target_time"] = NOW + TICK
    with pytest.raises(TemporalQueryError, match="historical_target_after_reference"):
        TemporalQuery.model_validate(data)
    data["unexpected"] = "private-value"
    with pytest.raises(ValidationError) as invalid:
        TemporalQuery.model_validate(data)
    assert "private-value" not in str(invalid.value)


def test_sql_and_values_stay_separate_and_identifiers_are_quoted() -> None:
    user = "u'; DROP TABLE memory_version; --"
    alias = 'm"; DROP TABLE memory_version; --'
    compiled = compile_predicate(current_state(user, NOW), alias=alias)
    text = compiled.statement.as_string()
    assert user not in text
    assert compiled.parameters["user_id"] == user
    assert '"m""; DROP TABLE memory_version; --"."user_id"' in text
    with pytest.raises(TypeError):
        compiled.parameters["user_id"] = "another"  # type: ignore[index]
    assert current_state(user, NOW).user_id == user


def version(*, user: str = "u", status: str = "active") -> TemporalVersion:
    return TemporalVersion.model_validate(
        dict(
            id="v",
            memory_key="key",
            user_id=user,
            status=status,
            subject_key="self",
            predicate_key="diet",
            single_valued=True,
            content="synthetic",
            valid_range=dict(lower=PAST.isoformat(), upper=None, lower_inclusive=True, upper_inclusive=False),
            recorded_range=dict(lower=PAST.isoformat(), upper="infinity", lower_inclusive=True, upper_inclusive=True),
            layer="semantic",
            valid_from_basis="explicit",
            assertion_mode="asserted",
            confidence=None,
            importance=None,
            reinforcement_count=0,
            extractor_version="synthetic",
            archival_branch=None,
        )
    )


def test_full_version_ranges_and_results_roundtrip_with_literal_infinity_distinct_from_unbounded() -> None:
    row = version()
    assert row.valid_range.upper is None
    assert row.recorded_range.upper == "infinity" and row.recorded_range.upper_inclusive
    assert TemporalVersion.model_validate_json(row.model_dump_json()) == row
    assert TimeRange.model_validate_json(row.valid_range.model_dump_json()) == row.valid_range
    result = TemporalResult(mode="current_state", user_id="u", versions=(row,))
    assert result.for_answer_context() == (row,)
    assert TemporalResult.model_validate_json(result.model_dump_json()) == result


def test_inspector_audit_data_cannot_be_requested_as_answer_context() -> None:
    result = TemporalResult(mode="audit_as_of", user_id="u", versions=(version(status="archived"),))
    with pytest.raises(TemporalQueryError, match="audit_not_for_answer_context"):
        result.for_answer_context()
    assert result.versions[0].status == "archived"
    with pytest.raises(ValidationError, match="archived_version_in_ordinary_result"):
        TemporalResult(mode="current_state", user_id="u", versions=result.versions)
    with pytest.raises(ValidationError, match="cross_user_temporal_result"):
        TemporalResult(mode="audit_as_of", user_id="u", versions=(version(user="other"),))


@pytest.mark.parametrize(
    "schema", ["", "public", "cme_engine;DROP SCHEMA public", "cme_" + "a" * 64, "cme_engine\x00"]
)
async def test_execution_rejects_non_engine_schema_before_any_connection_use(schema: str) -> None:
    with pytest.raises(TemporalQueryError, match="isolated_engine_schema"):
        await fetch_versions(cast(AsyncConnection, None), current_state("u", NOW), schema_name=schema)


def test_copied_models_cannot_bypass_guards_at_compilation() -> None:
    point = historical_as_of("u", PAST, NOW).model_copy(update={"target_time": NOW + TICK})
    with pytest.raises(TemporalQueryError, match="historical_target_after_reference"):
        compile_predicate(point)
    window = historical_window("u", PAST, NOW, NOW).model_copy(update={"window_end": NOW + TICK})
    with pytest.raises(TemporalQueryError, match="historical_window_after_reference"):
        compile_predicate(window)


async def test_execution_revalidates_copied_models_before_connection_use() -> None:
    point = historical_as_of("u", PAST, NOW).model_copy(update={"target_time": NOW + TICK})
    with pytest.raises(TemporalQueryError, match="historical_target_after_reference"):
        await fetch_versions(cast(AsyncConnection, None), point)
