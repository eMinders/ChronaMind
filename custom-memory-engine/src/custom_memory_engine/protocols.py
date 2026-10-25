"""Validated, deterministic P1 protocol primitives (no provider calls or test-data access)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import chain
from math import expm1, isclose, isfinite, log
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ReplayTurn(BaseModel):
    """One turn with its source timestamp preserved for provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    turn_id: str = Field(min_length=1)
    source_timestamp: AwareDatetime


class ReplaySession(BaseModel):
    """A source session; turn order is preserved within each session."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    turns: tuple[ReplayTurn, ...]


class ScheduledTurn(BaseModel):
    """Source timestamp and distinct strictly increasing recorded clock."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn: ReplayTurn
    recorded_clock: AwareDatetime


def schedule_replay(sessions: Sequence[ReplaySession]) -> tuple[ScheduledTurn, ...]:
    """Stable-sort sessions; reject decreasing turn dates; advance ties by 1 microsecond.

    Source times never change. Call separately per user/history. The monotonic
    recorded clock may advance beyond source time after a run of tied turns.
    """
    result: list[ScheduledTurn] = []
    previous_source: datetime | None = None
    previous_clock: datetime | None = None
    seen_ids: set[str] = set()
    session_ids = [session.session_id for session in sessions]
    if len(set(session_ids)) != len(session_ids):
        raise ValueError("duplicate_session_id")
    for session in sorted(sessions, key=lambda item: item.timestamp.astimezone(UTC)):
        session_time = session.timestamp.astimezone(UTC)
        for turn in session.turns:
            source = turn.source_timestamp.astimezone(UTC)
            if turn.turn_id in seen_ids:
                raise ValueError("duplicate_turn_id")
            if source < session_time:
                raise ValueError("turn_timestamp_precedes_session_timestamp")
            if previous_source is not None and source < previous_source:
                raise ValueError("non_monotonic_source_timestamp")
            clock = source
            if previous_clock is not None and clock <= previous_clock:
                clock = previous_clock + timedelta(microseconds=1)
            result.append(ScheduledTurn(session_id=session.session_id, turn=turn, recorded_clock=clock))
            seen_ids.add(turn.turn_id)
            previous_source, previous_clock = source, clock
    return tuple(result)


def reference_time(question_date: datetime, turns: Sequence[ScheduledTurn]) -> datetime:
    """Clamp reference time to the last recorded instant, including R3 tie increments."""
    if question_date.tzinfo is None or question_date.utcoffset() is None:
        raise ValueError("timezone_required")
    question_date = question_date.astimezone(UTC)
    return max(question_date, max((turn.recorded_clock for turn in turns), default=question_date))


def zero_leak_upper(scenarios: int, tail_probability: float = 0.025) -> float:
    """Exact zero-event Clopper–Pearson upper bound; units are independent scenarios."""
    if isinstance(scenarios, bool) or not isinstance(scenarios, int) or scenarios < 1:
        raise ValueError("positive_integer_scenario_count_required")
    if not isfinite(tail_probability) or not 0 < tail_probability < 1:
        raise ValueError("invalid_tail_probability")
    return -expm1(log(tail_probability) / scenarios)


class Agreement(BaseModel):
    """Binary confusion matrix, accuracy, and Cohen's kappa."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    sample_size: int = Field(ge=1)
    true_negative: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    kappa: float | None = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def validate_counts_and_metrics(self) -> "Agreement":
        """Reject inconsistent externally supplied metrics before they reach the gate."""
        tn, fp, fn, tp = self.true_negative, self.false_positive, self.false_negative, self.true_positive
        if tn + fp + fn + tp != self.sample_size:
            raise ValueError("sample_size_confusion_matrix_mismatch")
        accuracy, kappa = _agreement_metrics(tn, fp, fn, tp)
        if not isclose(self.accuracy, accuracy, rel_tol=0, abs_tol=1e-12):
            raise ValueError("accuracy_confusion_matrix_mismatch")
        if (self.kappa is None) != (kappa is None):
            raise ValueError("kappa_confusion_matrix_mismatch")
        if self.kappa is not None and kappa is not None:
            if not isclose(self.kappa, kappa, rel_tol=0, abs_tol=1e-12):
                raise ValueError("kappa_confusion_matrix_mismatch")
        return self


def _agreement_metrics(tn: int, fp: int, fn: int, tp: int) -> tuple[float, float | None]:
    """Derive metrics using integer arithmetic until final division to avoid cancellation."""
    n = tn + fp + fn + tp
    expected_numerator = (tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)
    denominator = n * n - expected_numerator
    kappa = None if denominator == 0 else ((tn + tp) * n - expected_numerator) / denominator
    return (tn + tp) / n, kappa


def binary_agreement(gold: Sequence[int], labels: Sequence[int]) -> Agreement:
    """Compute agreement; constant identical labels have undefined kappa (fail closed)."""
    if not gold or len(gold) != len(labels):
        raise ValueError("equal_nonempty_label_sequences_required")
    if any(type(label) is not int or label not in (0, 1) for label in chain(gold, labels)):
        raise ValueError("binary_integer_labels_required")
    tn = fp = fn = tp = 0
    for expected, actual in zip(gold, labels, strict=True):
        if expected == actual == 0:
            tn += 1
        elif expected == actual == 1:
            tp += 1
        elif expected == 0:
            fp += 1
        else:
            fn += 1
    n = len(gold)
    accuracy, kappa = _agreement_metrics(tn, fp, fn, tp)
    return Agreement(
        sample_size=n,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        true_positive=tp,
        accuracy=accuracy,
        kappa=kappa,
    )


def judge_gate(
    agreement: Agreement,
    *,
    split: Literal["dev", "test"],
    approvals_complete: bool,
    inter_human: Agreement | None = None,
    modes_covered: bool = True,
    perturbation_checked: bool = True,
) -> bool:
    """Numeric gate is necessary, not sufficient: blind audit and approvals also required."""
    inter_human_ok = True
    if inter_human is not None:
        inter_human_ok = (
            inter_human.true_negative + inter_human.false_positive > 0
            and inter_human.true_positive + inter_human.false_negative > 0
            and inter_human.sample_size >= 100
            and inter_human.kappa is not None
            and inter_human.kappa >= 0.75
        )
    return (
        split == "dev"
        and approvals_complete is True
        and modes_covered is True
        and perturbation_checked is True
        and inter_human_ok
        and agreement.true_negative + agreement.false_positive > 0
        and agreement.true_positive + agreement.false_negative > 0
        and agreement.sample_size >= 100
        and agreement.kappa is not None
        and agreement.kappa >= 0.75
        and agreement.accuracy >= 0.90
    )
