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

logger = structlog.get_logger(__name__)

type DeletionScope = Literal["memory", "topic", "all"]
MAX_LINEAGE_DEPTH = 3


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
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
    replay_time: datetime | None = None,
) -> DeletionReceipt:
    """Delete a memory key plus its lineage closure in one transaction."""
    if scope not in ("memory", "topic", "all"):
        raise DeletionError("invalid_deletion_scope")
    if scope != "all" and (not isinstance(target_ref, str) or not target_ref.strip()):
        raise DeletionError("nonblank_target_ref_required")

    await _verify(connection, user_id)
    tick = clock_tick or await allocate_clock(connection, user_id, schema_name=schema_name, replay_time=replay_time)

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    evidence = sql.Identifier(schema_name, "memory_evidence")
    audit = sql.Identifier(schema_name, "audit_event")
    requests = sql.Identifier(schema_name, "deletion_request")
    jobs = sql.Identifier(schema_name, "job")

    async with connection.transaction():
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) + 1 FROM {r} WHERE user_id = %s").format(r=requests),
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise DeletionError("deletion_epoch_allocation_failed")
            epoch: int = row[0]
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
                        Jsonb({"scope": scope, "deletion_epoch": epoch, "deleted_versions": 0}),
                    ),
                )
                return DeletionReceipt(request_id, scope, epoch, 0, 0, 0, 0)

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
                    SELECT DISTINCT version_id FROM walk WHERE depth > 0
                    """
                ).format(v=versions, d=derivations),
                {"user_id": user_id, "seeds": seeds, "depth": MAX_LINEAGE_DEPTH},
            )
            dependents = [row[0] for row in await cursor.fetchall()]
            doomed = list(dict.fromkeys([*seeds, *dependents]))
            doomed_ids = set(doomed)

            # Sources that survive let a dependent be re-derived rather than deleted.
            surviving: dict[str, list[str]] = {}
            if dependents:
                await cursor.execute(
                    sql.SQL(
                        "SELECT derived_version_id, source_version_id FROM {d} "
                        "WHERE user_id = %s AND derived_version_id = ANY(%s)"
                    ).format(d=derivations),
                    (user_id, dependents),
                )
                for derived_id, source_id in await cursor.fetchall():
                    if source_id not in doomed_ids:
                        surviving.setdefault(derived_id, []).append(source_id)
            rederive = {version_id: sources for version_id, sources in surviving.items() if sources}
            purge = [version_id for version_id in doomed if version_id not in rederive]

            # 1. Record turns whose queued work must not be processed later.
            turn_ids: set[str] = set()
            if purge:
                await cursor.execute(
                    sql.SQL("SELECT DISTINCT turn_id FROM {e} WHERE version_id = ANY(%s) AND user_id = %s").format(
                        e=evidence
                    ),
                    (purge, user_id),
                )
                turn_ids = {row[0] for row in await cursor.fetchall() if row[0]}

            # 2. Drop lineage edges from deleted sources, then re-derive survivors.
            if purge:
                await cursor.execute(
                    sql.SQL("DELETE FROM {d} WHERE source_version_id = ANY(%s) AND user_id = %s").format(
                        d=derivations
                    ),
                    (purge, user_id),
                )
            for version_id, sources in rederive.items():
                await cursor.execute(
                    sql.SQL("DELETE FROM {d} WHERE derived_version_id = %s AND user_id = %s").format(d=derivations),
                    (version_id, user_id),
                )
                for source in sources:
                    await cursor.execute(
                        sql.SQL(
                            "INSERT INTO {d} (derived_version_id, source_version_id, user_id, derivation_type) "
                            "VALUES (%s, %s, %s, 're_derivation')"
                        ).format(d=derivations),
                        (version_id, source, user_id),
                    )

            # 3. Physical delete; cascades clear evidence, embeddings and conflicts.
            if purge:
                await cursor.execute(
                    sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = ANY(%s)").format(v=versions),
                    (user_id, purge),
                )

            # 4. Purge queued work for the deleted turns so it cannot be re-ingested.
            purged_jobs = 0
            if turn_ids:
                await cursor.execute(
                    sql.SQL("DELETE FROM {j} WHERE user_id = %s AND turn_id = ANY(%s) AND status <> 'running'").format(
                        j=jobs
                    ),
                    (user_id, sorted(turn_ids)),
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
                            "re_derived_versions": len(rederive),
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
        re_derived_versions=len(rederive),
        purged_jobs=purged_jobs,
    )
    return DeletionReceipt(
        request_id=request_id,
        scope=scope,
        deletion_epoch=epoch,
        deleted_versions=len(purge),
        dependents=len(dependents),
        rederived_dependents=len(rederive),
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
