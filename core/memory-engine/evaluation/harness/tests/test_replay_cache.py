"""Unit and harness-integration tests for the RL-032 extraction record-and-replay cache."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evaluation.harness.budget import AccountedOperationError
from evaluation.harness.config import HarnessConfig, canonical_json, load_config
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.models import IngestReceipt, TurnRecord, Usage
from evaluation.harness.replay_cache import (
    ExtractedCandidate,
    ExtractionCacheKey,
    ExtractionProviderOutput,
    ExtractionReplayCache,
    ExtractionSchemaValidationError,
)
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_runner import fixture_case

ENGINE_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = ENGINE_ROOT / "evaluation/harness/fixtures"


def sample_candidate_dict(
    *,
    predicate_key: str = "residence_city",
    content: str = "Lives in Zurich",
    evidence_span: str = "I live in Zurich full time.",
) -> dict[str, Any]:
    return {
        "assertion_mode": "asserted",
        "candidate_fingerprint": "fp_zurich_01",
        "confidence": 0.95,
        "content": content,
        "evidence_role": "user",
        "evidence_span": evidence_span,
        "importance": 0.85,
        "layer": "semantic",
        "predicate_key": predicate_key,
        "single_valued": True,
        "subject_key": "user",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_from_basis": "explicit",
        "valid_to": None,
    }


def test_cache_hit_returns_byte_identical_candidates_with_zero_llm_calls() -> None:
    cache = ExtractionReplayCache()
    key = ExtractionCacheKey(
        turn_id="turn_001",
        extractor_version="ext_v1",
        model_id="qwen2.5-7b-instruct",
    )
    expected_candidates = [
        sample_candidate_dict(),
        sample_candidate_dict(
            predicate_key="dietary_regimen",
            content="Follows pescatarian diet",
            evidence_span="My dietary regimen is strictly pescatarian.",
        ),
    ]
    extraction_usage = Usage(
        input_tokens=180,
        output_tokens=45,
        embedding_tokens=0,
        cost_usd=Decimal("0.0012"),
    )
    llm_calls = 0

    def extractor() -> ExtractionProviderOutput:
        nonlocal llm_calls
        llm_calls += 1
        return ExtractionProviderOutput(
            candidates=tuple(expected_candidates),
            usage=extraction_usage,
        )

    miss_outcome = cache.get_or_extract(key, extractor)
    assert llm_calls == 1
    assert miss_outcome.cache_hit is False
    assert miss_outcome.usage == extraction_usage
    assert miss_outcome.recorded_usage == extraction_usage
    assert cache.hits == 0
    assert cache.misses == 1
    assert cache.entry_count == 1

    # Replay across ablations (e.g., A1..A10) with the same (turn_id, extractor_version, model_id)
    for _ablation in ("A1", "A2", "A3", "A5", "A6", "A8"):
        hit_outcome = cache.get_or_extract(
            ("turn_001", "ext_v1", "qwen2.5-7b-instruct"),
            lambda: pytest.fail("Extractor must never be invoked on a cache hit"),
        )
        assert hit_outcome.cache_hit is True
        assert hit_outcome.usage == Usage.zero()
        assert hit_outcome.recorded_usage == extraction_usage
        assert hit_outcome.entry_sha256 == miss_outcome.entry_sha256
        assert canonical_json(hit_outcome.as_dicts()) == canonical_json(expected_candidates)
        assert canonical_json(hit_outcome.as_dicts()) == canonical_json(miss_outcome.as_dicts())

    assert llm_calls == 1
    assert cache.hits == 6
    assert cache.misses == 1
    assert cache.errors_rejected == 0


def test_cache_keys_separate_turn_id_extractor_version_and_model_id() -> None:
    cache = ExtractionReplayCache()
    calls = 0

    def make_extractor(label: str):
        def _extract():
            nonlocal calls
            calls += 1
            return [sample_candidate_dict(content=label)]

        return _extract

    r1 = cache.get_or_extract(("t1", "v1", "m1"), make_extractor("c1"))
    r2 = cache.get_or_extract(("t2", "v1", "m1"), make_extractor("c2"))
    r3 = cache.get_or_extract(("t1", "v2", "m1"), make_extractor("c3"))
    r4 = cache.get_or_extract(("t1", "v1", "m2"), make_extractor("c4"))

    assert calls == 4
    assert cache.entry_count == 4
    assert r1.as_dicts()[0]["content"] == "c1"
    assert r2.as_dicts()[0]["content"] == "c2"
    assert r3.as_dicts()[0]["content"] == "c3"
    assert r4.as_dicts()[0]["content"] == "c4"


def test_caller_mutation_of_returned_dicts_cannot_corrupt_cached_bytes() -> None:
    cache = ExtractionReplayCache()
    key = ("turn_mut", "v1", "m1")
    original = [sample_candidate_dict()]
    original_bytes = canonical_json(original)

    outcome = cache.get_or_extract(key, lambda: original)
    returned_dicts = outcome.as_dicts()
    returned_dicts[0]["content"] = "CORRUPTED BY CALLER"
    returned_dicts.append({"injected": True})

    direct_hit = cache.get(key)
    assert direct_hit is not None
    assert canonical_json(direct_hit) == original_bytes
    direct_hit[0]["predicate_key"] = "MUTATED_AGAIN"

    second_hit = cache.get_or_extract(key, lambda: pytest.fail("Should hit cache"))
    assert canonical_json(second_hit.as_dicts()) == original_bytes


def test_extractor_exceptions_are_never_cached() -> None:
    cache = ExtractionReplayCache()
    key = ("turn_err", "v1", "m1")
    attempts = 0

    def flaky_extractor() -> tuple[list[dict[str, Any]], Usage]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient_provider_failure")
        if attempts == 2:
            raise AccountedOperationError(
                "schema_parse_failure",
                Usage(input_tokens=50, output_tokens=0, embedding_tokens=0, cost_usd=Decimal("0.0005")),
            )
        return [sample_candidate_dict()], Usage(
            input_tokens=50,
            output_tokens=20,
            embedding_tokens=0,
            cost_usd=Decimal("0.0005"),
        )

    with pytest.raises(RuntimeError, match="transient_provider_failure"):
        cache.get_or_extract(key, flaky_extractor)
    assert key not in cache
    assert cache.entry_count == 0
    assert cache.errors_rejected == 1

    with pytest.raises(AccountedOperationError, match="schema_parse_failure"):
        cache.get_or_extract(key, flaky_extractor)
    assert key not in cache
    assert cache.entry_count == 0
    assert cache.errors_rejected == 2

    success = cache.get_or_extract(key, flaky_extractor)
    assert success.cache_hit is False
    assert key in cache
    assert cache.entry_count == 1
    assert cache.errors_rejected == 2
    assert cache.misses == 3


@pytest.mark.parametrize(
    "bad_override",
    [
        {"subject_key": ""},
        {"subject_key": "   "},
        {"predicate_key": ""},
        {"content": ""},
        {"evidence_span": ""},
        {"layer": "invalid_layer"},
        {"assertion_mode": "speculative_invalid"},
        {"valid_from_basis": "guessed"},
        {"evidence_role": "system"},
        {"confidence": -0.01},
        {"confidence": 1.01},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": True},
        {"importance": -0.5},
        {"importance": 1.5},
        {"valid_from": "2026-01-01T00:00:00"},
        {
            "valid_from": "2026-05-01T00:00:00Z",
            "valid_to": "2026-05-01T00:00:00Z",
        },
        {
            "valid_from": "2026-05-02T00:00:00Z",
            "valid_to": "2026-05-01T00:00:00Z",
        },
        {"unexpected_extra_field": "forbidden"},
        {"candidate_fingerprint": "   "},
    ],
)
def test_schema_validation_gate_rejects_malformed_candidates_and_never_caches(
    bad_override: dict[str, Any],
) -> None:
    cache = ExtractionReplayCache()
    key = ("turn_bad", "v1", "m1")
    bad_candidate = {**sample_candidate_dict(), **bad_override}

    with pytest.raises(ExtractionSchemaValidationError):
        cache.get_or_extract(key, lambda: [bad_candidate])

    assert key not in cache
    assert cache.entry_count == 0
    assert cache.errors_rejected == 1


def test_conflicting_overwrite_for_same_key_is_rejected_and_preserves_original() -> None:
    cache = ExtractionReplayCache()
    key = ("turn_conflict", "v1", "m1")
    first = [sample_candidate_dict(content="First valid extraction")]
    second = [sample_candidate_dict(content="Conflicting extraction for same key")]

    entry_a = cache.put(key, first)
    entry_b = cache.put(key, first)
    assert entry_a == entry_b

    with pytest.raises(ValueError, match="replay_cache_conflicting_overwrite_for_key"):
        cache.put(key, second)

    assert cache.errors_rejected == 1
    assert cache.get(key) == first


def test_extracted_candidate_fingerprint_and_persistence_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cand_a = ExtractedCandidate.model_validate({**sample_candidate_dict(), "turn_id": "turn_001"})
    cand_b = ExtractedCandidate.model_validate({**sample_candidate_dict(), "turn_id": "turn_002", "confidence": 0.50})
    # Fingerprint depends on semantic/temporal/evidence identity, not transient turn_id or confidence
    assert cand_a.compute_fingerprint() == cand_b.compute_fingerprint()
    assert len(cand_a.compute_fingerprint()) == 64

    cache_path = tmp_path / "rollback_cache.json"
    cache = ExtractionReplayCache(cache_path=cache_path, auto_persist=True)

    def broken_save(_target: Path) -> None:
        raise OSError("simulated_disk_full")

    monkeypatch.setattr(cache, "_save_unlocked", broken_save)
    with pytest.raises(OSError, match="simulated_disk_full"):
        cache.put(("turn_rb", "v1", "m1"), [sample_candidate_dict()])

    assert ("turn_rb", "v1", "m1") not in cache
    assert cache.entry_count == 0
    assert cache.errors_rejected == 1


def test_disk_persistence_order_independence_and_tamper_detection(tmp_path: Path) -> None:
    cache_file_a = tmp_path / "cache_a.json"
    cache_file_b = tmp_path / "cache_b.json"

    k1 = ("turn_1", "v1", "m1")
    k2 = ("turn_2", "v1", "m1")
    c1 = [sample_candidate_dict(content="Fact 1")]
    c2 = [ExtractedCandidate.model_validate(sample_candidate_dict(content="Fact 2"))]

    cache_a = ExtractionReplayCache(cache_path=cache_file_a)
    cache_a.put(k1, c1)
    cache_a.put(k2, c2)

    cache_b = ExtractionReplayCache(cache_path=cache_file_b, auto_persist=False)
    cache_b.put(k2, c2)
    cache_b.put(k1, c1)
    cache_b.save()

    assert cache_a.integrity_sha256() == cache_b.integrity_sha256()
    assert cache_file_a.read_bytes() == cache_file_b.read_bytes()

    reloaded = ExtractionReplayCache.load(cache_file_a)
    assert reloaded.entry_count == 2
    assert reloaded.integrity_sha256() == cache_a.integrity_sha256()
    assert canonical_json(reloaded.get(k1)) == canonical_json(c1)

    envelope_data = json.loads(cache_file_a.read_text(encoding="utf-8"))
    envelope_data["entries"][0]["candidates"][0]["content"] = "Tampered content"
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(envelope_data), encoding="utf-8")
    with pytest.raises(ValueError, match="cache_entry_sha256_mismatch"):
        ExtractionReplayCache.load(tampered_path)


def test_run_evaluation_records_replay_cache_summary_and_replays_across_ablations(
    repo: Path,
) -> None:
    base_config = load_config(FIXTURES_DIR / "config.json")
    ingest_limit = Usage(
        input_tokens=50,
        output_tokens=20,
        embedding_tokens=0,
        cost_usd=Decimal("0.0010"),
    )
    config = base_config.model_copy(
        update={"operation_limits": base_config.operation_limits.model_copy(update={"ingest_turn": ingest_limit})}
    )
    split_path = repo / "dev_split.json"
    split_path.write_bytes(b'{"fixture":"public synthetic inputs only"}\n')
    shared_cache = ExtractionReplayCache(cache_path=repo / "shared_extraction_cache.json")
    llm_extraction_calls = 0

    class CachingAdapter(DummyAdapter):
        def __init__(self, cfg: HarnessConfig) -> None:
            super().__init__(cfg)
            self.replay_cache = shared_cache

        def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
            nonlocal llm_extraction_calls
            super().ingest_turn(turn)
            if turn.role == "user":
                key = ExtractionCacheKey(
                    turn_id=turn.turn_id,
                    extractor_version=self.config.adapter_version,
                    model_id=self.config.model_id,
                )

                def _extract() -> ExtractionProviderOutput:
                    nonlocal llm_extraction_calls
                    llm_extraction_calls += 1
                    return ExtractionProviderOutput(
                        candidates=(
                            sample_candidate_dict(
                                content=turn.content,
                                evidence_span=turn.content,
                            ),
                        ),
                        usage=Usage(
                            input_tokens=25,
                            output_tokens=10,
                            embedding_tokens=0,
                            cost_usd=Decimal("0.0005"),
                        ),
                    )

                outcome = self.replay_cache.get_or_extract(key, _extract)
                return IngestReceipt(turn_id=turn.turn_id, usage=outcome.usage)
            return IngestReceipt(turn_id=turn.turn_id, usage=Usage.zero())

    def _load_cases():
        return (
            fixture_case("scenario-1", "user-1"),
            fixture_case("scenario-2", "user-2"),
        )

    # First run (P): populates extraction cache (1 miss on first scenario, 1 hit on second)
    run_p = run_evaluation(
        config=config,
        suite_id="T",
        split="dev",
        split_path=split_path,
        case_loader=_load_cases,
        adapter_factory=CachingAdapter,
        scorer=dummy_score,
        run_id="run_p",
        output_root=repo / "runs",
        repo_root=repo,
    )
    manifest_p = json.loads((run_p / "manifest.json").read_text(encoding="utf-8"))
    cache_meta_p = manifest_p["extraction_replay_cache"]
    assert llm_extraction_calls == 1
    assert cache_meta_p["entry_count"] == 1
    assert cache_meta_p["hits"] == 1
    assert cache_meta_p["misses"] == 1
    assert cache_meta_p["errors_rejected"] == 0
    assert cache_meta_p["integrity_sha256"] == shared_cache.integrity_sha256()
    assert manifest_p["usage"]["input_tokens"] == 25

    # Second run (Ablation A2): replays identical candidates from cache with zero LLM calls
    ablation_config = config.model_copy(update={"config_id": "A2-nohistory"})
    run_a2 = run_evaluation(
        config=ablation_config,
        suite_id="T",
        split="dev",
        split_path=split_path,
        case_loader=_load_cases,
        adapter_factory=CachingAdapter,
        scorer=dummy_score,
        run_id="run_a2",
        output_root=repo / "runs",
        repo_root=repo,
        replay_cache=shared_cache,
    )
    manifest_a2 = json.loads((run_a2 / "manifest.json").read_text(encoding="utf-8"))
    cache_meta_a2 = manifest_a2["extraction_replay_cache"]
    assert llm_extraction_calls == 1
    assert cache_meta_a2["entry_count"] == 1
    assert cache_meta_a2["hits"] == 3
    assert cache_meta_a2["misses"] == 1
    assert cache_meta_a2["errors_rejected"] == 0
    assert cache_meta_a2["integrity_sha256"] == cache_meta_p["integrity_sha256"]
    assert manifest_a2["usage"]["input_tokens"] == 0
    assert manifest_a2["usage"]["cost_usd"] == "0"
