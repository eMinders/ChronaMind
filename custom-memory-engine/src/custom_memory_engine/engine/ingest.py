"""Turn persistence and candidate application: the engine's write path (RL-060 wiring).

Two responsibilities, deliberately separate:

* ``persist_turn`` is the fast path. It records the verbatim turn row that evidence
  and provenance point at. It performs no extraction and no gate work.
* ``apply_candidate`` is the slow-path body. It runs the validation gate on one
  already-extracted candidate and routes the verdict to the write procedure that
  implements it: initial commit, single-valued supersession, ``plans_to`` routing,
  reinforcement of a duplicate, or a held/rejected staging row.

Extraction itself is injected: this module never calls a model, so one frozen
extraction can be replayed across ``P`` and its ablations (``RL-032``).

Ordering note: ``MEMORY_CONFLICT.candidate_version_id`` is a foreign key to
``memory_version``, so the verdict is recorded *after* the row it describes exists.
Recording first would fail for a brand-new candidate.

Every function requires a caller-owned READ COMMITTED transaction with a matching
``app.user_id``. Nothing here commits, closes the connection, or changes tenant.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.engine.gate import (
    ConflictRelation,
    GateCheck,
    GateDecision,
    GateInput,
    evaluate,
    existing_relation,
    record_decision,
)
from custom_memory_engine.engine.transactions import (
    AblationMode,
    CandidateVersionInput,
    CommittedVersion,
    DerivationInput,
    MemoryLayer,
    ResolutionOutcome,
    SupersessionOutcome,
    commit_initial_version,
    resolve_conflict_pending,
    stage_candidate,
    supersede_single,
)

logger = structlog.get_logger(__name__)

PLANS_TO_PREDICATE = "plans_to"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
# A2/A2b delete the superseded row, so no MEMORY_CONFLICT row may still reference it.
DELETING_ABLATIONS = frozenset({"A2", "A2b"})


class IngestError(InvalidParameterValue):
    """Static ingest contract error (SQLSTATE 22023) without private content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TurnInput:
    """One verbatim dialogue turn; ``sequence_no`` orders turns within a session."""

    turn_id: str
    user_id: str
    session_id: str
    sequence_no: int
    role: Literal["user", "assistant"]
    content: str
    occurred_at: datetime


@dataclass(frozen=True)
class AppliedCandidate:
    """Outcome of applying one extracted candidate through the gate."""

    decision: GateDecision
    committed: CommittedVersion | None = None
    supersession: SupersessionOutcome | None = None
    existing_version_id: str | None = None


async def _verify_context(connection: AsyncConnection, user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise IngestError("ingest_user_id_required")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("ingest_user_context_mismatch")


async def persist_turn(
    connection: AsyncConnection,
    turn: TurnInput,
    *,
    schema_name: str = "cme_engine",
    create_user: bool = False,
    create_session: bool = True,
) -> None:
    """Insert one turn idempotently so provenance can reference it.

    ``ON CONFLICT DO NOTHING`` keeps replay safe: re-ingesting the same turn must
    neither fail the run nor rewrite history.
    """
    if not isinstance(turn.turn_id, str) or not turn.turn_id.strip():
        raise IngestError("ingest_turn_id_required")
    if turn.role not in ("user", "assistant"):
        raise IngestError("ingest_turn_role_invalid")
    if not isinstance(turn.sequence_no, int) or isinstance(turn.sequence_no, bool) or turn.sequence_no < 0:
        raise IngestError("ingest_sequence_number_required")
    if not isinstance(turn.occurred_at, datetime) or turn.occurred_at.utcoffset() is None:
        raise IngestError("ingest_aware_occurred_at_required")

    await _verify_context(connection, turn.user_id)
    if create_user:
        await connection.execute(
            sql.SQL(
                "INSERT INTO {users} (id, external_ref, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
            ).format(users=sql.Identifier(schema_name, "app_user")),
            (turn.user_id, turn.user_id, turn.occurred_at),
        )
    if create_session:
        await connection.execute(
            sql.SQL(
                "INSERT INTO {sessions} (id, user_id, started_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
            ).format(sessions=sql.Identifier(schema_name, "session")),
            (turn.session_id, turn.user_id, turn.occurred_at),
        )
    await connection.execute(
        sql.SQL(
            "INSERT INTO {turns} (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING"
        ).format(turns=sql.Identifier(schema_name, "turn")),
        (turn.turn_id, turn.session_id, turn.user_id, turn.sequence_no, turn.role, turn.content, turn.occurred_at),
    )


async def _version_exists(connection: AsyncConnection, schema_name: str, user_id: str, version_id: str) -> bool:
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {mv} WHERE id = %s AND user_id = %s)").format(
                mv=sql.Identifier(schema_name, "memory_version")
            ),
            (version_id, user_id),
        )
        row = await cursor.fetchone()
    return bool(row and row[0])


async def apply_candidate(
    connection: AsyncConnection,
    candidate: CandidateVersionInput,
    *,
    evidence_turns: Sequence[tuple[str, str]],
    gate_input: GateInput | None = None,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
    ablation: AblationMode = "P",
    allow_a2b: bool = False,
    gate_enabled: Sequence[GateCheck] = (
        "evidence",
        "assertion_mode",
        "instruction_screen",
        "conflict",
        "confidence",
    ),
    calibration: dict[str, float] | None = None,
    decided_by: str = "validation-gate",
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
) -> AppliedCandidate:
    """Gate one extracted candidate and apply the verdict transactionally.

    ``A3`` (``P-nogate``) passes ``gate_enabled=()``; ``A4`` disables individual
    checks. With no conflict check, a single-valued candidate is superseded.
    """
    await _verify_context(connection, candidate.user_id)
    tick = clock_tick or await allocate_clock(
        connection, candidate.user_id, schema_name=schema_name, replay_time=replay_time
    )

    if gate_input is None:
        gate_input = GateInput(
            version_id=candidate.version_id,
            user_id=candidate.user_id,
            content=candidate.content,
            evidence_spans=tuple(evidence.evidence_span for evidence in candidate.evidence),
            evidence_turns=tuple(evidence_turns),
            assertion_mode=candidate.assertion_mode,
            predicate_key=candidate.predicate_key,
            confidence=candidate.confidence,
        )
    elif gate_input.version_id != candidate.version_id or gate_input.user_id != candidate.user_id:
        raise IngestError("gate_input_identity_mismatch")

    relation: ConflictRelation | None = None
    existing_version_id: str | None = None
    if "conflict" in gate_enabled and candidate.single_valued:
        found, existing_version_id = await existing_relation(
            connection,
            user_id=candidate.user_id,
            subject_key=candidate.subject_key,
            predicate_key=candidate.predicate_key,
            candidate_content=candidate.content,
            schema_name=schema_name,
        )
        relation = found or ("update" if existing_version_id else "unrelated")

    decision = evaluate(gate_input, relation=relation, calibration=calibration, enabled=gate_enabled)

    if decision.decision == "reject":
        staged = await stage_candidate(connection, candidate, initial_status="rejected", schema_name=schema_name)
        await _record(connection, schema_name, candidate, decision, None, decided_by)
        logger.info(
            "candidate_rejected",
            user_id=candidate.user_id,
            version_id=candidate.version_id,
            reason_code=decision.reason_code,
        )
        return AppliedCandidate(decision, committed=staged)

    if decision.decision == "hold":
        staged = await stage_candidate(
            connection, candidate, initial_status="conflict_pending", schema_name=schema_name
        )
        await _record(connection, schema_name, candidate, decision, existing_version_id, decided_by)
        logger.info(
            "candidate_held",
            user_id=candidate.user_id,
            version_id=candidate.version_id,
            reason_code=decision.reason_code,
        )
        return AppliedCandidate(decision, committed=staged, existing_version_id=existing_version_id)

    if decision.reason_code == "reinforce_duplicate" and existing_version_id is not None:
        await connection.execute(
            sql.SQL(
                "UPDATE {mv} SET reinforcement_count = reinforcement_count + 1 WHERE id = %s AND user_id = %s"
            ).format(mv=sql.Identifier(schema_name, "memory_version")),
            (existing_version_id, candidate.user_id),
        )
        await _record(connection, schema_name, candidate, decision, existing_version_id, decided_by)
        logger.info("candidate_reinforced", user_id=candidate.user_id, version_id=existing_version_id)
        return AppliedCandidate(decision, existing_version_id=existing_version_id)

    target = _routed_candidate(candidate, decision)
    recorded_existing = None if ablation in DELETING_ABLATIONS else existing_version_id

    async def store_embedding(version_id: str) -> None:
        if embedding is not None:
            await attach_embedding(
                connection,
                version_id=version_id,
                user_id=candidate.user_id,
                embedding=embedding,
                embedding_model=embedding_model or DEFAULT_EMBEDDING_MODEL,
                schema_name=schema_name,
            )

    if target.single_valued and ablation != "A1":
        # Supersession is only meaningful when an open current belief exists for the
        # *routed* predicate. ``planned`` candidates move to ``plans_to``, which has no
        # prior belief, so they commit as an initial version instead.
        open_belief_id: str | None = None
        if target.predicate_key == candidate.predicate_key:
            open_belief_id = existing_version_id
        else:
            _, open_belief_id = await existing_relation(
                connection,
                user_id=target.user_id,
                subject_key=target.subject_key,
                predicate_key=target.predicate_key,
                candidate_content=target.content,
                schema_name=schema_name,
            )
        if open_belief_id is not None:
            outcome = await supersede_single(
                connection,
                target,
                schema_name=schema_name,
                clock_tick=tick,
                ablation=ablation,
                allow_a2b=allow_a2b,
            )
            await store_embedding(target.version_id)
            await _record(connection, schema_name, target, decision, recorded_existing, decided_by)
            logger.info(
                "candidate_superseded",
                user_id=candidate.user_id,
                version_id=target.version_id,
                reason_code=decision.reason_code,
                held=outcome.held_in_conflict_pending,
            )
            return AppliedCandidate(decision, supersession=outcome, existing_version_id=recorded_existing)

    committed, _ = await commit_initial_version(
        connection, target, schema_name=schema_name, clock_tick=tick, ablation=ablation
    )
    await store_embedding(target.version_id)
    await _record(connection, schema_name, target, decision, recorded_existing, decided_by)
    logger.info(
        "candidate_committed",
        user_id=candidate.user_id,
        version_id=target.version_id,
        reason_code=decision.reason_code,
    )
    return AppliedCandidate(decision, committed=committed, existing_version_id=existing_version_id)


async def _record(
    connection: AsyncConnection,
    schema_name: str,
    candidate: CandidateVersionInput,
    decision: GateDecision,
    existing_version_id: str | None,
    decided_by: str,
) -> None:
    """Record the verdict against the version row it actually concerns.

    ``MEMORY_CONFLICT.candidate_version_id`` is a foreign key, so a decision that
    created no row (a reinforced duplicate) references the version it acted on; the
    ``reinforce_duplicate`` reason code keeps that intent explicit.
    """
    if existing_version_id is not None and not await _version_exists(
        connection, schema_name, candidate.user_id, existing_version_id
    ):
        existing_version_id = None
    candidate_version_id = candidate.version_id
    if not await _version_exists(connection, schema_name, candidate.user_id, candidate_version_id):
        if existing_version_id is None:
            raise IngestError("conflict_decision_requires_a_persisted_version")
        candidate_version_id = existing_version_id
    await record_decision(
        connection,
        user_id=candidate.user_id,
        candidate_version_id=candidate_version_id,
        decision=decision,
        existing_version_id=existing_version_id,
        decided_by=decided_by,
        schema_name=schema_name,
    )


def _routed_candidate(candidate: CandidateVersionInput, decision: GateDecision) -> CandidateVersionInput:
    """Apply the routing the gate decided (``planned`` -> ``plans_to``)."""
    if decision.predicate_key and decision.predicate_key != candidate.predicate_key:
        return candidate.model_copy(update={"predicate_key": decision.predicate_key})
    return candidate


async def attach_embedding(
    connection: AsyncConnection,
    *,
    version_id: str,
    user_id: str,
    embedding: Sequence[float],
    embedding_model: str,
    schema_name: str = "cme_engine",
) -> None:
    """Persist one dense vector for a version, replacing any prior vector for that model.

    The dense channel in ``retrieval.rank_versions`` reads exactly this table, so a
    committed candidate without a row here is retrievable only lexically. The caller
    owns embeddings (the engine never loads a model) and passes the frozen model id.
    """
    if not isinstance(embedding_model, str) or not embedding_model.strip():
        raise IngestError("nonblank_embedding_model_required")
    if len(embedding) != 384:
        raise IngestError("embedding_dimension_mismatch")
    await _verify_context(connection, user_id)
    await connection.execute(
        sql.SQL(
            "INSERT INTO {e} (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (version_id, embedding_model) DO UPDATE SET embedding = EXCLUDED.embedding"
        ).format(e=sql.Identifier(schema_name, "memory_embedding")),
        (version_id, embedding_model, user_id, list(embedding)),
    )


async def resolve_held(
    connection: AsyncConnection,
    *,
    user_id: str,
    candidate_version_id: str,
    decision: Literal["accept", "reject"],
    reason_code: str,
    schema_name: str = "cme_engine",
    replay_time: datetime | None = None,
    decided_by: str = "memory-review",
) -> ResolutionOutcome:
    """Promote or reject a held candidate; used by the inspector and by review replay."""
    await _verify_context(connection, user_id)
    if decision not in ("accept", "reject"):
        raise IngestError("invalid_resolution_decision")
    return await resolve_conflict_pending(
        connection,
        user_id=user_id,
        candidate_version_id=candidate_version_id,
        decision=decision,
        reason_code=reason_code,
        schema_name=schema_name,
        replay_time=replay_time,
        decided_by=decided_by,
    )


def derived_candidate(
    *,
    version_id: str,
    user_id: str,
    memory_key: str,
    subject_key: str,
    predicate_key: str,
    content: str,
    valid_from: datetime,
    valid_until: datetime | None,
    source_version_ids: Sequence[str],
    layer: MemoryLayer = "semantic",
    derivation_type: str = "consolidation",
) -> CandidateVersionInput:
    """Build a consolidated/summary candidate carrying ``MEMORY_DERIVATION`` lineage.

    A derived version needs no turn evidence (Invariant 1 accepts evidence *or* an
    outgoing derivation), and covering ``V(r)`` for every source is what makes
    branch ``(C)`` archival legal.
    """
    if not source_version_ids:
        raise IngestError("derivation_requires_source_versions")
    return CandidateVersionInput(
        version_id=version_id,
        memory_key=memory_key,
        user_id=user_id,
        subject_key=subject_key,
        predicate_key=predicate_key,
        single_valued=False,
        content=content,
        valid_from=valid_from,
        valid_until=valid_until,
        layer=layer,
        evidence=(),
        derivations=tuple(
            DerivationInput(source_version_id=source_id, derivation_type=derivation_type)  # type: ignore[arg-type]
            for source_id in source_version_ids
        ),
    )


__all__ = (
    "AppliedCandidate",
    "DEFAULT_EMBEDDING_MODEL",
    "IngestError",
    "PLANS_TO_PREDICATE",
    "TurnInput",
    "apply_candidate",
    "derived_candidate",
    "attach_embedding",
    "persist_turn",
    "resolve_held",
)
