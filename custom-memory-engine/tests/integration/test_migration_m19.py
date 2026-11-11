"""Migration 19: an entity subject is a foreign key (``V2-D74``, E-024 §10 row 10).

Before this migration, ``assert``'s ``subject_entity`` reached the database only as ``subject_key`` text: the handler
refused another scope's entity and an unreadable one, but nothing in the schema could tell an entity id from the
literal ``'self'``. These tests run against the REAL migrated schema and check the three properties the migration
adds: an entity subject must exist, must belong to the same principal, and must lie in the claim's own scope. They
also check the contract that a gone entity is not silently removed from its claims, and that the rollback restores
version 18.
"""

from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation, RaiseException

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture; seeds u1 and u2)

pytestmark = pytest.mark.integration
M19 = 19


async def entity(conn: AsyncConnection, entity_id: str, user: str, scope: str) -> None:
    await conn.execute(
        "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type) "
        "VALUES (%s, %s, %s, 'Ada', 'person')",
        (entity_id, user, scope),
    )


async def claim(
    conn: AsyncConnection,
    version_id: str,
    *,
    user: str = "u1",
    scope: str = "/u/u1/",
    subject_key: str = "self",
    subject_entity_id: str | None = None,
) -> None:
    """A minimal active claim, with the axes and provenance the triggers otherwise require (I1, I1')."""
    session, turn_id = "s-m19-" + user, "t-m19-" + user
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, clock_now()) ON CONFLICT DO NOTHING",
        (session, user),
    )
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, 1, 'user', 'Ada is my coworker.', clock_now()) ON CONFLICT DO NOTHING",
        (turn_id, session, user),
    )
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, valid_from_basis, assertion_mode, extractor_version, "
        "scope_path, kind, lifecycle, retention, subject_entity_id) "
        "VALUES (%s, 'mk', %s, 'active', %s, 'statement', false, 'Ada is a coworker', "
        "tstzrange('-infinity', NULL), tstzrange(clock_now(), NULL), 'semantic', 'inferred', 'asserted', "
        "'test', %s, 'claim', 'asserted', 'durable', %s)",
        (version_id, user, subject_key, scope, subject_entity_id),
    )
    # provenance after the version it belongs to (the FK is the composite (version_id, user_id))
    await conn.execute(
        "INSERT INTO memory_evidence (version_id, user_id, turn_id, evidence_span, evidence_role) "
        "VALUES (%s, %s, %s, 'Ada is my coworker.', 'supports')",
        (version_id, user, turn_id),
    )


async def test_a_claim_can_name_an_entity_in_its_own_scope(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/p/repo/")
        await claim(conn, "v1", scope="/u/u1/p/repo/", subject_key="e1", subject_entity_id="e1")
        assert await rows(conn, "SELECT subject_key, subject_entity_id FROM memory_version WHERE id = 'v1'") == [
            ("e1", "e1")
        ]


async def test_a_claim_cannot_name_an_entity_of_another_scope(database) -> None:  # noqa: F811
    """The same-scope rule is the composite key's, not the handler's: the parent scope differs by one path segment."""
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/p/other/")
        with pytest.raises(ForeignKeyViolation):
            await claim(conn, "v1", scope="/u/u1/p/repo/", subject_key="e1", subject_entity_id="e1")


async def test_a_claim_cannot_name_an_entity_of_another_principal(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u2", "/u/u2/")
        with pytest.raises(ForeignKeyViolation):
            await claim(conn, "v1", scope="/u/u1/", subject_key="e1", subject_entity_id="e1")


async def test_a_claim_cannot_name_an_entity_that_does_not_exist(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(ForeignKeyViolation):
            await claim(conn, "v1", subject_key="ent_missing", subject_entity_id="ent_missing")


async def test_the_two_spellings_of_the_subject_cannot_disagree(database) -> None:  # noqa: F811
    """``subject_entity_id`` is what the key against ``entity`` checks; ``subject_key`` is what supersession compares.

    They must name the same thing, or a row could be excluded from supersession under one id while citing another.
    """
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/")
        with pytest.raises(Exception, match="memory_version_subject_entity_is_subject"):
            await claim(conn, "v1", subject_key="e1", subject_entity_id="e2")


async def test_an_entity_a_claim_cites_cannot_be_deleted(database) -> None:  # noqa: F811
    """ON DELETE RESTRICT: a claim would otherwise survive naming an entity that is gone.

    A link (migration 18) cascades because a link *is* the relation; a claim's subject is part of the claim.
    """
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await claim(conn, "v1", subject_key="e1", subject_entity_id="e1")
        with pytest.raises(ForeignKeyViolation):
            await conn.execute("DELETE FROM entity WHERE id = 'e1'")


async def test_subject_entity_id_is_immutable_in_place(database) -> None:  # noqa: F811
    """``guard_update`` treats it as belief content: re-pointing a claim would rewrite what it is about."""
    async with connect(database) as conn, conn.transaction():
        await entity(conn, "e1", "u1", "/u/u1/")
        await entity(conn, "e2", "u1", "/u/u1/")
        await claim(conn, "v1", subject_key="e1", subject_entity_id="e1")
        with pytest.raises(RaiseException, match="only status, archival_branch"):
            await conn.execute(
                "UPDATE memory_version SET subject_entity_id = 'e2', subject_key = 'e2' WHERE id = 'v1'"
            )


async def test_a_version_without_an_entity_subject_is_unchanged(database) -> None:  # noqa: F811
    """Every v1 row keeps ``subject_key = 'self'`` and a NULL entity: nothing was backfilled."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1")
        assert await rows(conn, "SELECT subject_key, subject_entity_id FROM memory_version WHERE id = 'v1'") == [
            ("self", None)
        ]


async def test_rollback_restores_version_18_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M19 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M19 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert before != recorded[0][0]  # the migration did change the catalog
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_version' AND column_name = 'subject_entity_id'",
        )
    assert (await runner.status()).version == M19 - 1
    assert (await runner.apply()).action == "applied"
