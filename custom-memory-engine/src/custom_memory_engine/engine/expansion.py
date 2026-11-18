"""The association stage of retrieval (E-032)."""

from collections.abc import Sequence
from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.engine.predicates import ScopedRow, TemporalQuery, compile_predicate
from custom_memory_engine.scope.access import ReadSet, read_predicate_named

# Migration 3 caps degree at 32. One hop bounds incident edges per seed at 32, versus up to 1,024
# additional inspections at two hops. This is a performance default, ahead of E-036's G3 measurement.
DEFAULT_HOP_BUDGET = 1


async def expand_associations(
    connection: AsyncConnection,
    query: TemporalQuery,
    read_set: ReadSet,
    seed_ids: Sequence[str],
    *,
    database_clock: datetime,
    hop_budget: int = DEFAULT_HOP_BUDGET,
    schema_name: str = "cme_engine",
) -> tuple[ScopedRow, ...]:
    """Return non-seed artifacts in minimum-hop order, breaking ties by artifact ID.

    Follow ``related_to``, ``contradicts`` and ``instance_of`` both ways and ``precedes`` from successor
    to predecessor. Follow the stored direction for ``refines``, ``specialises``, ``caused_by``, ``part_of``,
    ``corroborated_by`` and ``supersedes``. Edge weight does not affect reach or order.

    The hop budget bounds supplemental retrieval. At the boundary, return what was found. A budget of
    zero disables expansion. Every seed and reached artifact must pass the temporal and read predicates.
    Each hop also requires an association in the read set. Audit filters artifacts at the requested belief
    time using readable associations from the stored graph.
    """
    if isinstance(hop_budget, bool) or not isinstance(hop_budget, int) or hop_budget < 0:
        raise ValueError("nonnegative_hop_budget_required")
    if not hop_budget or not seed_ids or read_set.empty:
        return ()

    temporal = compile_predicate(query, alias="m", database_clock=database_clock)
    # Both eligibility CTEs read one table, so the same unqualified selector predicate binds to each table's
    # own scope and visibility. Inlining lets PostgreSQL combine it with the endpoint lookups.
    readable, read_parameters = read_predicate_named(read_set, "scope_path", "visibility")
    statement = sql.SQL(
        """
        WITH RECURSIVE eligible AS NOT MATERIALIZED (
            SELECT m.id
            FROM {versions} AS m
            WHERE {temporal} AND {readable}
              AND m.kind IS NOT NULL AND m.lifecycle IS NOT NULL
        ), readable_associations AS NOT MATERIALIZED (
            SELECT from_id, to_id, type
            FROM {associations}
            WHERE user_id = %(user_id)s AND {readable}
        ), reached (id, hop) AS (
            SELECT id, 0::bigint FROM eligible
            WHERE id = ANY(%(seeds)s)
              AND NULLIF(pg_catalog.current_setting('app.user_id', true), '') = %(user_id)s
            UNION
            SELECT m.id, r.hop + 1
            FROM reached AS r
            CROSS JOIN LATERAL (
                SELECT a.to_id AS id FROM readable_associations AS a
                WHERE a.from_id = r.id
                  AND a.type IN ('related_to', 'contradicts', 'instance_of', 'refines',
                                 'specialises', 'caused_by', 'part_of', 'corroborated_by', 'supersedes')
                UNION ALL
                SELECT a.from_id AS id FROM readable_associations AS a
                WHERE a.to_id = r.id
                  AND a.type IN ('related_to', 'contradicts', 'instance_of', 'precedes')
            ) AS endpoint
            JOIN eligible AS m ON m.id = endpoint.id
            WHERE r.hop < %(hop_budget)s
        ), shortest AS (
            SELECT id, min(hop) AS hop FROM reached GROUP BY id HAVING min(hop) > 0
        )
        SELECT m.id, m.kind, m.lifecycle, m.content, m.scope_path, m.visibility, s.hop
        FROM shortest AS s JOIN {versions} AS m ON m.id = s.id AND m.user_id = %(user_id)s
        ORDER BY s.hop, m.id
        """
    ).format(
        versions=sql.Identifier(schema_name, "memory_version"),
        associations=sql.Identifier(schema_name, "association"),
        temporal=temporal.statement,
        readable=readable,
    )
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            statement, {**temporal.parameters, **read_parameters, "seeds": list(seed_ids), "hop_budget": hop_budget}
        )
        return tuple(
            ScopedRow(str(id_), str(kind), str(lifecycle), str(content), str(scope), str(visibility), int(hop))
            for id_, kind, lifecycle, content, scope, visibility, hop in await cursor.fetchall()
        )
