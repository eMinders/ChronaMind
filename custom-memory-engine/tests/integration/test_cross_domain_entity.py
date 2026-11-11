"""Pin three cross-scope opacity properties from ARCHITECTURE_V2.md §5.2 under the restricted role.

Entity ids and claims remain scope-local; an explicit entity link requires both endpoint scopes in the read set; and
provenance for an unreadable source renders only the caller-specific opaque handle. The profile-neutral scenario does
not require E-021's optional coding profile.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import tuple_row

from custom_memory_engine.api import entry as entry_module
from custom_memory_engine.api import read_handlers
from custom_memory_engine.api import runtime as runtime_module
from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.handlers import Handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt, Runtime
from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    DerivationInput,
    commit_initial_version,
)
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope import access as access_module
from custom_memory_engine.scope.access import ReadSet, Selector, resolve_read_set
from custom_memory_engine.scope.provenance import (
    OpaqueReference,
    ProvenanceKey,
    ProvenanceSource,
    render_bundle,
    render_reference,
)
from tests.integration.test_api_wiring_structure import caller, database, world  # noqa: F401

T0 = datetime(2026, 3, 10, tzinfo=UTC)
PROVENANCE_KEY = ProvenanceKey(b"e024-test-key" * 4)


def scoped_caller(user: str, tier: Tier, *paths: str) -> CallerContext:
    read_set = resolve_read_set(user, [Selector.exact(path) for path in paths], [])
    return CallerContext.from_credential(Credential(user, tier), write_scope=f"/u/{user}/", read_set=read_set)


async def _seed_claim_turns(runtime: Runtime) -> None:
    sources = (
        ("t-e024-work", 2, "Ada is my coworker."),
        ("t-e024-home", 3, "Ada is my running partner."),
    )
    async with runtime.session("alice") as scoped_connection:
        for turn_id, sequence_no, content in sources:
            await scoped_connection.execute(
                "INSERT INTO turn "
                "(id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
                (turn_id, sequence_no, content, T0),
            )


async def _assert_claim(
    engine: Engine,
    user: str,
    scope_path: str,
    turn_id: str,
    content: str,
    key: str,
    entity_id: str | None = None,
) -> Receipt:
    arguments: dict[str, object] = {
        "scope": scope_path,
        "kind": "claim",
        "content": content,
        "evidence": [
            {
                "type": "turn",
                "ref": turn_id,
                "span": content,
                "content_hash": canonical_hash(content),
            }
        ],
        "event_range": None,
        "idempotency_key": key,
    }
    if entity_id is not None:
        arguments["subject_entity"] = entity_id
    result = await engine.call(scoped_caller(user, "C", scope_path), "assert", arguments)
    assert isinstance(result, Receipt) and result.effect == "asserted"
    return result


async def _query_ids(engine: Engine, user: str, scope_path: str) -> set[str]:
    """What the public `query` operation returns for one scope, as a set of artifact ids."""
    result = await engine.call(scoped_caller(user, "C", scope_path), "query", {"request": {"scope": scope_path}})
    assert isinstance(result, QueryResult)
    return {
        artifact.artifact_id
        for channel in (result.facts, result.hypotheses, result.procedures)
        for artifact in channel
    }


async def _scope_memory_ids(runtime: Runtime, user: str, read_set: ReadSet) -> set[str]:
    predicate, parameters = access_module.read_predicate(read_set, "scope_path", "visibility")
    async with runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id FROM {t} WHERE {predicate}").format(
                t=runtime.table("memory_version"), predicate=predicate
            ),
            parameters,
        )
        return {str(row[0]) for row in await cursor.fetchall()}


async def _expect_forbidden(engine: Engine, ctx: CallerContext, args: dict[str, object]) -> None:
    try:
        await engine.call(ctx, "link_entity", args)
    except EntryError as error:
        assert error.code == "forbidden"
        return
    raise AssertionError("expected_forbidden")


@pytest.mark.integration
@pytest.mark.parametrize(
    "scope_order", [("work", "personal"), ("personal", "work")], ids=["work-first", "personal-first"]
)
async def test_two_scopes_keep_claims_and_entities_private_while_explicit_link_is_idempotent(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    scope_order: tuple[str, str],
) -> None:  # noqa: F811
    identifiers = iter(range(1, 1000))

    def deterministic_uuid4() -> UUID:
        return UUID(int=next(identifiers))

    monkeypatch.setattr(runtime_module, "uuid4", deterministic_uuid4)
    async with world(database) as (connection, engine, schema):
        runtime = cast(DbTargets, engine.resolver).runtime
        register_ingest_handlers(engine, runtime)
        register_read_handlers(engine, runtime)  # `query`, through the entry point (E-033)
        await _seed_claim_turns(runtime)

        scope_specs = {
            "work": ("chronamind-work", "project"),
            "personal": ("chronamind-personal", "task"),
        }
        scope_paths: dict[str, str] = {}
        for name in scope_order:
            segment, kind = scope_specs[name]
            created_scope = await engine.call(
                caller("alice"),
                "create_scope",
                {
                    "parent": "/u/alice/",
                    "segment": segment,
                    "kind": kind,
                    "idempotency_key": f"e024-{name}-scope",
                },
            )
            assert isinstance(created_scope, str)
            scope_paths[name] = created_scope
        work_scope, personal_scope = scope_paths["work"], scope_paths["personal"]
        assert work_scope == "/u/alice/chronamind-work/"
        assert personal_scope == "/u/alice/chronamind-personal/"
        assert (
            await engine.call(
                caller("alice"),
                "create_scope",
                {
                    "parent": "/u/alice/",
                    "segment": "chronamind-work",
                    "kind": "project",
                    "idempotency_key": "e024-work-scope",
                },
            )
            == work_scope
        )

        entities: dict[str, str] = {}
        for name in scope_order:
            path = scope_paths[name]
            entity_id = await engine.call(
                scoped_caller("alice", "C", path),
                "create_entity",
                {
                    "scope": path,
                    "entity_type": "person",
                    "label": "Ada",
                    "idempotency_key": f"e024-{name}-entity",
                },
            )
            assert isinstance(entity_id, str)
            entities[name] = entity_id
        work_entity, personal_entity = entities["work"], entities["personal"]
        assert isinstance(work_entity, str) and work_entity.startswith("ent_")
        assert isinstance(personal_entity, str) and personal_entity.startswith("ent_")
        assert work_entity != personal_entity

        claim_args = {
            "work": (work_scope, "t-e024-work", "Ada is my coworker.", work_entity),
            "personal": (personal_scope, "t-e024-home", "Ada is my running partner.", personal_entity),
        }
        claims: dict[str, Receipt] = {}
        for name in scope_order:
            path, turn_id, content, entity_id = claim_args[name]
            claims[name] = await _assert_claim(
                engine, "alice", path, turn_id, content, f"e024-{name}-claim", entity_id
            )
        work_claim, personal_claim = claims["work"], claims["personal"]
        stored_work = await connection.execute(
            "SELECT id, scope_path FROM memory_version WHERE id = %s", (work_claim.id,)
        )
        stored_personal = await connection.execute(
            "SELECT id, scope_path FROM memory_version WHERE id = %s", (personal_claim.id,)
        )
        assert await stored_work.fetchone() == (work_claim.id, work_scope)
        assert await stored_personal.fetchone() == (personal_claim.id, personal_scope)
        work_subject = await connection.execute(
            "SELECT subject_key FROM memory_version WHERE id = %s", (work_claim.id,)
        )
        personal_subject = await connection.execute(
            "SELECT subject_key FROM memory_version WHERE id = %s", (personal_claim.id,)
        )
        assert await work_subject.fetchone() == (work_entity,)
        assert await personal_subject.fetchone() == (personal_entity,)

        both_scopes = scoped_caller("alice", "C", work_scope, personal_scope)
        link_args = {"entity_a": work_entity, "entity_b": personal_entity, "relation": "same_person"}
        link = await engine.call(both_scopes, "link_entity", link_args)
        replay = await engine.call(both_scopes, "link_entity", link_args)
        assert isinstance(link, Receipt) and link.effect == "linked"
        assert isinstance(replay, Receipt) and replay.id == link.id

        with pytest.raises(EntryError) as wrong_scope:
            await engine.call(
                both_scopes,
                "assert",
                {
                    "scope": work_scope,
                    "kind": "claim",
                    "content": "Ada is my coworker.",
                    "evidence": [
                        {
                            "type": "turn",
                            "ref": "t-e024-work",
                            "span": "Ada is my coworker.",
                            "content_hash": canonical_hash("Ada is my coworker."),
                        }
                    ],
                    "event_range": None,
                    "idempotency_key": "e024-cross-scope-subject",
                    "subject_entity": personal_entity,
                },
            )
        assert (wrong_scope.value.code, wrong_scope.value.detail) == ("forbidden", None)

        work_read_set = scoped_caller("alice", "C", work_scope).read_set
        personal_read_set = scoped_caller("alice", "C", personal_scope).read_set
        # The claim in scope A is not readable from scope B's read set, asserted through the public `query`
        # operation (rows 2 and 13) and, below it, against what the read predicate itself returns.
        assert await _query_ids(engine, "alice", work_scope) == {work_claim.id}
        assert await _query_ids(engine, "alice", personal_scope) == {personal_claim.id}
        assert await _scope_memory_ids(runtime, "alice", work_read_set) == {work_claim.id}
        assert await _scope_memory_ids(runtime, "alice", personal_read_set) == {personal_claim.id}
        assert "provenance" in set(engine.unwired_operations())  # E-039's remaining wiring; row 5 renders directly
        # A link is a row; a scope is its path, so nothing records the two scopes (V2-D65).
        links_cursor = await connection.execute(
            "SELECT entity_a, entity_b, relation FROM entity_link WHERE user_id = 'alice'"
        )
        # One row in the canonical orientation (``entity_a < entity_b``), whichever order the two scopes were built in.
        assert await links_cursor.fetchall() == [(*sorted((work_entity, personal_entity)), "same_person")]

        # A true descendant-scope source remains opaque to a caller selecting only the summary's scope.
        summary = CandidateVersionInput(
            version_id="v_e024_summary",
            memory_key="e024-summary",
            user_id="alice",
            scope_path=personal_scope,
            subject_key="self",
            predicate_key="statement",
            single_valued=False,
            content="Ada is known in two contexts.",
            valid_from=T0,
            derivations=(DerivationInput(source_version_id=work_claim.id, derivation_type="summary"),),
        )
        async with runtime.session("alice") as scoped_connection:
            await commit_initial_version(scoped_connection, summary, schema_name=schema, replay_time=T0)
            async with scoped_connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "SELECT source.id, source.scope_path, source.visibility, "
                        "'artifact', derivation.derivation_type "
                        "FROM {d} AS derivation JOIN {v} AS source "
                        "ON source.id = derivation.source_version_id "
                        "WHERE derivation.derived_version_id = %s"
                    ).format(d=runtime.table("memory_derivation"), v=runtime.table("memory_version")),
                    (summary.version_id,),
                )
                source_rows = await cursor.fetchall()
        sources = [
            ProvenanceSource(
                source_id=str(row[0]),
                scope_path=str(row[1]),
                visibility=str(row[2]),
                source_type=str(row[3]),
                derivation_type=str(row[4]),
            )
            for row in source_rows
        ]
        bundle = render_bundle(
            sources,
            caller_user_id="alice",
            read_set=personal_read_set,
            key=PROVENANCE_KEY,
        )
        assert len(bundle) == 1 and isinstance(bundle[0], OpaqueReference)
        assert bundle[0].derivation_type == "summary"
        assert work_claim.id not in repr(bundle[0]) and work_scope not in repr(bundle[0])
        assert not hasattr(bundle[0], "source_type") and not hasattr(bundle[0], "scope_path")


@pytest.mark.integration
async def test_unreadable_and_missing_entity_links_are_indistinguishable_and_guard_control_is_sensitive(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    async with world(database) as (connection, engine, _schema):
        first_scope = await engine.call(
            caller("alice"),
            "create_scope",
            {
                "parent": "/u/alice/",
                "segment": "scope-a",
                "kind": "project",
                "idempotency_key": "scope-a",
            },
        )
        second_scope = await engine.call(
            caller("alice"),
            "create_scope",
            {
                "parent": "/u/alice/",
                "segment": "scope-b",
                "kind": "project",
                "idempotency_key": "scope-b",
            },
        )
        assert isinstance(first_scope, str)
        assert isinstance(second_scope, str)
        first = await engine.call(
            scoped_caller("alice", "C", first_scope),
            "create_entity",
            {
                "scope": first_scope,
                "entity_type": "person",
                "label": "Ada",
                "idempotency_key": "entity-a",
            },
        )
        second = await engine.call(
            scoped_caller("alice", "C", second_scope),
            "create_entity",
            {
                "scope": second_scope,
                "entity_type": "person",
                "label": "Ada",
                "idempotency_key": "entity-b",
            },
        )
        assert isinstance(first, str)
        assert isinstance(second, str)
        one_scope = scoped_caller("alice", "C", first_scope)
        errors: list[EntryError] = []
        for other in (second, "not-an-entity"):
            with pytest.raises(EntryError) as raised:
                await engine.call(
                    one_scope,
                    "link_entity",
                    {"entity_a": first, "entity_b": other, "relation": "same_person"},
                )
            errors.append(raised.value)
        assert (
            (errors[0].code, errors[0].detail, str(errors[0]))
            == (
                errors[1].code,
                errors[1].detail,
                str(errors[1]),
            )
            == ("forbidden", None, "forbidden")
        )
        links_cursor = await connection.execute("SELECT count(*) FROM entity_link")
        assert await links_cursor.fetchone() == (0,)

        async def bypass_targets(*_args: object, **_kwargs: object) -> None:
            return None

        with monkeypatch.context() as patch:
            patch.setitem(entry_module.CONDITION_CHECKS, "targets_readable", bypass_targets)
            with pytest.raises(AssertionError, match="expected_forbidden"):
                await _expect_forbidden(
                    engine,
                    one_scope,
                    {"entity_a": first, "entity_b": second, "relation": "same_person"},
                )


@pytest.mark.integration
async def test_scope_read_filter_control_breaks_the_isolation_assertion(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    async with world(database) as (connection, engine, _schema):
        runtime = cast(DbTargets, engine.resolver).runtime
        register_ingest_handlers(engine, runtime)
        register_read_handlers(engine, runtime)  # `query`, through the entry point (E-033)
        await _seed_claim_turns(runtime)
        scope_a = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "isolation-a", "kind": "project", "idempotency_key": "iso-a"},
        )
        scope_b = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "isolation-b", "kind": "project", "idempotency_key": "iso-b"},
        )
        assert isinstance(scope_a, str)
        assert isinstance(scope_b, str)
        claim_a = await _assert_claim(engine, "alice", scope_a, "t-e024-work", "Ada is my coworker.", "iso-claim-a")
        claim_b = await _assert_claim(
            engine, "alice", scope_b, "t-e024-home", "Ada is my running partner.", "iso-claim-b"
        )
        read_set_a = scoped_caller("alice", "C", scope_a).read_set
        assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}
        assert await _query_ids(engine, "alice", scope_b) == {claim_b.id}
        assert await _scope_memory_ids(runtime, "alice", read_set_a) == {claim_a.id}

        with monkeypatch.context() as patch:
            patch.setattr(access_module, "read_predicate", lambda *_args: (sql.SQL("TRUE"), []))
            with pytest.raises(AssertionError):
                observed = await _scope_memory_ids(runtime, "alice", read_set_a)
                assert observed == {claim_a.id}
            assert claim_b.id in await _scope_memory_ids(runtime, "alice", read_set_a)


@pytest.mark.integration
async def test_the_query_operation_control_breaks_the_scope_isolation_assertion(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    """Row 2's control at the entry point: make ``query``'s SQL predicate unfiltered and the isolation assertion fails.

    The assertion under control is row 2's, through the public ``query`` operation. The guards are
    ``scope.access.read_predicate_named`` (the SQL half, named because it joins the temporal predicate) and
    ``row_visible`` (the reference half), so those are what the control replaces; nothing in the product path can be
    switched, and the monkeypatch lives only inside this test.
    """
    async with world(database) as (_connection, engine, _schema):
        runtime = cast(DbTargets, engine.resolver).runtime
        register_ingest_handlers(engine, runtime)
        register_read_handlers(engine, runtime)  # `query`, through the entry point (E-033)
        await _seed_claim_turns(runtime)
        scope_a = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "iso-query-a", "kind": "project", "idempotency_key": "q-iso-a"},
        )
        scope_b = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "iso-query-b", "kind": "project", "idempotency_key": "q-iso-b"},
        )
        assert isinstance(scope_a, str) and isinstance(scope_b, str)
        claim_a = await _assert_claim(engine, "alice", scope_a, "t-e024-work", "Ada is my coworker.", "q-claim-a")
        claim_b = await _assert_claim(
            engine, "alice", scope_b, "t-e024-home", "Ada is my running partner.", "q-claim-b"
        )
        assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}

        # `query` enforces the read set twice: the SQL predicate, then `row_visible` on every row (V2-D45 defence in
        # depth). Either alone keeps scope B out, so each is broken alone (B stays hidden) and then both (B leaks).
        with monkeypatch.context() as patch:
            patch.setattr(read_handlers, "read_predicate_named", lambda *_args: (sql.SQL("TRUE"), {}))
            assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}
        with monkeypatch.context() as patch:
            patch.setattr(read_handlers, "row_visible", lambda *_args: True)
            assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}
        with monkeypatch.context() as patch:
            patch.setattr(read_handlers, "read_predicate_named", lambda *_args: (sql.SQL("TRUE"), {}))
            patch.setattr(read_handlers, "row_visible", lambda *_args: True)
            with pytest.raises(AssertionError):  # the row-2 assertion now fails: the control is sensitive
                assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}
            assert claim_b.id in await _query_ids(engine, "alice", scope_a)

        assert await _query_ids(engine, "alice", scope_a) == {claim_a.id}  # restored


@pytest.mark.integration
async def test_a_refused_link_does_the_same_work_for_an_unreadable_and_an_absent_entity(
    database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    """Row 3/4, timing shape: the refusal must not merely look the same, it must *do* the same work.

    Wall-clock timing is not an assertion a test can hold; the work behind it is. Both refusals run the same number of
    SQL statements, in the same order, and neither reaches the handler, so no branch can be inferred from how much the
    call did. A handler-side check instead of ``targets_readable`` would break this (the handler runs one statement
    more, inside a transaction of its own).
    """
    async with world(database) as (_connection, engine, _schema):
        scope = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "timing", "kind": "project", "idempotency_key": "timing-scope"},
        )
        own = await engine.call(
            caller("alice"),
            "create_entity",
            {"scope": scope, "entity_type": "person", "label": "Ada", "idempotency_key": "timing-own"},
        )
        other_scope = await engine.call(
            caller("alice"),
            "create_scope",
            {"parent": "/u/alice/", "segment": "elsewhere", "kind": "project", "idempotency_key": "timing-other"},
        )
        unreadable = await engine.call(
            caller("alice"),
            "create_entity",
            {"scope": other_scope, "entity_type": "person", "label": "Ada", "idempotency_key": "timing-other-e"},
        )
        assert isinstance(scope, str) and isinstance(other_scope, str)
        assert isinstance(own, str) and isinstance(unreadable, str)
        # the caller's read set holds the first scope only, so the second entity exists but is not readable
        one_scope = scoped_caller("alice", "C", scope)

        statements: list[str] = []
        original_execute = psycopg.AsyncCursor.execute

        async def recording_execute(self: psycopg.AsyncCursor[Any], query: object, *args: object, **kwargs: object):
            statements.append(str(query))
            return await original_execute(self, query, *args, **kwargs)  # type: ignore[arg-type]

        reached_handler: list[str] = []
        original_link = Handlers.link_entity

        async def recording_link(self: Handlers, call: object) -> Receipt:
            reached_handler.append("link_entity")
            return await original_link(self, call)  # type: ignore[arg-type]

        with monkeypatch.context() as patch:
            patch.setattr(psycopg.AsyncCursor, "execute", recording_execute)
            patch.setattr(Handlers, "link_entity", recording_link)
            shapes: list[list[str]] = []
            for target in (unreadable, "ent_never_existed"):
                statements.clear()
                with pytest.raises(EntryError) as refused:
                    await engine.call(
                        one_scope, "link_entity", {"entity_a": own, "entity_b": target, "relation": "same_person"}
                    )
                assert (refused.value.code, refused.value.detail) == ("forbidden", None)
                shapes.append([re.sub(r"\s+", " ", statement) for statement in statements])
        # same statements, same order, same count, and the handler never ran for either
        assert shapes[0] == shapes[1] and len(shapes[0]) > 0
        assert reached_handler == []


def test_provenance_opacity_control_detects_a_bypassed_read_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    source = ProvenanceSource(
        source_id="private-source",
        scope_path="/u/alice/private/",
        visibility="private",
        source_type="artifact",
        derivation_type="summary",
    )
    selected = resolve_read_set("alice", [Selector.exact("/u/alice/summary/")], [])
    assert isinstance(
        render_reference(source, caller_user_id="alice", read_set=selected, key=PROVENANCE_KEY), OpaqueReference
    )

    with monkeypatch.context() as patch:
        patch.setattr("custom_memory_engine.scope.provenance.row_visible", lambda *_args: True)
        visible = render_reference(source, caller_user_id="alice", read_set=selected, key=PROVENANCE_KEY)
        with pytest.raises(AssertionError):
            assert isinstance(visible, OpaqueReference)
