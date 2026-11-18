"""``V2-D80``: a ``conflicts`` item's counterparts come from its own edges, never from what the query retrieved.

``CHANNELS.md`` §1 makes channel membership a pure function of five inputs -- "not on rank, score, budget, caller, time
of day or the query" -- and ``open_contradiction`` is one of them, derived from the edge (§2.2). R5 requires a
``conflicts`` item to show both sides and never merge them; R6 / ``V2-D37`` render anything the caller cannot read as
an opaque handle. Two defects broke this when ``query`` began routing through ``assemble``:

* **scope-dependent membership** -- an artifact whose counterpart lay outside the candidate page was *refused*
  (``conflict_counterpart_missing``) instead of rendered in ``conflicts``, so the same artifact appeared or vanished
  with the query's scope;
* **cross-pair merging** -- every contradicted row was offered as a side of every other, so ``a1 <-> b1`` and
  ``a2 <-> b2`` rendered ``a1`` against ``a2`` and ``b2`` as well.
"""

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import Call, Engine
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets
from custom_memory_engine.engine.planning import Plan, plan_retrieval
from custom_memory_engine.scope.provenance import is_opaque_reference
from tests.integration.test_api_wiring_structure import caller, world
from tests.integration.test_association_expansion_db import DAY, RECORDED, claim, link
from tests.integration.test_migration_m1 import database  # noqa: F401 (fixture)
from tests.integration.test_planning_db import QUERY

pytestmark = pytest.mark.integration

ONE = "/u/alice/p/one/"
TWO = "/u/alice/p/two/"


async def project_claim(conn: AsyncConnection, node: str, scope: str, *, day: int) -> None:
    await claim(conn, node, scope=scope, visibility="project", recorded=RECORDED + day * DAY)


async def contradicts(conn: AsyncConnection, left: str, right: str, scope: str = "/u/alice/") -> None:
    # `origin` defaults to `tier_c`, one of the two origins that open a contradiction (C-P4)
    await link(conn, left, right, "contradicts", scope=scope, visibility="project")


async def ask(engine: Engine, **request: object) -> QueryResult:
    assert isinstance(engine.resolver, DbTargets)
    register_read_handlers(engine, engine.resolver.runtime)
    result = await engine.call(caller("alice"), "query", {"request": {"limit": 10, **request}})
    assert isinstance(result, QueryResult)
    return result


def sides(result: QueryResult, artifact_id: str) -> list[tuple[str, str]]:
    (item,) = [item for item in result.conflicts if item.artifact_id == artifact_id]
    return [(side.artifact_id, side.text) for side in item.conflict_sides]


async def test_each_conflict_shows_only_its_own_counterpart(database) -> None:  # noqa: F811
    """R5 (never merge): two independent contradictions stay two pairs. Control: offering every contradicted row
    as a side -- the defect -- renders ``a1`` against ``a2`` and ``b2`` and fails here."""
    async with world(database) as (conn, engine, _):
        for day, node in enumerate(("a1", "b1", "a2", "b2"), start=1):
            await project_claim(conn, node, "/u/alice/", day=day)
        await contradicts(conn, "a1", "b1")
        await contradicts(conn, "a2", "b2")
        result = await ask(engine)
        assert result.refused == ()
        assert [side for side, _ in sides(result, "a1")] == ["a1", "b1"]
        assert [side for side, _ in sides(result, "b1")] == ["b1", "a1"]
        assert [side for side, _ in sides(result, "a2")] == ["a2", "b2"]
        assert [side for side, _ in sides(result, "b2")] == ["b2", "a2"]


async def test_a_readable_counterpart_outside_the_page_is_loaded_not_required(database) -> None:  # noqa: F811
    """§1: the counterpart is loaded by its edge, not by the candidate page.

    Expansion could reach it as a supplemental row, which would hide the defect this test is about. The replay below
    uses a **no-expansion** plan, so the only route left for ``older`` is the edge load. Control: building the sides
    from ``rows_by_id`` leaves ``newer`` with one side, and assembly refuses it as ``conflict_counterpart_missing``.
    """
    async with world(database) as (conn, engine, _):
        await project_claim(conn, "older", "/u/alice/", day=1)
        await project_claim(conn, "newer", "/u/alice/", day=2)
        await contradicts(conn, "newer", "older")
        assert isinstance(engine.resolver, DbTargets)
        handler = register_read_handlers(engine, engine.resolver.runtime)
        arguments = {"request": {"limit": 1}}
        plan = plan_retrieval(
            handler._intent_for("current_state", arguments["request"], 1, None), caller("alice").read_set
        )
        no_expansion = Plan.from_dict({**plan.to_dict(), "expansion": "none", "hop_budget": 0})
        result = await handler.execute_plan(
            Call(ctx=caller("alice"), operation=QUERY, arguments=arguments), no_expansion
        )
        assert result.refused == ()
        assert [item.artifact_id for item in result.conflicts] == ["newer"]
        assert sides(result, "newer") == [("newer", "newer"), ("older", "older")]


async def test_membership_does_not_depend_on_the_query_scope(database) -> None:  # noqa: F811
    """The defect itself: a scoped query refused an artifact the unscoped query rendered.

    Control: requiring the counterpart to be among the retrieved rows refuses ``left`` as
    ``conflict_counterpart_missing`` and fails every assertion below.
    """
    async with world(database) as (conn, engine, _):
        await project_claim(conn, "right", TWO, day=1)
        await project_claim(conn, "left", ONE, day=2)
        await contradicts(conn, "left", "right", scope=ONE)
        unscoped = await ask(engine)
        scoped = await ask(engine, scope=ONE)
        for result in (unscoped, scoped):
            assert result.refused == ()
            assert "left" in {item.artifact_id for item in result.conflicts}
            assert "left" not in {item.artifact_id for item in result.facts}
        # unscoped: the counterpart is readable and shown; scoped to ONE: it is outside the read set and opaque
        assert sides(unscoped, "left")[1] == ("right", "right")
        (handle, text) = sides(scoped, "left")[1]
        assert is_opaque_reference(handle) and handle != "right" and text == ""


async def test_get_shows_both_sides_exactly_as_query_does(database) -> None:  # noqa: F811
    """R5 holds wherever a conflicts item is rendered, not only on the ``query`` route."""
    async with world(database) as (conn, engine, _):
        await project_claim(conn, "older", "/u/alice/", day=1)
        await project_claim(conn, "newer", "/u/alice/", day=2)
        await contradicts(conn, "newer", "older")
        listed = await ask(engine)
        got = await engine.call(caller("alice"), "get", {"artifact_id": "newer"})
        assert isinstance(got, Artifact) and got.channel == "conflicts"
        assert [(s.artifact_id, s.text) for s in got.conflict_sides] == sides(listed, "newer")


async def test_an_opaque_counterpart_leaks_no_identifier_or_content(database) -> None:  # noqa: F811
    """R6 / ``V2-D37``: the hidden side is a per-caller handle -- not the id, and no text."""
    async with world(database) as (conn, engine, _):
        secret = "the counterpart's private wording"
        await claim(conn, "hidden", scope=TWO, visibility="project", recorded=RECORDED + DAY)
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        await project_claim(conn, "shown", ONE, day=2)
        await contradicts(conn, "shown", "hidden", scope=ONE)
        scoped = await ask(engine, scope=ONE)
        rendered = repr([(s.artifact_id, s.text) for item in scoped.conflicts for s in item.conflict_sides])
        assert "hidden" not in rendered and TWO not in rendered and secret not in rendered


async def test_the_engine_never_marks_an_open_contradiction_it_cannot_show_both_sides_of(database) -> None:  # noqa: F811
    """Assembly refuses a childless ``open_contradiction`` (R5), so the engine must never produce one.

    That refusal is the correct behaviour: R5 requires both sides and ``ConflictItem`` refuses fewer than two, so a
    view that cannot show them must be refused rather than rendered as one-sided. The defect was upstream -- sides
    were offered only for counterparts the query had retrieved -- so this pins the boundary from the engine's side:
    for every artifact the read path reports in ``conflicts`` with ``open_contradiction``, both sides arrived.
    """
    async with world(database) as (conn, engine, _):
        await project_claim(conn, "right", TWO, day=1)
        await project_claim(conn, "left", ONE, day=2)
        await contradicts(conn, "left", "right", scope=ONE)
        for request in ({}, {"scope": ONE}):
            result = await ask(engine, **request)
            assert result.refused == (), request
            (item,) = [i for i in result.conflicts if i.artifact_id == "left"]
            assert len(item.conflict_sides) == 2, request
