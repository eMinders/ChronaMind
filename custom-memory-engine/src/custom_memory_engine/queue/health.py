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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

#: How long a due job may wait before the worker is "degraded". Well above the loop's five-second idle poll, so a
#: healthy worker never trips it, and low enough to page someone before a backlog matters.
BACKLOG_ALERT = timedelta(minutes=30)
#: How long an expired lease may survive before the worker is "unhealthy". The loop runs ``recover_stale`` on every
#: idle cycle (``worker.RECOVERY_INTERVAL``), so a live worker returns an expired lease within a minute; one that is
#: still there after this long means no loop is running.
STALE_LEASE_GRACE = timedelta(minutes=2)

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
    role: str | None = None,
    stale_grace_seconds: float | None = None,
    backlog_seconds: float | None = None,
) -> QueueHealth:
    """Measure the queue for ``users`` and return the verdict. ``connection`` must be idle; this owns its transactions.

    **One transaction per tenant, each bound to that tenant.** ``job`` forces row-level security, so a statement
    without ``app.user_id`` sees *no* rows — and an empty queue is "healthy". The first version of this probe asked
    once, unbound, and was only ever tested through a superuser DSN (which bypasses RLS), so in a real deployment it
    would have reported healthy over any backlog and any stale lease. Each tenant is now read inside its own scope —
    under ``role`` when given, exactly as the worker's own transactions run.

    A lease is *expired* when its ``next_run_at`` has passed; it is a fault only after :data:`STALE_LEASE_GRACE`,
    because a live loop returns expired leases on its own (``recover_stale`` runs every idle cycle).
    """
    if not users:
        raise ValueError("at_least_one_user_required")
    if stale_grace_seconds is None:
        stale_grace_seconds = STALE_LEASE_GRACE.total_seconds()
    if backlog_seconds is None:
        backlog_seconds = BACKLOG_ALERT.total_seconds()
    due = stale = 0
    oldest_due = oldest_lease = 0.0
    for user in users:
        async with _scope(connection, user, role), connection.cursor(row_factory=tuple_row) as cursor:
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
                    " FROM {t} WHERE user_id = %s"
                ).format(t=sql.Identifier(schema_name, "job")),
                (user,),
            )
            row = await cursor.fetchone()
        if row is None:
            continue
        due += int(row[0])
        oldest_due = max(oldest_due, float(row[1]))
        stale += int(row[2])
        oldest_lease = max(oldest_lease, float(row[3]))
    # `next_run_at` of a running job is its lease *expiry*, so `oldest_lease` is how long ago the oldest one expired.
    if stale and oldest_lease > stale_grace_seconds:
        verdict: Verdict = "unhealthy"
    elif oldest_due > backlog_seconds:
        verdict = "degraded"
    else:
        verdict = "healthy"
    return QueueHealth(verdict, due, stale, oldest_due, oldest_lease)


@asynccontextmanager
async def _scope(connection: AsyncConnection, user: str, role: str | None) -> AsyncIterator[None]:
    if role is None:
        async with connection.transaction():
            await connection.execute("SELECT pg_catalog.set_config('app.user_id', %s, true)", (user,))
            yield
        return
    from custom_memory_engine.security.scoping import tenant_scope

    async with tenant_scope(connection, user, role=role):
        yield


__all__ = ["STALE_LEASE_GRACE", "BACKLOG_ALERT", "EXIT_CODES", "QueueHealth", "Verdict", "queue_health"]
