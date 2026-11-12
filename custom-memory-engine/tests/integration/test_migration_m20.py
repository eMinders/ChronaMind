"""Migration 20: the procedure outcome counters (``E-027``, ``V2-D75``).

These tests run against the REAL migrated schema and check the properties the migration itself must own, rather than
the ones the handler happens to enforce: only a procedure may have outcome statistics; the counters only ever grow; the
counters are per-principal under row-level security; and the rollback restores version 19 exactly.

The distinction matters because the handler is one caller. A replay harness, a backfill or an operator writes SQL
directly, so a rule that lives only in :mod:`api.outcome_handlers` is a rule nothing enforces.
"""

from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import RaiseException

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture; seeds u1 and u2)

pytestmark = pytest.mark.integration
M20 = 20


async def version(
    conn: AsyncConnection, version_id: str, *, kind: str = "procedure", user: str = "u1", active: bool = True
) -> None:
    """A minimal row of ``kind`` with the axes and provenance the triggers require (I1, I1').

    ``active=False`` seeds a ``candidate`` row. That is the status a test may delete: ``recorded_iff_believed`` ties an
    active row to a recorded range, and ``guard_update`` makes that range immutable, so a row cannot be demoted to
    ``candidate`` after the fact -- it has to be created that way.
    """
    status = "active" if active else "candidate"
    recorded = "tstzrange(clock_now(), NULL)" if active else "NULL"
    # `sync_memory_version_axes` accepts only (candidate, retired) and (active, asserted|observed|inferred)
    lifecycle = "asserted" if active else "retired"
    session, turn_id = f"s-m20-{user}", f"t-m20-{user}"
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, clock_now()) ON CONFLICT DO NOTHING",
        (session, user),
    )
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, 1, 'user', 'Rerun the focused suite.', clock_now()) ON CONFLICT DO NOTHING",
        (turn_id, session, user),
    )
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, valid_from_basis, assertion_mode, extractor_version, "
        "scope_path, kind, lifecycle, retention) "
        "VALUES (%s, 'mk', %s, %s, 'self', 'statement', false, 'Rerun the focused suite.', "
        "tstzrange('-infinity', NULL), "
        + recorded
        + ", 'semantic', 'inferred', 'asserted', 'test', %s, %s, %s, 'durable')",
        (version_id, user, status, f"/u/{user}/", kind, lifecycle),
    )
    await conn.execute(
        "INSERT INTO memory_evidence (version_id, user_id, turn_id, evidence_span, evidence_role) "
        "VALUES (%s, %s, %s, 'Rerun the focused suite.', 'supports')",
        (version_id, user, turn_id),
    )


async def record_outcome(
    conn: AsyncConnection, version_id: str, *, user: str = "u1", successes: int = 1, failures: int = 0
) -> None:
    await conn.execute(
        "INSERT INTO outcome_stats (version_id, user_id, successes, failures, last_used_at) "
        "VALUES (%s, %s, %s, %s, clock_now())",
        (version_id, user, successes, failures),
    )


async def test_a_procedure_has_outcome_statistics(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await record_outcome(conn, "p1", successes=2, failures=1)
        assert await rows(
            conn, "SELECT successes, failures, cost_count, mean_cost FROM outcome_stats WHERE version_id = 'p1'"
        ) == [(2, 1, 0, None)]


async def test_a_non_procedure_cannot_have_outcome_statistics(database) -> None:  # noqa: F811
    """Mirrors ``abstraction_stats``: the kind rule is the schema's, not one handler's."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "c1", kind="claim")
        with pytest.raises(RaiseException, match="outcome_stats_require_a_procedure"):
            await record_outcome(conn, "c1")


async def test_an_outcome_for_a_version_that_does_not_exist_is_refused(database) -> None:  # noqa: F811
    """No procedure, no statistics. The *guard* refuses it (a missing kind is not a procedure), not the FK.

    Both are correct answers; the guard simply runs first on INSERT, and asserting the actual code keeps this test
    honest about which one is doing the work.
    """
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(RaiseException, match="outcome_stats_require_a_procedure"):
            await record_outcome(conn, "no-such-version")


async def test_an_outcome_cannot_be_recorded_against_another_principals_procedure(database) -> None:  # noqa: F811
    """Cross-tenant statistics are structurally impossible.

    Two independent guards refuse this, and the *first* one to run is the kind guard: it looks the id up scoped to the
    writing principal, so another principal's procedure simply is not there. The composite foreign key would refuse it
    too, which is what makes the property structural rather than a consequence of one lookup order.
    """
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1", user="u1")
        with pytest.raises(RaiseException, match="outcome_stats_require_a_procedure"):
            await record_outcome(conn, "p1", user="u2")


@pytest.mark.parametrize("column", ["successes", "failures", "cost_count", "cost_sum"])
async def test_every_counter_is_monotone(database, column) -> None:  # noqa: F811
    """An outcome is a recorded event: a decrement would rewrite history (I3). One column at a time.

    Each starts at a non-zero value so a decrement is a real change: a 0 -> 0 "decrement" is not a decrement, and a
    parametrisation over zeros would pass without ever exercising the guard.
    """
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await record_outcome(conn, "p1", successes=2, failures=2)
        await conn.execute("UPDATE outcome_stats SET cost_count = 2, cost_sum = 5 WHERE version_id = 'p1'")
        with pytest.raises(RaiseException, match="outcome_stats_counters_are_monotone"):
            # an identifier cannot be a bound parameter, so it is composed (the value is a literal from this test)
            await conn.execute(
                sql.SQL("UPDATE outcome_stats SET {column} = {column} - 1 WHERE version_id = 'p1'").format(
                    column=sql.Identifier(column)
                )
            )


async def test_growing_a_counter_is_allowed(database) -> None:  # noqa: F811
    """The guard forbids a decrement, not an increment: ``record_outcome`` is exactly this statement."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await record_outcome(conn, "p1", successes=1)
        await conn.execute("UPDATE outcome_stats SET successes = successes + 1 WHERE version_id = 'p1'")
        assert await rows(conn, "SELECT successes FROM outcome_stats WHERE version_id = 'p1'") == [(2,)]


async def test_the_identity_of_a_statistics_row_cannot_be_repointed(database) -> None:  # noqa: F811
    """Moving a row to another procedure would carry its history to a procedure that never earned it."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await version(conn, "p2")
        await record_outcome(conn, "p1")
        with pytest.raises(RaiseException, match="outcome_stats_identity_is_fixed"):
            await conn.execute("UPDATE outcome_stats SET version_id = 'p2' WHERE version_id = 'p1'")


async def test_a_recorded_cost_of_zero_is_a_real_mean(database) -> None:  # noqa: F811
    """`cost_count = 1, cost_sum = 0` is legitimate, so no constraint may pair the two.

    This is the case a ``(cost_count = 0) = (cost_sum = 0)`` check would wrongly forbid; it is asserted here so the
    constraint cannot be reintroduced.
    """
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await conn.execute(
            "INSERT INTO outcome_stats (version_id, user_id, successes, cost_count, cost_sum) "
            "VALUES ('p1', 'u1', 1, 1, 0)"
        )
        assert await rows(conn, "SELECT cost_count, mean_cost FROM outcome_stats WHERE version_id = 'p1'") == [
            (1, 0.0)
        ]


async def test_mean_cost_is_the_quotient_of_sum_and_count(database) -> None:  # noqa: F811
    """A generated column, so the stored mean cannot disagree with the ledger it is derived from."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await conn.execute(
            "INSERT INTO outcome_stats (version_id, user_id, successes, cost_count, cost_sum) "
            "VALUES ('p1', 'u1', 2, 4, 10)"
        )
        assert await rows(conn, "SELECT mean_cost FROM outcome_stats WHERE version_id = 'p1'") == [(2.5,)]


async def test_deleting_the_procedure_deletes_its_statistics(database) -> None:  # noqa: F811
    """ON DELETE CASCADE: statistics without their procedure are unreachable rows, not history worth keeping."""
    async with connect(database) as conn, conn.transaction():
        # the procedure is seeded as a candidate so it can be deleted; the statistics row is what is under test
        await version(conn, "p1", active=False)
        await record_outcome(conn, "p1")
        await conn.execute("DELETE FROM memory_version WHERE id = 'p1'")
        assert await rows(conn, "SELECT count(*) FROM outcome_stats WHERE version_id = 'p1'") == [(0,)]


async def test_rollback_restores_version_19_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M20 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M20 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert before != recorded[0][0]  # the migration did change the catalog
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() "
            "AND table_name = 'outcome_stats'",
        )
        # the trigger function is not table-owned, so a stray copy would be a rollback defect
        assert not await rows(
            conn,
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = current_schema() AND p.proname = 'guard_outcome_stats'",
        )
    assert (await runner.status()).version == M20 - 1
    assert (await runner.apply()).action == "applied"
