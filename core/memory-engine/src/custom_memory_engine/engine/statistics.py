"""Per-artifact statistics for the ``stats`` operation (``API_V2.md`` §4.11, ``E-036``'s Observe sibling).

``API_V2.md`` promised ``stats(ctx, artifact_id) -> Stats`` — "counts, coverage, refutation attempts, verification
status and the *ids* of support and counter items", with the rule that item **content** is returned only if the caller
could read it through ``query``. Until this module the operation had no handler, so the ledger an abstraction carries
(``I15``) and the verification record behind its status (``I11``) were write-only through the public API: a caller
could have a belief verified and could never read what that verification said.

**Ids, never content.** Every identifier this module returns is an id the caller already has: the artifact it named,
the versions it may read (``targets_readable`` proved the artifact, and the ledger's items are read under the same
row-level security), and nothing else. A counter item a deletion removed is simply absent — not a tombstone, not an
error.

**Nothing here decides anything.** Counts are read from ``abstraction_stats`` (which the schema keeps balanced,
``o = s + k + u``), verification from the append-only ``verification`` table, staleness from
:func:`~.staleness.staleness_inputs`, and procedure outcomes from ``outcome_stats``. No LLM is involved and no score is
computed: this is a report, and a report that recomputed its own numbers would be a second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.engine.outcomes import OutcomeStats
from custom_memory_engine.engine.outcomes import stats_for as outcome_stats_for
from custom_memory_engine.engine.staleness import ClosureCache, StalenessInputs, staleness_inputs
from custom_memory_engine.soundness.admission import DEFAULT_TAU_CONFIDENCE, DEFAULT_TAU_COVERAGE


class StatsError(LookupError):
    """A static code: never an id, a path or content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ArtifactStats:
    """What the engine can say about one artifact without returning its content or anyone else's."""

    artifact_id: str
    kind: str
    lifecycle: str
    #: ``none`` for grounded artifacts, ``unverified``/``verified``/``refuted`` for abstractions (``I11``).
    verification: str
    #: The five derived causes, in the order ``engine/staleness.py`` defines them. Never a stored flag (``V2-D24``).
    staleness: StalenessInputs
    support_count: int
    counter_count: int
    undetermined_count: int
    opportunity_count: int
    coverage: float
    refutation_attempts: int
    confidence: float | None
    confidence_method_id: str | None
    expires_at: datetime | None
    closed_over_hash: str | None
    #: Ids only, and only ids the caller's own row-level security returned.
    support_ids: tuple[str, ...]
    counter_ids: tuple[str, ...]
    #: The newest verification record, if any: the split it was checked on, never the data behind it.
    verified_on: datetime | None
    held_out_split_id: str | None
    negative_control_slice_id: str | None
    windows_disjoint: bool | None
    #: Procedure outcomes, for a procedure; ``None`` for everything else.
    outcomes: OutcomeStats | None = None
    #: Sorted readable derivation/evidence/counter source ids used in ledger computation.
    derivation_sources: tuple[str, ...] = ()
    #: Pinned-artifact metadata for the newest verification record, if any.
    artifact_pinning_status: str | None = None
    pinning_label: str | None = None
    verified_support_ids: tuple[str, ...] = ()
    verified_held_out_ids: tuple[str, ...] = ()


async def _row(connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str) -> tuple[object, ...]:
    """The artifact and its ledger in one statement, or ``StatsError('artifact_not_found')``.

    RLS decides visibility, so an id the caller may not read is the same answer as an id that does not exist
    (``THREAT_MODEL.md`` T-11). The left join is what makes a grounded artifact — which has no ledger, no expiry and no
    verification — reportable rather than an error.
    """
    del user_id
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT v.id, v.kind, v.lifecycle,"
                " COALESCE(s.verification_status, 'none'),"
                " COALESCE(s.support_count, 0), COALESCE(s.counter_count, 0),"
                " COALESCE(s.undetermined_count, 0), COALESCE(s.opportunity_count, 0),"
                " COALESCE(s.coverage, 0)::double precision, COALESCE(s.refutation_attempts, 0),"
                " s.confidence, s.confidence_method_id, s.expires_at, s.closed_over_hash"
                " FROM {v} AS v LEFT JOIN {s} AS s ON s.version_id = v.id AND s.user_id = v.user_id"
                " WHERE v.id = %s"
            ).format(
                v=sql.Identifier(schema_name, "memory_version"), s=sql.Identifier(schema_name, "abstraction_stats")
            ),
            (artifact_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise StatsError("artifact_not_found")
    return tuple(row)


async def _support_ids(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[str, ...]:
    """The versions this one was derived from, in declaration order, readable-only.

    ``memory_derivation`` is the graph ``I1'`` requires an inferred artifact to carry, so this is the honest answer to
    "what supports it" — not the ledger's ``support_count``, which counts opportunities rather than specific rows.
    """
    del user_id
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT d.source_version_id FROM {d} AS d JOIN {v} AS v"
                " ON v.id = d.source_version_id AND v.user_id = d.user_id"
                " WHERE d.derived_version_id = %s ORDER BY d.source_version_id"
            ).format(
                d=sql.Identifier(schema_name, "memory_derivation"), v=sql.Identifier(schema_name, "memory_version")
            ),
            (artifact_id,),
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def _counter_ids(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[str, ...]:
    """Counter-items this principal may read, sorted, never containing ``artifact_id`` itself.

    Combines readable ``memory_conflict`` counterparts and readable ``association(type='contradicts')`` counterparts.
    """
    del user_id
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT DISTINCT other FROM ("
                " SELECT CASE WHEN c.candidate_version_id = %s THEN c.existing_version_id"
                "             ELSE c.candidate_version_id END AS other"
                " FROM {c} AS c JOIN {v} AS v ON v.user_id = c.user_id"
                "  AND v.id = CASE WHEN c.candidate_version_id = %s THEN c.existing_version_id"
                "                  ELSE c.candidate_version_id END"
                " WHERE %s IN (c.candidate_version_id, c.existing_version_id)"
                " UNION ALL"
                " SELECT CASE WHEN a.from_id = %s THEN a.to_id ELSE a.from_id END AS other"
                " FROM {a} AS a JOIN {v} AS v ON v.user_id = a.user_id"
                "  AND v.id = CASE WHEN a.from_id = %s THEN a.to_id ELSE a.from_id END"
                " WHERE a.type = 'contradicts' AND %s IN (a.from_id, a.to_id)"
                ") AS found WHERE other IS NOT NULL AND other <> %s ORDER BY other"
            ).format(
                c=sql.Identifier(schema_name, "memory_conflict"),
                a=sql.Identifier(schema_name, "association"),
                v=sql.Identifier(schema_name, "memory_version"),
            ),
            (
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
                artifact_id,
            ),
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def _derivation_sources(
    connection: AsyncConnection,
    user_id: str,
    support_ids: tuple[str, ...],
    counter_ids: tuple[str, ...],
    schema_name: str,
) -> tuple[str, ...]:
    """Sorted readable derivation, evidence, and counter source ids used in ledger computation."""
    del user_id
    sources: set[str] = set(support_ids) | set(counter_ids)
    if support_ids:
        src_list = list(support_ids)
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT DISTINCT other FROM ("
                    " SELECT CASE WHEN a.from_id = ANY(%s) THEN a.to_id ELSE a.from_id END AS other"
                    " FROM {a} AS a JOIN {v} AS v ON v.user_id = a.user_id"
                    "  AND v.id = CASE WHEN a.from_id = ANY(%s) THEN a.to_id ELSE a.from_id END"
                    " WHERE a.type = 'contradicts'"
                    "  AND a.origin::text IN ('grounding_policy', 'tier_c')"
                    "  AND (a.from_id = ANY(%s) OR a.to_id = ANY(%s))"
                    "  AND v.status = 'active' AND v.lifecycle IN ('observed', 'asserted')"
                    "  AND COALESCE(v.assertion_mode, 'asserted') = 'asserted'"
                    " UNION ALL"
                    " SELECT CASE WHEN c.candidate_version_id = ANY(%s) THEN c.existing_version_id"
                    "             ELSE c.candidate_version_id END AS other"
                    " FROM {c} AS c JOIN {v} AS v ON v.user_id = c.user_id"
                    "  AND v.id = CASE WHEN c.candidate_version_id = ANY(%s) THEN c.existing_version_id"
                    "                  ELSE c.candidate_version_id END"
                    " WHERE c.decision <> 'reject'"
                    "  AND (c.decision = 'hold'"
                    "       OR c.relation IN ('contradicts', 'contradiction', 'conflict', 'conflicts'))"
                    "  AND (c.candidate_version_id = ANY(%s) OR c.existing_version_id = ANY(%s))"
                    "  AND v.status IN ('active', 'conflict_pending')"
                    "  AND v.lifecycle IN ('observed', 'asserted', 'contested')"
                    ") AS found WHERE other IS NOT NULL"
                ).format(
                    a=sql.Identifier(schema_name, "association"),
                    c=sql.Identifier(schema_name, "memory_conflict"),
                    v=sql.Identifier(schema_name, "memory_version"),
                ),
                (
                    src_list,
                    src_list,
                    src_list,
                    src_list,
                    src_list,
                    src_list,
                    src_list,
                    src_list,
                ),
            )
            sources.update(str(row[0]) for row in await cursor.fetchall())
    return tuple(sorted(sources))


def _pinning_label(status: str | None) -> str | None:
    if status == "unpinned_pre_migration":
        return "unpinned (pre-migration)"
    if status == "pinned":
        return "pinned"
    return None


async def _verification(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[
    datetime | None,
    str | None,
    str | None,
    bool | None,
    str | None,
    str | None,
    tuple[str, ...],
    tuple[str, ...],
]:
    """The newest verification record and its pinned support/held-out artifact ids."""
    del user_id
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, verified_on, held_out_split_id, negative_control_slice_id,"
                " windows_disjoint, artifact_pinning_status FROM {t}"
                " WHERE version_id = %s ORDER BY verified_on DESC, id DESC LIMIT 1"
            ).format(t=sql.Identifier(schema_name, "verification")),
            (artifact_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, None, None, None, None, None, (), ()
        verification_id = str(row[0])
        pinning_status = None if row[5] is None else str(row[5])
        await cursor.execute(
            sql.SQL("SELECT role, artifact_id FROM {va} WHERE verification_id = %s ORDER BY artifact_id").format(
                va=sql.Identifier(schema_name, "verification_artifact")
            ),
            (verification_id,),
        )
        va_rows = await cursor.fetchall()
    support_pinned = tuple(sorted(str(r[1]) for r in va_rows if r[0] == "support"))
    held_out_pinned = tuple(sorted(str(r[1]) for r in va_rows if r[0] == "held_out"))
    return (
        row[1] if isinstance(row[1], datetime) else None,
        str(row[2]),
        str(row[3]),
        bool(row[4]),
        pinning_status,
        _pinning_label(pinning_status),
        support_pinned,
        held_out_pinned,
    )


async def read_stats(
    connection: AsyncConnection,
    *,
    user_id: str,
    artifact_id: str,
    cache: ClosureCache,
    schema_name: str = "cme_engine",
    tau_confidence: float = DEFAULT_TAU_CONFIDENCE,
    tau_coverage: float = DEFAULT_TAU_COVERAGE,
) -> ArtifactStats:
    """Everything the caller may know about one readable artifact, and nothing it may not."""
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise StatsError("artifact_id_required")
    row = await _row(connection, user_id, artifact_id, schema_name)
    kind, lifecycle = str(row[1]), str(row[2])
    staleness = await staleness_inputs(
        connection,
        user_id,
        artifact_id,
        cache=cache,
        schema_name=schema_name,
        tau_confidence=tau_confidence,
        tau_coverage=tau_coverage,
    )
    (
        verified_on,
        split,
        control,
        disjoint,
        pinning_status,
        pinning_lbl,
        verified_sup,
        verified_held,
    ) = await _verification(connection, user_id, artifact_id, schema_name)
    support_ids = await _support_ids(connection, user_id, artifact_id, schema_name)
    counter_ids = await _counter_ids(connection, user_id, artifact_id, schema_name)
    derivation_sources = await _derivation_sources(connection, user_id, support_ids, counter_ids, schema_name)
    outcomes = await outcome_stats_for(
        connection,
        user_id=user_id,
        procedure_ids=[artifact_id] if kind == "procedure" else [],
        schema_name=schema_name,
    )
    return ArtifactStats(
        artifact_id=str(row[0]),
        kind=kind,
        lifecycle=lifecycle,
        verification=str(row[3]),
        staleness=staleness,
        support_count=int(row[4]),  # type: ignore[arg-type]
        counter_count=int(row[5]),  # type: ignore[arg-type]
        undetermined_count=int(row[6]),  # type: ignore[arg-type]
        opportunity_count=int(row[7]),  # type: ignore[arg-type]
        coverage=float(row[8]),  # type: ignore[arg-type]
        refutation_attempts=int(row[9]),  # type: ignore[arg-type]
        confidence=None if row[10] is None else float(row[10]),  # type: ignore[arg-type]
        confidence_method_id=None if row[11] is None else str(row[11]),
        expires_at=row[12] if isinstance(row[12], datetime) else None,
        closed_over_hash=None if row[13] is None else str(row[13]),
        support_ids=support_ids,
        counter_ids=counter_ids,
        verified_on=verified_on,
        held_out_split_id=split,
        negative_control_slice_id=control,
        windows_disjoint=disjoint,
        outcomes=outcomes.get(artifact_id),
        derivation_sources=derivation_sources,
        artifact_pinning_status=pinning_status,
        pinning_label=pinning_lbl,
        verified_support_ids=verified_sup,
        verified_held_out_ids=verified_held,
    )


__all__ = ["ArtifactStats", "StatsError", "read_stats"]
