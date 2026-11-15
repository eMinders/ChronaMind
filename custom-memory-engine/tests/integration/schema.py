"""The one way integration suites build the engine schema: every shipped migration, in order.

Suites used to read ``001_full_schema.sql`` (some also ``002``) themselves, so engine behaviour was
tested against a schema that production never runs, and a new migration went untested by them.
``migrate.migration_scripts`` follows ``migrate.MIGRATIONS``, so each new migration is covered
automatically.

Two entry points:

* :func:`engine_database` - **the default for every test that needs the current schema.** The production
  :class:`~custom_memory_engine.schema.migrate.MigrationRunner` migrates a template database once per process
  (``<test db>_tpl``), and each test gets its own clone (``<test db>_c<n>``, ``CREATE DATABASE ... TEMPLATE``). A
  clone is a byte-for-byte copy of what the runner built (history and catalog hashes included, so ``status()`` reports
  ``applied``) and costs about 0.03 s instead of the runner's 0.9 s. Nothing is shared between tests: each clone is
  dropped afterwards, and ``CME_TEST_DATABASE_URL`` names the clone while the test runs.
  A job that migrates the configured database itself sets ``CME_TEST_PREMIGRATED_DATABASE`` (E-023): the template is
  then a copy of that database and no second migration runs, so the job log keeps one migration invocation.
* :func:`apply_migrations` - raw DDL into the current schema, for tests that need a *partial* chain
  (``through=``) or exercise the migration bodies themselves. The caller's ``search_path`` must already name the
  target schema first.
"""

import itertools
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import LiteralString, cast

import psycopg
import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import ObjectInUse
from pydantic import SecretStr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from custom_memory_engine.config import TestDatabaseConfig
from custom_memory_engine.schema.migrate import MigrationConfig, MigrationRunner, migration_scripts

URL = "CME_TEST_DATABASE_URL"
#: Set by a job that already ran ``schema.migrate apply`` on the configured database (E-023). The template then
#: becomes a byte copy of that database instead of a second migration, so the job log carries one invocation.
PREMIGRATED = "CME_TEST_PREMIGRATED_DATABASE"
#: The schema every clone carries, built by the production runner in the template.
ENGINE_SCHEMA = "cme_engine"
_EXTENSIONS = Path(__file__).parents[1] / "init_extensions.sql"
_clones = itertools.count(1)
_built: dict[str, str] = {}  # base database URL -> its migrated template database name
_base: list[str] = []  # the URL configured before any clone was entered (clones rewrite the environment)


async def apply_migrations(conn: AsyncConnection, *, through: int | None = None) -> None:
    """Apply migrations ``1..through`` (default: all) into the current schema."""
    for _, source in migration_scripts(through=through):
        await conn.execute(cast(LiteralString, source), prepare=False)


def derived_databases(base_url: str) -> re.Pattern[str]:
    """Exactly the template and clone names derived from ``base_url``'s database, for cleanup."""
    return re.compile(re.escape(_dbname(base_url)) + r"_(?:tpl|c[0-9]+)")


def drop_derived_databases(base_url: str) -> None:
    """Drop the template and any clone a crashed run left behind (none remain after a clean run).

    Synchronous on purpose: it runs from pytest hooks, where an ``asyncio.run`` would clear the main thread's event
    loop for the tests that follow.
    """
    derived = derived_databases(base_url)
    with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as admin:
        for (name,) in admin.execute("SELECT datname FROM pg_catalog.pg_database").fetchall():
            if derived.fullmatch(name):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _dbname(url: str) -> str:
    return str(conninfo_to_dict(url)["dbname"])


async def _admin(base_url: str) -> AsyncConnection:
    return await AsyncConnection.connect(base_url, autocommit=True, connect_timeout=10)


async def _maintenance(base_url: str) -> AsyncConnection:
    """A connection to ``postgres``: a database cannot be copied while any session is connected to it.

    ``postgres`` is the one connection target exempt from :class:`~custom_memory_engine.config.TestDatabaseConfig`:
    the source and every database this module drops or copies are still named by the validated base URL, and the
    copy needs a session that is attached to neither side of it.
    """
    return await AsyncConnection.connect(
        make_conninfo(base_url, dbname="postgres"), autocommit=True, connect_timeout=10
    )


async def _require_applied(url: str) -> None:
    """The copy must carry the full, verified migration set before any test clones it."""
    status = await MigrationRunner(MigrationConfig(database_url=SecretStr(url), schema_name=ENGINE_SCHEMA)).status()
    if status.state != "applied":
        raise RuntimeError(f"premigrated_database_not_applied:{status.state}")


@retry(
    retry=retry_if_exception_type(ObjectInUse),
    stop=stop_after_attempt(8),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    reraise=True,
)
async def _copy_database(base_url: str, name: str) -> None:
    """Make ``name`` a copy of the configured database (retried while a stray session still holds it).

    The premigrated job's healthcheck probes ``postgres``, never this source, so the recurring case is gone; the
    budget still covers a slow service handshake or another session attached when the copy starts.
    """
    async with await _maintenance(base_url) as admin:
        await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        await admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(_dbname(base_url)))
        )


async def _template(base_url: str) -> str:
    """Migrate ``<base>_tpl`` with the production runner, once per process."""
    built = _built.get(base_url)
    if built is not None:
        return built
    name = f"{_dbname(base_url)}_tpl"
    url = make_conninfo(base_url, dbname=name)
    TestDatabaseConfig(database_url=SecretStr(url))
    if os.environ.get(PREMIGRATED) == "1":
        # E-023: the job migrated the configured database once through the runner; this template is a copy, so the
        # migration stays singular while every test keeps its private clone. Workers would copy an unmigrated
        # per-worker database, so a premigrated run is deliberately single-process.
        if os.environ.get("PYTEST_XDIST_WORKER"):
            raise RuntimeError("premigrated_database_requires_a_single_process")
        await _copy_database(base_url, name)
        await _require_applied(url)
    else:
        async with await _admin(base_url) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            await admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=10) as conn:
            await conn.execute(cast(LiteralString, _EXTENSIONS.read_text(encoding="utf-8")))  # a repository file
        await MigrationRunner(MigrationConfig(database_url=SecretStr(url), schema_name=ENGINE_SCHEMA)).apply()
    _built[base_url] = name
    return name


@asynccontextmanager
async def engine_database() -> AsyncIterator[tuple[TestDatabaseConfig, str]]:
    """A private database whose ``cme_engine`` schema the runner fully migrated: ``(config, schema)``."""
    if not _base:
        if not os.environ.get(URL):
            pytest.skip(f"{URL} must name a disposable database with vector and btree_gist installed")
        _base.append(os.environ[URL])
    base_url = _base[0]
    template = await _template(base_url)
    clone = f"{_dbname(base_url)}_c{next(_clones)}"
    clone_url = make_conninfo(base_url, dbname=clone)
    config = TestDatabaseConfig(database_url=SecretStr(clone_url))
    async with await _admin(base_url) as admin:
        await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(clone)))
        await admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {} STRATEGY WAL_LOG").format(
                sql.Identifier(clone), sql.Identifier(template)
            )
        )
    previous = os.environ.get(URL)
    os.environ[URL] = clone_url
    try:
        yield config, ENGINE_SCHEMA
    finally:
        if previous is None:
            os.environ.pop(URL, None)
        else:
            os.environ[URL] = previous
        await _drop_clone_roles(clone_url)
        async with await _admin(base_url) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(clone)))


async def _drop_clone_roles(clone_url: str) -> None:
    """Drop roles the test granted on this clone's schema, so none outlive it (roles are cluster-wide).

    A role qualifies only through an explicit grant on ``cme_engine`` *in this clone* (never ``PUBLIC``), and test
    roles carry random names, so another test's or another worker's role can never match.
    """
    async with await AsyncConnection.connect(clone_url, autocommit=True, connect_timeout=10) as conn:
        cursor = await conn.execute(
            """SELECT DISTINCT r.rolname FROM pg_catalog.pg_namespace n
               CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) a
               JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
               WHERE n.nspname = %s AND a.grantee <> 0 AND r.rolname <> current_user AND NOT r.rolsuper""",
            (ENGINE_SCHEMA,),
        )
        for (role,) in await cursor.fetchall():
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
