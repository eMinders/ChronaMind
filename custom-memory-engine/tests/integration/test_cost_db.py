"""Integration tests for E-036: per-stage retrieval cost attribution and ``cost`` wired through ``Engine.call``.

Timing tolerance (W5, criterion 10):
The real wall-clock attribution tests inject a 50 ms delay into a single stage X and assert:

* stage X records ``latency_ms >= 40.0`` (20% downward headroom for OS sleep granularity);
* every other stage Y != X records ``latency_ms < 25.0`` (well above the < 5 ms uninjected cost on this small
  fixture, with >= 15 ms separation so CI runner jitter does not flake).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine.cost import STAGE_UNITS, CostReport, CostStore, QueryCost
from custom_memory_engine.engine.planning import (
    MODES,
    STAGES,
    Intent,
    Plan,
    ReadScope,
    RuleBasedPlanner,
    StageObserver,
)
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope.access import Selector, resolve_read_set
from evaluation.harness.cost import usage_from_cost_report, usage_from_query_cost
from evaluation.harness.models import Usage
from tests.integration.test_api_wiring_structure import (
    caller,
    database,  # noqa: F401  (fixture)
    world,
)
from tests.integration.test_read_path_db import CODING_PROFILE

pytestmark = pytest.mark.integration

T0 = datetime(2026, 5, 10, 9, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)
SCOPE = "/u/alice/p/cost/"
TURN_TEXT = "The user uses PostgreSQL with pgvector"

# Non-flaky timing tolerances for the 50 ms injected-delay attribution proof (E-036 §5.3, criterion 10).
INJECTED_DELAY_MS = 50.0
MIN_ATTRIBUTED_MS = 40.0
MAX_UNINJECTED_MS = 25.0


def _delay_observer(target_stage: str, delay_ms: float) -> StageObserver:
    """Inject a real wall-clock delay exclusively inside ``target_stage``'s boundary."""

    @contextmanager
    def _observe(stage: str):
        if stage == target_stage:
            time.sleep(delay_ms / 1000.0)
        yield

    return _observe


class _SlowPlanner:
    """Planner wrapper that sleeps inside ``plan`` to prove attribution without touching the observer seam."""

    def __init__(self, delay_ms: float) -> None:
        self._inner = RuleBasedPlanner()
        self._delay_s = delay_ms / 1000.0

    def plan(self, intent: Intent, scopes: Sequence[ReadScope]) -> Plan:
        time.sleep(self._delay_s)
        return self._inner.plan(intent, scopes)


@asynccontextmanager
async def _seeded_world(
    database,  # noqa: F811
    *,
    observer: StageObserver | None = None,
    planner: RuleBasedPlanner | _SlowPlanner | None = None,
    costs: CostStore | None = None,
) -> AsyncIterator[tuple[Engine, AsyncConnection, datetime]]:
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_read_handlers(engine, runtime, observer=observer, planner=planner, costs=costs)
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, 2, 'user', %s, %s)",
            ("turn-e036-alice", "s-alice", "alice", TURN_TEXT, T0),
        )
        alice = caller("alice")
        for idx, text in enumerate(("ran ruff check", "ran pyright cleanly"), start=1):
            res = await engine.call(
                alice,
                "ingest_episode",
                {
                    "scope": SCOPE,
                    "source": {
                        "type": "tool",
                        "ref": f"tool:e036-{idx}",
                        "span": text,
                        "content_hash": canonical_hash(text),
                    },
                    "content": text,
                    "event_time": T0.isoformat(),
                    "idempotency_key": f"e036-ep-{idx}",
                },
            )
            assert isinstance(res, Receipt)
        claim = await engine.call(
            alice,
            "assert",
            {
                "scope": SCOPE,
                "kind": "claim",
                "content": TURN_TEXT,
                "evidence": [
                    {
                        "type": "turn",
                        "ref": "turn-e036-alice",
                        "span": TURN_TEXT,
                        "content_hash": canonical_hash(TURN_TEXT),
                    }
                ],
                "event_range": [T0.isoformat(), T1.isoformat()],
                "idempotency_key": "e036-claim",
            },
        )
        assert isinstance(claim, Receipt)
        yield engine, conn, claim.recorded_at


# ------------------------------------------------------------------ criterion 3: real attribution proof (W5)


@pytest.mark.parametrize("target_stage", STAGES)
async def test_injected_delay_in_one_stage_appears_only_in_that_stage_bucket(database, target_stage: str) -> None:  # noqa: F811
    """W5 / criterion 3: an injected 50 ms delay in stage X appears exclusively in stage X's bucket."""
    async with _seeded_world(database, observer=_delay_observer(target_stage, INJECTED_DELAY_MS)) as (
        engine,
        _conn,
        _recorded,
    ):
        result = await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE}})
        assert isinstance(result, QueryResult)
        assert isinstance(result.stage_costs, QueryCost)
        assert tuple(entry.stage for entry in result.stage_costs.stages) == STAGES

        measured = {entry.stage: entry.latency_ms for entry in result.stage_costs.stages}
        assert measured[target_stage] >= MIN_ATTRIBUTED_MS, (
            f"expected >= {MIN_ATTRIBUTED_MS} ms in {target_stage}, got {measured}"
        )
        for other, value in measured.items():
            if other != target_stage:
                assert value < MAX_UNINJECTED_MS, f"delay injected into {target_stage} leaked into {other}: {measured}"

        report = await engine.call(caller("alice"), "cost", {"window": None})
        assert isinstance(report, CostReport)
        assert report.query_count == 1
        assert report.stage(target_stage).p95_latency_ms >= MIN_ATTRIBUTED_MS
        for other in STAGES:
            if other != target_stage:
                assert report.stage(other).p95_latency_ms < MAX_UNINJECTED_MS


async def test_slow_planner_work_is_attributed_exclusively_to_plan_stage(database) -> None:  # noqa: F811
    """Real stage work (no observer hook): a slow planner shows up exclusively in ``plan``."""
    async with _seeded_world(database, planner=_SlowPlanner(INJECTED_DELAY_MS)) as (engine, _conn, _recorded):
        result = await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE}})
        assert isinstance(result, QueryResult) and result.stage_costs is not None
        measured = {entry.stage: entry.latency_ms for entry in result.stage_costs.stages}
        assert measured["plan"] >= MIN_ATTRIBUTED_MS, measured
        for other in STAGES[1:]:
            assert measured[other] < MAX_UNINJECTED_MS, measured


# ------------------------------------------------------------------ criteria 1, 2, 4, 5: all modes + Usage


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_every_query_mode_records_all_five_stages_and_zero_charged_usage(database, mode: str) -> None:  # noqa: F811
    async with _seeded_world(database) as (engine, _conn, recorded_at):
        request: dict[str, object] = {"scope": SCOPE, "mode": mode}
        if mode == "historical_as_of":
            request["target_time"] = T0.isoformat()
        elif mode in ("historical_window", "change"):
            request["window_start"] = T0.isoformat()
            request["window_end"] = T1.isoformat()
        elif mode == "audit_as_of":
            request["target_time"] = T0.isoformat()
            request["belief_time"] = recorded_at.isoformat()

        result = await engine.call(caller("alice"), "query", {"request": request})
        assert isinstance(result, QueryResult) and result.stage_costs is not None
        sc = result.stage_costs
        assert sc.operation == "query"
        assert sc.mode == mode
        assert sc.hop_budget == (0 if mode == "change" else 1)
        assert tuple(entry.stage for entry in sc.stages) == STAGES
        assert all(entry.unit == STAGE_UNITS[entry.stage] for entry in sc.stages)
        assert sc.stage("plan").candidates_in >= 1
        assert sc.stage("candidate_generation").candidates_out >= 1
        if mode != "audit_as_of":
            assert sc.stage("assemble").candidates_in == result.cost.candidates_considered
            assert sc.stage("assemble").candidates_out == result.cost.items_rendered
            assert sc.stage("assemble").tokens_considered == result.cost.tokens_considered
            assert sc.stage("assemble").tokens_rendered == result.cost.tokens_rendered
        assert sc.charged_calls == 0
        assert sc.cost_usd == Decimal(0)
        assert usage_from_query_cost(sc) == Usage.zero()


# ------------------------------------------------------------------ criterion 6: cost wired, all tiers, isolation


async def test_cost_operation_is_wired_for_all_tiers_and_isolates_callers(database) -> None:  # noqa: F811
    """API_V2.md §3 row 29 & §4.11: ``cost`` works for Tier B and Tier C and returns only caller's own activity."""
    clock_now = [T0]
    store = CostStore(clock=lambda: clock_now[0])
    async with _seeded_world(database, costs=store) as (engine, _conn, _recorded):
        assert "cost" not in engine.unwired_operations()

        alice_c = caller("alice", "C")
        alice_b = caller("alice", "B")
        bob_c = caller("bob", "C")

        first = await engine.call(alice_c, "query", {"request": {"scope": SCOPE}})
        assert isinstance(first, QueryResult) and first.result_id is not None

        clock_now[0] = T0 + timedelta(minutes=2)
        refined = await engine.call(alice_b, "refine", {"result_id": first.result_id, "instruction": "channel facts"})
        assert isinstance(refined, QueryResult) and refined.stage_costs is not None
        assert refined.stage_costs.operation == "refine"
        assert refined.stage_costs.hop_budget == 0

        # Both Tier B and Tier C callers can observe alice's own activity.
        for ctx in (alice_b, alice_c):
            report = await engine.call(ctx, "cost", {"window": None})
            assert isinstance(report, CostReport)
            assert report.principal == "alice"
            assert report.query_count == 2
            assert tuple(entry.stage for entry in report.stages) == STAGES
            assert report.charged_calls == 0
            assert report.cost_usd == Decimal(0)
            assert usage_from_cost_report(report) == Usage.zero()

        # Window filtering returns only the refine inside the last 60 seconds.
        narrow = await engine.call(alice_c, "cost", {"window": 60})
        assert isinstance(narrow, CostReport) and narrow.query_count == 1

        # Explicit start/end window works too.
        explicit = await engine.call(
            alice_c,
            "cost",
            {
                "window": {
                    "start": (T0 - timedelta(seconds=10)).isoformat(),
                    "end": (T0 + timedelta(seconds=10)).isoformat(),
                }
            },
        )
        assert isinstance(explicit, CostReport) and explicit.query_count == 1

        # Principal isolation: bob never sees alice's counts.
        bob_report = await engine.call(bob_c, "cost", {"window": None})
        assert isinstance(bob_report, CostReport)
        assert bob_report.principal == "bob"
        assert bob_report.query_count == 0
        assert bob_report.total_latency_ms == 0.0

        # Invalid window fails cleanly with static validation_error.
        with pytest.raises(EntryError) as bad:
            await engine.call(alice_c, "cost", {"window": -10})
        assert (bad.value.code, bad.value.detail) == ("validation_error", "window_invalid")

        # Unknown argument is refused by Engine.call.
        with pytest.raises(EntryError) as unknown:
            await engine.call(alice_c, "cost", {"window": None, "principal": "bob"})
        assert unknown.value.code == "validation_error"


async def test_cost_report_handles_empty_caller_and_cross_scope_caller_read_set(database) -> None:  # noqa: F811
    async with _seeded_world(database) as (engine, _conn, _recorded):
        alice = CallerContext.from_credential(
            Credential("alice", "B", False),
            write_scope="/u/alice/",
            read_set=resolve_read_set("alice", [Selector.of(SCOPE)], []),
        )
        empty = await engine.call(alice, "cost", {"window": 300})
        assert isinstance(empty, CostReport)
        assert empty.query_count == 0
        assert all(stage.calls == 0 and stage.p95_latency_ms == 0.0 for stage in empty.stages)
