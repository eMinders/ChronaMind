"""Integration tests for Migration 31 verification artifact pinning and verification admission (#246)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import LiteralString, cast

import pytest
from psycopg import sql
from psycopg.errors import RaiseException

from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.engine.staleness import ClosureCache
from custom_memory_engine.engine.statistics import ArtifactStats, read_stats
from custom_memory_engine.engine.verification import (
    SplitWindow,
    VerificationError,
    VerificationRecord,
    assess_split,
)
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import ROLLBACK_DIR, rollback_sql
from tests.integration.test_api_wiring_lifecycle_db import T0, grounded, inferred, record, world
from tests.integration.test_api_wiring_structure import caller, database, forbidden, rows  # noqa: F401
from tests.integration.test_migration_m1 import connect

pytestmark = pytest.mark.integration


def test_assess_split_enforces_pinned_artifacts_and_disjointness() -> None:
    win_a = SplitWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
    win_b = SplitWindow(datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))
    base = dict(
        held_out_split_id="sp-1",
        negative_control_slice_id="ctrl-1",
        support_window=win_a,
        held_out_window=win_b,
        support_context_key="ctx-a",
        held_out_context_key="ctx-b",
        outcome="verified",
    )

    assert assess_split(VerificationRecord(**base, support_artifact_ids=(), held_out_artifact_ids=("h1",))) == (  # type: ignore[arg-type]
        "support_artifacts_required"
    )
    assert assess_split(VerificationRecord(**base, support_artifact_ids=("s1",), held_out_artifact_ids=())) == (  # type: ignore[arg-type]
        "held_out_artifacts_required"
    )
    assert (
        assess_split(
            VerificationRecord(**base, support_artifact_ids=("s1", "x"), held_out_artifact_ids=("h1", "x"))  # type: ignore[arg-type]
        )
        == "split_artifacts_overlap"
    )
    assert (
        assess_split(VerificationRecord(**base, support_artifact_ids=("s1",), held_out_artifact_ids=("h1",))) is None  # type: ignore[arg-type]
    )
    with pytest.raises(VerificationError, match="support_artifacts_duplicate"):
        VerificationRecord(**base, support_artifact_ids=("s1", "s1"), held_out_artifact_ids=("h1",))  # type: ignore[arg-type]


async def test_m31_triggers_enforce_pinned_artifacts_append_only_and_rls(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        role = engine.resolver.runtime.role
        await inferred(conn, "v-trig")

        # Default artifact_pinning_status='pinned' fails at COMMIT without verification_artifact rows.
        with pytest.raises(RaiseException, match="verification_support_artifacts_required"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                    "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                    "held_out_context_key, outcome) VALUES ('ver-miss-sup', 'v-trig', 'alice', 'sp-miss-sup', "
                    "clock_now(), 'ctrl-1', tstzrange('2026-01-01','2026-02-01','[)'), "
                    "tstzrange('2026-03-01','2026-04-01','[)'), 'ctx-a', 'ctx-b', 'verified')"
                )

        with pytest.raises(RaiseException, match="verification_held_out_artifacts_required"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                    "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                    "held_out_context_key, outcome) VALUES ('ver-miss-held', 'v-trig', 'alice', 'sp-miss-held', "
                    "clock_now(), 'ctrl-1', tstzrange('2026-01-01','2026-02-01','[)'), "
                    "tstzrange('2026-03-01','2026-04-01','[)'), 'ctx-a', 'ctx-b', 'verified')"
                )
                await conn.execute(
                    "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) "
                    "VALUES ('ver-miss-held', 'alice', 'support', 'src-v-trig')"
                )

        # Complete pinned verification succeeds and verification_artifact stays append-only.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome) VALUES ('ver-ok', 'v-trig', 'alice', 'sp-ok', "
                "clock_now(), 'ctrl-1', tstzrange('2026-01-01','2026-02-01','[)'), "
                "tstzrange('2026-03-01','2026-04-01','[)'), 'ctx-a', 'ctx-b', 'verified')"
            )
            await conn.execute(
                "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) VALUES "
                "('ver-ok', 'alice', 'support', 'src-v-trig'), ('ver-ok', 'alice', 'held_out', 'held-v-trig')"
            )

        with pytest.raises(RaiseException, match="verification_is_append_only"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "UPDATE verification_artifact SET role = 'held_out' "
                    "WHERE verification_id = 'ver-ok' AND artifact_id = 'src-v-trig'"
                )

        with pytest.raises(RaiseException, match="verification_is_append_only"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "DELETE FROM verification_artifact WHERE verification_id = 'ver-ok' AND artifact_id = 'src-v-trig'"
                )

        # Cross-tenant RLS isolation under the runtime role hides Alice's pinned artifacts from Bob.
        async with conn.transaction():
            await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            await conn.execute("SELECT set_config('app.user_id', 'bob', true)")
            cur = await conn.execute("SELECT artifact_id FROM verification_artifact WHERE verification_id = 'ver-ok'")
            assert await cur.fetchall() == []


async def test_verify_api_pins_artifacts_and_enforces_admission_and_t11_readability(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _schema):
        await inferred(conn, "v-pin")
        await grounded(conn, "v-bob-secret", user="bob")

        # Empty or overlapping artifact lists are refused before any write.
        for payload, expected_code in (
            (record("v-pin", support_artifact_ids=[]), "support_artifacts_required"),
            (record("v-pin", held_out_artifact_ids=[]), "held_out_artifacts_required"),
            (
                record("v-pin", support_artifact_ids=["src-v-pin"], held_out_artifact_ids=["src-v-pin"]),
                "split_artifacts_overlap",
            ),
        ):
            refused = await forbidden(
                engine, caller("alice", "B"), "verify", {"artifact_id": "v-pin", "verification": payload}
            )
            assert (refused.code, refused.detail) == ("precondition_failed", expected_code)

        # Absent and cross-tenant unreadable artifact ids both answer identically as forbidden (T-11).
        for unreadable_id in ("missing-artifact", "v-bob-secret"):
            refused = await forbidden(
                engine,
                caller("alice", "B"),
                "verify",
                {"artifact_id": "v-pin", "verification": record("v-pin", held_out_artifact_ids=[unreadable_id])},
            )
            assert refused.code == "forbidden"

        # Below admission threshold (support=10, refutation_attempts=3) refuses outcome='verified'.
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 10, counter_count = 0, undetermined_count = 0, "
            "opportunity_count = 10, refutation_attempts = 3 WHERE version_id = 'v-pin'"
        )
        unmet = await forbidden(
            engine, caller("alice", "B"), "verify", {"artifact_id": "v-pin", "verification": record("v-pin")}
        )
        assert (unmet.code, unmet.detail) == ("precondition_failed", "admission_bound_unmet")

        # Negative control not clean also refuses outcome='verified'.
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 36, counter_count = 0, undetermined_count = 0, "
            "opportunity_count = 36, refutation_attempts = 3 WHERE version_id = 'v-pin'"
        )
        dirty = await forbidden(
            engine,
            caller("alice", "B"),
            "verify",
            {"artifact_id": "v-pin", "verification": record("v-pin", negative_control_clean=False)},
        )
        assert (dirty.code, dirty.detail) == ("precondition_failed", "admission_bound_unmet")

        # Refuted outcome is still accepted when admission fails, and pins artifacts.
        await inferred(conn, "v-ref")
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 10, counter_count = 0, undetermined_count = 0, "
            "opportunity_count = 10, refutation_attempts = 3 WHERE version_id = 'v-ref'"
        )
        refuted_receipt = await engine.call(
            caller("alice", "B"),
            "verify",
            {"artifact_id": "v-ref", "verification": record("v-ref", outcome="refuted")},
        )
        assert isinstance(refuted_receipt, Receipt) and refuted_receipt.effect == "refuted"

        # Admissible verified outcome succeeds and populates stats, pinning rows, and audit details.
        ver_receipt = await engine.call(
            caller("alice", "B"),
            "verify",
            {"artifact_id": "v-pin", "verification": record("v-pin")},
        )
        assert isinstance(ver_receipt, Receipt) and ver_receipt.effect == "verified"

        stats = await engine.call(caller("alice", "C"), "stats", {"artifact_id": "v-pin"})
        assert isinstance(stats, ArtifactStats)
        assert stats.artifact_pinning_status == "pinned"
        assert stats.pinning_label == "pinned"
        assert stats.verified_support_ids == ("src-v-pin",)
        assert stats.verified_held_out_ids == ("held-v-pin",)
        assert stats.confidence is not None and stats.confidence >= 0.90
        assert stats.closed_over_hash is not None

        assert await rows(
            conn,
            "SELECT details->>'reason_code', details->>'pinning_status' FROM audit_event "
            "WHERE event_type = 'verification_recorded' AND memory_ref = 'v-pin'",
        ) == [("verified", "pinned")]


async def test_m31_upgrade_marks_legacy_rows_unpinned_and_rollback_restores_v30(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))

    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=30)), prepare=False
        )
        catalog_v30 = await runner._catalog(conn)

        # Seed a legacy v30 verification record before applying M31+.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u-m31', true)")
            await conn.execute(
                "INSERT INTO app_user (id, external_ref, created_at) VALUES ('u-m31', 'ext-m31', clock_now())"
            )
            await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-m31', 'u-m31', clock_now())")
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES ('t-m31', 's-m31', 'u-m31', 1, 'user', 'hi', clock_now())"
            )
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
                "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility) "
                "VALUES ('ep-m31-sup', 'ep-m31-sup', 'u-m31', 'active', 'observed', 'episode', 'self', 'statement', "
                "false, 'obs', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'episodic', '/u/u-m31/', 'private')",
                (T0,),
            )
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES ('ep-m31-sup', 't-m31', 'u-m31', 'hi', 'support')"
            )
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
                "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility) "
                "VALUES ('abs-legacy', 'abs-legacy', 'u-m31', 'active', 'inferred', 'abstraction', 'self', "
                "'statement', false, 'legacy claim', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
                "'persona', '/u/u-m31/', 'private')",
                (T0,),
            )
            await conn.execute(
                "INSERT INTO memory_derivation VALUES ('abs-legacy', 'ep-m31-sup', 'u-m31', 'consolidation')"
            )
            await conn.execute(
                "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome) VALUES ('ver-legacy', 'abs-legacy', 'u-m31', 'sp-leg', "
                "clock_now(), 'ctrl-leg', tstzrange('2026-01-01','2026-02-01','[)'), "
                "tstzrange('2026-03-01','2026-04-01','[)'), 'ctx-1', 'ctx-2', 'verified')"
            )
            await conn.execute(
                "UPDATE abstraction_stats SET verification_status = 'verified' WHERE version_id = 'abs-legacy'"
            )

    await runner.apply()

    async with connect(database) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u-m31', true)")
            legacy_stats = await read_stats(
                conn,
                user_id="u-m31",
                artifact_id="abs-legacy",
                cache=ClosureCache(),
                schema_name=schema,
            )
        assert legacy_stats.artifact_pinning_status == "unpinned_pre_migration"
        assert legacy_stats.pinning_label == "unpinned (pre-migration)"
        assert legacy_stats.verified_support_ids == ()
        assert legacy_stats.verified_held_out_ids == ()

        # Add one pinned M31 verification_artifact so rollback guard is exercised.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u-m31', true)")
            await conn.execute(
                "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                "held_out_context_key, outcome, artifact_pinning_status) "
                "VALUES ('ver-pinned-u1', 'abs-legacy', 'u-m31', 'sp-pin', clock_now(), 'ctrl-pin', "
                "tstzrange('2026-01-01','2026-02-01','[)'), tstzrange('2026-03-01','2026-04-01','[)'), "
                "'ctx-a', 'ctx-b', 'verified', 'pinned')"
            )
            await conn.execute(
                "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) VALUES "
                "('ver-pinned-u1', 'u-m31', 'support', 'ep-m31-sup'), "
                "('ver-pinned-u1', 'u-m31', 'held_out', 'ep-m31-held')"
            )

        if migrate.VERSION > 31:
            await conn.execute(
                cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=31)), prepare=False
            )

        m31_rollback = (ROLLBACK_DIR / dict(migrate.MIGRATIONS)[31]).read_text(encoding="utf-8")
        with pytest.raises(RaiseException, match="m31_rollback_refused_verification_artifacts_exist"):
            await conn.execute(cast(LiteralString, m31_rollback), prepare=False)

        async with conn.transaction():
            await conn.execute("SET LOCAL cme.rollback_discard = 'verification_artifacts'")
            await conn.execute(cast(LiteralString, m31_rollback), prepare=False)

        assert await runner._catalog(conn) == catalog_v30

    await runner.apply()
