"""E-033 minimal: ``get`` and ``query`` through ``Engine.call`` against the real migrated schema (V2-D66, #156).

Every case runs as a ``NOSUPERUSER NOBYPASSRLS`` role under a credential-built caller, so row-level security — not a
handler guard — is what keeps one tenant out of another's rows. Writes go through the same public entry point, so a
passing read here proves the round trip rather than a seeded fixture.
"""

from datetime import UTC, datetime

import pytest

from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.profile import parse_profile
from tests.integration.test_api_wiring_structure import caller, database, forbidden, rows, world  # noqa: F401

pytestmark = pytest.mark.integration
#: A four-kind profile, assembled here because `coding.json` lands with E-021's own PR. The retention floors are the
#: ones the coding profile declares (episode/procedure may be session; claim/abstraction must be durable).
CODING_PROFILE = parse_profile(
    {
        "schema_version": 1,
        "name": "test-coding",
        "kinds": ["episode", "claim", "procedure", "abstraction"],
        "extractors": [
            {"name": "tool_call_log", "version": "1", "source_types": ["tool"]},
            {"name": "user_instructions", "version": "1", "source_types": ["turn"]},
        ],
        "operators": ["consolidation"],
        "retention": {
            "defaults": {
                "episode": "session",
                "claim": "durable",
                "procedure": "session",
                "abstraction": "durable",
            },
            "promote_to_durable": True,
            "status": "placeholder:V2-D14",
        },
        "scope_root": "/u/{user}/p/{repo}/",
        "render": {"channel_budget_share": {"facts": 0.5, "hypotheses": 0.2, "procedures": 0.2, "conflicts": 0.1}},
        "facets": ["constraint", "context", "decision_pattern"],
        "association": {"degree_cap": 32, "extensions": []},
        "abstraction_expiry": {"days_by_facet": {}, "status": "placeholder:V2-D14"},
    }
)
T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def episode(scope: str, key: str, content: str, *, source_type: str = "external") -> dict[str, object]:
    return {
        "scope": scope,
        "source": {
            "type": source_type,
            "ref": f"{source_type}:{key}",
            "span": content,
            "content_hash": canonical_hash(content),
        },
        "content": content,
        "event_time": T0.isoformat(),
        "idempotency_key": key,
    }


async def wired(database):  # noqa: F811
    """The world fixture plus both writer and reader handlers, as a deployment wires them."""
    return world(database)


async def test_get_returns_what_ingest_stored(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        receipt = await engine.call(caller("alice"), "ingest_episode", episode("/u/alice/", "g1", "a CI run failed"))
        assert isinstance(receipt, Receipt)
        artifact = await engine.call(caller("alice"), "get", {"artifact_id": receipt.id})
        assert isinstance(artifact, Artifact)
        assert (artifact.artifact_id, artifact.kind, artifact.lifecycle) == (receipt.id, "episode", "observed")
        assert artifact.content == "a CI run failed"
        assert artifact.channel == "facts"
        assert artifact.scope_path == "/u/alice/"


async def test_get_refuses_another_users_artifact_exactly_like_a_missing_one(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        receipt = await engine.call(caller("alice"), "ingest_episode", episode("/u/alice/", "g2", "alice only"))
        assert isinstance(receipt, Receipt)
        foreign = await forbidden(engine, caller("bob"), "get", {"artifact_id": receipt.id})
        missing = await forbidden(engine, caller("bob"), "get", {"artifact_id": "no-such-artifact"})
        assert (foreign.code, foreign.detail) == (missing.code, missing.detail) == ("forbidden", None)


async def test_query_groups_stored_episodes_into_the_facts_channel(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        for index in range(3):
            receipt = await engine.call(
                caller("alice"), "ingest_episode", episode("/u/alice/", f"q{index}", f"observed event {index}")
            )
            assert isinstance(receipt, Receipt)
        result = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(result, QueryResult)
        assert len(result.facts) == 3
        assert {item.content for item in result.facts} == {f"observed event {i}" for i in range(3)}
        assert result.hypotheses == () and result.procedures == () and result.conflicts == ()
        assert all(item.channel == "facts" for item in result.facts)


async def test_query_is_scoped_to_the_caller(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await engine.call(caller("alice"), "ingest_episode", episode("/u/alice/", "s1", "alice fact"))
        await engine.call(caller("bob"), "ingest_episode", episode("/u/bob/", "s2", "bob fact"))
        alice = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(alice, QueryResult)
        assert [item.content for item in alice.facts] == ["alice fact"]
        bob = await engine.call(caller("bob"), "query", {"request": {"scope": "/u/bob/"}})
        assert isinstance(bob, QueryResult)
        assert [item.content for item in bob.facts] == ["bob fact"]


async def test_query_honours_the_repository_scope_stored_by_ingest(database) -> None:  # noqa: F811
    """The scope the writer persisted is the scope the reader filters on (E-021 AC4, issue #155)."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await engine.call(caller("alice"), "ingest_episode", episode("/u/alice/p/chrona/", "r1", "repo fact"))
        await engine.call(caller("alice"), "ingest_episode", episode("/u/alice/", "r2", "root fact"))
        scoped = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/p/chrona/"}})
        assert isinstance(scoped, QueryResult)
        assert [item.content for item in scoped.facts] == ["repo fact"]
        assert scoped.facts[0].scope_path == "/u/alice/p/chrona/"
        root = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(root, QueryResult)
        # V2 §5 rule 4 / T-06: a private row matches only a selector equal to its scope, so the repo's private
        # episode does not leak upward into a root read.
        assert [item.content for item in root.facts] == ["root fact"]


async def test_get_reports_a_procedure_in_the_procedures_channel(database) -> None:  # noqa: F811
    """A stored procedure reads back in ``procedures`` (CHANNELS.md), proving kind survives the round trip."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', 3, 'user', %s, %s)",
            ("turn:ci-recipe", "rerun the focused pytest command", T0),
        )
        receipt = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "procedure",
                "content": "Add the import and rerun the focused pytest command.",
                "evidence": [
                    {
                        "type": "turn",
                        "ref": "turn:ci-recipe",
                        "span": "rerun the focused pytest command",
                        "content_hash": canonical_hash("rerun the focused pytest command"),
                    }
                ],
                "event_range": None,
                "idempotency_key": "proc-1",
            },
        )
        assert isinstance(receipt, Receipt) and receipt.effect == "asserted"
        artifact = await engine.call(caller("alice"), "get", {"artifact_id": receipt.id})
        assert isinstance(artifact, Artifact)
        assert (artifact.kind, artifact.channel) == ("procedure", "procedures")
        result = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(result, QueryResult)
        assert [item.artifact_id for item in result.procedures] == [receipt.id]


async def test_query_validates_its_request_statically(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        register_read_handlers(engine, engine.resolver.runtime)
        cases = {
            "missing": ({}, ("validation_error", "request_required")),
            "bad_limit": ({"request": {"limit": 0}}, ("validation_error", "limit_out_of_range")),
            "bad_scope": ({"request": {"scope": "u/alice"}}, ("validation_error", "scope_invalid")),
        }
        for name, (arguments, expected) in cases.items():
            error = await forbidden(engine, caller("alice"), "query", arguments)
            assert (error.code, error.detail) == expected, name


async def test_query_and_get_are_no_longer_unwired(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        register_read_handlers(engine, engine.resolver.runtime)
        unwired = engine.unwired_operations()
        assert "query" not in unwired and "get" not in unwired
