"""Queue health for the slow-lane worker (``V2-D103``).

The *rule* lives here — engine code, no model imports, no HTTP — and `scripts/worker_healthcheck.py` is the CLI a
container or a systemd unit runs. That split is not cosmetic: `tests/` may not import `scripts/` (a boundary rule
with a test behind it), and a health rule nobody can test against a real queue is a rule nobody has verified.

What "healthy" means, and why these three checks rather than a PID:

1. **the database is reachable** — otherwise the loop cannot claim anything;
2. **no job has been ``running`` past its lease** — the one state the two-transaction lane introduces
   (:data:`~custom_memory_engine.semantic.slow_lane.LEASE`); a worker that died holding a lease leaves exactly this
   trace, and :func:`~custom_memory_engine.semantic.slow_lane.recover_stale` is what returns it, so a persistent stale
   lease means the loop is not running;
3. **the oldest due job is not older than the alert window** — a growing backlog behind a live process is a *degraded*
   worker, not a healthy one.

A probe that only checked "is there a process" would pass while the worker was stuck behind a hung model call, which is
the failure this lane is most likely to have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

#: How long a due job may wait before the worker is "degraded". Well above the loop's five-second idle poll, so a
#: healthy worker never trips it, and low enough to page someone before a backlog matters.
BACKLOG_ALERT = timedelta(minutes=30)

type Verdict = Literal["healthy", "degraded", "unhealthy"]
#: The exit code each verdict maps to, so a container `HEALTHCHECK` and a systemd check agree without translation.
EXIT_CODES: dict[str, int] = {"healthy": 0, "degraded": 1, "unhealthy": 2}


@dataclass(frozen=True, slots=True)
class QueueHealth:
    """One probe's answer. Counts and an age only: no id, no user, no content."""

    verdict: Verdict
    due: int = 0
    stale_leases: int = 0
    oldest_due_seconds: float = 0.0
    oldest_lease_seconds: float = 0.0

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]

    def describe(self) -> str:
        """A one-line, content-free summary for a log or a `HEALTHCHECK` message."""
        return (
            f"{self.verdict}: {self.due} due (oldest {self.oldest_due_seconds:.0f}s), "
            f"{self.stale_leases} stale lease(s) (oldest {self.oldest_lease_seconds:.0f}s)"
        )


async def queue_health(
    connection: AsyncConnection,
    *,
    users: tuple[str, ...],
    schema_name: str = "cme_engine",
    lease_seconds: float | None = None,
    backlog_seconds: float | None = None,
) -> QueueHealth:
    """Measure the queue for ``users`` and return the verdict. The caller owns the transaction and the role.

    ``lease_seconds`` defaults to the slow lane's own lease, imported lazily so this module stays importable without
    the semantic layer.
    """
    if not users:
        raise ValueError("at_least_one_user_required")
    if lease_seconds is None:
        from custom_memory_engine.semantic.slow_lane import LEASE

        lease_seconds = LEASE.total_seconds()
    if backlog_seconds is None:
        backlog_seconds = BACKLOG_ALERT.total_seconds()
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT count(*) FILTER (WHERE status = 'queued'"
                " AND next_run_at <= pg_catalog.transaction_timestamp()),"
                " COALESCE(EXTRACT(EPOCH FROM (pg_catalog.transaction_timestamp()"
                " - min(next_run_at) FILTER (WHERE status = 'queued'"
                " AND next_run_at <= pg_catalog.transaction_timestamp()))), 0)::float,"
                " count(*) FILTER (WHERE status = 'running'"
                " AND next_run_at < pg_catalog.transaction_timestamp()),"
                " COALESCE(EXTRACT(EPOCH FROM (pg_catalog.transaction_timestamp()"
                " - min(next_run_at) FILTER (WHERE status = 'running'"
                " AND next_run_at < pg_catalog.transaction_timestamp()))), 0)::float"
                " FROM {t} WHERE user_id = ANY(%s)"
            ).format(t=sql.Identifier(schema_name, "job")),
            (list(users),),
        )
        row = await cursor.fetchone()
    due = int(row[0]) if row else 0
    oldest_due = float(row[1]) if row else 0.0
    stale = int(row[2]) if row else 0
    oldest_lease = float(row[3]) if row else 0.0
    if stale and oldest_lease > lease_seconds:
        verdict: Verdict = "unhealthy"
    elif oldest_due > backlog_seconds:
        verdict = "degraded"
    else:
        verdict = "healthy"
    return QueueHealth(verdict, due, stale, oldest_due, oldest_lease)


__all__ = ["BACKLOG_ALERT", "EXIT_CODES", "QueueHealth", "Verdict", "queue_health"]
