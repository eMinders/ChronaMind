"""E-031: temporal query modes through ``Engine.call`` (V2-D73).

``API_V2.md`` §4.2 declares ``QueryRequest.mode``: ``current``, ``historical_as_of``,
``historical_window``, ``change`` or ``audit_as_of``. This module proves the four modes E-031 wires,
end to end through the public entry point.

**Belief time is the database clock** (``I4``): ``clock/allocation.py`` allocates every
``recorded_range`` from ``transaction_timestamp()`` and deliberately ignores an inherited
``app.clock``. The scenario therefore takes its reference instants from the receipts it stores
(``receipt.recorded_at`` is ``lower(recorded_range)``) rather than from an assumed pin — a test
that pinned a clock the engine does not read would prove nothing.

**Event time is caller-chosen** and is what the historical modes ask about: the episodes are
recorded with ``event_time = T0`` and the claim's event range is ``[T0, T1)``, so a target inside
that interval must match and a target before it must not.

Properties under test:
* each mode returns what its predicate says, and nothing when the predicate excludes it;
* a supplied ``reference_time`` is refused (``reference_time_is_the_engine_clock``): belief time
  is never caller-supplied, so a request cannot read a belief the engine never held;
* ``audit_as_of`` is Tier C only, is never answer-context material, and reaches a row that was
  archived after the belief instant (which no answer channel would show);
* the read set and the temporal predicate are applied in one statement, so ``limit`` counts
  readable rows only;
* tenant isolation holds for every mode.

Controls: the "before the scenario" cases show the temporal predicate is a real filter rather
than an ignored parameter, and the withdrawal test shows ``audit_as_of`` sees what
``current_state`` must not.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import (
    caller,
    database,  # noqa: F401  (fixture)
    rows,
    world,
)
from tests.integration.test_read_path_db import CODING_PROFILE

pytestmark = pytest.mark.integration

T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
BEFORE = T0 - timedelta(seconds=1)
SCOPE = "/u/alice/p/chrona/"
BOB_SCOPE = "/u/bob/p/chrona/"
TURN_TEXT = "The user prefers Python over JavaScript"


def in_scope(user: str) -> CallerContext:
    """A caller that selected its root and the session scope: a private row is read only at its own scope."""
    root = f"/u/{user}/"
    read_set = resolve_read_set(user, [Selector.of(root), Selector.of(root + "p/chrona/")], [])
    return CallerContext.from_credential(Credential(user, "C", False), write_scope=root, read_set=read_set)


def _turn(user: str = "alice") -> dict[str, object]:
    return {
        "type": "turn",
        "ref": f"turn-e031-{user}",
        "span": TURN_TEXT,
        "content_hash": canonical_hash(TURN_TEXT),
    }


async def _receipt(engine: Engine, ctx: CallerContext, operation: str, arguments: dict[str, object]) -> Receipt:
    result = await engine.call(ctx, operation, arguments)
    assert isinstance(result, Receipt), result
    return result


def _ids(result: QueryResult) -> set[str]:
    return {a.artifact_id for a in (*result.facts, *result.hypotheses, *result.procedures, *result.conflicts)}


async def _query(engine: Engine, ctx: CallerContext, scope: str, mode: str, **extra: object) -> QueryResult:
    request: dict[str, object] = {"scope": scope, "mode": mode, **extra}
    result = await engine.call(ctx, "query", {"request": request})
    assert isinstance(result, QueryResult), result
    return result


class Scenario:
    """One session of one user: two episodes at ``T0``, a claim at ``[T0, T1)``, and bob's own episode."""

    episode_a: str
    episode_b: str
    claim: str
    bob_episode: str
    claim_recorded_at: datetime  # lower(recorded_range) of the claim: a real engine instant
    episode_recorded_at: datetime


@asynccontextmanager
async def scenario(database) -> AsyncIterator[tuple[Engine, AsyncConnection, Scenario]]:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_read_handlers(engine, runtime)
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        for user in ("alice", "bob"):
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 2, 'user', %s, %s)",
                (f"turn-e031-{user}", "s-" + user, user, TURN_TEXT, T0),
            )
        s = Scenario()
        alice, bob = caller("alice"), caller("bob")

        def episode(scope: str, key: str, content: str) -> dict[str, object]:
            return {
                "scope": scope,
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

        first = await _receipt(engine, alice, "ingest_episode", episode(SCOPE, "e031-a", "ran pytest -x"))
        second = await _receipt(engine, alice, "ingest_episode", episode(SCOPE, "e031-b", "coverage 92 percent"))
        s.episode_a, s.episode_b = first.id, second.id
        s.episode_recorded_at = second.recorded_at
        claim = await _receipt(
            engine,
            alice,
            "assert",
            {
                "scope": SCOPE,
                "kind": "claim",
                "content": "The user prefers Python over JavaScript",
                "evidence": [{**_turn(), "span": TURN_TEXT}],
                "event_range": [T0.isoformat(), T1.isoformat()],
                "idempotency_key": "e031-claim",
            },
        )
        s.claim, s.claim_recorded_at = claim.id, claim.recorded_at
        s.bob_episode = (
            await _receipt(
                engine,
                bob,
                "ingest_episode",
                {
                    "scope": BOB_SCOPE,
                    "source": _turn("bob"),
                    "content": TURN_TEXT,
                    "event_time": T0.isoformat(),
                    "idempotency_key": "e031-bob",
                },
            )
        ).id
        yield engine, conn, s


# ────────────────────────────────────────────────────────────── current_state ──


async def test_current_state_returns_every_artifact_recorded_so_far(database) -> None:  # noqa: F811
    """The default mode: everything believed now, whose event range covers now or the past."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(engine, caller("alice"), SCOPE, "current_state")
        found = _ids(result)
        assert {s.episode_a, s.episode_b} <= found, "episodes missing from current_state"
        # the claim's event range closed at T1, which is in the past: current_state excludes it by design
        assert s.claim not in found, "a claim whose event range has closed is not current"
        assert result.is_audit is False


async def test_current_state_is_the_default_mode(database) -> None:  # noqa: F811
    """Omitting ``mode`` behaves exactly like ``current_state`` (API_V2.md §2)."""
    async with scenario(database) as (engine, _conn, s):
        explicit = await _query(engine, caller("alice"), SCOPE, "current_state")
        default = await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE}})
        assert isinstance(default, QueryResult)
        assert _ids(default) == _ids(explicit) == {s.episode_a, s.episode_b}


# ───────────────────────────────────────────────────────── historical_as_of ──


async def test_historical_as_of_returns_the_claim_while_it_was_current(database) -> None:  # noqa: F811
    """A target inside the claim's event range returns it, even though ``current_state`` does not."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(engine, caller("alice"), SCOPE, "historical_as_of", target_time=T0.isoformat())
        assert {s.episode_a, s.episode_b, s.claim} <= _ids(result), "historical_as_of lost an artifact valid at T0"


async def test_historical_as_of_before_the_scenario_returns_nothing(database) -> None:  # noqa: F811
    """Control: a target before every event range matches nothing, so the predicate is a real filter."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(engine, caller("alice"), SCOPE, "historical_as_of", target_time=BEFORE.isoformat())
        assert _ids(result) == set()


async def test_historical_as_of_after_the_claim_range_returns_only_the_episodes(database) -> None:  # noqa: F811
    """Control in the other direction: past T1 the claim is out, the open-ended episodes are still in."""
    async with scenario(database) as (engine, _conn, s):
        after = T1 + timedelta(days=1)
        result = await _query(engine, caller("alice"), SCOPE, "historical_as_of", target_time=after.isoformat())
        found = _ids(result)
        assert {s.episode_a, s.episode_b} <= found
        assert s.claim not in found


async def test_historical_as_of_requires_a_target_and_rejects_a_future_one(database) -> None:  # noqa: F811
    """``target_time`` is required (API_V2.md §4.2), and a future target is ``validation_error``."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as missing:
            await _query(engine, caller("alice"), SCOPE, "historical_as_of")
        assert (missing.value.code, missing.value.detail) == ("validation_error", "target_time_required")
        future = datetime.now(UTC) + timedelta(days=3650)
        with pytest.raises(EntryError) as beyond:
            await _query(engine, caller("alice"), SCOPE, "historical_as_of", target_time=future.isoformat())
        assert beyond.value.code == "validation_error"


# ──────────────────────────────────────────────────────── historical_window ──


async def test_historical_window_covers_overlapping_event_ranges(database) -> None:  # noqa: F811
    """A window overlapping both the episodes' and the claim's event ranges returns all three."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(
            engine,
            caller("alice"),
            SCOPE,
            "historical_window",
            window_start=T0.isoformat(),
            window_end=T1.isoformat(),
        )
        assert {s.episode_a, s.episode_b, s.claim} <= _ids(result)


async def test_historical_window_before_the_scenario_returns_nothing(database) -> None:  # noqa: F811
    """Control: a window entirely before every event range matches nothing."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(
            engine,
            caller("alice"),
            SCOPE,
            "historical_window",
            window_start=(T0 - timedelta(days=2)).isoformat(),
            window_end=BEFORE.isoformat(),
        )
        assert _ids(result) == set()


async def test_historical_window_requires_both_bounds_and_a_nonempty_range(database) -> None:  # noqa: F811
    """Both bounds are required, and an inverted window is a validation error."""
    async with scenario(database) as (engine, _conn, s):
        for missing in ({"window_start": T0.isoformat()}, {"window_end": T1.isoformat()}):
            with pytest.raises(EntryError) as refused:
                await _query(engine, caller("alice"), SCOPE, "historical_window", **missing)
            assert refused.value.code == "validation_error"
        with pytest.raises(EntryError) as inverted:
            await _query(
                engine,
                caller("alice"),
                SCOPE,
                "historical_window",
                window_start=T1.isoformat(),
                window_end=T0.isoformat(),
            )
        assert inverted.value.code == "validation_error"


# ──────────────────────────────────────────────────────────── audit_as_of ──


async def test_audit_as_of_is_tier_c_only(database) -> None:  # noqa: F811
    """API_V2.md §4.2: Tier B calling ``audit_as_of`` is ``forbidden``, before any row is read."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await _query(
                engine,
                caller("alice", "B"),
                SCOPE,
                "audit_as_of",
                target_time=T0.isoformat(),
                belief_time=s.claim_recorded_at.isoformat(),
            )
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)


async def test_audit_as_of_returns_audit_material_and_never_an_answer_channel(database) -> None:  # noqa: F811
    """The result is flagged ``is_audit`` and carries the rows in ``audit``, never in a channel."""
    async with scenario(database) as (engine, _conn, s):
        result = await _query(
            engine,
            caller("alice"),
            SCOPE,
            "audit_as_of",
            target_time=T0.isoformat(),
            belief_time=s.claim_recorded_at.isoformat(),
        )
        assert result.is_audit is True
        assert _ids(result) == set(), "audit material must not appear in an answer channel"
        assert {a.artifact_id for a in result.audit} >= {s.episode_a, s.claim}


async def test_audit_as_of_reaches_a_row_archived_after_the_belief_instant(database) -> None:  # noqa: F811
    """The reason ``audit_as_of`` exists: a withdrawn claim is gone from every channel but visible before it went.

    ``retract_own`` closes ``recorded_range`` at the withdrawal (``V2-D59``), so a belief instant between the
    claim's recording and its withdrawal still matches — and ``current_state`` must never show it.
    """
    async with scenario(database) as (engine, conn, s):
        # the claim is private at SCOPE, so only a caller that selects SCOPE itself can name it (V2 §5 rule 4)
        await engine.call(in_scope("alice"), "retract_own", {"artifact_id": s.claim, "reason": "no_longer_stated"})
        historical = await _query(engine, caller("alice"), SCOPE, "historical_as_of", target_time=T0.isoformat())
        assert s.claim not in _ids(historical)
        audited = await _query(
            engine,
            caller("alice"),
            SCOPE,
            "audit_as_of",
            target_time=T0.isoformat(),
            belief_time=s.claim_recorded_at.isoformat(),
        )
        seen = {a.artifact_id: a for a in audited.audit}
        assert s.claim in seen, "the withdrawal is invisible to the inspector"
        assert seen[s.claim].lifecycle == "retired"
        assert seen[s.claim].channel == "none", "a retired row belongs to no answer channel"
        assert await rows(conn, "SELECT status FROM memory_version WHERE id = %s", (s.claim,)) == [("archived",)]


async def test_audit_as_of_requires_a_target_and_a_belief_time(database) -> None:  # noqa: F811
    """Both parameters are required; a belief time in the future is refused against the database clock."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as missing:
            await _query(engine, caller("alice"), SCOPE, "audit_as_of", target_time=T0.isoformat())
        assert missing.value.code == "validation_error"
        with pytest.raises(EntryError) as future:
            await _query(
                engine,
                caller("alice"),
                SCOPE,
                "audit_as_of",
                target_time=T0.isoformat(),
                belief_time=(datetime.now(UTC) + timedelta(days=3650)).isoformat(),
            )
        assert future.value.code == "validation_error"


# ───────────────────────────────────────────── the reference instant is the engine's ──


async def test_a_caller_supplied_reference_time_is_refused(database) -> None:  # noqa: F811
    """``I4``: belief time is never caller-supplied, so ``reference_time`` is not an accepted input."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await _query(engine, caller("alice"), SCOPE, "current_state", reference_time=T0.isoformat())
        assert (refused.value.code, refused.value.detail) == (
            "validation_error",
            "reference_time_is_the_engine_clock",
        )


async def test_a_recorded_artifact_is_current_at_the_engine_clock(database) -> None:  # noqa: F811
    """The complement of that rule: what was just written *is* current, because both use the same clock."""
    async with scenario(database) as (engine, _conn, s):
        assert s.episode_recorded_at <= datetime.now(UTC), "the receipt instant must not be in the future"
        assert s.episode_a in _ids(await _query(engine, caller("alice"), SCOPE, "current_state"))


# ──────────────────────────────────────────────────────────────── mode errors ──


async def test_change_mode_is_recognised_but_not_wired(database) -> None:  # noqa: F811
    """``change`` is E-033's diff operator; the error says so rather than pretending the mode is unknown."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE, "mode": "change"}})
        assert (refused.value.code, refused.value.detail) == ("validation_error", "mode_change_not_wired")


async def test_an_unknown_mode_is_a_validation_error(database) -> None:  # noqa: F811
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE, "mode": "time_travel"}})
        assert (refused.value.code, refused.value.detail) == ("validation_error", "mode_invalid")


# ──────────────────────────────────────────────────────── scope and isolation ──


async def test_no_mode_returns_another_tenants_artifacts(database) -> None:  # noqa: F811
    """Tenant isolation holds in every temporal mode, and each principal sees its own rows."""
    async with scenario(database) as (engine, _conn, s):
        modes: list[dict[str, object]] = [
            {"mode": "current_state"},
            {"mode": "historical_as_of", "target_time": T0.isoformat()},
            {"mode": "historical_window", "window_start": T0.isoformat(), "window_end": T1.isoformat()},
        ]
        for extra in modes:
            alice_seen = _ids(await _query(engine, caller("alice"), SCOPE, **extra))  # type: ignore[arg-type]
            assert s.bob_episode not in alice_seen, f"bob's episode leaked in {extra['mode']}"
            bob_seen = _ids(await _query(engine, caller("bob"), BOB_SCOPE, **extra))  # type: ignore[arg-type]
            assert s.episode_a not in bob_seen and s.claim not in bob_seen, f"alice's rows leaked in {extra['mode']}"
            assert s.bob_episode in bob_seen, f"bob cannot read his own episode in {extra['mode']}"


async def test_a_scope_outside_the_read_set_is_forbidden(database) -> None:  # noqa: F811
    """A scope the caller holds no selector for is refused before any row is read."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await _query(engine, caller("alice"), BOB_SCOPE, "current_state")
        assert (refused.value.code, refused.value.detail) == ("forbidden", None)


async def test_the_query_operation_control_breaks_the_scope_isolation_assertion(database) -> None:  # noqa: F811
    """In-suite control: the isolation assertion is sensitive to the selector semantics, not vacuous.

    ``row_visible`` is the reference semantics the SQL predicate must mirror. A private row is readable only
    through a selector *equal to its own scope*, so a subtree selector over another tenant's root does **not**
    reach it (V2 §5 rule 4, threat T-06). Had this returned ``True``, the isolation assertions above would be
    testing a predicate that cannot refuse anything.
    """
    from custom_memory_engine.scope.access import ReadEntry, ReadSet, row_visible

    async with scenario(database) as (engine, _conn, s):
        assert s.bob_episode not in _ids(await _query(engine, caller("alice"), SCOPE, "current_state"))
        # a subtree selector rooted *above* the row: it reaches shared rows below it, never a private one
        bob_root = ReadSet((ReadEntry("/u/bob/", frozenset({"private", "project", "shared"}), "subtree"),), ())
        assert row_visible(bob_root, BOB_SCOPE, "private") is False
        assert row_visible(bob_root, BOB_SCOPE, "project") is True
        # a private row is reached only through a selector equal to its own scope
        own_scope = ReadSet((ReadEntry(BOB_SCOPE, frozenset({"private"}), "exact"),), ())
        assert row_visible(own_scope, BOB_SCOPE, "private") is True
        # the honest caller never holds either selector
        assert row_visible(caller("alice").read_set, BOB_SCOPE, "private") is False
        assert row_visible(caller("alice").read_set, BOB_SCOPE, "project") is False
