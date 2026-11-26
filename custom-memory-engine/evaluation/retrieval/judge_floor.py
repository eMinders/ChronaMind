"""Full-corpus, uncensored relevance-judge floor verification and re-derivation (``V2-D104``, ``V2-D118``).

Round 3 of ``reports/retrieval/`` originally reported the judge's unanswerable extreme (``-11.01``) over the
fused page, where ``DENSE_SIMILARITY_FLOOR = 0.55`` admitted only ``N = 1`` of the 8 unanswerable questions to
the judge. That censored measurement cannot tell whether a replacement cross-encoder separates *all* unanswerable
queries from genuine answers or merely happened to reject the one query that survived the dense pre-filter.

This module evaluates any :class:`~custom_memory_engine.engine.hybrid.RelevanceJudge` over the **full uncensored**
labelled retrieval set (:mod:`evaluation.retrieval.quality`):

* Every answerable question (56 total) against its gold target memory.
* Every ``no_match`` question (8 total) against **all** corpus memories (49 total), taking the maximum logit per
  unanswerable question without dense cosine pre-filtering.

It computes the weakest answerable logit, the strongest unanswerable logit, the implied midpoint floor, the margin to
the configured floor, whether the floor sits strictly inside the gap, and the SHA-256 fingerprint of the ONNX asset.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from custom_memory_engine.engine.hybrid import JUDGE_RELEVANCE_FLOOR, RelevanceJudge
from custom_memory_engine.integrations.cross_encoder import (
    CrossEncoderAssets,
    OnnxCrossEncoder,
    model_fingerprint,
    reranker_assets_for,
)
from evaluation.retrieval.quality import MEMORIES, QUESTIONS, Memory, Question


@dataclass(frozen=True, slots=True)
class JudgeFloorReport:
    """Uncensored full-set separation extremes for one relevance judge against a candidate floor."""

    model: str
    fingerprint: str | None
    floor: float
    weakest_answerable: float
    weakest_answerable_question: str
    weakest_answerable_memory: str
    strongest_unanswerable: float
    strongest_unanswerable_question: str
    strongest_unanswerable_memory: str
    implied_floor: float
    separation: float
    margin: float
    inside: bool
    answerable_count: int
    unanswerable_count: int
    corpus_size: int
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary dictionary with rounded logits."""
        return {
            "model": self.model,
            "fingerprint": self.fingerprint,
            "floor": self.floor,
            "weakest_answerable": round(self.weakest_answerable, 4),
            "weakest_answerable_question": self.weakest_answerable_question,
            "weakest_answerable_memory": self.weakest_answerable_memory,
            "strongest_unanswerable": round(self.strongest_unanswerable, 4),
            "strongest_unanswerable_question": self.strongest_unanswerable_question,
            "strongest_unanswerable_memory": self.strongest_unanswerable_memory,
            "implied_floor": round(self.implied_floor, 4),
            "separation": round(self.separation, 4),
            "margin": round(self.margin, 4),
            "inside": self.inside,
            "answerable_count": self.answerable_count,
            "unanswerable_count": self.unanswerable_count,
            "corpus_size": self.corpus_size,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

    def format_line(self) -> str:
        """Render the single-line summary used by ``scripts/check_judge_floor.py``."""
        status = "inside" if self.inside else "outside"
        fp = self.fingerprint or "unpinned"
        return (
            f"floor {self.floor:g} vs asset extremes "
            f"[{self.strongest_unanswerable:.4f}, {self.weakest_answerable:.4f}] — {status} "
            f"(implied midpoint {self.implied_floor:.4f}, margin {self.margin:.4f}, fingerprint {fp})"
        )


def _judge_fingerprint(judge: RelevanceJudge) -> str | None:
    assets = getattr(judge, "assets", None)
    if isinstance(assets, CrossEncoderAssets):
        return model_fingerprint(assets)
    return None


def _validated_floor(floor: float) -> float:
    if isinstance(floor, bool) or not isinstance(floor, int | float) or not isfinite(float(floor)):
        raise ValueError("finite_numeric_floor_required")
    return float(floor)


async def evaluate_judge_floor(
    judge: RelevanceJudge,
    *,
    floor: float = JUDGE_RELEVANCE_FLOOR,
    fingerprint: str | None = None,
    memories: Sequence[Memory] = MEMORIES,
    questions: Sequence[Question] = QUESTIONS,
) -> JudgeFloorReport:
    """Measure ``judge`` over the full uncensored retrieval set and compare against ``floor``.

    For every answerable question, scores its gold target memory; for every ``no_match`` question, scores every
    memory in ``memories`` and takes the maximum logit.
    """
    floor_value = _validated_floor(floor)
    if not memories:
        raise ValueError("nonempty_corpus_required")

    memory_by_key = {memory.key: memory for memory in memories}
    if len(memory_by_key) != len(memories):
        raise ValueError("duplicate_memory_key")
    corpus_texts = [memory.text for memory in memories]
    corpus_keys = [memory.key for memory in memories]

    answerable = [q for q in questions if q.kind != "no_match"]
    unanswerable = [q for q in questions if q.kind == "no_match"]
    if not answerable or not unanswerable:
        raise ValueError("both_answerable_and_unanswerable_questions_required")

    for question in answerable:
        if question.answer is None or question.answer not in memory_by_key:
            raise ValueError(f"missing_gold_memory:{question.text}")

    resolved_fp = fingerprint if fingerprint is not None else await asyncio.to_thread(_judge_fingerprint, judge)
    started = time.perf_counter()

    answerable_results: list[tuple[float, str, str]] = []
    for question in answerable:
        assert question.answer is not None
        gold = memory_by_key[question.answer]
        scores = list(await judge.relevance(question.text, [gold.text]))
        if len(scores) != 1 or not isfinite(float(scores[0])):
            raise ValueError("invalid_judge_score_output")
        answerable_results.append((float(scores[0]), question.text, gold.key))

    unanswerable_results: list[tuple[float, str, str]] = []
    for question in unanswerable:
        scores = list(await judge.relevance(question.text, corpus_texts))
        if len(scores) != len(corpus_texts) or not all(isfinite(float(s)) for s in scores):
            raise ValueError("invalid_judge_score_output")
        best_idx = max(range(len(scores)), key=lambda idx: float(scores[idx]))
        unanswerable_results.append((float(scores[best_idx]), question.text, corpus_keys[best_idx]))

    elapsed = time.perf_counter() - started
    weakest_score, weakest_q, weakest_mem = min(answerable_results, key=lambda item: item[0])
    strongest_score, strongest_q, strongest_mem = max(unanswerable_results, key=lambda item: item[0])
    implied = (weakest_score + strongest_score) / 2.0
    separation = weakest_score - strongest_score
    margin = min(weakest_score - floor_value, floor_value - strongest_score)
    inside = strongest_score < floor_value < weakest_score

    return JudgeFloorReport(
        model=judge.model,
        fingerprint=resolved_fp,
        floor=floor_value,
        weakest_answerable=weakest_score,
        weakest_answerable_question=weakest_q,
        weakest_answerable_memory=weakest_mem,
        strongest_unanswerable=strongest_score,
        strongest_unanswerable_question=strongest_q,
        strongest_unanswerable_memory=strongest_mem,
        implied_floor=implied,
        separation=separation,
        margin=margin,
        inside=inside,
        answerable_count=len(answerable_results),
        unanswerable_count=len(unanswerable_results),
        corpus_size=len(memories),
        elapsed_seconds=elapsed,
    )


async def measure_asset_floor(
    asset_name: str,
    directory: Path,
    *,
    floor: float = JUDGE_RELEVANCE_FLOOR,
    threads: int = 0,
    memories: Sequence[Memory] = MEMORIES,
    questions: Sequence[Question] = QUESTIONS,
) -> JudgeFloorReport:
    """Validate an ONNX cross-encoder asset in ``directory`` and run :func:`evaluate_judge_floor`."""

    def _prepare() -> tuple[CrossEncoderAssets, str]:
        assets = reranker_assets_for(asset_name, directory)
        return assets, model_fingerprint(assets)

    assets, fp = await asyncio.to_thread(_prepare)
    judge = OnnxCrossEncoder(assets, threads=threads)
    return await evaluate_judge_floor(
        judge,
        floor=floor,
        fingerprint=fp,
        memories=memories,
        questions=questions,
    )


__all__ = [
    "JudgeFloorReport",
    "evaluate_judge_floor",
    "measure_asset_floor",
]
