"""Suite ``D`` deletion runner: seven measured channels under ``P`` and ``A8`` (#46 / RL-070).

For every scenario in a Suite ``D`` split this runner

1. ingests the scripted turns through the engine write path (gate on), with a
   deterministic scripted extractor and real frozen-model embeddings;
2. commits each scripted derived artifact with its ``MEMORY_DERIVATION`` edges;
3. warms an epoch-keyed context cache with the event's probe questions;
4. deletes the target through ``delete_memory`` under the configured ablation,
   then invalidates the cache through the engine's ``CacheInvalidator`` contract;
5. measures every probe on all seven channels and records per-channel residues.

The scenario is the unit of inference (`RL-014` ``D-16``). A scenario *leaks*
when any primary-scored event leaks on any channel. The report carries the leak
count and the exact one-sided 97.5% Clopper–Pearson upper bound.

Decisions made explicit (each is a documented substitute, never a silent one):

* **Extraction is scripted, not model-generated.** Suite ``D`` is a scripted
  lineage benchmark: each event names its memory key, and its derived artifacts
  are part of the fixture. A model extractor would add variance that ``H4`` does
  not measure.
* **Channel 6 uses an extractive answerer by default.** It returns the assembled
  context verbatim, so every canary reaching the model input reaches the
  "answer". The canaries are high-entropy fictional strings (``D-26``) that a
  model cannot produce without seeing them, so this is a conservative upper
  bound on a generative answer's leakage. Any ``Answerer`` can be supplied.
* **Summary re-derivation is extractive.** Rebuilding concatenates the
  surviving sources. This cannot introduce a deleted canary, so it isolates
  the engine's lineage behaviour from summariser behaviour.
* **Test splits are refused.** `PLAN/CONTRIBUTING.md` §2 and gate ``G-TS``
  (`PLAN/V2/ROADMAP_V2.md`) reserve them for the confirmatory phase.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from rich.console import Console
from rich.table import Table

from custom_memory_engine.config import TestDatabaseConfig
from custom_memory_engine.deletion.closure import invalidate_cache_entries
from custom_memory_engine.deletion.rederive import ActiveSource, RebuiltSummary
from custom_memory_engine.deletion.scanner import (
    CHANNELS,
    ChannelProbe,
    LeakChannel,
    ProbeMeasurement,
    ScanContext,
    database_channel_probes,
    scan_deleted_key,
)
from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.ingest import (
    TurnInput,
    apply_candidate,
    derived_candidate,
    persist_turn,
)
from custom_memory_engine.engine.predicates import current_state
from custom_memory_engine.engine.render import render_context
from custom_memory_engine.engine.retrieval import rank_versions
from custom_memory_engine.engine.transactions import CandidateVersionInput, EvidenceInput
from custom_memory_engine.schema import migrate
from evaluation.baselines.embedding import OnnxBgeEmbedder
from evaluation.harness.leak_scanner import log_sink_probe
from evaluation.stats.clopper_pearson import cp_upper
from research.benchmarks.own_D.generator import (
    MANIFEST_PATH,
    DeletionEventSpec,
    DeletionProbe,
    DeletionScenario,
    SuiteDDataset,
    generate_suite_d,
)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema/001_full_schema.sql"
TOKEN_BUDGET = 2000
VECTOR_TOP_K = 10
CP_TAIL = 0.025  # one-sided 97.5%, STATISTICS.md §4 and V2-D18/D19

type Ablation = Literal["P", "A8"]


class SuiteRunError(RuntimeError):
    """Static failure code; never echoes scenario content or connection strings."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Embedder(Protocol):
    """Frozen embedding boundary; returns one 384-dim vector per text."""

    def __call__(self, texts: Sequence[str], *, query: bool) -> list[list[float]]: ...


class Answerer(Protocol):
    """Channel 6 boundary: produce an answer from the assembled context."""

    def __call__(self, context: str, question: str) -> str: ...


def extractive_answerer(context: str, question: str) -> str:
    """Conservative channel-6 surrogate: everything the model saw, it may repeat."""
    return context


def onnx_embedder(assets_directory: Path) -> Embedder:
    """Wrap the pinned ONNX BGE model (hash-verified assets, no download)."""
    model = OnnxBgeEmbedder(assets_directory)

    def embed(texts: Sequence[str], *, query: bool) -> list[list[float]]:
        return [list(vector) for vector in model.embed(list(texts), query=query).vectors]

    return embed


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


@dataclass
class EventResult:
    event_id: str
    lineage_structure: str
    scored_in_primary: bool
    relearning_control: bool
    deletion_epoch: int
    channel_residuals: dict[str, int]
    incomplete_channels: list[str]
    expected_a8_leak_channels: list[str]

    @property
    def leaked(self) -> bool:
        return any(count > 0 for count in self.channel_residuals.values())


@dataclass
class ScenarioResult:
    scenario_id: str
    events: list[EventResult]
    never_stated_false_positives: int

    @property
    def leaked(self) -> bool:
        return any(event.leaked for event in self.events if event.scored_in_primary)


@dataclass
class SuiteReport:
    suite: str
    split: str
    ablation: str
    embedding_model: str
    answerer: str
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def leaking_scenarios(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.leaked)

    @property
    def incomplete_measurements(self) -> int:
        return sum(len(event.incomplete_channels) for s in self.scenarios for event in s.events)

    def channel_totals(self) -> dict[str, int]:
        totals: dict[str, int] = dict.fromkeys(CHANNELS, 0)
        for scenario in self.scenarios:
            for event in scenario.events:
                if event.scored_in_primary:
                    for channel, count in event.channel_residuals.items():
                        totals[channel] += count
        return totals

    def to_json(self) -> dict[str, object]:
        n = len(self.scenarios)
        k = self.leaking_scenarios
        return {
            "suite": self.suite,
            "split": self.split,
            "ablation": self.ablation,
            "embedding_model": self.embedding_model,
            "answerer": self.answerer,
            "scenario_count": n,
            "leaking_scenarios": k,
            "incomplete_measurements": self.incomplete_measurements,
            "cp_upper_one_sided_97_5": cp_upper(k, n, tail_probability=CP_TAIL) if n else None,
            "never_stated_false_positives": sum(s.never_stated_false_positives for s in self.scenarios),
            "primary_channel_totals": self.channel_totals(),
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "leaked": s.leaked,
                    "never_stated_false_positives": s.never_stated_false_positives,
                    "events": [{**asdict(e), "leaked": e.leaked} for e in s.events],
                }
                for s in self.scenarios
            ],
        }


# ---------------------------------------------------------------------------
# Scripted extraction
# ---------------------------------------------------------------------------


def _clauses(content: str) -> list[str]:
    """Split a compound statement into independently groundable clauses."""
    parts = [part.strip().rstrip(".") for part in re.split(r",\s+and\s+", content)]
    return [part for part in parts if part]


def scripted_candidates(
    event: DeletionEventSpec, user_id: str, turn_id: str, content: str, occurred_at: datetime, index: int
) -> list[CandidateVersionInput]:
    """Map one user turn to the memory items Suite ``D`` says it creates.

    The canary-bearing clause is keyed to ``target_memory_key``. In a summary
    event, the other clauses are keyed to the surviving source keys in order.
    Every evidence span is a verbatim substring of the user turn, so the
    validation gate's evidence check runs unmodified.
    """
    clauses = _clauses(content)
    others: list[str] = []
    if event.derived_artifact is not None:
        others = [key for key in event.derived_artifact.source_memory_keys if key != event.target_memory_key]
    candidates: list[CandidateVersionInput] = []
    spare = iter(others)
    for clause_index, clause in enumerate(clauses):
        holds_target = event.canary_value in clause or (len(clauses) == 1)
        memory_key = event.target_memory_key if holds_target else next(spare, None)
        if memory_key is None:
            continue
        predicate = event.topic_key if memory_key == event.target_memory_key else f"{event.topic_key}_companion"
        candidates.append(
            CandidateVersionInput(
                version_id=f"{turn_id}:{clause_index}",
                memory_key=memory_key,
                user_id=user_id,
                subject_key="user",
                predicate_key=predicate,
                content=clause,
                single_valued=True,
                valid_from=occurred_at,
                evidence=(EvidenceInput(turn_id=turn_id, evidence_span=clause, evidence_role="user"),),
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Epoch-keyed context cache (channel 1, cache surface)
# ---------------------------------------------------------------------------


class ContextCache:
    """Assembled-context cache keyed by ``(user, query, epoch)``.

    Implements the engine's ``CacheInvalidator`` contract: invalidation drops
    every entry written before the deletion epoch and preserves newer ones.
    """

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], tuple[int, str]] = {}

    def put(self, user_id: str, query: str, epoch: int, text: str) -> None:
        self.entries[(user_id, query)] = (epoch, text)

    async def invalidate_before_epoch(self, user_id: str, deletion_epoch: int) -> None:
        for key in [k for k, (epoch, _) in self.entries.items() if k[0] == user_id and epoch < deletion_epoch]:
            del self.entries[key]

    def texts(self, user_id: str) -> list[str]:
        return [text for (owner, _), (_, text) in self.entries.items() if owner == user_id]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    conn: AsyncConnection
    schema: str
    ablation: Ablation
    embed: Embedder
    answer: Answerer
    cache: ContextCache
    log_lines: list[str]
    clock: datetime
    sequence: int = 0

    def tick(self) -> datetime:
        self.clock = self.clock + timedelta(minutes=1)
        return self.clock


async def _set_clock(run: _Run, moment: datetime) -> None:
    await run.conn.execute("SELECT set_config('app.clock', %s, false)", (moment.isoformat(),))


async def _ensure_predicates(conn: AsyncConnection, schema: str, keys: Sequence[str]) -> None:
    for key in keys:
        await conn.execute(
            sql.SQL("INSERT INTO {p} VALUES (%s, 'single', 'semantic') ON CONFLICT DO NOTHING").format(
                p=sql.Identifier(schema, "predicate_schema")
            ),
            (key,),
        )


async def _deletion_epoch(run: _Run, user_id: str) -> int:
    async with run.conn.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT COALESCE(MAX(deletion_epoch), 0) FROM {r} WHERE user_id = %s").format(
                r=sql.Identifier(run.schema, "deletion_request")
            ),
            (user_id,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _assemble(run: _Run, user_id: str, query: str) -> str:
    vector = run.embed([query], query=True)[0]
    async with run.conn.transaction():
        outcome = await rank_versions(
            run.conn,
            current_state(user_id, run.clock),
            query_text=query,
            query_embedding=vector,
            embedding_model=EMBEDDING_MODEL,
            schema_name=run.schema,
        )
        rendered = render_context(
            tuple(ranked.version for ranked in outcome.ranked),
            mode="current",
            token_budget=TOKEN_BUDGET,
            reference_time=run.clock,
        )
    return "\n".join(item.text for item in rendered.items)


async def _vector_hits(run: _Run, user_id: str, query: str) -> list[str]:
    """Raw nearest neighbours over every stored vector for the user (stricter than retrieval)."""
    vector = run.embed([query], query=True)[0]
    async with run.conn.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT v.content FROM {e} AS e JOIN {v} AS v ON v.id = e.version_id "
                "WHERE e.user_id = %s AND e.embedding_model = %s "
                "ORDER BY e.embedding <=> %s::vector LIMIT %s"
            ).format(e=sql.Identifier(run.schema, "memory_embedding"), v=sql.Identifier(run.schema, "memory_version")),
            (user_id, EMBEDDING_MODEL, "[" + ",".join(repr(float(x)) for x in vector) + "]", VECTOR_TOP_K),
        )
        return [str(row[0]) for row in await cursor.fetchall()]


async def _ingest_event(run: _Run, scenario: DeletionScenario, event: DeletionEventSpec) -> None:
    user_id = scenario.user_id
    session_id = f"{scenario.scenario_id}:session"
    keys = [event.topic_key, f"{event.topic_key}_companion", f"{event.topic_key}_derived"]
    await _ensure_predicates(run.conn, run.schema, keys)
    source_versions: dict[str, str] = {}
    for index, turn in enumerate(event.turns):
        moment = run.tick()
        await _set_clock(run, moment)
        run.sequence += 1
        turn_id = f"{event.event_id}:t{index}"
        async with run.conn.transaction():
            await persist_turn(
                run.conn,
                TurnInput(turn_id, user_id, session_id, run.sequence, turn.role, turn.content, moment),
                schema_name=run.schema,
                create_user=True,
            )
        if turn.role != "user":
            continue
        candidates = scripted_candidates(event, user_id, turn_id, turn.content, moment, index)
        vectors = run.embed([c.content for c in candidates], query=False) if candidates else []
        for candidate, vector in zip(candidates, vectors, strict=True):
            async with run.conn.transaction():
                applied = await apply_candidate(
                    run.conn,
                    candidate,
                    evidence_turns=(("user", turn.content),),
                    schema_name=run.schema,
                    replay_time=moment,
                    ablation=run.ablation,
                    embedding=vector,
                    embedding_model=EMBEDDING_MODEL,
                )
            version = applied.committed.version_id if applied.committed is not None else None  # type: ignore[union-attr]
            if version is None and applied.supersession is not None:
                version = candidate.version_id
            if version is not None:
                source_versions[candidate.memory_key] = version

    artifact = event.derived_artifact
    if artifact is None:
        return
    sources = [source_versions[key] for key in artifact.source_memory_keys if key in source_versions]
    if len(sources) != len(artifact.source_memory_keys):
        raise SuiteRunError("derived_artifact_sources_not_ingested")
    moment = run.tick()
    await _set_clock(run, moment)
    candidate = derived_candidate(
        version_id=artifact.derived_version_id,
        user_id=user_id,
        memory_key=artifact.derived_version_id,
        subject_key="user",
        predicate_key=f"{event.topic_key}_derived",
        content=artifact.initial_derived_content,
        valid_from=moment,
        valid_until=None,
        source_version_ids=sources,
        derivation_type=artifact.derivation_type,
    )
    vector = run.embed([candidate.content], query=False)[0]
    async with run.conn.transaction():
        await apply_candidate(
            run.conn,
            candidate,
            evidence_turns=(),
            schema_name=run.schema,
            replay_time=moment,
            gate_enabled=(),
            embedding=vector,
            embedding_model=EMBEDDING_MODEL,
        )


def _summary_builder(run: _Run) -> Callable[[str, Sequence[ActiveSource]], object]:
    async def build(summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary:
        content = "Summary: " + "; ".join(source.content for source in sources)
        return RebuiltSummary(content, EMBEDDING_MODEL, tuple(run.embed([content], query=False)[0]))

    return build


def _harness_probes(run: _Run, user_id: str, probe: DeletionProbe) -> dict[LeakChannel, ChannelProbe]:
    async def vector(context: ScanContext) -> ProbeMeasurement:
        hits = await _vector_hits(run, user_id, probe.query_text)
        return ProbeMeasurement(sum(1 for hit in hits if context.canary in hit), complete=True)

    async def assembled(context: ScanContext) -> ProbeMeasurement:
        text = await _assemble(run, user_id, probe.query_text)
        run.cache.put(user_id, probe.query_text, context.deletion_epoch, text)
        return ProbeMeasurement(int(context.canary in text), complete=True)

    async def answer(context: ScanContext) -> ProbeMeasurement:
        text = await _assemble(run, user_id, probe.query_text)
        return ProbeMeasurement(int(context.canary in run.answer(text, probe.query_text)), complete=True)

    database = database_channel_probes(run.conn, schema_name=run.schema)
    storage = database["channel_1_storage_index_queue_cache_scan"]

    async def storage_and_cache(context: ScanContext) -> ProbeMeasurement:
        # Channel 1 is complete only when its SQL surfaces *and* the cache surface are measured.
        stored = await storage(context)
        sql_count = stored.residual_count if isinstance(stored, ProbeMeasurement) else int(stored)
        cached = sum(1 for text in run.cache.texts(user_id) if context.canary in text)
        return ProbeMeasurement(sql_count + cached, complete=True)

    return {
        **database,
        "channel_1_storage_index_queue_cache_scan": storage_and_cache,
        "channel_3_vector_search": vector,
        "channel_5_assembled_context": assembled,
        "channel_6_final_answer": answer,
        "channel_7_application_logs": log_sink_probe(lambda: list(run.log_lines)),
    }


async def _measure(run: _Run, user_id: str, memory_key: str, canary: str, epoch: int, probes: Sequence[DeletionProbe]):
    residuals: dict[str, int] = dict.fromkeys(CHANNELS, 0)
    incomplete: set[str] = set()
    for probe in probes:
        context = ScanContext(user_id, memory_key, canary, max(epoch, 1))
        report = await scan_deleted_key(context, _harness_probes(run, user_id, probe))
        for finding in report.findings:
            if not finding.measured or not finding.complete:
                incomplete.add(finding.channel)
            residuals[finding.channel] += finding.residual_count or 0
    return residuals, sorted(incomplete)


async def run_scenario(run: _Run, scenario: DeletionScenario) -> ScenarioResult:
    user_id = scenario.user_id
    await run.conn.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
    results: list[EventResult] = []
    for event in scenario.events:
        await _ingest_event(run, scenario, event)
        # Warm the cache before deletion, as a serving system would have.
        before = await _deletion_epoch(run, user_id)
        for probe in event.probes:
            run.cache.put(user_id, probe.query_text, before, await _assemble(run, user_id, probe.query_text))
        moment = run.tick()
        await _set_clock(run, moment)
        async with run.conn.transaction():
            receipt = await delete_memory(
                run.conn,
                user_id,
                scope=event.deletion_scope,
                target_ref=event.target_memory_key,
                ablation=run.ablation,
                summary_builder=_summary_builder(run),  # type: ignore[arg-type]
                schema_name=run.schema,
                replay_time=moment,
            )
        await invalidate_cache_entries(run.cache, user_id, receipt.deletion_epoch)
        residuals, incomplete = await _measure(
            run, user_id, event.target_memory_key, event.canary_value, receipt.deletion_epoch, event.probes
        )
        results.append(
            EventResult(
                event_id=event.event_id,
                lineage_structure=event.lineage_structure,
                scored_in_primary=event.scored_in_primary_pdlr and not event.is_relearning_control,
                relearning_control=event.is_relearning_control,
                deletion_epoch=receipt.deletion_epoch,
                channel_residuals=residuals,
                incomplete_channels=incomplete,
                expected_a8_leak_channels=list(event.expected_a8_leak_channels),
            )
        )
    control = scenario.never_stated_control_probe
    control_residuals, _ = await _measure(
        run, user_id, "never-stated", control.target_canary, await _deletion_epoch(run, user_id), (control,)
    )
    return ScenarioResult(scenario.scenario_id, results, sum(control_residuals.values()))


class _LogCapture(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__(level=logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record.getMessage() + " " + json.dumps(record.__dict__.get("args") or [], default=str))


def load_split(split: str) -> SuiteDDataset:
    """Regenerate the split deterministically and verify it against the frozen manifest.

    The data directory is not committed (it is reproducible), so the runner never trusts
    a local file: it regenerates and refuses any byte-level mismatch with ``MANIFEST.txt``.
    """
    if split != "dev":
        raise SuiteRunError("test_split_reserved_for_confirmatory_phase")
    dataset = generate_suite_d("dev")
    digest = hashlib.sha256((dataset.model_dump_json(indent=2) + "\n").encode()).hexdigest()
    manifest: dict[str, str] = {}
    for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            manifest[parts[1]] = parts[0]
    if manifest.get("own_D_dev.json") != digest:
        raise SuiteRunError("suite_d_dev_does_not_match_frozen_manifest")
    return dataset


async def run_suite(
    database_url: str,
    *,
    ablation: Ablation,
    embed: Embedder,
    answer: Answerer = extractive_answerer,
    split: str = "dev",
    limit: int | None = None,
    answerer_name: str = "extractive",
) -> SuiteReport:
    """Run Suite ``D`` in a fresh disposable schema and return the measured report."""
    if ablation not in ("P", "A8"):
        raise SuiteRunError("ablation_must_be_p_or_a8")
    dataset = load_split(split)
    scenarios = dataset.scenarios[:limit] if limit else dataset.scenarios
    schema = "cme_suite_d_" + uuid4().hex[:12]
    report = SuiteReport(dataset.suite, dataset.split, ablation, EMBEDDING_MODEL, answerer_name)
    async with await AsyncConnection.connect(database_url, autocommit=True, connect_timeout=10) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        async with await AsyncConnection.connect(database_url, autocommit=True, connect_timeout=10) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            await conn.execute(
                "\n".join(SCHEMA_SQL.with_name(name).read_text(encoding="utf-8") for _, name in migrate.MIGRATIONS),  # type: ignore[arg-type]
                prepare=False,
            )
            for scenario in scenarios:
                lines: list[str] = []
                handler = _LogCapture(lines)
                root = logging.getLogger()
                root.addHandler(handler)
                try:
                    with structlog.testing.capture_logs() as captured:
                        run = _Run(
                            conn,
                            schema,
                            ablation,
                            embed,
                            answer,
                            ContextCache(),
                            lines,
                            datetime(2025, 5, 1, 9, tzinfo=UTC),
                        )
                        result = await run_scenario(run, scenario)
                    lines.extend(json.dumps(entry, default=str) for entry in captured)
                    # Channel 7 must also see logs emitted during the scan itself: re-measure.
                    for event, spec in zip(result.events, scenario.events, strict=True):
                        late = sum(1 for line in lines if spec.canary_value in line)
                        event.channel_residuals["channel_7_application_logs"] = max(
                            event.channel_residuals["channel_7_application_logs"], late
                        )
                finally:
                    root.removeHandler(handler)
                report.scenarios.append(result)
    finally:
        async with await AsyncConnection.connect(database_url, autocommit=True, connect_timeout=10) as admin:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    return report


def render(report: SuiteReport, console: Console) -> None:
    data = report.to_json()
    table = Table(title=f"Suite D {report.split} — ablation {report.ablation}")
    table.add_column("channel")
    table.add_column("primary residual", justify="right")
    for channel, count in report.channel_totals().items():
        table.add_row(channel, str(count))
    console.print(table)
    console.print(
        f"leaking scenarios {data['leaking_scenarios']}/{data['scenario_count']}  "
        f"CP upper (97.5%) {data['cp_upper_one_sided_97_5']}  "
        f"incomplete {data['incomplete_measurements']}  "
        f"never-stated false positives {data['never_stated_false_positives']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suite D seven-channel deletion run (#46).")
    parser.add_argument("--ablation", choices=("P", "A8"), required=True)
    parser.add_argument("--assets", type=Path, required=True, help="verified BGE asset directory")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    database_url = os.environ.get("CME_TEST_DATABASE_URL", "")
    if not database_url:
        raise SuiteRunError("cme_test_database_url_required")
    TestDatabaseConfig.from_environment()  # refuses anything but the disposable test database
    report = asyncio.run(
        run_suite(
            database_url, ablation=args.ablation, embed=onnx_embedder(args.assets), split=args.split, limit=args.limit
        )
    )
    if args.out:
        args.out.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    render(report, Console())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Answerer",
    "ContextCache",
    "Embedder",
    "SuiteReport",
    "SuiteRunError",
    "extractive_answerer",
    "load_split",
    "onnx_embedder",
    "run_suite",
    "scripted_candidates",
]
