"""Explicit JSON configuration: no root application imports or environment fallbacks."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from evaluation.harness.models import ContextPolicy, Record, Usage


def safe_component(value: str) -> str:
    """Portable path component, including Windows reserved-device-name protection."""
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if (
        not value
        or len(value) > 80
        or not value[0].isascii()
        or not value[0].isalnum()
        or any(not char.isascii() or not (char.isalnum() or char in "-_") for char in value)
        or value.upper() in reserved
    ):
        raise ValueError("unsafe_artifact_identifier")
    return value


def canonical_json(value: object) -> bytes:
    """Stable UTF-8/LF bytes, independent of Windows text newline translation."""
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


class OperationLimits(Record):
    """Worst-case bounds include each operation's bounded retries and failed calls."""

    ingest_turn: Usage
    drain_queue: Usage
    retrieve: Usage
    answer: Usage
    delete: Usage


class HarnessConfig(Record):
    config_id: str
    system_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=0)
    seed: int = Field(ge=0)
    B_mem: int = Field(gt=0)
    context_policy: ContextPolicy = "retrieved_memory"
    context_window_tokens: int | None = Field(default=None, gt=0)
    answer_output_tokens: int = Field(default=2048, gt=0)
    adapter_configuration_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tokenizer_id: str = Field(min_length=1)
    budget_id: str
    token_cap: int = Field(gt=0, le=50_000_000)
    cost_cap_usd: Decimal = Field(gt=0, le=100)
    dev_cost_cap_usd: Decimal = Field(gt=0, le=20)
    operation_limits: OperationLimits

    @field_validator("config_id", "budget_id")
    @classmethod
    def portable_identifiers(cls, value: str) -> str:
        return safe_component(value)

    @field_validator(
        "model_id", "embedding_model_id", "judge_model_id", "tokenizer_id", "system_id", "adapter_version"
    )
    @classmethod
    def nonblank_pin(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explicit_pin_required")
        return value

    @field_validator("temperature", mode="before")
    @classmethod
    def temperature_is_numeric(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("numeric_zero_temperature_required")
        return value

    @model_validator(mode="after")
    def validate_suballocation(self) -> "HarnessConfig":
        if self.dev_cost_cap_usd > self.cost_cap_usd:
            raise ValueError("development_budget_exceeds_total")
        expected_policy = {"B0": "current_session", "B1": "full_history"}.get(self.system_id, "retrieved_memory")
        if self.context_policy != expected_policy:
            raise ValueError("context_policy_system_mismatch")
        if self.context_policy != "retrieved_memory" and (
            self.context_window_tokens is None or self.context_window_tokens <= self.answer_output_tokens
        ):
            raise ValueError("history_context_window_required")
        return self

    def execution_sha256(self) -> str:
        """Renaming a display ID cannot authorize another confirmatory run."""
        value = self.model_dump(mode="json", exclude={"config_id", "budget_id"})
        return hashlib.sha256(canonical_json(value)).hexdigest()


def load_config(path: Path) -> HarnessConfig:
    """Load exact, explicit pins; caller remains responsible for research approvals."""
    return HarnessConfig.model_validate_json(path.read_bytes())
