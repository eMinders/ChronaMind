"""Migration 25: the durable, epoch-bound export manifest (``E-038``/``F11``, channel 11, ``V2-D99``).

What this migration owns, and what each property is worth:

* a manifest says **what was exported** (sorted ids, a digest over them, the count) and under **which deletion epoch**
—
  the two facts channel 11 needs and the two a process-local dict could not keep;
* it is **append-only**: an ``UPDATE`` or ``DELETE`` is refused, so a manifest cannot be rewritten to look current;
* it **describes itself**: the count equals the array's length, and the digest is a SHA-256, both enforced by the
table;
* it is **row-level secure per user**: a manifest holds ids and paths, so the user predicate is the whole rule.

The rollback is checked the same way every other one is: the catalog fingerprint it restores must equal what the
history row recorded, or the downgrade is refused rather than left half-done.
"""

from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, RaiseException
from psycopg.types.json import Jsonb

from custom_memory_engine.schema import migrate
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
M25 = 25
T0 = datetime(2026, 3, 10, tzinfo=UTC)
DIGEST = "a" * 64


async def manifest(
    conn: AsyncConnection,
    snapshot_id: str = "exp-1",
    *,
    user: str = "u1",
    epoch: int = 0,
    ids: tuple[str, ...] = ("v1", "v2"),
    digest: str = DIGEST,
    count: int | None = None,
) -> None:
    """One manifest row, written directly: the table's own constraints are what these tests are about."""
    await conn.execute(
        "INSERT INTO export_snapshot (id, user_id, captured_epoch, artifact_count, manifest_digest, artifact_ids, "
        "scope_paths, captured_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            snapshot_id,
            user,
            epoch,
            len(ids) if count is None else count,
            digest,
            Jsonb(list(ids)),
            [f"/u/{user}/"],
            T0,
        ),
    )


async def test_a_manifest_is_self_consistent_and_user_scoped(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await manifest(conn)
        assert await rows(
            conn, "SELECT id, captured_epoch, artifact_count FROM export_snapshot WHERE id = %s", ("exp-1",)
        ) == [("exp-1", 0, 2)]


async def test_a_manifest_that_lies_about_its_size_is_refused(database) -> None:  # noqa: F811
    """``artifact_count`` is what the caller was told, so it must equal what the array holds."""
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(CheckViolation) as refused:
            await manifest(conn, ids=("v1",), count=2)
        assert "manifest_matches_count" in str(refused.value)


async def test_a_digest_that_is_not_a_sha256_is_refused(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(CheckViolation):
            await manifest(conn, digest="not-a-digest")


async def test_a_manifest_cannot_be_rewritten_or_removed(database) -> None:  # noqa: F811
    """Append-only is the point: a manifest that can be edited after the fact is not evidence of an export."""
    async with connect(database) as conn, conn.transaction():
        await manifest(conn)
        with pytest.raises(RaiseException) as edited:
            async with conn.transaction():
                await conn.execute("UPDATE export_snapshot SET captured_epoch = 7 WHERE id = 'exp-1'")
        assert "export_snapshot_is_append_only" in str(edited.value)
        with pytest.raises(RaiseException) as removed:
            async with conn.transaction():
                await conn.execute("DELETE FROM export_snapshot WHERE id = 'exp-1'")
        assert "export_snapshot_is_append_only" in str(removed.value)
        assert await rows(conn, "SELECT captured_epoch FROM export_snapshot WHERE id = 'exp-1'") == [(0,)]


async def test_the_read_policy_isolates_one_users_manifest_from_another(database) -> None:  # noqa: F811
    """RLS, not a ``WHERE`` clause: the restricted role cannot see another user's manifest at all.

    Run as the runtime role rather than as the connection's own (superuser) identity, because a superuser bypasses RLS
    and would make this pass trivially while the policy was wrong.
    """
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await manifest(conn, "exp-u1", user="u1")
        await manifest(conn, "exp-u2", user="u2")
        await provision_runtime_role(conn, schema, role)
        try:
            for user in ("u1", "u2"):
                async with tenant_scope(conn, user, role=role):
                    assert await rows(conn, "SELECT id FROM export_snapshot ORDER BY id") == [(f"exp-{user}",)]
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_rollback_restores_version_24_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        # from the newest version: a rollback composes every script down to the target, so a later migration (26) has
        # to come off before 25 can be evaluated against the fingerprint recorded when it was applied
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M25 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M25 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert before != recorded[0][0]
        assert not await rows(
            conn,
            "SELECT 1 FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace"
            " WHERE n.nspname = current_schema() AND c.relname = 'export_snapshot'",
        )
        assert not await rows(
            conn, "SELECT 1 FROM pg_catalog.pg_proc WHERE proname = 'guard_export_snapshot_append_only'"
        )
    assert (await runner.status()).version == M25 - 1
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION


async def test_rollback_refuses_while_a_manifest_exists_and_proceeds_only_when_the_loss_is_named(database) -> None:  # noqa: F811
    """A downgrade that would discard export manifests is refused unless the operator names that exact loss.

    The same rule rollbacks 8 and 11 follow, with one addition: the manifests have no other home, so the operator may
    accept losing them — but only by naming *this* table, in *this* transaction. Acknowledging another table's loss is
    not an acknowledgement of this one.
    """
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    down = cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M25 - 1))
    async with connect(database) as conn:
        await manifest(conn)  # `u1` is seeded by the fixture; the connection is autocommit
        for setting in (None, "entity_embedding"):
            async with conn.transaction():
                if setting is not None:
                    await conn.execute(sql.SQL("SET LOCAL cme.rollback_discard = {}").format(sql.Literal(setting)))
                # 26 must come off first and has nothing to refuse here; 25 then refuses
                with pytest.raises(RaiseException, match="m25_rollback_refused_export_snapshots_exist"):
                    async with conn.transaction():
                        await conn.execute(down, prepare=False)
        assert await rows(conn, "SELECT count(*) FROM export_snapshot") == [(1,)]
        async with conn.transaction():
            await conn.execute("SET LOCAL cme.rollback_discard = 'export_snapshot'")
            await conn.execute(down, prepare=False)
    assert (await runner.status()).version == M25 - 1
    assert (await runner.apply()).action == "applied"


async def test_a_manifest_goes_with_its_user_by_cascade_and_only_that_way(database) -> None:  # noqa: F811
    """The header's promise, pinned (PR #241 review): `DELETE` of a manifest is refused while its user exists, and
    removing the user cascades through it. The guard used to raise on the cascade too, so an exporting user could
    never be removed."""
    async with connect(database) as conn:
        await manifest(conn, "exp-cascade")
        with pytest.raises(RaiseException, match="export_snapshot_is_append_only"):
            async with conn.transaction():
                await conn.execute("DELETE FROM export_snapshot WHERE id = 'exp-cascade'")
        # the user's other rows are removed first, as a real account erasure would; then the user itself
        async with conn.transaction():
            await conn.execute("DELETE FROM turn WHERE user_id = 'u1'")
            await conn.execute("DELETE FROM session WHERE user_id = 'u1'")
            await conn.execute("DELETE FROM app_user WHERE id = 'u1'")
        assert await rows(conn, "SELECT count(*) FROM export_snapshot WHERE id = 'exp-cascade'") == [(0,)]


async def test_migration_28_rolls_back_to_the_recorded_version_27_catalog(database) -> None:  # noqa: F811
    """Migration 28 replaces only the guard's body; its rollback restores migration 25's body exactly."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        down = cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=27))
        await conn.execute(down, prepare=False)
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 27")
        assert await runner._catalog(conn) == recorded[0][0]
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION
