"""E-037 capstone integration suite: deletion through cognition (``DELETION_ORDER.md`` F1–F13, 11 channels, T-29/S-08).

Every falsification test F1–F13 is present by the exact name specified in ``PLAN/V2/DELETION_ORDER.md`` §5 and §5.1,
alongside the 11-channel scan, the ``A8`` abstraction positive control, the channel-7 log-sink positive control
(§4.4 option (a)), ``T-29`` keyed-digest receipts, ``S-08`` exclusive-evidence deletion, ``E-038-F2`` purge-token
invalidation, ``delete`` wired through ``Engine.call``, and migration-22 rollback fingerprint verification.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import LiteralString, cast
from uuid import uuid4

import pytest
import pytest_asyncio
import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, RaiseException

from custom_memory_engine.api import delete_handlers
from custom_memory_engine.api.delete_handlers import register_delete_handlers
from custom_memory_engine.api.entry import CallerContext, Credential, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.maintain_handlers import register_maintain_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.deletion.closure import (
    MAX_LINEAGE_DEPTH,
    ExportSnapshot,
    ExportSnapshotStore,
    StaleDeletionEpoch,
    guard_verification_commit,
)
from custom_memory_engine.deletion.rederive import ActiveSource, RebuiltSummary, ReDerivationError
from custom_memory_engine.deletion.scanner import (
    ALL_CHANNELS,
    ChannelProbe,
    LeakChannel,
    ScanContext,
    scan_deleted_key,
)
from custom_memory_engine.engine.channels import stale_of
from custom_memory_engine.engine.deletion import (
    RECEIPT_DIGEST_PREFIX,
    RECEIPT_KEY_ENV,
    DeletionError,
    DeletionReceipt,
    default_receipt_key,
    delete_memory,
    deletion_epoch,
    target_ref_digest,
)
from custom_memory_engine.engine.staleness import ClosureCache, compute_closure, staleness_inputs
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from evaluation.harness.leak_scanner import (
    abstraction_bounds_probe,
    answer_probe,
    assembled_context_probe,
    export_snapshot_probe,
    external_cache_probe,
    hypothesis_rendering_probe,
    log_sink_probe,
    vector_probe,
    verdict,
)
from tests.integration.rollbacks import rollback_sql
from tests.integration.schema import engine_database
from tests.integration.test_api_wiring_structure import (
    caller,
    world,
)
from tests.integration.test_api_wiring_structure import (
    database as wired_database,  # noqa: F401
)
from tests.integration.test_read_path_db import CODING_PROFILE

pytestmark = pytest.mark.integration

START = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)
VERIFIED_AT = datetime(2026, 6, 10, 9, 0, 0, tzinfo=UTC)
EXPIRES_AT = datetime(2026, 12, 1, 9, 0, 0, tzinfo=UTC)
MODEL = "BAAI/bge-small-en-v1.5"
UNIT_VEC = tuple(0.5 if idx == 0 else 0.0 for idx in range(384))
VEC_LIT = "[" + ",".join(repr(float(v)) for v in UNIT_VEC) + "]"


@asynccontextmanager
async def connect(db: tuple[DatabaseConfig, str]) -> AsyncIterator[AsyncConnection]:
    config, schema = db
    async with await AsyncConnection.connect(config.database_url.get_secret_value(), connect_timeout=5) as conn:
        await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        await conn.commit()
        yield conn


@pytest_asyncio.fixture
async def db() -> AsyncIterator[tuple[DatabaseConfig, str]]:
    async with engine_database() as (config, schema):
        async with connect((config, schema)) as conn:
            await conn.execute(
                "INSERT INTO predicate_schema VALUES ('statement', 'multi', 'semantic') ON CONFLICT DO NOTHING"
            )
            await conn.execute("INSERT INTO app_user VALUES ('u1', 'ext-u1', %s)", (START,))
            await conn.execute("INSERT INTO session VALUES ('s-u1', 'u1', %s, NULL)", (START,))
            await conn.execute("SELECT set_config('app.user_id', 'u1', false)")
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES ('t-base', 's-u1', 'u1', 1, 'user', 'baseline turn', %s)",
                (START,),
            )
            await conn.commit()
        yield config, schema


async def _set_user(conn: AsyncConnection, user: str = "u1") -> None:
    await conn.execute("SELECT set_config('app.user_id', %s, false)", (user,))
    await conn.commit()


async def _add_turn(conn: AsyncConnection, turn_id: str, seq: int, content: str, user: str = "u1") -> None:
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, %s, 'user', %s, %s)",
        (turn_id, f"s-{user}", user, seq, content, START),
    )


async def _add_claim(
    conn: AsyncConnection,
    version_id: str,
    content: str,
    *,
    memory_key: str | None = None,
    subject_key: str = "user",
    turn_id: str = "t-base",
    derived: bool = False,
    user: str = "u1",
    scope_path: str | None = None,
    with_embedding: bool = True,
) -> None:
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, kind, lifecycle, retention, scope_path, visibility, method_id) "
        "VALUES (%s, %s, %s, 'active', %s, 'statement', false, %s, tstzrange(%s, NULL), tstzrange(clock_now(), NULL), "
        "'semantic', 'claim', %s, 'durable', %s, 'private', %s)",
        (
            version_id,
            memory_key or version_id,
            user,
            subject_key,
            content,
            START,
            "inferred" if derived else "asserted",
            scope_path or f"/u/{user}/",
            "m-v1-consolidation" if derived else None,
        ),
    )
    if not derived:
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, %s, 'support')",
            (version_id, turn_id, user, content[:64] or "span"),
        )
        await conn.execute(
            "INSERT INTO memory_conflict (id, candidate_version_id, user_id, relation, decision, reason_code, "
            "decided_by, decided_at) VALUES (%s, %s, %s, 'unrelated', 'accept', 'gate_passed', 'gate', clock_now())",
            (f"mc-{version_id}", version_id, user),
        )
    if with_embedding:
        await conn.execute(
            "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
            "VALUES (%s, %s, %s, %s::vector)",
            (version_id, MODEL, user, VEC_LIT),
        )


async def _add_verified_abstraction(
    conn: AsyncConnection,
    version_id: str,
    content: str,
    sources: Sequence[str],
    *,
    schema: str,
    user: str = "u1",
    split_id: str = "split-1",
    control_slice_id: str = "neg-1",
) -> str:
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, kind, lifecycle, retention, scope_path, visibility, method_id) "
        "VALUES (%s, %s, %s, 'active', 'user', 'statement', false, %s, tstzrange(%s, NULL), "
        "tstzrange(clock_now(), NULL), 'persona', 'abstraction', 'inferred', 'durable', %s, 'private', "
        "'m-v1-persona')",
        (version_id, version_id, user, content, START, f"/u/{user}/"),
    )
    for src in sources:
        await conn.execute(
            "INSERT INTO memory_derivation (derived_version_id, source_version_id, user_id, derivation_type) "
            "VALUES (%s, %s, %s, 'consolidation')",
            (version_id, src, user),
        )
    await conn.execute(
        "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding) "
        "VALUES (%s, %s, %s, %s::vector)",
        (version_id, MODEL, user, VEC_LIT),
    )
    closure = await compute_closure(conn, user, version_id, schema_name=schema)
    ver_id = f"ver-{version_id}"
    await conn.execute(
        "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
        "negative_control_slice_id, support_window, held_out_window, support_context_key, held_out_context_key, "
        "outcome) VALUES (%s, %s, %s, %s, %s, %s, tstzrange(%s, %s), tstzrange(%s, %s), 'sup-ctx', 'held-ctx', "
        "'verified')",
        (
            ver_id,
            version_id,
            user,
            split_id,
            VERIFIED_AT,
            control_slice_id,
            START,
            START + timedelta(days=2),
            START + timedelta(days=3),
            START + timedelta(days=5),
        ),
    )
    await conn.execute(
        "INSERT INTO verification_artifact (verification_id, user_id, role, artifact_id) "
        "VALUES (%s, %s, 'support', %s), (%s, %s, 'held_out', %s)",
        (ver_id, user, f"sup-{version_id}", ver_id, user, f"held-{version_id}"),
    )
    await conn.execute(
        "UPDATE abstraction_stats SET support_count = 50, counter_count = 0, undetermined_count = 0, "
        "opportunity_count = 50, refutation_attempts = 2, confidence = 0.95, "
        "confidence_method_id = 'm-v1-persona', closed_over_hash = %s, verification_status = 'verified', "
        "expires_at = %s WHERE user_id = %s AND version_id = %s",
        (closure.digest, EXPIRES_AT, user, version_id),
    )
    return closure.digest


async def _builder(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
    joined = " | ".join(f"{src.version_id}:{src.content}" for src in sources)
    return RebuiltSummary(content=f"rebuilt({summary_id})<-{joined}", embedding_model=MODEL, embedding=UNIT_VEC)


async def _all_ids(conn: AsyncConnection) -> list[str]:
    cursor = await conn.execute("SELECT id FROM memory_version ORDER BY id")
    return [str(row[0]) for row in await cursor.fetchall()]


# ------------------------------------------------------------------ F1–F3 & §5.1 scenario graphs


async def test_dependent_sees_final_fate_of_its_sources(db) -> None:
    """F1: ``1 <- 0``, ``2 <- {0, 1}``; delete ``0`` -> ``0``, ``1``, ``2`` all deleted."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "n0", "root fact")
            await _add_claim(conn, "n1", "derived from 0", derived=True)
            await _add_claim(conn, "n2", "derived from 0 and 1", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('n1', 'n0', 'u1', 'consolidation'), "
                "('n2', 'n0', 'u1', 'consolidation'), "
                "('n2', 'n1', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", target_ref="n0", rederivation_builder=_builder, schema_name=schema
            )
        assert receipt.deleted_versions == 3
        assert await _all_ids(conn) == []


async def test_chain_longer_than_any_bound_is_fully_reconciled(db) -> None:
    """F2: chain ``v0 -> v1 -> ... -> v9`` (length 10 >= MAX_LINEAGE_DEPTH + 2); delete ``v0`` -> all 10 deleted."""
    _, schema = db
    chain_len = max(10, MAX_LINEAGE_DEPTH + 4)
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "v0", "chain root")
            for idx in range(1, chain_len):
                await _add_claim(conn, f"v{idx}", f"chain node {idx}", derived=True)
                await conn.execute(
                    "INSERT INTO memory_derivation VALUES (%s, %s, 'u1', 'consolidation')",
                    (f"v{idx}", f"v{idx - 1}"),
                )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", target_ref="v0", schema_name=schema)
        assert receipt.deleted_versions == chain_len
        assert await _all_ids(conn) == []


async def test_depth_nine_abstraction_becomes_stale_without_a_walk(db) -> None:
    """§5.1 depth-9 staleness: ``v1 <- {v0, w}``, ``v2 <- v1 ... v9 <- v8``, ``v9`` verified abstraction."""
    _, schema = db
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "v0", "deleted root")
            await _add_claim(conn, "w", "surviving witness")
            await _add_claim(conn, "v1", "derived v1", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('v1', 'v0', 'u1', 'consolidation'), ('v1', 'w', 'u1', 'consolidation')"
            )
            for idx in range(2, 9):
                await _add_claim(conn, f"v{idx}", f"derived v{idx}", derived=True)
                await conn.execute(
                    "INSERT INTO memory_derivation VALUES (%s, %s, 'u1', 'consolidation')",
                    (f"v{idx}", f"v{idx - 1}"),
                )
            await _add_verified_abstraction(conn, "v9", "deep verified abstraction", ("v8",), schema=schema)

        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        try:
            runtime = Runtime(conn, role, schema)
            engine = build_engine(runtime)
            register_read_handlers(engine, runtime)
            read_set = resolve_read_set("u1", [Selector.of("/u/u1/")], [])
            u1_ctx = CallerContext.from_credential(Credential("u1", "C"), write_scope="/u/u1/", read_set=read_set)

            before = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(before, QueryResult)
            assert "v9" in {item.artifact_id for item in before.facts}

            async with conn.transaction():
                receipt = await delete_memory(
                    conn, "u1", target_ref="v0", rederivation_builder=_builder, schema_name=schema
                )
            assert receipt.deleted_versions == 1
            assert receipt.rederived_dependents == 9

            after = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(after, QueryResult)
            assert "v9" not in {item.artifact_id for item in after.facts}
            assert "v9" in {item.artifact_id for item in after.hypotheses}
            cur = await conn.execute("SELECT content FROM memory_version WHERE id = 'v9'")
            row = await cur.fetchone()
            assert row is not None and "deleted root" not in str(row[0])
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_diamond_dependent_reconciled_once_with_final_fates(db) -> None:
    """§5.1 diamond: ``1 <- 0``, ``2 <- 0``, ``3 <- {1, 2}``; delete ``0`` -> all deleted, 3 visited once."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "n0", "diamond root")
            await _add_claim(conn, "n1", "left branch", derived=True)
            await _add_claim(conn, "n2", "right branch", derived=True)
            await _add_claim(conn, "n3", "diamond sink", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('n1', 'n0', 'u1', 'consolidation'), ('n2', 'n0', 'u1', 'consolidation'), "
                "('n3', 'n1', 'u1', 'consolidation'), ('n3', 'n2', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", target_ref="n0", schema_name=schema)
        assert receipt.dependents == 3
        assert receipt.deleted_versions == 4
        assert await _all_ids(conn) == []


async def test_dependent_with_one_surviving_and_one_deleted_source(db) -> None:
    """§5.1 shared source: ``2 <- {0, 1}``; delete ``0`` -> ``t=1`` re-derives on ``{1}``; ``t=2`` tombstones."""
    _, schema = db
    # Case 1: t = 1 -> re-derived on {1}
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "n0", "secret-zero")
            await _add_claim(conn, "n1", "kept-one")
            await _add_claim(conn, "n2", "mentions secret-zero and kept-one", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('n2', 'n0', 'u1', 'consolidation'), ('n2', 'n1', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            r1 = await delete_memory(
                conn, "u1", target_ref="n0", rederivation_builder=_builder, threshold=1, schema_name=schema
            )
        assert (r1.deleted_versions, r1.rederived_dependents, r1.retired_dependents) == (1, 1, 0)
        cur = await conn.execute("SELECT content, lifecycle FROM memory_version WHERE id = 'n2'")
        row = await cur.fetchone()
        assert row is not None and row[1] == "inferred" and "secret-zero" not in str(row[0])

    # Case 2: t = 2 -> retired & tombstoned (content, vectors gone)
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "m0", "secret-m0")
            await _add_claim(conn, "m1", "kept-m1")
            await _add_claim(conn, "m2", "mentions secret-m0 and kept-m1", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('m2', 'm0', 'u1', 'consolidation'), ('m2', 'm1', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            r2 = await delete_memory(
                conn, "u1", target_ref="m0", rederivation_builder=_builder, threshold=2, schema_name=schema
            )
        assert (r2.deleted_versions, r2.rederived_dependents, r2.retired_dependents) == (1, 0, 1)
        cur = await conn.execute("SELECT lifecycle, retirement_reason, content FROM memory_version WHERE id = 'm2'")
        assert await cur.fetchone() == ("retired", "source_deleted", "")
        cur = await conn.execute("SELECT count(*) FROM memory_embedding WHERE version_id = 'm2'")
        assert await cur.fetchone() == (0,)
        # V2-D50 rule 3 + §11.5 control 3: Channel 1 storage scan must find zero residual of secret-m0 on tombstone.
        ch1 = await scan_deleted_key(
            ScanContext("u1", "m0", "secret-m0", r2.deletion_epoch),
            {"channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda _u: [])},
            connection=conn,
            schema_name=schema,
            channels=("channel_1_storage_index_queue_cache_scan",),
        )
        assert ch1.findings[0].measured is True
        assert ch1.findings[0].residual_count == 0
        assert ch1.findings[0].leaked is False


async def test_dependent_of_a_rederived_source_is_regenerated(db) -> None:
    """§5.1 transitive re-derivation: ``2 <- {0, 1}``, ``3 <- 2``; delete ``0`` -> both 2 and 3 regenerated."""
    _, schema = db
    rebuilt_calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    async def tracking_builder(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
        rebuilt_calls.append((summary_id, tuple(s.version_id for s in sources), tuple(s.content for s in sources)))
        return await _builder(summary_id, sources)

    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "n0", "DELETED-CANARY-TEXT")
            await _add_claim(conn, "n1", "surviving-fact")
            await _add_claim(conn, "n2", "combined DELETED-CANARY-TEXT + surviving-fact", derived=True)
            await _add_claim(conn, "n3", "higher summary of DELETED-CANARY-TEXT", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('n2', 'n0', 'u1', 'consolidation'), "
                "('n2', 'n1', 'u1', 'consolidation'), "
                "('n3', 'n2', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", target_ref="n0", rederivation_builder=tracking_builder, schema_name=schema
            )
        assert receipt.deleted_versions == 1
        assert receipt.rederived_dependents == 2
        assert [call[0] for call in rebuilt_calls] == ["n2", "n3"]
        # n3 received n2's NEW rebuilt content, never n2's pre-deletion text carrying DELETED-CANARY-TEXT
        assert all("DELETED-CANARY-TEXT" not in text for text in rebuilt_calls[1][2])
        cur = await conn.execute("SELECT content FROM memory_version WHERE id IN ('n2', 'n3')")
        for (content,) in await cur.fetchall():
            assert "DELETED-CANARY-TEXT" not in str(content)


async def test_classification_reads_edges_before_they_are_removed(db) -> None:
    """F3: Phase A reads every edge before Phase B removes any edge, so multi-source dependents classify accurately."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "s0", "deleted")
            await _add_claim(conn, "s1", "alive")
            await _add_claim(conn, "mid", "depends on s0", derived=True)
            await _add_claim(conn, "top", "depends on mid and s1", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('mid', 's0', 'u1', 'consolidation'), "
                "('top', 'mid', 'u1', 'consolidation'), "
                "('top', 's1', 'u1', 'consolidation')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", target_ref="s0", rederivation_builder=_builder, threshold=1, schema_name=schema
            )
        # s0 deleted, mid deleted (only source gone), top sees mid=delete and s1=alive -> re-derived on {s1} only
        assert (receipt.deleted_versions, receipt.rederived_dependents) == (2, 1)
        cur = await conn.execute("SELECT derived_version_id, source_version_id FROM memory_derivation ORDER BY 1, 2")
        assert await cur.fetchall() == [("top", "s1")]


# ------------------------------------------------------------------ F5, F6, F7, F8


async def test_verification_started_before_deletion_cannot_commit(db) -> None:
    """F5: a verification started at ``epoch_start`` before a deletion cannot commit after the deletion commits."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "src", "source claim")
            await _add_claim(conn, "other", "other claim")
            await _add_claim(conn, "abs-unv", "unverified abstraction", derived=True)
            await conn.execute("INSERT INTO memory_derivation VALUES ('abs-unv', 'src', 'u1', 'consolidation')")
        epoch_start = await deletion_epoch(conn, "u1", schema_name=schema)

        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="other", schema_name=schema)

        with pytest.raises(StaleDeletionEpoch, match="stale_deletion_epoch"):
            async with conn.transaction():
                await guard_verification_commit(conn, "u1", epoch_start, schema_name=schema)
                await conn.execute(
                    "INSERT INTO verification (id, version_id, user_id, held_out_split_id, verified_on, "
                    "negative_control_slice_id, support_window, held_out_window, support_context_key, "
                    "held_out_context_key, outcome) VALUES ('ver-late', 'abs-unv', 'u1', 'sp', %s, 'nc', "
                    "tstzrange(%s, %s), tstzrange(%s, %s), 'a', 'b', 'verified')",
                    (
                        VERIFIED_AT,
                        START,
                        START + timedelta(days=1),
                        START + timedelta(days=2),
                        START + timedelta(days=3),
                    ),
                )
        cur = await conn.execute("SELECT count(*) FROM verification WHERE id = 'ver-late'")
        assert await cur.fetchone() == (0,)


async def test_closure_hash_cache_is_invalidated_inside_the_deletion_transaction(db) -> None:
    """F6: warm ``ClosureCache`` is invalidated inside the deletion transaction before commit."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "src-keep", "kept source")
            await _add_claim(conn, "src-del", "deleted source")
            await _add_verified_abstraction(conn, "abs1", "verified abs", ("src-keep", "src-del"), schema=schema)

        cache = ClosureCache()
        before = await staleness_inputs(conn, "u1", "abs1", cache=cache, schema_name=schema)
        assert not stale_of(before) and cache.computations == 1

        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="src-del", rederivation_builder=_builder, schema_name=schema)
            during = await staleness_inputs(conn, "u1", "abs1", cache=cache, schema_name=schema)
            assert during.closure_changed and stale_of(during)
            assert cache.computations == 2


async def test_e038_f2_purge_working_moves_the_deletion_token_unconditionally(wired_database) -> None:  # noqa: F811
    """E-038-F2 remaining half: ``purge_working`` moves ``_deletion_token`` to invalidate ``ClosureCache``."""
    async with world(wired_database) as (conn, engine, schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_maintain_handlers(engine, runtime)
        await _set_user(conn, "alice")
        async with conn.transaction():
            await _add_turn(conn, "t-f2", 2, "turn text", user="alice")
            await _add_claim(conn, "s-keep", "kept claim", turn_id="t-f2", user="alice")
            await _add_verified_abstraction(conn, "abs-f2", "verified abs", ("s-keep",), schema=schema, user="alice")
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer, kind, lifecycle, retention, "
                "scope_path, visibility) VALUES ('ep-work', 'ep-work', 'alice', 'active', 'user', 'statement', "
                "false, 'ephemeral', tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'episodic', 'episode', "
                "'observed', 'working', '/u/alice/', 'private')",
                (START,),
            )
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES ('ep-work', 't-f2', 'alice', 'ephemeral', 'support')"
            )

        cache = ClosureCache()
        await staleness_inputs(conn, "alice", "abs-f2", cache=cache, schema_name=schema)
        await conn.commit()
        assert cache.computations == 1

        await engine.call(caller("alice", admin=True), "purge_working", {"scope": "/u/alice/"})
        await staleness_inputs(conn, "alice", "abs-f2", cache=cache, schema_name=schema)
        assert cache.computations == 2, "purge_working must move the deletion token unconditionally"


async def test_deletion_failure_during_retire_rolls_back_all_writes(db) -> None:
    """F7 extended to the retire path: a failure mid-retire rolls back every delete, retire and receipt."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "s0", "deleted source")
            await _add_claim(conn, "s1", "surviving source")
            await _add_claim(conn, "dep", "dependent to retire", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('dep', 's0', 'u1', 'consolidation'), ('dep', 's1', 'u1', 'consolidation')"
            )

        async def fail_retire(_c: AsyncConnection, _u: str, _vid: str, _kept: Sequence[str]) -> None:
            raise ReDerivationError("forced_retire_failure")

        with pytest.raises(ReDerivationError, match="forced_retire_failure"):
            async with conn.transaction():
                await delete_memory(conn, "u1", target_ref="s0", retire_hook=fail_retire, schema_name=schema)
        assert await _all_ids(conn) == ["dep", "s0", "s1"]
        cur = await conn.execute("SELECT lifecycle, content FROM memory_version WHERE id = 'dep'")
        assert await cur.fetchone() == ("inferred", "dependent to retire")
        cur = await conn.execute("SELECT count(*) FROM deletion_request")
        assert await cur.fetchone() == (0,)


@pytest.mark.parametrize("threshold", [1, 2, 3])
async def test_support_exactly_at_threshold_is_rederived_and_one_below_is_retired(db, threshold: int) -> None:
    """F8: parameterised over ``t in {1, 2, 3}`` — ``t`` survivors re-derive, ``t - 1`` retire (or delete if 0)."""
    _, schema = db
    # Part A: exactly `threshold` surviving sources -> re-derived
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            source_ids = [f"s{i}" for i in range(threshold + 1)]
            for sid in source_ids:
                await _add_claim(conn, sid, f"source {sid}")
            await _add_claim(conn, "x", "derived x", derived=True)
            for sid in source_ids:
                await conn.execute("INSERT INTO memory_derivation VALUES ('x', %s, 'u1', 'consolidation')", (sid,))
        async with conn.transaction():
            r_at = await delete_memory(
                conn,
                "u1",
                target_ref=source_ids[-1],
                rederivation_builder=_builder,
                threshold=threshold,
                schema_name=schema,
            )
        assert (r_at.deleted_versions, r_at.rederived_dependents, r_at.retired_dependents) == (1, 1, 0)

        # Part B: delete one more source -> threshold - 1 survivors -> retired (t > 1) or deleted (t == 1)
        async with conn.transaction():
            r_below = await delete_memory(
                conn,
                "u1",
                target_ref=source_ids[-2],
                rederivation_builder=_builder,
                threshold=threshold,
                schema_name=schema,
            )
        if threshold == 1:
            assert (r_below.deleted_versions, r_below.retired_dependents) == (2, 0)
            assert "x" not in await _all_ids(conn)
        else:
            assert (r_below.deleted_versions, r_below.retired_dependents) == (1, 1)
            cur = await conn.execute("SELECT lifecycle, retirement_reason, content FROM memory_version WHERE id = 'x'")
            assert await cur.fetchone() == ("retired", "source_deleted", "")


# ------------------------------------------------------------------ F9, F10, F11, F12, F13


async def test_cyclic_derivation_is_rejected_at_write_time(db) -> None:
    """F9: inserting ``b <- a`` then ``a <- b`` is rejected; an injected cycle terminates cleanly in closure."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "a", "node a")
            await _add_claim(conn, "b", "node b", derived=True)
            await _add_claim(conn, "c", "node c", derived=True)
            await conn.execute("INSERT INTO memory_derivation VALUES ('b', 'a', 'u1', 'consolidation')")
            await conn.execute("INSERT INTO memory_derivation VALUES ('c', 'b', 'u1', 'consolidation')")
        with pytest.raises(RaiseException, match="derivation_cycle_forbidden"):
            async with conn.transaction():
                await conn.execute("INSERT INTO memory_derivation VALUES ('a', 'b', 'u1', 'consolidation')")
        with pytest.raises(RaiseException, match="derivation_cycle_forbidden"):
            async with conn.transaction():
                await conn.execute("INSERT INTO memory_derivation VALUES ('a', 'c', 'u1', 'consolidation')")

        # Inject a cycle directly (triggers disabled) and confirm delete_memory terminates without hanging.
        async with conn.transaction():
            await conn.execute("ALTER TABLE memory_derivation DISABLE TRIGGER trg_memory_derivation_acyclic")
            await conn.execute("INSERT INTO memory_derivation VALUES ('a', 'c', 'u1', 'consolidation')")
            await conn.execute("ALTER TABLE memory_derivation ENABLE TRIGGER trg_memory_derivation_acyclic")
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", target_ref="a", schema_name=schema)
        assert receipt.deleted_versions == 3
        assert await _all_ids(conn) == []


async def test_deleting_an_artifact_removes_its_associations_but_not_the_associated_artifacts(db) -> None:
    """F10: ``x —contradicts— y``; delete ``x`` -> ``x`` and the association are deleted; ``y`` is untouched."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "x", "claim x")
            await _add_claim(conn, "y", "claim y")
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, weight, created_by, origin) "
                "VALUES ('assoc-1', 'u1', 'x', 'y', 'contradicts', 1.0, 'u1', 'tier_c')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance "
                "(id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-assoc-1', 'u1', 'association', 'assoc-1', 'tier_c_call', 'u1')"
            )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", target_ref="x", schema_name=schema)
        assert receipt.deleted_versions == 1
        assert await _all_ids(conn) == ["y"]
        cur = await conn.execute("SELECT count(*) FROM association WHERE id = 'assoc-1'")
        assert await cur.fetchone() == (0,)


async def test_export_snapshot_taken_before_deletion_is_not_served_after(db) -> None:
    """F11: an export snapshot captured before deletion is refused after deletion (channel 11)."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "exp-v1", "CANARY-EXPORT-SNAPSHOT-11")
        store = ExportSnapshotStore()
        before_epoch = await deletion_epoch(conn, "u1", schema_name=schema)
        store.record(ExportSnapshot("snap-1", "u1", before_epoch, "export payload CANARY-EXPORT-SNAPSHOT-11"))
        served_before = await store.serve(conn, "snap-1", schema_name=schema)
        assert "CANARY-EXPORT-SNAPSHOT-11" in served_before.payload

        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="exp-v1", schema_name=schema)

        with pytest.raises(StaleDeletionEpoch, match="stale_deletion_epoch"):
            await store.serve(conn, "snap-1", schema_name=schema)
        assert await store.servable_payloads(conn, "u1", schema_name=schema) == ()


async def test_rederived_dependent_renders_in_hypotheses_until_reverified(db) -> None:
    """F12: a re-derived verified abstraction keeps its old ``closed_over_hash`` and renders in ``hypotheses``."""
    _, schema = db
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "s1", "source one")
            await _add_claim(conn, "s2", "source two")
            await _add_verified_abstraction(conn, "abs-f12", "user prefers dark themes", ("s1", "s2"), schema=schema)

        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        try:
            runtime = Runtime(conn, role, schema)
            engine = build_engine(runtime)
            register_read_handlers(engine, runtime)
            read_set = resolve_read_set("u1", [Selector.of("/u/u1/")], [])
            u1_ctx = CallerContext.from_credential(Credential("u1", "C"), write_scope="/u/u1/", read_set=read_set)

            before = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(before, QueryResult)
            assert "abs-f12" in {item.artifact_id for item in before.facts}

            async with conn.transaction():
                receipt = await delete_memory(
                    conn, "u1", target_ref="s1", rederivation_builder=_builder, threshold=1, schema_name=schema
                )
            assert receipt.rederived_dependents == 1

            after = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(after, QueryResult)
            assert "abs-f12" not in {item.artifact_id for item in after.facts}
            assert "abs-f12" in {item.artifact_id for item in after.hypotheses}
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_concurrent_deletions_with_overlapping_closures_serialize(db) -> None:
    """F13: ``2 <- {0, 1}``; delete ``0`` and ``1`` concurrently -> both commit, no deadlock, ``2`` deleted once."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "c0", "concurrent root 0")
            await _add_claim(conn, "c1", "concurrent root 1")
            await _add_claim(conn, "c2", "shared dependent", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('c2', 'c0', 'u1', 'consolidation'), ('c2', 'c1', 'u1', 'consolidation')"
            )

    async def _delete_one(target: str) -> DeletionReceipt:
        async with connect(db) as worker:
            await _set_user(worker)
            async with worker.transaction():
                return await delete_memory(worker, "u1", target_ref=target, schema_name=schema)

    r0, r1 = await asyncio.gather(_delete_one("c0"), _delete_one("c1"))
    assert {r0.deletion_epoch, r1.deletion_epoch} == {1, 2}
    # First deleter removes its seed + retires c2; second removes its seed + deletes c2 (deleted exactly once).
    assert r0.deleted_versions + r1.deleted_versions == 3
    async with connect(db) as conn:
        await _set_user(conn)
        assert await _all_ids(conn) == []


async def test_cross_scope_deletions_in_opposite_directions_do_not_deadlock(db) -> None:
    """DELETION_ORDER.md §6.1 / V2-D51: ``A:x <- B:s`` and ``B:y <- A:t`` deleted concurrently without deadlock."""
    _, schema = db
    scope_a = "/u/u1/proj-a/"
    scope_b = "/u/u1/proj-b/"
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "s-b", "seed in scope B", scope_path=scope_b)
            await _add_claim(conn, "x-a", "dependent in scope A", derived=True, scope_path=scope_a)
            await _add_claim(conn, "t-a", "seed in scope A", scope_path=scope_a)
            await _add_claim(conn, "y-b", "dependent in scope B", derived=True, scope_path=scope_b)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('x-a', 's-b', 'u1', 'consolidation'), ('y-b', 't-a', 'u1', 'consolidation')"
            )

    async def _delete_one(target: str) -> DeletionReceipt:
        async with connect(db) as worker:
            await _set_user(worker)
            return await delete_memory(worker, "u1", target_ref=target, schema_name=schema)

    rs, rt = await asyncio.gather(_delete_one("s-b"), _delete_one("t-a"))
    assert rs.deleted_versions == 2
    assert rt.deleted_versions == 2
    async with connect(db) as conn:
        await _set_user(conn)
        assert await _all_ids(conn) == []


async def test_closure_growth_under_lock_triggers_a_retry_not_a_partial_deletion(db) -> None:
    """DELETION_ORDER.md §6.1 / V2-D51 step 3: closure growth before locks triggers retry and reconciles new node."""
    _, schema = db
    scope_a = "/u/u1/proj-a/"
    scope_c = "/u/u1/proj-c/"
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "seed-a", "seed in scope A", scope_path=scope_a)

    attempts: list[int] = []

    async def _inject_new_scope_dependent(attempt: int) -> None:
        attempts.append(attempt)
        if attempt == 1:
            async with connect(db) as writer:
                await _set_user(writer)
                async with writer.transaction():
                    await _add_claim(writer, "dep-c", "new dependent in scope C", derived=True, scope_path=scope_c)
                    await writer.execute(
                        "INSERT INTO memory_derivation VALUES ('dep-c', 'seed-a', 'u1', 'consolidation')"
                    )

    async with connect(db) as conn:
        await _set_user(conn)
        receipt = await delete_memory(
            conn,
            "u1",
            target_ref="seed-a",
            before_lock_hook=_inject_new_scope_dependent,
            schema_name=schema,
        )
        assert attempts == [1, 2], "closure growth must trigger a retry rather than a partial deletion"
        assert receipt.deleted_versions == 2
        assert await _all_ids(conn) == []

    # Bounded retry exhaustion fails closed with a static DeletionError.
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "seed-ex", "exhaustion seed", scope_path=scope_a)

        async def _always_grow(attempt: int) -> None:
            async with connect(db) as writer:
                await _set_user(writer)
                async with writer.transaction():
                    new_id = f"grow-{attempt}"
                    await _add_claim(
                        writer, new_id, f"grow {attempt}", derived=True, scope_path=f"/u/u1/grow-{attempt}/"
                    )
                    await writer.execute(
                        "INSERT INTO memory_derivation VALUES (%s, 'seed-ex', 'u1', 'consolidation')", (new_id,)
                    )

        with pytest.raises(DeletionError) as exc:
            await delete_memory(
                conn,
                "u1",
                target_ref="seed-ex",
                before_lock_hook=_always_grow,
                max_lock_retries=2,
                schema_name=schema,
            )
        assert exc.value.code == "closure_growth_retry_exhausted"
        assert "seed-ex" in await _all_ids(conn)


# ------------------------------------------------------------------ 11 channels, A8 through abstraction, Channel 7


class _Surface:
    def __init__(
        self,
        *,
        hypotheses: Sequence[str] = (),
        abstractions: Sequence[str] = (),
        snapshots: Sequence[str] = (),
        text: str = "",
    ) -> None:
        self._hypotheses = tuple(hypotheses)
        self._abstractions = tuple(abstractions)
        self._snapshots = tuple(snapshots)
        self._text = text

    def vector_hits(self, user_id: str, query: str, limit: int) -> Sequence[str]:
        return [self._text] if self._text else []

    def assembled_context(self, user_id: str, query: str) -> str:
        return self._text

    def answer(self, user_id: str, question: str) -> str:
        return self._text

    def rendered_hypotheses(self, user_id: str) -> Sequence[str]:
        return self._hypotheses

    def rendered_abstractions(self, user_id: str) -> Sequence[str]:
        return self._abstractions

    def servable_snapshots(self, user_id: str) -> Sequence[str]:
        return self._snapshots


async def test_eleven_channel_scan_clean_on_v2_deletion_and_a8_leaks_through_abstraction(db) -> None:
    """Criteria 15, 16, 21: 11 channels measured, A8 leaks through an abstraction, Channel 7 positive control fires."""
    _, schema = db
    role = "cme_rt_" + uuid4().hex[:12]
    canary_a8 = "CANARY-ABSTRACTION-A8-LEAK"
    canary_p = "CANARY-ABSTRACTION-P-CLEAN"

    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "a8-src", f"root {canary_a8}")
            await _add_claim(conn, "a8-keep", "surviving context for a8")
            await _add_verified_abstraction(
                conn,
                "a8-abs",
                f"user pattern about {canary_a8}",
                ("a8-src", "a8-keep"),
                schema=schema,
                split_id=f"split-{canary_a8}",
            )
            await _add_claim(conn, "p-src", f"root {canary_p}")
            await _add_claim(conn, "p-keep", "surviving context for p")
            await _add_verified_abstraction(
                conn,
                "p-abs",
                f"user pattern about {canary_p}",
                ("p-src",),
                schema=schema,
                split_id=f"split-{canary_p}",
            )
            await _add_verified_abstraction(
                conn,
                "p-abs-ret",
                f"shared pattern about {canary_p}",
                ("p-src", "p-keep"),
                schema=schema,
            )

        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        try:
            runtime = Runtime(conn, role, schema)
            engine = build_engine(runtime)
            register_read_handlers(engine, runtime)
            read_set = resolve_read_set("u1", [Selector.of("/u/u1/")], [])
            u1_ctx = CallerContext.from_credential(Credential("u1", "C"), write_scope="/u/u1/", read_set=read_set)

            snapshots = ExportSnapshotStore()
            initial_epoch = await deletion_epoch(conn, "u1", schema_name=schema)
            await conn.commit()
            snapshots.record(ExportSnapshot("snap-p", "u1", initial_epoch, f"exported {canary_p}"))

            # --- A8 positive control: skips lineage -> abstraction survives and leaks on channels 8 & 9
            async with conn.transaction():
                a8_receipt = await delete_memory(conn, "u1", target_ref="a8-src", ablation="A8", schema_name=schema)
            a8_query = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(a8_query, QueryResult)
            a8_hypotheses = [item.content for item in a8_query.hypotheses]
            a8_abstractions = [
                f"{item.content} {item.support_summary}"
                for item in (*a8_query.facts, *a8_query.hypotheses)
                if item.kind == "abstraction"
            ]
            a8_surface = _Surface(hypotheses=a8_hypotheses, abstractions=a8_abstractions)
            a8_probes: dict[LeakChannel, ChannelProbe] = {
                "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda _u: []),
                "channel_3_vector_search": vector_probe(a8_surface, query=canary_a8),
                "channel_5_assembled_context": assembled_context_probe(a8_surface, query=canary_a8),
                "channel_6_final_answer": answer_probe(a8_surface, question=canary_a8),
                "channel_7_application_logs": log_sink_probe(lambda: []),
                "channel_8_hypothesis_rendering": hypothesis_rendering_probe(a8_surface),
                "channel_9_abstraction_statements_and_bounds": abstraction_bounds_probe(a8_surface),
                "channel_11_export_snapshots": export_snapshot_probe(a8_surface),
            }
            a8_report = await scan_deleted_key(
                ScanContext("u1", "a8-src", canary_a8, a8_receipt.deletion_epoch),
                a8_probes,
                connection=conn,
                schema_name=schema,
                channels=ALL_CHANNELS,
            )
            await conn.commit()
            a8_verdict = verdict(a8_report)
            assert a8_verdict.complete is True
            assert a8_verdict.clean is False
            assert {
                "channel_1_storage_index_queue_cache_scan",
                "channel_2_lexical_search",
                "channel_4_derived_memory_lookup",
                "channel_8_hypothesis_rendering",
                "channel_9_abstraction_statements_and_bounds",
                "channel_10_verification_records_and_methods",
            } <= set(a8_verdict.leaking_channels)

            # --- Channel 7 positive control (§4.4 option (a)): prove log_sink_probe detects a real logged canary
            with structlog.testing.capture_logs() as leaky_logs:
                structlog.get_logger("e037.control").info("simulated_leaky_log", leaked_text=canary_p)
            ch7_control = await scan_deleted_key(
                ScanContext("u1", "p-src", canary_p, a8_receipt.deletion_epoch),
                {"channel_7_application_logs": log_sink_probe(lambda: [str(entry) for entry in leaky_logs])},
                channels=("channel_7_application_logs",),
            )
            assert ch7_control.findings[0].measured is True
            assert ch7_control.findings[0].complete is True
            assert ch7_control.findings[0].residual_count == 1
            assert ch7_control.findings[0].leaked is True

            # --- Normal V2 deletion (P): all 11 channels measured, complete, and zero residual
            with structlog.testing.capture_logs() as deletion_logs:
                async with conn.transaction():
                    p_receipt = await delete_memory(conn, "u1", target_ref="p-src", ablation="P", schema_name=schema)

            p_query = await engine.call(u1_ctx, "query", {"request": {}})
            assert isinstance(p_query, QueryResult)
            p_hypotheses = [item.content for item in p_query.hypotheses]
            p_abstractions = [
                f"{item.content} {item.support_summary}"
                for item in (*p_query.facts, *p_query.hypotheses)
                if item.kind == "abstraction"
            ]
            p_snapshots = await snapshots.servable_payloads(conn, "u1", schema_name=schema)
            p_surface = _Surface(hypotheses=p_hypotheses, abstractions=p_abstractions, snapshots=p_snapshots)
            p_probes: dict[LeakChannel, ChannelProbe] = {
                "channel_1_storage_index_queue_cache_scan": external_cache_probe(lambda _u: []),
                "channel_3_vector_search": vector_probe(p_surface, query=canary_p),
                "channel_5_assembled_context": assembled_context_probe(p_surface, query=canary_p),
                "channel_6_final_answer": answer_probe(p_surface, question=canary_p),
                "channel_7_application_logs": log_sink_probe(lambda: [str(entry) for entry in deletion_logs]),
                "channel_8_hypothesis_rendering": hypothesis_rendering_probe(p_surface),
                "channel_9_abstraction_statements_and_bounds": abstraction_bounds_probe(p_surface),
                "channel_11_export_snapshots": export_snapshot_probe(p_surface),
            }
            p_report = await scan_deleted_key(
                ScanContext("u1", "p-src", canary_p, p_receipt.deletion_epoch),
                p_probes,
                connection=conn,
                schema_name=schema,
                channels=ALL_CHANNELS,
            )
            p_verdict = verdict(p_report)
            assert len(p_report.findings) == 12
            assert p_verdict.complete is True, (
                f"incomplete: {p_verdict.unmeasured_channels} {p_verdict.partial_channels}"
            )
            assert p_verdict.leaking_channels == ()
            assert p_verdict.clean is True
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


# ------------------------------------------------------------------ T-29 & S-08


async def test_t29_receipt_stores_keyed_hmac_digest_not_plaintext_and_supports_idempotent_retry(db) -> None:
    """T-29: ``deletion_request.target_ref`` stores a keyed HMAC-SHA256 digest, never plaintext."""
    _, schema = db
    key_a = b"deployment-key-alpha-32bytes!!!!"
    key_b = b"deployment-key-bravo-32bytes!!!!"
    secret_topic = "medical-diagnosis-secret"  # pragma: allowlist secret
    assert target_ref_digest(key_a, "u1", "memory", secret_topic) != target_ref_digest(
        key_b, "u1", "memory", secret_topic
    )
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "v-t29", "sensitive note", memory_key=secret_topic)
        async with conn.transaction():
            first = await delete_memory(
                conn,
                "u1",
                scope="memory",
                target_ref=secret_topic,
                deletion_epoch=1,
                receipt_key=key_a,
                schema_name=schema,
            )
        cur = await conn.execute("SELECT target_ref FROM deletion_request WHERE id = %s", (first.request_id,))
        row = await cur.fetchone()
        assert row is not None
        stored = str(row[0])
        assert stored.startswith(RECEIPT_DIGEST_PREFIX)
        assert secret_topic not in stored
        assert stored == target_ref_digest(key_a, "u1", "memory", secret_topic)

        # Idempotent retry with the same key and epoch returns the original receipt without duplicating rows.
        async with conn.transaction():
            retry = await delete_memory(
                conn,
                "u1",
                scope="memory",
                target_ref=secret_topic,
                deletion_epoch=1,
                receipt_key=key_a,
                schema_name=schema,
            )
        assert retry == first


async def test_s08_exclusive_evidence_turns_deleted_in_transaction_and_shared_turns_preserved(db) -> None:
    """S-08: exclusive turns go in-transaction; shared turns stay; co-deleted turns become exclusive and go."""
    _, schema = db
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_turn(conn, "t-excl", 2, "exclusive turn text")
            await _add_turn(conn, "t-shared", 3, "shared turn text")
            await _add_turn(conn, "t-pair", 4, "pair-shared turn text")
            await _add_claim(conn, "v-excl", "only cites t-excl", memory_key="k-excl", turn_id="t-excl")
            await _add_claim(conn, "v-sh-del", "cites t-shared", memory_key="k-sh-del", turn_id="t-shared")
            await _add_claim(conn, "v-sh-keep", "also cites t-shared", memory_key="k-sh-keep", turn_id="t-shared")
            await _add_claim(conn, "v-p1", "pair 1", memory_key="kp1", subject_key="pair-topic", turn_id="t-pair")
            await _add_claim(conn, "v-p2", "pair 2", memory_key="kp2", subject_key="pair-topic", turn_id="t-pair")

        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        try:
            # Direct DELETE on turn by runtime role is still refused (grant widened ONLY via delete_exclusive_turns).
            with pytest.raises(InsufficientPrivilege):
                async with tenant_scope(conn, "u1", role=role):
                    await conn.execute("DELETE FROM turn WHERE id = 't-excl'")

            # 1. Exclusive turn deleted in same transaction under runtime role
            async with tenant_scope(conn, "u1", role=role):
                r_excl = await delete_memory(
                    conn, "u1", scope="memory", target_ref="k-excl", delete_exclusive_sources=True, schema_name=schema
                )
            assert r_excl.deleted_turns == 1

            # 2. Shared turn preserved because v-sh-keep still cites it
            async with tenant_scope(conn, "u1", role=role):
                r_sh = await delete_memory(
                    conn,
                    "u1",
                    scope="memory",
                    target_ref="k-sh-del",
                    delete_exclusive_sources=True,
                    schema_name=schema,
                )
            assert r_sh.deleted_turns == 0

            # 3. Two artifacts sharing t-pair co-deleted in one topic deletion -> t-pair becomes exclusive and goes
            async with tenant_scope(conn, "u1", role=role):
                r_pair = await delete_memory(
                    conn,
                    "u1",
                    scope="topic",
                    target_ref="pair-topic",
                    delete_exclusive_sources=True,
                    schema_name=schema,
                )
            assert r_pair.deleted_versions == 2
            assert r_pair.deleted_turns == 1

            await _set_user(conn)
            cur = await conn.execute("SELECT id FROM turn WHERE id IN ('t-excl', 't-shared', 't-pair') ORDER BY id")
            assert [row[0] for row in await cur.fetchall()] == ["t-shared"]
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_s08_turns_of_rederived_dependents_are_never_exclusive_deletion_candidates(db) -> None:
    """S-08: only turns cited by *deleted* versions are candidates; a re-derived dependent's own turn survives.

    Re-deriving rebuilds the dependent row (its old evidence goes with it), so counting its turns as candidates would
    erase a source that was never evidence of the deleted target.
    """
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_turn(conn, "t-seed", 2, "seed turn text")
            await _add_turn(conn, "t-dep", 3, "dependent's own turn text")
            await _add_claim(conn, "s-del", "deleted seed", turn_id="t-seed")
            await _add_claim(conn, "s-keep", "kept source")
            await _add_claim(conn, "dep", "derived from both", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('dep', 's-del', 'u1', 'consolidation'), ('dep', 's-keep', 'u1', 'consolidation')"
            )
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES ('dep', 't-dep', 'u1', 'own span', 'support')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn,
                "u1",
                target_ref="s-del",
                rederivation_builder=_builder,
                delete_exclusive_sources=True,
                schema_name=schema,
            )
        assert (receipt.deleted_versions, receipt.rederived_dependents, receipt.deleted_turns) == (1, 1, 1)
        cur = await conn.execute("SELECT id FROM turn WHERE id IN ('t-seed', 't-dep') ORDER BY id")
        assert [row[0] for row in await cur.fetchall()] == ["t-dep"]


async def test_deletion_marker_is_cleared_before_delete_returns(db) -> None:
    """S-08 / V2-D50 rule 3: the narrowed-privilege marker does not outlive the deletion in its transaction."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "m-del", "to delete")
        async with conn.transaction():
            await delete_memory(conn, "u1", target_ref="m-del", schema_name=schema)
            cur = await conn.execute("SELECT NULLIF(pg_catalog.current_setting('app.deletion_active', true), '')")
            assert await cur.fetchone() == (None,)
            with pytest.raises(InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute("SELECT delete_exclusive_turns('u1', ARRAY['t-base'])")


async def test_narrowed_paths_refuse_a_forged_marker_outside_a_deletion(db) -> None:
    """S-08 / V2-D50 rule 3: the GUCs alone are not enough; only a transaction that wrote a deletion receipt passes."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "keep-me", "content that must stay")
        async with conn.transaction():
            await conn.execute("SELECT pg_catalog.set_config('app.deletion_active', '1', true)")
            with pytest.raises(InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute("SELECT delete_exclusive_turns('u1', ARRAY['t-base'])")
            with pytest.raises(InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute("SELECT clear_retired_tombstone_content('u1', ARRAY['keep-me'])")
            # the in-place tombstone exception of guard_update is gated the same way
            with pytest.raises(RaiseException):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()), "
                        "status = 'archived', archival_branch = 'S', lifecycle = 'retired', "
                        "retirement_reason = 'source_deleted', content = '' WHERE id = 'keep-me'"
                    )
        cur = await conn.execute("SELECT content FROM memory_version WHERE id = 'keep-me'")
        assert await cur.fetchone() == ("content that must stay",)
        cur = await conn.execute("SELECT count(*) FROM turn WHERE id = 't-base'")
        assert await cur.fetchone() == (1,)


async def test_rederived_dependent_keeps_its_gate_references_evidence_and_associations(db) -> None:
    """Re-deriving rebuilds the row with the same id; rows that reference it are carried across, not lost or blocking.

    ``memory_conflict.existing_version_id`` has no ON DELETE action, so without carrying it a dependent that the gate
    once compared against would abort the whole deletion; evidence and associations would silently cascade away.
    """
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_turn(conn, "t-dep-own", 2, "the dependent's own turn")
            await _add_claim(conn, "s-gone", "deleted source")
            await _add_claim(conn, "s-stay", "surviving source")
            await _add_claim(conn, "dep", "derived from both", derived=True)
            await _add_claim(conn, "other", "an associated claim")
            await _add_claim(conn, "cand", "a later candidate matched against dep")
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('dep', 's-gone', 'u1', 'consolidation'), ('dep', 's-stay', 'u1', 'consolidation')"
            )
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES ('dep', 't-dep-own', 'u1', 'own span', 'support')"
            )
            await conn.execute(
                "INSERT INTO memory_conflict (id, candidate_version_id, existing_version_id, user_id, relation, "
                "decision, reason_code, decided_by, decided_at) VALUES ('mc-dup', 'cand', 'dep', 'u1', 'duplicate', "
                "'accept', 'reinforce_duplicate', 'gate', clock_now())"
            )
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, weight, created_by, origin) "
                "VALUES ('assoc-dep', 'u1', 'dep', 'other', 'contradicts', 1.0, 'u1', 'tier_c')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance "
                "(id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-assoc-dep', 'u1', 'association', 'assoc-dep', 'tier_c_call', 'u1')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", target_ref="s-gone", rederivation_builder=_builder, schema_name=schema
            )
        assert (receipt.deleted_versions, receipt.rederived_dependents) == (1, 1)
        cur = await conn.execute("SELECT existing_version_id FROM memory_conflict WHERE id = 'mc-dup'")
        assert await cur.fetchone() == ("dep",)
        cur = await conn.execute("SELECT turn_id FROM memory_evidence WHERE version_id = 'dep'")
        assert await cur.fetchall() == [("t-dep-own",)]
        cur = await conn.execute("SELECT from_id, to_id FROM association WHERE id = 'assoc-dep'")
        assert await cur.fetchone() == ("dep", "other")
        cur = await conn.execute("SELECT content FROM memory_version WHERE id = 'dep'")
        row = await cur.fetchone()
        assert row is not None and "deleted source" not in str(row[0])


async def test_dependent_named_by_a_sticky_dissent_is_retired_not_rederived(db) -> None:
    """V2-D50 rule 2: a dissent's reference cannot be re-pointed at a rebuilt row, so that dependent has no builder."""
    _, schema = db
    async with connect(db) as conn:
        await _set_user(conn)
        async with conn.transaction():
            await _add_claim(conn, "s-gone", "deleted source")
            await _add_claim(conn, "s-stay", "surviving source")
            await _add_claim(conn, "dep", "dissented dependent", derived=True)
            await conn.execute(
                "INSERT INTO memory_derivation VALUES "
                "('dep', 's-gone', 'u1', 'consolidation'), ('dep', 's-stay', 'u1', 'consolidation')"
            )
            await conn.execute(
                "INSERT INTO negative_constraint (id, user_id, predicate_key, scope_path, method_family, "
                "dissented_at, reason, dissented_version_id) VALUES ('nc-1', 'u1', 'statement', '/u/u1/', "
                "'consolidation', clock_now(), 'user dissent', 'dep')"
            )
        async with conn.transaction():
            receipt = await delete_memory(
                conn, "u1", target_ref="s-gone", rederivation_builder=_builder, schema_name=schema
            )
        assert (receipt.deleted_versions, receipt.rederived_dependents, receipt.retired_dependents) == (1, 0, 1)
        cur = await conn.execute("SELECT lifecycle, retirement_reason, content FROM memory_version WHERE id = 'dep'")
        assert await cur.fetchone() == ("retired", "source_deleted", "")
        cur = await conn.execute("SELECT dissented_version_id FROM negative_constraint WHERE id = 'nc-1'")
        assert await cur.fetchone() == ("dep",)


def test_receipt_key_defaults_to_the_deployment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-29: with ``CME_DELETION_RECEIPT_KEY`` set, every Runtime and direct caller digests with the deployment key."""
    monkeypatch.setenv(RECEIPT_KEY_ENV, "deployment-key-for-test")
    assert default_receipt_key() == b"deployment-key-for-test"
    assert Runtime(cast(AsyncConnection, None), "cme_rt_x").deletion_key == b"deployment-key-for-test"
    monkeypatch.delenv(RECEIPT_KEY_ENV)
    # Without a deployment key one process-wide ephemeral key is shared, so replays still match within the process.
    assert default_receipt_key() == default_receipt_key()
    assert Runtime(cast(AsyncConnection, None), "cme_rt_x").deletion_key == default_receipt_key()


async def test_delete_maps_exhausted_closure_growth_retries_to_retryable_conflict(
    wired_database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API_V2.md §4.8: exhausting the bounded lock retries is ``retryable_conflict``, not ``validation_error``."""

    async def _exhausted(*_args: object, **_kwargs: object) -> DeletionReceipt:
        raise DeletionError("closure_growth_retry_exhausted")

    monkeypatch.setattr(delete_handlers, "delete_memory", _exhausted)
    async with world(wired_database) as (_conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        register_delete_handlers(engine, engine.resolver.runtime)
        with pytest.raises(EntryError) as conflict:
            await engine.call(
                caller("alice", "C"), "delete", {"scope": "memory", "target_ref": "k", "deletion_epoch": 1}
            )
        assert conflict.value.code == "retryable_conflict"


# ------------------------------------------------------------------ delete wired through Engine.call & migration 22


async def test_delete_is_wired_through_engine_call_for_tier_c_only(wired_database) -> None:  # noqa: F811
    """E-039 criterion: ``delete`` wired through ``Engine.call``, Tier C only, out of ``unwired_operations()``."""
    async with world(wired_database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        register_delete_handlers(engine, runtime)
        assert "delete" not in engine.unwired_operations()

        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('t-del-api', 's-alice', 'alice', 2, 'user', 'api delete turn', %s)",
            (START,),
        )
        ep = await engine.call(
            caller("alice"),
            "ingest_episode",
            {
                "scope": "/u/alice/",
                "source": {
                    "type": "turn",
                    "ref": "t-del-api",
                    "span": "api delete turn",
                    "content_hash": canonical_hash("api delete turn"),
                },
                "content": "alice secret episode",
                "event_time": START.isoformat(),
                "idempotency_key": "e037-api-ep",
            },
        )
        ep_id = getattr(ep, "id", None)
        assert isinstance(ep_id, str)
        await _set_user(conn, "alice")
        cur = await conn.execute("SELECT memory_key FROM memory_version WHERE id = %s", (ep_id,))
        row = await cur.fetchone()
        await conn.commit()
        assert row is not None
        mem_key = str(row[0])

        # Tier B is refused (TB1).
        with pytest.raises(EntryError) as tier_b:
            await engine.call(
                caller("alice", "B"),
                "delete",
                {"scope": "memory", "target_ref": mem_key, "deletion_epoch": None},
            )
        assert tier_b.value.code == "forbidden"

        # Tier C deletes it through Engine.call (and removes exclusive turn t-del-api in-transaction).
        receipt = await engine.call(
            caller("alice", "C"),
            "delete",
            {"scope": "memory", "target_ref": mem_key, "deletion_epoch": 1},
        )
        assert isinstance(receipt, DeletionReceipt)
        assert receipt.deleted_versions == 1
        assert receipt.deleted_turns == 1

        # Deleted episode is no longer readable.
        with pytest.raises(EntryError) as gone:
            await engine.call(caller("alice", "C"), "get", {"artifact_id": ep_id})
        assert gone.value.code == "forbidden"


async def test_migration_22_rollback_restores_version_21_catalog_fingerprint_exactly(db) -> None:
    """§11.7 migration hygiene: rolling back migration 22 reproduces version 21's exact catalog fingerprint."""
    config, schema = db
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(db) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=21)), prepare=False
        )
        cur = await conn.execute("SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 21")
        recorded = await cur.fetchone()
        assert recorded is not None
        assert await runner._catalog(conn) == recorded[0]
        assert before != recorded[0]
    assert (await runner.status()).version == 21
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION
