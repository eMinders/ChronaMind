"""Unit tests for RL-011 pre-registered pilot freeze rules (D-09..D-14, D-21, D-22)."""

from pathlib import Path

import pytest

from custom_memory_engine.pilot_rules import (
    compute_luar_margin,
    compute_precision_scenario_count,
    compute_present_state_margin,
    evaluate_b4_inclusion_gate,
    select_longmemeval_budget_plan,
    select_memory_token_budget,
    select_primary_k,
    validate_h4_pilot_thresholds,
)

CME_ROOT = Path(__file__).resolve().parents[1]


def test_pilot_plan_document_completeness() -> None:
    plan_path = CME_ROOT / "evaluation/pilot_plan.md"
    assert plan_path.exists()
    content = plan_path.read_text(encoding="utf-8")
    for decision_id in ("D-09", "D-10", "D-11", "D-12", "D-13", "D-14", "D-21", "D-22"):
        assert decision_id in content
    assert "no test split" in content.lower()


def test_d09_select_memory_token_budget() -> None:
    scores = {512: 0.72, 1024: 0.78, 2048: 0.79}
    costs = {512: 8.0, 1024: 12.0, 2048: 19.0}
    assert select_memory_token_budget(scores, costs, budget_cap_usd=20.0) == 1024
    scores_high = {512: 0.70, 1024: 0.75, 2048: 0.79}
    assert select_memory_token_budget(scores_high, costs, budget_cap_usd=20.0) == 2048
    assert select_memory_token_budget(scores_high, costs, budget_cap_usd=10.0) == 512
    flat_scores = {512: 0.75, 1024: 0.755, 2048: 0.756}
    assert select_memory_token_budget(flat_scores, costs, budget_cap_usd=20.0, truncation_rate_at_512=0.15) == 1024
    with pytest.raises(ValueError, match="invalid_budget_cap"):
        select_memory_token_budget(scores, costs, budget_cap_usd=0.0)


def test_d10_select_primary_k() -> None:
    recalls = {3: 0.62, 5: 0.74, 10: 0.81}
    fits = {3: 1.0, 5: 0.96, 10: 0.82}
    assert select_primary_k(recalls, fits) == 5
    fits_tight = {3: 0.95, 5: 0.80, 10: 0.60}
    assert select_primary_k(recalls, fits_tight) == 3
    low_recalls = {3: 0.15, 5: 0.20, 10: 0.40}
    all_fit = {3: 1.0, 5: 1.0, 10: 0.95}
    assert select_primary_k(low_recalls, all_fit) == 10


def test_d11_compute_present_state_margin() -> None:
    diffs = [0.0, 0.05, -0.05, 0.0, 0.04, -0.04, 0.02, -0.02, 0.01, -0.01]
    margin = compute_present_state_margin(diffs, a2_mean=0.85, b0_mean=0.20)
    assert 0.02 <= margin <= 0.05
    with pytest.raises(ValueError, match="at_least_two_dev_scenarios_required"):
        compute_present_state_margin([0.0], a2_mean=0.85, b0_mean=0.20)


def test_d12_compute_luar_margin() -> None:
    diffs = [0.0, -0.02, 0.0, -0.03, 0.0, -0.01, 0.0, -0.02, 0.0, -0.01]
    margin = compute_luar_margin(diffs, dev_asr_reduction=0.75)
    assert 0.03 <= margin <= 0.08
    with pytest.raises(ValueError, match="insufficient_dev_asr_reduction"):
        compute_luar_margin(diffs, dev_asr_reduction=0.10)
    with pytest.raises(ValueError, match="excessive_dev_luar_degradation"):
        compute_luar_margin([-0.15] * 10, dev_asr_reduction=0.80)


def test_d13_validate_h4_pilot_thresholds() -> None:
    res = validate_h4_pilot_thresholds(
        test_scenarios=50,
        u_star=0.10,
        min_a8_leak_share=0.80,
        dev_p_leaks=0,
        dev_never_stated_false_positives=0,
        dev_a8_leak_share=1.0,
    )
    assert res.pilot_passed is True
    assert round(res.zero_leak_upper_bound, 4) == 0.0711
    with pytest.raises(ValueError, match="scenario_count_exceeds_u_star_bound"):
        validate_h4_pilot_thresholds(
            test_scenarios=50,
            u_star=0.05,
            min_a8_leak_share=0.80,
            dev_p_leaks=0,
            dev_never_stated_false_positives=0,
            dev_a8_leak_share=1.0,
        )


def test_d14_compute_precision_scenario_count() -> None:
    diffs = [0.20, 0.35, 0.15, 0.40, 0.25, 0.30, 0.10, 0.45, 0.20, 0.30]
    count = compute_precision_scenario_count(diffs, target_half_width=0.08, min_scenarios=50, max_budget_scenarios=100)
    assert 50 <= count <= 100
    with pytest.raises(ValueError, match="fewer_than_sign_flip_floor"):
        compute_precision_scenario_count([0.1, 0.2, 0.3])


def test_d21_evaluate_b4_inclusion_gate() -> None:
    assert evaluate_b4_inclusion_gate(
        end_to_end_success=True,
        uses_frozen_models=True,
        projected_b4_cost_usd=18.5,
        total_budget_cap_usd=100.0,
        projected_b4_tokens=8_000_000,
        total_token_cap=50_000_000,
    )
    assert not evaluate_b4_inclusion_gate(
        end_to_end_success=True,
        uses_frozen_models=True,
        projected_b4_cost_usd=35.0,
        total_budget_cap_usd=100.0,
        projected_b4_tokens=8_000_000,
        total_token_cap=50_000_000,
    )


def test_d22_select_longmemeval_budget_plan() -> None:
    full_plan = select_longmemeval_budget_plan(cost_per_history_usd=0.08, lme_budget_cap_usd=40.0)
    assert full_plan.mode == "full"
    assert full_plan.test_history_count == 400

    subset_plan = select_longmemeval_budget_plan(cost_per_history_usd=0.20, lme_budget_cap_usd=40.0)
    assert subset_plan.mode == "stratified_subset"
    assert subset_plan.test_history_count == 200

    with pytest.raises(ValueError, match="budget_insufficient_for_minimum_stratified_subset"):
        select_longmemeval_budget_plan(cost_per_history_usd=0.50, lme_budget_cap_usd=40.0)
