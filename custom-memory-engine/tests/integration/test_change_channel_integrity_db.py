"""E-035 Part B: channel integrity on the real V2-D81 public change path.

Seed harmless rows as the database owner, and query through a restricted runtime against the migrated schema.
Only reads are under test. The event windows are independent of the belief clock and of the abstraction expiry.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import CallerContext, Engine, EntryError
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.channels import Bound, ChannelCoverage, Refusal
from custom_memory_engine.engine.staleness import ClosureCache
from custom_memory_engine.scope.provenance import VisibleReference
from tests.integration.test_lifecycle_transitions import insert_version
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401 (fixture)
    rows,
    seed_turn,
)
from tests.integration.test_migration_m6 import abstraction, edge
from tests.integration.test_staleness_db import drop_role, read_handlers_for, seal

pytestmark = pytest.mark.integration

CHANNELS = ("facts", "hypotheses", "procedures", "conflicts")
PREVIOUS = START - timedelta(days=1)
CURRENT = START
TURN = "A note about a blue notebook."
CLAIMS = {
    "src": "steady observation",
    "inference": "possible summary",
    "left": "blue notebook",
    "right": "red notebook",
}
RETURNED = {
    "facts": ("fresh", "src"),
    "hypotheses": ("stale", "inference"),
    "procedures": ("procedure",),
    "conflicts": ("left", "right"),
}
EXPECTED_CHANGES = [
    {"outcome": "added", "previous": (), "current": ("fresh",)},
    {"outcome": "added", "previous": (), "current": ("incomplete",)},
    {"outcome": "removed", "previous": ("inference",), "current": ()},
    {"outcome": "added", "previous": (), "current": ("left",)},
    {"outcome": "added", "previous": (), "current": ("procedure",)},
    {"outcome": "added", "previous": (), "current": ("right",)},
    {"outcome": "added", "previous": (), "current": ("stale",)},
]
CANDIDATES = {"facts": 3, "hypotheses": 2, "procedures": 1, "conflicts": 2}
REFUSAL = Refusal("incomplete", "bound_missing")
STALE_LABEL = "verified, needs re-verification"
SUPPORT = "40 supporting observations of 40 opportunities"


async def _claim(
    conn: AsyncConnection,
    version_id: str,
    start: datetime,
    until: datetime | None = None,
    *,
    inferred: bool = False,
) -> None:
    """Give active samples explicit event bounds; recorded ranges open together at the pinned fixture clock."""
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, kind, lifecycle, retention, method_id) "
            "VALUES (%s, %s, 'u1', 'active', 'user', 'diet', false, %s, tstzrange(%s, %s), "
            "tstzrange(clock_now(), NULL), 'semantic', 'claim', %s, 'durable', %s)",
            (
                version_id,
                version_id,
                CLAIMS[version_id],
                start,
                until,
                "inferred" if inferred else "asserted",
                "m-v1-consolidation" if inferred else None,
            ),
        )
        await conn.execute("INSERT INTO memory_evidence VALUES (%s, 't0', 'u1', %s, 'support')", (version_id, TURN))
        if inferred:
            await conn.execute(
                "INSERT INTO memory_derivation VALUES (%s, 'src', 'u1', 'consolidation')", (version_id,)
            )


async def _seed_prior_claims(conn: AsyncConnection) -> None:
    """One grounded claim spans both endpoints; the inferred claim ends at the later endpoint."""
    await seed_turn(conn, "t0", TURN, PREVIOUS)
    await _claim(conn, "src", PREVIOUS)
    await _claim(conn, "inference", PREVIOUS, CURRENT, inferred=True)


@asynccontextmanager
async def _restricted_reader(
    database_config: tuple[DatabaseConfig, str], conn: AsyncConnection
) -> AsyncIterator[tuple[Engine, CallerContext]]:
    handlers, ctx, role = await read_handlers_for(database_config, conn)
    try:
        engine = build_engine(handlers.runtime)
        register_read_handlers(engine, handlers.runtime)
        yield engine, ctx
    finally:
        await drop_role(conn, role)


@pytest_asyncio.fixture
async def change_world(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> AsyncIterator[tuple[Engine, CallerContext, Bound]]:
    _, schema = database
    async with connect(database) as conn:
        await _seed_prior_claims(conn)
        await _claim(conn, "left", CURRENT)
        await _claim(conn, "right", CURRENT)
        await edge(conn, "left-right", "left", "right", kind="contradicts")
        await insert_version(conn, "procedure", "procedure", "asserted")

        ((now,),) = await rows(conn, "SELECT transaction_timestamp()")
        assert isinstance(now, datetime)
        # Both ledgers were still valid when verified. At rendering, only the shorter expiry has passed.
        expired, review_at, unexpired = (now + timedelta(days=days) for days in (1, 2, 3))
        cache = ClosureCache()
        for version_id, expiry, confidence in (
            ("fresh", unexpired, 0.91),
            ("stale", expired, 0.91),
            ("incomplete", unexpired, None),
        ):
            await abstraction(conn, version_id)
            await seal(conn, cache, schema, version_id)
            await conn.execute(
                "UPDATE abstraction_stats SET confidence = %s, expires_at = %s WHERE version_id = %s",
                (confidence, expiry, version_id),
            )
        ((verified_at,),) = await rows(conn, "SELECT verified_on FROM verification WHERE version_id = 'fresh'")
        assert isinstance(verified_at, datetime)
        expected_bound = Bound(40, 40, 0.91, 1.0, "m-test", "1", verified_at.isoformat(), unexpired)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (review_at.isoformat(),))

        async with _restricted_reader(database, conn) as (engine, ctx):
            yield engine, ctx, expected_bound


def _request(**extra: object) -> dict[str, object]:
    return {"mode": "change", "window_start": PREVIOUS, "window_end": CURRENT, **extra}


async def _query(engine: Engine, ctx: CallerContext, request: Mapping[str, object]) -> QueryResult:
    result = await engine.call(ctx, "query", {"request": request})
    assert isinstance(result, QueryResult), result
    return result


def _bounded_text(bound: Bound) -> str:
    # Hand-written expected sentence for this fixture (CHANNELS R2/R3), without calling the production renderer.
    return (
        "in 40 of 40 observed cases (confidence >= 0.91, coverage 1.00; m-test@1; "
        f"verified on {bound.verified_on}; expires {bound.expires_at.isoformat()}): x"
    )


def _fixture_token_totals(bound: Bound) -> dict[str, int]:
    # R7: the full bounded sentence, hypothesis labels, and both conflict sides contribute to coverage.
    return {
        "facts": len(_bounded_text(bound).split()) + 2,  # steady observation
        "hypotheses": 13,  # 10 for stale (label + x + support), 3 for unverified inference
        "procedures": 1,  # x
        "conflicts": 12,  # two 3-token sides per conflict item
    }


def _assert_changes(result: QueryResult) -> None:
    """V2-D81: version IDs only, including refused and clipped rows; a shared ID has no change record."""
    assert [asdict(change) for change in result.changes] == EXPECTED_CHANGES, "changes must be the exact ID-only diff"


def _assert_roomy_channels(result: QueryResult, bound: Bound) -> None:
    """C-P2–C-P4 and R2/R3: routing, staleness, bounds and both conflict sides survive the public projection."""
    facts = {item.artifact_id: item for item in result.facts}
    hypotheses = {item.artifact_id: item for item in result.hypotheses}
    assert "inference" not in facts, "inferred claim in facts"
    assert "stale" not in facts, "stale abstraction in facts"
    assert {"left", "right"}.isdisjoint(facts), "contradiction in facts"
    for name in CHANNELS:
        items = result.channel(name)
        assert tuple(item.artifact_id for item in items) == RETURNED[name], (
            f"{name}: duplicate, missing or misrouted ID"
        )
        assert all(item.channel == name for item in items)
    assert facts["fresh"].bound == bound, "fresh fact lost its precise bound"
    assert facts["fresh"].content == _bounded_text(bound), "fresh fact text lost its complete bound"
    assert facts["fresh"].kind == "abstraction" and facts["fresh"].verification == "verified"
    assert hypotheses["stale"].verification == "verified"
    assert hypotheses["stale"].label == STALE_LABEL, "stale hypothesis lost its uncertainty label"
    assert hypotheses["stale"].support_summary == SUPPORT
    assert hypotheses["inference"].label == "unverified"
    assert hypotheses["inference"].content == CLAIMS["inference"]
    for item, other in zip(result.conflicts, ("right", "left"), strict=True):
        assert tuple((side.artifact_id, side.lifecycle, side.text) for side in item.conflict_sides) == (
            (item.artifact_id, "asserted", CLAIMS[item.artifact_id]),
            (other, "asserted", CLAIMS[other]),
        )
    assert not result.is_audit and result.audit == ()
    assert result.refused == (("incomplete", "bound_missing"),), "a fresh abstraction without a full bound must refuse"


def _assert_coverage(
    result: QueryResult,
    budgets: Mapping[str, int],
    returned: Mapping[str, int],
    used: Mapping[str, int],
    total: Mapping[str, int],
) -> None:
    """CHANNELS R7/API §2: exact pre-budget coverage and whole-item omission distinct from refusal."""
    for name in CHANNELS:
        refusal = (REFUSAL,) if name == "facts" else ()
        assert result.coverage.channel(name) == ChannelCoverage(
            candidates=CANDIDATES[name],
            returned=returned[name],
            refused=refusal,
            truncated=len(RETURNED[name]) - returned[name],
            token_budget=budgets[name],
            tokens_used=used[name],
            tokens_truncated=total[name] - used[name],
        ), f"{name}: wrong public budget coverage"


def _assert_provenance(result: QueryResult, expected_ids: tuple[str, ...]) -> None:
    """API §2: only returned items appear in the flattened channel-ordered provenance bundle."""
    assert tuple(artifact_id for artifact_id, _ in result.provenance_bundle) == expected_ids
    evidence = VisibleReference("t0", "/u/u1/", "turn", "support")
    derivation = VisibleReference("src", "/u/u1/", "memory_version", "consolidation")
    for artifact_id, refs in result.provenance_bundle:
        expected = (evidence, derivation) if artifact_id in {"fresh", "stale", "inference"} else (evidence,)
        assert refs == expected, (artifact_id, refs)


async def test_public_change_preserves_routing_and_bounds_with_and_without_budget(
    change_world: tuple[Engine, CallerContext, Bound],
) -> None:
    engine, ctx, bound = change_world
    roomy = await _query(engine, ctx, _request())
    _assert_roomy_channels(roomy, bound)
    _assert_changes(roomy)
    _assert_provenance(roomy, tuple(artifact_id for name in CHANNELS for artifact_id in RETURNED[name]))
    eligible = {name: len(RETURNED[name]) for name in CHANNELS}

    bounded_tokens = len(_bounded_text(bound).split())
    totals = _fixture_token_totals(bound)
    _assert_coverage(roomy, dict.fromkeys(CHANNELS, 2048), eligible, totals, totals)

    previous = await _query(engine, ctx, {"mode": "historical_as_of", "target_time": PREVIOUS})
    current = await _query(engine, ctx, {"mode": "historical_as_of", "target_time": CURRENT})
    assert previous.changes == current.changes == ()
    for name in CHANNELS:
        endpoints = {item.artifact_id: item for item in (*previous.channel(name), *current.channel(name))}
        assert tuple(endpoints[artifact_id] for artifact_id in RETURNED[name]) == roomy.channel(name)

    zero = dict.fromkeys(CHANNELS, 0)
    for cap in (0, bounded_tokens - 1, bounded_tokens):
        budgets = zero | {"facts": cap}
        clipped = await _query(engine, ctx, _request(budget={**budgets, "total_tokens": cap}))
        fits = cap == bounded_tokens
        assert clipped.facts == (roomy.facts[:1] if fits else ()), "bounded fact must fit whole or be omitted"
        assert clipped.hypotheses == clipped.procedures == clipped.conflicts == clipped.audit == ()
        assert clipped.refused == roomy.refused
        _assert_changes(clipped)
        _assert_coverage(
            clipped, budgets, zero | {"facts": int(fits)}, zero | {"facts": bounded_tokens if fits else 0}, totals
        )
        _assert_provenance(clipped, ("fresh",) if fits else ())


def _assert_shared_budget_result(
    result: QueryResult, roomy: QueryResult, bound: Bound, budgets: Mapping[str, int], shared: int
) -> None:
    """Only the one-token procedure fits a shared cap of one; a cap of zero allows no content."""
    assert result.facts == result.hypotheses == result.conflicts == result.audit == (), "shared cap exceeded"
    assert result.procedures == (roomy.procedures if shared else ()), "shared cap must retain the fitting procedure"
    assert not result.is_audit
    assert result.refused == roomy.refused == (("incomplete", "bound_missing"),)
    _assert_changes(result)
    _assert_provenance(result, ("procedure",) if shared else ())
    counts = dict.fromkeys(CHANNELS, 0) | {"procedures": shared}
    _assert_coverage(result, budgets, counts, counts, _fixture_token_totals(bound))


@pytest.mark.parametrize("shared", [0, 1], ids=["zero-tokens", "one-token"])
async def test_public_change_enforces_shared_budget_with_roomy_channel_caps(
    change_world: tuple[Engine, CallerContext, Bound], shared: int
) -> None:
    engine, ctx, bound = change_world
    budgets: dict[str, int] = dict.fromkeys(CHANNELS, 2048)
    roomy = await _query(engine, ctx, _request(budget=budgets))
    _assert_roomy_channels(roomy, bound)
    result = await _query(engine, ctx, _request(budget={**budgets, "total_tokens": shared, "facts_floor_tokens": 0}))
    _assert_shared_budget_result(result, roomy, bound, budgets, shared)


async def test_public_change_reports_no_partial_diff_if_an_endpoint_exceeds_the_limit(
    change_world: tuple[Engine, CallerContext, Bound],
) -> None:
    """V2-D81: zero answer tokens do not turn an over-limit historical view into a partial diff."""
    engine, ctx, _ = change_world
    budget = {"total_tokens": 0}
    # The current view has seven rows, including the refusal and the shared row; the previous view has two.
    _assert_changes(await _query(engine, ctx, _request(limit=7, budget=budget)))
    for limit in (1, 6):
        with pytest.raises(EntryError) as caught:
            await engine.call(ctx, "query", {"request": _request(limit=limit, budget=budget)})
        assert (caught.value.code, caught.value.detail) == ("resource_limit", "change_view_exceeds_limit")


async def test_public_change_refuses_when_only_the_earlier_view_exceeds_the_limit(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> None:
    """V2-D81: check the earlier-view limit independently of the later-view limit."""
    async with connect(database) as conn:
        await _seed_prior_claims(conn)
        async with _restricted_reader(database, conn) as (engine, ctx):
            earlier = await _query(engine, ctx, {"mode": "historical_as_of", "target_time": PREVIOUS})
            later = await _query(engine, ctx, {"mode": "historical_as_of", "target_time": CURRENT})
            assert sorted(item.artifact_id for name in CHANNELS for item in earlier.channel(name)) == [
                "inference",
                "src",
            ]
            assert [item.artifact_id for name in CHANNELS for item in later.channel(name)] == ["src"]
            assert earlier.refused == later.refused == ()

            budget = {"total_tokens": 0}
            fits = await _query(engine, ctx, _request(limit=2, budget=budget))
            (change,) = fits.changes
            assert asdict(change) == {"outcome": "removed", "previous": ("inference",), "current": ()}
            assert all(not fits.channel(name) for name in CHANNELS)
            with pytest.raises(EntryError) as caught:
                await engine.call(ctx, "query", {"request": _request(limit=1, budget=budget)})
            assert (caught.value.code, caught.value.detail) == ("resource_limit", "change_view_exceeds_limit")


async def test_public_change_oracles_detect_synthetic_projection_errors(
    change_world: tuple[Engine, CallerContext, Bound],
) -> None:
    """The checks kill deliberate errors added to harmless public result objects, without altering the engine."""
    engine, ctx, bound = change_world
    good = await _query(engine, ctx, _request())
    _assert_roomy_channels(good, bound)
    _assert_changes(good)
    stale, inferred = good.hypotheses
    fresh, *_ = good.facts

    for artifact, message in (
        (inferred, "inferred claim in facts"),
        (stale, "stale abstraction in facts"),
        (good.conflicts[0], "contradiction in facts"),
    ):
        wrong = copy(good)
        wrong.facts = (*good.facts, artifact)
        with pytest.raises(AssertionError, match=message) as caught:
            _assert_roomy_channels(wrong, bound)
        print(str(caught.value).splitlines()[0])

    for invalid_bound, content, message in (
        (replace(bound, support_count=39), fresh.content, "fresh fact lost its precise bound"),
        (bound, "x", "fresh fact text lost its complete bound"),
    ):
        wrong = copy(good)
        incorrect = copy(fresh)
        incorrect.bound, incorrect.content = invalid_bound, content
        wrong.facts = (incorrect, *good.facts[1:])
        with pytest.raises(AssertionError, match=message) as caught:
            _assert_roomy_channels(wrong, bound)
        print(str(caught.value).splitlines()[0])

    for incorrect in (
        (replace(good.changes[0], current=("in 40 observed cases",)), *good.changes[1:]),
        (*good.changes[:2], replace(good.changes[2], previous=()), *good.changes[3:]),
    ):
        wrong = copy(good)
        wrong.changes = incorrect
        with pytest.raises(AssertionError, match="changes must be the exact ID-only diff") as caught:
            _assert_changes(wrong)
        print(str(caught.value).splitlines()[0])

    wrong = copy(good)
    wrong.coverage = replace(good.coverage, facts=replace(good.coverage.facts, tokens_used=0))
    totals = _fixture_token_totals(bound)
    eligible = {name: len(RETURNED[name]) for name in CHANNELS}
    with pytest.raises(AssertionError, match="facts: wrong public budget coverage") as caught:
        _assert_coverage(wrong, dict.fromkeys(CHANNELS, 2048), eligible, totals, totals)
    print(str(caught.value).splitlines()[0])

    with pytest.raises(AssertionError, match="shared cap exceeded") as caught:
        _assert_shared_budget_result(good, good, bound, dict.fromkeys(CHANNELS, 2048), shared=1)
    print(str(caught.value).splitlines()[0])
