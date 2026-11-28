"""``E-033`` (``V2-D81``): ``mode='change'`` through ``Engine.call``.

``API_V2.md`` §4.2 has listed ``change`` since ``E-031``; `E-033-F1` recorded that the mode could not land because no
decision record defined *which two views* it compares, *what key* it matches them on, or *what outcomes* it reports.
``V2-D81`` decides all three, and this module proves the decision against a live database through the public entry
point only: no handler is called directly, no SQL is issued to read.

The three outcomes are exercised on real version lifecycles:

* ``added`` — an artifact valid at ``window_end`` but not at ``window_start``;
* ``removed`` — an artifact valid at ``window_start`` but whose validity closed before ``window_end``;
* ``changed`` — one single-valued predicate superseded between the two instants (``R1``/``R2``: the old version is
  closed and re-issued, the new one inserted, all under one ``(subject_key, predicate_key)``).

**Why the views are seeded with event ranges, not belief times.** ``I4`` makes the belief instant the database clock,
so the two views are the *same* belief (ρ) asked about two validity instants — exactly what ``historical_as_of``
does. A test that moved recorded time instead would be testing ``audit_as_of`` (``V2-D73``).

Controls: the same scenario asked in the two single-view modes returns no ``changes`` and the rows the diff names, and
a window with no movement answers an empty diff rather than every row.
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
from custom_memory_engine.engine.change import Change
from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    EvidenceInput,
    commit_initial_version,
    supersede_single,
)
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import (
    caller,
    database,  # noqa: F401  (fixture)
    world,
)
from tests.integration.test_read_path_db import CODING_PROFILE

pytestmark = pytest.mark.integration

SCOPE = "/u/alice/p/chrona/"
# Four validity instants: an artifact valid at each one is what each view sees.
T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)
TURN_TEXT = "The user prefers Python over JavaScript"


def in_scope(user: str) -> CallerContext:
    """A caller that selected its root and the session scope: a private row is read only at its own scope."""
    root = f"/u/{user}/"
    read_set = resolve_read_set(user, [Selector.of(root), Selector.of(root + "p/chrona/")], [])
    return CallerContext.from_credential(Credential(user, "C", False), write_scope=root, read_set=read_set)


def _turn(user: str = "alice") -> dict[str, object]:
    return {
        "type": "turn",
        "ref": f"turn-e033-change-{user}",
        "span": TURN_TEXT,
        "content_hash": canonical_hash(TURN_TEXT),
    }


async def _receipt(engine: Engine, ctx: CallerContext, operation: str, arguments: dict[str, object]) -> Receipt:
    result = await engine.call(ctx, operation, arguments)
    assert isinstance(result, Receipt), result
    return result


async def _change(engine: Engine, ctx: CallerContext, start: datetime, end: datetime, **extra: object) -> QueryResult:
    request: dict[str, object] = {
        "scope": SCOPE,
        "mode": "change",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        **extra,
    }
    result = await engine.call(ctx, "query", {"request": request})
    assert isinstance(result, QueryResult), result
    return result


def _ids(result: QueryResult) -> set[str]:
    return {a.artifact_id for a in (*result.facts, *result.hypotheses, *result.procedures, *result.conflicts)}


def _by_outcome(changes: tuple[Change, ...]) -> dict[str, list[Change]]:
    grouped: dict[str, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.outcome, []).append(change)
    return grouped


class Scenario:
    """One session of one user with one artifact of each change outcome.

    * ``closing`` — a claim valid ``[T0, T1)``: in the *previous* view, gone by ``window_end``.
    * ``opening`` — a claim valid ``[T2, T3)``: absent from the *previous* view, present at ``window_end``.
    * ``residence`` — one single-valued predicate superseded at ``T2``: ``Cairo`` then ``Berlin``.
    * ``steady`` — an episode valid from ``T0`` with no upper bound: in both views, so never reported.
    """

    closing: str
    opening: str
    residence_before: str
    residence_after: str
    steady: str


@asynccontextmanager
async def scenario(database) -> AsyncIterator[tuple[Engine, AsyncConnection, Scenario]]:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_read_handlers(engine, runtime)
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, %s, %s, 2, 'user', %s, %s)",
            ("turn-e033-change-alice", "s-alice", "alice", TURN_TEXT, T0),
        )
        s = Scenario()
        alice = caller("alice")

        def claim(content: str, start: datetime, end: datetime, key: str) -> dict[str, object]:
            return {
                "scope": SCOPE,
                "kind": "claim",
                "content": content,
                "evidence": [{**_turn(), "span": TURN_TEXT}],
                "event_range": [start.isoformat(), end.isoformat()],
                "idempotency_key": key,
            }

        s.closing = (await _receipt(engine, alice, "assert", claim("uses tabs", T0, T1, "e033-change-closing"))).id
        s.opening = (await _receipt(engine, alice, "assert", claim("uses spaces", T2, T3, "e033-change-opening"))).id
        s.steady = (
            await _receipt(
                engine,
                alice,
                "ingest_episode",
                {
                    "scope": SCOPE,
                    "source": {
                        "type": "tool",
                        "ref": "tool:e033-change-steady",
                        "span": "ran pytest -x",
                        "content_hash": canonical_hash("ran pytest -x"),
                    },
                    "content": "ran pytest -x",
                    "event_time": T0.isoformat(),
                    "idempotency_key": "e033-change-steady",
                },
            )
        ).id

        # One single-valued predicate across the window: `Cairo` [T0, T2) then `Berlin` [T2, inf).
        # The predicate vocabulary is appendable by design (migration 10), so the scenario registers its own.
        await conn.execute(
            "INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) "
            "VALUES ('residence', 'single', 'semantic') ON CONFLICT (predicate_key) DO NOTHING"
        )
        # No public operation supersedes: single-valued supersession (R1/R2) is the engine's extraction path, which
        # runs as the owner. One clock allocation per transaction (I4), so the two writes are two transactions.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            first = await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="e033-change-residence-1",
                    memory_key="alice:residence",
                    user_id="alice",
                    scope_path=SCOPE,
                    subject_key="alice",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=T0,
                    evidence=(EvidenceInput(turn_id="turn-e033-change-alice", evidence_span=TURN_TEXT),),
                ),
                schema_name=runtime.schema_name,
            )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            superseded = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="e033-change-residence-2",
                    memory_key="alice:residence",
                    user_id="alice",
                    scope_path=SCOPE,
                    subject_key="alice",
                    predicate_key="residence",
                    single_valued=True,
                    content="Berlin",
                    valid_from=T2,
                    evidence=(EvidenceInput(turn_id="turn-e033-change-alice", evidence_span=TURN_TEXT),),
                ),
                reissued_version_id="e033-change-residence-1-reissued",
                schema_name=runtime.schema_name,
            )
        # R2 re-issues the old belief as `[T0, T2)` under a new id: that is the version the *previous* view sees
        assert superseded.reissued_version_id is not None, superseded
        assert first[0].version_id in superseded.closed_version_ids, superseded
        s.residence_before, s.residence_after = superseded.reissued_version_id, superseded.inserted_version_id
        yield engine, conn, s


# ─────────────────────────────────────────────────────────── the three outcomes ──


async def test_change_reports_each_outcome_once_and_names_the_versions(database) -> None:  # noqa: F811
    """One key that opened, one that closed, one that changed — each reported once, with its own version ids."""
    async with scenario(database) as (engine, _conn, s):
        # The window is [T0, T2): the previous view is the belief in effect at T0, the current one at T2.
        result = await _change(engine, caller("alice"), T0, T2)
        grouped = _by_outcome(result.changes)

        assert [change.current for change in grouped.get("added", [])] == [(s.opening,)], (
            f"the claim valid only at window_end must be the one addition: {result.changes}"
        )
        # `closing` is valid `[T0, T1)` and every range is half-open, so it is in the *previous* view at T0 and in
        # neither view at T2: exactly one removal.
        assert [change.previous for change in grouped.get("removed", [])] == [(s.closing,)], (
            f"the claim whose validity ended at window_start must be the one removal: {result.changes}"
        )
        changed = grouped.get("changed", [])
        assert [change.current for change in changed] == [(s.residence_after,)], (
            f"the superseded single-valued predicate must be the one change: {result.changes}"
        )
        assert changed[0].previous == (s.residence_before,), f"a change names the version it replaced: {changed[0]}"

        assert s.steady not in {
            version for change in result.changes for version in (*change.previous, *change.current)
        }, "an artifact unchanged across both views must not be reported"


async def test_a_change_is_absent_when_nothing_moved(database) -> None:  # noqa: F811
    """Control: a window in which no validity boundary is crossed answers an empty diff, not every row."""
    async with scenario(database) as (engine, _conn, s):  # noqa: F841
        result = await _change(engine, caller("alice"), T0, T0 + timedelta(minutes=30))
        assert result.changes == (), f"nothing moved in this window: {result.changes}"


async def test_the_two_views_are_the_single_view_queries(database) -> None:  # noqa: F811
    """Control: the diff's rows are exactly what the two ``historical_as_of`` queries return."""
    async with scenario(database) as (engine, _conn, s):
        before = await engine.call(
            caller("alice"),
            "query",
            {"request": {"scope": SCOPE, "mode": "historical_as_of", "target_time": T0.isoformat()}},
        )
        after = await engine.call(
            caller("alice"),
            "query",
            {"request": {"scope": SCOPE, "mode": "historical_as_of", "target_time": T2.isoformat()}},
        )
        assert isinstance(before, QueryResult) and isinstance(after, QueryResult)
        assert before.changes == () and after.changes == (), "only `change` reports a diff"

        change = await _change(engine, caller("alice"), T0, T2)
        # every id the diff names is a row one of the two single-view queries returned
        named = {version for item in change.changes for version in (*item.previous, *item.current)}
        assert named <= _ids(before) | _ids(after), named - (_ids(before) | _ids(after))
        assert s.opening in _ids(after) and s.opening not in _ids(before)
        assert s.closing in _ids(before) and s.closing not in _ids(after)


# ────────────────────────────────────────────────────────────────── the channels ──


async def test_the_channels_are_built_from_the_union_of_the_two_views(database) -> None:  # noqa: F811
    """A changed artifact is rendered as any query renders it, and both the old and the new side are reachable."""
    async with scenario(database) as (engine, _conn, s):
        result = await _change(engine, caller("alice"), T0, T2)
        found = _ids(result)
        assert {s.opening, s.closing, s.steady} <= found, f"each view's rows must reach a channel: {sorted(found)}"
        assert result.is_audit is False, "change is answer context, never an audit"
        assert result.audit == (), "no row of a change query is an audit row"


async def test_change_never_shows_a_retired_version(database) -> None:  # noqa: F811
    """A retired artifact is in neither view, so ``change`` is not a back door to an archived belief (V2-D73)."""
    async with scenario(database) as (engine, _conn, s):
        # a private row is readable only at its own scope, so the withdrawal names that scope in its read set
        await engine.call(in_scope("alice"), "retract_own", {"artifact_id": s.opening, "reason": "changed_mind"})
        result = await _change(engine, caller("alice"), T1, T2)
        assert s.opening not in _ids(result), "a retired artifact is not answer context"
        assert s.opening not in {v for item in result.changes for v in (*item.previous, *item.current)}


# ───────────────────────────────────────────────────────────────────── the guards ──


async def test_change_requires_both_window_bounds(database) -> None:  # noqa: F811
    """The two instants carry the window form's names and its refusals, so a caller learns one vocabulary."""
    async with scenario(database) as (engine, _conn, _s):
        for request, expected in (
            ({"mode": "change"}, "window_start_required"),
            ({"mode": "change", "window_start": T0.isoformat()}, "window_end_required"),
        ):
            with pytest.raises(EntryError) as refused:
                await engine.call(caller("alice"), "query", {"request": {"scope": SCOPE, **request}})
            assert (refused.value.code, refused.value.detail) == ("validation_error", expected)


async def test_change_refuses_an_empty_or_future_span(database) -> None:  # noqa: F811
    """An empty span asks nothing; a future one asks about a belief the engine does not hold."""
    async with scenario(database) as (engine, _conn, _s):
        for start, end, expected in (
            (T1, T1, "historical_window_must_be_nonempty"),
            (T1, T0, "historical_window_must_be_nonempty"),
            (T0, T0 + timedelta(days=3650), "historical_window_after_reference"),
        ):
            with pytest.raises(EntryError) as refused:
                await _change(engine, caller("alice"), start, end)
            assert (refused.value.code, refused.value.detail) == ("validation_error", expected)


async def test_a_truncated_view_is_refused_not_diffed(database) -> None:  # noqa: F811
    """``V2-D81``: a diff of a truncated view would call a row 'removed' when it was merely not fetched."""
    async with scenario(database) as (engine, _conn, _s):
        with pytest.raises(EntryError) as refused:
            await _change(engine, caller("alice"), T1, T2, limit=1)
        assert (refused.value.code, refused.value.detail) == ("resource_limit", "change_view_exceeds_limit")


async def test_change_respects_the_read_set(database) -> None:  # noqa: F811
    """A scope outside the caller's read set is refused; a narrower scope reports only that scope's movement."""
    async with scenario(database) as (engine, _conn, s):
        with pytest.raises(EntryError) as refused:
            await engine.call(
                caller("bob"),
                "query",
                {
                    "request": {
                        "scope": SCOPE,
                        "mode": "change",
                        "window_start": T1.isoformat(),
                        "window_end": T2.isoformat(),
                    },
                },
            )
        assert refused.value.code == "forbidden"

        # alice may narrow to her own session scope, and still sees the same movement
        narrowed = await _change(engine, caller("alice"), T1, T2, scope=SCOPE)
        assert [c.current for c in _by_outcome(narrowed.changes).get("added", [])] == [(s.opening,)]
