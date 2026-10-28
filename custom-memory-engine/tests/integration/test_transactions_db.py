"""PostgreSQL integration tests for transactional supersession, boundary revision, archival, and ablations (RL-042)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.engine.predicates import (
    audit_as_of,
    current_state,
    fetch_versions,
    historical_as_of,
)
from custom_memory_engine.engine.transactions import (
    ArchivalPreconditionError,
    CandidateVersionInput,
    DerivationInput,
    EvidenceInput,
    TransactionError,
    archive_version,
    commit_initial_version,
    end_multivalued,
    ensure_archival_trigger,
    resolve_conflict_pending,
    revise_transition_boundary,
    stage_candidate,
    supersede_single,
)

pytestmark = pytest.mark.integration

CME_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = CME_ROOT / "src/custom_memory_engine/schema/001_full_schema.sql"


def instant(month: int, day: int, hour: int = 0) -> datetime:
    return datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


JAN10 = instant(1, 10)
FEB1 = instant(2, 1)
MAR10 = instant(3, 10)
APR1 = instant(4, 1)
JUN1 = instant(6, 1)
JUL15 = instant(7, 15)
JUL20 = instant(7, 20)
AUG5 = instant(8, 5)
AUG10 = instant(8, 10)
SEP1 = instant(9, 1)
OCT1 = instant(10, 1)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must explicitly name the disposable test database")
    config = DatabaseConfig.from_environment()
    schema = "cme_tx_" + uuid4().hex[:20]
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        async with connect((config, schema)) as conn:
            await conn.execute(cast(LiteralString, SCHEMA_SQL.read_text(encoding="utf-8")), prepare=False)
            await ensure_archival_trigger(conn, schema_name=schema)
            await seed_base_entities(conn)
        yield config, schema
    finally:
        async with await AsyncConnection.connect(
            config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
        ) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@asynccontextmanager
async def connect(database: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = database
    async with await AsyncConnection.connect(
        config.database_url.get_secret_value(), autocommit=True, connect_timeout=5
    ) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.execute("SET statement_timeout = '30s'")
        await conn.execute("SET timezone = 'UTC'")
        yield conn


async def seed_base_entities(conn: AsyncConnection) -> None:
    """Seed app_user, session, turn, and predicate_schema rows for transactional tests."""
    await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
    await conn.execute(
        """
        INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) VALUES
          ('residence', 'single', 'semantic'),
          ('employer', 'single', 'semantic'),
          ('hobby', 'multi', 'persona');
        INSERT INTO app_user (id, external_ref, created_at) VALUES
          ('u1', 'ext-u1', '2026-01-01T00:00:00Z');
        INSERT INTO session (id, user_id, started_at) VALUES
          ('s1', 'u1', '2026-01-01T00:00:00Z');
        INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) VALUES
          ('t1', 's1', 'u1', 1, 'user', 'I live in Cairo.', '2026-03-10T00:00:00Z'),
          ('t2', 's1', 'u1', 2, 'user', 'I moved to Berlin on June 1.', '2026-06-01T00:00:00Z'),
          ('t3', 's1', 'u1', 3, 'user', 'Actually I moved to Berlin on July 15, not June 1.', '2026-07-20T00:00:00Z'),
          ('t4', 's1', 'u1', 4, 'user', 'Correction: my employer is Acme Corp.', '2026-08-05T00:00:00Z'),
          ('t5', 's1', 'u1', 5, 'user', 'I also play chess and cycling.', '2026-04-01T00:00:00Z');
        """
    )


@pytest.mark.asyncio
async def test_e1_e3_supersession_and_boundary_revision_lifecycle(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        # Step 1 (E1 initial commit at MAR10): User lives in Cairo [MAR10, inf), recorded [MAR10, inf)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            v1_committed, tick1 = await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="v1",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="I live in Cairo."),),
                ),
                schema_name=schema,
                replay_time=MAR10,
            )
            assert v1_committed.recorded_from == MAR10
            assert tick1.instant == MAR10

        # Step 2 (E1 supersession at JUN1): User moves to Berlin [JUN1, inf)
        # Closes v1 at [MAR10, JUN1), re-issues v1_reissued [MAR10, JUN1), inserts v2 [JUN1, inf)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            sup = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="v2",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Berlin",
                    valid_from=JUN1,
                    evidence=(EvidenceInput(turn_id="t2", evidence_span="I moved to Berlin on June 1."),),
                ),
                reissued_version_id="v1_reissued",
                schema_name=schema,
                replay_time=JUN1,
            )
            assert sup.closed_version_ids == ("v1",)
            assert sup.reissued_version_id == "v1_reissued"
            assert sup.inserted_version_id == "v2"
            assert not sup.held_in_conflict_pending

        # Step 3 (E3 boundary revision at JUL20): Transition occurred on JUL15, not JUN1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            rev = await revise_transition_boundary(
                conn,
                user_id="u1",
                prior_version_id="v1_reissued",
                later_version_id="v2",
                revised_boundary=JUL15,
                reissued_prior_version_id="v1_rev2",
                reissued_later_version_id="v2_rev1",
                later_evidence=(
                    EvidenceInput(
                        turn_id="t3",
                        evidence_span="Actually I moved to Berlin on July 15, not June 1.",
                    ),
                ),
                schema_name=schema,
                replay_time=JUL20,
            )
            assert rev.closed_version_ids == ("v1_reissued", "v2")
            assert rev.reissued_prior_version_id == "v1_rev2"
            assert rev.reissued_later_version_id == "v2_rev1"

        # Verify current_state, historical_as_of, and audit_as_of across the E1/E3 history
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await conn.execute("SELECT set_config('app.clock', %s, true)", (OCT1.isoformat(),))

            cur = await fetch_versions(conn, current_state("u1", OCT1), schema_name=schema)
            assert [v.id for v in cur.versions] == ["v2_rev1"]
            assert cur.versions[0].content == "Berlin"

            # Historical query for June 15 as known at OCT1 -> Cairo (v1_rev2 covers [MAR10, JUL15))
            hist_jun = await fetch_versions(
                conn,
                historical_as_of("u1", instant(6, 15), OCT1),
                schema_name=schema,
            )
            assert [v.id for v in hist_jun.versions] == ["v1_rev2"]
            assert hist_jun.versions[0].content == "Cairo"

            # Audit query for June 15 as believed on June 10 (before JUL20 revision) -> Berlin (v2)
            audit_jun = await fetch_versions(
                conn,
                audit_as_of("u1", instant(6, 15), instant(6, 10)),
                schema_name=schema,
            )
            assert [v.id for v in audit_jun.versions] == ["v2"]
            assert audit_jun.versions[0].content == "Berlin"


@pytest.mark.asyncio
async def test_e2_same_instant_correction_and_r2_rule3_conflict_pending(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        # Commit initial employer version at APR1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="emp_v1",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Globex",
                    valid_from=APR1,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="Employer Globex"),),
                ),
                schema_name=schema,
                replay_time=APR1,
            )

        # E2: Same-instant correction (v_old == v_new == APR1) at AUG5 closes emp_v1 without re-issuing empty range
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            e2_out = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="emp_v2",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Acme Corp",
                    valid_from=APR1,
                    evidence=(EvidenceInput(turn_id="t4", evidence_span="Correction: my employer is Acme Corp."),),
                ),
                schema_name=schema,
                replay_time=AUG5,
            )
            assert e2_out.closed_version_ids == ("emp_v1",)
            assert e2_out.reissued_version_id is None
            assert e2_out.inserted_version_id == "emp_v2"

        # R2 Rule 3: Out-of-order earlier valid_from (JAN10 < APR1) routes to conflict_pending
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            r2_hold = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="emp_early_cand",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Initech",
                    valid_from=JAN10,
                    evidence=(EvidenceInput(turn_id="t4", evidence_span="Earlier employer Initech"),),
                ),
                conflict_id="conf-early-1",
                schema_name=schema,
                replay_time=AUG10,
            )
            assert r2_hold.held_in_conflict_pending is True
            assert r2_hold.conflict_id == "conf-early-1"
            assert r2_hold.closed_version_ids == ()

        # R4: Resolve emp_early_cand with decision='accept' at SEP1 -> promotes in-place at SEP1 and closes emp_v2
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            res = await resolve_conflict_pending(
                conn,
                user_id="u1",
                candidate_version_id="emp_early_cand",
                decision="accept",
                reason_code="adjudicated_accept",
                schema_name=schema,
                replay_time=SEP1,
            )
            assert res.resolution_status == "active"
            assert res.closed_version_ids == ("emp_v2",)
            assert res.clock_tick is not None
            assert res.clock_tick.instant == SEP1

            cur = await fetch_versions(conn, current_state("u1", OCT1), schema_name=schema)
            assert [v.id for v in cur.versions] == ["emp_early_cand"]
            assert cur.versions[0].recorded_range is not None
            assert cur.versions[0].recorded_range.lower == SEP1

        # Stage another candidate and resolve with decision='reject'
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            staged = await stage_candidate(
                conn,
                CandidateVersionInput(
                    version_id="emp_reject_cand",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Bogus Corp",
                    valid_from=OCT1,
                ),
                initial_status="conflict_pending",
                schema_name=schema,
            )
            assert staged.status == "conflict_pending"
            assert staged.recorded_from is None

            rej = await resolve_conflict_pending(
                conn,
                user_id="u1",
                candidate_version_id="emp_reject_cand",
                decision="reject",
                reason_code="uncorroborated_claim",
                schema_name=schema,
                replay_time=OCT1,
            )
            assert rej.resolution_status == "rejected"


@pytest.mark.asyncio
async def test_r7_end_multivalued_and_r5_invariant_10_d27_archival(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        # Commit two multi-valued hobbies at APR1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            _, tick = await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="hobby_chess",
                    memory_key="user:hobby:chess",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="hobby",
                    single_valued=False,
                    content="Plays chess",
                    valid_from=APR1,
                    evidence=(EvidenceInput(turn_id="t5", evidence_span="I also play chess and cycling."),),
                ),
                schema_name=schema,
                replay_time=APR1,
            )
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="hobby_cycling",
                    memory_key="user:hobby:cycling",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="hobby",
                    single_valued=False,
                    content="Rides cycling",
                    valid_from=APR1,
                    evidence=(EvidenceInput(turn_id="t5", evidence_span="I also play chess and cycling."),),
                ),
                schema_name=schema,
                clock_tick=tick,
            )

        # R7: End hobby_cycling on JUN1 at recorded time JUN1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            ended = await end_multivalued(
                conn,
                user_id="u1",
                version_id="hobby_cycling",
                valid_end=JUN1,
                truncated_version_id="hobby_cycling_ended",
                schema_name=schema,
                replay_time=JUN1,
            )
            assert ended.closed_version_id == "hobby_cycling"
            assert ended.truncated_version_id == "hobby_cycling_ended"

        # R5 / Invariant 10 / D-27 archival tests (T1, T2, T3, T4, S-preference, and replacement guard):
        # T1: hobby_chess is currently-valid current belief ([APR1, inf), [APR1, inf)) without (C) -> REJECT
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            with pytest.raises(ArchivalPreconditionError, match="archival_precondition_unmet"):
                await archive_version(
                    conn,
                    user_id="u1",
                    version_id="hobby_chess",
                    schema_name=schema,
                    replay_time=JUL15,
                )

        # T2: hobby_cycling_ended is historical current belief ([APR1, JUN1), [JUN1, inf)) without (C) -> REJECT
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            with pytest.raises(ArchivalPreconditionError, match="archival_precondition_unmet"):
                await archive_version(
                    conn,
                    user_id="u1",
                    version_id="hobby_cycling_ended",
                    schema_name=schema,
                    replay_time=JUL15,
                )

        # T3: hobby_cycling has closed recorded_range [APR1, JUN1) -> branch (S) succeeds
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            t3_arch = await archive_version(
                conn,
                user_id="u1",
                version_id="hobby_cycling",
                schema_name=schema,
                replay_time=JUL15,
            )
            assert t3_arch.archival_branch == "S"

        # T4: Create consolidated version hobby_chess_summary covering hobby_chess, then archive -> branch (C)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            _, tick_t4 = await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="hobby_chess_summary",
                    memory_key="user:hobby:chess_summary",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="hobby",
                    single_valued=False,
                    content="Consolidated: plays chess regularly",
                    valid_from=MAR10,  # [MAR10, inf) @> [APR1, inf)
                    derivations=(
                        DerivationInput(
                            source_version_id="hobby_chess",
                            derivation_type="consolidation",
                        ),
                    ),
                ),
                schema_name=schema,
                replay_time=JUL20,
            )
            t4_arch = await archive_version(
                conn,
                user_id="u1",
                version_id="hobby_chess",
                schema_name=schema,
                clock_tick=tick_t4,
            )
            assert t4_arch.archival_branch == "C"

        # D-27 replacement protection: Ending or archiving hobby_chess_summary while it is the sole
        # active covering replacement for (C)-archived hobby_chess (which has open recorded_range) MUST FAIL!
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            with pytest.raises(ArchivalPreconditionError, match="archival_breaks_covered_source_invariant"):
                await end_multivalued(
                    conn,
                    user_id="u1",
                    version_id="hobby_chess_summary",
                    valid_end=SEP1,
                    schema_name=schema,
                    replay_time=AUG10,
                )

        # Also verify direct SQL trigger rejects raw SQL UPDATE trying to archive T2 without (C)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            with pytest.raises(InvalidParameterValue, match="archival_precondition_unmet"):
                await conn.execute(
                    sql.SQL(
                        "UPDATE {}.memory_version SET status = 'archived', archival_branch = 'C' "
                        "WHERE id = 'hobby_cycling_ended'"
                    ).format(sql.Identifier(schema))
                )


@pytest.mark.asyncio
async def test_branch_s_preferred_when_both_s_and_c_hold(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="res_v1",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="I live in Cairo."),),
                ),
                schema_name=schema,
                replay_time=MAR10,
            )
        # Supersede res_v1 at JUN1 AND link res_v2 as a derivation of res_v1 covering [MAR10, inf)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            sup = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="res_v2",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo & Berlin",
                    valid_from=MAR10,
                    derivations=(DerivationInput(source_version_id="res_v1", derivation_type="consolidation"),),
                ),
                schema_name=schema,
                replay_time=JUN1,
            )
            # Now res_v1 satisfies BOTH (S) (closed recorded_range [MAR10, JUN1)) AND (C) (covered by active res_v2).
            # D-27 requires preferring 'S'.
            arch = await archive_version(
                conn,
                user_id="u1",
                version_id="res_v1",
                schema_name=schema,
                clock_tick=sup.clock_tick,
            )
            assert arch.archival_branch == "S"


@pytest.mark.asyncio
async def test_ablation_modes_a1_a2_and_a2b(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        # 1. A1 (P-flat): stores facts with single_valued=False and does not close prior versions
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="a1_v1",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="Cairo"),),
                ),
                schema_name=schema,
                replay_time=MAR10,
                ablation="A1",
            )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            out_a1 = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="a1_v2",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Berlin",
                    valid_from=JUN1,
                    evidence=(EvidenceInput(turn_id="t2", evidence_span="Berlin"),),
                ),
                schema_name=schema,
                replay_time=JUN1,
                ablation="A1",
            )
            assert out_a1.closed_version_ids == ()
            cur_a1 = await fetch_versions(conn, current_state("u1", OCT1), schema_name=schema)
            assert {v.id for v in cur_a1.versions} == {"a1_v1", "a1_v2"}

        # Clean up A1 rows before testing A2
        async with conn.transaction():
            await conn.execute(
                sql.SQL("DELETE FROM {}.memory_version WHERE id IN ('a1_v1', 'a1_v2')").format(sql.Identifier(schema))
            )

        # 2. A2 (P-nohistory): destructive overwrite deletes superseded version without re-issuing prior range
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="a2_v1",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Globex",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="Globex"),),
                ),
                schema_name=schema,
                replay_time=JUL15,
                ablation="A2",
            )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            out_a2 = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="a2_v2",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Acme Corp",
                    valid_from=JUN1,
                    evidence=(EvidenceInput(turn_id="t4", evidence_span="Acme Corp"),),
                ),
                schema_name=schema,
                replay_time=JUL20,
                ablation="A2",
            )
            assert out_a2.deleted_version_ids == ("a2_v1",)
            assert out_a2.reissued_version_id is None
            hist_a2 = await fetch_versions(conn, historical_as_of("u1", APR1, OCT1), schema_name=schema)
            assert hist_a2.versions == ()

        # 3. A2b (D-15 with allow_a2b=True): keeps valid-time history, deletes closed recorded history
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="a2b_v1",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="Cairo"),),
                ),
                schema_name=schema,
                replay_time=AUG5,
                ablation="A2b",
                allow_a2b=True,
            )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            out_a2b = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="a2b_v2",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Berlin",
                    valid_from=JUN1,
                    evidence=(EvidenceInput(turn_id="t2", evidence_span="Berlin"),),
                ),
                reissued_version_id="a2b_v1_reissued",
                schema_name=schema,
                replay_time=AUG10,
                ablation="A2b",
                allow_a2b=True,
            )
            assert out_a2b.deleted_version_ids == ("a2b_v1",)
            assert out_a2b.reissued_version_id == "a2b_v1_reissued"
            hist_a2b = await fetch_versions(conn, historical_as_of("u1", APR1, OCT1), schema_name=schema)
            assert [v.id for v in hist_a2b.versions] == ["a2b_v1_reissued"]


@pytest.mark.asyncio
async def test_multi_interval_span_conflict_and_provenance_enforcement(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    async with connect(database) as conn:
        # 1. Commit v1 [MAR10, inf) at MAR10, then supersede with v2 [JUN1, inf) at JUN1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="span_v1",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Cairo",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t1", evidence_span="Cairo"),),
                ),
                schema_name=schema,
                replay_time=MAR10,
            )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="span_v2",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Berlin",
                    valid_from=JUN1,
                    evidence=(EvidenceInput(turn_id="t2", evidence_span="Berlin"),),
                ),
                reissued_version_id="span_v1_reissued",
                schema_name=schema,
                replay_time=JUN1,
            )

        # 2. Out-of-order claim at AUG5 with valid_from=APR1 (< JUN1) overlaps BOTH span_v1_reissued and span_v2;
        # R2 Rule 3 routes it cleanly to conflict_pending against the latest active version (span_v2)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            hold_out = await supersede_single(
                conn,
                CandidateVersionInput(
                    version_id="span_v3_apr",
                    memory_key="user:residence",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="residence",
                    single_valued=True,
                    content="Munich",
                    valid_from=APR1,
                    evidence=(EvidenceInput(turn_id="t3", evidence_span="Munich since April"),),
                ),
                schema_name=schema,
                replay_time=AUG5,
            )
            assert hold_out.held_in_conflict_pending is True

        # 3. Accepting span_v3_apr at SEP1 closes both span_v1_reissued and span_v2, re-issues [MAR10, APR1),
        # and activates span_v3_apr [APR1, inf) at [SEP1, inf)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            res = await resolve_conflict_pending(
                conn,
                user_id="u1",
                candidate_version_id="span_v3_apr",
                decision="accept",
                reason_code="verified_april_move",
                reissued_version_id="span_v1_mar_to_apr",
                schema_name=schema,
                replay_time=SEP1,
            )
            assert res.closed_version_ids == ("span_v1_reissued", "span_v2")
            assert res.reissued_version_id == "span_v1_mar_to_apr"

        # 4. Staged candidate without provenance cannot be accepted unless additional_evidence is supplied
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await stage_candidate(
                conn,
                CandidateVersionInput(
                    version_id="no_prov_cand",
                    memory_key="user:employer",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="employer",
                    single_valued=True,
                    content="Unverified Employer",
                    valid_from=OCT1,
                ),
                initial_status="candidate",
                schema_name=schema,
            )
            with pytest.raises(TransactionError, match="active_or_archived_version_requires_provenance"):
                await resolve_conflict_pending(
                    conn,
                    user_id="u1",
                    candidate_version_id="no_prov_cand",
                    decision="accept",
                    reason_code="try_accept_without_provenance",
                    schema_name=schema,
                    replay_time=OCT1,
                )


@pytest.mark.asyncio
async def test_non_superuser_rls_tenant_isolation_for_transactions(
    database: tuple[DatabaseConfig, str],
) -> None:
    _, schema = database
    role_name = f"cme_rls_{uuid4().hex[:12]}"
    async with connect(database) as conn:
        # Create user u2 and u1's active memory version under superuser setup
        await conn.execute("SELECT set_config('app.user_id', 'u2', false)")
        await conn.execute(
            "INSERT INTO app_user (id, external_ref, created_at) VALUES ('u2', 'ext-u2', '2026-01-01T00:00:00Z')"
        )
        await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await commit_initial_version(
                conn,
                CandidateVersionInput(
                    version_id="u1_secret_hobby",
                    memory_key="user:hobby:secret",
                    user_id="u1",
                    subject_key="user",
                    predicate_key="hobby",
                    single_valued=False,
                    content="Secret hobby",
                    valid_from=MAR10,
                    evidence=(EvidenceInput(turn_id="t5", evidence_span="Secret hobby"),),
                ),
                schema_name=schema,
                replay_time=MAR10,
            )

        await conn.execute(
            sql.SQL(
                "CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS; "
                "GRANT USAGE ON SCHEMA {schema} TO {role}; "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role};"
            ).format(role=sql.Identifier(role_name), schema=sql.Identifier(schema))
        )
        try:
            async with conn.transaction():
                await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role_name)))
                await conn.execute("SELECT set_config('app.user_id', 'u2', true)")
                # Cross-tenant guard rejects user_id='u1' when app.user_id='u2'
                with pytest.raises(InsufficientPrivilege, match="transaction_user_context_mismatch"):
                    await end_multivalued(
                        conn,
                        user_id="u1",
                        version_id="u1_secret_hobby",
                        valid_end=JUN1,
                        schema_name=schema,
                        replay_time=JUN1,
                    )
                # And attempting to end u1's version under user_id='u2' cannot see u1's row due to FORCE RLS
                with pytest.raises(TransactionError, match="multivalued_version_not_found"):
                    await end_multivalued(
                        conn,
                        user_id="u2",
                        version_id="u1_secret_hobby",
                        valid_end=JUN1,
                        schema_name=schema,
                        replay_time=JUN1,
                    )
        finally:
            await conn.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema} FROM {role}; "
                    "REVOKE USAGE ON SCHEMA {schema} FROM {role}; "
                    "DROP ROLE {role};"
                ).format(role=sql.Identifier(role_name), schema=sql.Identifier(schema))
            )


@pytest.mark.asyncio
async def test_archival_guard_verification_fails_closed_when_triggers_are_absent(database) -> None:
    """A schema without the shipped triggers must not silently run unguarded."""
    _, schema = database
    async with connect(database) as conn:
        before = await (
            await conn.execute("SELECT to_regclass(%s)", (f"{schema}.trg_memory_version_archival_invariant",))
        ).fetchone()
        assert before is not None, "triggers are installed by the schema itself"
        await ensure_archival_trigger(conn, schema_name=schema)
        await conn.execute("DROP TRIGGER trg_memory_version_archival_invariant ON memory_version")
        await conn.execute("DROP FUNCTION enforce_archival_invariant()")
        with pytest.raises(TransactionError, match="archival_guards_not_installed"):
            await ensure_archival_trigger(conn, schema_name=schema)
