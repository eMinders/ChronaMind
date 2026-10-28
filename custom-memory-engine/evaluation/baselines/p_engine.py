"""System ``P``: the Custom Memory Engine behind the harness's synchronous adapter.

This is the bridge that makes `H1`–`H4` runnable. It owns no retrieval logic of its
own: ingesting a turn persists it, embedding the candidate, applying the validation
gate and committing through the transactional write path; retrieving calls the
engine's hybrid ranker; deleting calls the lineage-closure deletion.

Three deliberate boundaries:

* **No provider calls here.** Extraction is injected as a callable and embeddings
  arrive through ``ModelServices``, so ``P`` and its ablations replay one frozen
  extraction (``RL-032``).
* **The extraction cache is where the ablation invariant is enforced.** Passing a
  replay cache routes every slow-path extraction through
  ``(turn_id, extractor_version, model_id)``; a hit re-derives the identical
  ``CandidateVersionInput`` sequence while charging ``Usage.zero()``, so ablations
  cannot differ through LLM variance.
* **The engine is async, the harness is sync.** One private event loop per adapter
  thread-marshals each engine call. It is created once and closed on ``close``.
* **Ablations are configuration, not forks.** ``A1``/``A2``/``A2b`` are write-path
  switches; ``A3``/``A4`` disable gate checks; ``A5``/``A6`` select a retrieval
  channel. They all run this same adapter, each labelled by its own system id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

import structlog
from psycopg import AsyncConnection, sql

from custom_memory_engine.engine.deletion import delete_memory
from custom_memory_engine.engine.gate import GateCheck
from custom_memory_engine.engine.ingest import TurnInput, apply_candidate, persist_turn
from custom_memory_engine.engine.predicates import (
    TemporalQuery,
    current_state,
    historical_as_of,
    historical_window,
)
from custom_memory_engine.engine.render import render_context
from custom_memory_engine.engine.retrieval import FusionMode, rank_versions
from custom_memory_engine.engine.transactions import AblationMode, CandidateVersionInput, EvidenceInput
from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.context import fit_ranked_items, join_items
from evaluation.baselines.contracts import BaselineSettings, ModelServices
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import (
    AnswerResult,
    DeletionReceipt,
    DeletionScope,
    IngestReceipt,
    QueryMode,
    QuestionRecord,
    QueueDrainStats,
    RetrievalDiagnostics,
    RetrievalResult,
    RetrievedItem,
    TurnRecord,
    Usage,
)
from evaluation.harness.replay_cache import ExtractionCacheKey, ExtractionReplayCache

logger = structlog.get_logger(__name__)

T = TypeVar("T")

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src/custom_memory_engine/schema/001_full_schema.sql"

type Extractor = Callable[[TurnRecord], Sequence[CandidateVersionInput]]


def _replayed_instant(value: object, fallback: datetime) -> datetime:
    """Parse a cached ISO-8601 instant; the cache stores JSON, the engine needs ``datetime``."""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def cached_candidates(
    turn: TurnRecord,
    records: Sequence[Mapping[str, Any]],
    extractor_version: str,
) -> tuple[CandidateVersionInput, ...]:
    """Re-derive engine version inputs from cached provider candidate records.

    Deterministic by construction: the identity the cache does not carry
    (``version_id``, ``memory_key``, ``user_id``) is a pure function of the turn and the
    record's own subject/predicate, so replay is byte-stable across ablations. Cached
    records hold provider-owned fields only — never engine version rows.
    """
    candidates: list[CandidateVersionInput] = []
    for index, record in enumerate(records):
        subject_key = str(record["subject_key"])
        predicate_key = str(record["predicate_key"])
        candidates.append(
            CandidateVersionInput(
                version_id=f"{turn.turn_id}:{index}",
                memory_key=f"{subject_key}:{predicate_key}",
                user_id=turn.user_id,
                subject_key=subject_key,
                predicate_key=predicate_key,
                content=str(record["content"]),
                single_valued=bool(record.get("single_valued", True)),
                layer=record.get("layer", "episodic"),
                assertion_mode=record.get("assertion_mode", "asserted"),
                valid_from=_replayed_instant(record.get("valid_from"), turn.occurred_at),
                valid_until=(
                    None
                    if record.get("valid_to") is None
                    else _replayed_instant(record.get("valid_to"), turn.occurred_at)
                ),
                valid_from_basis=record.get("valid_from_basis", "explicit"),
                confidence=record.get("confidence"),
                importance=record.get("importance"),
                extractor_version=extractor_version,
                evidence=(
                    EvidenceInput(
                        turn_id=turn.turn_id,
                        evidence_span=str(record["evidence_span"]),
                        evidence_role=str(record.get("evidence_role", "user")),
                    ),
                ),
            )
        )
    return tuple(candidates)


@dataclass(frozen=True)
class EngineOptions:
    """Explicit engine configuration; every choice is frozen into the run manifest."""

    database_url: str
    schema_name: str = "cme_engine"
    ablation: AblationMode = "P"
    allow_a2b: bool = False
    fusion: FusionMode = "rrf"
    extractor_version: str = "engine-v1"
    gate_enabled: tuple[GateCheck, ...] = (
        "evidence",
        "assertion_mode",
        "instruction_screen",
        "conflict",
        "confidence",
    )


class EngineAdapter(BaseAdapter):
    """``P`` over the canonical schema. Extraction and embeddings are injected."""

    # ``P`` is the full pipeline; each ablation is an equally first-class system that
    # runs this same adapter with different switches. The identity is asserted per
    # instance against ``EngineOptions.ablation`` in ``__init__``.
    system_id = "P"

    def __init__(
        self,
        config: HarnessConfig,
        settings: BaselineSettings,
        services: ModelServices,
        *,
        options: EngineOptions,
        extractor: Extractor | None = None,
        replay_cache: ExtractionReplayCache | None = None,
    ) -> None:
        super().__init__(config, settings, services)
        if self.config.system_id != options.ablation:
            # One system id labels one configuration: ``P`` is the full pipeline and each
            # ablation is its own system, so a run can never mislabel what it measured
            # (``D-ENG-01`` needs ``P − A2`` as a real, separately-named run).
            raise ValueError("engine_ablation_requires_matching_system_id")
        if options.ablation == "A2b" and not options.allow_a2b:
            raise ValueError("a2b_disabled_by_d15")
        self.options = options
        self.extractor = extractor
        # Exposed for ``runner``'s manifest summary (RL-032); ``None`` keeps prior behaviour.
        self.replay_cache = replay_cache
        self._schema = options.schema_name
        self._loop = asyncio.new_event_loop()
        self._connection: AsyncConnection | None = None
        self._session_sequence: dict[str, int] = {}

    # ---------------------------------------------------------------- plumbing

    def _run_async(self, awaitable: Awaitable[T]) -> T:
        """Run one engine coroutine on the adapter's private loop."""
        return self._loop.run_until_complete(awaitable)

    def _conn(self) -> AsyncConnection:
        if self._connection is None:
            raise ValueError("engine_connection_not_open")
        return self._connection

    def _prepare_user(self, user_id: str) -> None:
        """Open a connection for this user and set the RLS tenant context."""
        self._close_connection()
        connection = self._run_async(
            AsyncConnection.connect(self.options.database_url, autocommit=True, connect_timeout=10)
        )
        self._run_async(
            connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self._schema)))
        )
        self._run_async(connection.execute("SELECT set_config('app.user_id', %s, false)", (user_id,)))
        self._connection = connection
        self._session_sequence[user_id] = 0

    def _close_connection(self) -> None:
        if self._connection is not None:
            self._run_async(self._connection.close())
            self._connection = None

    def _set_replay_clock(self, moment: datetime) -> None:
        self._run_async(self._conn().execute("SELECT set_config('app.clock', %s, false)", (moment.isoformat(),)))

    # ----------------------------------------------------------- harness hooks

    def reset_user(self, user_id: str) -> None:
        if not user_id.strip():
            raise ValueError("nonblank_engine_user_required")
        # The base class owns local per-user bookkeeping; this adapter adds the
        # engine connection that carries that user's tenant context.
        super().reset_user(user_id)
        self._prepare_user(user_id)

    def release_user(self, user_id: str) -> None:
        self._session_sequence.pop(user_id, None)
        super().release_user(user_id)
        self._close_connection()

    def set_clock(self, ts: datetime) -> None:
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError("aware_engine_clock_required")
        self.clock = ts
        self._prepared.clear()
        self._questions.clear()

    def close(self) -> None:
        self._close_connection()
        if not self._loop.is_closed():
            self._loop.close()

    # ------------------------------------------------------------------ ingest

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        self._validate_turn(turn)
        if self.clock is None:
            raise ValueError("engine_replay_clock_required")

        def ingest() -> None:
            connection = self._conn()
            self._set_replay_clock(self.clock)  # type: ignore[arg-type]
            candidates = self._extract(turn)
            embeddings = self.services.embed([c.content for c in candidates]) if candidates else None

            async def work() -> None:
                sequence = self._session_sequence.get(turn.user_id, 0) + 1
                self._session_sequence[turn.user_id] = sequence
                async with connection.transaction():
                    await persist_turn(
                        connection,
                        TurnInput(
                            turn_id=turn.turn_id,
                            user_id=turn.user_id,
                            session_id=turn.session_id,
                            sequence_no=sequence,
                            role=turn.role,
                            content=turn.content,
                            occurred_at=turn.occurred_at,
                        ),
                        schema_name=self._schema,
                        create_user=True,
                    )
                for index, candidate in enumerate(candidates):
                    vector = embeddings.vectors[index] if embeddings is not None else None
                    async with connection.transaction():
                        await apply_candidate(
                            connection,
                            candidate,
                            evidence_turns=((turn.role, turn.content),),
                            schema_name=self._schema,
                            replay_time=self.clock,
                            ablation=self.options.ablation,
                            allow_a2b=self.options.allow_a2b,
                            gate_enabled=self.options.gate_enabled,
                            embedding=vector,
                            embedding_model=self.config.embedding_model_id if vector is not None else None,
                        )

            self._run_async(work())

        _, usage = self._run("ingest_turn", ingest)
        return IngestReceipt(turn_id=turn.turn_id, usage=usage)

    def _extract(self, turn: TurnRecord) -> tuple[CandidateVersionInput, ...]:
        """Return this turn's candidates, replaying them from the RL-032 cache when configured.

        With a cache, the provider-owned candidate records are cached under
        ``(turn_id, extractor_version, model_id)`` and the engine inputs are re-derived, so
        ``P`` and every ablation observe the identical extraction. A hit charges
        ``Usage.zero()`` because the extractor callable is never invoked.
        """
        if self.extractor is None:
            return ()
        extractor = self.extractor
        if self.replay_cache is None:
            return tuple(extractor(turn))
        key = ExtractionCacheKey(
            turn_id=turn.turn_id,
            extractor_version=self.options.extractor_version,
            model_id=self.config.model_id,
        )

        def extract() -> tuple[Mapping[str, Any], ...]:
            # Runs only on a cache miss: the extractor callable is what a hit avoids.
            # Only provider-owned, schema-validatable fields enter the cache; engine
            # identity is re-derived on replay (see ``cached_candidates``).
            return tuple(
                {
                    "subject_key": candidate.subject_key,
                    "predicate_key": candidate.predicate_key,
                    "content": candidate.content,
                    "single_valued": candidate.single_valued,
                    "layer": candidate.layer,
                    "assertion_mode": candidate.assertion_mode,
                    "valid_from": candidate.valid_from.isoformat(),
                    "valid_to": candidate.valid_until.isoformat() if candidate.valid_until else None,
                    "valid_from_basis": candidate.valid_from_basis,
                    "confidence": candidate.confidence,
                    "importance": candidate.importance,
                    "evidence_span": candidate.evidence[0].evidence_span if candidate.evidence else candidate.content,
                    # The engine allows free-form evidence roles (its default is
                    # ``primary``), but the cache's frozen extraction schema admits only
                    # ``user``/``assistant``. Preserve a valid role; otherwise project the
                    # role of the turn whose content the span actually grounds.
                    "evidence_role": (
                        candidate.evidence[0].evidence_role
                        if candidate.evidence and candidate.evidence[0].evidence_role in {"user", "assistant"}
                        else turn.role
                    ),
                }
                for candidate in extractor(turn)
            )

        outcome = self.replay_cache.get_or_extract(key, extract)
        return cached_candidates(turn, outcome.candidates, self.options.extractor_version)

    def drain_queue(self, user_id: str) -> QueueDrainStats:
        self._state(user_id)

        def drain() -> None:
            connection = self._conn()

            async def work() -> int:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        sql.SQL("SELECT count(*) FROM {j} WHERE user_id = %s AND status = 'queued'").format(
                            j=sql.Identifier(self._schema, "job")
                        ),
                        (user_id,),
                    )
                    row = await cursor.fetchone()
                return int(row[0]) if row else 0

            pending = self._run_async(work())
            if pending:
                raise ValueError("engine_queue_not_drained")

        _, usage = self._run("drain_queue", drain)
        return QueueDrainStats(processed=0, pending=0, usage=usage)

    # ---------------------------------------------------------------- retrieve

    def _temporal_query(self, user_id: str, mode: QueryMode, as_of: datetime) -> TemporalQuery:
        if self.clock is None:
            raise ValueError("engine_reference_clock_required")
        reference = max(self.clock, as_of)
        if mode == "current":
            return current_state(user_id, reference)
        if mode in ("as_of", "before_change", "stable"):
            return historical_as_of(user_id, as_of, reference)
        if mode == "what_changed":
            # A change query needs the window the belief moved in; the harness supplies
            # only a target instant, so the window is (as_of, reference).
            return historical_window(user_id, as_of, reference, reference)
        raise ValueError("unsupported_engine_query_mode")

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        question = self._question(user_id, query, mode, as_of, token_budget)

        def retrieve() -> RetrievalResult:
            started = perf_counter()
            connection = self._conn()
            self._set_replay_clock(self.clock)  # type: ignore[arg-type]
            temporal = self._temporal_query(user_id, mode, as_of)
            embedded = self.services.embed((query,), query=True)
            vector = embedded.vectors[0] if embedded.vectors else None

            async def work() -> tuple[object, object]:
                async with connection.transaction():
                    outcome = await rank_versions(
                        connection,
                        temporal,
                        query_text=query,
                        query_embedding=vector,
                        fusion=self.options.fusion,
                        embedding_model=self.config.embedding_model_id,
                        schema_name=self._schema,
                    )
                    versions = tuple(ranked.version for ranked in outcome.ranked)
                    rendered = render_context(
                        versions,
                        mode=mode,
                        token_budget=token_budget,
                        reference_time=as_of,
                    )
                return outcome, rendered

            outcome, rendered = self._run_async(work())  # type: ignore[misc]

            items = tuple(
                RetrievedItem(
                    reference_id=item.version.id,
                    content=item.text,
                    evidence_turn_ids=(),
                )
                for item in rendered.items  # type: ignore[attr-defined]
            )
            fitted, dropped_tokens = fit_ranked_items(items, self.services.tokenizer, token_budget)
            context = join_items(fitted)
            diagnostics = RetrievalDiagnostics(
                context_policy=self.config.context_policy,
                source_items=len(outcome.ranked),  # type: ignore[attr-defined]
                selected_items=len(fitted),
                truncated_items=rendered.truncated_items + (len(items) - len(fitted)),  # type: ignore[attr-defined]
                truncated_tokens=rendered.truncated_tokens + dropped_tokens,  # type: ignore[attr-defined]
                candidate_count=outcome.candidates,  # type: ignore[attr-defined]
            )
            result = RetrievalResult(
                items=fitted,
                rendered_context=context,
                context_tokens=len(self.services.tokenizer.encode(context)),
                retrieval_latency_ms=round((perf_counter() - started) * 1000, 3),
                diagnostics=diagnostics,
                usage=Usage.zero(),
            )
            return self._remember(question, as_of, result)

        result, usage = self._run("retrieve", retrieve)
        return result.model_copy(update={"usage": usage})

    # ------------------------------------------------------------------ answer

    def answer(self, user_id: str, question: QuestionRecord, as_of: datetime) -> AnswerResult:
        return super().answer(user_id, question, as_of)

    # ------------------------------------------------------------------ delete

    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt:
        self._state(user_id)
        if scope != "all" and not target_ref.strip():
            raise ValueError("nonblank_engine_deletion_target_required")
        if self.clock is None:
            raise ValueError("engine_replay_clock_required")

        def remove() -> int:
            connection = self._conn()
            self._set_replay_clock(self.clock)  # type: ignore[arg-type]

            async def work() -> int:
                async with connection.transaction():
                    receipt = await delete_memory(
                        connection,
                        user_id,
                        scope=scope,
                        target_ref=target_ref,
                        schema_name=self._schema,
                        replay_time=self.clock,
                    )
                return receipt.deleted_versions

            return self._run_async(work())

        count, usage = self._run("delete", remove)
        return DeletionReceipt(supported=True, deleted_count=count, usage=usage)

    # -------------------------------------------------------------- metadata

    def adapter_metadata(self) -> dict[str, object]:
        return {
            **super().adapter_metadata(),
            "engine": {
                "schema": self._schema,
                "ablation": self.options.ablation,
                "fusion": self.options.fusion,
                "gate_checks": list(self.options.gate_enabled),
                "source_unit": "memory_version",
                "deletion": {
                    "memory": "lineage_closure",
                    "topic": "lineage_closure",
                    "all": "all_versions_for_user",
                },
                "extraction": {
                    "source": "injected" if self.extractor is not None else "absent",
                    "replay_cache": "enabled" if self.replay_cache is not None else "absent",
                    "extractor_version": self.options.extractor_version,
                    "cache_key": ["turn_id", "extractor_version", "model_id"],
                },
            },
        }


def redact(database_url: str) -> str:
    """Never log a connection string; tests and manifests use this form."""
    if "@" not in database_url:
        return database_url
    return database_url.split("@", 1)[0].rsplit(":", 1)[0] + ":***@" + database_url.split("@", 1)[1]


__all__ = ("EngineAdapter", "EngineOptions", "Extractor", "redact")
