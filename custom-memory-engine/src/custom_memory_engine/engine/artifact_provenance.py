"""Structural artifact provenance (`I2`, `V2-D110`) and entity-mention tracking (`I8`, `V2-D111`).

Records and reads append-only attribution in ``artifact_provenance`` for ``association``, ``entity``,
``entity_alias``, and ``entity_link``, and records idempotent ``entity_mention`` links inside the
caller's transaction. Missing or unauthorized source versions fail closed without exposing raw identifiers
(``V2-D37``); deleted source versions remain explicitly labelled as ``provenance removed by deletion``,
and legacy pre-migration artifacts remain explicitly labelled as ``unattributed (pre-migration)``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.scope.access import ReadSet, row_visible
from custom_memory_engine.scope.paths import ScopeError
from custom_memory_engine.scope.provenance import (
    OpaqueReference,
    ProvenanceKey,
    ProvenanceReference,
    VisibleReference,
    opaque_handle,
)

type ArtifactKind = Literal["association", "entity", "entity_alias", "entity_link"]
type OriginKind = Literal[
    "evidence_version",
    "tier_c_call",
    "tier_b_call",
    "tier_b_method",
    "pre_migration",
    "source_deleted",
]
type WritableOriginKind = Literal["evidence_version", "tier_c_call", "tier_b_call", "tier_b_method"]
type ResolutionStage = Literal["exact", "alias", "trigram", "vector", "created", "subject"]

UNATTRIBUTED_PRE_MIGRATION_LABEL = "unattributed (pre-migration)"
SOURCE_DELETED_LABEL = "provenance removed by deletion"
SOURCE_UNREADABLE_LABEL = "unreadable source"

_ARTIFACT_KINDS = frozenset({"association", "entity", "entity_alias", "entity_link"})
_WRITABLE_ORIGIN_KINDS = frozenset({"evidence_version", "tier_c_call", "tier_b_call", "tier_b_method"})
_RESOLUTION_STAGES = frozenset({"exact", "alias", "trigram", "vector", "created", "subject"})

_ORIGIN_LABELS: dict[str, str] = {
    "evidence_version": "evidence version",
    "tier_c_call": "tier c call",
    "tier_b_call": "tier b call",
    "tier_b_method": "tier b method",
    "pre_migration": UNATTRIBUTED_PRE_MIGRATION_LABEL,
    "source_deleted": SOURCE_DELETED_LABEL,
}


class ArtifactProvenanceError(ValueError):
    """Static error code for structural provenance writes/reads; never includes user content or raw IDs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ArtifactAttribution:
    """Honest provenance read model for one structural artifact or alias attribution edge."""

    provenance_id: str | None
    artifact_kind: ArtifactKind
    artifact_id: str
    origin_kind: OriginKind
    label: str
    source_version_id: str | None = None
    source_reference: ProvenanceReference | None = None
    method_id: str | None = None
    alias_value: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    readable: bool = True


def new_provenance_id(prefix: str = "ap") -> str:
    """Return a fresh opaque provenance identifier."""
    clean = _require_nonblank(prefix, "provenance_prefix_required")
    return f"{clean}_{uuid4().hex}"


def deterministic_provenance_id(*parts: str, prefix: str = "ap") -> str:
    """Return a deterministic SHA-256-backed identifier for idempotent structural provenance writes."""
    clean = _require_nonblank(prefix, "provenance_prefix_required")
    if not parts:
        raise ArtifactProvenanceError("provenance_parts_required")
    payload = "\x00".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"{clean}_{digest}"


def _require_nonblank(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactProvenanceError(code)
    return value


def _dedupe_nonblank(values: Sequence[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ArtifactProvenanceError("nonblank_identifier_required")
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


async def validate_readable_sources(
    connection: AsyncConnection,
    *,
    user_id: str,
    source_version_ids: Sequence[str],
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
) -> tuple[str, ...]:
    """Validate that every source version exists for ``user_id`` and is readable under ``read_set``.

    Raises ``ArtifactProvenanceError("source_version_unreadable")`` when any source version is absent, owned by
    another principal, or outside ``read_set`` so hidden scopes stay indistinguishable from missing ones (``V2-D37``).
    """
    _require_nonblank(user_id, "user_id_required")
    if not source_version_ids:
        raise ArtifactProvenanceError("source_version_required")
    ordered_ids = _dedupe_nonblank(source_version_ids)
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id, scope_path, visibility FROM {v} WHERE user_id = %s AND id = ANY(%s)").format(
                v=versions
            ),
            (user_id, list(ordered_ids)),
        )
        found = {str(row[0]): (str(row[1]), str(row[2])) for row in await cursor.fetchall()}
    if len(found) != len(ordered_ids):
        raise ArtifactProvenanceError("source_version_unreadable")
    if read_set is not None:
        for source_id in ordered_ids:
            scope_path, visibility = found[source_id]
            try:
                visible = row_visible(read_set, scope_path, visibility)
            except ScopeError:
                visible = False
            if not visible:
                raise ArtifactProvenanceError("source_version_unreadable")
    return ordered_ids


async def _validate_method_exists(connection: AsyncConnection, method_id: str, schema_name: str) -> str:
    clean_method = _require_nonblank(method_id, "method_id_required")
    methods = sql.Identifier(schema_name, "method")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(sql.SQL("SELECT 1 FROM {m} WHERE id = %s").format(m=methods), (clean_method,))
        if await cursor.fetchone() is None:
            raise ArtifactProvenanceError("method_not_found")
    return clean_method


async def _insert_provenance_row(
    connection: AsyncConnection,
    *,
    provenance_id: str | None,
    user_id: str,
    artifact_kind: ArtifactKind,
    artifact_id: str,
    alias_value: str | None,
    source_version_id: str | None,
    method_id: str | None,
    origin_kind: WritableOriginKind,
    created_by: str,
    schema_name: str,
) -> str:
    candidate_id = provenance_id or new_provenance_id()
    table = sql.Identifier(schema_name, "artifact_provenance")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        if provenance_id is not None:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} "
                    "(id, user_id, artifact_kind, artifact_id, alias_value, source_version_id, method_id, "
                    "origin_kind, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id"
                ).format(t=table),
                (
                    candidate_id,
                    user_id,
                    artifact_kind,
                    artifact_id,
                    alias_value,
                    source_version_id,
                    method_id,
                    origin_kind,
                    created_by,
                ),
            )
            inserted = await cursor.fetchone()
            return str(inserted[0]) if inserted is not None else candidate_id

        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} "
                "(id, user_id, artifact_kind, artifact_id, alias_value, source_version_id, method_id, "
                "origin_kind, created_by) "
                "SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM {t} "
                "   WHERE user_id = %s AND artifact_kind = %s AND artifact_id = %s "
                "     AND alias_value IS NOT DISTINCT FROM %s "
                "     AND origin_kind = %s "
                "     AND source_version_id IS NOT DISTINCT FROM %s "
                "     AND method_id IS NOT DISTINCT FROM %s"
                ") "
                "RETURNING id"
            ).format(t=table),
            (
                candidate_id,
                user_id,
                artifact_kind,
                artifact_id,
                alias_value,
                source_version_id,
                method_id,
                origin_kind,
                created_by,
                user_id,
                artifact_kind,
                artifact_id,
                alias_value,
                origin_kind,
                source_version_id,
                method_id,
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is not None:
            return str(inserted[0])
        await cursor.execute(
            sql.SQL(
                "SELECT id FROM {t} "
                "WHERE user_id = %s AND artifact_kind = %s AND artifact_id = %s "
                "  AND alias_value IS NOT DISTINCT FROM %s "
                "  AND origin_kind = %s "
                "  AND source_version_id IS NOT DISTINCT FROM %s "
                "  AND method_id IS NOT DISTINCT FROM %s "
                "ORDER BY created_at, id LIMIT 1"
            ).format(t=table),
            (
                user_id,
                artifact_kind,
                artifact_id,
                alias_value,
                origin_kind,
                source_version_id,
                method_id,
            ),
        )
        existing = await cursor.fetchone()
    assert existing is not None
    return str(existing[0])


async def record_artifact_provenance(
    connection: AsyncConnection,
    *,
    user_id: str,
    artifact_kind: ArtifactKind,
    artifact_id: str,
    origin_kind: WritableOriginKind,
    created_by: str,
    source_version_ids: Sequence[str] = (),
    alias_value: str | None = None,
    method_id: str | None = None,
    read_set: ReadSet | None = None,
    provenance_id: str | None = None,
    schema_name: str = "cme_engine",
) -> tuple[str, ...]:
    """Record append-only provenance rows for one association, entity, alias, or entity link."""
    _require_nonblank(user_id, "user_id_required")
    if artifact_kind not in _ARTIFACT_KINDS:
        raise ArtifactProvenanceError("invalid_artifact_kind")
    _require_nonblank(artifact_id, "artifact_id_required")
    if origin_kind not in _WRITABLE_ORIGIN_KINDS:
        raise ArtifactProvenanceError("invalid_origin_kind")
    _require_nonblank(created_by, "created_by_required")

    clean_alias: str | None = None
    if artifact_kind == "entity_alias":
        clean_alias = _require_nonblank(alias_value, "alias_value_required")
    elif alias_value is not None:
        raise ArtifactProvenanceError("alias_value_not_allowed")

    validated_method: str | None = None
    if method_id is not None:
        validated_method = await _validate_method_exists(connection, method_id, schema_name)
    elif origin_kind == "tier_b_method":
        raise ArtifactProvenanceError("method_id_required")

    if origin_kind == "evidence_version":
        validated_sources = await validate_readable_sources(
            connection,
            user_id=user_id,
            source_version_ids=source_version_ids,
            read_set=read_set,
            schema_name=schema_name,
        )
        if provenance_id is not None and len(validated_sources) > 1:
            raise ArtifactProvenanceError("single_provenance_id_for_multiple_sources")
        ids: list[str] = []
        for source_id in validated_sources:
            row_id = await _insert_provenance_row(
                connection,
                provenance_id=provenance_id,
                user_id=user_id,
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                alias_value=clean_alias,
                source_version_id=source_id,
                method_id=validated_method,
                origin_kind=origin_kind,
                created_by=created_by,
                schema_name=schema_name,
            )
            ids.append(row_id)
        return tuple(ids)

    if source_version_ids:
        raise ArtifactProvenanceError("source_versions_not_allowed_for_call_origin")

    row_id = await _insert_provenance_row(
        connection,
        provenance_id=provenance_id,
        user_id=user_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        alias_value=clean_alias,
        source_version_id=None,
        method_id=validated_method,
        origin_kind=origin_kind,
        created_by=created_by,
        schema_name=schema_name,
    )
    return (row_id,)


async def record_entity_provenance(
    connection: AsyncConnection,
    *,
    user_id: str,
    entity_id: str,
    aliases: Sequence[str] = (),
    origin_kind: WritableOriginKind = "tier_c_call",
    source_version_ids: Sequence[str] = (),
    method_id: str | None = None,
    created_by: str,
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
) -> tuple[str, ...]:
    """Record provenance for an ``entity`` and every non-blank initial alias it carries."""
    recorded: list[str] = []
    recorded.extend(
        await record_artifact_provenance(
            connection,
            user_id=user_id,
            artifact_kind="entity",
            artifact_id=entity_id,
            origin_kind=origin_kind,
            created_by=created_by,
            source_version_ids=source_version_ids,
            method_id=method_id,
            read_set=read_set,
            schema_name=schema_name,
        )
    )
    seen_aliases: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip() or alias in seen_aliases:
            continue
        seen_aliases.add(alias)
        recorded.extend(
            await record_artifact_provenance(
                connection,
                user_id=user_id,
                artifact_kind="entity_alias",
                artifact_id=entity_id,
                alias_value=alias,
                origin_kind=origin_kind,
                created_by=created_by,
                source_version_ids=source_version_ids,
                method_id=method_id,
                read_set=read_set,
                schema_name=schema_name,
            )
        )
    return tuple(recorded)


async def merge_entity_provenance(
    connection: AsyncConnection,
    *,
    user_id: str,
    link_id: str,
    entity_a: str,
    entity_b: str,
    origin_kind: WritableOriginKind = "tier_c_call",
    source_version_ids: Sequence[str] = (),
    method_id: str | None = None,
    created_by: str,
    read_set: ReadSet | None = None,
    schema_name: str = "cme_engine",
) -> tuple[str, ...]:
    """Record ``entity_link`` provenance while retaining entity and alias attribution on both sides."""
    _require_nonblank(user_id, "user_id_required")
    _require_nonblank(entity_a, "entity_a_required")
    _require_nonblank(entity_b, "entity_b_required")

    recorded = list(
        await record_artifact_provenance(
            connection,
            user_id=user_id,
            artifact_kind="entity_link",
            artifact_id=link_id,
            origin_kind=origin_kind,
            created_by=created_by,
            source_version_ids=source_version_ids,
            method_id=method_id,
            read_set=read_set,
            schema_name=schema_name,
        )
    )

    entities = sql.Identifier(schema_name, "entity")
    provenance = sql.Identifier(schema_name, "artifact_provenance")
    pair = list(dict.fromkeys((entity_a, entity_b)))
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL("SELECT id, aliases FROM {e} WHERE user_id = %s AND id = ANY(%s)").format(e=entities),
            (user_id, pair),
        )
        aliases_by_entity = {
            str(row[0]): tuple(str(a) for a in (row[1] or ()) if isinstance(a, str) and a.strip())
            for row in await cursor.fetchall()
        }
        await cursor.execute(
            sql.SQL(
                "SELECT artifact_kind, artifact_id, alias_value FROM {p} "
                "WHERE user_id = %s AND artifact_kind IN ('entity', 'entity_alias') AND artifact_id = ANY(%s)"
            ).format(p=provenance),
            (user_id, pair),
        )
        existing_rows = await cursor.fetchall()

    entities_with_provenance = {str(r[1]) for r in existing_rows if r[0] == "entity"}
    aliases_with_provenance = {(str(r[1]), str(r[2])) for r in existing_rows if r[0] == "entity_alias" and r[2]}

    for entity_id in pair:
        if entity_id in aliases_by_entity and entity_id not in entities_with_provenance:
            recorded.extend(
                await record_artifact_provenance(
                    connection,
                    user_id=user_id,
                    artifact_kind="entity",
                    artifact_id=entity_id,
                    origin_kind=origin_kind,
                    created_by=created_by,
                    source_version_ids=source_version_ids,
                    method_id=method_id,
                    read_set=read_set,
                    schema_name=schema_name,
                )
            )
        for alias in aliases_by_entity.get(entity_id, ()):
            if (entity_id, alias) in aliases_with_provenance:
                continue
            recorded.extend(
                await record_artifact_provenance(
                    connection,
                    user_id=user_id,
                    artifact_kind="entity_alias",
                    artifact_id=entity_id,
                    alias_value=alias,
                    origin_kind=origin_kind,
                    created_by=created_by,
                    source_version_ids=source_version_ids,
                    method_id=method_id,
                    read_set=read_set,
                    schema_name=schema_name,
                )
            )
    return tuple(recorded)


async def record_entity_mention(
    connection: AsyncConnection,
    *,
    user_id: str,
    version_id: str,
    entity_id: str,
    resolution_stage: ResolutionStage,
    schema_name: str = "cme_engine",
) -> bool:
    """Idempotently record that ``version_id`` mentioned ``entity_id`` during ingest."""
    _require_nonblank(user_id, "user_id_required")
    _require_nonblank(version_id, "version_id_required")
    _require_nonblank(entity_id, "entity_id_required")
    if resolution_stage not in _RESOLUTION_STAGES:
        raise ArtifactProvenanceError("invalid_resolution_stage")
    mentions = sql.Identifier(schema_name, "entity_mention")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {m} (id, user_id, version_id, entity_id, resolution_stage) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, version_id, entity_id) DO NOTHING RETURNING id"
            ).format(m=mentions),
            (new_provenance_id("em"), user_id, version_id, entity_id, resolution_stage),
        )
        inserted = await cursor.fetchone()
    return inserted is not None


def _row_to_attribution(
    *,
    provenance_id: str | None,
    artifact_kind: ArtifactKind,
    artifact_id: str,
    alias_value: str | None,
    source_version_id: str | None,
    method_id: str | None,
    origin_kind: OriginKind,
    created_by: str | None,
    created_at: datetime | None,
    scope_path: str | None,
    visibility: str | None,
    caller_user_id: str,
    read_set: ReadSet | None,
    provenance_key: ProvenanceKey | None,
) -> ArtifactAttribution:
    if origin_kind == "source_deleted":
        return ArtifactAttribution(
            provenance_id=provenance_id,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            origin_kind="source_deleted",
            label=SOURCE_DELETED_LABEL,
            source_version_id=None,
            source_reference=None,
            method_id=method_id,
            alias_value=alias_value,
            created_by=created_by,
            created_at=created_at,
            readable=True,
        )
    if origin_kind == "pre_migration":
        return ArtifactAttribution(
            provenance_id=provenance_id,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            origin_kind="pre_migration",
            label=UNATTRIBUTED_PRE_MIGRATION_LABEL,
            source_version_id=None,
            source_reference=None,
            method_id=method_id,
            alias_value=alias_value,
            created_by=created_by,
            created_at=created_at,
            readable=True,
        )
    if origin_kind == "evidence_version":
        visible = False
        if source_version_id is not None and scope_path is not None and visibility is not None:
            if read_set is None:
                visible = True
            else:
                try:
                    visible = row_visible(read_set, scope_path, visibility)
                except ScopeError:
                    visible = False
        if not visible:
            ref: ProvenanceReference | None = None
            if provenance_key is not None and source_version_id is not None:
                ref = OpaqueReference(opaque_handle(provenance_key, caller_user_id, source_version_id), origin_kind)
            return ArtifactAttribution(
                provenance_id=provenance_id,
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                origin_kind=origin_kind,
                label=SOURCE_UNREADABLE_LABEL,
                source_version_id=None,
                source_reference=ref,
                method_id=method_id,
                alias_value=alias_value,
                created_by=created_by,
                created_at=created_at,
                readable=False,
            )
        assert source_version_id is not None and scope_path is not None
        visible_ref: ProvenanceReference | None = None
        if provenance_key is not None:
            visible_ref = VisibleReference(source_version_id, scope_path, "memory_version", origin_kind)
        return ArtifactAttribution(
            provenance_id=provenance_id,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            origin_kind=origin_kind,
            label=_ORIGIN_LABELS[origin_kind],
            source_version_id=source_version_id,
            source_reference=visible_ref,
            method_id=method_id,
            alias_value=alias_value,
            created_by=created_by,
            created_at=created_at,
            readable=True,
        )

    return ArtifactAttribution(
        provenance_id=provenance_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        origin_kind=origin_kind,
        label=_ORIGIN_LABELS.get(origin_kind, UNATTRIBUTED_PRE_MIGRATION_LABEL),
        source_version_id=None,
        source_reference=None,
        method_id=method_id,
        alias_value=alias_value,
        created_by=created_by,
        created_at=created_at,
        readable=True,
    )


async def read_artifact_provenance(
    connection: AsyncConnection,
    *,
    user_id: str,
    artifact_kind: ArtifactKind,
    artifact_id: str,
    caller_user_id: str | None = None,
    read_set: ReadSet | None = None,
    provenance_key: ProvenanceKey | None = None,
    schema_name: str = "cme_engine",
) -> tuple[ArtifactAttribution, ...]:
    """Read honest structural provenance for one ``association``, ``entity``, ``entity_alias``, or ``entity_link``.

    Rows without ``artifact_provenance`` rows or with ``origin_kind = 'pre_migration'`` are returned as
    ``"unattributed (pre-migration)"``. Deleted sources return ``"provenance removed by deletion"``, and
    unreadable ``evidence_version`` sources are withheld behind :class:`OpaqueReference` when ``provenance_key`` is
    supplied (``V2-D37``).
    """
    _require_nonblank(user_id, "user_id_required")
    if artifact_kind not in _ARTIFACT_KINDS:
        raise ArtifactProvenanceError("invalid_artifact_kind")
    _require_nonblank(artifact_id, "artifact_id_required")
    effective_caller = caller_user_id or user_id

    provenance = sql.Identifier(schema_name, "artifact_provenance")
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT p.id, p.artifact_kind, p.artifact_id, p.alias_value, p.source_version_id, p.method_id, "
                "p.origin_kind, p.created_by, p.created_at, v.scope_path, v.visibility "
                "FROM {p} AS p "
                "LEFT JOIN {v} AS v ON v.id = p.source_version_id AND v.user_id = p.user_id "
                "WHERE p.user_id = %s AND p.artifact_kind = %s AND p.artifact_id = %s "
                "ORDER BY p.created_at, p.id"
            ).format(p=provenance, v=versions),
            (user_id, artifact_kind, artifact_id),
        )
        rows = await cursor.fetchall()
    if not rows:
        return (
            ArtifactAttribution(
                provenance_id=None,
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                origin_kind="pre_migration",
                label=UNATTRIBUTED_PRE_MIGRATION_LABEL,
            ),
        )
    return tuple(
        _row_to_attribution(
            provenance_id=str(row[0]),
            artifact_kind=row[1],
            artifact_id=str(row[2]),
            alias_value=str(row[3]) if row[3] is not None else None,
            source_version_id=str(row[4]) if row[4] is not None else None,
            method_id=str(row[5]) if row[5] is not None else None,
            origin_kind=row[6],
            created_by=str(row[7]) if row[7] is not None else None,
            created_at=row[8],
            scope_path=str(row[9]) if row[9] is not None else None,
            visibility=str(row[10]) if row[10] is not None else None,
            caller_user_id=effective_caller,
            read_set=read_set,
            provenance_key=provenance_key,
        )
        for row in rows
    )


async def read_entity_provenance(
    connection: AsyncConnection,
    *,
    user_id: str,
    entity_id: str,
    caller_user_id: str | None = None,
    read_set: ReadSet | None = None,
    provenance_key: ProvenanceKey | None = None,
    schema_name: str = "cme_engine",
) -> tuple[ArtifactAttribution, ...]:
    """Read both ``entity`` and ``entity_alias`` attribution rows for ``entity_id``."""
    _require_nonblank(user_id, "user_id_required")
    _require_nonblank(entity_id, "entity_id_required")
    effective_caller = caller_user_id or user_id

    provenance = sql.Identifier(schema_name, "artifact_provenance")
    versions = sql.Identifier(schema_name, "memory_version")
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute(
            sql.SQL(
                "SELECT p.id, p.artifact_kind, p.artifact_id, p.alias_value, p.source_version_id, p.method_id, "
                "p.origin_kind, p.created_by, p.created_at, v.scope_path, v.visibility "
                "FROM {p} AS p "
                "LEFT JOIN {v} AS v ON v.id = p.source_version_id AND v.user_id = p.user_id "
                "WHERE p.user_id = %s AND p.artifact_kind IN ('entity', 'entity_alias') AND p.artifact_id = %s "
                "ORDER BY CASE WHEN p.artifact_kind = 'entity' THEN 0 ELSE 1 END, p.created_at, p.id"
            ).format(p=provenance, v=versions),
            (user_id, entity_id),
        )
        rows = await cursor.fetchall()
    if not rows:
        return (
            ArtifactAttribution(
                provenance_id=None,
                artifact_kind="entity",
                artifact_id=entity_id,
                origin_kind="pre_migration",
                label=UNATTRIBUTED_PRE_MIGRATION_LABEL,
            ),
        )
    return tuple(
        _row_to_attribution(
            provenance_id=str(row[0]),
            artifact_kind=row[1],
            artifact_id=str(row[2]),
            alias_value=str(row[3]) if row[3] is not None else None,
            source_version_id=str(row[4]) if row[4] is not None else None,
            method_id=str(row[5]) if row[5] is not None else None,
            origin_kind=row[6],
            created_by=str(row[7]) if row[7] is not None else None,
            created_at=row[8],
            scope_path=str(row[9]) if row[9] is not None else None,
            visibility=str(row[10]) if row[10] is not None else None,
            caller_user_id=effective_caller,
            read_set=read_set,
            provenance_key=provenance_key,
        )
        for row in rows
    )


__all__ = [
    "SOURCE_DELETED_LABEL",
    "SOURCE_UNREADABLE_LABEL",
    "UNATTRIBUTED_PRE_MIGRATION_LABEL",
    "ArtifactAttribution",
    "ArtifactKind",
    "ArtifactProvenanceError",
    "OriginKind",
    "ResolutionStage",
    "WritableOriginKind",
    "deterministic_provenance_id",
    "merge_entity_provenance",
    "new_provenance_id",
    "read_artifact_provenance",
    "read_entity_provenance",
    "record_artifact_provenance",
    "record_entity_mention",
    "record_entity_provenance",
    "validate_readable_sources",
]
