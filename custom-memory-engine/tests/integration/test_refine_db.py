"""E-034: ``refine`` through ``Engine.call`` on the real migrated schema (``API_V2.md`` §4.2, ``V2-D83``).

Every call runs as a ``NOSUPERUSER NOBYPASSRLS`` role with a credential-built caller, and the rows are written through
the public entry point (except where a test seeds a *verified* abstraction, because ``verify`` is not wired yet). The
five properties under test are the ones that make ``refine`` more than "query again with a filter":

* it drills *within* the read set of the result it names, so a widened credential changes nothing and a narrowed one
  is refused rather than silently redirected;
* an unknown, foreign or expired handle is one answer (``forbidden``, ``T-11``);
* the instruction vocabulary is rule-based and total: an unrecognised instruction is a static error, never a no-op;
* narrowing never moves an artifact between channels and never strips a hypothesis of its label, support or bound;
* the chain is bounded, and a row deleted since the query is not resurrected by drilling into the result that named it.
"""

from datetime import datetime, timedelta

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, Credential, Engine
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.maintain_handlers import register_maintain_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.refine import MAX_REFINE_DEPTH, ResultStore
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.scope.access import ReadSet, Selector, resolve, resolve_read_set
from tests.integration.test_api_wiring_structure import (
    T0,
    caller,
    database,  # noqa: F401  (fixture)
    forbidden,
    rows,
    world,
)
from tests.integration.test_change_mode_db import SCOPE, T2, scenario
from tests.integration.test_change_mode_db import T0 as CHANGE_T0
from tests.integration.test_purge_working_db import working_coding_profile
from tests.integration.test_read_path_db import CODING_PROFILE, episode

pytestmark = pytest.mark.integration
REPO_A = "/u/alice/p/a/"
REPO_B = "/u/alice/p/b/"
WINDOW_A = "[2026-01-01T00:00:00+00:00,2026-02-01T00:00:00+00:00)"
WINDOW_B = "[2026-02-01T00:00:00+00:00,2026-03-01T00:00:00+00:00)"

STATEMENT = "A missing import may explain repeated name errors in focused CI tests."


class Clock:
    """A movable clock, injected into the result store so expiry is decided instead of waited for."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def reader(user: str, *paths: str, tier: Tier = "C", admin: bool = False) -> CallerContext:
    """A caller that selected ``paths`` (its own root when none is named), with live grants resolved for it."""
    selected = paths or (f"/u/{user}/",)
    read_set = resolve_read_set(user, [Selector.of(path) for path in selected], [])
    return CallerContext.from_credential(Credential(user, tier, admin), write_scope=f"/u/{user}/", read_set=read_set)


def refine_args(result: QueryResult, instruction: str) -> dict[str, object]:
    assert result.result_id is not None, "every answer path issues a drill-down handle"
    return {"result_id": result.result_id, "instruction": instruction}


async def answer(engine: Engine, ctx: CallerContext, request: dict[str, object] | None = None) -> QueryResult:
    result = await engine.call(ctx, "query", {"request": request or {}})
    assert isinstance(result, QueryResult)
    return result


async def refined(engine: Engine, ctx: CallerContext, prior: QueryResult, instruction: str) -> QueryResult:
    result = await engine.call(ctx, "refine", refine_args(prior, instruction))
    assert isinstance(result, QueryResult)
    return result


def ids(result: QueryResult, channel: str) -> set[str]:
    return {item.artifact_id for item in result.channel(channel)}


async def seed(engine: Engine, scope: str, key: str, content: str) -> str:
    receipt = await engine.call(caller("alice"), "ingest_episode", episode(scope, key, content))
    assert isinstance(receipt, Receipt)
    return receipt.id


# --- narrowing, and the errors it reports --------------------------------------------------------------


async def test_refine_narrows_to_the_named_artifact_channel_text_or_scope(database) -> None:  # noqa: F811
    async with world(database) as (_conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        assert "refine" not in engine.unwired_operations()
        first = await seed(engine, REPO_A, "a1", "observed event one")
        second = await seed(engine, REPO_A, "a2", "observed event two")
        third = await seed(engine, REPO_B, "b1", "observed event three")
        prior = await answer(engine, reader("alice", REPO_A, REPO_B))
        assert ids(prior, "facts") == {first, second, third}

        by_artifact = await refined(engine, reader("alice", REPO_A, REPO_B), prior, f"artifact {second}")
        assert ids(by_artifact, "facts") == {second}
        assert by_artifact.result_id != prior.result_id, "each refine returns its own handle"

        by_text = await refined(engine, reader("alice", REPO_A, REPO_B), prior, "mention TWO")
        assert ids(by_text, "facts") == {second}, "the text match is case-insensitive"

        by_scope = await refined(engine, reader("alice", REPO_A, REPO_B), prior, f"scope {REPO_A}")
        assert ids(by_scope, "facts") == {first, second}

        by_channel = await refined(engine, reader("alice", REPO_A, REPO_B), prior, "channel hypotheses")
        assert by_channel.facts == () and by_channel.hypotheses == ()

        nothing = await refined(engine, reader("alice", REPO_A, REPO_B), prior, "artifact a_does_not_exist")
        assert nothing.facts == () and nothing.result_id is not None, "an empty drill-down is an answer, not an error"


async def test_an_unrecognised_instruction_is_a_static_error_never_the_unmodified_result(database) -> None:  # noqa: F811
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await seed(engine, REPO_A, "a1", "observed event one")
        prior = await answer(engine, reader("alice", REPO_A))

        unrecognised = await forbidden(engine, reader("alice", REPO_A), "refine", refine_args(prior, "explain"))
        assert (unrecognised.code, unrecognised.detail) == ("validation_error", "instruction_unrecognised")
        required = await forbidden(engine, reader("alice", REPO_A), "refine", refine_args(prior, "  "))
        assert (required.code, required.detail) == ("validation_error", "instruction_required")
        bad_channel = await forbidden(
            engine, reader("alice", REPO_A), "refine", refine_args(prior, "channel everything")
        )
        assert bad_channel.code == "validation_error"


async def test_an_injected_store_is_the_store_even_while_it_is_empty(database) -> None:  # noqa: F811
    """A store is a seam: it may carry an injected clock or capacity, so an empty one must not be swapped out."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        injected = ResultStore()
        register_read_handlers(engine, runtime, results=injected)
        assert engine.results is injected, "issue and resolve must share one map, or every refine is refused"
        await seed(engine, REPO_A, "a1", "observed event one")
        prior = await answer(engine, reader("alice", REPO_A))
        assert len(injected) == 1, "the injected store received the handle, and the entry point resolved it"
        await refined(engine, reader("alice", REPO_A), prior, "mention observed")
        assert len(injected) == 2


async def test_a_refine_reassembles_with_the_budget_the_answer_used(database) -> None:  # noqa: F811
    """Criterion 7: a drill-down returns a subset of the result it names, not a re-budgeted version of it."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        for index in range(4):
            await seed(engine, REPO_A, f"a{index}", f"observed event number {index}")
        prior = await answer(engine, reader("alice", REPO_A), {"budget": {"facts": 6}})
        assert prior.coverage.facts.truncated > 0, "the small budget must truncate, or this test proves nothing"
        narrowed = await refined(engine, reader("alice", REPO_A), prior, "mention observed")
        assert ids(narrowed, "facts") <= ids(prior, "facts"), "a refine must not re-budget its way to more rows"


async def test_a_narrowed_change_result_reports_only_the_keys_it_kept(database) -> None:  # noqa: F811
    """Criterion 7: `changes` is narrowed with the channels, so a drill-down never reports a row it dropped."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)

        async def episode_at(when: datetime, key: str) -> str:
            request = episode(REPO_A, key, f"observed event {key}")
            request["event_time"] = when.isoformat()
            receipt = await engine.call(caller("alice"), "ingest_episode", request)
            assert isinstance(receipt, Receipt)
            return receipt.id

        # Both are new inside the window, so `change` reports one `added` key for each.
        kept = await episode_at(T0 + timedelta(minutes=10), "keep")
        dropped = await episode_at(T0 + timedelta(minutes=20), "drop")
        prior = await answer(
            engine,
            reader("alice", REPO_A),
            {
                "mode": "change",
                "window_start": (T0 - timedelta(hours=1)).isoformat(),
                "window_end": (T0 + timedelta(hours=1)).isoformat(),
            },
        )
        reported = {identifier for change in prior.changes for identifier in (*change.previous, *change.current)}
        assert reported == {kept, dropped}
        narrowed = await refined(engine, reader("alice", REPO_A), prior, f"artifact {kept}")
        named = {identifier for change in narrowed.changes for identifier in (*change.previous, *change.current)}
        assert named == {kept}, "the drill-down must not report the change it narrowed away"


async def test_an_answer_with_nothing_read_still_carries_a_handle(database) -> None:  # noqa: F811
    """Criterion 1: every answer path issues one, including the empty-read-set shortcut in `query`."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_read_handlers(engine, runtime)
        refused = CallerContext.from_credential(
            Credential("alice", "C", False), write_scope="/u/alice/", read_set=ReadSet((), ())
        )
        result = await answer(engine, refused)
        assert result.result_id is not None
        assert engine.resolve_result(refused, result.result_id) is not None


# --- read-set containment ------------------------------------------------------------------------------


async def test_a_widened_credential_does_not_widen_the_drill_down(database) -> None:  # noqa: F811
    """Criterion 3: the refine executes under the result's recorded read set, so a later grant adds nothing."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        inside = await seed(engine, REPO_A, "a1", "observed event one")
        await seed(engine, REPO_B, "b1", "observed event two")
        # the query selected only repo A: repo B's row is not in its result, and so not in its handle
        prior = await answer(engine, reader("alice", REPO_A))
        assert ids(prior, "facts") == {inside}
        # the refining credential now also selects repo B: wider than the recorded set, and it covers it
        wider = reader("alice", REPO_A, REPO_B)
        widened = await refined(engine, wider, prior, "mention observed")
        assert ids(widened, "facts") == {inside}, "a widened credential must not reach repo B's row"
        # and it still cannot reach it by naming it: the candidate set is the prior result's own ids
        named = await refined(engine, wider, prior, "scope /u/alice/")
        assert ids(named, "facts") == {inside}


async def test_a_narrowed_credential_is_refused_rather_than_silently_redirected(database) -> None:  # noqa: F811
    """Criterion 4: the drill-down may only run while the caller still holds the recorded read set."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await seed(engine, REPO_A, "a1", "observed event one")
        prior = await answer(engine, reader("alice"))  # recorded read set: /u/alice/
        narrowed = reader("alice", REPO_A)  # no longer covers /u/alice/
        error = await forbidden(engine, narrowed, "refine", refine_args(prior, "mention observed"))
        assert (error.code, error.detail) == ("forbidden", None)


async def test_a_narrowed_change_keeps_each_change_whole_with_its_original_outcome(database) -> None:  # noqa: F811
    """Criterion 7: narrowing to one side of a `changed` key never re-labels it `added` or `removed`."""
    async with scenario(database) as (engine, _conn, s):
        prior = await engine.call(
            caller("alice"),
            "query",
            {
                "request": {
                    "scope": SCOPE,
                    "mode": "change",
                    "window_start": CHANGE_T0.isoformat(),
                    "window_end": T2.isoformat(),
                }
            },
        )
        assert isinstance(prior, QueryResult)
        original = {(change.outcome, change.previous, change.current) for change in prior.changes}
        assert ("changed", (s.residence_before,), (s.residence_after,)) in original, prior.changes
        for side in (s.residence_before, s.residence_after):
            narrowed = await refined(engine, caller("alice"), prior, f"artifact {side}")
            reported = {(change.outcome, change.previous, change.current) for change in narrowed.changes}
            assert reported == {("changed", (s.residence_before,), (s.residence_after,))}, (side, narrowed.changes)
        # a refine that keeps everything reports exactly the diff it drills into
        everything = await refined(engine, caller("alice"), prior, f"scope {SCOPE}")
        assert {(c.outcome, c.previous, c.current) for c in everything.changes} == original


async def test_a_grant_change_between_the_calls_neither_widens_nor_survives_a_revocation(database) -> None:  # noqa: F811
    """Criteria 3 and 4 with a real credential change: the read set is re-resolved from live grants on each call.

    The read path answers from the caller's own rows (RLS), so this asserts on the read set each drill-down executes
    under, which is what a grant changes: never the live one when it is wider, and no drill-down once it is narrower.
    """
    async with world(database) as (conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_read_handlers(engine, runtime)
        selectors = [Selector.of("/u/bob/"), Selector.of("/u/alice/", {"project", "shared"})]

        async def bob() -> CallerContext:
            read_set = await resolve(conn, "bob", selectors, schema_name=runtime.schema_name)
            credential = Credential("bob", "C", False)
            return CallerContext.from_credential(credential, write_scope="/u/bob/", read_set=read_set)

        def paths(ctx: CallerContext, result: QueryResult) -> set[str]:
            assert result.result_id is not None
            snapshot = engine.resolve_result(ctx, result.result_id)
            assert snapshot is not None
            return {entry.path for entry in snapshot.read_set.entries}

        # widened: before the grant alice's namespace is denied, so the result's read set does not hold it
        before_ctx = await bob()
        before = await answer(engine, before_ctx)
        assert paths(before_ctx, before) == {"/u/bob/"}
        grant = await engine.call(
            caller("alice"), "grant_access", {"scope": "/u/alice/", "principal": "bob", "idempotency_key": "g"}
        )
        assert isinstance(grant, str)
        widened_ctx = await bob()
        assert "/u/alice/" in {entry.path for entry in widened_ctx.read_set.entries}, "the grant really widened bob"
        widened = await refined(engine, widened_ctx, before, "scope /u/bob/")
        assert paths(widened_ctx, widened) == {"/u/bob/"}, "a later grant must not widen the drill-down"

        # narrowed: a result produced under the grant is refused once the grant is revoked
        granted_ctx = await bob()
        granted = await answer(engine, granted_ctx)
        assert paths(granted_ctx, granted) == {"/u/bob/", "/u/alice/"}
        await engine.call(caller("alice"), "revoke_access", {"grant_id": grant})
        revoked_ctx = await bob()
        assert "/u/alice/" not in {entry.path for entry in revoked_ctx.read_set.entries}
        error = await forbidden(engine, revoked_ctx, "refine", refine_args(granted, "scope /u/bob/"))
        assert (error.code, error.detail) == ("forbidden", None)


# --- handles -------------------------------------------------------------------------------------------


async def test_unknown_foreign_and_expired_handles_are_one_indistinguishable_answer(database) -> None:  # noqa: F811
    """Criterion 5: the caller learns nothing about whether a handle exists for somebody else."""
    async with world(database) as (_conn, engine, _schema):
        clock = Clock()
        engine.results = ResultStore(clock=clock)
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await seed(engine, REPO_A, "a1", "observed event one")
        prior = await answer(engine, reader("alice", REPO_A))

        never = {"result_id": "res_" + "0" * 32, "instruction": "mention observed"}
        alice = reader("alice", REPO_A)
        drill = refine_args(prior, "mention observed")
        assert (await forbidden(engine, alice, "refine", never)).code == "forbidden"
        assert (await forbidden(engine, reader("bob"), "refine", drill)).code == "forbidden"
        clock.now += timedelta(hours=1)  # past RESULT_TTL
        assert (await forbidden(engine, alice, "refine", drill)).code == "forbidden"
        # a handle-shaped argument is still only ever a handle: it never resolves as an artifact id
        shaped = await forbidden(engine, reader("alice", REPO_A), "get", {"artifact_id": "res_" + "0" * 32})
        assert shaped.code == "forbidden"


async def test_a_refine_chain_is_bounded(database) -> None:  # noqa: F811
    """Criterion 9: the depth lives in the handle, so an endless chain costs a fixed number of statements."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        await seed(engine, REPO_A, "a1", "observed event one")
        result = await answer(engine, reader("alice", REPO_A))
        for depth in range(MAX_REFINE_DEPTH):
            result = await refined(engine, reader("alice", REPO_A), result, "mention observed")
            assert result.facts, depth
        exceeded = await forbidden(engine, reader("alice", REPO_A), "refine", refine_args(result, "mention observed"))
        assert (exceeded.code, exceeded.detail) == ("validation_error", "refine_depth_exceeded")
        # the depth answer does not change with the instruction, so a malformed one at the bound is still the bound
        malformed = await forbidden(
            engine, reader("alice", REPO_A), "refine", {"result_id": result.result_id, "instruction": "explain"}
        )
        assert (malformed.code, malformed.detail) == ("validation_error", "refine_depth_exceeded")


# --- channel membership, labels and bounds -------------------------------------------------------------


async def test_narrowing_a_channel_keeps_exactly_that_membership_and_nothing_moves(database) -> None:  # noqa: F811
    """Criterion 7: the filter is `channel_of`'s own answer, and a kept row reaches assembly unchanged."""
    async with world(database) as (conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        episode_id = await seed(engine, REPO_A, "a1", "observed event one")
        abstraction_id = await verified_abstraction(engine, conn, "abs-a1")
        prior = await answer(engine, reader("alice", REPO_A))
        answered = {item.artifact_id for item in (*prior.facts, *prior.hypotheses)}
        assert answered == {episode_id, abstraction_id}
        for channel in ("facts", "hypotheses", "procedures", "conflicts"):
            narrowed = await refined(engine, reader("alice", REPO_A), prior, f"channel {channel}")
            assert ids(narrowed, channel) == ids(prior, channel), channel
            assert all(
                ids(narrowed, other) == set()
                for other in ("facts", "hypotheses", "procedures", "conflicts")
                if other != channel
            ), channel


async def test_a_narrowed_abstraction_still_carries_its_bound_label_and_support(database) -> None:  # noqa: F811
    """Criterion 8: a refinement narrows *which* artifacts answer, never what an artifact carries (``I14``)."""
    async with world(database) as (conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        identifier = await verified_abstraction(engine, conn, "abs-a1")
        prior = await answer(engine, reader("alice", REPO_A))
        original = next(item for item in (*prior.facts, *prior.hypotheses) if item.artifact_id == identifier)
        assert original.bound is not None, "the seeded abstraction must be verified with a bound"
        narrowed = await refined(engine, reader("alice", REPO_A), prior, f"artifact {identifier}")
        kept = next(item for item in (*narrowed.facts, *narrowed.hypotheses) if item.artifact_id == identifier)
        assert kept.channel == original.channel
        assert kept.bound == original.bound
        assert kept.label == original.label
        assert kept.support_summary == original.support_summary


# --- deletion ------------------------------------------------------------------------------------------


async def test_a_purged_row_is_not_resurrected_by_the_result_that_named_it(database) -> None:  # noqa: F811
    """Criterion 10: candidates are re-read from the database, so a purged id is simply absent."""
    async with world(database) as (_conn, engine, _schema):
        runtime = engine.resolver.runtime  # type: ignore[attr-defined]
        register_ingest_handlers(engine, runtime, working_coding_profile())
        register_read_handlers(engine, runtime)
        register_maintain_handlers(engine, runtime)
        purged = await seed(engine, REPO_A, "a1", "observed event one")
        kept = await seed(engine, REPO_B, "b1", "observed event two")  # outside the purged subtree
        prior = await answer(engine, reader("alice", REPO_A, REPO_B))
        assert ids(prior, "facts") == {purged, kept}
        await engine.call(reader("alice", admin=True), "purge_working", {"scope": REPO_A})
        narrowed = await refined(engine, reader("alice", REPO_A, REPO_B), prior, "mention observed")
        assert ids(narrowed, "facts") == {kept}, "a deleted row is not resurrected by the handle that named it"
        gone = await forbidden(engine, reader("alice", REPO_A, REPO_B), "get", {"artifact_id": purged})
        assert gone.code == "forbidden"
        only_purged = await refined(engine, reader("alice", REPO_A, REPO_B), prior, f"artifact {purged}")
        assert only_purged.facts == ()


# --- seeding: a verified abstraction with a bound ------------------------------------------------------
# `verify` is not wired yet (E-039 lands it), so the one artifact shape a read can render with a bound is seeded by
# the owner, exactly as `test_staleness_db.py` does for the staleness inputs.


async def verified_abstraction(engine: Engine, conn: AsyncConnection, version_id: str) -> str:
    """A verified, fresh abstraction at ``/u/alice/`` carrying a bound through ``Engine.call``."""
    assert isinstance(engine.resolver, DbTargets)
    method_id = await engine.call(
        reader("alice", admin=True),
        "register_method",
        {
            "name": "bound-method",
            "version": "1",
            "params_schema_hash": "a" * 64,
            "description": "bound support",
            "idempotency_key": "bound-method",
        },
    )
    assert isinstance(method_id, str)
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
            "single_valued, content, scope_path, event_range, recorded_range, layer, visibility, method_id, "
            "created_by, created_tier) VALUES (%s, %s, 'alice', 'active', 'code', 'statement', false, %s, %s, "
            "tstzrange(%s, NULL), tstzrange(pg_catalog.transaction_timestamp(), NULL), 'persona', 'private', %s, "
            "'alice', 'B')",
            (version_id, version_id, STATEMENT, REPO_A, T0, method_id),
        )
        await conn.execute(
            "INSERT INTO abstraction_stats (version_id, user_id) VALUES (%s, 'alice') "
            "ON CONFLICT (version_id) DO NOTHING",
            (version_id,),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, 't-alice', 'alice', 'x', 'support')",
            (version_id,),
        )
        ver_id = "ver-" + version_id
        await conn.execute(
            "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
            "negative_control_slice_id, support_window, held_out_window, support_context_key, held_out_context_key, "
            "outcome) VALUES (%s, %s, 'alice', 'split', clock_now(), 'neg', %s::tstzrange, %s::tstzrange, "
            "'ctx-a', 'ctx-b', 'verified')",
            (ver_id, version_id, WINDOW_A, WINDOW_B),
        )
        await conn.execute(
            "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) "
            "VALUES (%s, 'alice', 'support', %s), (%s, 'alice', 'held_out', %s)",
            (ver_id, f"sup-{version_id}", ver_id, f"held-{version_id}"),
        )
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 40, counter_count = 0, undetermined_count = 0, "
            "opportunity_count = 40, refutation_attempts = 3, confidence = 0.95, confidence_method_id = %s, "
            "verification_status = 'verified', expires_at = clock_now() + interval '365 days' "
            "WHERE version_id = %s AND user_id = 'alice'",
            (method_id, version_id),
        )
    assert await rows(conn, "SELECT kind FROM memory_version WHERE id = %s", (version_id,)) == [("abstraction",)]
    return version_id
