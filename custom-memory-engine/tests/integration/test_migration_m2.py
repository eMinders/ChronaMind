"""E-011 / M2 + M9: scope paths, visibility and the scope read policy, proven on the REAL migrated tables.

``test_scope_isolation.py`` proves the primitives on a scratch table. The r12 correction to issue #62 named the
gap: every case must run "against the migrated schema". These tests do, as a ``NOSUPERUSER NOBYPASSRLS`` role, with no
application filter at all.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import CheckViolation, InsufficientPrivilege, UniqueViolation

from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, acl_readable, resolve_read_set, rls_read_using
from custom_memory_engine.scope.audit import record_scope_read
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)
from tests.scope_isolation_cases import ROWS, USERS

pytestmark = pytest.mark.integration
M2 = 11
T0 = datetime(2026, 3, 10, tzinfo=UTC)
SCOPED = (
    "memory_version",
    "memory_evidence",
    "memory_derivation",
    "memory_embedding",
    "memory_conflict",
    "turn",
    "session",
    "job",
    "audit_event",
    "deletion_request",
    "association",
    "abstraction_stats",
    "verification",
    "entity",
    "negative_constraint",
)
#: The universe MA-011 fixed, restricted to what can exist on the real tables while every scope is personal (D11):
#: a row lies in its owner's own root, `/u/<user>/`. Sub-scope rows stay covered by the scratch-table suite.
PERSONAL_ROWS = tuple((path, visibility) for path, visibility in ROWS if path.count("/") == 3)
OWN_USER = "(user_id = NULLIF(current_setting('app.user_id'::text, true), ''::text))"


async def seed_users(conn: AsyncConnection) -> None:
    for user in USERS:
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", ("s-" + user, user, T0)
        )
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, 1, 'user', 'x', %s)",
            ("t-" + user, "s-" + user, user, T0),
        )


async def insert_row(conn: AsyncConnection, number: int, path: str, visibility: str) -> None:
    """One real `memory_version` (with its evidence) in `path`; the owner is the user the path names."""
    owner = path.split("/")[2]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (owner,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, scope_path, visibility) "
            "VALUES (%s, %s, %s, 'active', 'user', "
            "'diet', false, 'x', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', %s, %s)",
            (f"v{number}", f"k{number}", owner, START, path, visibility),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (f"v{number}", "t-" + owner, owner),
        )


@asynccontextmanager
async def restricted(database) -> AsyncIterator[tuple[AsyncConnection, str]]:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.clock', %s, false)", (T0.isoformat(),))
        await seed_users(conn)
        await provision_runtime_role(conn, schema, role)
        for number, (path, visibility) in enumerate(PERSONAL_ROWS):
            await insert_row(conn, number, path, visibility)
        try:
            yield conn, role
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def visible(conn: AsyncConnection, role: str, caller: str) -> frozenset[tuple[str, str]]:
    async with tenant_scope(conn, caller, role=role):
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT scope_path, visibility FROM memory_version")  # no application filter
            return frozenset((str(a), str(b)) for a, b in await cursor.fetchall())


def expected(caller: str, grants: tuple[str, ...] = ()) -> frozenset[tuple[str, str]]:
    return frozenset(r for r in PERSONAL_ROWS if acl_readable(caller, grants, *r))


async def grant(conn: AsyncConnection, role: str, owner: str, principal: str, path: str) -> str:
    grant_id = "g-" + uuid4().hex[:12]
    async with tenant_scope(conn, owner, role=role):
        await conn.execute(
            "INSERT INTO scope_grant (id, owner_user_id, principal_id, scope_path, granted_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (grant_id, owner, principal, path, T0),
        )
    return grant_id


# ------------------------------------------------------------------------------- the MA-011 cases, on real tables


async def test_rls_alone_returns_exactly_the_acl_for_every_caller_on_the_real_table(database) -> None:  # noqa: F811
    async with restricted(database) as (conn, role):
        assert len(PERSONAL_ROWS) >= 6
        for user in USERS:
            assert await visible(conn, role, user) == expected(user), user
        assert ("/u/42/", "private") not in await visible(conn, role, "4")  # /u/4/ never reaches /u/42/
        assert not any(path.startswith("/u/42/") for path, _ in await visible(conn, role, "4"))
        assert ("/u/aXb/", "private") not in await visible(conn, role, "a_b")  # '_' is not a wildcard


async def test_grants_expose_project_and_shared_rows_never_private_ones_and_revocation_closes_them(database) -> None:  # noqa: F811
    async with restricted(database) as (conn, role):
        grant_id = await grant(conn, role, "42", "4", "/u/42/")
        seen = await visible(conn, role, "4")
        assert seen == expected("4", ("/u/42/",))
        assert ("/u/42/", "shared") in seen and ("/u/42/", "private") not in seen
        assert await visible(conn, role, "4x") == expected("4x"), "a grant to 4 is not a grant to 4x"
        async with tenant_scope(conn, "42", role=role):
            await conn.execute("UPDATE scope_grant SET revoked_at = %s WHERE id = %s", (T0, grant_id))
        assert await visible(conn, role, "4") == expected("4")


async def visible_entities(conn: AsyncConnection, role: str, caller: str) -> frozenset[tuple[str, str]]:
    async with tenant_scope(conn, caller, role=role):
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT scope_path, visibility FROM entity")  # no application filter
            return frozenset((str(a), str(b)) for a, b in await cursor.fetchall())


async def test_the_full_universe_with_sub_scopes_holds_on_the_real_entity_table(database) -> None:  # noqa: F811
    """`entity` admits sub-scope paths, so every MA-011 row, not only the personal roots, runs on a real table."""
    async with restricted(database) as (conn, role):
        for number, (path, visibility) in enumerate(ROWS):
            owner = path.split("/")[2]
            async with tenant_scope(conn, owner, role=role):
                await conn.execute(
                    "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, visibility) "
                    "VALUES (%s, %s, %s, 'n', 't', %s)",
                    (f"e{number}", owner, path, visibility),
                )
        for user in USERS:
            assert await visible_entities(conn, role, user) == frozenset(
                r for r in ROWS if acl_readable(user, (), *r)
            ), user
        grant_id = await grant(conn, role, "42", "4", "/u/42/")
        await grant(conn, role, "40", "4", "/u/40/p/")
        seen = await visible_entities(conn, role, "4")
        assert seen == frozenset(r for r in ROWS if acl_readable("4", ("/u/42/", "/u/40/p/"), *r))
        assert ("/u/40/p/a/", "shared") in seen and ("/u/42/", "private") not in seen
        assert await visible_entities(conn, role, "4x") == frozenset(r for r in ROWS if acl_readable("4x", (), *r))
        async with tenant_scope(conn, "42", role=role):
            await conn.execute("UPDATE scope_grant SET revoked_at = %s WHERE id = %s", (T0, grant_id))
        assert await visible_entities(conn, role, "4") == frozenset(
            r for r in ROWS if acl_readable("4", ("/u/40/p/",), *r)
        )


@pytest.mark.parametrize("bad", ["/u/4/p", "/u/4//", "/u/4/../", "/u/4/P/", "/u/4/-p/"])
async def test_a_sub_scope_path_that_is_not_canonical_is_refused(database, bad: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        await conn.execute("INSERT INTO app_user VALUES ('4', 'x4', now())")
        with pytest.raises(CheckViolation, match="entity_scope_path_shape"):
            await conn.execute(
                "INSERT INTO entity (user_id, scope_path, canonical_name, entity_type) VALUES ('4', %s, 'n', 't')",
                (bad,),
            )


@pytest.mark.parametrize("identity", [None, "", "4/p", "a_b/p"])
async def test_an_unusable_identity_reads_nothing_on_the_real_table(database, identity: str | None) -> None:  # noqa: F811
    async with restricted(database) as (conn, role):
        async with conn.transaction():
            await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            if identity is not None:
                await conn.execute("SELECT set_config('app.user_id', %s, true)", (identity,))
            assert await rows(conn, "SELECT count(*) FROM memory_version") == [(0,)]


async def test_holding_a_grant_never_authorises_a_write(database) -> None:  # noqa: F811
    """The read policy is SELECT-only: a grantee reads a shared row but cannot change, delete or add to it (V2-D53)."""
    async with restricted(database) as (conn, role):
        await grant(conn, role, "42", "4", "/u/42/")
        async with tenant_scope(conn, "4", role=role):
            assert ("/u/42/", "shared") in {
                (str(a), str(b)) for a, b in await rows(conn, "SELECT scope_path, visibility FROM memory_version")
            }
            await conn.execute(
                "UPDATE memory_version SET reinforcement_count = reinforcement_count + 1 WHERE user_id = '42'"
            )
            assert await rows(
                conn, "SELECT count(*) FROM memory_version WHERE user_id = '42' AND reinforcement_count > 0"
            ) == [(0,)]
            await conn.execute("DELETE FROM memory_version WHERE user_id = '42'")
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE user_id = '42'") == [(2,)]
        with pytest.raises(
            Exception, match="row-level security"
        ):  # INSERT ... WITH CHECK: not for another user's root
            async with tenant_scope(conn, "4", role=role):
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, layer, scope_path) "
                    "VALUES ('evil', 'k', '42', 'rejected', 'u', "
                    "'diet', false, 'x', tstzrange(%s, NULL), 'semantic', '/u/42/')",
                    (START,),
                )


async def test_the_read_policy_is_the_library_expression_on_every_scoped_table(database) -> None:  # noqa: F811
    """Every scoped table carries `<table>_scope_read`, and its text is `scope.access.rls_read_using`'s."""
    async with connect(database) as conn:
        for table in SCOPED:
            (found,) = await rows(
                conn,
                "SELECT polcmd, pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polname = %s AND polrelid = %s::regclass",
                (f"{table}_scope_read", table),
            )
            assert found[0] == "r", table  # SELECT only
            # The library expression, deparsed by the server the same way, must be the installed one exactly.
            async with conn.transaction(force_rollback=True):
                await conn.execute(
                    sql.SQL("CREATE POLICY reference_read ON {} FOR SELECT USING ({})").format(
                        sql.Identifier(table), rls_read_using(table)
                    )
                )
                (reference,) = await rows(
                    conn,
                    "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                    "WHERE polname = 'reference_read' AND polrelid = %s::regclass",
                    (table,),
                )
            assert found[1] == reference[0], table
            # Apart from the read policy, the only policy is the v1 own-user one, governing writes too.
            assert await rows(
                conn,
                "SELECT polname, polcmd, polpermissive, pg_get_expr(polqual, polrelid), "
                "pg_get_expr(polwithcheck, polrelid) FROM pg_policy "
                "WHERE polrelid = %s::regclass AND polname <> %s",
                (table, f"{table}_scope_read"),
            ) == [(f"{table}_isolation", "*", True, OWN_USER, OWN_USER)], table
        for table in SCOPED:
            assert await rows(
                conn,
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (table,),
            ) == [(True, True)], table
            assert await rows(
                conn,
                "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() AND indexname = %s",
                (f"{table}_scope_path_idx",),
            ) == [(1,)], table


async def test_the_prefix_index_serves_a_starts_with_subtree_query(database) -> None:  # noqa: F811
    async with restricted(database) as (conn, _):
        await conn.execute("SET enable_seqscan = off")
        plan = " ".join(
            str(r[0])
            for r in await rows(conn, "EXPLAIN SELECT 1 FROM memory_version WHERE starts_with(scope_path, '/u/4/')")
        )
        assert "memory_version_scope_path_idx" in plan


# ------------------------------------------------------------------------------- D11, D12, D13


async def test_user_id_stays_stored_and_a_v1_writer_still_gets_the_personal_root(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await conn.execute("INSERT INTO app_user VALUES ('u9', 'x9', now())")
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES ('s9', 'u9', now())"
        )  # names only user_id
        assert await rows(conn, "SELECT user_id, scope_path, visibility FROM session WHERE id = 's9'") == [
            ("u9", "/u/u9/", "private")
        ]
        with pytest.raises(CheckViolation, match="scope_within_user"):  # an inconsistent dual-write is refused
            await conn.execute(
                "INSERT INTO session (id, user_id, started_at, scope_path) VALUES ('bad', 'u9', now(), '/u/u1/')"
            )
        with pytest.raises(CheckViolation, match="visibility_check"):
            await conn.execute(
                "INSERT INTO session (id, user_id, started_at, visibility) VALUES ('bad', 'u9', now(), 'public')"
            )


async def test_the_job_idempotency_key_is_unique_per_user_not_globally(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        for user, job in (("u1", "j1"), ("u2", "j2")):
            await conn.execute(
                "INSERT INTO job (id, user_id, kind, idempotency_key, status, attempts, next_run_at) "
                "VALUES (%s, %s, 'k', 'same-key', 'queued', 0, now())",
                (job, user),
            )
        with pytest.raises(UniqueViolation):  # still unique for one user
            await conn.execute(
                "INSERT INTO job (id, user_id, kind, idempotency_key, status, attempts, next_run_at) "
                "VALUES ('j3', 'u1', 'k', 'same-key', 'queued', 0, now())"
            )


@pytest.mark.parametrize("bad", ["a/b", "..", ".", " padded", "", "x" * 257, "ctrl\x07char"])
async def test_an_app_user_id_that_could_forge_a_path_is_refused(database, bad: str) -> None:  # noqa: F811
    async with connect(database) as conn:
        with pytest.raises(CheckViolation, match="app_user_id_is_scope_segment"):
            await conn.execute("INSERT INTO app_user VALUES (%s, 'x', now())", (bad,))


async def test_the_migration_refuses_to_run_over_an_id_that_could_forge_a_path_and_changes_nothing(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M2 - 1)), prepare=False
        )
        await conn.execute("INSERT INTO app_user VALUES ('evil/../u1', 'x', now())")
        before = await runner._catalog(conn)
    with pytest.raises(migrate.MigrationError):
        await runner.apply()
    async with connect(database) as conn:
        assert await runner._catalog(conn) == before  # fail closed: the schema is exactly at the previous version
        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(M2 - 1,)]


# ------------------------------------------------------------------------------- backfill, audit, rollback


async def test_backfill_maps_every_existing_row_to_its_personal_private_root(database) -> None:  # noqa: F811
    config, schema = database
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M2 - 1)), prepare=False
        )
        await conn.execute("SELECT set_config('app.clock', %s, false)", (T0.isoformat(),))
        await seed_users(conn)
        counts = {
            t: (await rows(conn, cast(LiteralString, f"SELECT count(*) FROM {t}")))[0][0] for t in ("session", "turn")
        }
    await migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=config.database_url, schema_name=schema)
    ).apply()
    async with connect(database) as conn:
        for table, count in counts.items():
            assert (await rows(conn, cast(LiteralString, f"SELECT count(*) FROM {table}")))[0][0] == count
            assert await rows(
                conn,
                cast(
                    LiteralString,
                    f"SELECT count(*) FROM {table} "
                    "WHERE scope_path <> '/u/' || user_id || '/' OR visibility <> 'private'",
                ),
            ) == [(0,)]
        assert await rows(conn, "SELECT count(*) FROM turn WHERE user_id IN ('4', '42')") == [
            (2,)
        ]  # user_id untouched
        # FORCE was lifted only for the backfill and restored
        assert await rows(conn, "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'turn'::regclass") == [(True,)]


async def test_a_cross_scope_read_leaves_a_content_free_audit_row_and_an_own_read_leaves_none(database) -> None:  # noqa: F811
    _, schema = database
    async with restricted(database) as (conn, _):
        grants = ("/u/42/",)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', '4', true)")
            own = resolve_read_set("4", [Selector.of("/u/4/")], grants)
            assert await record_scope_read(conn, "4", own, schema_name=schema) is None
            cross = resolve_read_set("4", [Selector.of("/u/4/"), Selector.of("/u/42/")], grants)
            event = await record_scope_read(conn, "4", cross, schema_name=schema)
        assert event is not None
        (details,) = (await rows(conn, "SELECT details::text FROM audit_event WHERE id = %s", (event,)))[0]
        assert '"cross_scope_count": 1' in str(details) and '"selector_count": 2' in str(details)
        assert "/u/42/" not in str(details) and "42" not in str(details).replace("2", "").replace(
            '"selector_count": ', ""
        )
        with pytest.raises(InsufficientPrivilege):  # the identity in the transaction must be the reader
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', '42', true)")
                await record_scope_read(conn, "4", cross, schema_name=schema)


async def test_rollback_restores_version_10_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M2 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M2 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical, policies and job key included
        assert await rows(
            conn,
            "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
            "WHERE c.relnamespace = current_schema()::regnamespace AND p.polname LIKE '%%_scope_read'",
        ) == [(0,)]
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M2 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(
            conn,
            "SELECT count(*) FROM pg_policy "
            "WHERE polname LIKE '%%_scope_read' AND polrelid::regclass::text = 'memory_version'",
        ) == [(1,)]
