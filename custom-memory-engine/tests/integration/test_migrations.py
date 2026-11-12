"""RL-041 public migration behavior against independently inspected PostgreSQL state."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import ForeignKeyViolation
from pydantic import SecretStr, ValidationError

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.schema import migrate

CME_ROOT = Path(__file__).resolve().parents[2]
CANARY = "private-migration-test-canary"
EXPLICIT_URL = f"postgresql://cme_migrator:{CANARY}@127.0.0.1:5433/cme_lab"
DOMAIN_TABLES = frozenset(
    {
        "app_user",
        "session",
        "turn",
        "predicate_schema",
        "memory_version",
        "memory_evidence",
        "memory_derivation",
        "memory_embedding",
        "memory_conflict",
        "audit_event",
        "deletion_request",
        "job",
        "scope_grant",
        "clock_watermark",
        "method",
        "association",
        "entity",
        "abstraction_stats",
        "verification",
        "negative_constraint",
        "api_idempotency",
        "entity_link",
        "outcome_stats",
    }
)
HISTORY_TABLE = "_cme_schema_migrations"


def test_runtime_configuration_uses_only_explicit_research_url_and_redacts_it() -> None:
    config = migrate.MigrationConfig.from_environment(
        {"RL_DATABASE_URL": EXPLICIT_URL, "DATABASE_URL": "product-do-not-use", "CME_DATABASE_URL": "other"},
        schema_name="cme_test_scope",
    )
    assert config.database_url.get_secret_value() == EXPLICIT_URL
    assert config.schema_name == "cme_test_scope"
    assert CANARY not in repr(config)
    assert (config.connect_timeout, config.statement_timeout_ms, config.lock_timeout_ms) == (5, 30000, 5000)
    with pytest.raises(ValidationError):
        migrate.MigrationConfig.from_environment({"DATABASE_URL": EXPLICIT_URL, "CME_DATABASE_URL": EXPLICIT_URL})


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        f"postgresql://cme_migrator:{CANARY}@127.0.0.1/postgres",
        f"postgresql://cme_migrator:{CANARY}@127.0.0.1/template0",
        f"postgresql://cme_migrator:{CANARY}@127.0.0.1/template1",
        f"postgresql://cme_migrator:{CANARY}@127.0.0.1",
        "postgresql://127.0.0.1/cme_lab",
        "postgresql:///cme_lab?user=cme_migrator",
        "service=implicit-target",
        "sqlite:///cme_lab",
    ],
)
def test_configuration_rejects_implicit_or_system_database_targets_without_secret_echo(database_url: str) -> None:
    with pytest.raises(ValidationError) as failure:
        migrate.MigrationConfig(database_url=SecretStr(database_url))
    assert CANARY not in str(failure.value)


@pytest.mark.parametrize(
    "schema_name", ["public", "pg_catalog", "CME_upper", "cme bad", "cme_x;DROP", "cme_" + "x" * 60]
)
def test_configuration_rejects_unmanaged_or_invalid_schema_names(schema_name: str) -> None:
    with pytest.raises(ValidationError):
        migrate.MigrationConfig(database_url=SecretStr(EXPLICIT_URL), schema_name=schema_name)


def test_source_checksum_is_canonical_across_checkout_line_endings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "001_full_schema.sql"
    source = "-- source fixture\nSELECT 'é';\n"
    monkeypatch.setattr(migrate, "SCHEMA_SQL", path)
    for newline in (b"\n", b"\r\n"):
        path.write_bytes(source.encode("utf-8").replace(b"\n", newline))
        actual, checksum = migrate._read_source()
        assert actual == source
        assert checksum == hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_constructor_does_not_open_a_database_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden_connection(*args, **kwargs):
        pytest.fail("migration construction must remain local")

    monkeypatch.setattr(AsyncConnection, "connect", forbidden_connection)
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=SecretStr(EXPLICIT_URL)))
    assert runner is not None


@pytest.mark.parametrize("updates", [{"schema_name": "public"}, {"connect_timeout": 0}, {"lock_timeout_ms": True}])
def test_runner_revalidates_copied_configuration_before_any_connection(updates: dict[str, object]) -> None:
    config = migrate.MigrationConfig(database_url=SecretStr(EXPLICIT_URL))
    copied = config.model_copy(update=updates)
    with pytest.raises(ValidationError):
        migrate.MigrationRunner(copied)


def _cli_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "WINDIR"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONPATH"] = os.pathsep.join((str(CME_ROOT), str(CME_ROOT / "src")))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def test_cli_help_needs_no_credentials_and_invalid_configuration_is_content_free() -> None:
    environment = _cli_environment()
    command = [sys.executable, "-m", "custom_memory_engine.schema.migrate"]
    help_result = subprocess.run(
        [*command, "--help"], cwd=CME_ROOT, env=environment, capture_output=True, text=True, timeout=15, check=False
    )
    assert help_result.returncode == 0
    assert all(name in help_result.stdout for name in ("apply", "status", "reset"))
    environment["RL_DATABASE_URL"] = f"postgresql://cme_migrator:{CANARY}@127.0.0.1/postgres"
    invalid = subprocess.run(
        [*command, "status"], cwd=CME_ROOT, env=environment, capture_output=True, text=True, timeout=15, check=False
    )
    assert invalid.returncode == 1
    assert CANARY not in invalid.stdout + invalid.stderr
    assert "Traceback" not in invalid.stderr
    assert json.loads(invalid.stderr.strip())


@pytest_asyncio.fixture
async def database() -> AsyncIterator[migrate.MigrationConfig]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable migration test database")
    disposable = DatabaseConfig.from_environment()
    schema_name = "cme_migrations_" + uuid4().hex
    config = migrate.MigrationConfig(database_url=disposable.database_url, schema_name=schema_name)
    try:
        yield config
    finally:
        # This exact UUID name was generated above for this fixture. Never scan
        # or remove any schema selected by environment or migration output.
        async with await AsyncConnection.connect(
            disposable.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))


@pytest_asyncio.fixture
async def fresh_database() -> AsyncIterator[migrate.MigrationConfig]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable migration test database")
    disposable = DatabaseConfig.from_environment()
    database_name = "cme_migration_bootstrap_" + uuid4().hex
    created = False
    async with await AsyncConnection.connect(
        disposable.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as admin:
        await admin.execute("SET statement_timeout = '30s'")
        try:
            # template0 excludes the extensions already installed in the shared
            # compatibility DB, exercising first-ever provisioning instead.
            await admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name)))
            created = True
            database_url = make_conninfo(disposable.database_url.get_secret_value(), dbname=database_name)
            yield migrate.MigrationConfig(database_url=SecretStr(database_url))
        finally:
            # Every connection to this exact generated DB is closed by the
            # runner/test contexts before teardown. Do not drop other databases.
            if created:
                await admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


@pytest.mark.integration
async def test_cli_apply_status_and_confirmed_reset_use_the_explicit_database(
    database: migrate.MigrationConfig,
) -> None:
    environment = _cli_environment()
    environment["RL_DATABASE_URL"] = database.database_url.get_secret_value()

    async def invoke(command: str, *arguments: str) -> dict:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "custom_memory_engine.schema.migrate",
                command,
                "--schema",
                database.schema_name,
                *arguments,
            ],
            cwd=CME_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # Do not echo child output on failure: the real fixture DSN is secret.
        assert result.returncode == 0, f"migration CLI {command} failed with exit {result.returncode}"
        assert database.database_url.get_secret_value() not in result.stdout + result.stderr
        return json.loads(result.stdout)

    assert (await invoke("status"))["state"] == "unapplied"
    applied = await invoke("apply")
    assert applied["action"] == "applied" and applied["status"]["version"] == migrate.VERSION
    async with connect(database) as conn:
        await seed_user(conn)
        original = await table_oids(conn, database.schema_name)
        assert (await invoke("apply"))["action"] == "unchanged"
        assert await table_oids(conn, database.schema_name) == original
        await assert_seed_retained(conn)
        status = await invoke("status")
        assert status["state"] == "applied" and status["version"] == migrate.VERSION
        reset = await invoke("reset", "--confirm-schema", database.schema_name)
        assert reset["action"] == "reset" and reset["status"]["state"] == "applied"
        assert set(await table_oids(conn, database.schema_name)) == DOMAIN_TABLES | {HISTORY_TABLE}
        cursor = await conn.execute("SELECT count(*) FROM app_user")
        assert await cursor.fetchone() == (0,)


@asynccontextmanager
async def connect(config: migrate.MigrationConfig) -> AsyncIterator[AsyncConnection]:
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(config.schema_name)))
        await conn.execute("SET statement_timeout = '10s'")
        yield conn


async def table_oids(conn: AsyncConnection, schema_name: str) -> dict[str, int]:
    cursor = await conn.execute(
        "SELECT relname, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relkind='r' ORDER BY relname",
        (schema_name,),
    )
    return dict(await cursor.fetchall())


async def seed_user(conn: AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO app_user (id,external_ref,created_at) VALUES ('retained-user','retained-external',now())"
    )


async def assert_seed_retained(conn: AsyncConnection) -> None:
    cursor = await conn.execute("SELECT id,external_ref FROM app_user ORDER BY id")
    assert await cursor.fetchall() == [("retained-user", "retained-external")]


@pytest.mark.integration
async def test_status_is_read_only_and_apply_is_idempotent_without_recreating_tables(
    database: migrate.MigrationConfig,
) -> None:
    read_only_url = make_conninfo(
        database.database_url.get_secret_value(), options="-c default_transaction_read_only=on"
    )
    read_only = migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=SecretStr(read_only_url), schema_name=database.schema_name)
    )
    status = await read_only.status()
    assert status.state == "unapplied" and status.version is None and status.checksum_sha256 is None
    async with connect(database) as conn:
        cursor = await conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (database.schema_name,))
        assert await cursor.fetchone() is None
    runner = migrate.MigrationRunner(database)
    first = await runner.apply()
    assert first.action == "applied" and first.status.state == "applied" and first.status.version == migrate.VERSION
    assert first.status.schema_name == database.schema_name
    assert len(first.status.checksum_sha256 or "") == 64
    async with connect(database) as conn:
        original_oids = await table_oids(conn, database.schema_name)
        assert set(original_oids) == DOMAIN_TABLES | {HISTORY_TABLE}
        await seed_user(conn)
    again = await runner.apply()
    assert again.action == "unchanged" and again.status == first.status
    assert await read_only.status() == first.status
    async with connect(database) as conn:
        assert await table_oids(conn, database.schema_name) == original_oids
        await assert_seed_retained(conn)
        cursor = await conn.execute(
            "SELECT version,filename,checksum_sha256 FROM _cme_schema_migrations ORDER BY version"
        )
        expected = [(version, filename, checksum) for version, filename, _, checksum in migrate._read_sources()]
        assert await cursor.fetchall() == expected
        assert first.status.checksum_sha256 == expected[-1][2]


@pytest.mark.integration
async def test_clean_reset_clears_domain_data_preserves_foreign_schema_and_extensions(
    database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    foreign = "cme_foreign_" + uuid4().hex
    async with connect(database) as conn:
        cursor = await conn.execute(
            "SELECT e.oid,extname,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
            "WHERE extname IN ('vector','btree_gist') ORDER BY extname"
        )
        extensions = await cursor.fetchall()
        assert len(extensions) == 2 and all(row[2] == "public" for row in extensions)
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(foreign)))
        try:
            await conn.execute(sql.SQL("CREATE TABLE {}.sentinel (value text)").format(sql.Identifier(foreign)))
            await conn.execute(sql.SQL("INSERT INTO {}.sentinel VALUES ('keep')").format(sql.Identifier(foreign)))
            await seed_user(conn)
            result = await runner.reset(confirm_schema=database.schema_name)
            assert (
                result.action == "reset"
                and result.status.state == "applied"
                and result.status.version == migrate.VERSION
            )
            assert set(await table_oids(conn, database.schema_name)) == DOMAIN_TABLES | {HISTORY_TABLE}
            # `method` (migration 10: the v1 operators) and `predicate_schema` (migration 13: the generic predicates)
            # are catalogs the migrations seed, not tenant data
            for table in sorted(DOMAIN_TABLES - {"method", "predicate_schema"}):
                cursor = await conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                assert await cursor.fetchone() == (0,), table
            cursor = await conn.execute("SELECT count(*) FROM method")
            assert await cursor.fetchone() == (5,)
            cursor = await conn.execute("SELECT count(*) FROM predicate_schema")
            assert await cursor.fetchone() == (2,)
            cursor = await conn.execute(sql.SQL("SELECT value FROM {}.sentinel").format(sql.Identifier(foreign)))
            assert await cursor.fetchall() == [("keep",)]
            cursor = await conn.execute(
                "SELECT e.oid,extname,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                "WHERE extname IN ('vector','btree_gist') ORDER BY extname"
            )
            assert await cursor.fetchall() == extensions
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(foreign)))


@pytest.mark.integration
async def test_fresh_database_bootstraps_extensions_outside_reset_scope_and_preserves_them(
    fresh_database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(fresh_database)
    async with connect(fresh_database) as conn:
        cursor = await conn.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector','btree_gist')")
        assert await cursor.fetchall() == []
    assert (await runner.status()).state == "unapplied"
    assert (await runner.apply()).action == "applied"
    async with connect(fresh_database) as conn:
        cursor = await conn.execute(
            "SELECT e.oid,extname,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
            "WHERE extname IN ('vector','btree_gist') ORDER BY extname"
        )
        original_extensions = await cursor.fetchall()
        assert len(original_extensions) == 2 and all(row[2] == "public" for row in original_extensions)
    assert (await runner.reset(confirm_schema=fresh_database.schema_name)).action == "reset"
    assert (await runner.status()).state == "applied"
    async with connect(fresh_database) as conn:
        cursor = await conn.execute(
            "SELECT e.oid,extname,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
            "WHERE extname IN ('vector','btree_gist') ORDER BY extname"
        )
        assert await cursor.fetchall() == original_extensions


@pytest.mark.integration
async def test_failed_first_install_rolls_back_extension_bootstrap_and_managed_schema(
    fresh_database: migrate.MigrationConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = migrate.MigrationRunner(fresh_database)
    original = runner._install

    async def fail_after_install(conn, sources):
        await original(conn, sources)
        await conn.execute("SELECT %s::integer", (CANARY,))

    monkeypatch.setattr(runner, "_install", fail_after_install)
    with pytest.raises(migrate.MigrationError) as failure:
        await runner.apply()
    assert failure.value.sqlstate == "22P02" and CANARY not in str(failure.value)
    assert (await runner.status()).state == "unapplied"
    async with connect(fresh_database) as conn:
        cursor = await conn.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector','btree_gist')")
        assert await cursor.fetchall() == []
        cursor = await conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (fresh_database.schema_name,))
        assert await cursor.fetchone() is None


@pytest.mark.integration
async def test_reset_requires_exact_confirmation_without_touching_seeded_data(
    database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await seed_user(conn)
        before = await table_oids(conn, database.schema_name)
        for confirmation in ("", "public", database.schema_name + "_other"):
            with pytest.raises(migrate.MigrationError):
                await runner.reset(confirm_schema=confirmation)
            assert await table_oids(conn, database.schema_name) == before
            await assert_seed_retained(conn)


@pytest.mark.integration
async def test_unmanaged_schema_is_never_adopted_or_erased(database: migrate.MigrationConfig) -> None:
    runner = migrate.MigrationRunner(database)
    async with connect(database) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(database.schema_name)))
        await conn.execute("CREATE TABLE sentinel (value text)")
        await conn.execute("INSERT INTO sentinel VALUES ('keep')")
        before = await table_oids(conn, database.schema_name)
        assert (await runner.status()).state == "unmanaged"
        for operation in (runner.apply, lambda: runner.reset(confirm_schema=database.schema_name)):
            with pytest.raises(migrate.MigrationError):
                await operation()
            assert await table_oids(conn, database.schema_name) == before
            cursor = await conn.execute("SELECT value FROM sentinel")
            assert await cursor.fetchall() == [("keep",)]


@pytest.mark.integration
async def test_failed_apply_rolls_back_schema_and_history_and_redacts_database_error(
    database: migrate.MigrationConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = migrate.MigrationRunner(database)
    original = runner._install

    async def fail_after_install(conn, sources):
        await original(conn, sources)
        await conn.execute("SELECT %s::integer", (CANARY,))

    monkeypatch.setattr(runner, "_install", fail_after_install)
    with pytest.raises(migrate.MigrationError) as failure:
        await runner.apply()
    assert failure.value.sqlstate == "22P02" and CANARY not in str(failure.value)
    assert (await runner.status()).state == "unapplied"
    async with connect(database) as conn:
        cursor = await conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (database.schema_name,))
        assert await cursor.fetchone() is None


@pytest.mark.integration
async def test_failed_reset_restores_original_schema_oids_history_and_data(
    database: migrate.MigrationConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    original = runner._install

    async def fail_after_reinstall(conn, sources):
        await original(conn, sources)
        await conn.execute("SELECT %s::integer", (CANARY,))

    async with connect(database) as conn:
        await seed_user(conn)
        before = await table_oids(conn, database.schema_name)
        monkeypatch.setattr(runner, "_install", fail_after_reinstall)
        with pytest.raises(migrate.MigrationError) as failure:
            await runner.reset(confirm_schema=database.schema_name)
        assert failure.value.sqlstate == "22P02" and CANARY not in str(failure.value)
        assert (await runner.status()).state == "applied"
        assert await table_oids(conn, database.schema_name) == before
        await assert_seed_retained(conn)


@pytest.mark.integration
@pytest.mark.parametrize("drifted_file", [filename for _, filename in migrate.MIGRATIONS])
async def test_source_checksum_drift_refuses_apply_and_reset_without_destroying_data(
    database: migrate.MigrationConfig, monkeypatch: pytest.MonkeyPatch, drifted_file: str
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    original = migrate._read_one

    def edited(filename: str) -> tuple[str, str]:
        source, checksum = original(filename)
        if filename != drifted_file:
            return source, checksum
        changed = source + "\n-- changed source identity\n"
        return changed, hashlib.sha256(changed.encode()).hexdigest()

    monkeypatch.setattr(migrate, "_read_one", edited)
    async with connect(database) as conn:
        await seed_user(conn)
        before = await table_oids(conn, database.schema_name)
        assert (await runner.status()).state == "drifted"
        for operation in (runner.apply, lambda: runner.reset(confirm_schema=database.schema_name)):
            with pytest.raises(migrate.MigrationError):
                await operation()
            assert await table_oids(conn, database.schema_name) == before
            await assert_seed_retained(conn)


@pytest.mark.integration
@pytest.mark.parametrize(
    "tamper",
    [
        "DELETE FROM _cme_schema_migrations",
        "UPDATE _cme_schema_migrations SET version=99 WHERE version=2",
        "DELETE FROM _cme_schema_migrations WHERE version=1",
        "UPDATE _cme_schema_migrations SET filename='other.sql' WHERE version=2",
        "ALTER TABLE _cme_schema_migrations DROP CONSTRAINT _cme_schema_migrations_checksum_sha256_check; "
        "ALTER TABLE _cme_schema_migrations ALTER COLUMN checksum_sha256 TYPE text[] USING ARRAY[checksum_sha256]",
        "DELETE FROM _cme_schema_migrations WHERE version > 1; "
        "ALTER TABLE _cme_schema_migrations ALTER COLUMN version TYPE boolean USING true",
    ],
)
async def test_invalid_or_unknown_migration_history_cannot_be_blessed_or_reset(
    database: migrate.MigrationConfig, tamper: str
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await seed_user(conn)
        before = await table_oids(conn, database.schema_name)
        await conn.execute(cast(LiteralString, tamper))
        assert (await runner.status()).state == "drifted"
        for operation in (runner.apply, lambda: runner.reset(confirm_schema=database.schema_name)):
            with pytest.raises(migrate.MigrationError):
                await operation()
            assert await table_oids(conn, database.schema_name) == before
            await assert_seed_retained(conn)


@pytest.mark.integration
async def test_catalog_drift_is_not_silently_accepted_and_explicit_reset_restores_it(
    database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await conn.execute("ALTER TABLE memory_version DROP CONSTRAINT valid_wellformed")
        status = await runner.status()
        assert status.state == "drifted" and status.reason == "catalog_drift"
        with pytest.raises(migrate.MigrationError):
            await runner.apply()
        await runner.reset(confirm_schema=database.schema_name)
        assert (await runner.status()).state == "applied"
        cursor = await conn.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid='memory_version'::regclass "
            "AND conname='valid_wellformed'"
        )
        assert await cursor.fetchall() == [("valid_wellformed",)]


@pytest.mark.integration
async def test_status_rejects_a_foreign_key_recreated_on_the_wrong_relation(database: migrate.MigrationConfig) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await seed_user(conn)
        await conn.execute("ALTER TABLE memory_evidence DROP CONSTRAINT memory_evidence_user_id_fkey")
        await conn.execute(
            "ALTER TABLE job ADD CONSTRAINT memory_evidence_user_id_fkey FOREIGN KEY (user_id) REFERENCES app_user(id)"
        )
        query = """SELECT c.relname FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                   WHERE con.conname='memory_evidence_user_id_fkey' AND con.connamespace=%s::regnamespace"""
        cursor = await conn.execute(query, (database.schema_name,))
        assert await cursor.fetchall() == [("job",)]
        status = await runner.status()
        assert status.state == "drifted" and status.reason == "catalog_drift"
        with pytest.raises(migrate.MigrationError, match="catalog_drift"):
            await runner.apply()
        await assert_seed_retained(conn)
        await runner.reset(confirm_schema=database.schema_name)
        assert (await runner.status()).state == "applied"
        cursor = await conn.execute(query, (database.schema_name,))
        assert await cursor.fetchall() == [("memory_evidence",)]


@pytest.mark.integration
async def test_status_rejects_disabled_internal_foreign_key_enforcement(database: migrate.MigrationConfig) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await seed_user(conn)
        await conn.execute("ALTER TABLE memory_evidence DISABLE TRIGGER ALL")
        triggers = """SELECT count(*),bool_and(tgenabled=%s) FROM pg_trigger
                      WHERE tgrelid='memory_evidence'::regclass AND tgisinternal"""
        cursor = await conn.execute(triggers, ("D",))
        state = await cursor.fetchone()
        assert state is not None and state[0] > 0 and state[1] is True
        # DISABLE TRIGGER ALL also disables the dual-write trigger (migration 7), so the source columns are supplied.
        orphan_insert = """INSERT INTO memory_evidence(version_id,turn_id,user_id,evidence_span,evidence_role,
                                                       source_type,source_ref,span,content_hash,scope_path)
                           VALUES ('missing-version','missing-turn','retained-user','fixture','support',
                                   'turn','missing-turn','fixture',repeat('0',64),'/u/retained-user/')"""
        # The privileged disposable fixture demonstrates the lost FK guarantee,
        # rather than relying solely on the trigger metadata as the oracle.
        await conn.execute(orphan_insert)
        cursor = await conn.execute("SELECT count(*) FROM memory_evidence")
        assert await cursor.fetchone() == (1,)
        status = await runner.status()
        assert status.state == "drifted" and status.reason == "catalog_drift"
        with pytest.raises(migrate.MigrationError, match="catalog_drift"):
            await runner.apply()
        await assert_seed_retained(conn)
        await runner.reset(confirm_schema=database.schema_name)
        assert (await runner.status()).state == "applied"
        cursor = await conn.execute("SELECT count(*) FROM memory_evidence")
        assert await cursor.fetchone() == (0,)
        cursor = await conn.execute(triggers, ("O",))
        state = await cursor.fetchone()
        assert state is not None and state[0] > 0 and state[1] is True
        await seed_user(conn)
        with pytest.raises(ForeignKeyViolation):
            await conn.execute(orphan_insert)


@pytest.mark.integration
async def test_unknown_objects_make_reset_fail_atomically_instead_of_cascading(
    database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    async with connect(database) as conn:
        await seed_user(conn)
        await conn.execute("CREATE TABLE foreign_addition (value text)")
        await conn.execute("INSERT INTO foreign_addition VALUES ('keep')")
        before = await table_oids(conn, database.schema_name)
        with pytest.raises(migrate.MigrationError):
            await runner.reset(confirm_schema=database.schema_name)
        assert await table_oids(conn, database.schema_name) == before
        await assert_seed_retained(conn)
        cursor = await conn.execute("SELECT value FROM foreign_addition")
        assert await cursor.fetchall() == [("keep",)]


@pytest.mark.integration
async def test_external_foreign_key_prevents_reset_and_preserves_both_sides(database: migrate.MigrationConfig) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    foreign = "cme_external_" + uuid4().hex
    async with connect(database) as conn:
        await seed_user(conn)
        before = await table_oids(conn, database.schema_name)
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(foreign)))
        try:
            await conn.execute(
                sql.SQL("CREATE TABLE {}.reference (user_id text REFERENCES {}.app_user(id))").format(
                    sql.Identifier(foreign), sql.Identifier(database.schema_name)
                )
            )
            await conn.execute(
                sql.SQL("INSERT INTO {}.reference VALUES ('retained-user')").format(sql.Identifier(foreign))
            )
            with pytest.raises(migrate.MigrationError):
                await runner.reset(confirm_schema=database.schema_name)
            assert await table_oids(conn, database.schema_name) == before
            await assert_seed_retained(conn)
            cursor = await conn.execute(sql.SQL("SELECT user_id FROM {}.reference").format(sql.Identifier(foreign)))
            assert await cursor.fetchall() == [("retained-user",)]
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(foreign)))


@pytest.mark.integration
async def test_concurrent_applies_settle_to_one_installation_and_one_history_row(
    database: migrate.MigrationConfig,
) -> None:
    first, second = migrate.MigrationRunner(database), migrate.MigrationRunner(database)
    results = await asyncio.wait_for(asyncio.gather(first.apply(), second.apply()), timeout=20)
    assert sorted(result.action for result in results) == ["applied", "unchanged"]
    assert all(result.status.state == "applied" and result.status.version == migrate.VERSION for result in results)
    async with connect(database) as conn:
        assert set(await table_oids(conn, database.schema_name)) == DOMAIN_TABLES | {HISTORY_TABLE}
        cursor = await conn.execute("SELECT version FROM _cme_schema_migrations ORDER BY version")
        assert await cursor.fetchall() == [(version,) for version, _ in migrate.MIGRATIONS]


@pytest.mark.integration
async def test_clean_reset_passes_all_forty_three_assertions_against_the_real_full_table(
    database: migrate.MigrationConfig,
) -> None:
    runner = migrate.MigrationRunner(database)
    await runner.apply()
    await runner.reset(confirm_schema=database.schema_name)
    script = (CME_ROOT / "src/custom_memory_engine/schema/bitemporal_on_full.sql").read_text(encoding="utf-8")
    script = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("\\"))
    async with connect(database) as conn:
        cursor = await conn.execute("SELECT 'memory_version'::regclass::oid")
        row = await cursor.fetchone()
        assert row is not None
        original_oid = row[0]
        cursor = await conn.execute(cast(LiteralString, script), prepare=False)
        passes = []
        while True:
            if cursor.description is not None:
                passes.extend(
                    row[0]
                    for row in await cursor.fetchall()
                    if row and isinstance(row[0], str) and row[0].startswith("PASS")
                )
            if not cursor.nextset():
                break
        assert len(passes) == 43 and len(set(passes)) == 43
        cursor = await conn.execute("SELECT 'memory_version'::regclass::oid")
        assert await cursor.fetchone() == (original_oid,), "checks must exercise the migrated table, not replace it"
        cursor = await conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='memory_version'",
            (database.schema_name,),
        )
        assert {"archival_branch", "layer", "extractor_version"} <= {row[0] for row in await cursor.fetchall()}
        cursor = await conn.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgrelid='memory_version'::regclass "
            "AND tgname='trg_memory_version_provenance'"
        )
        assert await cursor.fetchone() == ("O",)


@pytest.mark.integration
async def test_a_version_one_schema_is_pending_and_apply_advances_it_in_order(
    database: migrate.MigrationConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade path: a schema installed when only migration 1 shipped advances through every later one."""
    runner = migrate.MigrationRunner(database)
    with monkeypatch.context() as shipped_then:
        shipped_then.setattr(migrate, "MIGRATIONS", migrate.MIGRATIONS[:1])
        installed = await runner.apply()
        assert (installed.status.state, installed.status.version) == ("applied", 1)
    async with connect(database) as conn:
        await seed_user(conn)
    pending = await runner.status()
    assert (pending.state, pending.version) == ("pending", 1)
    result = await runner.apply()
    assert result.action == "applied" and (result.status.state, result.status.version) == ("applied", migrate.VERSION)
    async with connect(database) as conn:
        assert set(await table_oids(conn, database.schema_name)) == DOMAIN_TABLES | {HISTORY_TABLE}
        await assert_seed_retained(conn)
    assert (await runner.apply()).action == "unchanged"
