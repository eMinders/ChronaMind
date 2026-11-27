"""``T-49``: a restore that keeps the relation, and the identity that has to notice it (``V2-D128``).

Migration 33 identified the database the erasure journal was last replayed into by the relation OID of
``restore_reconciliation``. That sees a logical ``pg_restore``, which re-creates the table, and nothing else:

* a **data-only** load puts the rows back into the existing table, so the OID is unchanged and the identity row the
  dump carries lands in the table it names;
* a **physical (PITR)** restore restores the catalogs *and* the data, so the row and its OID both come back;
* a ``CREATE DATABASE ... TEMPLATE`` clone keeps the OID on purpose, and must keep being reconciled.

Migration 34 adds three witnesses to the OID, all of which a clone copies and a restore cannot fake: the transaction
the identity row itself came from (``written_xid`` = its own ``xmin``), how many other rows the table held when it was
written (``rows_before``), and the cluster timeline. These tests measure, per path, which witness refuses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict

from custom_memory_engine.deletion.markers import (
    ErasureMarker,
    is_reconciled,
    record_reconciliation,
    turn_digest,
)
from custom_memory_engine.worker import run_once
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)
from tests.integration.test_t46_restore_erasure_db import World, world  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

#: The deletion key the ``world`` fixture's deletions were made with (``test_t46_restore_erasure_db.KEY``).
KEY = b"t" * 32


#: The client image the backup rehearsal uses (`scripts/rehearse_restore.py`): ``pg_dump``/``pg_restore`` run inside a
#: container on the host network, never on the test host itself.
PG_DOCKER_IMAGE = (
    "pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
)


def _pg(
    binary: str, dsn: str, arguments: list[str], *, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run ``pg_dump``/``pg_restore`` in the container against ``dsn``, a repository test database."""
    if shutil.which("docker") is None:
        pytest.skip("docker is required for the real data-only dump")
    params = conninfo_to_dict(dsn)
    client = ["-h", str(params["host"]), "-p", str(params.get("port", "5432")), "-U", str(params["user"])]
    target = [str(params["dbname"])] if binary == "pg_dump" else [f"--dbname={params['dbname']}"]
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "host",
        "-e",
        "PGPASSWORD",
        PG_DOCKER_IMAGE,
        binary,
        *arguments,
        *client,
        *target,
    ]
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        env={**os.environ, "PGPASSWORD": str(params.get("password", ""))},
        check=False,
    )


def _data_only_dump(dsn: str, schema: str) -> bytes:
    result = _pg("pg_dump", dsn, ["--format=custom", "--data-only", f"--schema={schema}"])
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


def _data_only_load(dsn: str, dump: bytes) -> subprocess.CompletedProcess[bytes]:
    """Restore a data-only dump into the database it came from, as a data-only restore does.

    Not ``--exit-on-error``: a table the dump holds and the database already holds refuses its own rows, which is the
    same partial outcome the real procedure has (`#262` §2); what matters is that the rows only the backup holds land.
    """
    return _pg(
        "pg_restore",
        dsn,
        ["--format=custom", "--data-only", "--no-owner", "--no-acl"],
        stdin=dump,
    )


@dataclass(frozen=True)
class Witnesses:
    """Which of the four facts an identity row carries still holds for this database."""

    relation_oid: bool
    written_xid: bool
    rows_before: bool
    timeline: bool


async def _witnesses(connection: AsyncConnection, schema: str) -> Witnesses:
    """The probe of ``deletion.markers.is_reconciled``, split into its four witnesses (a test-only diagnostic)."""
    # test-only SQL with the per-test schema interpolated; there are no values to parameterize
    cursor = await connection.execute(
        cast(
            LiteralString,
            f"""
        SELECT
          EXISTS (SELECT 1 FROM {schema}.restore_reconciliation r
                  WHERE r.relation_oid = to_regclass('{schema}.restore_reconciliation')::oid),
          EXISTS (SELECT 1 FROM {schema}.restore_reconciliation r WHERE xid(r.written_xid) = r.xmin),
          EXISTS (SELECT 1 FROM {schema}.restore_reconciliation r
                  WHERE r.rows_before = (SELECT count(*) FROM {schema}.restore_reconciliation) - 1),
          EXISTS (SELECT 1 FROM {schema}.restore_reconciliation r WHERE r.timeline IS NOT DISTINCT FROM
                  CASE WHEN pg_is_in_recovery() THEN NULL ELSE
                  ('x' || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8))::bit(32)::bigint END)
        """,
        )
    )
    row = await cursor.fetchone()
    assert row is not None
    return Witnesses(bool(row[0]), bool(row[1]), bool(row[2]), bool(row[3]))


async def test_a_reconciled_clone_of_the_migrated_template_stays_reconciled(database) -> None:  # noqa: F811
    """The property migration 33 was built around, and every test fixture depends on: a clone is reconciled.

    ``tests/integration/schema.py`` makes each test database with ``CREATE DATABASE ... TEMPLATE``, so the row this
    test reads is a byte copy of the one the runner wrote into the template -- ``xmin``, row count and timeline
    included. If a witness did not survive a clone, the whole suite would be testing a database nothing may serve.
    """
    config, schema = database
    async with connect((config, schema)) as conn:
        assert await is_reconciled(conn, schema_name=schema) is True
        assert await _witnesses(conn, schema) == Witnesses(True, True, True, True)


async def test_a_data_only_load_of_an_older_backup_is_refused_although_the_relation_is_unchanged(
    database,  # noqa: F811
) -> None:
    """The measured hole in the relation-OID identity: the table is not re-created, so witness 1 still holds.

    ``pg_dump --data-only`` then ``pg_restore --data-only`` back into the same database is the issue's own probe. The
    identity row comes back, ``relation_oid`` is unchanged, and only the row's own ``xmin`` -- which the ``COPY``
    replaced with the restoring transaction's id -- shows that the copy is not the one this database recorded.
    """
    config, schema = database
    dsn = config.database_url.get_secret_value()
    async with connect((config, schema)) as conn:
        assert await is_reconciled(conn, schema_name=schema) is True
        before = await _witnesses(conn, schema)
    dump = _data_only_dump(dsn, schema)
    async with connect((config, schema)) as conn:
        await conn.execute(
            cast(LiteralString, f"DELETE FROM {schema}.restore_reconciliation")
        )  # the probe must be live first
        assert await is_reconciled(conn, schema_name=schema) is False
    _data_only_load(dsn, dump)
    async with connect((config, schema)) as conn:
        counted = await (
            await conn.execute(cast(LiteralString, f"SELECT count(*) FROM {schema}.restore_reconciliation"))
        ).fetchone()
        assert counted == (1,), "the identity row the dump carries lands in the table it names"
        after = await _witnesses(conn, schema)
        assert after.relation_oid is before.relation_oid is True, "a data-only load keeps the relation OID"
        assert after.written_xid is False, "the loaded row's xmin is the restoring transaction, not the one it records"
        assert await is_reconciled(conn, schema_name=schema) is False, "so the identity refuses the loaded copy"


async def _restored_after_a_data_only_load(world: World) -> tuple[bytes, str, str]:  # noqa: F811 - `world` names the fixture and its value
    """Do what the procedure does with a data-only backup, and stop where the replay belongs.

    The turn is ingested and its claim extracted, the backup is taken, the user deletes the claim (the deletion erases
    the turn with it), and the backup is loaded back into the database. Returns the dump, the deleted memory key and
    the turn's job id.
    """
    job = await world.enqueue_turn("t-peanut")
    # the backup is taken between the ingest and the extraction, which is the sequence `T-46` names: the backup holds
    # the turn and its queued job, and the claim was extracted and deleted after it
    dump = _data_only_dump(os.environ["CME_TEST_DATABASE_URL"], world.schema)
    assert (await run_once(world.worker, "alice", world.settings())) == f"done:{job}"
    [(_, memory_key)] = await world.claims("peanuts")
    receipt = await world.delete(memory_key)
    assert (receipt.deleted_versions, receipt.deleted_turns) == (1, 1)
    assert await world.claims("peanuts") == []
    assert await world.rows(f"SELECT id FROM {world.schema}.turn WHERE id = 't-peanut'") == []
    _data_only_load(os.environ["CME_TEST_DATABASE_URL"], dump)
    # the load cannot roll back what the backup predates: a restore replaces the whole database, so the rows the
    # deletion wrote after the backup are not there any more (the deletion's *effect* is what the journal carries)
    for table in ("erasure_marker", "deletion_request"):
        await world.rows(f"DELETE FROM {world.schema}.{table} WHERE user_id = 'alice'")
    return dump, memory_key, job


async def test_a_data_only_load_brings_back_the_turn_the_worker_would_re_extract(world: World) -> None:  # noqa: F811 - the fixture
    """What the load restores, and the control half: with the gate out of the way the deleted claim comes back.

    The backup predates the extraction, so it holds the *turn* and its queued job, not the claim. The turn is enough:
    the worker derives the deleted claim from it again, which is the resurrection ``T-46`` is about, and it runs as
    soon as the identity lets it. Recording the identity without a replay is what lets it -- the state the witnesses
    now refuse (the next test).
    """
    world_ = world
    _, _, job = await _restored_after_a_data_only_load(world_)
    schema = world_.schema
    assert await world_.rows(f"SELECT id FROM {schema}.turn WHERE id = 't-peanut'") == [("t-peanut",)]
    assert await world_.rows(f"SELECT status FROM {schema}.job WHERE id = %s", job) == [("queued",)]
    assert await world_.claims("peanuts") == [], "the backup predates the claim: nothing to load"

    async with world_.owner.transaction():
        await record_reconciliation(
            world_.owner, schema_name=schema, by="operator-assertion", restored_mode="data-only"
        )

    assert (await run_once(world_.worker, "alice", world_.settings())) == f"done:{job}"
    assert len(await world_.claims("peanuts")) == 1, "control: the worker re-derives the claim from the restored turn"


async def test_the_worker_refuses_a_data_only_restore_until_the_journal_is_replayed_and_then_it_erases(
    world: World,  # noqa: F811 - the fixture
) -> None:
    """The documented procedure, for the mode it was never tested in: the resurrected turn is erased and the worker
    refuses the database until the replay records it.

    Same restore as the control, and the identity refuses it: every row is one the table recorded, the relation OID is
    unchanged, and only the row's own ``xmin`` says the copy is not the one this database replayed into. The worker
    claims nothing, so nothing is re-derived; the replay -- told what kind of restore this is -- erases the marked turn
    and records the identity with ``restored_mode='data-only'``.
    """
    _, memory_key, job = await _restored_after_a_data_only_load(world)
    schema = world.schema
    assert await world.rows(f"SELECT status FROM {schema}.job WHERE id = %s", job) == [("queued",)]

    assert await is_reconciled(world.owner, schema_name=schema) is False
    calls = world.extractor.calls
    assert (await run_once(world.worker, "alice", world.settings())) == "unreconciled"
    assert world.extractor.calls == calls
    assert await world.claims("peanuts") == [], "the worker did not run, so the deleted claim was not re-derived"
    assert await world.rows(f"SELECT status FROM {schema}.job WHERE id = %s", job) == [("queued",)]

    # the replay re-applies the deletion with its markers, and records what kind of restore this was
    replayed = await world.replay(
        memory_key=memory_key, markers=[ErasureMarker("turn", turn_digest(KEY, "alice", "t-peanut"))]
    )
    async with world.owner.transaction():
        await record_reconciliation(world.owner, schema_name=schema, by="replay", restored_mode="data-only")

    assert await is_reconciled(world.owner, schema_name=schema) is True
    assert await world.rows(
        f"SELECT reconciled_by, restored_mode FROM {schema}.restore_reconciliation WHERE reconciled_by = 'replay'"
    ) == [("replay", "data-only")], "the reconciliation records which restore it follows"
    assert await world.rows(f"SELECT id FROM {schema}.turn WHERE id = 't-peanut'") == [], replayed
    assert await run_once(world.worker, "alice", world.settings()) == "idle"
    assert world.extractor.calls == calls
    assert await world.claims("peanuts") == []


async def test_an_identity_row_from_another_timeline_is_refused(database) -> None:  # noqa: F811
    """A PITR ends on a new timeline, so the timeline the row records no longer matches the cluster's.

    A physical restore restores the catalogs and the data together, so every other witness holds again -- the OID, the
    row's ``xmin`` and the count. What moves is the cluster: promotion writes a new timeline, and a copy restored to an
    earlier point can only end there. Recording the row one timeline back is the state a PITR promotes into; the
    measurement ``#262`` could not take (no base backup in this environment) is this predicate, asserted here.
    """
    config, schema = database
    async with connect((config, schema)) as conn:
        assert await _witnesses(conn, schema) == Witnesses(True, True, True, True)
        # appended, not updated: the row has to stay self-consistent (its own `xmin`, the count it saw) or the other
        # witnesses would refuse it and this test would prove nothing about the timeline. The valid row goes, because
        # one row that satisfies every witness is enough for the probe -- which is what a restore leaves behind.
        await conn.execute(cast(LiteralString, f"DELETE FROM {schema}.restore_reconciliation"))
        await conn.execute(
            cast(
                LiteralString,
                f"INSERT INTO {schema}.restore_reconciliation (relation_oid, written_xid, rows_before, timeline) "
                f"SELECT to_regclass('{schema}.restore_reconciliation')::oid, pg_current_xact_id(), 0, 0",
            )
        )
        assert await _witnesses(conn, schema) == Witnesses(True, True, True, False)
        assert await is_reconciled(conn, schema_name=schema) is False

    # and a replay re-records the identity on the timeline this cluster is on, so the database is usable again
    async with connect((config, schema)) as conn:
        await record_reconciliation(conn, schema_name=schema, by="replay", restored_mode="physical")
        assert await is_reconciled(conn, schema_name=schema) is True


async def test_a_loaded_identity_row_appended_beside_the_one_here_is_refused(database) -> None:  # noqa: F811
    """A load into a database that is already reconciled appends its row; the count witness is what sees the second.

    This is the identity row's own existence as evidence: the row a probe accepts must be the last one the table holds,
    so a copy of an older identity cannot sit next to a newer one and be read as current. ``record_reconciliation``
    writes the count it saw under an advisory lock, which is why the count is exact.
    """
    config, schema = database
    async with connect((config, schema)) as conn:
        await conn.execute(
            cast(
                LiteralString,
                # the row an older dump carries, appended as a load appends it
                f"INSERT INTO {schema}.restore_reconciliation (relation_oid, written_xid, rows_before, timeline) "
                "SELECT relation_oid, pg_current_xact_id(), 0, timeline FROM "
                f"{schema}.restore_reconciliation",
            )
        )
        witnesses = await _witnesses(conn, schema)
        assert witnesses.rows_before is False, "two rows, and neither can claim to be the last one written"
        assert await is_reconciled(conn, schema_name=schema) is False
