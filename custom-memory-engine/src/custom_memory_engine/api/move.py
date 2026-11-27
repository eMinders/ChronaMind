"""Re-file a memory without re-asserting its contents or changing its evidence (V2-D129)."""

from psycopg import AsyncConnection, sql
from psycopg.errors import ExclusionViolation
from psycopg.rows import tuple_row

from custom_memory_engine.api.entry import EntryError
from custom_memory_engine.api.runtime import Runtime, new_id, remember, replayed
from custom_memory_engine.clock import allocate_clock
from custom_memory_engine.engine.artifact_provenance import record_artifact_provenance
from custom_memory_engine.engine.transactions import ArchivalPreconditionError, archive_version

# Placement, version identity and recorded time are new. The generated search columns follow content.
_BELIEF_COLUMNS = sql.SQL(
    "memory_key, user_id, status, subject_key, predicate_key, single_valued, content, event_range, "
    "layer, event_basis, observed_at, assertion_mode, confidence, importance, reinforcement_count, "
    "extractor_version, kind, lifecycle, retention, um_facet, method_id, created_by, created_tier, "
    "semantic_slot, cardinality_hint"
)


async def _copy_children(runtime: Runtime, conn: AsyncConnection, user: str, old: str, new: str, scope: str) -> None:
    """Copy the actual citations and retrieval representations in SQL, including pre-v2 evidence and vectors."""
    async with conn.cursor() as cursor:
        for table, version, fields in (
            (
                "memory_evidence",
                "version_id",
                sql.SQL("turn_id, evidence_span, evidence_role, source_type, source_ref, span, content_hash"),
            ),
            ("memory_derivation", "derived_version_id", sql.SQL("source_version_id, derivation_type")),
            (
                "memory_embedding",
                "version_id",
                sql.SQL("embedding_model, embedding, embedding_768, embedding_1024, embedding_1536, embedding_3072"),
            ),
        ):
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} ({v}, scope_path, user_id, {fields}) "
                    "SELECT %s, %s, user_id, {fields} FROM {t} WHERE {v} = %s AND user_id = %s"
                ).format(t=runtime.table(table), v=sql.Identifier(version), fields=fields),
                (new, scope, old, user),
            )
        for table, fields in (
            ("procedure_shape", sql.SQL("preconditions, steps")),
            ("outcome_stats", sql.SQL("successes, failures, last_used_at, cost_count, cost_sum, updated_at")),
        ):
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} (version_id, user_id, {fields}) SELECT %s, user_id, {fields} "
                    "FROM {t} WHERE version_id = %s AND user_id = %s"
                ).format(t=runtime.table(table), fields=fields),
                (new, old, user),
            )
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {t} (id, version_id, user_id, entity_id, resolution_stage, created_at) "
                "SELECT pg_catalog.gen_random_uuid()::text, %s, user_id, entity_id, resolution_stage, created_at "
                "FROM {t} WHERE version_id = %s AND user_id = %s"
            ).format(t=runtime.table("entity_mention")),
            (new, old, user),
        )


async def move_version(runtime: Runtime, user: str, old: str, scope: str, key: str) -> str:
    """Copy and close at one belief-clock tick under the same user lock as deletion."""
    versions = runtime.table("memory_version")
    async with runtime.session(user) as conn, conn.cursor(row_factory=tuple_row) as cursor:
        if (prior := await replayed(runtime, conn, user, "move", key)) is not None:
            return prior
        tick = await allocate_clock(conn, user, schema_name=runtime.schema_name)
        await cursor.execute(
            sql.SQL(
                "SELECT scope_path, status, upper_inf(recorded_range), kind, subject_entity_id, lifecycle "
                "FROM {v} WHERE id = %s AND user_id = %s FOR UPDATE"
            ).format(v=versions),
            (old, user),
        )
        source = await cursor.fetchone()
        if source is None:
            raise EntryError("forbidden")
        previous_scope, status, current, kind, entity, lifecycle = source
        if previous_scope == scope or status != "active" or not current:
            raise EntryError("precondition_failed", "move_requires_current_version_in_another_scope")
        if entity is not None or kind == "abstraction":
            raise EntryError("precondition_failed", "scope_bound_artifact")
        await cursor.execute(
            sql.SQL(
                "SELECT EXISTS (SELECT 1 FROM {a} WHERE user_id = %s AND type = 'contradicts' "
                "AND origin IN ('tier_c', 'grounding_policy') AND %s IN (from_id, to_id)), "
                "EXISTS (SELECT 1 FROM {v} v JOIN {m} m ON m.id = v.method_id "
                "JOIN {c} c ON c.user_id = v.user_id AND c.method_family = m.name "
                "AND c.predicate_key = v.predicate_key "
                "WHERE v.id = %s AND v.lifecycle = 'inferred' AND c.scope_path = %s AND c.lifted_at IS NULL)"
            ).format(
                a=runtime.table("association"),
                v=versions,
                m=runtime.table("method"),
                c=runtime.table("negative_constraint"),
            ),
            (user, old, old, scope),
        )
        restrictions = await cursor.fetchone()
        if restrictions and any(restrictions):
            raise EntryError("precondition_failed", "move_blocked_by_conflict_or_dissent")
        new = new_id("v")
        try:
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {v} (id, scope_path, recorded_range, {cols}) "
                    "SELECT %s, %s, tstzrange(clock_now(), NULL), {cols} FROM {v} WHERE id = %s AND user_id = %s"
                ).format(v=versions, cols=_BELIEF_COLUMNS),
                (new, scope, old, user),
            )
        except ExclusionViolation:
            raise EntryError("precondition_failed", "target_scope_has_conflicting_belief") from None
        await _copy_children(runtime, conn, user, old, new, scope)
        if lifecycle == "asserted":
            # The entry record describes this continuation, including for pre-migration beliefs with no gate row.
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} (id, candidate_version_id, existing_version_id, user_id, scope_path, "
                    "relation, decision, reason_code, decided_by, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'scope_move', 'accept', 'scope_move', %s, clock_now())"
                ).format(t=runtime.table("memory_conflict")),
                (new_id("decision"), new, old, user, scope, user),
            )
        edge = new_id("assoc")
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, scope_path, from_id, to_id, type, created_by, origin) "
                "VALUES (%s, %s, %s, %s, %s, 'supersedes', %s, 'tier_c')"
            ).format(a=runtime.table("association")),
            (edge, user, scope, new, old, user),
        )
        await record_artifact_provenance(
            conn,
            user_id=user,
            artifact_kind="association",
            artifact_id=edge,
            origin_kind="tier_c_call",
            created_by=user,
            schema_name=runtime.schema_name,
        )
        await cursor.execute(
            sql.SQL(
                "UPDATE {v} SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
                "WHERE id = %s AND user_id = %s"
            ).format(v=versions),
            (old, user),
        )
        try:
            await archive_version(conn, user_id=user, version_id=old, schema_name=runtime.schema_name, clock_tick=tick)
        except ArchivalPreconditionError:
            raise EntryError("precondition_failed", "archival_precondition_unmet") from None
        await remember(runtime, conn, user, "move", key, new)
        return new
