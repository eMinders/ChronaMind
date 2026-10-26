"""Read-only, checksum-bound benchmark sources and guarded harness integration (RL-025)."""

import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import ijson
from pydantic import AwareDatetime, Field, model_validator

from evaluation.harness.config import HarnessConfig, canonical_json
from evaluation.harness.models import (
    AnswerResult,
    EvaluationCase,
    MemorySystemAdapter,
    MetricScore,
    QuestionRecord,
    Record,
    RetrievalResult,
    ScoreResult,
    Split,
    SuiteId,
)
from evaluation.harness.paths import reject_links
from evaluation.harness.runner import run_evaluation
from research.benchmarks.scoring import score_locomo, score_longmemeval, scorer_provenance


class GoldQuestion(Record):
    """Scoring-only labels, never supplied to a MemorySystemAdapter."""

    question_id: str
    cluster_id: str
    user_id: str
    answer: str
    question_type: str | None = None
    category: int | None = None
    gold_turn_ids: tuple[str, ...] = ()
    unresolved_evidence_ids: tuple[str, ...] = ()
    primary_recall_eligible: bool = False
    abstention: bool = False
    original_question_date: AwareDatetime | None = None
    original_date_anomaly: bool = False


class ClockAdjustment(Record):
    turn_id: str
    source_time: AwareDatetime
    recorded_time: AwareDatetime


class ReplayAudit(Record):
    original_session_ids: tuple[str, ...] = ()
    replay_session_ids: tuple[str, ...] = ()
    replay_session_indices: tuple[int, ...] = ()
    clock_adjustments: tuple[ClockAdjustment, ...] = ()
    source_speakers: tuple[tuple[str, str], ...] = ()
    unpaired_session_dates: tuple[str, ...] = ()


class PreparedCase(Record):
    """A system-visible case and a distinct, immutable scoring/provenance envelope."""

    case: EvaluationCase
    gold: tuple[GoldQuestion, ...]
    audit: ReplayAudit = ReplayAudit()


class BenchmarkSelection(Record):
    """Versioned JSON selection artifact; JSON also permits a YAML-compatible serialization.

    RL-026 owns real split creation/freeze. This schema creates no approved split.
    Its entire bytes are bound by the existing harness split hash/freeze guard.
    """

    schema_version: Literal[1]
    suite_id: SuiteId
    split: Split
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_ids: tuple[str, ...] = Field(min_length=1)
    retrieval_k: int = Field(ge=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "BenchmarkSelection":
        if len(set(self.case_ids)) != len(self.case_ids) or any(not value.strip() for value in self.case_ids):
            raise ValueError("distinct_nonempty_selected_case_ids_required")
        return self


class _UniqueMap(dict):
    duplicate_keys: bool = False

    def __setitem__(self, key: str, value: object) -> None:
        if key in self:
            # Raising inside ijson's C callback can leave a pending exception.
            # Validate the flag in ordinary Python before exposing the record.
            self.duplicate_keys = True
        super().__setitem__(key, value)


def manifest_digest(path: Path, filename: str) -> str:
    """Read trusted acquisition metadata without invoking any fetch/repair operation."""
    reject_links(path)
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]) or parts[1] in entries:
            raise ValueError("invalid_or_duplicate_manifest_entry")
        entries[parts[1]] = parts[0].lower()
    if filename not in entries:
        raise ValueError("benchmark_not_in_manifest")
    return entries[filename]


def _digest(handle: Any) -> str:
    handle.seek(0)
    result = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        result.update(chunk)
    return result.hexdigest()


def iter_records(path: Path, expected_sha256: str) -> Iterator[dict[str, Any]]:
    """Validate bytes, then stream one JSON-array object at a time; never modify raw files.

    Memory is bounded by an individual history/conversation, including for M.
    The second checksum detects cooperative edits during consumption. No cache,
    newline repair, acquisition, provider or model download is performed here.
    """
    reject_links(path)
    with path.open("rb") as handle:
        if _digest(handle) != expected_sha256:
            raise ValueError("benchmark_sha256_mismatch")
        handle.seek(0)
        first = handle.read(1)
        while first in (b" ", b"\n", b"\r", b"\t"):
            first = handle.read(1)
        if first != b"[":
            raise ValueError("benchmark_json_array_required")
        handle.seek(0)
        try:
            for row in ijson.items(handle, "item", map_type=_UniqueMap):
                if not isinstance(row, dict):
                    raise ValueError("benchmark_object_required")
                pending: list[object] = [row]
                while pending:
                    value = pending.pop()
                    if isinstance(value, _UniqueMap) and value.duplicate_keys:
                        raise ValueError("duplicate_json_key")
                    if isinstance(value, dict):
                        pending.extend(value.values())
                    elif isinstance(value, list):
                        pending.extend(value)
                yield row
        except ijson.JSONError:
            raise ValueError("invalid_benchmark_json") from None
        if _digest(handle) != expected_sha256:
            raise ValueError("benchmark_changed_during_read")


def evidence_recall(retrieval: RetrievalResult, gold_turn_ids: tuple[str, ...], k: int) -> float | None:
    """Unique gold-turn coverage through the first k memories' MEMORY_EVIDENCE links."""
    if type(k) is not int or k < 1:
        raise ValueError("positive_retrieval_k_required")
    if not retrieval.supported or not gold_turn_ids:
        return None
    recovered = {turn for item in retrieval.items[:k] for turn in item.evidence_turn_ids}
    gold = set(gold_turn_ids)
    return len(gold & recovered) / len(gold)


class BenchmarkSource:
    """Single-run source state; construct without accessing any dataset/split bytes.

    Standalone iter_cases is a low-level loader for already-authorized callers.
    Use run() for controlled evaluation: it defers all selection/source reads to
    the existing harness authorization boundary and preserves one-shot checks.
    """

    def __init__(
        self,
        *,
        raw_path: Path,
        manifest_path: Path,
        filename: str,
        suite_id: SuiteId,
        normalizer: Callable[[Mapping[str, Any]], PreparedCase],
        record_id: Callable[[Mapping[str, Any]], str],
    ) -> None:
        self.raw_path = raw_path
        self.manifest_path = manifest_path
        self.filename = filename
        self.suite_id: SuiteId = suite_id
        self._normalizer = normalizer
        self._record_id = record_id
        self._gold: dict[str, GoldQuestion] = {}
        self._audit: list[dict[str, object]] = []
        self._dataset_sha256: str | None = None
        self._metric_k: int | None = None
        self._run_lock = Lock()

    def iter_cases(self, case_ids: tuple[str, ...]) -> Iterator[EvaluationCase]:
        """Read selected original case IDs; retain only small scoring/audit metadata."""
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("distinct_nonempty_selected_case_ids_required")
        self._gold.clear()
        self._audit.clear()
        expected = manifest_digest(self.manifest_path, self.filename)
        self._dataset_sha256 = expected
        wanted = set(case_ids)
        seen: set[str] = set()
        found: set[str] = set()
        for row in iter_records(self.raw_path, expected):
            identifier = self._record_id(row)
            if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
                raise ValueError("invalid_or_duplicate_benchmark_case_id")
            seen.add(identifier)
            if identifier not in wanted:
                continue
            prepared = self._normalizer(row)
            found.add(identifier)
            for gold in prepared.gold:
                if gold.question_id in self._gold:
                    raise ValueError("duplicate_scoring_question_id")
                self._gold[gold.question_id] = gold
            audit = prepared.audit
            self._audit.append(
                {
                    "case_id": identifier,
                    "original_session_ids": list(audit.original_session_ids),
                    "replay_session_ids": list(audit.replay_session_ids),
                    "replay_session_indices": list(audit.replay_session_indices),
                    "adjusted_clock_count": len(audit.clock_adjustments),
                    "clock_adjustments_sha256": hashlib.sha256(
                        canonical_json([clock.model_dump(mode="json") for clock in audit.clock_adjustments])
                    ).hexdigest(),
                    "original_date_anomaly_count": sum(g.original_date_anomaly for g in prepared.gold),
                    "primary_recall_eligible_count": sum(g.primary_recall_eligible for g in prepared.gold),
                    "missing_gold_evidence_count": sum(not g.gold_turn_ids for g in prepared.gold),
                    "unresolved_evidence_question_count": sum(bool(g.unresolved_evidence_ids) for g in prepared.gold),
                    "unpaired_session_date_count": len(audit.unpaired_session_dates),
                }
            )
            yield prepared.case
        if found != wanted:
            raise ValueError("selected_benchmark_case_not_found")

    def source_provenance(self) -> dict[str, object]:
        """No raw dialogue, answers or gold labels in provenance; labels remain scorer-only."""
        reject_links(self.raw_path)
        with self.raw_path.open("rb") as handle:
            if _digest(handle) != self._dataset_sha256:
                raise ValueError("benchmark_changed_during_run")
        return {
            "adapter": "RL-025-v1",
            "suite_id": self.suite_id,
            "dataset_filename": self.filename,
            "dataset_sha256": self._dataset_sha256,
            "retrieval_k": self._metric_k,
            "timezone_policy": "documented source-specific timestamps interpreted as UTC",
            "replay_policy": (
                "stable session sort; recorded clock max(source, previous+1us); rho includes tie increments"
            ),
            "audit": self._audit,
            "official_scorers": scorer_provenance(),
        }

    def scorer(
        self, *, k: int, judge: Callable[[str], str] | None = None
    ) -> Callable[[QuestionRecord, RetrievalResult, AnswerResult], ScoreResult]:
        """Make a local/uncharged scorer; provider judges need RL-033 accounted integration."""
        if type(k) is not int or k < 1:
            raise ValueError("positive_retrieval_k_required")
        return lambda question, retrieval, answer: self._score(question, retrieval, answer, k=k, judge=judge)

    def _score(
        self,
        question: QuestionRecord,
        retrieval: RetrievalResult,
        answer: AnswerResult,
        *,
        k: int,
        judge: Callable[[str], str] | None,
    ) -> ScoreResult:
        # Functions are wired at module scope below once the licensed wrappers exist.
        gold = self._gold[question.question_id]
        if gold.user_id != question.user_id:
            raise ValueError("scoring_tenant_mismatch")
        recall = None if gold.unresolved_evidence_ids else evidence_recall(retrieval, gold.gold_turn_ids, k)
        all_found = float(recall == 1.0) if recall is not None and len(set(gold.gold_turn_ids)) > 1 else None
        if self.suite_id == "LoCoMo":
            if gold.category is None:
                raise ValueError("locomo_category_required")
            qa = score_locomo(gold.category, gold.answer, answer.answer)
            return ScoreResult(
                metrics=(
                    MetricScore(name="official_qa", value=qa),
                    MetricScore(name="evidence_recall_at_k", value=recall),
                    MetricScore(name="abstention_accuracy", value=qa if gold.abstention else None),
                )
            )
        qa = (
            score_longmemeval(
                gold.question_type or "",
                question.question,
                gold.answer,
                answer.answer,
                abstention=gold.abstention,
                judge=judge,
            )
            if judge is not None
            else None
        )
        return ScoreResult(
            metrics=(
                MetricScore(name="official_qa", value=qa),
                MetricScore(name="diagnostic_evidence_recall_at_k", value=recall),
                MetricScore(name="diagnostic_all_evidence_found", value=all_found),
                MetricScore(
                    name="primary_all_evidence_found", value=all_found if gold.primary_recall_eligible else None
                ),
                MetricScore(
                    name="primary_evidence_recall_at_k", value=recall if gold.primary_recall_eligible else None
                ),
                MetricScore(
                    name="primary_evidence_recall_at_k_excluding_date_anomalies",
                    value=recall if gold.primary_recall_eligible and not gold.original_date_anomaly else None,
                ),
                MetricScore(name="abstention_accuracy", value=qa if gold.abstention else None),
            )
        )

    def run(
        self,
        *,
        selection_path: Path,
        split: Split,
        config: HarnessConfig,
        adapter_factory: Callable[[HarnessConfig], MemorySystemAdapter],
        run_id: str,
        output_root: Path,
        repo_root: Path,
        k: int,
        judge: Callable[[str], str] | None = None,
        confirm_one_shot_test_run: bool = False,
        pilot_freeze_path: Path | None = None,
    ) -> Path:
        """Execute only selected cases after the harness guard; stream large M histories."""
        if not self._run_lock.acquire(blocking=False):
            raise ValueError("benchmark_source_already_running")

        def load() -> Iterator[EvaluationCase]:
            selection = BenchmarkSelection.model_validate_json(selection_path.read_bytes())
            if selection.suite_id != self.suite_id or selection.split != split:
                raise ValueError("benchmark_selection_suite_or_split_mismatch")
            if selection.retrieval_k != k:
                raise ValueError("benchmark_selection_retrieval_k_mismatch")
            self._metric_k = k
            if selection.dataset_sha256 != manifest_digest(self.manifest_path, self.filename):
                raise ValueError("benchmark_selection_dataset_hash_mismatch")
            yield from self.iter_cases(selection.case_ids)

        try:
            return run_evaluation(
                config=config,
                suite_id=self.suite_id,
                split=split,
                split_path=selection_path,
                case_loader=load,
                adapter_factory=adapter_factory,
                scorer=self.scorer(k=k, judge=judge),
                run_id=run_id,
                output_root=output_root,
                repo_root=repo_root,
                confirm_one_shot_test_run=confirm_one_shot_test_run,
                pilot_freeze_path=pilot_freeze_path,
                stream_cases=True,
                source_metadata=self.source_provenance,
            )
        finally:
            self._run_lock.release()
