"""Deterministic dev/test split builder, YAML 1.2 serializer, and Gate J5 verifier (RL-026)."""

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import structlog
from pydantic import Field, model_validator
from rich.console import Console

from evaluation.harness.config import canonical_json
from evaluation.harness.models import Record, Split, SuiteId
from evaluation.harness.paths import reject_links
from research.benchmarks.common import BenchmarkSelection
from research.benchmarks.locomo.fetch import (
    FILENAME as LOCOMO_FILENAME,
)
from research.benchmarks.locomo.fetch import (
    MANIFEST_PATH as LOCOMO_MANIFEST_PATH,
)
from research.benchmarks.locomo.fetch import (
    ensure_locomo_file,
)
from research.benchmarks.locomo.fetch import (
    load_manifest as load_locomo_manifest,
)
from research.benchmarks.longmemeval.fetch import (
    DATA_DIR as LME_DATA_DIR,
)
from research.benchmarks.longmemeval.fetch import (
    MANIFEST_PATH as LME_MANIFEST_PATH,
)
from research.benchmarks.longmemeval.fetch import (
    VARIANT_FILES as LME_VARIANT_FILES,
)
from research.benchmarks.longmemeval.fetch import (
    ensure_variant_file,
)
from research.benchmarks.longmemeval.fetch import (
    load_manifest as load_lme_manifest,
)
from research.benchmarks.own_D.generator import (
    DEFAULT_DEV_SEED as OWN_D_DEV_SEED,
)
from research.benchmarks.own_D.generator import (
    DEFAULT_TEST_SEED as OWN_D_TEST_SEED,
)
from research.benchmarks.own_D.generator import (
    MANIFEST_PATH as OWN_D_MANIFEST_PATH,
)
from research.benchmarks.own_D.generator import (
    generate_suite_d,
)
from research.benchmarks.own_G.generator import (
    DEFAULT_DEV_SEED as OWN_G_DEV_SEED,
)
from research.benchmarks.own_G.generator import (
    DEFAULT_TEST_SEED as OWN_G_TEST_SEED,
)
from research.benchmarks.own_G.generator import (
    MANIFEST_PATH as OWN_G_MANIFEST_PATH,
)
from research.benchmarks.own_G.generator import (
    generate_suite_g,
)
from research.benchmarks.own_I.generator import (
    DEFAULT_DEV_SEED as OWN_I_DEV_SEED,
)
from research.benchmarks.own_I.generator import (
    DEFAULT_TEST_SEED as OWN_I_TEST_SEED,
)
from research.benchmarks.own_I.generator import (
    MANIFEST_PATH as OWN_I_MANIFEST_PATH,
)
from research.benchmarks.own_I.generator import (
    generate_suite_i,
)
from research.benchmarks.own_T.generator import (
    DEFAULT_DEV_SEED as OWN_T_DEV_SEED,
)
from research.benchmarks.own_T.generator import (
    DEFAULT_TEST_SEED as OWN_T_TEST_SEED,
)
from research.benchmarks.own_T.generator import (
    MANIFEST_PATH as OWN_T_MANIFEST_PATH,
)
from research.benchmarks.own_T.generator import (
    generate_suite_t,
)

logger = structlog.get_logger(__name__)
console = Console()

SPLITS_DIR = Path(__file__).resolve().parent
FREEZE_LIST_PATH = SPLITS_DIR / "FREEZE_LIST.md"
DEFAULT_SPLIT_SEED = 42

SplitSuiteFamily = Literal["LongMemEval", "LoCoMo", "T", "G", "D", "I"]
ClusterKey = Literal["question_id", "conversation_id", "scenario_id"]

SPLIT_FILENAMES: dict[str, str] = {
    "longmemeval": "longmemeval.yaml",
    "locomo": "locomo.yaml",
    "own_T": "own_T.yaml",
    "own_G": "own_G.yaml",
    "own_D": "own_D.yaml",
    "own_I": "own_I.yaml",
}


def scenario_id_sha256(scenario_id: str) -> str:
    """Compute the unkeyed SHA-256 hex digest of a scenario_id string."""
    if not scenario_id or not scenario_id.strip():
        raise ValueError("nonempty_scenario_id_required")
    return hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()


def cluster_id_sha256(suite_family: str, seed: int, cluster_id: str) -> str:
    """Compute the deterministic seeded SHA-256 hex digest of a cluster identifier."""
    if not suite_family.strip() or not cluster_id.strip():
        raise ValueError("nonempty_suite_and_cluster_id_required")
    payload = f"{suite_family.lower()}:{seed}:{cluster_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def partition_scenarios_by_sha256(
    scenario_ids: Sequence[str],
    *,
    dev_count: int,
    seed: int = DEFAULT_SPLIT_SEED,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministically partition a sequence of scenario_ids into disjoint (dev, test) tuples via SHA-256."""
    cleaned = [sid.strip() for sid in scenario_ids]
    if not cleaned or any(not sid for sid in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError("distinct_nonempty_scenario_ids_required")
    if type(dev_count) is not int or dev_count < 1 or dev_count >= len(cleaned):
        raise ValueError("invalid_dev_scenario_count")
    ranked = sorted(
        cleaned,
        key=lambda sid: (cluster_id_sha256("scenario", seed, sid), scenario_id_sha256(sid), sid),
    )
    dev_ids = tuple(sorted(ranked[:dev_count]))
    test_ids = tuple(sorted(ranked[dev_count:]))
    return dev_ids, test_ids


def _compute_split_payload_sha256(
    *,
    suite_family: SplitSuiteFamily,
    suite_ids: tuple[SuiteId, ...],
    cluster_key: ClusterKey,
    seed: int,
    dev_generator_seed: int | None,
    test_generator_seed: int | None,
    dataset_files: dict[str, str],
    dev_ids: tuple[str, ...],
    test_ids: tuple[str, ...],
    strata_counts: dict[str, dict[str, int]],
) -> str:
    payload = {
        "cluster_key": cluster_key,
        "dataset_files": dataset_files,
        "dev_generator_seed": dev_generator_seed,
        "dev_ids": list(dev_ids),
        "seed": seed,
        "strata_counts": strata_counts,
        "suite_family": suite_family,
        "suite_ids": list(suite_ids),
        "test_generator_seed": test_generator_seed,
        "test_ids": list(test_ids),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class FrozenSuiteSplit(Record):
    """Immutable Gate J5 split specification for a benchmark or own suite (RL-026)."""

    schema_version: Literal[1] = 1
    task: Literal["RL-026"] = "RL-026"
    gate: Literal["J5"] = "J5"
    status: Literal["frozen"] = "frozen"
    suite_family: SplitSuiteFamily
    suite_ids: tuple[SuiteId, ...] = Field(min_length=1)
    cluster_key: ClusterKey
    assignment_rule: str = Field(min_length=1)
    seed: int
    dev_generator_seed: int | None = None
    test_generator_seed: int | None = None
    dataset_files: dict[str, str]
    dev_count: int = Field(ge=1)
    test_count: int = Field(ge=1)
    total_count: int = Field(ge=2)
    dev_ids: tuple[str, ...] = Field(min_length=1)
    test_ids: tuple[str, ...] = Field(min_length=1)
    dev_id_sha256: dict[str, str]
    test_id_sha256: dict[str, str]
    strata_counts: dict[str, dict[str, int]]
    split_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_disjoint_and_digests(self) -> "FrozenSuiteSplit":
        if len(self.dev_ids) != self.dev_count or len(self.test_ids) != self.test_count:
            raise ValueError("split_count_mismatch")
        if self.dev_count + self.test_count != self.total_count:
            raise ValueError("total_count_mismatch")
        if any(not cid.strip() for cid in self.dev_ids) or any(not cid.strip() for cid in self.test_ids):
            raise ValueError("blank_cluster_id_not_allowed")
        if len(set(self.dev_ids)) != len(self.dev_ids) or len(set(self.test_ids)) != len(self.test_ids):
            raise ValueError("duplicate_cluster_id_in_split")
        if not set(self.dev_ids).isdisjoint(set(self.test_ids)):
            raise ValueError("dev_test_cluster_overlap_detected")
        if not self.dataset_files or any(
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            for digest in self.dataset_files.values()
        ):
            raise ValueError("invalid_dataset_file_digest")
        if set(self.dev_id_sha256.keys()) != set(self.dev_ids):
            raise ValueError("dev_id_sha256_keys_mismatch")
        if set(self.test_id_sha256.keys()) != set(self.test_ids):
            raise ValueError("test_id_sha256_keys_mismatch")

        for cid, digest in self.dev_id_sha256.items():
            expected = (
                scenario_id_sha256(cid)
                if self.cluster_key == "scenario_id"
                else cluster_id_sha256(self.suite_family, self.seed, cid)
            )
            if digest != expected:
                raise ValueError(f"invalid_dev_cluster_sha256: {cid}")

        for cid, digest in self.test_id_sha256.items():
            expected = (
                scenario_id_sha256(cid)
                if self.cluster_key == "scenario_id"
                else cluster_id_sha256(self.suite_family, self.seed, cid)
            )
            if digest != expected:
                raise ValueError(f"invalid_test_cluster_sha256: {cid}")

        if not set(self.dev_id_sha256.values()).isdisjoint(set(self.test_id_sha256.values())):
            raise ValueError("dev_test_sha256_overlap_detected")

        expected_payload_sha = _compute_split_payload_sha256(
            suite_family=self.suite_family,
            suite_ids=self.suite_ids,
            cluster_key=self.cluster_key,
            seed=self.seed,
            dev_generator_seed=self.dev_generator_seed,
            test_generator_seed=self.test_generator_seed,
            dataset_files=self.dataset_files,
            dev_ids=self.dev_ids,
            test_ids=self.test_ids,
            strata_counts=self.strata_counts,
        )
        if self.split_payload_sha256 != expected_payload_sha:
            raise ValueError("split_payload_sha256_mismatch")
        return self


def assign_own_scenario_split_by_sha256(scenario_id: str, split_doc: FrozenSuiteSplit) -> Split:
    """Resolve an own-suite scenario_id to 'dev' or 'test' strictly via its SHA-256 digest."""
    if split_doc.cluster_key != "scenario_id":
        raise ValueError("scenario_cluster_key_required")
    digest = scenario_id_sha256(scenario_id)
    in_dev = split_doc.dev_id_sha256.get(scenario_id) == digest
    in_test = split_doc.test_id_sha256.get(scenario_id) == digest
    if in_dev and not in_test:
        return "dev"
    if in_test and not in_dev:
        return "test"
    raise ValueError(f"unknown_or_ambiguous_scenario_id: {scenario_id}")


def _read_manifest_file(path: Path) -> dict[str, str]:
    reject_links(path)
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"invalid_manifest_line: {path}")
        entries[parts[1]] = parts[0].lower()
    return entries


def build_longmemeval_split(
    seed: int = DEFAULT_SPLIT_SEED,
    *,
    verify_s_variant: bool = False,
) -> FrozenSuiteSplit:
    """Build the stratified 20% dev (100) / 80% test (400) split for LongMemEval with fixed seed."""
    manifest = load_lme_manifest(LME_MANIFEST_PATH)
    oracle_path = ensure_variant_file("oracle", allow_download=False)
    oracle_records = json.loads(oracle_path.read_text(encoding="utf-8"))

    s_path = LME_DATA_DIR / LME_VARIANT_FILES["s"]
    if verify_s_variant and s_path.exists():
        verified_s_path = ensure_variant_file("s", allow_download=False)
        s_records = json.loads(verified_s_path.read_text(encoding="utf-8"))
        oracle_pairs = {(str(r["question_id"]), str(r["question_type"])) for r in oracle_records}
        s_pairs = {(str(r["question_id"]), str(r["question_type"])) for r in s_records}
        if oracle_pairs != s_pairs:
            raise ValueError("longmemeval_oracle_and_s_question_metadata_mismatch")

    groups: dict[tuple[bool, str], list[str]] = defaultdict(list)
    qtype_by_id: dict[str, str] = {}
    for record in oracle_records:
        qid = str(record["question_id"])
        qtype = str(record["question_type"])
        is_abs = qid.endswith("_abs")
        if qid in qtype_by_id:
            raise ValueError(f"duplicate_longmemeval_question_id: {qid}")
        qtype_by_id[qid] = qtype
        groups[(is_abs, qtype)].append(qid)

    dev_list: list[str] = []
    test_list: list[str] = []
    # Exact 20% stratified allocation within answerable (94/470) and abstention (6/30)
    for is_abs, target_dev in ((False, 94), (True, 6)):
        cell_keys = sorted(key for key in groups if key[0] == is_abs)
        quotas: dict[tuple[bool, str], int] = {}
        remainders: list[tuple[int, tuple[bool, str]]] = []
        for key in cell_keys:
            cell_total = len(groups[key])
            quotas[key] = (cell_total * 20) // 100
            remainders.append(((cell_total * 20) % 100, key))
        needed = target_dev - sum(quotas.values())
        remainders.sort(key=lambda item: (-item[0], item[1][1]))
        for _, key in remainders[:needed]:
            quotas[key] += 1
        for key in cell_keys:
            ranked = sorted(
                groups[key],
                key=lambda qid: (cluster_id_sha256("LongMemEval", seed, qid), qid),
            )
            dev_quota = quotas[key]
            dev_list.extend(ranked[:dev_quota])
            test_list.extend(ranked[dev_quota:])

    dev_ids = tuple(sorted(dev_list))
    test_ids = tuple(sorted(test_list))

    def _summarize_lme(ids: Sequence[str]) -> dict[str, int]:
        summary: dict[str, int] = {
            "total_questions": len(ids),
            "answerable": 0,
            "abstention": 0,
            "single_session_assistant": 0,
            "primary_recall_eligible": 0,
        }
        qtypes = sorted({qtype_by_id[i] for i in qtype_by_id})
        for qt in qtypes:
            summary[f"question_type:{qt}"] = 0
        for qid in ids:
            qt = qtype_by_id[qid]
            is_abs = qid.endswith("_abs")
            summary[f"question_type:{qt}"] += 1
            if is_abs:
                summary["abstention"] += 1
            else:
                summary["answerable"] += 1
                if qt == "single-session-assistant":
                    summary["single_session_assistant"] += 1
                else:
                    summary["primary_recall_eligible"] += 1
        return summary

    strata_counts = {
        "dev": _summarize_lme(dev_ids),
        "test": _summarize_lme(test_ids),
    }
    suite_ids: tuple[SuiteId, ...] = ("LongMemEval_oracle", "LongMemEval_S", "LongMemEval_M")
    dev_id_sha256 = {qid: cluster_id_sha256("LongMemEval", seed, qid) for qid in dev_ids}
    test_id_sha256 = {qid: cluster_id_sha256("LongMemEval", seed, qid) for qid in test_ids}
    payload_sha = _compute_split_payload_sha256(
        suite_family="LongMemEval",
        suite_ids=suite_ids,
        cluster_key="question_id",
        seed=seed,
        dev_generator_seed=None,
        test_generator_seed=None,
        dataset_files=dict(sorted(manifest.items())),
        dev_ids=dev_ids,
        test_ids=test_ids,
        strata_counts=strata_counts,
    )
    return FrozenSuiteSplit(
        suite_family="LongMemEval",
        suite_ids=suite_ids,
        cluster_key="question_id",
        assignment_rule=(
            "Stratified 20% dev / 80% test split by (is_abstention, question_type) using "
            "Hamilton largest-remainder quotas and deterministic SHA-256 ranking with seed=42."
        ),
        seed=seed,
        dataset_files=dict(sorted(manifest.items())),
        dev_count=len(dev_ids),
        test_count=len(test_ids),
        total_count=len(dev_ids) + len(test_ids),
        dev_ids=dev_ids,
        test_ids=test_ids,
        dev_id_sha256=dev_id_sha256,
        test_id_sha256=test_id_sha256,
        strata_counts=strata_counts,
        split_payload_sha256=payload_sha,
    )


def build_locomo_split(seed: int = DEFAULT_SPLIT_SEED) -> FrozenSuiteSplit:
    """Build the conversation-level 20% dev (2) / 80% test (8) split for LoCoMo with fixed seed."""
    manifest = load_locomo_manifest(LOCOMO_MANIFEST_PATH)
    locomo_path = ensure_locomo_file(allow_download=False)
    records = json.loads(locomo_path.read_text(encoding="utf-8"))

    by_id: dict[str, dict[str, object]] = {}
    for row in records:
        cid = str(row["sample_id"])
        if cid in by_id:
            raise ValueError(f"duplicate_locomo_conversation_id: {cid}")
        by_id[cid] = row

    ranked = sorted(
        by_id.keys(),
        key=lambda cid: (cluster_id_sha256("LoCoMo", seed, cid), cid),
    )
    dev_quota = (len(ranked) * 20) // 100
    dev_ids = tuple(sorted(ranked[:dev_quota]))
    test_ids = tuple(sorted(ranked[dev_quota:]))

    def _summarize_locomo(cids: Sequence[str]) -> dict[str, int]:
        summary: dict[str, int] = {
            "conversations": len(cids),
            "total_questions": 0,
            "category_1": 0,
            "category_2": 0,
            "category_3": 0,
            "category_4": 0,
            "category_5": 0,
        }
        for cid in cids:
            qa_list = list(by_id[cid]["qa"])  # type: ignore[arg-type]
            summary["total_questions"] += len(qa_list)
            for q in qa_list:
                cat = int(q["category"])
                summary[f"category_{cat}"] += 1
        return summary

    strata_counts = {
        "dev": _summarize_locomo(dev_ids),
        "test": _summarize_locomo(test_ids),
    }
    suite_ids: tuple[SuiteId, ...] = ("LoCoMo",)
    dev_id_sha256 = {cid: cluster_id_sha256("LoCoMo", seed, cid) for cid in dev_ids}
    test_id_sha256 = {cid: cluster_id_sha256("LoCoMo", seed, cid) for cid in test_ids}
    payload_sha = _compute_split_payload_sha256(
        suite_family="LoCoMo",
        suite_ids=suite_ids,
        cluster_key="conversation_id",
        seed=seed,
        dev_generator_seed=None,
        test_generator_seed=None,
        dataset_files={LOCOMO_FILENAME: manifest[LOCOMO_FILENAME]},
        dev_ids=dev_ids,
        test_ids=test_ids,
        strata_counts=strata_counts,
    )
    return FrozenSuiteSplit(
        suite_family="LoCoMo",
        suite_ids=suite_ids,
        cluster_key="conversation_id",
        assignment_rule=(
            "Conversation-level 20% dev (2 conversations) / 80% test (8 conversations) split "
            "for descriptive reporting via deterministic SHA-256 ranking of sample_id with seed=42."
        ),
        seed=seed,
        dataset_files={LOCOMO_FILENAME: manifest[LOCOMO_FILENAME]},
        dev_count=len(dev_ids),
        test_count=len(test_ids),
        total_count=len(dev_ids) + len(test_ids),
        dev_ids=dev_ids,
        test_ids=test_ids,
        dev_id_sha256=dev_id_sha256,
        test_id_sha256=test_id_sha256,
        strata_counts=strata_counts,
        split_payload_sha256=payload_sha,
    )


def build_own_suite_split(
    suite_family: Literal["T", "G", "D", "I"],
    seed: int = DEFAULT_SPLIT_SEED,
) -> FrozenSuiteSplit:
    """Build and verify the scenario-level split and SHA-256 scenario digests for an Own Suite."""
    if suite_family == "T":
        dev_ds = generate_suite_t("dev")
        test_ds = generate_suite_t("test")
        manifest = _read_manifest_file(OWN_T_MANIFEST_PATH)
        dev_seed, test_seed = OWN_T_DEV_SEED, OWN_T_TEST_SEED
        strata_counts = {
            "dev": {
                "scenarios": dev_ds.scenario_count,
                "total_questions": dev_ds.total_questions,
                "history_dependent_questions": dev_ds.history_dependent_questions,
                "present_state_questions": dev_ds.present_state_questions,
                "audit_probes": dev_ds.total_audit_probes,
            },
            "test": {
                "scenarios": test_ds.scenario_count,
                "total_questions": test_ds.total_questions,
                "history_dependent_questions": test_ds.history_dependent_questions,
                "present_state_questions": test_ds.present_state_questions,
                "audit_probes": test_ds.total_audit_probes,
            },
        }
    elif suite_family == "G":
        dev_ds = generate_suite_g("dev")
        test_ds = generate_suite_g("test")
        manifest = _read_manifest_file(OWN_G_MANIFEST_PATH)
        dev_seed, test_seed = OWN_G_DEV_SEED, OWN_G_TEST_SEED
        strata_counts = {
            "dev": {
                "scenarios": dev_ds.scenario_count,
                "total_episodes": dev_ds.total_episodes,
                "legitimate_episodes": dev_ds.legitimate_episodes,
                "non_assertion_episodes": dev_ds.non_assertion_episodes,
                "injected_episodes": dev_ds.injected_episodes,
            },
            "test": {
                "scenarios": test_ds.scenario_count,
                "total_episodes": test_ds.total_episodes,
                "legitimate_episodes": test_ds.legitimate_episodes,
                "non_assertion_episodes": test_ds.non_assertion_episodes,
                "injected_episodes": test_ds.injected_episodes,
            },
        }
    elif suite_family == "D":
        dev_ds = generate_suite_d("dev")
        test_ds = generate_suite_d("test")
        manifest = _read_manifest_file(OWN_D_MANIFEST_PATH)
        dev_seed, test_seed = OWN_D_DEV_SEED, OWN_D_TEST_SEED
        strata_counts = {
            "dev": {
                "scenarios": dev_ds.scenario_count,
                "primary_deletion_events": dev_ds.scenario_count * dev_ds.primary_deletion_events_per_scenario,
                "relearning_control_events": dev_ds.scenario_count * dev_ds.relearning_control_events_per_scenario,
                "in_boundary_channels": len(dev_ds.in_boundary_channels),
                "total_probes": sum(sum(len(ev.probes) for ev in s.events) + 1 for s in dev_ds.scenarios),
            },
            "test": {
                "scenarios": test_ds.scenario_count,
                "primary_deletion_events": test_ds.scenario_count * test_ds.primary_deletion_events_per_scenario,
                "relearning_control_events": test_ds.scenario_count * test_ds.relearning_control_events_per_scenario,
                "in_boundary_channels": len(test_ds.in_boundary_channels),
                "total_probes": sum(sum(len(ev.probes) for ev in s.events) + 1 for s in test_ds.scenarios),
            },
        }
    elif suite_family == "I":
        dev_ds = generate_suite_i("dev")
        test_ds = generate_suite_i("test")
        manifest = _read_manifest_file(OWN_I_MANIFEST_PATH)
        dev_seed, test_seed = OWN_I_DEV_SEED, OWN_I_TEST_SEED
        strata_counts = {
            "dev": {
                "scenarios": dev_ds.scenario_count,
                "total_probes": dev_ds.total_probes,
                "expected_total_exposures": dev_ds.expected_total_exposures,
            },
            "test": {
                "scenarios": test_ds.scenario_count,
                "total_probes": test_ds.total_probes,
                "expected_total_exposures": test_ds.expected_total_exposures,
            },
        }
    else:
        raise ValueError(f"unsupported_own_suite_family: {suite_family}")

    dev_filename = f"own_{suite_family}_dev.json"
    test_filename = f"own_{suite_family}_test.json"
    dev_payload = (dev_ds.model_dump_json(indent=2) + "\n").encode("utf-8")
    test_payload = (test_ds.model_dump_json(indent=2) + "\n").encode("utf-8")
    if hashlib.sha256(dev_payload).hexdigest() != manifest[dev_filename]:
        raise ValueError(f"own_suite_dev_manifest_digest_mismatch: {dev_filename}")
    if hashlib.sha256(test_payload).hexdigest() != manifest[test_filename]:
        raise ValueError(f"own_suite_test_manifest_digest_mismatch: {test_filename}")

    dev_ids = tuple(s.scenario_id for s in dev_ds.scenarios)
    test_ids = tuple(s.scenario_id for s in test_ds.scenarios)
    dev_id_sha256 = {sid: scenario_id_sha256(sid) for sid in dev_ids}
    test_id_sha256 = {sid: scenario_id_sha256(sid) for sid in test_ids}
    suite_ids: tuple[SuiteId, ...] = (suite_family,)
    dataset_files = dict(sorted(manifest.items()))
    payload_sha = _compute_split_payload_sha256(
        suite_family=suite_family,
        suite_ids=suite_ids,
        cluster_key="scenario_id",
        seed=seed,
        dev_generator_seed=dev_seed,
        test_generator_seed=test_seed,
        dataset_files=dataset_files,
        dev_ids=dev_ids,
        test_ids=test_ids,
        strata_counts=strata_counts,
    )
    return FrozenSuiteSplit(
        suite_family=suite_family,
        suite_ids=suite_ids,
        cluster_key="scenario_id",
        assignment_rule=(
            f"Scenario-level split for Own Suite {suite_family} with disjoint seeded generators "
            f"(dev_seed={dev_seed}, test_seed={test_seed}) and deterministic SHA-256(scenario_id) "
            "binding so no scenario ever appears in both splits."
        ),
        seed=seed,
        dev_generator_seed=dev_seed,
        test_generator_seed=test_seed,
        dataset_files=dataset_files,
        dev_count=len(dev_ids),
        test_count=len(test_ids),
        total_count=len(dev_ids) + len(test_ids),
        dev_ids=dev_ids,
        test_ids=test_ids,
        dev_id_sha256=dev_id_sha256,
        test_id_sha256=test_id_sha256,
        strata_counts=strata_counts,
        split_payload_sha256=payload_sha,
    )


def build_all_splits(seed: int = DEFAULT_SPLIT_SEED) -> dict[str, FrozenSuiteSplit]:
    """Construct all 6 frozen suite split specifications deterministically."""
    return {
        "longmemeval": build_longmemeval_split(seed=seed),
        "locomo": build_locomo_split(seed=seed),
        "own_T": build_own_suite_split("T", seed=seed),
        "own_G": build_own_suite_split("G", seed=seed),
        "own_D": build_own_suite_split("D", seed=seed),
        "own_I": build_own_suite_split("I", seed=seed),
    }


def serialize_split_yaml(split_doc: FrozenSuiteSplit) -> str:
    """Serialize a FrozenSuiteSplit as canonical indented JSON (valid YAML 1.2)."""
    data = split_doc.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_split_yaml(path: Path) -> FrozenSuiteSplit:
    """Load and validate a FrozenSuiteSplit from a .yaml (YAML 1.2 / JSON subset) file."""
    reject_links(path)
    return FrozenSuiteSplit.model_validate_json(path.read_bytes())


def split_path_for_suite(suite_id: SuiteId, splits_dir: Path = SPLITS_DIR) -> Path:
    """Return the canonical frozen split .yaml path for a given SuiteId."""
    reject_links(splits_dir)
    if suite_id in ("LongMemEval_oracle", "LongMemEval_S", "LongMemEval_M"):
        return splits_dir / SPLIT_FILENAMES["longmemeval"]
    if suite_id == "LoCoMo":
        return splits_dir / SPLIT_FILENAMES["locomo"]
    if suite_id in ("T", "G", "D", "I"):
        return splits_dir / SPLIT_FILENAMES[f"own_{suite_id}"]
    raise ValueError(f"unsupported_suite_id: {suite_id}")


def load_suite_split(suite_id: SuiteId, splits_dir: Path = SPLITS_DIR) -> FrozenSuiteSplit:
    """Load and validate the FrozenSuiteSplit corresponding to suite_id."""
    return load_split_yaml(split_path_for_suite(suite_id, splits_dir=splits_dir))


def to_benchmark_selection(
    split_doc: FrozenSuiteSplit,
    *,
    suite_id: SuiteId,
    split: Split,
    retrieval_k: int = 5,
) -> BenchmarkSelection:
    """Materialize a version-1 BenchmarkSelection envelope from a FrozenSuiteSplit."""
    if suite_id not in split_doc.suite_ids:
        raise ValueError(f"suite_id_not_in_split_document: {suite_id}")
    if split not in ("dev", "test"):
        raise ValueError(f"invalid_split: {split}")
    if suite_id == "LongMemEval_oracle":
        dataset_filename = "longmemeval_oracle.json"
    elif suite_id == "LongMemEval_S":
        dataset_filename = "longmemeval_s_cleaned.json"
    elif suite_id == "LongMemEval_M":
        dataset_filename = "longmemeval_m_cleaned.json"
    elif suite_id == "LoCoMo":
        dataset_filename = LOCOMO_FILENAME
    else:
        dataset_filename = f"own_{suite_id}_{split}.json"

    if dataset_filename not in split_doc.dataset_files:
        raise ValueError(f"dataset_filename_missing_from_split: {dataset_filename}")

    case_ids = split_doc.dev_ids if split == "dev" else split_doc.test_ids
    return BenchmarkSelection(
        schema_version=1,
        suite_id=suite_id,
        split=split,
        dataset_sha256=split_doc.dataset_files[dataset_filename],
        case_ids=case_ids,
        retrieval_k=retrieval_k,
    )


def write_all_splits(splits_dir: Path = SPLITS_DIR, seed: int = DEFAULT_SPLIT_SEED) -> dict[str, str]:
    """Write all 6 split .yaml files to splits_dir and return a map of filename -> SHA-256."""
    reject_links(splits_dir)
    splits_dir.mkdir(parents=True, exist_ok=True)
    built = build_all_splits(seed=seed)
    file_digests: dict[str, str] = {}
    for key, filename in SPLIT_FILENAMES.items():
        target = splits_dir / filename
        reject_links(target)
        payload = serialize_split_yaml(built[key]).encode("utf-8")
        target.write_bytes(payload)
        file_digests[filename] = hashlib.sha256(payload).hexdigest()
    return file_digests


def verify_frozen_splits(
    splits_dir: Path = SPLITS_DIR,
    freeze_list_path: Path = FREEZE_LIST_PATH,
    seed: int = DEFAULT_SPLIT_SEED,
) -> dict[str, str]:
    """Verify all 6 on-disk split .yaml files and FREEZE_LIST.md against recomputed splits."""
    reject_links(splits_dir)
    reject_links(freeze_list_path)
    if not freeze_list_path.is_file():
        raise FileNotFoundError(f"freeze_list_missing: {freeze_list_path}")
    freeze_list_text = freeze_list_path.read_text(encoding="utf-8")

    expected_splits = build_all_splits(seed=seed)
    digests: dict[str, str] = {}
    for key, filename in SPLIT_FILENAMES.items():
        path = splits_dir / filename
        loaded = load_split_yaml(path)
        expected = expected_splits[key]
        if loaded != expected:
            raise ValueError(f"frozen_split_mismatch: {filename}")
        raw_bytes = path.read_bytes()
        expected_bytes = serialize_split_yaml(expected).encode("utf-8")
        if raw_bytes != expected_bytes:
            raise ValueError(f"noncanonical_split_yaml_bytes: {filename}")
        file_sha = hashlib.sha256(raw_bytes).hexdigest()
        digests[filename] = file_sha
        if file_sha not in freeze_list_text:
            raise ValueError(f"split_file_sha256_missing_from_freeze_list: {filename}")
        if loaded.split_payload_sha256 not in freeze_list_text:
            raise ValueError(f"split_payload_sha256_missing_from_freeze_list: {filename}")
        for ds_file, ds_sha in loaded.dataset_files.items():
            if ds_sha not in freeze_list_text:
                raise ValueError(f"dataset_sha256_missing_from_freeze_list: {ds_file}")
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint to write or verify frozen Gate J5 split files."""
    parser = argparse.ArgumentParser(description="Build or verify Gate J5 frozen benchmark splits (RL-026).")
    parser.add_argument("--write", action="store_true", help="Write all 6 .yaml split files to splits/.")
    args = parser.parse_args(argv)
    if args.write:
        digests = write_all_splits()
        logger.info("frozen_splits_written", count=len(digests), files=list(digests.keys()))
        console.print_json(json.dumps(digests, indent=2))
    else:
        digests = verify_frozen_splits()
        logger.info("frozen_splits_verified", count=len(digests), files=list(digests.keys()))
        console.print_json(json.dumps(digests, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
