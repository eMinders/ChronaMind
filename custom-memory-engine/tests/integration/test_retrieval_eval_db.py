"""The retrieval evaluation: every configuration through the real read path, on the labelled set (``V2-D100``).

The decision this test settles is **which ranking strategy the shipped read path keeps**, and it settles it on 48
questions rather than 16: five configurations, one corpus, one read set, so a difference in the numbers is a difference
in ranking and nothing else.

============================  ==============================================================================
configuration                 what it is
============================  ==============================================================================
``recency``                   the pre-W3.5 behaviour: no relevance at all, newest first
``lexical``                   the two language-agnostic lexical arms fused (no embedder configured)
``hybrid``                    the shipped path: dense (real ``bge-m3``) + lexical + trigram, RRF
``bm25``                      BM25 over the corpus, ranked independently of the engine's arms
``hybrid_then_bm25``          the shipped order, re-ranked by BM25 — the engine's ``Reranker`` seam at its cheapest
``hybrid_then_recency``       the shipped order, re-ranked by newest-first — a control with no relevance signal
============================  ==============================================================================

``bm25`` and ``hybrid_then_bm25`` are computed **offline in Python** over the corpus, on purpose: the question is
whether a term-weighting ranker would beat the fusion *if it were built*, and building a stateful IDF table first would
be implementing the thing under test. If either wins by a margin that survives the per-kind breakdown, the report says
what to build; if neither does, the engine keeps RRF and the numbers below are the evidence.

Both ``bm25`` configurations see the same candidates the engine ranked, so they cannot win by retrieving what the
engine could not. That is the honest comparison: a ranker's job here is ordering, and the engine's own arms decide
membership.

The report is written to ``reports/retrieval/evaluation.{json,md}`` and asserted to be self-consistent, so a re-run
reproduces the numbers in the document rather than disagreeing with them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_memory_engine.api.entry import Engine
from custom_memory_engine.api.read_handlers import QueryResult, register_read_handlers
from custom_memory_engine.integrations.cross_encoder import OnnxCrossEncoder, reranker_assets_for
from custom_memory_engine.integrations.onnx_embedder import OnnxEmbedder, assets_for
from evaluation.retrieval.quality import MEMORIES, QUESTIONS, Score, by_kind, score
from evaluation.retrieval.rankings import bm25_scores, document_frequencies, rerank_by
from tests.integration.test_api_wiring_structure import caller, database  # noqa: F401  (fixture)
from tests.integration.test_api_wiring_structure import world as structure_world

pytestmark = pytest.mark.integration
T0 = datetime(2026, 3, 10, tzinfo=UTC)
REPORT = Path(__file__).resolve().parents[2] / "reports" / "retrieval"
MODEL_DIR = Path(__file__).resolve().parents[2] / ".model-cache" / "bge-m3"
RERANKER_NAME = "bge-reranker-v2-m3"
RERANKER_DIR = Path(__file__).resolve().parents[2] / ".model-cache" / RERANKER_NAME
TOP = 5

needs_model = pytest.mark.skipif(
    not (MODEL_DIR / "model.onnx").is_file(),
    reason="the pinned embedding model is not cached locally (see scripts/fetch_models.py)",
)
needs_reranker = pytest.mark.skipif(
    not (RERANKER_DIR / "model_int8.onnx").is_file(),
    reason="the pinned reranker is not cached locally (see scripts/fetch_models.py)",
)

#: The judge's best score per question from the last measured pass, so the report can record the extremes its floor
#: sits between (`V2-D104`). Module-level because `rank` fills it in through an out-parameter rather than returning a
#: second value every other configuration would have to ignore.
JUDGE_BEST: dict[str, float] = {}

#: ``key -> version id`` as seeded, and the reverse, so a label can be scored without leaking an id into the dataset.
IDS: dict[str, str] = {memory.key: f"v-{memory.key}" for memory in MEMORIES}
TEXTS: dict[str, str] = {memory.key: memory.text for memory in MEMORIES}


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[object, Engine, str]]:  # noqa: F811
    """The corpus seeded as asserted claims, with the read handlers wired (embedder added per configuration)."""
    async with structure_world(database) as (conn, engine, schema):
        for index, memory in enumerate(MEMORIES):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute(
                    "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
                    "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, "
                    "visibility, retention) "
                    "VALUES (%s, %s, 'alice', 'active', 'asserted', 'claim', 'self', 'statement', false, %s, "
                    "tstzrange(%s, NULL), tstzrange(%s, NULL), 'semantic', '/u/alice/', 'private', 'durable')",
                    (IDS[memory.key], IDS[memory.key], memory.text, T0, T0 + timedelta(minutes=index)),
                )
                await conn.execute(
                    "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
                    "VALUES (%s, 't-alice', 'alice', 'x', 'support')",
                    (IDS[memory.key],),
                )
        register_read_handlers(engine, engine.resolver.runtime)  # type: ignore[attr-defined]
        yield conn, engine, schema


async def attach_vectors(conn: object, embedder: OnnxEmbedder) -> None:
    """Embed the corpus with the deployment's model and store it in the dense arm's slot.

    This is the *ingestion* half of `V2-D98`, done explicitly here because the corpus is seeded directly: the
    production write path (`assert`, `ingest_episode`) does exactly this through `attach_embedding`, and the retrieval
    numbers are only meaningful if the questions are encoded by the same model that encoded the memories.
    """
    from custom_memory_engine.engine.hybrid import vector_literal

    vectors = await embedder.embed([TEXTS[memory.key] for memory in MEMORIES])
    for memory, vector in zip(MEMORIES, vectors, strict=True):
        async with conn.transaction():  # type: ignore[attr-defined]
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")  # type: ignore[attr-defined]
            await conn.execute(  # type: ignore[attr-defined]
                "INSERT INTO memory_embedding (version_id, embedding_model, user_id, embedding_1024) "
                "VALUES (%s, %s, 'alice', %s::halfvec(1024))",
                (IDS[memory.key], embedder.model, vector_literal(vector)),
            )


async def rank(
    engine: Engine,
    embedder: OnnxEmbedder | None,
    question: str,
    *,
    text: bool = True,
    top: int = TOP,
    judge: OnnxCrossEncoder | None = None,
    best_score: dict[str, float] | None = None,
) -> tuple[str, ...]:
    """The keys one configuration returns for one question, best first, through ``Engine.call``.

    ``best_score`` is filled with the judge's highest score for this question when a judge ran. That is a *reported
    fact* on the result (:attr:`QueryResult.judgement`), not a re-computation: the measurement costs nothing extra.
    """
    register_read_handlers(engine, engine.resolver.runtime, embedder=embedder, judge=judge)  # type: ignore[attr-defined]
    request: dict[str, object] = {"scope": "/u/alice/", "limit": top}
    if text:
        request["text"] = question
    result = await engine.call(caller("alice"), "query", {"request": request})
    assert isinstance(result, QueryResult)
    if best_score is not None and judge is not None:
        assert result.judgement is not None, "a configured judge must report its judgement on the result"
        if result.judgement.best is not None:
            best_score[question] = result.judgement.best
    ordered = [artifact.artifact_id for artifact in result.facts]
    reverse = {version_id: key for key, version_id in IDS.items()}
    return tuple(reverse.get(version_id, version_id) for version_id in ordered)


async def rankings(
    engine: Engine, embedder: OnnxEmbedder | None, judge: OnnxCrossEncoder | None = None
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Every configuration's ranking for every question. One pass, so all configurations see identical state."""
    recency: dict[str, tuple[str, ...]] = {}
    lexical: dict[str, tuple[str, ...]] = {}
    hybrid: dict[str, tuple[str, ...]] = {}
    judged: dict[str, tuple[str, ...]] = {}
    global JUDGE_BEST
    for question in QUESTIONS:
        recency[question.text] = await rank(engine, None, question.text, text=False)
        lexical[question.text] = await rank(engine, None, question.text)
        hybrid[question.text] = await rank(engine, embedder, question.text)
        judged[question.text] = await rank(engine, embedder, question.text, judge=judge, best_score=JUDGE_BEST)
    frequencies = document_frequencies(TEXTS)
    bm25: dict[str, tuple[str, ...]] = {}
    hybrid_then_bm25: dict[str, tuple[str, ...]] = {}
    hybrid_then_recency: dict[str, tuple[str, ...]] = {}
    recency_order = tuple(IDS[memory.key] for memory in reversed(MEMORIES))
    for question in QUESTIONS:
        # BM25 ranks the whole corpus: it is the reference implementation, not a stage of the engine's pipeline
        whole = bm25_scores(question.text, tuple(IDS.values()), TEXTS, frequencies=frequencies)
        bm25[question.text] = tuple(
            key for key, _ in sorted(((IDS_KEY, whole[IDS[IDS_KEY]]) for IDS_KEY in IDS), key=lambda pair: -pair[1])
        )[:TOP]
        # …and as a second stage over exactly the candidates the fused order returned, which is the seam under test
        candidates = hybrid[question.text]
        ids = [IDS[key] for key in candidates]
        local = bm25_scores(question.text, ids, TEXTS, frequencies=frequencies)
        reranked = rerank_by(ids, {version_id: local[version_id] for version_id in ids})
        hybrid_then_bm25[question.text] = tuple(
            next(key for key, value in IDS.items() if value == version_id) for version_id in reranked
        )
        newest = [version_id for version_id in recency_order if version_id in set(ids)]
        hybrid_then_recency[question.text] = tuple(
            next(key for key, value in IDS.items() if value == version_id) for version_id in newest
        )
    return {
        "recency": recency,
        "lexical": lexical,
        "hybrid": hybrid,
        "hybrid_judged": judged,
        "bm25": bm25,
        "hybrid_then_bm25": hybrid_then_bm25,
        "hybrid_then_recency": hybrid_then_recency,
    }


def summarise(results: dict[str, dict[str, tuple[str, ...]]]) -> list[Score]:
    return [score(name, ranking) for name, ranking in results.items()]


KINDS = ("lexical", "paraphrase", "cross_script", "no_match")


def judge_report() -> dict[str, object]:
    """The judge's measured extremes: the two numbers its floor must sit between (`V2-D104`)."""
    from custom_memory_engine.engine.hybrid import JUDGE_RELEVANCE_FLOOR

    answerable = {q.text: JUDGE_BEST[q.text] for q in QUESTIONS if q.kind != "no_match" and q.text in JUDGE_BEST}
    unanswerable = {q.text: JUDGE_BEST[q.text] for q in QUESTIONS if q.kind == "no_match" and q.text in JUDGE_BEST}
    if not answerable or not unanswerable:
        return {"measured": False}
    weakest = min(answerable, key=answerable.__getitem__)
    strongest = max(unanswerable, key=unanswerable.__getitem__)
    return {
        "measured": True,
        "model": RERANKER_NAME,
        "floor": JUDGE_RELEVANCE_FLOOR,
        "weakest_answerable": round(answerable[weakest], 4),
        "weakest_answerable_question": weakest,
        "strongest_unanswerable": round(unanswerable[strongest], 4),
        "strongest_unanswerable_question": strongest,
        "separation": round(answerable[weakest] - unanswerable[strongest], 4),
    }


def write_report(results: dict[str, dict[str, tuple[str, ...]]], scores: list[Score]) -> None:
    """Persist the measurements, so the document's numbers are a *record* rather than a transcription."""
    REPORT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_size": len(MEMORIES),
        "questions": len(QUESTIONS),
        "questions_by_kind": {kind: sum(1 for q in QUESTIONS if q.kind == kind) for kind in KINDS},
        "top_k": TOP,
        "embedding_model": "BAAI/bge-m3 (local ONNX, halfvec(1024))",
        "configurations": [
            {**result.as_dict(), "by_kind": by_kind(result, results[result.name])} for result in scores
        ],
        "judge": judge_report(),
        "misses": {
            result.name: [
                {"question": question, "kind": kind, "expected": expected, "returned": list(returned)}
                for question, kind, expected, returned in result.misses
            ]
            for result in scores
        },
    }
    (REPORT / "evaluation.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    lines = [
        "# Retrieval evaluation (`V2-D100`, `V2-D104`)",
        "",
        f"{len(MEMORIES)} memories, {len(QUESTIONS)} questions, top {TOP}, measured through the real read path",
        "`hybrid_judged` is the shipped path with the relevance judge; `bm25` and the two rerank rows are offline.",
        "",
        "| configuration | recall@5 | top-1 | MRR@5 | nDCG@5 | no-match FP | misses |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in scores:
        lines.append(
            f"| `{result.name}` | {result.recall:.3f} | {result.top1:.3f} | {result.mrr:.3f} | {result.ndcg:.3f} "
            f"| {result.no_match_fp:.3f} | {len(result.misses)} |"
        )
    lines += ["", "### Recall by question kind", "", "| configuration | " + " | ".join(KINDS) + " |"]
    lines.append("|" + "---|" * (len(KINDS) + 1))
    for result in scores:
        shares = by_kind(result, results[result.name])
        lines.append(f"| `{result.name}` | " + " | ".join(f"{shares[kind]:.3f}" for kind in KINDS) + " |")
    (REPORT / "evaluation.md").write_text("\n".join(lines) + "\n", "utf-8")


async def test_the_evaluation_set_is_large_enough_and_covers_every_kind() -> None:
    """A measurement is only worth its sample. The set must stay substantially larger than the one it replaces."""
    assert len(MEMORIES) >= 40
    assert len(QUESTIONS) >= 40
    for kind in KINDS:
        assert sum(1 for question in QUESTIONS if question.kind == kind) >= 5, kind
    # every labelled answer names a memory that exists, in the script its label claims
    keys = {memory.key for memory in MEMORIES}
    for question in QUESTIONS:
        assert question.answer is None or question.answer in keys, question.text
    assert len({question.text for question in QUESTIONS}) == len(QUESTIONS), "labels must be distinct"


@needs_model
async def test_the_floor_is_swept_and_the_constant_matches_the_measured_operating_point(database) -> None:  # noqa: F811
    """Where the dense floor's value comes from, and the criterion that picks it.

    A similarity floor is a **product decision**, not a tuning detail: it trades "answers that are not there" against
    "answers a paraphrase deserved". The criterion this test applies is stated before the numbers: **minimise the
    no-match false-positive rate subject to recall@5 >= 0.90**. A configuration that answers an unanswerable question
    confidently is the failure this engine exists to avoid, so precision comes first and recall is the constraint, not
    the objective.

    The sweep runs through the real read path (`Engine.call`) with the module constant patched, so the number the
    constant ends up at is the number this test measured, on this corpus, with this model.
    """
    from custom_memory_engine.api.read_handlers import register_read_handlers as wire
    from custom_memory_engine.engine import hybrid

    async with world(database) as (conn, engine, schema):
        embedder = OnnxEmbedder(assets_for("bge-m3", MODEL_DIR))
        await attach_vectors(conn, embedder)
        wire(engine, engine.resolver.runtime, embedder=embedder)  # type: ignore[attr-defined]
        sweep: dict[str, dict[str, float]] = {}
        original = hybrid.DENSE_SIMILARITY_FLOOR
        try:
            for floor in (0.45, 0.55, 0.60, 0.62, 0.65, 0.70):
                hybrid.DENSE_SIMILARITY_FLOOR = floor
                ranking = {question.text: await rank(engine, embedder, question.text) for question in QUESTIONS}
                result = score(f"floor={floor}", ranking)
                sweep[f"{floor:.2f}"] = {
                    "recall@5": round(result.recall, 4),
                    "ndcg@5": round(result.ndcg, 4),
                    "no_match_false_positive": round(result.no_match_fp, 4),
                }
        finally:
            hybrid.DENSE_SIMILARITY_FLOOR = original
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / "floor_sweep.json").write_text(json.dumps(sweep, indent=2) + "\n", "utf-8")
        # the criterion, applied to the measurements rather than to an opinion
        eligible = {floor: row for floor, row in sweep.items() if row["recall@5"] >= 0.90}
        assert eligible, "no floor keeps recall at 0.90; the set or the model changed and the decision must be re-made"
        best = min(eligible.items(), key=lambda pair: (pair[1]["no_match_false_positive"], -pair[1]["recall@5"]))
        assert float(best[0]) == pytest.approx(hybrid.DENSE_SIMILARITY_FLOOR, abs=0.001), (
            f"the constant is {hybrid.DENSE_SIMILARITY_FLOOR} but the measurement prefers {best[0]}: {sweep}"
        )
        del schema


async def test_the_fusion_beats_the_alternatives_and_keeps_its_place(database) -> None:  # noqa: F811
    """The measurement, and the decision it supports.

    What this asserts is deliberately *relative*: the shipped fusion must dominate the configurations it replaced
    (recency, lexical-only) on nDCG and recall, and no alternative may beat it by more than the tolerance below. If a
    future change makes BM25 or a reranker genuinely better, this test fails — which is the point: the decision is
    re-opened by evidence rather than by a hunch.
    """
    async with world(database) as (conn, engine, schema):
        embedder = OnnxEmbedder(assets_for("bge-m3", MODEL_DIR))
        await attach_vectors(conn, embedder)
        judge = OnnxCrossEncoder(reranker_assets_for(RERANKER_NAME, RERANKER_DIR))
        results = await rankings(engine, embedder, judge)
        scores = summarise(results)
        write_report(results, scores)
        by_name = {result.name: result for result in scores}

        hybrid = by_name["hybrid"]
        # the shipped path must beat both things it replaced
        assert hybrid.ndcg > by_name["recency"].ndcg + 0.30
        assert hybrid.ndcg > by_name["lexical"].ndcg + 0.10
        assert hybrid.recall > by_name["lexical"].recall
        # and no alternative may beat it by more than noise (0.02 nDCG ~ one question's position)
        for alternative in ("bm25", "hybrid_then_bm25", "hybrid_then_recency"):
            assert by_name[alternative].ndcg <= hybrid.ndcg + 0.02, alternative
        # The no-match rows are the ones that keep a ranking honest. Without a judge the residual is one in eight: the
        # unanswerable Arabic question's nearest neighbour sits at 0.605, above the weakest true match at the other end
        # of the scale, so no cosine floor separates them (see `DENSE_SIMILARITY_FLOOR`'s table).
        assert hybrid.no_match_fp <= 0.125
        # …and with the relevance judge it is zero, because a cross-encoder reads the pair together (V2-D104). This is
        # the assertion that keeps the judge in the read path: delete it and the false positive comes back.
        judged = by_name["hybrid_judged"]
        assert judged.no_match_fp == 0.0, judged.misses
        # the gate may not cost a quarter of the answers to buy that precision
        assert judged.recall >= hybrid.recall - 0.05
        assert judged.ndcg > hybrid.ndcg
        del conn, schema
