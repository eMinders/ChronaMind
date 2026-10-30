"""Structured seven-channel deletion verification (Proposal §10.3, RL-070)."""

from collections.abc import Mapping
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

type LeakChannel = Literal[
    "channel_1_storage_index_queue_cache_scan",
    "channel_2_lexical_search",
    "channel_3_vector_search",
    "channel_4_derived_memory_lookup",
    "channel_5_assembled_context",
    "channel_6_final_answer",
    "channel_7_application_logs",
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
    """Complete seven-channel scan outcome; unmeasured channels remain inconclusive."""

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
) -> dict[LeakChannel, ChannelProbe]:
    """Create database probes for storage/index/queue and derived-memory channels.

    Channel 1 is marked partial unless a caller overrides it with a probe that also
    checks its external cache. Vector search and channels 5–7 require caller-owned
    probes because they depend on the embedding model and evaluation/application
    stores outside this package.
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
                        (payload::text ILIKE %s OR payload::text ILIKE %s))
                    """
                ).format(v=versions, e=embeddings, x=evidence, j=jobs),
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

    return {
        "channel_1_storage_index_queue_cache_scan": storage_probe,
        "channel_2_lexical_search": lexical_probe,
        "channel_4_derived_memory_lookup": derived_probe,
    }


async def scan_deleted_key(
    context: ScanContext,
    probes: Mapping[LeakChannel, ChannelProbe] | None = None,
    *,
    connection: AsyncConnection | None = None,
    schema_name: str = "cme_engine",
) -> DeletionScanReport:
    """Run configured probes and return one structured finding for each of seven channels.

    Channels 1–4 can be backed by engine SQL/retrieval probes; channels 5–7 are
    normally supplied by the evaluation harness. Missing probes are reported as
    unmeasured, never as a successful zero-leak result. Probe errors are surfaced
    as ``ScannerError`` rather than silently converted to a clean scan.
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
    configured = database_channel_probes(connection, schema_name=schema_name) if connection is not None else {}
    configured.update(probes or {})
    unknown = set(configured) - set(CHANNELS)
    if unknown:
        raise ScannerError("unknown_deletion_channel", sorted(unknown)[0])

    findings: list[ChannelFinding] = []
    for channel in CHANNELS:
        probe = configured.get(channel)  # type: ignore[arg-type]
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
    "CHANNELS",
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
