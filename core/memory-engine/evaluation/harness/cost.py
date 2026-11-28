"""Mapping engine retrieval cost records into harness :class:`~evaluation.harness.models.Usage` (E-036).

``Usage`` models **charged provider accounting** (``input_tokens``, ``output_tokens``, ``embedding_tokens``,
``cost_usd``) and documents that :meth:`Usage.zero` is used only when an operation made zero charged calls.

Engine retrieval (:class:`~custom_memory_engine.engine.cost.QueryCost` / :class:`CostReport`) measures local
per-stage latency, candidate counts and assembly context-budget tokens. Today it makes zero charged provider or
embedding calls, so mapping it into :class:`Usage` yields :meth:`Usage.zero` while keeping non-zero latency and
context token counts on ``QueryCost`` / ``CostReport`` (and ``RetrievalResult.retrieval_latency_ms`` /
``context_tokens``). Fabricating a currency figure for zero-charged-call work is forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping

from custom_memory_engine.engine.cost import CostError, CostReport, QueryCost
from evaluation.harness.models import Usage

#: Explicit accounting boundary between engine retrieval cost and harness ``Usage`` (E-036 criterion 4).
USAGE_FIELD_MAPPING: Mapping[str, str] = {
    "input_tokens": (
        "0 today (no LLM call in staged retrieval; assembly tokens_considered/tokens_rendered "
        "are context budget counts, not billed provider input tokens)"
    ),
    "output_tokens": "0 today (no LLM generation in staged retrieval)",
    "embedding_tokens": (
        "0 today (ReadHandlers runs SQL temporal/read/association queries without calling an "
        "embedding model; dense rank_versions accepts a precomputed vector)"
    ),
    "cost_usd": "Decimal(0) via Usage.zero() when charged_calls == 0; never fabricated for uncharged local work",
}


def usage_from_query_cost(cost: QueryCost) -> Usage:
    """Convert one :class:`QueryCost` into harness :class:`Usage` without fabricating charged cost."""
    if cost.charged_calls == 0:
        return Usage.zero()
    return Usage(
        input_tokens=cost.input_tokens,
        output_tokens=cost.output_tokens,
        embedding_tokens=cost.embedding_tokens,
        cost_usd=cost.cost_usd,
    )


def usage_from_cost_report(report: CostReport) -> Usage:
    """Convert a windowed :class:`CostReport` into harness :class:`Usage` without fabricating charged cost."""
    if report.charged_calls == 0:
        if (
            report.input_tokens != 0
            or report.output_tokens != 0
            or report.embedding_tokens != 0
            or report.cost_usd != 0
        ):
            raise CostError("fabricated_charged_cost")
        return Usage.zero()
    return Usage(
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
        embedding_tokens=report.embedding_tokens,
        cost_usd=report.cost_usd,
    )


__all__ = ["USAGE_FIELD_MAPPING", "usage_from_cost_report", "usage_from_query_cost"]
