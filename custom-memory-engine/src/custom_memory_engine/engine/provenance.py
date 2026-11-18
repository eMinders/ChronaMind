"""Read-only, caller-scoped provenance bundles and bounded lineage views."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.scope.access import ReadSet, row_visible
from custom_memory_engine.scope.provenance import (
    ProvenanceKey,
    ProvenanceReference,
    ProvenanceSource,
    VisibleReference,
    render_bundle,
    render_reference,
)

DEFAULT_PROVENANCE_DEPTH: Final = 1
MAX_PROVENANCE_DEPTH: Final = 8


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    """One source edge, rendered at its distance from the requested artifact."""

    depth: int
    reference: ProvenanceReference


@dataclass(frozen=True, slots=True)
class ProvenanceView:
    """A bounded lineage view; ``depth_limited`` says visible ancestors remain beyond the requested depth."""

    artifact_id: str
    depth: int
    edges: tuple[ProvenanceEdge, ...]
    depth_limited: bool


async def sources_for_artifacts(
    connection: AsyncConnection,
    user_id: str,
    artifact_ids: Sequence[str],
    *,
    schema_name: str,
) -> Mapping[str, tuple[ProvenanceSource, ...]]:
    """Load source metadata only; no source content is selected by this operation."""
    grouped: dict[str, list[ProvenanceSource]] = defaultdict(list)
    if not artifact_ids:
        return {}
    ids = list(dict.fromkeys(artifact_ids))
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT e.version_id, e.source_ref, e.source_type, e.evidence_role, "
                "v.scope_path, v.visibility "
                "FROM {e} AS e JOIN {v} AS v ON v.id = e.version_id AND v.user_id = e.user_id "
                "WHERE e.user_id = %s AND e.version_id = ANY(%s) "
                "ORDER BY e.version_id, e.source_type, e.source_ref, e.evidence_role"
            ).format(
                e=sql.Identifier(schema_name, "memory_evidence"), v=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, ids),
        )
        for artifact_id, source_id, source_type, role, scope_path, visibility in await cursor.fetchall():
            if source_id is None or source_type is None:
                continue
            grouped[str(artifact_id)].append(
                ProvenanceSource(
                    str(source_id),
                    str(scope_path),
                    str(visibility),
                    str(source_type),
                    str(role),
                )
            )
        await cursor.execute(
            sql.SQL(
                "SELECT d.derived_version_id, s.id, d.derivation_type, s.scope_path, s.visibility "
                "FROM {d} AS d "
                "JOIN {s} AS s ON s.id = d.source_version_id AND s.user_id = d.user_id "
                "WHERE d.user_id = %s AND d.derived_version_id = ANY(%s) "
                "ORDER BY d.derived_version_id, s.id, d.derivation_type"
            ).format(
                d=sql.Identifier(schema_name, "memory_derivation"), s=sql.Identifier(schema_name, "memory_version")
            ),
            (user_id, ids),
        )
        for artifact_id, source_id, derivation_type, scope_path, visibility in await cursor.fetchall():
            grouped[str(artifact_id)].append(
                ProvenanceSource(
                    str(source_id),
                    str(scope_path),
                    str(visibility),
                    "memory_version",
                    str(derivation_type),
                )
            )
    return {artifact_id: tuple(sources) for artifact_id, sources in grouped.items()}


async def bundles_for_artifacts(
    connection: AsyncConnection,
    user_id: str,
    artifact_ids: Sequence[str],
    *,
    read_set: ReadSet,
    key: ProvenanceKey,
    schema_name: str,
) -> Mapping[str, tuple[ProvenanceReference, ...]]:
    sources = await sources_for_artifacts(connection, user_id, artifact_ids, schema_name=schema_name)
    return {
        artifact_id: render_bundle(
            list(sources.get(artifact_id, ())), caller_user_id=user_id, read_set=read_set, key=key
        )
        for artifact_id in dict.fromkeys(artifact_ids)
    }


async def provenance_view(
    connection: AsyncConnection,
    user_id: str,
    artifact_id: str,
    *,
    depth: int,
    read_set: ReadSet,
    key: ProvenanceKey,
    schema_name: str,
) -> ProvenanceView:
    """Traverse readable lineage only; a hidden source becomes opaque and is never traversed."""
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= MAX_PROVENANCE_DEPTH:
        raise ValueError("provenance_depth_out_of_range")
    frontier = [artifact_id]
    seen = {artifact_id}
    edges: list[ProvenanceEdge] = []
    depth_limited = False
    for level in range(1, depth + 1):
        source_map = await sources_for_artifacts(connection, user_id, frontier, schema_name=schema_name)
        next_frontier: list[str] = []
        for parent_id in frontier:
            for source in source_map.get(parent_id, ()):
                reference = render_reference(source, caller_user_id=user_id, read_set=read_set, key=key)
                edges.append(ProvenanceEdge(level, reference))
                if (
                    source.source_type == "memory_version"
                    and isinstance(reference, VisibleReference)
                    and source.source_id not in seen
                ):
                    seen.add(source.source_id)
                    next_frontier.append(source.source_id)
        if level == depth:
            if next_frontier:
                boundary_sources = await sources_for_artifacts(
                    connection, user_id, next_frontier, schema_name=schema_name
                )
                depth_limited = any(
                    source.source_type == "memory_version"
                    and row_visible(read_set, source.scope_path, source.visibility)
                    for sources in boundary_sources.values()
                    for source in sources
                )
            break
        frontier = next_frontier
        if not frontier:
            break
    return ProvenanceView(artifact_id, depth, tuple(edges), depth_limited)


__all__ = [
    "DEFAULT_PROVENANCE_DEPTH",
    "MAX_PROVENANCE_DEPTH",
    "ProvenanceEdge",
    "ProvenanceView",
    "bundles_for_artifacts",
    "provenance_view",
    "sources_for_artifacts",
]
