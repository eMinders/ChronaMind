"""Synthetic LoCoMo schema/replay checks; no public benchmark content is read."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from evaluation.harness.models import IngestEvent, QuestionEvent
from research.benchmarks.locomo.adapter import parse_session_timestamp, prepare_conversation


def synthetic_conversation() -> dict[str, Any]:
    """Independently authored text and annotations, deliberately unsorted sessions."""
    return {
        "sample_id": "synthetic-one",
        "conversation": {
            "speaker_a": "Avery",
            "speaker_b": "Casey",
            "session_2": [{"speaker": "Casey", "dia_id": "D2:1", "text": "The route includes cedar."}],
            "session_2_date_time": "9:00 am on 1 May, 2023",
            "session_1": [
                {"speaker": "Avery", "dia_id": "D1:1", "text": "I own a silver kite."},
                {"speaker": "Casey", "dia_id": "D1:2", "text": "I prefer amber maps."},
            ],
            "session_1_date_time": "9:00 am on 2 May, 2023",
        },
        "qa": [
            {"question": "What object does Avery own?", "answer": "silver kite", "category": 2, "evidence": ["D1:1"]},
            {
                "question": "What is Casey's unstated code?",
                "adversarial_answer": "a decoy",
                "category": 5,
                "evidence": [],
            },
        ],
    }


def test_two_humans_are_preserved_as_speakers_without_fictitious_assistant() -> None:
    raw = synthetic_conversation()
    before = deepcopy(raw)
    prepared = prepare_conversation(raw)
    turns = [event.turn for event in prepared.case.events if isinstance(event, IngestEvent)]
    assert [turn.content for turn in turns] == [
        "The route includes cedar.",
        "I own a silver kite.",
        "I prefer amber maps.",
    ]
    assert [turn.source_speaker for turn in turns] == ["Casey", "Avery", "Casey"]
    assert {turn.role for turn in turns} == {"user"}
    assert prepared.case.user_ids == (turns[0].user_id,)
    assert raw == before


def test_source_times_stable_sort_and_tie_clock_increments_are_separate() -> None:
    prepared = prepare_conversation(synthetic_conversation())
    ingests = [event for event in prepared.case.events if isinstance(event, IngestEvent)]
    earliest = datetime(2023, 5, 1, 9, tzinfo=UTC)
    latest = datetime(2023, 5, 2, 9, tzinfo=UTC)
    assert [event.turn.occurred_at for event in ingests] == [earliest, latest, latest]
    assert [event.clock for event in ingests] == [earliest, latest, latest + timedelta(microseconds=1)]
    questions = [event for event in prepared.case.events if isinstance(event, QuestionEvent)]
    assert len(questions) == 2
    assert all(event.clock == latest + timedelta(microseconds=1) for event in questions)
    assert len(prepared.audit.clock_adjustments) == 1
    adjustment = prepared.audit.clock_adjustments[0]
    assert adjustment.source_time == latest
    assert adjustment.recorded_time == latest + timedelta(microseconds=1)


def test_equal_session_dates_follow_numeric_source_session_order() -> None:
    raw = synthetic_conversation()
    raw["conversation"]["session_2_date_time"] = raw["conversation"]["session_1_date_time"]
    prepared = prepare_conversation(raw)
    turns = [event.turn for event in prepared.case.events if isinstance(event, IngestEvent)]
    assert [turn.turn_id.rsplit(":", 2)[-2:] for turn in turns] == [["D1", "1"], ["D1", "2"], ["D2", "1"]]
    assert [turn.source_speaker for turn in turns] == ["Avery", "Casey", "Casey"]


def test_evidence_maps_exact_dialogue_ids_and_gold_is_not_in_adapter_inputs() -> None:
    raw = synthetic_conversation()
    raw["qa"][0]["answer"] = "ANNOTATION_ONLY_UNSPOKEN"
    raw["qa"][0]["evidence"] = ["(D1:1)", "D1:1", " D2:1 "]
    prepared = prepare_conversation(raw)
    assert len(prepared.gold[0].gold_turn_ids) == 2
    assert prepared.gold[0].gold_turn_ids[0].endswith(":D1:1")
    assert prepared.gold[0].gold_turn_ids[1].endswith(":D2:1")
    serialized_inputs = prepared.case.model_dump_json()
    assert "ANNOTATION_ONLY_UNSPOKEN" not in serialized_inputs
    assert "adversarial_answer" not in serialized_inputs
    assert "gold_turn_ids" not in serialized_inputs
    assert prepared.gold[0].category == 2
    assert prepared.gold[0].primary_recall_eligible is False
    assert prepared.gold[1].abstention is True
    assert prepared.gold[1].gold_turn_ids == ()


def test_question_ids_and_tenants_are_isolated_between_conversations() -> None:
    first = prepare_conversation(synthetic_conversation())
    second_raw = synthetic_conversation()
    second_raw["sample_id"] = "synthetic-two"
    second = prepare_conversation(second_raw)
    assert first.case.cluster_id != second.case.cluster_id
    assert set(first.case.user_ids).isdisjoint(second.case.user_ids)
    assert {gold.question_id for gold in first.gold}.isdisjoint(gold.question_id for gold in second.gold)
    assert set(first.gold[0].gold_turn_ids).isdisjoint(second.gold[0].gold_turn_ids)


@pytest.mark.parametrize("category", [1, 2, 3, 4, 5])
def test_numeric_categories_are_preserved_without_invented_names(category: int) -> None:
    raw = synthetic_conversation()
    raw["qa"] = [{"question": "A synthetic probe?", "answer": "a value", "category": category, "evidence": []}]
    prepared = prepare_conversation(raw)
    assert prepared.gold[0].category == category
    assert prepared.gold[0].abstention is (category == 5)
    question = next(event.question for event in prepared.case.events if isinstance(event, QuestionEvent))
    assert question.stratum == f"category_{category}"


def test_empty_evidence_is_kept_distinct_from_unknown_evidence() -> None:
    raw = synthetic_conversation()
    raw["qa"][0]["evidence"] = []
    assert prepare_conversation(raw).gold[0].gold_turn_ids == ()
    raw["qa"][0]["evidence"] = ["D8:99"]
    assert prepare_conversation(raw).gold[0].unresolved_evidence_ids == ("D8:99",)


@pytest.mark.parametrize(
    "date,expected",
    [
        ("12:00 am on 1 January, 2024", datetime(2024, 1, 1, tzinfo=UTC)),
        ("12:00 pm on 1 January, 2024", datetime(2024, 1, 1, 12, tzinfo=UTC)),
        ("1:02 pm on 29 February, 2024", datetime(2024, 2, 29, 13, 2, tzinfo=UTC)),
    ],
)
def test_documented_timestamp_format_has_deterministic_utc_interpretation(date: str, expected: datetime) -> None:
    assert parse_session_timestamp(date) == expected


@pytest.mark.parametrize(
    "date",
    [
        "2024-01-01",
        "13:00 pm on 1 May, 2023",
        "0:00 am on 1 May, 2023",
        "1:60 pm on 1 May, 2023",
        "1:00 pm on 29 February, 2023",
        "1:00 pm on 1 Bogus, 2023",
    ],
)
def test_invalid_or_unspecified_timestamp_formats_are_rejected(date: str) -> None:
    with pytest.raises(ValueError, match="session_timestamp"):
        parse_session_timestamp(date)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_id", ""),
        ("qa", []),
        ("qa", "not an array"),
        ("conversation", []),
    ],
)
def test_invalid_top_level_shapes_fail_without_mutation(field: str, value: Any) -> None:
    raw = synthetic_conversation()
    raw[field] = value
    before = deepcopy(raw)
    with pytest.raises(ValueError):
        prepare_conversation(raw)
    assert raw == before


@pytest.mark.parametrize(
    "change",
    [
        "missing_session_date",
        "malformed_session_key",
        "duplicate_speakers",
        "unknown_speaker",
        "duplicate_dialogue_id",
        "wrong_session_dialogue_id",
        "missing_text",
        "nonlist_evidence",
        "nonstr_evidence",
        "invalid_category",
        "bool_category",
        "empty_question",
    ],
)
def test_ambiguous_session_turn_or_question_records_are_rejected(change: str) -> None:
    raw = synthetic_conversation()
    conversation = raw["conversation"]
    if change == "missing_session_date":
        del conversation["session_1_date_time"]
    elif change == "malformed_session_key":
        conversation["session_bad"] = []
    elif change == "duplicate_speakers":
        conversation["speaker_b"] = conversation["speaker_a"]
    elif change == "unknown_speaker":
        conversation["session_1"][0]["speaker"] = "Unknown"
    elif change == "duplicate_dialogue_id":
        conversation["session_1"][1]["dia_id"] = "D1:1"
    elif change == "wrong_session_dialogue_id":
        conversation["session_1"][0]["dia_id"] = "D2:3"
    elif change == "missing_text":
        del conversation["session_1"][0]["text"]
    elif change == "nonlist_evidence":
        raw["qa"][0]["evidence"] = "D1:1"
    elif change == "nonstr_evidence":
        raw["qa"][0]["evidence"] = [17]
    elif change == "invalid_category":
        raw["qa"][0]["category"] = 6
    elif change == "bool_category":
        raw["qa"][0]["category"] = True
    elif change == "empty_question":
        raw["qa"][0]["question"] = " "
    with pytest.raises(ValueError):
        prepare_conversation(raw)


def test_unpaired_date_metadata_does_not_invent_missing_dialogue() -> None:
    raw = synthetic_conversation()
    raw["conversation"]["session_9_date_time"] = "9:00 am on 9 May, 2023"
    prepared = prepare_conversation(raw)
    assert len([e for e in prepared.case.events if isinstance(e, IngestEvent)]) == 3
    assert prepared.audit.unpaired_session_dates == ("session_9_date_time",)


def test_compound_evidence_resolves_known_units_and_retains_unresolved_annotations() -> None:
    raw = synthetic_conversation()
    raw["qa"][0]["evidence"] = ["D1:1; D1:2", "D2:1 D1:1", "D1:99", "D"]
    prepared = prepare_conversation(raw)
    assert len(prepared.gold[0].gold_turn_ids) == 3
    assert prepared.gold[0].unresolved_evidence_ids == ("D1:99", "D")
    assert "unresolved_evidence_ids" not in prepared.case.model_dump_json()
