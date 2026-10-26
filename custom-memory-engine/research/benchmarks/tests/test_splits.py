"""Gate J5 frozen split verification and disjointness tests (RL-026)."""

import hashlib
import shutil
from pathlib import Path

import pytest

from evaluation.harness.models import SuiteId
from research.benchmarks.splits import (
    FREEZE_LIST_PATH,
    SPLIT_FILENAMES,
    SPLITS_DIR,
    FrozenSuiteSplit,
    assign_own_scenario_split_by_sha256,
    build_all_splits,
    build_locomo_split,
    build_longmemeval_split,
    build_own_suite_split,
    load_split_yaml,
    load_suite_split,
    partition_scenarios_by_sha256,
    scenario_id_sha256,
    split_path_for_suite,
    to_benchmark_selection,
    verify_frozen_splits,
)


def test_all_frozen_split_yaml_files_and_freeze_list_verify_cleanly() -> None:
    digests = verify_frozen_splits(SPLITS_DIR, FREEZE_LIST_PATH)
    assert set(digests.keys()) == set(SPLIT_FILENAMES.values())
    for filename, sha256_hex in digests.items():
        path = SPLITS_DIR / filename
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256_hex


def test_zero_cluster_overlap_across_all_six_suites() -> None:
    splits = build_all_splits(seed=42)
    expected_keys = {
        "longmemeval": ("question_id", 100, 400, 500),
        "locomo": ("conversation_id", 2, 8, 10),
        "own_T": ("scenario_id", 20, 50, 70),
        "own_G": ("scenario_id", 20, 50, 70),
        "own_D": ("scenario_id", 20, 50, 70),
        "own_I": ("scenario_id", 20, 50, 70),
    }
    for name, (cluster_key, dev_n, test_n, total_n) in expected_keys.items():
        doc = splits[name]
        assert doc.cluster_key == cluster_key
        assert doc.dev_count == dev_n
        assert doc.test_count == test_n
        assert doc.total_count == total_n
        dev_set = set(doc.dev_ids)
        test_set = set(doc.test_ids)
        assert len(dev_set) == dev_n
        assert len(test_set) == test_n
        assert dev_set.isdisjoint(test_set), f"Overlap detected in {name}"
        assert set(doc.dev_id_sha256.values()).isdisjoint(set(doc.test_id_sha256.values()))


def test_longmemeval_stratified_split_proportions_and_determinism() -> None:
    split_a = build_longmemeval_split(seed=42, verify_s_variant=True)
    split_b = build_longmemeval_split(seed=42)
    assert split_a == split_b
    on_disk = load_split_yaml(SPLITS_DIR / "longmemeval.yaml")
    assert on_disk == split_a

    dev_strata = split_a.strata_counts["dev"]
    test_strata = split_a.strata_counts["test"]
    assert dev_strata["total_questions"] == 100
    assert test_strata["total_questions"] == 400
    assert dev_strata["abstention"] == 6
    assert test_strata["abstention"] == 24
    assert dev_strata["answerable"] == 94
    assert test_strata["answerable"] == 376
    assert dev_strata["single_session_assistant"] == 11
    assert test_strata["single_session_assistant"] == 45
    assert dev_strata["primary_recall_eligible"] == 83
    assert test_strata["primary_recall_eligible"] == 331

    expected_qtype_totals = {
        "question_type:knowledge-update": (16, 62),
        "question_type:multi-session": (27, 106),
        "question_type:single-session-assistant": (11, 45),
        "question_type:single-session-preference": (6, 24),
        "question_type:single-session-user": (14, 56),
        "question_type:temporal-reasoning": (26, 107),
    }
    for key, (expected_dev, expected_test) in expected_qtype_totals.items():
        assert dev_strata[key] == expected_dev
        assert test_strata[key] == expected_test


def test_locomo_conversation_level_split_and_category_totals() -> None:
    split_doc = build_locomo_split(seed=42)
    on_disk = load_split_yaml(SPLITS_DIR / "locomo.yaml")
    assert on_disk == split_doc
    assert split_doc.dev_count == 2
    assert split_doc.test_count == 8
    assert split_doc.total_count == 10

    dev_s = split_doc.strata_counts["dev"]
    test_s = split_doc.strata_counts["test"]
    assert dev_s["total_questions"] + test_s["total_questions"] == 1986
    expected_cat_totals = {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}
    for cat, total in expected_cat_totals.items():
        assert dev_s[f"category_{cat}"] > 0
        assert test_s[f"category_{cat}"] > 0
        assert dev_s[f"category_{cat}"] + test_s[f"category_{cat}"] == total


@pytest.mark.parametrize("suite_family", ["T", "G", "D", "I"])
def test_own_suites_scenario_sha256_assignment_and_disjointness(suite_family: str) -> None:
    doc = build_own_suite_split(suite_family, seed=42)  # type: ignore[arg-type]
    on_disk = load_split_yaml(SPLITS_DIR / f"own_{suite_family}.yaml")
    assert on_disk == doc
    for sid in doc.dev_ids:
        assert assign_own_scenario_split_by_sha256(sid, doc) == "dev"
        assert doc.dev_id_sha256[sid] == scenario_id_sha256(sid)
    for sid in doc.test_ids:
        assert assign_own_scenario_split_by_sha256(sid, doc) == "test"
        assert doc.test_id_sha256[sid] == scenario_id_sha256(sid)
    with pytest.raises(ValueError, match="unknown_or_ambiguous_scenario_id"):
        assign_own_scenario_split_by_sha256("nonexistent_scenario_999", doc)


def test_partition_scenarios_by_sha256_is_deterministic_and_disjoint() -> None:
    pool = [f"scenario_slot_{idx:03d}" for idx in range(1, 71)]
    dev_a, test_a = partition_scenarios_by_sha256(pool, dev_count=20, seed=42)
    dev_b, test_b = partition_scenarios_by_sha256(list(reversed(pool)), dev_count=20, seed=42)
    assert dev_a == dev_b
    assert test_a == test_b
    assert len(dev_a) == 20
    assert len(test_a) == 50
    assert set(dev_a).isdisjoint(set(test_a))
    assert set(dev_a) | set(test_a) == set(pool)

    with pytest.raises(ValueError, match="distinct_nonempty_scenario_ids_required"):
        partition_scenarios_by_sha256(["dup", "dup"], dev_count=1)
    with pytest.raises(ValueError, match="invalid_dev_scenario_count"):
        partition_scenarios_by_sha256(["a", "b"], dev_count=2)


@pytest.mark.parametrize(
    ("yaml_name", "suite_id"),
    [
        ("longmemeval.yaml", "LongMemEval_oracle"),
        ("longmemeval.yaml", "LongMemEval_S"),
        ("longmemeval.yaml", "LongMemEval_M"),
        ("locomo.yaml", "LoCoMo"),
        ("own_T.yaml", "T"),
        ("own_G.yaml", "G"),
        ("own_D.yaml", "D"),
        ("own_I.yaml", "I"),
    ],
)
def test_to_benchmark_selection_materializes_valid_envelopes(yaml_name: str, suite_id: SuiteId) -> None:
    assert split_path_for_suite(suite_id) == SPLITS_DIR / yaml_name
    doc = load_suite_split(suite_id)
    dev_sel = to_benchmark_selection(doc, suite_id=suite_id, split="dev", retrieval_k=5)
    test_sel = to_benchmark_selection(doc, suite_id=suite_id, split="test", retrieval_k=5)
    assert dev_sel.case_ids == doc.dev_ids
    assert test_sel.case_ids == doc.test_ids
    assert set(dev_sel.case_ids).isdisjoint(set(test_sel.case_ids))
    assert dev_sel.retrieval_k == 5

    with pytest.raises(ValueError, match="suite_id_not_in_split_document"):
        to_benchmark_selection(doc, suite_id="LoCoMo" if suite_id != "LoCoMo" else "T", split="dev")


def test_freeze_list_contains_required_signoffs_and_tamper_detection(tmp_path: Path) -> None:
    freeze_text = FREEZE_LIST_PATH.read_text(encoding="utf-8")
    assert "Member 4 (R-DB)" in freeze_text
    assert "Member 1 (R-LEAD)" in freeze_text
    assert "Gate J5" in freeze_text

    # Copy splits to tmp_path and verify tamper detection when FREEZE_LIST.md omits a hash
    tmp_splits = tmp_path / "splits"
    shutil.copytree(SPLITS_DIR, tmp_splits)
    tmp_freeze = tmp_splits / "FREEZE_LIST.md"
    assert verify_frozen_splits(tmp_splits, tmp_freeze)

    lme_doc = load_split_yaml(tmp_splits / "longmemeval.yaml")
    corrupted = freeze_text.replace(lme_doc.split_payload_sha256, "0" * 64)
    tmp_freeze.write_text(corrupted, encoding="utf-8")
    with pytest.raises(ValueError, match="split_payload_sha256_missing_from_freeze_list"):
        verify_frozen_splits(tmp_splits, tmp_freeze)


def test_frozen_suite_split_rejects_overlap_and_digest_corruption() -> None:
    doc = load_split_yaml(SPLITS_DIR / "locomo.yaml")
    raw = doc.model_dump()

    overlapping = {
        **raw,
        "dev_ids": (doc.dev_ids[0], doc.test_ids[0]),
    }
    with pytest.raises(ValueError, match="dev_test_cluster_overlap_detected"):
        FrozenSuiteSplit.model_validate(overlapping)

    bad_sha = {
        **raw,
        "split_payload_sha256": "f" * 64,
    }
    with pytest.raises(ValueError, match="split_payload_sha256_mismatch"):
        FrozenSuiteSplit.model_validate(bad_sha)
