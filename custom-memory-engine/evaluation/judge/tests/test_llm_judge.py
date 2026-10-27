"""Unit and integration tests for single-answer LLM judge, caching, perturbations, and D-24 validation (RL-033)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evaluation.harness.budget import BudgetLedger
from evaluation.harness.config import HarnessConfig, OperationLimits
from evaluation.harness.models import Usage
from evaluation.judge.llm_judge import (
    LENGTH_NORMALIZATION_CLAUSE,
    PINNED_ANSWERING_MODEL_ID,
    PINNED_JUDGE_MODEL_ID,
    DevValidationItem,
    JudgeCacheKey,
    JudgeConfig,
    JudgeItem,
    JudgeProviderResponse,
    JudgeResponseCache,
    JudgeTransientProviderError,
    JudgeValidationError,
    LLMJudge,
    append_irrelevant_detail,
    build_judge_prompt,
    build_stratified_dev_reference_sample,
    create_reference_rubric_judge_callable,
    evaluate_perturbation_stability,
    paraphrase_hypothesis,
    parse_judge_response,
    prompt_sha256,
    randomize_presentation_order,
    validate_judge_on_dev_sample,
)


def _sample_item(*, item_id: str = "T-S01-Q1", hypothesis: str = "Your current city is Lisbon.") -> JudgeItem:
    return JudgeItem(
        item_id=item_id,
        cluster_id="T-S01",
        suite="T",
        split="dev",
        mode="current",
        question="Where do I currently live?",
        gold_answer="Lisbon",
        hypothesis=hypothesis,
    )


def test_judge_config_enforces_d03_and_bias_disclosure() -> None:
    cfg = JudgeConfig()
    assert cfg.judge_model_id == PINNED_JUDGE_MODEL_ID
    assert cfg.answering_model_id == PINNED_ANSWERING_MODEL_ID
    assert cfg.temperature == 0.0
    assert cfg.top_p == 1.0
    assert cfg.max_completion_tokens == 1024
    assert cfg.same_family_bias_disclosed is True

    with pytest.raises(ValueError, match="judge_model_must_differ_from_answering_model"):
        JudgeConfig(judge_model_id=PINNED_ANSWERING_MODEL_ID, answering_model_id=PINNED_ANSWERING_MODEL_ID)

    with pytest.raises(ValueError, match="judge_temperature_must_be_zero"):
        JudgeConfig(temperature=0.2)

    with pytest.raises(ValueError, match="same_family_bias_disclosure_required"):
        JudgeConfig(same_family_bias_disclosed=False)


def test_build_judge_prompt_contains_length_normalization_and_single_answer_rubric() -> None:
    item = _sample_item()
    prompt = build_judge_prompt(item)
    assert LENGTH_NORMALIZATION_CLAUSE in prompt
    assert "Where do I currently live?" in prompt
    assert "Lisbon" in prompt
    assert "Your current city is Lisbon." in prompt
    assert len(prompt_sha256(prompt)) == 64

    # LongMemEval task prompt integration
    lme_item = JudgeItem(
        item_id="LME-01",
        cluster_id="LME-S01",
        suite="LongMemEval",
        split="dev",
        mode="current",
        longmemeval_question_type="temporal-reasoning",
        question="Which apartment did I move into last?",
        gold_answer="Apartment 4B",
        hypothesis="You moved into Apartment 4B most recently.",
    )
    lme_prompt = build_judge_prompt(lme_item)
    assert "off-by-one" in lme_prompt


def test_randomize_presentation_order_is_deterministic_and_bijective() -> None:
    items = [_sample_item(item_id=f"T-S01-Q{i}") for i in range(1, 9)]
    perm1 = randomize_presentation_order(items, seed=20260417)
    perm2 = randomize_presentation_order(items, seed=20260417)
    assert [it.item_id for _, it in perm1] == [it.item_id for _, it in perm2]
    assert {it.item_id for _, it in perm1} == {it.item_id for it in items}
    assert [it.item_id for _, it in perm1] != [it.item_id for it in items]


def test_parse_judge_response_json_and_yes_no() -> None:
    parsed = parse_judge_response('{"label": 1, "rationale": "Matches scripted gold value."}')
    assert parsed.label == 1
    assert parsed.rationale == "Matches scripted gold value."

    parsed_fenced = parse_judge_response('```json\n{"label": 0, "rationale": "Stale value."}\n```')
    assert parsed_fenced.label == 0

    parsed_yes = parse_judge_response("yes", allow_yes_no_fallback=True)
    assert parsed_yes.label == 1

    parsed_no = parse_judge_response("No\nThe answer states the old value.", allow_yes_no_fallback=True)
    assert parsed_no.label == 0

    with pytest.raises(JudgeValidationError, match="unparseable_judge_response"):
        parse_judge_response("maybe correct", allow_yes_no_fallback=False)


class _FakeSequenceCallable:
    """Test double that replays scripted responses or exceptions."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: int = 0

    def __call__(self, prompt: str, config: JudgeConfig) -> JudgeProviderResponse | str:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_harness_config() -> HarnessConfig:
    op_usage = Usage(input_tokens=500, output_tokens=100, embedding_tokens=0, cost_usd=Decimal("0.01"))
    return HarnessConfig(
        config_id="cfg-rl033",
        system_id="P",
        adapter_version="0.1.0",
        model_id=PINNED_ANSWERING_MODEL_ID,
        embedding_model_id="text-embedding-3-small",
        judge_model_id=PINNED_JUDGE_MODEL_ID,
        temperature=0.0,
        seed=42,
        B_mem=2000,
        prompt_sha256="a" * 64,
        tokenizer_id="o200k_base",
        budget_id="budget-rl033",
        token_cap=1_000_000,
        cost_cap_usd=Decimal("10.00"),
        dev_cost_cap_usd=Decimal("5.00"),
        operation_limits=OperationLimits(
            ingest_turn=op_usage,
            drain_queue=op_usage,
            retrieve=op_usage,
            answer=op_usage,
            delete=op_usage,
        ),
    )


def test_llm_judge_retries_transient_errors_and_never_caches_failures(tmp_path: Path) -> None:
    cache_file = tmp_path / "judge_cache.json"
    cache = JudgeResponseCache(cache_path=cache_file)
    ledger = BudgetLedger(tmp_path, _make_harness_config())
    max_usage = Usage(input_tokens=500, output_tokens=100, embedding_tokens=0, cost_usd=Decimal("0.01"))

    fn = _FakeSequenceCallable(
        [
            JudgeTransientProviderError("rate_limit_429"),
            JudgeProviderResponse(
                raw_text='{"label": 1, "rationale": "Correct current city."}',
                usage=Usage(input_tokens=180, output_tokens=20, embedding_tokens=0, cost_usd=Decimal("0.002")),
            ),
        ]
    )
    cfg = JudgeConfig()
    judge = LLMJudge(
        config=cfg,
        completion_fn=fn,
        cache=cache,
        budget_ledger=ledger,
        max_call_usage=max_usage,
    )
    item = _sample_item()

    verdict1 = judge.evaluate_item(item)
    assert verdict1.label == 1
    assert verdict1.cache_hit is False
    assert fn.calls == 2

    # Second call hits the validated cache without invoking the provider or charging budget again
    verdict2 = judge.evaluate_item(item)
    assert verdict2.label == 1
    assert verdict2.cache_hit is True
    assert fn.calls == 2

    # Reload cache from disk and verify SHA-256 envelope
    reloaded = JudgeResponseCache(cache_path=cache_file)
    assert reloaded.entry_count == 1

    # Verify failed format error is NOT cached and increments errors_rejected
    bad_item = _sample_item(item_id="T-S01-Q2", hypothesis="Malformed test")
    bad_fn = _FakeSequenceCallable(
        [
            JudgeProviderResponse(
                raw_text="not valid json at all",
                usage=Usage(input_tokens=100, output_tokens=10, embedding_tokens=0, cost_usd=Decimal("0.001")),
            )
        ]
    )
    bad_judge = LLMJudge(
        config=cfg,
        completion_fn=bad_fn,
        cache=cache,
        budget_ledger=ledger,
        max_call_usage=max_usage,
    )
    with pytest.raises(JudgeValidationError):
        bad_judge.evaluate_item(bad_item)
    bad_key = JudgeCacheKey(
        judge_model_id=cfg.judge_model_id,
        rubric_version=cfg.rubric_version,
        prompt_sha256=prompt_sha256(build_judge_prompt(bad_item, rubric_version=cfg.rubric_version)),
    )
    assert cache.get(bad_key) is None
    assert cache.errors_rejected == 1


def test_stratified_dev_sample_perturbation_stability_and_d24_gate(tmp_path: Path) -> None:
    judge_items, validation_items, metadata_map = build_stratified_dev_reference_sample()
    assert len(judge_items) == 120
    assert len(validation_items) == 120

    # Verify exact stratification across all 5 Suite T modes and balanced positive/negative classes
    modes = [it.mode for it in judge_items]
    for mode in ("current", "as_of", "what_changed", "before_change", "stable"):
        assert modes.count(mode) >= 1
    assert sum(1 for v in validation_items if v.adjudicated_gold_label == 1) == 60
    assert sum(1 for v in validation_items if v.adjudicated_gold_label == 0) == 60

    # Run LLMJudge with reference rubric callable over all 120 items and both perturbations
    ref_fn = create_reference_rubric_judge_callable(metadata_map)
    cache = JudgeResponseCache(cache_path=tmp_path / "dev_cache.json")
    judge = LLMJudge(config=JudgeConfig(), completion_fn=ref_fn, cache=cache)

    pert_report = evaluate_perturbation_stability(judge_items, judge)
    assert pert_report.sample_size == 120
    assert pert_report.irrelevant_flip_count == 0
    assert pert_report.paraphrase_flip_count == 0
    assert pert_report.irrelevant_flip_rate == 0.0
    assert pert_report.paraphrase_flip_rate == 0.0
    assert pert_report.passed is True

    # Validate D-24 report with approvals_complete=False (pre-sign-off state) and True
    summary_unsigned = validate_judge_on_dev_sample(
        validation_items,
        perturbation_report=pert_report,
        approvals_complete=False,
        bootstrap_replicates=10_000,
    )
    assert summary_unsigned.sample_size == 120
    assert summary_unsigned.cluster_count == 20
    assert summary_unsigned.judge_vs_gold.kappa is not None
    assert summary_unsigned.judge_vs_gold.kappa >= 0.80
    assert summary_unsigned.inter_human.kappa is not None
    assert summary_unsigned.inter_human.kappa >= 0.80
    assert summary_unsigned.gate_passed is False

    summary_signed = validate_judge_on_dev_sample(
        validation_items,
        perturbation_report=pert_report,
        approvals_complete=True,
        bootstrap_replicates=10_000,
    )
    assert summary_signed.gate_passed is True


def test_validate_judge_on_dev_sample_rejects_test_split_and_disagreement_without_adjudicator() -> None:
    with pytest.raises(ValueError, match="test_split_forbidden_in_judge_validation"):
        DevValidationItem(
            item_id="T-S01-Q1",
            cluster_id="T-S01",
            suite="T",
            split="test",
            mode="current",
            answerable=True,
            annotator_1_id="a1",
            annotator_2_id="a2",
            annotator_1_label=1,
            annotator_2_label=1,
            adjudicated_gold_label=1,
            judge_label=1,
        )

    with pytest.raises(ValueError, match="disagreement_requires_adjudicator_id"):
        DevValidationItem(
            item_id="T-S01-Q1",
            cluster_id="T-S01",
            suite="T",
            split="dev",
            mode="current",
            answerable=True,
            annotator_1_id="a1",
            annotator_2_id="a2",
            annotator_1_label=1,
            annotator_2_label=0,
            adjudicated_gold_label=1,
            judge_label=1,
        )


def test_perturbation_helpers_transform_item_deterministically() -> None:
    orig = _sample_item(hypothesis="Currently, your recorded value is Lisbon.")
    with_detail = append_irrelevant_detail(orig, suffix_index=0)
    assert with_detail.hypothesis.startswith(orig.hypothesis)
    assert "chamomile tea" in with_detail.hypothesis
    assert with_detail.item_id.endswith("__pert_irrelevant")

    paraphrased = paraphrase_hypothesis(orig)
    assert paraphrased.hypothesis != orig.hypothesis
    assert "Lisbon" in paraphrased.hypothesis
    assert paraphrased.hypothesis.startswith("At the present time, ")
    assert paraphrased.item_id.endswith("__pert_paraphrase")
