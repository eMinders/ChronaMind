"""Deletion leak scanner probes for the evaluation harness (#46 / RL-070, E-037).

Supplies caller/harness probes for channels 3, 5, 6, 7, 1 (cache surface) and V2 cognition channels 8, 9 (rendered
statements and bounds), 11 (pre-deletion export snapshots), and 12 (entity registry), so ``scan_deleted_key`` can
return a fully measured 7-channel or 12-channel report without ever reporting an unmeasured channel as zero.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from custom_memory_engine.deletion.scanner import (
    ALL_CHANNELS,
    COGNITION_CHANNELS,
    ChannelProbe,
    DeletionScanReport,
    LeakChannel,
    ProbeMeasurement,
    ScanContext,
    scan_deleted_key,
)

# A canary is matched as a literal substring. It is NOT compiled into a regex,
# so a canary containing `.` or `*` cannot accidentally widen the match, and no
# pattern-matching engine is involved in a security decision.
CANARY_MIN_LENGTH = 8


class LeakScannerError(RuntimeError):
    """A scanner precondition failed. The code is static and never echoes content."""

    def __init__(self, code: str, channel: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.channel = channel


# ---------------------------------------------------------------------------
# Observation surfaces
# ---------------------------------------------------------------------------


class AssembledContextSource(Protocol):
    """Provides the exact text a system would put in front of a model (channel 5)."""

    def assembled_context(self, user_id: str, query: str) -> str: ...


class AnswerSource(Protocol):
    """Provides a generative answer for a question (channel 6)."""

    def answer(self, user_id: str, question: str) -> str: ...


class VectorSearchSource(Protocol):
    """Provides ranked ids/text from the vector channel (channel 3)."""

    def vector_hits(self, user_id: str, query: str, limit: int) -> Sequence[str]: ...


class HypothesisSource(Protocol):
    """Provides rendered hypothesis-channel items (channel 8)."""

    def rendered_hypotheses(self, user_id: str) -> Sequence[str]: ...


class AbstractionRenderSource(Protocol):
    """Provides rendered abstraction statements including their rendered bounds (channel 9)."""

    def rendered_abstractions(self, user_id: str) -> Sequence[str]: ...


class ExportSnapshotSource(Protocol):
    """Provides payloads of export snapshots still servable for a user (channel 11)."""

    def servable_snapshots(self, user_id: str) -> Sequence[str]: ...


class EntityRegistrySource(Protocol):
    """Provides entity names, aliases, and alias provenance labels still present for a user (channel 12)."""

    def entity_registry_entries(self, user_id: str) -> Sequence[str]: ...


type LogSink = Callable[[], Iterable[str]]


class MeasuringProbe(Protocol):
    """A channel probe that always reports a :class:`ProbeMeasurement`.

    Structurally a ``ChannelProbe`` (its return type is a subtype), but precise
    enough that callers can read ``residual_count`` and ``complete`` directly.
    """

    async def __call__(self, context: ScanContext) -> ProbeMeasurement: ...


def _require_canary(canary: object) -> str:
    """Validate a canary so a short or blank value cannot produce false negatives."""
    if not isinstance(canary, str) or not canary.strip():
        raise LeakScannerError("nonblank_canary_required")
    if len(canary) < CANARY_MIN_LENGTH:
        raise LeakScannerError("canary_too_short_to_be_distinctive")
    return canary


def _count_occurrences(haystacks: Iterable[str], canary: str) -> int:
    """Count documents containing the canary, treating it as a literal."""
    return sum(1 for text in haystacks if canary in text)


# ---------------------------------------------------------------------------
# Channel probes 3, 5, 6, 7, 1 (cache) and V2 cognition channels 8, 9, 11, 12
# ---------------------------------------------------------------------------


def vector_probe(source: VectorSearchSource, *, query: str, limit: int = 50) -> MeasuringProbe:
    """Channel 3: the dense/vector channel must not return deleted content."""
    if limit < 1:
        raise LeakScannerError("positive_limit_required", "channel_3_vector_search")

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        hits = source.vector_hits(context.user_id, query, limit)
        return ProbeMeasurement(_count_occurrences(hits, canary), complete=True)

    return probe


def assembled_context_probe(source: AssembledContextSource, *, query: str) -> MeasuringProbe:
    """Channel 5: the assembled prompt/context must not contain deleted content."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        context_text = source.assembled_context(context.user_id, query)
        if not isinstance(context_text, str):
            raise LeakScannerError("assembled_context_must_be_a_string", "channel_5_assembled_context")
        return ProbeMeasurement(_count_occurrences((context_text,), canary), complete=True)

    return probe


def answer_probe(source: AnswerSource, *, question: str) -> MeasuringProbe:
    """Channel 6: a generated answer must not reproduce deleted content."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        answer_text = source.answer(context.user_id, question)
        if not isinstance(answer_text, str):
            raise LeakScannerError("answer_must_be_a_string", "channel_6_final_answer")
        return ProbeMeasurement(_count_occurrences((answer_text,), canary), complete=True)

    return probe


def log_sink_probe(sink: LogSink) -> MeasuringProbe:
    """Channel 7: application logs (and structured events) must not retain the canary."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        lines = tuple(sink())
        for line in lines:
            if not isinstance(line, str):
                raise LeakScannerError("log_lines_must_be_strings", "channel_7_application_logs")
        return ProbeMeasurement(_count_occurrences(lines, canary), complete=True)

    return probe


def external_cache_probe(lookup: Callable[[str], Sequence[str]]) -> MeasuringProbe:
    """Channel 1's cache surface: entries created before the epoch must be evicted."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        entries = tuple(lookup(context.user_id))
        return ProbeMeasurement(_count_occurrences(entries, canary), complete=True)

    return probe


def hypothesis_rendering_probe(source: HypothesisSource) -> MeasuringProbe:
    """Channel 8: rendered hypothesis-channel items must not retain deleted content."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        items = tuple(source.rendered_hypotheses(context.user_id))
        for item in items:
            if not isinstance(item, str):
                raise LeakScannerError("rendered_hypotheses_must_be_strings", "channel_8_hypothesis_rendering")
        return ProbeMeasurement(_count_occurrences(items, canary), complete=True)

    return probe


def abstraction_bounds_probe(source: AbstractionRenderSource) -> MeasuringProbe:
    """Channel 9's rendered surface: abstraction statements including their rendered bounds."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        items = tuple(source.rendered_abstractions(context.user_id))
        for item in items:
            if not isinstance(item, str):
                raise LeakScannerError(
                    "rendered_abstractions_must_be_strings", "channel_9_abstraction_statements_and_bounds"
                )
        return ProbeMeasurement(_count_occurrences(items, canary), complete=True)

    return probe


def export_snapshot_probe(source: ExportSnapshotSource) -> MeasuringProbe:
    """Channel 11: pre-deletion export snapshots must not be servable after deletion (F11)."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        snapshots = tuple(source.servable_snapshots(context.user_id))
        for item in snapshots:
            if not isinstance(item, str):
                raise LeakScannerError("export_snapshots_must_be_strings", "channel_11_export_snapshots")
        return ProbeMeasurement(_count_occurrences(snapshots, canary), complete=True)

    return probe


def entity_registry_probe(source: EntityRegistrySource) -> MeasuringProbe:
    """Channel 12: deleted entities, aliases, and alias provenance must not retain the canary."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        canary = _require_canary(context.canary)
        entries = tuple(source.entity_registry_entries(context.user_id))
        for item in entries:
            if not isinstance(item, str):
                raise LeakScannerError("entity_registry_entries_must_be_strings", "channel_12_entity_registry")
        return ProbeMeasurement(_count_occurrences(entries, canary), complete=True)

    return probe


# ---------------------------------------------------------------------------
# Composition and detection verification
# ---------------------------------------------------------------------------


def harness_probes(
    *,
    vector: tuple[VectorSearchSource, str] | None = None,
    assembled: tuple[AssembledContextSource, str] | None = None,
    answer: tuple[AnswerSource, str] | None = None,
    logs: LogSink | None = None,
    cache: Callable[[str], Sequence[str]] | None = None,
    hypotheses: HypothesisSource | None = None,
    abstractions: AbstractionRenderSource | None = None,
    export_snapshots: ExportSnapshotSource | None = None,
    entity_registry: EntityRegistrySource | None = None,
) -> dict[LeakChannel, ChannelProbe]:
    """Assemble the probes a caller has surfaces for, keyed by channel.

    Absent surfaces are simply omitted, so ``scan_deleted_key`` reports them as
    unmeasured (never as clean).
    """
    probes: dict[LeakChannel, ChannelProbe] = {}
    if vector is not None:
        probes["channel_3_vector_search"] = vector_probe(vector[0], query=vector[1])
    if assembled is not None:
        probes["channel_5_assembled_context"] = assembled_context_probe(assembled[0], query=assembled[1])
    if answer is not None:
        probes["channel_6_final_answer"] = answer_probe(answer[0], question=answer[1])
    if logs is not None:
        probes["channel_7_application_logs"] = log_sink_probe(logs)
    if cache is not None:
        probes["channel_1_storage_index_queue_cache_scan"] = external_cache_probe(cache)
    if hypotheses is not None:
        probes["channel_8_hypothesis_rendering"] = hypothesis_rendering_probe(hypotheses)
    if abstractions is not None:
        probes["channel_9_abstraction_statements_and_bounds"] = abstraction_bounds_probe(abstractions)
    if export_snapshots is not None:
        probes["channel_11_export_snapshots"] = export_snapshot_probe(export_snapshots)
    if entity_registry is not None:
        probes["channel_12_entity_registry"] = entity_registry_probe(entity_registry)
    return probes


@dataclass(frozen=True)
class DetectionVerification:
    """Result of proving the scanner can see a leak that is present."""

    injected_channel: LeakChannel
    detected: bool
    residual_count: int | None
    report: DeletionScanReport


async def verify_detection(
    *,
    context: ScanContext,
    probes: Mapping[LeakChannel, ChannelProbe],
    injected_channel: LeakChannel = "channel_7_application_logs",
) -> DetectionVerification:
    """Prove the scanner reports a leak when one is present (A8 sanity check)."""
    if injected_channel not in ALL_CHANNELS:
        raise LeakScannerError("unknown_deletion_channel", injected_channel)

    async def leaky(context: ScanContext) -> ProbeMeasurement:
        return ProbeMeasurement(1, complete=True)

    combined: dict[LeakChannel, ChannelProbe] = dict(probes)
    combined[injected_channel] = leaky
    target = ALL_CHANNELS if injected_channel in COGNITION_CHANNELS else None
    report = await scan_deleted_key(context, combined, channels=target)
    finding = next((f for f in report.findings if f.channel == injected_channel), None)
    detected = finding is not None and finding.leaked is True
    return DetectionVerification(
        injected_channel, detected, None if finding is None else finding.residual_count, report
    )


@dataclass(frozen=True)
class SevenChannelVerdict:
    """A decision, not a report: deletion is clean only if every targeted channel is measured and zero."""

    clean: bool
    measured_channels: tuple[LeakChannel, ...]
    unmeasured_channels: tuple[LeakChannel, ...]
    leaking_channels: tuple[LeakChannel, ...]
    partial_channels: tuple[LeakChannel, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Return whether every channel produced a complete measurement."""
        return not self.unmeasured_channels and not self.partial_channels


def verdict(report: DeletionScanReport) -> SevenChannelVerdict:
    """Classify a scan, failing closed on any unmeasured or partial channel."""
    measured: tuple[LeakChannel, ...] = tuple(f.channel for f in report.findings if f.measured)
    unmeasured: tuple[LeakChannel, ...] = tuple(f.channel for f in report.findings if not f.measured)
    leaking: tuple[LeakChannel, ...] = tuple(f.channel for f in report.findings if f.leaked is True)
    partial: tuple[LeakChannel, ...] = tuple(f.channel for f in report.findings if f.measured and not f.complete)
    clean = not unmeasured and not leaking and not partial
    return SevenChannelVerdict(clean, measured, unmeasured, leaking, partial)


__all__ = [
    "CANARY_MIN_LENGTH",
    "AbstractionRenderSource",
    "AnswerSource",
    "AssembledContextSource",
    "DetectionVerification",
    "EntityRegistrySource",
    "ExportSnapshotSource",
    "HypothesisSource",
    "LeakScannerError",
    "LogSink",
    "MeasuringProbe",
    "SevenChannelVerdict",
    "VectorSearchSource",
    "abstraction_bounds_probe",
    "answer_probe",
    "assembled_context_probe",
    "entity_registry_probe",
    "export_snapshot_probe",
    "external_cache_probe",
    "hypothesis_rendering_probe",
    "harness_probes",
    "log_sink_probe",
    "vector_probe",
    "verdict",
    "verify_detection",
]
