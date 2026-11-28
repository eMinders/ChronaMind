"""Scheduled per-tenant maintenance orchestration (``#255``, ``A-014``, ``V2-D119``).

Periodic maintenance executes ``expire`` and ``purge_working`` across configured tenants under ``CME_RUNTIME_ROLE``
and enqueues ``rebuild_index`` work for ``cme-worker`` to drain (``closure_hash`` per tenant, ``fts``/``vector`` once
per cycle since physical indexes are schema-wide). All operations execute through :meth:`Engine.call` so conditions,
row-level security, and content-free audit events are preserved identically to interactive admin calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import structlog
from psycopg import AsyncConnection, pq, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.engine.expiry import ExpiryReceipt
from custom_memory_engine.engine.index_maintenance import LOGICAL_INDEXES, LogicalIndex, logical_index
from custom_memory_engine.engine.purge import PurgeReceipt
from custom_memory_engine.model.axes import FACETS
from custom_memory_engine.profile import (
    ExpiryPolicy,
    Profile,
    RenderConventions,
    RetentionPolicy,
    load_profile,
)
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.paths import user_scope
from custom_memory_engine.security.scoping import tenant_scope

logger = structlog.get_logger(__name__)

#: Default per-facet abstraction expiry windows from ``DECISIONS_V2.md`` ``V2-D14``.
V2_D14_EXPIRY_DAYS: dict[str, int] = {
    "preference": 180,
    "goal": 90,
    "constraint": 365,
    "context": 14,
    "decision_pattern": 365,
    "meta_preference": 180,
}

DEFAULT_INDEXES: tuple[LogicalIndex, ...] = ("fts", "vector", "closure_hash")


def default_maintenance_profile() -> Profile:
    """Generic non-forked maintenance profile carrying the ``V2-D14`` expiry policy."""
    return Profile(
        name="maintenance-default",
        description="Default maintenance profile carrying V2-D14 abstraction expiry windows.",
        kinds=("episode", "claim", "procedure", "abstraction"),
        operators=("consolidation",),
        retention=RetentionPolicy(
            defaults={
                "episode": "working",
                "claim": "durable",
                "procedure": "session",
                "abstraction": "durable",
            },
            promote_to_durable=False,
            status="decided",
        ),
        scope_root="/u/{user}/",
        render=RenderConventions(
            channel_budget_share={"facts": 0.5, "hypotheses": 0.2, "procedures": 0.2, "conflicts": 0.1},
        ),
        facets=FACETS,
        abstraction_expiry=ExpiryPolicy(days_by_facet=dict(V2_D14_EXPIRY_DAYS), status="decided"),
    )


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    """Configuration for scheduled maintenance cycles."""

    role: str
    users: tuple[str, ...]
    schema: str = "cme_engine"
    profile: Profile | None = None
    indexes: tuple[LogicalIndex, ...] = DEFAULT_INDEXES
    interval_seconds: float = 86400.0
    run_expire: bool = True
    run_purge_working: bool = True
    run_rebuild_index: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("cme_runtime_role_required")
        if not self.users or any(not isinstance(u, str) or not u.strip() for u in self.users):
            raise ValueError("cme_worker_users_required")
        if not isinstance(self.schema, str) or not self.schema.strip():
            raise ValueError("cme_schema_invalid")
        if (
            isinstance(self.interval_seconds, bool)
            or not isinstance(self.interval_seconds, int | float)
            or not isfinite(self.interval_seconds)
            or self.interval_seconds <= 0.0
        ):
            raise ValueError("cme_maintenance_interval_seconds_invalid")
        for index in self.indexes:
            if index not in LOGICAL_INDEXES:
                raise ValueError("cme_maintenance_indexes_invalid")


@dataclass(frozen=True, slots=True)
class TenantMaintenanceResult:
    """Content-free maintenance outcome for one tenant."""

    user_id: str
    expired: int = 0
    considered: int = 0
    purged_episodes: int = 0
    retained_as_evidence: int = 0
    rebuild_jobs: tuple[str, ...] = ()
    dry_run: bool = False
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """Aggregated content-free report across all configured tenants."""

    tenants: tuple[TenantMaintenanceResult, ...]
    dry_run: bool

    @property
    def failed_tenants(self) -> int:
        return sum(1 for item in self.tenants if item.error_type is not None)

    @property
    def total_expired(self) -> int:
        return sum(item.expired for item in self.tenants)

    @property
    def total_purged(self) -> int:
        return sum(item.purged_episodes for item in self.tenants)

    @property
    def total_jobs_enqueued(self) -> int:
        return sum(len(item.rebuild_jobs) for item in self.tenants)


def settings_from_environment(environment: Mapping[str, str]) -> MaintenanceSettings:
    """Parse maintenance configuration from ``CME_*`` variables. Failures name only the variable."""

    def required(name: str) -> str:
        value = environment.get(name, "").strip()
        if not value:
            raise ValueError(f"{name.lower()}_required")
        return value

    users = tuple(item.strip() for item in environment.get("CME_WORKER_USERS", "").split(",") if item.strip())
    if not users:
        raise ValueError("cme_worker_users_required")

    raw_interval = environment.get("CME_MAINTENANCE_INTERVAL_SECONDS", "86400").strip() or "86400"
    try:
        interval_seconds = float(raw_interval)
    except ValueError:
        raise ValueError("cme_maintenance_interval_seconds_invalid") from None

    raw_indexes = environment.get("CME_MAINTENANCE_INDEXES", "").strip()
    if raw_indexes:
        parsed_indexes: list[LogicalIndex] = []
        for token in raw_indexes.split(","):
            cleaned = token.strip()
            if not cleaned:
                continue
            try:
                logical_index(cleaned)
            except Exception:
                raise ValueError("cme_maintenance_indexes_invalid") from None
            parsed_indexes.append(cleaned)  # type: ignore[arg-type]
        indexes = tuple(dict.fromkeys(parsed_indexes))
    else:
        indexes = DEFAULT_INDEXES

    profile_path = environment.get("CME_MAINTENANCE_PROFILE", "").strip()
    profile = load_profile(Path(profile_path)) if profile_path else None

    return MaintenanceSettings(
        role=required("CME_RUNTIME_ROLE"),
        users=users,
        schema=environment.get("CME_SCHEMA", "cme_engine").strip() or "cme_engine",
        profile=profile,
        indexes=indexes,
        interval_seconds=interval_seconds,
    )


def _admin_context(user: str, scope: str) -> CallerContext:
    return CallerContext.from_credential(
        Credential(user, "C", admin=True),
        write_scope=scope,
        read_set=resolve_read_set(user, [Selector.of(scope)], ()),
    )


async def _dry_run_counts(
    connection: AsyncConnection,
    user: str,
    scope: str,
    *,
    schema_name: str,
    role: str,
) -> tuple[int, int]:
    """Read-only candidate counts under ``tenant_scope`` (no mutations, no audit writes)."""
    versions = sql.Identifier(schema_name, "memory_version")
    async with tenant_scope(connection, user, role=role):
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT count(*) FILTER (WHERE kind = 'abstraction' AND lifecycle = 'inferred'),"
                    " count(*) FILTER (WHERE kind = 'episode' AND retention = 'working')"
                    " FROM {v} WHERE user_id = %s AND pg_catalog.starts_with(scope_path, %s)"
                ).format(v=versions),
                (user, scope),
            )
            row = await cursor.fetchone()
    if row is None:
        return 0, 0
    return int(row[0]), int(row[1])


def _indexes_for_tenant(indexes: tuple[LogicalIndex, ...], *, is_first: bool) -> tuple[LogicalIndex, ...]:
    """``closure_hash`` is per-tenant; ``fts`` and ``vector`` rebuild schema-wide physical indexes once per cycle."""
    selected: list[LogicalIndex] = []
    for index in indexes:
        if index == "closure_hash" or is_first:
            selected.append(index)
    return tuple(selected)


async def run_maintenance_once(
    connection: AsyncConnection,
    settings: MaintenanceSettings,
    *,
    dry_run: bool = False,
) -> MaintenanceReport:
    """Execute one maintenance cycle across all configured tenants."""
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise RuntimeError("maintenance_requires_idle_connection")

    effective_profile = settings.profile or default_maintenance_profile()
    results: list[TenantMaintenanceResult] = []
    for idx, user in enumerate(settings.users):
        scope = user_scope(user).text
        try:
            if dry_run:
                considered, working = await _dry_run_counts(
                    connection, user, scope, schema_name=settings.schema, role=settings.role
                )
                results.append(
                    TenantMaintenanceResult(
                        user_id=user,
                        considered=considered,
                        purged_episodes=working,
                        dry_run=True,
                    )
                )
                continue

            runtime = Runtime(connection, settings.role, settings.schema)
            engine = wire_engine(runtime, profile=effective_profile)
            ctx = _admin_context(user, scope)

            expired = 0
            considered = 0
            if settings.run_expire:
                expiry_receipt = await engine.call(ctx, "expire", {"scope": scope})
                if isinstance(expiry_receipt, ExpiryReceipt):
                    expired = expiry_receipt.expired
                    considered = expiry_receipt.considered

            purged = 0
            retained = 0
            if settings.run_purge_working:
                purge_receipt = await engine.call(ctx, "purge_working", {"scope": scope})
                if isinstance(purge_receipt, PurgeReceipt):
                    purged = purge_receipt.purged_episodes
                    retained = purge_receipt.retained_as_evidence

            jobs: list[str] = []
            if settings.run_rebuild_index:
                for index_name in _indexes_for_tenant(settings.indexes, is_first=(idx == 0)):
                    job_id = await engine.call(ctx, "rebuild_index", {"index": index_name})
                    jobs.append(str(job_id))

            results.append(
                TenantMaintenanceResult(
                    user_id=user,
                    expired=expired,
                    considered=considered,
                    purged_episodes=purged,
                    retained_as_evidence=retained,
                    rebuild_jobs=tuple(jobs),
                    dry_run=False,
                )
            )
        except Exception as error:  # noqa: BLE001 - isolate tenant failure so remaining tenants still maintain
            logger.warning("maintenance_tenant_failed", error_type=type(error).__name__)
            results.append(TenantMaintenanceResult(user_id=user, dry_run=dry_run, error_type=type(error).__name__))

    report = MaintenanceReport(tenants=tuple(results), dry_run=dry_run)
    logger.info(
        "maintenance_cycle_completed",
        tenants=len(report.tenants),
        dry_run=report.dry_run,
        expired=report.total_expired,
        purged_episodes=report.total_purged,
        jobs_enqueued=report.total_jobs_enqueued,
        failed_tenants=report.failed_tenants,
    )
    return report


async def serve_maintenance(
    connection: AsyncConnection,
    settings: MaintenanceSettings,
    stop: asyncio.Event,
    *,
    once: bool = False,
    dry_run: bool = False,
) -> int:
    """Run maintenance once (or on ``interval_seconds`` until ``stop`` is set)."""
    if once or dry_run:
        report = await run_maintenance_once(connection, settings, dry_run=dry_run)
        return 1 if report.failed_tenants > 0 else 0

    while not stop.is_set():
        await run_maintenance_once(connection, settings, dry_run=False)
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.interval_seconds)
        except TimeoutError:
            pass
    logger.info("maintenance_scheduler_stopped")
    return 0


__all__ = [
    "DEFAULT_INDEXES",
    "V2_D14_EXPIRY_DAYS",
    "MaintenanceReport",
    "MaintenanceSettings",
    "TenantMaintenanceResult",
    "default_maintenance_profile",
    "run_maintenance_once",
    "serve_maintenance",
    "settings_from_environment",
]
