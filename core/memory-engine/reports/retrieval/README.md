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

A cross-encoder reads question and memory **together**. Two multilingual ones were measured on both the fused page (`DENSE_SIMILARITY_FLOOR = 0.55`, where only `N = 1` of the 8 unanswerable questions survived to reach the judge) and the **full uncensored** labelled set (`evaluation/retrieval/judge_floor.py`: all 56 answerable gold pairs plus all 8 `no_match` questions scored against all 49 corpus memories = 448 pairs):

| judge | scope | answerable (worst logit) | unanswerable (best logit) | separation | midpoint |
|---|---|---|---|---|---|
| `bge-reranker-v2-m3` (int8 ONNX) | fused page (`N = 1` surviving 0.55) | -6.6790 | -11.0099 | **4.33 logits** | -8.84 |
| `bge-reranker-v2-m3` (int8 ONNX) | **full uncensored** (`8 × 49` + 56 pairs) | -6.6790 | -9.4076 | **2.73 logits** | -8.04 |
| `jina-reranker-v2-base-multilingual` (int8 ONNX) | fused page | -2.91 | -2.38 | overlaps | — |
| `jina-reranker-v2-base-multilingual` (int8 ONNX) | **full uncensored** (`8 × 49` + 56 pairs) | -3.1210 | -1.9922 | overlaps (-1.13) | — |

`bge-reranker-v2-m3` (ONNX fingerprint `1269c11fa1af439a9e3e01573e8bc5e3`) ships as an optional judge, applied to at most 10 fused candidates (~28 ms each on the reference CPU), with `JUDGE_RELEVANCE_FLOOR = -8.8`. That floor sits strictly inside both intervals: `2.1210` logits below the weakest answer (`"cryptocurrency"` → `crypto`, `-6.6790`), `2.2099` logits above the fused-page surviving non-answer (`"جيراننا يشكون من الضجيج كل ليلة"`, `-11.0099`), and `0.6076` logits above the uncensored worst non-answer (`"which visa do I need for Japan"` → `passport`, `-9.4076`). `jina-reranker-v2-base-multilingual` (fingerprint `9ee9afe75ec7b53185d8ad31f65f710b`) overlaps on both scopes and cannot gate.

**Swapping or re-verifying the judge asset.** Before deploying a replacement cross-encoder or changing `CME_RERANKER_FLOOR`, run the uncensored full-set check (`evaluation/retrieval/judge_floor.py`):

```bash
uv run --no-sync python scripts/check_judge_floor.py --asset bge-reranker-v2-m3
# exit 0 when strongest_unanswerable < floor < weakest_answerable ("inside"), exit 1 when "outside"
```

Set `CME_RERANKER_FLOOR` to the asset's implied midpoint (or a chosen point strictly inside its gap) rather than editing code; `tests/test_hybrid.py` asserts `JUDGE_RELEVANCE_FLOOR` sits inside both `reports/retrieval/evaluation.json` and the live uncensored `.model-cache/bge-reranker-v2-m3` extremes.

**Sample-size and power limitation (`N = 8` `no_match` questions).** The `0.125 → 0.000` false-positive drop (`1/8 → 0/8`) is backed by the **2.73-logit separation** across all 448 uncensored pairs, not by hypothesis-test power on `N = 8` alone. Computed exactly via `evaluation/stats/power.py` (`alpha = 0.05`, target `80%` power):

| regime (`0/8` vs `1/8`, i.e. `0.00` vs `0.125`) | power at `N = 8` (two-sided / one-sided) | required `N` for 80% power (two-sided / one-sided) |
|---|---|---|
| One-sample exact binomial (`p1 = 0.00` vs `p0 = 0.125`) | `0.0000` / `0.0000` | `28` / `23` queries |
| Two-proportion Fisher exact (`0/8` vs `1/8` per arm) | `0.0012` / `0.0112` | `62` / `53` per arm |
| Paired McNemar exact (`p10 = 1/8, p01 = 0`) | `0.0001` / `0.0012` | `62` / `53` pairs |

`N = 8` therefore proves separation of these concrete adversarial decoys rather than bounding rare false-positive rates in general.

**Cost and fallback.** A deployment without the asset (`CME_RERANKER_ASSET` unset) keeps round 2's behaviour and the result says `judgement: null`. A judge failure degrades to the same, logged. The model files are pinned by revision and SHA-256 in `scripts/fetch_models.py --check`.

## Round 2 — 49 memories, 64 questions (`V2-D100`)

15 lexical, 33 paraphrase, 8 cross-script (question and answer in different scripts), 8 whose correct answer is *nothing*. `bm25` and the two rerank rows are computed offline in Python over the same candidates the engine ranked, so they cannot win by retrieving something the engine could not.

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
