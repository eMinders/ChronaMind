"""E-011 r13 / migration 15: sub-scopes, the per-scope exclusion key and the scope-keyed indexes (``V2-D64``).

Migration 11 admitted only the personal root on thirteen tables and deferred everything below it to "the per-scope
``I9`` decision". This suite proves that decision: a row may lie anywhere INSIDE its owner's namespace, and nowhere
else; single-valued exclusivity is per scope (``I5``) rather than per user; belief time is monotonic per scope
(``I4``); and the facet index is led by the scope key.

Every test runs against the REAL migrated tables. The isolation tests run as a ``NOSUPERUSER NOBYPASSRLS`` role, so a
bug that only manifests for a non-bypassing principal cannot hide behind the owner's privileges.
"""

from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ExclusionViolation, RaiseException

from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, resolve_read_set, row_visible
from custom_memory_engine.scope.paths import ScopeError, parse_scope
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)

pytestmark = pytest.mark.integration
M15 = 15
PERSONAL = (
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
)


async def constraint(conn: AsyncConnection, name: str) -> str | None:
    """The scoped definition of one constraint, or ``None``. Catalog queries are pinned to the schema under test."""
    found = await rows(
        conn,
        "SELECT pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint "
        "WHERE connamespace = current_schema()::regnamespace AND conname = %s",
        (name,),
    )
    return None if not found else str(found[0][0])


async def constraints_like(conn: AsyncConnection, pattern: str) -> list[str]:
    found = await rows(
        conn,
        "SELECT conname FROM pg_catalog.pg_constraint "
        "WHERE connamespace = current_schema()::regnamespace AND conname LIKE %s ORDER BY 1",
        (pattern,),
    )
    return [str(row[0]) for row in found]


async def seed_user(conn: AsyncConnection, user_id: str) -> None:
    await conn.execute(
        "INSERT INTO app_user (id, external_ref, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, "ext-" + user_id, START),
    )


async def seed_session(conn: AsyncConnection, session_id: str, user_id: str, scope_path: str | None = None) -> None:
    if scope_path is None:
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", (session_id, user_id, START)
        )
        return
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at, scope_path) VALUES (%s, %s, %s, %s)",
        (session_id, user_id, START, scope_path),
    )


# --------------------------------------------------------------------------------- the namespace boundary (rule 1)


@pytest.mark.parametrize("table", PERSONAL, ids=lambda t: t)
async def test_every_scoped_table_admits_a_sub_scope_inside_its_owner_namespace(database, table: str) -> None:  # noqa: F811
    """The personal-root equality of migration 11 is gone on all thirteen tables: a sub-scope is admissible."""
    async with connect(database) as conn:
        assert await constraint(conn, table + "_scope_matches_user") is None, (
            "migration 11's equality guard is still installed on " + table
        )
        definition = await constraint(conn, table + "_scope_within_user")
        assert definition is not None, table + " carries no within-namespace guard"
        assert "starts_with" in definition and "/u/" in definition and "user_id" in definition


async def test_a_sub_scope_inside_the_owners_namespace_is_accepted(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_user(conn, "u2")
        await seed_session(conn, "s1", "u1")  # the v1 writer still gets the personal root
        await seed_session(conn, "s2", "u1", "/u/u1/p/repo/")  # the coding profile's root
        await seed_session(conn, "s3", "u1", "/u/u1/o/acme/")  # the enterprise profile's root
        assert await rows(conn, "SELECT id, scope_path FROM session ORDER BY id") == [
            ("s1", "/u/u1/"),
            ("s2", "/u/u1/p/repo/"),
            ("s3", "/u/u1/o/acme/"),
        ]


async def test_a_sub_scope_inside_another_tenants_namespace_is_refused(database) -> None:  # noqa: F811
    """The property migration 11 established is preserved, not weakened: `/u/u1/` never contains `/u/u2/`."""
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_user(conn, "u2")
        for index, forged in enumerate(("/u/u2/p/x/", "/u/u2/", "/u/u1x/", "/u/u1/../../u2/")):
            with pytest.raises(CheckViolation) as refused:
                await seed_session(conn, f"bad{index}", "u1", forged)
            assert "scope_within_user" in str(refused.value) or "scope_path_shape" in str(refused.value)


async def test_the_prefix_boundary_holds_for_ids_that_share_a_prefix(database) -> None:  # noqa: F811
    """`/u/4/` must not contain `/u/42/`: the reason the trailing separator exists at all (B-01)."""
    async with connect(database) as conn:
        await seed_user(conn, "4")
        await seed_user(conn, "42")
        await seed_session(conn, "ok", "4", "/u/4/s/task-1/")
        with pytest.raises(CheckViolation):
            await seed_session(conn, "bad", "4", "/u/42/")


# ------------------------------------------------------------------------------- the per-scope exclusion key (I5)


async def insert_belief(
    conn: AsyncConnection, version_id: str, user_id: str, scope_path: str, *, single_valued: bool = True
) -> None:
    """One active single-valued belief in ``scope_path``, with its evidence (Invariant 1)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, "
            "(SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM turn WHERE session_id = %s), 'user', 'x', %s)",
            ("t-" + version_id, "s-" + user_id, user_id, "s-" + user_id, START),
        )
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, kind, lifecycle, retention, observed_at, event_basis, "
            "scope_path, visibility) "
            "VALUES (%s, %s, %s, 'active', 'user', 'diet', %s, 'x', tstzrange(%s, NULL), "
            "tstzrange(clock_now(), NULL), 'semantic', 'claim', 'asserted', 'durable', %s, 'explicit', %s, 'private')",
            (version_id, version_id, user_id, single_valued, START, START, scope_path),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (version_id, "t-" + version_id, user_id),
        )


async def test_the_exclusion_key_is_the_scope_not_the_user(database) -> None:  # noqa: F811
    """``I5``: exclusivity is *within a scope*.

    Two scopes of one user each hold the predicate; the same scope holds it only once.
    """
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_session(conn, "s-u1", "u1")
        definition = await constraint(conn, "no_bitemporal_overlap")
        assert definition is not None
        assert "scope_path WITH =" in definition, "the exclusion key is not the scope: " + definition
        await insert_belief(conn, "a1", "u1", "/u/u1/p/a/")
        await insert_belief(conn, "b1", "u1", "/u/u1/p/b/")  # a sibling scope, same predicate: legal
        with pytest.raises(ExclusionViolation):
            await insert_belief(conn, "a2", "u1", "/u/u1/p/a/")  # twice in one scope: refused


async def test_a_v1_row_in_the_personal_root_still_excludes(database) -> None:  # noqa: F811
    """Re-keying must not lose the v1 guarantee: the personal root is a scope like any other."""
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_session(conn, "s-u1", "u1")
        await insert_belief(conn, "p1", "u1", "/u/u1/")
        with pytest.raises(ExclusionViolation):
            await insert_belief(conn, "p2", "u1", "/u/u1/")


async def test_two_users_with_the_same_sub_scope_do_not_collide(database) -> None:  # noqa: F811
    """The scope key must not accidentally equate `/u/u1/p/x/` with `/u/u2/p/x/`."""
    async with connect(database) as conn:
        for user in ("u1", "u2"):
            await seed_user(conn, user)
            await seed_session(conn, "s-" + user, user)
        await insert_belief(conn, "one", "u1", "/u/u1/p/x/")
        await insert_belief(conn, "two", "u2", "/u/u2/p/x/")


# ------------------------------------------------------------------------- belief time is monotonic per scope (I4)


async def test_the_watermark_is_keyed_by_scope(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        columns = await rows(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'clock_watermark' ORDER BY ordinal_position",
        )
        assert [(str(row[0]),) for row in columns] == [("scope_path",), ("high",)]


async def test_a_sibling_scope_cannot_walk_belief_time_backwards(database) -> None:  # noqa: F811
    """Under the user key a write to one scope advanced the watermark for every scope of that user."""
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_session(conn, "s-u1", "u1")
        await conn.execute("SELECT set_config('app.clock', %s, false)", (START.isoformat(),))
        await insert_belief(conn, "later", "u1", "/u/u1/p/a/")
        # The same user, an earlier instant, a DIFFERENT scope: allowed, because the watermark is per scope.
        await conn.execute("SELECT set_config('app.clock', %s, false)", ("2026-03-01T00:00:00Z",))
        await insert_belief(conn, "earlier", "u1", "/u/u1/p/b/")
        assert await rows(conn, "SELECT scope_path FROM clock_watermark ORDER BY 1") == [
            ("/u/u1/p/a/",),
            ("/u/u1/p/b/",),
        ]


async def test_belief_time_still_cannot_move_backwards_within_one_scope(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_session(conn, "s-u1", "u1")
        await conn.execute("SELECT set_config('app.clock', %s, false)", (START.isoformat(),))
        await insert_belief(conn, "first", "u1", "/u/u1/p/a/")
        await conn.execute("SELECT set_config('app.clock', %s, false)", ("2026-03-01T00:00:00Z",))
        with pytest.raises(RaiseException, match="recorded_time_moved_backwards"):
            await insert_belief(conn, "back", "u1", "/u/u1/p/a/")


# --------------------------------------------------------------------------------------- the scope-keyed index


async def test_the_facet_index_is_led_by_the_scope_path(database) -> None:  # noqa: F811
    """E-020's index note pointed here: facet-filtered reads are scope-filtered reads."""
    async with connect(database) as conn:
        found = await rows(
            conn,
            "SELECT indexdef FROM pg_catalog.pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = 'memory_version_um_facet_idx'",
        )
        assert len(found) == 1
        definition = str(found[0][0])
        assert "(scope_path text_pattern_ops, um_facet)" in definition, definition
        assert "WHERE (um_facet IS NOT NULL)" in definition


async def test_the_facet_index_serves_a_scope_and_facet_filtered_read(database) -> None:  # noqa: F811
    """A plan check, not a timing: the index is usable for the query shape the read path issues."""
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = await rows(
            conn,
            "EXPLAIN SELECT id FROM memory_version WHERE scope_path = '/u/u1/' AND um_facet = 'preference'",
        )
        rendered = " ".join(str(row[0]) for row in plan)
        assert "memory_version_um_facet_idx" in rendered, rendered


# -------------------------------------------------------------------------------------------- the grammar agrees


@pytest.mark.parametrize("path", ["/u/u1/p/repo/", "/u/u1/o/acme/", "/u/u1/s/task-1/"])
def test_the_resolver_grammar_admits_the_shapes_the_schema_now_accepts(path: str) -> None:
    """The database and `scope.paths` must agree, or a legal row would be unreadable through the resolver.

    Rule 3 makes a `private` row readable only by a selector whose path *equals* the row's scope, so the sub-scope
    is reachable through `exact(sub-scope)` (what a writer reads back) - never through the bare parent subtree.
    """
    parsed = parse_scope(path)
    assert parsed.is_within(parse_scope("/u/u1/"))
    owner = resolve_read_set("u1", [Selector.subtree("/u/u1/")], ())
    assert row_visible(owner, path, "private") is False, "a private row must not leak to the parent subtree"
    exact = resolve_read_set("u1", [Selector.exact(path)], ())
    assert row_visible(exact, path, "private") is True, "the writer must read its own row back (rule 1)"
    # A `project` row is readable from any selector whose subtree contains it.
    assert row_visible(owner, path, "project") is True


def test_a_top_level_org_root_is_still_not_a_namespace() -> None:
    """`V2-D64`: `/o/<org>/` is a *sub-scope* of a user namespace, not a namespace of its own."""
    for path in ("/o/acme/", "/org/acme/"):
        with pytest.raises(ScopeError):
            parse_scope(path)
    # ... and the enterprise root the profile document names is reachable as a sub-scope.
    assert parse_scope("/u/u1/o/acme/").is_within(parse_scope("/u/u1/"))


# ------------------------------------------------------------------------------------------------- the rollback


async def test_rollback_restores_version_14_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M15 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M15 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical, constraints included
        assert await constraints_like(conn, "%_scope_within_user") == []
        restored = await constraint(conn, "no_bitemporal_overlap")
        assert restored is not None and "user_id WITH =" in restored
        assert await constraint(conn, "session_scope_matches_user") is not None
        watermark = await rows(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'clock_watermark' ORDER BY ordinal_position",
        )
        assert [str(row[0]) for row in watermark] == ["user_id", "high"]
    assert (await runner.status()).version == M15 - 1
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await constraint(conn, "session_scope_within_user") is not None
        rekeyed = await constraint(conn, "no_bitemporal_overlap")
        assert rekeyed is not None and "scope_path WITH =" in rekeyed


async def test_rollback_refuses_while_a_sub_scope_row_exists(database) -> None:  # noqa: F811
    """Version 14 cannot represent a sub-scope row; flattening it into the root would merge two scopes."""
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_user(conn, "u1")
        await seed_session(conn, "sub", "u1", "/u/u1/p/repo/")
        with pytest.raises(RaiseException, match="sub_scope_rows_present_rollback_refused"):
            await conn.execute(cast(LiteralString, rollback_sql(from_version=M15, to_version=M15 - 1)), prepare=False)
        # Nothing was changed by the refused rollback.
        assert await constraint(conn, "session_scope_within_user") is not None
    assert (await runner.status()).version == migrate.VERSION
