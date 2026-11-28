"""E-040 `create_scope`: the scope-creation operation, reachable only through ``Engine.call``.

``E-011`` migrated the scope columns, the path grammar and the ACL policy but left this operation unwired, because
while every scope was the personal root a created sub-scope could hold no row. Migration 15 admits a row anywhere
inside its owner's namespace (``V2-D64``), so the operation now creates something usable, which is what these tests
establish - through the public entry point, never against a table.

A scope has no row of its own: it is a canonical path, so "creating" one is validating ``parent + segment``, refusing
anything outside the caller's namespace, recording an idempotent answer, and leaving a content-free audit trail.
"""

from collections.abc import Mapping

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import CallerContext, Engine, EntryError
from custom_memory_engine.scope.paths import MAX_DEPTH
from tests.integration.test_api_wiring_structure import caller, database, forbidden, world  # noqa: F401
from tests.integration.test_migration_m1 import rows

pytestmark = pytest.mark.integration

BASE = {"parent": "/u/alice/", "segment": "repo", "kind": "project"}


def arguments(**overrides: object) -> dict[str, object]:
    return {**BASE, "idempotency_key": "k1", **overrides}


async def audit_rows(conn: AsyncConnection, event: str) -> list[tuple[object, ...]]:
    cursor = await conn.execute(
        "SELECT user_id, event_type, details::text FROM audit_event WHERE event_type = %s ORDER BY id", (event,)
    )
    return [tuple(row) for row in await cursor.fetchall()]


async def assert_statements_blocked(engine: Engine, ctx: CallerContext, arguments: Mapping[str, object]) -> EntryError:
    """The refusal must arrive before any statement: the resolver is never consulted, so no I/O can have happened."""
    calls: list[object] = []

    class Tripwire:
        async def resolve(self, *_args: object) -> Mapping[str, object]:
            calls.append(_args)
            raise AssertionError("a statement would have run")

    engine.resolver = Tripwire()  # type: ignore[assignment]
    error = await forbidden(engine, ctx, "create_scope", arguments)
    assert calls == []
    return error


# ------------------------------------------------------------------------------- acceptance: wired and reachable


async def test_create_scope_is_wired_and_no_longer_reported_as_unwired(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _schema):
        assert "create_scope" not in engine.unwired_operations()


async def test_a_project_scope_inside_the_callers_namespace_is_created(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        assert await engine.call(caller("alice"), "create_scope", arguments()) == "/u/alice/repo/"


@pytest.mark.parametrize("kind", ["project", "session", "task", "shared"])
async def test_every_creatable_kind_is_accepted(database, kind: str) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        created = await engine.call(caller("alice"), "create_scope", arguments(kind=kind, idempotency_key="k-" + kind))
        assert created == "/u/alice/repo/"


async def test_a_personal_root_cannot_be_minted(database) -> None:  # noqa: F811
    """`personal` is the `/u/<user>/` root, which exists with the user; it is not something this operation makes."""
    async with world(database) as (_, engine, _):
        error = await forbidden(engine, caller("alice"), "create_scope", arguments(kind="personal"))
        assert (error.code, error.detail) == ("validation_error", "scope_kind_invalid")


async def test_nested_scopes_are_created_below_an_existing_sub_scope(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        await engine.call(caller("alice"), "create_scope", arguments(idempotency_key="a"))
        nested = await engine.call(
            caller("alice"),
            "create_scope",
            arguments(parent="/u/alice/repo/", segment="task-1", kind="task", idempotency_key="b"),
        )
        assert nested == "/u/alice/repo/task-1/"


# --------------------------------------------------------------------------- acceptance: authorisation and grammar


async def test_a_tier_b_caller_is_forbidden_before_any_statement(database) -> None:  # noqa: F811
    """`create_scope` is Tier C only (`API_V2.md` §3): a Tier B credential never reaches a handler."""
    async with world(database) as (_, engine, _):
        error = await assert_statements_blocked(engine, caller("alice", "B"), arguments())
        assert error.code == "forbidden"


async def test_a_parent_outside_the_callers_namespace_is_forbidden_and_reveals_nothing(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        outside = await assert_statements_blocked(engine, caller("alice"), arguments(parent="/u/bob/"))
        assert (outside.code, outside.detail) == ("forbidden", None)
        # The same answer as any other forbidden target, so it says nothing about whether /u/bob/ exists.
        other = await forbidden(engine, caller("alice"), "create_scope", arguments(parent="/u/x/"))
        assert outside.code == other.code == "forbidden"


async def test_the_mutation_test_names_the_namespace_check(database) -> None:  # noqa: F811
    """E-039's pattern lives in `tests/test_api_entry.py`; this pins that `create_scope` is the case it uses."""
    from tests.test_api_entry import VIOLATIONS

    operation, overrides, *_ = VIOLATIONS["namespace_owner_only"]
    assert operation == "create_scope" and overrides == {"parent": "/u/bob/"}


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"parent": "/u/alice"}, "scope_invalid"),  # no trailing separator
        ({"parent": "/u/alice//"}, "scope_invalid"),  # empty segment
        ({"parent": "alice/"}, "scope_invalid"),  # not rooted
        ({"segment": "Repo"}, "scope_invalid"),  # uppercase is outside the grammar
        ({"segment": "with space"}, "scope_invalid"),
        ({"segment": "a/b"}, "scope_invalid"),
        ({"segment": ""}, "segment_required"),
        ({"kind": "organization"}, "scope_kind_invalid"),
    ],
)
async def test_malformed_arguments_are_validation_errors(database, overrides: dict, detail: str) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        error = await forbidden(engine, caller("alice"), "create_scope", arguments(**overrides))
        assert (error.code, error.detail) == ("validation_error", detail)


async def test_a_scope_deeper_than_the_grammar_allows_is_refused(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _):
        deep = "/u/alice/" + "s/" * (MAX_DEPTH - 2)
        error = await forbidden(engine, caller("alice"), "create_scope", arguments(parent=deep))
        assert (error.code, error.detail) == ("validation_error", "scope_invalid")


# ------------------------------------------------------------------------------------ acceptance: idempotent replay


async def test_a_replayed_key_returns_the_original_path_and_creates_nothing_again(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        first = await engine.call(caller("alice"), "create_scope", arguments())
        second = await engine.call(caller("alice"), "create_scope", arguments())
        assert first == second == "/u/alice/repo/"
        entries = await rows(conn, "SELECT count(*) FROM api_idempotency WHERE operation = 'create_scope'")
        assert entries == [(1,)]
        assert len(await audit_rows(conn, "scope_created")) == 1, "a replay must not leave a second audit row"


async def test_two_keys_creating_the_same_scope_both_succeed(database) -> None:  # noqa: F811
    """The path is the identity, so the same target under a new key is the answer, not a conflict."""
    async with world(database) as (_, engine, _):
        assert await engine.call(caller("alice"), "create_scope", arguments(idempotency_key="a")) == "/u/alice/repo/"
        assert await engine.call(caller("alice"), "create_scope", arguments(idempotency_key="b")) == "/u/alice/repo/"


async def test_another_callers_replay_does_not_leak_a_scope(database) -> None:  # noqa: F811
    """Idempotency is per user: bob's own key creates his own scope and never returns alice's answer."""
    async with world(database) as (_, engine, _):
        await engine.call(caller("alice"), "create_scope", arguments())
        assert (
            await engine.call(caller("bob"), "create_scope", arguments(parent="/u/bob/", idempotency_key="k1"))
            == "/u/bob/repo/"
        )


# ------------------------------------------------------------------------------------ acceptance: content-free audit


async def test_the_audit_row_carries_no_path_segment_or_content(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await engine.call(caller("alice"), "create_scope", arguments(segment="secret-project"))
        ((user_id, event_type, details),) = await audit_rows(conn, "scope_created")
        assert (user_id, event_type) == ("alice", "scope_created")
        rendered = str(details)
        assert "secret-project" not in rendered and "alice" not in rendered and "/u/" not in rendered
        assert '"scope_kind": "project"' in rendered and '"depth": 3' in rendered


# ------------------------------------------------------------------- acceptance: the created scope is actually usable


async def test_an_entity_can_live_in_the_created_scope(database) -> None:  # noqa: F811
    """The property E-011 could not offer and migration 15 can: a sub-scope holds rows."""
    async with world(database) as (conn, engine, _):
        scope = await engine.call(caller("alice"), "create_scope", arguments())
        entity = await engine.call(
            caller("alice"),
            "create_entity",
            {"scope": scope, "entity_type": "repository", "label": "chronamind", "idempotency_key": "e1"},
        )
        assert await rows(conn, "SELECT scope_path FROM entity WHERE id = %s", (entity,)) == [(scope,)]


async def test_a_memory_version_can_live_in_the_created_scope(database) -> None:  # noqa: F811
    """The thirteen tables migration 15 relaxed, not just `entity`: a session row in the new scope."""
    async with world(database) as (conn, engine, _):
        scope = await engine.call(caller("alice"), "create_scope", arguments())
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        await conn.execute(
            "INSERT INTO session (id, user_id, started_at, scope_path) VALUES ('s-new', 'alice', clock_now(), %s)",
            (scope,),
        )
        assert await rows(conn, "SELECT scope_path FROM session WHERE id = 's-new'") == [(scope,)]
