"""Opt-in real pinned CPU assets; synthetic software checks, never corpus inference."""

import importlib.util
import math
import os
import socket
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaluation.baselines.context import format_turn
from evaluation.baselines.contracts import QUERY_PREFIX
from evaluation.baselines.embedding import OnnxBgeEmbedder
from evaluation.baselines.factory import RuntimeOptions, baseline_adapter
from evaluation.baselines.runtime import MeteredServices
from evaluation.baselines.tests.helpers import config, settings, turn
from evaluation.baselines.tokenization import O200K_SHA256, TiktokenTokenizer
from evaluation.harness.models import QuestionRecord, Usage


@pytest.fixture
def frozen_runtime(monkeypatch: pytest.MonkeyPatch):
    raw_directory = os.environ.get("CME_BASELINE_TEST_ASSETS")
    if raw_directory is None:
        pytest.skip("CME_BASELINE_TEST_ASSETS must explicitly name the prepared immutable assets directory")
    directory = Path(raw_directory)
    if not directory.is_absolute():
        pytest.fail("CME_BASELINE_TEST_ASSETS must be an explicit absolute directory")
    for package in ("onnxruntime", "tokenizers", "tiktoken"):
        if importlib.util.find_spec(package) is None:
            pytest.skip(f"optional baselines dependency {package} is absent")

    def denied(*args, **kwargs):
        pytest.fail("a local frozen-assets test attempted network access")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    return directory, TiktokenTokenizer(directory / "o200k_base.tiktoken"), OnnxBgeEmbedder(directory)


def _unit_vectors(vectors: Sequence[Sequence[float]]) -> None:
    assert vectors
    for vector in vectors:
        assert len(vector) == 384 and all(math.isfinite(value) for value in vector)
        assert math.fsum(value * value for value in vector) == pytest.approx(1.0, abs=1e-10)


def test_real_frozen_tokenizer_cls_normalization_query_prefix_and_usage(frozen_runtime) -> None:
    directory, tokenizer, embedder = frozen_runtime
    # A separate tokenizer call independently counts original IDs, including
    # CLS/SEP; no fixture replaces the production embedder/model/tokenizer.
    tokenizers = pytest.importorskip("tokenizers")
    oracle = tokenizers.Tokenizer.from_file(str(directory / "tokenizer.json"))
    oracle.no_truncation()
    oracle.no_padding()
    text = "My dog's name is Nimbus."
    raw_text = "café 🌍 العربية <|endoftext|>\n"
    assert tokenizer.identity == f"tiktoken-0.14.0:o200k_base:{O200K_SHA256}"
    assert tokenizer.decode(tokenizer.encode(raw_text)) == raw_text
    memory = embedder.embed((text,))
    query = embedder.embed((text,), query=True)
    _unit_vectors(memory.vectors)
    _unit_vectors(query.vectors)
    expected_memory = len(oracle.encode(text, add_special_tokens=True).ids)
    expected_query = len(oracle.encode(QUERY_PREFIX + text, add_special_tokens=True).ids)
    assert expected_query > expected_memory
    assert memory.usage.embedding_tokens == expected_memory
    assert query.usage.embedding_tokens == expected_query
    assert memory.usage.tokens == query.usage.tokens == 0
    assert memory.usage.cost_usd == query.usage.cost_usd == 0
    difference = math.sqrt(math.fsum((a - b) ** 2 for a, b in zip(memory.vectors[0], query.vectors[0], strict=True)))
    assert difference > 1e-5


def test_real_frozen_overflow_preserves_all_text_and_accounts_every_dispatched_id(frozen_runtime) -> None:
    directory, _, embedder = frozen_runtime
    tokenizers = pytest.importorskip("tokenizers")
    oracle = tokenizers.Tokenizer.from_file(str(directory / "tokenizer.json"))
    oracle.no_truncation()
    oracle.no_padding()
    text = " \n" + "alpha βeta\t" * 600 + " TAIL_SOURCE_PAYLOAD نهاية \n"
    chunks = embedder.chunks(text)
    assert len(chunks) > 1 and "".join(chunks) == text
    counts = [len(oracle.encode(chunk, add_special_tokens=True).ids) for chunk in chunks]
    assert all(2 <= count <= 512 for count in counts)
    expected = sum(counts)
    assert expected > 512 and embedder.token_count((text,)) == expected
    embedded = embedder.embed((text,))
    _unit_vectors(embedded.vectors)
    assert embedded.usage.embedding_tokens == expected
    assert embedded.usage.tokens == 0 and embedded.usage.cost_usd == 0


class NoAnswerTransport:
    def request(self, payload: dict[str, object]) -> dict[str, object]:
        pytest.fail("B2 ingestion/retrieval must not call an answering/extraction provider")


def test_real_frozen_b2_raw_index_and_semantic_retrieval_accounted_without_answering(frozen_runtime) -> None:
    directory, tokenizer, embedder = frozen_runtime
    selected = settings(tokenizer_id=tokenizer.identity, retrieval_k=2)
    services = MeteredServices(selected, tokenizer, embedder, NoAnswerTransport())
    configured = config("B2", selected, memory_budget=2048)
    clock = datetime(2020, 1, 2, tzinfo=UTC)
    documents = (
        turn("dog", "My dog's name is Nimbus."),
        turn("programming", "I usually write web applications in Rust.", role="assistant"),
    )
    with baseline_adapter(
        configured, selected, RuntimeOptions(assets_directory=directory), services=services
    ) as adapter:
        adapter.reset_user("user")
        adapter.set_clock(clock)
        for document in documents:
            expected = sum(embedder.token_count((chunk,)) for chunk in embedder.chunks(format_turn(document)))
            receipt = adapter.ingest_turn(document)
            assert receipt.usage.embedding_tokens == expected
            assert receipt.usage.tokens == 0 and receipt.usage.cost_usd == 0
        question = QuestionRecord(question_id="q", user_id="user", question="What is my dog's name?")
        adapter.prepare_question(question)
        retrieved = adapter.retrieve("user", question.question, "current", clock, configured.B_mem)
        assert [item.reference_id for item in retrieved.items] == ["dog", "programming"]
        assert retrieved.items[0].evidence_turn_ids == ("dog",)
        assert "Nimbus" in retrieved.rendered_context and "assistant" in retrieved.rendered_context
        assert retrieved.context_tokens == len(tokenizer.encode(retrieved.rendered_context)) <= configured.B_mem
        assert retrieved.usage.embedding_tokens == embedder.token_count((question.question,), query=True)
        assert retrieved.usage.tokens == 0 and retrieved.usage.cost_usd == 0
        assert adapter.drain_queue("user").usage == Usage.zero()
        metrics = services.operation_metrics()
        assert metrics["llm_attempts"] == 0
