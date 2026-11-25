"""Transport-layer rate limits and concurrency bounds for the HTTP host (``#251``, ``V2-D117``).

Both guards are keyed by the authenticated principal (never by IP, header, or body field), so one saturated
caller cannot exhaust the shared database pool or starve another caller's reads. Refusals raise
:class:`ResourceLimitError` (``resource_limit``, HTTP 429) with static codes only and zero caller content.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from math import isfinite

from custom_memory_engine.api.entry import EntryError


class ResourceLimitError(EntryError):
    """Static transport-layer limit refusal (HTTP 429 ``resource_limit``). Never carries caller content."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__("resource_limit", detail)


class RateLimiter:
    """Per-principal token-bucket rate limiter. Disabled when ``rps == 0.0``."""

    def __init__(
        self,
        rps: float = 0.0,
        burst: int = 20,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(rps, bool) or not isinstance(rps, int | float) or not isfinite(rps) or rps < 0.0:
            raise EntryError("validation_error", "rate_limit_rps_invalid")
        if isinstance(burst, bool) or not isinstance(burst, int) or burst < 1:
            raise EntryError("validation_error", "rate_limit_burst_invalid")
        self.rps = float(rps)
        self.burst = burst
        self._clock = clock or time.monotonic
        self._buckets: dict[str, tuple[float, float]] = {}

    @property
    def enabled(self) -> bool:
        """Whether rate limiting is active (``rps > 0``)."""
        return self.rps > 0.0

    def allow(self, principal: str) -> bool:
        """Consume one token for ``principal`` if available; returns ``False`` when rate-limited."""
        if not self.enabled:
            return True
        if not isinstance(principal, str) or not principal:
            raise EntryError("validation_error", "principal_required")
        now = float(self._clock())
        entry = self._buckets.get(principal)
        if entry is None:
            self._buckets[principal] = (float(self.burst) - 1.0, now)
            return True
        tokens, last = entry
        elapsed = max(0.0, now - last)
        refilled = min(float(self.burst), tokens + elapsed * self.rps)
        if refilled < 1.0:
            self._buckets[principal] = (refilled, now)
            return False
        self._buckets[principal] = (refilled - 1.0, now)
        return True

    def check(self, principal: str) -> None:
        """Raise :class:`ResourceLimitError` when ``principal`` has exhausted its token bucket."""
        if not self.allow(principal):
            raise ResourceLimitError("rate_limit_exceeded")


class ConcurrencyLimiter:
    """Global plus optional per-principal in-flight concurrency guard.

    Acquiring the per-principal semaphore before the global semaphore guarantees that a single caller hitting its
    per-principal cap fails fast without exhausting the global slots reserved for other principals, and the global
    ``max_inflight`` bound keeps at least one connection free in the pool for ``/healthz`` and ``/metrics``.
    """

    def __init__(
        self,
        max_inflight: int,
        *,
        per_principal_max_inflight: int | None = None,
        acquire_timeout: float = 0.05,
    ) -> None:
        if isinstance(max_inflight, bool) or not isinstance(max_inflight, int) or max_inflight < 1:
            raise EntryError("validation_error", "max_inflight_invalid")
        if per_principal_max_inflight is not None:
            if (
                isinstance(per_principal_max_inflight, bool)
                or not isinstance(per_principal_max_inflight, int)
                or per_principal_max_inflight < 1
                or per_principal_max_inflight > max_inflight
            ):
                raise EntryError("validation_error", "per_principal_max_inflight_invalid")
        if (
            isinstance(acquire_timeout, bool)
            or not isinstance(acquire_timeout, int | float)
            or not isfinite(acquire_timeout)
            or acquire_timeout < 0.0
        ):
            raise EntryError("validation_error", "inflight_timeout_invalid")
        self.max_inflight = max_inflight
        self.per_principal_max_inflight = per_principal_max_inflight
        self.acquire_timeout = float(acquire_timeout)
        self._global = asyncio.Semaphore(max_inflight)
        self._per_principal: dict[str, asyncio.Semaphore] = {}

    def _principal_semaphore(self, principal: str) -> asyncio.Semaphore | None:
        if self.per_principal_max_inflight is None:
            return None
        semaphore = self._per_principal.get(principal)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.per_principal_max_inflight)
            self._per_principal[principal] = semaphore
        return semaphore

    async def _try_acquire(self, semaphore: asyncio.Semaphore) -> bool:
        if not semaphore.locked():
            await semaphore.acquire()
            return True
        if self.acquire_timeout == 0.0:
            return False
        try:
            async with asyncio.timeout(self.acquire_timeout):
                await semaphore.acquire()
        except TimeoutError:
            return False
        return True

    @asynccontextmanager
    async def acquire(self, principal: str) -> AsyncIterator[None]:
        """Acquire per-principal and global concurrency slots or fail fast with :class:`ResourceLimitError`."""
        if not isinstance(principal, str) or not principal:
            raise EntryError("validation_error", "principal_required")
        principal_sem = self._principal_semaphore(principal)
        if principal_sem is not None:
            if not await self._try_acquire(principal_sem):
                raise ResourceLimitError("concurrency_limit_exceeded")
        try:
            if not await self._try_acquire(self._global):
                raise ResourceLimitError("concurrency_limit_exceeded")
            try:
                yield
            finally:
                self._global.release()
        finally:
            if principal_sem is not None:
                principal_sem.release()


__all__ = ["ConcurrencyLimiter", "RateLimiter", "ResourceLimitError"]
