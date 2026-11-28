"""Hybrid retrieval over canonical temporal predicates (RL-050, Proposal §7.6).

Two channels rank the same candidate set: an exact per-user cosine scan over
``memory_embedding`` and PostgreSQL English full-text ``ts_rank_cd``. Reciprocal
Rank Fusion (``k = 60``) merges them. Superseded versions never enter the
candidate set because every channel reuses ``compile_predicate``.

Ranking is deterministic: RRF score descending, then ``upper(event_range)``
descending with unbounded last, then version id ascending.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from re import fullmatch
from typing import Literal

import structlog
from psycopg import AsyncConnection, sql
from psycopg.errors import InsufficientPrivilege, InvalidParameterValue
from psycopg.rows import dict_row, tuple_row

from custom_memory_engine.engine.predicates import (
    TemporalQuery,
    TemporalVersion,
    compile_predicate,
    current_state,
    historical_as_of,
    historical_window,
    version_projection,
)

logger = structlog.get_logger(__name__)

type FusionMode = Literal["rrf", "dense", "lexical"]

RRF_CONSTANT = 60
DEFAULT_CANDIDATES = 50


class RetrievalError(InvalidParameterValue):
    """Static retrieval contract error (SQLSTATE 22023) without private input values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RankedVersion:
    """One retrieved version with its per-channel ranks and fused score."""

    version: TemporalVersion
    dense_rank: int | None
    lexical_rank: int | None
    score: float


@dataclass(frozen=True)
class RetrievalOutcome:
    """Ordered candidates plus the candidate-set size before ranking."""

    mode: str
    user_id: str
    candidates: int
    ranked: tuple[RankedVersion, ...]


def rrf_score(dense_rank: int | None, lexical_rank: int | None, *, k: int = RRF_CONSTANT) -> float:
    """``Σ_c 1/(k + rank_c)``; a channel with no rank contributes nothing."""
    score = 0.0
    if dense_rank is not None:
        score += 1.0 / (k + dense_rank)
    if lexical_rank is not None:
        score += 1.0 / (k + lexical_rank)
    return score


def fusion_order(
    dense: Sequence[str],
    lexical: Sequence[str],
    *,
    mode: FusionMode = "rrf",
    k: int = RRF_CONSTANT,
) -> tuple[tuple[str, float], ...]:
    """Fuse two one-based channel rankings; ties break on version id ascending.

    ``A5``/``A6`` ablations pass ``mode="dense"``/``"lexical"`` to drop a channel.
    Weighted-sum fusion (``A7``) is exploratory and intentionally not implemented.
    """
    if not dense and not lexical:
        return ()
    if k < 1:
        raise RetrievalError("rrf_constant_must_be_positive")
    if mode not in ("rrf", "dense", "lexical"):
        raise RetrievalError("unsupported_fusion_mode")

    dense_rank = {version_id: rank for rank, version_id in enumerate(dict.fromkeys(dense), start=1)}
    lexical_rank = {version_id: rank for rank, version_id in enumerate(dict.fromkeys(lexical), start=1)}
    if mode == "dense":
        lexical_rank = {}
    elif mode == "lexical":
        dense_rank = {}

    scored = [
        (version_id, rrf_score(dense_rank.get(version_id), lexical_rank.get(version_id), k=k))
        for version_id in set(dense_rank) | set(lexical_rank)
    ]
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0])))


def _schema(schema_name: str) -> str:
    if (
        not isinstance(schema_name, str)
        or len(schema_name) > 63
        or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None
    ):
        raise RetrievalError("isolated_engine_schema_identifier_required")
    return schema_name


async def _verify_context(connection: AsyncConnection, user_id: str) -> None:
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT NULLIF(pg_catalog.current_setting('app.user_id', true), '')")
        row = await cursor.fetchone()
        if row is None or row[0] != user_id:
            raise InsufficientPrivilege("retrieval_user_context_mismatch")


async def rank_versions(
    connection: AsyncConnection,
    query: TemporalQuery,
    *,
    query_text: str,
    query_embedding: Sequence[float] | None = None,
    embedding_model: str = "BAAI/bge-small-en-v1.5",
    limit: int = DEFAULT_CANDIDATES,
    fusion: FusionMode = "rrf",
    schema_name: str = "cme_engine",
) -> RetrievalOutcome:
    """Rank the temporal candidate set by dense, lexical, or fused score.

    The caller owns the transaction and the ``app.user_id`` context. Audit
    predicates are rejected: they are inspector-only and never build answers.
    """
    query = TemporalQuery.model_validate(query.model_dump())
    if query.mode == "audit_as_of":
        raise RetrievalError("audit_not_for_answer_context")
    if not isinstance(query_text, str) or not query_text.strip():
        raise RetrievalError("nonblank_query_text_required")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise RetrievalError("positive_candidate_limit_required")
    if fusion == "dense" and query_embedding is None:
        raise RetrievalError("dense_channel_requires_query_embedding")
    if not isinstance(embedding_model, str) or not embedding_model.strip():
        raise RetrievalError("nonblank_embedding_model_required")

    namespace = _schema(schema_name)
    await _verify_context(connection, query.user_id)
    compiled = compile_predicate(query, alias="m")

    # Ranking needs identifiers and scores only. Version payloads are projected
    # for the surviving top-k in a second statement: a full projection costs
    # ~25 us/row (measured), so projecting all candidates would dominate latency.
    async with connection.transaction():
        async with connection.cursor(row_factory=dict_row) as cursor:
            # Rank in SQL and return only the top-k: ts_rank_cd over every match and a
            # client-side sort costs one full payload transfer per candidate set.
            await cursor.execute(
                sql.SQL(
                    "SELECT m.id, {rank} AS lexical_score FROM {table} AS m WHERE {where} "
                    "AND m.content_fts @@ pg_catalog.plainto_tsquery('english', %(query_text)s) "
                    "ORDER BY lexical_score DESC, m.id LIMIT %(limit)s"
                ).format(
                    rank=sql.SQL(
                        "pg_catalog.ts_rank_cd(m.content_fts, pg_catalog.plainto_tsquery('english', %(query_text)s))"
                    ),
                    table=sql.Identifier(namespace, "memory_version"),
                    where=compiled.statement,
                ),
                {**compiled.parameters, "query_text": query_text, "limit": limit},
            )
            matches = {row["id"]: row["lexical_score"] for row in await cursor.fetchall()}

            lexical = [version_id for version_id, score in matches.items() if score > 0] if fusion != "dense" else []

            dense: list[str] = []
            candidates = len(matches)
            if fusion == "lexical":
                candidates = len(matches)
            elif query_embedding is not None:
                # The temporal predicate and the cosine ordering are one statement so
                # the candidate set never crosses the wire; only the top-k returns.
                await cursor.execute(
                    sql.SQL(
                        "SELECT m.id, e.embedding <=> %(embedding)s::vector AS distance "
                        "FROM {table} AS m JOIN {embeddings} AS e ON e.version_id = m.id "
                        "AND e.embedding_model = %(embedding_model)s "
                        "WHERE {where} ORDER BY distance, m.id LIMIT %(limit)s"
                    ).format(
                        table=sql.Identifier(namespace, "memory_version"),
                        embeddings=sql.Identifier(namespace, "memory_embedding"),
                        where=compiled.statement,
                    ),
                    {
                        **compiled.parameters,
                        "embedding": list(query_embedding),
                        "embedding_model": embedding_model,
                        "limit": limit,
                    },
                )
                dense = [row["id"] for row in await cursor.fetchall()]
                await cursor.execute(
                    sql.SQL("SELECT count(*) AS n FROM {table} AS m WHERE {where}").format(
                        table=sql.Identifier(namespace, "memory_version"), where=compiled.statement
                    ),
                    compiled.parameters,
                )
                count_row = await cursor.fetchone()
                # A separate count keeps the dense join free of a window function,
                # which forces materialisation of every candidate row.
                candidates = int(count_row["n"]) if count_row is not None else candidates

            fused = fusion_order(dense, lexical, mode=fusion)[:limit]
            selected = [version_id for version_id, _ in fused]
            payloads: dict[str, object] = {}
            if selected:
                await cursor.execute(
                    sql.SQL(
                        "SELECT {projection} AS version FROM {table} AS m "
                        "WHERE m.user_id = %(user_id)s AND m.id = ANY(%(ids)s)"
                    ).format(
                        projection=version_projection("m"),
                        table=sql.Identifier(namespace, "memory_version"),
                    ),
                    {"user_id": query.user_id, "ids": selected},
                )
                payloads = {
                    row["version"]["id"]: row["version"]
                    for row in await cursor.fetchall()  # type: ignore[index]
                }

    dense_positions = {version_id: rank for rank, version_id in enumerate(dense, start=1)}
    lexical_positions = {version_id: rank for rank, version_id in enumerate(lexical, start=1)}
    ranked = tuple(
        RankedVersion(
            version=TemporalVersion.model_validate(payloads[version_id]),
            dense_rank=dense_positions.get(version_id),
            lexical_rank=lexical_positions.get(version_id),
            score=score,
        )
        for version_id, score in fused
    )
    return RetrievalOutcome(mode=query.mode, user_id=query.user_id, candidates=candidates, ranked=ranked)


async def retrieve_current(
    connection: AsyncConnection,
    user_id: str,
    reference_time: datetime,
    **kwargs: object,
) -> RetrievalOutcome:
    """Convenience wrapper for the ``current`` query mode."""
    return await rank_versions(connection, current_state(user_id, reference_time), **kwargs)  # type: ignore[arg-type]


async def retrieve_as_of(
    connection: AsyncConnection,
    user_id: str,
    target_time: datetime,
    reference_time: datetime,
    **kwargs: object,
) -> RetrievalOutcome:
    """Convenience wrapper for the ``historical_as_of`` query mode."""
    query = historical_as_of(user_id, target_time, reference_time)
    return await rank_versions(connection, query, **kwargs)  # type: ignore[arg-type]


async def retrieve_window(
    connection: AsyncConnection,
    user_id: str,
    start: datetime,
    end: datetime,
    reference_time: datetime,
    **kwargs: object,
) -> RetrievalOutcome:
    """Convenience wrapper for the ``historical_window`` change mode."""
    query = historical_window(user_id, start, end, reference_time)
    return await rank_versions(connection, query, **kwargs)  # type: ignore[arg-type]
