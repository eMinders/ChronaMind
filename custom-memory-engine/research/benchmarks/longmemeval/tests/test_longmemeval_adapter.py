"""Independent synthetic histories for the RL-025 LongMemEval source adapter."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evaluation.harness.models import IngestEvent, QuestionEvent
from research.benchmarks.longmemeval.adapter import LongMemEvalAdapter, prepare_item


def history() -> dict[str, Any]:
    """Entirely fictional schema fixture; no benchmark dialogue or question reuse."""
    return {
        "question_id": "fixture_q",
        "question_type": "temporal-reasoning",
        "question": "Which invented label belongs to the fictional test object?",
        "answer": "GOLD_ONLY_MARKER",
        "question_date": "2024/01/01 (Mon) 09:00",
        "haystack_session_ids": ["later", "earlier", "equal_time"],
        "haystack_dates": [
            "2024/01/03 (Wed) 10:00",
            "2024/01/01 (Mon) 09:00",
            "2024/01/01 (Mon) 09:00",
        ],
        "answer_session_ids": ["earlier"],
        "haystack_sessions": [
            [{"role": "user", "content": "A later invented observation."}],
            [
                {"role": "user", "content": "A fictional source statement.", "has_answer": True},
                {"role": "assistant", "content": "An invented acknowledgement."},
            ],
            [{"role": "user", "content": "A same-time fictional distractor."}],
        ],
    }


def test_stable_session_sort_preserves_turns_source_ids_and_input() -> None:
    raw = history()
    before = copy.deepcopy(raw)
    prepared = prepare_item(raw)
    turns = [event for event in prepared.case.events if isinstance(event, IngestEvent)]
    assert [event.turn.content for event in turns] == [
        "A fictional source statement.",
        "An invented acknowledgement.",
        "A same-time fictional distractor.",
        "A later invented observation.",
    ]
    assert [event.turn.role for event in turns] == ["user", "assistant", "user", "user"]
    first = datetime(2024, 1, 1, 9, tzinfo=UTC)
    assert [event.clock for event in turns[:3]] == [
        first,
        first + timedelta(microseconds=1),
        first + timedelta(microseconds=2),
    ]
    assert [event.turn.occurred_at for event in turns[:3]] == [first, first, first]
    assert turns[0].turn.session_id.endswith(":s0001")
    assert prepared.audit.original_session_ids == ("later", "earlier", "equal_time")
    assert prepared.audit.replay_session_ids == ("earlier", "equal_time", "later")
    assert len(prepared.audit.clock_adjustments) == 2
    assert raw == before


def test_reference_clock_includes_all_cumulative_tie_adjustments() -> None:
    raw = history()
    raw["haystack_dates"] = ["2024/01/01 (Mon) 09:00"] * 3
    prepared = prepare_item(raw)
    question = prepared.case.events[-1]
    assert isinstance(question, QuestionEvent)
    assert question.clock == datetime(2024, 1, 1, 9, tzinfo=UTC) + timedelta(microseconds=3)
    assert question.question.as_of_time == question.clock
    assert prepared.gold[0].original_question_date == datetime(2024, 1, 1, 9, tzinfo=UTC)
    assert prepared.gold[0].original_date_anomaly is False


def test_original_date_sensitivity_uses_raw_sessions_not_adjusted_ties() -> None:
    prepared = prepare_item(history())
    assert prepared.gold[0].original_date_anomaly is True
    question = prepared.case.events[-1]
    assert isinstance(question, QuestionEvent)
    assert question.clock == datetime(2024, 1, 3, 10, tzinfo=UTC)


def test_gold_labels_are_separate_from_every_adapter_visible_record() -> None:
    prepared = prepare_item(history())
    visible = prepared.case.model_dump_json()
    assert "GOLD_ONLY_MARKER" not in visible
    assert "has_answer" not in visible
    assert "answer_session_ids" not in visible
    assert prepared.gold[0].answer == "GOLD_ONLY_MARKER"
    gold_ids = prepared.gold[0].gold_turn_ids
    turns = [event.turn for event in prepared.case.events if isinstance(event, IngestEvent)]
    assert gold_ids == (turns[0].turn_id,)
    assert prepared.gold[0].question_id == prepared.case.events[-1].question.question_id  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("variant", "qtype", "qid", "eligible", "abstention"),
    [
        ("s", "multi-session", "ordinary", True, False),
        ("s", "single-session-assistant", "assistant", False, False),
        ("s", "multi-session", "fictional_abs", False, True),
        ("oracle", "multi-session", "ordinary", False, False),
        ("m", "multi-session", "ordinary", False, False),
    ],
)
def test_primary_population_and_abstention_are_explicit(
    variant: str, qtype: str, qid: str, eligible: bool, abstention: bool
) -> None:
    raw = history()
    raw.update(question_id=qid, question_type=qtype)
    prepared = prepare_item(raw, variant=variant)  # type: ignore[arg-type]
    assert prepared.gold[0].primary_recall_eligible is eligible
    assert prepared.gold[0].abstention is abstention
    assert variant in prepared.case.cluster_id


def test_assistant_gold_evidence_is_retained_for_diagnostics() -> None:
    raw = history()
    raw["question_type"] = "single-session-assistant"
    raw["haystack_sessions"][1][1]["has_answer"] = True
    prepared = prepare_item(raw)
    assert len(prepared.gold[0].gold_turn_ids) == 2
    assert prepared.gold[0].primary_recall_eligible is False


def test_missing_gold_evidence_does_not_silently_shrink_eligible_population() -> None:
    raw = history()
    raw["haystack_sessions"][1][0].pop("has_answer")
    prepared = prepare_item(raw)
    assert prepared.gold[0].primary_recall_eligible is True
    assert prepared.gold[0].gold_turn_ids == ()


def test_empty_history_uses_original_question_date() -> None:
    raw = history()
    for field in ("haystack_session_ids", "haystack_dates", "haystack_sessions", "answer_session_ids"):
        raw[field] = []
    prepared = prepare_item(raw)
    assert len(prepared.case.events) == 1
    assert prepared.case.events[0].clock == datetime(2024, 1, 1, 9, tzinfo=UTC)
    assert prepared.audit.replay_session_ids == ()


def test_history_and_variant_ids_are_isolated_and_collision_safe() -> None:
    raw = history()
    raw["question_id"] = "literal:special/id"
    first = prepare_item(raw)
    other_variant = prepare_item(raw, variant="oracle")
    other_history = prepare_item({**raw, "question_id": "different"})
    assert len({x.case.user_ids[0] for x in (first, other_variant, other_history)}) == 3
    first_ids = {e.turn.turn_id for e in first.case.events if isinstance(e, IngestEvent)}
    other_ids = {e.turn.turn_id for e in other_history.case.events if isinstance(e, IngestEvent)}
    assert first_ids.isdisjoint(other_ids)


@pytest.mark.parametrize("answer", ["text", 42, 2.5, Decimal("2.5")])
def test_scalar_gold_answers_are_preserved_as_text(answer: object) -> None:
    prepared = prepare_item({**history(), "answer": answer})
    assert prepared.gold[0].answer == str(answer)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("question_id", ""),
        ("question_id", 12),
        ("question", " "),
        ("question_type", "unregistered-kind"),
        ("question_date", "2024-01-01T09:00:00"),
        ("question_date", "2024/99/01 (Mon) 09:00"),
        ("haystack_session_ids", ["same", "same", "other"]),
        ("haystack_dates", ["2024/01/01 (Mon) 09:00"]),
        ("haystack_sessions", "not-an-array"),
        ("answer_session_ids", ["unknown-session"]),
        ("answer_session_ids", "earlier"),
        ("answer", True),
        ("answer", float("nan")),
        ("answer", None),
    ],
)
def test_invalid_shape_ids_dates_or_answer_rejected(field: str, invalid: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        prepare_item({**history(), field: invalid})


@pytest.mark.parametrize(
    "change",
    [
        {"role": "system"},
        {"content": 7},
        {"has_answer": "true"},
        {"timestamp": "not-a-time"},
        {"timestamp": "2023/12/31 (Sun) 09:00"},
    ],
)
def test_invalid_turn_or_turn_before_session_rejected(change: dict[str, object]) -> None:
    raw = history()
    raw["haystack_sessions"][1][0].update(change)
    with pytest.raises((ValueError, TypeError)):
        prepare_item(raw)


def test_turn_source_time_cannot_decrease_within_session() -> None:
    raw = history()
    raw["haystack_sessions"][1][0]["timestamp"] = "2024/01/01 (Mon) 10:00"
    raw["haystack_sessions"][1][1]["timestamp"] = "2024/01/01 (Mon) 09:00"
    with pytest.raises(ValueError, match="non_monotonic_source_timestamp"):
        prepare_item(raw)


def test_optional_turn_times_are_preserved_separately_from_recorded_clock() -> None:
    raw = history()
    raw["haystack_sessions"] = [[{"role": "user", "content": "Fiction.", "timestamp": "2024/01/03 (Wed) 11:00"}]]
    raw["haystack_session_ids"] = ["later"]
    raw["haystack_dates"] = ["2024/01/03 (Wed) 10:00"]
    raw["answer_session_ids"] = []
    prepared = prepare_item(raw)
    turn = prepared.case.events[0]
    assert isinstance(turn, IngestEvent)
    assert turn.turn.occurred_at == datetime(2024, 1, 3, 11, tzinfo=UTC)
    assert prepared.case.events[-1].clock == turn.clock


def test_unknown_variant_rejected_before_any_file_access(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown_variant"):
        LongMemEvalAdapter(tmp_path / "absent.json", variant="other")  # type: ignore[arg-type]
