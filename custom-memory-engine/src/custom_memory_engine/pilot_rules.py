"""Deterministic pilot parameter freeze functions for RL-011 / RL-100."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from math import ceil, isfinite
from statistics import mean, stdev
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from custom_memory_engine.protocols import zero_leak_upper

TokenBudget = Literal[512, 1024, 2048]
RecallK = Literal[3, 5, 10]
HOLM_THREE_TEST_Z = 2.3939797998185106  # Φ⁻¹(1 − 0.05 / 6) for two-sided (1 − 0.05/3) CI
SIGN_FLIP_CLUSTER_FLOOR = 7


class H4PilotThresholds(BaseModel):
    """Validated H4 scenario-level upper bound threshold u* and A8 positive-control gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    test_scenarios: int = Field(ge=36)
    zero_leak_upper_bound: float = Field(gt=0, lt=1)
    u_star: float = Field(gt=0, le=0.10)
    min_a8_leak_share: float = Field(ge=0.80, le=1.0)
    pilot_passed: bool


class LongMemEvalBudgetPlan(BaseModel):
    """Frozen LongMemEval_S test evaluation plan (full vs. seeded stratified subset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["full", "stratified_subset"]
    test_history_count: int = Field(ge=100, le=400)
    sampling_seed: int = 42
    projected_cost_usd: float = Field(ge=0)


def select_memory_token_budget(
    composite_scores: Mapping[int, float],
    projected_cost_usd: Mapping[int, float],
    *,
    budget_cap_usd: float,
    truncation_rate_at_512: float = 0.0,
    min_step_gain: float = 0.02,
) -> TokenBudget:
    """D-09: deterministically select B_mem in {512, 1024, 2048} from dev coverage and cost."""
    for candidate in (512, 1024, 2048):
        if candidate not in composite_scores or candidate not in projected_cost_usd:
            raise ValueError("missing_candidate_budget_metrics")
        score = composite_scores[candidate]
        cost = projected_cost_usd[candidate]
        if not isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("invalid_composite_score")
        if not isfinite(cost) or cost < 0.0:
            raise ValueError("invalid_projected_cost")
    if not isfinite(budget_cap_usd) or budget_cap_usd <= 0.0:
        raise ValueError("invalid_budget_cap")
    if not isfinite(truncation_rate_at_512) or not 0.0 <= truncation_rate_at_512 <= 1.0:
        raise ValueError("invalid_truncation_rate")

    selected: TokenBudget = 512
    gain_1024 = composite_scores[1024] - composite_scores[512]
    if projected_cost_usd[1024] <= budget_cap_usd and (gain_1024 >= min_step_gain or truncation_rate_at_512 > 0.10):
        selected = 1024
        gain_2048 = composite_scores[2048] - composite_scores[1024]
        if projected_cost_usd[2048] <= budget_cap_usd and gain_2048 >= min_step_gain:
            selected = 2048
    return selected


def select_primary_k(
    dev_recall: Mapping[int, float],
    effective_fit_ratio: Mapping[int, float],
) -> RecallK:
    """D-10: deterministically select primary k in {3, 5, 10} for Evidence Recall@k."""
    for k in (3, 5, 10):
        if k not in dev_recall or k not in effective_fit_ratio:
            raise ValueError("missing_k_candidate")
        if not isfinite(dev_recall[k]) or not 0.0 <= dev_recall[k] <= 1.0:
            raise ValueError("invalid_recall_value")
        if not isfinite(effective_fit_ratio[k]) or not 0.0 <= effective_fit_ratio[k] <= 1.0:
            raise ValueError("invalid_fit_ratio")

    admissible: list[RecallK] = [k for k in (3, 5, 10) if effective_fit_ratio[k] >= 0.90]
    if not admissible:
        return 3
    non_saturated: list[RecallK] = [k for k in admissible if 0.25 <= dev_recall[k] <= 0.95]
    candidates = non_saturated if non_saturated else admissible
    if 5 in candidates:
        return 5
    return max(candidates)


def compute_present_state_margin(
    paired_diffs_p_minus_a2: Sequence[float],
    *,
    a2_mean: float,
    b0_mean: float,
) -> float:
    """D-11: compute H1 present-state non-inferiority margin delta_pres in [0.02, 0.05]."""
    if len(paired_diffs_p_minus_a2) < 2:
        raise ValueError("at_least_two_dev_scenarios_required")
    if any(not isfinite(val) or not -1.0 <= val <= 1.0 for val in paired_diffs_p_minus_a2):
        raise ValueError("invalid_paired_difference")
    if not (isfinite(a2_mean) and isfinite(b0_mean) and 0.0 <= a2_mean <= 1.0 and 0.0 <= b0_mean <= 1.0):
        raise ValueError("invalid_baseline_means")

    sd_pres = stdev(paired_diffs_p_minus_a2)
    delta_mem = max(a2_mean - b0_mean, 0.0)
    raw = min(0.15 * delta_mem, 0.50 * sd_pres)
    clipped = min(max(raw, 0.02), 0.05)
    return round(clipped, 4)


def compute_luar_margin(
    paired_luar_diffs_p_minus_a3: Sequence[float],
    *,
    dev_asr_reduction: float,
) -> float:
    """D-12: compute H3 LUAR non-inferiority margin delta_luar in [0.03, 0.08]."""
    if len(paired_luar_diffs_p_minus_a3) < 2:
        raise ValueError("at_least_two_dev_scenarios_required")
    if any(not isfinite(val) or not -1.0 <= val <= 1.0 for val in paired_luar_diffs_p_minus_a3):
        raise ValueError("invalid_luar_difference")
    if not isfinite(dev_asr_reduction) or dev_asr_reduction < 0.30:
        raise ValueError("insufficient_dev_asr_reduction")

    mean_diff = abs(mean(paired_luar_diffs_p_minus_a3))
    if mean_diff > 0.05:
        raise ValueError("excessive_dev_luar_degradation")
    sd_luar = stdev(paired_luar_diffs_p_minus_a3)
    raw = max(mean_diff + 0.50 * sd_luar, 0.03)
    clipped = min(max(raw, 0.03), 0.08)
    return round(clipped, 4)


def validate_h4_pilot_thresholds(
    *,
    test_scenarios: int,
    u_star: float = 0.10,
    min_a8_leak_share: float = 0.80,
    dev_p_leaks: int,
    dev_never_stated_false_positives: int,
    dev_a8_leak_share: float,
) -> H4PilotThresholds:
    """D-13: validate N_D vs u* and dev positive/negative deletion controls."""
    if test_scenarios < 36:
        raise ValueError("insufficient_scenarios_for_informativeness_bound")
    if not isfinite(u_star) or not 0.0 < u_star <= 0.10:
        raise ValueError("invalid_u_star")
    if not isfinite(min_a8_leak_share) or not 0.80 <= min_a8_leak_share <= 1.0:
        raise ValueError("invalid_min_a8_leak_share")
    if not isfinite(dev_a8_leak_share) or not 0.0 <= dev_a8_leak_share <= 1.0:
        raise ValueError("invalid_dev_a8_leak_share")

    upper = zero_leak_upper(test_scenarios)
    if upper > u_star:
        raise ValueError("scenario_count_exceeds_u_star_bound")

    pilot_passed = (
        dev_p_leaks == 0 and dev_never_stated_false_positives == 0 and dev_a8_leak_share >= min_a8_leak_share
    )
    return H4PilotThresholds(
        test_scenarios=test_scenarios,
        zero_leak_upper_bound=upper,
        u_star=u_star,
        min_a8_leak_share=min_a8_leak_share,
        pilot_passed=pilot_passed,
    )


def compute_precision_scenario_count(
    paired_cluster_diffs: Sequence[float],
    *,
    target_half_width: float = 0.08,
    min_scenarios: int = 50,
    max_budget_scenarios: int = 100,
) -> int:
    """D-14: compute test scenario count from dev between-scenario SD and target CI half-width."""
    if len(paired_cluster_diffs) < SIGN_FLIP_CLUSTER_FLOOR:
        raise ValueError("fewer_than_sign_flip_floor")
    if any(not isfinite(val) for val in paired_cluster_diffs):
        raise ValueError("non_finite_cluster_diff")
    if not isfinite(target_half_width) or target_half_width <= 0.0:
        raise ValueError("invalid_target_half_width")
    if min_scenarios < SIGN_FLIP_CLUSTER_FLOOR or max_budget_scenarios < min_scenarios:
        raise ValueError("invalid_scenario_bounds")

    cluster_sd = stdev(paired_cluster_diffs)
    required = ceil(((HOLM_THREE_TEST_Z * cluster_sd) / target_half_width) ** 2)
    return max(min_scenarios, min(required, max_budget_scenarios))


def evaluate_b4_inclusion_gate(
    *,
    end_to_end_success: bool,
    uses_frozen_models: bool,
    projected_b4_cost_usd: float,
    total_budget_cap_usd: float = 100.0,
    projected_b4_tokens: int,
    total_token_cap: int = 50_000_000,
) -> bool:
    """D-21: decide whether conditional baseline B4 (Graphiti) is included."""
    if not isfinite(projected_b4_cost_usd) or projected_b4_cost_usd < 0.0:
        raise ValueError("invalid_projected_cost")
    if not isfinite(total_budget_cap_usd) or total_budget_cap_usd <= 0.0:
        raise ValueError("invalid_budget_cap")
    if projected_b4_tokens < 0 or total_token_cap <= 0:
        raise ValueError("invalid_token_counts")

    return (
        end_to_end_success is True
        and uses_frozen_models is True
        and projected_b4_cost_usd <= 0.25 * total_budget_cap_usd
        and projected_b4_tokens <= 0.25 * total_token_cap
    )


def select_longmemeval_budget_plan(
    *,
    cost_per_history_usd: float,
    lme_budget_cap_usd: float = 40.0,
    total_test_histories: int = 400,
    min_subset_histories: int = 100,
    sampling_seed: int = 42,
) -> LongMemEvalBudgetPlan:
    """D-22: select full LongMemEval_S (400 test histories) or a seeded stratified subset."""
    if not isfinite(cost_per_history_usd) or cost_per_history_usd <= 0.0:
        raise ValueError("invalid_cost_per_history")
    if not isfinite(lme_budget_cap_usd) or lme_budget_cap_usd <= 0.0:
        raise ValueError("invalid_lme_budget_cap")
    if total_test_histories < min_subset_histories or min_subset_histories < 50:
        raise ValueError("invalid_history_bounds")

    full_cost = cost_per_history_usd * total_test_histories
    if full_cost <= lme_budget_cap_usd:
        return LongMemEvalBudgetPlan(
            mode="full",
            test_history_count=total_test_histories,
            sampling_seed=sampling_seed,
            projected_cost_usd=round(full_cost, 4),
        )

    affordable = int(Decimal(str(lme_budget_cap_usd)) // Decimal(str(cost_per_history_usd)))
    if affordable < min_subset_histories:
        raise ValueError("budget_insufficient_for_minimum_stratified_subset")

    subset_count = min(affordable, total_test_histories)
    return LongMemEvalBudgetPlan(
        mode="stratified_subset",
        test_history_count=subset_count,
        sampling_seed=sampling_seed,
        projected_cost_usd=round(subset_count * cost_per_history_usd, 4),
    )
