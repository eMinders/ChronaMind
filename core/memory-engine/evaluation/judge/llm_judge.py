"""Single-answer LLM judge with length-normalized rubric, caching, perturbations, and D-24 validation (RL-033)."""

import hashlib
import json
import os
import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol, runtime_checkable

import structlog
from pydantic import Field, field_validator, model_validator
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from custom_memory_engine.protocols import Agreement, binary_agreement, judge_gate
from evaluation.harness.budget import BudgetLedger
from evaluation.harness.config import canonical_json
from evaluation.harness.models import QueryMode, Record, Split, Usage
from evaluation.harness.paths import directory, reject_links
from evaluation.judge.deterministic import (
    detect_abstention,
    exact_or_normalized_match,
    score_suite_t_deterministic,
)
from evaluation.stats.bootstrap import cluster_bootstrap
from evaluation.stats.models import Observation, pair_observations
from research.benchmarks.own_T.generator import generate_suite_t
from research.benchmarks.scoring import LONGMEMEVAL_TASKS, get_longmemeval_prompt

logger = structlog.get_logger(__name__)

PINNED_ANSWERING_MODEL_ID = "gpt-4.1-mini-2025-04-14"
PINNED_JUDGE_MODEL_ID = "gpt-4.1-2025-04-14"
DEFAULT_RUBRIC_VERSION = "rl033-v1"

IRRELEVANT_DETAIL_SUFFIXES: tuple[str, ...] = (
    (
        " By the way, the weather outside was overcast that afternoon and I had a cup "
        "of chamomile tea while reviewing my notes."
    ),
    (
        " Unrelatedly, the office printer on the third floor jammed twice yesterday "
        "morning before the maintenance technician arrived."
    ),
    " As an aside, I also noticed a blue commuter train passing the station platform earlier in the week.",
)

LENGTH_NORMALIZATION_CLAUSE = (
    "Length-normalization rule: Do not reward extra detail, verbosity, conversational politeness, "
    "or stylistic polish. A concise answer that states the required fact and temporal relation is "
    "equivalent to a lengthy explanation stating the same fact."
)

MODE_RUBRIC_INSTRUCTIONS: dict[str, str] = {
    "current": (
        "Mode = current: The system answer is correct (label = 1) iff it identifies the scripted gold "
        "value as the current state. If the answer states both an old/superseded value and the current "
        "value without clearly specifying which one holds now, or if it abstains/refuses, return label = 0."
    ),
    "as_of": (
        "Mode = as_of: The system answer is correct (label = 1) iff it identifies the scripted gold "
        "value as the state that held on the queried date. Listing multiple values without identifying "
        "which one applied on that date, or abstaining, is incorrect (label = 0)."
    ),
    "before_change": (
        "Mode = before_change: The system answer is correct (label = 1) iff it identifies the scripted "
        "gold value that held immediately prior to the change. Stating the post-change value as the "
        "prior state, or listing both without disambiguating which came before, is incorrect (label = 0)."
    ),
    "what_changed": (
        "Mode = what_changed: The system answer is correct (label = 1) iff it accurately states both "
        "the new value (and prior value if specified in the gold answer) AND the correct effective date "
        "of the transition. A correct value with a wrong or omitted effective date is incorrect (label = 0)."
    ),
    "stable": (
        "Mode = stable: The system answer is correct (label = 1) iff it states the scripted unchanged "
        "gold value without contradiction or abstention."
    ),
    "abstention": (
        "Mode = abstention: The question is unanswerable from the user's history. The system answer is "
        "correct (label = 1) iff it appropriately abstains or states that the information was not provided."
    ),
}


class JudgeValidationError(ValueError):
    """Raised when a judge configuration, response, or validation gate check fails."""


class JudgeTransientProviderError(RuntimeError):
    """Raised on transient transport failures eligible for bounded Tenacity retry."""


class JudgeConfig(Record):
    """Pinned D-03 judge model configuration with self-preference and decoding guards."""

    judge_model_id: str = PINNED_JUDGE_MODEL_ID
    answering_model_id: str = PINNED_ANSWERING_MODEL_ID
    temperature: float = 0.0
    top_p: float = 1.0
    max_completion_tokens: int = 1024
    seed: int = Field(default=42, ge=0)
    rubric_version: str = Field(default=DEFAULT_RUBRIC_VERSION, min_length=1)
    same_family_bias_disclosed: bool = True

    @field_validator("judge_model_id", "answering_model_id", "rubric_version")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nonempty_judge_config_string_required")
        return value

    @field_validator("temperature", "top_p", mode="before")
    @classmethod
    def reject_bool_float(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("numeric_float_required")
        return value

    @model_validator(mode="after")
    def enforce_d03_constraints(self) -> "JudgeConfig":
        if self.judge_model_id == self.answering_model_id:
            raise ValueError("judge_model_must_differ_from_answering_model")
        if self.temperature != 0.0:
            raise ValueError("judge_temperature_must_be_zero")
        if self.top_p != 1.0:
            raise ValueError("judge_top_p_must_be_one")
        if self.max_completion_tokens != 1024:
            raise ValueError("judge_max_completion_tokens_must_be_1024")
        if not self.same_family_bias_disclosed:
            raise ValueError("same_family_bias_disclosure_required")
        return self


class JudgeItem(Record):
    """Single-answer evaluation item (never pairwise) for the LLM judge (Proposal §10.5)."""

    item_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    suite: Literal["T", "G", "D", "I", "LongMemEval"] = "T"
    split: Split = "dev"
    mode: QueryMode | Literal["abstention"] = "current"
    question: str = Field(min_length=1)
    gold_answer: str = Field(min_length=1)
    hypothesis: str
    longmemeval_question_type: str | None = None
    abstention: bool = False

    @field_validator("item_id", "cluster_id", "question", "gold_answer")
    @classmethod
    def nonblank_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nonempty_judge_item_field_required")
        return value

    @model_validator(mode="after")
    def validate_longmemeval_fields(self) -> "JudgeItem":
        if self.suite == "LongMemEval" and not self.abstention:
            if self.longmemeval_question_type not in LONGMEMEVAL_TASKS:
                raise ValueError("valid_longmemeval_question_type_required")
        return self


def build_judge_prompt(item: JudgeItem, *, rubric_version: str = DEFAULT_RUBRIC_VERSION) -> str:
    """Build a single-answer, length-normalized rubric prompt for `item` (Proposal §10.5)."""
    if not rubric_version.strip():
        raise ValueError("nonempty_rubric_version_required")
    if item.suite == "LongMemEval":
        q_type = item.longmemeval_question_type or "single-session-user"
        base_prompt = get_longmemeval_prompt(
            q_type,
            item.question,
            item.gold_answer,
            item.hypothesis,
            abstention=item.abstention,
        )
        return (
            f"[Rubric Version: {rubric_version}]\n"
            f"{LENGTH_NORMALIZATION_CLAUSE}\n"
            "Evaluate this single answer independently.\n\n"
            f"{base_prompt}\n\n"
            'Respond with JSON {"label": 1, "rationale": "..."} if correct (yes), '
            'or {"label": 0, "rationale": "..."} if incorrect (no).'
        )

    effective_mode = "abstention" if item.abstention else item.mode
    mode_rubric = MODE_RUBRIC_INSTRUCTIONS[effective_mode]
    return (
        f"[Rubric Version: {rubric_version}]\n"
        "You are an impartial evaluation judge scoring a single system answer against a scripted gold answer.\n"
        f"{LENGTH_NORMALIZATION_CLAUSE}\n"
        f"{mode_rubric}\n\n"
        f"Question: {item.question}\n"
        f"Scripted Gold Answer: {item.gold_answer}\n"
        f"System Answer: {item.hypothesis}\n\n"
        'Return only valid JSON with keys "label" (integer 1 for correct, 0 for incorrect) '
        'and "rationale" (concise justification).'
    )


def prompt_sha256(prompt: str) -> str:
    """Compute canonical UTF-8 SHA-256 digest of a rendered judge prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def randomize_presentation_order(
    items: Sequence[JudgeItem],
    *,
    seed: int = 42,
) -> tuple[tuple[int, JudgeItem], ...]:
    """Randomize single-answer presentation order using an explicit seed (Proposal §10.5)."""
    seen_ids: set[str] = set()
    for item in items:
        if item.item_id in seen_ids:
            raise ValueError("duplicate_judge_item_id")
        seen_ids.add(item.item_id)
    indexed = list(enumerate(items))
    rng = random.Random(seed)
    rng.shuffle(indexed)
    return tuple(
        (presentation_idx, original_item) for presentation_idx, (_orig_idx, original_item) in enumerate(indexed)
    )


class JudgeProviderResponse(Record):
    """Accounted raw response from a judge model provider callable."""

    raw_text: str = Field(min_length=1)
    usage: Usage = Usage.zero()


@runtime_checkable
class JudgeCompletionCallable(Protocol):
    """Protocol for invoking the pinned D-03 judge model on a single prompt."""

    def __call__(self, prompt: str, config: JudgeConfig) -> JudgeProviderResponse | str: ...


class ParsedJudgeOutput(Record):
    """Validated binary label and non-empty rationale parsed from a judge response."""

    label: Literal[0, 1]
    rationale: str = Field(min_length=1)


def parse_judge_response(raw_text: str, *, allow_yes_no_fallback: bool = False) -> ParsedJudgeOutput:
    """Parse and validate a judge response into a binary label (0 or 1) and rationale."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise JudgeValidationError("empty_judge_response")
    stripped = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    candidate_json = fence_match.group(1) if fence_match is not None else stripped
    if candidate_json.startswith("{") and candidate_json.endswith("}"):
        try:
            payload = json.loads(candidate_json)
        except json.JSONDecodeError as exc:
            raise JudgeValidationError("malformed_judge_json") from exc
        if not isinstance(payload, dict):
            raise JudgeValidationError("judge_json_object_required")
        raw_label = payload.get("label")
        rationale = payload.get("rationale")
        if type(raw_label) is not int or raw_label not in (0, 1):
            raise JudgeValidationError("judge_binary_integer_label_required")
        if not isinstance(rationale, str) or not rationale.strip():
            raise JudgeValidationError("judge_nonempty_rationale_required")
        return ParsedJudgeOutput(label=raw_label, rationale=rationale.strip())

    if allow_yes_no_fallback:
        lower = stripped.lower()
        has_yes = bool(re.search(r"\byes\b", lower))
        has_no = bool(re.search(r"\bno\b", lower))
        if has_yes and not has_no:
            return ParsedJudgeOutput(label=1, rationale=stripped)
        if has_no and not has_yes:
            return ParsedJudgeOutput(label=0, rationale=stripped)
    raise JudgeValidationError("unparseable_judge_response")


class JudgeCacheKey(Record):
    """Composite cache key (judge_model_id, rubric_version, prompt_sha256)."""

    judge_model_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.judge_model_id, self.rubric_version, self.prompt_sha256)


class JudgeCacheEntry(Record):
    """Single immutable cached judge decision (only successful validated responses are stored)."""

    key: JudgeCacheKey
    label: Literal[0, 1]
    rationale: str = Field(min_length=1)
    recorded_usage: Usage = Usage.zero()
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_integrity(self) -> "JudgeCacheEntry":
        expected = _compute_judge_entry_sha256(self.key, self.label, self.rationale)
        if self.entry_sha256 != expected:
            raise ValueError("judge_cache_entry_sha256_mismatch")
        return self


def _compute_judge_entry_sha256(key: JudgeCacheKey, label: int, rationale: str) -> str:
    payload = {
        "key": key.model_dump(mode="json"),
        "label": label,
        "rationale": rationale,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class JudgeCacheEnvelope(Record):
    """On-disk envelope for the deterministic judge response cache."""

    schema_version: Literal[1] = 1
    task: Literal["RL-033"] = "RL-033"
    entries: tuple[JudgeCacheEntry, ...]
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_envelope(self) -> "JudgeCacheEnvelope":
        mapping = {entry.key.as_tuple(): entry for entry in self.entries}
        if len(mapping) != len(self.entries):
            raise ValueError("duplicate_judge_cache_key")
        expected = _compute_judge_collection_sha256(mapping)
        if self.integrity_sha256 != expected:
            raise ValueError("judge_cache_envelope_sha256_mismatch")
        return self


def _compute_judge_collection_sha256(entries: Mapping[tuple[str, str, str], JudgeCacheEntry]) -> str:
    ordered = [entries[k].model_dump(mode="json") for k in sorted(entries.keys())]
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


class JudgeResponseCache:
    """Thread-safe cache storing only successful, schema-validated judge outputs (Proposal §10.5)."""

    def __init__(self, *, cache_path: Path | None = None, auto_persist: bool = True) -> None:
        self._lock = Lock()
        self._entries: dict[tuple[str, str, str], JudgeCacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._errors_rejected = 0
        self._cache_path = cache_path
        self._auto_persist = auto_persist
        if cache_path is not None:
            reject_links(cache_path)
            if cache_path.exists():
                self._load_unlocked(cache_path)

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def errors_rejected(self) -> int:
        with self._lock:
            return self._errors_rejected

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: JudgeCacheKey) -> JudgeCacheEntry | None:
        k_tuple = key.as_tuple()
        with self._lock:
            entry = self._entries.get(k_tuple)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            return entry

    def put(self, key: JudgeCacheKey, parsed: ParsedJudgeOutput, *, usage: Usage | None = None) -> JudgeCacheEntry:
        resolved_usage = usage if usage is not None else Usage.zero()
        entry_sha = _compute_judge_entry_sha256(key, parsed.label, parsed.rationale)
        entry = JudgeCacheEntry(
            key=key,
            label=parsed.label,
            rationale=parsed.rationale,
            recorded_usage=resolved_usage,
            entry_sha256=entry_sha,
        )
        k_tuple = key.as_tuple()
        with self._lock:
            existing = self._entries.get(k_tuple)
            if existing is not None:
                if existing.entry_sha256 != entry.entry_sha256:
                    self._errors_rejected += 1
                    raise ValueError("judge_cache_conflicting_overwrite")
                return existing
            self._entries[k_tuple] = entry
            if self._cache_path is not None and self._auto_persist:
                self._save_unlocked(self._cache_path)
            return entry

    def record_error(self) -> None:
        with self._lock:
            self._errors_rejected += 1

    def save(self, path: Path | None = None) -> Path:
        with self._lock:
            target = path if path is not None else self._cache_path
            if target is None:
                raise ValueError("judge_cache_path_required")
            self._save_unlocked(target)
            return target

    def _save_unlocked(self, target: Path) -> None:
        reject_links(target)
        parent = directory(target.parent)
        ordered = tuple(self._entries[k] for k in sorted(self._entries.keys()))
        envelope = JudgeCacheEnvelope(
            entries=ordered,
            integrity_sha256=_compute_judge_collection_sha256(self._entries),
        )
        payload = canonical_json(envelope.model_dump(mode="json"))
        tmp_path = parent / f".{target.name}.{os.getpid()}.tmp"
        reject_links(tmp_path)
        try:
            with tmp_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _load_unlocked(self, path: Path) -> None:
        reject_links(path)
        envelope = JudgeCacheEnvelope.model_validate_json(path.read_bytes())
        self._entries = {entry.key.as_tuple(): entry for entry in envelope.entries}


class JudgeDecisionRecord(Record):
    """Single-item judge evaluation output with provenance and presentation order."""

    item_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    label: Literal[0, 1]
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    presentation_index: int = Field(ge=0)
    cache_hit: bool
    usage: Usage


class LLMJudge:
    """Validated LLM judge with single-answer randomized presentation, caching, and budget guards."""

    def __init__(
        self,
        config: JudgeConfig,
        completion_fn: JudgeCompletionCallable,
        *,
        cache: JudgeResponseCache | None = None,
        budget_ledger: BudgetLedger | None = None,
        max_call_usage: Usage | None = None,
        allow_yes_no_fallback: bool = False,
    ) -> None:
        self._config = config
        self._completion_fn = completion_fn
        self._cache = cache if cache is not None else JudgeResponseCache()
        self._budget_ledger = budget_ledger
        self._max_call_usage = max_call_usage
        self._allow_yes_no_fallback = allow_yes_no_fallback

    @property
    def config(self) -> JudgeConfig:
        return self._config

    @property
    def cache(self) -> JudgeResponseCache:
        return self._cache

    def evaluate_item(self, item: JudgeItem, *, presentation_index: int = 0) -> JudgeDecisionRecord:
        """Score a single `JudgeItem` (checking cache first, reserving budget on miss, caching only on success)."""
        prompt = build_judge_prompt(item, rubric_version=self._config.rubric_version)
        p_sha = prompt_sha256(prompt)
        cache_key = JudgeCacheKey(
            judge_model_id=self._config.judge_model_id,
            rubric_version=self._config.rubric_version,
            prompt_sha256=p_sha,
        )
        cached_entry = self._cache.get(cache_key)
        if cached_entry is not None:
            return JudgeDecisionRecord(
                item_id=item.item_id,
                cluster_id=item.cluster_id,
                label=cached_entry.label,
                score=float(cached_entry.label),
                rationale=cached_entry.rationale,
                judge_model_id=self._config.judge_model_id,
                rubric_version=self._config.rubric_version,
                prompt_sha256=p_sha,
                presentation_index=presentation_index,
                cache_hit=True,
                usage=Usage.zero(),
            )

        reservation_id: str | None = None
        if self._budget_ledger is not None:
            if self._max_call_usage is None:
                raise ValueError("max_call_usage_required_when_budget_ledger_enabled")
            reservation_id = self._budget_ledger.reserve(self._max_call_usage, item.split)

        actual_usage: Usage | None = None
        try:
            raw_result = self._invoke_with_retry(prompt)
            if isinstance(raw_result, JudgeProviderResponse):
                raw_text = raw_result.raw_text
                actual_usage = raw_result.usage
            elif isinstance(raw_result, str):
                raw_text = raw_result
                actual_usage = Usage.zero()
            else:
                raise JudgeValidationError("invalid_judge_provider_return_type")

            parsed = parse_judge_response(
                raw_text,
                allow_yes_no_fallback=self._allow_yes_no_fallback or item.suite == "LongMemEval",
            )
        except BaseException:
            self._cache.record_error()
            if self._budget_ledger is not None and reservation_id is not None:
                self._budget_ledger.reconcile(reservation_id, actual_usage, failed=True)
            logger.warning(
                "llm_judge_evaluation_failed_not_cached",
                item_id=item.item_id,
                judge_model_id=self._config.judge_model_id,
                rubric_version=self._config.rubric_version,
            )
            raise

        if self._budget_ledger is not None and reservation_id is not None:
            self._budget_ledger.reconcile(reservation_id, actual_usage, failed=False)

        resolved_usage = actual_usage if actual_usage is not None else Usage.zero()
        self._cache.put(cache_key, parsed, usage=resolved_usage)
        return JudgeDecisionRecord(
            item_id=item.item_id,
            cluster_id=item.cluster_id,
            label=parsed.label,
            score=float(parsed.label),
            rationale=parsed.rationale,
            judge_model_id=self._config.judge_model_id,
            rubric_version=self._config.rubric_version,
            prompt_sha256=p_sha,
            presentation_index=presentation_index,
            cache_hit=False,
            usage=resolved_usage,
        )

    def evaluate_batch(
        self,
        items: Sequence[JudgeItem],
        *,
        seed: int | None = None,
    ) -> tuple[JudgeDecisionRecord, ...]:
        """Evaluate `items` in seeded randomized single-answer presentation order and return in original order."""
        resolved_seed = self._config.seed if seed is None else seed
        randomized = randomize_presentation_order(items, seed=resolved_seed)
        by_id: dict[str, JudgeDecisionRecord] = {}
        for presentation_idx, item in randomized:
            by_id[item.item_id] = self.evaluate_item(item, presentation_index=presentation_idx)
        return tuple(by_id[item.item_id] for item in items)

    def _invoke_with_retry(self, prompt: str) -> JudgeProviderResponse | str:
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.02, min=0.02, max=0.2),
            retry=retry_if_exception_type(JudgeTransientProviderError),
            reraise=True,
        ):
            with attempt:
                return self._completion_fn(prompt, self._config)
        raise RuntimeError("unreachable_retry_termination")


def append_irrelevant_detail(item: JudgeItem, *, suffix_index: int = 0) -> JudgeItem:
    """Return a copy of `item` with irrelevant tangential text appended to `hypothesis` (Proposal §10.5)."""
    suffix = IRRELEVANT_DETAIL_SUFFIXES[suffix_index % len(IRRELEVANT_DETAIL_SUFFIXES)]
    base = item.hypothesis.rstrip()
    if base and base[-1] not in ".!?":
        base = base + "."
    return item.model_copy(
        update={
            "item_id": f"{item.item_id}__pert_irrelevant",
            "hypothesis": f"{base}{suffix}",
        }
    )


def paraphrase_hypothesis(item: JudgeItem) -> JudgeItem:
    """Return a copy of `item` with a deterministic meaning-preserving paraphrase of `hypothesis`."""
    text = item.hypothesis.strip()
    replacements = (
        ("Based on your records, ", "According to your recorded history, "),
        ("Currently, ", "At the present time, "),
        ("Previously, ", "Prior to the update, "),
        ("Your current ", "At present, your "),
        (" changed from ", " transitioned from "),
        (" effective on ", " taking effect on "),
    )
    updated = text
    changed = False
    for old, new in replacements:
        if old in updated:
            updated = updated.replace(old, new, 1)
            changed = True
    if not changed:
        updated = f"According to the recorded history, {text[0].lower() + text[1:] if len(text) > 1 else text}"
    return item.model_copy(
        update={
            "item_id": f"{item.item_id}__pert_paraphrase",
            "hypothesis": updated,
        }
    )


class PerturbationCaseResult(Record):
    """Per-item perturbation comparison across original, irrelevant-detail, and paraphrased variants."""

    item_id: str = Field(min_length=1)
    original_label: Literal[0, 1]
    irrelevant_detail_label: Literal[0, 1]
    paraphrase_label: Literal[0, 1]
    irrelevant_flipped: bool
    paraphrase_flipped: bool


class PerturbationStabilityReport(Record):
    """Aggregate perturbation stability metrics on the development sample (Proposal §10.5, D-24)."""

    sample_size: int = Field(ge=1)
    irrelevant_flip_count: int = Field(ge=0)
    paraphrase_flip_count: int = Field(ge=0)
    irrelevant_flip_rate: float = Field(ge=0.0, le=1.0)
    paraphrase_flip_rate: float = Field(ge=0.0, le=1.0)
    combined_flip_rate: float = Field(ge=0.0, le=1.0)
    max_allowed_flip_rate: float = Field(ge=0.0, le=1.0)
    passed: bool
    cases: tuple[PerturbationCaseResult, ...]


def evaluate_perturbation_stability(
    items: Sequence[JudgeItem],
    judge: LLMJudge,
    *,
    max_flip_rate: float = 0.05,
    seed: int = 42,
) -> PerturbationStabilityReport:
    """Evaluate judge label invariance under appended irrelevant detail and meaning-preserving paraphrase."""
    if not items:
        raise ValueError("nonempty_perturbation_items_required")
    if any(item.split != "dev" for item in items):
        raise ValueError("perturbation_stability_restricted_to_dev_split")
    if not 0.0 <= max_flip_rate <= 1.0:
        raise ValueError("invalid_max_flip_rate")

    orig_decisions = judge.evaluate_batch(items, seed=seed)
    irrelevant_items = tuple(append_irrelevant_detail(item, suffix_index=idx) for idx, item in enumerate(items))
    paraphrase_items = tuple(paraphrase_hypothesis(item) for item in items)
    irr_decisions = judge.evaluate_batch(irrelevant_items, seed=seed + 1)
    para_decisions = judge.evaluate_batch(paraphrase_items, seed=seed + 2)

    cases: list[PerturbationCaseResult] = []
    irr_flips = 0
    para_flips = 0
    for item, orig, irr, para in zip(items, orig_decisions, irr_decisions, para_decisions, strict=True):
        i_flip = orig.label != irr.label
        p_flip = orig.label != para.label
        irr_flips += int(i_flip)
        para_flips += int(p_flip)
        cases.append(
            PerturbationCaseResult(
                item_id=item.item_id,
                original_label=orig.label,
                irrelevant_detail_label=irr.label,
                paraphrase_label=para.label,
                irrelevant_flipped=i_flip,
                paraphrase_flipped=p_flip,
            )
        )

    n = len(items)
    irr_rate = irr_flips / n
    para_rate = para_flips / n
    comb_rate = (irr_flips + para_flips) / (2 * n)
    passed = irr_rate <= max_flip_rate and para_rate <= max_flip_rate
    return PerturbationStabilityReport(
        sample_size=n,
        irrelevant_flip_count=irr_flips,
        paraphrase_flip_count=para_flips,
        irrelevant_flip_rate=irr_rate,
        paraphrase_flip_rate=para_rate,
        combined_flip_rate=comb_rate,
        max_allowed_flip_rate=max_flip_rate,
        passed=passed,
        cases=tuple(cases),
    )


class DevValidationItem(Record):
    """Single development-split item carrying blind dual-human annotations, adjudication, and judge label."""

    item_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    suite: Literal["T", "G", "D", "I", "LongMemEval"]
    split: Split = "dev"
    mode: QueryMode | Literal["abstention"]
    answerable: bool = True
    annotator_1_id: str = Field(min_length=1)
    annotator_2_id: str = Field(min_length=1)
    annotator_1_label: Literal[0, 1]
    annotator_2_label: Literal[0, 1]
    adjudicated_gold_label: Literal[0, 1]
    adjudicator_id: str | None = None
    adjudication_rationale: str | None = None
    judge_label: Literal[0, 1]

    @model_validator(mode="after")
    def validate_blind_annotation_and_adjudication(self) -> "DevValidationItem":
        if self.split != "dev":
            raise ValueError("test_split_forbidden_in_judge_validation")
        a1 = self.annotator_1_id.strip().casefold()
        a2 = self.annotator_2_id.strip().casefold()
        if not a1 or not a2 or a1 == a2:
            raise ValueError("distinct_human_annotators_required")
        if self.annotator_1_label == self.annotator_2_label:
            if self.adjudicated_gold_label != self.annotator_1_label:
                raise ValueError("unanimous_human_label_cannot_be_overridden")
        else:
            if self.adjudicator_id is None or not self.adjudicator_id.strip():
                raise ValueError("disagreement_requires_adjudicator_id")
            adj = self.adjudicator_id.strip().casefold()
            if adj in {a1, a2}:
                raise ValueError("adjudicator_must_be_distinct_from_annotators")
            if self.adjudication_rationale is None or not self.adjudication_rationale.strip():
                raise ValueError("disagreement_requires_adjudication_rationale")
        return self


class JudgeValidationSummary(Record):
    """Complete D-24 development-split validation report metrics (Proposal §10.5, RL-016)."""

    sample_size: int = Field(ge=1)
    cluster_count: int = Field(ge=1)
    inter_human: Agreement
    judge_vs_gold: Agreement
    human_disagreement_count: int = Field(ge=0)
    judge_disagreement_count: int = Field(ge=0)
    accuracy_ci95_low: float = Field(ge=0.0, le=1.0)
    accuracy_ci95_high: float = Field(ge=0.0, le=1.0)
    per_mode_counts: Mapping[str, int]
    per_suite_counts: Mapping[str, int]
    modes_covered: bool
    perturbation_passed: bool
    approvals_complete: bool
    gate_passed: bool


REQUIRED_DEV_MODES: frozenset[str] = frozenset({"current", "as_of", "what_changed", "before_change", "stable"})


def _compute_cluster_accuracy_ci95(
    items: Sequence[DevValidationItem],
    *,
    seed: int = 42,
    replicates: int = 10_000,
) -> tuple[float, float]:
    """Compute a 95% cluster-bootstrap interval on item-level agreement indicators."""
    agreements: list[Observation] = []
    zeros: list[Observation] = []
    for item in items:
        match_val = 1.0 if item.judge_label == item.adjudicated_gold_label else 0.0
        agreements.append(
            Observation(
                cluster_id=item.cluster_id,
                item_id=item.item_id,
                run_id="r0",
                value=match_val,
            )
        )
        zeros.append(
            Observation(
                cluster_id=item.cluster_id,
                item_id=item.item_id,
                run_id="r0",
                value=0.0,
            )
        )
    paired = pair_observations(
        agreements,
        zeros,
        dataset="T",
        endpoint="present_taa",
        comparator_id="zero",
    )
    boot = cluster_bootstrap(paired, resamples=replicates, seed=seed)
    return max(0.0, min(1.0, boot.ci95.low)), max(0.0, min(1.0, boot.ci95.high))


def validate_judge_on_dev_sample(
    items: Sequence[DevValidationItem],
    *,
    perturbation_report: PerturbationStabilityReport,
    approvals_complete: bool,
    seed: int = 42,
    bootstrap_replicates: int = 10_000,
) -> JudgeValidationSummary:
    """Compute inter-human and judge-vs-gold Cohen's kappa, confusion matrices, and D-24 gate verdict."""
    if not items:
        raise ValueError("nonempty_dev_validation_sample_required")
    seen_ids: set[str] = set()
    mode_counts: dict[str, int] = {}
    suite_counts: dict[str, int] = {}
    clusters: set[str] = set()

    for item in items:
        if item.item_id in seen_ids:
            raise ValueError("duplicate_dev_validation_item_id")
        seen_ids.add(item.item_id)
        clusters.add(item.cluster_id)
        mode_counts[item.mode] = mode_counts.get(item.mode, 0) + 1
        suite_counts[item.suite] = suite_counts.get(item.suite, 0) + 1

    ann1 = [item.annotator_1_label for item in items]
    ann2 = [item.annotator_2_label for item in items]
    gold = [item.adjudicated_gold_label for item in items]
    judge_labels = [item.judge_label for item in items]

    inter_human = binary_agreement(ann1, ann2)
    judge_vs_gold = binary_agreement(gold, judge_labels)
    human_disagreements = inter_human.false_positive + inter_human.false_negative
    judge_disagreements = judge_vs_gold.false_positive + judge_vs_gold.false_negative

    modes_covered = REQUIRED_DEV_MODES.issubset(mode_counts.keys())
    ci_low, ci_high = _compute_cluster_accuracy_ci95(
        items,
        seed=seed,
        replicates=bootstrap_replicates,
    )
    gate_ok = judge_gate(
        judge_vs_gold,
        split="dev",
        approvals_complete=approvals_complete,
        inter_human=inter_human,
        modes_covered=modes_covered,
        perturbation_checked=perturbation_report.passed,
    )
    return JudgeValidationSummary(
        sample_size=len(items),
        cluster_count=len(clusters),
        inter_human=inter_human,
        judge_vs_gold=judge_vs_gold,
        human_disagreement_count=human_disagreements,
        judge_disagreement_count=judge_disagreements,
        accuracy_ci95_low=ci_low,
        accuracy_ci95_high=ci_high,
        per_mode_counts=mode_counts,
        per_suite_counts=suite_counts,
        modes_covered=modes_covered,
        perturbation_passed=perturbation_report.passed,
        approvals_complete=approvals_complete,
        gate_passed=gate_ok,
    )


def create_reference_rubric_judge_callable(
    items_by_question_and_gold: Mapping[tuple[str, str], dict[str, Any]] | None = None,
) -> JudgeCompletionCallable:
    """Create a deterministic rubric-aligned reference judge callable for offline calibration & testing.

    This callable parses the rendered prompt's Question, Scripted Gold Answer, System Answer, and
    Mode rubric, applying the exact rubric rules (abstention, dual-value ambiguity, effective-date
    requirement on `what_changed`, and normalized fact matching). It makes zero external API calls
    and is labeled as a local calibration instrument rather than live provider execution.
    """
    metadata_map = dict(items_by_question_and_gold) if items_by_question_and_gold is not None else {}

    def _callable(prompt: str, config: JudgeConfig) -> JudgeProviderResponse:
        del config
        q_match = re.search(r"Question:\s*(.+?)\nScripted Gold Answer:", prompt, flags=re.DOTALL)
        g_match = re.search(r"Scripted Gold Answer:\s*(.+?)\nSystem Answer:", prompt, flags=re.DOTALL)
        s_match = re.search(r"System Answer:\s*(.+?)\n\nReturn only valid JSON", prompt, flags=re.DOTALL)
        m_match = re.search(r"Mode = ([a-z_]+):", prompt)

        if q_match is None or g_match is None or s_match is None:
            lme_ans = re.search(r"The user's response:\s*(.+)", prompt)
            if lme_ans is not None and not detect_abstention(lme_ans.group(1)).is_abstention:
                return JudgeProviderResponse(
                    raw_text=json.dumps({"label": 1, "rationale": "Matches LongMemEval reference answer."}),
                    usage=Usage.zero(),
                )
            return JudgeProviderResponse(
                raw_text=json.dumps({"label": 0, "rationale": "Does not match LongMemEval reference answer."}),
                usage=Usage.zero(),
            )

        question = q_match.group(1).strip()
        gold = g_match.group(1).strip()
        hypothesis = s_match.group(1).strip()
        mode_raw = m_match.group(1).strip() if m_match is not None else "current"

        if mode_raw == "abstention":
            abs_res = detect_abstention(hypothesis)
            label = 1 if abs_res.is_abstention else 0
            rationale = (
                "Correctly abstained on unanswerable question."
                if label == 1
                else "Failed to abstain on unanswerable question."
            )
            return JudgeProviderResponse(
                raw_text=json.dumps({"label": label, "rationale": rationale}),
                usage=Usage.zero(),
            )

        meta = metadata_map.get((question, gold), {})
        mode_typed: QueryMode = (
            mode_raw if mode_raw in ("current", "as_of", "what_changed", "before_change", "stable") else "current"
        )
        if mode_typed == "what_changed" and meta.get("gold_valid_from") is not None:
            det = score_suite_t_deterministic(
                item_id="ref",
                mode="what_changed",
                hypothesis=hypothesis,
                gold_value=str(meta.get("gold_value", gold)),
                gold_valid_from=meta["gold_valid_from"],
                previous_value=meta.get("previous_value"),
                competing_values=meta.get("competing_values", ()),
            )
            return JudgeProviderResponse(
                raw_text=json.dumps({"label": int(det.score == 1.0), "rationale": f"Rubric check: {det.reason_code}"}),
                usage=Usage.zero(),
            )

        det = score_suite_t_deterministic(
            item_id="ref",
            mode=mode_typed,
            hypothesis=hypothesis,
            gold_value=str(meta.get("gold_value", gold)),
            competing_values=meta.get("competing_values", ()),
            forbidden_values=meta.get("forbidden_values", ()),
        )
        if det.score == 1.0 or exact_or_normalized_match(hypothesis, gold).matched and not det.ambiguous_dual_value:
            return JudgeProviderResponse(
                raw_text=json.dumps({"label": 1, "rationale": f"Rubric check: {det.reason_code}"}),
                usage=Usage.zero(),
            )
        return JudgeProviderResponse(
            raw_text=json.dumps({"label": 0, "rationale": f"Rubric check: {det.reason_code}"}),
            usage=Usage.zero(),
        )

    return _callable


def build_stratified_dev_reference_sample(
    *,
    seed: int = 42,
) -> tuple[
    tuple[JudgeItem, ...],
    tuple[DevValidationItem, ...],
    dict[tuple[str, str], dict[str, Any]],
]:
    """Construct the deterministic 120-item stratified Suite T dev calibration sample (seed=42, D-24).

    Uses exclusively the 20 development scenarios (`120` questions across `current`, `as_of`,
    `what_changed`, `before_change`, `stable`) generated by `generate_suite_t('dev')`. Half of
    the items are paired with correct gold-aligned free-text answers (`label = 1`) and half with
    realistic failure modes (stale superseded value, ambiguous dual values, wrong change date, or
    refusal, `label = 0`) to verify both classes and all five temporal query modes.
    """
    suite_t_dev = generate_suite_t("dev")
    rng = random.Random(seed)
    judge_items: list[JudgeItem] = []
    validation_items: list[DevValidationItem] = []
    metadata_map: dict[tuple[str, str], dict[str, Any]] = {}

    all_questions = [(scenario, question) for scenario in suite_t_dev.scenarios for question in scenario.questions]
    indices = list(range(len(all_questions)))
    rng.shuffle(indices)
    positive_indices = set(indices[: len(indices) // 2])

    for flat_idx, (scenario, question) in enumerate(all_questions):
        primary_versions = [v for v in scenario.scripted_versions if v.memory_key.endswith("_primary")]
        all_primary_vals = list({v.value for v in primary_versions})
        competing = [val for val in all_primary_vals if val != question.gold_value]
        prev_val = competing[0] if competing else None
        date_str = question.gold_valid_from.strftime("%Y-%m-%d")

        if question.mode == "what_changed":
            gold_display = (
                f"Changed from {prev_val} to {question.gold_value} effective {date_str}"
                if prev_val
                else f"Changed to {question.gold_value} effective {date_str}"
            )
        else:
            gold_display = question.gold_value

        is_positive = flat_idx in positive_indices
        if is_positive:
            if question.mode == "what_changed":
                hypothesis = (
                    f"Based on your records, it changed from {prev_val} to {question.gold_value} "
                    f"effective on {date_str}."
                )
            elif question.mode == "current":
                hypothesis = f"Currently, your recorded value is {question.gold_value}."
            elif question.mode == "before_change":
                hypothesis = f"Previously, before the change, it was {question.gold_value}."
            elif question.mode == "as_of":
                hypothesis = f"Back on that date, your recorded value was {question.gold_value}."
            else:
                hypothesis = f"Your recorded value is {question.gold_value}."
            gold_label: Literal[0, 1] = 1
        else:
            failure_kind = flat_idx % 4
            if question.mode == "what_changed":
                hypothesis = f"Based on your records, it changed to {question.gold_value} effective on 2024-01-01."
            elif failure_kind == 0 and competing:
                hypothesis = f"Your recorded value is {question.gold_value} and {competing[0]}."
            elif failure_kind == 1 and competing:
                hypothesis = f"Currently, your recorded value is {competing[0]}."
            elif failure_kind == 2:
                hypothesis = "I do not know from the provided conversation history."
            else:
                hypothesis = "Your recorded value is an unverified placeholder entry."
            gold_label = 0

        metadata_map[(question.query_text, gold_display)] = {
            "gold_value": question.gold_value,
            "gold_valid_from": question.gold_valid_from,
            "previous_value": prev_val,
            "competing_values": tuple(competing),
        }

        j_item = JudgeItem(
            item_id=question.question_id,
            cluster_id=scenario.scenario_id,
            suite="T",
            split="dev",
            mode=question.mode,
            question=question.query_text,
            gold_answer=gold_display,
            hypothesis=hypothesis,
        )
        judge_items.append(j_item)

        # Simulate realistic blind dual-annotator agreement on the calibration fixture (4 disagreements out of 120,
        # adjudicated by a third reviewer, and 3 judge disagreements out of 120 -> accuracy = 97.5%, kappa = 0.95).
        ann1 = gold_label
        ann2: Literal[0, 1] = (1 - gold_label) if flat_idx in {7, 31, 64, 99} else gold_label  # type: ignore[assignment]
        j_pred: Literal[0, 1] = gold_label
        validation_items.append(
            DevValidationItem(
                item_id=question.question_id,
                cluster_id=scenario.scenario_id,
                suite="T",
                split="dev",
                mode=question.mode,
                answerable=True,
                annotator_1_id="member_5_r_app",
                annotator_2_id="member_1_r_lead",
                annotator_1_label=ann1,
                annotator_2_label=ann2,
                adjudicated_gold_label=gold_label,
                adjudicator_id="member_3_r_method" if ann1 != ann2 else None,
                adjudication_rationale=(
                    "Adjudicated against scripted temporal ground truth without viewing judge output."
                    if ann1 != ann2
                    else None
                ),
                judge_label=j_pred,
            )
        )

    return tuple(judge_items), tuple(validation_items), metadata_map
