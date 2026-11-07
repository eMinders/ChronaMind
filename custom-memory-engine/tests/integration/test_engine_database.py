"""The per-test database clone (``tests/integration/schema.py::engine_database``) is what the suite rests on.

Each claim the helper's docstring makes is checked here: the clone is the production runner's own artifact (``status``
reports ``applied``, so history and catalog hash match), tests cannot see each other's writes, the environment names
the clone only while the test runs, and nothing - database or role - outlives it.
"""

import os
from typing import LiteralString

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict

from custom_memory_engine.schema import migrate
from tests.integration.schema import URL, engine_database

pytestmark = pytest.mark.integration


async def _exists(url: str, query: LiteralString, value: str) -> bool:
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
        return await (await conn.execute(query, (value,))).fetchone() is not None


async def test_a_clone_is_the_runners_artifact_private_and_leaves_nothing_behind() -> None:
    if not os.environ.get(URL):
        pytest.skip(f"{URL} must name a disposable database")
    configured = os.environ[URL]
    role = "cme_rt_probe_" + os.urandom(6).hex()
    async with engine_database() as (config, schema):
        url = config.database_url.get_secret_value()
        clone = str(conninfo_to_dict(url)["dbname"])
        assert os.environ[URL] == url, "the environment names the clone while the test runs"
        status = await migrate.MigrationRunner(
            migrate.MigrationConfig(database_url=config.database_url, schema_name=schema)
        ).status()
        assert (status.state, status.version) == ("applied", migrate.VERSION), "byte-for-byte the runner's output"
        async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await conn.execute("INSERT INTO app_user VALUES ('private', 'ext-private', now())")
            await conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            await conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role))
            )
        async with engine_database() as (other, other_schema):
            async with await AsyncConnection.connect(
                other.database_url.get_secret_value(), autocommit=True, connect_timeout=5
            ) as conn:
                await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(other_schema)))
                cursor = await conn.execute("SELECT count(*) FROM app_user")
                assert await cursor.fetchone() == (0,), "another clone never sees this test's rows"
        assert os.environ[URL] == url, "leaving a nested clone restores the outer one"
    assert os.environ[URL] == configured, "leaving the clone restores the configured database"
    assert not await _exists(configured, "SELECT 1 FROM pg_database WHERE datname = %s", clone), "clone dropped"
    assert not await _exists(configured, "SELECT 1 FROM pg_roles WHERE rolname = %s", role), "granted role dropped"
