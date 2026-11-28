"""E-031: the plan path end to end — four modes, replay, and "temporal stays a filter" (through ``Engine.call``).

The pure properties live in ``tests/test_planning.py``. These are the ones that need rows: that every mode produces
its documented result **through the plan path**, that a plan records and replays to the identical result, that
``audit_as_of`` keeps its rows out of every answer channel while planned, and — the security-relevant one — that the
temporal predicate cannot move a row between channels.

**Why the "filter not a score" test is built the way it is.** ``channel_of`` is a pure function of
``(kind, lifecycle, verification, contradiction)``: the mode is not among its arguments, so the mode *cannot* change
membership. That is an argument, not evidence, so the test asserts the consequence directly: for one fixed artifact
the channel is the same in all four modes that return it, and an artifact the mode excludes is absent from every
channel rather than moved to another one.

**Replay.** A recorder captures the plan ``query`` built; ``execute_plan`` then runs that *recorded* plan again and the
result must be identical. The recorded plan is first round-tripped through JSON, so the replay exercises the record
rather than an in-memory object that could share mutable state with the original.

Controls: the read-set-widening test asserts a recorded plan naming a scope the caller does not hold is refused
(``I7``), which is what makes "a plan cannot widen access" a property rather than a hope.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.contract import OPERATIONS
from custom_memory_engine.api.entry import Call, CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.outcome_handlers import register_outcome_handlers
from custom_memory_engine.api.read_handlers import QueryResult, ReadHandlers, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine.planning import MODES, STAGES, Plan
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope.access import ReadEntry, ReadSet, Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import (
    caller,
    database,  # noqa: F401  (fixture)
    world,
)
from tests.integration.test_read_path_db import CODING_PROFILE

pytestmark = pytest.mark.integration

#: The real ``query`` contract entry, so a replayed ``Call`` is the one ``Engine.call`` would have built.
QUERY = next(operation for operation in OPERATIONS if operation.name == "query")

T0 = datetime(2026, 5, 4, 9, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)
SCOPE = "/u/alice/p/planner/"
TURN_TEXT = "The user prefers Python over JavaScript"


def in_scope(user: str = "alice") -> CallerContext:
    root = f"/u/{user}/"
    read_set = resolve_read_set(user, [Selector.of(root), Selector.of(root + "p/planner/")], [])
    return CallerContext.from_credential(Credential(user, "C", False), write_scope=root, read_set=read_set)


async def _receipt(engine: Engine, ctx: CallerContext, operation: str, arguments: dict[str, object]) -> Receipt:
    result = await engine.call(ctx, operation, arguments)
    assert isinstance(result, Receipt), result
    return result


def _ids(result: QueryResult) -> set[str]:
    return {a.artifact_id for a in (*result.facts, *result.hypotheses, *result.procedures, *result.conflicts)}


def _channel_of_artifact(result: QueryResult, artifact_id: str) -> str | None:
    for name in ("facts", "hypotheses", "procedures", "conflicts"):
        if artifact_id in {a.artifact_id for a in result.channel(name)}:
            return name
    return None


class Recorder:
    """A ``PlanRecorder`` that keeps what ``query`` planned, so a test can replay it."""

    def __init__(self) -> None:
        self.plans: list[Plan] = []

    def __call__(self, plan: Plan) -> None:
        self.plans.append(plan)

    @property
    def last(self) -> Plan:
        assert self.plans, "query did not record a plan"
        return self.plans[-1]


class Observer:
    """A ``StageObserver`` that records the stage boundaries it entered, in order."""

    def __init__(self) -> None:
        self.entered: list[str] = []

    def __call__(self, stage: str):
        entered = self.entered

        class _Boundary:
            def __enter__(self) -> None:
                entered.append(stage)

            def __exit__(self, *_exc: object) -> None:
                return None

        return _Boundary()


class Scenario:
    episode_a: str
    episode_b: str
    claim: str
    claim_recorded_at: datetime


@asynccontextmanager
async def scenario(
    database,  # noqa: F811
    *,
    recorder: Recorder | None = None,
    observer: Observer | None = None,
) -> AsyncIterator[tuple[Engine, AsyncConnection, Scenario, ReadHandlers]]:
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        replay = register_read_handlers(engine, runtime, recorder=recorder, observer=observer)
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, 2, 'user', %s, %s)",
            ("turn-e031p-alice", "s-alice", "alice", TURN_TEXT, T0),
        )
        s = Scenario()
        alice = caller("alice")

        def episode(key: str, content: str) -> dict[str, object]:
            return {
                "scope": SCOPE,
                "source": {
                    "type": "tool",
                    "ref": f"tool:{key}",
                    "span": content,
                    "content_hash": canonical_hash(content),
                },
                "content": content,
                "event_time": T0.isoformat(),
                "idempotency_key": key,
            }

        s.episode_a = (await _receipt(engine, alice, "ingest_episode", episode("e031p-a", "ran pytest -x"))).id
        s.episode_b = (await _receipt(engine, alice, "ingest_episode", episode("e031p-b", "coverage 92"))).id
        claim = await _receipt(
            engine,
            alice,
            "assert",
            {
                "scope": SCOPE,
                "kind": "claim",
                "content": TURN_TEXT,
                "evidence": [
                    {
                        "type": "turn",
                        "ref": "turn-e031p-alice",
                        "span": TURN_TEXT,
                        "content_hash": canonical_hash(TURN_TEXT),
                    }
                ],
                "event_range": [T0.isoformat(), T1.isoformat()],
                "idempotency_key": "e031p-claim",
            },
        )
        s.claim, s.claim_recorded_at = claim.id, claim.recorded_at
        yield engine, conn, s, replay


async def _query(engine: Engine, ctx: CallerContext, mode: str, **extra: object) -> QueryResult:
    request: dict[str, object] = {"scope": SCOPE, "mode": mode, **extra}
    result = await engine.call(ctx, "query", {"request": request})
    assert isinstance(result, QueryResult), result
    return result


def _request_for(mode: str, s: Scenario) -> dict[str, object]:
    """A request that reaches a row in ``mode`` (a target inside the claim's event range)."""
    if mode == "current_state":
        return {}
    if mode == "historical_as_of":
        return {"target_time": T0.isoformat()}
    if mode in ("historical_window", "change"):
        # `change` (V2-D81) diffs the views at the window's two instants; its channels are their union
        return {"window_start": T0.isoformat(), "window_end": T1.isoformat()}
    return {"target_time": T0.isoformat(), "belief_time": s.claim_recorded_at.isoformat()}


# ──────────────────────────────────────────────────── criteria 4 and 10: the plan path ──


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_every_mode_plans_and_executes_through_the_plan_path(database, mode: str) -> None:  # noqa: F811
    """Criterion 4: every mode produces its documented result *through* the plan path (``change`` since ``V2-D81``).

    The recorder proves the path was taken (a plan was built and recorded), and the result matches what the mode's
    own documented semantics say — ``audit_as_of`` in ``audit`` and flagged, the others in channels.
    """
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, s, _replay):
        result = await _query(engine, caller("alice"), mode, **_request_for(mode, s))
        plan = recorder.last
        assert plan.mode == mode
        assert plan.route == ("audit" if mode == "audit_as_of" else "channels")
        if mode == "audit_as_of":
            assert result.is_audit is True
            assert _ids(result) == set()
            assert {a.artifact_id for a in result.audit}
        else:
            assert result.is_audit is False
            found = _ids(result)
            assert {s.episode_a, s.episode_b} <= found, f"{mode} lost an episode the predicate admits"
            if mode in ("historical_as_of", "historical_window", "change"):
                assert s.claim in found, f"{mode} must see the claim while its event range is open"


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_every_mode_records_a_plan_for_e_036_stages(database, mode: str) -> None:  # noqa: F811
    """Criterion 10: the plan carries the five stages, and the executor enters each one, in order."""
    observer = Observer()
    recorder = Recorder()
    async with scenario(database, recorder=recorder, observer=observer) as (engine, _conn, s, _replay):
        await _query(engine, caller("alice"), mode, **_request_for(mode, s))
        assert recorder.last.stages == STAGES
        assert tuple(observer.entered) == STAGES, f"stages entered out of order or missing: {observer.entered}"


async def test_the_plan_rows_are_the_requests_own_limit(database) -> None:  # noqa: F811
    """The planner does not decide the limit: the request's own value enters the plan unchanged."""
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, _s, _replay):
        await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE, "limit": 7}})
        assert recorder.last.rows == 7


# ──────────────────────────────────────────── criterion 5: temporal stays a filter ──


async def test_the_temporal_predicate_cannot_move_a_row_between_channels(database) -> None:  # noqa: F811
    """Criterion 5: a mode decides *whether* a row appears, never *where*.

    One artifact, four modes: wherever it appears, it is in the same channel. A planner that turned the mode into a
    score term would show up here as the claim moving from ``facts`` to ``hypotheses`` as the target instant moved.
    """
    async with scenario(database) as (engine, _conn, s, _replay):
        placements: dict[str, str | None] = {}
        for mode in sorted(MODES):
            result = await _query(engine, caller("alice"), mode, **_request_for(mode, s))
            placements[mode] = _channel_of_artifact(result, s.episode_a)
        present = {channel for channel in placements.values() if channel is not None}
        assert present == {"facts"}, f"the same artifact appeared in different channels across modes: {placements}"


async def test_a_row_the_mode_excludes_is_absent_rather_than_moved(database) -> None:  # noqa: F811
    """Exclusion is absence from every channel, not a demotion to another one.

    ``current_state`` excludes the claim (its event range has closed). The claim must then be in no channel at all —
    not in ``hypotheses``, which is where a "score" implementation would be tempted to put it.
    """
    async with scenario(database) as (engine, _conn, s, _replay):
        current = await _query(engine, caller("alice"), "current_state")
        assert _channel_of_artifact(current, s.claim) is None
        # and the same artifact *is* placed, in facts, when the mode's predicate admits it
        historical = await _query(engine, caller("alice"), "historical_as_of", target_time=T0.isoformat())
        assert _channel_of_artifact(historical, s.claim) == "facts"


async def test_the_plan_carries_the_mode_but_the_channel_does_not(database) -> None:  # noqa: F811
    """The structural reason criterion 5 holds: ``channel_of`` has no mode argument, so it cannot see one.

    Inspected rather than assumed: ``_channel_for`` is called with the artifact's own tuple only.
    """
    import inspect

    from custom_memory_engine.api.read_handlers import _channel_for

    signature = inspect.signature(_channel_for)
    assert "mode" not in signature.parameters
    # `stale` is a required argument (E-038-F5), so no call site can default it to fresh; the temporal mode is still
    # not among the inputs, which is what keeps the predicate a filter rather than a score term.
    assert set(signature.parameters) == {"kind", "lifecycle", "verification", "open_contradiction", "stale"}
    assert signature.parameters["stale"].default is inspect.Parameter.empty


# ──────────────────────────────────────────────────────────── criterion 6: audit isolation ──


async def test_audit_as_of_plans_to_the_audit_route_and_never_a_channel(database) -> None:  # noqa: F811
    """Criterion 6: ``audit_as_of`` yields ``audit[]``/``is_audit=true``, never an answer channel.

    Also asserts the plan said so: the route is ``audit``, which is what stops a future executor from routing it.
    """
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, s, _replay):
        result = await _query(engine, caller("alice"), "audit_as_of", **_request_for("audit_as_of", s))
        assert recorder.last.route == "audit"
        assert result.is_audit is True
        assert _ids(result) == set(), "audit material reached an answer channel"
        assert result.audit, "the audit returned nothing, so isolation is untested"


async def test_audit_as_of_stays_tier_c_only_through_the_plan_path(database) -> None:  # noqa: F811
    """``V2-D73``: a Tier B caller is refused before any plan is executed."""
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, s, _replay):
        with pytest.raises(EntryError) as refused:
            await _query(engine, caller("alice", "B"), "audit_as_of", **_request_for("audit_as_of", s))
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)


# ────────────────────────────────────────────────────────────── criterion 7: replay ──


async def test_a_recorded_plan_replays_to_the_identical_result(database) -> None:  # noqa: F811
    """Criterion 7: a recorded plan, round-tripped through JSON, replays to the identical result.

    The plan is rebuilt from its serialised record, so the replay runs the record and not an object that shares state
    with the original call.
    """
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, s, replay):
        original = await _query(engine, caller("alice"), "historical_window", **_request_for("historical_window", s))
        recorded = Plan.from_dict(json.loads(json.dumps(recorder.last.to_dict())))
        replayed = await replay.execute_plan(Call(ctx=in_scope("alice"), operation=QUERY, arguments={}), recorded)
        assert replayed == original, "the replay diverged from the recorded run"


async def test_a_replay_of_a_different_plan_differs_so_the_assertion_is_not_vacuous(database) -> None:  # noqa: F811
    """Control: replaying a *different* plan gives a different result, so equality above means something."""
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, s, replay):
        original = await _query(engine, caller("alice"), "historical_as_of", **_request_for("historical_as_of", s))
        # same scopes, a target before the scenario: the predicate excludes everything
        before = (T0 - timedelta(days=1)).isoformat()
        other = Plan.from_dict({**recorder.last.to_dict(), "target_time": before, "mode": "historical_as_of"})
        replayed = await replay.execute_plan(Call(ctx=in_scope("alice"), operation=QUERY, arguments={}), other)
        assert replayed != original
        assert _ids(replayed) == set()


async def test_a_recorded_plan_cannot_widen_the_callers_read_set(database) -> None:  # noqa: F811
    """``I7``: a plan is data, and data must not widen authorization. A foreign scope is refused."""
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, _s, replay):
        await _query(engine, caller("alice"), "current_state")
        foreign = Plan.from_dict(
            {
                **recorder.last.to_dict(),
                "scopes": [{"path": "/u/bob/", "mode": "subtree", "visibilities": ["project", "shared"]}],
            }
        )
        with pytest.raises(EntryError) as refused:
            await replay.execute_plan(Call(ctx=caller("alice"), operation=QUERY, arguments={}), foreign)
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)


async def test_a_recorded_plan_cannot_add_a_visibility_the_caller_lacks(database) -> None:  # noqa: F811
    """A scope the caller holds, naming a visibility they were **not** granted, is refused (``V2 §5`` rule 4).

    The caller below holds ``/u/alice/`` restricted to ``project``. A recorded plan that names the same path with
    ``private`` must be refused: the plan is data, and data does not grant access.
    """
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, _s, replay):
        await _query(engine, caller("alice"), "current_state")
        narrowed = CallerContext.from_credential(
            Credential("alice", "C", False),
            write_scope="/u/alice/",
            read_set=ReadSet((ReadEntry("/u/alice/", frozenset({"project"}), "subtree"),), ()),
        )
        widened = Plan.from_dict(
            {
                **recorder.last.to_dict(),
                "scopes": [{"path": "/u/alice/", "mode": "subtree", "visibilities": ["private", "project"]}],
            }
        )
        with pytest.raises(EntryError) as refused:
            await replay.execute_plan(
                Call(ctx=narrowed, operation=QUERY, arguments={}),
                widened,
            )
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)
        # control: the same plan with only the granted visibility executes, so the refusal is about `private`
        allowed = Plan.from_dict(
            {
                **recorder.last.to_dict(),
                "scopes": [{"path": "/u/alice/", "mode": "subtree", "visibilities": ["project"]}],
            }
        )
        assert isinstance(
            await replay.execute_plan(Call(ctx=narrowed, operation=QUERY, arguments={}), allowed), QueryResult
        )


async def test_an_unknown_visibility_in_a_recorded_plan_is_refused(database) -> None:  # noqa: F811
    """A plan naming a visibility that does not exist fails closed rather than reading with an unknown level."""
    recorder = Recorder()
    async with scenario(database, recorder=recorder) as (engine, _conn, _s, replay):
        await _query(engine, caller("alice"), "current_state")
        bogus = Plan.from_dict(
            {
                **recorder.last.to_dict(),
                "scopes": [{"path": "/u/alice/", "mode": "subtree", "visibilities": ["everything"]}],
            }
        )
        with pytest.raises(EntryError) as refused:
            await replay.execute_plan(Call(ctx=caller("alice"), operation=QUERY, arguments={}), bogus)
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)


# ───────────────────────────────────────────────────────── criterion 8: nothing changed ──


async def test_the_plan_path_preserves_the_documented_refusals(database) -> None:  # noqa: F811
    """Criterion 8, re-asserted here: ``query``'s own validation and codes are unchanged by planning."""
    async with scenario(database) as (engine, _conn, _s, _replay):
        cases: list[tuple[dict[str, object], str]] = [
            ({"mode": "change"}, "window_start_required"),  # V2-D81: wired, under the window form's codes
            ({"mode": "recently"}, "mode_invalid"),
            ({"reference_time": T0.isoformat()}, "reference_time_is_the_engine_clock"),
            ({"mode": "historical_as_of"}, "target_time_required"),
            ({"mode": "historical_window", "window_start": T0.isoformat()}, "window_end_required"),
            ({"mode": "audit_as_of", "target_time": T0.isoformat()}, "belief_time_required"),
            ({"limit": 0}, "limit_out_of_range"),
        ]
        for extra, expected in cases:
            with pytest.raises(EntryError) as refused:
                await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE, **extra}})
            assert refused.value.code in ("validation_error", "resource_limit"), (extra, refused.value.code)
            assert refused.value.detail == expected, (extra, refused.value.detail)


async def test_planning_does_not_relocate_the_procedure_ranking(database) -> None:  # noqa: F811
    """``V2-D75``: procedure ordering stays **after** routing, so a statistic cannot promote a procedure.

    Two procedures are written; one records successes. Both are ``asserted``, so both belong in ``procedures`` and the
    recorded outcome is allowed to order them *within* that channel. The assertion that matters is the negative: the
    ranked procedure is still in ``procedures`` and in no other channel, which is what "after routing" means.
    """
    async with scenario(database) as (engine, _conn, _s, _replay):
        # a private row is readable only through a selector equal to its own scope (V2 SS5 rule 4), so the caller
        # that records outcomes and reads them back selects SCOPE itself
        alice = in_scope("alice")
        procedures = []
        for key in ("p031-rank-a", "p031-rank-b"):
            receipt = await _receipt(
                engine,
                alice,
                "assert",
                {
                    "scope": SCOPE,
                    "kind": "procedure",
                    "content": f"procedure {key}",
                    "evidence": [
                        {
                            "type": "turn",
                            "ref": "turn-e031p-alice",
                            "span": TURN_TEXT,
                            "content_hash": canonical_hash(TURN_TEXT),
                        }
                    ],
                    "idempotency_key": key,
                },
            )
            procedures.append(receipt.id)
        assert isinstance(engine.resolver, DbTargets)
        register_outcome_handlers(engine, engine.resolver.runtime)
        for _ in range(3):
            await engine.call(alice, "record_outcome", {"procedure_id": procedures[1], "outcome": "success"})
        result = await _query(engine, alice, "current_state")
        assert {a.artifact_id for a in result.procedures} == set(procedures), "a procedure left its channel"
        for other in ("facts", "hypotheses", "conflicts"):
            assert not {a.artifact_id for a in result.channel(other)} & set(procedures), (
                f"ranking promoted a procedure into {other}"
            )
        # the recorded outcome decides the order *inside* the channel (V2-D75), and the plan did not change it
        assert [a.artifact_id for a in result.procedures] == [procedures[1], procedures[0]]
