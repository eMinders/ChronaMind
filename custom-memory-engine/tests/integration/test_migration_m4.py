"""E-013 / M4: evidence becomes a source reference (type, ref, span, hash); turn evidence stays byte-identical."""

import hashlib
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation

from custom_memory_engine.engine.deletion import ExternalExclusion, delete_memory
from custom_memory_engine.engine.sources import fetch_sources
from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    EvidenceInput,
    _copy_provenance,
    commit_initial_version,
)
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import adapt, rollback_sql
from tests.integration.test_migration_m1 import (
    START,
    connect,
    database,  # noqa: F401  (fixture)
    rows,
)

pytestmark = pytest.mark.integration
M4 = 8
EXTERNAL_TEXT = "def f():\r\n    return 1\n"
EXTERNAL_HASH = canonical_hash(EXTERNAL_TEXT)

#: Turn texts that stress the canonicalisation: CRLF/CR, NFD vs NFC, astral characters, empty-ish whitespace.
TURNS = {
    "t-plain": "I am vegetarian",
    "t-crlf": "line one\r\nline two\rline three\n",
    "t-nfd": "café au lait",  # decomposed é
    "t-nfc": "café au lait",
    "t-emoji": "plan \U0001f331 salad — 日本語",
}


async def seed_turns(conn: AsyncConnection) -> None:
    await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s1', 'u1', %s)", (START,))
    for number, (turn_id, content) in enumerate(TURNS.items(), start=1):
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES (%s, 's1', 'u1', %s, 'user', %s, %s)",
            (turn_id, number, content, START),
        )


async def insert_version(conn: AsyncConnection, version_id: str, key: str = "diet") -> None:
    await conn.execute(
        await adapt(
            conn,
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, single_valued, "
            "content, event_range, recorded_range, layer) VALUES (%s, %s, 'u1', 'active', 'user', 'diet', false, 'x', "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic')",
        ),
        (version_id, key, START),
    )


def external_candidate(version_id: str, key: str = "ext") -> CandidateVersionInput:
    return CandidateVersionInput(
        version_id=version_id,
        memory_key=key,
        user_id="u1",
        subject_key="user",
        predicate_key="diet",
        content="the tool run passed",
        valid_from=START,
        single_valued=False,
        layer="semantic",
        evidence=(
            EvidenceInput(
                source_type="external", source_ref="ci://run/42", content_hash=EXTERNAL_HASH, evidence_span="lines 1-2"
            ),
        ),
    )


OLD_COLUMNS_MD5 = (
    "SELECT md5(string_agg(concat_ws('|', version_id, turn_id, user_id, evidence_span, evidence_role), E'\\n' "
    "ORDER BY version_id, turn_id, evidence_span)) FROM memory_evidence"
)


# ----------------------------------------------------------------------------------------------- backfill


async def test_backfill_is_byte_identical_and_hashes_match_the_python_canonicalisation(database) -> None:  # noqa: F811
    config, schema = database
    async with connect(database) as conn:
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M4 - 1)), prepare=False
        )
        assert await rows(
            conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'memory_evidence'",
        ) == [(5,)]
        await seed_turns(conn)
        async with conn.transaction():
            for number, turn_id in enumerate(TURNS):
                await insert_version(conn, f"v{number}", key=f"k{number}")
                for span in ("span a", "span b"):
                    await conn.execute(
                        "INSERT INTO memory_evidence VALUES (%s, %s, 'u1', %s, 'primary')",
                        (f"v{number}", turn_id, span),
                    )
        before = await rows(conn, cast(LiteralString, OLD_COLUMNS_MD5))
        old_rows = await rows(
            conn,
            "SELECT version_id, turn_id, user_id, evidence_span, evidence_role FROM memory_evidence ORDER BY 1, 2, 4",
        )
        assert len(old_rows) == 2 * len(TURNS)

    await migrate.MigrationRunner(
        migrate.MigrationConfig(database_url=config.database_url, schema_name=schema)
    ).apply()

    async with connect(database) as conn:
        assert await rows(conn, cast(LiteralString, OLD_COLUMNS_MD5)) == before  # the old columns are byte-identical
        assert (
            await rows(
                conn,
                "SELECT version_id, turn_id, user_id, evidence_span, evidence_role FROM memory_evidence "
                "ORDER BY 1, 2, 4",
            )
            == old_rows
        )
        migrated = await rows(
            conn,
            "SELECT e.turn_id, e.source_type, e.source_ref, e.span, e.evidence_span, e.content_hash, t.content "
            "FROM memory_evidence e JOIN turn t ON t.id = e.turn_id ORDER BY 1, 4",
        )
        assert len(migrated) == 2 * len(TURNS)
        for turn_id, source_type, source_ref, span, legacy_span, content_hash, content in migrated:
            assert (source_type, source_ref, span) == ("turn", turn_id, legacy_span)
            assert content_hash == canonical_hash(str(content)), turn_id  # SQL and Python canonicalise identically
        # NFC and NFD spellings of the same text hash the same; CRLF/CR/LF do too
        by_turn = {row[0]: row[5] for row in migrated}
        assert by_turn["t-nfd"] == by_turn["t-nfc"]
        assert by_turn["t-crlf"] == canonical_hash("line one\nline two\nline three\n")


async def test_a_v1_writer_still_inserts_turn_evidence_unchanged(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_turns(conn)
        async with conn.transaction():
            await insert_version(conn, "v1")
            await conn.execute(
                "INSERT INTO memory_evidence VALUES ('v1', 't-plain', 'u1', 'I am vegetarian', 'support')"
            )
        assert await rows(
            conn, "SELECT source_type, source_ref, span, content_hash FROM memory_evidence WHERE version_id = 'v1'"
        ) == [("turn", "t-plain", "I am vegetarian", hashlib.sha256(b"I am vegetarian").hexdigest())]


# ----------------------------------------------------------------------------------------------- shape rules


async def test_external_sources_need_a_hash_and_a_locator_and_no_engine_row(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        async with conn.transaction():
            await insert_version(conn, "v1")
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, user_id, evidence_span, evidence_role, source_type, "
                "source_ref, span, content_hash) VALUES ('v1', 'u1', 'lines 1-2', 'support', 'external', "
                "'ci://run/42', 'lines 1-2', %s)",
                (EXTERNAL_HASH,),
            )
        assert await rows(conn, "SELECT turn_id, source_type, source_ref FROM memory_evidence") == [
            (None, "external", "ci://run/42")
        ]
        bad_inserts = {
            "unknown_type": ("url", None, "ci://x", "s", EXTERNAL_HASH),  # tool/file/event are declared (m17)
            "artifact_is_not_evidence": ("artifact", None, "a1", "s", EXTERNAL_HASH),
            "short_hash": ("external", None, "ci://x", "s", "abc"),
            "upper_hash": ("external", None, "ci://x", "s", EXTERNAL_HASH.upper()),
            "blank_ref": ("external", None, "  ", "s", EXTERNAL_HASH),
            "blank_span": ("external", None, "ci://x", " ", EXTERNAL_HASH),
        }
        for source_type, turn_id, ref, span, content_hash in bad_inserts.values():
            with pytest.raises(CheckViolation):
                async with conn.transaction():
                    await insert_version(conn, "bad")
                    await conn.execute(
                        "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role, "
                        "source_type, source_ref, span, content_hash) "
                        "VALUES ('bad', %s, 'u1', %s, 'r', %s, %s, %s, %s)",
                        (turn_id, span, source_type, ref, span, content_hash),
                    )
        with pytest.raises(RaiseException, match="external_source_has_no_turn"):
            async with conn.transaction():
                await seed_turns(conn)
                await insert_version(conn, "bad")
                await conn.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role, "
                    "source_type, source_ref, span, content_hash) VALUES ('bad', 't-plain', 'u1', 's', 'r', "
                    "'external', 'ci://x', 's', %s)",
                    (EXTERNAL_HASH,),
                )


async def test_turn_sources_keep_referential_integrity_and_cannot_disagree_with_the_legacy_columns(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await seed_turns(conn)
        with pytest.raises(ForeignKeyViolation):  # a dangling turn is still refused
            async with conn.transaction():
                await insert_version(conn, "v1")
                await conn.execute("INSERT INTO memory_evidence VALUES ('v1', 'no-such-turn', 'u1', 's', 'r')")
        with pytest.raises(CheckViolation, match="turn_source_check"):  # source_ref must equal turn_id
            async with conn.transaction():
                await insert_version(conn, "v2")
                await conn.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role, "
                    "source_type, source_ref, span, content_hash) VALUES ('v2', 't-plain', 'u1', 's', 'r', 'turn', "
                    "'t-crlf', 's', %s)",
                    (EXTERNAL_HASH,),
                )
        with pytest.raises(UniqueViolation):  # the new key is the old key for turns
            async with conn.transaction():
                await insert_version(conn, "v3")
                await conn.execute("INSERT INTO memory_evidence VALUES ('v3', 't-plain', 'u1', 'same', 'a')")
                await conn.execute("INSERT INTO memory_evidence VALUES ('v3', 't-plain', 'u1', 'same', 'b')")


async def test_invariant_1_holds_over_generalised_sources(database) -> None:  # noqa: F811
    """An active version needs evidence or derivation; an external source counts, nothing at all does not."""
    async with connect(database) as conn:
        with pytest.raises(RaiseException, match="requires_evidence_or_derivation"):
            async with conn.transaction():
                await insert_version(conn, "bare")
        _, schema = database
        async with conn.transaction():
            await commit_initial_version(conn, external_candidate("ext1"), schema_name=schema, replay_time=START)
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'ext1'") == [(1,)]


# ------------------------------------------------------------------------------------------ store, retrieve, delete


async def test_an_external_episode_is_stored_retrieved_with_provenance_and_deleted_with_an_exclusion(database) -> None:  # noqa: F811
    _, schema = database
    async with connect(database) as conn:
        async with conn.transaction():
            await commit_initial_version(conn, external_candidate("ext1"), schema_name=schema, replay_time=START)
        sources = await fetch_sources(conn, "u1", "ext1", schema_name=schema)
        assert [(s.source_type, s.source_ref, s.span, s.content_hash, s.role) for s in sources] == [
            ("external", "ci://run/42", "lines 1-2", EXTERNAL_HASH, "primary")
        ]
        assert sources[0].in_deletion_boundary is False

        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="ext", schema_name=schema)
        assert receipt.deleted_versions == 1
        assert receipt.external_exclusions == (ExternalExclusion("external", EXTERNAL_HASH),)
        assert await rows(conn, "SELECT count(*) FROM memory_version") == [(0,)]
        assert await rows(conn, "SELECT count(*) FROM memory_evidence") == [(0,)]
        assert receipt.external_exclusion_count == 1
        # the audit record keeps a count only: no hash, no locator, no text (RL-072)
        (details,) = (
            await rows(conn, "SELECT details::text FROM audit_event WHERE event_type = 'deletion_completed'")
        )[0]
        assert '"external_exclusions": 1' in str(details)
        assert (
            EXTERNAL_HASH not in str(details) and "ci://run/42" not in str(details) and "lines 1-2" not in str(details)
        )

        async with conn.transaction():
            replay = await delete_memory(
                conn, "u1", scope="memory", target_ref="ext", deletion_epoch=receipt.deletion_epoch, schema_name=schema
            )
        # an idempotent retry reports how many sources were excluded; which ones was the original receipt's to carry
        assert (replay.external_exclusion_count, replay.external_exclusions) == (1, ())


async def test_deleting_turn_evidence_lists_no_exclusion_and_still_purges_jobs(database) -> None:  # noqa: F811
    _, schema = database
    async with connect(database) as conn:
        await seed_turns(conn)
        async with conn.transaction():
            await insert_version(conn, "v1", key="mem")
            await conn.execute("INSERT INTO memory_evidence VALUES ('v1', 't-plain', 'u1', 'I am vegetarian', 'r')")
            await conn.execute(
                "INSERT INTO job (id, user_id, kind, turn_id, idempotency_key, status, attempts, next_run_at, "
                "payload) VALUES ('j1', 'u1', 'extract', 't-plain', 'k1', 'queued', 0, %s, '{}')",
                (START,),
            )
        async with conn.transaction():
            receipt = await delete_memory(conn, "u1", scope="memory", target_ref="mem", schema_name=schema)
        assert (
            receipt.deleted_versions,
            receipt.purged_jobs,
            receipt.external_exclusions,
            receipt.external_exclusion_count,
        ) == (
            1,
            1,
            (),
            0,
        )


async def test_mixed_turn_and_external_evidence_is_copied_whole_when_provenance_is_reissued(database) -> None:  # noqa: F811
    _, schema = database
    async with connect(database) as conn:
        await seed_turns(conn)
        async with conn.transaction():
            await insert_version(conn, "old", key="a")
            await conn.execute("INSERT INTO memory_evidence VALUES ('old', 't-plain', 'u1', 'I am vegetarian', 'r')")
            await conn.execute(
                "INSERT INTO memory_evidence (version_id, user_id, evidence_span, evidence_role, source_type, "
                "source_ref, span, content_hash) VALUES ('old', 'u1', 'L1', 'r', 'external', 'ci://x', 'L1', %s)",
                (EXTERNAL_HASH,),
            )
            await insert_version(conn, "new", key="b")
            async with conn.cursor() as cursor:
                await _copy_provenance(cursor, schema, from_version_id="old", to_version_id="new", user_id="u1")
        select = (
            "SELECT turn_id, evidence_span, evidence_role, source_type, source_ref, span, content_hash "
            "FROM memory_evidence WHERE version_id = %s ORDER BY source_type"
        )
        assert await rows(conn, select, ("new",)) == await rows(conn, select, ("old",))
        assert len(await rows(conn, select, ("new",))) == 2


# ----------------------------------------------------------------------------------------------- rollback


async def test_rollback_refuses_while_non_turn_sources_exist_and_loses_nothing(database) -> None:  # noqa: F811
    _, schema = database
    async with connect(database) as conn:
        async with conn.transaction():
            await commit_initial_version(conn, external_candidate("ext1"), schema_name=schema, replay_time=START)
        with pytest.raises(RaiseException, match="m4_rollback_refused_non_turn_sources_exist"):
            async with conn.transaction():
                await conn.execute(
                    cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M4 - 1)), prepare=False
                )
        assert await rows(conn, "SELECT source_type FROM memory_evidence") == [("external",)]
        assert await rows(conn, "SELECT max(version) FROM _cme_schema_migrations") == [(migrate.VERSION,)]


async def test_rollback_with_only_turn_sources_restores_version_7_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        await seed_turns(conn)
        async with conn.transaction():
            await insert_version(conn, "v1")
            await conn.execute("INSERT INTO memory_evidence VALUES ('v1', 't-plain', 'u1', 'I am vegetarian', 'r')")
        before = await rows(conn, cast(LiteralString, OLD_COLUMNS_MD5))
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M4 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M4 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # byte-identical to the pre-M4 schema
        assert await rows(conn, cast(LiteralString, OLD_COLUMNS_MD5)) == before  # and the data is untouched
    status = await runner.status()
    assert (status.state, status.version) == ("pending", M4 - 1)
    assert (await runner.apply()).action == "applied"
    async with connect(database) as conn:
        assert await rows(conn, cast(LiteralString, OLD_COLUMNS_MD5)) == before
        assert await rows(conn, "SELECT source_type, source_ref FROM memory_evidence") == [("turn", "t-plain")]
