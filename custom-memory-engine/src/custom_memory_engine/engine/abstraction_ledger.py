"""Derived abstraction ledger from caller-readable database evidence (``I15``, issue #245).

Caller-supplied ledger counts on ``infer`` can overstate support or hide counter-evidence.
This module computes ``(support, counter, undetermined, opportunity)`` directly from
caller-readable rows in PostgreSQL so every inferred abstraction carries an evidence-backed
ledger rather than an unverified claim.

Classification is purely deterministic over readable ``memory_version`` sources and active
grounded contradictions:

* ``support``: active, non-retired, ``observed`` or ``asserted`` source with
  ``assertion_mode = 'asserted'`` and no active grounded contradiction;
* ``counter``: active grounded source that faces an active grounded contradiction from a
  ``contradicts`` association (``origin IN ('grounding_policy', 'tier_c')``), a
  contradicting ``memory_conflict`` record, or a single-valued slot conflict in
  ``scope_path``;
* ``undetermined``: everything else (``contested``, ``inferred``, ``retired``, non-active
  status, or hedged/hypothetical/planned/quoted/third-party/joking assertion modes).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.scope.access import ReadSet, row_visible
from custom_memory_engine.soundness.admission import AdmissionError, cp_upper

_CONTRADICT_ORIGINS: tuple[str, ...] = ("grounding_policy", "tier_c")
_CONFLICT_RELATIONS: frozenset[str] = frozenset({"contradicts", "contradiction", "conflict", "conflicts"})
_GROUNDED_LIFECYCLES: frozenset[str] = frozenset({"observed", "asserted"})


class LedgerError(ValueError):
    """Static refusal code for an empty, unsupported, or contradictory derivation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DerivedLedger:
    """Deterministic opportunity ledger and sorted readable source identifiers."""

    support: int
    counter: int
    undetermined: int
    opportunity: int
    support_ids: tuple[str, ...]
    counter_ids: tuple[str, ...]
    undetermined_ids: tuple[str, ...]
    derivation_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _VersionRow:
    version_id: str
    status: str
    lifecycle: str
    assertion_mode: str | None
    scope_path: str
    visibility: str
    subject_key: str
    predicate_key: str
    semantic_slot: str | None
    single_valued: bool
    content: str

    def is_grounded(self) -> bool:
        return (
            self.status == "active"
            and self.lifecycle in _GROUNDED_LIFECYCLES
            and self.assertion_mode in ("asserted", None)
        )


def _visible(read_set: ReadSet | None, scope_path: object, visibility: object) -> bool:
    if read_set is None:
        return True
    return row_visible(read_set, scope_path, visibility)


def _norm_content(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def ledger_confidence_lower(support: int, counter: int, undetermined: int) -> float:
    """Exact partially identified lower confidence bound matching ``soundness.admission``."""
    for name, value in (("support", support), ("counter", counter), ("undetermined", undetermined)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LedgerError(f"{name}_must_be_a_nonnegative_integer")
    opportunity = support + counter + undetermined
    if opportunity == 0:
        raise LedgerError("insufficient_evidence")
    try:
        upper = cp_upper(counter + undetermined, opportunity)
    except AdmissionError as exc:
        raise LedgerError(exc.code) from None
    return 1.0 - upper


def _validate_inputs(user_id: str, source_ids: Sequence[str], statement: str, scope_path: str) -> tuple[str, ...]:
    if not isinstance(user_id, str) or not user_id.strip() or "\x00" in user_id:
        raise LedgerError("user_id_required")
    if not isinstance(statement, str) or not statement.strip() or "\x00" in statement:
        raise LedgerError("statement_required")
    if not isinstance(scope_path, str) or not scope_path.strip() or "\x00" in scope_path:
        raise LedgerError("scope_path_required")
    if isinstance(source_ids, str | bytes) or not isinstance(source_ids, Sequence):
        raise LedgerError("sources_invalid")
    cleaned: list[str] = []
    for item in source_ids:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise LedgerError("sources_invalid")
        cleaned.append(item)
    return tuple(sorted(dict.fromkeys(cleaned)))


async def derive_ledger(
    connection: AsyncConnection,
    user_id: str,
    source_ids: Sequence[str],
    *,
    statement: str,
    scope_path: str,
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
) -> DerivedLedger:
    """Derive ``(s, k, u, o)`` deterministically from caller-readable database evidence."""
    unique_sources = _validate_inputs(user_id, source_ids, statement, scope_path)
    if not unique_sources:
        raise LedgerError("insufficient_evidence")

    mv_table = sql.Identifier(schema_name, "memory_version")
    assoc_table = sql.Identifier(schema_name, "association")
    mc_table = sql.Identifier(schema_name, "memory_conflict")

    readable_sources: dict[str, _VersionRow] = {}
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, status, lifecycle, assertion_mode, scope_path, visibility,"
                " subject_key, predicate_key, semantic_slot, single_valued, content"
                " FROM {mv} WHERE user_id = %s AND id = ANY(%s) ORDER BY id"
            ).format(mv=mv_table),
            (user_id, list(unique_sources)),
        )
        for row in await cursor.fetchall():
            if not _visible(read_set, row[4], row[5]):
                continue
            item = _VersionRow(
                version_id=str(row[0]),
                status=str(row[1]),
                lifecycle=str(row[2]),
                assertion_mode=None if row[3] is None else str(row[3]),
                scope_path=str(row[4]),
                visibility=str(row[5]),
                subject_key=str(row[6]),
                predicate_key=str(row[7]),
                semantic_slot=None if row[8] is None else str(row[8]),
                single_valued=bool(row[9]),
                content=str(row[10]),
            )
            readable_sources[item.version_id] = item

    if not readable_sources:
        raise LedgerError("insufficient_evidence")

    readable_ids = list(readable_sources)
    contradicted_by: dict[str, set[str]] = {}
    internal_contradiction = False

    def _record_counter(source_id: str, counterpart_id: str) -> None:
        contradicted_by.setdefault(source_id, set()).add(counterpart_id)

    async with connection.cursor(row_factory=tuple_row) as cursor:
        # 1. Grounded `contradicts` associations involving any readable source.
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
            (user_id, list(_CONTRADICT_ORIGINS), readable_ids, readable_ids),
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
            if from_id in readable_sources and to_grounded:
                _record_counter(from_id, to_id)
                if to_id in readable_sources and readable_sources[from_id].is_grounded():
                    internal_contradiction = True
            if to_id in readable_sources and from_grounded:
                _record_counter(to_id, from_id)
                if from_id in readable_sources and readable_sources[to_id].is_grounded():
                    internal_contradiction = True

        # 2. Active contradicting `memory_conflict` rows touching any readable source.
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
            (user_id, readable_ids, readable_ids),
        )
        for row in await cursor.fetchall():
            if not _visible(read_set, row[4], row[5]):
                continue
            cand_id = str(row[0])
            ex_id = None if row[1] is None else str(row[1])
            relation = str(row[2])
            decision = str(row[3])
            if decision == "reject":
                continue
            is_contradicting = decision == "hold" or relation in _CONFLICT_RELATIONS
            if not is_contradicting:
                continue
            cand_visible = _visible(read_set, row[9], row[10])
            ex_visible = ex_id is not None and _visible(read_set, row[14], row[15])
            if not (cand_visible and ex_visible and ex_id is not None):
                continue
            cand_active_or_held = (
                str(row[6]) in ("active", "conflict_pending")
                and str(row[7]) in ("observed", "asserted", "contested")
                and (row[8] is None or str(row[8]) == "asserted")
            )
            ex_active = (
                str(row[11]) == "active"
                and str(row[12]) in _GROUNDED_LIFECYCLES
                and (row[13] is None or str(row[13]) == "asserted")
            )
            if cand_id in readable_sources and ex_active:
                _record_counter(cand_id, ex_id)
                if ex_id in readable_sources and readable_sources[cand_id].is_grounded():
                    internal_contradiction = True
            if ex_id in readable_sources and cand_active_or_held:
                _record_counter(ex_id, cand_id)
                if cand_id in readable_sources and readable_sources[ex_id].is_grounded():
                    internal_contradiction = True

        # 3. Single-valued slot conflicts in `scope_path`: same `(subject_key, predicate_key, semantic_slot)`.
        for src_id, src in readable_sources.items():
            if not (src.is_grounded() and src.single_valued):
                continue
            await cursor.execute(
                sql.SQL(
                    "SELECT id, status, lifecycle, assertion_mode, scope_path, visibility, content"
                    " FROM {mv}"
                    " WHERE user_id = %s AND scope_path = %s"
                    " AND subject_key = %s AND predicate_key = %s"
                    " AND semantic_slot IS NOT DISTINCT FROM %s"
                    " AND single_valued = true AND id <> %s"
                    " ORDER BY id"
                ).format(mv=mv_table),
                (user_id, scope_path, src.subject_key, src.predicate_key, src.semantic_slot, src_id),
            )
            for row in await cursor.fetchall():
                if not _visible(read_set, row[4], row[5]):
                    continue
                other_grounded = (
                    str(row[1]) == "active"
                    and str(row[2]) in _GROUNDED_LIFECYCLES
                    and (row[3] is None or str(row[3]) == "asserted")
                )
                if not other_grounded:
                    continue
                if _norm_content(str(row[6])) != _norm_content(src.content):
                    other_id = str(row[0])
                    _record_counter(src_id, other_id)
                    if other_id in readable_sources:
                        internal_contradiction = True

    support_ids: list[str] = []
    counter_ids: list[str] = []
    undetermined_ids: list[str] = []
    all_derivation_sources: set[str] = set(readable_sources)

    for src_id in sorted(readable_sources):
        row = readable_sources[src_id]
        if not row.is_grounded():
            undetermined_ids.append(src_id)
        elif src_id in contradicted_by:
            counter_ids.append(src_id)
            all_derivation_sources.update(contradicted_by[src_id])
        else:
            support_ids.append(src_id)

    support = len(support_ids)
    counter = len(counter_ids)
    undetermined = len(undetermined_ids)
    opportunity = support + counter + undetermined

    if opportunity == 0:
        raise LedgerError("insufficient_evidence")
    if internal_contradiction or (support == 0 and counter > 0):
        raise LedgerError("contradictory_derivation")
    if support == 0:
        raise LedgerError("no_supporting_evidence")

    return DerivedLedger(
        support=support,
        counter=counter,
        undetermined=undetermined,
        opportunity=opportunity,
        support_ids=tuple(support_ids),
        counter_ids=tuple(counter_ids),
        undetermined_ids=tuple(undetermined_ids),
        derivation_sources=tuple(sorted(all_derivation_sources)),
    )


__all__ = ["DerivedLedger", "LedgerError", "derive_ledger", "ledger_confidence_lower"]
