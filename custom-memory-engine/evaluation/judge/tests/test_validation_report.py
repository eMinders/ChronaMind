"""Verification tests for evaluation/judge/validation_report.md (RL-033)."""

from pathlib import Path

from evaluation.judge.llm_judge import (
    PINNED_ANSWERING_MODEL_ID,
    PINNED_JUDGE_MODEL_ID,
    JudgeConfig,
    LLMJudge,
    build_stratified_dev_reference_sample,
    create_reference_rubric_judge_callable,
    evaluate_perturbation_stability,
    validate_judge_on_dev_sample,
)


def test_validation_report_exists_and_matches_calibration_numbers() -> None:
    report_path = Path(__file__).resolve().parents[1] / "validation_report.md"
    assert report_path.is_file()
    content = report_path.read_text(encoding="utf-8")

    # Pinned model IDs and governance references
    assert PINNED_ANSWERING_MODEL_ID in content
    assert PINNED_JUDGE_MODEL_ID in content
    assert "D-03" in content
    assert "D-24" in content
    assert "same-family" in content.lower()
    assert "length-normalization" in content.lower()

    # Verify calibration numbers match build_stratified_dev_reference_sample()
    judge_items, validation_items, metadata_map = build_stratified_dev_reference_sample()
    judge = LLMJudge(
        config=JudgeConfig(),
        completion_fn=create_reference_rubric_judge_callable(metadata_map),
    )
    pert = evaluate_perturbation_stability(judge_items, judge)
    summary = validate_judge_on_dev_sample(
        validation_items,
        perturbation_report=pert,
        approvals_complete=False,
        bootstrap_replicates=10_000,
    )
    assert summary.inter_human.kappa is not None
    assert summary.judge_vs_gold.kappa is not None
    assert f"{summary.inter_human.kappa:.4f}" in content
    assert f"{summary.judge_vs_gold.kappa:.4f}" in content
    assert "approvals_complete=False" in content
