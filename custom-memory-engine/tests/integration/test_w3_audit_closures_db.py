"""W3 pre-closure audit regressions: ``get`` channel-integrity (``I14``/``I1'``) and ``refine`` guards.

Each test below failed against ``main`` (``cdcbc84``) before its fix and proves a real W3 contract property
through the public entry point (``Engine.call``) under a restricted ``NOSUPERUSER NOBYPASSRLS`` role:

1. ``get`` routes through :func:`custom_memory_engine.engine.channels.assemble`:
   * a fresh verified abstraction renders in ``facts`` with its complete ``Bound`` in both ``bound`` and ``content``
     (``I14``, ``CHANNELS.md`` R2/R3), identical to ``query``;
   * a fresh verified abstraction missing its bound is refused (``forbidden``), never rendered bare;
   * a stale verified abstraction renders in ``hypotheses`` with ``label="verified, needs re-verification"``,
     ``support_summary`` and ``bound`` (``CHANNELS.md`` R4, ``E-038``);
   * an unverified inference renders in ``hypotheses`` with ``label="unverified"`` (``CHANNELS.md`` R4);
   * an open contradiction whose counterpart cannot be loaded is refused (``forbidden``), never one-sided (R5).
2. ``refine`` enforces Tier C on ``audit_as_of`` handles even when the prior audit returned zero rows, before
   depth/instruction validation.
3. ``refine`` evaluates procedure applicability over the live candidate set *before* applying the caller's
   narrowing instruction, so drilling into ``channel procedures`` or ``artifact <proc_id>`` keeps a procedure
   whose ``exists{...}`` precondition holds on the result's live rows — and still drops it if that sibling was
   purged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.api.entry import CallerContext, Credential, Engine
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.maintain_handlers import register_maintain_handlers
from custom_memory_engine.api.read_handlers import Artifact, QueryResult, ReadHandlers, register_read_handlers
from custom_memory_engine.api.runtime import DbTargets, Receipt
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.consolidation import consolidate
from custom_memory_engine.engine.channels import Bound, render_bounded_text
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.ingest import TurnInput, apply_candidate, persist_turn
from custom_memory_engine.engine.staleness import ClosureCache
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import caller, forbidden, world
from tests.integration.test_change_channel_integrity_db import STALE_LABEL, SUPPORT, _claim, _seed_prior_claims
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)
from tests.integration.test_migration_m6 import abstraction, edge
from tests.integration.test_read_path_db import CODING_PROFILE
from tests.integration.test_staleness_db import drop_role, read_handlers_for, seal
from tests.integration.test_type_completeness import profile_with

pytestmark = pytest.mark.integration

CURRENT = START
T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _abstraction_world(
    database_config: tuple[DatabaseConfig, str],
) -> AsyncIterator[tuple[Engine, ReadHandlers, CallerContext, Bound]]:
    _, schema = database_config
    async with connect(database_config) as conn:
        await _seed_prior_claims(conn)
        await _claim(conn, "left", CURRENT)
        await _claim(conn, "right", CURRENT)
        await edge(conn, "left-right", "left", "right", kind="contradicts")

        ((now,),) = await rows(conn, "SELECT transaction_timestamp()")
        assert isinstance(now, datetime)
        expired, review_at, unexpired = (now + timedelta(days=days) for days in (1, 2, 3))
        cache = ClosureCache()
        for version_id, expiry, confidence in (
            ("fresh", unexpired, 0.91),
            ("stale", expired, 0.91),
            ("incomplete", unexpired, None),
        ):
            await abstraction(conn, version_id)
            await seal(conn, cache, schema, version_id)
            await conn.execute(
                "UPDATE abstraction_stats SET confidence = %s, expires_at = %s WHERE version_id = %s",
                (confidence, expiry, version_id),
            )
        ((verified_at,),) = await rows(conn, "SELECT verified_on FROM verification WHERE version_id = 'fresh'")
        assert isinstance(verified_at, datetime)
        expected_bound = Bound(40, 40, 0.91, 1.0, "m-test", "1", verified_at.isoformat(), unexpired)
        await conn.execute("SELECT set_config('app.clock', %s, false)", (review_at.isoformat(),))

        handlers, ctx, role = await read_handlers_for(database_config, conn)
        try:
            engine = build_engine(handlers.runtime)
            wired = register_read_handlers(engine, handlers.runtime)
            yield engine, wired, ctx, expected_bound
        finally:
            await drop_role(conn, role)


# ------------------------------------------------------------------ Finding 1: get() channel integrity (I14 / I1')


async def test_get_renders_fresh_verified_abstraction_with_its_bound_matching_query(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> None:
    """I14 / R2-R3: ``get`` must render a fresh verified abstraction with its bound, identically to ``query``."""
    async with _abstraction_world(database) as (engine, _handlers, ctx, expected_bound):
        got = await engine.call(ctx, "get", {"artifact_id": "fresh"})
        assert isinstance(got, Artifact)
        assert got.channel == "facts"
        assert got.bound == expected_bound
        assert expected_bound.summary() in got.content
        assert got.content == render_bounded_text("x", expected_bound)

        queried = await engine.call(ctx, "query", {"request": {}})
        assert isinstance(queried, QueryResult)
        (from_query,) = [item for item in queried.facts if item.artifact_id == "fresh"]
        assert got == from_query


async def test_get_refuses_fresh_verified_abstraction_without_a_bound_never_bare(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> None:
    """I14 / R2-R3: a fresh verified abstraction missing its bound must be refused, never returned as a bare fact."""
    async with _abstraction_world(database) as (engine, _handlers, ctx, _expected_bound):
        error = await forbidden(engine, ctx, "get", {"artifact_id": "incomplete"})
        assert (error.code, error.detail) == ("forbidden", None)


async def test_get_renders_stale_verified_abstraction_in_hypotheses_with_label_and_support(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> None:
    """R4 / E-038: ``get`` renders a stale verified abstraction in ``hypotheses`` with its re-verification label."""
    async with _abstraction_world(database) as (engine, _handlers, ctx, _expected_bound):
        got = await engine.call(ctx, "get", {"artifact_id": "stale"})
        assert isinstance(got, Artifact)
        assert got.channel == "hypotheses"
        assert got.label == STALE_LABEL
        assert got.support_summary == SUPPORT
        assert got.bound is not None

        queried = await engine.call(ctx, "query", {"request": {}})
        assert isinstance(queried, QueryResult)
        (from_query,) = [item for item in queried.hypotheses if item.artifact_id == "stale"]
        assert got == from_query


async def test_get_renders_unverified_inference_in_hypotheses_with_unverified_label(
    database: tuple[DatabaseConfig, str],  # noqa: F811
) -> None:
    """R4 / I1': ``get`` labels an unverified inference ``unverified`` in ``hypotheses``."""
    async with _abstraction_world(database) as (engine, _handlers, ctx, _expected_bound):
        got = await engine.call(ctx, "get", {"artifact_id": "inference"})
        assert isinstance(got, Artifact)
        assert got.channel == "hypotheses"
        assert got.label == "unverified"
        assert got.bound is None


async def test_get_refuses_open_contradiction_when_counterpart_cannot_be_loaded(
    database: tuple[DatabaseConfig, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5: ``get`` must refuse an ``open_contradiction`` if its counterpart cannot be loaded, never render one side."""
    async with _abstraction_world(database) as (engine, handlers, ctx, _expected_bound):
        monkeypatch.setattr(handlers, "_conflict_counterparts", AsyncMock(return_value={}))
        error = await forbidden(engine, ctx, "get", {"artifact_id": "left"})
        assert (error.code, error.detail) == ("forbidden", None)


# ------------------------------------------------------------------ Finding 2a: refine() Tier C guard on audit_as_of


async def test_tier_b_cannot_refine_empty_or_nonempty_tier_c_audit_handle(database) -> None:  # noqa: F811
    """API_V2.md §4.2: ``audit_as_of`` is Tier C only, including when refining an empty or non-empty audit handle."""
    async with world(database) as (conn, engine, _schema):
        assert isinstance(engine.resolver, DbTargets)
        runtime = engine.resolver.runtime
        register_ingest_handlers(engine, runtime, CODING_PROFILE)
        register_read_handlers(engine, runtime)
        alice_c = caller("alice", "C")
        alice_b = caller("alice", "B")

        # Empty audit result (no rows at T0): Tier C gets an audit handle; Tier B must still be forbidden.
        empty_audit = await engine.call(
            alice_c,
            "query",
            {
                "request": {
                    "scope": "/u/alice/",
                    "mode": "audit_as_of",
                    "target_time": T0.isoformat(),
                    "belief_time": T0.isoformat(),
                }
            },
        )
        assert isinstance(empty_audit, QueryResult) and empty_audit.is_audit and empty_audit.audit == ()
        assert empty_audit.result_id is not None
        denied_empty = await forbidden(
            engine, alice_b, "refine", {"result_id": empty_audit.result_id, "instruction": "mention anything"}
        )
        assert (denied_empty.code, denied_empty.detail) == ("forbidden", None)

        # Non-empty audit result: Tier B is refused with forbidden even if the instruction would be malformed.
        receipt = await engine.call(
            alice_c,
            "ingest_episode",
            {
                "scope": "/u/alice/",
                "source": {
                    "type": "tool",
                    "ref": "tool:audit-ep",
                    "span": "audit row",
                    "content_hash": canonical_hash("audit row"),
                },
                "content": "audit row",
                "event_time": T0.isoformat(),
                "idempotency_key": "audit-ep",
            },
        )
        assert isinstance(receipt, Receipt)
        full_audit = await engine.call(
            alice_c,
            "query",
            {
                "request": {
                    "scope": "/u/alice/",
                    "mode": "audit_as_of",
                    "target_time": T0.isoformat(),
                    "belief_time": receipt.recorded_at.isoformat(),
                }
            },
        )
        assert isinstance(full_audit, QueryResult) and full_audit.is_audit and len(full_audit.audit) == 1
        assert full_audit.result_id is not None
        denied_full = await forbidden(
            engine, alice_b, "refine", {"result_id": full_audit.result_id, "instruction": "not_an_operator foo"}
        )
        assert (denied_full.code, denied_full.detail) == ("forbidden", None)


# ------------------------------------------------------------------ Finding 2b: refine() procedure applicability order


async def _seed_procedure_with_exists_precondition(
    conn: AsyncConnection, engine: Engine, *, episode_retention: str
) -> tuple[str, str]:
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES ('turn-proc-ref', 's-alice', 'alice', 2, 'user', 'run pytest on failures', %s)",
        (T0,),
    )
    profile = profile_with(
        "proc-refine",
        kinds=["episode", "procedure"],
        retention={"episode": episode_retention, "procedure": "durable"},
    )
    assert isinstance(engine.resolver, DbTargets)
    runtime = engine.resolver.runtime
    register_ingest_handlers(engine, runtime, profile)
    register_read_handlers(engine, runtime)
    register_maintain_handlers(engine, runtime)

    ep = await engine.call(
        caller("alice"),
        "ingest_episode",
        {
            "scope": "/u/alice/",
            "source": {
                "type": "turn",
                "ref": "turn-proc-ref",
                "span": "run pytest",
                "content_hash": canonical_hash("run pytest on failures"),
            },
            "content": "failing pytest run observed",
            "event_time": T0.isoformat(),
            "idempotency_key": "ep-sibling",
        },
    )
    assert isinstance(ep, Receipt)

    proc = await engine.call(
        caller("alice"),
        "assert",
        {
            "scope": "/u/alice/",
            "kind": "procedure",
            "content": "Run focused pytest after failure",
            "evidence": [
                {
                    "type": "turn",
                    "ref": "turn-proc-ref",
                    "span": "run pytest on failures",
                    "content_hash": canonical_hash("run pytest on failures"),
                }
            ],
            "event_range": None,
            "idempotency_key": "proc-exists",
            "preconditions": [{"op": "exists", "match": {"kind": "episode"}}],
            "steps": ["pytest -q"],
        },
    )
    assert isinstance(proc, Receipt)
    return ep.id, proc.id


async def test_refine_to_procedures_channel_or_artifact_keeps_procedure_whose_exists_precondition_holds(
    database,  # noqa: F811
) -> None:
    """E-030 + E-034: narrowing to ``channel procedures`` or ``artifact <proc>`` must not invalidate ``exists``."""
    async with world(database) as (conn, engine, _schema):
        ep_id, proc_id = await _seed_procedure_with_exists_precondition(conn, engine, episode_retention="durable")
        alice = CallerContext.from_credential(
            Credential("alice", "C", False),
            write_scope="/u/alice/",
            read_set=resolve_read_set("alice", [Selector.of("/u/alice/")], []),
        )
        prior = await engine.call(alice, "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(prior, QueryResult) and prior.result_id is not None
        assert [item.artifact_id for item in prior.facts] == [ep_id]
        assert [item.artifact_id for item in prior.procedures] == [proc_id]

        by_channel = await engine.call(
            alice, "refine", {"result_id": prior.result_id, "instruction": "channel procedures"}
        )
        assert isinstance(by_channel, QueryResult)
        assert [item.artifact_id for item in by_channel.procedures] == [proc_id]
        assert by_channel.facts == ()

        by_artifact = await engine.call(
            alice, "refine", {"result_id": prior.result_id, "instruction": f"artifact {proc_id}"}
        )
        assert isinstance(by_artifact, QueryResult)
        assert [item.artifact_id for item in by_artifact.procedures] == [proc_id]
        assert by_artifact.facts == ()


async def test_refine_drops_procedure_when_its_exists_precondition_sibling_was_purged(database) -> None:  # noqa: F811
    """Control for the ordering fix: if the sibling episode is actually gone, ``refine`` drops the procedure."""
    async with world(database) as (conn, engine, _schema):
        _ep_id, proc_id = await _seed_procedure_with_exists_precondition(conn, engine, episode_retention="working")
        alice = caller("alice", "C", admin=True)
        prior = await engine.call(alice, "query", {"request": {"scope": "/u/alice/"}})
        assert isinstance(prior, QueryResult) and prior.result_id is not None
        assert [item.artifact_id for item in prior.procedures] == [proc_id]

        await engine.call(alice, "purge_working", {"scope": "/u/alice/"})

        after_purge = await engine.call(
            alice, "refine", {"result_id": prior.result_id, "instruction": "channel procedures"}
        )
        assert isinstance(after_purge, QueryResult)
        assert after_purge.procedures == ()


# ------------------------------------------------------------------ Finding 3: depth > 3 consolidation + deletion


async def test_consolidation_accepts_explicit_depth_above_three_and_unbounded_deletion_removes_entire_chain(
    database,  # noqa: F811
) -> None:
    """V2-D09 / #73 §7: ``consolidate(..., max_depth=4 | None)`` builds depth 4 and ``delete_memory`` clears it."""
    async with world(database) as (conn, _engine, schema):
        await conn.execute("INSERT INTO predicate_schema VALUES ('diet', 'single', 'semantic') ON CONFLICT DO NOTHING")
        await conn.execute("SELECT set_config('app.clock', %s, false)", (T0.isoformat(),))
        await conn.execute("SELECT set_config('app.user_id', 'alice', false)")
        async with conn.transaction():
            await persist_turn(
                conn,
                TurnInput("t-depth", "alice", "s-alice", 10, "user", "prefers plant based meals", T0),
                schema_name=schema,
            )
            await apply_candidate(
                conn,
                CandidateVersionInput(
                    version_id="v0",
                    memory_key="alice:diet:0",
                    user_id="alice",
                    subject_key="alice",
                    predicate_key="diet",
                    content="prefers plant based meals",
                    single_valued=False,
                    valid_from=T0,
                    evidence=(
                        EvidenceInput(
                            turn_id="t-depth",
                            evidence_span="prefers plant based meals",
                            evidence_role="user",
                        ),
                    ),
                ),
                evidence_turns=(("user", "prefers plant based meals"),),
                schema_name=schema,
                replay_time=T0,
            )
        parent = "v0"
        for hop in range(1, 5):
            async with conn.transaction():
                out = await consolidate(
                    conn,
                    "alice",
                    (parent,),
                    content=f"consolidated hop {hop}",
                    version_id=f"c{hop}",
                    schema_name=schema,
                    replay_time=T0 + timedelta(minutes=hop),
                    max_depth=4 if hop < 4 else None,
                )
            assert out.depth == hop
            parent = out.version_id

        async with conn.transaction():
            receipt = await delete_memory(
                conn,
                "alice",
                scope="memory",
                target_ref="alice:diet:0",
                schema_name=schema,
                replay_time=T0 + timedelta(minutes=10),
            )
        assert receipt.deleted_versions == 5
        leftover = await rows(conn, "SELECT id FROM memory_version WHERE id IN ('v0', 'c1', 'c2', 'c3', 'c4')")
        assert leftover == []
