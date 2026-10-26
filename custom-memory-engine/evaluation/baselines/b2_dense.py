"""B2: exact cosine over frozen raw-turn embedding chunks, without a time gate."""

from collections.abc import Sequence
from datetime import datetime
from math import fsum, hypot, isfinite
from time import perf_counter

from evaluation.baselines.base import BaseAdapter
from evaluation.baselines.context import fit_ranked_items, format_turn, join_items, turn_item
from evaluation.baselines.contracts import BaselineSettings, ModelServices
from evaluation.baselines.stores import MemoryRawStore, RawChunk, RawStore
from evaluation.harness.config import HarnessConfig
from evaluation.harness.models import (
    DeletionReceipt,
    DeletionScope,
    IngestReceipt,
    QueryMode,
    RetrievalDiagnostics,
    RetrievalResult,
    TurnRecord,
    Usage,
)


def normalized(vector: Sequence[float]) -> tuple[float, ...]:
    norm = hypot(*vector)
    if len(vector) != 384 or not all(isfinite(value) for value in vector) or not isfinite(norm) or norm == 0:
        raise ValueError("finite_nonzero_384_vector_required")
    return tuple(value / norm for value in vector)


def dense_turn_order(chunks: Sequence[RawChunk], query_vector: Sequence[float]) -> tuple[str, ...]:
    """The strongest chunk locates its original turn; one turn occupies one rank."""
    query = normalized(query_vector)
    scores: dict[str, float] = {}
    for chunk in chunks:
        score = fsum(a * b for a, b in zip(normalized(chunk.embedding), query, strict=True))
        turn_id = chunk.turn.turn_id
        scores[turn_id] = max(scores.get(turn_id, float("-inf")), score)
    return tuple(sorted(scores, key=lambda turn_id: (-scores[turn_id], turn_id)))


class B2DenseAdapter(BaseAdapter):
    system_id = "B2"

    def __init__(
        self,
        config: HarnessConfig,
        settings: BaselineSettings,
        services: ModelServices,
        store: RawStore | None = None,
    ) -> None:
        super().__init__(config, settings, services)
        if self.config.system_id != self.system_id:
            raise ValueError("dense_baseline_system_identity_required")
        self.store: RawStore = store if store is not None else MemoryRawStore()

    def _retire_generation(self, user_id: str, generation: str) -> None:
        self.store.delete(generation, user_id, "all", "generation-retirement")

    def ingest_turn(self, turn: TurnRecord) -> IngestReceipt:
        state = self._validate_turn(turn)

        def ingest() -> None:
            # Chunk the entire rendered original record, preserving all overflow
            # text. Only the first slice may carry metadata; every slice retains
            # the complete immutable original turn mapping in RawChunk.turn.
            chunks = self.services.chunks(format_turn(turn))
            if not chunks:
                raise ValueError("raw_turn_chunks_required")
            embedded = self.services.embed(chunks)
            if len(embedded.vectors) != len(chunks):
                raise ValueError("embedding_batch_cardinality_mismatch")
            stored = tuple(
                RawChunk(
                    chunk_id=f"{turn.turn_id}:chunk:{index:06d}",
                    turn=turn,
                    content=content,
                    embedding=normalized(vector),
                )
                for index, (content, vector) in enumerate(zip(chunks, embedded.vectors, strict=True))
            )
            self.store.put(state.generation, turn.user_id, stored)
            state.turns.append(turn)
            state.mutation += 1
            self._invalidate(turn.user_id)

        _, usage = self._run("ingest_turn", ingest)
        return IngestReceipt(turn_id=turn.turn_id, usage=usage)

    def _ranked_turns(self, user_id: str, query: str, chunks: tuple[RawChunk, ...]) -> tuple[str, ...]:
        embedded = self.services.embed((query,), query=True)
        if len(embedded.vectors) != 1:
            raise ValueError("query_embedding_cardinality_mismatch")
        return dense_turn_order(chunks, embedded.vectors[0])[: self.settings.dense_candidates]

    def retrieve(
        self, user_id: str, query: str, mode: QueryMode, as_of: datetime, token_budget: int
    ) -> RetrievalResult:
        question = self._question(user_id, query, mode, as_of, token_budget)

        def retrieve() -> RetrievalResult:
            started = perf_counter()
            state = self._state(user_id)
            chunks = self.store.list(state.generation, user_id)
            order = self._ranked_turns(user_id, query, chunks) if chunks else ()
            # Dense/lexical baselines intentionally ignore P's time-validity,
            # extraction and write-gate policies. Replay already bounds visibility.
            by_turn = {chunk.turn.turn_id: chunk.turn for chunk in chunks}
            if any(chunk.turn.user_id != user_id for chunk in chunks) or any(
                turn_id not in by_turn for turn_id in order
            ):
                raise ValueError("raw_store_tenant_or_rank_mismatch")
            ranked = tuple(turn_item(by_turn[turn_id]) for turn_id in order[: self.settings.retrieval_k])
            items, dropped_tokens = fit_ranked_items(ranked, self.services.tokenizer, token_budget)
            # A truncated metadata header contains none of the raw utterance.
            # It must not claim the source turn as recovered evidence. Ordinary
            # partial utterances retain the documented turn-level source unit.
            items = tuple(
                item
                for item in items
                if item.content == format_turn(by_turn[item.reference_id])
                or len(item.content) > format_turn(by_turn[item.reference_id]).index("\n") + 1
            )
            context = join_items(items)
            dropped_tokens = max(
                dropped_tokens,
                len(self.services.tokenizer.encode(join_items(ranked))) - len(self.services.tokenizer.encode(context)),
            )
            return RetrievalResult(
                items=items,
                rendered_context=context,
                context_tokens=len(self.services.tokenizer.encode(context)),
                # D-02 embedding is local CPU work; include it, SQL, exact cosine
                # and assembly. This path invokes no answering/extraction API.
                retrieval_latency_ms=(perf_counter() - started) * 1000,
                diagnostics=RetrievalDiagnostics(
                    context_policy="retrieved_memory",
                    source_items=len(ranked),
                    selected_items=len(items),
                    truncated_items=len(ranked) - len(items),
                    truncated_tokens=dropped_tokens,
                    candidate_count=len(chunks),
                ),
                usage=Usage.zero(),
            )

        result, usage = self._run("retrieve", retrieve)
        return self._remember(question, as_of, result.model_copy(update={"usage": usage}))

    def delete(self, user_id: str, scope: DeletionScope, target_ref: str) -> DeletionReceipt:
        state = self._state(user_id)
        if scope == "topic":
            return super().delete(user_id, scope, target_ref)

        def remove() -> int:
            count = self.store.delete(state.generation, user_id, scope, target_ref)
            state.turns = [] if scope == "all" else [turn for turn in state.turns if turn.turn_id != target_ref]
            state.mutation += 1
            self._invalidate(user_id)
            return count

        count, usage = self._run("delete", remove)
        return DeletionReceipt(supported=True, deleted_count=count, usage=usage)

    def close(self) -> None:
        self.store.close()

    def adapter_metadata(self) -> dict[str, object]:
        return {
            **super().adapter_metadata(),
            "retrieval": {
                "dense": "exact_cosine",
                "chunking": self.settings.chunk_policy,
                "chunk_embedding_input": "whole_rendered_original_record_slices",
                "chunk_to_turn": "maximum_cosine_then_original_turn_id",
                "rendering": "original_turn_once_partial_final_item_allowed",
                "temporal_filter": "not_supported",
            },
            "store": type(self.store).__name__,
        }
