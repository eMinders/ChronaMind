"""Backup & restore rehearsal against real PostgreSQL, and the erasure-journal replay (``#255``, ``V2-D122``)."""

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
    # T-46 (V2-D125): a turn the backup holds, whose claim was extracted and deleted after it, does not come back
    assert report["t46_markers_exported"] > 0
    assert report["t46_turn_erased_by_replay"] is True
    assert report["t46_worker_cannot_re_extract"] is True
    assert report["post_restore_migration_state"] == "applied"
    assert report["post_restore_migration_reason"] is None
    assert report["post_restore_kept_count"] == 1
    assert report["post_restore_deleted_count"] == 0
    assert report["post_restore_deletion_requests"] == 5
    assert report["post_restore_digest_verified"] is True
    # the risk: a backup from before the deletions brings the deleted memories back, with no record of the deletions
    assert report["pre_restore_resurrected_count"] == 1
    assert report["pre_restore_deletion_requests"] == 0
    # the erasure journal (V2-D122): five deletions after the backup, kept outside the database
    assert report["journal_entries"] == 5
    assert report["journal_entries_after_stale_export"] == 5  # exporting from the older database cannot shrink it
    assert report["journal_wrong_key_refused"] is True
    assert report["journal_unstamped_refused"] is True
    assert report["journal_truncated_refused"] is True
    # replayed into the restored database: the resolved memory deletion and bob's and carol's `delete all` erase
    # again, the two memories the backup never held (alice's late claim and the T-46 peanut claim) are recorded with
    # nothing to erase, and carol (whom the backup predates) is re-provisioned so her epochs are taken. The T-46 turn
    # is erased by that replay even though its own target never resolves -- a difference the second number used to hide
    assert (report["replay_replayed"], report["replay_recorded_unresolved"], report["replay_already_present"]) == (
        3,
        2,
        0,
    )
    assert report["replay_users_reprovisioned"] == 1
    assert report["replayed_resurrected_count"] == 0
    assert report["replayed_bob_count"] == 0
    assert report["replayed_kept_count"] == 1
    assert report["replayed_request_ids_match"] is True
    # idempotent: a second replay finds every request and does nothing
    assert (report["second_replay_already_present"], report["second_replay_replayed"]) == (5, 0)
    # life after the restore: the next export merges the replayed requests (no conflict), carol's new deletion takes
    # the next epoch and is journalled, and a replay of the grown journal finds everything present
    assert report["journal_entries_after_replay_export"] == 5
    assert report["journal_entries_after_new_deletion"] == 6
    assert report["third_replay_already_present"] == 6
    # the leak scanner's database channels: the resurrection is visible before the replay and gone after it
    assert report["scan_leaks_before_replay"] > 0
    assert report["scan_leaks_after_replay"] == 0
    assert report["scan_measured_channels_after_replay"] > 0


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
