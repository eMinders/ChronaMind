"""The host's engine service: one pooled connection per call, one fully wired engine per call (``E-046``, ``#251``).

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
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from custom_memory_engine.api.entry import CallerContext, Credential, Engine, EntryError
from custom_memory_engine.api.refine import ResultStore
from custom_memory_engine.api.runtime import Runtime
from custom_memory_engine.api.wiring import wire_engine
from custom_memory_engine.engine.cost import CostStore
from custom_memory_engine.engine.hybrid import JUDGE_RELEVANCE_FLOOR, RecallError, RelevanceJudge, slot_for_dimension
from custom_memory_engine.host.credentials import HostCredentials
from custom_memory_engine.host.limits import ConcurrencyLimiter, RateLimiter, ResourceLimitError
from custom_memory_engine.host.metrics import HostMetrics
from custom_memory_engine.profile import Profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from custom_memory_engine.scope.paths import ScopeError, parse_scope, user_scope
from custom_memory_engine.semantic.contracts import Embedder

logger = structlog.get_logger(__name__)

PROVENANCE_KEY_ENV = "CME_PROVENANCE_KEY"
#: How many selectors one call may name. The scope layer has its own bound; this one fails earlier and cheaper.
MAX_READ_SELECTORS = 8


class _InstrumentedEmbedder:
    """Thin proxy recording embedder degradations into :class:`HostMetrics` without altering engine fallback."""

    def __init__(self, inner: Embedder, metrics: HostMetrics) -> None:
        self._inner = inner
        self._metrics = metrics

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> Sequence[Sequence[float]]:
        try:
            vectors = await self._inner.embed(texts, query=query)
        except Exception:
            self._metrics.record_degradation("embedder")
            raise
        if len(vectors) != len(texts) or any(len(vector) != self._inner.dimensions for vector in vectors):
            self._metrics.record_degradation("embedder")
        elif vectors:
            try:
                slot_for_dimension(len(vectors[0]))
            except RecallError:
                self._metrics.record_degradation("embedder")
        return vectors


class _InstrumentedJudge:
    """Thin proxy recording relevance-judge degradations into :class:`HostMetrics` without altering fallback."""

    def __init__(self, inner: RelevanceJudge, metrics: HostMetrics) -> None:
        self._inner = inner
        self._metrics = metrics

    @property
    def model(self) -> str:
        return self._inner.model

    async def relevance(self, query: str, documents: Sequence[str]) -> Sequence[float]:
        try:
            scores = await self._inner.relevance(query, documents)
        except Exception:
            self._metrics.record_degradation("judge")
            raise
        if len(scores) != len(documents) or not all(isfinite(float(value)) for value in scores):
            self._metrics.record_degradation("judge")
        return scores


@dataclass(frozen=True, slots=True)
class HostSettings:
    """Everything the host needs that is not a request. Read once, at start-up, and validated there."""

    dsn: str
    role: str
    schema_name: str = "cme_engine"
    profile: Profile | None = None
    tau_confidence: float = 0.90
    tau_coverage: float = 0.60
    max_body_bytes: int = 1_048_576
    pool_min: int = 1
    pool_max: int = 8
    rate_limit_rps: float = 0.0
    rate_limit_burst: int = 20
    max_inflight: int = 6
    per_principal_max_inflight: int | None = None
    inflight_acquire_timeout: float = 0.05
    provenance_key: bytes | None = None
    deletion_key: bytes | None = None
    #: The dense arm's query embedder (``V2-D98``). ``None`` leaves the engine lexical-only, which is honest: it
    #: ranks by the two language-agnostic arms rather than pretending a vector it does not have.
    embedder: Embedder | None = None
    #: The relevance judge (``V2-D104``). ``None`` returns the fused page unfiltered, which is what pre-W3.7
    #: deployments do; with one, a question nothing answers comes back empty. Both the judge and its floor are set by
    #: the same model, so a deployment that swaps the model must re-derive the floor (``reports/retrieval``).
    judge: RelevanceJudge | None = None
    judge_floor: float = JUDGE_RELEVANCE_FLOOR

    def __post_init__(self) -> None:
        if isinstance(self.pool_min, bool) or self.pool_min < 1:
            raise EntryError("validation_error", "pool_min_invalid")
        if isinstance(self.pool_max, bool) or self.pool_max < self.pool_min:
            raise EntryError("validation_error", "pool_max_invalid")
        if isinstance(self.max_inflight, bool) or self.max_inflight < 1:
            raise EntryError("validation_error", "max_inflight_invalid")
        if self.pool_max < self.max_inflight + 1:
            # Reserve at least one pool connection so `/healthz` and `/metrics` are never starved by request traffic.
            raise EntryError("validation_error", "pool_max_must_exceed_max_inflight")
        if self.per_principal_max_inflight is None:
            # Default below `max_inflight` when >= 2 slots exist so one saturated caller cannot starve a second caller.
            object.__setattr__(self, "per_principal_max_inflight", max(1, self.max_inflight - 1))
        elif (
            isinstance(self.per_principal_max_inflight, bool)
            or self.per_principal_max_inflight < 1
            or self.per_principal_max_inflight > self.max_inflight
        ):
            raise EntryError("validation_error", "per_principal_max_inflight_invalid")
        if isinstance(self.rate_limit_rps, bool) or not isfinite(self.rate_limit_rps) or self.rate_limit_rps < 0.0:
            raise EntryError("validation_error", "rate_limit_rps_invalid")
        if isinstance(self.rate_limit_burst, bool) or self.rate_limit_burst < 1:
            raise EntryError("validation_error", "rate_limit_burst_invalid")
        if (
            isinstance(self.inflight_acquire_timeout, bool)
            or not isfinite(self.inflight_acquire_timeout)
            or self.inflight_acquire_timeout < 0.0
        ):
            raise EntryError("validation_error", "inflight_timeout_invalid")
        if isinstance(self.max_body_bytes, bool) or self.max_body_bytes < 1:
            raise EntryError("validation_error", "max_body_bytes_invalid")
        if (
            isinstance(self.tau_confidence, bool)
            or not isfinite(self.tau_confidence)
            or not (0.0 < self.tau_confidence <= 1.0)
        ):
            raise EntryError("validation_error", "tau_confidence_invalid")
        if (
            isinstance(self.tau_coverage, bool)
            or not isfinite(self.tau_coverage)
            or not (0.0 <= self.tau_coverage <= 1.0)
        ):
            raise EntryError("validation_error", "tau_coverage_invalid")


class HostService:
    """The pool plus the per-call wiring. One instance per process, opened and closed by the ASGI lifespan."""

    def __init__(self, settings: HostSettings, credentials: HostCredentials) -> None:
        self.settings = settings
        self.credentials = credentials
        self._pool: AsyncConnectionPool | None = None
        # Read state that must outlive one request (each request gets a freshly wired engine on its own pooled
        # connection): the result handles `refine` drills into and the per-caller cost records `cost` reports. Both
        # stores are caller-scoped internally, so sharing them across requests shares nothing across principals.
        self._results = ResultStore()
        self._costs = CostStore()
        self.metrics = HostMetrics()
        self.rate_limiter = RateLimiter(settings.rate_limit_rps, settings.rate_limit_burst)
        self.concurrency_limiter = ConcurrencyLimiter(
            settings.max_inflight,
            per_principal_max_inflight=settings.per_principal_max_inflight,
            acquire_timeout=settings.inflight_acquire_timeout,
        )
        self._embedder: Embedder | None = (
            _InstrumentedEmbedder(settings.embedder, self.metrics) if settings.embedder is not None else None
        )
        self._judge: RelevanceJudge | None = (
            _InstrumentedJudge(settings.judge, self.metrics) if settings.judge is not None else None
        )

    @property
    def is_started(self) -> bool:
        """Whether the database connection pool has been opened."""
        return self._pool is not None

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

    # -- probes & metrics (bypass request concurrency semaphore using reserved pool capacity) -----------------

    async def probe_health(self) -> bool:
        """Verify database connectivity using reserved pool capacity, bypassing request concurrency bounds."""
        if self._pool is None:
            return True
        try:
            async with self._pool.connection(timeout=2.0) as connection:
                await connection.execute("SELECT 1")
        except Exception as error:  # noqa: BLE001 - readiness probe reports status only
            logger.warning("host_health_probe_failed", error_type=type(error).__name__)
            return False
        return True

    async def collect_queue_metrics(self) -> dict[tuple[str, str], int]:
        """Collect aggregate ``(kind, status)`` job counts without tenant or content identifiers."""
        if self._pool is None:
            return {}
        counts: dict[tuple[str, str], int] = {}
        try:
            async with self._pool.connection(timeout=2.0) as connection:
                async with connection.cursor(row_factory=tuple_row) as cursor:
                    await cursor.execute(
                        sql.SQL("SELECT kind, status, count(*) FROM {} GROUP BY kind, status").format(
                            sql.Identifier(self.settings.schema_name, "job")
                        )
                    )
                    for kind, status, total in await cursor.fetchall():
                        counts[(str(kind), str(status))] = int(total)
        except Exception as error:  # noqa: BLE001 - metrics scrape degrades gracefully
            logger.warning("host_metrics_queue_query_failed", error_type=type(error).__name__)
            return {}
        return counts

    # -- the call ---------------------------------------------------------------------------------------------

    async def call(
        self,
        credential: Credential,
        operation: str,
        arguments: Mapping[str, object],
        *,
        delegated: bool = False,
    ) -> object:
        """One operation, as ``credential``, under rate/concurrency bounds and row-level security.

        ``delegated`` marks a credential a delegating service token built for an end user (``V2-D120``). That end user
        has no operator to provision it, so its ``app_user`` row is created here, idempotently and as the user itself
        (the ``app_user`` policy admits only the session's own id). The limits key on the end user, so one busy user
        of a shared service cannot starve the others.
        """
        started = time.perf_counter()
        if self._pool is None:
            raise EntryError("internal_error")
        try:
            self.rate_limiter.check(credential.principal)
        except ResourceLimitError:
            logger.warning(
                "host_call_refused",
                operation=operation,
                tier=credential.tier,
                status=429,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        try:
            async with self.concurrency_limiter.acquire(credential.principal):
                async with self._pool.connection() as connection:
                    runtime = self._runtime(connection)
                    engine: Engine = wire_engine(
                        runtime,
                        profile=self.settings.profile,
                        tau_confidence=self.settings.tau_confidence,
                        tau_coverage=self.settings.tau_coverage,
                        embedder=self._embedder,
                        judge=self._judge,
                        judge_floor=self.settings.judge_floor,
                        results=self._results,
                        costs=self._costs,
                    )
                    if delegated:
                        await self._provision(runtime, credential.principal)
                    context = await self._context(engine, runtime, credential, arguments)
                    forward_args = {key: value for key, value in arguments.items() if key != "read_scopes"}
                    return await engine.call(context, operation, forward_args)
        except ResourceLimitError:
            logger.warning(
                "host_call_refused",
                operation=operation,
                tier=credential.tier,
                status=429,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise

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

    async def _provision(self, runtime: Runtime, principal: str) -> None:
        """Create ``principal``'s ``app_user`` row if it is absent. A no-op on every call after the first.

        Not cached in-process: a cache would have to be invalidated by an operator's out-of-band change, and the
        statement is one indexed ``INSERT ... ON CONFLICT DO NOTHING`` on a primary key.
        """
        async with runtime.session(principal) as connection:
            await connection.execute(
                sql.SQL(
                    "INSERT INTO {u} (id, external_ref, created_at) VALUES (%s, %s, clock_now()) "
                    "ON CONFLICT DO NOTHING"
                ).format(u=runtime.table("app_user")),
                (principal, principal),
            )

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


def judge_from_environment(environment: Mapping[str, str]) -> RelevanceJudge | None:
    """The deployment's relevance judge, or ``None`` when ``CME_RERANKER_ASSET`` is unset.

    The asset is validated at start-up for the same reason the embedder is: a missing file would otherwise surface as
    every read silently returning unfiltered candidates, which looks like a working engine with a precision problem
    rather than like a misconfiguration. The model itself loads on first use.
    """
    from custom_memory_engine.integrations.cross_encoder import OnnxCrossEncoder, assets_from_environment

    assets = assets_from_environment(dict(environment))
    return None if assets is None else OnnxCrossEncoder(assets)


def judge_floor_from_environment(environment: Mapping[str, str]) -> float:
    """The judge's operating point: the measured constant unless the deployment re-derived its own (``V2-D104``)."""
    raw = environment.get("CME_RERANKER_FLOOR", "").strip()
    if not raw:
        return JUDGE_RELEVANCE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        raise EntryError("validation_error", "cme_reranker_floor_invalid") from None
    if not isfinite(value):
        raise EntryError("validation_error", "cme_reranker_floor_invalid")
    return value


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

    def parse_int(name: str, default: str) -> int:
        raw = env.get(name, default).strip() or default
        try:
            return int(raw)
        except ValueError:
            raise EntryError("validation_error", f"{name.lower()}_invalid") from None

    def parse_float(name: str, default: str) -> float:
        raw = env.get(name, default).strip() or default
        try:
            return float(raw)
        except ValueError:
            raise EntryError("validation_error", f"{name.lower()}_invalid") from None

    raw_per_principal = env.get("CME_HOST_PER_PRINCIPAL_MAX_INFLIGHT", "").strip()
    if not raw_per_principal:
        per_principal_max_inflight: int | None = None
    else:
        try:
            per_principal_max_inflight = int(raw_per_principal)
        except ValueError:
            raise EntryError("validation_error", "cme_host_per_principal_max_inflight_invalid") from None

    return HostSettings(
        dsn=required("CME_DATABASE_URL"),
        role=required("CME_RUNTIME_ROLE"),
        schema_name=env.get("CME_SCHEMA", "cme_engine").strip() or "cme_engine",
        tau_confidence=parse_float("CME_TAU_CONFIDENCE", "0.90"),
        tau_coverage=parse_float("CME_TAU_COVERAGE", "0.60"),
        max_body_bytes=parse_int("CME_HOST_MAX_BODY_BYTES", "1048576"),
        pool_min=parse_int("CME_HOST_POOL_MIN", "1"),
        pool_max=parse_int("CME_HOST_POOL_MAX", "8"),
        rate_limit_rps=parse_float("CME_HOST_RATE_LIMIT_RPS", "0.0"),
        rate_limit_burst=parse_int("CME_HOST_RATE_LIMIT_BURST", "20"),
        max_inflight=parse_int("CME_HOST_MAX_INFLIGHT", "6"),
        per_principal_max_inflight=per_principal_max_inflight,
        inflight_acquire_timeout=parse_float("CME_HOST_INFLIGHT_TIMEOUT_SECONDS", "0.05"),
        provenance_key=key(PROVENANCE_KEY_ENV),
        deletion_key=key("CME_DELETION_RECEIPT_KEY"),
        embedder=embedder_from_environment(env),
        judge=judge_from_environment(env),
        judge_floor=judge_floor_from_environment(env),
    )


__all__ = [
    "HostService",
    "HostSettings",
    "MAX_READ_SELECTORS",
    "embedder_from_environment",
    "judge_floor_from_environment",
    "judge_from_environment",
    "settings_from_environment",
]
