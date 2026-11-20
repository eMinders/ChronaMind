"""Synchronous, transactional re-derivation and retire-by-deletion tombstoning (RL-070, E-037; ``V2-D50``).

Two separate named paths execute Phase B's non-delete decisions so partial-failure rollback tests (F7) can target
each independently:

* :func:`rederive_summary` / :func:`rederive_version` — rebuilds an active dependent from its surviving active
  sources in-place (preserving its stable ID and, for a verified abstraction, its prior ``closed_over_hash`` so
  derived staleness automatically routes it to ``hypotheses`` until re-verified, F12).
* :func:`retire_version` — tombstones a dependent retired by source deletion (``V2-D50`` rule 3): ``id``, ``scope``,
  ``kind``, ``lifecycle='retired'``, ``retirement_reason='source_deleted'`` and the audit record stay, while
  ``content`` (and generated ``content_fts``), rendered bounds in ``abstraction_stats`` and vectors in
  ``memory_embedding`` are removed in the same transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

from custom_memory_engine.clock import ClockTick
from custom_memory_engine.consolidation.consolidator import consolidate

EMBEDDING_DIMENSION = 384


class ReDerivationError(InvalidParameterValue):
    """A re-derivation or retire-by-deletion step failed and its surrounding transaction must roll back."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActiveSource:
    """Content and identity of one surviving Active source passed to a re-derivation builder."""

    version_id: str
    content: str


@dataclass(frozen=True)
class RebuiltSummary:
    """New text and model-specific vector representation returned by a builder."""

    content: str
    embedding_model: str
    embedding: tuple[float, ...]


class SummaryBuilder(Protocol):
    """Provider boundary for rebuilding a derived artifact from surviving source content."""

    async def __call__(self, summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary: ...


def _validate_ids(user_id: str, version_id: str, schema_name: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ReDerivationError("nonblank_user_id_required")
    if not isinstance(version_id, str) or not version_id.strip():
        raise ReDerivationError("nonblank_summary_id_required")
    if not schema_name.startswith("cme_") or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None:
        raise ReDerivationError("isolated_engine_schema_identifier_required")


# Rows that reference a version and would be lost (``ON DELETE CASCADE``) or would block the rebuild (a NO ACTION
# foreign key) when the version row is deleted and recreated with the same id. Re-deriving replaces the artifact's
# content only, so these are carried across the rebuild unchanged: the dependent's own evidence, its gate decisions,
# the gate decisions that name it as the version compared against, and its associations.
_CARRIED_TABLES = ("memory_evidence", "memory_conflict", "association")


@dataclass(frozen=True)
class _CarriedRows:
    rows: tuple[tuple[str, str], ...]  # (table, row as JSON text) to re-insert after the rebuild
    gate_refs: tuple[str, ...]  # memory_conflict ids whose existing_version_id names the rebuilt version


async def _relation_exists(cursor: object, schema_name: str, table: str) -> bool:
    await cursor.execute("SELECT pg_catalog.to_regclass(%s) IS NOT NULL", (f"{schema_name}.{table}",))  # type: ignore[attr-defined]
    row = await cursor.fetchone()  # type: ignore[attr-defined]
    return bool(row and row[0])


async def rederivation_blocker(
    connection: AsyncConnection, user_id: str, version_id: str, *, schema_name: str = "cme_engine"
) -> str | None:
    """Why ``version_id`` cannot be faithfully re-derived in place, or ``None`` when it can.

    A builder returns new *text*; it cannot rebuild a procedure's shape and outcomes, and a sticky dissent
    (``negative_constraint``) that names the version cannot be re-pointed at the rebuilt row (its guard allows only the
    database to clear the reference). Such a dependent is retired instead (``V2-D50`` rule 2: no builder).
    """
    _validate_ids(user_id, version_id, schema_name)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT kind FROM {v} WHERE user_id = %s AND id = %s").format(
                v=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, version_id),
        )
        row = await cursor.fetchone()
        if row is not None and row[0] == "procedure":
            return "rederivation_unsupported_for_procedure"
        if await _relation_exists(cursor, schema_name, "negative_constraint"):
            await cursor.execute(
                sql.SQL(
                    "SELECT EXISTS (SELECT 1 FROM {n} WHERE user_id = %s "
                    "AND (dissented_version_id = %s OR lift_evidence_version_id = %s))"
                ).format(n=sql.Identifier(schema_name, "negative_constraint")),
                (user_id, version_id, version_id),
            )
            found = await cursor.fetchone()
            if found and found[0]:
                return "rederivation_blocked_by_negative_constraint"
    return None


async def _detach_carried_rows(
    connection: AsyncConnection, user_id: str, version_id: str, schema_name: str
) -> _CarriedRows:
    """Read every carried row of ``version_id`` and clear the gate references that would block deleting it."""
    rows: list[tuple[str, str]] = []
    async with connection.cursor(row_factory=tuple_row) as cursor:
        for table in _CARRIED_TABLES:
            if not await _relation_exists(cursor, schema_name, table):
                continue
            if table == "association":
                where, params = sql.SQL("(t.from_id = %s OR t.to_id = %s)"), (user_id, version_id, version_id)
            elif table == "memory_conflict":
                where, params = sql.SQL("t.candidate_version_id = %s"), (user_id, version_id)
            else:
                where, params = sql.SQL("t.version_id = %s"), (user_id, version_id)
            await cursor.execute(
                sql.SQL("SELECT pg_catalog.to_jsonb(t)::text FROM {t} AS t WHERE t.user_id = %s AND {w}").format(
                    t=sql.Identifier(schema_name, table), w=where
                ),
                params,
            )
            rows.extend((table, str(record[0])) for record in await cursor.fetchall())
        conflicts = sql.Identifier(schema_name, "memory_conflict")
        await cursor.execute(
            sql.SQL(
                "UPDATE {c} SET existing_version_id = NULL WHERE user_id = %s AND existing_version_id = %s "
                "AND candidate_version_id <> %s RETURNING id"
            ).format(c=conflicts),
            (user_id, version_id, version_id),
        )
        gate_refs = tuple(str(record[0]) for record in await cursor.fetchall())
    return _CarriedRows(tuple(rows), gate_refs)


async def _restore_carried_rows(
    connection: AsyncConnection, user_id: str, version_id: str, carried: _CarriedRows, schema_name: str
) -> None:
    """Re-insert the carried rows on the rebuilt version and point the gate references back at it."""
    async with connection.cursor() as cursor:
        for table, record in carried.rows:
            target = sql.Identifier(schema_name, table)
            await cursor.execute(
                sql.SQL("INSERT INTO {t} SELECT * FROM pg_catalog.jsonb_populate_record(NULL::{t}, %s::jsonb)").format(
                    t=target
                ),
                (record,),
            )
        if carried.gate_refs:
            await cursor.execute(
                sql.SQL("UPDATE {c} SET existing_version_id = %s WHERE user_id = %s AND id = ANY(%s)").format(
                    c=sql.Identifier(schema_name, "memory_conflict")
                ),
                (version_id, user_id, list(carried.gate_refs)),
            )


async def rederive_summary(
    connection: AsyncConnection,
    user_id: str,
    summary_id: str,
    source_ids: Sequence[str],
    *,
    builder: SummaryBuilder,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
) -> bool:
    """Replace one semantic summary in-place using only surviving Active sources.

    The existing row is deleted and recreated with the same stable ID so immutable
    memory content and its embedding are replaced together. All writes are made on
    the caller's transaction; any builder or database failure propagates and rolls
    that transaction back. Returns ``False`` without invoking the builder when no
    Active source survives.
    """
    _validate_ids(user_id, summary_id, schema_name)
    ordered_ids = tuple(dict.fromkeys(source_ids))
    if not ordered_ids:
        return False

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    embeddings = sql.Identifier(schema_name, "memory_embedding")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, content FROM {v} WHERE user_id = %s AND id = ANY(%s) AND status = 'active' ORDER BY id"
            ).format(v=versions),
            (user_id, list(ordered_ids)),
        )
        active_sources = tuple(ActiveSource(row[0], row[1]) for row in await cursor.fetchall())
        if not active_sources:
            return False
        await cursor.execute(
            sql.SQL("SELECT memory_key, single_valued, status, layer FROM {v} WHERE user_id = %s AND id = %s").format(
                v=versions
            ),
            (user_id, summary_id),
        )
        summary_row = await cursor.fetchone()
    if summary_row is None or summary_row[2] != "active" or summary_row[3] != "semantic":
        raise ReDerivationError("active_semantic_summary_required")

    try:
        rebuilt = await builder(summary_id, active_sources)
    except Exception as exc:
        raise ReDerivationError("summary_rebuild_failed") from exc
    _validate_rebuilt_summary(rebuilt)

    carried = await _detach_carried_rows(connection, user_id, summary_id, schema_name)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "DELETE FROM {d} WHERE user_id = %s AND (derived_version_id = %s OR source_version_id = %s)"
            ).format(d=derivations),
            (user_id, summary_id, summary_id),
        )
        await cursor.execute(
            sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = %s").format(v=versions), (user_id, summary_id)
        )

    await consolidate(
        connection,
        user_id,
        tuple(source.version_id for source in active_sources),
        content=rebuilt.content,
        version_id=summary_id,
        memory_key=summary_row[0],
        derivation_type="summary",
        single_valued=bool(summary_row[1]),
        schema_name=schema_name,
        clock_tick=clock_tick,
    )
    vector_literal = "[" + ",".join(repr(float(value)) for value in rebuilt.embedding) + "]"
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {e} (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, %s, %s::vector)"
            ).format(e=embeddings),
            (summary_id, rebuilt.embedding_model, user_id, vector_literal),
        )
    await _restore_carried_rows(connection, user_id, summary_id, carried, schema_name)
    return True


async def rederive_version(
    connection: AsyncConnection,
    user_id: str,
    version_id: str,
    source_ids: Sequence[str],
    *,
    builder: SummaryBuilder,
    derivation_type: str = "re_derivation",
    schema_name: str = "cme_engine",
) -> bool:
    """Re-derive an active dependent (claim, abstraction, episode) in place on its surviving sources.

    A procedure, or a version a sticky dissent names, is refused (:func:`rederivation_blocker`); the deletion retires
    it instead. Evidence, gate decisions and associations of the version are carried across the rebuild unchanged.

    Preserves the row's V2 axes (``kind``, ``lifecycle``, ``retention``, ``scope_path``, ``visibility``,
    ``method_id``, ``um_facet``) and, for an ``abstraction``, preserves its prior ``abstraction_stats`` (including the
    old ``closed_over_hash``) and ``verification`` records so derived staleness sees the changed closure and routes
    the re-derived abstraction to ``hypotheses`` until re-verified (F12).
    """
    _validate_ids(user_id, version_id, schema_name)
    ordered_ids = tuple(dict.fromkeys(source_ids))
    if not ordered_ids:
        return False

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    embeddings = sql.Identifier(schema_name, "memory_embedding")
    stats_tbl = sql.Identifier(schema_name, "abstraction_stats")
    verif_tbl = sql.Identifier(schema_name, "verification")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, content FROM {v} WHERE user_id = %s AND id = ANY(%s) AND status = 'active' ORDER BY id"
            ).format(v=versions),
            (user_id, list(ordered_ids)),
        )
        active_sources = tuple(ActiveSource(str(row[0]), str(row[1])) for row in await cursor.fetchall())
        if not active_sources:
            return False

        await cursor.execute(
            sql.SQL(
                "SELECT memory_key, subject_key, predicate_key, single_valued, event_range, layer, "
                "valid_from_basis, assertion_mode, confidence, importance, extractor_version, "
                "kind, lifecycle, retention, scope_path, visibility, method_id, um_facet, "
                "subject_entity_id, created_by, created_tier, status "
                "FROM {v} WHERE user_id = %s AND id = %s"
            ).format(v=versions),
            (user_id, version_id),
        )
        meta = await cursor.fetchone()
        if meta is None or meta[21] != "active":
            raise ReDerivationError("active_dependent_required")

    blocker = await rederivation_blocker(connection, user_id, version_id, schema_name=schema_name)
    if blocker is not None:
        raise ReDerivationError(blocker)

    async with connection.cursor(row_factory=tuple_row) as cursor:
        prior_stats = None
        prior_verifications: list[tuple[object, ...]] = []
        if meta[11] == "abstraction":
            await cursor.execute(
                sql.SQL(
                    "SELECT support_count, counter_count, undetermined_count, opportunity_count, "
                    "evidence_generation, counter_absence_basis, refutation_attempts, confidence, "
                    "confidence_method_id, closed_over_hash, verification_status, expires_at "
                    "FROM {s} WHERE user_id = %s AND version_id = %s"
                ).format(s=stats_tbl),
                (user_id, version_id),
            )
            prior_stats = await cursor.fetchone()
            await cursor.execute(
                sql.SQL(
                    "SELECT id, held_out_split_id, verified_on, negative_control_slice_id, "
                    "support_window, held_out_window, support_context_key, held_out_context_key, outcome "
                    "FROM {ver} WHERE user_id = %s AND version_id = %s ORDER BY verified_on, id"
                ).format(ver=verif_tbl),
                (user_id, version_id),
            )
            prior_verifications = list(await cursor.fetchall())

    try:
        rebuilt = await builder(version_id, active_sources)
    except Exception as exc:
        raise ReDerivationError("summary_rebuild_failed") from exc
    _validate_rebuilt_summary(rebuilt)

    (
        memory_key,
        subject_key,
        predicate_key,
        single_valued,
        event_range,
        layer,
        valid_from_basis,
        assertion_mode,
        confidence,
        importance,
        extractor_version,
        kind,
        lifecycle,
        retention,
        scope_path,
        visibility,
        method_id,
        um_facet,
        subject_entity_id,
        created_by,
        created_tier,
        _status,
    ) = meta

    edge_type = (
        derivation_type
        if derivation_type in ("consolidation", "summary", "corroboration", "re_derivation")
        else "re_derivation"
    )

    carried = await _detach_carried_rows(connection, user_id, version_id, schema_name)
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "DELETE FROM {d} WHERE user_id = %s AND (derived_version_id = %s OR source_version_id = %s)"
            ).format(d=derivations),
            (user_id, version_id, version_id),
        )
        await cursor.execute(
            sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = %s").format(v=versions), (user_id, version_id)
        )
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {v} ("
                "id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content, "
                "event_range, recorded_range, layer, valid_from_basis, assertion_mode, confidence, "
                "importance, extractor_version, kind, lifecycle, retention, scope_path, visibility, "
                "method_id, um_facet, subject_entity_id, created_by, created_tier"
                ") VALUES ("
                "%s, %s, %s, 'active', %s, %s, %s, %s, "
                "%s, tstzrange(clock_now(), NULL, '[)'), %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s"
                ")"
            ).format(v=versions),
            (
                version_id,
                memory_key,
                user_id,
                subject_key,
                predicate_key,
                single_valued,
                rebuilt.content,
                event_range,
                layer,
                valid_from_basis,
                assertion_mode,
                confidence,
                importance,
                extractor_version,
                kind,
                lifecycle,
                retention,
                scope_path,
                visibility,
                method_id,
                um_facet,
                subject_entity_id,
                created_by,
                created_tier,
            ),
        )
        for src in active_sources:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {d} (derived_version_id, source_version_id, user_id, derivation_type) "
                    "VALUES (%s, %s, %s, %s)"
                ).format(d=derivations),
                (version_id, src.version_id, user_id, edge_type),
            )
        vector_literal = "[" + ",".join(repr(float(value)) for value in rebuilt.embedding) + "]"
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {e} (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, %s, %s::vector)"
            ).format(e=embeddings),
            (version_id, rebuilt.embedding_model, user_id, vector_literal),
        )
        if kind == "abstraction" and prior_stats is not None:
            for vrow in prior_verifications:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {ver} ("
                        "id, version_id, user_id, held_out_split_id, verified_on, negative_control_slice_id, "
                        "support_window, held_out_window, support_context_key, held_out_context_key, outcome"
                        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    ).format(ver=verif_tbl),
                    (vrow[0], version_id, user_id, *vrow[1:]),
                )
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {s} (version_id, user_id) VALUES (%s, %s) ON CONFLICT (version_id) DO NOTHING"
                ).format(s=stats_tbl),
                (version_id, user_id),
            )
            await cursor.execute(
                sql.SQL(
                    "UPDATE {s} SET "
                    "support_count = %s, counter_count = %s, undetermined_count = %s, opportunity_count = %s, "
                    "evidence_generation = %s, counter_absence_basis = %s, refutation_attempts = %s, "
                    "confidence = %s, confidence_method_id = %s, closed_over_hash = %s, "
                    "verification_status = %s, expires_at = %s "
                    "WHERE user_id = %s AND version_id = %s"
                ).format(s=stats_tbl),
                (*prior_stats, user_id, version_id),
            )
    await _restore_carried_rows(connection, user_id, version_id, carried, schema_name)
    return True


async def retire_version(
    connection: AsyncConnection,
    user_id: str,
    version_id: str,
    surviving_source_ids: Sequence[str] = (),
    *,
    schema_name: str = "cme_engine",
) -> None:
    """Retire one dependent by source deletion and tombstone its content, bounds and vectors (``V2-D50`` rule 3).

    Runs inside the deletion transaction (with ``app.deletion_active = '1'``). Keeps ``id``, ``scope_path``, ``kind``,
    ``lifecycle = 'retired'`` and ``retirement_reason = 'source_deleted'``; removes ``content`` (clearing generated
    ``content_fts``), clears ``abstraction_stats`` bounds, deletes vectors (and a procedure's shape) via
    ``clear_retired_tombstone_content``,
    and retains derivation edges **only** to surviving sources so a later deletion of those sources still deletes the
    tombstone.
    """
    _validate_ids(user_id, version_id, schema_name)
    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    stats_tbl = sql.Identifier(schema_name, "abstraction_stats")
    clear_content = sql.Identifier(schema_name, "clear_retired_tombstone_content")

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT kind, lifecycle, recorded_range FROM {v} WHERE user_id = %s AND id = %s").format(
                v=versions
            ),
            (user_id, version_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ReDerivationError("retire_target_missing")
        kind, lifecycle, recorded_range = row
        if lifecycle != "retired":
            if recorded_range is not None:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {v} SET "
                        "recorded_range = CASE WHEN upper_inf(recorded_range) "
                        "  THEN tstzrange(lower(recorded_range), clock_now()) ELSE recorded_range END, "
                        "status = 'archived', archival_branch = 'S', lifecycle = 'retired', "
                        "retirement_reason = 'source_deleted', content = '' "
                        "WHERE user_id = %s AND id = %s"
                    ).format(v=versions),
                    (user_id, version_id),
                )
            else:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {v} SET "
                        "status = 'rejected', archival_branch = NULL, lifecycle = 'retired', "
                        "retirement_reason = 'source_deleted', content = '' "
                        "WHERE user_id = %s AND id = %s"
                    ).format(v=versions),
                    (user_id, version_id),
                )
        if kind == "abstraction":
            await cursor.execute(
                sql.SQL(
                    "UPDATE {s} SET "
                    "support_count = 0, counter_count = 0, undetermined_count = 0, opportunity_count = 0, "
                    "counter_absence_basis = NULL, refutation_attempts = 0, confidence = NULL, "
                    "confidence_method_id = NULL, closed_over_hash = NULL, expires_at = NULL "
                    "WHERE user_id = %s AND version_id = %s"
                ).format(s=stats_tbl),
                (user_id, version_id),
            )
        await cursor.execute(sql.SQL("SELECT {fn}(%s, %s)").format(fn=clear_content), (user_id, [version_id]))
        for source_id in dict.fromkeys(surviving_source_ids):
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {d} (derived_version_id, source_version_id, user_id, derivation_type) "
                    "VALUES (%s, %s, %s, 're_derivation') ON CONFLICT DO NOTHING"
                ).format(d=derivations),
                (version_id, source_id, user_id),
            )


def _validate_rebuilt_summary(rebuilt: RebuiltSummary) -> None:
    if not isinstance(rebuilt, RebuiltSummary):
        raise ReDerivationError("summary_builder_return_type_invalid")
    if (
        not isinstance(rebuilt.content, str)
        or not rebuilt.content.strip()
        or not isinstance(rebuilt.embedding_model, str)
        or not rebuilt.embedding_model.strip()
    ):
        raise ReDerivationError("summary_builder_output_incomplete")
    if (
        not isinstance(rebuilt.embedding, tuple)
        or len(rebuilt.embedding) != EMBEDDING_DIMENSION
        or any(
            isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value)
            for value in rebuilt.embedding
        )
    ):
        raise ReDerivationError("summary_embedding_invalid")


__all__ = [
    "ActiveSource",
    "EMBEDDING_DIMENSION",
    "ReDerivationError",
    "RebuiltSummary",
    "SummaryBuilder",
    "rederivation_blocker",
    "rederive_summary",
    "rederive_version",
    "retire_version",
]
