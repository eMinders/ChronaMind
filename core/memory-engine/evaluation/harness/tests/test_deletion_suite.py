"""Unit contracts for the Suite D runner (#46): split guard, scripted extraction, cache epochs."""

import pytest

from evaluation.harness import deletion_suite
from evaluation.harness.deletion_suite import (
    ContextCache,
    SuiteRunError,
    extractive_answerer,
    load_split,
    scripted_candidates,
)


def test_test_split_is_refused() -> None:
    with pytest.raises(SuiteRunError) as error:
        load_split("test")
    assert error.value.code == "test_split_reserved_for_confirmatory_phase"


def test_dev_split_loads_twenty_scenarios() -> None:
    dataset = load_split("dev")
    assert dataset.split == "dev"
    assert len(dataset.scenarios) == 20


def test_summary_turn_splits_into_canary_and_surviving_clauses() -> None:
    dataset = load_split("dev")
    event = next(e for e in dataset.scenarios[0].events if e.lineage_structure == "multi_source_summary_memory")
    turn = next(t for t in event.turns if t.role == "user")
    candidates = scripted_candidates(event, "u", "t0", turn.content, turn.occurred_at, 0)
    keys = {c.memory_key: c.content for c in candidates}
    assert event.canary_value in keys[event.target_memory_key]
    survivors = [k for k in keys if k != event.target_memory_key]
    assert survivors and all(event.canary_value not in keys[k] for k in survivors)
    for candidate in candidates:
        assert candidate.evidence[0].evidence_span in turn.content  # gate-groundable


def test_direct_turn_yields_one_target_candidate() -> None:
    dataset = load_split("dev")
    event = next(e for e in dataset.scenarios[0].events if e.lineage_structure == "direct_extracted_memory")
    turn = next(t for t in event.turns if t.role == "user")
    candidates = scripted_candidates(event, "u", "t0", turn.content, turn.occurred_at, 0)
    assert [c.memory_key for c in candidates] == [event.target_memory_key]


@pytest.mark.asyncio
async def test_cache_invalidation_drops_older_epochs_and_keeps_newer() -> None:
    cache = ContextCache()
    cache.put("u", "q-old", 1, "old CANARY")
    cache.put("u", "q-new", 3, "new")
    cache.put("other", "q", 1, "other user")
    await cache.invalidate_before_epoch("u", 2)
    assert cache.texts("u") == ["new"]
    assert cache.texts("other") == ["other user"]


def test_extractive_answerer_is_a_conservative_upper_bound() -> None:
    assert extractive_answerer("context with CANARY", "q") == "context with CANARY"


def test_split_that_does_not_match_the_frozen_manifest_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    forged = tmp_path / "MANIFEST.txt"
    forged.write_text("0" * 64 + "  own_D_dev.json\n", encoding="utf-8")
    monkeypatch.setattr(deletion_suite, "MANIFEST_PATH", forged)
    with pytest.raises(SuiteRunError) as error:
        load_split("dev")
    assert error.value.code == "suite_d_dev_does_not_match_frozen_manifest"
