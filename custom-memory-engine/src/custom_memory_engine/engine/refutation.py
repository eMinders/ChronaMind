"""Deterministic database refutation probes for inferred abstractions (``I15``, issue #245).

``refutation_attempts`` in ``abstraction_stats`` must count real falsification probes executed
against caller-readable evidence — never a caller-supplied integer. This module runs at least
three independent deterministic database probes:

1. ``contradicts`` associations (``origin IN ('grounding_policy', 'tier_c')``) touching the
   artifact or its sources;
2. contradicting ``memory_conflict`` records and single-valued slot conflicts within
   ``scope_path``;
3. withdrawn/retired ancestors (across ``source_ids`` and ``memory_derivation``) and unlifted
   ``negative_constraint`` blocks in ``scope_path``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.scope.access import ReadSet, row_visible

_CONTRADICT_ORIGINS: tuple[str, ...] = ("grounding_policy", "tier_c")
_CONFLICT_RELATIONS: frozenset[str] = frozenset({"contradicts", "contradiction", "conflict", "conflicts"})
_GROUNDED_LIFECYCLES: frozenset[str] = frozenset({"observed", "asserted"})


@dataclass(frozen=True, slots=True)
class RefutationReport:
    """Summary of executed refutation probes over caller-readable evidence."""

    attempts: int
    counter_ids: tuple[str, ...]
    negative_control_clean: bool


def _visible(read_set: ReadSet | None, scope_path: object, visibility: object) -> bool:
    if read_set is None:
        return True
    return row_visible(read_set, scope_path, visibility)


def _norm_content(value: str) -> str:
    return " ".join(value.strip().casefold().split())


async def run_refutation_probes(
    connection: AsyncConnection,
    user_id: str,
    artifact_id: str | None,
    source_ids: Sequence[str],
    *,
    scope_path: str,
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
) -> RefutationReport:
    """Execute >= 3 deterministic refutation probes over caller-readable database evidence.

    With no artifact and no source there is nothing to probe, so nothing runs and the report says so: zero attempts and
    an unclean negative control. A count is only ever the number of probes executed; reporting the three this function
    would have run is exactly the declared-not-measured count ``I15`` rules out, and the conservative pair can only
    make an admission fail (``too_few_refutation_attempts``), never pass.
    """
    targets = tuple(dict.fromkeys([*source_ids, *([artifact_id] if artifact_id is not None else [])]))
    if not targets:
        return RefutationReport(attempts=0, counter_ids=(), negative_control_clean=False)

    mv_table = sql.Identifier(schema_name, "memory_version")
    assoc_table = sql.Identifier(schema_name, "association")
    mc_table = sql.Identifier(schema_name, "memory_conflict")
    deriv_table = sql.Identifier(schema_name, "memory_derivation")
    nc_table = sql.Identifier(schema_name, "negative_constraint")
    method_table = sql.Identifier(schema_name, "method")

    target_list = list(targets)
    counter_ids: set[str] = set()
    control_dirty = False
    attempts = 0

    async with connection.cursor(row_factory=tuple_row) as cursor:
        # Probe 1: Grounded `contradicts` associations against artifact or sources.
        attempts += 1
        await cursor.execute(
            sql.SQL(
                "SELECT a.from_id, a.to_id,"
                " f.status, f.lifecycle, f.assertion_mode, f.scope_path, f.visibility,"
                " t.status, t.lifecycle, t.assertion_mode, t.scope_path, t.visibility"
                " FROM {assoc} AS a"
                " JOIN {mv} AS f ON f.user_id = a.user_id AND f.id = a.from_id"
                " JOIN {mv} AS t ON t.user_id = a.user_id AND t.id = a.to_id"
                " WHERE a.user_id = %s AND a.type = 'contradicts'"
                " AND a.origin::text = ANY(%s)"
                " AND (a.from_id = ANY(%s) OR a.to_id = ANY(%s))"
                " ORDER BY a.from_id, a.to_id, a.id"
            ).format(assoc=assoc_table, mv=mv_table),
            (user_id, list(_CONTRADICT_ORIGINS), target_list, target_list),
        )
        for row in await cursor.fetchall():
            if not (_visible(read_set, row[5], row[6]) and _visible(read_set, row[10], row[11])):
                continue
            from_id, to_id = str(row[0]), str(row[1])
            from_grounded = (
                str(row[2]) == "active"
                and str(row[3]) in _GROUNDED_LIFECYCLES
                and (row[4] is None or str(row[4]) == "asserted")
            )
            to_grounded = (
                str(row[7]) == "active"
                and str(row[8]) in _GROUNDED_LIFECYCLES
                and (row[9] is None or str(row[9]) == "asserted")
            )
            if from_id in targets and to_grounded and to_id != artifact_id:
                counter_ids.add(to_id)
            if to_id in targets and from_grounded and from_id != artifact_id:
                counter_ids.add(from_id)
            if artifact_id is not None and (
                (from_id == artifact_id and to_grounded) or (to_id == artifact_id and from_grounded)
            ):
                control_dirty = True

        # Probe 2: Contradicting `memory_conflict` rows and single-valued slot conflicts in `scope_path`.
        attempts += 1
        await cursor.execute(
            sql.SQL(
                "SELECT c.candidate_version_id, c.existing_version_id, c.relation, c.decision,"
                " c.scope_path, c.visibility,"
                " cand.status, cand.lifecycle, cand.assertion_mode, cand.scope_path, cand.visibility,"
                " ex.status, ex.lifecycle, ex.assertion_mode, ex.scope_path, ex.visibility"
                " FROM {mc} AS c"
                " JOIN {mv} AS cand ON cand.user_id = c.user_id AND cand.id = c.candidate_version_id"
                " LEFT JOIN {mv} AS ex ON ex.user_id = c.user_id AND ex.id = c.existing_version_id"
                " WHERE c.user_id = %s"
                " AND (c.candidate_version_id = ANY(%s) OR c.existing_version_id = ANY(%s))"
                " ORDER BY c.id"
            ).format(mc=mc_table, mv=mv_table),
            (user_id, target_list, target_list),
        )
        for row in await cursor.fetchall():
            if not _visible(read_set, row[4], row[5]):
                continue
            cand_id = str(row[0])
            ex_id = None if row[1] is None else str(row[1])
            relation = str(row[2])
            decision = str(row[3])
            if decision == "reject" or not (decision == "hold" or relation in _CONFLICT_RELATIONS):
                continue
            if ex_id is None or not (_visible(read_set, row[9], row[10]) and _visible(read_set, row[14], row[15])):
                continue
            cand_ok = (
                str(row[6]) in ("active", "conflict_pending")
                and str(row[7]) in ("observed", "asserted", "contested")
                and (row[8] is None or str(row[8]) == "asserted")
            )
            ex_ok = (
                str(row[11]) == "active"
                and str(row[12]) in _GROUNDED_LIFECYCLES
                and (row[13] is None or str(row[13]) == "asserted")
            )
            if cand_id in targets and ex_ok and ex_id != artifact_id:
                counter_ids.add(ex_id)
            if ex_id in targets and cand_ok and cand_id != artifact_id:
                counter_ids.add(cand_id)
            if artifact_id is not None and ((cand_id == artifact_id and ex_ok) or (ex_id == artifact_id and cand_ok)):
                control_dirty = True

        await cursor.execute(
            sql.SQL(
                "SELECT t.id, o.id, o.scope_path, o.visibility, t.content, o.content"
                " FROM {mv} AS t"
                " JOIN {mv} AS o ON o.user_id = t.user_id"
                "  AND o.scope_path = %s"
                "  AND o.subject_key = t.subject_key"
                "  AND o.predicate_key = t.predicate_key"
                "  AND o.semantic_slot IS NOT DISTINCT FROM t.semantic_slot"
                "  AND o.single_valued = true"
                "  AND o.status = 'active'"
                "  AND o.lifecycle IN ('observed', 'asserted')"
                "  AND COALESCE(o.assertion_mode, 'asserted') = 'asserted'"
                "  AND o.id <> t.id"
                " WHERE t.user_id = %s AND t.id = ANY(%s) AND t.single_valued = true"
                "  AND t.status = 'active' AND t.lifecycle IN ('observed', 'asserted')"
                "  AND COALESCE(t.assertion_mode, 'asserted') = 'asserted'"
                " ORDER BY t.id, o.id"
            ).format(mv=mv_table),
            (scope_path, user_id, target_list),
        )
        for row in await cursor.fetchall():
            if not _visible(read_set, row[2], row[3]):
                continue
            if _norm_content(str(row[4])) != _norm_content(str(row[5])):
                other_id = str(row[1])
                if other_id != artifact_id:
                    counter_ids.add(other_id)
                if artifact_id is not None and str(row[0]) == artifact_id:
                    control_dirty = True

        # Probe 3: Withdrawn/retired ancestors in derivation chain and unlifted negative constraints.
        attempts += 1
        await cursor.execute(
            sql.SQL(
                "WITH RECURSIVE anc(id, depth) AS ("
                "  SELECT id, 0 FROM {mv} WHERE user_id = %s AND id = ANY(%s)"
                "  UNION"
                "  SELECT d.source_version_id, anc.depth + 1"
                "  FROM {deriv} AS d JOIN anc ON anc.id = d.derived_version_id"
                "  WHERE d.user_id = %s AND anc.depth < 4"
                ")"
                " SELECT DISTINCT v.id, v.lifecycle, v.status, v.scope_path, v.visibility"
                " FROM anc JOIN {mv} AS v ON v.id = anc.id AND v.user_id = %s"
                " WHERE v.lifecycle = 'retired' OR v.status IN ('rejected', 'archived')"
            ).format(mv=mv_table, deriv=deriv_table),
            (user_id, target_list, user_id, user_id),
        )
        for row in await cursor.fetchall():
            if _visible(read_set, row[3], row[4]):
                control_dirty = True
                break

        if not control_dirty and artifact_id is not None:
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM {mv} AS v"
                    "  JOIN {m} AS m ON m.id = v.method_id"
                    "  JOIN {nc} AS c ON c.user_id = v.user_id"
                    "   AND c.scope_path = %s AND c.predicate_key = 'statement'"
                    "   AND c.method_family = m.name AND c.lifted_at IS NULL"
                    "  WHERE v.user_id = %s AND v.id = %s"
                    ")"
                ).format(mv=mv_table, m=method_table, nc=nc_table),
                (scope_path, user_id, artifact_id),
            )
            nc_row = await cursor.fetchone()
            if nc_row is not None and nc_row[0]:
                control_dirty = True

    return RefutationReport(
        attempts=attempts,
        counter_ids=tuple(sorted(counter_ids)),
        negative_control_clean=not control_dirty,
    )


__all__ = ["RefutationReport", "run_refutation_probes"]
