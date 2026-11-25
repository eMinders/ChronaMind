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


async def _row(connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str) -> tuple[object, ...]:
    """The artifact and its ledger in one statement, or ``StatsError('artifact_not_found')``.

    RLS decides visibility, so an id the caller may not read is the same answer as an id that does not exist
    (``THREAT_MODEL.md`` T-11). The left join is what makes a grounded artifact — which has no ledger, no expiry and no
    verification — reportable rather than an error.
    """
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
                " WHERE v.user_id = %s AND v.id = %s"
            ).format(
                v=sql.Identifier(schema_name, "memory_version"), s=sql.Identifier(schema_name, "abstraction_stats")
            ),
            (user_id, artifact_id),
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
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT d.source_version_id FROM {d} AS d JOIN {v} AS v"
                " ON v.id = d.source_version_id AND v.user_id = d.user_id"
                " WHERE d.user_id = %s AND d.derived_version_id = %s ORDER BY d.source_version_id"
            ).format(
                d=sql.Identifier(schema_name, "memory_derivation"), v=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, artifact_id),
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def _counter_ids(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[str, ...]:
    """The versions recorded as evidence *against* this one, readable-only.

    A counter item is the other side of a ``memory_conflict`` — the belief this candidate was measured against, or
    which was measured against it. Grounded counter-evidence lives in ``memory_evidence`` with role ``counter``; both
    sources are gathered so a refutation and a contradiction are both visible, and the union is sorted so the report
    is stable.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT DISTINCT other FROM ("
                " SELECT CASE WHEN c.candidate_version_id = %s THEN c.existing_version_id"
                "             ELSE c.candidate_version_id END AS other"
                " FROM {c} AS c WHERE c.user_id = %s AND %s IN (c.candidate_version_id, c.existing_version_id)"
                " UNION ALL"
                " SELECT e.version_id FROM {e} AS e"
                " WHERE e.user_id = %s AND e.version_id = %s AND e.evidence_role = 'counter'"
                ") AS found WHERE other IS NOT NULL ORDER BY other"
            ).format(
                c=sql.Identifier(schema_name, "memory_conflict"),
                e=sql.Identifier(schema_name, "memory_evidence"),
            ),
            (artifact_id, user_id, artifact_id, user_id, artifact_id),
        )
        return tuple(str(row[0]) for row in await cursor.fetchall())


async def _verification(
    connection: AsyncConnection, user_id: str, artifact_id: str, schema_name: str
) -> tuple[datetime | None, str | None, str | None, bool | None]:
    """The newest verification record: ``(verified_on, split id, control slice id, windows disjoint)``."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT verified_on, held_out_split_id, negative_control_slice_id, windows_disjoint FROM {t}"
                " WHERE user_id = %s AND version_id = %s ORDER BY verified_on DESC, id DESC LIMIT 1"
            ).format(t=sql.Identifier(schema_name, "verification")),
            (user_id, artifact_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, None, None, None
    return (
        row[0] if isinstance(row[0], datetime) else None,
        str(row[1]),
        str(row[2]),
        bool(row[3]),
    )


async def read_stats(
    connection: AsyncConnection,
    *,
    user_id: str,
    artifact_id: str,
    cache: ClosureCache,
    schema_name: str = "cme_engine",
    tau_confidence: float = 0.6,
    tau_coverage: float = 0.5,
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
    verified_on, split, control, disjoint = await _verification(connection, user_id, artifact_id, schema_name)
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
        support_ids=await _support_ids(connection, user_id, artifact_id, schema_name),
        counter_ids=await _counter_ids(connection, user_id, artifact_id, schema_name),
        verified_on=verified_on,
        held_out_split_id=split,
        negative_control_slice_id=control,
        windows_disjoint=disjoint,
        outcomes=outcomes.get(artifact_id),
    )


__all__ = ["ArtifactStats", "StatsError", "read_stats"]
