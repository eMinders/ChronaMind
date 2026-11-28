"""DB integration tests for I15 derived abstraction ledger, refutation probes, and stats fixes (#245)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation

from custom_memory_engine.api.runtime import DbTargets, Receipt, Rejected
from custom_memory_engine.clock import allocate_clock
from custom_memory_engine.engine.abstraction_ledger import (
    DerivedLedger,
    LedgerError,
    derive_ledger,
    ledger_confidence_lower,
)
from custom_memory_engine.engine.artifact_provenance import record_artifact_provenance
from custom_memory_engine.engine.refutation import run_refutation_probes
from custom_memory_engine.engine.transactions import archive_version
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.soundness.admission import Ledger as SoundnessLedger
from custom_memory_engine.soundness.admission import assess as soundness_assess
from tests.integration.test_api_wiring_lifecycle_db import ArtifactStats, record, world
from tests.integration.test_api_wiring_structure import database, rows  # noqa: F401
from tests.integration.test_infer_db import agent, infer_args, method
from tests.integration.test_read_path_db import episode
from validation.cp_bounds import cp_upper

pytestmark = pytest.mark.integration

T1 = datetime(2026, 2, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)


async def _insert_version(
    conn,
    version_id: str,
    *,
    user: str = "alice",
    scope_path: str = "/u/alice/p/chrona/",
    status: str = "active",
    lifecycle: str = "observed",
    assertion_mode: str = "asserted",
    single_valued: bool = False,
    subject_key: str = "self",
    predicate_key: str = "statement",
    semantic_slot: str | None = None,
    content: str = "observation text",
    valid_from: datetime = T1,
    valid_until: datetime | None = None,
    layer: str = "episodic",
    kind: str = "episode",
) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        recorded_sql = "NULL" if status == "conflict_pending" else "tstzrange(clock_now(), NULL)"
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, semantic_slot, single_valued, content, event_range, recorded_range, layer, "
            "scope_path, visibility, retention, assertion_mode, created_by, created_tier) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, tstzrange(%s, %s), {recorded_sql}, "
            "%s, %s, 'private', 'durable', %s, %s, 'B')",
            (
                version_id,
                f"mk-{version_id}",
                user,
                status,
                lifecycle,
                kind,
                subject_key,
                predicate_key,
                semantic_slot,
                single_valued,
                content,
                valid_from,
                valid_until,
                layer,
                scope_path,
                assertion_mode,
                user,
            ),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'span', 'support')",
            (version_id, f"t-{user}", user),
        )
        if lifecycle in ("asserted", "contested"):
            decision = "accept" if lifecycle == "asserted" else "hold"
            await conn.execute(
                "INSERT INTO memory_conflict (id, candidate_version_id, user_id, relation, decision, "
                "reason_code, decided_by, decided_at) "
                "VALUES (%s, %s, %s, 'unrelated', %s, 'gate_check', 'gate', clock_now())",
                (f"mc-{version_id}", version_id, user, decision),
            )


async def test_derive_ledger_is_deterministic_and_classifies_mixed_evidence(database) -> None:  # noqa: F811
    async with world(database) as (conn, _engine, schema):
        await _insert_version(conn, "v-sup-1")
        await _insert_version(conn, "v-sup-2", lifecycle="asserted", layer="semantic", kind="claim")
        await _insert_version(conn, "v-cnt-src")
        await _insert_version(conn, "v-ext-counter")
        await _insert_version(conn, "v-hedged", assertion_mode="hedged")
        await _insert_version(
            conn, "v-contested", status="conflict_pending", lifecycle="contested", layer="semantic", kind="claim"
        )
        await _insert_version(conn, "v-retired")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            tick = await allocate_clock(conn, "alice", schema_name=schema)
            await conn.execute(
                "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
                "WHERE id = 'v-retired' AND user_id = 'alice'"
            )
            await archive_version(
                conn,
                user_id="alice",
                version_id="v-retired",
                schema_name=schema,
                clock_tick=tick,
            )

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            assoc_id = f"assoc-{uuid4().hex}"
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, weight, created_by, origin) "
                "VALUES (%s, 'alice', 'v-ext-counter', 'v-cnt-src', 'contradicts', 1.0, 'alice', 'tier_c')",
                (assoc_id,),
            )
            await record_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id=assoc_id,
                origin_kind="evidence_version",
                source_version_ids=("v-ext-counter",),
                created_by="alice",
                schema_name=schema,
            )

        sources = ["v-sup-1", "v-sup-2", "v-cnt-src", "v-hedged", "v-contested", "v-retired"]
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            first = await derive_ledger(
                conn,
                "alice",
                sources,
                statement="test statement",
                scope_path="/u/alice/p/chrona/",
                schema_name=schema,
            )
            second = await derive_ledger(
                conn,
                "alice",
                list(reversed(sources)),
                statement="test statement",
                scope_path="/u/alice/p/chrona/",
                schema_name=schema,
            )

        assert isinstance(first, DerivedLedger)
        assert first == second
        assert (first.support, first.counter, first.undetermined, first.opportunity) == (2, 1, 3, 6)
        assert first.support_ids == ("v-sup-1", "v-sup-2")
        assert first.counter_ids == ("v-cnt-src",)
        assert first.undetermined_ids == ("v-contested", "v-hedged", "v-retired")
        assert first.derivation_sources == tuple(sorted([*sources, "v-ext-counter"]))

        lower = ledger_confidence_lower(first.support, first.counter, first.undetermined)
        assert lower == pytest.approx(1.0 - cp_upper(4, 6), abs=1e-9)
        adm = soundness_assess(
            SoundnessLedger(first.support, first.counter, first.undetermined),
            refutation_attempts=3,
            negative_control_clean=True,
        )
        assert lower == pytest.approx(adm.confidence_lower, abs=1e-9)


async def test_derive_ledger_detects_single_valued_slot_conflicts_and_refuses_contradictions(database) -> None:  # noqa: F811
    async with world(database) as (conn, _engine, schema):
        # Non-overlapping windows permit two active single-valued versions in the same semantic_slot.
        await _insert_version(
            conn,
            "v-slot-a",
            lifecycle="asserted",
            layer="semantic",
            kind="claim",
            single_valued=True,
            semantic_slot="preferred_editor",
            content="neovim",
            valid_from=T1,
            valid_until=T2,
        )
        await _insert_version(
            conn,
            "v-slot-b",
            lifecycle="asserted",
            layer="semantic",
            kind="claim",
            single_valued=True,
            semantic_slot="preferred_editor",
            content="emacs",
            valid_from=T2,
            valid_until=T2 + timedelta(days=30),
        )
        await _insert_version(conn, "v-good")

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            # Using v-slot-a with v-good sees external slot conflict v-slot-b -> counter=1, support=1.
            mixed = await derive_ledger(
                conn,
                "alice",
                ["v-slot-a", "v-good"],
                statement="alice editor",
                scope_path="/u/alice/p/chrona/",
                schema_name=schema,
            )
            assert (mixed.support, mixed.counter, mixed.undetermined, mixed.opportunity) == (1, 1, 0, 2)
            assert mixed.counter_ids == ("v-slot-a",)
            assert "v-slot-b" in mixed.derivation_sources

            # Passing conflicting sources together triggers direct internal contradiction refusal.
            with pytest.raises(LedgerError) as exc_internal:
                await derive_ledger(
                    conn,
                    "alice",
                    ["v-slot-a", "v-slot-b", "v-good"],
                    statement="alice editor",
                    scope_path="/u/alice/p/chrona/",
                    schema_name=schema,
                )
            assert exc_internal.value.code == "contradictory_derivation"

            # Only counter (support == 0 and counter > 0) refuses with contradictory_derivation.
            with pytest.raises(LedgerError) as exc_counter_only:
                await derive_ledger(
                    conn,
                    "alice",
                    ["v-slot-a"],
                    statement="alice editor",
                    scope_path="/u/alice/p/chrona/",
                    schema_name=schema,
                )
            assert exc_counter_only.value.code == "contradictory_derivation"

        # Verify SQL CHECK constraint rejects 'counter' in memory_derivation.
        await _insert_version(conn, "v-target")
        with pytest.raises(CheckViolation):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute("INSERT INTO memory_derivation VALUES ('v-target', 'v-good', 'alice', 'counter')")


async def test_derive_ledger_refuses_empty_or_ungrounded_only_evidence(database) -> None:  # noqa: F811
    async with world(database) as (conn, _engine, schema):
        await _insert_version(conn, "v-hedged", assertion_mode="hedged")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            with pytest.raises(LedgerError) as empty_exc:
                await derive_ledger(
                    conn, "alice", [], statement="empty", scope_path="/u/alice/p/chrona/", schema_name=schema
                )
            assert empty_exc.value.code == "insufficient_evidence"

            with pytest.raises(LedgerError) as no_sup_exc:
                await derive_ledger(
                    conn,
                    "alice",
                    ["v-hedged"],
                    statement="hedged only",
                    scope_path="/u/alice/p/chrona/",
                    schema_name=schema,
                )
            assert no_sup_exc.value.code == "no_supporting_evidence"

            # Unreadable source filtered out by read_set gives opportunity == 0 -> insufficient_evidence.
            narrow_read = resolve_read_set("alice", [Selector.of("/u/alice/")], [])
            await _insert_version(conn, "v-priv", scope_path="/u/alice/p/chrona/")
            with pytest.raises(LedgerError) as hidden_exc:
                await derive_ledger(
                    conn,
                    "alice",
                    ["v-priv"],
                    statement="hidden",
                    scope_path="/u/alice/p/chrona/",
                    read_set=narrow_read,
                    schema_name=schema,
                )
            assert hidden_exc.value.code == "insufficient_evidence"


async def test_refutation_probes_and_stats_counter_ids_and_end_to_end_verification(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, schema):
        assert isinstance(engine.resolver, DbTargets)
        # Ingest 36 observed episodes so derived abstraction satisfies >= 36 admission threshold.
        sources: list[str] = []
        for idx in range(36):
            ep = await engine.call(
                agent("alice"),
                "ingest_episode",
                episode("/u/alice/p/chrona/", f"k-{idx}", f"pytest failure observation {idx}"),
            )
            assert isinstance(ep, Receipt)
            sources.append(ep.id)

        ref = await method(engine)
        # Omit ledger: derived automatically (36, 0, 0, 36) and probed (>= 3 attempts).
        inferred = await engine.call(agent("alice"), "infer", infer_args(ref, sources, ledger=None))
        assert isinstance(inferred, Receipt) and inferred.effect == "inferred"

        # Mismatched ledger must be rejected cleanly.
        bad = await engine.call(
            agent("alice"),
            "infer",
            infer_args(
                ref,
                sources,
                ledger={"support": 1, "counter": 0, "undetermined": 0, "opportunity": 1},
                idempotency_key="bad-ledger",
            ),
        )
        assert bad == Rejected("ledger_mismatch")

        # Stats reads back derived counts, refutation_attempts >= 3, and non-empty derivation_sources.
        stats = await engine.call(agent("alice", "C"), "stats", {"artifact_id": inferred.id})
        assert isinstance(stats, ArtifactStats)
        assert (stats.support_count, stats.counter_count, stats.undetermined_count, stats.opportunity_count) == (
            36,
            0,
            0,
            36,
        )
        assert stats.refutation_attempts >= 3
        assert stats.counter_ids == ()
        assert stats.derivation_sources == tuple(sorted(sources))
        assert stats.closed_over_hash is not None

        # Held-out verification pins support + held_out artifacts and passes soundness bound check.
        held_out = await engine.call(
            agent("alice"),
            "ingest_episode",
            episode("/u/alice/p/chrona/", "held-1", "held out observation"),
        )
        assert isinstance(held_out, Receipt)
        ver_payload = record(
            support_artifact_ids=sources[:3],
            held_out_artifact_ids=[held_out.id],
        )
        verified = await engine.call(
            agent("alice", "B"),
            "verify",
            {"artifact_id": inferred.id, "verification": ver_payload},
        )
        assert isinstance(verified, Receipt) and verified.effect == "verified"

        verified_stats = await engine.call(agent("alice", "C"), "stats", {"artifact_id": inferred.id})
        assert isinstance(verified_stats, ArtifactStats)
        assert verified_stats.verification == "verified"
        assert verified_stats.confidence is not None and verified_stats.confidence >= 0.90
        assert verified_stats.artifact_pinning_status == "pinned"
        assert verified_stats.pinning_label == "pinned"
        assert verified_stats.verified_support_ids == tuple(sorted(sources[:3]))
        assert verified_stats.verified_held_out_ids == (held_out.id,)

        # Refute links counter evidence with M29 provenance, and stats.counter_ids never includes self.
        counter_ep = await engine.call(
            agent("alice"),
            "ingest_episode",
            episode("/u/alice/p/chrona/", "counter-1", "counter observation"),
        )
        assert isinstance(counter_ep, Receipt)
        second_inferred = await engine.call(
            agent("alice"),
            "infer",
            infer_args(ref, [sources[0]], ledger=None, idempotency_key="second-inf"),
        )
        assert isinstance(second_inferred, Receipt)
        refuted = await engine.call(
            agent("alice", "B"),
            "refute",
            {
                "artifact_id": second_inferred.id,
                "reason": "counter_observed",
                "counter_evidence": [{"type": "turn", "ref": counter_ep.id}],
            },
        )
        assert isinstance(refuted, Receipt) and refuted.effect == "retired"

        refuted_stats = await engine.call(agent("alice", "C"), "stats", {"artifact_id": second_inferred.id})
        assert isinstance(refuted_stats, ArtifactStats)
        assert refuted_stats.counter_ids == (counter_ep.id,)
        assert second_inferred.id not in refuted_stats.counter_ids
        assert counter_ep.id in refuted_stats.derivation_sources

        # Active refutation probe detects contradiction targeting `second_inferred.id`.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            probe_rep = await run_refutation_probes(
                conn,
                "alice",
                second_inferred.id,
                [sources[0]],
                scope_path="/u/alice/p/chrona/",
                schema_name=schema,
            )
        assert probe_rep.attempts >= 3
        assert probe_rep.counter_ids == (counter_ep.id,)
        assert probe_rep.negative_control_clean is False

        # M29 provenance row exists for the created `contradicts` association.
        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance WHERE user_id = 'alice' AND artifact_kind = 'association'",
        ) == [(1,)]
