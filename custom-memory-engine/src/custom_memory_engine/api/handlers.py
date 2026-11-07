"""Handlers for the W1 structure, lifecycle and scope-administration operations (E-039, wired by E-011/E-014/E-016).

Each handler is reached only through :meth:`api.entry.Engine.call`, so authorisation and every condition code have
already run. A handler still never trusts that: it operates under the caller's row-level security
(``Runtime.session``), so a row the caller may not write is simply not affected, and it answers with the entry
point's static errors (``EntryError``) and no identifier, path, scope or content.

Operations wired here: ``register_method``, ``create_entity``, ``link_entity``, ``create_scope``, ``link``, ``unlink``,
``adjudicate``, ``retract_own``, ``grant_access`` and ``revoke_access``. ``create_scope`` was held back by ``E-011``
while every scope was the personal root; migration 16 admits a row anywhere inside its owner's namespace
(``V2-D64``), so a created sub-scope now holds entities, associations and memories like any other (``E-040``).
``link_entity`` records a cross-scope entity link; both endpoints must be in the caller's read set (``E-024``).
"""

import re
from collections.abc import Mapping

from psycopg import AsyncConnection, sql
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from custom_memory_engine.api.entry import Call, Engine, EntryError, Handler
from custom_memory_engine.api.runtime import Receipt, Runtime, new_id, now, remember, replayed
from custom_memory_engine.engine.transactions import archive_version, resolve_conflict_pending
from custom_memory_engine.scope.paths import ScopeError, parse_scope, user_scope
from custom_memory_engine.security.audit_guard import content_free_details

_HASH = re.compile(r"[a-f0-9]{64}")
_CODE = re.compile(r"[a-z0-9_]{1,64}")
_ENTITY_RELATION = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SCOPE_KINDS = frozenset({"project", "session", "task", "shared"})
_ASSERTABLE_KINDS = frozenset({"claim", "procedure"})  # V2-D48: an abstraction or an episode can only be denied


def _text(call: Call, name: str, *, limit: int = 256) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EntryError("validation_error", f"{name}_required")
    return value


def _key(call: Call) -> str:
    return _text(call, "idempotency_key")


def _scope(call: Call, name: str = "scope") -> str:
    try:
        return parse_scope(call.arguments.get(name)).text
    except ScopeError:
        raise EntryError("validation_error", f"{name}_invalid") from None


async def _audit(
    runtime: Runtime, connection: AsyncConnection, user: str, event: str, details: Mapping[str, object]
) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL(
                "INSERT INTO {a} (id, user_id, memory_ref, event_type, event_at, details) "
                "VALUES (%s, %s, %s, %s, pg_catalog.transaction_timestamp(), %s)"
            ).format(a=runtime.table("audit_event")),
            (new_id("audit"), user, new_id("ref"), event, Jsonb(content_free_details(details))),
        )


class Handlers:
    """The W1 handlers, bound to one :class:`Runtime`."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    # -- METHOD ------------------------------------------------------------------------------------------------

    async def register_method(self, call: Call) -> str:
        name, version = _text(call, "name"), _text(call, "version")
        digest = _text(call, "params_schema_hash", limit=64)
        description = call.arguments.get("description", "")
        if not _HASH.fullmatch(digest) or not isinstance(description, str) or len(description) > 1024:
            raise EntryError("validation_error", "method_fields_invalid")
        _key(call)  # required by the contract; the registry is naturally idempotent on (name, version, hash)
        async with (
            self.runtime.session(call.ctx.principal) as connection,
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(
                sql.SQL(
                    "INSERT INTO {t} (id, name, version, params_schema_hash, description) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (name, version) DO NOTHING RETURNING id"
                ).format(t=self.runtime.table("method")),
                (new_id("m"), name, version, digest, description),
            )
            created = await cursor.fetchone()
            if created is not None:
                return str(created[0])
            await cursor.execute(
                sql.SQL("SELECT id, params_schema_hash FROM {t} WHERE name = %s AND version = %s").format(
                    t=self.runtime.table("method")
                ),
                (name, version),
            )
            existing = await cursor.fetchone()
        assert existing is not None
        if existing[1] != digest:  # the same (name, version) with a different hash is never silently replaced (I3)
            raise EntryError("precondition_failed", "method_version_exists_with_a_different_hash")
        return str(existing[0])

    # -- ENTITY ------------------------------------------------------------------------------------------------

    async def create_entity(self, call: Call) -> str:
        scope, kind, label, key = _scope(call), _text(call, "entity_type"), _text(call, "label"), _key(call)
        user = call.ctx.principal
        async with self.runtime.session(user) as connection:
            if (found := await replayed(self.runtime, connection, user, "create_entity", key)) is not None:
                return found
            if not await self.runtime.scope_exists(connection, user, scope):
                raise EntryError("forbidden")
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {t} (user_id, scope_path, canonical_name, entity_type) VALUES (%s, %s, %s, %s) "
                        "RETURNING id"
                    ).format(t=self.runtime.table("entity")),
                    (user, scope, label, kind),
                )
                row = await cursor.fetchone()
            assert row is not None
            await remember(self.runtime, connection, user, "create_entity", key, str(row[0]))
            return str(row[0])

    async def link_entity(self, call: Call) -> Receipt:
        entity_a = _text(call, "entity_a")
        entity_b = _text(call, "entity_b")
        relation = _text(call, "relation", limit=64)
        if not _ENTITY_RELATION.fullmatch(relation):
            raise EntryError("validation_error", "entity_relation_invalid")
        if entity_a == entity_b:
            raise EntryError("validation_error", "entity_link_requires_two_entities")
        entity_a, entity_b = sorted((entity_a, entity_b))
        user = call.ctx.principal
        try:
            async with self.runtime.session(user) as connection:
                async with connection.cursor(row_factory=tuple_row) as cursor:
                    await cursor.execute(
                        sql.SQL(
                            "INSERT INTO {t} (id, user_id, entity_a, entity_b, relation) "
                            "VALUES (%s, %s, %s, %s, %s) "
                            "ON CONFLICT (user_id, entity_a, entity_b, relation) DO NOTHING "
                            "RETURNING id, created_at"
                        ).format(t=self.runtime.table("entity_link")),
                        (new_id("elink"), user, entity_a, entity_b, relation),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        await cursor.execute(
                            sql.SQL(
                                "SELECT id, created_at FROM {t} "
                                "WHERE user_id = %s AND entity_a = %s AND entity_b = %s AND relation = %s"
                            ).format(t=self.runtime.table("entity_link")),
                            (user, entity_a, entity_b, relation),
                        )
                        row = await cursor.fetchone()
        except ForeignKeyViolation:
            raise EntryError("forbidden") from None
        if row is None:
            raise EntryError("forbidden")
        return Receipt(str(row[0]), "linked", row[1])

    # -- ASSOCIATION -------------------------------------------------------------------------------------------

    async def link(self, call: Call) -> str:
        source, target, kind = _text(call, "from"), _text(call, "to"), _text(call, "type")
        weight = call.arguments.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, int | float) or not 0 <= weight <= 1:
            raise EntryError("validation_error", "weight_out_of_range")
        origin = call.contradiction_origin or ("tier_b" if call.ctx.tier == "B" else "tier_c")
        user = call.ctx.principal
        try:
            async with self.runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {t} (id, user_id, from_id, to_id, type, weight, created_by, origin) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (user_id, from_id, to_id, type) DO NOTHING RETURNING id"
                    ).format(t=self.runtime.table("association")),
                    (new_id("assoc"), user, source, target, kind, float(weight), user, origin),
                )
                created = await cursor.fetchone()
                if created is not None:
                    return str(created[0])
                await cursor.execute(
                    sql.SQL(
                        "SELECT id FROM {t} WHERE user_id = %s AND from_id = %s AND to_id = %s AND type = %s"
                    ).format(t=self.runtime.table("association")),
                    (user, source, target, kind),
                )
                existing = await cursor.fetchone()
        except (
            ForeignKeyViolation
        ):  # an endpoint that is not the caller's own cannot be linked, and looks like a missing one
            raise EntryError("forbidden") from None
        assert existing is not None
        return str(existing[0])  # linking the same edge twice is the same edge

    async def unlink(self, call: Call) -> Receipt:
        association_id, user = _text(call, "association_id"), call.ctx.principal
        async with self.runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("DELETE FROM {t} WHERE id = %s AND user_id = %s RETURNING id").format(
                    t=self.runtime.table("association")
                ),
                (association_id, user),
            )
            deleted = await cursor.fetchone()
            if deleted is None:
                raise EntryError("forbidden")  # absent, or not the caller's to remove: the same answer
            return Receipt(association_id, "unlinked", await now(connection))

    # -- LIFECYCLE ---------------------------------------------------------------------------------------------

    async def _own_version(self, connection: AsyncConnection, user: str, artifact_id: str) -> tuple[str, str, str]:
        async with connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL("SELECT kind, lifecycle, status FROM {t} WHERE id = %s AND user_id = %s").format(
                    t=self.runtime.table("memory_version")
                ),
                (artifact_id, user),
            )
            row = await cursor.fetchone()
        if row is None:
            raise EntryError("forbidden")  # absent, or readable only through a grant: the caller cannot change it
        return str(row[0]), str(row[1]), str(row[2])

    async def adjudicate(self, call: Call) -> Receipt:
        artifact_id, decision, user = _text(call, "artifact_id"), call.arguments.get("decision"), call.ctx.principal
        if decision not in ("confirm", "deny"):
            raise EntryError("validation_error", "decision_invalid")
        async with self.runtime.session(user) as connection:
            kind, lifecycle, _ = await self._own_version(connection, user, artifact_id)
            if lifecycle != "contested":
                raise EntryError("precondition_failed", "artifact_not_contested")
            if decision == "confirm" and kind not in _ASSERTABLE_KINDS:
                raise EntryError("precondition_failed", "kind_cannot_be_asserted")  # V2-D48 C-P1
            outcome = await resolve_conflict_pending(
                connection,
                user_id=user,
                candidate_version_id=artifact_id,
                decision="accept" if decision == "confirm" else "reject",
                reason_code="adjudicated_confirm" if decision == "confirm" else "adjudicated_deny",
                decided_by=f"adjudication-{user}"[:200],
                schema_name=self.runtime.schema_name,
            )
            del outcome
            return Receipt(artifact_id, "asserted" if decision == "confirm" else "retired", await now(connection))

    async def retract_own(self, call: Call) -> Receipt:
        artifact_id, user = _text(call, "artifact_id"), call.ctx.principal
        reason = call.arguments.get("reason")
        if not isinstance(reason, str) or not _CODE.fullmatch(reason):
            raise EntryError("validation_error", "reason_must_be_a_code")  # audit rows are content-free
        async with self.runtime.session(user) as connection:
            _, lifecycle, status = await self._own_version(connection, user, artifact_id)
            if lifecycle == "retired" or status == "archived":
                raise EntryError("precondition_failed", "already_retired")
            if lifecycle not in ("asserted", "observed", "inferred"):
                raise EntryError("precondition_failed", "artifact_not_retractable")  # a contested one is adjudicated
            # V2-D59: a withdrawal IS the recorded-time close, which is branch (S) of I10; then it is archived.
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL(
                        "UPDATE {t} SET recorded_range = tstzrange(lower(recorded_range), clock_now()) "
                        "WHERE id = %s AND user_id = %s AND upper_inf(recorded_range)"
                    ).format(t=self.runtime.table("memory_version")),
                    (artifact_id, user),
                )
            await archive_version(
                connection, user_id=user, version_id=artifact_id, schema_name=self.runtime.schema_name
            )
            await _audit(self.runtime, connection, user, "lifecycle_retired", {"reason_code": reason})
            return Receipt(artifact_id, "retired", await now(connection))

    # -- SCOPE ACCESS ------------------------------------------------------------------------------------------

    async def grant_access(self, call: Call) -> str:
        scope, key, user = _scope(call), _key(call), call.ctx.principal
        grantee = call.arguments.get("principal")
        try:
            grantee = user_scope(grantee).text.split("/")[2]
        except ScopeError:
            raise EntryError("forbidden") from None  # a malformed handle is indistinguishable from an unknown one
        if grantee == user:
            raise EntryError("validation_error", "cannot_grant_to_self")
        try:
            async with self.runtime.session(user) as connection:
                if (found := await replayed(self.runtime, connection, user, "grant_access", key)) is not None:
                    return found
                grant_id = new_id("grant")
                async with connection.cursor(row_factory=tuple_row) as cursor:
                    try:
                        async with connection.transaction():
                            await cursor.execute(
                                sql.SQL(
                                    "INSERT INTO {t} (id, owner_user_id, principal_id, scope_path, granted_at) "
                                    "VALUES (%s, %s, %s, %s, clock_now())"
                                ).format(t=self.runtime.table("scope_grant")),
                                (grant_id, user, grantee, scope),
                            )
                    except UniqueViolation:  # the same grant is already active: that is the answer
                        await cursor.execute(
                            sql.SQL(
                                "SELECT id FROM {t} WHERE owner_user_id = %s AND principal_id = %s "
                                "AND scope_path = %s AND revoked_at IS NULL"
                            ).format(t=self.runtime.table("scope_grant")),
                            (user, grantee, scope),
                        )
                        row = await cursor.fetchone()
                        assert row is not None
                        grant_id = str(row[0])
                await remember(self.runtime, connection, user, "grant_access", key, grant_id)
                return grant_id
        except ForeignKeyViolation:  # T-30: an unknown principal is `forbidden`, the same as an unauthorised scope
            raise EntryError("forbidden") from None

    async def revoke_access(self, call: Call) -> Receipt:
        grant_id, user = _text(call, "grant_id"), call.ctx.principal
        async with self.runtime.session(user) as connection, connection.cursor(row_factory=tuple_row) as cursor:
            await cursor.execute(
                sql.SQL(
                    "UPDATE {t} SET revoked_at = clock_now() WHERE id = %s AND owner_user_id = %s "
                    "AND revoked_at IS NULL RETURNING id"
                ).format(t=self.runtime.table("scope_grant")),
                (grant_id, user),
            )
            if await cursor.fetchone() is not None:
                return Receipt(grant_id, "revoked", await now(connection))
            await cursor.execute(
                sql.SQL("SELECT revoked_at IS NOT NULL FROM {t} WHERE id = %s AND owner_user_id = %s").format(
                    t=self.runtime.table("scope_grant")
                ),
                (grant_id, user),
            )
            row = await cursor.fetchone()
        if row is not None and row[0]:
            raise EntryError("precondition_failed", "already_revoked")
        raise EntryError("forbidden")

    async def create_scope(self, call: Call) -> str:
        parent = _scope(call, "parent")
        segment = _text(call, "segment", limit=128)
        kind = _text(call, "kind", limit=32)
        key, user = _key(call), call.ctx.principal
        if kind not in _SCOPE_KINDS:
            raise EntryError("validation_error", "scope_kind_invalid")
        try:
            scope_path = parse_scope(parent).child(segment).text
        except ScopeError:
            raise EntryError("validation_error", "scope_invalid") from None

        async with self.runtime.session(user) as connection:
            if (found := await replayed(self.runtime, connection, user, "create_scope", key)) is not None:
                return found
            if not await self.runtime.scope_exists(connection, user, parent):
                raise EntryError("forbidden")
            parent_path = None if parent == user_scope(user).text else parent
            async with connection.cursor(row_factory=tuple_row) as cursor:
                await cursor.execute(
                    sql.SQL(
                        "INSERT INTO {t} (scope_path, user_id, parent_path, segment, kind) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (scope_path) DO NOTHING RETURNING scope_path, kind"
                    ).format(t=self.runtime.table("scope")),
                    (scope_path, user, parent_path, segment, kind),
                )
                row = await cursor.fetchone()
                if row is None:
                    await cursor.execute(
                        sql.SQL("SELECT scope_path, kind FROM {t} WHERE user_id = %s AND scope_path = %s").format(
                            t=self.runtime.table("scope")
                        ),
                        (user, scope_path),
                    )
                    row = await cursor.fetchone()
            if row is None:
                raise EntryError("forbidden")
            if row[1] != kind:
                raise EntryError("validation_error", "scope_already_exists")
            # Counts and a declared word only: a path names a user and a segment, so it is never audited (V2-D54).
            depth = scope_path.count("/") - 1  # /u/<user>/seg/ -> depth 3; /u/<user>/ -> depth 2
            await _audit(self.runtime, connection, user, "scope_created", {"scope_kind": kind, "depth": depth})
            await remember(self.runtime, connection, user, "create_scope", key, scope_path)
            return scope_path


def register_structure_handlers(engine: Engine, runtime: Runtime) -> Handlers:
    """Wire the operations above onto ``engine``; returns the bound handlers."""
    handlers = Handlers(runtime)
    table: Mapping[str, Handler] = {
        "register_method": handlers.register_method,
        "create_entity": handlers.create_entity,
        "link_entity": handlers.link_entity,
        "create_scope": handlers.create_scope,
        "link": handlers.link,
        "unlink": handlers.unlink,
        "adjudicate": handlers.adjudicate,
        "retract_own": handlers.retract_own,
        "grant_access": handlers.grant_access,
        "revoke_access": handlers.revoke_access,
    }
    for name, handler in table.items():
        engine.register(name, handler)
    return handlers


__all__ = ["Handlers", "register_structure_handlers"]
