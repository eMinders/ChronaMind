"""Backup & restore rehearsal against real PostgreSQL ``pg_dump`` / ``pg_restore`` (``#255``, ``V2-D119``).

Exercises every documented requirement in ``deploy/README.md``:

1. Verifies the dump role carries ``rolbypassrls`` or ``rolsuper`` (``FORCE ROW LEVEL SECURITY`` protection).
2. Provisions isolated temporary source & restore databases (cleaned up in ``finally``).
3. Applies migrations via :class:`~custom_memory_engine.schema.migrate.MigrationRunner`, writes sampled memories
   through :func:`~custom_memory_engine.api.wiring.wire_engine`, captures a **pre-deletion** ``pg_dump`` (``-Fc``),
   deletes one memory via ``Engine.call("delete", ...)``, and captures a **post-deletion** ``pg_dump`` (``-Fc``).
4. Restores both dumps into fresh databases after bootstrapping ``public`` extensions (``vector``, ``btree_gist``,
   ``pg_trgm``), re-provisions ``CME_RUNTIME_ROLE`` with :func:`provision_runtime_role`, verifies
   ``MigrationRunner.status().state == "applied"`` and post-deletion row/receipt integrity, AND verifies that
   restoring the pre-deletion dump resurrects the deleted memory (proving the ``#255`` / ``V2-D119`` policy caveat).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import tuple_row
from pydantic import SecretStr
from rich.console import Console

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.read_handlers import QueryResult
from custom_memory_engine.api.runtime import Receipt, Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.engine.deletion import target_ref_digest
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.schema.migrate import (
    _CATALOG_QUERIES,
    EXTENSIONS,
    OWNER_MARKER,
    VERSION,
    MigrationConfig,
    MigrationRunner,
)
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import ProvenanceKey
from custom_memory_engine.security import (
    provision_runtime_role,
    verify_forced_rls,
    verify_privileges,
    verify_runtime_role,
)

PG_DOCKER_IMAGE = (
    "pgvector/pgvector:0.8.7-pg16-bookworm@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
)
PROVENANCE_KEY = ProvenanceKey(b"r" * 32)
DELETION_KEY = b"d" * 32
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
KEEP_TEXT = "Alice works as an architect in Vienna"
DELETE_TEXT = "Alice is allergic to shellfish"


@dataclass(frozen=True, slots=True)
class RehearsalReport:
    """Content-free summary of a full backup/restore rehearsal."""

    dump_mode: str
    role_bypassrls_verified: bool
    post_restore_migration_state: str
    post_restore_migration_reason: str | None
    pre_restore_migration_state: str
    pre_restore_migration_reason: str | None
    catalog_non_index_attname_identical: bool
    post_restore_kept_count: int
    post_restore_deleted_count: int
    post_restore_deletion_requests: int
    post_restore_digest_verified: bool
    pre_restore_resurrected_count: int
    pre_restore_deletion_requests: int


def _resolve_pg_tool_mode() -> str:
    """Return ``native`` when ``pg_dump``/``pg_restore`` are on PATH, else ``docker`` if available."""
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return "native"
    if shutil.which("docker"):
        return "docker"
    raise RuntimeError("pg_dump_and_pg_restore_unavailable")


def _pg_command(mode: str, binary: str, args: list[str]) -> list[str]:
    if mode == "native":
        return [binary, *args]
    return [
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
        *args,
    ]


def _run_pg_dump(mode: str, dsn: str, schema: str) -> bytes:
    params = conninfo_to_dict(dsn)
    host = str(params["host"])
    port = str(params.get("port", "5432"))
    user = str(params["user"])
    dbname = str(params["dbname"])
    password = str(params.get("password", ""))
    env = {**os.environ, "PGPASSWORD": password}
    cmd = _pg_command(
        mode,
        "pg_dump",
        ["--format=custom", f"--schema={schema}", "-h", host, "-p", port, "-U", user, dbname],
    )
    proc = subprocess.run(cmd, env=env, capture_output=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"pg_dump_failed:{err}")
    return proc.stdout


def _run_pg_restore(mode: str, dsn: str, schema: str, dump_bytes: bytes) -> None:
    params = conninfo_to_dict(dsn)
    host = str(params["host"])
    port = str(params.get("port", "5432"))
    user = str(params["user"])
    dbname = str(params["dbname"])
    password = str(params.get("password", ""))
    env = {**os.environ, "PGPASSWORD": password}
    cmd = _pg_command(
        mode,
        "pg_restore",
        [
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            dbname,
        ],
    )
    proc = subprocess.run(cmd, input=dump_bytes, env=env, capture_output=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"pg_restore_failed:{err}")


def _context(tier: str, *, admin: bool = False) -> CallerContext:
    scope = "/u/alice/"
    return CallerContext.from_credential(
        Credential("alice", tier, admin),  # type: ignore[arg-type]
        write_scope=scope,
        read_set=resolve_read_set("alice", [Selector.of(scope)], ()),
    )


async def _write_claim(conn: AsyncConnection, role: str, schema: str, text: str, key: str) -> str:
    runtime = Runtime(conn, role, schema, provenance_key=PROVENANCE_KEY, deletion_key=DELETION_KEY)
    engine = wire_engine(runtime)
    ctx = _context("B")
    receipt = await engine.call(
        ctx,
        "assert",
        {
            "scope": "/u/alice/",
            "kind": "claim",
            "content": text,
            "evidence": [
                {
                    "type": "external",
                    "ref": "app://session/s-alice",
                    "span": text,
                    "content_hash": canonical_hash(text),
                    "role": "user",
                }
            ],
            "event_range": None,
            "idempotency_key": key,
        },
    )
    return cast(Receipt, receipt).id


async def _query_facts(conn: AsyncConnection, role: str, schema: str, query_text: str) -> list[str]:
    runtime = Runtime(conn, role, schema, provenance_key=PROVENANCE_KEY, deletion_key=DELETION_KEY)
    engine = wire_engine(runtime)
    result = await engine.call(
        _context("B"),
        "query",
        {"request": {"scope": "/u/alice/", "text": query_text}},
    )
    return sorted({item.content for item in cast(QueryResult, result).facts})


async def _bootstrap_extensions(dsn: str) -> None:
    async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        for extension in EXTENSIONS:
            await conn.execute(
                sql.SQL("CREATE EXTENSION IF NOT EXISTS {} WITH SCHEMA public").format(sql.Identifier(extension))
            )


async def _verify_schema_comment(conn: AsyncConnection, schema: str) -> None:
    async with conn.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT pg_catalog.obj_description(n.oid, 'pg_namespace') "
            "FROM pg_catalog.pg_namespace n WHERE n.nspname = %s",
            (schema,),
        )
        row = await cursor.fetchone()
    if row is None or row[0] != OWNER_MARKER:
        raise RuntimeError(f"schema_owner_marker_missing:{row}")


async def _drop_role_in_db(dsn: str, role: str) -> None:
    async with await AsyncConnection.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        await conn.execute("RESET ROLE")
        async with conn.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (role,))
            if await cursor.fetchone() is None:
                return
        await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))


async def _snapshot_catalog(conn: AsyncConnection, schema: str) -> list[list[tuple[object, ...]]]:
    """Capture all 9 ``MigrationRunner._CATALOG_QUERIES`` plus non-index ``pg_attribute`` rows."""
    snapshots: list[list[tuple[object, ...]]] = []
    async with conn.transaction():
        await conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
        for query in _CATALOG_QUERIES:
            cursor = await conn.execute(query, (schema,))  # type: ignore[arg-type]
            snapshots.append([tuple(row) for row in await cursor.fetchall()])
        # Also capture table/view-only pg_attribute rows (excluding relkind='i' where PostgreSQL RENAME COLUMN
        # in migrations 009/015 preserves pre-rename index attnames while pg_restore recreates post-rename names).
        cursor = await conn.execute(
            """SELECT c.relname, a.attnum, a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
                      a.attnotnull, a.attidentity, a.attgenerated,
                      pg_catalog.pg_get_expr(d.adbin,d.adrelid)
               FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
               JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
               LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
               WHERE n.nspname=%s AND c.relkind <> 'i' AND a.attnum>0 AND NOT a.attisdropped
               ORDER BY c.relname,a.attnum""",
            (schema,),
        )
        snapshots.append([tuple(row) for row in await cursor.fetchall()])
    return snapshots


async def _restore_and_provision(
    *,
    mode: str,
    target_dsn: str,
    schema: str,
    role: str,
    dump_bytes: bytes,
    source_catalog: list[list[tuple[object, ...]]],
) -> tuple[str, str | None, bool]:
    await _bootstrap_extensions(target_dsn)
    _run_pg_restore(mode, target_dsn, schema, dump_bytes)
    async with await AsyncConnection.connect(target_dsn, autocommit=True, connect_timeout=5) as conn:
        await _verify_schema_comment(conn, schema)
        await provision_runtime_role(conn, schema, role)
        await verify_forced_rls(conn, schema)
        await verify_runtime_role(conn, role)
        await verify_privileges(conn, schema, role)
        restored_catalog = await _snapshot_catalog(conn, schema)

    for idx, (src_rows, rst_rows) in enumerate(zip(source_catalog, restored_catalog, strict=True)):
        if rst_rows != src_rows:
            raise RuntimeError(f"restored_catalog_mismatch_query_{idx}")

    status = await MigrationRunner(MigrationConfig(database_url=SecretStr(target_dsn), schema_name=schema)).status()
    if status.version != VERSION or not status.checksum_sha256:
        raise RuntimeError(f"restored_migration_history_invalid:{status.state}:{status.version}:{status.reason}")
    if status.state != "applied":
        raise RuntimeError(f"restored_migration_state_invalid:{status.state}:{status.reason}")
    return status.state, status.reason, True


async def rehearse_restore(source_dsn: str, *, schema_name: str = "cme_engine") -> RehearsalReport:
    """Execute full backup & restore rehearsal against isolated temporary PostgreSQL databases."""
    params = conninfo_to_dict(source_dsn)
    if not all(params.get(key) for key in ("host", "user", "dbname")):
        raise ValueError("source_dsn_must_name_host_user_dbname")
    mode = _resolve_pg_tool_mode()
    tag = uuid4().hex[:8]
    src_db = f"cme_rehearsal_src_{tag}"
    post_db = f"cme_rehearsal_post_{tag}"
    pre_db = f"cme_rehearsal_pre_{tag}"
    src_role = f"cme_rt_src_{tag}"
    post_role = f"cme_rt_post_{tag}"
    pre_role = f"cme_rt_pre_{tag}"

    admin_dsn = make_conninfo(source_dsn, dbname="postgres")
    src_dsn = make_conninfo(source_dsn, dbname=src_db)
    post_dsn = make_conninfo(source_dsn, dbname=post_db)
    pre_dsn = make_conninfo(source_dsn, dbname=pre_db)

    async with await AsyncConnection.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
        async with admin.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user")
            role_row = await cursor.fetchone()
        if role_row is None or not (bool(role_row[0]) or bool(role_row[1])):
            raise RuntimeError("dump_role_must_have_bypassrls")

        try:
            for db_name in (src_db, post_db, pre_db):
                await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))
                await admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(db_name)))

            await _bootstrap_extensions(src_dsn)
            await MigrationRunner(MigrationConfig(database_url=SecretStr(src_dsn), schema_name=schema_name)).apply()

            async with await AsyncConnection.connect(src_dsn, autocommit=True, connect_timeout=5) as src_conn:
                await src_conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
                await src_conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
                await provision_runtime_role(src_conn, schema_name, src_role)

                await _write_claim(src_conn, src_role, schema_name, KEEP_TEXT, "reh-keep")
                del_version_id = await _write_claim(src_conn, src_role, schema_name, DELETE_TEXT, "reh-del")

                async with src_conn.cursor(row_factory=tuple_row) as cursor:
                    await cursor.execute("SELECT set_config('app.user_id', 'alice', false)")
                    await cursor.execute(
                        sql.SQL("SELECT memory_key FROM {} WHERE id = %s").format(
                            sql.Identifier(schema_name, "memory_version")
                        ),
                        (del_version_id,),
                    )
                    key_row = await cursor.fetchone()
                    await cursor.execute("RESET app.user_id")
                if key_row is None:
                    raise RuntimeError("seeded_memory_key_missing")
                del_memory_key = str(key_row[0])

                pre_facts = await _query_facts(src_conn, src_role, schema_name, "shellfish")
                if DELETE_TEXT not in pre_facts:
                    raise RuntimeError("seeded_delete_target_not_queryable")

                pre_dump = _run_pg_dump(mode, src_dsn, schema_name)

                runtime = Runtime(
                    src_conn, src_role, schema_name, provenance_key=PROVENANCE_KEY, deletion_key=DELETION_KEY
                )
                engine = wire_engine(runtime)
                await engine.call(
                    _context("C", admin=True),
                    "delete",
                    {"scope": "memory", "target_ref": del_memory_key, "deletion_epoch": None},
                )
                if DELETE_TEXT in await _query_facts(src_conn, src_role, schema_name, "shellfish"):
                    raise RuntimeError("source_delete_failed")

                post_dump = _run_pg_dump(mode, src_dsn, schema_name)
                source_catalog = await _snapshot_catalog(src_conn, schema_name)

            post_state, post_reason, post_cat_ok = await _restore_and_provision(
                mode=mode,
                target_dsn=post_dsn,
                schema=schema_name,
                role=post_role,
                dump_bytes=post_dump,
                source_catalog=source_catalog,
            )
            pre_state, pre_reason, pre_cat_ok = await _restore_and_provision(
                mode=mode,
                target_dsn=pre_dsn,
                schema=schema_name,
                role=pre_role,
                dump_bytes=pre_dump,
                source_catalog=source_catalog,
            )

            expected_digest = target_ref_digest(DELETION_KEY, "alice", "memory", del_memory_key)

            async with await AsyncConnection.connect(post_dsn, autocommit=True, connect_timeout=5) as post_conn:
                post_kept = await _query_facts(post_conn, post_role, schema_name, "Vienna")
                post_deleted = await _query_facts(post_conn, post_role, schema_name, "shellfish")
                async with post_conn.cursor(row_factory=tuple_row) as cursor:
                    await cursor.execute(
                        sql.SQL("SELECT target_ref FROM {}").format(sql.Identifier(schema_name, "deletion_request"))
                    )
                    req_rows = await cursor.fetchall()
                post_req_count = len(req_rows)
                digest_ok = post_req_count == 1 and str(req_rows[0][0]) == expected_digest

            async with await AsyncConnection.connect(pre_dsn, autocommit=True, connect_timeout=5) as pre_conn:
                pre_resurrected = await _query_facts(pre_conn, pre_role, schema_name, "shellfish")
                async with pre_conn.cursor(row_factory=tuple_row) as cursor:
                    await cursor.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema_name, "deletion_request"))
                    )
                    pre_req_row = await cursor.fetchone()
                pre_req_count = int(pre_req_row[0]) if pre_req_row else 0

            if KEEP_TEXT not in post_kept:
                raise RuntimeError("post_restore_kept_memory_missing")
            if DELETE_TEXT in post_deleted:
                raise RuntimeError("post_restore_deleted_memory_unexpectedly_present")
            if not digest_ok:
                raise RuntimeError("post_restore_deletion_digest_mismatch")
            if DELETE_TEXT not in pre_resurrected:
                raise RuntimeError("pre_restore_resurrection_check_did_not_trigger")

            return RehearsalReport(
                dump_mode=mode,
                role_bypassrls_verified=True,
                post_restore_migration_state=post_state,
                post_restore_migration_reason=post_reason,
                pre_restore_migration_state=pre_state,
                pre_restore_migration_reason=pre_reason,
                catalog_non_index_attname_identical=(post_cat_ok and pre_cat_ok),
                post_restore_kept_count=len(post_kept),
                post_restore_deleted_count=len(post_deleted),
                post_restore_deletion_requests=post_req_count,
                post_restore_digest_verified=digest_ok,
                pre_restore_resurrected_count=len(pre_resurrected),
                pre_restore_deletion_requests=pre_req_count,
            )
        finally:
            for dsn, role in ((src_dsn, src_role), (post_dsn, post_role), (pre_dsn, pre_role)):
                try:
                    await _drop_role_in_db(dsn, role)
                except Exception:  # noqa: BLE001 - best-effort cleanup on failed setup
                    pass
            for db_name in (src_db, post_db, pre_db):
                await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))
            for role in (src_role, post_role, pre_role):
                await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rehearse pg_dump / pg_restore against isolated temporary databases.")
    parser.add_argument(
        "--source-dsn",
        default=os.environ.get("CME_TEST_DATABASE_URL", ""),
        help="PostgreSQL connection string with BYPASSRLS/SUPERUSER privilege (defaults to CME_TEST_DATABASE_URL).",
    )
    parser.add_argument("--schema", default="cme_engine", help="Target engine schema name.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point returning 0 on successful rehearsal and non-zero otherwise."""
    args = _parse_args(argv)
    if not args.source_dsn.strip():
        print("cme_test_database_url_required", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(rehearse_restore(args.source_dsn, schema_name=args.schema))
    except Exception as error:  # noqa: BLE001 - never echo connection strings
        print(f"rehearsal_failed:{type(error).__name__}:{str(error).splitlines()[0][:120]}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(report)))
    else:
        Console().print_json(data=asdict(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
