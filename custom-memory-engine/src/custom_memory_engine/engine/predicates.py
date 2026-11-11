"""Canonical temporal reads from Proposal §7.4 and Technical Specification §3.5."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from re import fullmatch
from types import MappingProxyType
from typing import Literal

from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import dict_row, tuple_row
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

type QueryMode = Literal["current_state", "historical_as_of", "historical_window", "audit_as_of"]
type RangeBound = AwareDatetime | Literal["-infinity", "infinity"]


class TemporalQueryError(InvalidParameterValue):
    """Static validation code with SQLSTATE 22023, without private input values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TemporalQueryError("timezone_aware_instant_required")
    return value.astimezone(UTC)


class TemporalQuery(Record):
    """One user and one mode; audit clock authorization belongs to guarded execution."""

    mode: QueryMode
    user_id: str = Field(min_length=1)
    reference_time: AwareDatetime | None = None
    target_time: AwareDatetime | None = None
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    belief_time: AwareDatetime | None = None

    @field_validator("user_id")
    @classmethod
    def nonblank_user(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nonblank_user_id_required")
        return value

    @field_validator("reference_time", "target_time", "window_start", "window_end", "belief_time")
    @classmethod
    def normalize_instants(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def mode_and_guards(self) -> "TemporalQuery":
        expected = {
            "current_state": {"reference_time"},
            "historical_as_of": {"target_time", "reference_time"},
            "historical_window": {"window_start", "window_end", "reference_time"},
            "audit_as_of": {"target_time", "belief_time"},
        }[self.mode]
        present = {
            name
            for name in ("reference_time", "target_time", "window_start", "window_end", "belief_time")
            if getattr(self, name) is not None
        }
        if present != expected:
            raise ValueError("temporal_mode_parameter_mismatch")
        if self.mode == "historical_as_of":
            assert self.target_time is not None and self.reference_time is not None
            if self.target_time > self.reference_time:
                raise TemporalQueryError("historical_target_after_reference")
        if self.mode == "historical_window":
            assert self.window_start is not None and self.window_end is not None and self.reference_time is not None
            if self.window_start >= self.window_end:
                raise TemporalQueryError("historical_window_must_be_nonempty")
            if self.window_end > self.reference_time:
                raise TemporalQueryError("historical_window_after_reference")
        return self


def current_state(user_id: str, reference_time: datetime) -> TemporalQuery:
    return TemporalQuery(mode="current_state", user_id=user_id, reference_time=reference_time)


def historical_as_of(user_id: str, target_time: datetime, reference_time: datetime) -> TemporalQuery:
    return TemporalQuery(
        mode="historical_as_of", user_id=user_id, target_time=target_time, reference_time=reference_time
    )


def historical_window(user_id: str, start: datetime, end: datetime, reference_time: datetime) -> TemporalQuery:
    return TemporalQuery(
        mode="historical_window", user_id=user_id, window_start=start, window_end=end, reference_time=reference_time
    )


def audit_as_of(user_id: str, target_time: datetime, belief_time: datetime) -> TemporalQuery:
    """Build an inspector query; compilation/execution requires the database clock."""
    return TemporalQuery(mode="audit_as_of", user_id=user_id, target_time=target_time, belief_time=belief_time)


@dataclass(frozen=True)
class CompiledPredicate:
    """SQL syntax and immutable values stay separate for Psycopg server-side binding."""

    statement: sql.Composable
    parameters: Mapping[str, str | datetime]


def compile_predicate(
    query: TemporalQuery, *, alias: str | None = None, database_clock: datetime | None = None
) -> CompiledPredicate:
    """Compile a reusable WHERE clause; audit cannot compile without a clock check.

    The caller of this pure builder must supply the database's clock for audit.
    fetch_versions always obtains it itself in the read transaction.
    """
    query = TemporalQuery.model_validate(query.model_dump())
    if alias is not None and (not isinstance(alias, str) or not alias.strip()):
        raise TemporalQueryError("nonblank_sql_alias_required")

    def column(name: str) -> sql.Identifier:
        return sql.Identifier(name) if alias is None else sql.Identifier(alias, name)

    values: dict[str, str | datetime] = {"user_id": query.user_id}
    clauses = [sql.SQL("{} = %(user_id)s").format(column("user_id"))]
    if query.mode == "audit_as_of":
        if database_clock is None:
            raise TemporalQueryError("audit_database_clock_required")
        assert query.belief_time is not None
        if query.belief_time > _utc(database_clock):
            raise TemporalQueryError("audit_belief_after_database_clock")
        clauses.append(sql.SQL("{} IN ('active', 'archived')").format(column("status")))
        values["belief_time"] = query.belief_time
        clauses.append(sql.SQL("{} @> %(belief_time)s::timestamptz").format(column("recorded_range")))
    else:
        assert query.reference_time is not None
        clauses.append(sql.SQL("{} = 'active'").format(column("status")))
        values["reference_time"] = query.reference_time
        clauses.append(sql.SQL("{} @> %(reference_time)s::timestamptz").format(column("recorded_range")))
    if query.mode == "historical_window":
        assert query.window_start is not None and query.window_end is not None
        values.update(window_start=query.window_start, window_end=query.window_end)
        clauses.append(
            sql.SQL("{} && tstzrange(%(window_start)s::timestamptz, %(window_end)s::timestamptz, '[)')").format(
                column("event_range")
            )
        )
    else:
        target = query.reference_time if query.mode == "current_state" else query.target_time
        assert target is not None
        values["target_time"] = target
        clauses.append(sql.SQL("{} @> %(target_time)s::timestamptz").format(column("event_range")))
    return CompiledPredicate(sql.SQL(" AND ").join(clauses), MappingProxyType(values))


class TimeRange(Record):
    """Unbounded endpoints remain distinct from PostgreSQL's literal infinities."""

    lower: RangeBound
    upper: RangeBound | None
    lower_inclusive: bool
    upper_inclusive: bool

    @field_validator("lower", "upper", mode="before")
    @classmethod
    def parse_database_bound(cls, value: object) -> object:
        if isinstance(value, str) and value not in {"-infinity", "infinity"}:
            return _utc(datetime.fromisoformat(value))
        return value


class TemporalVersion(Record):
    """Canonical memory-version fields, preserving native range boundary semantics."""

    id: str
    memory_key: str
    user_id: str
    status: Literal["active", "archived"]
    subject_key: str
    predicate_key: str
    single_valued: bool
    content: str
    event_range: TimeRange
    recorded_range: TimeRange
    layer: Literal["episodic", "semantic", "opinion", "persona"]
    valid_from_basis: Literal["explicit", "inferred"]
    assertion_mode: Literal["asserted", "hedged", "hypothetical", "planned", "quoted", "third_party", "joking"]
    confidence: float | None
    importance: float | None
    reinforcement_count: int
    extractor_version: str
    archival_branch: Literal["S", "C"] | None


class TemporalResult(Record):
    mode: QueryMode
    user_id: str
    versions: tuple[TemporalVersion, ...]

    @model_validator(mode="after")
    def correct_visibility(self) -> "TemporalResult":
        for version in self.versions:
            if version.user_id != self.user_id:
                raise ValueError("cross_user_temporal_result")
            if self.mode != "audit_as_of" and version.status != "active":
                raise ValueError("archived_version_in_ordinary_result")
        return self

    def for_answer_context(self) -> tuple[TemporalVersion, ...]:
        if self.mode == "audit_as_of":
            raise TemporalQueryError("audit_not_for_answer_context")
        return self.versions


def _range_json(column: str, alias: str = "m") -> sql.Composed:
    name = sql.Identifier(alias, column)
    return sql.SQL(
        "pg_catalog.jsonb_build_object('lower', pg_catalog.lower({r}), "
        "'upper', pg_catalog.upper({r}), 'lower_inclusive', pg_catalog.lower_inc({r}), "
        "'upper_inclusive', pg_catalog.upper_inc({r}))"
    ).format(r=name)


# Explicit projection with a castless jsonb object: `to_jsonb(m)` costs ~4-5x more per
# row (measured in scripts/bench_retrieval.py), so retrieval prunes narrow candidates.
def version_projection(alias: str = "m") -> sql.Composed:
    """Column list producing one dict per memory_version row for TemporalVersion."""
    columns = (
        "id",
        "memory_key",
        "user_id",
        "status",
        "subject_key",
        "predicate_key",
        "single_valued",
        "content",
        "layer",
        "valid_from_basis",
        "assertion_mode",
        "confidence",
        "importance",
        "reinforcement_count",
        "extractor_version",
        "archival_branch",
    )
    pairs = [
        sql.SQL("{name}, {column}").format(name=sql.Literal(column), column=sql.Identifier(alias, column))
        for column in columns
    ]
    pairs.append(
        sql.SQL("{name}, {range}").format(name=sql.Literal("event_range"), range=_range_json("event_range", alias))
    )
    pairs.append(
        sql.SQL("{name}, {range}").format(
            name=sql.Literal("recorded_range"),
            range=sql.SQL("CASE WHEN {col} IS NULL THEN NULL ELSE {range} END").format(
                col=sql.Identifier(alias, "recorded_range"), range=_range_json("recorded_range", alias)
            ),
        )
    )
    return sql.SQL("pg_catalog.jsonb_build_object({pairs})").format(pairs=sql.SQL(", ").join(pairs))


async def fetch_versions(
    connection: AsyncConnection, query: TemporalQuery, *, schema_name: str = "cme_engine"
) -> TemporalResult:
    """Read with caller-owned transaction/tenant context and the schema's DB clock.

    This does not change app.user_id/app.clock, bypass RLS, close the connection,
    or commit an existing caller transaction. A nested transaction is a savepoint.
    """
    query = TemporalQuery.model_validate(query.model_dump())
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise TemporalQueryError("isolated_engine_schema_identifier_required")
    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
            context = await cursor.fetchone()
            if context is None or context[0] != query.user_id:
                raise InsufficientPrivilege("temporal_user_context_mismatch")
            database_clock = None
            if query.mode == "audit_as_of":
                await cursor.execute(sql.SQL("SELECT {}.clock_now()").format(sql.Identifier(schema_name)))
                row = await cursor.fetchone()
                if row is None or not isinstance(row[0], datetime):
                    raise TemporalQueryError("database_clock_instant_required")
                database_clock = row[0]
        compiled = compile_predicate(query, alias="m", database_clock=database_clock)
        statement = sql.SQL("SELECT {projection} AS version FROM {table} AS m WHERE {where} ORDER BY m.id").format(
            projection=version_projection("m"),
            table=sql.Identifier(schema_name, "memory_version"),
            where=compiled.statement,
        )
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(statement, compiled.parameters)
            versions = tuple(TemporalVersion.model_validate(row["version"]) for row in await cursor.fetchall())
    return TemporalResult(mode=query.mode, user_id=query.user_id, versions=versions)


@dataclass(frozen=True)
class ScopedRow:
    """One ``memory_version`` row as a scoped temporal read returns it (the columns channel routing needs)."""

    id: str
    kind: str
    lifecycle: str
    content: str
    scope_path: str
    visibility: str


async def reference_instant(connection: AsyncConnection) -> datetime:
    """The engine's present as **writes** record it: ``transaction_timestamp()``.

    ``recorded_range`` opens at the instant ``clock.allocate_clock`` allocates, and that function reads
    ``transaction_timestamp()`` on purpose — it "never treats an inherited ``app.clock`` as a raw live clock"
    (a replay harness pins ``app.clock``, and the pin must not leak into a live belief). A read whose reference
    instant came from ``clock_now()`` would therefore disagree with every row it is filtering over: pinned low,
    nothing is current; pinned high, a caller reads a belief the engine never held. Using the writer's own clock is
    the only way "the view now" means the same thing on both sides (``I4``).
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT pg_catalog.transaction_timestamp()")
        row = await cursor.fetchone()
    if row is None or not isinstance(row[0], datetime):
        raise TemporalQueryError("database_clock_instant_required")
    return row[0]


async def fetch_scoped_rows(
    connection: AsyncConnection,
    query: TemporalQuery,
    read_filter: sql.Composable,
    read_parameters: Mapping[str, object],
    *,
    limit: int,
    schema_name: str = "cme_engine",
) -> tuple[ScopedRow, ...]:
    """The temporal predicate and a caller's read predicate in **one** statement, newest belief first.

    Both filters run in SQL before ``LIMIT``, so the limit counts only rows the caller may read: filtering after the
    limit would silently return fewer rows than exist. ``read_filter`` must bind by name (it is composed with the
    temporal predicate, which does); ``scope.access.read_predicate_named`` produces it.

    Like :func:`fetch_versions` this runs in the caller's transaction and refuses a context whose ``app.user_id`` is
    not the query's user. ``audit_as_of`` is checked against the database clock here, never the host's.
    """
    query = TemporalQuery.model_validate(query.model_dump())
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise TemporalQueryError("positive_row_limit_required")
    if not isinstance(schema_name, str) or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None:
        raise TemporalQueryError("isolated_engine_schema_identifier_required")
    if read_parameters.keys() & {"user_id", "reference_time", "target_time", "window_start", "window_end"}:
        raise TemporalQueryError("read_parameters_collide_with_temporal_parameters")
    clock_instant = await reference_instant(connection)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        context = await cursor.fetchone()
        if context is None or context[0] != query.user_id:
            raise InsufficientPrivilege("temporal_user_context_mismatch")
        compiled = compile_predicate(query, alias="m", database_clock=clock_instant)
        await cursor.execute(
            sql.SQL(
                "SELECT m.id, m.kind, m.lifecycle, m.content, m.scope_path, m.visibility FROM {table} AS m "
                "WHERE {temporal} AND m.kind IS NOT NULL AND m.lifecycle IS NOT NULL AND {readable} "
                "ORDER BY lower(m.recorded_range) DESC NULLS LAST, m.id LIMIT %(row_limit)s"
            ).format(
                table=sql.Identifier(schema_name, "memory_version"),
                temporal=compiled.statement,
                readable=read_filter,
            ),
            {**compiled.parameters, **read_parameters, "row_limit": limit},
        )
        return tuple(ScopedRow(*(str(value) for value in row)) for row in await cursor.fetchall())
