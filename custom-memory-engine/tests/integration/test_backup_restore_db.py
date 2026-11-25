"""End-to-end PostgreSQL backup & restore rehearsal integration tests (``#255``, ``V2-D119``)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo

from tests.integration.test_migration_m1 import database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rehearse_restore.py"


def test_backup_and_restore_rehearsal_passes_against_real_postgres() -> None:
    dsn = os.environ.get("CME_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("CME_TEST_DATABASE_URL is required")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--source-dsn", dsn, "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["role_bypassrls_verified"] is True
    assert report["catalog_non_index_attname_identical"] is True
    assert report["post_restore_migration_state"] == "applied"
    assert report["post_restore_migration_reason"] is None
    assert report["post_restore_kept_count"] == 1
    assert report["post_restore_deleted_count"] == 0
    assert report["post_restore_deletion_requests"] == 1
    assert report["post_restore_digest_verified"] is True
    assert report["pre_restore_resurrected_count"] == 1
    assert report["pre_restore_deletion_requests"] == 0


async def test_rehearsal_refuses_non_bypassrls_dump_role(database) -> None:  # noqa: F811
    config, _ = database
    base_dsn = config.database_url.get_secret_value()
    low_role = f"cme_nobypass_{uuid4().hex[:8]}"
    admin_dsn = make_conninfo(base_dsn, dbname="postgres")
    async with await AsyncConnection.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
        await admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'nobypass_pw_only' NOSUPERUSER NOBYPASSRLS").format(
                sql.Identifier(low_role)
            )
        )
    try:
        low_dsn = make_conninfo(base_dsn, user=low_role, password="nobypass_pw_only")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            "--source-dsn",
            low_dsn,
            "--json",
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        assert proc.returncode == 1
        assert "dump_role_must_have_bypassrls" in stderr_bytes.decode("utf-8")
    finally:
        async with await AsyncConnection.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(low_role)))
