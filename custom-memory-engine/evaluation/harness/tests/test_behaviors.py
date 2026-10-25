"""Observable runner contracts for timing, queues, usage, and clustered outputs."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.harness.budget import BudgetExceeded, BudgetLedger
from evaluation.harness.config import HarnessConfig
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.models import (
    AnswerResult,
    DeleteEvent,
    EvaluationCase,
    MetricScore,
    QuestionEvent,
    QuestionRecord,
    QueueDrainStats,
    RetrievalResult,
    ScoreResult,
    Usage,
)
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_config_contract import config_data
from evaluation.harness.tests.test_runner import fixture_case, run_fixture


def test_unsupported_capability_is_unscored_and_not_zero(repo: Path) -> None:
    case = fixture_case()
    ingest, probe = case.events
    assert isinstance(probe, QuestionEvent)
    question = probe.question.model_copy(update={"required_capabilities": ("deletion",)})
    deletion = DeleteEvent(clock=probe.clock, user_id="user-1", scope="memory", target_ref="fixture")
    changed = EvaluationCase(
        cluster_id=case.cluster_id,
        user_ids=case.user_ids,
        events=(ingest, deletion, probe.model_copy(update={"question": question})),
    )
    output = run_fixture(repo, loader=lambda: (changed,))
    score = json.loads((output / "scores.jsonl").read_bytes())
    cluster = json.loads((output / "per_cluster.jsonl").read_bytes())
    assert score["status"] == "not_supported" and score["metrics"] == []
    assert cluster["n_not_supported"] == 1 and cluster["metrics"] == []
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["not_supported_count"] == 1


def test_known_usage_over_reservation_stops_immediately_without_success(repo: Path) -> None:
    class OverBudget(DummyAdapter):
        def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
            return AnswerResult(
                answer="fixture",
                usage=Usage(
                    input_tokens=1,
                    output_tokens=0,
                    embedding_tokens=0,
                    cost_usd=Usage.zero().cost_usd,
                ),
            )

    with pytest.raises(BudgetExceeded, match="operation_exceeded_declared_reservation"):
        run_fixture(repo, factory=OverBudget)
    state = json.loads((repo / "runs/.budgets/fixture-campaign/ledger.json").read_bytes())
    assert state["breached"] and state["spent"]["input_tokens"] == 1
    assert not (repo / "runs/first/dummy/T/manifest.json").exists()


def test_nonempty_queue_never_reaches_retrieval_or_answer(repo: Path) -> None:
    class Pending(DummyAdapter):
        def drain_queue(self, user_id: str) -> QueueDrainStats:
            return QueueDrainStats(processed=0, pending=1, usage=Usage.zero())

        def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
            pytest.fail("answer ran with pending writes")

    with pytest.raises(ValueError, match="queue_not_drained"):
        run_fixture(repo, factory=Pending)


def test_duplicate_cluster_rejected_before_any_output_or_adapter(repo: Path) -> None:
    with pytest.raises(ValueError, match="duplicate_cluster_id"):
        run_fixture(
            repo,
            loader=lambda: (fixture_case(), fixture_case()),
            factory=lambda config: pytest.fail("adapter initialized before valid input"),
        )
    assert not (repo / "runs").exists()


def test_backwards_and_naive_replay_clocks_fail_before_replay() -> None:
    case = fixture_case()
    value = case.model_dump(mode="json")
    value["events"][1]["clock"] = "2019-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="decreasing_replay_clock"):
        EvaluationCase.model_validate_json(json.dumps(value))
    value["events"][1]["clock"] = "2020-01-02T00:00:00"
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate_json(json.dumps(value))


def test_rendered_context_budget_is_enforced(repo: Path) -> None:
    class ExcessContext(DummyAdapter):
        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            return RetrievalResult(context_tokens=token_budget + 1, usage=Usage.zero())

    with pytest.raises(ValueError, match="retrieved_context_exceeds_frozen_budget"):
        run_fixture(repo, factory=ExcessContext)


def test_cluster_strata_and_missing_metrics_remain_separate(repo: Path) -> None:
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    split = repo / "dev.json"
    split.write_bytes(b"synthetic dev membership only\n")
    case = fixture_case()
    first, probe = case.events
    assert isinstance(probe, QuestionEvent)
    history = QuestionEvent(
        clock=probe.clock,
        question=probe.question.model_copy(
            update={
                "question_id": "history",
                "stratum": "history-dependent",
            }
        ),
    )
    absent = QuestionEvent(clock=probe.clock, question=probe.question.model_copy(update={"question_id": "missing"}))
    expanded = EvaluationCase(
        cluster_id=case.cluster_id, user_ids=case.user_ids, events=(first, probe, history, absent)
    )

    def score(question, retrieval, answer) -> ScoreResult:
        return ScoreResult(
            metrics=(MetricScore(name="synthetic", value=None if question.question_id == "missing" else 1.0),)
        )

    output = run_evaluation(
        config=config,
        suite_id="T",
        split="dev",
        split_path=split,
        case_loader=lambda: (expanded,),
        adapter_factory=DummyAdapter,
        scorer=score,
        run_id="cluster-test",
        output_root=repo / "runs",
        repo_root=repo,
    )
    rows = [json.loads(line) for line in (output / "per_cluster.jsonl").read_bytes().splitlines()]
    assert [row["stratum"] for row in rows] == ["history-dependent", "present-state"]
    assert rows[0]["metrics"] == [{"name": "synthetic", "mean": 1.0, "n_missing": 0, "n_scored": 1}]
    assert rows[1]["metrics"] == [{"name": "synthetic", "mean": 1.0, "n_missing": 1, "n_scored": 1}]


def test_guard_denial_never_constructs_adapter_loads_data_or_creates_output(repo: Path) -> None:
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    with pytest.raises(ValueError, match="test_run_requires_confirmation_and_freeze"):
        run_evaluation(
            config=config,
            suite_id="T",
            split="test",
            split_path=repo / "unreadable-test.json",
            case_loader=lambda: pytest.fail("test split loader was called"),
            adapter_factory=lambda config: pytest.fail("adapter was constructed"),
            scorer=dummy_score,
            run_id="denied",
            output_root=repo / "runs",
            repo_root=repo,
        )
    assert not (repo / "runs").exists()


def test_ledger_io_is_outside_operation_timing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    reserve = BudgetLedger.reserve
    reconcile = BudgetLedger.reconcile

    def slow_reserve(self, maximum, split):
        now[0] += 50.0
        return reserve(self, maximum, split)

    def slow_reconcile(self, reservation_id, actual, *, failed=False):
        now[0] += 50.0
        return reconcile(self, reservation_id, actual, failed=failed)

    monkeypatch.setattr(BudgetLedger, "reserve", slow_reserve)
    monkeypatch.setattr(BudgetLedger, "reconcile", slow_reconcile)
    output = run_fixture(repo, timer=lambda: now[0])
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["wall_clock_duration_ms"] > 0
    assert manifest["timing_ms"]["retrieval_operation_wall"] == {"p50": 0.0, "p95": 0.0}
