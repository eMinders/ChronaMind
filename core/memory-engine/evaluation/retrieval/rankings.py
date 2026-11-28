"""The ranking strategies the retrieval evaluation compares, as pure functions over one candidate set.

The question this module answers is narrow, and it is the one the plan left open: **does a second-stage ranker earn its
place on top of Reciprocal Rank Fusion?** Every strategy here re-orders the same authorized candidates, so a difference
in the numbers is a difference in ranking and not in what was retrieved.

* ``bm25`` — the classic term weighting, computed offline in Python from a corpus-wide document-frequency table. It is
  the *reference* a stateful IDF implementation would have to beat; if it does not beat the fusion, the engine has no
  reason to carry an IDF table that every write must maintain.
* ``fusion`` — the order the engine already produces (dense + lexical + trigram, RRF).
* ``fusion_then_bm25`` — the engine's :class:`~custom_memory_engine.engine.hybrid.Reranker` seam, exercised with the
  BM25 score as the second stage. This is the cheapest possible reranker (no model, no network), so it is the strongest
  case *for* adding any reranker.
* ``fusion_then_recency`` — a reranker with no relevance signal at all, as a control: if it scores as well as BM25, the
  second stage is measuring the candidate set rather than the ranking.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

#: BM25's parameters, the literature defaults. Fixed rather than tuned: a tuning pass on 48 documents would fit the set
#: instead of measuring it, and the point of the comparison is a decision, not a leaderboard.
K1 = 1.2
B = 0.75

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> list[str]:
    """Lowercased word tokens, Unicode-aware so Arabic and mixed-script text tokenise as English does."""
    return _TOKEN.findall(text.lower())


def document_frequencies(documents: Mapping[str, str]) -> tuple[Counter[str], float]:
    """``(df, average length in tokens)`` over the corpus. The state an IDF-weighted ranker has to maintain."""
    counts: Counter[str] = Counter()
    lengths: list[int] = []
    for text in documents.values():
        terms = tokens(text)
        lengths.append(len(terms))
        counts.update(set(terms))
    average = sum(lengths) / len(lengths) if lengths else 0.0
    return counts, average


def bm25_scores(
    query: str,
    candidates: Sequence[str],
    documents: Mapping[str, str],
    *,
    frequencies: tuple[Counter[str], float] | None = None,
) -> dict[str, float]:
    """BM25 for ``candidates`` against ``query``; ids absent from ``documents`` score 0.

    A query term that appears in no candidate contributes nothing, which is correct: BM25's IDF is over the corpus,
    so a
    term that is everywhere (or nowhere) carries little signal.
    """
    df, average = frequencies if frequencies is not None else document_frequencies(documents)
    total = max(len(documents), 1)
    terms = tokens(query)
    scores: dict[str, float] = {}
    for candidate in candidates:
        text = documents.get(candidate, "")
        words = tokens(text)
        if not words:
            scores[candidate] = 0.0
            continue
        counts = Counter(words)
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            idf = math.log(1 + (total - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            score += idf * (frequency * (K1 + 1)) / (frequency + K1 * (1 - B + B * len(words) / (average or 1)))
        scores[candidate] = score
    return scores


def rerank_by(candidates: Sequence[str], scores: Mapping[str, float]) -> tuple[str, ...]:
    """A second stage over fused candidates: re-order, never admit or remove.

    This is exactly the contract :class:`~custom_memory_engine.engine.hybrid.Reranker` gives a deployment — the
    engine's ``rerank`` replaces the fused score with the second stage's order and keeps membership — so measuring this
    function measures the seam rather than a hypothesis.
    """
    return tuple(sorted(candidates, key=lambda candidate: (-scores.get(candidate, 0.0), candidate)))


__all__ = ["B", "K1", "bm25_scores", "document_frequencies", "rerank_by", "tokens"]
