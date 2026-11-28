"""Structured deletion verification across 7 v1 + 5 cognition channels (RL-070, E-037; ``ARCHITECTURE_V2.md`` §10.3).

``CHANNELS`` preserves the 7 v1 channels intact so all existing ``RL-070`` 7-channel ``ENFORCED`` tests stay untouched.
``COGNITION_CHANNELS`` adds channels 8–12 for V2 deletion through cognition:

* **Channel 8** (``channel_8_hypothesis_rendering``): hypothesis-channel rendering (``QueryResult.hypotheses``).
* **Channel 9** (``channel_9_abstraction_statements_and_bounds``): abstraction statements **including their rendered
  bounds** (engine DB ``memory_version``/``abstraction_stats`` supplemented with rendered channel output).
* **Channel 10** (``channel_10_verification_records_and_methods``): ``verification`` records and ``method``
  registry metadata/parameters.
* **Channel 11** (``channel_11_export_snapshots``): export snapshots generated before the deletion (F11).
* **Channel 12** (``channel_12_entity_registry``): ``entity`` registry names and aliases, and ``artifact_provenance``
  alias attribution rows (``I8``, ``V2-D111``). An entity's vector (``embedding_1024``, migration 26) is a column of
  the same row, embedded from its name: it is not content a canary can match, so it is covered through its row. An
  erased entity takes its vector with it, and a surviving row whose name or alias still carries the canary is counted
  whether or not it has a vector. Not measured: a vector whose row no longer carries the deleted name, which no engine
  path produces today (no path renames an entity).

``ALL_CHANNELS = (*CHANNELS, *COGNITION_CHANNELS)`` is the complete 12-channel surface. Unmeasured or partially
measured channels are always reported as ``inconclusive`` (``measured=False`` / ``complete=False``), never as zero.

**Outside every channel:** a copy a *client* keeps (an application's response cache, a transcript store). The engine
cannot observe a caller's process, so no channel here measures it and ``I8`` does not cover it; the shipped app keeps
no memory-content cache for that reason (``THREAT_MODEL.md`` T-43, ``V2-D116``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from re import fullmatch
from typing import Literal, Protocol

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

CHANNELS = (
    "channel_1_storage_index_queue_cache_scan",
    "channel_2_lexical_search",
    "channel_3_vector_search",
    "channel_4_derived_memory_lookup",
    "channel_5_assembled_context",
    "channel_6_final_answer",
    "channel_7_application_logs",
)

COGNITION_CHANNELS = (
    "channel_8_hypothesis_rendering",
    "channel_9_abstraction_statements_and_bounds",
    "channel_10_verification_records_and_methods",
    "channel_11_export_snapshots",
    "channel_12_entity_registry",
)

ALL_CHANNELS = (*CHANNELS, *COGNITION_CHANNELS)

type LeakChannel = Literal[
    "channel_1_storage_index_queue_cache_scan",
    "channel_2_lexical_search",
    "channel_3_vector_search",
    "channel_4_derived_memory_lookup",
    "channel_5_assembled_context",
    "channel_6_final_answer",
    "channel_7_application_logs",
    "channel_8_hypothesis_rendering",
    "channel_9_abstraction_statements_and_bounds",
    "channel_10_verification_records_and_methods",
    "channel_11_export_snapshots",
    "channel_12_entity_registry",
]


class ScannerError(RuntimeError):
    """A configured channel probe failed or returned an invalid measurement."""

    def __init__(self, code: str, channel: str) -> None:
        super().__init__(code)
        self.code = code
        self.channel = channel


@dataclass(frozen=True)
class ScanContext:
    """Non-secret identifiers required by a caller-owned channel probe."""

    user_id: str
    memory_key: str
    canary: str
    deletion_epoch: int


@dataclass(frozen=True)
class ProbeMeasurement:
    """Count plus completeness flag for a channel with more than one storage surface."""

    residual_count: int
    complete: bool


class ChannelProbe(Protocol):
    """One channel-specific check returning the number of residual matches."""

    async def __call__(self, context: ScanContext) -> int | ProbeMeasurement: ...


@dataclass(frozen=True)
class ChannelFinding:
    """Measured or explicitly unmeasured residual count for one deletion channel."""

    channel: LeakChannel
    measured: bool
    residual_count: int | None
    complete: bool

    @property
    def leaked(self) -> bool | None:
        """Return whether this channel leaked, or ``None`` if it was not measured."""
        return None if not self.measured else bool(self.residual_count)


@dataclass(frozen=True)
class DeletionScanReport:
    """Structured scan outcome across all targeted channels; unmeasured channels remain inconclusive."""

    deletion_epoch: int
    findings: tuple[ChannelFinding, ...]

    @property
    def leaks(self) -> tuple[ChannelFinding, ...]:
        """Return all measured channels with at least one residual match."""
        return tuple(finding for finding in self.findings if finding.leaked is True)

    @property
    def inconclusive(self) -> tuple[ChannelFinding, ...]:
        """Return unmeasured or partially measured channels."""
        return tuple(finding for finding in self.findings if not finding.measured or not finding.complete)


def database_channel_probes(
    connection: AsyncConnection,
    *,
    schema_name: str = "cme_engine",
    include_cognition: bool = False,
) -> dict[LeakChannel, ChannelProbe]:
    """Create database probes for storage/index/queue, derived-memory and (optionally) cognition channels.

    Channel 1 is marked partial unless a caller supplements it with an external-cache probe.
    When ``include_cognition=True``, adds database probes for channel 9 (marked partial until supplemented with a
    rendered-abstraction/bounds probe), channel 10 (``verification`` records and ``method`` metadata, complete),
    and channel 12 (``entity`` canonical names and aliases, and ``artifact_provenance`` aliases, complete; an entity's
    vector is covered through its row, see the module docstring).
    """
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise ScannerError("isolated_engine_schema_identifier_required", "channel_1_storage_index_queue_cache_scan")
    versions = sql.Identifier(schema_name, "memory_version")
    embeddings = sql.Identifier(schema_name, "memory_embedding")
    evidence = sql.Identifier(schema_name, "memory_evidence")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    jobs = sql.Identifier(schema_name, "job")
    audit = sql.Identifier(schema_name, "audit_event")
    requests = sql.Identifier(schema_name, "deletion_request")
    stats = sql.Identifier(schema_name, "abstraction_stats")
    verifications = sql.Identifier(schema_name, "verification")
    methods = sql.Identifier(schema_name, "method")
    entities = sql.Identifier(schema_name, "entity")
    artifact_prov = sql.Identifier(schema_name, "artifact_provenance")

    async def storage_probe(context: ScanContext) -> ProbeMeasurement:
        canary_pattern = f"%{context.canary}%"
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    """
                    SELECT
                      (SELECT count(*) FROM {v} WHERE user_id=%s AND
                        (memory_key=%s OR content ILIKE %s)) +
                      (SELECT count(*) FROM {e} AS e JOIN {v} AS v ON v.id=e.version_id
                        WHERE v.user_id=%s AND (v.memory_key=%s OR v.content ILIKE %s)) +
                      (SELECT count(*) FROM {x} AS x JOIN {v} AS v ON v.id=x.version_id
                        WHERE v.user_id=%s AND (v.memory_key=%s OR v.content ILIKE %s)) +
                      (SELECT count(*) FROM {j} WHERE user_id=%s AND
                        (payload::text ILIKE %s OR payload::text ILIKE %s)) +
                      (SELECT count(*) FROM {a} WHERE user_id=%s AND
                        (details::text ILIKE %s OR memory_ref ILIKE %s)) +
                      (SELECT count(*) FROM {r} WHERE user_id=%s AND
                        (target_ref ILIKE %s))
                    """
                ).format(v=versions, e=embeddings, x=evidence, j=jobs, a=audit, r=requests),
                (
                    context.user_id,
                    context.memory_key,
                    canary_pattern,
                    context.user_id,
                    context.memory_key,
                    canary_pattern,
                    context.user_id,
                    context.memory_key,
                    canary_pattern,
                    context.user_id,
                    canary_pattern,
                    f"%{context.memory_key}%",
                    context.user_id,
                    canary_pattern,
                    canary_pattern,
                    context.user_id,
                    canary_pattern,
                ),
            )
            row = await cursor.fetchone()
        return ProbeMeasurement(int(row[0]) if row else 0, complete=False)

    async def lexical_probe(context: ScanContext) -> int:
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "SELECT count(*) FROM {v} WHERE user_id=%s "
                    "AND content_fts @@ pg_catalog.plainto_tsquery('english', %s)"
                ).format(v=versions),
                (context.user_id, context.canary),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def derived_probe(context: ScanContext) -> int:
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    """
                    SELECT
                      (SELECT count(*) FROM {v} AS v
                        WHERE v.user_id=%s AND v.content ILIKE %s
                          AND v.layer IN ('semantic', 'persona')) +
                      (SELECT count(*) FROM {d} AS d
                        JOIN {v} AS v ON v.id IN (d.derived_version_id, d.source_version_id)
                        WHERE d.user_id=%s AND v.user_id=%s
                          AND (v.memory_key=%s OR v.content ILIKE %s))
                    """
                ).format(v=versions, d=derivations),
                (
                    context.user_id,
                    f"%{context.canary}%",
                    context.user_id,
                    context.user_id,
                    context.memory_key,
                    f"%{context.canary}%",
                ),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    probes: dict[LeakChannel, ChannelProbe] = {
        "channel_1_storage_index_queue_cache_scan": storage_probe,
        "channel_2_lexical_search": lexical_probe,
        "channel_4_derived_memory_lookup": derived_probe,
    }

    if include_cognition:

        async def abstraction_db_probe(context: ScanContext) -> ProbeMeasurement:
            canary_pattern = f"%{context.canary}%"
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        """
                        SELECT
                          (SELECT count(*) FROM {v} AS v
                            WHERE v.user_id=%s AND v.kind = 'abstraction'
                              AND v.content ILIKE %s) +
                          (SELECT count(*) FROM {s} AS s JOIN {v} AS v ON v.id = s.version_id AND v.user_id = s.user_id
                            WHERE s.user_id=%s AND v.lifecycle = 'retired' AND v.retirement_reason = 'source_deleted'
                              AND (s.confidence IS NOT NULL OR s.support_count > 0 OR s.opportunity_count > 0))
                        """
                    ).format(v=versions, s=stats),
                    (context.user_id, canary_pattern, context.user_id),
                )
                row = await cursor.fetchone()
            return ProbeMeasurement(int(row[0]) if row else 0, complete=False)

        async def verification_and_method_probe(context: ScanContext) -> ProbeMeasurement:
            canary_pattern = f"%{context.canary}%"
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        """
                        SELECT
                          (SELECT count(*) FROM {ver} AS ver
                            WHERE ver.user_id=%s AND (
                              ver.held_out_split_id ILIKE %s
                              OR ver.negative_control_slice_id ILIKE %s
                              OR ver.support_context_key ILIKE %s
                              OR ver.held_out_context_key ILIKE %s
                            )) +
                          (SELECT count(*) FROM {m} AS m
                            WHERE m.name ILIKE %s OR m.description ILIKE %s)
                        """
                    ).format(ver=verifications, m=methods),
                    (
                        context.user_id,
                        canary_pattern,
                        canary_pattern,
                        canary_pattern,
                        canary_pattern,
                        canary_pattern,
                        canary_pattern,
                    ),
                )
                row = await cursor.fetchone()
            return ProbeMeasurement(int(row[0]) if row else 0, complete=True)

        async def entity_registry_db_probe(context: ScanContext) -> ProbeMeasurement:
            canary_pattern = f"%{context.canary}%"
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {e} AS e WHERE e.user_id = %s AND ("
                        "e.canonical_name ILIKE %s "
                        "OR EXISTS (SELECT 1 FROM pg_catalog.unnest(e.aliases) AS al WHERE al ILIKE %s)"
                        ")"
                    ).format(e=entities),
                    (context.user_id, canary_pattern, canary_pattern),
                )
                entity_hits = int((await cursor.fetchone() or (0,))[0])
                await cursor.execute(
                    "SELECT pg_catalog.to_regclass(%s) IS NOT NULL",
                    (f"{schema_name}.artifact_provenance",),
                )
                has_ap = bool((await cursor.fetchone() or (False,))[0])
                ap_hits = 0
                if has_ap:
                    await cursor.execute(
                        sql.SQL(
                            "SELECT count(*) FROM {p} AS p WHERE p.user_id = %s "
                            "AND p.artifact_kind = 'entity_alias' AND p.alias_value ILIKE %s"
                        ).format(p=artifact_prov),
                        (context.user_id, canary_pattern),
                    )
                    ap_hits = int((await cursor.fetchone() or (0,))[0])
            return ProbeMeasurement(entity_hits + ap_hits, complete=True)

        probes["channel_9_abstraction_statements_and_bounds"] = abstraction_db_probe
        probes["channel_10_verification_records_and_methods"] = verification_and_method_probe
        probes["channel_12_entity_registry"] = entity_registry_db_probe

    return probes


def _supplemented(storage: ChannelProbe, surface: ChannelProbe) -> ChannelProbe:
    """Combine a SQL storage probe with a caller's extra surface; complete when that surface is complete."""

    async def probe(context: ScanContext) -> ProbeMeasurement:
        stored, extra = await storage(context), await surface(context)
        stored_count = stored.residual_count if isinstance(stored, ProbeMeasurement) else stored
        if isinstance(extra, ProbeMeasurement):
            return ProbeMeasurement(stored_count + extra.residual_count, complete=extra.complete)
        return ProbeMeasurement(stored_count + extra, complete=True)

    return probe


async def scan_deleted_key(
    context: ScanContext,
    probes: Mapping[LeakChannel, ChannelProbe] | None = None,
    *,
    connection: AsyncConnection | None = None,
    schema_name: str = "cme_engine",
    channels: Sequence[LeakChannel] | None = None,
) -> DeletionScanReport:
    """Run configured probes and return one structured finding for each targeted channel.

    When ``channels`` is omitted and no probe names a cognition channel (8–12), scans the 7 v1 ``CHANNELS`` so
    existing 7-channel callers keep exact compatibility. Passing ``channels=ALL_CHANNELS`` (or supplying any probe in
    ``COGNITION_CHANNELS``) scans all 12 channels, reporting any missing channel as unmeasured, never zero.
    """
    if not isinstance(context.user_id, str) or not context.user_id.strip():
        raise ScannerError("nonblank_user_id_required", "channel_1_storage_index_queue_cache_scan")
    if not isinstance(context.memory_key, str) or not context.memory_key.strip():
        raise ScannerError("nonblank_memory_key_required", "channel_1_storage_index_queue_cache_scan")
    if not isinstance(context.canary, str) or not context.canary.strip():
        raise ScannerError("nonblank_canary_required", "channel_1_storage_index_queue_cache_scan")
    if (
        isinstance(context.deletion_epoch, bool)
        or not isinstance(context.deletion_epoch, int)
        or context.deletion_epoch < 1
    ):
        raise ScannerError("positive_deletion_epoch_required", "channel_1_storage_index_queue_cache_scan")

    caller = dict(probes or {})
    unknown_caller = set(caller) - set(ALL_CHANNELS)
    if unknown_caller:
        raise ScannerError("unknown_deletion_channel", sorted(unknown_caller)[0])

    if channels is not None:
        unknown_target = set(channels) - set(ALL_CHANNELS)
        if unknown_target:
            raise ScannerError("unknown_deletion_channel", sorted(unknown_target)[0])
        target_channels: tuple[LeakChannel, ...] = tuple(channels)
    elif set(caller) & set(COGNITION_CHANNELS):
        target_channels = ALL_CHANNELS
    else:
        target_channels = CHANNELS

    wants_cognition = bool(set(target_channels) & set(COGNITION_CHANNELS))
    configured = (
        database_channel_probes(connection, schema_name=schema_name, include_cognition=wants_cognition)
        if connection is not None
        else {}
    )
    surface_ch1 = caller.pop("channel_1_storage_index_queue_cache_scan", None)
    surface_ch9 = caller.pop("channel_9_abstraction_statements_and_bounds", None)
    configured.update(caller)

    storage = configured.get("channel_1_storage_index_queue_cache_scan")
    if surface_ch1 is not None:
        # A caller's channel-1 probe (e.g. an external cache) supplements the SQL storage scan; it must
        # never replace it, or channel 1 would report "complete, zero" without scanning storage at all.
        configured["channel_1_storage_index_queue_cache_scan"] = (
            surface_ch1 if storage is None else _supplemented(storage, surface_ch1)
        )

    abs_db = configured.get("channel_9_abstraction_statements_and_bounds")
    if surface_ch9 is not None:
        # Channel 9 combines DB abstraction statements/bounds with rendered output; both must be clean for complete.
        configured["channel_9_abstraction_statements_and_bounds"] = (
            surface_ch9 if abs_db is None else _supplemented(abs_db, surface_ch9)
        )

    findings: list[ChannelFinding] = []
    for channel in target_channels:
        probe = configured.get(channel)
        if probe is None:
            findings.append(ChannelFinding(channel, False, None, False))
            continue
        try:
            measurement = await probe(context)
        except Exception as exc:
            raise ScannerError("deletion_channel_probe_failed", channel) from exc
        result = measurement if isinstance(measurement, ProbeMeasurement) else ProbeMeasurement(measurement, True)
        if (
            isinstance(result.residual_count, bool)
            or not isinstance(result.residual_count, int)
            or result.residual_count < 0
            or not isinstance(result.complete, bool)
        ):
            raise ScannerError("deletion_channel_count_invalid", channel)
        findings.append(ChannelFinding(channel, True, result.residual_count, result.complete))
    return DeletionScanReport(context.deletion_epoch, tuple(findings))


__all__ = [
    "ALL_CHANNELS",
    "CHANNELS",
    "COGNITION_CHANNELS",
    "ChannelFinding",
    "ChannelProbe",
    "DeletionScanReport",
    "LeakChannel",
    "ProbeMeasurement",
    "ScanContext",
    "ScannerError",
    "database_channel_probes",
    "scan_deleted_key",
]
