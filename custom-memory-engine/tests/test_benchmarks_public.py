"""Verification tests for RL-020 (LongMemEval) and RL-021 (LoCoMo) public benchmarks."""

from research.benchmarks.locomo.fetch import (
    EXPECTED_CATEGORY_COUNTS,
    ensure_locomo_file,
    verify_locomo_file,
)
from research.benchmarks.locomo.fetch import (
    load_manifest as load_locomo_manifest,
)
from research.benchmarks.longmemeval.fetch import (
    DATA_DIR as LME_DATA_DIR,
)
from research.benchmarks.longmemeval.fetch import (
    VARIANT_FILES,
    audit_longmemeval_records,
    build_replay_schedule_for_item,
    ensure_variant_file,
    verify_longmemeval_file,
)
from research.benchmarks.longmemeval.fetch import (
    load_manifest as load_lme_manifest,
)


def test_longmemeval_manifest_and_oracle_s_verification() -> None:
    manifest = load_lme_manifest()
    assert set(manifest.keys()) == {
        "longmemeval_oracle.json",
        "longmemeval_s_cleaned.json",
        "longmemeval_m_cleaned.json",
    }

    oracle_path = ensure_variant_file("oracle", allow_download=False)
    oracle_summary = verify_longmemeval_file("oracle", file_path=oracle_path, verify_replay=True)
    assert oracle_summary.total_questions == 500
    assert oracle_summary.abstention_questions == 30
    assert oracle_summary.single_session_assistant_questions == 56
    assert oracle_summary.answerable_questions == 470
    assert oracle_summary.primary_recall_eligible_questions == 414
    assert oracle_summary.evidence_out_of_order_histories == 34
    assert oracle_summary.evidence_question_before_last_session_histories == 43
    assert oracle_summary.full_haystack_out_of_order_histories == 34
    assert oracle_summary.full_haystack_question_before_last_session_histories == 43
    assert oracle_summary.tied_session_timestamp_histories == 0
    assert oracle_summary.replay_schedules_verified == 500

    s_candidate = LME_DATA_DIR / VARIANT_FILES["s"]
    if s_candidate.exists():
        s_path = ensure_variant_file("s", allow_download=False)
        s_summary = verify_longmemeval_file("s", file_path=s_path, verify_replay=True)
        assert s_summary.total_questions == 500
        assert s_summary.abstention_questions == 30
        assert s_summary.single_session_assistant_questions == 56
        assert s_summary.answerable_questions == 470
        assert s_summary.primary_recall_eligible_questions == 414
        assert s_summary.evidence_out_of_order_histories == 39
        assert s_summary.evidence_question_before_last_session_histories == 44
        assert s_summary.full_haystack_out_of_order_histories == 211
        assert s_summary.full_haystack_question_before_last_session_histories == 76
        assert s_summary.tied_session_timestamp_histories == 95
        assert s_summary.replay_schedules_verified == 500


def test_longmemeval_d23_s_replay_schedule_and_timestamp_anomaly_audit() -> None:
    synthetic_s_item: dict[str, object] = {
        "question_id": "q_synth_s_01",
        "question_type": "temporal-reasoning",
        "question_date": "2023/05/10 (Wed) 10:00",
        "haystack_session_ids": ["filler_late", "ev_early", "filler_tie"],
        "haystack_dates": [
            "2023/05/20 (Sat) 14:00",
            "2023/05/01 (Mon) 09:00",
            "2023/05/01 (Mon) 09:00",
        ],
        "answer_session_ids": ["ev_early"],
        "haystack_sessions": [
            [{"role": "user", "content": "Filler session from a later date."}],
            [{"role": "user", "content": "Evidence session from May 1."}],
            [{"role": "user", "content": "Filler session tied at May 1 09:00."}],
        ],
    }
    ordered, ref_time = build_replay_schedule_for_item(synthetic_s_item)
    assert [t.session_id for t in ordered] == [
        "q_synth_s_01_s0001_ev_early",
        "q_synth_s_01_s0002_filler_tie",
        "q_synth_s_01_s0000_filler_late",
    ]
    assert ref_time.isoformat() == "2023-05-20T14:00:00+00:00"

    audit = audit_longmemeval_records(
        [synthetic_s_item],
        variant="s",
        filename="longmemeval_s_cleaned.json",
        sha256="0" * 64,
        verify_replay=True,
    )
    assert audit.full_haystack_out_of_order_histories == 1
    assert audit.full_haystack_question_before_last_session_histories == 1
    assert audit.tied_session_timestamp_histories == 1
    assert audit.replay_schedules_verified == 1


def test_locomo_manifest_and_counts_verification() -> None:
    manifest = load_locomo_manifest()
    assert manifest == {
        "locomo10.json": "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4",
    }
    locomo_path = ensure_locomo_file(allow_download=False)
    summary = verify_locomo_file(file_path=locomo_path)
    assert summary.conversations == 10
    assert summary.total_questions == 1986
    assert summary.empty_evidence_questions == 4
    assert summary.category_counts == EXPECTED_CATEGORY_COUNTS
    assert sum(summary.category_counts.values()) == 1986
    assert summary.license == "CC-BY-NC-4.0"
