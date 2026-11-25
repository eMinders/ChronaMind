"""The host's engine service: one pooled connection per call, one fully wired engine per call (``E-046``).

``Runtime`` is deliberately one connection — a call's whole read set, its clock watermark and its row-level security
live in a transaction — so the host cannot hold "an engine" as a process-wide singleton. It holds a **pool**, and each
request takes a connection, builds the deployment's wiring on it, calls, and puts it back. That is cheap (wiring builds
a dict of bound methods) and it is the only shape that keeps the connection-scoped guarantees intact.

**Deployment keys are process-wide, not per-connection.** ``provenance_key`` signs evidence and ``deletion_key``
digests receipt targets; if they were generated per connection, a receipt issued by one request would not verify in
the next.
They are read once at start-up from ``CME_PROVENANCE_KEY`` / ``CME_DELETION_RECEIPT_KEY`` and passed to every
``Runtime``, so a restart with the same environment reproduces the same signatures.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import structlog
from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool

from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.engine.hybrid import RecallError, slot_for_dimension
from custom_memory_engine.host.credentials import HostCredentials
from custom_memory_engine.profile import Profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.paths import ScopeError, parse_scope, user_scope
from custom_memory_engine.semantic.contracts import Embedder

logger = structlog.get_logger(__name__)

PROVENANCE_KEY_ENV = "CME_PROVENANCE_KEY"
#: How many selectors one call may name. The scope layer has its own bound; this one fails earlier and cheaper.
MAX_READ_SELECTORS = 8


@dataclass(frozen=True, slots=True)
class HostSettings:
    """Everything the host needs that is not a request. Read once, at start-up, and validated there."""

    dsn: str
    role: str
    schema_name: str = "cme_engine"
    profile: Profile | None = None
    tau_confidence: float = 0.6
    tau_coverage: float = 0.5
    max_body_bytes: int = 1_048_576
    pool_min: int = 1
    pool_max: int = 8
    provenance_key: bytes | None = None
    deletion_key: bytes | None = None
    #: The dense arm's query embedder (``V2-D98``). ``None`` leaves the engine lexical-only, which is honest: it
    #: ranks by the two language-agnostic arms rather than pretending a vector it does not have.
    embedder: Embedder | None = None


class HostService:
    """The pool plus the per-call wiring. One instance per process, opened and closed by the ASGI lifespan."""

    def __init__(self, settings: HostSettings, credentials: HostCredentials) -> None:
        self.settings = settings
        self.credentials = credentials
        self._pool: AsyncConnectionPool | None = None

    @classmethod
    async def open(cls, settings: HostSettings, credentials: HostCredentials) -> HostService:
        """Build **and** start: the constructor stays side-effect free so a test can inspect settings first."""
        service = cls(settings, credentials)
        await service.start()
        return service

    async def _configure(self, connection: AsyncConnection) -> None:
        """Session settings every pooled connection needs: the schema search path, and UTC.

        Only identity-free settings: `app.user_id` and the runtime role are set per transaction by ``tenant_scope``,
        and a session-level default would hand the next request on this connection the previous caller's identity.
        """
        await connection.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.settings.schema_name))
        )
        await connection.execute("SET timezone = 'UTC'")

    async def start(self) -> None:
        """Open the pool. Idempotent, so a lifespan hook and a test can both call it."""
        if self._pool is not None:
            return
        settings = self.settings
        self._pool = AsyncConnectionPool(
            settings.dsn,
            min_size=settings.pool_min,
            max_size=settings.pool_max,
            open=False,
            # Autocommit, because every engine write opens its own explicit transaction (`tenant_scope`) and requires
            # an idle connection to start from. Without it the configure callback leaves each connection INTRANS and
            # the pool discards it.
            kwargs={"autocommit": True},
            # `configure` sets only what is session-scoped and identity-free: the schema search path and the time zone.
            # It never sets `app.user_id` or a role, because those are per-transaction (`tenant_scope`) and a session
            # default would leak the previous caller's identity into the next request on the same connection.
            configure=self._configure,
        )
        await self._pool.open(wait=True, timeout=10.0)
        logger.info("host_pool_opened", pool_min=settings.pool_min, pool_max=settings.pool_max)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("host_pool_closed")

    # -- the call ---------------------------------------------------------------------------------------------

    async def call(self, credential: Credential, operation: str, arguments: Mapping[str, object]) -> object:
        """One operation, as ``credential``, under row-level security."""
        if self._pool is None:
            raise EntryError("internal_error")
        async with self._pool.connection() as connection:
            runtime = self._runtime(connection)
            engine: Engine = wire_engine(
                runtime,
                profile=self.settings.profile,
                tau_confidence=self.settings.tau_confidence,
                tau_coverage=self.settings.tau_coverage,
                embedder=self.settings.embedder,
            )
            context = await self._context(engine, runtime, credential, arguments)
            return await engine.call(context, operation, arguments)

    def _runtime(self, connection: object) -> Runtime:
        """A ``Runtime`` on this connection with the deployment's keys, so a signature verifies across requests."""
        kwargs: dict[str, bytes] = {}
        if self.settings.provenance_key is not None:
            kwargs["provenance_key"] = self.settings.provenance_key
        if self.settings.deletion_key is not None:
            kwargs["deletion_key"] = self.settings.deletion_key
        return Runtime(connection, self.settings.role, self.settings.schema_name, **kwargs)  # type: ignore[arg-type]

    async def _context(
        self,
        engine: Engine,
        runtime: Runtime,
        credential: Credential,
        arguments: Mapping[str, object],
    ) -> CallerContext:
        """Build the caller: own namespace for writes, own namespace **plus** granted scopes for reads.

        The read set is computed from selectors the request may narrow but cannot widen: :func:`resolve_read_set`
        intersects them with the caller's own namespace and the grants the database says are active, so naming a
        stranger's scope produces a `Denial` in the read set, never access. The grants are read *here*, from the
        database, and never from the request.
        """
        own = user_scope(credential.principal).text
        selectors = self._selectors(arguments, own)
        grants = await self._active_grants(runtime, credential.principal)
        try:
            read_set = resolve_read_set(credential.principal, selectors, grants)
        except ScopeError as error:
            raise EntryError("validation_error", "read_scope_invalid") from error
        del engine  # the conditions re-check every target; this only decides what the call may look at
        return CallerContext.from_credential(credential, write_scope=own, read_set=read_set)

    @staticmethod
    def _selectors(arguments: Mapping[str, object], own: str) -> list[Selector]:
        """The request's ``read_scopes`` if it named any, else the caller's own subtree.

        A selector is a *request*, not a permission: it is intersected with the caller's namespace and active grants.
        Bounding the count here means a call with a thousand selectors is refused before the scope layer allocates.
        """
        named = arguments.get("read_scopes")
        if named is None:
            return [Selector.of(own)]
        if not isinstance(named, list) or not named or len(named) > MAX_READ_SELECTORS:
            raise EntryError("validation_error", "read_scopes_invalid")
        selectors: list[Selector] = []
        for item in named:
            if not isinstance(item, str):
                raise EntryError("validation_error", "read_scopes_invalid")
            try:
                selectors.append(Selector.of(parse_scope(item).text))
            except ScopeError:
                raise EntryError("validation_error", "read_scopes_invalid") from None
        return selectors

    async def _active_grants(self, runtime: Runtime, principal: str) -> tuple[str, ...]:
        """Scopes other principals have granted this one, straight from ``scope_grant`` under RLS."""
        async with runtime.session(principal) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql.SQL(
                        "SELECT scope_path FROM {g} WHERE principal_id = %s AND revoked_at IS NULL ORDER BY scope_path"
                    ).format(g=runtime.table("scope_grant")),
                    (principal,),
                )
                rows = await cursor.fetchall()
        return tuple(str(row[0]) for row in rows)


def embedder_from_environment(environment: Mapping[str, str]) -> Embedder | None:
    """The deployment's query embedder, or ``None`` when ``CME_EMBEDDING_ASSET`` is unset.

    Two failures are refused at start-up rather than at the first question, because both are deployment mistakes that
    would otherwise surface as a permanently lexical engine (reads still answer, so nothing looks broken) or as a
    vector stored in a slot nothing indexes:

    * the asset's declared width must be one ``hybrid.EMBEDDING_SLOTS`` can index;
    * the asset's files must exist, which :func:`assets_for` checks.

    The model is *not* loaded here: :class:`~custom_memory_engine.integrations.onnx_embedder.OnnxEmbedder` loads its
    session on first use and then keeps it, so start-up does not pay for it and every request afterwards reuses it.
    """
    from custom_memory_engine.integrations.onnx_embedder import OnnxEmbedder, assets_from_environment

    assets = assets_from_environment(dict(environment))
    if assets is None:
        return None
    try:
        slot_for_dimension(assets.dimensions)
    except RecallError:
        raise EntryError("validation_error", "embedding_dimensions_unsupported") from None
    return OnnxEmbedder(assets)


def settings_from_environment(environment: Mapping[str, str] | None = None) -> HostSettings:
    """Read the host's configuration. Every failure names a variable and never its value."""
    env = dict(os.environ if environment is None else environment)

    def required(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise EntryError("validation_error", f"{name.lower()}_required")
        return value

    def key(name: str) -> bytes | None:
        value = env.get(name, "").strip()
        if not value:
            return None
        if len(value) < 32:
            # A short HMAC key is a silent downgrade of every signature it produces.
            raise EntryError("validation_error", f"{name.lower()}_too_short")
        return value.encode("utf-8")

    return HostSettings(
        dsn=required("CME_DATABASE_URL"),
        role=required("CME_RUNTIME_ROLE"),
        schema_name=env.get("CME_SCHEMA", "cme_engine"),
        tau_confidence=float(env.get("CME_TAU_CONFIDENCE", "0.6")),
        tau_coverage=float(env.get("CME_TAU_COVERAGE", "0.5")),
        max_body_bytes=int(env.get("CME_HOST_MAX_BODY_BYTES", "1048576")),
        pool_min=int(env.get("CME_HOST_POOL_MIN", "1")),
        pool_max=int(env.get("CME_HOST_POOL_MAX", "8")),
        provenance_key=key(PROVENANCE_KEY_ENV),
        deletion_key=key("CME_DELETION_RECEIPT_KEY"),
        embedder=embedder_from_environment(env),
    )


__all__ = [
    "HostService",
    "HostSettings",
    "MAX_READ_SELECTORS",
    "embedder_from_environment",
    "settings_from_environment",
]
