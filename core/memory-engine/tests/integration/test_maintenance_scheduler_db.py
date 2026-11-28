"""Integration tests for the A-014 periodic maintenance scheduler and CLI (``#255``, ``V2-D119``)."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.runtime import Receipt, Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.profile import ExtractorSpec
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic.contracts import ExtractionRequest, ExtractionResult
from custom_memory_engine.worker import WorkerSettings, run_once
from custom_memory_engine.worker.maintenance import (
    MaintenanceSettings,
    default_maintenance_profile,
    run_maintenance_once,
)
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_maintenance.py"
T0 = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
OLD_BELIEF_AT = datetime.now(UTC) - timedelta(days=250)


class NoopExtractor:
    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        del request
        return ExtractionResult(claims=(), extractor_version="noop/v1")


@dataclass
class MaintRig:
    owner: AsyncConnection
    worker: AsyncConnection
    dsn: str
    schema: str
    role: str

    async def rows(self, query: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        cursor = await self.owner.execute(query, params)  # type: ignore[arg-type]
        found = [tuple(row) for row in await cursor.fetchall()]
        await self.owner.commit()
        return found


@asynccontextmanager
async def rig(database) -> AsyncIterator[MaintRig]:  # noqa: F811
    config, schema = database
    role = "cme_rt_mnt_" + uuid4().hex[:10]
    dsn = config.database_url.get_secret_value()
    async with connect(database) as owner:
        for user in ("alice", "bob"):
            await owner.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, f"ext-{user}", T0))
            await owner.execute(
                "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", (f"s-{user}", user, T0)
            )
            await owner.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 1, 'user', 'I prefer coffee', %s)",
                (f"t-{user}", f"s-{user}", user, T0),
            )
        await provision_runtime_role(owner, schema, role)
        await owner.commit()
        async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as worker:
            await worker.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await worker.execute("SET timezone = 'UTC'")
            try:
                yield MaintRig(owner, worker, dsn, schema, role)
            finally:
                await owner.rollback()
                await owner.execute("RESET ROLE")
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                await owner.commit()


async def _seed_expired_abstraction(conn: AsyncConnection, user: str, version_id: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "retention) VALUES (%s, %s, %s, 'active', 'asserted', 'claim', 'self', 'statement', false, "
            "'support for abs', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', %s, 'private', "
            "'durable')",
            (f"src-{version_id}", f"src-{version_id}", user, OLD_BELIEF_AT, f"/u/{user}/"),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'I prefer coffee', 'support')",
            (f"src-{version_id}", f"t-{user}", user),
        )
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "created_by, created_tier, um_facet) VALUES (%s, %s, %s, 'active', 'inferred', 'abstraction', "
            "'self', 'statement', false, 'user prefers coffee', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
            "'persona', %s, 'private', %s, 'B', 'preference')",
            (version_id, version_id, user, OLD_BELIEF_AT, f"/u/{user}/", user),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'I prefer coffee', 'support')",
            (version_id, f"t-{user}", user),
        )
        await conn.execute(
            "INSERT INTO memory_derivation VALUES (%s, %s, %s, 'consolidation')",
            (version_id, f"src-{version_id}", user),
        )
        await conn.execute(
            "UPDATE memory_version SET method_id = 'm-v1-persona' WHERE id = %s AND method_id IS NULL",
            (version_id,),
        )


async def _seed_working_episode(conn: AsyncConnection, role: str, schema: str, user: str, key: str) -> str:
    runtime = Runtime(conn, role, schema)
    seed_profile = default_maintenance_profile().model_copy(
        update={"extractors": (ExtractorSpec(name="external_ingest", version="1", source_types=("external",)),)}
    )
    engine = wire_engine(runtime, profile=seed_profile)
    scope = f"/u/{user}/"
    ctx = CallerContext.from_credential(
        Credential(user, "B"),
        write_scope=scope,
        read_set=resolve_read_set(user, [Selector.of(scope)], ()),
    )
    text = "transient working episode"
    receipt = await engine.call(
        ctx,
        "ingest_episode",
        {
            "scope": scope,
            "content": text,
            "source": {
                "type": "external",
                "ref": f"app://session/s-{user}",
                "span": text,
                "content_hash": canonical_hash(text),
            },
            "event_time": T0.isoformat(),
            "idempotency_key": key,
        },
    )
    return cast(Receipt, receipt).id


async def test_maintenance_dry_run_is_readonly_and_once_runs_and_worker_drains_rebuilds(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await _seed_expired_abstraction(r.owner, "alice", "abs-alice-old")
        await _seed_expired_abstraction(r.owner, "bob", "abs-bob-old")
        await r.owner.commit()

        ep_alice = await _seed_working_episode(r.worker, r.role, r.schema, "alice", "ep-a")
        ep_bob = await _seed_working_episode(r.worker, r.role, r.schema, "bob", "ep-b")

        settings = MaintenanceSettings(role=r.role, users=("alice", "bob"), schema=r.schema)

        dry = await run_maintenance_once(r.worker, settings, dry_run=True)
        assert dry.dry_run is True
        assert dry.failed_tenants == 0
        assert [t.considered for t in dry.tenants] == [1, 1]
        assert [t.purged_episodes for t in dry.tenants] == [1, 1]
        assert dry.total_jobs_enqueued == 0

        # Zero mutations occurred during dry-run.
        assert await r.rows(f"SELECT count(*) FROM {r.schema}.abstraction_stats WHERE expires_at IS NOT NULL") == [
            (0,)
        ]
        assert await r.rows(
            f"SELECT count(*) FROM {r.schema}.memory_version WHERE id = ANY(%s)", ([ep_alice, ep_bob],)
        ) == [(2,)]
        assert await r.rows(f"SELECT count(*) FROM {r.schema}.job") == [(0,)]
        assert await r.rows(f"SELECT count(*) FROM {r.schema}.audit_event WHERE event_type = 'retention_expired'") == [
            (0,)
        ]

        # Real execution expires, purges working episodes, and enqueues rebuild_index jobs.
        report = await run_maintenance_once(r.worker, settings, dry_run=False)
        assert report.dry_run is False
        assert report.failed_tenants == 0
        assert report.total_expired == 2
        assert report.total_purged == 2
        # First tenant gets fts, vector, closure_hash (3); second tenant gets closure_hash only (1).
        assert len(report.tenants[0].rebuild_jobs) == 3
        assert len(report.tenants[1].rebuild_jobs) == 1
        assert report.total_jobs_enqueued == 4

        assert await r.rows(f"SELECT count(*) FROM {r.schema}.abstraction_stats WHERE expires_at IS NOT NULL") == [
            (2,)
        ]
        assert await r.rows(
            f"SELECT count(*) FROM {r.schema}.memory_version WHERE id = ANY(%s)", ([ep_alice, ep_bob],)
        ) == [(0,)]
        assert await r.rows(f"SELECT count(*) FROM {r.schema}.audit_event WHERE event_type = 'retention_expired'") == [
            (2,)
        ]

        queued = await r.rows(f"SELECT user_id, payload->>'index', status FROM {r.schema}.job ORDER BY user_id, id")
        assert len(queued) == 4
        assert all(status == "queued" for _, _, status in queued)
        assert sorted(str(idx) for _, idx, _ in queued) == ["closure_hash", "closure_hash", "fts", "vector"]

        # Drain all enqueued rebuild_index jobs with the real worker loop.
        worker_settings = WorkerSettings(
            role=r.role, users=("alice", "bob"), schema=r.schema, extractor=NoopExtractor()
        )
        drained: list[str] = []
        for user in ("alice", "bob"):
            while True:
                outcome = await run_once(r.worker, user, worker_settings)
                if outcome == "idle":
                    break
                drained.append(outcome)
        assert len(drained) == 4
        assert all(item.startswith("done:") for item in drained), drained
        assert await r.rows(f"SELECT status FROM {r.schema}.job") == [("done",)] * 4


async def test_maintenance_cli_runs_dry_run_once_and_missing_env_exit_2(database) -> None:  # noqa: F811
    async with rig(database) as r:
        await _seed_expired_abstraction(r.owner, "alice", "abs-cli-old")
        await r.owner.commit()
        await _seed_working_episode(r.worker, r.role, r.schema, "alice", "ep-cli")

        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "CME_DATABASE_URL": r.dsn,
            "CME_RUNTIME_ROLE": r.role,
            "CME_SCHEMA": r.schema,
            "CME_WORKER_USERS": "alice",
        }

        dry_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            cwd=str(ROOT),
            env=base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        dry_out, dry_err = await asyncio.wait_for(dry_proc.communicate(), timeout=20)
        assert dry_proc.returncode == 0, dry_err.decode()
        assert "maintenance_cycle_completed" in (dry_out.decode() + dry_err.decode())
        assert await r.rows(f"SELECT count(*) FROM {r.schema}.job") == [(0,)]

        once_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            "--once",
            cwd=str(ROOT),
            env=base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        once_out, once_err = await asyncio.wait_for(once_proc.communicate(), timeout=20)
        assert once_proc.returncode == 0, once_err.decode()
        assert "maintenance_cycle_completed" in (once_out.decode() + once_err.decode())
        async with r.owner.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(r.schema, "job")))
            assert await cursor.fetchone() == (3,)
        await r.owner.commit()

        bad_env = dict(base_env)
        del bad_env["CME_RUNTIME_ROLE"]
        bad_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            "--once",
            cwd=str(ROOT),
            env=bad_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, bad_err_bytes = await asyncio.wait_for(bad_proc.communicate(), timeout=20)
        bad_err = bad_err_bytes.decode()
        assert bad_proc.returncode == 2
        assert "cme_runtime_role_required" in bad_err
        assert r.dsn not in bad_err
