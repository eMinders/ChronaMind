"""``semantic/slow_lane.py`` under lock contention, without a database (#252 criterion 4).

A serialization failure or a deadlock in the claim or completion transaction is retried exactly once, then surfaces as
the queue's own static error, ``QueueError("queue_contention_conflict")``, with no chained cause: the original
``psycopg`` error carries the server's message, which can name a relation or a row, so it must not ride along. The
tenant transaction is replaced by one that fails the way PostgreSQL does, at its first statement or at ``COMMIT``.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from typing import cast

import pytest
from psycopg import AsyncConnection, pq
from psycopg.errors import DeadlockDetected, SerializationFailure

from custom_memory_engine.clock import ClockTick
from custom_memory_engine.queue.contracts import Job, QueueError
from custom_memory_engine.semantic import slow_lane

ROLE = "cme_rt_contention"
CONFLICTS = [SerializationFailure, DeadlockDetected]
type ScopeFactory = Callable[..., AbstractAsyncContextManager[None]]


class _EmptyQueueCursor:
    """The claim's ``SELECT ... FOR UPDATE SKIP LOCKED`` finding nothing; any other use is a test failure."""

    async def __aenter__(self) -> "_EmptyQueueCursor":
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def execute(self, query: object, params: object = None) -> None:
        return None

    async def fetchone(self) -> None:
        return None


class _IdleConnection:
    """An idle connection whose only statement is the empty claim; the tenant transaction is replaced."""

    def __init__(self) -> None:
        self.info = SimpleNamespace(transaction_status=pq.TransactionStatus.IDLE)
        self.closed = False

    def cursor(self, *args: object, **kwargs: object) -> _EmptyQueueCursor:
        return _EmptyQueueCursor()


def _conflicting_scope(
    error: type[Exception], *, failures: int, at_commit: bool = False
) -> tuple[ScopeFactory, list[str]]:
    """A ``tenant_scope`` stand-in failing its first ``failures`` transactions; records each one it opens."""
    opened: list[str] = []

    @asynccontextmanager
    async def scope(connection: object, user_id: str, *, role: str) -> AsyncIterator[None]:
        opened.append(role)
        failing = len(opened) <= failures
        if failing and not at_commit:
            raise error("conflict naming relation job and row j-1")
        yield
        if failing:
            raise error("conflict naming relation job and row j-1")

    return scope, opened


def _running_job() -> Job:
    return Job(
        id="j-1",
        user_id="alice",
        kind="extract",
        turn_id=None,
        idempotency_key="k-1",
        status="running",
        attempts=1,
        next_run_at=datetime(2026, 3, 1, tzinfo=UTC),
        payload=None,
        extractor_version=None,
        candidate_fingerprint=None,
    )


async def _never_called(connection: AsyncConnection, job: Job, tick: ClockTick) -> None:
    raise AssertionError("work_must_not_run_in_a_failed_transaction")


def _assert_static_unchained(error: QueueError) -> None:
    assert str(error) == "queue_contention_conflict"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert "relation" not in str(error) and "j-1" not in str(error)


@pytest.mark.parametrize("conflict", CONFLICTS)
async def test_a_claim_that_keeps_conflicting_is_retried_once_then_raises_the_queue_contention_error(
    monkeypatch: pytest.MonkeyPatch, conflict: type[Exception]
) -> None:
    scope, opened = _conflicting_scope(conflict, failures=10)
    monkeypatch.setattr(slow_lane, "tenant_scope", scope)

    with pytest.raises(QueueError) as raised:
        await slow_lane.claim_one(cast(AsyncConnection, _IdleConnection()), "alice", role=ROLE)

    assert opened == [ROLE, ROLE]
    _assert_static_unchained(raised.value)


@pytest.mark.parametrize("conflict", CONFLICTS)
async def test_a_completion_that_keeps_conflicting_is_retried_once_then_raises_the_queue_contention_error(
    monkeypatch: pytest.MonkeyPatch, conflict: type[Exception]
) -> None:
    scope, opened = _conflicting_scope(conflict, failures=10)
    monkeypatch.setattr(slow_lane, "tenant_scope", scope)

    with pytest.raises(QueueError) as raised:
        await slow_lane.complete_one(
            cast(AsyncConnection, _IdleConnection()), "alice", _running_job(), _never_called, role=ROLE
        )

    assert opened == [ROLE, ROLE]
    _assert_static_unchained(raised.value)


@pytest.mark.parametrize("conflict", CONFLICTS)
async def test_control_a_claim_whose_first_commit_conflicts_succeeds_on_its_one_retry(
    monkeypatch: pytest.MonkeyPatch, conflict: type[Exception]
) -> None:
    scope, opened = _conflicting_scope(conflict, failures=1, at_commit=True)
    monkeypatch.setattr(slow_lane, "tenant_scope", scope)

    claimed = await slow_lane.claim_one(cast(AsyncConnection, _IdleConnection()), "alice", role=ROLE)

    assert claimed is None  # the retried transaction ran to completion and found the queue empty
    assert opened == [ROLE, ROLE]


async def test_control_a_non_retryable_error_in_the_claim_is_not_retried_or_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, opened = _conflicting_scope(RuntimeError, failures=10)
    monkeypatch.setattr(slow_lane, "tenant_scope", scope)

    with pytest.raises(RuntimeError):
        await slow_lane.claim_one(cast(AsyncConnection, _IdleConnection()), "alice", role=ROLE)

    assert opened == [ROLE]
