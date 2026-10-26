"""Deterministic model-service test boundary; baselines under test remain real."""

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from evaluation.baselines.contracts import (
    ADAPTER_VERSION,
    BaselineSettings,
    Completion,
    Embeddings,
    Message,
    Tokenizer,
)
from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.config import HarnessConfig, OperationLimits
from evaluation.harness.models import ContextPolicy, TurnRecord, Usage


class CharacterTokenizer:
    identity = "fixture-codepoints-v1"

    def encode(self, text: str) -> list[int]:
        return [ord(char) for char in text]

    def decode(self, tokens: Sequence[int]) -> str:
        return "".join(chr(token) for token in tokens)

    def count_messages(self, messages: Sequence[Message]) -> int:
        return sum(len(message.content) + 4 for message in messages) + 2


def settings(**changes: object) -> BaselineSettings:
    value = {
        "tokenizer_id": CharacterTokenizer.identity,
        "answer_prompt": "Answer using the supplied context.",
        "context_window_tokens": 10000,
        "retrieval_k": 5,
        "dense_candidates": 20,
        "lexical_candidates": 20,
        "input_usd_per_million": Decimal("0.40"),
        "output_usd_per_million": Decimal("1.60"),
        "max_llm_calls_per_operation": 10,
        "max_embedding_calls_per_operation": 10,
        "provider_attempts": 1,
        "provider_timeout_seconds": 5.0,
        "vendor_timeout_seconds": 10.0,
        **changes,
    }
    return BaselineSettings.model_validate(value)


def config(system_id: str, selected: BaselineSettings, *, memory_budget: int = 2000) -> HarnessConfig:
    maximum = Usage(input_tokens=50000, output_tokens=5000, embedding_tokens=50000, cost_usd=Decimal("0.5"))
    policy: ContextPolicy = (
        "current_session" if system_id == "B0" else ("full_history" if system_id == "B1" else "retrieved_memory")
    )
    return HarnessConfig(
        config_id=f"fixture-{system_id}",
        system_id=system_id,
        adapter_version=ADAPTER_VERSION,
        model_id=selected.answer_model,
        embedding_model_id=f"{selected.embedding_model}@{selected.embedding_revision}",
        judge_model_id="fixture-no-judge",
        temperature=0.0,
        seed=42,
        B_mem=memory_budget,
        context_policy=policy,
        context_window_tokens=selected.context_window_tokens,
        answer_output_tokens=selected.max_output_tokens,
        adapter_configuration_sha256=selected.sha256(),
        prompt_sha256=hashlib.sha256(selected.answer_prompt.encode()).hexdigest(),
        tokenizer_id=selected.tokenizer_id,
        budget_id="fixture-baselines",
        token_cap=1000000,
        cost_cap_usd=Decimal("2"),
        dev_cost_cap_usd=Decimal("1"),
        operation_limits=OperationLimits(
            ingest_turn=maximum, drain_queue=maximum, retrieve=maximum, answer=maximum, delete=maximum
        ),
    )


def vector(x: float, y: float = 0.0) -> tuple[float, ...]:
    return (float(x), float(y), *(0.0 for _ in range(382)))


@dataclass
class Scope:
    usage: Usage = Usage.zero()


class FakeServices:
    """Fake only paid inference/tokenization; assert every call has a scope."""

    def __init__(self, selected: BaselineSettings) -> None:
        self.settings = selected
        self.tokenizer: Tokenizer = CharacterTokenizer()
        self.current: Scope | None = None
        self.requests: list[tuple[Message, ...]] = []
        self.embedding_requests: list[tuple[tuple[str, ...], bool]] = []
        self.vector_for_text = lambda text, query: vector(1.0)
        self.chunk_size = 500
        self.answer_text = "fixture answer"
        self.fail_after_completion = False
        self.completion_usage = Usage(input_tokens=11, output_tokens=3, embedding_tokens=0, cost_usd=Decimal("0.001"))

    @contextmanager
    def scope(self, maximum: Usage) -> Iterator[Scope]:
        assert self.current is None, "nested/unbounded model scope"
        active = Scope()
        self.current = active
        try:
            yield active
        finally:
            self.current = None

    def chunks(self, text: str) -> tuple[str, ...]:
        assert self.current is not None, "tokenizer chunking must be accounted"
        return tuple(text[index : index + self.chunk_size] for index in range(0, len(text), self.chunk_size)) or ("",)

    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings:
        assert self.current is not None, "embedding outside an operation scope"
        frozen = tuple(texts)
        self.embedding_requests.append((frozen, query))
        usage = Usage(input_tokens=0, output_tokens=0, embedding_tokens=sum(map(len, frozen)), cost_usd=Decimal(0))
        self.current.usage = self.current.usage.plus(usage)
        return Embeddings(vectors=tuple(self.vector_for_text(text, query) for text in frozen), usage=usage)

    def complete(
        self, messages: Sequence[Message], *, schema: dict[str, object] | None = None, json_object: bool = False
    ) -> Completion:
        assert self.current is not None, "completion outside an operation scope"
        self.requests.append(tuple(messages))
        self.current.usage = self.current.usage.plus(self.completion_usage)
        if self.fail_after_completion:
            raise AccountedOperationError("private provider message", self.current.usage)
        return Completion(text=self.answer_text, usage=self.completion_usage)


def turn(
    turn_id: str,
    content: str,
    *,
    user: str = "user",
    session: str = "old-session",
    role: str = "user",
    speaker: str | None = None,
) -> TurnRecord:
    return TurnRecord.model_validate(
        {
            "user_id": user,
            "session_id": session,
            "turn_id": turn_id,
            "role": role,
            "content": content,
            "occurred_at": datetime(2020, 1, 1, tzinfo=UTC),
            "source_speaker": speaker,
        }
    )
