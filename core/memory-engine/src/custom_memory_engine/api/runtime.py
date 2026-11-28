"""The database side of the public entry point: caller-scoped lookups, receipts and idempotent writes (E-039).

Every statement a handler or a resolver runs goes through :meth:`Runtime.session`, which binds the **authenticated**
principal as ``app.user_id`` and drops to the restricted runtime role for the length of one transaction
(``security.tenant_scope``). Row-level security therefore decides what the caller can see: an id the caller may
not read is simply not returned, which is why "absent" and "unreadable" are the same answer
(``THREAT_MODEL.md`` T-11, T-18).
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from secrets import token_bytes
from typing import Literal, LiteralString, cast
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row

from custom_memory_engine.api.contract import Tier
from custom_memory_engine.api.entry import CallerContext, TargetInfo
from custom_memory_engine.deletion.markers import replayed_write
from custom_memory_engine.engine.deletion import default_receipt_key
from custom_memory_engine.model.axes import LIFECYCLES, Lifecycle
from custom_memory_engine.scope.provenance import ProvenanceKey
from custom_memory_engine.security import tenant_scope


@dataclass(frozen=True, slots=True)
class Receipt:
    """Content-free outcome of a write: what was affected, how, and when (belief time is the transaction clock)."""

    id: str
    effect: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class Rejected:
    """The admission policy declined a write: a result, not an exception (``API_V2.md`` 1.1). It is recorded."""

    reason_code: str


@dataclass(frozen=True, slots=True)
class Runtime:
    """One connection, the restricted role to run as, and the engine schema."""

    connection: AsyncConnection
    role: str
    schema_name: str = "cme_engine"
    provenance_key: ProvenanceKey = field(default_factory=lambda: ProvenanceKey(token_bytes(32)), repr=False)
    #: Keyed HMAC-SHA256 secret for ``deletion_request.target_ref`` digests (``T-29``, E-037).
    #: Defaults to the deployment key (``CME_DELETION_RECEIPT_KEY``) so replays match across processes and restarts.
    deletion_key: bytes = field(default_factory=default_receipt_key, repr=False)

    @asynccontextmanager
    async def session(self, principal: str) -> AsyncIterator[AsyncConnection]:
        """A transaction as ``principal`` under the restricted role; ends (and un-binds) on exit."""
        async with tenant_scope(self.connection, principal, role=self.role):
            yield self.connection

    def table(self, name: str) -> sql.Identifier:
        return sql.Identifier(self.schema_name, name)


def new_id(prefix: str) -> str:
    """An engine-generated opaque identifier: random, never derived from content or from the request (T-27)."""
    return f"{prefix}_{uuid4().hex}"


async def now(connection: AsyncConnection) -> datetime:
    """The transaction clock (belief time is never caller-supplied, ``I4``)."""
    async with connection.cursor(row_factory=tuple_row) as cursor:
        await cursor.execute("SELECT clock_now()")
        row = await cursor.fetchone()
    assert row is not None
    return cast(datetime, row[0])


#: The three tables that carry an artifact's own ``visibility`` (``V2-D113``). A child table has none of its own: its
#: readability delegates to its parent -- migration 32's ``*_scope_read`` policy.
VISIBILITY_TABLES: tuple[str, ...] = ("memory_version", "entity", "association")


async def change_visibility(
    connection: AsyncConnection, artifact_id: str, visibility: str, *, schema_name: str = "cme_engine"
) -> str | None:
    """Set one artifact's ``visibility`` as the caller; the table it was found in, or ``None`` if none was.

    An ordinary ``UPDATE``, deliberately: the caller must hold the write privilege **and** pass the table's own
    ``*_isolation`` policy's ``WITH CHECK``, so a grantee that can now *read* an artifact still cannot change it
    (``V2-D53``), and a row outside ``/u/<caller>/`` is simply not updated -- which is why an artifact the caller does
    not own is one answer with an artifact that does not exist. No privileged function and no ``app.user_id``-only gate
    is involved (``CLAUDE.md`` rule 4): sharing an artifact cannot reach past what row-level security already lets the
    caller write.
    """
    async with connection.cursor(row_factory=tuple_row) as cursor:
        for table in VISIBILITY_TABLES:
            await cursor.execute(
                sql.SQL("UPDATE {t} SET visibility = %s WHERE id = %s RETURNING id").format(
                    t=sql.Identifier(schema_name, table)
                ),
                (visibility, artifact_id),
            )
            if await cursor.fetchone() is not None:
                return table
    return None


async def replayed(runtime: Runtime, connection: AsyncConnection, user: str, operation: str, key: str) -> str | None:
    """The result id recorded for ``(user, operation, key)``, or ``None`` if this is the first time.

    Takes a transaction lock on ``(user, operation, key)`` first, so a concurrent call with the same key waits for the
    first one to commit and then replays its result, instead of racing it to a unique violation.

    A write a deletion erased answers with its erased result too, from the deletion's ``write`` marker (``T-46``,
    ``V2-D125``). Live, the ``api_idempotency`` row survives the deletion and answers first; after a restore into a
    backup older than the write, the row is gone and only the replayed marker stops a resend from writing it again.
    """
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s), pg_catalog.hashtext(%s))",
            (user, operation + ":" + key),
        )
    return await replayed_write(
        connection,
        user_id=user,
        operation=operation,
        idempotency_key=key,
        key=runtime.deletion_key,
        schema_name=runtime.schema_name,
    )


async def remember(
    runtime: Runtime, connection: AsyncConnection, user: str, operation: str, key: str, result_id: str
) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute(
            sql.SQL("INSERT INTO {t} (user_id, operation, idempotency_key, result_id) VALUES (%s, %s, %s, %s)").format(
                t=runtime.table("api_idempotency")
            ),
            (user, operation, key, result_id),
        )


class DbTargets:
    """Resolves artifact, association, entity and grant ids as the caller: RLS treats absent and unreadable alike."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def resolve(self, ctx: CallerContext, ids: Sequence[str]) -> Mapping[str, TargetInfo]:
        found: dict[str, TargetInfo] = {}
        schema = self.runtime.schema_name
        statements = (
            # artifacts: the creator is the recorded one, or - for a row v1 wrote - the user, acting as Tier C
            "SELECT id, scope_path, visibility, lifecycle, COALESCE(created_by, user_id), COALESCE(created_tier, 'C') "
            "FROM {schema}.memory_version WHERE id = ANY(%s)",
            "SELECT id, scope_path, visibility, NULL, created_by, CASE origin WHEN 'tier_b' THEN 'B' ELSE 'C' END "
            "FROM {schema}.association WHERE id = ANY(%s)",
            "SELECT id, scope_path, visibility, NULL, user_id, 'C' FROM {schema}.entity WHERE id = ANY(%s)",
            "SELECT id, scope_path, 'private', NULL, owner_user_id, 'C' FROM {schema}.scope_grant WHERE id = ANY(%s)",
        )
        async with (
            self.runtime.session(ctx.principal) as connection,
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            for template in statements:
                await cursor.execute(
                    sql.SQL(cast(LiteralString, template)).format(schema=sql.Identifier(schema)), (list(ids),)
                )
                for identifier, path, visibility, lifecycle, owner, tier in await cursor.fetchall():
                    found[str(identifier)] = TargetInfo(
                        scope_path=str(path),
                        visibility=str(visibility),
                        lifecycle=cast(Lifecycle, lifecycle) if lifecycle in LIFECYCLES else None,
                        lineage_owner=None if owner is None else str(owner),
                        lineage_tier=cast(Tier, tier),
                    )
        return found


class DbMethods:
    """Whether ``{id, version, params_hash}`` names a registered, append-only method (``I3``)."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def is_registered(self, ctx: CallerContext, method: Mapping[str, object]) -> bool:
        identifier, version, params_hash = method.get("id"), method.get("version"), method.get("params_hash")
        if not all(isinstance(value, str) and value for value in (identifier, version, params_hash)):
            return False
        async with (
            self.runtime.session(ctx.principal) as connection,
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(
                sql.SQL("SELECT 1 FROM {t} WHERE id = %s AND version = %s AND params_schema_hash = %s").format(
                    t=self.runtime.table("method")
                ),
                (identifier, version, params_hash),
            )
            return await cursor.fetchone() is not None


class ForeignKeyPrincipals:
    """The grantee check for ``grant_access`` (T-30) is the database's own foreign key.

    Row-level security hides other users from the caller, so the engine cannot ask "does this user exist" without a
    privileged back door. Instead the grant is inserted and an unknown principal violates the key to ``app_user``; the
    handler turns that into ``forbidden``, exactly the answer for an unauthorised scope. This registry therefore says
    "yes" and leaves the decision to the key.
    """

    async def exists(self, principal: str) -> bool:
        del principal
        return True


type Effect = Literal[
    "created", "linked", "unlinked", "granted", "revoked", "asserted", "retired", "registered", "visibility_changed"
]

__all__ = [
    "DbMethods",
    "DbTargets",
    "Effect",
    "ForeignKeyPrincipals",
    "Receipt",
    "Rejected",
    "Runtime",
    "VISIBILITY_TABLES",
    "change_visibility",
    "new_id",
    "now",
    "remember",
    "replayed",
]
