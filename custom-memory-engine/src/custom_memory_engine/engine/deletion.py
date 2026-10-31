"""Transactional lineage deletion (RL-070, Proposal §7.7, rule R6).

One transaction, one per-user advisory lock, one deletion-epoch increment:

1. Bump ``deletion_epoch`` and record a completed ``DELETION_REQUEST``.
2. Select the target set: every ``MEMORY_VERSION`` sharing the target
   ``memory_key`` (R6, all statuses and superseded rows included) for ``memory``;
   every version whose ``memory_key`` **or** ``subject_key`` matches for ``topic``;
   every version the user owns for ``all``.
3. Walk ``MEMORY_DERIVATION`` upward (bounded depth) to find dependents.
4. A dependent that still has an independent surviving source is **re-derived**
   inside the same transaction instead of being deleted.
5. Delete the remaining set; ``ON DELETE CASCADE`` clears evidence, derivations,
   embeddings and conflicts. Queued ``JOB`` rows referencing deleted turns are
   removed so a slow-path worker cannot resurrect the content.
6. Write one content-free ``AUDIT_EVENT``: counts only, never content, hashes,
   subjects or predicates.

The 7-channel leak scanner is not here: channels such as rendered prompts,
answers and application logs are owned by the evaluation harness.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.clock import ClockTick, allocate_clock
from custom_memory_engine.deletion.closure import (
    MAX_LINEAGE_DEPTH,
    should_follow_lineage,
)
from custom_memory_engine.deletion.rederive import ReDerivationError, SummaryBuilder, rederive_summary
from custom_memory_engine.engine.transactions import AblationMode

logger = structlog.get_logger(__name__)

type DeletionScope = Literal["memory", "topic", "all"]


class DeletionError(InvalidParameterValue):
    """Static deletion contract error (SQLSTATE 22023) without private content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DeletionReceipt:
    """Content-free deletion outcome suitable for receipts and structured logs."""

    request_id: str
    scope: DeletionScope
    deletion_epoch: int
    deleted_versions: int
    dependents: int
    rederived_dependents: int
    purged_jobs: int


async def _verify(connection: AsyncConnection, user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise DeletionError("nonblank_user_id_required")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("deletion_user_context_mismatch")
        await cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (user_id,))


async def delete_memory(
    connection: AsyncConnection,
    user_id: str,
    *,
    scope: DeletionScope = "memory",
    target_ref: str = "",
    ablation: AblationMode = "P",
    deletion_epoch: int | None = None,
    summary_builder: SummaryBuilder | None = None,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> DeletionReceipt:
    """Delete a memory key in one transaction and optionally rebuild partial summaries.

    Supplying ``deletion_epoch`` makes retries idempotent: a completed request with
    the same scope, target, and epoch returns its original receipt. ``A8`` is the
    positive-control ablation and intentionally skips lineage and re-derivation.
    """
    if scope not in ("memory", "topic", "all"):
        raise DeletionError("invalid_deletion_scope")
    if scope != "all" and (not isinstance(target_ref, str) or not target_ref.strip()):
        raise DeletionError("nonblank_target_ref_required")
    follows_lineage = should_follow_lineage(ablation)
    if deletion_epoch is not None and (
        isinstance(deletion_epoch, bool) or not isinstance(deletion_epoch, int) or deletion_epoch < 1
    ):
        raise DeletionError("positive_deletion_epoch_required")

    await _verify(connection, user_id)
    tick = clock_tick or await allocate_clock(connection, user_id, schema_name=schema_name, replay_time=replay_time)

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    evidence = sql.Identifier(schema_name, "memory_evidence")
    audit = sql.Identifier(schema_name, "audit_event")
    requests = sql.Identifier(schema_name, "deletion_request")
    jobs = sql.Identifier(schema_name, "job")
    conflicts = sql.Identifier(schema_name, "memory_conflict")

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            if deletion_epoch is not None:
                await cursor.execute(
                    sql.SQL(
                        "SELECT id, scope, target_ref, status FROM {r} "
                        "WHERE user_id = %s AND deletion_epoch = %s ORDER BY requested_at DESC LIMIT 1"
                    ).format(r=requests),
                    (user_id, deletion_epoch),
                )
                previous = await cursor.fetchone()
                if previous is not None:
                    if (previous[1], previous[2]) != (scope, target_ref) or previous[3] != "completed":
                        raise DeletionError("deletion_epoch_conflict")
                    await cursor.execute(
                        sql.SQL(
                            "SELECT details FROM {a} WHERE user_id = %s AND event_type = 'deletion_completed' "
                            "AND details->>'deletion_epoch' = %s ORDER BY event_at DESC LIMIT 1"
                        ).format(a=audit),
                        (user_id, str(deletion_epoch)),
                    )
                    audit_row = await cursor.fetchone()
                    details = audit_row[0] if audit_row else None
                    if not isinstance(details, dict):
                        raise DeletionError("completed_deletion_receipt_missing")
                    return DeletionReceipt(
                        request_id=previous[0],
                        scope=scope,
                        deletion_epoch=deletion_epoch,
                        deleted_versions=int(details.get("deleted_versions", 0)),
                        dependents=int(details.get("dependents", 0)),
                        rederived_dependents=int(details.get("re_derived_versions", 0)),
                        purged_jobs=int(details.get("purged_jobs", 0)),
                    )

            await cursor.execute(
                sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) + 1 FROM {r} WHERE user_id = %s").format(r=requests),
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise DeletionError("deletion_epoch_allocation_failed")
            epoch: int = row[0]
            if deletion_epoch is not None and deletion_epoch != epoch:
                raise DeletionError("deletion_epoch_not_next")
            request_id = f"del-{uuid4().hex}"
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {r} (id, user_id, scope, target_ref, status, deletion_epoch, requested_at, "
                    "completed_at) VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s)"
                ).format(r=requests),
                (request_id, user_id, scope, target_ref, epoch, tick.instant, tick.instant),
            )

            # ``memory`` removes one logical item (all versions sharing a memory_key).
            # ``topic`` removes every belief about a subject *or* a memory key, so a
            # deletion request for "topic: diet" also reaches items keyed differently.
            # ``all`` removes every version the user owns.
            if scope == "all":
                await cursor.execute(sql.SQL("SELECT id FROM {v} WHERE user_id = %s").format(v=versions), (user_id,))
            elif scope == "topic":
                await cursor.execute(
                    sql.SQL("SELECT id FROM {v} WHERE user_id = %s AND (memory_key = %s OR subject_key = %s)").format(
                        v=versions
                    ),
                    (user_id, target_ref, target_ref),
                )
            else:
                await cursor.execute(
                    sql.SQL("SELECT id FROM {v} WHERE user_id = %s AND memory_key = %s").format(v=versions),
                    (user_id, target_ref),
                )
            seeds = [row[0] for row in await cursor.fetchall()]
            if not seeds:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                        "VALUES (%s, %s, %s, 'deletion_completed', %s, %s)"
                    ).format(a=audit),
                    (
                        f"audit-{uuid4().hex}",
                        user_id,
                        uuid4().hex,
                        tick.instant,
                        Jsonb(
                            {
                                "scope": scope,
                                "deletion_epoch": epoch,
                                "deleted_versions": 0,
                                "dependents": 0,
                                "re_derived_versions": 0,
                                "purged_jobs": 0,
                            }
                        ),
                    ),
                )
                return DeletionReceipt(request_id, scope, epoch, 0, 0, 0, 0)

            depths: dict[str, int] = {}
            if follows_lineage:
                await cursor.execute(
                    sql.SQL(
                        """
                        WITH RECURSIVE walk(version_id, depth) AS (
                          SELECT id, 0 FROM {v} WHERE user_id = %(user_id)s AND id = ANY(%(seeds)s)
                          UNION
                          SELECT d.derived_version_id, walk.depth + 1
                          FROM walk
                          JOIN {d} AS d ON d.source_version_id = walk.version_id AND d.user_id = %(user_id)s
                          WHERE walk.depth < %(depth)s
                        )
                        SELECT version_id, MIN(depth) AS depth
                        FROM walk WHERE depth > 0
                        GROUP BY version_id ORDER BY MIN(depth), version_id
                        """
                    ).format(v=versions, d=derivations),
                    {"user_id": user_id, "seeds": seeds, "depth": MAX_LINEAGE_DEPTH},
                )
                depth_rows = await cursor.fetchall()
                depths = {row[0]: int(row[1]) for row in depth_rows}
            dependents = list(depths)
            doomed = list(dict.fromkeys([*seeds, *dependents]))
            doomed_ids = set(doomed)

            edge_rows: dict[str, list[tuple[str, str, str]]] = {}
            if dependents:
                await cursor.execute(
                    sql.SQL(
                        "SELECT d.derived_version_id, d.source_version_id, d.derivation_type, "
                        "derived.status, source.status "
                        "FROM {d} AS d "
                        "JOIN {v} AS derived ON derived.id = d.derived_version_id AND derived.user_id = d.user_id "
                        "JOIN {v} AS source ON source.id = d.source_version_id AND source.user_id = d.user_id "
                        "WHERE d.user_id = %s AND d.derived_version_id = ANY(%s)"
                    ).format(d=derivations, v=versions),
                    (user_id, dependents),
                )
                for derived_id, source_id, kind, derived_status, source_status in await cursor.fetchall():
                    edge_rows.setdefault(derived_id, []).append((source_id, kind, f"{derived_status}:{source_status}"))

            surviving: dict[str, list[str]] = {}
            summary_rebuilds: set[str] = set()
            available: set[str] = set()
            for dependent_id in sorted(dependents, key=lambda item: (depths[item], item)):
                rows = edge_rows.get(dependent_id, [])
                active_sources = [
                    source_id
                    for source_id, _kind, statuses in rows
                    if statuses.startswith("active:")
                    and statuses.endswith(":active")
                    and (source_id not in doomed_ids or source_id in available)
                ]
                if not active_sources:
                    continue
                surviving[dependent_id] = list(dict.fromkeys(active_sources))
                if (
                    rows
                    and all(kind == "summary" for _source, kind, _statuses in rows)
                    and len(rows) > 1
                    and any(source_id in doomed_ids for source_id, _kind, _statuses in rows)
                ):
                    summary_rebuilds.add(dependent_id)
                available.add(dependent_id)

            purge = [version_id for version_id in doomed if version_id not in surviving]

            if summary_rebuilds and summary_builder is None:
                raise ReDerivationError("summary_builder_required")

            # Include turns linked to replaced summaries as well as physically purged rows.
            turn_ids: set[str] = set()
            evicted_ids = list(dict.fromkeys([*purge, *summary_rebuilds]))
            if evicted_ids:
                await cursor.execute(
                    sql.SQL("SELECT DISTINCT turn_id FROM {e} WHERE version_id = ANY(%s) AND user_id = %s").format(
                        e=evidence
                    ),
                    (evicted_ids, user_id),
                )
                turn_ids = {row[0] for row in await cursor.fetchall() if row[0]}

            # Remove edges before their source rows, whose foreign key is intentionally restrictive.
            edge_cleanup = list(dict.fromkeys([*purge, *surviving]))
            if edge_cleanup:
                await cursor.execute(
                    sql.SQL(
                        "DELETE FROM {d} WHERE user_id = %s AND "
                        "(source_version_id = ANY(%s) OR derived_version_id = ANY(%s))"
                    ).format(d=derivations),
                    (user_id, edge_cleanup, edge_cleanup),
                )

            # A gate decision names the version it was compared against in
            # ``existing_version_id``, which has no ON DELETE action. Clear references
            # to doomed versions first; otherwise deleting a belief that was ever
            # superseded through the gate fails with a foreign-key violation (R6).
            # Rows whose *candidate* is doomed are removed by the cascade below;
            # surviving candidates keep their content-free decision record.
            if purge:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {c} SET existing_version_id = NULL "
                        "WHERE user_id = %s AND existing_version_id = ANY(%s)"
                    ).format(c=conflicts),
                    (user_id, purge),
                )

            # Physical deletes cascade evidence, embeddings, candidate conflicts and generated FTS data.
            if purge:
                await cursor.execute(
                    sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = ANY(%s)").format(v=versions),
                    (user_id, purge),
                )

        # Rebuild summaries before restoring other survivors' edges: a rebuild recreates the
        # summary row and drops every edge that touches it, including edges *from* it to
        # memories derived from it. Restoring those edges afterwards keeps that lineage, so
        # a later deletion of the summary's remaining sources still reaches them.
        for version_id in sorted(summary_rebuilds, key=lambda item: (depths[item], item)):
            if summary_builder is None:
                raise ReDerivationError("summary_builder_required")
            await rederive_summary(
                connection,
                user_id,
                version_id,
                surviving[version_id],
                builder=summary_builder,
                schema_name=schema_name,
                clock_tick=tick,
            )

        async with connection.cursor(row_factory=tuple_row) as cursor:
            for version_id, sources in surviving.items():
                if version_id in summary_rebuilds:
                    continue
                for source in sources:
                    await cursor.execute(
                        sql.SQL(
                            "INSERT INTO {d} (derived_version_id, source_version_id, user_id, derivation_type) "
                            "VALUES (%s, %s, %s, 're_derivation')"
                        ).format(d=derivations),
                        (version_id, source, user_id),
                    )

        async with connection.cursor(row_factory=tuple_row) as cursor:
            purged_jobs = 0
            # A payload references an evicted version only when some JSON string value in it
            # equals the version id exactly. A substring match would also purge surviving
            # work that mentions e.g. ``v10`` when ``v1`` is deleted.
            if turn_ids or evicted_ids:
                await cursor.execute(
                    sql.SQL(
                        "DELETE FROM {j} WHERE user_id = %s AND status <> 'running' AND "
                        "(turn_id = ANY(%s) OR EXISTS ("
                        "SELECT 1 FROM pg_catalog.unnest(%s::text[]) AS evicted(id) "
                        "WHERE pg_catalog.jsonb_path_exists(payload, '$.** ? (@ == $id)', "
                        "pg_catalog.jsonb_build_object('id', evicted.id))))"
                    ).format(j=jobs),
                    (user_id, sorted(turn_ids), evicted_ids),
                )
                purged_jobs = max(cursor.rowcount, 0)

            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                    "VALUES (%s, %s, %s, 'deletion_completed', %s, %s)"
                ).format(a=audit),
                (
                    f"audit-{uuid4().hex}",
                    user_id,
                    uuid4().hex,
                    tick.instant,
                    Jsonb(
                        {
                            "scope": scope,
                            "deletion_epoch": epoch,
                            "deleted_versions": len(purge),
                            "dependents": len(dependents),
                            "re_derived_versions": len(summary_rebuilds),
                            "purged_jobs": purged_jobs,
                        }
                    ),
                ),
            )

    logger.info(
        "memory_deletion_completed",
        user_id=user_id,
        scope=scope,
        deletion_epoch=epoch,
        deleted_versions=len(purge),
        re_derived_versions=len(summary_rebuilds),
        purged_jobs=purged_jobs,
    )
    return DeletionReceipt(
        request_id=request_id,
        scope=scope,
        deletion_epoch=epoch,
        deleted_versions=len(purge),
        dependents=len(dependents),
        rederived_dependents=len(summary_rebuilds),
        purged_jobs=purged_jobs,
    )


async def deletion_epoch(connection: AsyncConnection, user_id: str, *, schema_name: str = "cme_engine") -> int:
    """Current per-user deletion epoch, for slow-path jobs to re-check before commit."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) FROM {r} WHERE user_id = %s").format(
                r=sql.Identifier(schema_name, "deletion_request")
            ),
            (user_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


__all__: Sequence[str] = ("DeletionError", "DeletionReceipt", "MAX_LINEAGE_DEPTH", "delete_memory", "deletion_epoch")
