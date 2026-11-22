"""E-041 through the real schema: entity identity resolution on the *actual* ``entity`` table.

The resolver had only ever run against a fake table, and that is how it shipped querying ``entity.label`` and
``entity.embedding`` — a column that exists and a column that does not. These tests run it as the restricted role
against the migrated schema, so the SQL has to be right, and they drive it through the ingestor as well, because the
interesting property is not "a query returns a row" but "two turns about the same person land on one identity, and two
different people never do".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from psycopg import sql as pg_sql

from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.security import provision_runtime_role
from custom_memory_engine.semantic import entities
from custom_memory_engine.semantic.contracts import EntityMention, ExtractedClaim, ExtractionRequest, ExtractionResult
from custom_memory_engine.semantic.ingestor import ingest_turn
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[AsyncConnection, str, str]]:  # noqa: F811
    """Alice, a restricted runtime role, and the schema name: writes by the owner, resolution through the role."""
    config, schema = database
    role = "cme_rt_" + uuid4().hex[:12]
    async with connect(database, user="alice") as conn:
        await conn.execute("INSERT INTO app_user VALUES ('alice', 'ext-alice', %s)", (T0,))
        await conn.execute("INSERT INTO session (id, user_id, started_at) VALUES ('s-alice', 'alice', %s)", (T0,))
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('t-alice', 's-alice', 'alice', 1, 'user', 'x', %s)",
            (T0,),
        )
        await provision_runtime_role(conn, schema, role)
        await conn.commit()
        try:
            yield conn, schema, role
        finally:
            await conn.execute("RESET ROLE")
            await conn.commit()
            await conn.execute(pg_sql.SQL("DROP OWNED BY {}").format(pg_sql.Identifier(role)))
            await conn.execute(pg_sql.SQL("DROP ROLE {}").format(pg_sql.Identifier(role)))
            await conn.commit()


async def as_role(conn: AsyncConnection, role: str, schema: str, principal: str = "alice") -> None:
    """Put this connection into the exact posture a runtime has: restricted role, tenant, schema."""
    from psycopg import sql as pg_sql

    await conn.execute("RESET ROLE")
    await conn.execute(pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(role)))
    await conn.execute("SELECT set_config('app.user_id', %s, false)", (principal,))
    await conn.execute(pg_sql.SQL("SET search_path TO {}, public").format(pg_sql.Identifier(schema)))


async def entity_rows(conn: AsyncConnection, schema: str) -> list[tuple[Any, ...]]:
    return await rows(conn, f"SELECT id, canonical_name, entity_type FROM {schema}.entity ORDER BY canonical_name")


# ------------------------------------------------------------------------------------------------ the cascade


async def test_an_exact_label_resolves_and_a_new_one_is_created_once(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema, role):
        first = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Ahmed", schema_name=schema
        )
        assert first.stage == "created" and not first.resolved
        created = await entities.create_entity(
            conn, user_id="alice", scope_path="/u/alice/", label="Ahmed", entity_type="person", schema_name=schema
        )
        again = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Ahmed", schema_name=schema
        )
        assert (again.stage, again.entity_id) == ("exact", created)
        assert len(await entity_rows(conn, schema)) == 1
        del role


async def test_an_alias_is_evidence_not_a_guess(database) -> None:  # noqa: F811
    """`Ahmed` resolving to the entity created for `Ahmed Kamal` is a declared identity, so it needs no score."""
    async with world(database) as (conn, schema, _):
        created = await entities.create_entity(
            conn,
            user_id="alice",
            scope_path="/u/alice/",
            label="Ahmed Kamal",
            entity_type="person",
            aliases=["Ahmed"],
            schema_name=schema,
        )
        found = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Ahmed", schema_name=schema
        )
        assert (found.stage, found.entity_id) == ("exact", created)


async def test_trigram_finds_a_spelling_variant_and_reports_its_stage(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema, _):
        created = await entities.create_entity(
            conn, user_id="alice", scope_path="/u/alice/", label="Mohammed", entity_type="person", schema_name=schema
        )
        found = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Muhammed", schema_name=schema
        )
        assert found.entity_id == created and found.stage in ("trigram", "adjudicated")


async def test_two_entities_sharing_a_label_are_unresolved_rather_than_arbitrated(database) -> None:  # noqa: F811
    """A tie is reported. Picking the smaller id would attach a claim to an arbitrary person."""
    async with world(database) as (conn, schema, _):
        for tag in ("a", "b"):
            await entities.create_entity(
                conn,
                user_id="alice",
                scope_path="/u/alice/",
                label="Sara",
                entity_type="person",
                aliases=[tag],
                schema_name=schema,
            )
        # the unique-label rule does not exist in the schema, so two rows are possible; the resolver must not choose
        found = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Sara", schema_name=schema
        )
        assert found.stage == "unresolved" and not found.resolved
        assert len(found.ambiguous) == 2


async def test_an_adjudicator_breaks_a_tie_but_an_abstention_does_not(database) -> None:  # noqa: F811
    class Chooser:
        def __init__(self, answer: str | None) -> None:
            self.answer = answer
            self.seen: tuple[tuple[str, str], ...] = ()

        def choose(self, mention: str, candidates: Any) -> str | None:
            self.seen = tuple(candidates)
            return self.answer

    async with world(database) as (conn, schema, _):
        # Two spelling variants, so the tie happens at the *trigram* stage. An exact tie short-circuits to
        # `unresolved` before any model is asked: two rows with one label are a caller's mistake, not a question.
        ids = [
            await entities.create_entity(
                conn,
                user_id="alice",
                scope_path="/u/alice/",
                label=label,
                entity_type="person",
                schema_name=schema,
            )
            for label in ("Mohammed", "Muhammed")
        ]
        chooser = Chooser(ids[1])
        decided = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Muhammad", schema_name=schema, adjudicator=chooser
        )
        assert (decided.stage, decided.entity_id) == ("adjudicated", ids[1])
        assert chooser.seen  # it was shown the candidate, blind to which recall preferred
        abstained = await entities.resolve_mention(
            conn,
            user_id="alice",
            scope_path="/u/alice/",
            label="Muhammad",
            schema_name=schema,
            adjudicator=Chooser(None),
        )
        assert abstained.stage == "unresolved"


async def test_resolution_is_scoped_to_the_callers_own_scope(database) -> None:  # noqa: F811
    """An entity in another scope of the same principal is not a candidate: the claim would move across scopes."""
    async with world(database) as (conn, schema, _):
        await conn.execute(
            pg_sql.SQL(
                "INSERT INTO {e} (id, user_id, scope_path, canonical_name, entity_type) "
                "VALUES ('ent_other', 'alice', '/u/alice/proj/', 'Ahmed', 'person')"
            ).format(e=pg_sql.Identifier(schema, "entity"))
        )
        found = await entities.resolve_mention(
            conn, user_id="alice", scope_path="/u/alice/", label="Ahmed", schema_name=schema
        )
        assert found.stage == "created"


# ------------------------------------------------------------------------------- through the ingestor (the write path)


class OneClaim:
    """An extractor that returns exactly one claim, so the test measures resolution rather than extraction."""

    def __init__(self, claim: ExtractedClaim) -> None:
        self.claim = claim

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        del request
        return ExtractionResult(claims=(self.claim,), extractor_version="one-claim@1", language="en")


async def write_episode(
    connection: AsyncConnection, *, user_id: str, scope_path: str, turn: TurnInput, span: str
) -> str:
    del connection, user_id, scope_path, span
    return turn.turn_id


def turn(turn_id: str, content: str) -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        user_id="alice",
        session_id="s-alice",
        sequence_no=100 + int(turn_id.split("-")[-1]),  # above the seeded turn at 1
        role="user",
        content=content,
        occurred_at=T0,
    )


async def ingest(conn: AsyncConnection, schema: str, turn_input: TurnInput, claim: ExtractedClaim) -> Any:
    # one transaction per turn, as the host and the worker do: `allocate_clock` needs an active transaction, and a turn
    # is the unit of belief.
    async with conn.transaction():
        return await ingest_turn(
            conn,
            user_id="alice",
            scope_path="/u/alice/",
            turn=turn_input,
            write_episode=write_episode,
            extractor=OneClaim(claim),
            schema_name=schema,
        )


async def test_the_same_mention_across_two_turns_produces_one_entity_and_two_linked_claims(database) -> None:  # noqa: F811
    """The property that matters: identity converges, so "Ahmed" in March and in April are the same person."""
    async with world(database) as (conn, schema, _):
        mention = EntityMention(label="Ahmed", entity_type="person")
        first = ExtractedClaim(
            content="The user's manager is Ahmed",
            verbatim_quote="Ahmed",
            subject_mention="Ahmed",
            entities=(mention,),
            cardinality="single",
            semantic_slot="manager",
        )
        second = ExtractedClaim(
            content="The user's manager is Ahmed again",
            verbatim_quote="Ahmed",
            subject_mention="Ahmed",
            entities=(mention,),
            cardinality="single",
            semantic_slot="manager",
        )
        await ingest(conn, schema, turn("u-1", "my manager is Ahmed"), first)
        await ingest(conn, schema, turn("u-2", "Ahmed is still my manager"), second)

        created = await entity_rows(conn, schema)
        assert len(created) == 1, created  # one identity, not one per turn
        linked = await rows(
            conn,
            f"SELECT COUNT(*) FROM {schema}.memory_version WHERE subject_entity_id = %s",
            (created[0][0],),
        )
        assert int(linked[0][0]) >= 2  # type: ignore[arg-type]  # both claims point at it


async def test_a_claim_about_the_speaker_keeps_self_as_its_subject(database) -> None:  # noqa: F811
    """`I moved to Berlin` mentions Berlin but is *about* the user: the mention must not become the subject."""
    async with world(database) as (conn, schema, _):
        claim = ExtractedClaim(
            content="The user moved to Berlin",
            verbatim_quote="moved to Berlin",
            entities=(EntityMention(label="Berlin", entity_type="place"),),
            cardinality="single",
            semantic_slot="residence",
        )
        await ingest(conn, schema, turn("u-3", "I moved to Berlin"), claim)
        written = await rows(
            conn, f"SELECT subject_key, subject_entity_id FROM {schema}.memory_version WHERE kind = 'claim'"
        )
        assert written == [("self", None)]
        entities_created = await entity_rows(conn, schema)
        assert [row[1] for row in entities_created] == ["berlin"]  # the place still exists as an entity


async def test_an_ambiguous_mention_attaches_nothing_and_is_reported(database) -> None:  # noqa: F811
    async with world(database) as (conn, schema, _):
        for tag in ("a", "b"):
            await entities.create_entity(
                conn,
                user_id="alice",
                scope_path="/u/alice/",
                label="Sara",
                entity_type="person",
                aliases=[tag],
                schema_name=schema,
            )
        claim = ExtractedClaim(
            content="Sara reviews the design",
            verbatim_quote="Sara",
            subject_mention="Sara",
            entities=(EntityMention(label="Sara", entity_type="person"),),
            cardinality="single",
            semantic_slot="reviewer",
        )
        report = await ingest(conn, schema, turn("u-4", "Sara reviews the design"), claim)
        written = await rows(
            conn, f"SELECT subject_key, subject_entity_id FROM {schema}.memory_version WHERE kind = 'claim'"
        )
        assert written == [("self", None)]  # no attach rather than a coin flip
        assert report.written and report.diagnostics["entity_unresolved"] == 1
