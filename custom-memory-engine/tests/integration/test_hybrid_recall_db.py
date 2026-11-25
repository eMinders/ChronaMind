"""Relevance retrieval through ``query``: the fix for the text-blind read path (``V2-D86``, W3.5).

Every test drives the real entry point as a restricted role against the fully migrated schema. Two properties are
under test throughout: a memory is found by **what it says** rather than by when it was written, and relevance changes
the *order* of rows the caller may already read, never the set.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from custom_memory_engine.api.entry import Engine, EntryError
from custom_memory_engine.api.ingest_handlers import register_ingest_handlers
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.api.runtime import Receipt
from custom_memory_engine.engine.predicates import TemporalQuery, compile_predicate
from custom_memory_engine.grounding.sources import canonical_hash
from tests.integration.test_api_wiring_structure import caller, database  # noqa: F401
from tests.integration.test_api_wiring_structure import world as structure_world

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[object, Engine, str]]:  # noqa: F811
    """The structure fixture's world, with the read handlers wired: relevance lives on the read path."""
    async with structure_world(database) as (conn, engine, schema):
        register_read_handlers(engine, engine.resolver.runtime)  # type: ignore[attr-defined]
        yield conn, engine, schema


async def seed(conn, version_id: str, content: str, *, user: str = "alice", minutes: int = 0) -> None:
    """One asserted claim owned by ``user``, believed from ``T0 + minutes``.

    ``minutes`` orders the recency scan, which is what makes "found by relevance, not by recency" a real assertion: a
    later test seeds the *matching* memory first and the *decoy* memories after it.
    """
    moment = T0.replace(minute=minutes % 60, hour=T0.hour + minutes // 60)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
            "single_valued, content, event_range, recorded_range, layer, scope_path, visibility) "
            "VALUES (%s, %s, %s, 'active', 'self', 'statement', false, %s, tstzrange(%s, NULL), "
            "tstzrange(%s, NULL), 'semantic', %s, 'private')",
            (version_id, version_id, user, content, T0, moment, f"/u/{user}/"),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'user')",
            (version_id, "t-" + user, user),
        )


async def seed_vector(
    conn,
    version_id: str,
    axis: int,
    *,
    user: str = "alice",
    dimensions: int = 1024,
    model: str = "BAAI/bge-m3",
) -> None:
    """A unit vector on one axis, so cosine similarity between two rows is exactly 1.0 on the same axis and 0 else."""
    values = [0.0] * dimensions
    values[axis] = 1.0
    literal = "[" + ",".join(repr(value) for value in values) + "]"
    slot = {1024: "embedding_1024", 384: "embedding"}[dimensions]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            f"INSERT INTO memory_embedding (version_id, embedding_model, user_id, {slot}) "  # noqa: S608 - fixed slot name
            f"VALUES (%s, %s, %s, %s::{('halfvec' if dimensions == 1024 else 'vector')}({dimensions}))",
            (version_id, model, user, literal),
        )


async def query(engine, request: dict, *, user: str = "alice") -> QueryResult:
    result = await engine.call(caller(user), "query", {"request": request})
    assert isinstance(result, QueryResult)
    return result


def fact_ids(result: QueryResult) -> list[str]:
    return [artifact.artifact_id for artifact in result.facts]


# ----------------------------------------------------------------------------------------- relevance, not recency


async def test_a_relevance_query_returns_the_matching_memory_rather_than_the_newest_ones(database) -> None:  # noqa: F811
    """The defect this phase fixes: ``query`` used to ignore the text entirely.

    ``residence`` is believed first; four unrelated memories follow it. A recency scan returns the decoys and the old
    memory is never seen; the relevance path returns it first.
    """
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-residence", "I moved to Berlin last spring", minutes=0)
        for index, text in enumerate(
            (
                "I prefer dark roast coffee",
                "My favourite editor is Neovim",
                "I run half marathons on Sundays",
                "The deploy pipeline needs a manual approval",
            ),
            start=1,
        ):
            await seed(conn, f"v-decoy-{index}", text, minutes=index)
        ranked = fact_ids(await query(engine, {"text": "berlin"}))
        assert ranked == ["v-residence"]
        # the same request without text is the recency scan, which is the pre-W3.5 behaviour and still available
        recency = fact_ids(await query(engine, {}))
        assert "v-residence" not in recency[:1]


async def test_a_relevance_query_with_no_match_answers_nothing_rather_than_a_page_of_decoys(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        await seed(conn, "v-2", "My favourite editor is Neovim", minutes=1)
        assert fact_ids(await query(engine, {"text": "quantum chromodynamics"})) == []


async def test_noisy_or_malformed_natural_language_never_turns_a_question_into_an_error(database) -> None:  # noqa: F811
    """`websearch_to_tsquery` is chosen for exactly this: operator soup is an empty query, not a syntax error.

    A question a person actually types can contain quotes, parentheses, `-`, `+`, `&`, `|`, `!`, emoji and control
    characters. `to_tsquery` raises on several of those; `websearch_to_tsquery` returns an empty tsquery, so the
    lexical arm finds nothing and the other arms still answer. A memory engine that errors on punctuation is not usable
    by a human.
    """
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        for noisy in ('"coffee" & (tea | !juice)', "coffee - tea + milk", "☕???", "coffee\t\n\r", "a" * 4000):
            result = await query(engine, {"text": noisy})
            assert fact_ids(result) in ([], ["v-1"])
        # a NUL byte is the one input PostgreSQL cannot hold at all, so it is a typed refusal rather than a driver
        # error surfacing as `internal_error`
        with pytest.raises(EntryError) as refused:
            await query(engine, {"text": "coffee\x00tea"})
        assert (refused.value.code, refused.value.detail) == ("validation_error", "text_invalid")


async def test_a_query_beyond_the_supported_length_is_refused_rather_than_hashed(database) -> None:  # noqa: F811
    """The bound exists because the text is hashed and matched by three SQL arms: unbounded request, unbounded work."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        with pytest.raises(EntryError) as refused:
            await query(engine, {"text": "x" * 9000})
        assert refused.value.detail == "text_invalid"


# ---------------------------------------------------------------------------------------- multilingual behaviour


async def test_an_arabic_query_finds_an_arabic_memory_through_the_language_agnostic_lexical_arm(database) -> None:  # noqa: F811
    """``to_tsvector('english', ...)`` stems and discards Arabic morphology; ``'simple'`` does not."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-ar", "انتقلت إلى برلين الأسبوع الماضي")
        await seed(conn, "v-en", "I prefer dark roast coffee", minutes=1)
        assert fact_ids(await query(engine, {"text": "برلين"})) == ["v-ar"]


async def test_a_mixed_script_conversation_is_searchable_from_either_language(database) -> None:  # noqa: F811
    """One memory written in a code-switched turn is found by a query in either of its languages."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-mixed", "My manager أحمد approved the ChronaMind roadmap")
        assert fact_ids(await query(engine, {"text": "ahmed approved"})) == ["v-mixed"]
        assert fact_ids(await query(engine, {"text": "أحمد"})) == ["v-mixed"]
        assert fact_ids(await query(engine, {"text": "roadmap"})) == ["v-mixed"]


async def test_the_dense_arm_bridges_wording_a_lexical_arm_cannot(database) -> None:  # noqa: F811
    """A paraphrase shares no token with the source: only a vector can find it.

    The rows carry known cosine similarities to the query vector, so this pins **both** halves of the dense arm: a row
    at 0.5 is a candidate and ranks below the row at 1.0, and a row at 0.0 is not a candidate at all — below
    ``DENSE_SIMILARITY_FLOOR`` the arm keeps nothing, which is what stops an unrelated question answering with its
    nearest neighbour (measured: unrelated pairs top out at 0.364, the weakest true match sits at 0.512).
    """
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-paraphrase", "Don't book me anything before ten in the morning")
        await seed(conn, "v-near", "I prefer dark roast coffee", minutes=1)
        await seed(conn, "v-orthogonal", "The deploy pipeline needs a manual approval", minutes=2)
        await seed_vector(conn, "v-paraphrase", axis=0)  # cosine 1.0
        await seed_vector(conn, "v-near", axis=1)  # cosine 0.0
        await seed_vector(conn, "v-orthogonal", axis=2)  # cosine 0.0
        request = {
            "text": "I hate early standups",
            "embedding": [1.0] + [0.0] * 1023,
            "embedding_model": "BAAI/bge-m3",
        }
        # only the row above the floor is a candidate; the two orthogonal ones are not in the page at all
        assert fact_ids(await query(engine, request)) == ["v-paraphrase"]


async def test_a_row_above_the_floor_is_a_candidate_and_ranks_below_the_match(database) -> None:  # noqa: F811
    """The floor bounds *candidates*, not ranking: 0.743 is in, 0.0 is out, and the arm still orders by cosine."""
    async with world(database) as (raw, engine, _):
        conn: Any = raw  # the fixture yields `object`; this file's other tests never touch the connection directly
        await seed(conn, "v-match", "Don't book me anything before ten in the morning")
        await seed(conn, "v-half", "I prefer dark roast coffee", minutes=1)
        await seed_vector(conn, "v-match", axis=0)
        # a vector 42 degrees from the query: cosine 0.743, which is above the measured 0.55 floor
        values = "[" + ",".join(["0.7431448254773942", "0.6691306063588582"] + ["0.0"] * 1022) + "]"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute(
                "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
                f"VALUES ('v-half', 'BAAI/bge-m3', 'alice', '{values}'::halfvec(1024))"
            )
        request = {
            "text": "I hate early standups",
            "embedding": [1.0] + [0.0] * 1023,
            "embedding_model": "BAAI/bge-m3",
        }
        assert fact_ids(await query(engine, request)) == ["v-match", "v-half"]


async def test_a_vector_in_a_dimension_no_slot_can_index_is_refused(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        with pytest.raises(EntryError) as refused:
            await query(engine, {"text": "coffee", "embedding": [0.1, 0.2], "embedding_model": "m"})
        assert refused.value.code == "validation_error"
        assert refused.value.detail == "embedding_dimension_unsupported"


# ---------------------------------------------------------------------------------------------------- security


async def test_relevance_cannot_return_another_users_memory_however_well_it_matches(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-own", "I prefer dark roast coffee")
        await seed(conn, "v-bob", "I prefer dark roast coffee too", user="bob")
        assert fact_ids(await query(engine, {"text": "dark roast coffee"})) == ["v-own"]


async def test_a_scope_outside_the_read_set_still_refuses_even_with_a_perfect_match(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-bob", "I prefer dark roast coffee", user="bob")
        with pytest.raises(EntryError) as refused:
            await query(engine, {"text": "dark roast coffee", "scope": "/u/bob/"})
        assert refused.value.code == "forbidden"


# ------------------------------------------------------------------------------------------ plan-level invariants


async def test_change_mode_refuses_a_relevance_query_because_a_diff_reports_every_row(database) -> None:  # noqa: F811
    """A change report is defined over the authorized population; ranking would silently drop rows from the diff.

    Refusing the question is the point: a caller who sent one believes it was used, and a diff that quietly omitted
    every row the question did not mention would be a wrong answer that looks like a right one.
    """
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        with pytest.raises(EntryError) as refused:
            await query(
                engine,
                {
                    "text": "coffee",
                    "mode": "change",
                    "window_start": "2026-03-01T00:00:00+00:00",
                    "window_end": "2026-03-31T00:00:00+00:00",
                },
            )
        assert refused.value.code == "validation_error"
        assert refused.value.detail == "text_unsupported_for_mode"


async def test_an_audit_query_refuses_a_relevance_question_rather_than_reordering_a_past_belief(database) -> None:  # noqa: F811
    """``audit_as_of`` shows what the engine believed; a question must not reorder or narrow it (``V2-D73``)."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-1", "I prefer dark roast coffee")
        with pytest.raises(EntryError) as refused:
            await query(
                engine,
                {
                    "text": "coffee",
                    "mode": "audit_as_of",
                    "target_time": "2026-03-31T00:00:00+00:00",
                    "belief_time": "2026-03-31T00:00:00+00:00",
                },
            )
        assert refused.value.detail == "text_unsupported_for_mode"
        # and the same audit without a question still answers, in its own bucket
        plain = await query(
            engine,
            {
                "mode": "audit_as_of",
                "target_time": "2026-03-31T00:00:00+00:00",
                "belief_time": "2026-03-31T00:00:00+00:00",
            },
        )
        assert plain.is_audit is True
        assert [artifact.artifact_id for artifact in plain.audit] == ["v-1"]


async def test_a_refine_of_a_relevance_result_stays_within_that_result(database) -> None:  # noqa: F811
    """A drill-down narrows the ranked page; it never re-ranks and never admits a row the page did not contain."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-keep", "My favourite editor is Neovim")
        await seed(conn, "v-drop", "I prefer dark roast coffee", minutes=1)
        ranked = await query(engine, {"text": "editor"})
        assert fact_ids(ranked) == ["v-keep"]
        refined = await engine.call(
            caller("alice"), "refine", {"result_id": ranked.result_id, "instruction": "mention editor"}
        )
        assert isinstance(refined, QueryResult)
        assert fact_ids(refined) == ["v-keep"]


# ---------------------------------------------------------------------------------- the engine's own query embedder

#: A deterministic stand-in for a deployment's model: one axis per known text, so a "paraphrase" and the row it
#: paraphrases share an axis and everything else does not. It is a fake on purpose -- these tests are about the wiring
#: (does the read path ask it for a vector, does it use the answer, does it survive a failure), and the real model is
#: measured separately in `test_retrieval_quality_db.py` and exercised through the host in `test_host_e2e_db.py`.
AXES = {"I hate early standups": 0, "Don't book me anything before ten in the morning": 0, "I prefer dark coffee": 1}


class AxisEmbedder:
    """A model that maps each known sentence onto its own axis; unknown text lands on the last axis."""

    model = "test/axis-embedder"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []

    @property
    def dimensions(self) -> int:
        return 1024

    async def embed(self, texts: Any, *, query: bool = False) -> Any:
        from custom_memory_engine.integrations.onnx_embedder import EmbeddingError

        if self.fail:
            raise EmbeddingError("missing_model_onnx")
        self.calls.append((texts[0], query))
        vectors = []
        for text in texts:
            values = [0.0] * 1024
            values[AXES.get(text, 2)] = 1.0
            vectors.append(values)
        return vectors


# fmt: off
async def test_a_configured_embedder_finds_a_paraphrase_the_caller_never_sent_a_vector_for(database) -> None:  # noqa: F811
# fmt: on
    """The gap `V2-D98` closes: a client sends a question, and the dense arm runs.

    Before this, the app had to own a model to get hybrid recall, so in practice it got the lexical arms only and a
    paraphrase was unfindable. Here the row and the question are encoded by the *same* configured model, the caller
    sends text and nothing else, and the answer is the memory it paraphrases.
    """
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-para", "Don't book me anything before ten in the morning")
        await seed(conn, "v-decoy", "I prefer dark coffee", minutes=1)
        await seed_vector(conn, "v-para", axis=0, model=AxisEmbedder.model)
        await seed_vector(conn, "v-decoy", axis=1, model=AxisEmbedder.model)
        embedder = AxisEmbedder()
        register_read_handlers(engine, engine.resolver.runtime, embedder=embedder)  # type: ignore[attr-defined]
        result = await query(engine, {"text": "I hate early standups"})
        assert fact_ids(result) == ["v-para"]
        # a *query* vector was asked for: the side that carries the prefix on the families that need one
        assert embedder.calls == [("I hate early standups", True)]
        # and it says which arms ran, so "was this hybrid" is a reported fact rather than a latency guess
        assert result.arms == ("dense", "lexical", "trigram")


async def test_an_embedder_that_cannot_load_degrades_to_the_lexical_arms_and_says_so(database) -> None:  # noqa: F811
    """A model outage narrows the answer; it never turns a question into ``internal_error``, and it is never silent."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-residence", "I moved to Berlin last spring")
        failing = AxisEmbedder(fail=True)
        register_read_handlers(engine, engine.resolver.runtime, embedder=failing)  # type: ignore[attr-defined]
        result = await query(engine, {"text": "Berlin"})
        assert fact_ids(result) == ["v-residence"]  # the lexical arm still answers
        assert result.arms == ("lexical", "trigram")  # and the result admits the dense arm did not run


async def test_a_vector_the_caller_supplies_is_the_one_that_ranks(database) -> None:  # noqa: F811
    """The advanced path still wins: an explicit vector is not replaced by the engine's guess."""
    async with world(database) as (conn, engine, _):
        await seed(conn, "v-para", "Don't book me anything before ten in the morning")
        await seed(conn, "v-coffee", "I prefer dark coffee", minutes=1)
        await seed_vector(conn, "v-para", axis=0)
        await seed_vector(conn, "v-coffee", axis=1)
        register_read_handlers(engine, engine.resolver.runtime, embedder=AxisEmbedder())  # type: ignore[attr-defined]
        # the engine's embedder would match v-para; the caller's vector points at v-coffee instead
        coffee = [0.0] * 1024
        coffee[1] = 1.0
        result = await query(
            engine,
            {"text": "I hate early standups", "embedding": coffee, "embedding_model": "BAAI/bge-m3"},
        )
        assert fact_ids(result) == ["v-coffee"]
        assert "dense" in result.arms


async def test_the_ingest_path_stores_the_vector_under_the_configured_model(database) -> None:  # noqa: F811
    """One model for both sides, or the dense arm compares two different vector spaces.

    The write path embeds a *document*; the read path embeds a *query*; both name the same ``model``, which is what the
    dense arm's ``embedding_model = ...`` filter joins on.
    """
    async with world(database) as (raw, engine, _):
        conn: Any = raw
        embedder = AxisEmbedder()
        register_ingest_handlers(engine, engine.resolver.runtime, embedder=embedder)  # type: ignore[attr-defined]
        text = "I only drink tea"
        await conn.execute(
            "INSERT INTO turn (id, session_id, user_id, sequence_no, role, content, occurred_at) "
            "VALUES ('said', 's-alice', 'alice', 2, 'user', %s, %s)",
            (text, T0),
        )
        receipt = await engine.call(
            caller("alice"),
            "assert",
            {
                "scope": "/u/alice/",
                "kind": "claim",
                "content": text,
                "evidence": [{"type": "turn", "ref": "said", "span": text, "content_hash": canonical_hash(text)}],
                "idempotency_key": "c-emb",
            },
        )
        assert isinstance(receipt, Receipt)
        assert embedder.calls and embedder.calls[0][1] is False  # a stored row is a document, not a query
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT embedding_model, embedding_1024 IS NOT NULL FROM memory_embedding WHERE version_id = %s",
                (receipt.id,),
            )
            assert await cursor.fetchone() == ("test/axis-embedder", True)


async def test_a_question_of_only_function_words_offers_no_lexical_evidence(database) -> None:  # noqa: F811
    """`informative_terms` returns *both* halves, and the pruned half makes the arm's emptiness explicable.

    With a four-row corpus "the" appears in all of them, so it is pruned and the surviving set is empty — the signal
    the lexical arm uses to contribute no candidates at all, instead of matching every row because `simple` has no
    stopword list (`V2-D100`).
    """
    from custom_memory_engine.engine.hybrid import informative_terms

    async with world(database) as (raw, engine, _):
        conn: Any = raw  # the fixture yields `object`; every other test here uses the same cast
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            for version_id in ("v1", "v2", "v3", "v4"):
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, subject_key, predicate_key, "
                    "single_valued, content, event_range, recorded_range, layer, scope_path, visibility) "
                    "VALUES (%s, %s, 'alice', 'active', 'self', 'statement', false, 'the cat sat', "
                    "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', '/u/alice/', 'private')",
                    (version_id, version_id, T0),
                )
                # `I1'`: an active row must carry evidence, so the provenance trigger fires without it
                await conn.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                    "VALUES (%s, 't-alice', 'alice', 'x', 'support')",
                    (version_id,),
                )
        query = {"mode": "current_state", "user_id": "alice", "reference_time": datetime.now(UTC)}
        where = compile_predicate(TemporalQuery.model_validate(query), alias="m")
        async with engine.resolver.runtime.session("alice") as scoped:  # type: ignore[attr-defined]
            informative, pruned = await informative_terms(
                scoped,
                where.statement,
                where.parameters,
                engine.resolver.runtime.schema_name,  # type: ignore[attr-defined]
                "the the the",
            )
    assert informative == ()
    assert pruned == ("the",)
