"""Durable per-user allocation using canonical clock and content-free audit rows."""

from datetime import UTC, datetime, timedelta
from re import fullmatch
from typing import Literal
from uuid import uuid4

from psycopg import AsyncConnection, pq, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator


class ClockError(InvalidParameterValue):
    """Static validation code and SQLSTATE 22023, without private input values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ClockError("clock_aware_instant_required")
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ClockError("clock_instant_out_of_range") from error


def advance_instant(raw_instant: datetime, previous: datetime | None = None) -> datetime:
    """Return max(raw, previous + 1 microsecond), comparing normalized instants."""
    raw = _instant(raw_instant)
    if previous is None:
        return raw
    last = _instant(previous)
    if raw > last:
        return raw
    try:
        return last + timedelta(microseconds=1)
    except OverflowError as error:
        raise ClockError("clock_instant_exhausted") from error


class ClockTick(BaseModel):
    """One allocation shared by every statement of its caller mutation transaction."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    instant: AwareDatetime
    raw_instant: AwareDatetime
    mode: Literal["live", "replay"]
    advanced: bool

    @field_validator("instant", "raw_instant")
    @classmethod
    def normalize_instants(cls, value: datetime) -> datetime:
        return _instant(value)

    @model_validator(mode="after")
    def consistent_allocation(self) -> "ClockTick":
        if self.instant < self.raw_instant or self.advanced != (self.instant > self.raw_instant):
            raise ValueError("clock_tick_inconsistent")
        return self


async def allocate_clock(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
    replay_time: datetime | None = None,
) -> ClockTick:
    """Allocate once in an existing READ COMMITTED, tenant-scoped transaction.

    The caller must allocate before reading/mutating memory, reuse this tick for
    all R1 statements and own commit/rollback. An explicit replay instant belongs
    only to the replay harness; live mode always reads the database transaction
    timestamp and never treats an inherited app.clock as a raw live clock.

    A canonical content-free audit event persists the watermark even when no
    memory row is inserted, or all memory rows are later deleted. Its mode is
    attributable by (user_id, recorded_from). No extra schema objects are added.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ClockError("clock_user_id_required")
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise ClockError("isolated_engine_schema_identifier_required")
    replay = None if replay_time is None else _instant(replay_time)
    if connection.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise ClockError("clock_active_transaction_required")

    # The savepoint makes a rejected allocation atomic while preserving caller
    # transaction ownership. Transaction-local GUC and audit row roll back with it.
    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                "SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), ''), "
                "pg_catalog.current_setting('transaction_isolation')"
            )
            context = await cursor.fetchone()
            if context is None or context[0] != user_id:
                raise InsufficientPrivilege("clock_user_context_mismatch")
            if context[1] != "read committed":
                raise ClockError("clock_read_committed_required")
            await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))
            await cursor.execute("SELECT pg_catalog.transaction_timestamp(), pg_catalog.pg_current_xact_id()::text")
            current = await cursor.fetchone()
            if current is None or not isinstance(current[0], datetime) or not isinstance(current[1], str):
                raise ClockError("clock_database_instant_required")
            raw = _instant(current[0]) if replay is None else replay
            transaction_id = current[1]

            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {audit} WHERE user_id = %s "
                    "AND event_type = 'clock_allocated' AND details->>'transaction_id' = %s)"
                ).format(audit=sql.Identifier(schema_name, "audit_event")),
                (user_id, transaction_id),
            )
            allocated = await cursor.fetchone()
            if allocated is None:
                raise ClockError("clock_watermark_query_failed")
            if allocated[0]:
                raise ClockError("clock_already_allocated_in_transaction")

            # Negative infinity is below every finite clock. A positive-infinite
            # lower/event bound cannot be advanced by a finite clock. Infinite
            # upper endpoints mean an open-ended belief, not a commit instant.
            await cursor.execute(
                sql.SQL(
                    "SELECT pg_catalog.max(instant) FILTER (WHERE pg_catalog.isfinite(instant)), "
                    "COALESCE(pg_catalog.bool_or(instant = 'infinity'::timestamptz), false) "
                    "FROM ("
                    "SELECT event_at AS instant FROM {audit} "
                    "WHERE user_id = %s AND event_type = 'clock_allocated' "
                    "UNION ALL SELECT pg_catalog.lower(recorded_range) FROM {memory} "
                    "WHERE user_id = %s AND recorded_range IS NOT NULL "
                    "UNION ALL SELECT pg_catalog.upper(recorded_range) FROM {memory} "
                    "WHERE user_id = %s AND recorded_range IS NOT NULL "
                    "AND pg_catalog.isfinite(pg_catalog.upper(recorded_range))"
                    ") AS prior_clocks"
                ).format(
                    audit=sql.Identifier(schema_name, "audit_event"),
                    memory=sql.Identifier(schema_name, "memory_version"),
                ),
                (user_id, user_id, user_id),
            )
            watermark = await cursor.fetchone()
            if watermark is None:
                raise ClockError("clock_watermark_query_failed")
            if watermark[1]:
                raise ClockError("clock_nonfinite_watermark")
            previous = watermark[0]
            if previous is not None and not isinstance(previous, datetime):
                raise ClockError("clock_database_instant_required")
            instant = advance_instant(raw, previous)
            tick = ClockTick(
                instant=instant, raw_instant=raw, mode="live" if replay is None else "replay", advanced=instant > raw
            )
            await cursor.execute("SELECT pg_catalog.set_config('app.clock', %s, true)", (instant.isoformat(),))
            await cursor.execute(sql.SQL("SELECT {}.clock_now()").format(sql.Identifier(schema_name)))
            synchronized = await cursor.fetchone()
            if (
                synchronized is None
                or not isinstance(synchronized[0], datetime)
                or _instant(synchronized[0]) != instant
            ):
                raise ClockError("clock_database_synchronization_failed")
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {audit} (id,user_id,memory_ref,event_type,event_at,details) "
                    "VALUES (%s,%s,%s,'clock_allocated',%s,%s)"
                ).format(audit=sql.Identifier(schema_name, "audit_event")),
                (
                    uuid4().hex,
                    user_id,
                    uuid4().hex,
                    instant,
                    Jsonb({"clock_mode": tick.mode, "transaction_id": transaction_id}),
                ),
            )
    return tick
