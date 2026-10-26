"""B2h: raw-turn cosine and PostgreSQL English FTS fused with one-based RRF60."""

from collections.abc import Sequence
from fractions import Fraction

from evaluation.baselines.b2_dense import B2DenseAdapter
from evaluation.baselines.contracts import BaselineSettings, ModelServices
from evaluation.baselines.stores import PostgresRawStore, RawChunk
from evaluation.harness.config import HarnessConfig


def reciprocal_rank_fusion(dense: Sequence[str], lexical: Sequence[str]) -> tuple[str, ...]:
    scores: dict[str, Fraction] = {}
    for channel in (dense, lexical):
        # Repeated chunks of an original turn do not consume extra ranks.
        unique = tuple(dict.fromkeys(channel))
        for rank, turn_id in enumerate(unique, start=1):
            scores[turn_id] = scores.get(turn_id, Fraction()) + Fraction(1, 60 + rank)
    return tuple(sorted(scores, key=lambda turn_id: (-scores[turn_id], turn_id)))


class B2hHybridAdapter(B2DenseAdapter):
    system_id = "B2h"

    def __init__(
        self, config: HarnessConfig, settings: BaselineSettings, services: ModelServices, store: PostgresRawStore
    ) -> None:
        if not isinstance(store, PostgresRawStore):
            raise ValueError("b2h_requires_postgresql_fts_store")
        super().__init__(config, settings, services, store)
        if self.config.system_id != "B2h":
            raise ValueError("b2h_system_identity_required")

    def _ranked_turns(self, user_id: str, query: str, chunks: tuple[RawChunk, ...]) -> tuple[str, ...]:
        dense = super()._ranked_turns(user_id, query, chunks)
        state = self._state(user_id)
        chunk_turns = {chunk.chunk_id: chunk.turn.turn_id for chunk in chunks}
        lexical_chunks = self.store.lexical(state.generation, user_id, query, max(len(chunks), 1))
        if any(chunk_id not in chunk_turns for chunk_id in lexical_chunks):
            raise ValueError("lexical_source_chunk_mismatch")
        lexical = tuple(dict.fromkeys(chunk_turns[chunk_id] for chunk_id in lexical_chunks))[
            : self.settings.lexical_candidates
        ]
        return reciprocal_rank_fusion(dense, lexical)

    def adapter_metadata(self) -> dict[str, object]:
        return {
            **super().adapter_metadata(),
            "lexical": {
                "language": "english",
                "query": "plainto_tsquery",
                "ranking": "ts_rank_cd",
                "zero_overlap": "no_lexical_rank",
                "chunk_to_turn": "first_ranked_chunk_then_deduplicate",
                "fusion": "unweighted_reciprocal_rank",
                "rrf_constant": 60,
            },
        }
