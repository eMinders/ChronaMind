"""Sequential controlled replay through the uniform adapter; no provider implementation."""

import hashlib
import platform
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from datetime import UTC, datetime
from functools import partial
from math import isfinite
from os import cpu_count
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast, runtime_checkable

import structlog

from evaluation.harness.artifacts import ArtifactWriter, percentile
from evaluation.harness.budget import AccountedOperationError, BudgetExceeded, BudgetLedger
from evaluation.harness.config import HarnessConfig
from evaluation.harness.guard import authorize_run, claim_test_run
from evaluation.harness.models import (
    AnswerResult,
    DeleteEvent,
    EvaluationCase,
    IngestEvent,
    MemorySystemAdapter,
    QuestionRecord,
    RetrievalResult,
    ScoreResult,
    Split,
    SuiteId,
    Usage,
    apply_metric_capabilities,
)
from evaluation.harness.paths import run_destination
from evaluation.harness.replay_cache import ReplayCacheSummaryProvider

logger = structlog.get_logger(__name__)


class Accounted(Protocol):
    @property
    def usage(self) -> Usage: ...


@runtime_checkable
class _QuestionPreparation(Protocol):
    """Optional local control; reporting labels never enter this hook."""

    def prepare_question(self, question: QuestionRecord) -> None: ...


@runtime_checkable
class _AdapterEvents(Protocol):
    """Optional local, uncharged instrumentation emitted by accounted operations."""

    def drain_adapter_events(self) -> tuple[dict[str, object], ...]: ...


@runtime_checkable
class _AdapterMetadata(Protocol):
    """Optional local, uncharged runtime provenance, namespaced in the manifest."""

    def adapter_metadata(self) -> dict[str, object]: ...


@runtime_checkable
class _AdapterResources(Protocol):
    """Optional owned resources; cleanup must settle before success publication."""

    def close(self) -> None: ...


@runtime_checkable
class _AdapterCaseLifecycle(MemorySystemAdapter, Protocol):
    """Optional local hook; backend retirement belongs to accounted operations."""

    def release_user(self, user_id: str) -> None: ...


def _validate_json(value: object) -> None:
    """Refuse Python-only values and JSON's nonfinite/implicit-key coercions."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("adapter_hook_json_keys_required")
            _validate_json(item)
        return
    raise ValueError("adapter_hook_json_value_required")


def _json_object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("adapter_hook_json_object_required")
    _validate_json(value)
    return cast(dict[str, object], value)


def _drain_adapter_events(adapter: MemorySystemAdapter, writer: ArtifactWriter) -> None:
    if not isinstance(adapter, _AdapterEvents):
        return
    events = adapter.drain_adapter_events()
    if type(events) is not tuple:
        raise ValueError("adapter_hook_event_tuple_required")
    for event in events:
        writer.append("adapter_events", _json_object(event))


def _dispatch[T: Accounted](
    ledger: BudgetLedger,
    split: Split,
    maximum: Usage,
    operation: Callable[[], T],
    *,
    timings: list[float] | None = None,
    timer: Callable[[], float] = perf_counter,
    event_sink: Callable[[], None] | None = None,
) -> T:
    """Reserve before dispatch, including bounded retry costs; never retry here."""
    reservation = ledger.reserve(maximum, split)
    try:
        try:
            started = timer() if timings is not None else None
            result = operation()
            if started is not None and timings is not None:
                timings.append(_elapsed(started, timer()))
            if not isinstance(result.usage, Usage):
                raise TypeError("operation_usage_required")
        except AccountedOperationError as exc:
            ledger.reconcile(reservation, exc.usage, failed=True)
            raise
        except BaseException:
            ledger.reconcile(reservation, None, failed=True)
            raise
        ledger.reconcile(reservation, result.usage)
        if ledger.snapshot().breached:
            raise BudgetExceeded("operation_exceeded_declared_reservation")
        return result
    finally:
        # Reconcile exactly once before touching instrumentation. Its errors must
        # never turn a completed provider operation into an unknown-usage charge.
        if event_sink is not None:
            event_sink()


def _git_provenance(repo_root: Path) -> dict[str, str | bool]:
    """Fail if Git provenance cannot be established; never report a guessed SHA."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=15, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo_root, capture_output=True, text=True, timeout=15, check=True
    ).stdout.strip()
    return {"commit_sha": sha, "dirty_tree": bool(dirty)}


def _elapsed(start: float, stop: float) -> float:
    result = (stop - start) * 1000
    if not isfinite(result) or result < 0:
        raise ValueError("invalid_measurement_clock")
    return result


def run_evaluation(
    *,
    config: HarnessConfig,
    suite_id: SuiteId,
    split: Split,
    split_path: Path,
    case_loader: Callable[[], Iterable[EvaluationCase]],
    adapter_factory: Callable[[HarnessConfig], MemorySystemAdapter],
    scorer: Callable[[QuestionRecord, RetrievalResult, AnswerResult], ScoreResult],
    run_id: str,
    output_root: Path,
    repo_root: Path,
    confirm_one_shot_test_run: bool = False,
    pilot_freeze_path: Path | None = None,
    timer: Callable[[], float] = perf_counter,
    stream_cases: bool = False,
    source_metadata: Callable[[], dict[str, object]] | None = None,
    replay_cache: ReplayCacheSummaryProvider | None = None,
) -> Path:
    """Run normalized cases only after authorization, then publish a success manifest.

    Source adapters (RL-025) own chronological normalization and split selection.
    Scorers (RL-033) own gold matching; this function performs no inference.
    Adapter constructors/reset/clock methods must be local and uncharged. Provider
    operations must return usage covering every internal failed/retried call.
    """
    authorization = authorize_run(
        config=config,
        split=split,
        suite_id=suite_id,
        split_path=split_path,
        repo_root=repo_root,
        confirm_one_shot_test_run=confirm_one_shot_test_run,
        pilot_freeze_path=pilot_freeze_path,
    )
    run_destination(output_root, run_id, config.config_id, suite_id)
    provenance = _git_provenance(repo_root)
    claim = claim_test_run(output_root, authorization)
    started = datetime.now(UTC)
    start = timer()
    measurements: list[dict[str, object]] = []
    visibility_ms: list[float] = []
    retrieval_ms: list[float] = []
    retrieval_operation_ms: list[float] = []
    answer_ms: list[float] = []
    grouped: dict[tuple[str, str, str], list[float | None]] = defaultdict(list)
    cluster_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    seen_questions: set[tuple[str, str]] = set()
    case_count = 0
    question_count = 0
    skipped_count = 0
    totals = Usage.zero()
    limits = config.operation_limits
    # Ordinary callers keep eager validation before adapter construction. Large
    # benchmark sources explicitly opt into bounded, per-history consumption.
    cases: Iterable[EvaluationCase]
    if not stream_cases:
        validated_cases = tuple(case_loader())
        if not validated_cases or any(not isinstance(case, EvaluationCase) for case in validated_cases):
            raise ValueError("nonempty_validated_cases_required")
        if len({case.cluster_id for case in validated_cases}) != len(validated_cases):
            raise ValueError("duplicate_cluster_id")
        cases = validated_cases
    else:
        cases = case_loader()
    seen_clusters: set[str] = set()
    with ArtifactWriter(output_root, run_id, config.config_id, suite_id) as writer, ExitStack() as resources:
        ledger = BudgetLedger(output_root, config)
        adapter = adapter_factory(config)
        adapter_resources = resources.enter_context(ExitStack())
        if isinstance(adapter, _AdapterResources):
            adapter_resources.callback(adapter.close)
        event_sink = partial(_drain_adapter_events, adapter, writer)
        for case in cases:
            if not isinstance(case, EvaluationCase):
                raise ValueError("nonempty_validated_cases_required")
            if case.cluster_id in seen_clusters:
                raise ValueError("duplicate_cluster_id")
            seen_clusters.add(case.cluster_id)
            case_count += 1
            if (
                case_count > 1
                and isinstance(adapter, _AdapterResources)
                and not isinstance(adapter, _AdapterCaseLifecycle)
            ):
                # Retire the previous case's owned store, worker, graph names,
                # and provenance maps before the next case starts. This keeps
                # replay resource usage bounded; reset remains local/uncharged.
                adapter_resources.close()
                adapter = adapter_factory(config)
                if isinstance(adapter, _AdapterResources):
                    adapter_resources.callback(adapter.close)
                event_sink = partial(_drain_adapter_events, adapter, writer)
            deletion_supported: dict[str, bool] = {}
            for user_id in case.user_ids:
                adapter.reset_user(user_id)
            for event in case.events:
                adapter.set_clock(event.clock)
                if isinstance(event, IngestEvent):
                    visible_start = timer()
                    receipt = _dispatch(
                        ledger,
                        split,
                        limits.ingest_turn,
                        partial(adapter.ingest_turn, event.turn),
                        event_sink=event_sink,
                    )
                    if receipt.turn_id != event.turn.turn_id:
                        raise ValueError("ingestion_receipt_turn_mismatch")
                    drained = _dispatch(
                        ledger,
                        split,
                        limits.drain_queue,
                        partial(adapter.drain_queue, event.turn.user_id),
                        event_sink=event_sink,
                    )
                    if drained.pending:
                        raise ValueError("queue_not_drained")
                    totals = totals.plus(receipt.usage).plus(drained.usage)
                    visibility_ms.append(_elapsed(visible_start, timer()))
                    continue
                if isinstance(event, DeleteEvent):
                    deleted = _dispatch(
                        ledger,
                        split,
                        limits.delete,
                        partial(adapter.delete, event.user_id, event.scope, event.target_ref),
                        event_sink=event_sink,
                    )
                    totals = totals.plus(deleted.usage)
                    deletion_supported[event.user_id] = deleted.supported
                    continue
                question = event.question
                # Strata can encode abstention/category ground truth. They are
                # reporting/scoring metadata, never a hint to the answer system.
                system_question = question.model_copy(update={"stratum": None})
                key = (case.cluster_id, question.question_id)
                if key in seen_questions:
                    raise ValueError("duplicate_cluster_question")
                seen_questions.add(key)
                drained = _dispatch(
                    ledger,
                    split,
                    limits.drain_queue,
                    partial(adapter.drain_queue, question.user_id),
                    event_sink=event_sink,
                )
                if drained.pending:
                    raise ValueError("queue_not_drained")
                if isinstance(adapter, _QuestionPreparation):
                    adapter.prepare_question(system_question)
                retrieval = _dispatch(
                    ledger,
                    split,
                    limits.retrieve,
                    partial(
                        adapter.retrieve,
                        question.user_id,
                        question.question,
                        question.mode,
                        question.as_of_time or event.clock,
                        config.B_mem,
                    ),
                    timings=retrieval_operation_ms,
                    timer=timer,
                    event_sink=event_sink,
                )
                if retrieval.diagnostics is not None and retrieval.diagnostics.context_policy != config.context_policy:
                    raise ValueError("retrieval_diagnostics_policy_mismatch")
                if config.context_policy == "retrieved_memory":
                    if retrieval.context_tokens > config.B_mem:
                        raise ValueError("retrieved_context_exceeds_frozen_budget")
                elif (
                    config.context_window_tokens is None
                    or retrieval.context_tokens > config.context_window_tokens - config.answer_output_tokens
                ):
                    raise ValueError("history_context_exceeds_model_window")
                supported = not (
                    ("retrieval" in question.required_capabilities and not retrieval.supported)
                    or (
                        "deletion" in question.required_capabilities
                        and not deletion_supported.get(question.user_id, False)
                    )
                )
                answer_attempted = supported
                answer = (
                    _dispatch(
                        ledger,
                        split,
                        limits.answer,
                        partial(adapter.answer, question.user_id, system_question, event.clock),
                        timings=answer_ms,
                        timer=timer,
                        event_sink=event_sink,
                    )
                    if supported
                    else AnswerResult(supported=False, usage=Usage.zero())
                )
                supported = supported and answer.supported
                item_usage = drained.usage.plus(retrieval.usage).plus(answer.usage)
                totals = totals.plus(item_usage)
                question_count += 1
                capability_fields: dict[str, object] = {}
                if retrieval.unavailable_metrics:
                    capability_fields = {
                        "unavailable_metrics": list(retrieval.unavailable_metrics),
                        "unavailable_metrics_reason": retrieval.unavailable_metrics_reason,
                    }
                row: dict[str, object] = {
                    "cluster_id": case.cluster_id,
                    "question_id": question.question_id,
                    "user_id": question.user_id,
                    "stratum": question.stratum,
                    "mode": question.mode,
                    "reference_time": event.clock.isoformat(),
                    "status": "scored" if supported else "not_supported",
                    "rendered_context": retrieval.rendered_context,
                    "answer": answer.answer,
                    "context_tokens": retrieval.context_tokens,
                    "usage": item_usage.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in retrieval.items],
                    "timing_key": [case.cluster_id, question.question_id],
                    **capability_fields,
                }
                if retrieval.diagnostics is not None:
                    row["diagnostics"] = retrieval.diagnostics.model_dump(mode="json")
                writer.append("answers", row)
                metrics = (
                    apply_metric_capabilities(scorer(question, retrieval, answer), retrieval).metrics
                    if supported
                    else ()
                )
                if len({metric.name for metric in metrics}) != len(metrics):
                    raise ValueError("duplicate_metric_name")
                writer.append(
                    "scores",
                    {
                        "cluster_id": case.cluster_id,
                        "question_id": question.question_id,
                        "stratum": question.stratum,
                        "status": "scored" if supported else "not_supported",
                        "metrics": [metric.model_dump(mode="json") for metric in metrics],
                        **capability_fields,
                    },
                )
                if not supported:
                    skipped_count += 1
                cluster_counts[(case.cluster_id, question.stratum or "")][0] += 1
                cluster_counts[(case.cluster_id, question.stratum or "")][1] += int(not supported)
                for metric in metrics:
                    grouped[(case.cluster_id, question.stratum or "", metric.name)].append(metric.value)
                retrieval_delta = retrieval.retrieval_latency_ms
                answer_delta = answer_ms[-1] if answer_attempted else None
                if retrieval_delta is not None:
                    retrieval_ms.append(retrieval_delta)
                measurements.append(
                    {
                        "cluster_id": case.cluster_id,
                        "question_id": question.question_id,
                        "retrieval_ms": retrieval_delta,
                        "retrieval_operation_wall_ms": retrieval_operation_ms[-1],
                        "answer_ms": answer_delta,
                    }
                )
            if isinstance(adapter, _AdapterCaseLifecycle):
                for user_id in case.user_ids:
                    adapter.release_user(user_id)
        if not case_count or not question_count:
            raise ValueError("nonempty_evaluation_questions_required")
        # Detect edits while a loader was consuming the declared split.
        if hashlib.sha256(split_path.read_bytes()).hexdigest() != authorization.dataset_split_sha256:
            raise ValueError("dataset_split_changed_during_run")
        for (cluster, stratum), (items, unsupported) in sorted(cluster_counts.items()):
            metrics = []
            for (metric_cluster, metric_stratum, metric), values in sorted(grouped.items()):
                if (metric_cluster, metric_stratum) != (cluster, stratum):
                    continue
                scored = [value for value in values if value is not None]
                metrics.append(
                    {
                        "name": metric,
                        "n_scored": len(scored),
                        "n_missing": len(values) - len(scored),
                        "mean": sum(scored) / len(scored) if scored else None,
                    }
                )
            writer.append(
                "per_cluster",
                {
                    "cluster_id": cluster,
                    "stratum": stratum or None,
                    "n_items": items,
                    "n_not_supported": unsupported,
                    "metrics": metrics,
                },
            )
        snapshot = ledger.snapshot()
        if snapshot.breached:
            raise BudgetExceeded("campaign_budget_breached")
        manifest = {
            "schema_version": 1,
            "task": "RL-030",
            "status": "complete",
            "run_id": run_id,
            "config_id": config.config_id,
            "suite_id": suite_id,
            "split": split,
            **provenance,
            **authorization.model_dump(mode="json"),
            "configuration": config.model_dump(mode="json"),
            "seed": config.seed,
            "started_at": started.isoformat(),
            "wall_clock_duration_ms": _elapsed(start, timer()),
            "hardware": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "logical_cpu_count": cpu_count(),
                "python": platform.python_version(),
            },
            "usage": totals.model_dump(mode="json"),
            "campaign_budget": snapshot.model_dump(mode="json"),
            "case_count": case_count,
            "question_count": question_count,
            "not_supported_count": skipped_count,
            "cluster_key": "question_id"
            if suite_id in {"LongMemEval_oracle", "LongMemEval_S", "LongMemEval_M"}
            else ("conversation_id" if suite_id == "LoCoMo" else "scenario_id"),
            "timing_ms": {
                "retrieval": {"p50": percentile(retrieval_ms, 0.50), "p95": percentile(retrieval_ms, 0.95)},
                "retrieval_operation_wall": {
                    "p50": percentile(retrieval_operation_ms, 0.50),
                    "p95": percentile(retrieval_operation_ms, 0.95),
                },
                "answer": {"p50": percentile(answer_ms, 0.50), "p95": percentile(answer_ms, 0.95)},
                "visibility_runner_wall": {
                    "p50": percentile(visibility_ms, 0.50),
                    "p95": percentile(visibility_ms, 0.95),
                },
            },
            "question_timings": measurements,
            "one_shot_claim": claim.name if claim else None,
            "limits": (
                "Software replay only; scoring/primary endpoint definitions and inference belong to RL-025/033/034"
            ),
        }
        if source_metadata is not None:
            manifest["benchmark_source"] = source_metadata()
        active_replay_cache = (
            replay_cache
            if replay_cache is not None
            else (
                adapter_cache
                if isinstance((adapter_cache := getattr(adapter, "replay_cache", None)), ReplayCacheSummaryProvider)
                else None
            )
        )
        if active_replay_cache is not None:
            manifest["extraction_replay_cache"] = active_replay_cache.manifest_summary().model_dump(mode="json")
        if isinstance(adapter, _AdapterMetadata):
            manifest["adapter_metadata"] = _json_object(adapter.adapter_metadata())
        resources.close()
        writer.finish(manifest)
    logger.info("evaluation_run_completed", cases=case_count, questions=question_count, unsupported=skipped_count)
    return writer.destination
