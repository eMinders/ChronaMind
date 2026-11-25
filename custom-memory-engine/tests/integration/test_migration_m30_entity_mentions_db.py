"""Migration 30 (`I8`, ``V2-D111``): entity mentions, memory-scope erasure, and Channel 12.

Covers:
1. ``semantic.ingestor`` records ``entity_mention`` rows under the restricted tenant role for both ``created``
   (``origin='mention'``) and ``attached`` (exact/alias) mentions.
2. ``delete(scope='memory')`` removes ``entity_mention`` rows, reports ``deleted_mentions`` and ``deleted_entities`` in
   the receipt and content-free audit event, erases ``origin='mention'`` entities left with zero surviving mentions
   and zero surviving ``subject_entity_id`` references (including canonical_name, aliases, embedding_1024, and
   ``entity_alias`` provenance), while preserving ``origin='declared'`` entities and entities still referenced by
   surviving mentions or ``subject_entity_id``.
3. Positive control: when ``delete_orphaned_mention_entities`` is bypassed, leftover mention entity/vector trips
   ``channel_12_entity_registry``.
4. Channel 12 (``channel_12_entity_registry``) in ``deletion.scanner`` and ``evaluation.harness.leak_scanner``
   measures ``0`` after per-memory deletion and ``> 0`` on injected leftover ``canonical_name``, ``aliases``,
   ``embedding_1024``, and ``artifact_provenance`` ``entity_alias`` rows.
5. Idempotent replay of ``delete(scope='memory')`` preserves both ``deleted_entities`` and ``deleted_mentions``.
6. Rollback ``030`` restores the exact version-29 catalog fingerprint and refuses while ``entity_mention`` rows or
   ``origin='mention'`` entities exist unless ``cme.rollback_discard = 'entity_mentions'`` is set.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import RaiseException

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.delete_handlers import register_delete_handlers
from custom_memory_engine.api.entry import CallerContext, Credential, Engine
from custom_memory_engine.api.handlers import register_structure_handlers
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.deletion.scanner import (
    ALL_CHANNELS,
    LeakChannel,
    ProbeMeasurement,
    ScanContext,
    scan_deleted_key,
)
from custom_memory_engine.engine.deletion import DeletionReceipt, delete_memory
from custom_memory_engine.engine.hybrid import vector_literal
from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from custom_memory_engine.semantic import entities
from custom_memory_engine.semantic.contracts import (
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
)
from custom_memory_engine.semantic.ingestor import IngestionReport, ingest_turn
from evaluation.harness.leak_scanner import harness_probes, verdict
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import (
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 3, 10, 12, tzinfo=UTC)
VECTOR: tuple[float, ...] = (1.0, *(0.0,) * 1023)
CANARY = "CANARY-M30-LAYLA-HASSAN-SECRET"
RECEIPT_KEY = b"m30-test-deletion-key-32-bytes!!"


class _FakeExtractor:
    def __init__(self, *claims: ExtractedClaim) -> None:
        self._claims = claims

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        del request
        return ExtractionResult(claims=self._claims, extractor_version="fake-m30@1", language="en")


def _basis_vector(slot: int) -> tuple[float, ...]:
    vec = [0.0] * 1024
    vec[slot] = 1.0
    return tuple(vec)


class _FakeEmbedder:
    model = "fake/bge-m3"
    dimensions = 1024

    def __init__(self) -> None:
        self._slots: dict[str, int] = {}

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[tuple[float, ...]]:
        del query
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            slot = self._slots.get(text)
            if slot is None:
                slot = len(self._slots) + 1  # slot 0 reserved for pre-seeded VECTOR
                assert slot < 1024
                self._slots[text] = slot
            vectors.append(_basis_vector(slot))
        return vectors


class _FakeEntityRegistry:
    def __init__(self, entries: Sequence[str]) -> None:
        self._entries = tuple(entries)

    def entity_registry_entries(self, user_id: str) -> Sequence[str]:
        del user_id
        return self._entries


async def _write_episode(
    connection: AsyncConnection, *, user_id: str, scope_path: str, turn: TurnInput, span: str
) -> str:
    del connection, user_id, scope_path, span
    return f"episode-of-{turn.turn_id}"


def _caller(user: str = "alice", tier: Tier = "C") -> CallerContext:
    read_set = resolve_read_set(user, [Selector.of(f"/u/{user}/")], [])
    return CallerContext.from_credential(Credential(user, tier, False), write_scope=f"/u/{user}/", read_set=read_set)


@asynccontextmanager
async def _world(database) -> AsyncIterator[tuple[AsyncConnection, Engine, Runtime, str]]:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.clock', %s, false)", (NOW.isoformat(),))
        await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (NOW,))
        await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (NOW,))
        await provision_runtime_role(conn, schema, role)
        runtime = Runtime(conn, role, schema, deletion_key=RECEIPT_KEY)
        engine = build_engine(runtime)
        register_structure_handlers(engine, runtime)
        register_delete_handlers(engine, runtime)
        try:
            yield conn, engine, runtime, schema
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _turn(text: str, *, turn_id: str, seq: int) -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        user_id="alice",
        session_id="s-alice",
        sequence_no=seq,
        role="user",
        content=text,
        occurred_at=NOW,
    )


async def _ingest(
    runtime: Runtime,
    schema: str,
    turn_input: TurnInput,
    claim_input: ExtractedClaim,
    *,
    embedder: _FakeEmbedder | None = None,
) -> IngestionReport:
    async with runtime.session("alice") as session:
        return await ingest_turn(
            session,
            user_id="alice",
            scope_path="/u/alice/",
            turn=turn_input,
            write_episode=_write_episode,
            extractor=_FakeExtractor(claim_input),
            embedder=embedder or _FakeEmbedder(),
            schema_name=schema,
        )


def _clean_surface_probes() -> dict[LeakChannel, Any]:
    async def zero(context: ScanContext) -> ProbeMeasurement:
        del context
        return ProbeMeasurement(0, complete=True)

    return {
        "channel_1_storage_index_queue_cache_scan": zero,
        "channel_3_vector_search": zero,
        "channel_5_assembled_context": zero,
        "channel_6_final_answer": zero,
        "channel_7_application_logs": zero,
        "channel_8_hypothesis_rendering": zero,
        "channel_9_abstraction_statements_and_bounds": zero,
        "channel_11_export_snapshots": zero,
    }


async def test_ingestor_records_entity_mentions_for_created_and_attached_entities(database) -> None:  # noqa: F811
    async with _world(database) as (conn, engine, runtime, schema):
        # Declare an existing entity first so the next claim attaches to it (`resolution_stage='exact'`).
        declared_id = await engine.call(
            _caller("alice"),
            "create_entity",
            {"scope": "/u/alice/", "entity_type": "person", "label": "Omar Haddad", "idempotency_key": "k-omar"},
        )
        assert isinstance(declared_id, str)

        r1 = await _ingest(
            runtime,
            schema,
            _turn("Layla Hassan and Omar Haddad live in Alexandria", turn_id="t-m1", seq=1),
            ExtractedClaim(
                content="alice knows Layla Hassan and Omar Haddad",
                verbatim_quote="Layla Hassan and Omar Haddad",
                cardinality="multi",
                semantic_slot="contacts",
                um_facet="context",
                entities=(EntityMention(label="Layla Hassan"), EntityMention(label="Omar Haddad")),
            ),
        )
        v1_id = r1.claims[0].version_id
        assert v1_id is not None

        # A second turn attaches to the newly created mention entity via exact match.
        r2 = await _ingest(
            runtime,
            schema,
            _turn("Layla Hassan teaches physics", turn_id="t-m2", seq=2),
            ExtractedClaim(
                content="Layla Hassan teaches physics",
                verbatim_quote="Layla Hassan teaches physics",
                cardinality="single",
                semantic_slot="occupation",
                um_facet="context",
                entities=(EntityMention(label="Layla Hassan"),),
                subject_mention="Layla Hassan",
            ),
        )
        v2_id = r2.claims[0].version_id
        assert v2_id is not None

        async with tenant_scope(conn, "alice", role=runtime.role):
            mentions_rows = await rows(
                conn,
                "SELECT m.version_id, e.canonical_name, e.origin, m.resolution_stage "
                "FROM entity_mention AS m "
                "JOIN entity AS e ON e.id = m.entity_id AND e.user_id = m.user_id "
                "ORDER BY m.version_id, e.canonical_name",
            )
            prov_rows = await rows(
                conn,
                "SELECT artifact_kind, origin_kind, source_version_id FROM artifact_provenance "
                "WHERE artifact_id = (SELECT id FROM entity WHERE canonical_name = 'layla hassan')",
            )

        assert set(mentions_rows) == {
            (v1_id, "layla hassan", "mention", "created"),
            (v1_id, "Omar Haddad", "declared", "exact"),
            (v2_id, "layla hassan", "mention", "exact"),
        }
        assert prov_rows == [("entity", "evidence_version", v1_id)]


async def test_memory_deletion_erases_orphaned_mention_entities_preserves_declared_and_surviving_refs(
    database,  # noqa: F811
) -> None:
    async with _world(database) as (conn, engine, runtime, schema):
        # 1. Declared entity: must survive memory-scope deletion even after its last mention is deleted.
        declared_id = await engine.call(
            _caller("alice"),
            "create_entity",
            {
                "scope": "/u/alice/",
                "entity_type": "person",
                "label": "Zainab Mansour",
                "idempotency_key": "k-declared",
            },
        )
        assert isinstance(declared_id, str)

        # 2. Mention entity carrying an alias, embedding_1024, and entity_alias provenance (trigram-disjoint).
        alias_canary = "XRAY-ALIAS-TAG-5566778899"
        async with tenant_scope(conn, "alice", role=runtime.role):
            alias_mention_ent = await entities.create_entity(
                conn,
                user_id="alice",
                scope_path="/u/alice/",
                label="Kareem Nassar",
                entity_type="person",
                aliases=[alias_canary],
                embedding=VECTOR,
                embedding_model="fake/bge-m3",
                origin="mention",
                created_by="alice",
                schema_name=schema,
            )

        embedder = _FakeEmbedder()

        # Turn 1 mentions: brand-new mention entity, alias-attached mention entity, shared mention entity,
        # subject-referenced mention entity, and the declared entity (all trigram-disjoint).
        r1 = await _ingest(
            runtime,
            schema,
            _turn(
                f"{CANARY} and {alias_canary} met Nadia Farouk, Tariq Soliman, and Zainab Mansour",
                turn_id="t-del-1",
                seq=1,
            ),
            ExtractedClaim(
                content=f"alice notes {CANARY} and {alias_canary} with Nadia Farouk, Tariq Soliman, Zainab Mansour",
                verbatim_quote=f"{CANARY} and {alias_canary}",
                cardinality="multi",
                semantic_slot="event_note",
                um_facet="context",
                entities=(
                    EntityMention(label=CANARY),
                    EntityMention(label=alias_canary),
                    EntityMention(label="Nadia Farouk"),
                    EntityMention(label="Tariq Soliman"),
                    EntityMention(label="Zainab Mansour"),
                ),
            ),
            embedder=embedder,
        )
        v1_id = r1.claims[0].version_id
        assert v1_id is not None

        # Turn 2 mentions "Nadia Farouk" (keeps that mention entity alive while t2 survives).
        r2 = await _ingest(
            runtime,
            schema,
            _turn("Nadia Farouk visited again", turn_id="t-del-2", seq=2),
            ExtractedClaim(
                content="Nadia Farouk visited again",
                verbatim_quote="Nadia Farouk visited again",
                cardinality="single",
                semantic_slot="visit_note",
                um_facet="context",
                entities=(EntityMention(label="Nadia Farouk"),),
            ),
            embedder=embedder,
        )
        v2_id = r2.claims[0].version_id
        assert v2_id is not None

        # Turn 3 cites "Tariq Soliman" as subject_entity_id (keeps it alive via surviving subject ref).
        r3 = await _ingest(
            runtime,
            schema,
            _turn("Tariq Soliman lives in Cairo", turn_id="t-del-3", seq=3),
            ExtractedClaim(
                content="Tariq Soliman lives in Cairo",
                verbatim_quote="Tariq Soliman lives in Cairo",
                cardinality="single",
                semantic_slot="subject_city",
                um_facet="context",
                entities=(EntityMention(label="Tariq Soliman"),),
                subject_mention="Tariq Soliman",
            ),
            embedder=embedder,
        )
        v3_id = r3.claims[0].version_id
        assert v3_id is not None

        key_rows = await rows(conn, "SELECT memory_key FROM memory_version WHERE id = %s", (v1_id,))
        mem_key_1 = str(key_rows[0][0])

        # Delete memory 1 under restricted tenant role.
        async with tenant_scope(conn, "alice", role=runtime.role):
            receipt = await delete_memory(
                conn,
                "alice",
                scope="memory",
                target_ref=mem_key_1,
                deletion_epoch=1,
                receipt_key=RECEIPT_KEY,
                schema_name=schema,
            )

        assert receipt.deleted_versions == 1
        assert receipt.deleted_mentions == 5
        assert receipt.deleted_entities == 2  # CANARY created entity + alias_mention_ent erased

        # Audit event contains counts without leaking entity names/canaries.
        audit_details = await rows(
            conn,
            "SELECT details FROM audit_event "
            "WHERE user_id = 'alice' AND event_type = 'deletion_completed' ORDER BY event_at DESC LIMIT 1",
        )
        detail_map = cast(dict[str, Any], audit_details[0][0])
        assert detail_map["deleted_mentions"] == 5
        assert detail_map["deleted_entities"] == 2
        assert CANARY.lower() not in str(audit_details).lower()
        assert alias_canary.lower() not in str(audit_details).lower()

        # Verify erased entities (names, aliases, vectors, and alias provenance) are completely gone.
        assert await rows(
            conn,
            "SELECT count(*) FROM entity WHERE id = %s OR canonical_name ILIKE %s",
            (alias_mention_ent, f"%{CANARY}%"),
        ) == [(0,)]
        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance "
            "WHERE artifact_kind IN ('entity', 'entity_alias') AND artifact_id = %s",
            (alias_mention_ent,),
        ) == [(0,)]

        # Declared, shared-mention, and subject-referenced entities are all preserved.
        surviving_names = await rows(
            conn,
            "SELECT canonical_name, origin FROM entity WHERE user_id = 'alice' ORDER BY canonical_name",
        )
        assert surviving_names == [
            ("nadia farouk", "mention"),
            ("tariq soliman", "mention"),
            ("Zainab Mansour", "declared"),
        ]

        # Idempotent replay of delete(scope='memory') preserves both deleted_entities and deleted_mentions.
        async with tenant_scope(conn, "alice", role=runtime.role):
            replay = await delete_memory(
                conn,
                "alice",
                scope="memory",
                target_ref=mem_key_1,
                deletion_epoch=1,
                receipt_key=RECEIPT_KEY,
                schema_name=schema,
            )
        assert isinstance(replay, DeletionReceipt)
        assert replay == receipt
        assert replay.deleted_entities == 2
        assert replay.deleted_mentions == 5

        # Channel 12 DB scan reports zero residual after per-memory deletion (for both name and alias canaries).
        for check_canary in (CANARY, alias_canary):
            async with tenant_scope(conn, "alice", role=runtime.role):
                clean_report = await scan_deleted_key(
                    ScanContext("alice", mem_key_1, check_canary, receipt.deletion_epoch),
                    _clean_surface_probes(),
                    connection=conn,
                    schema_name=schema,
                    channels=ALL_CHANNELS,
                )
            clean_verdict = verdict(clean_report)
            assert clean_verdict.clean is True
            ch12 = next(f for f in clean_report.findings if f.channel == "channel_12_entity_registry")
            assert ch12.measured and ch12.complete and ch12.residual_count == 0 and ch12.leaked is False

        # Deleting v2 and v3 removes the last surviving mention/subject refs and erases those mention entities too.
        key_2 = str((await rows(conn, "SELECT memory_key FROM memory_version WHERE id = %s", (v2_id,)))[0][0])
        key_3 = str((await rows(conn, "SELECT memory_key FROM memory_version WHERE id = %s", (v3_id,)))[0][0])
        r_del_2 = await engine.call(_caller("alice"), "delete", {"scope": "memory", "target_ref": key_2})
        r_del_3 = await engine.call(_caller("alice"), "delete", {"scope": "memory", "target_ref": key_3})
        assert isinstance(r_del_2, DeletionReceipt) and r_del_2.deleted_entities == 1
        assert isinstance(r_del_3, DeletionReceipt) and r_del_3.deleted_entities == 1
        assert await rows(conn, "SELECT canonical_name FROM entity WHERE user_id = 'alice'") == [("Zainab Mansour",)]


async def test_control_skipping_mention_erasure_trips_channel_12_and_injected_leaks_are_detected(
    database,  # noqa: F811
) -> None:
    async with _world(database) as (conn, _engine, runtime, schema):
        # 1. Negative control: replace `delete_orphaned_mention_entities` with a no-op to simulate skipping it.
        report_ingest = await _ingest(
            runtime,
            schema,
            _turn(f"{CANARY} stays orphaned without erasure", turn_id="t-ctrl", seq=1),
            ExtractedClaim(
                content=f"alice met {CANARY}",
                verbatim_quote=CANARY,
                cardinality="single",
                semantic_slot="meeting",
                um_facet="context",
                entities=(EntityMention(label=CANARY),),
            ),
        )
        v_id = report_ingest.claims[0].version_id
        assert v_id is not None
        mem_key = str((await rows(conn, "SELECT memory_key FROM memory_version WHERE id = %s", (v_id,)))[0][0])

        await conn.execute(
            "CREATE OR REPLACE FUNCTION delete_orphaned_mention_entities(p_user_id text, p_entity_ids text[]) "
            "RETURNS integer LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN RETURN 0; END $$"
        )
        async with tenant_scope(conn, "alice", role=runtime.role):
            receipt = await delete_memory(
                conn,
                "alice",
                scope="memory",
                target_ref=mem_key,
                deletion_epoch=1,
                receipt_key=RECEIPT_KEY,
                schema_name=schema,
            )
            assert receipt.deleted_mentions == 1
            assert receipt.deleted_entities == 0
            leaked_report = await scan_deleted_key(
                ScanContext("alice", mem_key, CANARY, receipt.deletion_epoch),
                _clean_surface_probes(),
                connection=conn,
                schema_name=schema,
                channels=ALL_CHANNELS,
            )
        ch12_finding = next(f for f in leaked_report.findings if f.channel == "channel_12_entity_registry")
        assert ch12_finding.leaked is True and (ch12_finding.residual_count or 0) > 0
        assert verdict(leaked_report).clean is False

        # Clean out the leaked entity, then inject each Channel 12 residual vector individually.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute("DELETE FROM entity WHERE user_id = 'alice'")

        async def _scan_ch12(canary: str) -> int | None:
            async with tenant_scope(conn, "alice", role=runtime.role):
                rep = await scan_deleted_key(
                    ScanContext("alice", mem_key, canary, 2),
                    {},
                    connection=conn,
                    schema_name=schema,
                    channels=("channel_12_entity_registry",),
                )
            return rep.findings[0].residual_count

        # Zero baseline.
        assert await _scan_ch12(CANARY) == 0

        # A. Injected canonical_name leak.
        c1 = f"CANARY-NAME-{CANARY}"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases, origin) "
                "VALUES ('e-leak-name', 'alice', '/u/alice/', %s, 'person', '{}', 'mention')",
                (c1.lower(),),
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-leak-name', 'alice', 'entity', 'e-leak-name', 'tier_c_call', 'alice')"
            )
        assert (await _scan_ch12(c1) or 0) > 0

        # B. Injected aliases array leak.
        c2 = f"CANARY-ALIAS-ARR-{CANARY}"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases, origin) "
                "VALUES ('e-leak-alias', 'alice', '/u/alice/', 'opaque label', 'person', %s, 'mention')",
                ([c2],),
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-leak-alias', 'alice', 'entity', 'e-leak-alias', 'tier_c_call', 'alice')"
            )
        assert (await _scan_ch12(c2) or 0) > 0

        # C. Injected vector-carrying (embedding_1024) entity leak.
        c3 = f"CANARY-VEC-{CANARY}"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity "
                "(id, user_id, scope_path, canonical_name, entity_type, aliases, origin, "
                "embedding_1024, embedding_model) "
                "VALUES ('e-leak-vec', 'alice', '/u/alice/', %s, 'person', '{}', 'mention', "
                "%s::halfvec(1024), 'fake/bge-m3')",
                (c3.lower(), vector_literal(VECTOR)),
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-leak-vec', 'alice', 'entity', 'e-leak-vec', 'tier_c_call', 'alice')"
            )
        assert (await _scan_ch12(c3) or 0) > 0

        # D. Injected artifact_provenance entity_alias leak (even with an opaque entity row).
        c4 = f"CANARY-AP-ALIAS-{CANARY}"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases, origin) "
                "VALUES ('e-leak-ap', 'alice', '/u/alice/', 'opaque entity', 'person', '{}', 'declared')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-leak-ap-ent', 'alice', 'entity', 'e-leak-ap', 'tier_c_call', 'alice')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance "
                "(id, user_id, artifact_kind, artifact_id, alias_value, origin_kind, created_by) "
                "VALUES ('ap-leak-ap-al', 'alice', 'entity_alias', 'e-leak-ap', %s, 'tier_c_call', 'alice')",
                (c4,),
            )
        assert (await _scan_ch12(c4) or 0) > 0

        # E. Evaluation harness `entity_registry_probe` via `harness_probes(entity_registry=...)`.
        clean_harness = await scan_deleted_key(
            ScanContext("alice", mem_key, CANARY, 2),
            harness_probes(entity_registry=_FakeEntityRegistry(())),
            channels=("channel_12_entity_registry",),
        )
        assert clean_harness.findings[0].residual_count == 0 and clean_harness.findings[0].leaked is False

        leaky_harness = await scan_deleted_key(
            ScanContext("alice", mem_key, CANARY, 2),
            harness_probes(entity_registry=_FakeEntityRegistry((CANARY,))),
            channels=("channel_12_entity_registry",),
        )
        assert leaky_harness.findings[0].residual_count == 1 and leaky_harness.findings[0].leaked is True


async def test_m30_rollback_restores_v29_catalog_and_refuses_when_mentions_exist(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))

    async with connect(database) as conn:
        recorded_29 = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 29")
        assert len(recorded_29) == 1

        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=29)),
            prepare=False,
        )
        assert await runner._catalog(conn) == recorded_29[0][0]
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'entity_mention'",
        )
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'entity' AND column_name = 'origin'",
        )
        assert not await rows(
            conn,
            "SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = current_schema() AND p.proname = 'delete_orphaned_mention_entities'",
        )

    assert (await runner.apply()).action == "applied"

    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=30)),
            prepare=False,
        )
        async with conn.transaction():
            await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (NOW,))
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases, origin) "
                "VALUES ('e-m30', 'alice', '/u/alice/', 'layla', 'person', '{}', 'mention')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-m30', 'alice', 'entity', 'e-m30', 'tier_c_call', 'alice')"
            )

        down_30 = cast(LiteralString, rollback_sql(from_version=30, to_version=29))
        with pytest.raises(RaiseException, match="m30_rollback_refused_entity_mentions_exist"):
            async with conn.transaction():
                await conn.execute(down_30, prepare=False)

        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(30,)]

        recorded_29 = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 29")
        async with conn.transaction():
            await conn.execute("SET LOCAL cme.rollback_discard = 'entity_mentions'")
            await conn.execute(down_30, prepare=False)

        assert await runner._catalog(conn) == recorded_29[0][0]
        assert await rows(conn, "SELECT id FROM entity WHERE id = 'e-m30'") == [("e-m30",)]

    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION
