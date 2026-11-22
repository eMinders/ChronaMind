# Retrieval quality — the numbers that decided the arms (2026-11-22)

Measured through the shipped read path (`Engine.call(..., "query", ...)`), on a hand-labelled multilingual set of 12
memories and 16 questions: 8 paraphrase, 2 verbatim, 2 cross-script, 2 whose correct answer is *nothing*. The set and the
assertions live in `tests/integration/test_retrieval_quality_db.py`; this file records what they measured.

| configuration | recall@5 | top-1 | verbatim | paraphrase | cross-script | no-match |
|---|---|---|---|---|---|---|
| recency (no text) | 0.36 | 0.36 | — | — | — | — |
| lexical (`'simple'` FTS + `pg_trgm`, no embedder) | **0.44** | 0.44 | 1.00 | **0.40** | 0.50 | 1.00 |
| hybrid (lexical + `bge-m3` in the 1024 `halfvec` slot) | **1.00** | 0.69 | 1.00 | **1.00** | **1.00** | 1.00 |

Three decisions came out of this table.

**The dense arm earns its place.** Paraphrase recall goes from 0.40 to 1.00 and cross-script from 0.50 to 1.00. Every
question the lexical configuration missed is a question phrased differently from the memory ("where do I live" against
"I moved to Berlin last spring"), which is the whole reason a memory engine cannot be lexical-only.

**The dense arm needs a floor.** The first hybrid run answered *every* question, including "quantum chromodynamics",
with its nearest neighbour. Measuring the cosine distribution separated the populations cleanly — unrelated pairs top
out at 0.364, the weakest true match sits at 0.512 — so `hybrid.DENSE_SIMILARITY_FLOOR = 0.45` was added and the no-match
questions return nothing again. Without the floor the engine has a worse failure than the lexical one: it always
answers, plausibly and wrongly.

**BM25 is not implemented, and this is why.** BM25 needs corpus statistics (document frequency per term) kept current on
every write — a table, a trigger and a migration. Its benefit is *ranking* within a recall set. On this set the recall
set is already complete with three arms (recall@5 = 1.00), so BM25 could only reorder it, and the direct measurement of
reordering — an LLM reranker over the top 5 — bought 0.71 top-1 against the fused order's 0.69, inside noise for 14
questions. Both are recorded rather than assumed, and the trigger for revisiting is explicit: a labelled set large enough
to show a real ordering gap, or a locally hosted cross-encoder, which costs no per-read model call.

**A reranker is not shipped.** `hybrid.Reranker` exists as a protocol and `hybrid.rerank` applies one when a deployment
injects it (`V2-D89`), but the engine ships none: the read path makes zero generative calls. The measurement above is why
that stays true, and what would have to change for it not to.
