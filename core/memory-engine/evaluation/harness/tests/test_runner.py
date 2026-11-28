"""Exercise actual file output/replay: no model calls, benchmark assets or research runs."""

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import pytest

from evaluation.harness.config import HarnessConfig
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.models import (
    AnswerResult,
    EvaluationCase,
    IngestEvent,
    QuestionEvent,
    QuestionRecord,
    TurnRecord,
    Usage,
)
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_config_contract import config_data


def fixture_case(cluster: str = "scenario-1", user: str = "user-1") -> EvaluationCase:
    first = datetime(2020, 1, 1, tzinfo=UTC)
    second = datetime(2020, 1, 2, tzinfo=UTC)
    return EvaluationCase(
        cluster_id=cluster,
        user_ids=(user,),
        events=(
            IngestEvent(
                clock=first,
                turn=TurnRecord(
                    user_id=user,
                    session_id="session",
                    turn_id="turn-1",
                    role="user",
                    content="fictional café α",
                    occurred_at=first,
                ),
            ),
            QuestionEvent(
                clock=second,
                question=QuestionRecord(
                    question_id="question-1",
                    user_id=user,
                    question="Echo?",
                    stratum="present-state",
                ),
            ),
        ),
    )


def run_fixture(
    repo: Path,
    run_id: str = "first",
    *,
    factory: Callable = DummyAdapter,
    loader: Callable | None = None,
    timer: Callable | None = None,
) -> Path:
    split = repo / "dev.json"
    split.write_bytes(b'{"fixture":"public synthetic inputs only"}\n')
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    return run_evaluation(
        config=config,
        suite_id="T",
        split="dev",
        split_path=split,
        case_loader=loader or (lambda: (fixture_case(),)),
        adapter_factory=factory,
        scorer=dummy_score,
        run_id=run_id,
        output_root=repo / "runs",
        repo_root=repo,
        timer=timer or perf_counter,
    )


def test_two_real_runs_have_identical_artifacts_and_volatile_manifest_timings(repo: Path) -> None:
    first_times = iter(float(i) for i in range(20))
    second_times = iter(float(i * 2) for i in range(20))
    first = run_fixture(repo, "first", timer=lambda: next(first_times))
    second = run_fixture(repo, "second", timer=lambda: next(second_times))
    for name in ("answers.jsonl", "scores.jsonl", "per_cluster.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
        assert b"\r" not in (first / name).read_bytes()
    manifest = json.loads((first / "manifest.json").read_bytes())
    assert (
        manifest["commit_sha"]
        == subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    )
    assert manifest["dirty_tree"] is True
    assert manifest["dataset_split_sha256"] == hashlib.sha256((repo / "dev.json").read_bytes()).hexdigest()
    assert manifest["configuration"]["model_id"] == "fixture-answer-v1"
    assert manifest["seed"] == 42
    assert manifest["timing_ms"]["retrieval_operation_wall"] == {"p50": 1000.0, "p95": 1000.0}
    assert manifest["usage"]["cost_usd"] == "0"
    assert manifest["usage"]["input_tokens"] == 0
    assert manifest["campaign_budget"]["unknown_operations"] == 0
    assert (
        manifest["artifact_sha256"]["answers.jsonl"]
        == hashlib.sha256((first / "answers.jsonl").read_bytes()).hexdigest()
    )
    assert manifest["timing_ms"] != json.loads((second / "manifest.json").read_bytes())["timing_ms"]


def test_existing_run_is_immutable_before_adapter_construction(repo: Path) -> None:
    destination = run_fixture(repo)
    old = (destination / "answers.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        run_fixture(repo, factory=lambda config: pytest.fail("adapter created on duplicate run"))
    assert (destination / "answers.jsonl").read_bytes() == old


def test_adapter_failure_has_no_success_manifest_and_charges_unknown_usage(repo: Path) -> None:
    class Failing(DummyAdapter):
        def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
            raise RuntimeError("private-content-not-for-failure-marker")

    with pytest.raises(RuntimeError):
        run_fixture(repo, factory=Failing)
    output = repo / "runs/first/dummy/T"
    assert not (output / "manifest.json").exists()
    assert "private-content" not in (output / "FAILED.json").read_text(encoding="utf-8")
    state = json.loads((repo / "runs/.budgets/fixture-campaign/ledger.json").read_bytes())
    assert state["unknown_operations"] == state["failed_operations"] == 1


def test_optional_adapter_resources_close_before_success_publication(repo: Path) -> None:
    observed = []

    class Closing(DummyAdapter):
        def close(self) -> None:
            observed.append((repo / "runs/first/dummy/T/manifest.json").exists())

    destination = run_fixture(repo, factory=Closing)
    assert observed == [False]
    assert (destination / "manifest.json").exists()


def test_adapter_cleanup_failure_prevents_success_publication(repo: Path) -> None:
    class Closing(DummyAdapter):
        def close(self) -> None:
            raise RuntimeError("private cleanup diagnostic")

    with pytest.raises(RuntimeError, match="private cleanup diagnostic"):
        run_fixture(repo, factory=Closing)
    destination = repo / "runs/first/dummy/T"
    assert not (destination / "manifest.json").exists()
    assert "private cleanup" not in (destination / "FAILED.json").read_text()


def test_replay_failure_still_closes_optional_adapter_resources(repo: Path) -> None:
    closed = []

    class Failing(DummyAdapter):
        def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
            raise RuntimeError("synthetic answer failure")

        def close(self) -> None:
            closed.append(True)

    with pytest.raises(RuntimeError, match="synthetic answer failure"):
        run_fixture(repo, factory=Failing)
    assert closed == [True]


def test_reset_and_replay_do_not_mix_user_stores(repo: Path) -> None:
    path = run_fixture(repo, loader=lambda: (fixture_case("one", "one"), fixture_case("two", "two")))
    rows = [json.loads(line) for line in (path / "answers.jsonl").read_bytes().splitlines()]
    assert [row["user_id"] for row in rows] == ["one", "two"]
    assert [row["cluster_id"] for row in rows] == ["one", "two"]


def test_exact_usage_aggregation_preserves_decimal_tail() -> None:
    first = Usage(
        input_tokens=0, output_tokens=0, embedding_tokens=0, cost_usd=Decimal("0.999999999999999999999999999999")
    )
    second = Usage(
        input_tokens=0, output_tokens=0, embedding_tokens=0, cost_usd=Decimal("0.000000000000000000000000000002")
    )
    assert first.plus(second).cost_usd == Decimal("1.000000000000000000000000000001")
