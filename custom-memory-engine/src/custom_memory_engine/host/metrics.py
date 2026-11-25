"""Dependency-free Prometheus text format v0.0.4 metrics for the HTTP host (``#255``, ``V2-D119``).

Privacy invariant: no label or value may ever carry a tenant identity (``user_id``, ``principal``), credential
(``token``, ``authorization``), request path, query text, scope path, or memory content. Label names are allowlisted
per metric family and unknown operations collapse to ``_unknown`` to keep cardinality bounded.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from math import isfinite

from custom_memory_engine.api.contract import BY_NAME

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

DEFAULT_DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

#: Labels that must never appear on any emitted metric series.
FORBIDDEN_LABELS: frozenset[str] = frozenset(
    {
        "user_id",
        "principal",
        "token",
        "path",
        "query",
        "content",
        "scope",
        "scope_path",
        "authorization",
        "body",
        "evidence_span",
        "embedding",
    }
)

#: Exact allowlisted label names per metric family.
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "cme_host_calls_total": ("operation", "tier", "status"),
    "cme_host_call_duration_seconds": ("operation", "tier"),
    "cme_host_rate_limited_total": ("operation", "tier"),
    "cme_queue_depth": ("kind", "status"),
    "cme_queue_dead_letters_total": (),
    "cme_degradations_total": ("component",),
}

_SAFE_TOKEN = re.compile(r"[a-zA-Z0-9_.:-]{1,64}")


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(names: tuple[str, ...], values: tuple[str, ...], extra: tuple[str, str] | None = None) -> str:
    for name in names:
        if name in FORBIDDEN_LABELS:
            raise ValueError(f"forbidden_metric_label:{name}")
    pairs = list(zip(names, values, strict=True))
    if extra is not None:
        pairs.append(extra)
    if not pairs:
        return ""
    rendered = ",".join(f'{name}="{_escape_label_value(value)}"' for name, value in pairs)
    return f"{{{rendered}}}"


def _norm_operation(operation: str | None) -> str:
    if isinstance(operation, str) and operation in BY_NAME:
        return operation
    return "_unknown"


def _norm_tier(tier: str | None) -> str:
    if tier in ("B", "C"):
        return tier
    return "none"


def _norm_token(value: str, fallback: str = "_unknown") -> str:
    if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
        return value
    return fallback


class HostMetrics:
    """Thread-safe in-memory collector rendering Prometheus text format v0.0.4 without external packages."""

    def __init__(self, *, buckets: tuple[float, ...] = DEFAULT_DURATION_BUCKETS) -> None:
        if not buckets or any(not isfinite(b) or b <= 0.0 for b in buckets):
            raise ValueError("histogram_buckets_must_be_positive_finite")
        if tuple(sorted(buckets)) != tuple(buckets):
            raise ValueError("histogram_buckets_must_be_sorted")
        for metric_name, label_names in METRIC_LABELS.items():
            overlap = FORBIDDEN_LABELS.intersection(label_names)
            if overlap:
                raise ValueError(f"forbidden_metric_label:{metric_name}:{sorted(overlap)[0]}")
        self._buckets = buckets
        self._lock = threading.Lock()
        self._calls: dict[tuple[str, str, str], int] = {}
        self._duration_buckets: dict[tuple[str, str], list[int]] = {}
        self._duration_sums: dict[tuple[str, str], float] = {}
        self._duration_counts: dict[tuple[str, str], int] = {}
        self._rate_limited: dict[tuple[str, str], int] = {}
        self._degradations: dict[str, int] = {}
        self._queue_snapshot: dict[tuple[str, str], int] = {}

    def record_call(
        self,
        *,
        operation: str | None,
        tier: str | None,
        status: int,
        duration_seconds: float,
        rate_limited: bool = False,
    ) -> None:
        """Record one host call outcome, latency bucket, and optional 429 rate/concurrency refusal."""
        op_label = _norm_operation(operation)
        tier_label = _norm_tier(tier)
        status_label = str(int(status))
        duration = max(0.0, float(duration_seconds)) if isfinite(duration_seconds) else 0.0
        with self._lock:
            call_key = (op_label, tier_label, status_label)
            self._calls[call_key] = self._calls.get(call_key, 0) + 1

            dur_key = (op_label, tier_label)
            counts = self._duration_buckets.get(dur_key)
            if counts is None:
                counts = [0] * (len(self._buckets) + 1)
                self._duration_buckets[dur_key] = counts
            for idx, upper in enumerate(self._buckets):
                if duration <= upper:
                    counts[idx] += 1
            counts[-1] += 1
            self._duration_sums[dur_key] = self._duration_sums.get(dur_key, 0.0) + duration
            self._duration_counts[dur_key] = self._duration_counts.get(dur_key, 0) + 1

            if rate_limited:
                self._rate_limited[dur_key] = self._rate_limited.get(dur_key, 0) + 1

    def record_rate_limited(self, *, operation: str | None, tier: str | None) -> None:
        """Increment ``cme_host_rate_limited_total`` directly."""
        key = (_norm_operation(operation), _norm_tier(tier))
        with self._lock:
            self._rate_limited[key] = self._rate_limited.get(key, 0) + 1

    def record_degradation(self, component: str) -> None:
        """Increment ``cme_degradations_total`` for a static component name (e.g. ``embedder``, ``judge``)."""
        if component in FORBIDDEN_LABELS:
            raise ValueError(f"forbidden_metric_label:{component}")
        comp = _norm_token(component)
        with self._lock:
            self._degradations[comp] = self._degradations.get(comp, 0) + 1

    def set_queue_snapshot(self, counts: Mapping[tuple[str, str], int]) -> None:
        """Store aggregated ``(kind, status) -> count`` queue depth metrics (zero tenant identifiers)."""
        sanitized: dict[tuple[str, str], int] = {}
        for (kind, status), count in counts.items():
            key = (_norm_token(kind), _norm_token(status))
            sanitized[key] = sanitized.get(key, 0) + max(0, int(count))
        with self._lock:
            self._queue_snapshot = sanitized

    def render(self, queue_counts: Mapping[tuple[str, str], int] | None = None) -> str:
        """Render all metric families in Prometheus exposition text format v0.0.4."""
        with self._lock:
            calls = dict(self._calls)
            dur_buckets = {k: list(v) for k, v in self._duration_buckets.items()}
            dur_sums = dict(self._duration_sums)
            dur_counts = dict(self._duration_counts)
            rate_limited = dict(self._rate_limited)
            degradations = dict(self._degradations)
            snapshot = (
                {(_norm_token(k), _norm_token(s)): max(0, int(c)) for (k, s), c in queue_counts.items()}
                if queue_counts is not None
                else dict(self._queue_snapshot)
            )

        lines: list[str] = []

        lines.append("# HELP cme_host_calls_total Total number of HTTP host calls by operation, tier, and status.")
        lines.append("# TYPE cme_host_calls_total counter")
        for (op, tier, status), value in sorted(calls.items()):
            labels = _format_labels(METRIC_LABELS["cme_host_calls_total"], (op, tier, status))
            lines.append(f"cme_host_calls_total{labels} {value}")

        lines.append("# HELP cme_host_call_duration_seconds HTTP host call duration in seconds.")
        lines.append("# TYPE cme_host_call_duration_seconds histogram")
        for op, tier in sorted(dur_counts):
            buckets = dur_buckets[(op, tier)]
            for idx, upper in enumerate(self._buckets):
                labels = _format_labels(
                    METRIC_LABELS["cme_host_call_duration_seconds"], (op, tier), ("le", f"{upper:g}")
                )
                lines.append(f"cme_host_call_duration_seconds_bucket{labels} {buckets[idx]}")
            inf_labels = _format_labels(METRIC_LABELS["cme_host_call_duration_seconds"], (op, tier), ("le", "+Inf"))
            lines.append(f"cme_host_call_duration_seconds_bucket{inf_labels} {buckets[-1]}")
            base_labels = _format_labels(METRIC_LABELS["cme_host_call_duration_seconds"], (op, tier))
            lines.append(f"cme_host_call_duration_seconds_sum{base_labels} {dur_sums[(op, tier)]:.6f}")
            lines.append(f"cme_host_call_duration_seconds_count{base_labels} {dur_counts[(op, tier)]}")

        lines.append("# HELP cme_host_rate_limited_total Total number of 429 resource-limit refusals.")
        lines.append("# TYPE cme_host_rate_limited_total counter")
        for (op, tier), value in sorted(rate_limited.items()):
            labels = _format_labels(METRIC_LABELS["cme_host_rate_limited_total"], (op, tier))
            lines.append(f"cme_host_rate_limited_total{labels} {value}")

        lines.append("# HELP cme_queue_depth Current number of jobs in the slow-lane queue by kind and status.")
        lines.append("# TYPE cme_queue_depth gauge")
        for (kind, status), value in sorted(snapshot.items()):
            labels = _format_labels(METRIC_LABELS["cme_queue_depth"], (kind, status))
            lines.append(f"cme_queue_depth{labels} {value}")

        dead_letters = sum(count for (_, status), count in snapshot.items() if status == "dead_letter")
        lines.append("# HELP cme_queue_dead_letters_total Total number of dead-lettered slow-lane jobs.")
        lines.append("# TYPE cme_queue_dead_letters_total counter")
        lines.append(f"cme_queue_dead_letters_total {dead_letters}")

        lines.append("# HELP cme_degradations_total Total number of component degradations (embedder, judge).")
        lines.append("# TYPE cme_degradations_total counter")
        for component, value in sorted(degradations.items()):
            labels = _format_labels(METRIC_LABELS["cme_degradations_total"], (component,))
            lines.append(f"cme_degradations_total{labels} {value}")

        return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_DURATION_BUCKETS",
    "FORBIDDEN_LABELS",
    "METRIC_LABELS",
    "PROMETHEUS_CONTENT_TYPE",
    "HostMetrics",
]
