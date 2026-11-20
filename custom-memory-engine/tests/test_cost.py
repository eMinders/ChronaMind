"""Unit tests for per-stage retrieval cost records, attribution, Usage mapping and CostStore (E-036)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from custom_memory_engine.engine.cost import (
    STAGE_UNITS,
    CostError,
    CostReport,
    CostStore,
    QueryCost,
    StageCollector,
    StageCost,
    parse_window,
)
from custom_memory_engine.engine.planning import STAGES
from evaluation.harness.cost import USAGE_FIELD_MAPPING, usage_from_cost_report, usage_from_query_cost
from evaluation.harness.models import Usage

T0 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


def _sample_query_cost(
    *,
    latencies: dict[str, float] | None = None,
    tokens_considered: int = 120,
    tokens_rendered: int = 80,
) -> QueryCost:
    times = latencies or dict.fromkeys(STAGES, 1.0)
    stages = tuple(
        StageCost(
            stage=name,
            unit=STAGE_UNITS[name],
            latency_ms=times[name],
            candidates_in=10,
            candidates_out=5,
            tokens_considered=tokens_considered if name == "assemble" else 0,
            tokens_rendered=tokens_rendered if name == "assemble" else 0,
        )
        for name in STAGES
    )
    return QueryCost(operation="query", mode="current_state", hop_budget=1, stages=stages)


# ------------------------------------------------------------------ criteria 1 & 2: record shape & plan stages


def test_stage_units_match_e031_plan_stages_exactly() -> None:
    assert tuple(STAGE_UNITS) == STAGES
    assert STAGE_UNITS["plan"] == "scope_selectors"
    assert STAGE_UNITS["candidate_generation"] == "rows"
    assert STAGE_UNITS["expansion"] == "rows"
    assert STAGE_UNITS["filter_rerank"] == "rows"
    assert STAGE_UNITS["assemble"] == "items"


def test_stage_cost_and_query_cost_are_immutable() -> None:
    record = _sample_query_cost()
    with pytest.raises(FrozenInstanceError):
        record.hop_budget = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.stages[0].latency_ms = 99.0  # type: ignore[misc]


def test_stage_cost_validation_rejects_bad_stage_unit_and_numbers() -> None:
    with pytest.raises(CostError, match="unknown_stage"):
        StageCost("unknown", "rows", 1.0, 1, 1)
    with pytest.raises(CostError, match="stage_unit_mismatch"):
        StageCost("assemble", "rows", 1.0, 1, 1)
    for bad_latency in (-0.1, float("nan"), float("inf"), True):
        with pytest.raises(CostError, match="latency_must_be_nonnegative_finite"):
            StageCost("plan", "scope_selectors", bad_latency, 1, 1)  # type: ignore[arg-type]
    with pytest.raises(CostError, match="candidate_count_must_be_nonnegative_int"):
        StageCost("expansion", "rows", 1.0, -1, 0)
    with pytest.raises(CostError, match="token_count_must_be_nonnegative_int"):
        StageCost("assemble", "items", 1.0, 1, 1, tokens_considered=True)  # type: ignore[arg-type]


def test_query_cost_requires_exact_plan_stage_order() -> None:
    reversed_stages = tuple(reversed(_sample_query_cost().stages))
    with pytest.raises(CostError, match="stages_must_match_plan_stages"):
        QueryCost(operation="query", mode="current_state", hop_budget=1, stages=reversed_stages)
    with pytest.raises(CostError, match="stages_must_match_plan_stages"):
        QueryCost(operation="query", mode="current_state", hop_budget=1, stages=_sample_query_cost().stages[:-1])


# ------------------------------------------------------------------ criterion 3: deterministic attribution


@pytest.mark.parametrize("target_stage", STAGES)
def test_stage_collector_attributes_injected_delay_exclusively_to_target_stage(target_stage: str) -> None:
    clock = _FakeClock()
    collector = StageCollector(clock=clock)
    for name in STAGES:
        with collector.track(name) as span:
            span.candidates_in = 4
            span.candidates_out = 2
            if name == target_stage:
                clock.advance_ms(50.0)
            if name == "assemble":
                span.tokens_considered = 64
                span.tokens_rendered = 32
    cost = collector.finish(operation="query", mode="current_state", hop_budget=1)
    assert cost.stage(target_stage).latency_ms == pytest.approx(50.0)
    for other in STAGES:
        if other != target_stage:
            assert cost.stage(other).latency_ms == pytest.approx(0.0)
    assert cost.total_latency_ms == pytest.approx(50.0)
    assert cost.tokens_considered == 64
    assert cost.tokens_rendered == 32


def test_stage_collector_closes_boundary_on_exception_and_rejects_incomplete_finish() -> None:
    clock = _FakeClock()
    collector = StageCollector(clock=clock)
    with pytest.raises(RuntimeError, match="boom"), collector.track("plan"):
        clock.advance_ms(12.0)
        raise RuntimeError("boom")
    assert collector.has_stage("plan")
    with pytest.raises(CostError, match="stage_missing"):
        collector.finish(operation="query", mode="current_state", hop_budget=0)
    with pytest.raises(CostError, match="stage_already_recorded"), collector.track("plan"):
        pass


# ------------------------------------------------------------------ criteria 4 & 5: Usage mapping, no fabrication


def test_zero_charged_calls_forbids_fabricated_cost_usd_and_maps_to_usage_zero() -> None:
    assert set(USAGE_FIELD_MAPPING) == {"input_tokens", "output_tokens", "embedding_tokens", "cost_usd"}
    cost = _sample_query_cost(latencies=dict.fromkeys(STAGES, 4.5), tokens_considered=250, tokens_rendered=180)
    assert cost.total_latency_ms > 0.0
    assert cost.tokens_considered == 250
    assert cost.charged_calls == 0
    assert cost.cost_usd == Decimal(0)
    mapped = usage_from_query_cost(cost)
    assert mapped == Usage.zero()

    with pytest.raises(CostError, match="fabricated_charged_cost"):
        QueryCost(
            operation="query",
            mode="current_state",
            hop_budget=1,
            stages=cost.stages,
            charged_calls=0,
            cost_usd=Decimal("0.0001"),
        )
    with pytest.raises(CostError, match="fabricated_charged_cost"):
        QueryCost(
            operation="query",
            mode="current_state",
            hop_budget=1,
            stages=cost.stages,
            charged_calls=0,
            input_tokens=10,
        )


def test_cost_report_maps_to_usage_zero_when_uncharged_and_preserves_charged_when_present() -> None:
    store = CostStore(clock=lambda: T0)
    store.record("alice", _sample_query_cost())
    report = store.report("alice")
    assert isinstance(report, CostReport)
    assert report.charged_calls == 0
    assert usage_from_cost_report(report) == Usage.zero()

    charged = QueryCost(
        operation="query",
        mode="current_state",
        hop_budget=1,
        stages=_sample_query_cost().stages,
        charged_calls=1,
        input_tokens=100,
        output_tokens=20,
        embedding_tokens=50,
        cost_usd=Decimal("0.0025"),
    )
    assert usage_from_query_cost(charged) == Usage(
        input_tokens=100, output_tokens=20, embedding_tokens=50, cost_usd=Decimal("0.0025")
    )


# ------------------------------------------------------------------ window parsing & CostStore


def test_parse_window_accepts_valid_forms_and_rejects_invalid() -> None:
    assert parse_window(None, now=T0, default_retention=timedelta(minutes=30)) == (T0 - timedelta(minutes=30), T0)
    assert parse_window(60, now=T0) == (T0 - timedelta(seconds=60), T0)
    assert parse_window(timedelta(minutes=5), now=T0) == (T0 - timedelta(minutes=5), T0)
    assert parse_window(
        {"start": (T0 - timedelta(minutes=10)).isoformat(), "end": T0.isoformat()},
        now=T0,
    ) == (T0 - timedelta(minutes=10), T0)

    for bad in (
        0,
        -5,
        True,
        float("nan"),
        float("inf"),
        timedelta(0),
        timedelta(seconds=-1),
        "last_hour",
        {"start": T0.isoformat()},
        {"start": T0.isoformat(), "end": T0.isoformat()},
        {"start": T0.isoformat(), "end": (T0 - timedelta(seconds=1)).isoformat()},
        {"start": "2026-05-10T11:00:00", "end": T0.isoformat()},  # naive timestamp
    ):
        with pytest.raises(CostError, match="window_invalid"):
            parse_window(bad, now=T0)


def test_cost_store_isolates_principals_filters_windows_and_evicts_by_capacity_and_retention() -> None:
    current = [T0]
    store = CostStore(retention=timedelta(minutes=10), capacity=3, clock=lambda: current[0])

    store.record(
        "alice",
        _sample_query_cost(
            latencies={
                "plan": 1.0,
                "candidate_generation": 10.0,
                "expansion": 2.0,
                "filter_rerank": 3.0,
                "assemble": 4.0,
            }
        ),
    )
    current[0] = T0 + timedelta(minutes=2)
    store.record("bob", _sample_query_cost(latencies=dict.fromkeys(STAGES, 99.0)))
    current[0] = T0 + timedelta(minutes=4)
    store.record(
        "alice",
        _sample_query_cost(
            latencies={
                "plan": 3.0,
                "candidate_generation": 30.0,
                "expansion": 6.0,
                "filter_rerank": 9.0,
                "assemble": 12.0,
            }
        ),
    )

    alice_report = store.report("alice", window=None)
    assert alice_report.query_count == 2
    assert alice_report.stage("candidate_generation").total_latency_ms == pytest.approx(40.0)
    assert alice_report.stage("candidate_generation").mean_latency_ms == pytest.approx(20.0)
    assert alice_report.stage("assemble").tokens_considered == 240
    assert alice_report.stage("assemble").tokens_rendered == 160

    # Window containing only the second alice query
    recent = store.report("alice", window=timedelta(minutes=1))
    assert recent.query_count == 1
    assert recent.stage("candidate_generation").total_latency_ms == pytest.approx(30.0)

    # Bob sees only bob's own query
    bob_report = store.report("bob", window=None)
    assert bob_report.query_count == 1
    assert bob_report.stage("plan").total_latency_ms == pytest.approx(99.0)

    # Capacity eviction (4th entry evicts the 1st)
    current[0] = T0 + timedelta(minutes=5)
    store.record("alice", _sample_query_cost())
    assert len(store) == 3

    # Retention expiry drops old records
    current[0] = T0 + timedelta(minutes=20)
    expired = store.report("alice", window=None)
    assert expired.query_count == 0
    assert tuple(entry.stage for entry in expired.stages) == STAGES
