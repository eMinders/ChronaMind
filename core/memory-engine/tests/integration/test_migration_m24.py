"""Migration 24: the open semantic slot, the cardinality hint and source authority (``V2-D87``, W3.5).

The properties this migration owns: a slot is a soft, normalized grouping key with no catalog to register into; a
single-valued belief is exclusive **within its slot**, so two unrelated facts about the same subject can be active at
once without the exclusion constraint refusing a legal write; a slot cannot be re-pointed in place; and an evidence
item's authority is derived from what it is, not from what it says it is.
"""

from datetime import UTC, datetime, timedelta
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ExclusionViolation, GeneratedAlways, RaiseException

from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import JULY, connect, database, rows  # noqa: F401  (fixture; u1, u2)

pytestmark = pytest.mark.integration
M24 = 24
T0 = datetime(2026, 3, 10, tzinfo=UTC)


async def claim(
    conn: AsyncConnection,
    version_id: str,
    content: str,
    *,
    user: str = "u1",
    single_valued: bool = False,
    semantic_slot: str | None = None,
    cardinality_hint: str | None = None,
    evidence_role: str = "user",
    source_type: str | None = None,
    event_start: datetime = T0,
    event_end: datetime | None = None,
) -> None:
    """One asserted claim, written directly, with an optional slot and a chosen evidence role.

    A session and a turn are created per claim: `turn` is `UNIQUE (session_id, sequence_no)`, so two claims sharing a
    session would silently skip the second turn insert and then fail the evidence foreign key.
    """
    session, turn_id = f"s-m24-{version_id}", f"t-m24-{version_id}"
    await conn.execute(
        "INSERT INTO session (id, user_id, started_at) VALUES (%s, %s, clock_now())",
        (session, user),
    )
    # `turn.role` admits only 'user' and 'assistant'; the authority ladder distinguishes more roles than a dialogue
    # turn does, so the role under test is the *evidence* role, which is what the generated column reads.
    await conn.execute(
        "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
        "VALUES (%s, %s, %s, 1, 'user', %s, clock_now())",
        (turn_id, session, user, content),
    )
    await conn.execute(
        "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
        "content, event_range, recorded_range, layer, valid_from_basis, assertion_mode, extractor_version, "
        "scope_path, kind, lifecycle, retention, semantic_slot, cardinality_hint) "
        "VALUES (%s, %s, %s, 'active', 'self', 'statement', %s, %s, tstzrange(%s, %s), "
        "tstzrange(clock_now(), NULL), 'semantic', 'inferred', 'asserted', 'test', %s, 'claim', 'asserted', "
        "'durable', %s, %s)",
        (
            version_id,
            version_id,
            user,
            single_valued,
            content,
            event_start,
            event_end,
            f"/u/{user}/",
            semantic_slot,
            cardinality_hint,
        ),
    )
    if source_type is None:
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, user_id, turn_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, %s, %s)",
            (version_id, user, turn_id, content, evidence_role),
        )
    else:
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, user_id, turn_id, evidence_span, evidence_role, source_type, "
            "source_ref, span, content_hash) "
            "VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s)",
            (version_id, user, content, evidence_role, source_type, f"ref-{version_id}", content, "a" * 64),
        )


async def test_a_slot_is_an_open_key_with_no_catalog_to_register_into(database) -> None:  # noqa: F811
    """`predicate_schema` stays closed for structure; the domain vocabulary is not a table."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I live in Berlin", semantic_slot="residence", single_valued=True)
        await claim(conn, "v2", "My favourite editor is Neovim", semantic_slot="preferred_editor", single_valued=True)
        assert await rows(conn, "SELECT id, semantic_slot FROM memory_version ORDER BY id") == [
            ("v1", "residence"),
            ("v2", "preferred_editor"),
        ]


async def test_two_single_valued_facts_in_different_slots_coexist(database) -> None:  # noqa: F811
    """The defect the slot-scoped exclusion key removes.

    Both rows are single-valued, in one scope, about the same subject, under the same generic predicate, and both are
    active with overlapping event and recorded ranges. Under the version-1 key they collide and the second is refused.
    """
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "residence", "I live in Berlin", semantic_slot="residence", single_valued=True)
        await claim(conn, "editor", "I use Neovim", semantic_slot="preferred_editor", single_valued=True)
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE status = 'active'") == [(2,)]


async def test_two_single_valued_facts_in_the_same_slot_cannot_overlap(database) -> None:  # noqa: F811
    """`I5` is preserved, not dropped: exclusivity still holds where it is meant to hold."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "berlin", "I live in Berlin", semantic_slot="residence", single_valued=True)
        with pytest.raises(ExclusionViolation):
            async with conn.transaction():
                await claim(conn, "munich", "I live in Munich", semantic_slot="residence", single_valued=True)


async def test_a_multi_valued_row_is_never_exclusive_even_in_a_slot(database) -> None:  # noqa: F811
    """An `event` or `multi` belief is a series, not a state; the exclusion applies only to single-valued rows."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "a", "I met Sara in Berlin", semantic_slot="meeting", cardinality_hint="event")
        await claim(conn, "b", "I met Sara in Munich", semantic_slot="meeting", cardinality_hint="event")
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(2,)]


@pytest.mark.parametrize("slot", ["  padded  ", "", "x" * 121, "bad\u0007slot"])
async def test_a_slot_is_normalized_at_the_row(database, slot: str) -> None:  # noqa: F811
    """The normalization is the schema's, so a backfill and a handler are held to the same rule."""
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(CheckViolation):
            async with conn.transaction():
                await claim(conn, "v1", "I live in Berlin", semantic_slot=slot)


async def test_a_cardinality_hint_is_one_of_three_values_or_absent(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(CheckViolation):
            async with conn.transaction():
                await claim(conn, "v1", "I live in Berlin", cardinality_hint="sometimes")
        await claim(conn, "v2", "I live in Berlin", cardinality_hint="single")


async def test_a_slot_cannot_be_re_pointed_in_place(database) -> None:  # noqa: F811
    """A slot is a belief column like `subject_key`: moving it would rewrite what the claim is about."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I live in Berlin", semantic_slot="residence")
        with pytest.raises(RaiseException, match="may change"):
            await conn.execute("UPDATE memory_version SET semantic_slot = 'other' WHERE id = 'v1'")


@pytest.mark.parametrize(
    ("role", "source_type", "rank"),
    [
        ("user", None, 0),
        ("system", None, 1),
        ("assistant", None, 2),
        ("tool", "tool", 3),
        ("file", "file", 3),
        ("event", "event", 3),
        ("external", "external", 3),
        ("user", "tool", 3),  # the source type wins: a claimed user role on third-party content proves nothing
        ("root", None, 3),  # an unrecognised role is untrusted, not trusted by default
    ],
)
async def test_authority_is_derived_from_the_source_and_cannot_be_declared(
    database,  # noqa: F811
    role: str,
    source_type: str | None,
    rank: int,
) -> None:
    """Veracium's ladder is a guarantee only if the text it governs cannot set its own rank."""
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I live in Berlin", evidence_role=role, source_type=source_type)
        assert await rows(conn, "SELECT authority_rank FROM memory_evidence WHERE version_id = 'v1'") == [(rank,)]


async def test_authority_cannot_be_written_by_a_caller(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await claim(conn, "v1", "I live in Berlin")
        with pytest.raises(GeneratedAlways):
            async with conn.transaction():
                await conn.execute("UPDATE memory_evidence SET authority_rank = 0 WHERE version_id = 'v1'")


async def test_rollback_restores_version_23_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M24 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M24 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]
        assert before != recorded[0][0]
        for column in ("semantic_slot", "cardinality_hint"):
            assert not await rows(
                conn,
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'memory_version' AND column_name = %s",
                (column,),
            )
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_evidence' AND column_name = 'authority_rank'",
        )
        definition = await rows(
            conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'no_bitemporal_overlap'"
        )
        assert "semantic_slot" not in str(definition[0][0])
    assert (await runner.status()).version == M24 - 1
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == migrate.VERSION


async def test_rolling_back_refuses_rather_than_destroying_a_belief_it_cannot_represent(database) -> None:  # noqa: F811
    """A downgrade must be safe to *run*: it preflights the one state version 23 cannot hold and says so.

    Version 23's key cannot hold two active single-valued beliefs under one predicate, and neither resolution is
    available inside a rollback -- archiving needs a covering successor (``enforce_archival_invariant``), deleting
    destroys an asserted memory. So the rollback refuses, counts the collisions without naming a row, and leaves the
    ledger untouched; the operator resolves the pair through the engine and reruns.
    """
    config, schema = database
    async with connect(database) as conn:
        async with conn.transaction():
            await claim(conn, "residence", "I live in Berlin", semantic_slot="residence", single_valued=True)
            await claim(conn, "editor", "I use Neovim", semantic_slot="preferred_editor", single_valued=True)
        with pytest.raises(RaiseException) as refused:
            await conn.execute(
                cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M24 - 1)), prepare=False
            )
        assert "migration_024_downgrade_blocked" in str(refused.value)
        assert "2" in str(refused.value)  # the count, never an id
        # nothing was written: both beliefs are still active and both slots are still there
        assert await rows(conn, "SELECT id, status FROM memory_version ORDER BY id") == [
            ("editor", "active"),
            ("residence", "active"),
        ]


async def test_rolling_back_succeeds_when_the_two_slots_do_not_overlap(database) -> None:  # noqa: F811
    """The refusal is a preflight, not a veto: a pair version 15 *can* represent downgrades cleanly.

    Two single-valued beliefs in different slots whose event ranges are disjoint are legal under both keys, so the
    narrower one accepts them and only the slot columns go.
    """
    async with connect(database) as conn:
        async with conn.transaction():
            await claim(conn, "residence", "I lived in Berlin", semantic_slot="residence", single_valued=True,
                        event_start=T0 - timedelta(days=10), event_end=T0)  # fmt: skip
            await claim(conn, "editor", "I use Neovim", semantic_slot="preferred_editor", single_valued=True)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M24 - 1)), prepare=False
        )
        assert await rows(conn, "SELECT id, status FROM memory_version ORDER BY id") == [
            ("editor", "active"),
            ("residence", "active"),
        ]
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'memory_version' AND column_name = 'semantic_slot'",
        )
