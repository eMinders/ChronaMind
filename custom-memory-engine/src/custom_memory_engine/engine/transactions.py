"""Transactional supersession (R1–R4, R7), boundary revision (E3), and archival (R5, Invariant 10) (RL-042)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from re import fullmatch
from typing import Any, Literal, LiteralString, cast
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, pq, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from psycopg.types.range import TimestamptzRange
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from custom_memory_engine.clock.allocation import ClockError, ClockTick, allocate_clock
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from custom_memory_engine.security.audit_guard import content_free_details

logger = structlog.get_logger(__name__)

type AblationMode = Literal["P", "A1", "A2", "A2b", "A8"]
type VersionStatus = Literal["candidate", "conflict_pending", "rejected", "active", "archived"]
type MemoryLayer = Literal["episodic", "semantic", "opinion", "persona"]
type ValidFromBasis = Literal["explicit", "inferred"]
type AssertionMode = Literal[
    "asserted",
    "hedged",
    "hypothetical",
    "planned",
    "quoted",
    "third_party",
    "joking",
]
type DerivationType = Literal["consolidation", "summary", "corroboration", "re_derivation"]
type ArchivalBranch = Literal["S", "C"]


class TransactionError(InvalidParameterValue):
    """Static validation or precondition error (SQLSTATE 22023) without private content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArchivalPreconditionError(TransactionError):
    """Raised when R5 / Invariant 10 / D-27 archival preconditions (S) or (C) are unmet."""


class AblationDisabledError(TransactionError):
    """Raised when an ablation disabled by governance (such as A2b under D-15) is invoked without override."""


class _TransactionModel(BaseModel):
    """Strict, immutable Pydantic base model for transactional inputs and outcomes."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


def _utc_instant(value: datetime, *, code: str = "transaction_aware_instant_required") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TransactionError(code)
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise TransactionError("transaction_instant_out_of_range") from exc


def _validate_nonblank(value: str, *, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransactionError(code)
    return value


def _validate_schema_name(schema_name: str) -> str:
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise TransactionError("isolated_engine_schema_identifier_required")
    return schema_name


class EvidenceInput(_TransactionModel):
    """Evidence link for a memory version (Proposal §7.2, Invariant 8; generalised by migration 7 / E-013).

    A dialogue turn (the default, and byte-identical to v1) is named by ``turn_id``. An ``external`` source - tool
    output, a file span, a paper, anything outside the engine - is named by ``source_ref`` with the SHA-256
    ``content_hash`` of the captured text (``grounding.sources.canonical_hash``) and ``evidence_span`` as its locator.
    An external source needs no engine row and lies outside the deletion boundary (D-ENG-04).
    """

    turn_id: str | None = None
    evidence_span: str
    evidence_role: str = "primary"
    source_type: Literal["turn", "external", "tool", "file", "event"] = "turn"
    source_ref: str | None = None
    content_hash: str | None = None

    @field_validator("evidence_span", "evidence_role")
    @classmethod
    def check_nonblank(cls, value: str) -> str:
        return _validate_nonblank(value, code="evidence_nonblank_field_required")

    @model_validator(mode="after")
    def check_source_shape(self) -> "EvidenceInput":
        if self.source_type == "turn":
            if self.turn_id is None or self.source_ref is not None or self.content_hash is not None:
                raise ValueError("turn_evidence_requires_only_a_turn_id")
            _validate_nonblank(self.turn_id, code="evidence_nonblank_field_required")
            return self
        # external / tool / file / event: all stored as external-reference rows.
        # They carry a content_hash and source_ref but no turn_id.
        if self.turn_id is not None or self.source_ref is None or self.content_hash is None:
            raise ValueError("external_evidence_requires_source_ref_and_content_hash")
        _validate_nonblank(self.source_ref, code="evidence_nonblank_field_required")
        if fullmatch(r"[a-f0-9]{64}", self.content_hash) is None:
            raise ValueError("external_evidence_content_hash_must_be_sha256_hex")
        return self


class DerivationInput(_TransactionModel):
    """Source-version derivation link for a consolidated/summarized/re-derived version (Invariant 8)."""

    source_version_id: str
    derivation_type: DerivationType = "consolidation"

    @field_validator("source_version_id")
    @classmethod
    def check_source_id(cls, value: str) -> str:
        return _validate_nonblank(value, code="derivation_source_version_id_required")


class CandidateVersionInput(_TransactionModel):
    """Input specification for staging or committing a memory_version row."""

    version_id: str
    memory_key: str
    user_id: str
    scope_path: str | None = None
    kind: str | None = None  # set explicitly for kinds the layer cannot imply (e.g. 'procedure')
    method_id: str | None = None  # the registered METHOD of an inferred artifact (I3); fixed once set
    subject_key: str
    predicate_key: str
    single_valued: bool = True
    content: str
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    layer: MemoryLayer = "semantic"
    valid_from_basis: ValidFromBasis = "explicit"
    assertion_mode: AssertionMode = "asserted"
    confidence: float | None = Field(default=0.9, ge=0.0, le=1.0)
    importance: float | None = Field(default=0.7, ge=0.0, le=1.0)
    reinforcement_count: int = Field(default=0, ge=0)
    extractor_version: str = "engine-v1"
    evidence: tuple[EvidenceInput, ...] = ()
    derivations: tuple[DerivationInput, ...] = ()

    @field_validator(
        "version_id",
        "memory_key",
        "user_id",
        "subject_key",
        "predicate_key",
        "content",
        "extractor_version",
    )
    @classmethod
    def check_nonblank_fields(cls, value: str) -> str:
        return _validate_nonblank(value, code="version_nonblank_field_required")

    @field_validator("scope_path")
    @classmethod
    def check_scope_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return parse_scope(value).text
        except ScopeError:
            raise TransactionError("scope_path_invalid") from None

    @field_validator("valid_from", mode="before")
    @classmethod
    def normalize_valid_from(cls, value: datetime) -> datetime:
        return _utc_instant(value)

    @field_validator("valid_until", mode="before")
    @classmethod
    def normalize_valid_until(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc_instant(value)

    @model_validator(mode="after")
    def check_bounds_and_self_derivation(self) -> "CandidateVersionInput":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise TransactionError("event_range_must_be_nonempty")
        for deriv in self.derivations:
            if deriv.source_version_id == self.version_id:
                raise TransactionError("self_derivation_forbidden")
        if self.scope_path is not None and not self.scope_path.startswith(f"/u/{self.user_id}/"):
            raise TransactionError("scope_path_outside_user_namespace")
        return self

    def require_provenance(self) -> None:
        """Enforce Invariant 8: active/archived versions require >= 1 evidence or derivation link."""
        if not self.evidence and not self.derivations:
            raise TransactionError("active_or_archived_version_requires_provenance")


class CommittedVersion(_TransactionModel):
    """Snapshot of a persisted memory_version row after a transactional operation."""

    version_id: str
    memory_key: str
    user_id: str
    status: VersionStatus
    subject_key: str
    predicate_key: str
    single_valued: bool
    content: str
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    recorded_from: AwareDatetime | None = None
    recorded_until: AwareDatetime | None = None
    archival_branch: ArchivalBranch | None = None


class SupersessionOutcome(_TransactionModel):
    """Result of single-valued supersession (R1–R3, E1–E2) or ablation write (A1, A2, A2b)."""

    ablation: AblationMode
    clock_tick: ClockTick
    closed_version_ids: tuple[str, ...] = ()
    deleted_version_ids: tuple[str, ...] = ()
    reissued_version_id: str | None = None
    inserted_version_id: str
    conflict_id: str | None = None
    held_in_conflict_pending: bool = False


class BoundaryRevisionOutcome(_TransactionModel):
    """Result of E3 transition-boundary revision across two adjacent active versions."""

    clock_tick: ClockTick
    closed_version_ids: tuple[str, str]
    reissued_prior_version_id: str
    reissued_later_version_id: str
    revised_boundary: AwareDatetime


class ResolutionOutcome(_TransactionModel):
    """Result of R4 conflict_pending or candidate resolution (accept or reject)."""

    clock_tick: ClockTick | None = None
    candidate_version_id: str
    resolution_status: Literal["active", "rejected"]
    closed_version_ids: tuple[str, ...] = ()
    reissued_version_id: str | None = None
    conflict_id: str


class MultivaluedEndOutcome(_TransactionModel):
    """Result of R7 multi-valued fact termination."""

    ablation: AblationMode
    clock_tick: ClockTick
    closed_version_id: str | None = None
    deleted_version_id: str | None = None
    truncated_version_id: str
    valid_end: AwareDatetime


class ArchivalOutcome(_TransactionModel):
    """Result of R5 / Invariant 10 / D-27 archival transition."""

    clock_tick: ClockTick | None = None
    archived_version_id: str
    archival_branch: ArchivalBranch


def _tstzrange(lower: datetime, upper: datetime | None) -> TimestamptzRange:
    return TimestamptzRange(_utc_instant(lower), None if upper is None else _utc_instant(upper), "[)")


async def _verify_tenant_and_lock_user(
    connection: AsyncConnection,
    user_id: str,
    schema_name: str,
) -> None:
    """Verify INTRANS, READ COMMITTED, app.user_id match, and acquire per-user xact advisory lock."""
    _validate_nonblank(user_id, code="transaction_user_id_required")
    _validate_schema_name(schema_name)
    if connection.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise TransactionError("transaction_active_transaction_required")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), ''), "
            "pg_catalog.current_setting('transaction_isolation')"
        )
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("transaction_user_context_mismatch")
        if row[1] != "read committed":
            raise TransactionError("transaction_read_committed_required")
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))


async def _resolve_clock(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str,
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> ClockTick:
    """Reuse an existing transaction ClockTick or allocate a new monotonic tick via allocate_clock (R3).

    A supplied tick is re-validated against this transaction: the same tenant must be
    set, and the tick must not regress behind the user's recorded watermark, which
    would produce an empty or inverted `recorded_range`.
    """
    if clock_tick is not None:
        if replay_time is not None:
            raise TransactionError("cannot_pass_both_clock_tick_and_replay_time")
        # A supplied tick must be the one this transaction already allocated: app.clock is
        # transaction-local and is never set before an allocation. This rejects a tick
        # borrowed from another transaction without re-deriving a watermark.
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                "SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), ''), "
                "NULLIF(pg_catalog.current_setting('app.clock', true), '')"
            )
            row = await cursor.fetchone()
            if row is None or row[0] != user_id:
                raise InsufficientPrivilege("transaction_user_context_mismatch")
            if row[1] is None:
                raise TransactionError("clock_tick_requires_transaction_allocation")
            if _utc_instant(datetime.fromisoformat(row[1])) != clock_tick.instant:
                raise TransactionError("clock_tick_differs_from_transaction_allocation")
        return clock_tick
    try:
        return await allocate_clock(
            connection,
            user_id,
            schema_name=schema_name,
            replay_time=replay_time,
        )
    except ClockError as exc:
        raise TransactionError(exc.code) from exc


async def ensure_archival_trigger(
    connection: AsyncConnection,
    *,
    schema_name: str = "cme_engine",
) -> None:
    """Verify the archival guards from ``001_full_schema.sql`` are installed.

    The triggers used to be created here, which left a freshly migrated database
    unprotected until an application call. They now ship with migration 1; this
    helper only asserts presence so callers that relied on it fail loudly instead
    of silently running unguarded.

    Raises:
        TransactionError: when either constraint trigger or its function is absent.
    """
    _validate_schema_name(schema_name)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            """
            SELECT t.tgname
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND NOT t.tgisinternal
              AND t.tgname IN (
                'trg_memory_version_archival_invariant',
                'trg_memory_derivation_archival_coverage'
              )
            """,
            (schema_name,),
        )
        installed = {row[0] for row in await cursor.fetchall()}
        await cursor.execute(
            """
            SELECT p.proname
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s
              AND p.proname IN ('enforce_archival_invariant', 'guard_derivation_coverage')
            """,
            (schema_name,),
        )
        functions = {row[0] for row in await cursor.fetchall()}

    missing = ({"trg_memory_version_archival_invariant", "trg_memory_derivation_archival_coverage"} - installed) | (
        {"enforce_archival_invariant", "guard_derivation_coverage"} - functions
    )
    if missing:
        raise TransactionError("archival_guards_not_installed")


_INSERT_COLUMNS = (
    "id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content, "
    "event_range, recorded_range, layer, valid_from_basis, assertion_mode, confidence, "
    "importance, reinforcement_count, extractor_version, archival_branch"
)
_INSERT_BASE_ARITY = 18
#: Columns written only when the caller supplies them; absent, the axes/method triggers derive them.
_OPTIONAL_COLUMNS = ("scope_path", "kind", "method_id")


async def _insert_version(
    cursor: Any,
    schema_name: str,
    *,
    version_id: str,
    memory_key: str,
    user_id: str,
    status: VersionStatus,
    subject_key: str,
    predicate_key: str,
    single_valued: bool,
    content: str,
    event_range: TimestamptzRange,
    recorded_range: TimestamptzRange | None,
    layer: str,
    valid_from_basis: str,
    assertion_mode: str,
    confidence: float | None,
    importance: float | None,
    reinforcement_count: int,
    extractor_version: str,
    scope_path: str | None = None,
    kind: str | None = None,
    method_id: str | None = None,
    archival_branch: str | None = None,
) -> None:
    """Single write path for memory_version rows; keeps the column list in one place."""
    base_values = (
        version_id,
        memory_key,
        user_id,
        status,
        subject_key,
        predicate_key,
        single_valued,
        content,
        event_range,
        recorded_range,
        layer,
        valid_from_basis,
        assertion_mode,
        confidence,
        importance,
        reinforcement_count,
        extractor_version,
        archival_branch,
    )
    optional = {"scope_path": scope_path, "kind": kind, "method_id": method_id}
    extra = [name for name in _OPTIONAL_COLUMNS if optional[name] is not None]
    columns = _INSERT_COLUMNS + "".join(f", {name}" for name in extra)
    placeholders = ", ".join(["%s"] * (_INSERT_BASE_ARITY + len(extra)))
    template = sql.SQL(cast(LiteralString, f"INSERT INTO {{mv}} ({columns}) VALUES ({placeholders})"))
    await cursor.execute(
        template.format(mv=sql.Identifier(schema_name, "memory_version")),
        base_values + tuple(optional[name] for name in extra),
    )


async def _insert_provenance(
    cursor: Any,
    schema_name: str,
    *,
    version_id: str,
    user_id: str,
    evidence: Sequence[EvidenceInput],
    derivations: Sequence[DerivationInput],
) -> None:
    ident_ev = sql.Identifier(schema_name, "memory_evidence")
    ident_md = sql.Identifier(schema_name, "memory_derivation")
    for ev in evidence:
        if ev.source_type != "turn":
            # external / tool / file / event: a reference row under its declared type (migrations 8, 17)
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {ev} (version_id, user_id, evidence_span, evidence_role, "
                    "source_type, source_ref, span, content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(ev=ident_ev),
                (
                    version_id,
                    user_id,
                    ev.evidence_span,
                    ev.evidence_role,
                    ev.source_type,
                    ev.source_ref,
                    ev.evidence_span,
                    ev.content_hash,
                ),
            )
            continue
        # A turn: the dual-write trigger derives the source columns, so this statement is exactly v1's.
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {ev} (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES (%s, %s, %s, %s, %s)"
            ).format(ev=ident_ev),
            (version_id, ev.turn_id, user_id, ev.evidence_span, ev.evidence_role),
        )
    for deriv in derivations:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {md} (derived_version_id, source_version_id, user_id, derivation_type) "
                "VALUES (%s, %s, %s, %s)"
            ).format(md=ident_md),
            (version_id, deriv.source_version_id, user_id, deriv.derivation_type),
        )


async def _copy_provenance(
    cursor: Any,
    schema_name: str,
    *,
    from_version_id: str,
    to_version_id: str,
    user_id: str,
) -> None:
    ident_ev = sql.Identifier(schema_name, "memory_evidence")
    ident_md = sql.Identifier(schema_name, "memory_derivation")
    await cursor.execute(
        sql.SQL(
            "INSERT INTO {ev} (version_id, turn_id, user_id, evidence_span, evidence_role, "
            "source_type, source_ref, span, content_hash) "
            "SELECT %s, turn_id, user_id, evidence_span, evidence_role, source_type, source_ref, span, content_hash "
            "FROM {ev} WHERE version_id = %s AND user_id = %s"
        ).format(ev=ident_ev),
        (to_version_id, from_version_id, user_id),
    )
    await cursor.execute(
        sql.SQL(
            "INSERT INTO {md} (derived_version_id, source_version_id, user_id, derivation_type) "
            "SELECT %s, source_version_id, user_id, derivation_type "
            "FROM {md} WHERE derived_version_id = %s AND user_id = %s"
        ).format(md=ident_md),
        (to_version_id, from_version_id, user_id),
    )


async def _record_audit_event(
    cursor: Any,
    schema_name: str,
    *,
    user_id: str,
    memory_ref: str,
    event_type: str,
    event_at: datetime,
    details: dict[str, Any],
) -> None:
    ident_audit = sql.Identifier(schema_name, "audit_event")
    await cursor.execute(
        sql.SQL(
            "INSERT INTO {audit} (id, user_id, memory_ref, event_type, event_at, details) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        ).format(audit=ident_audit),
        (f"audit-{uuid4().hex}", user_id, memory_ref, event_type, event_at, Jsonb(content_free_details(details))),
    )


async def _ensure_no_uncovered_c_archived_sources(
    cursor: Any,
    schema_name: str,
    *,
    mutating_replacement_id: str,
    user_id: str,
) -> None:
    """Verify D-27: closing, archiving, or deleting `mutating_replacement_id` cannot uncover a (C)-archived source."""
    ident_mv = sql.Identifier(schema_name, "memory_version")
    ident_md = sql.Identifier(schema_name, "memory_derivation")
    await cursor.execute(
        sql.SQL(
            """
            SELECT src.id
            FROM {md} d
            JOIN {mv} src ON src.id = d.source_version_id
            WHERE d.derived_version_id = %s
              AND d.user_id = %s
              AND src.user_id = %s
              AND src.status = 'archived'
              AND src.archival_branch = 'C'
              AND src.recorded_range IS NOT NULL
              AND upper_inf(src.recorded_range)
              AND NOT EXISTS (
                SELECT 1
                FROM {md} d2
                JOIN {mv} r_other ON r_other.id = d2.derived_version_id
                WHERE d2.source_version_id = src.id
                  AND d2.user_id = src.user_id
                  AND r_other.id <> src.id
                  AND r_other.id <> %s
                  AND r_other.user_id = src.user_id
                  AND r_other.status = 'active'
                  AND r_other.recorded_range IS NOT NULL
                  AND upper_inf(r_other.recorded_range)
                  AND r_other.event_range @> src.event_range
              )
            LIMIT 1
            """
        ).format(mv=ident_mv, md=ident_md),
        (mutating_replacement_id, user_id, user_id, mutating_replacement_id),
    )
    row = await cursor.fetchone()
    if row is not None:
        raise ArchivalPreconditionError("archival_breaks_covered_source_invariant")


async def stage_candidate(
    connection: AsyncConnection,
    candidate: CandidateVersionInput,
    *,
    initial_status: Literal["candidate", "conflict_pending", "rejected"] = "candidate",
    schema_name: str = "cme_engine",
) -> CommittedVersion:
    """Insert a non-believed row (`candidate`, `conflict_pending`, or `rejected`) with `recorded_range = NULL` (R4)."""
    if initial_status not in ("candidate", "conflict_pending", "rejected"):
        raise TransactionError("stage_candidate_requires_non_believed_status")
    await _verify_tenant_and_lock_user(connection, candidate.user_id, schema_name)
    v_range = _tstzrange(candidate.valid_from, candidate.valid_until)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await _insert_version(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                memory_key=candidate.memory_key,
                user_id=candidate.user_id,
                scope_path=candidate.scope_path,
                status=initial_status,
                subject_key=candidate.subject_key,
                predicate_key=candidate.predicate_key,
                single_valued=candidate.single_valued,
                content=candidate.content,
                event_range=v_range,
                recorded_range=None,
                layer=candidate.layer,
                valid_from_basis=candidate.valid_from_basis,
                assertion_mode=candidate.assertion_mode,
                confidence=candidate.confidence,
                importance=candidate.importance,
                reinforcement_count=candidate.reinforcement_count,
                extractor_version=candidate.extractor_version,
                kind=candidate.kind,
                method_id=candidate.method_id,
            )
            await _insert_provenance(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                user_id=candidate.user_id,
                evidence=candidate.evidence,
                derivations=candidate.derivations,
            )

    return CommittedVersion(
        version_id=candidate.version_id,
        memory_key=candidate.memory_key,
        user_id=candidate.user_id,
        status=initial_status,
        subject_key=candidate.subject_key,
        predicate_key=candidate.predicate_key,
        single_valued=candidate.single_valued,
        content=candidate.content,
        valid_from=candidate.valid_from,
        valid_until=candidate.valid_until,
        recorded_from=None,
        recorded_until=None,
        archival_branch=None,
    )


async def commit_initial_version(
    connection: AsyncConnection,
    candidate: CandidateVersionInput,
    *,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
    ablation: AblationMode = "P",
    allow_a2b: bool = False,
) -> tuple[CommittedVersion, ClockTick]:
    """Commit an initial or additive `active` version at `recorded_range = [c, NULL)` with provenance (R1, R3, R4)."""
    if ablation == "A2b" and not allow_a2b:
        raise AblationDisabledError("a2b_disabled_by_d15")
    candidate.require_provenance()
    await _verify_tenant_and_lock_user(connection, candidate.user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        candidate.user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )
    effective_single_valued = False if ablation == "A1" else candidate.single_valued
    v_range = _tstzrange(candidate.valid_from, candidate.valid_until)
    r_range = _tstzrange(tick.instant, None)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await _insert_version(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                memory_key=candidate.memory_key,
                user_id=candidate.user_id,
                scope_path=candidate.scope_path,
                status="active",
                subject_key=candidate.subject_key,
                predicate_key=candidate.predicate_key,
                single_valued=effective_single_valued,
                content=candidate.content,
                event_range=v_range,
                recorded_range=r_range,
                layer=candidate.layer,
                valid_from_basis=candidate.valid_from_basis,
                assertion_mode=candidate.assertion_mode,
                confidence=candidate.confidence,
                importance=candidate.importance,
                reinforcement_count=candidate.reinforcement_count,
                extractor_version=candidate.extractor_version,
                kind=candidate.kind,
                method_id=candidate.method_id,
            )
            await _insert_provenance(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                user_id=candidate.user_id,
                evidence=candidate.evidence,
                derivations=candidate.derivations,
            )
            await _record_audit_event(
                cursor,
                schema_name,
                user_id=candidate.user_id,
                memory_ref=candidate.version_id,
                event_type="version_committed",
                event_at=tick.instant,
                details={"version_id": candidate.version_id, "ablation": ablation},
            )

    return (
        CommittedVersion(
            version_id=candidate.version_id,
            memory_key=candidate.memory_key,
            user_id=candidate.user_id,
            status="active",
            subject_key=candidate.subject_key,
            predicate_key=candidate.predicate_key,
            single_valued=effective_single_valued,
            content=candidate.content,
            valid_from=candidate.valid_from,
            valid_until=candidate.valid_until,
            recorded_from=tick.instant,
            recorded_until=None,
            archival_branch=None,
        ),
        tick,
    )


async def supersede_single(
    connection: AsyncConnection,
    candidate: CandidateVersionInput,
    *,
    schema_name: str = "cme_engine",
    reissued_version_id: str | None = None,
    conflict_id: str | None = None,
    decided_by: str = "governance-r2",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
    ablation: AblationMode = "P",
    allow_a2b: bool = False,
) -> SupersessionOutcome:
    """Execute single-valued close-then-insert supersession (R1–R3, E1–E2) or ablation write (A1, A2, A2b).

    Rules enforced (Technical Specification §3.4, Proposal §§7.2, 8.2, 10.2):
    - `R1`: Close-then-insert only; `event_range` is never mutated in place on `memory_version`.
    - `R2`:
      1. If `v_old < v_new`, close `r_old` at `c`, re-issue `r_old'` with `event_range = [v_old, v_new)`
         and `recorded_range = [c, NULL)` copying all provenance from `r_old`, and insert `r_new` with
         `event_range = [v_new, v_new_end)` and `recorded_range = [c, NULL)`.
      2. If `v_old == v_new`, close `r_old` at `c`, omit `r_old'` (which would have an empty valid range),
         and insert `r_new` at `[c, NULL)`.
      3. If `v_new < v_old`, hold `r_new` as `conflict_pending` (`recorded_range = NULL`), record a
         `memory_conflict` row (`decision = 'hold'`), and leave `r_old` active and untouched.
    - `A1` (`P-flat`): Disables versioning; inserts `candidate` as `active` with `single_valued = False`
      without closing prior versions.
    - `A2` (`P-nohistory`): Destructively deletes the superseded row `r_old` and inserts `r_new` without
      re-issuing `r_old'` or keeping recorded-time history.
    - `A2b` (`D-15`, disabled unless `allow_a2b=True`): Deletes `r_old` (no closed recorded-time row)
      while inserting truncated `r_old'` (`[v_old, v_new)`) and `r_new` (`[v_new, v_new_end)`) at `[c, NULL)`.
    """
    if ablation == "A2b" and not allow_a2b:
        raise AblationDisabledError("a2b_disabled_by_d15")
    if ablation != "A1" and not candidate.single_valued:
        raise TransactionError("supersede_single_requires_single_valued_candidate")
    candidate.require_provenance()

    await _verify_tenant_and_lock_user(connection, candidate.user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        candidate.user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )

    if ablation == "A1":
        await commit_initial_version(
            connection,
            candidate,
            schema_name=schema_name,
            clock_tick=tick,
            ablation="A1",
        )
        return SupersessionOutcome(
            ablation="A1",
            clock_tick=tick,
            inserted_version_id=candidate.version_id,
        )

    ident_mv = sql.Identifier(schema_name, "memory_version")
    ident_mc = sql.Identifier(schema_name, "memory_conflict")
    v_new_range = _tstzrange(candidate.valid_from, candidate.valid_until)
    r_open_range = _tstzrange(tick.instant, None)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, memory_key, content, lower(event_range), upper(event_range), "
                    "upper_inf(event_range), lower(recorded_range), layer, valid_from_basis, "
                    "assertion_mode, confidence, importance, reinforcement_count, extractor_version "
                    "FROM {mv} "
                    "WHERE user_id = %s AND subject_key = %s AND predicate_key = %s "
                    "AND single_valued = true AND status = 'active' "
                    "AND recorded_range IS NOT NULL AND upper_inf(recorded_range) "
                    "AND event_range && %s "
                    "ORDER BY lower(event_range) ASC, id ASC "
                    "FOR UPDATE"
                ).format(mv=ident_mv),
                (
                    candidate.user_id,
                    candidate.subject_key,
                    candidate.predicate_key,
                    v_new_range,
                ),
            )
            existing_rows = await cursor.fetchall()

            if not existing_rows:
                await _insert_version(
                    cursor,
                    schema_name,
                    version_id=candidate.version_id,
                    memory_key=candidate.memory_key,
                    user_id=candidate.user_id,
                    status="active",
                    subject_key=candidate.subject_key,
                    predicate_key=candidate.predicate_key,
                    single_valued=True,
                    content=candidate.content,
                    event_range=v_new_range,
                    recorded_range=r_open_range,
                    layer=candidate.layer,
                    valid_from_basis=candidate.valid_from_basis,
                    assertion_mode=candidate.assertion_mode,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                    reinforcement_count=candidate.reinforcement_count,
                    extractor_version=candidate.extractor_version,
                )
                await _insert_provenance(
                    cursor,
                    schema_name,
                    version_id=candidate.version_id,
                    user_id=candidate.user_id,
                    evidence=candidate.evidence,
                    derivations=candidate.derivations,
                )
                await _record_audit_event(
                    cursor,
                    schema_name,
                    user_id=candidate.user_id,
                    memory_ref=candidate.version_id,
                    event_type="version_committed",
                    event_at=tick.instant,
                    details={"version_id": candidate.version_id, "ablation": ablation},
                )
                return SupersessionOutcome(
                    ablation=ablation,
                    clock_tick=tick,
                    inserted_version_id=candidate.version_id,
                )

            (
                old_id,
                old_memory_key,
                old_content,
                v_old_raw,
                v_old_end_raw,
                v_old_upper_inf,
                r_old_from_raw,
                old_layer,
                old_basis,
                old_assertion_mode,
                old_confidence,
                old_importance,
                old_reinforcement,
                old_extractor,
            ) = existing_rows[-1]
            v_old = _utc_instant(v_old_raw)
            v_old_end = None if v_old_upper_inf or v_old_end_raw is None else _utc_instant(v_old_end_raw)
            r_old_from = _utc_instant(r_old_from_raw)
            v_new = candidate.valid_from

            # R2 Rule 3: v_new < v_old -> route to conflict_pending and leave r_old untouched
            if v_new < v_old:
                resolved_conflict_id = conflict_id or f"conflict-{uuid4().hex}"
                await _insert_version(
                    cursor,
                    schema_name,
                    version_id=candidate.version_id,
                    memory_key=candidate.memory_key,
                    user_id=candidate.user_id,
                    status="conflict_pending",
                    subject_key=candidate.subject_key,
                    predicate_key=candidate.predicate_key,
                    single_valued=True,
                    content=candidate.content,
                    event_range=v_new_range,
                    recorded_range=None,
                    layer=candidate.layer,
                    valid_from_basis=candidate.valid_from_basis,
                    assertion_mode=candidate.assertion_mode,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                    reinforcement_count=candidate.reinforcement_count,
                    extractor_version=candidate.extractor_version,
                )
                await _insert_provenance(
                    cursor,
                    schema_name,
                    version_id=candidate.version_id,
                    user_id=candidate.user_id,
                    evidence=candidate.evidence,
                    derivations=candidate.derivations,
                )
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {mc} ("
                        "id, candidate_version_id, existing_version_id, user_id, relation, "
                        "decision, reason_code, decided_by, decided_at"
                        ") VALUES (%s, %s, %s, %s, 'contradicts', 'hold', 'valid_from_precedes_active', %s, %s)"
                    ).format(mc=ident_mc),
                    (
                        resolved_conflict_id,
                        candidate.version_id,
                        old_id,
                        candidate.user_id,
                        decided_by,
                        tick.instant,
                    ),
                )
                await _record_audit_event(
                    cursor,
                    schema_name,
                    user_id=candidate.user_id,
                    memory_ref=candidate.version_id,
                    event_type="version_held_conflict_pending",
                    event_at=tick.instant,
                    details={
                        "candidate_version_id": candidate.version_id,
                        "existing_version_id": old_id,
                        "conflict_id": resolved_conflict_id,
                    },
                )
                return SupersessionOutcome(
                    ablation=ablation,
                    clock_tick=tick,
                    inserted_version_id=candidate.version_id,
                    conflict_id=resolved_conflict_id,
                    held_in_conflict_pending=True,
                )

            # Check D-27 before closing or deleting old_id
            await _ensure_no_uncovered_c_archived_sources(
                cursor,
                schema_name,
                mutating_replacement_id=old_id,
                user_id=candidate.user_id,
            )

            closed_ids: tuple[str, ...] = ()
            deleted_ids: tuple[str, ...] = ()
            reissued_id: str | None = None

            if ablation in ("A2", "A2b"):
                ident_ev = sql.Identifier(schema_name, "memory_evidence")
                ident_md = sql.Identifier(schema_name, "memory_derivation")
                await cursor.execute(
                    sql.SQL(
                        "SELECT turn_id, evidence_span, evidence_role, source_type, source_ref, content_hash "
                        "FROM {ev} WHERE version_id = %s AND user_id = %s"
                    ).format(ev=ident_ev),
                    (old_id, candidate.user_id),
                )
                saved_ev = [
                    EvidenceInput(turn_id=r[0], evidence_span=r[1], evidence_role=r[2])
                    if r[3] == "turn"
                    else EvidenceInput(
                        source_type=r[3],
                        source_ref=r[4],
                        content_hash=r[5],
                        evidence_span=r[1],
                        evidence_role=r[2],
                    )
                    for r in await cursor.fetchall()
                ]
                await cursor.execute(
                    sql.SQL(
                        "SELECT source_version_id, derivation_type "
                        "FROM {md} WHERE derived_version_id = %s AND user_id = %s"
                    ).format(md=ident_md),
                    (old_id, candidate.user_id),
                )
                saved_md = [
                    DerivationInput(source_version_id=r[0], derivation_type=r[1]) for r in await cursor.fetchall()
                ]
                await cursor.execute(
                    sql.SQL("DELETE FROM {mv} WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                    (old_id, candidate.user_id),
                )
                deleted_ids = (old_id,)
                if ablation == "A2b" and v_old < v_new:
                    reissued_id = reissued_version_id or f"{old_id}__reissued"
                    reissued_upper = v_new if v_old_end is None or v_new < v_old_end else v_old_end
                    await _insert_version(
                        cursor,
                        schema_name,
                        version_id=reissued_id,
                        memory_key=old_memory_key,
                        user_id=candidate.user_id,
                        status="active",
                        subject_key=candidate.subject_key,
                        predicate_key=candidate.predicate_key,
                        single_valued=True,
                        content=old_content,
                        event_range=_tstzrange(v_old, reissued_upper),
                        recorded_range=r_open_range,
                        layer=old_layer,
                        valid_from_basis=old_basis,
                        assertion_mode=old_assertion_mode,
                        confidence=old_confidence,
                        importance=old_importance,
                        reinforcement_count=old_reinforcement,
                        extractor_version=old_extractor,
                    )
                    await _insert_provenance(
                        cursor,
                        schema_name,
                        version_id=reissued_id,
                        user_id=candidate.user_id,
                        evidence=saved_ev,
                        derivations=saved_md,
                    )
            else:
                # Full bitemporal mode P: close old_id at tick.instant
                await cursor.execute(
                    sql.SQL("UPDATE {mv} SET recorded_range = %s WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                    (_tstzrange(r_old_from, tick.instant), old_id, candidate.user_id),
                )
                closed_ids = (old_id,)
                if v_old < v_new:
                    reissued_id = reissued_version_id or f"{old_id}__reissued"
                    reissued_upper = v_new if v_old_end is None or v_new < v_old_end else v_old_end
                    await _insert_version(
                        cursor,
                        schema_name,
                        version_id=reissued_id,
                        memory_key=old_memory_key,
                        user_id=candidate.user_id,
                        status="active",
                        subject_key=candidate.subject_key,
                        predicate_key=candidate.predicate_key,
                        single_valued=True,
                        content=old_content,
                        event_range=_tstzrange(v_old, reissued_upper),
                        recorded_range=r_open_range,
                        layer=old_layer,
                        valid_from_basis=old_basis,
                        assertion_mode=old_assertion_mode,
                        confidence=old_confidence,
                        importance=old_importance,
                        reinforcement_count=old_reinforcement,
                        extractor_version=old_extractor,
                    )
                    await _copy_provenance(
                        cursor,
                        schema_name,
                        from_version_id=old_id,
                        to_version_id=reissued_id,
                        user_id=candidate.user_id,
                    )

            # Insert the new active version r_new
            await _insert_version(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                memory_key=candidate.memory_key,
                user_id=candidate.user_id,
                status="active",
                subject_key=candidate.subject_key,
                predicate_key=candidate.predicate_key,
                single_valued=True,
                content=candidate.content,
                event_range=v_new_range,
                recorded_range=r_open_range,
                layer=candidate.layer,
                valid_from_basis=candidate.valid_from_basis,
                assertion_mode=candidate.assertion_mode,
                confidence=candidate.confidence,
                importance=candidate.importance,
                reinforcement_count=candidate.reinforcement_count,
                extractor_version=candidate.extractor_version,
            )
            await _insert_provenance(
                cursor,
                schema_name,
                version_id=candidate.version_id,
                user_id=candidate.user_id,
                evidence=candidate.evidence,
                derivations=candidate.derivations,
            )
            await _record_audit_event(
                cursor,
                schema_name,
                user_id=candidate.user_id,
                memory_ref=candidate.version_id,
                event_type="version_superseded",
                event_at=tick.instant,
                details={
                    "ablation": ablation,
                    "closed_version_ids": list(closed_ids),
                    "deleted_version_ids": list(deleted_ids),
                    "reissued_version_id": reissued_id,
                    "inserted_version_id": candidate.version_id,
                },
            )

    return SupersessionOutcome(
        ablation=ablation,
        clock_tick=tick,
        closed_version_ids=closed_ids,
        deleted_version_ids=deleted_ids,
        reissued_version_id=reissued_id,
        inserted_version_id=candidate.version_id,
        conflict_id=None,
        held_in_conflict_pending=False,
    )


async def revise_transition_boundary(
    connection: AsyncConnection,
    *,
    user_id: str,
    prior_version_id: str,
    later_version_id: str,
    revised_boundary: datetime,
    reissued_prior_version_id: str | None = None,
    reissued_later_version_id: str | None = None,
    later_evidence: Sequence[EvidenceInput] = (),
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> BoundaryRevisionOutcome:
    """Revise the shared valid-time boundary between two adjacent active versions (E3, R1–R3).

    Closes both `prior_version_id` (`[v1, v_old_boundary)`) and `later_version_id`
    (`[v_old_boundary, v_end)`) at `c`, and inserts re-issued `prior''` (`[v1, revised_boundary)`)
    and `later'` (`[revised_boundary, v_end)`) at `[c, NULL)` with preserved provenance.
    """
    _validate_nonblank(prior_version_id, code="prior_version_id_required")
    _validate_nonblank(later_version_id, code="later_version_id_required")
    if prior_version_id == later_version_id:
        raise TransactionError("distinct_boundary_versions_required")
    v_rev = _utc_instant(revised_boundary)

    await _verify_tenant_and_lock_user(connection, user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )

    ident_mv = sql.Identifier(schema_name, "memory_version")
    r_open_range = _tstzrange(tick.instant, None)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT id, memory_key, status, subject_key, predicate_key, single_valued, content, "
                    "lower(event_range), upper(event_range), upper_inf(event_range), "
                    "lower(recorded_range), upper_inf(recorded_range), "
                    "layer, valid_from_basis, assertion_mode, confidence, importance, "
                    "reinforcement_count, extractor_version "
                    "FROM {mv} WHERE user_id = %s AND id IN (%s, %s) ORDER BY id ASC FOR UPDATE"
                ).format(mv=ident_mv),
                (user_id, prior_version_id, later_version_id),
            )
            rows = {row[0]: row for row in await cursor.fetchall()}
            if prior_version_id not in rows or later_version_id not in rows:
                raise TransactionError("boundary_revision_versions_not_found")

            p_row = rows[prior_version_id]
            l_row = rows[later_version_id]
            if p_row[2] != "active" or not p_row[11] or l_row[2] != "active" or not l_row[11]:
                raise TransactionError("boundary_revision_requires_open_active_versions")
            if (p_row[3], p_row[4]) != (l_row[3], l_row[4]):
                raise TransactionError("boundary_revision_subject_predicate_mismatch")

            p_start = _utc_instant(p_row[7])
            p_end = None if p_row[9] or p_row[8] is None else _utc_instant(p_row[8])
            l_start = _utc_instant(l_row[7])
            l_end = None if l_row[9] or l_row[8] is None else _utc_instant(l_row[8])

            if p_end is None or p_end != l_start:
                raise TransactionError("boundary_revision_requires_adjacent_event_ranges")
            if v_rev <= p_start or (l_end is not None and v_rev >= l_end):
                raise TransactionError("revised_boundary_out_of_bounds")

            await _ensure_no_uncovered_c_archived_sources(
                cursor,
                schema_name,
                mutating_replacement_id=prior_version_id,
                user_id=user_id,
            )
            await _ensure_no_uncovered_c_archived_sources(
                cursor,
                schema_name,
                mutating_replacement_id=later_version_id,
                user_id=user_id,
            )

            # Close both prior and later versions at c
            await cursor.execute(
                sql.SQL("UPDATE {mv} SET recorded_range = %s WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                (_tstzrange(_utc_instant(p_row[10]), tick.instant), prior_version_id, user_id),
            )
            await cursor.execute(
                sql.SQL("UPDATE {mv} SET recorded_range = %s WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                (_tstzrange(_utc_instant(l_row[10]), tick.instant), later_version_id, user_id),
            )

            new_prior_id = reissued_prior_version_id or f"{prior_version_id}__rev"
            new_later_id = reissued_later_version_id or f"{later_version_id}__rev"

            # Insert re-issued prior version [p_start, v_rev)
            await _insert_version(
                cursor,
                schema_name,
                version_id=new_prior_id,
                memory_key=p_row[1],
                user_id=user_id,
                status="active",
                subject_key=p_row[3],
                predicate_key=p_row[4],
                single_valued=p_row[5],
                content=p_row[6],
                event_range=_tstzrange(p_start, v_rev),
                recorded_range=r_open_range,
                layer=p_row[12],
                valid_from_basis=p_row[13],
                assertion_mode=p_row[14],
                confidence=p_row[15],
                importance=p_row[16],
                reinforcement_count=p_row[17],
                extractor_version=p_row[18],
            )
            await _copy_provenance(
                cursor,
                schema_name,
                from_version_id=prior_version_id,
                to_version_id=new_prior_id,
                user_id=user_id,
            )

            # Insert re-issued later version [v_rev, l_end)
            await _insert_version(
                cursor,
                schema_name,
                version_id=new_later_id,
                memory_key=l_row[1],
                user_id=user_id,
                status="active",
                subject_key=l_row[3],
                predicate_key=l_row[4],
                single_valued=l_row[5],
                content=l_row[6],
                event_range=_tstzrange(v_rev, l_end),
                recorded_range=r_open_range,
                layer=l_row[12],
                valid_from_basis=l_row[13],
                assertion_mode=l_row[14],
                confidence=l_row[15],
                importance=l_row[16],
                reinforcement_count=l_row[17],
                extractor_version=l_row[18],
            )
            await _copy_provenance(
                cursor,
                schema_name,
                from_version_id=later_version_id,
                to_version_id=new_later_id,
                user_id=user_id,
            )
            if later_evidence:
                await _insert_provenance(
                    cursor,
                    schema_name,
                    version_id=new_later_id,
                    user_id=user_id,
                    evidence=later_evidence,
                    derivations=(),
                )
            await _record_audit_event(
                cursor,
                schema_name,
                user_id=user_id,
                memory_ref=new_later_id,
                event_type="transition_boundary_revised",
                event_at=tick.instant,
                details={
                    "closed_version_ids": [prior_version_id, later_version_id],
                    "reissued_prior_version_id": new_prior_id,
                    "reissued_later_version_id": new_later_id,
                },
            )

    return BoundaryRevisionOutcome(
        clock_tick=tick,
        closed_version_ids=(prior_version_id, later_version_id),
        reissued_prior_version_id=new_prior_id,
        reissued_later_version_id=new_later_id,
        revised_boundary=v_rev,
    )


async def resolve_conflict_pending(
    connection: AsyncConnection,
    *,
    user_id: str,
    candidate_version_id: str,
    decision: Literal["accept", "reject"],
    reason_code: str,
    decided_by: str = "governance-resolver",
    conflict_id: str | None = None,
    reissued_version_id: str | None = None,
    additional_evidence: Sequence[EvidenceInput] = (),
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> ResolutionOutcome:
    """Resolve a `conflict_pending` or `candidate` row to `active` or `rejected` (R4).

    When `decision == 'accept'`, allocates/reuses the resolution transaction clock `c`,
    closes (and re-issues if `[v_old, v_cand)` is non-empty) any overlapping active single-valued
    row in the same transaction at `c`, and transitions `candidate_version_id` in-place to
    `status = 'active', recorded_range = [c, NULL)` so that `recorded_range` starts at resolution
    time `c` (never backdated to when the candidate was staged).
    """
    _validate_nonblank(candidate_version_id, code="candidate_version_id_required")
    _validate_nonblank(reason_code, code="resolution_reason_code_required")
    _validate_nonblank(decided_by, code="resolution_decided_by_required")
    if decision not in ("accept", "reject"):
        raise TransactionError("invalid_conflict_resolution_decision")

    await _verify_tenant_and_lock_user(connection, user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )

    ident_mv = sql.Identifier(schema_name, "memory_version")
    ident_mc = sql.Identifier(schema_name, "memory_conflict")
    resolved_conflict_id = conflict_id or f"conflict-{uuid4().hex}"
    r_open_range = _tstzrange(tick.instant, None)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT status, subject_key, predicate_key, single_valued, event_range, "
                    "lower(event_range), upper(event_range), upper_inf(event_range), recorded_range "
                    "FROM {mv} WHERE id = %s AND user_id = %s FOR UPDATE"
                ).format(mv=ident_mv),
                (candidate_version_id, user_id),
            )
            cand_row = await cursor.fetchone()
            if cand_row is None:
                raise TransactionError("candidate_version_not_found")
            (
                cand_status,
                subject_key,
                predicate_key,
                single_valued,
                cand_event_range,
                v_cand_raw,
                _v_cand_end_raw,
                _v_cand_upper_inf,
                cand_recorded_range,
            ) = cand_row
            if cand_status not in ("candidate", "conflict_pending") or cand_recorded_range is not None:
                raise TransactionError("resolve_requires_unbelieved_candidate_or_conflict_pending")

            if additional_evidence:
                await _insert_provenance(
                    cursor,
                    schema_name,
                    version_id=candidate_version_id,
                    user_id=user_id,
                    evidence=additional_evidence,
                    derivations=(),
                )

            if decision == "reject":
                await cursor.execute(
                    sql.SQL("UPDATE {mv} SET status = 'rejected' WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                    (candidate_version_id, user_id),
                )
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {mc} ("
                        "id, candidate_version_id, existing_version_id, user_id, relation, "
                        "decision, reason_code, decided_by, decided_at"
                        ") VALUES (%s, %s, NULL, %s, 'contradicts', 'reject', %s, %s, %s)"
                    ).format(mc=ident_mc),
                    (resolved_conflict_id, candidate_version_id, user_id, reason_code, decided_by, tick.instant),
                )
                await _record_audit_event(
                    cursor,
                    schema_name,
                    user_id=user_id,
                    memory_ref=candidate_version_id,
                    event_type="conflict_resolved_rejected",
                    event_at=tick.instant,
                    details={"candidate_version_id": candidate_version_id, "conflict_id": resolved_conflict_id},
                )
                return ResolutionOutcome(
                    clock_tick=tick,
                    candidate_version_id=candidate_version_id,
                    resolution_status="rejected",
                    conflict_id=resolved_conflict_id,
                )

            # Ensure candidate has provenance before promoting to active (Invariant 8)
            ident_ev = sql.Identifier(schema_name, "memory_evidence")
            ident_md = sql.Identifier(schema_name, "memory_derivation")
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {ev} WHERE version_id = %s AND user_id = %s) "
                    "OR EXISTS (SELECT 1 FROM {md} WHERE derived_version_id = %s AND user_id = %s)"
                ).format(ev=ident_ev, md=ident_md),
                (candidate_version_id, user_id, candidate_version_id, user_id),
            )
            prov_row = await cursor.fetchone()
            if prov_row is None or not bool(prov_row[0]):
                raise TransactionError("active_or_archived_version_requires_provenance")

            closed_ids: list[str] = []
            reissued_id: str | None = None
            existing_id_for_conflict: str | None = None

            if single_valued:
                await cursor.execute(
                    sql.SQL(
                        "SELECT id, memory_key, content, lower(event_range), upper(event_range), "
                        "upper_inf(event_range), lower(recorded_range), layer, valid_from_basis, "
                        "assertion_mode, confidence, importance, reinforcement_count, extractor_version "
                        "FROM {mv} "
                        "WHERE user_id = %s AND subject_key = %s AND predicate_key = %s "
                        "AND single_valued = true AND status = 'active' "
                        "AND recorded_range IS NOT NULL AND upper_inf(recorded_range) "
                        "AND event_range && %s "
                        "ORDER BY lower(event_range) ASC, id ASC "
                        "FOR UPDATE"
                    ).format(mv=ident_mv),
                    (user_id, subject_key, predicate_key, cand_event_range),
                )
                overlapping = await cursor.fetchall()
                v_cand = _utc_instant(v_cand_raw)
                for row in overlapping:
                    (
                        old_id,
                        old_memory_key,
                        old_content,
                        v_old_raw,
                        v_old_end_raw,
                        v_old_upper_inf,
                        r_old_from_raw,
                        old_layer,
                        old_basis,
                        old_assertion_mode,
                        old_confidence,
                        old_importance,
                        old_reinforcement,
                        old_extractor,
                    ) = row
                    await _ensure_no_uncovered_c_archived_sources(
                        cursor,
                        schema_name,
                        mutating_replacement_id=old_id,
                        user_id=user_id,
                    )
                    existing_id_for_conflict = old_id
                    v_old = _utc_instant(v_old_raw)
                    v_old_end = None if v_old_upper_inf or v_old_end_raw is None else _utc_instant(v_old_end_raw)
                    r_old_from = _utc_instant(r_old_from_raw)

                    await cursor.execute(
                        sql.SQL("UPDATE {mv} SET recorded_range = %s WHERE id = %s AND user_id = %s").format(
                            mv=ident_mv
                        ),
                        (_tstzrange(r_old_from, tick.instant), old_id, user_id),
                    )
                    closed_ids.append(old_id)

                    if v_old < v_cand:
                        reissued_id = reissued_version_id or f"{old_id}__reissued"
                        reissued_upper = v_cand if v_old_end is None or v_cand < v_old_end else v_old_end
                        await _insert_version(
                            cursor,
                            schema_name,
                            version_id=reissued_id,
                            memory_key=old_memory_key,
                            user_id=user_id,
                            status="active",
                            subject_key=subject_key,
                            predicate_key=predicate_key,
                            single_valued=True,
                            content=old_content,
                            event_range=_tstzrange(v_old, reissued_upper),
                            recorded_range=r_open_range,
                            layer=old_layer,
                            valid_from_basis=old_basis,
                            assertion_mode=old_assertion_mode,
                            confidence=old_confidence,
                            importance=old_importance,
                            reinforcement_count=old_reinforcement,
                            extractor_version=old_extractor,
                        )
                        await _copy_provenance(
                            cursor,
                            schema_name,
                            from_version_id=old_id,
                            to_version_id=reissued_id,
                            user_id=user_id,
                        )

            await cursor.execute(
                sql.SQL(
                    "UPDATE {mv} SET status = 'active', recorded_range = %s WHERE id = %s AND user_id = %s"
                ).format(mv=ident_mv),
                (r_open_range, candidate_version_id, user_id),
            )
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {mc} ("
                    "id, candidate_version_id, existing_version_id, user_id, relation, "
                    "decision, reason_code, decided_by, decided_at"
                    ") VALUES (%s, %s, %s, %s, 'supersedes', 'accept', %s, %s, %s)"
                ).format(mc=ident_mc),
                (
                    resolved_conflict_id,
                    candidate_version_id,
                    existing_id_for_conflict,
                    user_id,
                    reason_code,
                    decided_by,
                    tick.instant,
                ),
            )
            await _record_audit_event(
                cursor,
                schema_name,
                user_id=user_id,
                memory_ref=candidate_version_id,
                event_type="conflict_resolved_accepted",
                event_at=tick.instant,
                details={
                    "candidate_version_id": candidate_version_id,
                    "closed_version_ids": closed_ids,
                    "reissued_version_id": reissued_id,
                    "conflict_id": resolved_conflict_id,
                },
            )

    return ResolutionOutcome(
        clock_tick=tick,
        candidate_version_id=candidate_version_id,
        resolution_status="active",
        closed_version_ids=tuple(closed_ids),
        reissued_version_id=reissued_id,
        conflict_id=resolved_conflict_id,
    )


async def end_multivalued(
    connection: AsyncConnection,
    *,
    user_id: str,
    version_id: str,
    valid_end: datetime,
    truncated_version_id: str | None = None,
    additional_evidence: Sequence[EvidenceInput] = (),
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
    ablation: AblationMode = "P",
    allow_a2b: bool = False,
) -> MultivaluedEndOutcome:
    """End one member of a multi-valued predicate (`single_valued = False`) via close-then-insert (R7).

    Closes `version_id` at `recorded_range = [c_1, c_2)` and inserts `truncated_version_id` with
    `event_range = [v_old, valid_end)` and `recorded_range = [c_2, NULL)`, copying all provenance.
    Under `A2` / `A2b`, replaces `version_id` with the truncated row without keeping closed
    recorded-time history. Under `A1` (`P-flat`), raises `TransactionError` because `A1` disables
    version truncation.
    """
    if ablation == "A2b" and not allow_a2b:
        raise AblationDisabledError("a2b_disabled_by_d15")
    if ablation == "A1":
        raise TransactionError("a1_flat_disables_multivalued_truncation")
    _validate_nonblank(version_id, code="multivalued_version_id_required")
    v_end = _utc_instant(valid_end)

    await _verify_tenant_and_lock_user(connection, user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )

    ident_mv = sql.Identifier(schema_name, "memory_version")
    new_id = truncated_version_id or f"{version_id}__ended"
    r_open_range = _tstzrange(tick.instant, None)

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT memory_key, status, subject_key, predicate_key, single_valued, content, "
                    "lower(event_range), upper(event_range), upper_inf(event_range), "
                    "lower(recorded_range), upper_inf(recorded_range), "
                    "layer, valid_from_basis, assertion_mode, confidence, importance, "
                    "reinforcement_count, extractor_version "
                    "FROM {mv} WHERE id = %s AND user_id = %s FOR UPDATE"
                ).format(mv=ident_mv),
                (version_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise TransactionError("multivalued_version_not_found")
            (
                memory_key,
                status,
                subject_key,
                predicate_key,
                single_valued,
                content,
                v_old_raw,
                v_old_end_raw,
                v_old_upper_inf,
                r_old_from_raw,
                r_old_upper_inf,
                layer,
                basis,
                assertion_mode,
                confidence,
                importance,
                reinforcement,
                extractor,
            ) = row

            if status != "active" or not r_old_upper_inf:
                raise TransactionError("end_multivalued_requires_open_active_version")
            if single_valued:
                raise TransactionError("end_multivalued_requires_non_single_valued_version")

            v_old = _utc_instant(v_old_raw)
            v_old_end = None if v_old_upper_inf or v_old_end_raw is None else _utc_instant(v_old_end_raw)
            if v_end <= v_old or (v_old_end is not None and v_end >= v_old_end):
                raise TransactionError("invalid_multivalued_valid_end")

            await _ensure_no_uncovered_c_archived_sources(
                cursor,
                schema_name,
                mutating_replacement_id=version_id,
                user_id=user_id,
            )

            closed_id: str | None = None
            deleted_id: str | None = None

            if ablation in ("A2", "A2b"):
                await _insert_version(
                    cursor,
                    schema_name,
                    version_id=new_id,
                    memory_key=memory_key,
                    user_id=user_id,
                    status="active",
                    subject_key=subject_key,
                    predicate_key=predicate_key,
                    single_valued=False,
                    content=content,
                    event_range=_tstzrange(v_old, v_end),
                    recorded_range=r_open_range,
                    layer=layer,
                    valid_from_basis=basis,
                    assertion_mode=assertion_mode,
                    confidence=confidence,
                    importance=importance,
                    reinforcement_count=reinforcement,
                    extractor_version=extractor,
                )
                await _copy_provenance(
                    cursor,
                    schema_name,
                    from_version_id=version_id,
                    to_version_id=new_id,
                    user_id=user_id,
                )
                if additional_evidence:
                    await _insert_provenance(
                        cursor,
                        schema_name,
                        version_id=new_id,
                        user_id=user_id,
                        evidence=additional_evidence,
                        derivations=(),
                    )
                await cursor.execute(
                    sql.SQL("DELETE FROM {mv} WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                    (version_id, user_id),
                )
                deleted_id = version_id
            else:
                r_old_from = _utc_instant(r_old_from_raw)
                await cursor.execute(
                    sql.SQL("UPDATE {mv} SET recorded_range = %s WHERE id = %s AND user_id = %s").format(mv=ident_mv),
                    (_tstzrange(r_old_from, tick.instant), version_id, user_id),
                )
                closed_id = version_id
                await _insert_version(
                    cursor,
                    schema_name,
                    version_id=new_id,
                    memory_key=memory_key,
                    user_id=user_id,
                    status="active",
                    subject_key=subject_key,
                    predicate_key=predicate_key,
                    single_valued=False,
                    content=content,
                    event_range=_tstzrange(v_old, v_end),
                    recorded_range=r_open_range,
                    layer=layer,
                    valid_from_basis=basis,
                    assertion_mode=assertion_mode,
                    confidence=confidence,
                    importance=importance,
                    reinforcement_count=reinforcement,
                    extractor_version=extractor,
                )
                await _copy_provenance(
                    cursor,
                    schema_name,
                    from_version_id=version_id,
                    to_version_id=new_id,
                    user_id=user_id,
                )
                if additional_evidence:
                    await _insert_provenance(
                        cursor,
                        schema_name,
                        version_id=new_id,
                        user_id=user_id,
                        evidence=additional_evidence,
                        derivations=(),
                    )

            await _record_audit_event(
                cursor,
                schema_name,
                user_id=user_id,
                memory_ref=new_id,
                event_type="multivalued_ended",
                event_at=tick.instant,
                details={
                    "ablation": ablation,
                    "closed_version_id": closed_id,
                    "deleted_version_id": deleted_id,
                    "truncated_version_id": new_id,
                },
            )

    return MultivaluedEndOutcome(
        ablation=ablation,
        clock_tick=tick,
        closed_version_id=closed_id,
        deleted_version_id=deleted_id,
        truncated_version_id=new_id,
        valid_end=v_end,
    )


async def archive_version(
    connection: AsyncConnection,
    *,
    user_id: str,
    version_id: str,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> ArchivalOutcome:
    """Transition an `active` version to `archived` under R5, Invariant 10, and D-27.

    Concurrency & lock protocol (D-27, Technical Specification §3.4):
    1. Acquires `pg_advisory_xact_lock(hashtext(user_id))`.
    2. Locks the source row `r` (`FOR UPDATE`) and all candidate replacement rows `r'`
       linked via `memory_derivation` (`FOR UPDATE` in deterministic ID order).
    3. Evaluates:
       - `(S)` Superseded belief: `NOT upper_inf(r.recorded_range)`.
       - `(C)` Consolidated into a covering active belief: distinct same-user `r'`
         (`r'.id <> r.id`) linked in `memory_derivation(derived_version_id = r'.id, source_version_id = r.id)`
         with `r'.status = 'active'`, `upper_inf(r'.recorded_range)`, and `r'.event_range @> r.event_range`.
    4. Prefers `'S'` when both `(S)` and `(C)` hold; sets `archival_branch = 'C'` when only `(C)` holds;
       rejects (`T1` / `T2`) with `ArchivalPreconditionError("archival_precondition_unmet")` when neither holds.
    5. Also verifies that archiving `r` does not leave any already-archived `(C)`-branch source row uncovered.
    """
    _validate_nonblank(version_id, code="archive_version_id_required")
    await _verify_tenant_and_lock_user(connection, user_id, schema_name)
    tick = await _resolve_clock(
        connection,
        user_id,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )

    ident_mv = sql.Identifier(schema_name, "memory_version")
    ident_md = sql.Identifier(schema_name, "memory_derivation")

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT status, event_range, recorded_range, upper_inf(recorded_range) "
                    "FROM {mv} WHERE id = %s AND user_id = %s FOR UPDATE"
                ).format(mv=ident_mv),
                (version_id, user_id),
            )
            src_row = await cursor.fetchone()
            if src_row is None:
                raise TransactionError("archive_version_not_found")
            status, src_event_range, src_recorded_range, src_rec_upper_inf = src_row
            if status != "active" or src_recorded_range is None:
                raise ArchivalPreconditionError("archive_requires_active_version")

            # Verify archiving version_id does not break coverage for an earlier (C)-archived source
            await _ensure_no_uncovered_c_archived_sources(
                cursor,
                schema_name,
                mutating_replacement_id=version_id,
                user_id=user_id,
            )

            # Lock derivation links and derived replacement rows in deterministic order
            await cursor.execute(
                sql.SQL(
                    "SELECT r_prime.id, r_prime.status, r_prime.recorded_range IS NOT NULL, "
                    "upper_inf(r_prime.recorded_range), (r_prime.event_range @> %s) "
                    "FROM {md} d "
                    "JOIN {mv} r_prime ON r_prime.id = d.derived_version_id "
                    "WHERE d.source_version_id = %s AND d.user_id = %s "
                    "AND r_prime.id <> %s AND r_prime.user_id = %s "
                    "ORDER BY r_prime.id ASC "
                    "FOR UPDATE OF d, r_prime"
                ).format(mv=ident_mv, md=ident_md),
                (src_event_range, version_id, user_id, version_id, user_id),
            )
            replacement_rows = await cursor.fetchall()

            branch_s = not bool(src_rec_upper_inf)
            branch_c = any(
                r_status == "active" and bool(r_has_rec) and bool(r_upper_inf) and bool(r_covers)
                for _r_id, r_status, r_has_rec, r_upper_inf, r_covers in replacement_rows
            )

            if branch_s:
                chosen_branch: ArchivalBranch = "S"
            elif branch_c:
                chosen_branch = "C"
            else:
                raise ArchivalPreconditionError("archival_precondition_unmet")

            await cursor.execute(
                sql.SQL(
                    "UPDATE {mv} SET status = 'archived', archival_branch = %s WHERE id = %s AND user_id = %s"
                ).format(mv=ident_mv),
                (chosen_branch, version_id, user_id),
            )
            await _record_audit_event(
                cursor,
                schema_name,
                user_id=user_id,
                memory_ref=version_id,
                event_type="version_archived",
                event_at=tick.instant,
                details={
                    "version_id": version_id,
                    "archival_branch": chosen_branch,
                },
            )

    return ArchivalOutcome(
        clock_tick=tick,
        archived_version_id=version_id,
        archival_branch=chosen_branch,
    )


__all__ = [
    "AblationDisabledError",
    "AblationMode",
    "ArchivalBranch",
    "ArchivalOutcome",
    "ArchivalPreconditionError",
    "AssertionMode",
    "BoundaryRevisionOutcome",
    "CandidateVersionInput",
    "CommittedVersion",
    "DerivationInput",
    "DerivationType",
    "EvidenceInput",
    "MemoryLayer",
    "MultivaluedEndOutcome",
    "ResolutionOutcome",
    "SupersessionOutcome",
    "TransactionError",
    "ValidFromBasis",
    "VersionStatus",
    "archive_version",
    "commit_initial_version",
    "end_multivalued",
    "ensure_archival_trigger",
    "resolve_conflict_pending",
    "revise_transition_boundary",
    "stage_candidate",
    "supersede_single",
]
