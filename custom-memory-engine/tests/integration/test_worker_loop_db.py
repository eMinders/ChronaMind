"""The worker loop against a real queue, under the restricted role (``E-047``, ``V2-D106``).

Every model adapter here is a fake that records **the transaction state of the worker's connection at the moment it
was called**. That is the property the loop exists to provide and the one the first version broke: an embedder or an
adjudicator called while a write transaction is open holds the tenant's clock for the length of the call. Each test
asserts the state was ``IDLE`` for every call — so the regression fails here, not in a latency graph.

Everything else is real: the schema, the provisioned runtime role (so row-level security is the boundary, not a
superuser DSN), the queue, the clock, the writes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, pq, sql

from custom_memory_engine.queue.worker import enqueue
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic import entities
from custom_memory_engine.semantic.contracts import (
    AdjudicatedRelation,
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
)
from custom_memory_engine.semantic.slow_lane import enqueue_slow_lane
from custom_memory_engine.worker import WorkerSettings, run_once, serve
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
DIMENSIONS = 1024


def axis(*weights: float) -> tuple[float, ...]:
    return tuple(weights) + (0.0,) * (DIMENSIONS - len(weights))


@dataclass
class Spy:
    """Records the worker connection's transaction status every time a model adapter is called."""

    connection: AsyncConnection | None = None
    states: list[str] = field(default_factory=list)

    def mark(self, what: str) -> None:
        assert self.connection is not None
        status = self.connection.info.transaction_status
        self.states.append(f"{what}:{'idle' if status == pq.TransactionStatus.IDLE else 'in_transaction'}")


@dataclass
class Extractor:
    spy: Spy
    by_text: dict[str, tuple[ExtractedClaim, ...]]
    fail: bool = False
    gate: asyncio.Event | None = None

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.spy.mark("extract")
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("provider_unavailable")
        return ExtractionResult(claims=self.by_text.get(request.text, ()), extractor_version="fake/extract-v1")


@dataclass
class Embedder:
    spy: Spy
    vectors: dict[str, tuple[float, ...]]
    model: str = "test/axes"
    dimensions: int = DIMENSIONS

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> Sequence[Sequence[float]]:
        del query
        self.spy.mark("embed")
        return [self.vectors.get(text, axis(0.0, 0.0, 1.0)) for text in texts]


@dataclass
class Relations:
    spy: Spy
    relation: str = "update"
    seen: list[tuple[str, ...]] = field(default_factory=list)

    async def adjudicate(
        self, claim: ExtractedClaim, existing: Sequence[tuple[str, str]], *, language: str | None = None
    ) -> Sequence[AdjudicatedRelation]:
        del claim, language
        self.spy.mark("relations")
        self.seen.append(tuple(version_id for version_id, _ in existing))
        return tuple(AdjudicatedRelation(existing_id=version_id, relation=self.relation) for version_id, _ in existing)  # type: ignore[arg-type]


@dataclass
class Entities:
    spy: Spy
    asked: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    async def choose(self, mention: str, candidates: Sequence[tuple[str, str]]) -> str | None:
        self.spy.mark("entities")
        self.asked.append((mention, tuple(label for _, label in candidates)))
        return candidates[0][0] if candidates else None


@dataclass
class World:
    owner: AsyncConnection
    worker: AsyncConnection
    schema: str
    role: str
    spy: Spy

    async def turn(self, turn_id: str, content: str, sequence: int) -> None:
        await self.owner.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s)",
            (turn_id, sequence, content, T0),
        )
        await enqueue(
            self.owner,
            enqueue_slow_lane(
                job_id="job-" + turn_id,
                user_id="alice",
                turn_id=turn_id,
                extractor_version="fake/extract-v1",
                candidate_fingerprint="f" * 32,
            ),
            schema_name=self.schema,
        )
        await self.owner.commit()

    async def rows(self, query: str, *args: object) -> list[tuple[Any, ...]]:
        # test-only SQL with the per-test schema interpolated; the values still travel as parameters
        cursor = await self.owner.execute(cast(Any, query), args)
        found = await cursor.fetchall()
        await self.owner.commit()
        return found

    def settings(self, **overrides: Any) -> WorkerSettings:
        base: dict[str, Any] = {"role": self.role, "users": ("alice",), "schema": self.schema, "idle_poll": 0.05}
        return WorkerSettings(**{**base, **overrides})


@asynccontextmanager
async def world(database) -> AsyncIterator[World]:  # noqa: F811
    config, schema = database
    role = "cme_rt_w_" + uuid4().hex[:10]
    async with connect(database) as owner:
        await owner.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
        await owner.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
        await owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        await provision_runtime_role(owner, schema, role)
        await owner.commit()
        dsn = config.database_url.get_secret_value()
        async with await AsyncConnection.connect(dsn, autocommit=True) as worker:
            spy = Spy(worker)
            try:
                yield World(owner, worker, schema, role, spy)
            finally:
                await owner.rollback()
                await owner.execute("RESET ROLE")
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.commit()


def claim(content: str, quote: str, **extra: Any) -> ExtractedClaim:
    return ExtractedClaim(content=content, verbatim_quote=quote, **extra)


async def test_a_job_is_written_under_the_role_and_every_model_call_happens_outside_a_transaction(database) -> None:  # noqa: F811
    """The whole job, the way the deployed loop runs it — and the transaction state at each of its model calls.

    Two turns about the same slot: the second is adjudicated against the first, so all three model seams are exercised
    (extraction, embedding, relation adjudication). Every call must see the worker's connection idle. Before
    ``V2-D106`` the embed and adjudicate calls ran inside the write transaction, after the clock was allocated.
    """
    async with world(database) as w:
        munich = claim("The user lives in Munich", "I live in Munich", cardinality="single", semantic_slot="residence")
        berlin = claim("The user lives in Berlin", "I live in Berlin", cardinality="single", semantic_slot="residence")
        relations = Relations(w.spy)
        settings = w.settings(
            extractor=Extractor(w.spy, {"I live in Munich": (munich,), "I live in Berlin": (berlin,)}),
            embedder=Embedder(w.spy, {munich.content: axis(1.0), berlin.content: axis(0.9, 0.1)}),
            adjudicator=relations,
        )
        await w.turn("t-1", "I live in Munich", 1)
        assert (await run_once(w.worker, "alice", settings)).startswith("done:")
        await w.turn("t-2", "I live in Berlin", 2)
        assert (await run_once(w.worker, "alice", settings)).startswith("done:")

        assert w.spy.states, "the fakes must have been called"
        assert all(state.endswith(":idle") for state in w.spy.states), w.spy.states
        # the adjudicator was asked about the Munich belief, and its answer was applied: Berlin is the current value
        assert relations.seen and any(relations.seen)
        current = await w.rows(
            f"SELECT content FROM {w.schema}.memory_version WHERE kind = 'claim' AND upper_inf(recorded_range)"
            " AND semantic_slot = 'residence'"
        )
        assert current == [("The user lives in Berlin",)], current
        # both jobs done, payload cleared
        assert await w.rows(f"SELECT status, payload FROM {w.schema}.job ORDER BY id") == [
            ("done", None),
            ("done", None),
        ]


async def test_a_cross_script_mention_is_confirmed_by_the_entity_adjudicator_outside_the_transaction(database) -> None:  # noqa: F811
    """``V2-D106`` end to end: the vector stage proposes, the entity adjudicator confirms, the claim attaches.

    "ليلى" shares no trigram with "Layla Hassan"; the mention vector proposes her, and only a confirmation attaches the
    claim. The confirmation is asked before the write transaction opens and replayed inside it.
    """
    async with world(database) as w:
        layla = await entities.create_entity(
            w.owner,
            user_id="alice",
            scope_path="/u/alice/",
            label="Layla Hassan",
            entity_type="person",
            embedding=axis(1.0),
            embedding_model="test/axes",
            schema_name=w.schema,
        )
        await w.owner.commit()
        text = "ليلى تعيش في القاهرة"
        mentioned = claim(
            text, text, subject_mention="ليلى", entities=(EntityMention(label="ليلى", entity_type="person"),)
        )
        asked = Entities(w.spy)
        settings = w.settings(
            extractor=Extractor(w.spy, {text: (mentioned,)}),
            embedder=Embedder(w.spy, {"ليلى": axis(0.95, 0.05), text: axis(0.0, 1.0)}),
            entity_adjudicator=asked,
        )
        await w.turn("t-ar", text, 1)
        assert (await run_once(w.worker, "alice", settings)).startswith("done:")
        # the adjudicator is shown the stored name, which is the normalized key (`entities.normalize_label`)
        assert asked.asked == [("ليلى", ("layla hassan",))]
        assert all(state.endswith(":idle") for state in w.spy.states), w.spy.states
        attached = await w.rows(
            f"SELECT subject_entity_id FROM {w.schema}.memory_version WHERE kind = 'claim' AND content = %s", text
        )
        assert attached == [(layla,)]
        # and no second "Layla" was minted
        assert await w.rows(f"SELECT count(*) FROM {w.schema}.entity") == [(1,)]


async def test_without_an_entity_adjudicator_the_proposal_is_left_unattached(database) -> None:  # noqa: F811
    """The same job with no adjudicator: nothing attaches, nothing is created, and the job still completes."""
    async with world(database) as w:
        await entities.create_entity(
            w.owner,
            user_id="alice",
            scope_path="/u/alice/",
            label="Berlin office",
            embedding=axis(1.0),
            embedding_model="test/axes",
            schema_name=w.schema,
        )
        await w.owner.commit()
        text = "The Munich office closes at six"
        mentioned = claim(
            text,
            text,
            subject_mention="Munich office",
            entities=(EntityMention(label="Munich office", entity_type="place"),),
        )
        settings = w.settings(
            extractor=Extractor(w.spy, {text: (mentioned,)}),
            embedder=Embedder(w.spy, {"Munich office": axis(0.769, 0.639), text: axis(0.0, 1.0)}),
        )
        await w.turn("t-office", text, 1)
        assert (await run_once(w.worker, "alice", settings)).startswith("done:")
        assert await w.rows(
            f"SELECT subject_entity_id FROM {w.schema}.memory_version WHERE kind = 'claim' AND content = %s", text
        ) == [(None,)]
        assert await w.rows(f"SELECT count(*) FROM {w.schema}.entity") == [(1,)]


async def test_an_extraction_failure_backs_off_instead_of_holding_the_lease(database) -> None:  # noqa: F811
    """A provider outage is recorded like any failure: queued again with a backoff, attempt counted.

    Before, an exception in the extractor escaped ``run_once`` and left the job ``running`` until its ten-minute
    lease expired — the attempt budget was spent waiting rather than retrying.
    """
    async with world(database) as w:
        settings = w.settings(extractor=Extractor(w.spy, {}, fail=True))
        await w.turn("t-fail", "I live in Berlin", 1)
        outcome = await run_once(w.worker, "alice", settings)
        assert outcome.startswith("idle:"), outcome  # `complete_one` reports a recorded, retryable failure as idle
        status = await w.rows(
            f"SELECT status, attempts, next_run_at > pg_catalog.now() FROM {w.schema}.job WHERE id = 'job-t-fail'"
        )
        assert status == [("queued", 1, True)]


async def test_a_lease_left_by_a_dead_worker_is_recovered_by_a_live_one_without_a_restart(database) -> None:  # noqa: F811
    """Recovery runs every idle cycle, not only at start-up.

    The job is put in the state a crashed worker leaves — ``running``, lease expired — *after* the live loop started,
    so start-up recovery cannot be what returns it.
    """
    async with world(database) as w:
        settings = w.settings(extractor=Extractor(w.spy, {"I live in Berlin": ()}))
        stop = asyncio.Event()
        loop = asyncio.create_task(serve(w.worker, settings, stop))
        await asyncio.sleep(0.2)  # the loop has started and is idling
        await w.turn("t-dead", "I live in Berlin", 1)
        await w.owner.execute(
            cast(
                Any,
                f"UPDATE {w.schema}.job SET status = 'running', attempts = 1,"
                " next_run_at = pg_catalog.now() - interval '1 minute' WHERE id = 'job-t-dead'",
            )
        )
        await w.owner.commit()
        for _ in range(100):
            if await w.rows(f"SELECT status FROM {w.schema}.job WHERE id = 'job-t-dead'") == [("done",)]:
                break
            await asyncio.sleep(0.05)
        stop.set()
        assert await asyncio.wait_for(loop, timeout=10) == 0
        assert await w.rows(f"SELECT status FROM {w.schema}.job WHERE id = 'job-t-dead'") == [("done",)]


async def test_a_stop_finishes_the_job_in_hand_and_then_returns(database) -> None:  # noqa: F811
    """Graceful stop: the signal arrives mid-extraction, the job is still written, and the loop exits cleanly."""
    async with world(database) as w:
        gate = asyncio.Event()
        berlin = claim("The user lives in Berlin", "I live in Berlin")
        settings = w.settings(extractor=Extractor(w.spy, {"I live in Berlin": (berlin,)}, gate=gate))
        await w.turn("t-stop", "I live in Berlin", 1)
        stop = asyncio.Event()
        loop = asyncio.create_task(serve(w.worker, settings, stop))
        for _ in range(100):
            if w.spy.states:
                break
            await asyncio.sleep(0.02)
        assert w.spy.states == ["extract:idle"], "the extractor is in flight"
        stop.set()  # SIGTERM arrives now
        gate.set()  # …and the model answers afterwards
        assert await asyncio.wait_for(loop, timeout=10) == 0
        assert await w.rows(f"SELECT status FROM {w.schema}.job WHERE id = 'job-t-stop'") == [("done",)]
        assert await w.rows(
            f"SELECT count(*) FROM {w.schema}.memory_version WHERE kind = 'claim' AND content = %s", berlin.content
        ) == [(1,)]


async def test_the_worker_cannot_read_another_tenants_turn(database) -> None:  # noqa: F811
    """Under the role, a job that names another tenant's turn finds nothing — RLS, not the worker's good manners.

    The job is alice's and names bob's turn id. As the owner (the old behaviour) the lookup filtered by ``user_id`` in
    SQL; under the role the row is invisible even to a query that forgot to, and the job fails visibly.
    """
    async with world(database) as w:
        await w.owner.execute("INSERT INTO app_user VALUES ('bob', 'ext-bob', %s)", (T0,))
        await w.owner.execute("SELECT set_config('app.user_id', 'bob', false)")
        await w.owner.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-bob', 'bob', %s)", (T0,))
        await w.owner.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('t-bob', 's-bob', 'bob', 1, 'user', 'bob secret', %s)",
            (T0,),
        )
        await w.owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        await w.owner.commit()
        extractor = Extractor(w.spy, {})
        settings = w.settings(extractor=extractor)
        from custom_memory_engine.worker.loop import load_turn

        with pytest.raises(LookupError):
            await load_turn(w.worker, settings, "alice", "t-bob")
        assert w.spy.states == [], "nothing of bob's reached a model"


async def test_a_deletion_during_the_model_call_dead_letters_the_prepared_write(database) -> None:  # noqa: F811
    """``F5`` for the slow lane: work prepared before a committed deletion is never written.

    The turn is read at deletion epoch 0, the extractor is in flight, and a real ``delete`` commits (epoch 1). Without
    the fence the write would land, carrying a belief derived from a view the user has since cut. With it the job is
    dead-lettered with the content-free audit event ``process_one`` writes for the same case, and nothing is written.
    """
    from custom_memory_engine.engine.deletion import delete_memory

    async with world(database) as w:
        config, _ = database
        dsn = config.database_url.get_secret_value()

        class Deleting(Extractor):
            async def extract(self, request: ExtractionRequest) -> ExtractionResult:
                async with await AsyncConnection.connect(dsn) as other:
                    await other.execute("SELECT set_config('app.user_id', 'alice', false)")
                    async with other.transaction():
                        await delete_memory(
                            other, "alice", scope="memory", target_ref="anything", schema_name=w.schema
                        )
                return await super().extract(request)

        berlin = claim("The user lives in Berlin", "I live in Berlin")
        settings = w.settings(extractor=Deleting(w.spy, {"I live in Berlin": (berlin,)}))
        await w.turn("t-race", "I live in Berlin", 1)
        outcome = await run_once(w.worker, "alice", settings)
        assert outcome == "dead_letter:job-t-race", outcome
        assert await w.rows(
            f"SELECT count(*) FROM {w.schema}.memory_version WHERE kind = 'claim' AND content = %s", berlin.content
        ) == [(0,)]
        assert await w.rows(
            f"SELECT details->>'epoch_start', details->>'current_epoch' FROM {w.schema}.audit_event"
            " WHERE event_type = 'job_discarded_stale_deletion_epoch'"
        ) == [("0", "1")]
