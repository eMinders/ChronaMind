"""Tenant integrity enforced by the database itself (migration 4, V2-D54).

Each attack runs through the restricted runtime role. Every test has a control on the schema as it was
before migration 4 (``through=3``), where the same attack succeeds, so a pass proves the fix and not a
weaker attack. The two catalog rules at the end bind every future migration (including W1's).
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege, RaiseException

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput, commit_initial_version
from custom_memory_engine.schema import migrate
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

MAR10 = datetime(2026, 3, 10, tzinfo=UTC)
type Env = tuple[AsyncConnection, str, str]


@asynccontextmanager
async def _environment(through: int | None) -> AsyncIterator[Env]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable test database")
    url = DatabaseConfig.from_environment().database_url.get_secret_value()
    schema, role = "cme_ti_" + uuid4().hex[:16], "cme_rt_" + uuid4().hex[:12]
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET timezone = 'UTC'")
        await apply_migrations(conn, through=through)
        # The control schemas predate migration 9, but the engine now writes `event_range`; rename so the control can
        # still drive the engine (the controls are about foreign keys and trigger search paths, not clocks).
        await conn.execute(
            "DO $r$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_version' AND column_name = 'valid_range') THEN "
            "ALTER TABLE memory_version RENAME COLUMN valid_range TO event_range; END IF; END $r$"
        )
        await provision_runtime_role(conn, schema, role)
        await conn.execute("INSERT INTO predicate_schema VALUES ('fact', 'multi', 'semantic')")
        for user in ("u1", "u2"):
            await _seed(conn, schema, user)
        try:
            yield conn, schema, role
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def _seed(conn: AsyncConnection, schema: str, user: str) -> None:
    await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, f"ext-{user}", MAR10))
    await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", (f"s-{user}", user, MAR10))
    await conn.execute(
        "INSERT INTO turn VALUES (%s, %s, %s, 1, 'user', %s, %s)", (f"t-{user}", f"s-{user}", user, "c", MAR10)
    )
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await _version(conn, schema, user, f"v-{user}", f"t-{user}")


async def _version(conn: AsyncConnection, schema: str, user: str, version_id: str, turn_id: str) -> None:
    await commit_initial_version(
        conn,
        CandidateVersionInput(
            version_id=version_id,
            memory_key=f"{user}:{version_id}",
            user_id=user,
            subject_key="user",
            predicate_key="fact",
            single_valued=False,
            content="c",
            valid_from=MAR10,
            evidence=(EvidenceInput(turn_id=turn_id, evidence_span="c"),),
        ),
        schema_name=schema,
        replay_time=MAR10,
    )


@pytest_asyncio.fixture
async def fixed() -> AsyncIterator[Env]:
    async with _environment(None) as env:
        yield env


@pytest_asyncio.fixture
async def before_fix() -> AsyncIterator[Env]:
    async with _environment(3) as env:
        yield env


async def _cite(env: Env, turn_id: str) -> str:
    """u2 commits a version citing ``turn_id``; return the outcome class only."""
    conn, schema, role = env
    try:
        async with tenant_scope(conn, "u2", role=role):
            await _version(conn, schema, "u2", "v-u2-" + uuid4().hex[:8], turn_id)
    except ForeignKeyViolation as error:
        return f"rejected:{error.diag.constraint_name}"
    return "accepted"


async def _derive_from_u1(env: Env) -> None:
    conn, _, role = env
    async with tenant_scope(conn, "u2", role=role):
        await conn.execute("INSERT INTO memory_derivation VALUES ('v-u2', 'v-u1', 'u2', 'summary')")


async def test_another_tenants_turn_is_indistinguishable_from_a_missing_one(fixed: Env) -> None:
    other, missing = await _cite(fixed, "t-u1"), await _cite(fixed, "t-nobody")
    assert other == missing == "rejected:memory_evidence_turn_same_tenant"


async def test_control_before_migration_4_the_reference_is_accepted_and_is_an_oracle(before_fix: Env) -> None:
    assert await _cite(before_fix, "t-u1") == "accepted"
    assert (await _cite(before_fix, "t-nobody")).startswith("rejected:")


async def test_another_tenant_cannot_block_a_users_deletion(fixed: Env) -> None:
    conn, schema, role = fixed
    with pytest.raises(ForeignKeyViolation):
        await _derive_from_u1(fixed)
    async with tenant_scope(conn, "u1", role=role):
        receipt = await delete_memory(conn, "u1", scope="all", schema_name=schema, replay_time=MAR10)
    assert receipt.deleted_versions == 1
    cursor = await conn.execute("SELECT count(*) FROM memory_version WHERE user_id = 'u1'")
    assert (await cursor.fetchone() or (None,))[0] == 0


async def test_control_before_migration_4_the_deletion_is_blocked(before_fix: Env) -> None:
    conn, schema, role = before_fix
    await _derive_from_u1(before_fix)
    with pytest.raises(ForeignKeyViolation):
        async with tenant_scope(conn, "u1", role=role):
            await delete_memory(conn, "u1", scope="all", schema_name=schema, replay_time=MAR10)


async def _shadowed_evidence_insert(env: Env) -> None:
    conn, schema, role = env
    async with tenant_scope(conn, "u1", role=role):
        await conn.execute("CREATE TEMP TABLE memory_evidence (version_id text) ON COMMIT DROP")
        await conn.execute("INSERT INTO pg_temp.memory_evidence VALUES ('v-fake')")
        await conn.execute(
            sql.SQL(
                "INSERT INTO {} (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
                "content, event_range, recorded_range) VALUES ('v-fake', 'k', 'u1', 'active', 'user', 'fact', "
                "false, 'no evidence', tstzrange(%s, NULL), tstzrange({}.clock_now(), NULL))"
            ).format(sql.Identifier(schema, "memory_version"), sql.Identifier(schema)),
            (MAR10,),
        )


async def test_a_temporary_table_cannot_shadow_an_engine_table_inside_a_trigger(fixed: Env) -> None:
    with pytest.raises(RaiseException, match="requires_evidence_or_derivation"):
        await _shadowed_evidence_insert(fixed)


async def test_control_before_migration_4_shadowing_bypasses_the_provenance_invariant(before_fix: Env) -> None:
    await _shadowed_evidence_insert(before_fix)
    cursor = await before_fix[0].execute("SELECT count(*) FROM memory_version WHERE id = 'v-fake'")
    assert (await cursor.fetchone() or (None,))[0] == 1


async def test_audit_and_deletion_receipts_cannot_be_rewritten_by_the_runtime(fixed: Env) -> None:
    conn, _, role = fixed
    for statement in (
        "UPDATE audit_event SET details = '{}'::jsonb",
        "DELETE FROM audit_event",
        "DELETE FROM deletion_request",
        "UPDATE deletion_request SET status = 'failed'",
    ):
        with pytest.raises(InsufficientPrivilege):
            async with tenant_scope(conn, "u1", role=role):
                await conn.execute(cast(LiteralString, statement))


async def test_rule_every_foreign_key_between_tenant_tables_carries_user_id(fixed: Env) -> None:
    """Catalog rule: if both tables have ``user_id``, the key must include it on both sides."""
    conn, schema, _ = fixed
    cursor = await conn.execute(
        "SELECT con.conname FROM pg_catalog.pg_constraint con "
        "JOIN pg_catalog.pg_class child ON child.oid = con.conrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = child.relnamespace "
        "WHERE con.contype = 'f' AND n.nspname = %(s)s "
        "AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid = con.confrelid AND attname = 'user_id') "
        "AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid = con.conrelid AND attname = 'user_id') "
        "AND NOT ("
        "  (SELECT attnum FROM pg_catalog.pg_attribute WHERE attrelid = con.conrelid AND attname = 'user_id')"
        "    = ANY (con.conkey) AND "
        "  (SELECT attnum FROM pg_catalog.pg_attribute WHERE attrelid = con.confrelid AND attname = 'user_id')"
        "    = ANY (con.confkey)) ORDER BY 1",
        {"s": schema},
    )
    assert await cursor.fetchall() == []


async def test_rule_every_engine_function_pins_search_path_with_pg_temp_last(fixed: Env) -> None:
    conn, schema, _ = fixed
    cursor = await conn.execute(
        "SELECT p.proname, p.proconfig FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %s ORDER BY 1",
        (schema,),
    )
    functions = await cursor.fetchall()
    assert {name for name, _ in functions} == set(migrate.FUNCTIONS)
    expected = f"search_path={schema}, pg_catalog, pg_temp"
    assert [name for name, config in functions if expected not in (config or [])] == []
