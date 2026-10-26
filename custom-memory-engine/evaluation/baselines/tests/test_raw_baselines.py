"""Raw baseline behavior with real adapters and fake model services only."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from evaluation.baselines.b0_session import B0SessionAdapter
from evaluation.baselines.b1_full_history import B1FullHistoryAdapter
from evaluation.baselines.b2_dense import B2DenseAdapter
from evaluation.baselines.b2h_hybrid import B2hHybridAdapter, reciprocal_rank_fusion
from evaluation.baselines.context import answer_messages, fit_ranked_items, format_turn, history_items
from evaluation.baselines.contracts import Embeddings
from evaluation.baselines.runtime import MeteredServices
from evaluation.baselines.stores import MemoryRawStore
from evaluation.baselines.tests.helpers import CharacterTokenizer, FakeServices, config, settings, turn, vector
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.models import QuestionRecord, RetrievedItem, TurnRecord, Usage

CLOCK = datetime(2020, 1, 2, tzinfo=UTC)


def initialize(adapter, *turns: TurnRecord) -> None:
    for user in {item.user_id for item in turns} or {"user"}:
        adapter.reset_user(user)
    adapter.set_clock(CLOCK)
    for item in turns:
        adapter.ingest_turn(item)


def question(adapter, *, user="user", session=None, text="What is known?", mode="current", target=None):
    value = QuestionRecord.model_validate(
        {
            "question_id": "question",
            "user_id": user,
            "question": text,
            "session_id": session,
            "mode": mode,
            "as_of_time": target,
        }
    )
    adapter.prepare_question(value)
    result = adapter.retrieve(user, text, mode, target or CLOCK, adapter.config.B_mem)
    return value, result


@pytest.mark.parametrize("session,expected", [(None, []), ("unknown", []), ("old", ["old"]), ("new", ["new"])])
def test_b0_uses_only_explicit_question_session(session, expected) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B0SessionAdapter(config("B0", selected, memory_budget=1), selected, services)
    initialize(adapter, turn("old", "old fact", session="old"), turn("new", "new fact", session="new"))
    probe, retrieval = question(adapter, session=session)
    assert [item.reference_id for item in retrieval.items] == expected
    assert retrieval.context_tokens == len(services.tokenizer.encode(retrieval.rendered_context))
    adapter.answer("user", probe, CLOCK)
    assert services.requests[-1] == answer_messages(selected, probe, retrieval.rendered_context, CLOCK)
    assert not services.embedding_requests


def test_b1_full_history_exceeds_bmem_and_preserves_speakers_roles_order() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected, memory_budget=1), selected, services)
    initialize(
        adapter,
        turn("old", "FIRST", role="assistant"),
        turn("new", "SECOND", session="new", speaker="Bob"),
    )
    probe, result = question(adapter)
    assert [item.reference_id for item in result.items] == ["old", "new"]
    assert result.context_tokens > 1
    assert "assistant" in result.rendered_context and "Bob" in result.rendered_context
    assert result.rendered_context.index("FIRST") < result.rendered_context.index("SECOND")
    adapter.answer("user", probe, CLOCK)
    assert result.diagnostics is not None and result.diagnostics.truncated_items == 0


def test_b1_history_truncation_uses_bounded_tokenizer_work() -> None:
    class CountingTokenizer(CharacterTokenizer):
        def __init__(self) -> None:
            self.count = 0

        def count_messages(self, messages):
            self.count += 1
            return super().count_messages(messages)

    tokenizer = CountingTokenizer()
    selected = settings(context_window_tokens=3200)
    turns = tuple(turn(str(index), "x" * 100) for index in range(2000))
    probe = QuestionRecord(question_id="q", user_id="user", question="Q")
    items, _ = history_items(turns, probe, selected, tokenizer, CLOCK)
    assert items and len(items) < len(turns)
    assert tokenizer.count < 40


def test_b1_drops_oldest_whole_turns_at_actual_prompt_window() -> None:
    selected = settings(context_window_tokens=3200)
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected, memory_budget=1), selected, services)
    initialize(adapter, turn("a", "A" * 450), turn("b", "B" * 450), turn("c", "C" * 100))
    probe, result = question(adapter)
    assert [item.reference_id for item in result.items] == ["b", "c"]
    assert result.diagnostics is not None
    assert result.diagnostics.source_items == 3 and result.diagnostics.truncated_items == 1
    assert result.diagnostics.truncated_tokens > 450
    assert (
        services.tokenizer.count_messages(answer_messages(selected, probe, result.rendered_context, CLOCK)) + 2048 + 64
        <= 3200
    )
    events = adapter.drain_adapter_events()
    assert events[-1]["event"] == "history_truncated" and events[-1]["truncated_items"] == 1
    assert events[-1]["question_id"] == "question"
    question(adapter)
    assert adapter.adapter_metadata()["history_truncation"] == {
        "question_cases": 1,
        "truncated_question_cases": 1,
        "truncated_items": 1,
        "truncated_tokens": result.diagnostics.truncated_tokens,
    }


def test_unfit_question_fails_before_provider_call() -> None:
    selected = settings(context_window_tokens=2100)
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter)
    with pytest.raises(AccountedOperationError, match="baseline_operation_failed") as error:
        question(adapter, text="Q" * 100)
    assert error.value.usage == Usage.zero() and not services.requests


class UnusedEmbedder:
    def chunks(self, text: str) -> tuple[str, ...]:
        pytest.fail("history baseline attempted embedding tokenization")

    def token_count(self, texts, *, query=False) -> int:
        pytest.fail("history baseline attempted embedding tokenization")

    def embed(self, texts, *, query=False) -> Embeddings:
        pytest.fail("history baseline attempted model inference")


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def request(self, payload: dict[str, object]) -> dict[str, object]:
        self.requests.append(payload)
        return {
            "model": "gpt-4.1-mini-2025-04-14",
            "choices": [{"message": {"content": "fixture"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


@pytest.mark.parametrize("one_over", [False, True])
@pytest.mark.parametrize("historical", [False, True])
def test_history_fitting_and_real_metered_dispatch_share_framing_reserve(one_over: bool, historical: bool) -> None:
    tokenizer = CharacterTokenizer()
    initial = settings()
    document = turn("one", "A" * 489)
    target = datetime(2019, 12, 1, tzinfo=UTC) if historical else None
    probe = QuestionRecord(
        question_id="question",
        user_id="user",
        question="What is known?",
        mode="as_of" if historical else "current",
        as_of_time=target,
    )
    exact_window = (
        tokenizer.count_messages(answer_messages(initial, probe, format_turn(document), CLOCK))
        + initial.max_output_tokens
        + initial.request_overhead_tokens
    )
    selected = settings(context_window_tokens=exact_window - int(one_over))
    transport = RecordingTransport()
    services = MeteredServices(selected, tokenizer, UnusedEmbedder(), transport)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, document)
    prepared, retrieval = question(adapter, mode=probe.mode, target=target)
    assert [item.reference_id for item in retrieval.items] == ([] if one_over else ["one"])
    assert adapter.answer("user", prepared, CLOCK).answer == "fixture"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "adapter_type,system", [(B0SessionAdapter, "B0"), (B1FullHistoryAdapter, "B1"), (B2DenseAdapter, "B2")]
)
def test_answer_uses_preceding_exact_context_without_second_retrieval(adapter_type, system) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = adapter_type(config(system, selected), selected, services)
    initialize(adapter, turn("one", "fixture data"))
    probe, retrieval = question(adapter, session="old-session")
    embedding_calls = len(services.embedding_requests)
    response = adapter.answer("user", probe, CLOCK)
    assert response.answer == "fixture answer" and response.usage.input_tokens == 11
    assert len(services.embedding_requests) == embedding_calls
    assert services.requests[-1] == answer_messages(selected, probe, retrieval.rendered_context, CLOCK)
    with pytest.raises(ValueError, match="prepared"):
        adapter.answer("user", probe.model_copy(update={"question": "different"}), CLOCK)


@pytest.mark.parametrize("mutation", ["ingest", "clock", "reset", "delete", "prepare"])
def test_prepared_context_is_invalidated_by_mutation(mutation) -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, turn("one", "first"))
    probe, _ = question(adapter)
    if mutation == "ingest":
        adapter.ingest_turn(turn("two", "second"))
    elif mutation == "clock":
        adapter.set_clock(CLOCK + timedelta(seconds=1))
    elif mutation == "reset":
        adapter.reset_user("user")
    elif mutation == "delete":
        adapter.delete("user", "memory", "one")
    else:
        adapter.prepare_question(probe.model_copy(update={"question_id": "another"}))
    with pytest.raises(ValueError, match="prepared"):
        adapter.answer("user", probe, CLOCK)


def test_reset_retires_old_dense_generation_without_touching_foreign_tenant() -> None:
    selected = settings()
    services = FakeServices(selected)
    store = MemoryRawStore()
    adapter = B2DenseAdapter(config("B2", selected), selected, services, store)
    initialize(adapter, turn("a", "alice", user="a"), turn("b", "bob", user="b"))
    old_generation = adapter._state("a").generation
    adapter.reset_user("a")
    initialize(adapter, turn("new", "new alice", user="a"))
    assert (old_generation, "a") not in store._items
    assert (adapter._state("a").generation, "a") in store._items
    assert (adapter._state("b").generation, "b") in store._items
    adapter.close()
    assert store._items == {}


def test_reset_and_local_deletion_remain_tenant_scoped() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, turn("a", "alice", user="a"), turn("b", "bob", user="b"))
    adapter.reset_user("a")
    _, a = question(adapter, user="a")
    _, b = question(adapter, user="b")
    assert not a.items and [item.reference_id for item in b.items] == ["b"]
    assert adapter.delete("b", "topic", "bob").supported is False
    assert adapter.delete("b", "memory", "b").deleted_count == 1
    _, empty = question(adapter, user="b")
    assert not empty.items


def test_dense_cosine_uses_normalized_vectors_and_no_time_gate_or_extraction() -> None:
    selected = settings(retrieval_k=3)
    services = FakeServices(selected)
    services.vector_for_text = lambda text, query: (
        vector(1, 0) if query or '"turn_id":"aligned"' in text else vector(100, 100)
    )
    adapter = B2DenseAdapter(config("B2", selected), selected, services)
    initialize(adapter, turn("large", "distractor"), turn("aligned", "aligned old fact"))
    _, result = question(adapter, mode="as_of", target=CLOCK - timedelta(days=10))
    assert [item.reference_id for item in result.items] == ["aligned", "large"]
    assert all(item.evidence_turn_ids == (item.reference_id,) for item in result.items)
    assert not services.requests
    assert services.embedding_requests[-1][1] is True


def test_dense_preserves_overflow_chunks_and_deduplicates_source_turns() -> None:
    selected = settings(retrieval_k=2)
    services = FakeServices(selected)
    services.chunk_size = 100
    adapter = B2DenseAdapter(config("B2", selected), selected, services)
    initialize(adapter, turn("long", "L" * 300 + "TAIL_EVIDENCE"), turn("short", "other"))
    assert any("TAIL_EVIDENCE" in text for texts, _ in services.embedding_requests for text in texts)
    _, result = question(adapter)
    assert [item.reference_id for item in result.items] == ["long", "short"]
    assert "TAIL_EVIDENCE" in result.rendered_context
    assert result.diagnostics is not None and result.diagnostics.candidate_count > 2


def test_dense_cosine_ties_break_on_original_turn_id_and_embedding_usage_is_returned() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B2DenseAdapter(config("B2", selected), selected, services)
    initialize(adapter)
    services.vector_for_text = lambda text, query: vector(1000 if not query else 1)
    receipt = adapter.ingest_turn(turn("z", "FIRST"))
    adapter.ingest_turn(turn("a", "SECOND", role="assistant", speaker="Another human"))
    assert receipt.usage.embedding_tokens > len("FIRST") and receipt.usage.input_tokens == 0
    _, result = question(adapter)
    assert [item.reference_id for item in result.items] == ["a", "z"]
    assert result.usage.embedding_tokens == len("What is known?")
    assert any(
        '"role":"assistant"' in text for texts, query in services.embedding_requests if not query for text in texts
    )
    assert "Another human" in result.rendered_context


def test_known_empty_dense_retrieval_has_true_zero_evidence_capability() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B2DenseAdapter(config("B2", selected), selected, services)
    initialize(adapter)
    _, result = question(adapter)
    assert result.supported is True and result.items == () and result.unavailable_metrics == ()
    assert result.usage == Usage.zero() and not services.embedding_requests


def test_b2h_rejects_a_memory_store_before_any_model_work() -> None:
    selected = settings()
    services = FakeServices(selected)
    with pytest.raises(ValueError, match="b2h_requires_postgresql_fts_store"):
        B2hHybridAdapter(config("B2h", selected), selected, services, MemoryRawStore())  # type: ignore[arg-type]
    assert services.current is None and not services.embedding_requests


def test_constructors_validate_exact_class_identity_and_services_pins() -> None:
    selected = settings()
    services = FakeServices(selected)
    with pytest.raises(ValueError, match="b0_system_identity_required"):
        B0SessionAdapter(config("B1", selected), selected, services)
    with pytest.raises(ValueError, match="dense_baseline_system_identity_required"):
        B2DenseAdapter(config("B2h", selected), selected, services)
    wrong_services = FakeServices(settings(retrieval_k=3))
    with pytest.raises(ValueError, match="baseline_model_services_configuration_mismatch"):
        B2DenseAdapter(config("B2", selected), selected, wrong_services)
    assert not services.embedding_requests and not services.requests


@pytest.mark.parametrize("bad", [(0.0,) * 384, (float("inf"),) * 384, (1.0,) * 383])
def test_dense_rejects_invalid_embedding_without_publishing_turn(bad) -> None:
    selected = settings()
    services = FakeServices(selected)
    services.vector_for_text = lambda text, query: bad
    adapter = B2DenseAdapter(config("B2", selected), selected, services)
    initialize(adapter)
    with pytest.raises(AccountedOperationError):
        adapter.ingest_turn(turn("bad", "bad vector"))
    services.vector_for_text = lambda text, query: vector(1)
    _, result = question(adapter)
    assert not result.items


def test_retrieval_truncates_in_rank_order_and_has_no_phantom_evidence() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B2DenseAdapter(config("B2", selected, memory_budget=160), selected, services)
    initialize(adapter, turn("a", "A" * 400), turn("b", "B" * 400))
    _, result = question(adapter)
    assert result.context_tokens <= 160
    assert [item.reference_id for item in result.items] == ["a"]
    assert result.rendered_context == result.items[0].content
    assert "b" not in result.items[0].evidence_turn_ids


def test_dense_header_only_partial_does_not_claim_source_evidence() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B2DenseAdapter(config("B2", selected, memory_budget=20), selected, services)
    initialize(adapter, turn("gold-turn", "ANSWER_PAYLOAD"))
    _, result = question(adapter)
    assert result.items == () and result.rendered_context == "" and result.context_tokens == 0
    assert result.diagnostics is not None and result.diagnostics.truncated_items == 1


def test_packing_never_inserts_replacement_text_for_a_partial_unicode_code_point() -> None:
    class ByteTokenizer(CharacterTokenizer):
        def encode(self, text: str) -> list[int]:
            return list(text.encode())

        def decode(self, tokens) -> str:
            return bytes(tokens).decode(errors="replace")

    item = RetrievedItem(reference_id="unicode", content="🌍X", evidence_turn_ids=("unicode",))
    assert fit_ranked_items((item,), ByteTokenizer(), 3)[0] == ()
    selected, _ = fit_ranked_items((item,), ByteTokenizer(), 4)
    assert selected[0].content == "🌍" and "\ufffd" not in selected[0].content


def test_rrf_uses_one_based_ranks_k60_missing_channel_and_deduplication() -> None:
    dense = ("dense", "both", "x", "lexical")
    lexical = ("lexical", "both", "x")
    ranking = reciprocal_rank_fusion(dense, lexical)
    assert ranking[0] == "both"
    assert ranking.index("lexical") < ranking.index("dense")
    assert reciprocal_rank_fusion(("a", "a", "b"), ("b",)) == ("b", "a")


def test_failed_answer_usage_is_accounted_without_private_message() -> None:
    selected = settings()
    services = FakeServices(selected)
    services.fail_after_completion = True
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, turn("one", "data"))
    probe, _ = question(adapter)
    with pytest.raises(AccountedOperationError, match="baseline_operation_failed") as error:
        adapter.answer("user", probe, CLOCK)
    assert error.value.usage.cost_usd == Decimal("0.001")
    assert "private" not in str(error.value)


def test_preparation_rejects_label_hint_and_naive_clock_before_model_work() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B0SessionAdapter(config("B0", selected), selected, services)
    initialize(adapter)
    with pytest.raises(ValueError):
        adapter.prepare_question(QuestionRecord(question_id="q", user_id="user", question="?", stratum="abstention"))
    with pytest.raises(ValueError):
        adapter.set_clock(datetime(2020, 1, 2))
    assert not services.requests and not services.embedding_requests


def test_relative_time_question_receives_distinct_actual_replay_reference_clocks() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, turn("purchase", "I bought a book today."))
    probe = QuestionRecord(question_id="elapsed", user_id="user", question="How many days ago did I buy the book?")
    references = (datetime(2020, 2, 1, tzinfo=UTC), datetime(2020, 3, 1, tzinfo=UTC))
    for reference in references:
        adapter.set_clock(reference)
        adapter.prepare_question(probe)
        adapter.retrieve("user", probe.question, "current", reference, adapter.config.B_mem)
        adapter.answer("user", probe, reference)
    assert services.requests[0] != services.requests[1], "the answering provider cannot see the changed replay clock"
    assert "2020-02-01T00:00:00+00:00" in services.requests[0][1].content
    assert "2020-03-01T00:00:00+00:00" in services.requests[1][1].content
    assert "Query mode: current" in services.requests[0][1].content


def test_historical_target_and_reference_clock_remain_distinct_in_the_answer_request() -> None:
    selected = settings()
    services = FakeServices(selected)
    adapter = B1FullHistoryAdapter(config("B1", selected), selected, services)
    initialize(adapter, turn("one", "fictional history"))
    target = datetime(2019, 12, 1, tzinfo=UTC)
    probe, _ = question(adapter, mode="as_of", target=target)
    adapter.answer("user", probe, CLOCK)
    payload = services.requests[0][1].content
    assert "Reference time (UTC): 2020-01-02T00:00:00+00:00" in payload
    assert "Target time (UTC): 2019-12-01T00:00:00+00:00" in payload
    assert "Query mode: as_of" in payload


@pytest.mark.parametrize("mode", ["current", "as_of", "what_changed", "before_change", "stable"])
def test_common_answer_metadata_exposes_mode_but_does_not_invent_an_absent_target(mode: str) -> None:
    selected = settings()
    probe = QuestionRecord.model_validate(
        {"question_id": "private-id", "user_id": "private-user", "question": "Raw question?", "mode": mode}
    )
    messages = answer_messages(selected, probe, "raw context", CLOCK)
    assert f"Query mode: {mode}" in messages[1].content
    assert "Target time" not in messages[1].content
    assert "raw context" in messages[1].content and messages[1].content.endswith("Question:\nRaw question?")
    assert "private-" not in messages[1].content


def test_common_answer_reference_is_utc_canonical_and_requires_aware_times() -> None:
    selected = settings()
    probe = QuestionRecord(question_id="q", user_id="user", question="Raw question?")
    offset = datetime(2020, 1, 2, 2, tzinfo=timezone(timedelta(hours=2)))
    assert answer_messages(selected, probe, "context", offset) == answer_messages(selected, probe, "context", CLOCK)
    with pytest.raises(ValueError, match="aware_answer_timestamp_required"):
        answer_messages(selected, probe, "context", datetime(2020, 1, 2))
    with pytest.raises(ValueError, match="aware_answer_timestamp_required"):
        answer_messages(selected, probe.model_copy(update={"as_of_time": datetime(2020, 1, 1)}), "context", CLOCK)
