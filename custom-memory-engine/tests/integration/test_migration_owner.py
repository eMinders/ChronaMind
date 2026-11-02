"""A populated schema must be upgradeable by the owner that owns it, with no RLS bypass.

The migration sources are checksum-bound, so this is a property of the runner, not of one migration. The suite's usual
connection is a superuser, which bypasses row-level security and hides the whole failure mode: ``FORCE ROW LEVEL
SECURITY`` binds the *owner*, so a backfill's unqualified ``UPDATE``/``INSERT ... SELECT`` matches zero rows and the
following ``SET NOT NULL`` refuses the migration. Migrations 5, 8, 9, 10 and 11 all backfill that way.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import adapt

pytestmark = pytest.mark.integration


def _disposable_dsn() -> str:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable migration test database")
    return DatabaseConfig.from_environment().database_url.get_secret_value()


@asynccontextmanager
async def ordinary_owner(schema: str) -> AsyncIterator[str]:
    """A role that owns ``schema`` and does not bypass RLS; yields the DSN that connects as it."""
    admin_dsn = _disposable_dsn()
    parameters = conninfo_to_dict(admin_dsn)
    database_name = str(parameters["dbname"])
    owner = "cme_owner_" + uuid4().hex[:12]
    password = uuid4().hex
    owner_dsn = make_conninfo(**{**parameters, "user": owner, "password": password})
    async with await AsyncConnection.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
        await admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(owner), sql.Literal(password)
            )
        )
        await admin.execute(
            sql.SQL("GRANT CREATE, CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), sql.Identifier(owner)
            )
        )
        await admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(owner)))
    try:
        yield owner_dsn
    finally:
        async with await AsyncConnection.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            await admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            await admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                    sql.Identifier(database_name), sql.Identifier(owner)
                )
            )
            await admin.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(owner)))
            await admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(owner)))
            await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))


async def seed_v1_data(owner_dsn: str, schema: str) -> None:
    """One user, one turn and one evidenced version: the shape every later backfill has to reach."""
    async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await conn.execute("SELECT set_config('app.clock', '2026-07-20T00:00:00+00:00', true)")
            await conn.execute("INSERT INTO app_user (id, external_ref, created_at) VALUES ('u1', 'x', clock_now())")
            await conn.execute("INSERT INTO predicate_schema VALUES ('diet', 'single', 'semantic')")
            await conn.execute("INSERT INTO session VALUES ('s1', 'u1', clock_now())")
            await conn.execute("INSERT INTO turn VALUES ('t1', 's1', 'u1', 1, 'user', 'I am vegetarian', clock_now())")
            await conn.execute(
                await adapt(
                    conn,
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, recorded_range) VALUES ('src', 'mk-src', 'u1', "
                    "'active', 'u1', 'diet', false, 'vegetarian', tstzrange(clock_now(), NULL), 'semantic', "
                    "tstzrange(clock_now(), NULL))",
                )
            )
            await conn.execute("INSERT INTO memory_evidence VALUES ('src', 't1', 'u1', 'vegetarian', 'support')")


@asynccontextmanager
async def schema_at(stop_at: int) -> AsyncIterator[tuple[migrate.MigrationRunner, str, str]]:
    """A schema owned by an ordinary role with migrations 1..``stop_at`` applied and v1-shaped data seeded."""
    schema = "cme_owner_" + uuid4().hex[:12]
    async with ordinary_owner(schema) as owner_dsn:
        runner = migrate.MigrationRunner(
            migrate.MigrationConfig(database_url=SecretStr(owner_dsn), schema_name=schema)
        )
        original = migrate.MIGRATIONS
        try:
            migrate.MIGRATIONS = tuple(entry for entry in original if entry[0] <= stop_at)
            installed = await runner.apply()
        finally:
            migrate.MIGRATIONS = original
        assert installed.status.version == stop_at
        await seed_v1_data(owner_dsn, schema)
        yield runner, owner_dsn, schema


async def rows(conn: AsyncConnection, statement: str) -> list[tuple[object, ...]]:
    cursor = await conn.execute(cast(LiteralString, statement), prepare=False)
    return [tuple(row) for row in await cursor.fetchall()]


@pytest.mark.parametrize("upgrade_to", [5, 8, 9, 10, 11])
async def test_every_backfilling_migration_upgrades_a_populated_schema_for_a_non_bypass_owner(upgrade_to: int) -> None:
    """Migrations 5, 8, 9, 10 and 11 carry existing rows forward when the owner cannot bypass RLS.

    5 backfills the axes then runs `SET NOT NULL`; 8 backfills the generalised source then runs `SET NOT NULL`; 9
    backfills `observed_at`/`event_basis` (also `NOT NULL`); 10 backfills the method reference and the ledgers; 11
    backfills the scope path and visibility (also `NOT NULL`). Each matched zero rows before the runner lifted `FORCE`,
    so the migration refused and the schema stayed behind. Data is seeded while the schema is at ``upgrade_to - 1``, so
    the backfill under test always has rows to reach.
    """
    async with schema_at(upgrade_to - 1) as (runner, owner_dsn, schema):
        result = await runner.apply()
        assert (result.action, result.status.state) == ("applied", "applied")
        assert result.status.version == migrate.VERSION
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
            if upgrade_to == 5:
                assert await rows(
                    conn, "SELECT count(*) FROM memory_version WHERE kind IS NULL OR lifecycle IS NULL"
                ) == [(0,)], "migration 5's axes backfill reached no rows"
            if upgrade_to == 8:
                assert await rows(conn, "SELECT count(*) FROM memory_evidence WHERE source_type IS NULL") == [(0,)], (
                    "migration 8's source backfill reached no rows"
                )
            if upgrade_to == 9:
                assert await rows(
                    conn, "SELECT count(*) FROM memory_version WHERE observed_at IS NULL OR event_basis IS NULL"
                ) == [(0,)], "migration 9's clocks backfill reached no rows"
            if upgrade_to == 10:
                assert await rows(conn, "SELECT method_id FROM memory_version") == [(None,)]
            if upgrade_to == 11:
                for table in ("session", "turn", "memory_version", "memory_evidence"):
                    assert await rows(conn, f"SELECT scope_path, visibility FROM {table}") == [
                        ("/u/u1/", "private")
                    ], "migration 11's scope backfill reached no rows"
            # The lift covers the body only: every relation that had FORCE has it again. `clock_watermark` is
            # deliberately non-forced (migration 9), so it must not be forced by the runner either.
            unprotected = await rows(
                conn,
                f"""SELECT c.relname FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='{schema}' AND c.relkind='r' AND c.relrowsecurity
                  AND NOT c.relforcerowsecurity AND c.relname <> 'clock_watermark'""",
            )
            assert unprotected == [], "the runner did not restore FORCE ROW LEVEL SECURITY"
        status = await runner.status()
        assert (status.state, status.version) == ("applied", migrate.VERSION), "recorded catalog != live catalog"


async def test_the_upgraded_rows_carry_the_axes_the_later_migrations_own() -> None:
    async with schema_at(4) as (runner, owner_dsn, schema):
        assert (await runner.apply()).status.version == migrate.VERSION
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
            assert await rows(conn, "SELECT kind, lifecycle, retention, method_id IS NULL FROM memory_version") == [
                ("claim", "asserted", "durable", True)
            ]
            assert await rows(conn, "SELECT version FROM _cme_schema_migrations ORDER BY version DESC LIMIT 1") == [
                (migrate.VERSION,)
            ]


async def test_a_refused_upgrade_leaves_force_restored_and_the_history_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lift lives inside the transaction: a failing body rolls back with the lift, leaving nothing unprotected."""
    async with schema_at(4) as (runner, owner_dsn, schema):
        monkeypatch.setattr(migrate, "MIGRATIONS", (*migrate.MIGRATIONS, (99, "009_clocks.sql")))
        with pytest.raises(migrate.MigrationError):
            await runner.apply()
        async with await AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            assert await rows(
                conn,
                f"""SELECT count(*) FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='{runner.config.schema_name}' AND c.relkind='r' AND c.relrowsecurity
                      AND NOT c.relforcerowsecurity AND c.relname <> 'clock_watermark'""",
            ) == [(0,)]
            assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(4,)]
