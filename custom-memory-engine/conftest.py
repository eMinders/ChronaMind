"""Per-worker test databases for ``pytest -n`` (pytest-xdist), issue #167.

The migration runner takes a **database-wide** advisory lock (``schema/migrate.py``), a production safety property, and
almost every DB test migrates a fresh schema, so workers sharing one database queue on that lock and gain nothing.
Advisory locks are scoped to a database, so each worker gets its own:

* ``cme_compatibility_<worker>`` is created from ``template0`` at worker start (dropped first if a crashed run
  left it), bootstrapped with ``tests/init_extensions.sql``, and dropped when the worker ends;
* ``CME_TEST_DATABASE_URL`` (and ``CME_BASELINE_TEST_DATABASE_URL`` when it names the same database) are rewritten
  for that worker only.

Without xdist, or without a test database URL, nothing happens: a serial run uses the configured database unchanged.
The configured URL must already pass ``TestDatabaseConfig`` (the disposable ``cme_compatibility`` database); worker
names are accepted by the same guard and by nothing broader.
"""

import os
from pathlib import Path
from typing import LiteralString, cast

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr

from custom_memory_engine.config import TestDatabaseConfig

_URL = "CME_TEST_DATABASE_URL"
_BASELINE_URL = "CME_BASELINE_TEST_DATABASE_URL"
_EXTENSIONS = Path(__file__).parent / "tests" / "init_extensions.sql"
_ORIGINAL = pytest.StashKey[tuple[str, str]]()


def _worker_database(config: pytest.Config) -> tuple[str, str] | None:
    """``(base_url, worker_database_name)`` for an xdist worker with a test database configured, else ``None``."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    base = os.environ.get(_URL)
    if not worker or not base:
        return None
    # validate the configured target before touching anything; the worker name is checked by the same guard below
    TestDatabaseConfig(database_url=SecretStr(base))
    return base, f"cme_compatibility_{worker}"


def _admin(base: str) -> psycopg.Connection:
    return psycopg.connect(base, autocommit=True, connect_timeout=10)


def pytest_configure(config: pytest.Config) -> None:
    found = _worker_database(config)
    if found is None:
        return
    base, name = found
    worker_url = make_conninfo(base, dbname=name)
    TestDatabaseConfig(database_url=SecretStr(worker_url))
    with _admin(base) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    with psycopg.connect(worker_url, autocommit=True, connect_timeout=10) as worker:
        worker.execute(cast(LiteralString, _EXTENSIONS.read_text(encoding="utf-8")))  # a repository file, not input
    baseline = os.environ.get(_BASELINE_URL)
    if baseline and conninfo_to_dict(baseline).get("dbname") == conninfo_to_dict(base).get("dbname"):
        os.environ[_BASELINE_URL] = make_conninfo(baseline, dbname=name)
    os.environ[_URL] = worker_url
    config.stash[_ORIGINAL] = (base, name)


def pytest_unconfigure(config: pytest.Config) -> None:
    found = config.stash.get(_ORIGINAL, None)
    if found is None:
        return
    base, name = found
    with _admin(base) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
