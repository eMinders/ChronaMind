"""E-032 association expansion against the migrated schema and the restricted runtime role."""

import inspect
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import Call, CallerContext, Credential, Engine
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Runtime
from custom_memory_engine.engine.expansion import expand_associations
from custom_memory_engine.engine.planning import Plan
from custom_memory_engine.engine.predicates import current_state, historical_as_of, reference_instant
from custom_memory_engine.scope.access import ReadSet, Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import caller, make_version, world
from tests.integration.test_lifecycle_transitions import insert_version, seed_source
from tests.integration.test_migration_m1 import database, rows  # noqa: F401 (fixture)
from tests.integration.test_planning_db import QUERY, Recorder
from tests.integration.test_staleness_db import retire, verified_world

pytestmark = pytest.mark.integration

TARGET = datetime(2026, 3, 20, tzinfo=UTC)
DAY = timedelta(days=1)
START = TARGET - 2 * DAY
RECORDED = TARGET + DAY


def runtime_for(engine: Engine) -> Runtime:
    assert isinstance(engine.resolver, DbTargets)
    return engine.resolver.runtime


async def claim(
    conn: AsyncConnection,
    artifact: str,
    *,
    event_start: datetime = START,
    event_end: datetime | None = None,
    recorded: datetime = RECORDED,
    scope: str = "/u/alice/",
    visibility: str = "private",
) -> None:
    """Seed an asserted artifact with an independently chosen event interval and recording instant."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        await conn.execute(
            "INSERT INTO memory_version "
            "(id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer, visibility, scope_path) "
            "VALUES (%s, %s, 'alice', 'active', 'user', 'diet', false, %s, tstzrange(%s, %s), "
            "tstzrange(%s, NULL), 'semantic', %s, %s)",
            (artifact, artifact, artifact, event_start, event_end, recorded, visibility, scope),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, 't-alice', 'alice', 'x', 'support')",
            (artifact,),
        )


async def link(
    conn: AsyncConnection,
    origin: str,
    endpoint: str,
    edge_type: str = "related_to",
    *,
    user: str = "alice",
    scope: str | None = None,
    visibility: str = "private",
) -> None:
    # Keep the edge's scope independent of its endpoints' scopes.
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, scope_path, visibility) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (f"{origin}:{endpoint}:{edge_type}", user, origin, endpoint, edge_type, user, scope, visibility),
        )


async def reach(
    engine: Engine,
    seeds: Sequence[str],
    budget: int,
    *,
    user: str = "alice",
    at: datetime | None = None,
    read_set: ReadSet | None = None,
    row_budget: int = 1000,
) -> tuple[tuple[str, int], ...]:
    runtime = runtime_for(engine)
    async with runtime.session(user) as conn:
        clock = await reference_instant(conn)
        query = current_state(user, clock) if at is None else historical_as_of(user, at, clock)
        expanded = await expand_associations(
            conn,
            query,
            caller(user).read_set if read_set is None else read_set,
            seeds,
            database_clock=clock,
            hop_budget=budget,
            row_budget=row_budget,
            schema_name=runtime.schema_name,
        )
    return tuple((row.id, row.hop_distance) for row in expanded)


async def test_expansion_edge_directions(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "seed")
        # Each type occurs in both stored directions so this catches both a missing edge and a too-permissive rule.
        for kind in (
            "related_to", "contradicts", "instance_of", "precedes", "refines", "specialises",
            "caused_by", "part_of", "corroborated_by", "supersedes",
        ):  # fmt: skip
            await make_version(conn, f"in-{kind}")
            await make_version(conn, f"out-{kind}")
            await link(conn, f"in-{kind}", "seed", kind)
            await link(conn, "seed", f"out-{kind}", kind)
        assert await reach(engine, ["seed"], 1) == (
            ("in-contradicts", 1),
            ("in-instance_of", 1),
            ("in-precedes", 1),
            ("in-related_to", 1),
            ("out-caused_by", 1),
            ("out-contradicts", 1),
            ("out-corroborated_by", 1),
            ("out-instance_of", 1),
            ("out-part_of", 1),
            ("out-refines", 1),
            ("out-related_to", 1),
            ("out-specialises", 1),
            ("out-supersedes", 1),
        )


@pytest.mark.parametrize("mode", ["current_state", "historical_as_of", "historical_window", "audit_as_of"])
async def test_expansion_uses_each_temporal_mode(database, mode: str) -> None:  # noqa: F811
    """With limit one, the eligible neighbor can appear only by expansion."""
    async with world(database) as (conn, engine, _):
        register_read_handlers(engine, runtime_for(engine))
        await claim(conn, "eligible")
        await claim(conn, "beyond-default-budget")
        request: dict[str, object] = {"mode": mode, "limit": 1}
        if mode == "current_state":
            await claim(conn, "excluded", event_end=TARGET)
        elif mode == "historical_as_of":
            request["target_time"] = TARGET.isoformat()
            await claim(conn, "excluded", event_start=TARGET + DAY)
        elif mode == "historical_window":
            request.update(window_start=START.isoformat(), window_end=TARGET.isoformat())
            # Exact, exclusive window endpoint: the row would match a closed window.
            await claim(conn, "excluded", event_start=TARGET)
        await claim(conn, "seed", recorded=RECORDED + DAY)
        if mode == "audit_as_of":
            request.update(target_time=TARGET.isoformat(), belief_time=(RECORDED + DAY).isoformat())
            await claim(conn, "excluded", recorded=RECORDED + 2 * DAY)
        await link(conn, "seed", "eligible")
        await link(conn, "seed", "excluded")
        await link(conn, "eligible", "beyond-default-budget")
        result = await engine.call(caller("alice"), "query", {"request": request})
        assert isinstance(result, QueryResult)
        assert result.refused == ()
        assert result.hypotheses == result.procedures == result.conflicts == ()
        assert result.is_audit == (mode == "audit_as_of")
        artifacts, empty = (result.audit, result.facts) if result.is_audit else (result.facts, result.audit)
        assert empty == ()
        assert [(row.artifact_id, row.hop_distance) for row in artifacts] == [("seed", 0), ("eligible", 1)]


async def test_temporal_rejection_stops_the_next_hop(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await claim(conn, "seed")
        await claim(conn, "later-bridge", event_start=TARGET + DAY)
        await claim(conn, "eligible-leaf")
        await link(conn, "seed", "later-bridge", "part_of")
        await link(conn, "later-bridge", "eligible-leaf", "part_of")
        assert await reach(engine, ["seed"], 2, at=TARGET) == ()
        assert await reach(engine, ["seed"], 2, at=TARGET + DAY) == (("later-bridge", 1), ("eligible-leaf", 2))


async def test_unreadable_endpoint_cannot_bridge_to_a_visible_row(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        register_read_handlers(engine, runtime_for(engine))
        await claim(conn, "visible-leaf")
        await claim(conn, "seed", recorded=RECORDED + DAY)
        ctx = caller("alice")
        request = {"request": {"limit": 1}}
        public_absent = await engine.call(ctx, "query", request)
        assert isinstance(public_absent, QueryResult)
        assert [(row.artifact_id, row.hop_distance) for row in public_absent.facts] == [("seed", 0)]
        absent = await reach(engine, ["seed"], 2)
        assert absent == ()
        await claim(conn, "hidden-bridge", scope="/u/alice/p/other/")
        await link(conn, "seed", "hidden-bridge", "part_of")
        await link(conn, "hidden-bridge", "visible-leaf", "part_of")
        assert await reach(engine, ["seed"], 2) == absent
        assert await reach(engine, ["hidden-bridge"], 2) == absent
        assert await engine.call(ctx, "query", request) == public_absent
        await link(conn, "seed", "visible-leaf", "part_of")
        assert await reach(engine, ["seed"], 2) == (("visible-leaf", 1),)


async def test_expansion_honours_hop_budget(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        for node in ("seed", "one", "two", "three", "isolated"):
            await make_version(conn, node)
        await link(conn, "seed", "one", "part_of")
        await link(conn, "one", "two", "part_of")
        await link(conn, "two", "three", "part_of")
        assert await reach(engine, [], 3) == ()
        assert await reach(engine, ["isolated"], 3) == ()
        assert await reach(engine, ["seed"], 0) == ()
        assert await reach(engine, ["seed"], 1) == (("one", 1),)
        assert await reach(engine, ["seed"], 2) == (("one", 1), ("two", 2))
        assert await reach(engine, ["seed"], 3) == (("one", 1), ("two", 2), ("three", 3))


async def test_expansion_preserves_derivation_graph(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await seed_source(conn)
        await insert_version(conn, "derived", "claim", "inferred")
        await insert_version(conn, "seed", "claim", "asserted")
        await link(conn, "seed", "derived", user="u1")
        graph_sql = (
            "SELECT convert_to(row_to_json(d)::text, 'UTF8') FROM memory_derivation AS d "
            "ORDER BY derived_version_id, source_version_id"
        )
        before = await rows(conn, graph_sql)
        assert len(before) == 1
        snapshot = before[0][0]
        assert isinstance(snapshot, bytes) and b'"source_version_id":"src"' in snapshot
        assert await reach(engine, ["seed"], 2, user="u1") == (("derived", 1),)
        assert await rows(conn, graph_sql) == before


async def test_expansion_is_deterministic(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        for node in ("seed-a", "seed-b", "right", "left", "deep", "leaf"):
            await make_version(conn, node)
        for origin, endpoint in (
            ("seed-b", "right"),
            ("seed-a", "left"),
            ("left", "deep"),
            ("right", "deep"),
            ("deep", "seed-a"),
            ("deep", "leaf"),
        ):
            await link(conn, origin, endpoint, "part_of")
        expected = (("left", 1), ("right", 1), ("deep", 2), ("leaf", 3))
        for seeds in (["seed-a", "seed-b"], ["seed-b", "seed-a", "seed-b"], ["seed-a", "seed-b"]):
            assert await reach(engine, seeds, 4) == expected
        await link(conn, "seed-a", "deep", "part_of")
        assert await reach(engine, ["seed-b", "seed-a"], 4) == (("deep", 1), ("left", 1), ("right", 1), ("leaf", 2))


async def test_expansion_is_tenant_isolated(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        for user in ("alice", "bob"):
            await make_version(conn, f"{user}-seed", user)
            await make_version(conn, f"{user}-leaf", user)
            await link(conn, f"{user}-seed", f"{user}-leaf", user=user)
        seeds = ["alice-seed", "bob-seed"]
        assert await reach(engine, seeds, 2) == (("alice-leaf", 1),)
        assert await reach(engine, seeds, 2, user="bob") == (("bob-leaf", 1),)
        assert await reach(engine, ["bob-seed"], 2) == ()


async def test_expanded_stale_abstraction_stays_a_hypothesis(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, schema):
        await verified_world(conn, schema)
        # Give the seed the newest recording so abs1 cannot already be a candidate at limit one.
        await conn.execute("SELECT set_config('app.clock', %s, false)", (RECORDED.isoformat(),))
        await insert_version(conn, "seed", "claim", "asserted")
        await link(conn, "seed", "abs1", user="u1")
        register_read_handlers(engine, runtime_for(engine))
        query = {"request": {"limit": 1}}
        fresh = await engine.call(caller("u1"), "query", query)
        assert isinstance(fresh, QueryResult)
        assert [(row.artifact_id, row.hop_distance) for row in fresh.facts] == [("seed", 0), ("abs1", 1)]
        assert fresh.hypotheses == ()
        await retire(conn, "mid")
        stale = await engine.call(caller("u1"), "query", query)
        assert isinstance(stale, QueryResult)
        assert [(row.artifact_id, row.hop_distance) for row in stale.facts] == [("seed", 0)]
        assert [(row.artifact_id, row.hop_distance, row.verification) for row in stale.hypotheses] == [
            ("abs1", 1, "verified")
        ]


async def test_replayed_legacy_plan_keeps_expansion_disabled(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await claim(conn, "neighbor")
        await claim(conn, "seed", recorded=RECORDED + DAY)
        await link(conn, "seed", "neighbor")
        recorder = Recorder()
        handler = register_read_handlers(engine, runtime_for(engine), recorder=recorder)
        ctx = caller("alice")
        arguments = {"request": {"limit": 1}}
        original = await engine.call(ctx, "query", arguments)
        assert isinstance(original, QueryResult)
        assert [item.artifact_id for item in original.facts] == ["seed", "neighbor"]
        # a genuine pre-E-032 record: the intent *and* its budget say no traversal (a `none` plan at a nonzero depth
        # is unrepresentable, which `Plan.__post_init__` refuses)
        legacy = Plan.from_dict(replace(recorder.last, expansion="none", hop_budget=0).to_dict())
        replay = await handler.execute_plan(Call(ctx=ctx, operation=QUERY, arguments=arguments), legacy)
        assert [(item.artifact_id, item.hop_distance) for item in replay.facts] == [("seed", 0)]


async def test_expansion_cannot_widen_a_project_selector(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        register_read_handlers(engine, runtime_for(engine))
        project = "/u/alice/p/first/"
        await claim(conn, "sibling", scope="/u/alice/p/second/", visibility="project")
        await claim(conn, "seed", scope=project, visibility="project", recorded=RECORDED + DAY)
        await link(conn, "seed", "sibling", scope=project, visibility="project")
        ctx = caller("alice")
        narrow = await engine.call(ctx, "query", {"request": {"limit": 1, "scope": project}})
        broad = await engine.call(ctx, "query", {"request": {"limit": 1}})
        assert isinstance(narrow, QueryResult) and isinstance(broad, QueryResult)
        assert [(item.artifact_id, item.hop_distance) for item in narrow.facts] == [("seed", 0)]
        assert [(item.artifact_id, item.hop_distance) for item in broad.facts] == [("seed", 0), ("sibling", 1)]


@pytest.mark.parametrize(
    ("source", "target"), [("seed", "neighbor"), ("neighbor", "seed")], ids=["forward", "reverse"]
)
async def test_expansion_excludes_a_private_ancestor_link(database, source: str, target: str) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        register_read_handlers(engine, runtime_for(engine))
        project = "/u/alice/p/first/"
        await claim(conn, "neighbor", scope=project, visibility="project")
        await claim(conn, "seed", scope=project, visibility="project", recorded=RECORDED + DAY)
        read_set = resolve_read_set("alice", [Selector.exact(project)], [])
        narrow = CallerContext.from_credential(Credential("alice", "C", False), write_scope=project, read_set=read_set)
        broad = caller("alice")
        request = {"request": {"limit": 1}}

        absent = await engine.call(narrow, "query", request)
        assert isinstance(absent, QueryResult)
        assert [(item.artifact_id, item.hop_distance) for item in absent.facts] == [("seed", 0)]

        root_link = await engine.call(broad, "link", {"from": source, "to": target, "type": "related_to"})
        assert isinstance(root_link, str)
        assert await rows(conn, "SELECT scope_path, visibility FROM association WHERE id = %s", (root_link,)) == [
            ("/u/alice/", "private")
        ]
        assert await engine.call(narrow, "query", request) == absent
        broad_result = await engine.call(broad, "query", request)
        assert isinstance(broad_result, QueryResult)
        assert [(item.artifact_id, item.hop_distance) for item in broad_result.facts] == [("seed", 0), ("neighbor", 1)]

        await engine.call(broad, "unlink", {"association_id": root_link})
        await link(conn, source, target, scope=project)
        authorized = await engine.call(narrow, "query", request)
        assert isinstance(authorized, QueryResult)
        assert [(item.artifact_id, item.hop_distance) for item in authorized.facts] == [("seed", 0), ("neighbor", 1)]


async def test_expansion_rechecks_the_association_scope_at_the_second_hop(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        project = "/u/alice/p/first/"
        for artifact in ("seed", "bridge", "leaf"):
            await claim(conn, artifact, scope=project, visibility="project")
        read_set = resolve_read_set("alice", [Selector.exact(project)], [])
        await link(conn, "seed", "bridge", "part_of", scope=project)
        assert await reach(engine, ["seed"], 2, read_set=read_set) == (("bridge", 1),)

        # This edge's endpoint is readable, but the edge is outside the selector.
        await link(conn, "bridge", "leaf", "part_of")
        assert await reach(engine, ["seed"], 2, read_set=read_set) == (("bridge", 1),)

        await link(conn, "bridge", "leaf", "related_to", scope=project)
        assert await reach(engine, ["seed"], 2, read_set=read_set) == (("bridge", 1), ("leaf", 2))


# ───────────────────────────────────────────── E-032-F2: a response carries a bounded page ──


async def test_a_dense_seed_cannot_make_one_response_unbounded(database) -> None:  # noqa: F811
    """``E-032-F2``: the response is bounded by the page size, not by the graph's out-degree.

    Before the bound, one seed with 30 neighbours returned 30 reached rows for a caller who asked for one page, and the
    one-hop ceiling is the degree cap (32) times ``MAX_LIMIT`` -- 32,000 rows -- which no page size promises.
    """
    async with world(database) as (conn, engine, _):
        await make_version(conn, "seed")
        # 30 is under migration 3's incident cap of 32, so this fan-out is one the schema permits in a single hop
        for index in range(30):
            await make_version(conn, f"leaf-{index:02d}")
            await link(conn, "seed", f"leaf-{index:02d}", "related_to")
        assert len(await reach(engine, ["seed"], 1)) == 30, "the control: the whole fan-out is reachable"
        assert len(await reach(engine, ["seed"], 1, row_budget=1)) == 1
        assert len(await reach(engine, ["seed"], 1, row_budget=7)) == 7


async def test_a_truncated_page_is_the_nearest_hop_prefix_of_the_order(database) -> None:  # noqa: F811
    """Truncation takes the order this function already guarantees, so a page is a prefix and not a subset."""
    async with world(database) as (conn, engine, _):
        for node in ("seed", "near-a", "near-b", "far-a"):
            await make_version(conn, node)
        await link(conn, "seed", "near-a", "related_to")
        await link(conn, "seed", "near-b", "related_to")
        await link(conn, "near-b", "far-a", "related_to")
        full = await reach(engine, ["seed"], 2)
        assert full == (("near-a", 1), ("near-b", 1), ("far-a", 2))
        assert await reach(engine, ["seed"], 2, row_budget=2) == full[:2]
        assert await reach(engine, ["seed"], 2, row_budget=1) == full[:1]


async def test_the_row_budget_is_required_not_defaulted() -> None:
    """A forgotten budget is the defect itself, so the parameter has no default to forget (``E-032-F2``)."""
    assert inspect.signature(expand_associations).parameters["row_budget"].default is inspect.Parameter.empty


async def test_a_zero_row_budget_reaches_nothing(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "seed")
        await make_version(conn, "leaf")
        await link(conn, "seed", "leaf", "related_to")
        assert await reach(engine, ["seed"], 1, row_budget=0) == ()


@pytest.mark.parametrize("row_budget", [-1, True, 1.5])
async def test_a_malformed_row_budget_is_refused(database, row_budget: object) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await make_version(conn, "seed")
        with pytest.raises(ValueError):
            await reach(engine, ["seed"], 1, row_budget=row_budget)  # type: ignore[arg-type]


async def test_query_bounded_the_way_the_plan_says(database) -> None:  # noqa: F811
    """Through the public ``query``: a dense neighbour set cannot return more reached rows than the plan's page size.

    The end-to-end form of the finding -- the plan's ``rows`` is the page size and the executor passes it as the
    reached-row budget, so a whole response is at most twice ``limit``. Control: passing a large literal instead of
    ``plan.rows`` returns the 30-row fan-out and fails here.
    """
    async with world(database) as (conn, engine, _):
        for index in range(30):
            await claim(conn, f"leaf-{index:02d}", recorded=RECORDED + index * DAY)
        await claim(conn, "hub", recorded=RECORDED + 40 * DAY)  # the newest belief: the one candidate at limit 1
        for index in range(30):
            await link(conn, "hub", f"leaf-{index:02d}", "related_to")
        recorder = Recorder()
        register_read_handlers(engine, runtime_for(engine), recorder=recorder)
        limited = await engine.call(caller("alice"), "query", {"request": {"limit": 1}})
        assert isinstance(limited, QueryResult)
        assert recorder.last.rows == 1
        reached = [item for item in limited.facts if item.hop_distance > 0]
        assert len(reached) <= 1, [item.artifact_id for item in reached]
        total = sum(len(limited.channel(name)) for name in ("facts", "hypotheses", "procedures", "conflicts"))
        assert total <= 2, total


async def test_the_executor_traverses_the_depth_the_plan_recorded(database) -> None:  # noqa: F811
    """``E-032-F3`` end to end: ``execute_plan`` runs *the plan's* budget, not whatever the default is.

    The page is wide enough for the reached rows (``limit`` 2, so a budget of two admits both) that the response bound
    cannot mask the depth: only ``plan.hop_budget`` decides two hops from one. Control: substituting a literal ``1`` at
    the call site returns one hop and fails here.
    """
    async with world(database) as (conn, engine, _):
        # Increasing recording order (the watermark is monotonic). The candidate page of two is then the hub and one
        # unrelated artifact, so the whole chain must arrive by expansion.
        await claim(conn, "deep", recorded=RECORDED + 1 * DAY)
        await claim(conn, "middle", recorded=RECORDED + 2 * DAY)
        await claim(conn, "unrelated", recorded=RECORDED + 3 * DAY)
        await claim(conn, "hub", recorded=RECORDED + 4 * DAY)
        await link(conn, "hub", "middle", "part_of")
        await link(conn, "middle", "deep", "part_of")
        recorder = Recorder()
        handler = register_read_handlers(engine, runtime_for(engine), recorder=recorder)
        ctx = caller("alice")
        arguments = {"request": {"limit": 2}}
        one_hop = await engine.call(ctx, "query", arguments)
        assert isinstance(one_hop, QueryResult)
        assert recorder.last.hop_budget == 1, "the planner records the default depth"
        # candidates keep the temporal read's own order (newest belief first); reached rows follow in hop order
        assert [(item.artifact_id, item.hop_distance) for item in one_hop.facts] == [
            ("hub", 0),
            ("unrelated", 0),
            ("middle", 1),
        ]
        deeper = Plan.from_dict({**recorder.last.to_dict(), "hop_budget": 2})
        replay = await handler.execute_plan(Call(ctx=ctx, operation=QUERY, arguments=arguments), deeper)
        assert [(item.artifact_id, item.hop_distance) for item in replay.facts] == [
            ("hub", 0),
            ("unrelated", 0),
            ("middle", 1),
            ("deep", 2),
        ]
