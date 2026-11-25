"""Index maintenance: ``rebuild_index``'s plan, its worker, and its two execution shapes (``A-014``).

``API_V2.md`` §4.7 gives ``rebuild_index(ctx, index: fts|vector|closure_hash) -> JobId``. Nothing implemented it, so an
operator whose full-text or vector index had bloated had no supported way to rebuild it — and ``closure_hash`` is not
even a catalog object: it is a *derived column* (``abstraction_stats.closed_over_hash``) that a ``REINDEX`` cannot
touch, because it is computed by Python over the lineage graph.

Three logical indexes, two execution shapes, and the difference is not cosmetic:

* ``fts`` — the GIN indexes on ``content_fts`` and ``content_fts_simple``. ``REINDEX ... CONCURRENTLY``, therefore
  **outside** any transaction.
* ``vector`` — the HNSW graph of every embedding slot (``vector(384)`` and the four ``halfvec`` slots). Same shape.
* ``closure_hash`` — no catalog object at all: recompute ``abstraction_stats.closed_over_hash`` for the tenant, row by
  row, **inside** the job's own transaction.

**Why a job and not an inline call.** ``rebuild_index`` is admin-only and returns a ``JobId``, which is what makes a
slow maintenance operation safe to start from a request: the request answers immediately, the work happens on the
worker, and a failure is retried like any other job rather than surfacing as a 500. The queue already carries a
deletion epoch, so a rebuild prepared before a deletion cannot run after it (``F5``).

**Why ``CONCURRENTLY`` matters.** A plain ``REINDEX`` takes an ``ACCESS EXCLUSIVE`` lock on the index for its whole
duration: every read of the corpus blocks behind it. That is acceptable in a test and unacceptable in a deployment,
which is exactly the kind of difference that only shows up in production.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from psycopg import AsyncConnection, pq, sql
from psycopg.rows import tuple_row
from pydantic import JsonValue

from custom_memory_engine.engine.staleness import ClosureCache
from custom_memory_engine.queue.contracts import Job, NewJob

type LogicalIndex = Literal["fts", "vector", "closure_hash"]

#: The three logical names ``API_V2.md`` publishes, and nothing else.
LOGICAL_INDEXES: Final[frozenset[str]] = frozenset({"fts", "vector", "closure_hash"})

#: Physical index names per logical index. Read from the migrations, not guessed: `001` created the English GIN index,
#: `023` the language-agnostic one and the four HNSW graphs. A missing index is skipped rather than failing the job, so
#: a deployment that never enabled a slot does not dead-letter its rebuild.
PHYSICAL_INDEXES: Final[dict[str, tuple[str, ...]]] = {
    "fts": ("memory_version_content_fts_idx", "memory_version_content_fts_simple_idx"),
    "vector": (
        "memory_embedding_1024_hnsw_idx",
        "memory_embedding_768_hnsw_idx",
        "memory_embedding_1536_hnsw_idx",
        "memory_embedding_3072_hnsw_idx",
    ),
}

#: The job kind the worker dispatches on.
JOB_KIND = "index_rebuild"
#: How many abstractions one ``closure_hash`` job reseals. A bound keeps one job from holding a transaction open over
#: the whole corpus; a tenant with more gets several jobs rather than one unbounded one.
RESEAL_BATCH = 500


class MaintenanceError(RuntimeError):
    """A static code. Never a schema name, an index name or a row id."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def logical_index(value: object) -> str:
    """Validate the request's index name. A static code on failure: the caller's problem, so the boundary maps it."""
    if not isinstance(value, str) or value not in LOGICAL_INDEXES:
        raise MaintenanceError("index_unknown")
    return value


def job_for(user_id: str, index: str, *, job_id: str, key: str, deletion_epoch: int | None = None) -> NewJob:
    """The queued work for one rebuild. ``deletion_epoch`` rides in the payload, where the queue reads it (``F5``)."""
    payload: dict[str, JsonValue] = {"index": logical_index(index)}
    if deletion_epoch is not None:
        payload["deletion_epoch"] = deletion_epoch
    return NewJob(id=job_id, user_id=user_id, kind=JOB_KIND, idempotency_key=key, payload=payload)


@dataclass(frozen=True, slots=True)
class RebuildOutcome:
    """What the worker did, for the log and for the test. Counts and names only: an index name is not content."""

    index: str
    rebuilt: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    resealed: int = 0


async def reindex_concurrently(connection: AsyncConnection, index: str, *, schema_name: str) -> RebuildOutcome:
    """``REINDEX INDEX CONCURRENTLY`` every physical index of ``index``.

    The connection must be **idle and autocommit**: PostgreSQL refuses ``REINDEX ... CONCURRENTLY`` inside a
    transaction
    block, and the pool's autocommit connection is the only shape that satisfies that. It is checked rather than
    assumed,
    because a caller that passes a transaction would otherwise get a driver error with a SQLSTATE rather than a static
    code that says what is wrong.
    """
    logical_index(index)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise MaintenanceError("maintenance_requires_idle_connection")
    rebuilt: list[str] = []
    absent: list[str] = []
    for name in PHYSICAL_INDEXES.get(index, ()):
        qualified = sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(name))
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n"
                " ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'i')",
                (schema_name, name),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            absent.append(name)
            continue
        await connection.execute(sql.SQL("REINDEX INDEX CONCURRENTLY {}").format(qualified))
        rebuilt.append(name)
    return RebuildOutcome(index=index, rebuilt=tuple(rebuilt), absent=tuple(absent))


async def reseal_closure_hashes(
    connection: AsyncConnection,
    user_id: str,
    *,
    schema_name: str = "cme_engine",
    cache: ClosureCache | None = None,
    limit: int = RESEAL_BATCH,
) -> int:
    """Recompute ``abstraction_stats.closed_over_hash`` for this tenant's abstractions; returns how many changed.

    ``closure_hash`` is not a catalog object, so this is what "rebuild that index" means for it. It runs **inside** the
    job's transaction, which is the opposite of the two ``REINDEX`` shapes and is deliberately the same kind of
    work the
    rest of the engine does: recompute from the lineage graph and write the sealed digest.

    Only rows whose digest differs are updated (the statement's ``IS DISTINCT FROM``), so a second run over an
    unchanged
    corpus writes nothing and the receipt is honest about it.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise MaintenanceError("nonblank_user_id_required")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise MaintenanceError("positive_limit_required")
    state = cache or ClosureCache()
    versions = sql.Identifier(schema_name, "memory_version")
    stats = sql.Identifier(schema_name, "abstraction_stats")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT v.id FROM {v} AS v JOIN {s} AS s ON s.version_id = v.id AND s.user_id = v.user_id"
                " WHERE v.user_id = %s AND v.kind = 'abstraction' ORDER BY v.id LIMIT %s"
            ).format(v=versions, s=stats),
            (user_id, limit),
        )
        ids = [str(row[0]) for row in await cursor.fetchall()]
    changed = 0
    for artifact_id in ids:
        closure = await state.closure_of(connection, user_id, artifact_id, schema_name=schema_name)
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {s} SET closed_over_hash = %s WHERE user_id = %s AND version_id = %s"
                    " AND closed_over_hash IS DISTINCT FROM %s RETURNING version_id"
                ).format(s=stats),
                (closure.digest, user_id, artifact_id, closure.digest),
            )
            if await cursor.fetchone() is not None:
                changed += 1
    return changed


async def perform_inline(connection: AsyncConnection, job: Job, *, schema_name: str) -> int:
    """The ``closure_hash`` half, run inside the worker's transaction. Returns the number of rows resealed."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    index = logical_index(payload.get("index"))
    if index != "closure_hash":
        raise MaintenanceError("index_requires_its_own_connection")
    return await reseal_closure_hashes(connection, job.user_id, schema_name=schema_name)


def name_is_safe(name: str) -> bool:
    """A physical index name is interpolated into DDL, so it is checked to be an identifier before it is used."""
    return re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None


__all__ = [
    "JOB_KIND",
    "LOGICAL_INDEXES",
    "PHYSICAL_INDEXES",
    "RESEAL_BATCH",
    "LogicalIndex",
    "MaintenanceError",
    "RebuildOutcome",
    "job_for",
    "logical_index",
    "name_is_safe",
    "perform_inline",
    "reindex_concurrently",
    "reseal_closure_hashes",
]
