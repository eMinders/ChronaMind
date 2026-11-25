"""Migration 29 (`I2`, ``V2-D110``): structural provenance in ``artifact_provenance``.

Covers:
1. Deferred commit-time triggers reject unattributed ``association``, ``entity``, and ``entity_link`` inserts with
   static codes (`*_requires_provenance`), with positive controls both when attribution is written and when the trigger
   is dropped inside a test transaction.
2. ``Engine.call`` for ``link``, ``create_entity``, and ``link_entity`` records readable attribution, and direct
   handler calls with ``source_version_ids`` / ``aliases`` record ``evidence_version`` and ``entity_alias`` rows while
   ``link_entity`` preserves both endpoints' ``entity`` and ``entity_alias`` attribution.
3. Deleting a cited ``memory_version`` transitions ``artifact_provenance`` rows to ``source_version_id = NULL`` and
   ``origin_kind = 'source_deleted'`` (rendering ``"provenance removed by deletion"`` with zero dangling IDs).
4. Deleting an ``association``, ``entity``, or ``entity_link`` cascades all of its attribution rows (including
   ``entity_alias`` rows on entity deletion).
5. Pre-migration rows render ``"unattributed (pre-migration)"`` and are never presented as newly attributed.
6. Rollback ``029`` restores the exact version-28 catalog fingerprint and refuses while attributed rows exist unless
   ``cme.rollback_discard = 'artifact_provenance'`` is explicitly set.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import RaiseException

from custom_memory_engine.api.contract import BY_NAME, Tier
from custom_memory_engine.api.delete_handlers import register_delete_handlers
from custom_memory_engine.api.entry import Call, CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.handlers import Handlers, register_structure_handlers
from custom_memory_engine.api.runtime import Receipt, Runtime
from custom_memory_engine.api.wiring import build_engine
from custom_memory_engine.engine.artifact_provenance import (
    SOURCE_DELETED_LABEL,
    UNATTRIBUTED_PRE_MIGRATION_LABEL,
    read_artifact_provenance,
    read_entity_provenance,
    record_entity_provenance,
)
from custom_memory_engine.schema import migrate
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.provenance import ProvenanceKey
from custom_memory_engine.security import provision_runtime_role, tenant_scope
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_api_wiring_structure import make_version
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)
PROV_KEY = ProvenanceKey(b"m29-test-provenance-key-32bytes!")


def _caller(user: str, tier: Tier = "C", *, admin: bool = False) -> CallerContext:
    read_set = resolve_read_set(user, [Selector.of(f"/u/{user}/")], [])
    return CallerContext.from_credential(Credential(user, tier, admin), write_scope=f"/u/{user}/", read_set=read_set)


@asynccontextmanager
async def _world(database) -> AsyncIterator[tuple[AsyncConnection, Engine, Runtime, str]]:  # noqa: F811
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database) as conn:
        await conn.execute("SELECT set_config('app.clock', %s, false)", (T0.isoformat(),))
        for user in ("alice", "bob"):
            await conn.execute("INSERT INTO app_user VALUES (%s, %s, %s)", (user, "ext-" + user, T0))
            await conn.execute(
                "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, %s)",
                ("s-" + user, user, T0),
            )
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES (%s, %s, %s, 1, 'user', 'x', %s)",
                ("t-" + user, "s-" + user, user, T0),
            )
        await provision_runtime_role(conn, schema, role)
        runtime = Runtime(conn, role, schema)
        engine = build_engine(runtime)
        register_structure_handlers(engine, runtime)
        register_delete_handlers(engine, runtime)
        try:
            yield conn, engine, runtime, schema
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def _seed_raw_entity(
    conn: AsyncConnection,
    entity_id: str,
    *,
    user: str = "alice",
    label: str = "seeded",
    attributed: bool = True,
) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases) "
            "VALUES (%s, %s, %s, %s, 'person', '{}')",
            (entity_id, user, f"/u/{user}/", label),
        )
        if attributed:
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES (%s, %s, 'entity', %s, 'tier_c_call', %s)",
                (f"ap-{entity_id}", user, entity_id, user),
            )


async def test_unattributed_structural_inserts_fail_at_commit_with_positive_controls(database) -> None:  # noqa: F811
    async with _world(database) as (conn, _engine, _runtime, _schema):
        await make_version(conn, "v-a", "alice")
        await make_version(conn, "v-b", "alice")
        await _seed_raw_entity(conn, "ent-a", user="alice", label="entity a")
        await _seed_raw_entity(conn, "ent-b", user="alice", label="entity b")

        # 1. Negative controls: unattributed association, entity, and entity_link fail at COMMIT.
        with pytest.raises(RaiseException, match="association_requires_provenance"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, origin) "
                    "VALUES ('assoc-unattr', 'alice', 'v-a', 'v-b', 'related_to', 'alice', 'tier_c')"
                )

        with pytest.raises(RaiseException, match="entity_requires_provenance"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases) "
                    "VALUES ('ent-unattr', 'alice', '/u/alice/', 'ghost', 'person', '{}')"
                )

        with pytest.raises(RaiseException, match="entity_link_requires_provenance"):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) "
                    "VALUES ('el-unattr', 'alice', 'ent-a', 'ent-b', 'same_person')"
                )

        # 2. Positive control A: inserting matching artifact_provenance in the same transaction commits cleanly.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, origin) "
                "VALUES ('assoc-attr', 'alice', 'v-a', 'v-b', 'related_to', 'alice', 'tier_c')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-assoc-attr', 'alice', 'association', 'assoc-attr', 'tier_c_call', 'alice')"
            )
            await conn.execute(
                "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) "
                "VALUES ('el-attr', 'alice', 'ent-a', 'ent-b', 'same_person')"
            )
            await conn.execute(
                "INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by) "
                "VALUES ('ap-el-attr', 'alice', 'entity_link', 'el-attr', 'tier_c_call', 'alice')"
            )

        # 3. Positive control B: dropping each trigger in a tx lets unattributed inserts commit cleanly.
        async with conn.transaction():
            await conn.execute("DROP TRIGGER trg_association_provenance ON association")
            await conn.execute("DROP TRIGGER trg_entity_provenance ON entity")
            await conn.execute("DROP TRIGGER trg_entity_link_provenance ON entity_link")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, origin) "
                "VALUES ('assoc-notrig', 'alice', 'v-b', 'v-a', 'related_to', 'alice', 'tier_c')"
            )
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases) "
                "VALUES ('ent-notrig', 'alice', '/u/alice/', 'no trigger entity', 'person', '{}')"
            )
            await conn.execute(
                "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) "
                "VALUES ('el-notrig', 'alice', 'ent-a', 'ent-notrig', 'same_person')"
            )
        assert await rows(conn, "SELECT count(*) FROM association WHERE id = 'assoc-notrig'") == [(1,)]
        assert await rows(conn, "SELECT count(*) FROM entity WHERE id = 'ent-notrig'") == [(1,)]
        assert await rows(conn, "SELECT count(*) FROM entity_link WHERE id = 'el-notrig'") == [(1,)]


async def test_api_calls_and_direct_handlers_record_provenance_aliases_and_links(database) -> None:  # noqa: F811
    async with _world(database) as (conn, engine, runtime, schema):
        await make_version(conn, "v-1", "alice")
        await make_version(conn, "v-2", "alice")
        await make_version(conn, "v-bob", "bob")

        alice = _caller("alice")

        # 1. Existing Engine.call contracts (`create_entity`, `link`, `link_entity`) record readable attribution.
        ent_api_1 = await engine.call(
            alice,
            "create_entity",
            {"scope": "/u/alice/", "entity_type": "person", "label": "Layla Hassan", "idempotency_key": "k-api-1"},
        )
        ent_api_2 = await engine.call(
            alice,
            "create_entity",
            {"scope": "/u/alice/", "entity_type": "person", "label": "Dr. Yusuf", "idempotency_key": "k-api-2"},
        )
        assert isinstance(ent_api_1, str) and isinstance(ent_api_2, str)
        assoc_api = await engine.call(alice, "link", {"from": "v-1", "to": "v-2", "type": "related_to"})
        assert isinstance(assoc_api, str)
        link_api = await engine.call(
            alice, "link_entity", {"entity_a": ent_api_1, "entity_b": ent_api_2, "relation": "colleague_of"}
        )
        assert isinstance(link_api, Receipt)

        async with tenant_scope(conn, "alice", role=runtime.role):
            ent_prov = await read_entity_provenance(
                conn,
                user_id="alice",
                entity_id=ent_api_1,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            assert len(ent_prov) == 1
            assert ent_prov[0].artifact_kind == "entity"
            assert ent_prov[0].origin_kind == "tier_c_call"
            assert ent_prov[0].readable is True
            assert ent_prov[0].created_by == "alice"

            assoc_prov = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id=assoc_api,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            assert len(assoc_prov) == 1
            assert assoc_prov[0].origin_kind == "tier_c_call"

            link_prov = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="entity_link",
                artifact_id=link_api.id,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            assert len(link_prov) == 1
            assert link_prov[0].origin_kind == "tier_c_call"

        # 2. Direct Handlers calls with `source_version_ids` and `aliases` record `evidence_version` + `entity_alias`.
        handlers = Handlers(runtime)
        ent_h_a = await handlers.create_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["create_entity"],
                arguments={
                    "scope": "/u/alice/",
                    "entity_type": "person",
                    "label": "Nadia Farid",
                    "idempotency_key": "k-handler-a",
                    "source_version_ids": ["v-1"],
                    "aliases": ["Dr. Nadia", "Nadia F."],
                },
            )
        )
        ent_h_b = await handlers.create_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["create_entity"],
                arguments={
                    "scope": "/u/alice/",
                    "entity_type": "person",
                    "label": "Nadia Al-Farid",
                    "idempotency_key": "k-handler-b",
                    "source_version_ids": ["v-2"],
                    "aliases": ["N. Farid"],
                },
            )
        )
        assoc_h = await handlers.link(
            Call(
                ctx=alice,
                operation=BY_NAME["link"],
                arguments={
                    "from": "v-1",
                    "to": "v-2",
                    "type": "corroborated_by",
                    "source_version_ids": ["v-1", "v-2"],
                },
            )
        )
        link_h = await handlers.link_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["link_entity"],
                arguments={
                    "entity_a": ent_h_a,
                    "entity_b": ent_h_b,
                    "relation": "same_person",
                    "source_version_ids": ["v-1"],
                },
            )
        )

        # Unreadable/cross-tenant cited source fails closed without creating the entity.
        with pytest.raises(EntryError, match="forbidden"):
            await handlers.create_entity(
                Call(
                    ctx=alice,
                    operation=BY_NAME["create_entity"],
                    arguments={
                        "scope": "/u/alice/",
                        "entity_type": "person",
                        "label": "Bob Secret",
                        "idempotency_key": "k-handler-bad",
                        "source_version_ids": ["v-bob"],
                    },
                )
            )

        async with tenant_scope(conn, "alice", role=runtime.role):
            prov_a = await read_entity_provenance(
                conn,
                user_id="alice",
                entity_id=ent_h_a,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            prov_b = await read_entity_provenance(
                conn,
                user_id="alice",
                entity_id=ent_h_b,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            prov_assoc_h = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id=assoc_h,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            prov_link_h = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="entity_link",
                artifact_id=link_h.id,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )

        assert {row.artifact_kind for row in prov_a} == {"entity", "entity_alias"}
        assert {row.alias_value for row in prov_a if row.artifact_kind == "entity_alias"} == {
            "Dr. Nadia",
            "Nadia F.",
        }
        assert all(row.origin_kind == "evidence_version" and row.source_version_id == "v-1" for row in prov_a)
        assert all(row.readable and row.source_reference is not None for row in prov_a)

        # `link_entity` retains both sides' entity and alias attributions alongside the new link attribution.
        assert {row.alias_value for row in prov_b if row.artifact_kind == "entity_alias"} == {"N. Farid"}
        assert {row.source_version_id for row in prov_assoc_h} == {"v-1", "v-2"}
        assert len(prov_link_h) == 1
        assert prov_link_h[0].origin_kind == "evidence_version"
        assert prov_link_h[0].source_version_id == "v-1"


async def test_deleting_source_version_marks_provenance_deleted_and_artifact_delete_cascades(database) -> None:  # noqa: F811
    async with _world(database) as (conn, engine, runtime, schema):
        await make_version(conn, "v-1", "alice")
        await make_version(conn, "v-2", "alice")
        await make_version(conn, "v-cite", "alice")

        alice = _caller("alice")
        handlers = Handlers(runtime)
        ent_a = await handlers.create_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["create_entity"],
                arguments={
                    "scope": "/u/alice/",
                    "entity_type": "person",
                    "label": "Tariq Aziz",
                    "idempotency_key": "k-del-a",
                    "source_version_ids": ["v-cite"],
                    "aliases": ["T. Aziz", "Tariq A."],
                },
            )
        )
        ent_b = await handlers.create_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["create_entity"],
                arguments={
                    "scope": "/u/alice/",
                    "entity_type": "person",
                    "label": "Tariq Senior",
                    "idempotency_key": "k-del-b",
                    "source_version_ids": ["v-1"],
                },
            )
        )
        assoc_id = await handlers.link(
            Call(
                ctx=alice,
                operation=BY_NAME["link"],
                arguments={
                    "from": "v-1",
                    "to": "v-2",
                    "type": "related_to",
                    "source_version_ids": ["v-cite"],
                },
            )
        )
        link_receipt = await handlers.link_entity(
            Call(
                ctx=alice,
                operation=BY_NAME["link_entity"],
                arguments={
                    "entity_a": ent_a,
                    "entity_b": ent_b,
                    "relation": "same_person",
                    "source_version_ids": ["v-cite"],
                },
            )
        )

        # Delete cited source version `v-cite`: attribution rows stay with origin_kind='source_deleted' & NULL source.
        await engine.call(alice, "delete", {"scope": "memory", "target_ref": "v-cite"})

        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance WHERE source_version_id = 'v-cite'",
        ) == [(0,)]
        async with tenant_scope(conn, "alice", role=runtime.role):
            assoc_after_src = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id=assoc_id,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            ent_after_src = await read_entity_provenance(
                conn,
                user_id="alice",
                entity_id=ent_a,
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
        assert len(assoc_after_src) == 1
        assert assoc_after_src[0].origin_kind == "source_deleted"
        assert assoc_after_src[0].source_version_id is None
        assert assoc_after_src[0].source_reference is None
        assert assoc_after_src[0].label == SOURCE_DELETED_LABEL

        assert len(ent_after_src) == 3  # 1 entity + 2 entity_alias rows
        assert all(
            item.origin_kind == "source_deleted"
            and item.source_version_id is None
            and item.label == SOURCE_DELETED_LABEL
            for item in ent_after_src
        )

        # Deleting owning association, entity_link, and entity cascades all artifact_provenance rows (incl. aliases).
        await engine.call(alice, "unlink", {"association_id": assoc_id})
        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance WHERE artifact_kind = 'association' AND artifact_id = %s",
            (assoc_id,),
        ) == [(0,)]

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute("DELETE FROM entity_link WHERE id = %s", (link_receipt.id,))
        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance WHERE artifact_kind = 'entity_link' AND artifact_id = %s",
            (link_receipt.id,),
        ) == [(0,)]

        await engine.call(alice, "delete", {"scope": "topic", "target_ref": ent_a})
        assert await rows(
            conn,
            "SELECT count(*) FROM artifact_provenance "
            "WHERE artifact_kind IN ('entity', 'entity_alias') AND artifact_id = %s",
            (ent_a,),
        ) == [(0,)]


async def test_pre_migration_rows_render_unattributed_and_never_look_newly_attributed(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=28)),
            prepare=False,
        )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.clock', %s, true)", (T0.isoformat(),))
            await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
            await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
            await conn.execute(
                "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
                "VALUES ('t-alice', 's-alice', 'alice', 1, 'user', 'legacy note', %s)",
                (T0,),
            )
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                "single_valued, content, event_range, recorded_range, layer, visibility) "
                "VALUES ('v-old-1', 'k-old-1', 'alice', 'active', 'user', 'diet', false, 'a', "
                "tstzrange(%s, NULL), tstzrange(%s, NULL), 'semantic', 'private'), "
                "('v-old-2', 'k-old-2', 'alice', 'active', 'user', 'diet', false, 'b', "
                "tstzrange(%s, NULL), tstzrange(%s, NULL), 'semantic', 'private')",
                (T0, T0, T0, T0),
            )
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                "VALUES ('v-old-1', 't-alice', 'alice', 'legacy note', 'support'), "
                "('v-old-2', 't-alice', 'alice', 'legacy note', 'support')"
            )
            await conn.execute(
                "INSERT INTO association (id, user_id, from_id, to_id, type, created_by, origin) "
                "VALUES ('assoc-pre', 'alice', 'v-old-1', 'v-old-2', 'related_to', 'alice', 'tier_c')"
            )
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases) "
                "VALUES ('ent-pre-a', 'alice', '/u/alice/', 'legacy alice', 'person', ARRAY['Legacy Alias']), "
                "('ent-pre-b', 'alice', '/u/alice/', 'legacy bob', 'person', '{}')"
            )
            await conn.execute(
                "INSERT INTO entity_link (id, user_id, entity_a, entity_b, relation) "
                "VALUES ('el-pre', 'alice', 'ent-pre-a', 'ent-pre-b', 'same_person')"
            )

    # Applying 29..VERSION backfills pre-existing artifacts as 'pre_migration'.
    assert (await runner.apply()).action == "applied"

    alice = _caller("alice")
    async with connect(database) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            ent_prov = await read_entity_provenance(
                conn,
                user_id="alice",
                entity_id="ent-pre-a",
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            assoc_prov = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id="assoc-pre",
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            link_prov = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="entity_link",
                artifact_id="el-pre",
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )
            missing_prov = await read_artifact_provenance(
                conn,
                user_id="alice",
                artifact_kind="association",
                artifact_id="assoc-unbackfilled-id",
                caller_user_id="alice",
                read_set=alice.read_set,
                provenance_key=PROV_KEY,
                schema_name=schema,
            )

    assert len(ent_prov) == 2
    assert {item.artifact_kind for item in ent_prov} == {"entity", "entity_alias"}
    for group in (ent_prov, assoc_prov, link_prov):
        for item in group:
            assert item.origin_kind == "pre_migration"
            assert item.label == UNATTRIBUTED_PRE_MIGRATION_LABEL
            assert item.created_by == "pre_migration"
            assert item.source_version_id is None
            assert item.source_reference is None
    assert len(missing_prov) == 1
    assert missing_prov[0].origin_kind == "pre_migration"
    assert missing_prov[0].label == UNATTRIBUTED_PRE_MIGRATION_LABEL


async def test_m29_rollback_restores_v28_catalog_and_refuses_when_attributed_rows_exist(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))

    async with connect(database) as conn:
        recorded_28 = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 28")
        assert len(recorded_28) == 1

        # 1. Clean downgrade to 29, verify 29->28 succeeds on empty DB and restores exact version-28 catalog.
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=28)),
            prepare=False,
        )
        assert await runner._catalog(conn) == recorded_28[0][0]
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'artifact_provenance'",
        )

    assert (await runner.apply()).action == "applied"

    # 2. At version 29 with a non-pre_migration provenance row, rollback 29->28 refuses unless discard is set.
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=29)),
            prepare=False,
        )
        async with conn.transaction():
            await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO entity (id, user_id, scope_path, canonical_name, entity_type, aliases) "
                "VALUES ('e-m29', 'alice', '/u/alice/', 'layla', 'person', '{}')"
            )
            await record_entity_provenance(
                conn,
                user_id="alice",
                entity_id="e-m29",
                origin_kind="tier_c_call",
                created_by="alice",
                schema_name=schema,
            )

        down_29 = cast(LiteralString, rollback_sql(from_version=29, to_version=28))
        with pytest.raises(RaiseException, match="m29_rollback_refused_artifact_provenance_exists"):
            async with conn.transaction():
                await conn.execute(down_29, prepare=False)

        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(29,)]

        recorded_28 = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = 28")
        async with conn.transaction():
            await conn.execute("SET LOCAL cme.rollback_discard = 'artifact_provenance'")
            await conn.execute(down_29, prepare=False)

        assert await runner._catalog(conn) == recorded_28[0][0]
        # Entity itself remains without attribution triggers blocking commit.
        assert await rows(conn, "SELECT id FROM entity WHERE id = 'e-m29'") == [("e-m29",)]

    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION
