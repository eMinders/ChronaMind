# Retrieval quality — the numbers that decided the ranking (2026-11-25, revised)

Three rounds. Round 3 (`V2-D104`) adds a relevance judge that closes round 2's residual false positive. Earlier: The first (2026-11-22) settled whether the **dense arm** earns its place on a 16-question set. The second
(`V2-D100`) is on a set large enough to decide the **ranking** question the first one could not: whether BM25 or a
second-stage reranker beats Reciprocal Rank Fusion. Both run through the shipped read path
(`Engine.call(..., "query", ...)`); the set, the metrics and the assertions live in
`tests/integration/test_retrieval_eval_db.py` and `evaluation/retrieval/`, and `evaluation.{json,md}` beside this file is
its output rather than a transcription.

## Round 3 — the relevance judge (`V2-D104`)

Round 2 left one residual it could not remove: one unanswerable question in eight returned a neighbour, because its
nearest memory sat at cosine 0.605 while the weakest *genuine* paraphrase sat at 0.46. Before reaching for a model,
three cheaper rules were measured on the full 64 × 49 cosine matrix, and each failed on the numbers:

| rule | answerable (worst) | unanswerable (best) | verdict |
|---|---|---|---|
| corpus-relative z-score of the top hit | 2.16 | 3.44 | overlaps |
| top-1 minus top-2 gap | 0.004 | 0.050 | overlaps |
| require a lexical/trigram arm to agree | drops 30 of 56 answers | — | unusable |
| embed only the question's informative terms | 0.361 | 0.569 | worse than the full question |

A cross-encoder reads question and memory **together**. Two multilingual ones were measured over the same candidates:

| judge | answerable (worst logit) | unanswerable (best logit) | separates? |
|---|---|---|---|
| `bge-reranker-v2-m3` (int8 ONNX) | -6.68 | -11.01 | **yes, by 4.3 logits** |
| `jina-reranker-v2-base-multilingual` (int8 ONNX) | -2.91 | -2.38 | no |

`bge-reranker-v2-m3` ships as an optional judge, applied to at most 10 fused candidates (~28 ms each on the reference
CPU), with the floor at the midpoint, **-8.8**. `hybrid_judged` in `evaluation.md`: recall@5 0.982 (unchanged), top-1
0.768 (from 0.732), nDCG@5 0.896 (from 0.882), **no-match false positives 0.000** (from 0.125). The extremes are
written to `evaluation.json` (`judge`) by the test, and `tests/test_hybrid.py` asserts the constant sits between them.
Unlike BM25 and the recency rerank of round 2, this second stage *improved* ranking as well as gating, because it is
the only one that reads the pair rather than scoring the memory alone.

**Cost and fallback.** A deployment without the asset (`CME_RERANKER_ASSET` unset) keeps round 2's behaviour and the
result says `judgement: null`. A judge failure degrades to the same, logged. The model files are pinned by revision and
SHA-256 in `scripts/fetch_models.py --check`.

## Round 2 — 49 memories, 64 questions (`V2-D100`)

20 lexical, 12 paraphrase, 8 cross-script (question and answer in different scripts), 8 whose correct answer is
*nothing*. `bm25` and the two rerank rows are computed offline in Python over the same candidates the engine ranked, so
they cannot win by retrieving something the engine could not.

| configuration | recall@5 | top-1 | MRR@5 | nDCG@5 | no-match FP |
|---|---|---|---|---|---|
| `recency` | 0.089 | 0.018 | 0.041 | 0.053 | 1.000 |
| `lexical` (no embedder) | 0.393 | 0.375 | 0.384 | 0.386 | 0.000 |
| **`hybrid`** (shipped) | **0.982** | **0.732** | **0.847** | **0.882** | 0.125 |
| `bm25` (whole corpus) | 0.143 | 0.071 | 0.094 | 0.106 | 1.000 |
| `hybrid` → rerank by BM25 | 0.982 | 0.482 | 0.691 | 0.765 | 0.125 |
| `hybrid` → rerank by recency (control) | 0.982 | 0.411 | 0.640 | 0.726 | 0.125 |

| configuration | lexical | paraphrase | cross-script | no-match |
|---|---|---|---|---|
| `recency` | 0.333 | 0.000 | 0.000 | 0.000 |
| `lexical` | 0.733 | 0.333 | **0.000** | 1.000 |
| **`hybrid`** | **1.000** | **0.970** | **1.000** | 0.875 |
| `bm25` | 0.067 | 0.182 | 0.125 | 0.000 |

**BM25 is rejected, and now on evidence.** As a standalone ranker it recalls 0.143: term weighting cannot connect a
paraphrase to its memory, which is the same conclusion the dense arm rests on. As a *reranker* over fused candidates it
keeps the recall and destroys the ranking — top-1 falls from 0.732 to 0.482, nDCG from 0.882 to 0.765 — because it pulls
lexically-overlapping rows above semantically-correct ones. The control says the method is sound rather than miscalibrated:
reranking by recency also degrades (0.726). A second stage has to be *better informed* than the fusion, and BM25 is not.

**A reranker is still not shipped.** The engine's `Reranker` protocol stays a seam (`V2-D89`), and the numbers above are
the reason: the two cheapest possible rerankers both make the order worse. What would change the decision is a
cross-encoder, which the earlier round measured as one model call per read for a top-1 gain inside noise.

**The no-match residual, stated rather than hidden.** The shipped path returns a neighbour for 1 of 8 unanswerable
questions. The cause is measured: on this set the weakest *true* match sits at cosine 0.46 and the strongest *false* one
at 0.605, so no single threshold separates them. Lowering the floor to 0.45 (round 1's value, chosen on a 16-question
set whose two unanswerable questions shared no vocabulary with anything) answered **7 of 8** unanswerable questions with
their nearest neighbour; `DENSE_SIMILARITY_FLOOR = 0.55` answers one and keeps recall at 0.982. The sweep is recorded in
`floor_sweep.json` and asserted against the constant, so the value cannot drift away from its measurement.

**The floor was not the whole story.** Function words were: the `simple` configuration has no stopword list (a stopword
list is language-specific), so a question like "what is the wifi password at the office" matched every row containing
"the". The lexical arm now prunes query terms whose document frequency in the caller's own rows exceeds
`TERM_PRUNING_DF_RATIO` — a statistic, not a word list — computed inside the same temporal and read predicate, so it is
a statistic about rows the caller may already read and never a cross-tenant oracle.

## Round 1 — 12 memories, 16 questions (2026-11-22)

Kept because it is the record of the earlier decision, and because the two rounds disagree in an instructive way: on the
smaller set the 0.45 floor looked clean (`recall 1.00`, `no-match 1.00`), and on the larger set it was answering almost
everything. A sample of two unanswerable questions cannot measure a precision claim.

| configuration | recall@5 | top-1 | verbatim | paraphrase | cross-script | no-match |
|---|---|---|---|---|---|---|
| recency (no text) | 0.36 | 0.36 | — | — | — | — |
| lexical (`'simple'` FTS + `pg_trgm`, no embedder) | 0.44 | 0.44 | 1.00 | 0.40 | 0.50 | 1.00 |
| hybrid (lexical + `bge-m3` in the 1024 `halfvec` slot) | 1.00 | 0.69 | 1.00 | 1.00 | 1.00 | 1.00 |

The dense arm's place was never in doubt in either round: paraphrase recall 0.40 → 1.00 and cross-script 0.00 → 1.00
(round 2), which is the whole reason a memory engine cannot be lexical-only.
