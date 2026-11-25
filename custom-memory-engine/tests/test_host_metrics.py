"""Unit tests for dependency-free Prometheus /metrics rendering and privacy invariants (``#255``, ``V2-D119``)."""

from __future__ import annotations

import re

import pytest

from custom_memory_engine.host.metrics import (
    FORBIDDEN_LABELS,
    METRIC_LABELS,
    PROMETHEUS_CONTENT_TYPE,
    HostMetrics,
)
from custom_memory_engine.host.service import _InstrumentedEmbedder, _InstrumentedJudge


class FailingEmbedder:
    model = "fake/embed"
    dimensions = 1024

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        del texts, query
        raise RuntimeError("onnx_session_failed")


class FailingJudge:
    model = "fake/judge"

    async def relevance(self, query: str, documents: list[str]) -> list[float]:
        del query, documents
        raise RuntimeError("judge_failed")


def test_prometheus_text_format_renders_all_families_and_counts() -> None:
    assert PROMETHEUS_CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"
    metrics = HostMetrics(buckets=(0.01, 0.1, 1.0))

    metrics.record_call(operation="query", tier="B", status=200, duration_seconds=0.05)
    metrics.record_call(operation="query", tier="B", status=429, duration_seconds=0.002, rate_limited=True)
    metrics.record_call(operation="no_such_op", tier=None, status=403, duration_seconds=0.001)
    metrics.record_degradation("embedder")
    metrics.record_degradation("judge")

    rendered = metrics.render(
        queue_counts={
            ("semantic_extraction", "queued"): 4,
            ("semantic_extraction", "dead_letter"): 2,
            ("index_rebuild", "dead_letter"): 1,
        }
    )

    for metric in METRIC_LABELS:
        assert f"# HELP {metric} " in rendered
        assert f"# TYPE {metric} " in rendered

    assert 'cme_host_calls_total{operation="query",tier="B",status="200"} 1' in rendered
    assert 'cme_host_calls_total{operation="query",tier="B",status="429"} 1' in rendered
    # Unknown operation collapses to _unknown and missing tier becomes none (cardinality bound).
    assert 'cme_host_calls_total{operation="_unknown",tier="none",status="403"} 1' in rendered
    assert 'cme_host_rate_limited_total{operation="query",tier="B"} 1' in rendered

    # Histogram buckets are cumulative (0.002 <= 0.01 -> 1; 0.05 <= 0.1 -> 2; +Inf -> 2).
    assert 'cme_host_call_duration_seconds_bucket{operation="query",tier="B",le="0.01"} 1' in rendered
    assert 'cme_host_call_duration_seconds_bucket{operation="query",tier="B",le="0.1"} 2' in rendered
    assert 'cme_host_call_duration_seconds_bucket{operation="query",tier="B",le="+Inf"} 2' in rendered
    assert 'cme_host_call_duration_seconds_count{operation="query",tier="B"} 2' in rendered

    assert 'cme_queue_depth{kind="semantic_extraction",status="queued"} 4' in rendered
    assert "cme_queue_dead_letters_total 3" in rendered
    assert 'cme_degradations_total{component="embedder"} 1' in rendered
    assert 'cme_degradations_total{component="judge"} 1' in rendered


def test_metrics_forbid_tenant_token_and_content_labels() -> None:
    metrics = HostMetrics()
    for metric_name, labels in METRIC_LABELS.items():
        assert not FORBIDDEN_LABELS.intersection(labels), metric_name

    for forbidden in sorted(FORBIDDEN_LABELS):
        with pytest.raises(ValueError, match="forbidden_metric_label"):
            metrics.record_degradation(forbidden)

    # Even if an arbitrary string is passed as operation or queue status, it never emits user_id/content values.
    metrics.record_call(operation="alice_secret_passport", tier="B", status=403, duration_seconds=0.01)
    metrics.set_queue_snapshot({("semantic_extraction", "bob/private/note"): 1})
    rendered = metrics.render()
    assert "alice_secret_passport" not in rendered
    assert "bob/private/note" not in rendered
    # Verify no forbidden label key appears in `{...}` label blocks.
    label_blocks = re.findall(r"\{([^}]*)\}", rendered)
    for block in label_blocks:
        keys = {pair.split("=")[0].strip() for pair in block.split(",") if "=" in pair}
        assert not FORBIDDEN_LABELS.intersection(keys), block


async def test_instrumented_proxies_record_embedder_and_judge_degradations() -> None:
    metrics = HostMetrics()
    embedder = _InstrumentedEmbedder(FailingEmbedder(), metrics)  # type: ignore[arg-type]
    judge = _InstrumentedJudge(FailingJudge(), metrics)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="onnx_session_failed"):
        await embedder.embed(["hello world"], query=True)
    with pytest.raises(RuntimeError, match="judge_failed"):
        await judge.relevance("hello", ["world"])

    rendered = metrics.render()
    assert 'cme_degradations_total{component="embedder"} 1' in rendered
    assert 'cme_degradations_total{component="judge"} 1' in rendered
    assert "hello" not in rendered
    assert "world" not in rendered
