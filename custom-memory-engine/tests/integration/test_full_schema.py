"""RL-040 full-schema seams: apply DDL, invariants 1/7/9, and the 43 bitemporal assertions."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, InvalidParameterValue, RaiseException, UniqueViolation

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.schema import migrate

pytestmark = pytest.mark.integration

CME_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = CME_ROOT / "src" / "custom_memory_engine" / "schema" / "001_full_schema.sql"
CHECKS_SQL = CME_ROOT / "src" / "custom_memory_engine" / "schema" / "bitemporal_on_full.sql"
README = CME_ROOT / "src" / "custom_memory_engine" / "schema" / "README.md"
USER_DATA_TABLES = (
    "app_user",
    "session",
    "turn",
    "memory_version",
    "memory_evidence",
    "memory_derivation",
    "memory_embedding",
    "memory_conflict",
    "audit_event",
    "deletion_request",
    "job",
)
CANONICAL_TABLES = USER_DATA_TABLES + ("predicate_schema",)
CLOCK = datetime(2026, 3, 10, tzinfo=UTC)


def strip_psql_meta_commands(script: str) -> str:
    """Drop psql backslash meta-commands so the script is valid for a driver."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("\\"))


def split_sql(script: str) -> list[str]:
    """Split a PostgreSQL script on top-level semicolons, respecting dollar quotes."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar: str | None = None
    in_single = False
    in_line_comment = False
    in_block_comment = False
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if dollar is not None:
            buf.append(ch)
            if script.startswith(dollar, i):
                buf.append(script[i + 1 : i + len(dollar)])
                i += len(dollar)
                dollar = None
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "-" and nxt == "-":
            buf.extend(["-", "-"])
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            buf.extend(["/", "*"])
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            buf.append(ch)
            in_single = True
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < n and script[j] == "$":
                dollar = script[i : j + 1]
                buf.append(dollar)
                i = j + 1
                continue
        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [item for item in statements if not item.startswith("\\")]


def read_script(path: Path) -> list[str]:
    return split_sql(strip_psql_meta_commands(path.read_text(encoding="utf-8")))


async def apply_sql(conn: AsyncConnection, statements: list[str]) -> None:
    for statement in statements:
        await conn.execute(cast(LiteralString, statement))


# The whole migration chain, so the 43 bitemporal assertions and the invariant tests run on the schema the engine uses
# (they replay bitemporal_on_full.sql, which names the V2 columns).
SCHEMA_STATEMENTS = [
    statement for _, filename in migrate.MIGRATIONS for statement in read_script(SCHEMA_SQL.with_name(filename))
]
CHECK_STATEMENTS = read_script(CHECKS_SQL) if CHECKS_SQL.is_file() else []


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist installed")
    config = DatabaseConfig.from_environment()
    schema = "cme_full_" + uuid4().hex
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '30s'")
        yield conn


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_schema_readme_maps_invariants_1_to_10(readme_text: str) -> None:
    for invariant in range(1, 11):
        assert f"Invariant {invariant}" in readme_text


def test_split_sql_keeps_function_bodies_intact() -> None:
    script = "CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$;\nSELECT 1;"
    assert split_sql(script) == [
        "CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$",
        "SELECT 1",
    ]


def test_strip_psql_meta_commands_keeps_sql_lines() -> None:
    script = "\\echo 'hello'\nSELECT 1;\n  \\echo 'nested'\nSELECT 2;"
    assert strip_psql_meta_commands(script) == "SELECT 1;\nSELECT 2;"


@pytest.mark.parametrize("path", [SCHEMA_SQL, CHECKS_SQL, README])
def test_deliverable_files_exist(path: Path) -> None:
    assert path.is_file()


async def test_full_schema_creates_twelve_tables_and_named_constraints(
    database: tuple[DatabaseConfig, str],
) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        cursor = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename"
        )
        names = {row[0] for row in await cursor.fetchall()}
        # the twelve canonical tables, plus what later migrations add (scope grants, the clock watermark)
        assert names == set(CANONICAL_TABLES) | {
            "scope_grant",
            "clock_watermark",
            "method",
            "association",
            "entity",
            "abstraction_stats",
            "verification",
            "negative_constraint",
            "api_idempotency",
            "scope",
            "entity_link",
        }
        cursor = await conn.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'memory_version'::regclass
              AND conname IN ('valid_wellformed','recorded_iff_believed','recorded_wellformed','no_bitemporal_overlap')
            """
        )
        assert {row[0] for row in await cursor.fetchall()} == {
            "valid_wellformed",
            "recorded_iff_believed",
            "recorded_wellformed",
            "no_bitemporal_overlap",
        }


async def test_row_level_security_is_forced_on_user_data_tables(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        cursor = await conn.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname = ANY(%s)
            ORDER BY c.relname
            """,
            (list(USER_DATA_TABLES),),
        )
        rows = await cursor.fetchall()
        assert [row[0] for row in rows] == sorted(USER_DATA_TABLES)
        assert all(row[1] is True and row[2] is True for row in rows)


async def test_job_idempotency_unique_constraints(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        await _seed_user_graph(conn)
        await conn.execute(
            """
            INSERT INTO job (id, user_id, kind, turn_id, idempotency_key, status, next_run_at,
                             extractor_version, candidate_fingerprint)
            VALUES ('j1','u1','extract','t1','key-1','queued', %s, 'ex-1', 'fp-1')
            """,
            (CLOCK,),
        )
        with pytest.raises(UniqueViolation):
            await conn.execute(
                """
                INSERT INTO job (id, user_id, kind, turn_id, idempotency_key, status, next_run_at,
                                 extractor_version, candidate_fingerprint)
                VALUES ('j2','u1','extract','t1','key-2','queued', %s, 'ex-1', 'fp-1')
                """,
                (CLOCK,),
            )
        with pytest.raises(UniqueViolation):
            await conn.execute(
                """
                INSERT INTO job (id, user_id, kind, turn_id, idempotency_key, status, next_run_at)
                VALUES ('j3','u1','extract','t1','key-1','queued', %s)
                """,
                (CLOCK,),
            )


async def _seed_user_graph(conn: AsyncConnection) -> None:
    await conn.execute("INSERT INTO app_user (id, external_ref, created_at) VALUES ('u1','ext-u1', %s)", (CLOCK,))
    await conn.execute(
        """
        INSERT INTO predicate_schema (predicate_key, cardinality, default_layer)
        VALUES ('diet','single','semantic'), ('diet-summary','multi','semantic')
        """
    )
    await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s1','u1', %s)", (CLOCK,))
    await conn.execute(
        """
        INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at)
        VALUES ('t1','s1','u1',1,'user','I am vegetarian', %s)
        """,
        (CLOCK,),
    )


async def test_invariant_1_rejects_active_row_without_provenance(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        await _seed_user_graph(conn)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (CLOCK.isoformat(),))
        with pytest.raises((RaiseException, CheckViolation)):
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO memory_version (
                        id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                        event_range, recorded_range
                    ) VALUES (
                        'v1','diet','u1','active','user','diet',true,'vegetarian',
                        tstzrange(%s, NULL), tstzrange(%s, NULL)
                    )
                    """,
                    (CLOCK, CLOCK),
                )


async def test_invariant_1_accepts_evidence_or_derivation(database: tuple[DatabaseConfig, str]) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        await _seed_user_graph(conn)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (CLOCK.isoformat(),))
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO memory_version (
                    id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                    event_range, recorded_range
                ) VALUES (
                    'v1','diet','u1','active','user','diet',true,'vegetarian',
                    tstzrange(%s, NULL), tstzrange(%s, NULL)
                )
                """,
                (CLOCK, CLOCK),
            )
            await conn.execute(
                """
                INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role)
                VALUES ('v1','t1','u1','I am vegetarian','support')
                """
            )
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO memory_version (
                    id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                    event_range, recorded_range, layer
                ) VALUES (
                    'v2','diet-summary','u1','active','user','diet-summary',false,'vegetarian (derived)',
                    tstzrange(%s, NULL), tstzrange(%s, NULL), 'semantic'
                )
                """,
                (CLOCK, CLOCK),
            )
            await conn.execute(
                """
                INSERT INTO memory_derivation (derived_version_id, source_version_id, user_id, derivation_type)
                VALUES ('v2','v1','u1','summary')
                """
            )
        cursor = await conn.execute("SELECT id FROM memory_version ORDER BY id")
        assert [row[0] for row in await cursor.fetchall()] == ["v1", "v2"]


async def test_guard_update_rejects_content_edit_and_allows_archival_branch(
    database: tuple[DatabaseConfig, str],
) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        await _seed_user_graph(conn)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (CLOCK.isoformat(),))
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO memory_version (
                    id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                    event_range, recorded_range
                ) VALUES (
                    'v1','diet','u1','active','user','diet',true,'vegetarian',
                    tstzrange(%s, NULL), tstzrange(%s, NULL)
                )
                """,
                (CLOCK, CLOCK),
            )
            await conn.execute(
                """
                INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role)
                VALUES ('v1','t1','u1','I am vegetarian','support')
                """
            )
        with pytest.raises(RaiseException, match="only status"):
            await conn.execute("UPDATE memory_version SET content = 'x' WHERE id = 'v1'")
        with pytest.raises(RaiseException, match="archival_branch"):
            await conn.execute("UPDATE memory_version SET archival_branch = 'S' WHERE id = 'v1'")
        # Invariant 10 (T1): a currently-valid belief has no (S) and no (C) replacement.
        with pytest.raises(InvalidParameterValue, match="archival_precondition_unmet"):
            await conn.execute("UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'v1'")
        # Branch (C): an active, currently-believed derived version covering V(v1).
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO memory_version (
                    id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
                    event_range, recorded_range
                ) VALUES (
                    'v1s','diet','u1','active','user','diet-summary',true,'vegetarian (summary)',
                    tstzrange(%s, NULL), tstzrange(%s, NULL)
                )
                """,
                (CLOCK, CLOCK),
            )
            await conn.execute(
                """
                INSERT INTO memory_derivation (derived_version_id, source_version_id, user_id, derivation_type)
                VALUES ('v1s','v1','u1','summary')
                """
            )
        await conn.execute("UPDATE memory_version SET status = 'archived', archival_branch = 'C' WHERE id = 'v1'")
        cursor = await conn.execute("SELECT status, archival_branch FROM memory_version WHERE id = 'v1'")
        assert await cursor.fetchone() == ("archived", "C")


async def test_forty_three_bitemporal_assertions_pass_against_full_memory_version(
    database: tuple[DatabaseConfig, str],
) -> None:
    async with connect(database) as conn:
        await apply_sql(conn, SCHEMA_STATEMENTS)
        passes = 0
        for statement in CHECK_STATEMENTS:
            cursor = await conn.execute(cast(LiteralString, statement))
            if cursor.description is None:
                continue
            rows = await cursor.fetchall()
            for row in rows:
                if row and isinstance(row[0], str) and row[0].startswith("PASS"):
                    passes += 1
        assert passes == 43
        cursor = await conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'trg_memory_version_provenance'")
        assert await cursor.fetchone() == ("O",), "replay must re-enable the provenance trigger"
