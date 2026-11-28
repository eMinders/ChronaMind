"""Session -> durable promotion through :meth:`Engine.call` (``V2-D70``, issue #163; E-021-F4).

The promotion event is a durable artifact coming to rest on a session source: ``infer`` derives an abstraction (always
``durable``, ``V2-D10`` floor) from it. Under a profile with ``retention.promote_to_durable`` the source becomes
``durable`` and the promotion is recorded; under one without, the same calls leave it ``session``. Every call runs as a
``NOSUPERUSER NOBYPASSRLS`` role.
"""

import json

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import Engine
from custom_memory_engine.api.infer_handlers import register_infer_handlers
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.profile import Profile, parse_profile
from tests.integration.test_api_wiring_structure import database, rows, world  # noqa: F401
from tests.integration.test_infer_db import agent, infer_args, method
from tests.integration.test_read_path_db import CODING_PROFILE, episode

pytestmark = pytest.mark.integration
SCOPE = "/u/alice/p/chrona/"


def variant(*, promote: bool, episode_retention: str = "session") -> Profile:
    """The coding test profile with only the promotion flag and the episode default changed: configuration only."""
    document = json.loads(CODING_PROFILE.model_dump_json())
    document["name"] = f"promotion-{str(promote).lower()}-{episode_retention}"
    document["retention"]["promote_to_durable"] = promote
    document["retention"]["defaults"]["episode"] = episode_retention
    return parse_profile(document)


async def scenario(engine: Engine, profile: Profile) -> tuple[str, str]:
    """The same events under any profile: one tool episode, then an abstraction inferred from it."""
    assert isinstance(engine.resolver, DbTargets)
    runtime = engine.resolver.runtime
    register_ingest_handlers(engine, runtime, profile)
    register_infer_handlers(engine, runtime, tau_confidence=0.90, tau_coverage=0.60, profile=profile)
    source = await engine.call(
        agent("alice", "C"), "ingest_episode", episode(SCOPE, "e1", "run one", source_type="tool")
    )
    assert isinstance(source, Receipt)
    ref = await method(engine)
    inferred = await engine.call(agent("alice"), "infer", infer_args(ref, [source.id]))
    assert isinstance(inferred, Receipt)
    return source.id, inferred.id


async def promotions(conn: AsyncConnection, source_id: str) -> list[tuple[object, ...]]:
    return await rows(
        conn,
        "SELECT details->>'reason_code', details->>'kind' FROM audit_event "
        "WHERE event_type = 'retention_promoted' AND memory_ref = %s",
        (source_id,),
    )


async def test_a_promoting_profile_makes_the_session_source_of_a_durable_artifact_durable(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        source_id, abstraction_id = await scenario(engine, variant(promote=True))
        assert await rows(conn, "SELECT retention FROM memory_version WHERE id = %s", (source_id,)) == [("durable",)]
        assert await promotions(conn, source_id) == [("derived_into_durable", "episode")]
        # the derivation the promotion protects is intact, and the abstraction itself is durable (floor)
        assert await rows(
            conn, "SELECT source_version_id FROM memory_derivation WHERE derived_version_id = %s", (abstraction_id,)
        ) == [(source_id,)]
        assert await rows(conn, "SELECT retention FROM memory_version WHERE id = %s", (abstraction_id,)) == [
            ("durable",)
        ]


async def test_a_non_promoting_profile_leaves_the_same_source_session(database) -> None:  # noqa: F811
    """Same events, different configuration, different outcome: the flag is read, and nothing else decides."""
    async with world(database) as (conn, engine, _schema):
        source_id, _ = await scenario(engine, variant(promote=False))
        assert await rows(conn, "SELECT retention FROM memory_version WHERE id = %s", (source_id,)) == [("session",)]
        assert await promotions(conn, source_id) == []


async def test_a_working_source_is_never_promoted(database) -> None:  # noqa: F811
    """Only ``session`` is promoted; ``working`` keeps its purge semantics (E-026)."""
    async with world(database) as (conn, engine, _schema):
        source_id, _ = await scenario(engine, variant(promote=True, episode_retention="working"))
        assert await rows(conn, "SELECT retention FROM memory_version WHERE id = %s", (source_id,)) == [("working",)]
        assert await promotions(conn, source_id) == []


async def test_a_replayed_infer_records_no_second_promotion(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        source_id, abstraction_id = await scenario(engine, variant(promote=True))
        ref = await method(engine)
        again = await engine.call(agent("alice"), "infer", infer_args(ref, [source_id]))
        assert isinstance(again, Receipt) and again.id == abstraction_id
        assert await promotions(conn, source_id) == [("derived_into_durable", "episode")]
