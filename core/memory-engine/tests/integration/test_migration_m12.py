"""Migration 12: the creator of an artifact and the idempotency record the public API relies on (E-039)."""

from typing import LiteralString, cast

import pytest
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
    seed_turn,
)

pytestmark = pytest.mark.integration
M12 = 12


async def insert(conn, version_id: str, **extra: str) -> None:
    columns = "".join(f", {name}" for name in extra)
    marks = ", %s" * len(extra)
    async with conn.transaction():
        await conn.execute(
            cast(
                LiteralString,
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer"
                + columns
                + ") VALUES (%s, %s, 'u1', 'active', "
                "'user', 'diet', false, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic'"
                + marks
                + ")",
            ),
            (version_id, version_id, START, *extra.values()),
        )
        await conn.execute("INSERT INTO memory_evidence VALUES (%s, 't-x', 'u1', 'x', 'support')", (version_id,))


async def test_the_creator_is_recorded_whole_or_not_at_all_and_fixed_once_set(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_turn(conn, "t-x", "x", START)
        await insert(conn, "legacy")  # a v1 writer names neither
        assert await rows(conn, "SELECT created_by, created_tier FROM memory_version WHERE id = 'legacy'") == [
            (None, None)
        ]
        await insert(conn, "api", created_by="u1", created_tier="B")
        with pytest.raises(CheckViolation, match="creator_check"):
            await insert(conn, "half", created_by="u1")
        with pytest.raises(CheckViolation, match="creator_check"):
            await insert(conn, "bad-tier", created_by="u1", created_tier="X")
        with pytest.raises(CheckViolation, match="creator_check"):
            await insert(conn, "blank", created_by=" ", created_tier="C")
        with pytest.raises(RaiseException, match="creator_is_fixed_once_set"):
            await conn.execute("UPDATE memory_version SET created_tier = 'C' WHERE id = 'api'")
        await conn.execute(
            "UPDATE memory_version SET created_by = 'u1', created_tier = 'C' WHERE id = 'legacy'"
        )  # set once


async def test_the_idempotency_record_is_per_user_append_only_and_isolated(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        for user in ("u1", "u2"):
            await conn.execute(
                "INSERT INTO api_idempotency (user_id, operation, idempotency_key, result_id) "
                "VALUES (%s, 'op', 'k', %s)",
                (user, "r-" + user),
            )  # the same key for two users is not a collision
        with pytest.raises(UniqueViolation):
            await conn.execute(
                "INSERT INTO api_idempotency (user_id, operation, idempotency_key, result_id) "
                "VALUES ('u1', 'op', 'k', 'again')"
            )
        with pytest.raises(CheckViolation):
            await conn.execute(
                "INSERT INTO api_idempotency (user_id, operation, idempotency_key, result_id) "
                "VALUES ('u1', 'op', ' ', 'r')"
            )
        assert await rows(
            conn, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'api_idempotency'::regclass"
        ) == [(True, True)]


def test_the_runtime_role_can_append_but_never_rewrite_an_idempotency_record() -> None:
    from custom_memory_engine.security import expected_grants

    assert expected_grants("api_idempotency") == frozenset({"SELECT", "INSERT"})


async def test_rollback_restores_version_11_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M12 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M12 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert await rows(conn, "SELECT to_regclass('api_idempotency')") == [(None,)]
    assert (await runner.apply()).action == "applied"
