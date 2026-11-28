"""``worker.settings_from_environment``: which adapter gets which model, and what refuses to start (``V2-D107``)."""

import pytest

from custom_memory_engine.worker import settings_from_environment

BASE = {
    "CME_RUNTIME_ROLE": "cme_rt",
    "CME_WORKER_USERS": "alice, bob",
    "CME_LLM_BASE_URL": "http://127.0.0.1:9/v1",
    "CME_LLM_API_KEY": "test-key-not-real",  # pragma: allowlist secret
    "CME_LLM_PROTOCOL": "openai",
    "CME_EXTRACTOR_MODEL": "extract-model",
}


def test_each_capability_gets_the_model_selected_for_it() -> None:
    settings = settings_from_environment(
        {**BASE, "CME_ADJUDICATOR_MODEL": "relation-model", "CME_ENTITY_ADJUDICATOR_MODEL": "entity-model"}
    )
    assert settings.users == ("alice", "bob")
    assert settings.extractor.client.model == "extract-model"  # type: ignore[attr-defined]
    assert settings.adjudicator.client.model == "relation-model"  # type: ignore[union-attr]
    assert settings.entity_adjudicator.client.model == "entity-model"  # type: ignore[union-attr]
    assert settings.embedder is None  # no asset named: lexical-only, said rather than guessed


def test_unset_adjudicator_models_fall_back_in_order() -> None:
    settings = settings_from_environment(BASE)
    assert settings.adjudicator.client.model == "extract-model"  # type: ignore[union-attr]
    assert settings.entity_adjudicator.client.model == "extract-model"  # type: ignore[union-attr]


def test_adjudication_can_be_switched_off() -> None:
    settings = settings_from_environment({**BASE, "CME_ADJUDICATE": "0"})
    assert settings.adjudicator is None and settings.entity_adjudicator is None


@pytest.mark.parametrize("missing", ["CME_RUNTIME_ROLE", "CME_WORKER_USERS", "CME_LLM_API_KEY", "CME_EXTRACTOR_MODEL"])
def test_a_missing_variable_refuses_to_start_and_names_only_the_variable(missing: str) -> None:
    environment = {key: value for key, value in BASE.items() if key != missing}
    with pytest.raises(ValueError) as refused:
        settings_from_environment(environment)
    assert missing.lower() in str(refused.value)
    assert "test-key-not-real" not in str(refused.value)
