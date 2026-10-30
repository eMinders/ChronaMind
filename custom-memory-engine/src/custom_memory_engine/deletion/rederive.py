"""Synchronous, transactional re-derivation of summaries after source deletion."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection, sql
from psycopg.errors import InvalidParameterValue
from psycopg.rows import tuple_row

from custom_memory_engine.clock import ClockTick

EMBEDDING_DIMENSION = 384


class ReDerivationError(InvalidParameterValue):
    """A summary rebuild failed and its surrounding deletion transaction must roll back."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActiveSource:
    """Content and identity of one surviving Active source passed to a summary builder."""

    version_id: str
    content: str


@dataclass(frozen=True)
class RebuiltSummary:
    """New summary text and its model-specific vector representation."""

    content: str
    embedding_model: str
    embedding: tuple[float, ...]


class SummaryBuilder(Protocol):
    """Provider boundary for rebuilding a summary from surviving source content."""

    async def __call__(self, summary_id: str, sources: Sequence[ActiveSource]) -> RebuiltSummary: ...


async def rederive_summary(
    connection: AsyncConnection,
    user_id: str,
    summary_id: str,
    source_ids: Sequence[str],
    *,
    builder: SummaryBuilder,
    schema_name: str = "cme_engine",
    clock_tick: ClockTick | None = None,
) -> bool:
    """Replace one summary in-place using only surviving Active sources.

    The existing row is deleted and recreated with the same stable ID so immutable
    memory content and its embedding are replaced together. All writes are made on
    the caller's transaction; any builder or database failure propagates and rolls
    that transaction back. Returns ``False`` without invoking the builder when no
    Active source survives.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ReDerivationError("nonblank_user_id_required")
    if not isinstance(summary_id, str) or not summary_id.strip():
        raise ReDerivationError("nonblank_summary_id_required")
    if not schema_name.startswith("cme_") or fullmatch(r"cme_[a-z][a-z0-9_]*", schema_name) is None:
        raise ReDerivationError("isolated_engine_schema_identifier_required")
    ordered_ids = tuple(dict.fromkeys(source_ids))
    if not ordered_ids:
        return False

    versions = sql.Identifier(schema_name, "memory_version")
    derivations = sql.Identifier(schema_name, "memory_derivation")
    embeddings = sql.Identifier(schema_name, "memory_embedding")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT id, content FROM {v} WHERE user_id = %s AND id = ANY(%s) AND status = 'active' ORDER BY id"
            ).format(v=versions),
            (user_id, list(ordered_ids)),
        )
        active_sources = tuple(ActiveSource(row[0], row[1]) for row in await cursor.fetchall())
        if not active_sources:
            return False
        await cursor.execute(
            sql.SQL("SELECT memory_key, single_valued, status, layer FROM {v} WHERE user_id = %s AND id = %s").format(
                v=versions
            ),
            (user_id, summary_id),
        )
        summary_row = await cursor.fetchone()
    if summary_row is None or summary_row[2] != "active" or summary_row[3] != "semantic":
        raise ReDerivationError("active_semantic_summary_required")

    try:
        rebuilt = await builder(summary_id, active_sources)
    except Exception as exc:
        raise ReDerivationError("summary_rebuild_failed") from exc
    _validate_rebuilt_summary(rebuilt)

    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "DELETE FROM {d} WHERE user_id = %s AND (derived_version_id = %s OR source_version_id = %s)"
            ).format(d=derivations),
            (user_id, summary_id, summary_id),
        )
        await cursor.execute(
            sql.SQL("DELETE FROM {v} WHERE user_id = %s AND id = %s").format(v=versions), (user_id, summary_id)
        )

    from custom_memory_engine.consolidation.consolidator import consolidate

    await consolidate(
        connection,
        user_id,
        tuple(source.version_id for source in active_sources),
        content=rebuilt.content,
        version_id=summary_id,
        memory_key=summary_row[0],
        derivation_type="summary",
        single_valued=bool(summary_row[1]),
        schema_name=schema_name,
        clock_tick=clock_tick,
    )
    vector_literal = "[" + ",".join(repr(float(value)) for value in rebuilt.embedding) + "]"
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {e} (version_id, embedding_model, user_id, embedding) VALUES (%s, %s, %s, %s::vector)"
            ).format(e=embeddings),
            (summary_id, rebuilt.embedding_model, user_id, vector_literal),
        )
    return True


def _validate_rebuilt_summary(rebuilt: RebuiltSummary) -> None:
    if not isinstance(rebuilt, RebuiltSummary):
        raise ReDerivationError("summary_builder_return_type_invalid")
    if (
        not isinstance(rebuilt.content, str)
        or not rebuilt.content.strip()
        or not isinstance(rebuilt.embedding_model, str)
        or not rebuilt.embedding_model.strip()
    ):
        raise ReDerivationError("summary_builder_output_incomplete")
    if (
        not isinstance(rebuilt.embedding, tuple)
        or len(rebuilt.embedding) != EMBEDDING_DIMENSION
        or any(
            isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value)
            for value in rebuilt.embedding
        )
    ):
        raise ReDerivationError("summary_embedding_invalid")


__all__ = [
    "ActiveSource",
    "EMBEDDING_DIMENSION",
    "RebuiltSummary",
    "ReDerivationError",
    "SummaryBuilder",
    "rederive_summary",
]
