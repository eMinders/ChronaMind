"""Public queue contracts and error boundaries, independent of a database."""

from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import DeadlockDetected, ExclusionViolation, SerializationFailure
from pydantic import ValidationError

from implementation.queue import JobIdentityConflict, NewJob, QueueError, enqueue, process_one
from implementation.queue.worker import is_transaction_retryable

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def request(**values: object) -> NewJob:
    return NewJob.model_validate(dict(id="j", user_id="u", kind="extract", idempotency_key="stable") | values)


@pytest.mark.parametrize("name", ["id", "user_id", "kind", "idempotency_key"])
@pytest.mark.parametrize("value", ["", " ", "bad\x00value", True, None])
def test_required_identity_fields_are_explicit_nonblank_text(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        request(**{name: value})


def test_exact_identity_and_json_work_survive_round_trip() -> None:
    job = request(user_id="  u  ", payload={"values": ["synthetic", 1, True, None]})
    assert job.user_id == "  u  "
    assert NewJob.model_validate_json(job.model_dump_json()) == job
    with pytest.raises(ValidationError):
        job.kind = "another"  # type: ignore[misc]


def test_extraction_identity_is_complete_and_cannot_be_silently_partial() -> None:
    for partial in (
        {"extractor_version": "v1"},
        {"candidate_fingerprint": "fp"},
        {"turn_id": "t", "extractor_version": "v1"},
    ):
        with pytest.raises(ValidationError, match="complete_extraction_identity_required"):
            request(**partial)
    assert request(turn_id="t").turn_id == "t"
    assert request(turn_id="t", extractor_version="v1", candidate_fingerprint="fp").candidate_fingerprint == "fp"


def test_not_before_is_aware_and_normalized_without_borrowing_record_clock() -> None:
    with pytest.raises(ValidationError):
        request(not_before=datetime(2026, 10, 1))
    assert request(not_before=NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))).not_before == NOW


def test_private_input_values_are_not_echoed_by_model_error_strings() -> None:
    with pytest.raises(ValidationError) as error:
        request(unexpected="private-synthetic-value")
    assert "private-synthetic-value" not in str(error.value)


def test_only_transaction_conflicts_are_retryable_not_semantic_or_unknown_failures() -> None:
    assert is_transaction_retryable(SerializationFailure("synthetic"))
    assert is_transaction_retryable(DeadlockDetected("synthetic"))
    assert not is_transaction_retryable(ExclusionViolation("synthetic"))
    assert not is_transaction_retryable(RuntimeError("synthetic"))
    assert JobIdentityConflict("job_identity_conflict").sqlstate == "23505"
    assert QueueError("static").sqlstate == "22023"


@pytest.mark.parametrize("schema", ["public", "", "cme_" + "a" * 64, "cme_x;DROP SCHEMA public", "cme_x\x00"])
async def test_invalid_schema_fails_before_connection_use(schema: str) -> None:
    with pytest.raises(QueueError, match="isolated_engine_schema_identifier_required"):
        await enqueue(cast(AsyncConnection, None), request(), schema_name=schema)


async def test_copied_requests_are_revalidated_before_connection_use() -> None:
    copied = request().model_copy(update={"user_id": ""})
    with pytest.raises(ValidationError):
        await enqueue(cast(AsyncConnection, None), copied)


async def test_worker_preconditions_validate_identity_and_schema_before_connection_use() -> None:
    async def unused(_connection, _job, _tick) -> None:
        raise AssertionError("must not run")

    with pytest.raises(QueueError, match="nonblank_user_id_required"):
        await process_one(cast(AsyncConnection, None), "", unused)
    with pytest.raises(QueueError, match="isolated_engine_schema_identifier_required"):
        await process_one(cast(AsyncConnection, None), "u", unused, schema_name="public")
