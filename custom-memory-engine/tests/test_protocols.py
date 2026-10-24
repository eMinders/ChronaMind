"""P1 arithmetic, replay, and judge-gate regressions."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from custom_memory_engine.protocols import (
    Agreement,
    ReplaySession,
    ReplayTurn,
    binary_agreement,
    judge_gate,
    reference_time,
    schedule_replay,
    zero_leak_upper,
)

T = datetime(2026, 1, 1, tzinfo=UTC)


def session(name: str, stamp: datetime, *turn_times: datetime) -> ReplaySession:
    return ReplaySession(
        session_id=name,
        timestamp=stamp,
        turns=tuple(
            ReplayTurn(turn_id=f"{name}-{index}", source_timestamp=time) for index, time in enumerate(turn_times)
        ),
    )


def test_stable_session_sort_and_tie_increment() -> None:
    turns = schedule_replay(
        [
            session("late", T + timedelta(days=1), T + timedelta(days=1)),
            session("first", T, T, T),
            session("second", T, T),
        ]
    )
    assert [turn.session_id for turn in turns] == ["first", "first", "second", "late"]
    assert [turn.recorded_clock for turn in turns[:3]] == [T + timedelta(microseconds=i) for i in range(3)]
    assert all(turn.turn.source_timestamp == T for turn in turns[:3])
    assert reference_time(T, turns) == T + timedelta(days=1)
    assert reference_time(T, turns[:3]) == T + timedelta(microseconds=2)


def test_replay_guards() -> None:
    with pytest.raises(ValueError, match="non_monotonic"):
        schedule_replay([session("bad", T, T + timedelta(seconds=1), T)])
    item = session("one", T, T)
    with pytest.raises(ValueError, match="duplicate_session_id"):
        schedule_replay([item, item])
    with pytest.raises(ValueError, match="duplicate_turn_id"):
        schedule_replay([item, ReplaySession(session_id="two", timestamp=T, turns=item.turns)])
    with pytest.raises(ValidationError):
        ReplayTurn(turn_id="naive", source_timestamp=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone"):
        reference_time(datetime(2026, 1, 1), [])
    assert schedule_replay([]) == ()
    assert reference_time(T, []) == T


@pytest.mark.parametrize(("n", "expected"), [(20, 0.1684), (36, 0.0974), (50, 0.0711), (60, 0.0596), (72, 0.0499)])
def test_exact_bounds(n: int, expected: float) -> None:
    assert round(zero_leak_upper(n), 4) == expected
    assert zero_leak_upper(n) == pytest.approx(1 - 0.025 ** (1 / n))


@pytest.mark.parametrize("n", [0, -1, True, 1.5])
def test_invalid_cardinality(n: int) -> None:
    with pytest.raises(ValueError):
        zero_leak_upper(n)


@pytest.mark.parametrize("tail", [0, 1, -1, float("nan"), float("inf")])
def test_invalid_tail(tail: float) -> None:
    with pytest.raises(ValueError):
        zero_leak_upper(50, tail)


def test_judge_validation_and_fail_closed() -> None:
    gold = [0] * 50 + [1] * 50
    labels = [1] * 5 + [0] * 45 + [0] * 5 + [1] * 45
    agreement = binary_agreement(gold, labels)
    assert agreement.accuracy == 0.9
    assert agreement.kappa == pytest.approx(0.8)
    assert (agreement.true_negative, agreement.false_positive, agreement.false_negative, agreement.true_positive) == (
        45,
        5,
        5,
        45,
    )
    assert judge_gate(agreement, split="dev", approvals_complete=True)
    assert not judge_gate(agreement, split="test", approvals_complete=True)
    assert not judge_gate(agreement, split="dev", approvals_complete=False)
    constant = binary_agreement([1] * 100, [1] * 100)
    assert constant.kappa is None
    assert not judge_gate(constant, split="dev", approvals_complete=True)
    assert not judge_gate(binary_agreement([0, 1], [0, 1]), split="dev", approvals_complete=True)


@pytest.mark.parametrize(("gold", "labels"), [([], []), ([0], [0, 1]), ([2], [0]), ([True], [1])])
def test_invalid_labels(gold: list[int], labels: list[int]) -> None:
    with pytest.raises(ValueError):
        binary_agreement(gold, labels)


def test_equivalent_timezone_instants_have_stable_order() -> None:
    from_offset = T.astimezone(timezone(timedelta(hours=2)))
    turns = schedule_replay([session("offset", from_offset, from_offset), session("utc", T, T)])
    assert [turn.session_id for turn in turns] == ["offset", "utc"]
    assert [turn.recorded_clock for turn in turns] == [T, T + timedelta(microseconds=1)]


@pytest.mark.parametrize(
    "change",
    [
        {"sample_size": 101},
        {"true_negative": -1},
        {"accuracy": 1.0},
        {"kappa": 1.0},
        {"accuracy": float("nan")},
        {"kappa": float("inf")},
        {"sample_size": True},
        {"unrecognized": "ignored?"},
    ],
)
def test_inconsistent_or_nonfinite_agreement_rejected(change: dict[str, object]) -> None:
    data = binary_agreement([0] * 50 + [1] * 50, [0] * 45 + [1] * 5 + [0] * 5 + [1] * 45).model_dump()
    data.update(change)
    with pytest.raises(ValidationError):
        Agreement.model_validate(data)


def test_both_constant_label_distributions_and_complete_disagreement() -> None:
    assert binary_agreement([0] * 100, [0] * 100).kappa is None
    agreement = binary_agreement([0, 1] * 50, [1, 0] * 50)
    assert agreement.kappa == -1.0
    assert agreement.accuracy == 0.0
    assert not judge_gate(agreement, split="dev", approvals_complete=True)
