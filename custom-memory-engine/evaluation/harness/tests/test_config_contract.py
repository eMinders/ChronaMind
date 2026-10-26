"""Pinned configuration must reject silent changes and invalid accounting."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.harness.config import HarnessConfig, load_config


def config_data() -> dict[str, object]:
    return {
        "config_id": "dummy",
        "system_id": "fixture-dummy",
        "adapter_version": "fixture-v1",
        "model_id": "fixture-answer-v1",
        "embedding_model_id": "fixture-embedding-v1",
        "judge_model_id": "fixture-judge-v1",
        "temperature": 0,
        "seed": 42,
        "B_mem": 512,
        "prompt_sha256": "a" * 64,
        "tokenizer_id": "fixture-tokenizer-v1",
        "budget_id": "fixture-campaign",
        "token_cap": 1000,
        "cost_cap_usd": "1.00",
        "dev_cost_cap_usd": "0.50",
        "operation_limits": {
            name: {"input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0, "cost_usd": "0"}
            for name in ("ingest_turn", "drain_queue", "retrieve", "answer", "delete")
        },
    }


def test_load_requires_explicit_pins_and_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_data()), encoding="utf-8")
    config = load_config(path)
    assert config.seed == 42
    assert config.B_mem == 512
    with pytest.raises(ValidationError):
        config.seed = 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 0.1),
        ("seed", True),
        ("B_mem", 0),
        ("cost_cap_usd", "NaN"),
        ("config_id", "../escape"),
        ("config_id", "CON"),
        ("model_id", ""),
        ("extra", "ignored"),
    ],
)
def test_invalid_config_rejected(field: str, value: object) -> None:
    data = config_data()
    data[field] = value
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate_json(json.dumps(data))


def test_config_hash_is_content_based_and_not_a_display_name() -> None:
    first = config_data()
    second = {**first, "config_id": "renamed"}
    assert HarnessConfig.model_validate_json(json.dumps(first)).execution_sha256() == (
        HarnessConfig.model_validate_json(json.dumps(second)).execution_sha256()
    )


def test_distinct_systems_share_controls_but_have_distinct_execution_identity() -> None:
    first = config_data()
    second = {**first, "system_id": "P"}
    assert HarnessConfig.model_validate_json(json.dumps(first)).execution_sha256() != (
        HarnessConfig.model_validate_json(json.dumps(second)).execution_sha256()
    )
