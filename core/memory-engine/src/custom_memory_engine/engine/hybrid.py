"""Hybrid (multi-arm) recall for the ``query`` path: W3.5, ``V2-D86``.

Before this module, the V2 ``query`` path never read the request's own text. ``execute_plan`` fetched rows
``ORDER BY lower(recorded_range) DESC LIMIT n`` and expanded one association hop; nothing scored relevance, so a
caller with more than a page of memories received the newest ones whatever they asked. The Gate G3 latency comparison
(11.82 ms) was therefore honest about the code and meaningless about the product: it measured a recency scan.

This module is the missing half. It performs the recall the review of the reference engines converged on -- the shape
Hindsight (`MIT`) and Honcho (`AGPL-3.0`, pattern only) both reach -- and it does so inside ChronaMind's own
guarantees rather than beside them:

====================  ==========================================================================
arm                   what it ranks
====================  ==========================================================================
``temporal``          **not an arm**: the compiled temporal predicate plus the caller's read predicate,
                      applied as a filter to every arm. A filter never becomes a score (``§9.3``).
``dense``             cosine distance in the version's embedding slot (migration 23), HNSW-indexed.
``lexical``           ``content_fts_simple`` -- ``to_tsvector('simple', ...)``, the only PostgreSQL
                      configuration with no language, so Arabic, Cyrillic, CJK and mixed-script
                      identifiers tokenise the same way English does.
``trigram``           ``pg_trgm`` substring/similarity on the longest query token, which rescues
                      transliteration, agglutination and partially-typed names that no tokeniser splits
                      the way the document did.
====================  ==========================================================================

The fourth *candidate source* is graph expansion, which the executor already runs after recall
(`engine.expansion`); this module ranks it by hop distance so a reached neighbour is ordered after the row that
reached it.

**Fusion.** Reciprocal Rank Fusion, ``Σ_arms 1/(k + rank)`` with ``k = 60``: rank-based, so arms whose scores live on
incomparable scales (cosine distance, ``ts_rank_cd``, trigram similarity) combine without normalisation, and a version
found by two arms outranks one found by a single arm at the same position. The order is total -- score descending,
then version id ascending -- so two identical calls produce identical output (``V2-D11``'s determinism requirement).

**No generative model, ever.** Every arm is SQL or an injected embedding, and there is **zero** LLM call on this path.
The optional second stage is a *relevance judge* (:class:`RelevanceJudge`): a cross-encoder is deterministic code over
fixed weights, so a deployment that wants one injects it and the same question over the same memory always gets the
same verdict. That is what keeps the hot read path at tens of milliseconds and near-zero marginal cost, and it is why
this module can be replayed. The judge does not live here — it is applied where the candidate rows are already in hand
(`api.read_handlers`), so this module stays pure SQL over ids.

**Security is unchanged.** Each arm composes :func:`~.predicates.scoped_artifact_filter`, so the temporal predicate,
the typed-artifact guard and the caller's read predicate all run *before* the row limit, under the caller's own role
and ``app.user_id``. An arm therefore cannot see a row the caller may not read, and the id list this module returns is
a set of ids the caller could already have fetched -- relevance changes the *order* of authorized rows, never the set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from re import findall
from typing import Literal, LiteralString, Protocol, cast, runtime_checkable

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

from custom_memory_engine.engine.predicates import (
    CompiledPredicate,
    TemporalQuery,
    compile_predicate,
    scoped_artifact_filter,
)

#: Reciprocal Rank Fusion's constant (``V2-D11``, unchanged from v1's two-arm fusion).
RRF_CONSTANT = 60
#: How many candidates each arm contributes before fusion, as a multiple of the requested page. Fusion needs
#: overlapping candidates to reward agreement; one page per arm starves it.
ARM_DEPTH_FACTOR = 4
MIN_ARM_DEPTH = 20
#: The shortest query token a trigram arm will probe. Below three characters a trigram index matches most of the
#: corpus, which is the same as scanning it. Non-ASCII tokens are admitted at two characters because a two-character
#: CJK or Arabic token is a word, not a prefix.
MIN_TRIGRAM_TOKEN = 3
MIN_TRIGRAM_TOKEN_NON_ASCII = 2

#: ``memory_embedding``'s dimension slots (migration 23): width -> column. A dimension outside this map has no slot,
#: so it cannot be stored *and* indexed; refusing it is the honest answer, and adding one is a one-paragraph migration
#: recorded in ``schema/README.md``.
EMBEDDING_SLOTS: Mapping[int, str] = {
    384: "embedding",
    768: "embedding_768",
    1024: "embedding_1024",
    1536: "embedding_1536",
    3072: "embedding_3072",
}
#: PostgreSQL type each slot is declared as. ``halfvec`` for the multilingual slots, ``vector`` for the legacy 384 one
#: (migration 23 changed neither the legacy column's type nor its data).
SLOT_TYPES: Mapping[str, str] = {
    "embedding": "vector",
    "embedding_768": "halfvec",
    "embedding_1024": "halfvec",
    "embedding_1536": "halfvec",
    "embedding_3072": "halfvec",
}

type ArmName = Literal["dense", "lexical", "trigram", "graph"]
#: The arms that rank rows directly from the corpus. ``graph`` is executed by ``engine.expansion`` and only *ordered*
#: here, so it never appears in a recall statement.
RANKING_ARMS: tuple[ArmName, ...] = ("dense", "lexical", "trigram")

#: The share of the caller's own authorized rows a query term may appear in and still count as **evidence** for the
#: lexical arm. Measured, not guessed (`V2-D100`): the ``simple`` configuration has no stopword list — by design, since
#: a stopword list is language-specific — so a question like "what is the wifi password at the office" shares "the",
#: "is" and "at" with most of the corpus and matched memories that answer nothing. On the labelled set this ratio is
#: what separates a question's informative terms from its function words without a single language rule.
TERM_PRUNING_DF_RATIO = 0.35
#: How many query terms the pruning statistic is computed for: the longest ones, which are the most informative in
#: every script. A bound, because the statistic is one bounded index scan per term.
MAX_STATISTIC_TERMS = 6

#: Cosine similarity below which the dense arm keeps nothing. **Measured on the labelled set** in
#: `evaluation/retrieval` (`V2-D100`) by sweeping the constant through the real read path. The criterion is stated
#: before the numbers:
#: minimise the no-match false-positive rate subject to recall@5 >= 0.90, because answering an unanswerable question
#: confidently is the failure this engine exists to avoid.
#:
#: ======  =========  ========  ============================
#: floor   recall@5   nDCG@5    no-match false positives
#: ======  =========  ========  ============================
#: 0.45    1.000      0.900     0.875
#: 0.55    0.982      0.882     0.125
#: 0.60    0.875      0.812     0.125
#: 0.62    0.768      0.728     0.000
#: 0.65    0.714      0.688     0.000
#: 0.70    0.607      0.594     0.000
#: ======  =========  ========  ============================
#:
#: The earlier value (0.45) came from a 16-question set whose two unanswerable questions shared no vocabulary with
#: anything; on the larger set it answered 7 of 8 unanswerable questions with their nearest neighbour. At 0.55 one
#: remains, and going further costs more recall than it buys precision.
#:
#: **What this cannot do.** The weakest *true* match on this set sits at 0.46 and the strongest *false* one at 0.605,
#: so no single cosine threshold separates them: an unanswerable question whose nearest neighbour is unusually close is
#: indistinguishable from a genuine cross-script paraphrase without a cross-encoder. That is a measured limit of the
#: model, not of the threshold. It is why the read path carries a *relevance judge* (`V2-D104`): a cross-encoder reads
#: the pair together and separates exactly this case, where no floor on a bi-encoder's cosine can.
DENSE_SIMILARITY_FLOOR = 0.55


class RecallError(InvalidParameterValue):
    """Static recall contract error (SQLSTATE 22023); never carries a query, a vector or an id."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RecallQuery:
    """What to recall by: the caller's text, and optionally a vector for the dense arm.

    The engine never embeds anything. ``embedding`` is supplied by the caller (or by the deployment's
    :class:`~custom_memory_engine.semantic.contracts.Embedder`) and validated here against the slot map, so a
    dimension the schema cannot index is refused at the boundary rather than silently dropped from the arms.
    """

    text: str
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None
    #: Overrides :data:`DENSE_SIMILARITY_FLOOR` for one call. A deployment that changes embedding model should measure
    #: its own floor rather than inherit this one: the numbers above belong to the model named in the report.
    similarity_floor: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise RecallError("nonblank_query_text_required")
        if self.embedding is None:
            if self.embedding_model is not None:
                raise RecallError("embedding_model_without_embedding")
            return
        if not self.embedding:
            raise RecallError("nonempty_query_embedding_required")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in self.embedding):
            raise RecallError("query_embedding_must_be_numeric")
        if not all(isfinite(float(value)) for value in self.embedding):
            raise RecallError("query_embedding_must_be_finite")
        if not isinstance(self.embedding_model, str) or not self.embedding_model.strip():
            raise RecallError("nonblank_embedding_model_required")
        slot_for_dimension(len(self.embedding))  # refuses a dimension no slot can index

    @property
    def arms(self) -> tuple[ArmName, ...]:
        """The ranking arms this query can run. Lexical and trigram need text; dense needs a vector."""
        return RANKING_ARMS if self.embedding is not None else ("lexical", "trigram")


@dataclass(frozen=True, slots=True)
class ArmRanking:
    """One arm's ranked ids, best first. ``ids`` is de-duplicated and ordered."""

    arm: ArmName
    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Recall:
    """The fused result: the page in rank order, plus the per-arm evidence for the cost record."""

    ordered: tuple[str, ...]
    #: ``(id, fused score)`` in the same order as ``ordered``, so a caller can report or test the ranking without
    #: recomputing it.
    scored: tuple[tuple[str, float], ...]
    arms: tuple[ArmRanking, ...]
    #: How many rows the temporal + read predicate admitted, i.e. the size of the population the arms ranked. A dense
    #: arm cannot report this without a second statement, so it is the lexical arm's count when one ran; ``None``
    #: otherwise (a dense-only recall is bounded by the arm depth, not by a counted population).
    candidates: int | None = None


@runtime_checkable
class RelevanceJudge(Protocol):
    """A *calibrated* second stage: it reads a question and a memory together and returns one absolute score.

    The difference from a reranker is the whole point (`V2-D104`). A reranker's scale is arbitrary, so only its order
    can be used; a relevance judge's score is a decision on a fixed scale, so a floor on it is meaningful and a
    question nothing answers can come back empty instead of merely re-ordered.

    Deliberately **not** an LLM: a generative "is this relevant?" call per candidate would put a model call and a
    per-turn cost inside the hot read. A cross-encoder is one forward pass and one number with no sampling, so the same
    question and the same memory always produce the same verdict.
    """

    @property
    def model(self) -> str: ...

    async def relevance(self, query: str, documents: Sequence[str]) -> Sequence[float]: ...


#: Below this relevance logit a candidate is not an answer to the question. **Measured**, not chosen (`V2-D104`): on
#: the 64-question labelled set (56 answerable, 8 ``no_match``, 49 memories), judged over the fused page where
#: ``DENSE_SIMILARITY_FLOOR = 0.55`` admitted only ``N = 1`` unanswerable question, the weakest *answerable* question
#: scored ``-6.68`` (``-6.6790``) and the surviving *unanswerable* one ``-11.01`` (``-11.0099``), whose midpoint is
#: ``-8.8`` (2.12 logits below the worst real answer and 2.21 above that surviving non-answer). Across the **full
#: uncensored** labelled set without the dense pre-filter (`evaluation/retrieval/judge_floor.py`), the weakest
#: answerable gold pair scores ``-6.68`` and the strongest unanswerable pair across all 49 memories scores ``-9.41``
#: (``-9.4076``, uncensored midpoint ``-8.04``; separation ``2.73`` logits). ``-8.8`` sits strictly inside both
#: intervals (``0.61`` logits above the uncensored ceiling); a deployment that swaps the judge asset must verify or
#: re-derive the floor with ``scripts/check_judge_floor.py``, and ``CME_RERANKER_FLOOR`` overrides it without editing
#: code.
#:
#: This is what the bi-encoder could not do: its own weakest true match (0.46) sat *below* its strongest false one
#: (0.605), so no cosine floor separated them. The judge exists because ranking and judging are different questions.
JUDGE_RELEVANCE_FLOOR = -8.8
#: How many of the fused candidates the judge reads. A bound, because the judge costs ~28 ms per pair and the page it
#: is guarding is small: a relevance gate over the first page is a decision about whether to answer at all.
JUDGE_CANDIDATES = 10


def slot_for_dimension(dimensions: int) -> tuple[str, str]:
    """``(column, type)`` for a vector width, or ``RecallError`` when no slot can index it."""
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise RecallError("embedding_dimension_must_be_an_integer")
    column = EMBEDDING_SLOTS.get(dimensions)
    if column is None:
        raise RecallError("embedding_dimension_unsupported")
    return column, SLOT_TYPES[column]


def vector_literal(values: Sequence[float]) -> str:
    """A pgvector text literal for ``values``.

    Built here rather than left to psycopg's ``list`` adapter on purpose: psycopg renders a Python list as a
    PostgreSQL array (``{0.1,0.2}``), which ``vector`` accepts and **``halfvec`` rejects** -- it requires the
    ``[0.1,0.2]`` form. Relying on the adapter would therefore make the legacy 384 arm work and every multilingual arm
    fail at run time, which is exactly the class of defect this phase exists to remove.
    """
    if not values:
        raise RecallError("nonempty_vector_required")
    rendered: list[str] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecallError("vector_component_must_be_numeric")
        number = float(value)
        if not isfinite(number):
            raise RecallError("vector_component_must_be_finite")
        rendered.append(repr(number))
    return "[" + ",".join(rendered) + "]"


def statistic_terms(text: str, *, limit: int = MAX_STATISTIC_TERMS) -> tuple[str, ...]:
    """The query's distinct word tokens worth a document-frequency probe: the longest, up to ``limit``.

    Length is the only language-agnostic proxy for informativeness this module is allowed to use: a two-character token
    is a word in many scripts and a prefix in Latin ones, so the floor is the same one the trigram arm uses, and the
    ranking heuristic prefers longer tokens rather than applying a stopword list.
    """
    if not isinstance(text, str):
        raise RecallError("nonblank_query_text_required")
    seen: dict[str, int] = {}
    for token in findall(r"\w+", text):
        lowered = token.casefold()
        minimum = MIN_TRIGRAM_TOKEN if lowered.isascii() else MIN_TRIGRAM_TOKEN_NON_ASCII
        if len(lowered) < minimum:
            continue
        seen.setdefault(lowered, len(lowered))
    ordered = sorted(seen, key=lambda token: (-seen[token], token))
    return tuple(ordered[:limit])


async def informative_terms(
    connection: AsyncConnection,
    where: sql.Composable,
    parameters: Mapping[str, object],
    schema_name: str,
    text: str,
    *,
    limit: int = MAX_STATISTIC_TERMS,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the query's terms into ``(informative, pruned)`` by their document frequency in the caller's own rows.

    This is the statistical replacement for a stopword list, and it lives here rather than in a table because the
    statistic must be *the caller's*: it is computed inside the same temporal + read predicate the arms use, under
    row-level security, so the counts a question reveals are counts of rows that caller may already read. A global IDF
    table would be both a write-path cost and a cross-tenant oracle.

    One statement, one bounded index scan per term. An empty term list is an empty answer: a question made only of
    words that appear everywhere in the caller's corpus has no lexical evidence to offer, which is exactly what
    "answer nothing rather than the nearest thing" requires.
    """
    terms = statistic_terms(text, limit=limit)
    if not terms:
        return (), ()
    # Named placeholders, because the statement also binds the arm's own `%(...)s` parameters (the temporal and read
    # predicates). Psycopg cannot mix the two styles in one statement.
    values = sql.SQL(", ").join(sql.SQL("(%(term_{})s)").format(sql.Literal(index)) for index in range(len(terms)))
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT t.term, (SELECT count(*) FROM {table} AS m WHERE {where}"
                " AND m.content_fts_simple @@ pg_catalog.plainto_tsquery('simple', t.term)) AS df,"
                " (SELECT count(*) FROM {table} AS m WHERE {where}) AS total"
                " FROM (VALUES {values}) AS t(term)"
            ).format(table=sql.Identifier(schema_name, "memory_version"), where=where, values=values),
            {**parameters, **{f"term_{index}": term for index, term in enumerate(terms)}},
        )
        rows = await cursor.fetchall()
    total = int(rows[0][2]) if rows else 0
    if not total:
        return (), terms
    ceiling = max(1, int(total * TERM_PRUNING_DF_RATIO))
    informative = tuple(str(term) for term, df, _ in rows if int(df) <= ceiling)
    pruned = tuple(term for term in terms if term not in informative)
    return informative, pruned


def trigram_token(text: str) -> str | None:
    """The longest word of ``text`` worth a trigram probe, or ``None`` when none is long enough.

    Unicode-aware (``\\w`` under Python's default string patterns is not ASCII-only), so an Arabic or CJK token is
    found rather than the ASCII leftovers of one. Length thresholds differ by script for the reason given at
    :data:`MIN_TRIGRAM_TOKEN`: two characters is a word in many scripts and a prefix in Latin ones.
    """
    if not isinstance(text, str):
        return None
    best: str | None = None
    for token in findall(r"\w+", text):
        minimum = MIN_TRIGRAM_TOKEN if token.isascii() else MIN_TRIGRAM_TOKEN_NON_ASCII
        if len(token) < minimum:
            continue
        if best is None or len(token) > len(best):
            best = token
    return best


def fuse(arms: Sequence[ArmRanking], *, limit: int, k: int = RRF_CONSTANT) -> tuple[tuple[str, float], ...]:
    """Reciprocal Rank Fusion over any number of arms: ``Σ 1/(k + rank)``, ties on id ascending.

    ``limit`` is the page size; ``k`` must be a positive integer. An arm with no ids contributes nothing, so a recall
    whose dense arm found nothing fuses exactly like a lexical-only recall rather than failing.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise RecallError("positive_result_limit_required")
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise RecallError("rrf_constant_must_be_positive")
    if not arms:
        return ()
    scores: dict[str, float] = {}
    for arm in arms:
        for rank, version_id in enumerate(dict.fromkeys(arm.ids), start=1):
            scores[version_id] = scores.get(version_id, 0.0) + 1.0 / (k + rank)
    order = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(order[:limit])


def arm_depth(limit: int) -> int:
    """How deep each arm fetches. Wider than the page so arms can agree, and never below ``MIN_ARM_DEPTH``."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise RecallError("positive_result_limit_required")
    return max(limit * ARM_DEPTH_FACTOR, MIN_ARM_DEPTH)


async def trgm_schema(connection: AsyncConnection) -> str | None:
    """The schema ``pg_trgm`` is installed in, or ``None`` when the extension is absent.

    `similarity()` and the `%` operator live in whatever schema the extension was created in. In this project's
    provisioning that is ``public``, precisely so the engine schema's catalog fingerprint stays free of another
    project's functions (migration 23, ``migrate.EXTENSIONS``). Naming them ``pg_catalog.similarity`` therefore does
    not work, and leaving them unqualified binds the query to whatever `search_path` a connection happens to carry.
    Asking the catalog once is the only form that is correct in both places.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            "SELECT n.nspname FROM pg_catalog.pg_extension AS e "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
        )
        row = await cursor.fetchone()
    return None if row is None else str(row[0])


async def _relax_ann_filter(connection: AsyncConnection) -> None:
    """Ask pgvector to keep scanning when a filtered HNSW search would otherwise return too few rows.

    PostgreSQL applies a filter *after* the index returns its ``ef_search`` candidates, so a scope or temporal
    predicate can starve an ANN arm of results that exist. ``hnsw.iterative_scan`` (pgvector >= 0.8) makes the index
    continue until the filter is satisfied. Probed rather than assumed: an older pgvector has no such setting, and
    issuing ``SET`` for an unknown parameter would fail the whole read. ``SET LOCAL`` keeps it inside the caller's
    transaction, so it cannot leak into the next statement on a pooled connection.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT pg_catalog.current_setting('hnsw.iterative_scan', true)")
        row = await cursor.fetchone()
        if row is not None and row[0] is not None:
            await cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")


async def hybrid_recall(
    connection: AsyncConnection,
    temporal: TemporalQuery,
    read_filter: sql.Composable,
    read_parameters: Mapping[str, object],
    query: RecallQuery,
    *,
    limit: int,
    schema_name: str = "cme_engine",
) -> Recall:
    """Rank the caller's authorized rows by relevance to ``query``; return a page of ids in fused order.

    The caller owns the transaction and the ``app.user_id`` context (``api.runtime.Runtime.session``). Every arm
    reuses :func:`~.predicates.compile_predicate` and :func:`~.predicates.scoped_artifact_filter`, so this function
    adds relevance and nothing else: no arm can widen what the caller may read, and no arm can return a row the
    temporal predicate excluded.
    """
    temporal = TemporalQuery.model_validate(temporal.model_dump())
    if temporal.mode == "audit_as_of":
        # An audit shows what the engine believed; ranking it by relevance to a present question would let the
        # question reorder a historical record. ``audit_as_of`` has its own path and must not reach answer recall.
        raise RecallError("audit_not_for_answer_context")
    if read_parameters.keys() & {"user_id", "reference_time", "target_time", "window_start", "window_end"}:
        raise RecallError("read_parameters_collide_with_temporal_parameters")
    depth = arm_depth(limit)
    compiled: CompiledPredicate = compile_predicate(temporal, alias="m", cross_tenant=True)
    where = scoped_artifact_filter(compiled.statement, read_filter)
    table = sql.Identifier(schema_name, "memory_version")
    arms: list[ArmRanking] = []
    candidates: int | None = None

    if "dense" in query.arms:
        assert query.embedding is not None and query.embedding_model is not None
        column, slot_type = slot_for_dimension(len(query.embedding))
        await _relax_ann_filter(connection)
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT m.id FROM {table} AS m JOIN {embeddings} AS e "
                    "ON e.version_id = m.id AND e.user_id = m.user_id "
                    "WHERE {where} AND e.embedding_model = %(embedding_model)s AND e.{slot} IS NOT NULL "
                    "AND 1 - (e.{slot} <=> %(embedding)s::{slot_type}({dimensions})) >= %(floor)s "
                    "ORDER BY e.{slot} <=> %(embedding)s::{slot_type}({dimensions}), m.id "
                    "LIMIT %(depth)s"
                ).format(
                    table=table,
                    embeddings=sql.Identifier(schema_name, "memory_embedding"),
                    where=where,
                    slot=sql.Identifier(column),
                    slot_type=sql.SQL(cast(LiteralString, slot_type)),
                    dimensions=sql.Literal(len(query.embedding)),
                ),
                {
                    **compiled.parameters,
                    **read_parameters,
                    "embedding_model": query.embedding_model,
                    "embedding": vector_literal(query.embedding),
                    "floor": query.similarity_floor if query.similarity_floor is not None else DENSE_SIMILARITY_FLOOR,
                    "depth": depth,
                },
            )
            arms.append(ArmRanking("dense", tuple(str(row[0]) for row in await cursor.fetchall())))

    if "lexical" in query.arms:
        # Prune function words by document frequency before the arm runs (`V2-D100`). The arm's *membership* is what
        # needs this: `simple` has no stopword list (a stopword list would be language-specific), so without it a
        # question like "what is the wifi password at the office" matches every row containing "the". Ranking was never
        # the problem; the candidate set was.
        informative, _pruned = await informative_terms(
            connection, where, {**compiled.parameters, **read_parameters}, schema_name, query.text
        )
        async with connection.cursor(row_factory=tuple_row) as cursor:
            if informative:
                # `websearch_to_tsquery` never raises on user input (unlike `to_tsquery`), which is what makes this arm
                # safe on arbitrary natural language: a query of punctuation yields an empty tsquery and no matches,
                # rather than a syntax error that would turn a question into a failure.
                await cursor.execute(
                    sql.SQL(
                        "SELECT m.id FROM {table} AS m WHERE {where} "
                        "AND m.content_fts_simple @@ pg_catalog.websearch_to_tsquery('simple', %(query_text)s) "
                        "ORDER BY pg_catalog.ts_rank_cd("
                        "m.content_fts_simple, pg_catalog.websearch_to_tsquery('simple', %(query_text)s)"
                        ") DESC, m.id LIMIT %(depth)s"
                    ).format(table=table, where=where),
                    {
                        **compiled.parameters,
                        **read_parameters,
                        # the informative terms only: the pruned ones are the ones that match everything
                        "query_text": " ".join(informative),
                        "depth": depth,
                    },
                )
                arms.append(ArmRanking("lexical", tuple(str(row[0]) for row in await cursor.fetchall())))
            else:
                # No informative term: the question offers no lexical evidence at all. The dense and trigram arms may
                # still find an answer; if one of those finds nothing, the page is empty, which is the truthful answer
                # for a question this corpus cannot answer.
                arms.append(ArmRanking("lexical", ()))
            await cursor.execute(
                sql.SQL("SELECT count(*) FROM {table} AS m WHERE {where}").format(table=table, where=where),
                {**compiled.parameters, **read_parameters},
            )
            counted = await cursor.fetchone()
            candidates = int(counted[0]) if counted is not None else None

    token = trigram_token(query.text)
    if "trigram" in query.arms and token is not None:
        async with connection.cursor(row_factory=tuple_row) as cursor:
            # `ILIKE '%token%'` (not a bare `%` similarity operator) so the trigram GIN index can serve it and the
            # arm means the same thing at every `pg_trgm.similarity_threshold` a deployment happens to set.
            await cursor.execute(
                sql.SQL(
                    "SELECT m.id FROM {table} AS m WHERE {where} "
                    "AND m.content ILIKE %(pattern)s "
                    # `similarity()` lives in whichever schema `pg_trgm` was installed into, so calling it here would
                    # bind this arm to a deployment's extension schema for a signal RRF discards anyway (fusion uses
                    # rank, not magnitude). Ordering by length keeps the arm deterministic and prefers the denser
                    # match: a short memory that contains the token says more about it than a long one that does.
                    "ORDER BY pg_catalog.length(m.content), m.id LIMIT %(depth)s"
                ).format(table=table, where=where),
                {
                    **compiled.parameters,
                    **read_parameters,
                    "pattern": f"%{token}%",
                    "depth": depth,
                },
            )
            arms.append(ArmRanking("trigram", tuple(str(row[0]) for row in await cursor.fetchall())))

    fused = fuse(arms, limit=limit)
    return Recall(
        ordered=tuple(version_id for version_id, _ in fused),
        scored=fused,
        arms=tuple(arms),
        candidates=candidates,
    )


__all__ = [
    "ARM_DEPTH_FACTOR",
    "EMBEDDING_SLOTS",
    "MIN_ARM_DEPTH",
    "RANKING_ARMS",
    "RRF_CONSTANT",
    "SLOT_TYPES",
    "ArmName",
    "ArmRanking",
    "arm_depth",
    "Recall",
    "RecallError",
    "RecallQuery",
    "JUDGE_CANDIDATES",
    "JUDGE_RELEVANCE_FLOOR",
    "RelevanceJudge",
    "fuse",
    "hybrid_recall",
    "slot_for_dimension",
    "trigram_token",
    "vector_literal",
]
