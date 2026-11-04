"""The six coding-shaped fixtures, end to end through the generic public API under the coding profile (E-021 AC4/AC5).

Every call goes through :meth:`Engine.call` as a ``NOSUPERUSER NOBYPASSRLS`` role, with the literal source types the
fixtures declare (``tool``, ``file``, ``event``, ``turn``), in the repository scope ``/u/alice/p/chrona/``. Each
artifact is read back through ``get`` and ``query``, and its channel is the one the reader receives, not one
recomputed from the fixture's own fields.

The recurring failure mode is an inference, so it has no source of its own: the tool output it rests on is ingested
as an episode, and ``infer`` derives the abstraction from that episode with a registered method.

A coding-agent session selects its repository scope. Under V2 section 5 rule 4 (T-06), a ``private`` row is read
only through a selector equal to its scope, so a session that selected only ``/u/alice/`` does not see the
repository's rows; the last test pins that.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine
from custom_memory_engine.api.infer_handlers import register_infer_handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.profile import Profile, load_profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.fixtures.coding_agent import SCOPE, CodingArtifact, coding_artifacts
from tests.integration.test_api_wiring_structure import HASH, caller, database, forbidden, rows, world  # noqa: F401

pytestmark = pytest.mark.integration
PROFILE = Path(__file__).resolve().parents[2] / "src" / "custom_memory_engine" / "profiles" / "coding.json"
# V2-D30 is open: the soundness thresholds are stated by whoever wires `infer`; these are its suggested values.
TAU_CONFIDENCE, TAU_COVERAGE = 0.90, 0.60
LEDGER = {"support": 2, "counter": 0, "undetermined": 0, "opportunity": 2}  # the two failing runs the source shows


def agent(user: str, tier: Tier = "C") -> CallerContext:
    """A coding-agent session: it selects the repository scope as well as the personal root."""
    read_set = resolve_read_set(user, [Selector.of(f"/u/{user}/"), Selector.of(SCOPE.replace("alice", user))], [])
    return CallerContext.from_credential(Credential(user, tier, False), write_scope=f"/u/{user}/", read_set=read_set)


@dataclass(frozen=True, slots=True)
class Scenario:
    """The stored ids: one per fixture, plus the observation the inference rests on."""

    ids: Mapping[str, str]
    observation: str


def wire(engine: Engine, profile: Profile) -> None:
    assert isinstance(engine.resolver, DbTargets)
    runtime = engine.resolver.runtime
    register_ingest_handlers(engine, runtime, profile)
    register_read_handlers(engine, runtime)
    register_infer_handlers(engine, runtime, tau_confidence=TAU_CONFIDENCE, tau_coverage=TAU_COVERAGE, profile=profile)


def source_of(item: CodingArtifact) -> dict[str, str]:
    return {
        "type": item.source.source_type,
        "ref": item.source.ref,
        "span": item.source.span,
        "content_hash": item.source.content_hash,
    }


async def store(conn: AsyncConnection, engine: Engine, item: CodingArtifact, sequence: int) -> str:
    """Store one grounded fixture through the operation its kind uses; return the artifact id."""
    if item.kind == "episode":
        receipt = await engine.call(
            agent("alice"),
            "ingest_episode",
            {
                "scope": SCOPE,
                "source": source_of(item),
                "content": item.content,
                "event_time": item.event_time.isoformat(),
                "idempotency_key": item.name,
            },
        )
    else:  # claim / procedure: grounded in a captured instruction turn through the v1 gate
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
            (item.source.ref, sequence, item.source.content, item.event_time),
        )
        receipt = await engine.call(
            agent("alice"),
            "assert",
            {
                "scope": SCOPE,
                "kind": item.kind,
                "content": item.content,
                "evidence": [source_of(item)],
                "event_range": None,
                "idempotency_key": item.name,
            },
        )
    assert isinstance(receipt, Receipt), item.name
    expected = "observed" if item.kind == "episode" else "asserted"
    assert receipt.effect == expected, f"{item.name}: {receipt.effect}"
    return receipt.id


async def infer(engine: Engine, item: CodingArtifact) -> tuple[str, str]:
    """Ingest the tool output the inference rests on, then derive the abstraction; return (abstraction, episode)."""
    observation = await engine.call(
        agent("alice"),
        "ingest_episode",
        {
            "scope": SCOPE,
            "source": source_of(item),
            "content": item.source.content,
            "event_time": item.event_time.isoformat(),
            "idempotency_key": f"{item.name}-observation",
        },
    )
    assert isinstance(observation, Receipt)
    method = await engine.call(
        caller("alice", admin=True),
        "register_method",
        {"name": "failure-recurrence", "version": "1", "params_schema_hash": HASH, "idempotency_key": "method"},
    )
    assert isinstance(method, str)
    receipt = await engine.call(
        agent("alice", "B"),
        "infer",
        {
            "scope": SCOPE,
            "method": {"id": method, "version": "1", "params_hash": HASH},
            "sources": [observation.id],
            "statement": item.content,
            "predicate": None,
            "ledger": LEDGER,
            "idempotency_key": item.name,
        },
    )
    assert isinstance(receipt, Receipt) and receipt.effect == "inferred", item.name
    return receipt.id, observation.id


async def scenario(conn: AsyncConnection, engine: Engine) -> Scenario:
    ids: dict[str, str] = {}
    observation = ""
    for sequence, item in enumerate(coding_artifacts(), start=2):
        if item.kind == "abstraction":
            ids[item.name], observation = await infer(engine, item)
        else:
            ids[item.name] = await store(conn, engine, item, sequence)
    return Scenario(ids, observation)


async def test_each_fixture_is_stored_under_the_coding_profile_in_the_repository_scope(database) -> None:  # noqa: F811
    """AC4 (stored): kind, lifecycle, the profile's retention default, the repository scope and the declared source."""
    profile = load_profile(PROFILE)
    async with world(database) as (conn, engine, _schema):
        wire(engine, profile)
        stored = await scenario(conn, engine)
        for item in coding_artifacts():
            artifact_id = stored.ids[item.name]
            row = await rows(
                conn,
                "SELECT kind, lifecycle, retention, scope_path, created_tier FROM memory_version WHERE id = %s",
                (artifact_id,),
            )
            tier = "B" if item.kind == "abstraction" else "C"
            retention = profile.retention.defaults[item.kind]
            assert row == [(item.kind, item.lifecycle, retention, SCOPE, tier)], item.name
            if item.kind == "abstraction":
                # an inference carries a derivation, never evidence; the evidence sits on the observation it rests on
                assert await rows(
                    conn,
                    "SELECT source_version_id FROM memory_derivation WHERE derived_version_id = %s",
                    (artifact_id,),
                ) == [(stored.observation,)], item.name
                assert await rows(
                    conn,
                    "SELECT support_count, opportunity_count, verification_status FROM abstraction_stats "
                    "WHERE version_id = %s",
                    (artifact_id,),
                ) == [(2, 2, "unverified")], item.name
                artifact_id = stored.observation
            evidence = await rows(
                conn,
                "SELECT source_type, source_ref, span, content_hash FROM memory_evidence WHERE version_id = %s",
                (artifact_id,),
            )
            source = item.source
            assert evidence == [(source.source_type, source.ref, source.span, source.content_hash)], item.name


async def test_each_fixture_routes_to_its_channel_as_the_reader_receives_it(database) -> None:  # noqa: F811
    """AC5: the channel ``get`` reports, per artifact, against the fixture's CHANNELS.md expectation."""
    async with world(database) as (conn, engine, _schema):
        wire(engine, load_profile(PROFILE))
        stored = await scenario(conn, engine)
        for item in coding_artifacts():
            got = await engine.call(agent("alice"), "get", {"artifact_id": stored.ids[item.name]})
            assert isinstance(got, Artifact), item.name
            assert (got.kind, got.lifecycle, got.verification, got.channel, got.scope_path) == (
                item.kind,
                item.lifecycle,
                item.verification,
                item.expected_channel,
                SCOPE,
            ), item.name


async def test_a_repository_query_returns_each_fixture_in_its_channel(database) -> None:  # noqa: F811
    """AC4 (queried) and AC5: one scoped ``query`` returns all six, each in exactly the channel CHANNELS.md names."""
    async with world(database) as (conn, engine, _schema):
        wire(engine, load_profile(PROFILE))
        stored = await scenario(conn, engine)
        result = await engine.call(agent("alice"), "query", {"request": {"scope": SCOPE}})
        assert isinstance(result, QueryResult) and not result.refused
        channels = {
            "facts": result.facts,
            "hypotheses": result.hypotheses,
            "procedures": result.procedures,
            "conflicts": result.conflicts,
        }
        placed = {a.artifact_id: name for name, bucket in channels.items() for a in bucket}
        for item in coding_artifacts():
            assert placed.get(stored.ids[item.name]) == item.expected_channel, item.name
        # the only other row is the observation the inference rests on, itself an observed episode
        assert set(placed) - set(stored.ids.values()) == {stored.observation}
        assert placed[stored.observation] == "facts"


async def test_another_user_sees_none_of_the_scenario(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        wire(engine, load_profile(PROFILE))
        stored = await scenario(conn, engine)
        for artifact_id in stored.ids.values():
            assert (await forbidden(engine, agent("bob"), "get", {"artifact_id": artifact_id})).code == "forbidden"
        bobs = await engine.call(agent("bob"), "query", {"request": {"scope": "/u/bob/"}})
        assert isinstance(bobs, QueryResult)
        assert not (bobs.facts or bobs.hypotheses or bobs.procedures or bobs.conflicts)


async def test_a_session_that_did_not_select_the_repository_does_not_read_it(database) -> None:  # noqa: F811
    """V2 section 5 rule 4 / T-06: private repository rows are not read through the personal root alone."""
    async with world(database) as (conn, engine, _schema):
        wire(engine, load_profile(PROFILE))
        stored = await scenario(conn, engine)
        root = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(root, QueryResult)
        assert not (root.facts or root.hypotheses or root.procedures or root.conflicts)
        tool_call = stored.ids[coding_artifacts()[0].name]
        assert (await forbidden(engine, caller("alice"), "get", {"artifact_id": tool_call})).code == "forbidden"
