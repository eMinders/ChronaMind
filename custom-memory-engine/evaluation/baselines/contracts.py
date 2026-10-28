"""Frozen operational settings and small real-provider boundaries shared by baselines."""

import hashlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field, model_validator

from evaluation.harness.config import HarnessConfig, canonical_json
from evaluation.harness.models import Record, Usage

ANSWER_MODEL = "gpt-4.1-mini-2025-04-14"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
ADAPTER_VERSION = "rl031-v1"
# ``P`` plus every engine ablation the adapter can actually configure. Ablations are
# separate systems (``D-ENG-01``: ``P − A2`` is a real paired contrast), so their ids must
# pass the same frozen-configuration validation as ``P``.
ENGINE_SYSTEM_IDS = frozenset({"P", "A1", "A2", "A2b"})
EVIDENCE_METRICS = (
    "evidence_recall_at_k",
    "diagnostic_evidence_recall_at_k",
    "diagnostic_all_evidence_found",
    "primary_all_evidence_found",
    "primary_evidence_recall_at_k",
    "primary_evidence_recall_at_k_excluding_date_anomalies",
)


class Message(Record):
    role: Literal["system", "user", "assistant"]
    content: str


class Completion(Record):
    text: str
    usage: Usage


class Embeddings(Record):
    vectors: tuple[tuple[float, ...], ...]
    usage: Usage


class BaselineSettings(Record):
    """No model/default fallback; all operational choices enter a frozen digest."""

    schema_version: Literal[1] = 1
    answer_model: Literal["gpt-4.1-mini-2025-04-14"] = ANSWER_MODEL
    embedding_model: Literal["BAAI/bge-small-en-v1.5"] = EMBEDDING_MODEL
    embedding_revision: Literal["5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"] = EMBEDDING_REVISION
    embedding_dimension: Literal[384] = 384
    embedding_max_tokens: Literal[512] = 512
    query_prefix: Literal["Represent this sentence for searching relevant passages: "] = QUERY_PREFIX
    tokenizer_id: str = Field(min_length=1)
    answer_prompt: str = Field(min_length=1)
    context_window_tokens: int = Field(gt=0, le=1_047_576)
    max_output_tokens: Literal[2048] = 2048
    request_overhead_tokens: Literal[64] = 64
    top_p: Literal[1] = 1
    seed: Literal[42] = 42
    send_seed: bool = False
    retrieval_k: int = Field(ge=1)
    dense_candidates: int = Field(ge=1)
    lexical_candidates: int = Field(ge=1)
    rrf_constant: Literal[60] = 60
    chunk_policy: Literal["bge-offsets-510-v1"] = "bge-offsets-510-v1"
    embedding_inference_policy: Literal["cls-l2;mean-chunk-vectors-l2;same-length-batches-16-v1"] = (
        "cls-l2;mean-chunk-vectors-l2;same-length-batches-16-v1"
    )
    structured_output_policy: Literal["strict-required-properties-v1"] = "strict-required-properties-v1"
    answer_format_policy: Literal["context-question-reference-utc-v2"] = "context-question-reference-utc-v2"
    history_truncation: Literal["drop-oldest-whole-turns-v1"] = "drop-oldest-whole-turns-v1"
    input_usd_per_million: Decimal = Field(ge=0)
    output_usd_per_million: Decimal = Field(ge=0)
    max_llm_calls_per_operation: int = Field(ge=1)
    max_embedding_calls_per_operation: int = Field(ge=1)
    provider_attempts: int = Field(ge=1, le=3)
    provider_timeout_seconds: float = Field(gt=0, le=120)
    vendor_timeout_seconds: float = Field(gt=0, le=600)
    mem0_version: Literal["2.2.1"] = "2.2.1"
    graphiti_version: Literal["0.31.2"] = "0.31.2"
    mem0_replay_policy: Literal["source-observation-recorded-current-utc-v1"] = (
        "source-observation-recorded-current-utc-v1"
    )
    graphiti_replay_policy: Literal["injected-utc-query-role-source-shims-v1"] = (
        "injected-utc-query-role-source-shims-v1"
    )

    @model_validator(mode="after")
    def validate_window(self) -> "BaselineSettings":
        if self.context_window_tokens <= self.max_output_tokens:
            raise ValueError("answer_context_window_too_small")
        if self.dense_candidates < self.retrieval_k or self.lexical_candidates < self.retrieval_k:
            raise ValueError("candidate_pools_smaller_than_retrieval_k")
        if not self.answer_prompt.strip() or not self.tokenizer_id.strip():
            raise ValueError("explicit_prompt_and_tokenizer_required")
        return self

    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json"))).hexdigest()

    def validate_harness(self, config: HarnessConfig) -> HarnessConfig:
        # model_copy(update=...) bypasses Pydantic validation; revalidate here.
        checked = HarnessConfig.model_validate(config.model_dump())
        if checked.system_id not in {"B0", "B1", "B2", "B2h", "B3", "B4"} | ENGINE_SYSTEM_IDS:
            raise ValueError("unknown_baseline_system")
        if (
            checked.adapter_version != ADAPTER_VERSION
            or checked.model_id != self.answer_model
            or checked.embedding_model_id != f"{self.embedding_model}@{self.embedding_revision}"
            or checked.tokenizer_id != self.tokenizer_id
            or checked.prompt_sha256 != hashlib.sha256(self.answer_prompt.encode()).hexdigest()
            or checked.adapter_configuration_sha256 != self.sha256()
            or checked.context_window_tokens != self.context_window_tokens
            or checked.answer_output_tokens != self.max_output_tokens
            or checked.seed != self.seed
        ):
            raise ValueError("baseline_frozen_configuration_mismatch")
        return checked


class Tokenizer(Protocol):
    @property
    def identity(self) -> str: ...
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: Sequence[int]) -> str: ...
    def count_messages(self, messages: Sequence[Message]) -> int: ...


class ModelServices(Protocol):
    """Accounted calls only inside a bounded operation; production runtime implements this."""

    tokenizer: Tokenizer
    settings: BaselineSettings

    def scope(self, maximum: Usage) -> AbstractContextManager["OperationScope"]: ...
    def chunks(self, text: str) -> tuple[str, ...]: ...
    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings: ...
    def complete(
        self, messages: Sequence[Message], *, schema: dict[str, object] | None = None, json_object: bool = False
    ) -> Completion: ...


class OperationScope(Protocol):
    @property
    def usage(self) -> Usage: ...


type JsonObject = dict[str, object]
type Operation[T] = Callable[[], T]
type EventIterator = Iterator[JsonObject]
