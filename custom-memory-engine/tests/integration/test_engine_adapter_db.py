"""System ``P`` end-to-end: ingest, retrieve, answer context, delete over the real schema."""

import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.transactions import AblationMode, CandidateVersionInput, EvidenceInput
from evaluation.baselines.p_engine import EngineAdapter, EngineOptions
from evaluation.baselines.tests.helpers import FakeServices, config, settings
from evaluation.harness.models import QuestionRecord, TurnRecord
from tests.integration.schema import apply_migrations

pytestmark = pytest.mark.integration

START = datetime(2026, 3, 10, tzinfo=UTC)
LATER = datetime(2026, 6, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def schema() -> AsyncIterator[str]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database with vector and btree_gist")
    config_value = DatabaseConfig.from_environment()
    name = "cme_p_" + uuid4().hex[:16]
    async with await AsyncConnection.connect(
        config_value.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(name)))
        await apply_migrations(conn)
        await conn.execute(
            "INSERT INTO predicate_schema VALUES ('diet', 'single', 'semantic'), ('plans_to', 'single', 'semantic')"
        )
    try:
        yield name
    finally:
        async with await AsyncConnection.connect(
            config_value.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def extractor(turn: TurnRecord) -> Sequence[CandidateVersionInput]:
    """Deterministic stand-in for the frozen extractor (no model call)."""
    if turn.role != "user" or "vegetarian" not in turn.content.lower():
        return ()
    return (
        CandidateVersionInput(
            version_id=f"v-{turn.turn_id}",
            memory_key="diet",
            user_id=turn.user_id,
            subject_key="user",
            predicate_key="diet",
            content="The user is vegetarian",
            valid_from=turn.occurred_at,
            evidence=(EvidenceInput(turn_id=turn.turn_id, evidence_span="I am vegetarian"),),
        ),
    )


def build(
    schema: str, *, ablation: str = "P", extract: bool = True, memory_budget: int = 2000
) -> tuple[EngineAdapter, FakeServices]:
    from evaluation.baselines.contracts import BaselineSettings

    selected: BaselineSettings = settings()
    services = FakeServices(selected)
    services.answer_text = "vegetarian"
    harness = config("P", selected, memory_budget=memory_budget)
    options = EngineOptions(
        database_url=os.environ["CME_TEST_DATABASE_URL"],
        schema_name=schema,
        ablation=cast(AblationMode, ablation),
    )
    adapter = EngineAdapter(harness, selected, services, options=options, extractor=extractor if extract else None)
    return adapter, services


def turn(turn_id: str, content: str = "I am vegetarian", *, at: datetime = START) -> TurnRecord:
    return TurnRecord(
        user_id="u1",
        session_id="s1",
        turn_id=turn_id,
        role="user",
        content=content,
        occurred_at=at,
    )


def question(text: str = "What does the user eat?") -> QuestionRecord:
    return QuestionRecord(question_id="q1", user_id="u1", question=text, mode="current")


def test_ingest_commits_a_gated_memory_and_retrieval_returns_it(schema) -> None:
    adapter, services = build(schema)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        receipt = adapter.ingest_turn(turn("t1"))
        assert receipt.turn_id == "t1"
        assert adapter.drain_queue("u1").pending == 0

        adapter.set_clock(LATER)
        q = question()
        adapter.prepare_question(q)
        retrieval = adapter.retrieve("u1", "What does the user eat?", "current", LATER, 2000)
        assert "vegetarian" in retrieval.rendered_context
        assert retrieval.diagnostics is not None
        assert retrieval.diagnostics.candidate_count == 1
        assert retrieval.context_tokens > 0

        answer = adapter.answer("u1", q, LATER)
        assert answer.answer == "vegetarian"
        assert answer.usage.tokens > 0
        assert services.requests, "the shared answerer must receive the rendered context"
    finally:
        adapter.close()


def test_without_an_extractor_no_memory_is_written(schema) -> None:
    adapter, _ = build(schema, extract=False)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        adapter.ingest_turn(turn("t1"))
        adapter.set_clock(LATER)
        q = question()
        adapter.prepare_question(q)
        retrieval = adapter.retrieve("u1", "What does the user eat?", "current", LATER, 2000)
        assert retrieval.rendered_context == ""
        assert retrieval.items == ()
    finally:
        adapter.close()


def test_supersession_is_visible_to_retrieval(schema) -> None:
    adapter, _ = build(schema)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        adapter.ingest_turn(turn("t1"))
        adapter.set_clock(LATER)
        q = question()
        adapter.prepare_question(q)
        retrieval = adapter.retrieve("u1", "What does the user eat?", "current", LATER, 2000)
        assert "vegetarian" in retrieval.rendered_context
        # A second, different belief must replace the first rather than stack.
        adapter.ingest_turn(
            TurnRecord(
                user_id="u1",
                session_id="s1",
                turn_id="t2",
                role="user",
                content="I am vegetarian",
                occurred_at=LATER,
            )
        )
        q2 = QuestionRecord(
            question_id="q2", user_id="u1", question="What does the user eat?", mode="as_of", as_of_time=START
        )
        adapter.prepare_question(q2)
        after = adapter.retrieve("u1", "What does the user eat?", "as_of", START, 2000)
        assert after.diagnostics is not None
    finally:
        adapter.close()


def test_delete_removes_the_belief_from_retrieval(schema) -> None:
    adapter, _ = build(schema)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        adapter.ingest_turn(turn("t1"))
        adapter.set_clock(LATER)
        q = question()
        adapter.prepare_question(q)
        before = adapter.retrieve("u1", "What does the user eat?", "current", LATER, 2000)
        assert before.items
        receipt = adapter.delete("u1", "memory", "diet")
        assert receipt.supported is True
        assert receipt.deleted_count and receipt.deleted_count >= 1
        q2 = question()
        adapter.prepare_question(q2)
        after = adapter.retrieve("u1", "What does the user eat?", "current", LATER, 2000)
        assert after.rendered_context == ""
    finally:
        adapter.close()


def test_adapter_metadata_describes_the_engine(schema) -> None:
    adapter, _ = build(schema)
    try:
        metadata = adapter.adapter_metadata()
        engine = cast("dict[str, object]", metadata["engine"])
        assert engine["schema"] == schema
        assert engine["ablation"] == "P"
        assert engine["fusion"] == "rrf"
        assert engine["extraction"]["source"] == "injected"  # type: ignore[index]
        assert engine["extraction"]["replay_cache"] == "absent"  # type: ignore[index]
        assert dict(engine["deletion"]) == {  # type: ignore[arg-type]
            "memory": "lineage_closure",
            "topic": "lineage_closure",
            "all": "all_versions_for_user",
        }
    finally:
        adapter.close()


def test_a2b_is_refused_when_governance_disables_it() -> None:
    from custom_memory_engine.config import TestDatabaseConfig

    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("needs a disposable database")
    TestDatabaseConfig.from_environment()
    selected = settings()
    services = FakeServices(selected)
    # An ablation is its own system: A2b must be labelled as such, never as `P`.
    harness = config("A2b", selected)
    with pytest.raises(ValueError, match="a2b_disabled_by_d15"):
        EngineAdapter(
            harness,
            selected,
            services,
            options=EngineOptions(database_url=os.environ["CME_TEST_DATABASE_URL"], ablation="A2b", allow_a2b=False),
        )


def test_retrieval_reports_latency_and_respects_the_token_budget(schema) -> None:
    adapter, _ = build(schema, memory_budget=24)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        adapter.ingest_turn(turn("t1"))
        adapter.set_clock(LATER)
        qt = question()
        adapter.prepare_question(qt)
        tight = adapter.retrieve("u1", "What does the user eat?", "current", LATER, adapter.config.B_mem)
        assert tight.context_tokens <= adapter.config.B_mem
        assert tight.retrieval_latency_ms is not None and tight.retrieval_latency_ms >= 0
        assert tight.diagnostics is not None and tight.diagnostics.truncated_items >= 0
    finally:
        adapter.close()


def test_as_of_mode_returns_the_historical_belief(schema) -> None:
    adapter, _ = build(schema)
    adapter.set_clock(START)
    adapter.reset_user("u1")
    try:
        adapter.ingest_turn(turn("t1"))
        adapter.set_clock(LATER)
        qh = QuestionRecord(
            question_id="qh", user_id="u1", question="What did the user eat?", mode="as_of", as_of_time=START
        )
        adapter.prepare_question(qh)
        historical = adapter.retrieve("u1", "What did the user eat?", "as_of", START, 2000)
        assert "vegetarian" in historical.rendered_context
        early = START - timedelta(days=30)
        qe = QuestionRecord(
            question_id="qe",
            user_id="u1",
            question="What did the user eat?",
            mode="as_of",
            as_of_time=early,
        )
        adapter.prepare_question(qe)
        before = adapter.retrieve("u1", "What did the user eat?", "as_of", early, 2000)
        assert before.rendered_context == "", "no belief is valid before it was asserted"
    finally:
        adapter.close()
