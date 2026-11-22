"""``semantic.ingestor``: two lanes over the real write path (W3.5, ``V2-D91``).

These tests run against the migrated schema as a restricted role, because the claim being made is not "the orchestrator
is wired correctly" but "an extraction result ends up governed": provenance, the temporal exclusion, the authority
ladder, the channel routing and the deletion boundary all still decide. The model-shaped collaborators are fakes -- an
extractor that returns fixed claims, an embedder that returns a fixed vector -- so what is measured is the engine's
behaviour, not a model's.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.engine.ingest import TurnInput
from custom_memory_engine.semantic.contracts import (
    AdjudicatedRelation,
    EntityMention,
    ExtractedClaim,
    ExtractionRequest,
    ExtractionResult,
    TemporalBounds,
)
from custom_memory_engine.semantic.ingestor import IngestionReport, ingest_turn
from tests.integration.test_api_wiring_structure import caller, database  # noqa: F401
from tests.integration.test_api_wiring_structure import world as structure_world

pytestmark = pytest.mark.integration
NOW = datetime(2026, 3, 10, 12, tzinfo=UTC)

#: A deterministic 1024-wide unit vector: the dense arm's slot, with no model anywhere.
VECTOR: tuple[float, ...] = (1.0, *(0.0,) * 1023)


class FakeExtractor:
    """Returns whatever it was constructed with. The engine's job is to check it, not to produce it."""

    def __init__(self, *claims: ExtractedClaim, version: str = "fake-extractor@1") -> None:
        self._claims = claims
        self._version = version
        self.requests: list[ExtractionRequest] = []

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        return ExtractionResult(claims=self._claims, extractor_version=self._version, language="ar")


class FakeEmbedder:
    model = "fake/bge-m3"
    dimensions = 1024

    async def embed(self, texts: Any) -> list[tuple[float, ...]]:
        return [VECTOR for _ in texts]


class FakeAdjudicator:
    def __init__(self, *proposals: AdjudicatedRelation) -> None:
        self._proposals = proposals
        self.seen: tuple[tuple[str, str], ...] = ()

    async def adjudicate(self, claim: ExtractedClaim, existing: Any, *, language: str | None = None) -> Any:
        del claim, language
        self.seen = tuple(existing)
        return self._proposals


async def write_episode(connection: Any, *, user_id: str, scope_path: str, turn: TurnInput, span: str) -> str:
    """The fast lane's episode write, in the shape a deployment supplies.

    Kept as a small fake so this test measures the *orchestrator* rather than re-testing ``ingest_episode``, which has
    its own suite. It persists nothing beyond the turn the orchestrator already wrote.
    """
    del connection, user_id, scope_path, span
    return f"episode-of-{turn.turn_id}"


def turn(text: str = "I moved to Berlin last spring", *, turn_id: str = "t1") -> TurnInput:
    """One user turn. The sequence number is derived from the id, because ``turn`` is `UNIQUE (session_id,
    sequence_no)` and two turns in one session would otherwise collide."""
    suffix = turn_id[1:]
    return TurnInput(
        turn_id=turn_id,
        user_id="alice",
        session_id="s-alice",
        sequence_no=100 + int(suffix) if suffix.isdigit() else 199,
        role="user",
        content=text,
        occurred_at=NOW,
    )


async def ingest(engine: Any, schema: str, turn_input: TurnInput, **kwargs: Any) -> IngestionReport:
    """One ingestion in its own transaction, which is how a turn arrives in production.

    ``allocate_clock`` allocates once per tenant transaction, so two turns sharing one transaction contend for a single
    belief instant; a deployment that batches a session's turns must allocate once and pass that tick onwards, which is
    a different (and larger) decision than these tests need.
    """
    async with engine.resolver.runtime.session("alice") as session:  # type: ignore[attr-defined]
        return await ingest_turn(
            session,
            user_id="alice",
            scope_path="/u/alice/",
            turn=turn_input,
            write_episode=write_episode,
            schema_name=schema,
            **kwargs,
        )


def claim(**overrides: Any) -> ExtractedClaim:
    base = ExtractedClaim(
        content="alice lives in Berlin",
        verbatim_quote="moved to Berlin",
        cardinality="single",
        semantic_slot="residence",
        um_facet="context",
    )
    return replace(base, **overrides)


async def scalar(conn: Any, statement: str) -> Any:
    cursor = await conn.execute(statement)
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def columns(conn: Any, statement: str) -> tuple[Any, ...]:
    """One row of separate columns (a composite ``SELECT (a, b)`` renders every field as text)."""
    cursor = await conn.execute(statement)
    found = await cursor.fetchone()
    assert found is not None
    return tuple(found)


# ------------------------------------------------------------------------------------------------ the fast lane


async def test_without_an_extractor_nothing_is_understood_and_the_turn_is_still_durable(database) -> None:  # noqa: F811
    """The configuration a latency-critical deployment runs on the request path."""
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(engine, schema, turn())
        assert report.turn_id == "t1"
        assert report.episode_id == "episode-of-t1"
        assert report.claims == ()
        assert await scalar(conn, "SELECT count(*) FROM turn WHERE id = 't1'") == 1
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0


# ------------------------------------------------------------------------------------------------ the slow lane


async def test_a_grounded_claim_is_written_with_every_governed_field(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()), embedder=FakeEmbedder())
        assert [item.outcome for item in report.claims] == ["written"]
        assert await columns(
            conn,
            "SELECT semantic_slot, cardinality_hint, um_facet, assertion_mode, single_valued FROM memory_version",
        ) == ("residence", "single", "context", "asserted", True)
        # the evidence is the quote, so provenance still reaches the turn
        assert await scalar(conn, "SELECT evidence_span FROM memory_evidence") == "moved to Berlin"
        # the embedding landed in the 1024 slot, which is the one the dense arm searches
        assert await scalar(conn, "SELECT vector_dims(embedding_1024) FROM memory_embedding") == 1024


async def test_an_ungrounded_claim_is_refused_with_the_reason_and_nothing_is_written(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim(verbatim_quote="moved to Munich")))
        assert [(item.outcome, item.reason_code) for item in report.claims] == [("rejected", "quote_not_in_source")]
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0


async def test_a_fabricated_number_is_refused_before_anything_is_stored(database) -> None:  # noqa: F811
    """Veracium's specifics check, end to end: the quote is real and the number inside the claim is not."""
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(
            engine,
            schema,
            turn("I moved to Berlin last spring"),
            extractor=FakeExtractor(claim(content="alice moved to Berlin in 2019")),
        )
        assert report.claims[0].reason_code == "specifics_not_in_source"
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0


async def test_a_temporal_bound_after_the_utterance_is_refused_for_an_asserted_claim(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(
            engine,
            schema,
            turn(),
            extractor=FakeExtractor(claim(temporal=TemporalBounds(start="2026-09-01T00:00:00+00:00"))),
        )
        assert report.claims[0].reason_code == "event_start_after_occurrence"
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 0


async def test_a_planned_claim_may_point_into_the_future_and_is_routed_to_plans_to(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        report = await ingest(
            engine,
            schema,
            turn("I will move to Berlin in September"),
            extractor=FakeExtractor(
                claim(
                    verbatim_quote="move to Berlin in September",
                    assertion_mode="planned",
                    temporal=TemporalBounds(start="2026-09-01T00:00:00+00:00"),
                )
            ),
        )
        assert [item.outcome for item in report.claims] == ["written"]
        assert await scalar(conn, "SELECT predicate_key FROM memory_version") == "plans_to"


async def test_an_adjudicated_contradiction_opens_a_conflict_rather_than_resolving_it(database) -> None:  # noqa: F811
    """The second claim contradicts the first; both must survive, and the engine must not pick a winner."""
    async with structure_world(database) as (conn, engine, schema):
        first = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        existing = first.claims[0].version_id
        assert existing is not None
        second = await ingest(
            engine,
            schema,
            turn("Actually I live in Munich now", turn_id="t2"),
            extractor=FakeExtractor(claim(content="alice lives in Munich", verbatim_quote="live in Munich now")),
            adjudicator=FakeAdjudicator(AdjudicatedRelation(existing_id=existing, relation="contradiction")),
        )
        assert [item.outcome for item in second.claims] == ["conflict"]
        assert await scalar(conn, "SELECT count(*) FROM association WHERE type = 'contradicts'") == 1
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 2


async def test_a_contradicting_claim_is_held_and_readable_as_a_conflict(database) -> None:  # noqa: F811
    """The end-to-end property, read back through `query` and `get`.

    A new claim that contradicts a current belief in the same slot cannot be a second *active* belief (the GiST
    exclusion forbids it) and must not replace the user's own statement (nothing has shown it to be wrong). The
    engine's established answer for exactly that situation is a **held candidate**: `conflict_pending`, which is
    `contested`, not current, and therefore absent from `query` -- while `get` still returns it, routed to
    `conflicts`, and an adjudication can accept or reject it. Both the fact and the disagreement are preserved, and
    neither is laundered into the other.

    Retiring the belief the candidate contradicts is `C-026`'s refutation path (`W5`); inventing it here would be a
    second, unreviewed lifecycle transition (`V2-D89`).
    """
    async with structure_world(database) as (conn, engine, schema):
        register_read_handlers(engine, engine.resolver.runtime)  # type: ignore[attr-defined]
        first = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        existing = first.claims[0].version_id
        assert existing is not None
        second = await ingest(
            engine,
            schema,
            turn("Actually I live in Munich now", turn_id="t2"),
            extractor=FakeExtractor(claim(content="alice lives in Munich", verbatim_quote="live in Munich now")),
            adjudicator=FakeAdjudicator(AdjudicatedRelation(existing_id=existing, relation="contradiction")),
        )
        candidate = second.claims[0].version_id
        assert candidate is not None
        # the held candidate is not current, so a query answers with the belief the user actually asserted
        result = await engine.call(caller("alice"), "query", {"request": {}})
        assert isinstance(result, QueryResult)
        assert [item.artifact_id for item in result.facts] == [existing]
        # ... and it is still readable, in the conflicts channel, with the disagreement recorded
        held = await engine.call(caller("alice"), "get", {"artifact_id": candidate})
        assert held.kind == "claim"  # type: ignore[union-attr]
        assert held.channel == "conflicts"  # type: ignore[union-attr]
        assert await scalar(conn, "SELECT count(*) FROM association WHERE type = 'contradicts'") == 1


async def test_a_weaker_source_cannot_supersede_a_users_own_statement(database) -> None:  # noqa: F811
    """The adjudicator proposes an update; the ladder refuses it because the *turn role* decides, not the text."""
    async with structure_world(database) as (conn, engine, schema):
        first = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        existing = first.claims[0].version_id
        assert existing is not None
        assistant = replace(
            turn("the CRM says alice lives in Munich", turn_id="t2"), role="assistant", sequence_no=201
        )
        second = await ingest(
            engine,
            schema,
            assistant,
            extractor=FakeExtractor(claim(content="alice lives in Munich", verbatim_quote="alice lives in Munich")),
            adjudicator=FakeAdjudicator(AdjudicatedRelation(existing_id=existing, relation="update")),
        )
        # an assistant turn is weaker than the user's own words: it holds or is refused, and never supersedes
        assert second.claims[0].outcome in ("rejected", "conflict")
        assert await scalar(conn, "SELECT count(*) FROM memory_version WHERE status = 'active'") == 1


async def test_a_duplicate_reinforces_instead_of_accumulating_copies(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        first = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        existing = first.claims[0].version_id
        assert existing is not None
        second = await ingest(
            engine,
            schema,
            turn("I moved to Berlin last spring, as I said", turn_id="t2"),
            extractor=FakeExtractor(claim()),
            adjudicator=FakeAdjudicator(AdjudicatedRelation(existing_id=existing, relation="duplicate")),
        )
        assert [item.outcome for item in second.claims] == ["reinforced"]
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 1
        assert await scalar(conn, "SELECT reinforcement_count FROM memory_version") == 1


async def test_a_correction_supersedes_and_the_old_belief_stays_auditable(database) -> None:  # noqa: F811
    async with structure_world(database) as (conn, engine, schema):
        first = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        existing = first.claims[0].version_id
        assert existing is not None
        second = await ingest(
            engine,
            schema,
            turn("Actually I moved to Munich", turn_id="t2"),
            extractor=FakeExtractor(claim(content="alice lives in Munich", verbatim_quote="moved to Munich")),
            adjudicator=FakeAdjudicator(AdjudicatedRelation(existing_id=existing, relation="correction")),
        )
        assert [item.outcome for item in second.claims] == ["superseded"]
        # R1's close-then-insert: exactly **one current belief**. `upper_inf(recorded_range)` is what "current" means
        # (`recorded_iff_believed`, migration 1) -- a row whose recorded range is closed is a belief the engine held
        # until that instant, and it stays `active` on purpose so a historical query can still answer with it.
        assert (
            await scalar(
                conn,
                "SELECT count(*) FROM memory_version WHERE status = 'active' AND upper_inf(recorded_range)",
            )
            == 1
        )
        assert await scalar(conn, "SELECT count(*) FROM memory_version") == 2


async def test_an_entity_mention_is_not_linked_without_resolution(database) -> None:  # noqa: F811
    """`V2-D90`'s remaining criterion: resolution is not wired yet, so the claim is stored against the subject and the
    entity link stays unset rather than naming a string that looks like an id."""
    async with structure_world(database) as (conn, engine, schema):
        await ingest(
            engine,
            schema,
            turn(),
            extractor=FakeExtractor(claim(entities=(EntityMention(label="Alice"),))),
        )
        assert await scalar(conn, "SELECT subject_key FROM memory_version") == "self"
        assert await scalar(conn, "SELECT subject_entity_id FROM memory_version") is None


async def test_ingestion_cannot_leave_the_callers_namespace(database) -> None:  # noqa: F811
    async with structure_world(database) as (_conn, engine, schema):
        async with engine.resolver.runtime.session("alice") as session:  # type: ignore[attr-defined]
            with pytest.raises(Exception, match="scope_outside_the_callers_namespace"):
                await ingest_turn(
                    session,
                    user_id="alice",
                    scope_path="/u/bob/",
                    turn=turn(),
                    write_episode=write_episode,
                    schema_name=schema,
                )


async def test_a_turn_is_persisted_once_even_if_the_slow_lane_runs_twice(database) -> None:  # noqa: F811
    """Re-extraction is the recovery path, and ``persist_turn`` is idempotent by key, so a retry is safe."""
    async with structure_world(database) as (conn, engine, schema):
        for _ in range(2):
            await ingest(engine, schema, turn())
        assert await scalar(conn, "SELECT count(*) FROM turn WHERE id = 't1'") == 1


async def test_a_report_never_carries_the_turns_content(database) -> None:  # noqa: F811
    """Logging channel 7: a report is a record of a decision, not a copy of what the user said."""
    async with structure_world(database) as (_conn, engine, schema):
        report = await ingest(engine, schema, turn(), extractor=FakeExtractor(claim()))
        rendered = repr(report)
        assert "Berlin" not in rendered
        assert "residence" not in rendered
        assert report.extractor_version == "fake-extractor@1"
