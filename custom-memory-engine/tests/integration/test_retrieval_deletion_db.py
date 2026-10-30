"""Retrieval ranking, lineage deletion and gate persistence over the full schema."""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.deletion.closure import StaleDeletionEpoch, require_current_deletion_epoch
from custom_memory_engine.deletion.rederive import ActiveSource, RebuiltSummary, ReDerivationError
from custom_memory_engine.deletion.scanner import ScanContext, scan_deleted_key
from custom_memory_engine.engine.deletion import delete_memory, deletion_epoch
from custom_memory_engine.engine.gate import GateDecision, existing_relation, record_decision
from custom_memory_engine.engine.predicates import current_state
from custom_memory_engine.engine.render import render_context
from custom_memory_engine.engine.retrieval import RetrievalError, rank_versions, retrieve_current
from custom_memory_engine.queue.contracts import NewJob
from custom_memory_engine.queue.worker import enqueue, process_one

pytestmark = pytest.mark.integration

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema/001_full_schema.sql"
MODEL = "BAAI/bge-small-en-v1.5"
START = datetime(2026, 3, 1, tzinfo=UTC)
MID = datetime(2026, 6, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


def vector(*weights: float) -> list[float]:
    """A deterministic 384-dim unit vector concentrated on the given weights."""
    base = [0.0] * 384
    for index, weight in enumerate(weights):
        base[index] = weight
    return base


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    config = DatabaseConfig.from_environment()
    schema = "cme_retrieval_" + uuid4().hex
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        async with connect((config, schema)) as conn:
            await conn.execute(cast(LiteralString, SCHEMA_SQL.read_text(encoding="utf-8")), prepare=False)
            await seed(conn)
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '30s'")
        await conn.execute("SET timezone = 'UTC'")
        yield conn


async def identity(conn: AsyncConnection, user: str = "u1", clock: datetime = END, *, local: bool = False) -> None:
    await conn.execute("SELECT set_config('app.user_id', %s, %s)", (user, local))
    await conn.execute("SELECT set_config('app.clock', %s, %s)", (clock.isoformat(), local))


async def insert_version(
    conn: AsyncConnection,
    version_id: str,
    content: str,
    *,
    valid_from: datetime = START,
    valid_until: datetime | None = None,
    memory_key: str = "diet",
    subject: str = "user",
    predicate: str = "diet",
    embedding: list[float] | None = None,
    user: str = "u1",
    single_valued: bool = True,
) -> None:
    await conn.execute(
        """
        INSERT INTO memory_version (
            id, memory_key, user_id, status, subject_key, predicate_key, single_valued, content,
            valid_range, recorded_range
        ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, tstzrange(%s, %s, '[)'), tstzrange(clock_now(), NULL, '[)'))
        """,
        (version_id, memory_key, user, subject, predicate, single_valued, content, valid_from, valid_until),
    )
    turn_id = f"turn-{version_id}"
    await conn.execute(
        """INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at)
           VALUES (%s, %s, %s, (SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM turn WHERE session_id = %s),
                   'user', %s, %s)
           ON CONFLICT DO NOTHING""",
        (turn_id, "s-" + user, user, "s-" + user, content, valid_from),
    )
    await conn.execute(
        """INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role)
           VALUES (%s, %s, %s, %s, 'support')""",
        (version_id, turn_id, user, content),
    )
    if embedding is not None:
        await conn.execute(
            "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, %s, %s)",
            (version_id, MODEL, user, embedding),
        )


async def seed(conn: AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO predicate_schema VALUES "
        "('diet', 'single', 'semantic'), ('exercise', 'single', 'semantic'), "
        "('diet_other', 'single', 'semantic'), ('diet_summary', 'single', 'semantic'), "
        "('diet_a', 'single', 'semantic'), ('diet_b', 'single', 'semantic'), "
        "('topic_a', 'single', 'semantic'), ('topic_b', 'single', 'semantic'), "
        "('topic_c', 'single', 'semantic'), ('plans_to', 'single', 'semantic')"
    )
    for user in ("u1", "u2"):
        await identity(conn, user, START)
        await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, START))
        await conn.execute("INSERT INTO session VALUES (%s, %s, %s, NULL)", ("s-" + user, user, START))
    # Phase 1 (clock START): the vegetarian belief is the only current one.
    await identity(conn, "u1", START)
    async with conn.transaction():
        await conn.execute(
            """INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at)
               VALUES ('t1', 's-u1', 'u1', 1, 'user', 'I am vegetarian', %s)""",
            (START,),
        )
        await insert_version(conn, "v1", "the user is vegetarian", embedding=vector(0.1))
        await insert_version(
            conn,
            "v3",
            "the user runs every morning",
            memory_key="exercise",
            predicate="exercise",
            embedding=vector(0.2),
        )
    # Phase 2 (clock MID): R1/R2 close-then-insert supersession; v2 becomes current.
    await identity(conn, "u1", MID)
    async with conn.transaction():
        await conn.execute(
            "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = 'v1'"
        )
        await insert_version(conn, "v1p", "the user is vegetarian", valid_until=MID, embedding=vector(0.1))
        await insert_version(conn, "v2", "the user eats fish", valid_from=MID, embedding=vector(0.9))
    await identity(conn, "u1", END)


async def test_dense_channel_ranks_by_cosine_and_never_returns_superseded_rows(database) -> None:
    config, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        outcome = await rank_versions(
            conn,
            current_state("u1", END),
            query_text="what does the user eat",
            query_embedding=vector(1.0),
            fusion="dense",
            schema_name=schema,
        )
    assert [ranked.version.id for ranked in outcome.ranked] == ["v2", "v3"]
    assert outcome.candidates == 2, "superseded and ended-validity rows never enter the candidate set"
    assert outcome.ranked[0].dense_rank == 1
    assert outcome.ranked[0].lexical_rank is None


async def test_lexical_channel_uses_english_full_text_and_zero_overlap_drops_out(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        outcome = await rank_versions(
            conn, current_state("u1", END), query_text="morning runs", fusion="lexical", schema_name=schema
        )
    assert [ranked.version.id for ranked in outcome.ranked] == ["v3"]
    assert outcome.ranked[0].lexical_rank == 1


async def test_fused_ranking_survives_both_channels_and_respects_limit(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        outcome = await retrieve_current(
            conn,
            "u1",
            END,
            query_text="morning",
            query_embedding=vector(1.0),
            limit=2,
            schema_name=schema,
        )
    assert len(outcome.ranked) == 2
    # RRF rewards two-channel agreement: v3 (lexical 1st, dense 2nd) outranks v2 (dense 1st only).
    by_id = {ranked.version.id: ranked for ranked in outcome.ranked}
    assert (by_id["v3"].lexical_rank, by_id["v3"].dense_rank) == (1, 2)
    assert (by_id["v2"].dense_rank, by_id["v2"].lexical_rank) == (1, None)
    assert outcome.ranked[0].version.id == "v3"


async def test_retrieval_rejects_audit_queries_and_missing_embeddings(database) -> None:
    from custom_memory_engine.engine.predicates import audit_as_of

    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        with pytest.raises(RetrievalError, match="audit_not_for_answer_context"):
            await rank_versions(conn, audit_as_of("u1", MID, END), query_text="x", schema_name=schema)
        with pytest.raises(RetrievalError, match="dense_channel_requires_query_embedding"):
            await rank_versions(conn, current_state("u1", END), query_text="x", fusion="dense", schema_name=schema)


async def test_retrieval_requires_matching_tenant_context(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u2")
        with pytest.raises(Exception, match="retrieval_user_context_mismatch"):
            await rank_versions(conn, current_state("u1", END), query_text="x", fusion="lexical", schema_name=schema)


async def test_ranked_result_renders_within_budget_and_labels_roles(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        outcome = await rank_versions(
            conn, current_state("u1", END), query_text="fish", fusion="lexical", schema_name=schema
        )
    versions = tuple(ranked.version for ranked in outcome.ranked)
    rendered = render_context(versions, mode="current", token_budget=1_000, reference_time=END)
    assert rendered.tokens == sum(item.tokens for item in rendered.items)
    assert rendered.items and all(item.label == "current" for item in rendered.items)


async def test_gate_relation_detects_exact_duplicate_and_records_decision(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        duplicate, existing_id = await existing_relation(
            conn,
            user_id="u1",
            subject_key="user",
            predicate_key="diet",
            candidate_content="The user   eats FISH",
            schema_name=schema,
        )
        assert (duplicate, existing_id) == ("duplicate", "v2")

        novel, _ = await existing_relation(
            conn,
            user_id="u1",
            subject_key="user",
            predicate_key="diet",
            candidate_content="the user is left-handed",
            schema_name=schema,
        )
        assert novel is None

        conflict_id = await record_decision(
            conn,
            user_id="u1",
            candidate_version_id="v2",
            decision=GateDecision("accept", "reinforce_duplicate", ("conflict",), "active", relation="duplicate"),
            existing_version_id="v2",
            schema_name=schema,
        )
    async with connect(database) as conn:
        await identity(conn, "u1")
        cursor = await conn.execute(
            "SELECT user_id, relation, decision, reason_code FROM memory_conflict WHERE id = %s", (conflict_id,)
        )
        row = await cursor.fetchone()
    assert row == ("u1", "duplicate", "accept", "reinforce_duplicate")


async def test_delete_memory_removes_key_and_cascades_without_touching_other_keys(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="diet", schema_name=schema)
        assert receipt.deleted_versions == 3, "v1, v1p and v2 share the diet memory_key"
        assert receipt.deletion_epoch == 1
        assert receipt.request_id.startswith("del-")

    async with connect(database) as conn:
        await identity(conn, "u1")
        cursor = await conn.execute("SELECT id FROM memory_version ORDER BY id")
        assert [row[0] for row in await cursor.fetchall()] == ["v3"]
        cursor = await conn.execute("SELECT count(*) FROM memory_evidence WHERE version_id <> 'v3'")
        remaining = await cursor.fetchone()
        assert remaining is not None and remaining[0] == 0
        cursor = await conn.execute(
            "SELECT status, deletion_epoch FROM deletion_request WHERE id = %s", (receipt.request_id,)
        )
        assert (await cursor.fetchone()) == ("completed", 1)
        cursor = await conn.execute(
            "SELECT details FROM audit_event WHERE event_type = 'deletion_completed' ORDER BY id DESC LIMIT 1"
        )
        audit_row = await cursor.fetchone()
        assert audit_row is not None
        details = audit_row[0]
        assert set(details) == {
            "scope",
            "deletion_epoch",
            "deleted_versions",
            "dependents",
            "re_derived_versions",
            "purged_jobs",
        }


async def test_delete_re_derives_a_dependent_with_a_surviving_source(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            # Two independent source memories under the same memory_key as the delete target.
            await insert_version(
                conn,
                "src-a",
                "the user is vegetarian",
                memory_key="diet",
                predicate="diet_summary",
                subject="derived_sources",
                single_valued=False,
            )
            await insert_version(
                conn,
                "src-b",
                "the user avoids red meat",
                memory_key="other",
                predicate="diet_summary",
                subject="derived_sources",
                single_valued=False,
            )
            await insert_version(
                conn,
                "summary",
                "the user eats a vegetarian diet",
                memory_key="summary",
                predicate="diet_summary",
                subject="derived_sources",
                single_valued=False,
            )
            await conn.execute(
                "INSERT INTO memory_derivation VALUES ('summary', 'src-a', 'u1', 'consolidation'), "
                "('summary', 'src-b', 'u1', 'consolidation')"
            )
        async with conn.transaction():

            async def rebuild(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
                assert summary_id == "summary"
                assert [source.version_id for source in sources] == ["src-b"]
                return RebuiltSummary(
                    content="the user avoids red meat",
                    embedding_model=MODEL,
                    embedding=tuple(vector(0.3)),
                )

            receipt = await delete_memory(
                conn,
                "u1",
                scope="memory",
                target_ref="diet",
                summary_builder=rebuild,
                schema_name=schema,
            )
        assert receipt.rederived_dependents == 1

    async with connect(database) as conn:
        await identity(conn, "u1")
        cursor = await conn.execute(
            "SELECT derived_version_id, source_version_id FROM memory_derivation WHERE derived_version_id='summary'"
        )
        assert [tuple(row) for row in await cursor.fetchall()] == [("summary", "src-b")]
        cursor = await conn.execute("SELECT content FROM memory_version WHERE id='summary'")
        assert await cursor.fetchone() == ("the user avoids red meat",)
        cursor = await conn.execute("SELECT count(*) FROM memory_embedding WHERE version_id='summary'")
        assert await cursor.fetchone() == (1,)


async def test_delete_summary_with_no_active_surviving_sources_does_not_rebuild(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await insert_version(conn, "src-a", "first source", memory_key="diet", predicate="diet_a")
            await insert_version(conn, "src-b", "second source", memory_key="diet", predicate="diet_b")
            await insert_version(conn, "summary", "combined canary", memory_key="summary", predicate="diet_summary")
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('summary', 'src-a', 'u1', 'summary'), ('summary', 'src-b', 'u1', 'summary')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn,
                "u1",
                scope="memory",
                target_ref="diet",
                schema_name=schema,
            )
        assert receipt.rederived_dependents == 0
    async with connect(database) as conn:
        await identity(conn, "u1")
        cursor = await conn.execute("SELECT count(*) FROM memory_version WHERE id='summary'")
        assert await cursor.fetchone() == (0,)


async def test_a8_skips_lineage_and_primary_deletion_removes_it(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await insert_version(conn, "a8-root", "A8CANARYTOKENZ", memory_key="a8-key", predicate="diet_a")
            await insert_version(
                conn,
                "a8-derived",
                "A8CANARYTOKENZ derived",
                memory_key="a8-derived",
                predicate="diet_summary",
                subject="a8-derived",
                single_valued=False,
            )
            await insert_version(conn, "p-root", "P-CANARY", memory_key="p-key", predicate="diet_b")
            await insert_version(
                conn,
                "p-derived",
                "P-CANARY derived",
                memory_key="p-derived",
                predicate="diet_summary",
                subject="p-derived",
                single_valued=False,
            )
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('a8-derived', 'a8-root', 'u1', 'consolidation'), "
                "('p-derived', 'p-root', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            a8_receipt = await delete_memory(conn, "u1", target_ref="a8-key", ablation="A8", schema_name=schema)
            scan = await scan_deleted_key(
                ScanContext("u1", "a8-key", "A8CANARYTOKENZ", a8_receipt.deletion_epoch),
                connection=conn,
                schema_name=schema,
            )
            assert {finding.channel for finding in scan.leaks} == {
                "channel_1_storage_index_queue_cache_scan",
                "channel_2_lexical_search",
                "channel_4_derived_memory_lookup",
            }
            assert {finding.channel for finding in scan.inconclusive} == {
                "channel_1_storage_index_queue_cache_scan",
                "channel_3_vector_search",
                "channel_5_assembled_context",
                "channel_6_final_answer",
                "channel_7_application_logs",
            }
            await delete_memory(conn, "u1", target_ref="p-key", ablation="P", schema_name=schema)
        cursor = await conn.execute(
            "SELECT id FROM memory_version WHERE id IN ('a8-derived', 'p-derived') ORDER BY id"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["a8-derived"]


async def test_deletion_closure_stops_at_depth_three(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            for depth in range(5):
                await insert_version(
                    conn,
                    f"depth-{depth}",
                    f"depth fact {depth}",
                    memory_key="depth-root" if depth == 0 else f"depth-key-{depth}",
                    subject=f"depth-{depth}",
                    predicate="diet_summary",
                )
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('depth-1', 'depth-0', 'u1', 'consolidation'), "
                "('depth-2', 'depth-1', 'u1', 'consolidation'), "
                "('depth-3', 'depth-2', 'u1', 'consolidation'), "
                "('depth-4', 'depth-3', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", target_ref="depth-root", schema_name=schema)
        assert receipt.deleted_versions == 4
        cursor = await conn.execute("SELECT id FROM memory_version WHERE id LIKE 'depth-%' ORDER BY id")
        assert [row[0] for row in await cursor.fetchall()] == ["depth-4"]


async def test_deletion_failure_during_rebuild_rolls_back_all_writes(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await insert_version(conn, "src-a", "source to delete", memory_key="diet", predicate="diet_a")
            await insert_version(conn, "src-b", "source to keep", memory_key="other", predicate="diet_b")
            await insert_version(conn, "summary", "original summary", memory_key="summary", predicate="diet_summary")
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('summary', 'src-a', 'u1', 'summary'), ('summary', 'src-b', 'u1', 'summary')"
            )

        async def fail_rebuild(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
            assert summary_id == "summary"
            assert len(sources) == 1
            raise RuntimeError("forced_rebuild_failure")

        with pytest.raises(ReDerivationError, match="summary_rebuild_failed"):
            async with conn.transaction():
                await delete_memory(
                    conn,
                    "u1",
                    target_ref="diet",
                    summary_builder=fail_rebuild,
                    schema_name=schema,
                )
        cursor = await conn.execute(
            "SELECT id FROM memory_version WHERE id IN ('src-a', 'src-b', 'summary') ORDER BY id"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["src-a", "src-b", "summary"]
        cursor = await conn.execute("SELECT count(*) FROM deletion_request")
        assert await cursor.fetchone() == (0,)


async def test_deletion_epoch_retry_returns_the_original_receipt(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            first = await delete_memory(conn, "u1", target_ref="exercise", deletion_epoch=1, schema_name=schema)
        async with conn.transaction():
            retry = await delete_memory(conn, "u1", target_ref="exercise", deletion_epoch=1, schema_name=schema)
        assert retry == first
        cursor = await conn.execute("SELECT count(*) FROM deletion_request WHERE user_id='u1'")
        assert await cursor.fetchone() == (1,)


async def test_stale_worker_epoch_is_rejected(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        await require_current_deletion_epoch(conn, "u1", 0, schema_name=schema)
        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="exercise", schema_name=schema)
        with pytest.raises(StaleDeletionEpoch, match="stale_deletion_epoch"):
            await require_current_deletion_epoch(conn, "u1", 0, schema_name=schema)


async def test_worker_refuses_job_with_stale_deletion_epoch(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="exercise", schema_name=schema)
        await enqueue(
            conn,
            NewJob(
                id="stale-epoch-job",
                user_id="u1",
                kind="extract",
                idempotency_key="stale-epoch-job",
                payload={"deletion_epoch": 0},
            ),
            schema_name=schema,
        )

        async def handler(_connection: AsyncConnection, _job, _tick) -> None:
            pytest.fail("stale work must not reach its handler")

        with pytest.raises(StaleDeletionEpoch, match="stale_deletion_epoch") as error:
            await process_one(conn, "u1", handler, schema_name=schema)
        assert error.value.epoch_start == 0
        assert error.value.current_epoch == 1
        cursor = await conn.execute("SELECT status, attempts, payload FROM job WHERE id='stale-epoch-job'")
        assert await cursor.fetchone() == ("queued", 0, {"deletion_epoch": 0})


async def test_deletion_epoch_is_monotonic_and_scope_all_clears_every_version(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        assert await deletion_epoch(conn, "u1", schema_name=schema) == 0
        async with conn.transaction():
            first = await delete_memory(conn, "u1", scope="memory", target_ref="exercise", schema_name=schema)
        async with conn.transaction():
            second = await delete_memory(conn, "u1", scope="all", schema_name=schema)
        assert (first.deletion_epoch, second.deletion_epoch) == (1, 2)
        assert second.deleted_versions >= 1
        assert await deletion_epoch(conn, "u1", schema_name=schema) == 2


async def test_deleted_content_is_not_recoverable_by_retrieval(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            await delete_memory(conn, "u1", scope="all", schema_name=schema)
        outcome = await rank_versions(
            conn, current_state("u1", END), query_text="fish", query_embedding=vector(1.0), schema_name=schema
        )
    assert outcome.ranked == ()
    assert outcome.candidates == 0


async def test_topic_scope_deletes_every_belief_about_a_subject(database) -> None:
    _, schema = database
    async with connect(database) as conn:
        await identity(conn, "u1")
        async with conn.transaction():
            # Two different memory_keys but one shared subject.
            await insert_version(
                conn, "topic-a", "the user is vegetarian", memory_key="diet", subject="target", predicate="topic_a"
            )
            await insert_version(
                conn,
                "topic-b",
                "the user avoids red meat",
                memory_key="other",
                subject="target",
                predicate="topic_b",
            )
            await insert_version(
                conn,
                "unrelated",
                "the user runs",
                memory_key="exercise",
                subject="elsewhere",
                predicate="topic_c",
            )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="topic", target_ref="target", schema_name=schema)
        assert receipt.deleted_versions == 2
    async with connect(database) as conn:
        await identity(conn, "u1")
        cursor = await conn.execute(
            "SELECT id FROM memory_version WHERE status = 'active' "
            "AND id IN ('topic-a', 'topic-b', 'unrelated') ORDER BY id"
        )
        # Both subject-matched versions are gone; the unrelated subject survives.
        assert [row[0] for row in await cursor.fetchall()] == ["unrelated"]
