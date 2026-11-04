"""The minimal ``infer`` through :meth:`Engine.call` on the real migrated schema (C-021 minimal; V2-D66, issue #158).

Every call runs as a ``NOSUPERUSER NOBYPASSRLS`` role. The abstraction is created by the public entry point only, and
read back by ``get``/``query`` (E-033 minimal), so the channel is the one a reader sees.
"""

from collections.abc import Mapping

import pytest

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine
from custom_memory_engine.api.infer_handlers import register_infer_handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt, Rejected
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import HASH, caller, database, forbidden, rows, world  # noqa: F401
from tests.integration.test_read_path_db import CODING_PROFILE, episode

pytestmark = pytest.mark.integration

# V2-D30 is open; these are its suggested values, stated by the wiring as the handler requires.
TAU_CONFIDENCE, TAU_COVERAGE = 0.90, 0.60
LEDGER = {"support": 3, "counter": 0, "undetermined": 1, "opportunity": 4}
STATEMENT = "A missing import may explain repeated name errors in focused CI tests."


def agent(user: str, tier: Tier = "B") -> CallerContext:
    """A coding-agent session: it selects its repository scope as well as the root.

    V2 §5 rule 4 (T-06): a ``private`` row is read only through a selector equal to its scope, so a session that
    selected only ``/u/<user>/`` does not hold the repository's private episodes - and may not derive from them.
    """
    read_set = resolve_read_set(user, [Selector.of(f"/u/{user}/"), Selector.of(f"/u/{user}/p/chrona/")], [])
    return CallerContext.from_credential(Credential(user, tier, False), write_scope=f"/u/{user}/", read_set=read_set)


def wire(engine: Engine) -> None:
    assert isinstance(engine.resolver, DbTargets)
    runtime = engine.resolver.runtime
    register_ingest_handlers(engine, runtime, CODING_PROFILE)
    register_read_handlers(engine, runtime)
    register_infer_handlers(
        engine, runtime, tau_confidence=TAU_CONFIDENCE, tau_coverage=TAU_COVERAGE, profile=CODING_PROFILE
    )


async def method(engine: Engine) -> dict[str, str]:
    args = {"name": "failure-clustering", "version": "1", "params_schema_hash": HASH, "idempotency_key": "m"}
    identifier = await engine.call(caller("alice", admin=True), "register_method", args)
    assert isinstance(identifier, str)
    return {"id": identifier, "version": "1", "params_hash": HASH}


async def observed(engine: Engine, user: str, key: str) -> str:
    content = "run one: NameError: json"
    receipt = await engine.call(caller(user), "ingest_episode", episode(f"/u/{user}/p/chrona/", key, content))
    assert isinstance(receipt, Receipt)
    return receipt.id


def infer_args(ref: Mapping[str, str], sources: list[str], **override: object) -> dict[str, object]:
    return {
        "scope": "/u/alice/p/chrona/",
        "method": dict(ref),
        "sources": sources,
        "statement": STATEMENT,
        "predicate": None,
        "ledger": LEDGER,
        "idempotency_key": "i1",
        **override,
    }


async def test_infer_creates_an_inferred_abstraction_read_back_as_a_hypothesis(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        ref = await method(engine)
        first, second = await observed(engine, "alice", "e1"), await observed(engine, "alice", "e2")
        receipt = await engine.call(agent("alice"), "infer", infer_args(ref, [first, second]))
        assert isinstance(receipt, Receipt) and receipt.effect == "inferred"
        assert await rows(
            conn,
            "SELECT kind, lifecycle, layer, method_id, scope_path, created_tier FROM memory_version WHERE id = %s",
            (receipt.id,),
        ) == [("abstraction", "inferred", "persona", ref["id"], "/u/alice/p/chrona/", "B")]
        assert sorted(
            await rows(
                conn, "SELECT source_version_id FROM memory_derivation WHERE derived_version_id = %s", (receipt.id,)
            )
        ) == sorted([(first,), (second,)])
        assert await rows(
            conn,
            "SELECT support_count, counter_count, undetermined_count, opportunity_count, verification_status "
            "FROM abstraction_stats WHERE version_id = %s",
            (receipt.id,),
        ) == [(3, 0, 1, 4, "unverified")]
        got = await engine.call(agent("alice", "C"), "get", {"artifact_id": receipt.id})
        assert isinstance(got, Artifact)
        assert (got.kind, got.lifecycle, got.channel, got.verification) == (
            "abstraction",
            "inferred",
            "hypotheses",
            "unverified",
        )
        found = await engine.call(agent("alice", "C"), "query", {"request": {"scope": "/u/alice/p/chrona/"}})
        assert isinstance(found, QueryResult)
        assert [a.content for a in found.hypotheses] == [STATEMENT]
        assert receipt.id not in {a.artifact_id for a in found.facts}  # an inference never renders as a fact


async def test_infer_replays_by_idempotency_key(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        ref = await method(engine)
        source = await observed(engine, "alice", "e1")
        first = await engine.call(agent("alice"), "infer", infer_args(ref, [source]))
        again = await engine.call(agent("alice"), "infer", infer_args(ref, [source]))
        assert isinstance(first, Receipt) and isinstance(again, Receipt)
        assert again == first
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'abstraction'") == [(1,)]


async def test_another_user_cannot_see_or_derive_from_the_abstraction(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        ref = await method(engine)
        source = await observed(engine, "alice", "e1")
        receipt = await engine.call(agent("alice"), "infer", infer_args(ref, [source]))
        assert isinstance(receipt, Receipt)
        assert (await forbidden(engine, caller("bob"), "get", {"artifact_id": receipt.id})).code == "forbidden"
        bobs = await engine.call(caller("bob"), "query", {"request": {"scope": "/u/bob/"}})
        assert isinstance(bobs, QueryResult) and not bobs.hypotheses
        # bob cannot cite alice's artifact as a source: unreadable answers exactly like absent
        stolen = infer_args(ref, [receipt.id], scope="/u/bob/", idempotency_key="b1")
        assert (await forbidden(engine, agent("bob"), "infer", stolen)).code == "forbidden"
        missing = infer_args(ref, ["v_does_not_exist"], scope="/u/bob/", idempotency_key="b2")
        assert (await forbidden(engine, agent("bob"), "infer", missing)).code == "forbidden"
        # nor write into alice's namespace
        foreign = infer_args(ref, [source], idempotency_key="b3")
        assert (await forbidden(engine, agent("bob"), "infer", foreign)).code == "forbidden"
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'abstraction'") == [(1,)]


async def test_infer_is_tier_b_only_and_requires_a_registered_method(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        ref = await method(engine)
        source = await observed(engine, "alice", "e1")
        assert (await forbidden(engine, caller("alice"), "infer", infer_args(ref, [source]))).code == "forbidden"
        unregistered = infer_args({**ref, "version": "2"}, [source])
        refused = await forbidden(engine, agent("alice"), "infer", unregistered)  # the entry point's condition
        assert (refused.code, refused.detail) == ("rejected", "method_not_registered")
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'abstraction'") == [(0,)]


async def test_an_empty_ledger_is_a_recorded_rejection_and_a_malformed_one_is_invalid(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine)
        ref = await method(engine)
        source = await observed(engine, "alice", "e1")
        missing = await engine.call(agent("alice"), "infer", infer_args(ref, [source], ledger=None))
        assert missing == Rejected("insufficient_evidence")
        assert await rows(
            conn,
            "SELECT details->>'creation_allowed', details->>'opportunity' FROM audit_event "
            "WHERE event_type = 'soundness_assessed' AND user_id = 'alice'",
        ) == [("false", "0")]
        for ledger, detail in (
            ({"support": 1, "counter": 0, "undetermined": 0}, "ledger_incomplete"),
            ({"support": 1, "counter": 0, "undetermined": 0, "opportunity": 2}, "ledger_unbalanced"),
            ({"support": -1, "counter": 0, "undetermined": 1, "opportunity": 0}, "ledger_counts_invalid"),
        ):
            bad = await forbidden(engine, agent("alice"), "infer", infer_args(ref, [source], ledger=ledger))
            assert (bad.code, bad.detail) == ("validation_error", detail)
        predicate = infer_args(ref, [source], predicate="recurring_failure")
        bad = await forbidden(engine, agent("alice"), "infer", predicate)
        assert (bad.code, bad.detail) == ("validation_error", "predicate_not_supported")
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE kind = 'abstraction'") == [(0,)]


async def test_infer_is_no_longer_unwired(database) -> None:  # noqa: F811
    async with world(database) as (_conn, engine, _schema):
        wire(engine)
        assert "infer" not in engine.unwired_operations()
