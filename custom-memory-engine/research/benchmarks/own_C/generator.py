"""Deterministic script-first generator for Own Suite C (coding-agent CI-failure patterns, E-022).

The suite freezes five scenario families from the ticket's §5 table: recall of a prior failed
fix attempt, reuse of a stored CI procedure on a recurrence, a planted near-miss control with
the same symptom but a different recorded cause, cross-session repo facts, and an abstention
control with no supporting episode. Content is assembled from fixed template tables with a
seeded per-scenario canary; the instrument is local and deterministic and no provider, judge
or network call is made. Scoring-only labels are frozen inside the JSON payload and must be
stripped before anything is dispatched to a system under test (PreparedCase.case / .gold).
"""

import argparse
import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from rich.console import Console

from custom_memory_engine.engine.channels import Verification
from custom_memory_engine.model.axes import Kind, Lifecycle
from custom_memory_engine.protocols import zero_leak_upper
from evaluation.judge.deterministic import (
    detect_abstention,
    exact_or_normalized_match,
    tokenize_normalized,
)

logger = structlog.get_logger(__name__)
console = Console()

BENCHMARK_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_DIR / "data"
MANIFEST_PATH = BENCHMARK_DIR / "MANIFEST.txt"

DEFAULT_DEV_SEED = 102501
DEFAULT_TEST_SEED = 202501
DEFAULT_DEV_SCENARIOS = 20
DEFAULT_TEST_SCENARIOS = 50
BASE_TIME = datetime(2025, 5, 1, 10, 0, tzinfo=UTC)

Family = Literal[
    "prior_attempt_recall",
    "procedure_reuse",
    "near_miss_control",
    "repo_fact_recall",
    "abstention_control",
]

FAMILY_CYCLE: tuple[Family, ...] = (
    "prior_attempt_recall",
    "procedure_reuse",
    "near_miss_control",
    "repo_fact_recall",
    "abstention_control",
)

ProbeType = Literal["prior_attempt_query", "procedure_offer", "repo_fact_query", "abstention_query"]
ProbeMode = Literal["qa", "offer", "abstain"]
SourceType = Literal["turn", "tool", "file", "event"]

# Fictional repo/error-pattern catalog: four independently authored entries cycled across a
# family. `preconditions` is the environment the stored recipe matches; `alternate_binding` is
# the planted near-miss change (same error-signature text, a different recorded cause).
PATTERN_CATALOG: tuple[dict[str, object], ...] = (
    {
        "repo": "helios-ingest",
        "error_name": "IngestPoolTimeout",
        "signature_line": "IngestPoolTimeout: no ingest pool slot became free within 5s",
        "preconditions": (("worker_concurrency", "12"), ("pool_max", "10")),
        "alternate_binding": ("worker_concurrency", "4"),
        "alternate_cause": "a scheduler retry storm is opening extra ingest attempts, not the pool cap",
        "recipe_steps": "raise pool_max to 20, restart the ingest workers, then run tools/verify:pool",
        "attempt_change": "a pool-cap bump in src/ingest/pool.py",
        "attempt_file": "src/ingest/pool.py",
        "attempt_diff": "+            pool_max = 20",
    },
    {
        "repo": "bramble-queue",
        "error_name": "SchedulerStall",
        "signature_line": "SchedulerStall: the job queue lock stayed held beyond its TTL",
        "preconditions": (("job_workers", "2"), ("lock_ttl", "300s")),
        "alternate_binding": ("job_workers", "4"),
        "alternate_cause": "a poisoned payload keeps one lock forked indefinitely, not the TTL",
        "recipe_steps": "raise the queue lock TTL to 900s, rerun the sweep, then run tools/verify:queue",
        "attempt_change": "a lock-TTL bump in cfg/scheduler.toml",
        "attempt_file": "cfg/scheduler.toml",
        "attempt_diff": '+            lock_ttl = "900s"',
    },
    {
        "repo": "corvid-docs",
        "error_name": "RenderCacheMiss",
        "signature_line": "RenderCacheMiss: doc render cache misses stay above 50 percent",
        "preconditions": (("cache_backend", "tmpfs"), ("render_workers", "3")),
        "alternate_binding": ("cache_backend", "s3"),
        "alternate_cause": "a corrupted cache key format, not the storage backend noise",
        "recipe_steps": "clear the stale render lock, re-pin cache_backend to tmpfs, then run tools/verify:render",
        "attempt_change": "a lock-cleanup change in tools/render/sweep.py",
        "attempt_file": "tools/render/sweep.py",
        "attempt_diff": '+            render_backend = "tmpfs"',
    },
    {
        "repo": "alpine-metrics",
        "error_name": "MetricsSkew",
        "signature_line": "MetricsSkew: ingest lag exceeds the 30s alert threshold",
        "preconditions": (("batch_window", "30s"), ("downstream_api", "stable")),
        "alternate_binding": ("downstream_api", "degraded"),
        "alternate_cause": "a throttled downstream collector, not the batch window",
        "recipe_steps": "pin batch_window back to 30s after the operator edit, then run tools/verify:lag",
        "attempt_change": "a batch-window bump in conf/collector.ini",
        "attempt_file": "conf/collector.ini",
        "attempt_diff": '+            batch_window = "60s"',
    },
)

REPO_BUILD_INVARIANTS: dict[str, str] = {
    "helios-ingest": "Builds must run tools/setup:env before the unit tests.",
    "bramble-queue": "The queue simulator refuses to start unless tools/gen:cert ran first.",
    "corvid-docs": "Doc builds pin the preview index before any template edit merges.",
    "alpine-metrics": "The dashboard build requires vendored tools/vendor:pull to pass.",
}

FILLER_NOTE: str = "rotation of the staging runner credentials is scheduled for next quarter"


def catalog_binding_keys(entry: dict[str, object]) -> tuple[str, ...]:
    """Return the recorded trigger precondition keys of a catalog entry."""
    return tuple(str(key) for key, _value in _catalog_bindings(entry))


def _catalog_bindings(entry: dict[str, object]) -> tuple[tuple[str, str], ...]:
    bindings = entry["preconditions"]
    if not isinstance(bindings, tuple):
        raise ValueError("catalog_bindings_tuple_required")
    return bindings


def _entry(template_index: int) -> dict[str, object]:
    return PATTERN_CATALOG[template_index % len(PATTERN_CATALOG)]


# --- frozen contracts -----------------------------------------------------------------------------------


class PreconditionBinding(BaseModel):
    """One observed environment binding; near-miss siblings differ in exactly one value."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)


class CodingSourceSpec(BaseModel):
    """Verbatim captured source with a span that must be a substring of its content."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source_type: SourceType
    ref: str = Field(min_length=1)
    content: str = Field(min_length=1)
    span: str = Field(min_length=1)


class CodingEpisode(BaseModel):
    """One stored artifact; axes are the E-021 vocabulary, sources the E-013 non-turn types."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    episode_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Kind
    lifecycle: Lifecycle
    verification: Verification
    content: str = Field(min_length=1)
    source: CodingSourceSpec
    event_time: AwareDatetime


class ProcedureSpec(BaseModel):
    """The stored CI recipe the suite matches on recorded preconditions, never on similarity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    procedure_id: str = Field(min_length=1)
    trigger_signature: str = Field(min_length=1)
    preconditions: tuple[PreconditionBinding, ...] = Field(min_length=1)
    rendered_steps: str = Field(min_length=1)


class OfferExpectation(BaseModel):
    """Scoring-only label: whether the procedure must be offered for this probe."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    procedure_id: str = Field(min_length=1)
    should_offer: bool


class CodingProbe(BaseModel):
    """One probe with scoring-only labels never handed to a system under test."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    probe_id: str = Field(min_length=1)
    probe_type: ProbeType
    mode: ProbeMode
    query_text: str = Field(min_length=1)
    gold_answer: str | None
    gold_evidence_episode_ids: tuple[str, ...] = ()
    is_near_miss_control: bool = False
    expected_offer: OfferExpectation | None = None
    observed_bindings: tuple[PreconditionBinding, ...] = ()
    observed_signature: str | None = None


class CodingScenario(BaseModel):
    """One synthetic developer/repo scenario in a repo scope (E-011), single primary family."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str = Field(min_length=1)
    split: Literal["dev", "test"]
    seed: int
    family: Family
    user_id: str = Field(min_length=1)
    scope_ref: str = Field(min_length=1)
    canary: str = Field(min_length=1)
    episodes: tuple[CodingEpisode, ...] = Field(min_length=1)
    procedure: ProcedureSpec | None = None
    probes: tuple[CodingProbe, ...] = Field(min_length=1)


class SuiteCDataset(BaseModel):
    """Complete deterministic Suite C split with expected-zero near-miss false offers."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    suite: Literal["own_C"] = "own_C"
    split: Literal["dev", "test"]
    seed: int
    scenario_count: int
    total_probes: int
    family_scenario_counts: dict[str, int]
    family_probe_counts: dict[str, int]
    near_miss_control_probes: int
    abstention_probes: int
    procedure_channel_episodes: int
    expected_false_offers: int = 0
    false_offer_cp_975_upper_bound: float
    scenarios: tuple[CodingScenario, ...]


# --- deterministic text scaffolding ---------------------------------------------------------------------


def _make_canary(rng: random.Random, split: str, idx: int) -> str:
    token = "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=12))
    return f"OPNOTE-{split.upper()}-{idx:03d}-{token}"


def _bindings_text(bindings: Sequence[tuple[str, str] | PreconditionBinding]) -> str:
    rendered: list[str] = []
    for binding in bindings:
        key, value = (binding.key, binding.value) if isinstance(binding, PreconditionBinding) else binding
        rendered.append(f"{key}={value}")
    return "; ".join(rendered)


def _episode(
    scenario_id: str,
    name: str,
    episode_seq: int,
    kind: Kind,
    lifecycle: Lifecycle,
    verification: Verification,
    source_type: SourceType,
    ref: str,
    content: str,
    span: str,
    event_time: datetime,
) -> CodingEpisode:
    return CodingEpisode(
        episode_id=f"{scenario_id}_ep{episode_seq:02d}_{name}",
        name=name,
        kind=kind,
        lifecycle=lifecycle,
        verification=verification,
        content=content,
        source=CodingSourceSpec(source_type=source_type, ref=ref, content=content, span=span),
        event_time=event_time,
    )


def _filler_episode(scenario_id: str, episode_seq: int, repo: str, event_time: datetime) -> CodingEpisode:
    """One unrelated retained note per scenario (interference, never part of probe evidence)."""
    content = f"Standing note on {repo}: {FILLER_NOTE}."
    return _episode(
        scenario_id,
        "filler_note",
        episode_seq,
        "claim",
        "asserted",
        "none",
        "turn",
        f"turn:{repo}/standing-note",
        content,
        f"Standing note on {repo}",
        event_time,
    )


# --- family builders ------------------------------------------------------------------------------------


def _build_prior_attempt(
    scenario_id: str,
    entry: dict[str, object],
    canary: str,
    day: datetime,
) -> tuple[tuple[CodingEpisode, ...], None, tuple[CodingProbe, ...]]:
    """A fix attempt that failed, later queried as 'what did we try before?' (ticket §5)."""
    repo = str(entry["repo"])
    error_name = str(entry["error_name"])
    signature_line = str(entry["signature_line"])
    attempt_file = str(entry["attempt_file"])
    attempt_change = str(entry["attempt_change"])
    attempt_diff = str(entry["attempt_diff"])
    run_token = f"run-{canary}"

    failure = _episode(
        scenario_id,
        "ci_failure",
        1,
        "episode",
        "observed",
        "none",
        "tool",
        f"tool:ci:{repo}/run",
        f"{run_token}\npytest -q {repo}\n{signature_line}\nexit status 1\n",
        signature_line,
        day,
    )
    attempt = _episode(
        scenario_id,
        "fix_attempt_diff",
        2,
        "episode",
        "observed",
        "none",
        "file",
        f"file:{repo}/{attempt_file}.diff",
        f"--- a/{attempt_file}\n+++ b/{attempt_file}\n@@ -2,0 +3,1 @@\n{attempt_diff}\n",
        attempt_diff,
        day + timedelta(minutes=5),
    )
    outcome = _episode(
        scenario_id,
        "attempt_outcome",
        3,
        "episode",
        "observed",
        "none",
        "event",
        f"event:ci:{repo}/rerun",
        f"rerun {run_token}\n{signature_line}\noutcome: the fix attempt failed; change reverted.\n",
        f"rerun {run_token}",
        day + timedelta(minutes=10),
    )
    filler = _filler_episode(scenario_id, 4, repo, day + timedelta(minutes=15))
    probe = CodingProbe(
        probe_id=f"{scenario_id}_p01_prior_attempt",
        probe_type="prior_attempt_query",
        mode="qa",
        query_text=f"What fix did we already try for {error_name} in {repo}, and what happened?",
        gold_answer=(f"We added {attempt_change}, but {error_name} still occurred and the change was reverted."),
        gold_evidence_episode_ids=(attempt.episode_id, outcome.episode_id),
    )
    return (failure, attempt, outcome, filler), None, (probe,)


def _stored_bindings(entry: dict[str, object]) -> tuple[PreconditionBinding, ...]:
    return tuple(PreconditionBinding(key=str(key), value=str(value)) for key, value in _catalog_bindings(entry))


def _recipe_episode(
    scenario_id: str,
    entry: dict[str, object],
    episode_seq: int,
    event_time: datetime,
) -> CodingEpisode:
    """The asserted CI procedure artifact; its channel contract is `procedures` (E-017)."""
    repo = str(entry["repo"])
    error_name = str(entry["error_name"])
    recipe_steps = str(entry["recipe_steps"])
    content = f"Stored procedure for {error_name} on {repo}: {recipe_steps}."
    return _episode(
        scenario_id,
        "stored_recipe",
        episode_seq,
        "procedure",
        "asserted",
        "none",
        "turn",
        f"turn:{repo}/ci-recipe",
        content,
        recipe_steps,
        event_time,
    )


def _build_procedure_reuse(
    scenario_id: str,
    entry: dict[str, object],
    canary: str,
    day: datetime,
) -> tuple[tuple[CodingEpisode, ...], ProcedureSpec, tuple[CodingProbe, ...]]:
    """A stored recipe re-offered on a recurrence with identical preconditions (ticket §5)."""
    repo = str(entry["repo"])
    error_name = str(entry["error_name"])
    signature_line = str(entry["signature_line"])
    stored_bindings = _stored_bindings(entry)
    recipe_steps = str(entry["recipe_steps"])
    env_token = f"env-{canary}"

    failure = _episode(
        scenario_id,
        "trigger_failure",
        1,
        "episode",
        "observed",
        "none",
        "tool",
        f"tool:ci:{repo}/first-run",
        f"{env_token} on {repo}\n{signature_line}\nbindings: {_bindings_text(stored_bindings)}\n",
        signature_line,
        day,
    )
    recipe = _recipe_episode(scenario_id, entry, 2, day + timedelta(minutes=5))
    filler = _filler_episode(scenario_id, 3, repo, day + timedelta(minutes=10))
    recurrence = _episode(
        scenario_id,
        "recurrence_failure",
        4,
        "episode",
        "observed",
        "none",
        "event",
        f"event:ci:{repo}/recur-run",
        f"pipeline stage verify on {repo} failed again\n{signature_line}\nexit status 1\n",
        signature_line,
        day + timedelta(days=14),
    )
    recurrence_env = _episode(
        scenario_id,
        "recurrence_environment",
        5,
        "episode",
        "observed",
        "none",
        "tool",
        f"tool:ci:{repo}/recur-env",
        (f"{env_token} probe on {repo}\n{signature_line}\nbindings: {_bindings_text(stored_bindings)}\n"),
        f"bindings: {_bindings_text(stored_bindings)}",
        day + timedelta(days=14, minutes=5),
    )
    procedure = ProcedureSpec(
        procedure_id=f"{scenario_id}_proc",
        trigger_signature=signature_line,
        preconditions=stored_bindings,
        rendered_steps=recipe_steps,
    )
    probe = CodingProbe(
        probe_id=f"{scenario_id}_p01_procedure_reuse",
        probe_type="procedure_offer",
        mode="offer",
        query_text=(f"CI on {repo} is failing again with {error_name}. What is our stored procedure for this?"),
        gold_answer=None,
        gold_evidence_episode_ids=(recurrence.episode_id, recurrence_env.episode_id),
        is_near_miss_control=False,
        expected_offer=OfferExpectation(procedure_id=procedure.procedure_id, should_offer=True),
        observed_bindings=stored_bindings,
        observed_signature=signature_line,
    )
    return (failure, recipe, filler, recurrence, recurrence_env), procedure, (probe,)


def _build_near_miss(
    scenario_id: str,
    entry: dict[str, object],
    canary: str,
    day: datetime,
) -> tuple[tuple[CodingEpisode, ...], ProcedureSpec, tuple[CodingProbe, ...]]:
    """Same symptom, one precondition binding changed: the recipe must NOT be re-offered."""
    repo = str(entry["repo"])
    error_name = str(entry["error_name"])
    signature_line = str(entry["signature_line"])
    stored_bindings = _stored_bindings(entry)
    alternate_key, alternate_value = _alternate_binding(entry)
    alternate_cause = str(entry["alternate_cause"])
    recipe_steps = str(entry["recipe_steps"])
    env_token = f"env-{canary}"

    if alternate_key not in catalog_binding_keys(entry):
        raise ValueError(f"catalog_alternate_binding_key_unknown: {scenario_id}")

    observed_bindings = tuple(
        PreconditionBinding(key=str(key), value=str(alternate_value if key == alternate_key else value))
        for key, value in _catalog_bindings(entry)
    )
    failure = _episode(
        scenario_id,
        "first_failure",
        1,
        "episode",
        "observed",
        "none",
        "tool",
        f"tool:ci:{repo}/first-run",
        f"{env_token} on {repo}\n{signature_line}\nbindings: {_bindings_text(stored_bindings)}\n",
        signature_line,
        day,
    )
    recipe = _recipe_episode(scenario_id, entry, 2, day + timedelta(minutes=5))
    recurrence = _episode(
        scenario_id,
        "renewed_failure",
        3,
        "episode",
        "observed",
        "none",
        "event",
        f"event:ci:{repo}/renewed-run",
        f"pipeline stage verify on {repo} failed again\n{signature_line}\nexit status 1\n",
        signature_line,
        day + timedelta(days=14),
    )
    renewed_env = _episode(
        scenario_id,
        "renewed_environment",
        4,
        "episode",
        "observed",
        "none",
        "tool",
        f"tool:ci:{repo}/renewed-env",
        (
            f"{env_token} probe on {repo}\n{signature_line}\n"
            f"bindings: {_bindings_text(observed_bindings)}\n"
            f"diagnosis: {alternate_cause}\n"
        ),
        f"bindings: {_bindings_text(observed_bindings)}",
        day + timedelta(days=14, minutes=5),
    )
    hypothesis = _episode(
        scenario_id,
        "reapply_hypothesis",
        5,
        "abstraction",
        "inferred",
        "unverified",
        "tool",
        f"tool:ci:{repo}/renew-hypothesis",
        (
            f"Suggestion: the renewed {error_name} on {repo} looks like the earlier failure; "
            "reapplying the stored procedure steps may stop the recurrence."
        ),
        "reapplying the stored procedure steps",
        day + timedelta(days=14, minutes=10),
    )
    filler = _filler_episode(scenario_id, 6, repo, day + timedelta(days=14, minutes=15))
    procedure = ProcedureSpec(
        procedure_id=f"{scenario_id}_proc",
        trigger_signature=signature_line,
        preconditions=stored_bindings,
        rendered_steps=recipe_steps,
    )
    probe = CodingProbe(
        probe_id=f"{scenario_id}_p01_near_miss",
        probe_type="procedure_offer",
        mode="offer",
        query_text=(f"CI on {repo} is failing again with {error_name}. What is our stored procedure for this?"),
        gold_answer=None,
        gold_evidence_episode_ids=(recurrence.episode_id, renewed_env.episode_id),
        is_near_miss_control=True,
        expected_offer=OfferExpectation(procedure_id=procedure.procedure_id, should_offer=False),
        observed_bindings=observed_bindings,
        observed_signature=signature_line,
    )
    return (failure, recipe, recurrence, renewed_env, hypothesis, filler), procedure, (probe,)


def _alternate_binding(entry: dict[str, object]) -> tuple[str, str]:
    binding = entry["alternate_binding"]
    if not isinstance(binding, tuple) or len(binding) != 2:
        raise ValueError("catalog_alternate_binding_pair_required")
    return str(binding[0]), str(binding[1])


def _build_repo_fact(
    scenario_id: str,
    entry: dict[str, object],
    canary: str,
    day: datetime,
) -> tuple[tuple[CodingEpisode, ...], None, tuple[CodingProbe, ...]]:
    """A build invariant recorded once, queried in a later session (ticket §5)."""
    repo = str(entry["repo"])
    invariant_sentence = REPO_BUILD_INVARIANTS[repo]

    claim_content = f"Recorded build invariant for {repo}, ticket {canary}: {invariant_sentence}"
    claim = _episode(
        scenario_id,
        "build_invariant",
        1,
        "claim",
        "asserted",
        "none",
        "turn",
        f"turn:{repo}/build-invariant",
        claim_content,
        invariant_sentence,
        day,
    )
    audit_episode = _episode(
        scenario_id,
        "dependency_audit",
        2,
        "episode",
        "observed",
        "none",
        "event",
        f"event:{repo}/dep-audit",
        f"dependency audit on {repo} (run {canary})\nreport: tools/lock:check found no drift\n",
        "tools/lock:check found no drift",
        day + timedelta(minutes=5),
    )
    filler = _filler_episode(scenario_id, 3, repo, day + timedelta(minutes=10))
    probe = CodingProbe(
        probe_id=f"{scenario_id}_p01_repo_fact",
        probe_type="repo_fact_query",
        mode="qa",
        query_text=f"In {repo}, what does the build require before it succeeds?",
        gold_answer=invariant_sentence,
        gold_evidence_episode_ids=(claim.episode_id,),
    )
    return (claim, audit_episode, filler), None, (probe,)


def _build_abstention(
    scenario_id: str,
    entry: dict[str, object],
    canary: str,
    day: datetime,
) -> tuple[tuple[CodingEpisode, ...], None, tuple[CodingProbe, ...]]:
    """A question with no supporting episode anywhere in the scenario (ticket §5)."""
    repo = str(entry["repo"])

    distractor_one = _episode(
        scenario_id,
        "oncall_note",
        1,
        "claim",
        "asserted",
        "none",
        "turn",
        f"turn:{repo}/oncall-note",
        "Escalation handbook: pages route through the primary scheduling tool first.",
        "pages route through the primary scheduling tool",
        day,
    )
    distractor_two = _episode(
        scenario_id,
        "backup_run",
        2,
        "episode",
        "observed",
        "none",
        "event",
        f"event:{repo}/backup",
        f"backup run {canary}\nnightly snapshot sync finished at 02:14 UTC\n",
        "nightly snapshot sync finished",
        day + timedelta(minutes=5),
    )
    probe = CodingProbe(
        probe_id=f"{scenario_id}_p01_abstention",
        probe_type="abstention_query",
        mode="abstain",
        query_text=f"What is the monthly CI minute quota for {repo} renewals?",
        gold_answer=None,
        gold_evidence_episode_ids=(),
    )
    return (distractor_one, distractor_two), None, (probe,)


# --- generation -----------------------------------------------------------------------------------------


def generate_suite_c(
    split: Literal["dev", "test"],
    *,
    seed: int | None = None,
    scenario_count: int | None = None,
) -> SuiteCDataset:
    """Deterministically generate Suite C coding-agent scenarios for the specified split."""
    resolved_seed = (DEFAULT_DEV_SEED if split == "dev" else DEFAULT_TEST_SEED) if seed is None else seed
    resolved_count = (
        (DEFAULT_DEV_SCENARIOS if split == "dev" else DEFAULT_TEST_SCENARIOS)
        if scenario_count is None
        else scenario_count
    )
    rng = random.Random(resolved_seed)
    scenarios: list[CodingScenario] = []

    for idx in range(1, resolved_count + 1):
        scenario_id = f"own_C_{split}_{idx:03d}"
        family = FAMILY_CYCLE[(idx - 1) % len(FAMILY_CYCLE)]
        template_index = ((idx - 1) // len(FAMILY_CYCLE)) % len(PATTERN_CATALOG)
        entry = _entry(template_index)
        user_id = f"dev_{split}_{idx:03d}"
        scope_ref = f"/u/{user_id}/p/{entry['repo']}/"
        canary = _make_canary(rng, split, idx)
        day = BASE_TIME + timedelta(days=idx)

        if family == "prior_attempt_recall":
            episodes, procedure, probes = _build_prior_attempt(scenario_id, entry, canary, day)
        elif family == "procedure_reuse":
            episodes, procedure, probes = _build_procedure_reuse(scenario_id, entry, canary, day)
        elif family == "near_miss_control":
            episodes, procedure, probes = _build_near_miss(scenario_id, entry, canary, day)
        elif family == "repo_fact_recall":
            episodes, procedure, probes = _build_repo_fact(scenario_id, entry, canary, day)
        else:
            episodes, procedure, probes = _build_abstention(scenario_id, entry, canary, day)

        scenarios.append(
            CodingScenario(
                scenario_id=scenario_id,
                split=split,
                seed=resolved_seed + idx,
                family=family,
                user_id=user_id,
                scope_ref=scope_ref,
                canary=canary,
                episodes=episodes,
                procedure=procedure,
                probes=probes,
            )
        )

    near_miss_probe_count = sum(len(s.probes) for s in scenarios if s.family == "near_miss_control")
    dataset = SuiteCDataset(
        split=split,
        seed=resolved_seed,
        scenario_count=len(scenarios),
        total_probes=sum(len(s.probes) for s in scenarios),
        family_scenario_counts={family: sum(1 for s in scenarios if s.family == family) for family in FAMILY_CYCLE},
        family_probe_counts={
            family: sum(len(s.probes) for s in scenarios if s.family == family) for family in FAMILY_CYCLE
        },
        near_miss_control_probes=near_miss_probe_count,
        abstention_probes=sum(len(s.probes) for s in scenarios if s.family == "abstention_control"),
        procedure_channel_episodes=sum(1 for s in scenarios for episode in s.episodes if episode.kind == "procedure"),
        expected_false_offers=0,
        false_offer_cp_975_upper_bound=round(zero_leak_upper(near_miss_probe_count), 12),
        scenarios=tuple(scenarios),
    )
    verify_suite_c(dataset)
    return dataset


# --- verification ---------------------------------------------------------------------------------------


def verify_suite_c(dataset: SuiteCDataset) -> None:
    """Verify canary isolation, span placement, replay clocks, and scoring-label consistency."""
    seen_canaries: set[str] = set()
    for scenario in dataset.scenarios:
        if scenario.canary in seen_canaries:
            raise ValueError(f"duplicate_canary_across_scenarios: {scenario.scenario_id}")
        seen_canaries.add(scenario.canary)

        episode_ids = {episode.episode_id for episode in scenario.episodes}
        if len(episode_ids) != len(scenario.episodes):
            raise ValueError(f"duplicate_episode_id_in_scenario: {scenario.scenario_id}")

        serialized = scenario.model_dump_json()
        for other in dataset.scenarios:
            if other.scenario_id != scenario.scenario_id and other.canary in serialized:
                raise ValueError(f"canary_leak_between_scenarios: {scenario.scenario_id}")

        previous_time: datetime | None = None
        for episode in scenario.episodes:
            if episode.source.span not in episode.source.content:
                raise ValueError(f"episode_span_not_verbatim: {episode.episode_id}")
            if previous_time is not None and episode.event_time <= previous_time:
                raise ValueError(f"non_increasing_episode_clock: {episode.episode_id}")
            previous_time = episode.event_time

        family_procedure = scenario.procedure
        if family_procedure is not None:
            stored = {(b.key, b.value) for b in family_procedure.preconditions}
            for probe in scenario.probes:
                observed = {(b.key, b.value) for b in probe.observed_bindings}
                if probe.expected_offer is None:
                    raise ValueError(f"offer_probe_expectation_missing: {probe.probe_id}")
                if probe.expected_offer.procedure_id != family_procedure.procedure_id:
                    raise ValueError(f"offer_probe_procedure_mismatch: {probe.probe_id}")
                if probe.expected_offer.should_offer:
                    if observed != stored:
                        raise ValueError(f"reuse_probe_bindings_differ: {probe.probe_id}")
                    if probe.is_near_miss_control:
                        raise ValueError(f"reuse_probe_marked_near_miss: {probe.probe_id}")
                else:
                    if observed == stored:
                        raise ValueError(f"near_miss_probe_bindings_match_storage: {probe.probe_id}")
                    if not probe.is_near_miss_control:
                        raise ValueError(f"near_miss_probe_not_marked: {probe.probe_id}")

        for probe in scenario.probes:
            if probe.mode == "qa":
                if probe.gold_answer is None:
                    raise ValueError(f"answerable_probe_requires_gold: {probe.probe_id}")
            elif probe.mode == "abstain":
                if probe.gold_answer is not None or probe.gold_evidence_episode_ids:
                    raise ValueError(f"abstention_probe_must_be_unsupported: {probe.probe_id}")
            elif probe.gold_answer is not None or not probe.observed_bindings:
                raise ValueError(f"offer_probe_contract_violation: {probe.probe_id}")
            if not set(probe.gold_evidence_episode_ids) <= episode_ids:
                raise ValueError(f"probe_evidence_not_in_scenario: {probe.probe_id}")


# --- scoring instruments (metrics defined in the suite README first; local, deterministic) ---------------


def library_of(scenario: CodingScenario) -> dict[str, ProcedureSpec]:
    """Build the per-scenario procedure library keyed by procedure_id."""
    if scenario.procedure is None:
        raise ValueError(f"scenario_procedure_library_empty: {scenario.scenario_id}")
    return {scenario.procedure.procedure_id: scenario.procedure}


def score_qa_probe(probe: CodingProbe, hypothesis: str) -> float:
    """Score an answerable probe: abstention scores 0; containment of gold scores 1."""
    if probe.gold_answer is None:
        raise ValueError(f"gold_answer_required_for_qa_probe: {probe.probe_id}")
    if detect_abstention(hypothesis).is_abstention:
        return 0.0
    return 1.0 if exact_or_normalized_match(hypothesis, probe.gold_answer).matched else 0.0


def score_abstention_probe(probe: CodingProbe, hypothesis: str) -> float:
    """Score an abstention probe: correct iff the hypothesis abstains (fabrications score 0)."""
    if probe.gold_answer is not None or probe.gold_evidence_episode_ids:
        raise ValueError(f"not_an_abstention_probe: {probe.probe_id}")
    return 1.0 if detect_abstention(hypothesis).is_abstention else 0.0


def offer_by_preconditions(probe: CodingProbe, library: Mapping[str, ProcedureSpec]) -> bool:
    """The suite's scoring matcher: every recorded precondition binding must hold observed."""
    expectation = probe.expected_offer
    if expectation is None:
        raise ValueError(f"offer_expectation_required: {probe.probe_id}")
    procedure = library.get(expectation.procedure_id)
    if procedure is None:
        raise ValueError(f"procedure_missing_from_library: {expectation.procedure_id}")
    observed = {binding.key: binding.value for binding in probe.observed_bindings}
    return all(observed.get(pre.key) == pre.value for pre in procedure.preconditions)


def offer_by_similarity(probe: CodingProbe, library: Mapping[str, ProcedureSpec], threshold: float = 0.5) -> bool:
    """Declared trivial strawman: most token-overlapping trigger, no precondition gate.

    A negative control only. Every near-miss probe repeats its sibling's signature line, so
    the strawman offers on every near-miss scenario; a precondition matcher refuses.
    """
    expectation = probe.expected_offer
    if expectation is None:
        raise ValueError(f"offer_expectation_required: {probe.probe_id}")
    procedure = library.get(expectation.procedure_id)
    if procedure is None:
        raise ValueError(f"procedure_missing_from_library: {expectation.procedure_id}")
    observed_signature = probe.observed_signature
    if observed_signature is None:
        raise ValueError(f"observed_signature_required_for_offer_probe: {probe.probe_id}")
    trigger_tokens = set(tokenize_normalized(procedure.trigger_signature))
    observed_tokens = set(tokenize_normalized(observed_signature))
    if not trigger_tokens or not observed_tokens:
        return False
    overlap = len(trigger_tokens & observed_tokens) / len(trigger_tokens | observed_tokens)
    return overlap >= threshold


def score_offer_probe(probe: CodingProbe, library: Mapping[str, ProcedureSpec]) -> float:
    """Score an offer probe: the precondition matcher's decision must equal the expectation."""
    offered = offer_by_preconditions(probe, library)
    expectation = probe.expected_offer
    if expectation is None:
        raise ValueError(f"offer_expectation_required: {probe.probe_id}")
    return 1.0 if offered == expectation.should_offer else 0.0


# --- write / CLI ----------------------------------------------------------------------------------------


def write_suite_c_files(data_dir: Path = DATA_DIR, manifest_path: Path = MANIFEST_PATH) -> dict[str, str]:
    """Generate dev and test splits for Suite C, write JSON files, and update MANIFEST.txt."""
    data_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    lines: list[str] = []
    for split in ("dev", "test"):
        dataset = generate_suite_c(split)
        filename = f"own_C_{split}.json"
        out_path = data_dir / filename
        payload = dataset.model_dump_json(indent=2) + "\n"
        out_path.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        digests[filename] = digest
        lines.append(f"{digest}  {filename}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for generating and verifying Own Suite C."""
    parser = argparse.ArgumentParser(description="Generate and verify Own Suite C (coding agent).")
    parser.add_argument("--write", action="store_true", help="Write JSON files to data/ and update MANIFEST.txt.")
    args = parser.parse_args(argv)
    if args.write:
        digests = write_suite_c_files()
        logger.info("suite_c_files_written", files=list(digests.keys()))
        console.print_json(json.dumps(digests, indent=2))
    else:
        for split in ("dev", "test"):
            ds = generate_suite_c(split)
            logger.info(
                "suite_c_verified",
                split=split,
                scenario_count=ds.scenario_count,
                total_probes=ds.total_probes,
            )
            console.print(f"{split}: scenarios={ds.scenario_count}, probes={ds.total_probes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
