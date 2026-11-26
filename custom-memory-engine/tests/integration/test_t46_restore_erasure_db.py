"""``T-46``: a deletion's whole live effect survives the restore of an older backup (``V2-D125``).

The journal (``V2-D122``) re-applies a deletion's *target*. These tests cover what the target does not describe, and
what re-created the deleted memory on its own once an older backup was restored:

* a turn the backup still holds, whose extraction job the backup still holds, re-extracted by the worker -- the
  deletion had erased the claim and the turn, but the backup predates both;
* a write the client resends under the same idempotency key -- the app resends the whole conversation every turn, so a
  restore without the ``api_idempotency`` row writes the erased claim again.

Each scenario is built the way a restore builds it: by putting back what the backup holds and removing what only the
live database had. The worker, the deletion and the API run for real.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql

from custom_memory_engine.api.entry import CallerContext, Credential
from custom_memory_engine.api.runtime import Receipt, Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.deletion.markers import (
    ErasureMarker,
    MarkerKind,
    record_reconciliation,
    turn_digest,
    write_digest,
)
from custom_memory_engine.engine.deletion import delete_memory, target_ref_digest
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.queue.worker import enqueue
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import ProvenanceKey
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic.contracts import ExtractedClaim, ExtractionRequest, ExtractionResult
from custom_memory_engine.semantic.slow_lane import enqueue_slow_lane
from custom_memory_engine.worker import WorkerSettings, run_once
from tests.integration.test_migration_m1 import connect, database  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)
TURN_TEXT = "I am allergic to peanuts"
CLAIM_TEXT = "The user is allergic to peanuts"
KEY = b"t" * 32
PROVENANCE = ProvenanceKey(b"p" * 32)


@dataclass
class Extractor:
    """A model that always reads ``TURN_TEXT`` as one claim, and anything else as nothing."""

    calls: int = 0

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls += 1
        claims: Sequence[ExtractedClaim] = (
            (ExtractedClaim(content=CLAIM_TEXT, verbatim_quote=TURN_TEXT),) if request.text == TURN_TEXT else ()
        )
        return ExtractionResult(claims=claims, extractor_version="fake/extract-v1")


@dataclass
class World:
    owner: AsyncConnection
    worker: AsyncConnection
    schema: str
    role: str
    extractor: Extractor

    def settings(self) -> WorkerSettings:
        return WorkerSettings(
            role=self.role, users=("alice",), schema=self.schema, extractor=self.extractor, idle_poll=0.01
        )

    def context(self) -> CallerContext:
        scope = "/u/alice/"
        return CallerContext.from_credential(
            Credential("alice", "C", admin=True),
            write_scope=scope,
            read_set=resolve_read_set("alice", [Selector.of(scope)], ()),
        )

    async def rows(self, statement: str, *args: object) -> list[tuple[Any, ...]]:
        """Rows of a SELECT; a statement that returns none (an INSERT, a DELETE) is executed and returns nothing."""
        # test-only SQL with the per-test schema interpolated; the values still travel as parameters
        cursor = await self.owner.execute(cast(Any, statement), args)
        found = [tuple(row) for row in await cursor.fetchall()] if cursor.description is not None else []
        await self.owner.commit()
        return found

    async def enqueue_turn(self, turn_id: str, text: str = TURN_TEXT, sequence: int = 1) -> str:
        await self.owner.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's-alice', 'alice', %s, 'user', %s, %s) ON CONFLICT DO NOTHING",
            (turn_id, sequence, text, T0),
        )
        await enqueue(
            self.owner,
            enqueue_slow_lane(
                job_id=f"job-{turn_id}",
                user_id="alice",
                turn_id=turn_id,
                extractor_version="fake/extract-v1",
                candidate_fingerprint="f" * 32,
            ),
            schema_name=self.schema,
        )
        await self.owner.commit()
        return f"job-{turn_id}"

    async def claims(self, like: str) -> list[tuple[str, str]]:
        """The user's live (non-retired) versions whose content matches, as ``(id, memory_key)``."""
        await self.owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        rows = await self.rows(
            f"SELECT id, memory_key FROM {self.schema}.memory_version WHERE user_id = 'alice' "
            "AND content ILIKE %s AND lifecycle <> 'retired' ORDER BY id",
            f"%{like}%",
        )
        return [(str(row[0]), str(row[1])) for row in rows]

    async def markers(self, request_id: str) -> list[tuple[str, str, str]]:
        return [
            (str(kind), str(digest), str(result))
            for kind, digest, result in await self.rows(
                f"SELECT kind, digest, result_id FROM {self.schema}.erasure_marker "
                "WHERE request_id = %s ORDER BY kind, digest",
                request_id,
            )
        ]

    async def delete(self, memory_key: str, *, delete_turns: bool = True) -> Any:
        return await delete_memory(
            self.owner,
            "alice",
            target_ref=memory_key,
            delete_exclusive_sources=delete_turns,
            receipt_key=KEY,
            schema_name=self.schema,
        )

    async def restore_backup_state(self) -> None:
        """What an older backup holds instead of the live database: no deletion of this user, at all."""
        for table in (
            "erasure_marker",
            "erased_turn",
            "deletion_request",
            "api_idempotency",
            "audit_event",
            "memory_derivation",
            "memory_evidence",
            "memory_version",
            "job",
        ):
            await self.rows(f"DELETE FROM {self.schema}.{table} WHERE user_id = 'alice'")

    async def replay(self, *, memory_key: str, markers: Sequence[ErasureMarker]) -> Any:
        """Re-apply one journalled request the way ``deletion/journal.replay_journal`` does."""
        return await delete_memory(
            self.owner,
            "alice",
            target_ref=memory_key,
            delete_exclusive_sources=True,
            receipt_key=KEY,
            schema_name=self.schema,
            request_id="del-t46",
            stored_target_ref=target_ref_digest(KEY, "alice", "memory", memory_key),
            deletion_epoch=1,
            erasure_markers=markers,
        )


@pytest.fixture
async def world(database) -> AsyncIterator[World]:  # noqa: F811
    config, schema = database
    role = "cme_rt_t46_" + uuid4().hex[:8]
    async with connect((config, schema)) as owner:
        await owner.execute("INSERT INTO predicate_schema VALUES ('fact', 'multi', 'semantic') ON CONFLICT DO NOTHING")
        await owner.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s) ON CONFLICT DO NOTHING", (T0,))
        await owner.execute(
            "INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s) ON CONFLICT DO NOTHING",
            (T0,),
        )
        await owner.execute("SELECT set_config('app.user_id', 'alice', false)")
        await provision_runtime_role(owner, schema, role)
        await owner.commit()
        async with await AsyncConnection.connect(config.database_url.get_secret_value(), autocommit=True) as worker:
            try:
                yield World(owner, worker, schema, role, Extractor())
            finally:
                await owner.rollback()
                await owner.execute("RESET ROLE")
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.commit()


async def test_a_replayed_deletion_erases_the_marked_turn_so_the_worker_cannot_extract_it_again(world: World) -> None:
    """``T-46``, end to end: the backup holds a turn whose claim the deletion erased.

    The sequence is the real one. The backup is taken, the extraction runs, the user deletes the claim (erasing the
    turn with it), and the backup is restored. The restored database holds the turn and its queued job again, and the
    journal holds nothing but a digest of the target -- the claim is gone from the restored database too, so the replay
    cannot find it. Without the request's markers the worker then re-extracts the claim (the ``control`` half, which is
    the code before ``V2-D125``); with them the replay erases the turn it marked, and the worker never sees it.
    """
    job = await world.enqueue_turn("t-peanut")
    assert (await run_once(world.worker, "alice", world.settings())) == f"done:{job}"
    [(_, memory_key)] = await world.claims("peanuts")
    receipt = await world.delete(memory_key)
    assert (receipt.deleted_versions, receipt.deleted_turns) == (1, 1)
    assert await world.claims("peanuts") == []
    markers = [
        ErasureMarker(cast(MarkerKind, kind), digest, result)
        for kind, digest, result in await world.markers(receipt.request_id)
    ]
    # the extraction wrote the claim with no idempotency key, so there is nothing to answer a resend with
    assert [marker.kind for marker in markers] == ["turn"]

    # control: a replay that carries no markers leaves the turn, and the worker resurrects the claim
    await world.restore_backup_state()
    await world.enqueue_turn("t-peanut")
    await world.replay(memory_key=memory_key, markers=[])
    assert (await run_once(world.worker, "alice", world.settings())) == f"done:{job}"
    assert len(await world.claims("peanuts")) == 1, "control: without markers the turn is re-extracted"

    # the fix: the same replay with the markers erases the turn it marked, and its job with it
    await world.restore_backup_state()
    await world.enqueue_turn("t-peanut")
    calls = world.extractor.calls
    await world.replay(memory_key=memory_key, markers=markers)
    assert await world.rows(f"SELECT id FROM {world.schema}.turn WHERE id = 't-peanut'") == []
    assert await world.rows(f"SELECT id FROM {world.schema}.job WHERE turn_id = 't-peanut'") == []
    assert await run_once(world.worker, "alice", world.settings()) == "idle"
    assert world.extractor.calls == calls
    assert await world.claims("peanuts") == []


async def test_a_turn_a_deletion_marked_is_never_extracted_again_even_when_it_is_still_stored(world: World) -> None:
    """A marked turn that stays in the database (turns are retained) is refused, not retried.

    ``delete_exclusive_sources=False`` is a supported configuration: the deletion then keeps every evidence turn, and
    only the flag it leaves behind stops a later re-extraction from re-deriving the deleted claim.
    """
    job = await world.enqueue_turn("t-peanut")
    assert (await run_once(world.worker, "alice", world.settings())) == f"done:{job}"
    [(_, memory_key)] = await world.claims("peanuts")
    receipt = await world.delete(memory_key, delete_turns=False)
    assert receipt.deleted_versions == 1 and receipt.deleted_turns == 0
    assert await world.rows(f"SELECT id FROM {world.schema}.turn WHERE id = 't-peanut'") == [("t-peanut",)]
    assert await world.rows(f"SELECT turn_id FROM {world.schema}.erased_turn WHERE user_id = 'alice'") == [
        ("t-peanut",)
    ]
    # a re-extraction of that turn (a new extractor version) must not reach the model
    calls = world.extractor.calls
    await world.enqueue_turn("t-peanut")
    assert (await run_once(world.worker, "alice", world.settings())).startswith("dead_letter:")
    assert world.extractor.calls == calls
    assert await world.claims("peanuts") == []


async def test_a_deletion_marks_the_turn_it_erased_and_the_write_that_answered_with_the_claim(world: World) -> None:
    """What the journal has to carry besides the target: a ``turn`` digest and a ``write`` digest with its result."""
    job = await world.enqueue_turn("t-peanut")
    assert (await run_once(world.worker, "alice", world.settings())) == f"done:{job}"
    [(version_id, memory_key)] = await world.claims("peanuts")
    await world.rows(
        "INSERT INTO api_idempotency (user_id, operation, idempotency_key, result_id) "
        "VALUES ('alice', 'assert', 'claim:turn-1', %s)",
        version_id,
    )
    receipt = await world.delete(memory_key)
    assert await world.markers(receipt.request_id) == [
        ("turn", turn_digest(KEY, "alice", "t-peanut"), ""),
        ("write", write_digest(KEY, "alice", "assert", "claim:turn-1"), version_id),
    ]


async def test_a_resend_after_a_restore_answers_with_the_erased_result_instead_of_writing_again(world: World) -> None:
    """The second resurrection path, as the deployed app hits it: it resends its whole history every turn.

    Live, the deletion leaves the write's ``api_idempotency`` row in place, so the resend replays and writes nothing. A
    restore brings back a database that never had it; the replayed ``write`` marker is what still answers with the
    erased result. The ``control`` half removes the marker -- the code before ``V2-D125`` -- and the resend writes the
    claim again, which is the resurrection.
    """
    # the app cites its own transcript, not an engine turn (`V2-D93`): nothing the deletion erases stands between the
    # resend and the write, so only the idempotency record can stop it
    evidence = [
        {
            "type": "external",
            "ref": "app://session/turn-1",
            "span": "peanuts",
            "content_hash": canonical_hash("Alice is allergic to peanuts"),
            "role": "user",
        }
    ]
    engine = wire_engine(Runtime(world.owner, world.role, world.schema, provenance_key=PROVENANCE, deletion_key=KEY))
    arguments = {
        "scope": "/u/alice/",
        "kind": "claim",
        "content": "Alice is allergic to peanuts",
        "evidence": evidence,
        "idempotency_key": "claim:turn-1",
    }
    written = await engine.call(world.context(), "assert", arguments)
    assert isinstance(written, Receipt)
    [(version_id, memory_key)] = await world.claims("peanuts")
    assert version_id == written.id
    receipt = await world.delete(memory_key)
    markers = [
        ErasureMarker(cast(MarkerKind, kind), digest, result)
        for kind, digest, result in await world.markers(receipt.request_id)
    ]
    assert markers == [ErasureMarker("write", write_digest(KEY, "alice", "assert", "claim:turn-1"), version_id)]
    assert await world.claims("peanuts") == []

    # live: the recorded row survives the deletion, so the resend replays and writes nothing (the erased row is gone,
    # so only the id is read back: the belief time with it was the deleted version's, which no longer exists)
    live = await engine.call(world.context(), "assert", arguments)
    assert isinstance(live, Receipt) and live.id == version_id
    assert await world.claims("peanuts") == []

    # the restore: neither the claim nor its idempotency row is in the backup, so the resend writes again
    await world.restore_backup_state()
    await engine.call(world.context(), "assert", arguments)
    assert len(await world.claims("peanuts")) == 1, "control: without the marker the resend writes the claim again"

    # the replay re-applies the deletion and its marker, from a state with no trace of either write
    await world.restore_backup_state()
    await world.replay(memory_key=memory_key, markers=markers)
    replayed = await engine.call(world.context(), "assert", arguments)
    assert isinstance(replayed, Receipt) and replayed.id == version_id
    assert await world.claims("peanuts") == []


async def test_the_worker_claims_nothing_until_the_journal_is_replayed_into_the_restored_database(
    world: World,
) -> None:
    """A restored copy is refused until the replay records it, so no worker runs on evidence a deletion marked."""
    await world.enqueue_turn("t-peanut")
    await world.rows(f"DELETE FROM {world.schema}.restore_reconciliation")
    assert await run_once(world.worker, "alice", world.settings()) == "unreconciled"
    assert world.extractor.calls == 0
    # the job goes back untouched: it is the database that is not ready, so no attempt is spent and nothing is audited
    assert await world.rows(f"SELECT status, attempts FROM {world.schema}.job") == [("queued", 0)]
    assert await world.rows(f"SELECT count(*) FROM {world.schema}.audit_event WHERE left(event_type, 4) = 'job_'") == [
        (0,)
    ]
    async with world.owner.transaction():
        await world.owner.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(world.role)))
        await record_reconciliation(world.owner, schema_name=world.schema)
    assert (await run_once(world.worker, "alice", world.settings())).startswith("done:job-t-peanut")
    assert world.extractor.calls == 1


async def test_a_replay_erases_the_marked_turn_even_when_its_target_is_not_in_the_restored_database(
    world: World,
) -> None:
    """A replay re-applies a request whose target the backup never held, and still erases what it marked.

    ``recorded_unresolved`` describes the target alone. The marked turn is here, so it is erased with its jobs:
    otherwise the one deletion whose target is missing would be the one that leaves the most behind.
    """
    job = await world.enqueue_turn("t-peanut")
    await world.enqueue_turn("t-unmarked", "I keep peanuts in the cupboard", sequence=2)
    await world.restore_backup_state()
    await world.enqueue_turn("t-peanut")
    await world.enqueue_turn("t-unmarked", "I keep peanuts in the cupboard", sequence=2)
    markers = [ErasureMarker("turn", turn_digest(KEY, "alice", "t-peanut"))]
    receipt = await world.replay(memory_key="a-memory-the-backup-never-held", markers=markers)
    assert receipt.deleted_versions == 0
    assert await world.rows(f"SELECT id FROM {world.schema}.turn WHERE id = 't-peanut'") == []
    assert await world.rows(f"SELECT id FROM {world.schema}.job WHERE id = %s", job) == []
    # a turn the request did not mark is untouched
    assert await world.rows(f"SELECT id FROM {world.schema}.turn WHERE id = 't-unmarked'") == [("t-unmarked",)]
    # and the next export carries the same markers, so the journal does not lose them by being replayed
    assert await world.markers("del-t46") == [("turn", turn_digest(KEY, "alice", "t-peanut"), "")]
