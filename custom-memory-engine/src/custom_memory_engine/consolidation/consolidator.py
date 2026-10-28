"""Derive covering semantic/summary versions from active sources (RL-061).

The pipeline is four pure-ish steps over one caller-owned transaction:

1. ``load_sources`` reads the selected source rows and their current derivation depth.
2. ``plan_consolidation`` validates the request against Invariant 10 ``(C)`` and the
   ``depth <= 3`` budget, and refuses anything it could not legally commit.
3. ``consolidate`` commits one derived version with ``MEMORY_DERIVATION`` lineage.
4. The caller may then call ``archive_version`` for each covered source.

Nothing here deletes, archives, or mutates a source. A consolidation failure leaves
the database exactly as it was, because every write happens inside the caller's
transaction and this module never commits.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from re import fullmatch
from typing import Literal
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row

from custom_memory_engine.clock import ClockTick
from custom_memory_engine.engine.ingest import derived_candidate
from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    CommittedVersion,
    DerivationType,
    MemoryLayer,
    commit_initial_version,
)

logger = structlog.get_logger(__name__)

#: Maximum derivation chain length, matching ``deletion.MAX_LINEAGE_DEPTH`` so a
#: consolidation the deletion walk cannot see is never created.
MAX_DERIVATION_DEPTH = 3

#: ``layer`` in the schema is one of ``episodic|semantic|opinion|persona``; "summary"
#: is a *derivation type*, not a layer. A summary is a ``semantic`` row whose
#: ``MEMORY_DERIVATION.derivation_type`` is ``summary``.
ConsolidationLayer = Literal["semantic"]


class ConsolidationError(InvalidParameterValue):
    """Static consolidation contract error (SQLSTATE 22023) without private content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceVersion:
    """One source row as consolidation needs to see it (no content, no audit data)."""

    version_id: str
    memory_key: str
    user_id: str
    subject_key: str
    predicate_key: str
    status: str
    layer: str
    single_valued: bool
    has_open_recorded_range: bool
    valid_from: datetime
    valid_until: datetime | None
    depth: int


@dataclass(frozen=True)
class ConsolidationPlan:
    """A validated consolidation request, ready to commit."""

    user_id: str
    version_id: str
    memory_key: str
    subject_key: str
    predicate_key: str
    content: str
    layer: MemoryLayer
    derivation_type: DerivationType
    single_valued: bool
    valid_from: datetime
    valid_until: datetime | None
    source_version_ids: tuple[str, ...]
    source_depth: int


@dataclass(frozen=True)
class ConsolidationOutcome:
    """What consolidation did, in content-free form suitable for a receipt."""

    version_id: str
    memory_key: str
    source_version_ids: tuple[str, ...]
    depth: int
    committed: CommittedVersion


async def _verify_context(connection: AsyncConnection, user_id: str) -> None:
    """Require a matching tenant context and take the per-user advisory lock (D-18)."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ConsolidationError("nonblank_user_id_required")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("consolidation_user_context_mismatch")
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))


def _schema(schema_name: str) -> str:
    """Same identifier contract as the transactional layer; never interpolate a raw name."""
    if not isinstance(schema_name, str) or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None:
        raise ConsolidationError("isolated_engine_schema_identifier_required")
    return schema_name


async def load_sources(
    connection: AsyncConnection,
    user_id: str,
    source_version_ids: Sequence[str],
    *,
    schema_name: str = "cme_engine",
) -> tuple[SourceVersion, ...]:
    """Read the requested source rows and their existing derivation depth.

    Depth is the longest derivation chain *above* a version (how many hops already
    sit between it and the rows it was built from), so a new derived version is one
    higher: ``max(source depth) + 1``. Sources must be ``active``: consolidating an
    archived or staging row would launder a non-belief into a belief.
    """
    if not isinstance(source_version_ids, Sequence) or isinstance(source_version_ids, (str, bytes)):
        raise ConsolidationError("source_version_id_sequence_required")
    ordered = tuple(dict.fromkeys(str(item) for item in source_version_ids))
    if not ordered:
        raise ConsolidationError("consolidation_requires_source_versions")
    if any(not item.strip() for item in ordered):
        raise ConsolidationError("nonblank_source_version_id_required")

    namespace = _schema(schema_name)
    await _verify_context(connection, user_id)

    versions = sql.Identifier(namespace, "memory_version")
    derivations = sql.Identifier(namespace, "memory_derivation")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, memory_key, user_id, subject_key, predicate_key, status, layer, "
                "single_valued, upper_inf(recorded_range), lower(valid_range), upper(valid_range) "
                "FROM {v} WHERE user_id = %s AND id = ANY(%s)"
            ).format(v=versions),
            (user_id, list(ordered)),
        )
        rows = await cursor.fetchall()
        found = {row[0]: row for row in rows}
        missing = [version_id for version_id in ordered if version_id not in found]
        if missing:
            # Count only: a deleted or cross-tenant id must not be distinguishable.
            raise ConsolidationError("source_version_not_found")

        # Depth = length of the longest derivation chain *above* a version, computed over
        # the user's links and then looked up for the requested sources. A new derived
        # version sits one hop above that, so its depth is ``max(depth) + 1``.
        await cursor.execute(
            sql.SQL(
                "WITH RECURSIVE chain(derived, source, depth) AS ("
                "  SELECT derived_version_id, source_version_id, 1 FROM {d} WHERE user_id = %s "
                "  UNION ALL "
                "  SELECT c.derived, d.source_version_id, c.depth + 1 "
                "  FROM {d} AS d JOIN chain AS c ON d.derived_version_id = c.source "
                "  WHERE d.user_id = %s AND c.depth < %s "
                ") SELECT derived, MAX(depth) FROM chain GROUP BY derived"
            ).format(d=derivations),
            (user_id, user_id, MAX_DERIVATION_DEPTH),
        )
        depths = {row[0]: int(row[1]) for row in await cursor.fetchall()}

    sources: list[SourceVersion] = []
    for version_id in ordered:
        row = found[version_id]
        status = row[5]
        if status != "active":
            raise ConsolidationError("source_version_must_be_active")
        sources.append(
            SourceVersion(
                version_id=row[0],
                memory_key=row[1],
                user_id=row[2],
                subject_key=row[3],
                predicate_key=row[4],
                status=status,
                layer=row[6],
                single_valued=bool(row[7]),
                has_open_recorded_range=bool(row[8]),
                valid_from=row[9],
                valid_until=row[10],
                depth=depths.get(version_id, 0),
            )
        )
    return tuple(sources)


def _covering_interval(
    sources: Sequence[SourceVersion],
) -> tuple[datetime, datetime | None]:
    """Smallest single valid range covering every source, preserving open-endedness.

    ``V(r') @> V(r)`` must hold for every source. The lower bound is the earliest
    source start; the upper bound is unbounded if any source is unbounded, otherwise
    the latest source end.
    """
    valid_from = min(source.valid_from for source in sources)
    if any(source.valid_until is None for source in sources):
        return valid_from, None
    ends = [source.valid_until for source in sources if source.valid_until is not None]
    return valid_from, max(ends)


def plan_consolidation(
    sources: Sequence[SourceVersion],
    *,
    user_id: str,
    content: str,
    version_id: str | None = None,
    layer: ConsolidationLayer = "semantic",
    derivation_type: DerivationType = "consolidation",
    single_valued: bool = False,
) -> ConsolidationPlan:
    """Validate a request against ``(C)`` coverage rules and the depth budget.

    Raises ``ConsolidationError`` rather than returning a plan that could not be
    committed, so a caller never writes a partially-valid consolidation.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ConsolidationError("nonblank_user_id_required")
    if not isinstance(content, str) or not content.strip():
        raise ConsolidationError("nonblank_consolidated_content_required")
    if layer != "semantic":
        # A derived row is a belief about the user, so it is ``semantic``. Episodic
        # rows are turn-grounded and must carry evidence, which a derived row lacks.
        raise ConsolidationError("consolidation_layer_must_be_semantic")
    if derivation_type not in ("consolidation", "summary"):
        raise ConsolidationError("consolidation_derivation_type_invalid")
    if not isinstance(single_valued, bool):
        raise ConsolidationError("single_valued_flag_required")
    if not sources:
        raise ConsolidationError("consolidation_requires_source_versions")

    identifiers = {source.version_id for source in sources}
    if len(identifiers) != len(sources):
        raise ConsolidationError("duplicate_source_version_id")

    resolved_id = version_id if version_id is not None else f"c-{uuid4().hex}"
    if not isinstance(resolved_id, str) or not resolved_id.strip():
        raise ConsolidationError("nonblank_version_id_required")
    if resolved_id in identifiers:
        # A derived version may never derive from itself.
        raise ConsolidationError("derived_version_must_differ_from_sources")

    owners = {source.user_id for source in sources}
    if owners != {user_id}:
        raise ConsolidationError("source_versions_must_share_one_user")

    depth = max(source.depth for source in sources) + 1
    if depth > MAX_DERIVATION_DEPTH:
        raise ConsolidationError("derivation_depth_exceeded")

    keys = {(source.subject_key, source.predicate_key) for source in sources}
    if len(keys) != 1:
        # One derived artifact summarizes one (subject, predicate); spanning several
        # would make the covering-range claim meaningless for archival.
        raise ConsolidationError("sources_must_share_subject_and_predicate")
    subject_key, predicate_key = next(iter(keys))

    memory_key = sorted(source.memory_key for source in sources)[0]
    valid_from, valid_until = _covering_interval(sources)

    return ConsolidationPlan(
        user_id=user_id,
        version_id=resolved_id,
        memory_key=memory_key,
        subject_key=subject_key,
        predicate_key=predicate_key,
        content=content,
        layer=layer,
        derivation_type=derivation_type,
        single_valued=single_valued,
        valid_from=valid_from,
        valid_until=valid_until,
        source_version_ids=tuple(source.version_id for source in sources),
        source_depth=depth,
    )


def plan_to_candidate(plan: ConsolidationPlan) -> CandidateVersionInput:
    """Materialize a validated plan as the transactional write input.

    The store layer passes this to ``commit_initial_version`` instead of
    ``apply_candidate``: a derived version has no turn evidence, so the evidence
    check would reject it as ``unconfirmed_assistant_claim``. Invariant 1 is
    satisfied by the derivation links instead.
    """
    return derived_candidate(
        version_id=plan.version_id,
        user_id=plan.user_id,
        memory_key=plan.memory_key,
        subject_key=plan.subject_key,
        predicate_key=plan.predicate_key,
        content=plan.content,
        valid_from=plan.valid_from,
        valid_until=plan.valid_until,
        source_version_ids=plan.source_version_ids,
        layer=plan.layer,
        derivation_type=plan.derivation_type,
    )


async def consolidate(
    connection: AsyncConnection,
    user_id: str,
    source_version_ids: Sequence[str],
    *,
    content: str,
    version_id: str | None = None,
    layer: ConsolidationLayer = "semantic",
    derivation_type: DerivationType = "consolidation",
    single_valued: bool = False,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> ConsolidationOutcome:
    """Derive one consolidated version covering the given active sources.

    The derived row is committed with ``recorded_range = [c, NULL)``. Sources are
    left untouched; archive them separately via ``archive_version`` afterwards if
    the caller wants branch ``(C)`` archival.
    """
    sources = await load_sources(connection, user_id, source_version_ids, schema_name=schema_name)
    plan = plan_consolidation(
        sources,
        user_id=user_id,
        content=content,
        version_id=version_id,
        layer=layer,
        derivation_type=derivation_type,
        single_valued=single_valued,
    )
    candidate = plan_to_candidate(plan)
    committed, _ = await commit_initial_version(
        connection,
        candidate,
        schema_name=schema_name,
        clock_tick=clock_tick,
        replay_time=replay_time,
    )
    logger.info(
        "memory_consolidated",
        user_id=user_id,
        version_id=plan.version_id,
        memory_key=plan.memory_key,
        source_count=len(plan.source_version_ids),
        depth=plan.source_depth,
        layer=plan.layer,
    )
    return ConsolidationOutcome(
        version_id=plan.version_id,
        memory_key=plan.memory_key,
        source_version_ids=plan.source_version_ids,
        depth=plan.source_depth,
        committed=committed,
    )
