"""E-039: the structure, lifecycle and scope-administration operations, driven through ``Engine.call`` only.

Everything runs as a ``NOSUPERUSER NOBYPASSRLS`` role against the real migrated schema. Rows are seeded by the owner
(that is "the world"); every operation under test goes through the entry point with a credential-built caller.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql

from custom_memory_engine.api import handlers as handlers_module
from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.handlers import register_structure_handlers
from custom_memory_engine.api.runtime import DbMethods, DbTargets, ForeignKeyPrincipals, Receipt, Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role
from tests.integration.test_migration_m1 import (
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)
HASH = "c" * 64
WIRED = {
    "register_method",
    "create_entity",
    "create_scope",
    "link",
    "unlink",
    "adjudicate",
    "retract_own",
    "grant_access",
    "revoke_access",
}


def caller(user: str, tier: Tier = "C", *, admin: bool = False) -> CallerContext:
    read_set = resolve_read_set(user, [Selector.of(f"/u/{user}/")], [])
    return CallerContext.from_credential(Credential(user, tier, admin), write_scope=f"/u/{user}/", read_set=read_set)


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[AsyncConnection, Engine, str]]:  # noqa: F811
    """Two users (alice, bob), a restricted role, and an engine wired exactly as production would wire it."""
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.clock', %s, false)", (T0.isoformat(),))
        for user in ("alice", "bob"):
            await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
            await conn.execute(
                "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)", ("s-" + user, user, T0)
            )
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 1, 'user', 'x', %s)",
                ("t-" + user, "s-" + user, user, T0),
            )
        await provision_runtime_role(conn, schema, role)
        runtime = Runtime(conn, role, schema)
        # The one production wiring, so this fixture cannot drift from what a deployment builds (E-011 rule 6).
        engine = build_engine(runtime)
        register_structure_handlers(engine, runtime)
        try:
            yield conn, engine, schema
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def make_version(
    conn: AsyncConnection,
    version_id: str,
    user: str = "alice",
    *,
    kind: str = "claim",
    held: bool = False,
    visibility: str = "private",
    created: tuple[str, str] | None = None,
) -> None:
    """One artifact owned by `user`: believed (asserted) or held (contested). `created` is (principal, tier)."""
    layer = {"claim": "semantic", "abstraction": "persona", "episode": "episodic"}[kind]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, visibility, created_by, created_tier) "
            "VALUES (%s, %s, %s, %s, 'user', 'diet', false, 'x', tstzrange(%s, NULL), "
            "CASE WHEN %s THEN NULL ELSE tstzrange(clock_now(), NULL) END, %s, %s, %s, %s)",
            (
                version_id,
                version_id,
                user,
                "conflict_pending" if held else "active",
                T0,
                held,
                layer,
                visibility,
                *(created or (None, None)),
            ),
        )
        # a held candidate has its evidence too (the gate only holds grounded candidates)
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (version_id, "t-" + user, user),
        )


async def forbidden(engine: Engine, ctx: CallerContext, operation: str, arguments: Mapping[str, object]) -> EntryError:
    with pytest.raises(EntryError) as raised:
        await engine.call(ctx, operation, arguments)
    return raised.value


# ----------------------------------------------------------------------------------------------- wiring


async def test_exactly_these_operations_are_wired_and_the_rest_stay_closed(database) -> None:  # noqa: F811
    async with world(database) as (_, engine, _schema):
        assert WIRED.isdisjoint(engine.unwired_operations())
        assert len(engine.unwired_operations()) == 29 - len(WIRED)
        error = await forbidden(engine, caller("alice"), "cost", {"window": None})  # unwired: fail closed
        assert (error.code, error.detail) == ("forbidden", "operation_not_wired")


# ----------------------------------------------------------------------------------------------- METHOD


async def test_register_method_is_admin_only_idempotent_and_never_redefines_a_version(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        args = {
            "name": "cooccurrence",
            "version": "1",
            "params_schema_hash": HASH,
            "description": "d",
            "idempotency_key": "k",
        }
        assert (await forbidden(engine, caller("alice"), "register_method", args)).code == "forbidden"  # not admin
        assert (await forbidden(engine, caller("alice", "B", admin=True), "register_method", args)).code == "forbidden"
        first = await engine.call(caller("alice", admin=True), "register_method", args)
        assert isinstance(first, str) and first.startswith("m_")  # engine-generated, never caller-chosen
        assert await engine.call(caller("alice", admin=True), "register_method", args) == first  # same hash: same id
        different = await forbidden(
            engine, caller("alice", admin=True), "register_method", {**args, "params_schema_hash": "d" * 64}
        )
        assert (different.code, different.detail) == (
            "precondition_failed",
            "method_version_exists_with_a_different_hash",
        )
        assert await rows(conn, "SELECT count(*) FROM method WHERE name = 'cooccurrence'") == [(1,)]
        bad = await forbidden(
            engine, caller("alice", admin=True), "register_method", {**args, "params_schema_hash": "xyz"}
        )
        assert (bad.code, bad.detail) == ("validation_error", "method_fields_invalid")
        # the registry is what `infer` consults (condition `registered_method`)
        assert await engine.methods.is_registered(
            caller("bob", "B"), {"id": first, "version": "1", "params_hash": HASH}
        )
        assert not await engine.methods.is_registered(
            caller("bob", "B"), {"id": first, "version": "2", "params_hash": HASH}
        )


# ----------------------------------------------------------------------------------------------- ENTITY


async def test_create_entity_replays_by_key_and_is_invisible_to_everyone_else(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        args = {"scope": "/u/alice/", "entity_type": "person", "label": "Ada", "idempotency_key": "e1"}
        first = await engine.call(caller("alice"), "create_entity", args)
        assert isinstance(first, str) and first.startswith("ent_") and len(first) == 36
        assert (
            await engine.call(caller("alice"), "create_entity", args) == first
        )  # a replay: the same id, no second row
        other = await engine.call(caller("alice"), "create_entity", {**args, "idempotency_key": "e2"})
        assert other != first
        assert await rows(conn, "SELECT count(*) FROM entity WHERE user_id = 'alice'") == [(2,)]
        # alice's key is not a collision for bob (D12's lesson), and bob cannot see alice's entity
        bobs = await engine.call(caller("bob"), "create_entity", {**args, "scope": "/u/bob/"})
        assert bobs not in (first, other)
        assert await DbTargets(engine.resolver.runtime).resolve(caller("bob"), [first]) == {}  # type: ignore[attr-defined]
        wrong_scope = await forbidden(engine, caller("alice"), "create_entity", {**args, "scope": "/u/bob/"})
        assert wrong_scope.code == "forbidden"
        blank = await forbidden(
            engine, caller("alice"), "create_entity", {**args, "label": "  ", "idempotency_key": "e3"}
        )
        assert (blank.code, blank.detail) == ("validation_error", "label_required")


@pytest.mark.parametrize("operation", ["create_entity", "grant_access"])
async def test_concurrent_calls_with_one_key_have_one_effect_and_one_answer(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Two callers racing on the same key: both get the original result, and there is exactly one row (I9)."""
    args: dict[str, object] = {"scope": "/u/alice/", "idempotency_key": "race"}
    if operation == "create_entity":
        args |= {"entity_type": "person", "label": "Ada"}
    else:
        args |= {"principal": "bob"}
    release = asyncio.Event()
    real_remember = handlers_module.remember

    async def slow_remember(*a: object, **kw: object) -> None:
        await release.wait()  # hold the first writer inside its transaction until the second has started
        await real_remember(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(handlers_module, "remember", slow_remember)
    async with world(database) as (conn, engine, schema), connect(database) as other_conn:
        role = engine.resolver.runtime.role  # type: ignore[attr-defined]
        other = Engine(
            resolver=DbTargets(Runtime(other_conn, role, schema)),
            methods=DbMethods(Runtime(other_conn, role, schema)),
            principals=ForeignKeyPrincipals(),
        )
        register_structure_handlers(other, Runtime(other_conn, role, schema))
        first = asyncio.create_task(engine.call(caller("alice"), operation, args))
        second = asyncio.create_task(other.call(caller("alice"), operation, args))
        await asyncio.sleep(0.3)
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert not any(isinstance(r, BaseException) for r in results), results
        assert results[0] == results[1]
        table = "entity WHERE user_id = 'alice'" if operation == "create_entity" else "scope_grant"
        assert await rows(conn, f"SELECT count(*) FROM {table}") == [(1,)]  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------- ASSOCIATION


async def test_link_and_unlink_never_touch_the_endpoints_and_record_the_origin(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "x")
        await make_version(conn, "y")
        edge = await engine.call(caller("alice"), "link", {"from": "x", "to": "y", "type": "related_to"})
        assert isinstance(edge, str) and edge.startswith("assoc_")
        assert await engine.call(caller("alice"), "link", {"from": "x", "to": "y", "type": "related_to"}) == edge
        c = await engine.call(caller("alice"), "link", {"from": "x", "to": "y", "type": "contradicts"})
        b = await engine.call(caller("alice", "B"), "link", {"from": "y", "to": "x", "type": "contradicts"})
        assert {str(r[0]): r[1] for r in await rows(conn, "SELECT id, origin FROM association")} == {
            edge: "tier_c",
            c: "tier_c",
            b: "tier_b",
        }
        before = await rows(conn, "SELECT id, lifecycle, status FROM memory_version ORDER BY id")
        receipt = await engine.call(caller("alice"), "unlink", {"association_id": c})
        assert isinstance(receipt, Receipt) and (receipt.id, receipt.effect) == (c, "unlinked")
        assert await rows(conn, "SELECT id, lifecycle, status FROM memory_version ORDER BY id") == before  # no cascade
        assert (await forbidden(engine, caller("alice"), "unlink", {"association_id": c})).code == "forbidden"  # gone
        assert (await forbidden(engine, caller("bob"), "unlink", {"association_id": edge})).code == "forbidden"
        assert await rows(conn, "SELECT count(*) FROM association WHERE id = %s", (edge,)) == [(1,)]


async def test_a_foreign_endpoint_is_indistinguishable_from_a_missing_one(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "mine")
        await make_version(conn, "bobs", "bob")
        errors = [
            await forbidden(engine, caller("alice"), "link", {"from": "mine", "to": target, "type": "related_to"})
            for target in ("bobs", "does-not-exist")
        ]
        assert errors[0].code == errors[1].code == "forbidden" and errors[0].detail == errors[1].detail
        assert await rows(conn, "SELECT count(*) FROM association") == [(0,)]


async def test_the_association_degree_cap_is_a_resource_limit(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "hub")
        for number in range(32):
            await make_version(conn, f"s{number}")
            await engine.call(caller("alice"), "link", {"from": "hub", "to": f"s{number}", "type": "related_to"})
        await make_version(conn, "one-too-many")
        error = await forbidden(
            engine, caller("alice"), "link", {"from": "hub", "to": "one-too-many", "type": "related_to"}
        )
        assert error.code == "resource_limit"


# ----------------------------------------------------------------------------------------------- LIFECYCLE


async def test_adjudicate_confirms_or_denies_a_contested_artifact_with_a_recorded_decision(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        for version_id in ("c1", "c2"):
            await make_version(conn, version_id, held=True)
        confirmed = await engine.call(caller("alice"), "adjudicate", {"artifact_id": "c1", "decision": "confirm"})
        assert isinstance(confirmed, Receipt) and confirmed.effect == "asserted"
        denied = await engine.call(caller("alice"), "adjudicate", {"artifact_id": "c2", "decision": "deny"})
        assert isinstance(denied, Receipt) and denied.effect == "retired"
        assert {str(r[0]): r[1] for r in await rows(conn, "SELECT id, lifecycle FROM memory_version ORDER BY id")} == {
            "c1": "asserted",
            "c2": "retired",
        }
        # the adjudication record exists (E-016 criterion 4): an accept and a reject decision
        assert sorted(
            str(r[0])
            for r in await rows(conn, "SELECT decision FROM memory_conflict WHERE reason_code LIKE 'adjudicated%%'")
        ) == [
            "accept",
            "reject",
        ]
        again = await forbidden(engine, caller("alice"), "adjudicate", {"artifact_id": "c1", "decision": "deny"})
        assert (again.code, again.detail) == ("precondition_failed", "artifact_not_contested")


async def test_adjudicate_cannot_confirm_a_kind_that_cannot_be_asserted_and_is_tier_c_only(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "abs", kind="abstraction", held=True)
        await make_version(conn, "ep", kind="episode", held=True)
        for version_id in ("abs", "ep"):
            error = await forbidden(
                engine, caller("alice"), "adjudicate", {"artifact_id": version_id, "decision": "confirm"}
            )
            assert (error.code, error.detail) == ("precondition_failed", "kind_cannot_be_asserted")  # V2-D48 C-P1
            assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = %s", (version_id,)) == [
                ("contested",)
            ]
        denied = await engine.call(caller("alice"), "adjudicate", {"artifact_id": "abs", "decision": "deny"})
        assert isinstance(denied, Receipt) and denied.effect == "retired"
        assert (
            await forbidden(engine, caller("alice", "B"), "adjudicate", {"artifact_id": "ep", "decision": "deny"})
        ).code == "forbidden"
        assert (
            await forbidden(engine, caller("alice"), "adjudicate", {"artifact_id": "ep", "decision": "maybe"})
        ).code == "validation_error"


async def test_a_grantee_cannot_adjudicate_what_it_can_only_read(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "shared-held", "alice", held=True, visibility="shared")
        await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        assert "shared-held" in await DbTargets(engine.resolver.runtime).resolve(caller("bob"), ["shared-held"])  # type: ignore[attr-defined]
        error = await forbidden(
            engine, caller("bob"), "adjudicate", {"artifact_id": "shared-held", "decision": "confirm"}
        )
        assert error.code == "forbidden"
        assert await rows(conn, "SELECT lifecycle FROM memory_version WHERE id = 'shared-held'") == [("contested",)]


async def test_retract_own_closes_recorded_time_then_archives_and_is_tier_scoped(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "a1", created=("alice", "C"))
        await make_version(conn, "a2", created=("alice", "B"))
        await conn.execute("SELECT set_config('app.clock', '2026-04-01T00:00:00+00:00', false)")  # time passes
        receipt = await engine.call(caller("alice"), "retract_own", {"artifact_id": "a1", "reason": "user_withdrawal"})
        assert isinstance(receipt, Receipt) and receipt.effect == "retired"
        assert await rows(
            conn,
            "SELECT lifecycle, status, archival_branch, upper_inf(recorded_range) FROM memory_version WHERE id = 'a1'",
        ) == [
            ("retired", "archived", "S", False)  # V2-D59: the withdrawal IS branch (S)
        ]
        again = await forbidden(
            engine, caller("alice"), "retract_own", {"artifact_id": "a1", "reason": "user_withdrawal"}
        )
        assert (again.code, again.detail) == ("precondition_failed", "already_retired")
        # a Tier B call may retract only what Tier B produced, and Tier C only its own assertions
        assert (
            await forbidden(engine, caller("alice", "C"), "retract_own", {"artifact_id": "a2", "reason": "x"})
        ).code == "forbidden"
        assert isinstance(
            await engine.call(caller("alice", "B"), "retract_own", {"artifact_id": "a2", "reason": "method_wrong"}),
            Receipt,
        )
        assert (
            await forbidden(engine, caller("bob"), "retract_own", {"artifact_id": "a1", "reason": "x"})
        ).code == "forbidden"
        bad = await forbidden(
            engine, caller("alice"), "retract_own", {"artifact_id": "a1", "reason": "Because I said so!"}
        )
        assert (bad.code, bad.detail) == ("validation_error", "reason_must_be_a_code")
        (details,) = (
            await rows(
                conn,
                "SELECT details::text FROM audit_event WHERE event_type = 'lifecycle_retired' ORDER BY id LIMIT 1",
            )
        )[0]
        assert "user_withdrawal" in str(details) or "method_wrong" in str(details)


async def test_a_v1_artifact_belongs_to_the_user_acting_as_tier_c(database) -> None:  # noqa: F811
    """A row written before the API existed has no recorded creator; it is the user's own, as Tier C."""
    async with world(database) as (conn, engine, _):
        await make_version(conn, "legacy")  # created_by / created_tier NULL
        await conn.execute("SELECT set_config('app.clock', '2026-04-01T00:00:00+00:00', false)")
        assert (
            await forbidden(engine, caller("alice", "B"), "retract_own", {"artifact_id": "legacy", "reason": "x"})
        ).code == "forbidden"
        assert isinstance(
            await engine.call(caller("alice"), "retract_own", {"artifact_id": "legacy", "reason": "legacy_retired"}),
            Receipt,
        )


# ----------------------------------------------------------------------------------------------- SCOPE ACCESS


async def test_grant_and_revoke_access_control_what_a_grantee_can_read(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "open", visibility="shared")
        await make_version(conn, "closed", visibility="private")
        targets = DbTargets(engine.resolver.runtime)  # type: ignore[attr-defined]
        assert await targets.resolve(caller("bob"), ["open", "closed"]) == {}
        grant = await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g1"}
        )
        assert isinstance(grant, str) and grant.startswith("grant_")
        assert (
            await engine.call(
                caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g1"}
            )
            == grant
        )
        same = await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g2"}
        )
        assert same == grant  # an active grant is not duplicated
        assert set(await targets.resolve(caller("bob"), ["open", "closed"])) == {
            "open"
        }  # never a private row (V2-D36)
        receipt = await engine.call(caller("alice"), "revoke_access", {"grant_id": grant})
        assert isinstance(receipt, Receipt) and receipt.effect == "revoked"
        assert await targets.resolve(caller("bob"), ["open", "closed"]) == {}
        again = await forbidden(engine, caller("alice"), "revoke_access", {"grant_id": grant})
        assert (again.code, again.detail) == ("precondition_failed", "already_revoked")
        assert (
            await forbidden(engine, caller("bob"), "revoke_access", {"grant_id": grant})
        ).code == "forbidden"  # not the owner


async def test_granting_to_an_unknown_or_malformed_principal_is_forbidden_like_an_unauthorised_scope(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        results = [
            await forbidden(
                engine, caller("alice"), "grant_access", {"scope": scope, "principal": who, "idempotency_key": f"k{n}"}
            )
            for n, (scope, who) in enumerate(
                [("/u/alice/", "nobody-at-all"), ("/u/alice/", "a/b"), ("/u/bob/", "bob")]
            )
        ]
        assert {(e.code, e.detail) for e in results} == {("forbidden", None)}
        assert await rows(conn, "SELECT count(*) FROM scope_grant") == [(0,)]
        blank = await forbidden(
            engine, caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": " ", "idempotency_key": "b"}
        )
        assert blank.code == "validation_error"  # blank is malformed input, not an unknown user
        selfgrant = await forbidden(
            engine,
            caller("alice"),
            "grant_access",
            {"scope": "/u/alice/", "principal": "alice", "idempotency_key": "s"},
        )
        assert selfgrant.code == "validation_error"
