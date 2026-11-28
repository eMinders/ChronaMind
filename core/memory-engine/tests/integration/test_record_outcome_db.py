"""The procedure feedback loop through ``Engine.call`` (E-027, ``API_V2.md`` 4.5, ``V2-D75``).

The claim this file exists to prove is short: **storing a procedure, recording outcomes against it, and querying again
returns a different order, and removing the statistics term breaks exactly that**. Everything runs through the public
entry point under a ``NOSUPERUSER NOBYPASSRLS`` role against the real migrated schema, so a pass here is a round trip
through authorisation, row-level security, migration 20 and the ranking term -- not a unit test of a sorting function
(that is ``tests/test_procedure_ranking.py``).

Each criterion of issue #142 section 10 that is testable here is named in the test that proves it.
"""

from datetime import UTC, datetime

import pytest

from custom_memory_engine.api.entry import Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.outcome_handlers import register_outcome_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine.procedure_ranking import OutcomeStats
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.profile import Profile, parse_profile
from tests.integration.test_api_wiring_structure import database, forbidden, rows, world  # noqa: F401
from tests.integration.test_read_path_db import caller

pytestmark = pytest.mark.integration

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
PROFILE: Profile = parse_profile(
    {
        "schema_version": 1,
        "name": "test-coding",
        "kinds": ["episode", "claim", "procedure", "abstraction"],
        "extractors": [
            {"name": "tool_call_log", "version": "1", "source_types": ["tool"]},
            {"name": "user_instructions", "version": "1", "source_types": ["turn"]},
        ],
        "operators": ["consolidation"],
        "retention": {
            "defaults": {
                "episode": "session",
                "claim": "durable",
                "procedure": "session",
                "abstraction": "durable",
            },
            "promote_to_durable": True,
            "status": "placeholder:V2-D14",
        },
        "scope_root": "/u/{user}/p/{repo}/",
        "render": {"channel_budget_share": {"facts": 0.5, "hypotheses": 0.2, "procedures": 0.2, "conflicts": 0.1}},
        "facets": ["constraint", "context", "decision_pattern"],
        "association": {"degree_cap": 32, "extensions": []},
        "abstraction_expiry": {"days_by_facet": {}, "status": "placeholder:V2-D14"},
    }
)


async def wired(database):  # noqa: F811
    """The world fixture with the writer, reader and outcome handlers wired as a deployment wires them."""
    return world(database)


async def seed_turn(conn, turn_id: str, content: str, *, sequence: int) -> None:
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
        (turn_id, sequence, content, T0),
    )


async def store_procedure(engine: Engine, conn, key: str, content: str, *, sequence: int) -> str:
    """A procedure stored through ``assert``, grounded on a turn seeded by the world fixture."""
    await seed_turn(conn, f"turn:{key}", content, sequence=sequence)
    receipt = await engine.call(
        caller("alice"),
        "assert",
        {
            "scope": "/u/alice/",
            "kind": "procedure",
            "content": content,
            "evidence": [
                {"type": "turn", "ref": f"turn:{key}", "span": content, "content_hash": canonical_hash(content)}
            ],
            "idempotency_key": key,
        },
    )
    assert isinstance(receipt, Receipt)
    return receipt.id


async def query_procedures(engine: Engine, scope: str = "/u/alice/") -> list[str]:
    """The ``procedures`` channel, in the order the engine returned it."""
    result = await engine.call(caller("alice"), "query", {"request": {"scope": scope}})
    assert isinstance(result, QueryResult)
    return [item.artifact_id for item in result.procedures]


async def record(engine: Engine, procedure_id: str, outcome: str, cost: float | None = None) -> OutcomeStats:
    stats = await engine.call(
        caller("alice"), "record_outcome", {"procedure_id": procedure_id, "outcome": outcome, "cost": cost}
    )
    assert isinstance(stats, OutcomeStats), stats
    return stats


# --- criterion 1: the error contract ------------------------------------------------------------------


async def test_record_outcome_refuses_a_non_procedure_with_precondition_failed(database) -> None:  # noqa: F811
    """§7.2 case 1: recording an outcome against a claim is ``precondition_failed``, not a silent no-op."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('turn:c1', 's-alice', 'alice', 9, 'user', 'a claim', %s)",
            (T0,),
        )
        claim = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "claim",
                "content": "The CI job runs pytest.",
                "evidence": [
                    {"type": "turn", "ref": "turn:c1", "span": "a claim", "content_hash": canonical_hash("a claim")}
                ],
                "idempotency_key": "claim-c1",
            },
        )
        assert isinstance(claim, Receipt)
        with pytest.raises(EntryError) as error:
            await record(engine, claim.id, "success")
        assert error.value.code == "precondition_failed"
        assert error.value.detail == "target_is_not_a_procedure"
        # nothing was recorded for it
        assert await rows(conn, "SELECT count(*) FROM outcome_stats WHERE version_id = %s", (claim.id,)) == [(0,)]


async def test_an_unreadable_procedure_and_an_absent_one_are_the_same_answer(database) -> None:  # noqa: F811
    """§7.2 case 2: ``forbidden`` for another principal's procedure, identical to a missing id (T-09, T-11)."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-foreign", "Rerun the focused suite.", sequence=10)
        foreign = await forbidden(
            engine, caller("bob"), "record_outcome", {"procedure_id": procedure_id, "outcome": "success"}
        )
        missing = await forbidden(
            engine, caller("bob"), "record_outcome", {"procedure_id": "no-such", "outcome": "success"}
        )
        assert (foreign.code, foreign.detail) == (missing.code, missing.detail) == ("forbidden", None)


async def test_an_outcome_outside_the_caller_read_set_is_forbidden(database) -> None:  # noqa: F811
    """§7.2 case 3: ``targets_readable`` refuses a procedure the caller's read set does not cover."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-scoped", "Rerun the focused suite.", sequence=11)
        # bob holds no grant at all, so even a well-formed id in alice's namespace is outside his read set
        refused = await forbidden(
            engine, caller("bob"), "record_outcome", {"procedure_id": procedure_id, "outcome": "success"}
        )
        assert refused.code == "forbidden"


@pytest.mark.parametrize(
    ("arguments", "detail"),
    [
        ({"outcome": "maybe"}, "outcome_invalid"),
        ({"outcome": "success", "cost": -1}, "cost_invalid"),
        ({"outcome": "success", "cost": True}, "cost_invalid"),
        ({"outcome": "success", "episode_ref": ""}, "episode_ref_invalid"),
    ],
    ids=["bad-outcome", "negative-cost", "bool-cost", "empty-episode-ref"],
)
async def test_malformed_arguments_are_validation_errors(database, arguments, detail) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-invalid", "Rerun the focused suite.", sequence=12)
        with pytest.raises(EntryError) as error:
            await engine.call(caller("alice"), "record_outcome", {"procedure_id": procedure_id, **arguments})
        assert (error.value.code, error.value.detail) == ("validation_error", detail)


# --- criterion 2: the counters are persisted and readable ---------------------------------------------


async def test_successes_failures_last_used_at_and_mean_cost_are_persisted(database) -> None:  # noqa: F811
    """§10 row 2: every counter of ``ARCHITECTURE_V2.md`` section 6 round-trips through the table."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-counters", "Rerun the focused suite.", sequence=13)

        first = await record(engine, procedure_id, "success", cost=2.0)
        assert (first.successes, first.failures, first.uses) == (1, 0, 1)
        assert first.mean_cost == 2.0
        assert first.last_used_at is not None

        second = await record(engine, procedure_id, "failure", cost=4.0)
        assert (second.successes, second.failures) == (1, 1)
        assert second.mean_cost == 3.0  # (2 + 4) / 2, a mean over recorded costs only
        assert second.last_used_at is not None and second.last_used_at >= first.last_used_at

        # the persisted row agrees with what the call returned
        stored = await rows(
            conn,
            "SELECT successes, failures, cost_count, cost_sum, mean_cost FROM outcome_stats WHERE version_id = %s",
            (procedure_id,),
        )
        assert stored == [(1, 1, 2, 6.0, 3.0)]


async def test_an_absent_cost_is_not_a_zero_cost(database) -> None:  # noqa: F811
    """``mean_cost`` is ``None`` until a cost is recorded, and a recorded ``0.0`` is a real mean."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-nocost", "Rerun the focused suite.", sequence=14)
        assert (await record(engine, procedure_id, "success")).mean_cost is None
        assert (await record(engine, procedure_id, "success", cost=0.0)).mean_cost == 0.0


async def test_replaying_an_outcome_counts_twice_by_design(database) -> None:  # noqa: F811
    """§5 Q3: ``record_outcome`` has no ``idempotency_key``; a replay is a second recorded outcome, not a no-op."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-replay", "Rerun the focused suite.", sequence=15)
        assert (await record(engine, procedure_id, "success")).successes == 1
        assert (await record(engine, procedure_id, "success")).successes == 2


async def test_an_outcome_for_one_procedure_does_not_touch_another(database) -> None:  # noqa: F811
    """Isolation between procedures, so a ranking difference cannot come from a shared counter."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        first = await store_procedure(engine, conn, "p-iso-a", "Rerun the focused suite.", sequence=16)
        second = await store_procedure(engine, conn, "p-iso-b", "Clear the build cache.", sequence=17)
        await record(engine, first, "success")
        assert await rows(conn, "SELECT count(*) FROM outcome_stats WHERE version_id = %s", (second,)) == [(0,)]


async def test_an_episode_reference_is_audited_without_becoming_a_locator(database) -> None:  # noqa: F811
    """``episode_ref`` is recorded as a flag, never as the id (``RL-072``: no locators in audit details)."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "p-ep", "Rerun the focused suite.", sequence=18)
        await engine.call(
            caller("alice"),
            "record_outcome",
            {"procedure_id": procedure_id, "outcome": "success", "episode_ref": "v_some_episode"},
        )
        audited = await rows(
            conn,
            "SELECT details FROM audit_event WHERE event_type = 'outcome_recorded' AND memory_ref = %s",
            (procedure_id,),
        )
        assert audited, "the outcome must be recorded as a content-free audit event"
        details = audited[0][0]
        assert isinstance(details, dict)
        assert details["episode_referenced"] is True
        assert "v_some_episode" not in str(details), "the episode id is a locator and must not be stored"


# --- criteria 3 and 4: the order changes because of the statistics ------------------------------------


async def test_the_same_query_returns_a_different_order_after_outcomes_are_recorded(database) -> None:  # noqa: F811
    """§10 rows 3 and 4: feedback changes the order, deterministically, and only because of the statistics.

    Three procedures are stored and their pre-feedback order is whatever the id tie-break gives (they all start at
    0/0, so nothing else distinguishes them). Outcomes are then recorded so that the success rate is *strictly
    decreasing* in the reverse of that order, which forces the exact reverse to come back. Three distinct rates make
    the assertion independent of the random ids: reversing three distinct items can never reproduce the original
    order, so this cannot pass by coincidence the way a two-item ``after != before`` check can.
    """
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        ids = [
            await store_procedure(engine, conn, key, content, sequence=sequence)
            for key, content, sequence in (
                ("r-a", "Rerun the focused suite.", 20),
                ("r-b", "Clear the build cache.", 21),
                ("r-c", "Reinstall the toolchain.", 22),
            )
        ]

        before = await query_procedures(engine)
        assert sorted(before) == sorted(ids)  # all three, in the tie-break order
        assert await query_procedures(engine) == before  # deterministic before any feedback

        # the last procedure in the pre-feedback order gets the best record, the first gets the worst, so the ranking
        # order is exactly the reverse of `before`; 3/0, 2/0 and 0/1 give distinct smoothed rates (4/5, 3/4, 1/3)
        worst, middle, best = before
        for _ in range(3):
            await record(engine, best, "success")
        for _ in range(2):
            await record(engine, middle, "success")
        await record(engine, worst, "failure")

        after = await query_procedures(engine)
        assert after == list(reversed(before)), (before, after)
        assert await query_procedures(engine) == after  # deterministic after feedback
        assert after != before


async def test_the_statistics_term_is_what_changes_the_order(database) -> None:  # noqa: F811
    """§10 row 4's control, in-process: with the counters neutralised the order reverts to the tie-break.

    Deleting the term from ``read_handlers`` is what the PR demonstrates separately; this is the same claim as a
    regression test, so a future refactor that drops the term fails here rather than only in a review.
    """
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        first = await store_procedure(engine, conn, "ctl-a", "Rerun the focused suite.", sequence=22)
        second = await store_procedure(engine, conn, "ctl-b", "Clear the build cache.", sequence=23)
        await record(engine, second, "success")
        await record(engine, first, "failure")
        ranked = await query_procedures(engine)
        assert ranked == [second, first]

        # Remove the recorded outcomes (as the owner: `guard_outcome_stats` correctly refuses a decrement, which the
        # schema test asserts). With both procedures back at 0/0 the tie-break alone decides.
        await conn.execute("DELETE FROM outcome_stats")
        neutral = await query_procedures(engine)
        assert sorted(neutral) == sorted(ranked)
        assert neutral == sorted(ranked), (
            "without statistics the order must be the id tie-break, not the feedback order"
        )


async def test_a_contested_procedure_never_outranks_an_asserted_one_whatever_the_statistics(database) -> None:  # noqa: F811
    """§10 row 5 and §7.6: ranking cannot promote across channels, so a perfect record cannot lift a non-asserted one.

    Two procedures are stored; one is then retired and its record is made perfect. It must not appear in
    ``procedures`` at all -- which is stronger than "ranked lower", and is what ``CHANNELS.md`` membership guarantees.
    """
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        keeper = await store_procedure(engine, conn, "keep-me", "Rerun the focused suite.", sequence=24)
        loser = await store_procedure(engine, conn, "retire-me", "Clear the build cache.", sequence=25)

        await record(engine, loser, "success")
        await record(engine, loser, "success")
        await record(engine, keeper, "failure")

        # retire the one with the perfect record: its statistics must not resurrect it
        # Tier C, because the assertion was made by alice acting as Tier C: `own_lineage_only` binds the retraction to
        # the tier that produced the artifact, and Tier B retracting a Tier C assertion is itself `forbidden`.
        retired = await engine.call(caller("alice"), "retract_own", {"artifact_id": loser, "reason": "superseded"})
        assert isinstance(retired, Receipt)

        channel = await query_procedures(engine)
        assert channel == [keeper], channel
        assert loser not in channel


# --- criterion 6: membership is untouched by ranking --------------------------------------------------


async def test_ranking_does_not_change_which_channel_an_item_is_in(database) -> None:  # noqa: F811
    """§10 row 6: asserted for every returned item, not only for procedures."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        procedure_id = await store_procedure(engine, conn, "chan-p", "Rerun the focused suite.", sequence=26)
        await engine.call(
            caller("alice"),
            "ingest_episode",
            {
                "scope": "/u/alice/",
                "source": {
                    "type": "tool",
                    "ref": "tool:chan",
                    "span": "ran the suite",
                    "content_hash": canonical_hash("ran the suite"),
                },
                "content": "ran the suite",
                "event_time": T0.isoformat(),
                "idempotency_key": "chan-ep",
            },
        )
        before = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(before, QueryResult)
        await record(engine, procedure_id, "success")
        after = await engine.call(caller("alice"), "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(after, QueryResult)

        def placement(result: QueryResult) -> dict[str, str]:
            placed = {}
            for name, items in (
                ("facts", result.facts),
                ("hypotheses", result.hypotheses),
                ("procedures", result.procedures),
                ("conflicts", result.conflicts),
            ):
                placed.update({item.artifact_id: name for item in items})
            return placed

        assert placement(before) == placement(after)
        assert all(item.channel == "procedures" for item in after.procedures)
        assert all(item.channel == "facts" for item in after.facts)


# --- criterion 5/7: the coding-profile CI-recipe case --------------------------------------------------


async def test_the_recipe_that_worked_for_the_planted_failure_is_surfaced_first(database) -> None:  # noqa: F811
    """§10 row 7: two CI recipes for the same planted failure; the one that worked ranks first.

    This is the case that makes the ``procedure`` kind live rather than decorative: the profile stores recipes,
    outcomes accumulate, and a later query answers "what did we try before" with the recipe that actually worked.
    """
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, PROFILE)
        register_outcome_handlers(engine, runtime)
        register_read_handlers(engine, runtime)
        worked = await store_procedure(
            engine,
            conn,
            "worked",
            "For a missing json name, add the import and rerun the focused pytest command.",
            sequence=30,
        )
        did_not = await store_procedure(
            engine,
            conn,
            "did-not",
            "For a missing json name, clear the build cache and rerun everything.",
            sequence=31,
        )
        # the planted failure is a NameError: the cache-clearing recipe did not fix it, the import did
        await record(engine, worked, "success")
        await record(engine, worked, "success")
        await record(engine, did_not, "failure")
        await record(engine, did_not, "failure")

        order = await query_procedures(engine)
        assert order == [worked, did_not], order
