"""RL-090: the mandatory T1-T5 archival regression contract (Invariant 10, D-27).

One test per row of ``TECHNICAL_SPECIFICATION.md`` section 6 ("Mandatory T1-T5 Regression Contract"),
each asserting that row's expected outcome and nothing weaker. Scenario (multi-valued ``hobby``):

* ``chess``         a currently-valid current belief, ``[APR1, inf)`` x ``[APR1, inf)``;
* ``cycling``       superseded by ``end_multivalued`` at JUN1 (recorded range closed);
* ``cycling_ended`` its truncated replacement, a historical current belief ``[APR1, JUN1)`` x ``[JUN1, inf)``;
* ``summary``       (T4 and T5 only) an active consolidation of ``chess`` covering ``[MAR10, inf)``.

T5 measures all seven deletion channels against the engine's real outputs after deletion: storage and
derived lookups in PostgreSQL (1, 2, 4), dense+lexical retrieval (3), the rendered context (5), an
extractive answer over it (6), and every structlog event emitted during the whole scenario (7). A control
runs the same measurement before deletion and must see the canary, so a clean verdict is not vacuous.
"""

import json
from collections.abc import AsyncIterator, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
import structlog
from psycopg import AsyncConnection, sql

from custom_memory_engine.deletion.scanner import ChannelProbe, LeakChannel, ScanContext, scan_deleted_key
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.predicates import audit_as_of, current_state, fetch_versions, historical_as_of
from custom_memory_engine.engine.render import render_context
from custom_memory_engine.engine.retrieval import rank_versions
from custom_memory_engine.engine.transactions import (
    ArchivalPreconditionError,
    CandidateVersionInput,
    DerivationInput,
    EvidenceInput,
    archive_version,
    commit_initial_version,
    end_multivalued,
)
from evaluation.harness.deletion_suite import extractive_answerer
from evaluation.harness.leak_scanner import (
    answer_probe,
    assembled_context_probe,
    external_cache_probe,
    log_sink_probe,
    vector_probe,
    verdict,
)
from tests.integration.schema import engine_database

pytestmark = pytest.mark.integration

MAR10, APR1, MAY1 = (datetime(2026, m, d, tzinfo=UTC) for m, d in ((3, 10), (4, 1), (5, 1)))
JUN1, JUL15, JUL20, OCT1 = (datetime(2026, m, d, tzinfo=UTC) for m, d in ((6, 1), (7, 15), (7, 20), (10, 1)))
CANARY = "quillfeather-canary-77"
CHESS_KEY = "user:hobby:chess"
MODEL = "BAAI/bge-small-en-v1.5"
QUERY_VECTOR = [1.0] + [0.0] * 383


@dataclass
class World:
    conn: AsyncConnection
    schema: str
    logs: list[MutableMapping[str, Any]]  # the live structlog capture of the whole scenario (channel 7)


@pytest_asyncio.fixture
async def world() -> AsyncIterator[World]:
    async with (
        engine_database() as (config, schema),
        await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn,
    ):
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET timezone = 'UTC'")
        with structlog.testing.capture_logs() as captured:
            world = World(conn, schema, captured)
            await _seed(world)
            yield world


async def _seed(w: World) -> None:
    c = w.conn
    await c.execute("INSERT INTO predicate_schema VALUES ('hobby', 'multi', 'persona')")
    await c.execute("INSERT INTO app_user VALUES ('u1', 'ext-u1', %s)", (MAR10,))
    await c.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s1', 'u1', %s)", (MAR10,))
    await c.execute(
        "INSERT INTO turn VALUES ('t1', 's1', 'u1', 1, 'user', %s, %s)", (f"I play chess ({CANARY}) and cycle", APR1)
    )
    async with c.transaction():
        await c.execute("SELECT set_config('app.user_id', 'u1', true)")
        _, tick = await commit_initial_version(
            c,
            _candidate("chess", CHESS_KEY, f"Plays chess, codename {CANARY}", APR1),
            schema_name=w.schema,
            replay_time=APR1,
        )
        await commit_initial_version(
            c,
            _candidate("cycling", "user:hobby:cycling", "Rides a bicycle", APR1),
            schema_name=w.schema,
            clock_tick=tick,
        )
        await _embed(c, "chess")
    async with c.transaction():
        await c.execute("SELECT set_config('app.user_id', 'u1', true)")
        await end_multivalued(
            c,
            user_id="u1",
            version_id="cycling",
            valid_end=JUN1,
            truncated_version_id="cycling_ended",
            schema_name=w.schema,
            replay_time=JUN1,
        )


def _candidate(
    version_id: str, key: str, content: str, valid_from: datetime, *, derived_from: str | None = None
) -> CandidateVersionInput:
    return CandidateVersionInput(
        version_id=version_id,
        memory_key=key,
        user_id="u1",
        subject_key="user",
        predicate_key="hobby",
        single_valued=False,
        content=content,
        valid_from=valid_from,
        evidence=() if derived_from else (EvidenceInput(turn_id="t1", evidence_span="I play chess"),),
        derivations=(DerivationInput(source_version_id=derived_from, derivation_type="consolidation"),)
        if derived_from
        else (),
    )


async def _embed(c: AsyncConnection, version_id: str) -> None:
    await c.execute(
        "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, 'u1', %s)",
        (version_id, MODEL, QUERY_VECTOR),
    )


async def _archive(w: World, version_id: str, at: datetime = JUL15) -> str:
    async with w.conn.transaction():
        await w.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        outcome = await archive_version(
            w.conn, user_id="u1", version_id=version_id, schema_name=w.schema, replay_time=at
        )
    return outcome.archival_branch


async def _status(w: World, version_id: str) -> tuple[str, str | None] | None:
    cursor = await w.conn.execute("SELECT status, archival_branch FROM memory_version WHERE id = %s", (version_id,))
    row = await cursor.fetchone()
    return None if row is None else (row[0], row[1])


async def _ids(w: World, query) -> set[str]:  # noqa: ANN001 - TemporalQuery
    async with w.conn.transaction():
        await w.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        result = await fetch_versions(w.conn, query, schema_name=w.schema)
    return {v.id for v in result.versions}


async def _t4(w: World) -> str:
    """Commit the covering consolidation ``summary`` (derived from ``chess``) and archive ``chess``."""
    async with w.conn.transaction():
        await w.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        _, tick = await commit_initial_version(
            w.conn,
            _candidate(
                "summary", "user:hobby:chess_summary", f"Regular chess player ({CANARY})", MAR10, derived_from="chess"
            ),
            schema_name=w.schema,
            replay_time=JUL20,
        )
        await _embed(w.conn, "summary")
        outcome = await archive_version(
            w.conn, user_id="u1", version_id="chess", schema_name=w.schema, clock_tick=tick
        )
    return outcome.archival_branch


async def test_t1_currently_valid_current_belief_is_not_archivable(world: World) -> None:
    with pytest.raises(ArchivalPreconditionError, match="archival_precondition_unmet"):
        await _archive(world, "chess")
    assert await _status(world, "chess") == ("active", None)


async def test_t2_historical_current_belief_is_not_archivable_and_stays_queryable(world: World) -> None:
    with pytest.raises(ArchivalPreconditionError, match="archival_precondition_unmet"):
        await _archive(world, "cycling_ended")
    assert await _status(world, "cycling_ended") == ("active", None)
    assert "cycling_ended" in await _ids(world, historical_as_of("u1", MAY1, OCT1))


async def test_t3_superseded_version_archives_via_branch_s_and_stays_auditable(world: World) -> None:
    assert await _archive(world, "cycling") == "S"
    assert await _status(world, "cycling") == ("archived", "S")
    assert "cycling" in await _ids(world, audit_as_of("u1", MAY1, MAY1))


async def test_t4_covered_version_archives_via_branch_c_and_queries_return_the_replacement(world: World) -> None:
    assert await _t4(world) == "C"
    assert await _status(world, "chess") == ("archived", "C")
    assert "summary" in await _ids(world, current_state("u1", OCT1))
    cursor = await world.conn.execute(
        "SELECT source_version_id FROM memory_derivation WHERE derived_version_id = 'summary'"
    )
    assert await cursor.fetchall() == [("chess",)], "the replacement keeps its provenance link to r"


async def _seven_channels(w: World, epoch: int) -> tuple[tuple[str, ...], bool]:
    """Measure every channel against the engine's real outputs; return (leaking channels, complete)."""
    async with w.conn.transaction():
        await w.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        ranked = []
        for query in (current_state("u1", OCT1), historical_as_of("u1", MAY1, OCT1)):
            outcome = await rank_versions(
                w.conn, query, query_text=CANARY, query_embedding=QUERY_VECTOR, schema_name=w.schema
            )
            ranked.extend(r.version for r in outcome.ranked)
    context = render_context(ranked, mode="current", token_budget=4000, reference_time=OCT1).text

    class Engine:
        def vector_hits(self, user_id: str, query: str, limit: int) -> list[str]:
            return [v.content for v in ranked][:limit]

        def assembled_context(self, user_id: str, query: str) -> str:
            return context

        def answer(self, user_id: str, question: str) -> str:
            return extractive_answerer(context, question)

    engine = Engine()
    probes: dict[LeakChannel, ChannelProbe] = {
        "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda user_id: []),  # the engine has none
        "channel_3_vector_search": vector_probe(engine, query=CANARY),
        "channel_5_assembled_context": assembled_context_probe(engine, query=CANARY),
        "channel_6_final_answer": answer_probe(engine, question=CANARY),
        "channel_7_application_logs": log_sink_probe(lambda: [json.dumps(e, default=str) for e in w.logs]),
    }
    report = await scan_deleted_key(
        ScanContext("u1", CHESS_KEY, CANARY, epoch), probes, connection=w.conn, schema_name=w.schema
    )
    result = verdict(report)
    return result.leaking_channels, result.complete


async def test_t5_deleting_the_key_removes_it_and_its_replacement_with_zero_leakage(world: World) -> None:
    assert await _t4(world) == "C"
    leaking_before, _ = await _seven_channels(world, epoch=1)
    assert {"channel_1_storage_index_queue_cache_scan", "channel_3_vector_search"} <= set(leaking_before), (
        "control: the canary must be visible before deletion, or the clean verdict below means nothing"
    )

    async with world.conn.transaction():
        await world.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        receipt = await delete_memory(
            world.conn, "u1", scope="memory", target_ref=CHESS_KEY, schema_name=world.schema, replay_time=OCT1
        )
    cursor = await world.conn.execute("SELECT count(*) FROM memory_version WHERE memory_key = %s", (CHESS_KEY,))
    assert (await cursor.fetchone() or (None,))[0] == 0, "every version of r.memory_key is gone"
    cursor = await world.conn.execute(
        "SELECT count(*) FROM memory_derivation WHERE source_version_id = 'chess' OR derived_version_id = 'chess'"
    )
    assert (await cursor.fetchone() or (None,))[0] == 0
    # r' was derived from r alone, so it cannot be re-derived without r: it must be removed.
    assert await _status(world, "summary") is None
    assert receipt.dependents == 1 and receipt.rederived_dependents == 0

    leaking_after, complete = await _seven_channels(world, epoch=receipt.deletion_epoch)
    assert leaking_after == ()
    assert complete, "all seven channels measured, none partial"
    assert await _status(world, "cycling_ended") == ("active", None), "unrelated memory is untouched"
    assert "memory_deletion_completed" in {e.get("event") for e in world.logs}, (
        "channel 7 observed the engine's own deletion event, so its zero is not vacuous"
    )


async def test_t5_counterpart_deleting_only_the_replacement_is_still_refused(world: World) -> None:
    """Deferring the archival triggers inside deletion must not weaken Invariant 10: removing r' while the
    (C)-archived r survives would leave r uncovered, so the whole deletion is refused and nothing is removed."""
    assert await _t4(world) == "C"
    with pytest.raises(Exception, match="archival_breaks_covered_source_invariant"):
        async with world.conn.transaction():
            await world.conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await delete_memory(
                world.conn,
                "u1",
                scope="memory",
                target_ref="user:hobby:chess_summary",
                schema_name=world.schema,
                replay_time=OCT1,
            )
    assert await _status(world, "summary") == ("active", None)
    assert await _status(world, "chess") == ("archived", "C")
