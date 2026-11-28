"""Migration 33 grants on a running deployment, and reconciliation failures during a worker claim."""

import asyncio
import json
import os
import sys
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import sql
from pydantic import SecretStr

from custom_memory_engine.api.entry import Credential, EntryError
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.deletion.markers import MarkerError, RestoreNotReconciled
from custom_memory_engine.host import HostCredentials, HostService, HostSettings
from custom_memory_engine.queue.worker import MAX_ATTEMPTS
from custom_memory_engine.schema import migrate
from custom_memory_engine.security.provisioning import (
    RlsProvisioningError,
    privilege_violations,
    provision_runtime_role,
)
from custom_memory_engine.semantic.slow_lane import recover_stale
from custom_memory_engine.worker import run_once
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database  # noqa: F401 (fixture)
from tests.integration.test_t46_restore_erasure_db import Extractor, World

pytestmark = pytest.mark.integration
type Database = tuple[DatabaseConfig, str]


def host(target: Database, role: str) -> HostService:
    config, schema = target
    return HostService(
        HostSettings(
            dsn=config.database_url.get_secret_value(), role=role, schema_name=schema, deletion_key=b"d" * 32
        ),
        HostCredentials({"test-erasure-upgrade-token": Credential("u1", "C")}),
    )


async def cli(database_url: SecretStr, role: str, *args: str) -> dict:
    dsn = database_url.get_secret_value()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *args,
        env={
            **os.environ,
            "RL_DATABASE_URL": dsn,
            "CME_DATABASE_URL": dsn,
            "CME_RUNTIME_ROLE": role,
            "CME_SCHEMA": "cme_engine",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout)


async def test_an_in_place_upgrade_restores_grants_on_the_existing_role_before_reconciliation(
    database: Database,  # noqa: F811
) -> None:
    config, schema = database
    role = "cme_upgrade_" + uuid4().hex[:12]
    service = host(database, role)
    async with connect(database) as conn:
        await provision_runtime_role(conn, schema, role)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=32)), prepare=False
        )
        assert (await cli(config.database_url, role, "-m", "custom_memory_engine.schema.migrate", "status"))[
            "version"
        ] == 32
        await cli(config.database_url, role, "-m", "custom_memory_engine.schema.migrate", "apply")
        with pytest.raises(MarkerError, match="^erasure_runtime_grant_missing$"):
            await service.start()
        assert not service.is_started
        for _ in range(2):
            assert await cli(config.database_url, role, "scripts/provision_role.py", "--role", role) == {
                "status": "provisioned"
            }
        assert await privilege_violations(conn, schema, role) == []
        status = await cli(config.database_url, role, "-m", "custom_memory_engine.schema.migrate", "status")
        assert (status["state"], status["version"]) == ("applied", migrate.VERSION)
        with pytest.raises(RestoreNotReconciled):
            await service.start()
        assert not service.is_started
        result = await cli(
            config.database_url,
            role,
            "scripts/erasure_journal.py",
            "replay",
            "--mark-reconciled",
            "--json",
            "--restored-mode",
            "in-place",
        )
        assert result["reconciled"] is True
        try:
            await service.start()
            assert await service.probe_health()
        finally:
            await service.close()


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT"])
@pytest.mark.parametrize("table", ["erasure_marker", "erased_turn", "restore_reconciliation"])
async def test_host_start_and_a_live_delete_both_report_a_missing_runtime_grant(
    database: Database,  # noqa: F811
    privilege: str,
    table: str,
) -> None:
    _, schema = database
    role = "cme_grants_" + uuid4().hex[:12]
    service = host(database, role)
    restarting = host(database, role)
    async with connect(database) as conn:
        await provision_runtime_role(conn, schema, role)
        try:
            await service.start()
            await conn.execute(
                sql.SQL("REVOKE {} ON {} FROM {}").format(
                    sql.SQL(cast(LiteralString, privilege)), sql.Identifier(schema, table), sql.Identifier(role)
                )
            )
            with pytest.raises(MarkerError, match="^erasure_runtime_grant_missing$"):
                await restarting.start()
            assert not restarting.is_started
            assert not await service.probe_health()
            with pytest.raises(EntryError) as error:
                await service.call(Credential("u1", "C"), "delete", {"scope": "all"})
            assert (error.value.code, error.value.detail) == ("forbidden", "erasure_runtime_grant_missing")
            assert await (await conn.execute("SELECT count(*) FROM deletion_request")).fetchone() == (0,)
            await provision_runtime_role(conn, schema, role)
            await restarting.start()
            await service.call(Credential("u1", "C"), "delete", {"scope": "all"})
            assert await (await conn.execute("SELECT count(*) FROM deletion_request")).fetchone() == (1,)
        finally:
            await restarting.close()
            await service.close()


@pytest.mark.parametrize("unsafe", ["INHERIT", "LOGIN", "BYPASSRLS", "excess_privilege"])
async def test_failed_verification_preserves_the_previous_grants_on_autocommit(
    database: Database,  # noqa: F811
    unsafe: str,
) -> None:
    _, schema = database
    role = "cme_unsafe_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await provision_runtime_role(conn, schema, role)
        await conn.execute(sql.SQL("REVOKE INSERT ON erased_turn FROM {}").format(sql.Identifier(role)))
        if unsafe == "excess_privilege":
            await conn.execute(sql.SQL("GRANT DELETE ON erased_turn TO {}").format(sql.Identifier(role)))
            code = "runtime_role_privileges_not_least"
        else:
            await conn.execute(
                sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(role), sql.SQL(cast(LiteralString, unsafe)))
            )
            code = "runtime_role_has_dangerous_attribute"
        with pytest.raises(RlsProvisioningError, match=f"^{code}$"):
            await provision_runtime_role(conn, schema, role)
        assert await (
            await conn.execute("SELECT has_table_privilege(%s, 'erased_turn', 'INSERT')", (role,))
        ).fetchone() == (False,)


async def test_repeated_raising_reconciliation_probes_leave_the_same_job_and_attempt_budget(
    database: Database,  # noqa: F811
) -> None:
    _, schema = database
    role = "cme_worker_" + uuid4().hex[:12]
    async with connect(database, user="alice") as owner, connect(database, user="alice") as worker:
        await provision_runtime_role(owner, schema, role)
        await owner.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', now())")
        await owner.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', now())")
        world = World(owner, worker, schema, role, Extractor())
        job = await world.enqueue_turn("upgrade-turn", text="empty extraction")
        await owner.execute(sql.SQL("REVOKE SELECT ON restore_reconciliation FROM {}").format(sql.Identifier(role)))
        for _ in range(MAX_ATTEMPTS + 1):
            with pytest.raises(MarkerError, match="^erasure_runtime_grant_missing$"):
                await run_once(worker, "alice", world.settings())
            await owner.execute("UPDATE job SET next_run_at = now() - interval '1 hour' WHERE status = 'running'")
            assert await recover_stale(worker, "alice", schema_name=schema, role=role) == 0
            assert await world.rows("SELECT status, attempts FROM job WHERE id = %s", job) == [("queued", 0)]
        assert world.extractor.calls == 0
        await provision_runtime_role(owner, schema, role)
        assert await run_once(worker, "alice", world.settings()) == f"done:{job}"
        assert world.extractor.calls == 1
        assert await world.rows("SELECT status, attempts FROM job WHERE id = %s", job) == [("done", 1)]
