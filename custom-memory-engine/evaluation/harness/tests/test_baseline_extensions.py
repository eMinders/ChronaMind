"""Real runner/file contracts for baseline extensions; no models, DB or corpora."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from evaluation.harness.artifacts import ArtifactWriter
from evaluation.harness.budget import AccountedOperationError, BudgetExceeded, BudgetLedger
from evaluation.harness.config import HarnessConfig
from evaluation.harness.dummy import DummyAdapter
from evaluation.harness.models import (
    AnswerResult,
    EvaluationCase,
    IngestReceipt,
    MetricScore,
    QuestionEvent,
    QuestionRecord,
    RetrievalDiagnostics,
    RetrievalResult,
    ScoreResult,
    TurnRecord,
    Usage,
    apply_metric_capabilities,
)
from evaluation.harness.runner import _dispatch, run_evaluation
from evaluation.harness.tests.test_config_contract import config_data
from evaluation.harness.tests.test_runner import fixture_case, run_fixture


class BaselineFixture(DummyAdapter):
    """Only relax the dummy's identity guard for controlled configuration tests."""

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config.model_copy(update={"system_id": "fixture-dummy", "adapter_version": "fixture-v1"}))
        self.config = config


def run_extension(
    repo: Path,
    *,
    changes: dict[str, object] | None = None,
    factory: Callable = BaselineFixture,
    scorer: Callable | None = None,
    case: EvaluationCase | None = None,
) -> Path:
    data = {**config_data(), **(changes or {})}
    config = HarnessConfig.model_validate_json(json.dumps(data))
    split = repo / "dev.json"
    split.write_bytes(b"synthetic baseline extension membership\n")
    return run_evaluation(
        config=config,
        suite_id="T",
        split="dev",
        split_path=split,
        case_loader=lambda: (case or fixture_case(),),
        adapter_factory=factory,
        scorer=scorer or (lambda q, r, a: ScoreResult(metrics=())),
        run_id="extension",
        output_root=repo / "runs",
        repo_root=repo,
    )


def test_source_speaker_and_optional_question_session_are_explicit() -> None:
    case = fixture_case()
    turn = case.events[0].model_dump(mode="json")["turn"]
    turn["source_speaker"] = "fictional speaker"
    assert TurnRecord.model_validate_json(json.dumps(turn)).source_speaker == "fictional speaker"
    probe = case.events[-1]
    assert isinstance(probe, QuestionEvent)
    data = probe.question.model_dump(mode="json")
    assert QuestionRecord.model_validate_json(json.dumps(data)).session_id is None
    assert QuestionRecord.model_validate_json(json.dumps({**data, "session_id": "new-session"})).session_id == (
        "new-session"
    )
    for blank in ("", " ", "\t"):
        with pytest.raises(ValidationError):
            QuestionRecord.model_validate_json(json.dumps({**data, "session_id": blank}))


def test_prepare_question_and_answer_receive_no_reporting_stratum(repo: Path) -> None:
    trace: list[str] = []

    class Prepared(BaselineFixture):
        def prepare_question(self, question: QuestionRecord) -> None:
            assert question.stratum is None
            assert question.session_id == "fresh-session"
            trace.append("prepare")

        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            assert trace == ["prepare"]
            trace.append("retrieve")
            return RetrievalResult(context_tokens=0, usage=Usage.zero())

        def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
            assert question.stratum is None
            assert question.session_id == "fresh-session"
            trace.append("answer")
            return AnswerResult(answer="fixture", usage=Usage.zero())

    case = fixture_case()
    value = case.model_dump(mode="json")
    value["events"][-1]["question"]["session_id"] = "fresh-session"
    case = EvaluationCase.model_validate_json(json.dumps(value))

    def score(question, retrieval, answer) -> ScoreResult:
        assert question.stratum == "present-state"
        return ScoreResult(metrics=())

    output = run_extension(repo, factory=Prepared, case=case, scorer=score)
    assert trace == ["prepare", "retrieve", "answer"]
    assert json.loads((output / "answers.jsonl").read_bytes())["stratum"] == "present-state"


@pytest.mark.parametrize(("system", "policy"), [("B0", "current_session"), ("B1", "full_history")])
def test_history_context_can_exceed_memory_budget_but_not_model_window(repo: Path, system: str, policy: str) -> None:
    class History(BaselineFixture):
        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            return RetrievalResult(rendered_context="visible history", context_tokens=600, usage=Usage.zero())

        def answer(self, user_id, question, as_of) -> AnswerResult:
            return AnswerResult(answer="fixture", usage=Usage.zero())

    output = run_extension(
        repo,
        changes={
            "system_id": system,
            "context_policy": policy,
            "context_window_tokens": 700,
            "answer_output_tokens": 100,
        },
        factory=History,
    )
    row = json.loads((output / "answers.jsonl").read_bytes())
    assert row["context_tokens"] == 600 and row["rendered_context"] == "visible history"


def test_history_context_over_window_aborts_before_answer(repo: Path) -> None:
    class Overflow(BaselineFixture):
        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            return RetrievalResult(context_tokens=601, usage=Usage.zero())

        def answer(self, user_id, question, as_of) -> AnswerResult:
            pytest.fail("answer must not see context above reserved window")

    with pytest.raises(ValueError, match="history_context_exceeds_model_window"):
        run_extension(
            repo,
            changes={
                "system_id": "B1",
                "context_policy": "full_history",
                "context_window_tokens": 700,
                "answer_output_tokens": 100,
            },
            factory=Overflow,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"system_id": "B0"},
        {"system_id": "B1"},
        {"system_id": "B2", "context_policy": "full_history", "context_window_tokens": 4000},
        {"system_id": "B1", "context_policy": "current_session", "context_window_tokens": 4000},
        {"system_id": "B0", "context_policy": "current_session"},
        {"system_id": "B1", "context_policy": "full_history", "context_window_tokens": 2048},
        {"answer_output_tokens": 0},
        {"adapter_configuration_sha256": "not-a-digest"},
    ],
)
def test_invalid_context_policy_configuration_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate_json(json.dumps({**config_data(), **changes}))


def test_new_config_fields_participate_in_execution_identity() -> None:
    first = HarnessConfig.model_validate_json(json.dumps(config_data()))
    for changes in (
        {"answer_output_tokens": 1024},
        {"context_window_tokens": 8192},
        {"adapter_configuration_sha256": "a" * 64},
    ):
        changed = HarnessConfig.model_validate_json(json.dumps({**config_data(), **changes}))
        assert changed.execution_sha256() != first.execution_sha256()


@pytest.mark.parametrize("names", [("",), (" recall",), ("a b",), ("recall", "recall")])
def test_unavailable_metric_names_are_exact_unique_identifiers(names: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate_json(
            json.dumps(
                {
                    "context_tokens": 0,
                    "usage": Usage.zero().model_dump(mode="json"),
                    "unavailable_metrics": names,
                    "unavailable_metrics_reason": "no source provenance",
                }
            )
        )


@pytest.mark.parametrize("reason", [None, "", " "])
def test_unavailable_metrics_require_a_nonblank_reason(reason: str | None) -> None:
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate_json(
            json.dumps(
                {
                    "context_tokens": 0,
                    "usage": Usage.zero().model_dump(mode="json"),
                    "unavailable_metrics": ["recall"],
                    "unavailable_metrics_reason": reason,
                }
            )
        )


def test_metric_capability_mask_is_reusable_and_does_not_change_inputs() -> None:
    score = ScoreResult(metrics=(MetricScore(name="qa", value=1.0), MetricScore(name="recall", value=0.0)))
    retrieval = RetrievalResult(
        context_tokens=0,
        usage=Usage.zero(),
        unavailable_metrics=("recall", "metric_from_other_suite"),
        unavailable_metrics_reason="no complete provenance",
    )
    masked = apply_metric_capabilities(score, retrieval)
    assert [(m.name, m.value) for m in masked.metrics] == [("qa", 1.0), ("recall", None)]
    assert score.metrics[1].value == 0.0 and retrieval.supported is True


def test_capability_mask_and_diagnostics_reach_artifacts_and_cluster_missingness(repo: Path) -> None:
    diagnostics = RetrievalDiagnostics(
        context_policy="retrieved_memory",
        source_items=5,
        selected_items=2,
        truncated_items=3,
        truncated_tokens=7,
        candidate_count=5,
    )

    class MissingProvenance(BaselineFixture):
        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            return RetrievalResult(
                context_tokens=0,
                usage=Usage.zero(),
                unavailable_metrics=("recall",),
                unavailable_metrics_reason="no complete source provenance",
                diagnostics=diagnostics,
            )

        def answer(self, user_id, question, as_of) -> AnswerResult:
            return AnswerResult(answer="fixture", usage=Usage.zero())

    score = ScoreResult(metrics=(MetricScore(name="qa", value=1.0), MetricScore(name="recall", value=0.0)))
    output = run_extension(repo, factory=MissingProvenance, scorer=lambda q, r, a: score)
    answer = json.loads((output / "answers.jsonl").read_bytes())
    scores = json.loads((output / "scores.jsonl").read_bytes())
    cluster = json.loads((output / "per_cluster.jsonl").read_bytes())
    assert answer["diagnostics"] == diagnostics.model_dump(mode="json")
    for row in (answer, scores):
        assert row["status"] == "scored"
        assert row["unavailable_metrics"] == ["recall"]
        assert row["unavailable_metrics_reason"] == "no complete source provenance"
    assert scores["metrics"] == [{"name": "qa", "value": 1.0}, {"name": "recall", "value": None}]
    assert cluster["n_not_supported"] == 0
    assert cluster["metrics"] == [
        {"name": "qa", "n_scored": 1, "n_missing": 0, "mean": 1.0},
        {"name": "recall", "n_scored": 0, "n_missing": 1, "mean": None},
    ]


def test_diagnostics_policy_cannot_disagree_with_frozen_configuration(repo: Path) -> None:
    class Contradictory(BaselineFixture):
        def retrieve(self, user_id, query, mode, as_of, token_budget) -> RetrievalResult:
            return RetrievalResult(
                context_tokens=0,
                usage=Usage.zero(),
                diagnostics=RetrievalDiagnostics(
                    context_policy="full_history",
                    source_items=0,
                    selected_items=0,
                    truncated_items=0,
                    truncated_tokens=0,
                    candidate_count=0,
                ),
            )

        def answer(self, user_id, question, as_of) -> AnswerResult:
            pytest.fail("contradictory diagnostics must fail before answering")

    with pytest.raises(ValueError, match="retrieval_diagnostics_policy_mismatch"):
        run_extension(repo, factory=Contradictory)


def test_optional_extension_artifacts_are_absent_for_old_adapters(repo: Path) -> None:
    output = run_fixture(repo)
    assert {path.name for path in output.iterdir()} == {
        "answers.jsonl",
        "scores.jsonl",
        "per_cluster.jsonl",
        "manifest.json",
    }
    for stream in ("answers", "scores"):
        row = json.loads((output / f"{stream}.jsonl").read_bytes())
        assert "unavailable_metrics" not in row and "diagnostics" not in row
    assert "adapter_metadata" not in json.loads((output / "manifest.json").read_bytes())


class InstrumentedFixture(BaselineFixture):
    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
        self.events: list[dict[str, object]] = []

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        self.events.append({"event": "episode_ingested", "episode_id": "fixture-episode", "llm_calls": 3})
        return super().ingest_turn(turn)

    def drain_adapter_events(self) -> tuple[dict[str, object], ...]:
        result = tuple(self.events)
        self.events.clear()
        return result

    def adapter_metadata(self) -> dict[str, object]:
        return {"distribution": {"name": "fictional-library", "version": "1.0"}, "research_probe": "unmeasured"}


def test_adapter_events_are_lazy_fixed_hashed_and_metadata_is_namespaced(repo: Path) -> None:
    output = run_extension(repo, factory=InstrumentedFixture)
    event_path = output / "adapter_events.jsonl"
    assert event_path.exists(), "adapter instrumentation was discarded"
    assert [json.loads(row) for row in event_path.read_bytes().splitlines()] == [
        {"event": "episode_ingested", "episode_id": "fixture-episode", "llm_calls": 3}
    ]
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["adapter_metadata"] == {
        "distribution": {"name": "fictional-library", "version": "1.0"},
        "research_probe": "unmeasured",
    }
    assert manifest["artifact_sha256"]["adapter_events.jsonl"] == hashlib.sha256(event_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("known_usage", [False, True])
def test_failed_operation_persists_events_and_content_free_failure_marker(repo: Path, known_usage: bool) -> None:
    actual = Usage(input_tokens=7, output_tokens=2, embedding_tokens=0, cost_usd=Decimal("0.03"))
    maximum = Usage(input_tokens=9, output_tokens=3, embedding_tokens=0, cost_usd=Decimal("0.10"))

    class Failed(InstrumentedFixture):
        def answer(self, user_id, question, as_of) -> AnswerResult:
            self.events.append({"event": "episode_failed", "llm_calls": 2})
            if known_usage:
                raise AccountedOperationError("private-secret-do-not-record", actual)
            raise RuntimeError("private-secret-do-not-record")

    limits = config_data()["operation_limits"]
    assert isinstance(limits, dict)
    limits["answer"] = maximum.model_dump(mode="json")
    with pytest.raises((RuntimeError, AccountedOperationError)):
        run_extension(repo, factory=Failed, changes={"operation_limits": limits})
    output = repo / "runs/extension/dummy/T"
    event_path = output / "adapter_events.jsonl"
    assert event_path.exists(), "failed-operation instrumentation was discarded"
    events = [json.loads(row) for row in event_path.read_bytes().splitlines()]
    assert events[-1] == {"event": "episode_failed", "llm_calls": 2}
    assert not (output / "manifest.json").exists()
    assert b"private-secret" not in (output / "FAILED.json").read_bytes()
    ledger = json.loads((repo / "runs/.budgets/fixture-campaign/ledger.json").read_bytes())
    assert ledger["failed_operations"] == 1 and ledger["unknown_operations"] == int(not known_usage)
    assert ledger["spent"] == (actual if known_usage else maximum).model_dump(mode="json")
    assert ledger["pending"] == {} and ledger["completed_operations"] == 5


def test_event_sink_failure_occurs_after_accounting(repo: Path) -> None:
    class InvalidEvents(InstrumentedFixture):
        def drain_adapter_events(self) -> tuple[dict[str, object], ...]:
            return ({"event": float("nan")},)

    with pytest.raises(ValueError):
        run_extension(repo, factory=InvalidEvents)
    ledger = json.loads((repo / "runs/.budgets/fixture-campaign/ledger.json").read_bytes())
    assert ledger["completed_operations"] == 1 and ledger["pending"] == {}
    assert ledger["failed_operations"] == ledger["unknown_operations"] == 0
    assert not (repo / "runs/extension/dummy/T/manifest.json").exists()


def test_reservation_denial_does_not_invoke_operation_or_drain_events(repo: Path) -> None:
    config = HarnessConfig.model_validate_json(json.dumps({**config_data(), "token_cap": 1}))
    ledger = BudgetLedger(repo, config)
    maximum = Usage(input_tokens=2, output_tokens=0, embedding_tokens=0, cost_usd=Decimal(0))

    def operation() -> AnswerResult:
        pytest.fail("provider work preceded reservation")

    def sink() -> None:
        pytest.fail("reservation denial drained operation events")

    with pytest.raises(BudgetExceeded):
        _dispatch(ledger, "dev", maximum, operation, event_sink=sink)
    assert ledger.snapshot().completed_operations == 0


def test_event_serialization_time_is_outside_operation_measurement(repo: Path) -> None:
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    ledger = BudgetLedger(repo, config)
    now = [0.0]
    timings: list[float] = []

    def operation() -> AnswerResult:
        now[0] += 0.001
        return AnswerResult(usage=Usage.zero())

    def sink() -> None:
        now[0] += 100

    _dispatch(ledger, "dev", Usage.zero(), operation, event_sink=sink, timings=timings, timer=lambda: now[0])
    assert timings == [1.0]


@pytest.mark.parametrize(
    "invalid",
    [
        {"nested": {1: "coerced-key"}},
        {"value": datetime(2020, 1, 1)},
        {"value": b"bytes"},
        {"value": Decimal("0.5")},
        {"value": ("tuple",)},
        {"value": float("inf")},
        {"value": float("nan")},
        ["not-an-object"],
    ],
)
def test_adapter_events_require_strict_nested_json(repo: Path, invalid: object) -> None:
    class Invalid(InstrumentedFixture):
        def drain_adapter_events(self) -> tuple[dict[str, object], ...]:
            # Deliberately malformed third-party hook values exercise runtime validation.
            return cast(tuple[dict[str, object], ...], (invalid,))

    with pytest.raises(ValueError):
        run_extension(repo, factory=Invalid)
    output = repo / "runs/extension/dummy/T"
    assert not (output / "manifest.json").exists()
    assert json.loads((output / "FAILED.json").read_bytes())["exception_type"] == "ValueError"


def test_adapter_metadata_cannot_override_authoritative_manifest_fields(repo: Path) -> None:
    class Namespaced(BaselineFixture):
        def adapter_metadata(self) -> dict[str, object]:
            return {"commit_sha": "fictional", "status": "unmeasured", "configuration": {"seed": 0}}

    output = run_extension(repo, factory=Namespaced)
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "complete" and manifest["commit_sha"] != "fictional"
    assert manifest["configuration"]["seed"] == 42
    assert manifest["adapter_metadata"]["status"] == "unmeasured"


def test_artifact_writer_rejects_unknown_stream_without_creating_paths(tmp_path: Path) -> None:
    with ArtifactWriter(tmp_path / "runs", "fixture", "config", "T") as writer:
        with pytest.raises(ValueError, match="unsupported_artifact_stream"):
            writer.append("../arbitrary", {"safe": True})
        writer.finish({"status": "complete"})
    assert not (tmp_path / "runs/fixture/config/arbitrary.jsonl").exists()
