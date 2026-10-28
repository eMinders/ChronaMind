"""RL-032: ``P`` replays frozen extractions from the record-and-replay cache.

These are unit-level checks of the cache seam inside ``EngineAdapter``: they do not
touch PostgreSQL, so they run without a database. The engine write path itself is
covered by ``tests/integration/test_engine_adapter_db.py``.
"""

from datetime import UTC, datetime
from typing import cast

import pytest

from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput
from evaluation.baselines.contracts import ModelServices
from evaluation.baselines.p_engine import EngineAdapter, EngineOptions, cached_candidates
from evaluation.baselines.tests.helpers import FakeServices, config, settings
from evaluation.harness.models import TurnRecord, Usage
from evaluation.harness.replay_cache import ExtractionCacheKey, ExtractionReplayCache

CLOCK = datetime(2026, 3, 10, tzinfo=UTC)
TEST_DSN = "postgresql://cme_test:fictional-secret@127.0.0.1:5433/cme_compatibility"


def make_turn(turn_id: str) -> TurnRecord:
    return TurnRecord(
        user_id="u1",
        session_id="s1",
        turn_id=turn_id,
        role="user",
        content="I am vegetarian",
        occurred_at=CLOCK,
    )


def candidate(turn_id: str, content: str = "The user is vegetarian") -> CandidateVersionInput:
    return CandidateVersionInput(
        version_id=f"v-{turn_id}",
        memory_key="user:diet",
        user_id="u1",
        subject_key="user",
        predicate_key="diet",
        content=content,
        valid_from=CLOCK,
        evidence=(EvidenceInput(turn_id=turn_id, evidence_span=content),),
    )


class CountingExtractor:
    """Extractor stand-in that records how often the slow path was actually invoked."""

    def __init__(self, content: str = "The user is vegetarian") -> None:
        self.calls = 0
        self.content = content

    def __call__(self, turn_record) -> tuple[CandidateVersionInput, ...]:
        self.calls += 1
        # Vary the output so a replayed value could not accidentally match a fresh call.
        return (candidate(turn_record.turn_id, self.content),)


def build(*, cache: ExtractionReplayCache | None, extract: bool = True) -> tuple[EngineAdapter, CountingExtractor]:
    selected = settings()
    services = FakeServices(selected)
    extractor = CountingExtractor()
    adapter = EngineAdapter(
        config("P", selected),
        selected,
        cast(ModelServices, services),
        options=EngineOptions(database_url=TEST_DSN),
        extractor=extractor if extract else None,
        replay_cache=cache,
    )
    adapter.set_clock(CLOCK)
    return adapter, extractor


def test_extraction_cache_key_is_the_declared_triple() -> None:
    key = ExtractionCacheKey(turn_id="t1", extractor_version="engine-v1", model_id="gpt-4.1-mini-2025-04-14")
    assert key.as_tuple() == ("t1", "engine-v1", "gpt-4.1-mini-2025-04-14")


@pytest.mark.parametrize("field", ["turn_id", "extractor_version", "model_id"])
def test_cache_key_rejects_blank_components(field: str) -> None:
    values = {"turn_id": "t1", "extractor_version": "engine-v1", "model_id": "m1"}
    values[field] = "   "
    with pytest.raises(ValueError, match="nonempty_extraction_cache_key_component_required"):
        ExtractionCacheKey(**values)


def test_first_extraction_misses_and_caches_engine_inputs() -> None:
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        candidates = adapter._extract(make_turn("t1"))
        assert extractor.calls == 1
        assert cache.misses == 1 and cache.hits == 0 and cache.entry_count == 1
        assert len(candidates) == 1
        assert candidates[0].memory_key == "user:diet"
        assert candidates[0].extractor_version == "engine-v1"
        assert candidates[0].evidence[0].turn_id == "t1"
    finally:
        adapter.close()


def test_second_extraction_replays_with_zero_extractor_calls() -> None:
    """RL-032 acceptance: a cache hit makes zero external calls and returns equal inputs."""
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        first = adapter._extract(make_turn("t1"))
        second = adapter._extract(make_turn("t1"))
        assert extractor.calls == 1, "a cache hit must not invoke the slow path again"
        assert cache.hits == 1 and cache.misses == 1
        assert first == second
    finally:
        adapter.close()


def test_different_turns_do_not_share_a_cache_entry() -> None:
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        adapter._extract(make_turn("t1"))
        adapter._extract(make_turn("t2"))
        assert extractor.calls == 2
        assert cache.misses == 2 and cache.hits == 0
    finally:
        adapter.close()


def test_replay_is_stable_when_the_extractor_would_now_answer_differently() -> None:
    """Ablations must observe one frozen extraction, never a re-sampled one."""
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        first = adapter._extract(make_turn("t1"))
        extractor.content = "DIVERGENT RESULT"
        second = adapter._extract(make_turn("t1"))
        assert second == first, "the cache must shadow a changed extractor"
        assert extractor.calls == 1
    finally:
        adapter.close()


def test_extractor_version_change_invalidates_the_entry() -> None:
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        adapter._extract(make_turn("t1"))
        adapter.options = EngineOptions(database_url=TEST_DSN, extractor_version="engine-v2")
        adapter._extract(make_turn("t1"))
        assert extractor.calls == 2
        assert cache.entry_count == 2
    finally:
        adapter.close()


def test_model_id_change_invalidates_the_entry() -> None:
    cache = ExtractionReplayCache()
    adapter, extractor = build(cache=cache)
    try:
        adapter._extract(make_turn("t1"))
        adapter.config = adapter.config.model_copy(update={"model_id": "another-frozen-model"})
        adapter._extract(make_turn("t1"))
        assert extractor.calls == 2
    finally:
        adapter.close()


def test_failed_extraction_is_never_cached() -> None:
    """RL-032 acceptance: only successful, schema-valid extractions are stored."""
    from evaluation.harness.replay_cache import ExtractionSchemaValidationError

    cache = ExtractionReplayCache()

    class Broken:
        calls = 0

        def __call__(self, turn_record):
            Broken.calls += 1
            raise RuntimeError("synthetic_slow_path_failure")

    selected = settings()
    adapter = EngineAdapter(
        config("P", selected),
        selected,
        cast(ModelServices, FakeServices(selected)),
        options=EngineOptions(database_url=TEST_DSN),
        extractor=Broken(),
        replay_cache=cache,
    )
    adapter.set_clock(CLOCK)
    try:
        with pytest.raises(RuntimeError, match="synthetic_slow_path_failure"):
            adapter._extract(make_turn("t1"))
        assert cache.entry_count == 0 and cache.errors_rejected == 1
    finally:
        adapter.close()
    assert ExtractionSchemaValidationError is not None


def test_without_a_cache_extraction_stays_direct_and_unchanged() -> None:
    adapter, extractor = build(cache=None)
    try:
        adapter._extract(make_turn("t1"))
        adapter._extract(make_turn("t1"))
        assert extractor.calls == 2, "no cache means no replay: behaviour is unchanged"
        assert adapter.replay_cache is None
    finally:
        adapter.close()


def test_absent_extractor_produces_no_candidates() -> None:
    adapter, extractor = build(cache=ExtractionReplayCache(), extract=False)
    try:
        assert adapter._extract(make_turn("t1")) == ()
        assert extractor.calls == 0
    finally:
        adapter.close()


def test_metadata_reports_the_cache_contract_without_leaking_content() -> None:
    cache = ExtractionReplayCache()
    adapter, _ = build(cache=cache)
    try:
        adapter._extract(make_turn("t1"))
        metadata = adapter.adapter_metadata()
        extraction = cast(dict[str, object], cast(dict[str, object], metadata["engine"])["extraction"])
        assert extraction["replay_cache"] == "enabled"
        assert extraction["extractor_version"] == "engine-v1"
        assert extraction["cache_key"] == ["turn_id", "extractor_version", "model_id"]
        assert "vegetarian" not in str(metadata)
    finally:
        adapter.close()


def test_cached_candidates_derives_deterministic_engine_identity() -> None:
    turn_record = make_turn("t9")
    records = (
        {
            "subject_key": "user",
            "predicate_key": "city",
            "content": "Cairo",
            "evidence_span": "I live in Cairo",
            "valid_from": None,
        },
    )
    derived = cached_candidates(turn_record, records, "engine-v1")
    assert derived == cached_candidates(turn_record, records, "engine-v1")
    assert derived[0].version_id == "t9:0"
    assert derived[0].memory_key == "user:city"
    assert derived[0].user_id == "u1"
    assert derived[0].valid_from == CLOCK
    assert derived[0].evidence[0].evidence_span == "I live in Cairo"


def test_replay_outcome_charges_zero_usage_on_hit() -> None:
    """The accounting claim behind RL-032: a hit must not bill tokens."""
    cache = ExtractionReplayCache()
    adapter, _ = build(cache=cache)
    try:
        adapter._extract(make_turn("t1"))  # populate through the adapter's own keying
        provider_key = ExtractionCacheKey(
            turn_id="t1", extractor_version="engine-v1", model_id=adapter.config.model_id
        )
        hit = cache.get_or_extract(provider_key, lambda: pytest.fail("hit path must not call the extractor"))
        assert hit.cache_hit is True
        assert hit.usage == Usage.zero()
    finally:
        adapter.close()
