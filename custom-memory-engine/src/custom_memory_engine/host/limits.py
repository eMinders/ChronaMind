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
from dataclasses import dataclass
from math import isfinite

from custom_memory_engine.api.entry import EntryError


class ResourceLimitError(EntryError):
    """Static transport-layer limit refusal (HTTP 429 ``resource_limit``). Never carries caller content."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__("resource_limit", detail)


class RateLimiter:
    """Per-principal token-bucket rate limiter. Disabled when ``rps == 0.0``.

    With a delegating token every end user is a principal, so the bucket map is swept: a bucket idle long enough to
    have refilled to ``burst`` is exactly a bucket that does not exist (both admit the next ``burst`` calls), so
    dropping it changes no decision. The sweep runs when the map passes a high-water mark that doubles with the live
    size, so it is amortised O(1) per call and the map stays bounded by the principals active within one refill.
    """

    #: The smallest map size that triggers a sweep.
    SWEEP_FLOOR = 1024

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
        self._sweep_at = self.SWEEP_FLOOR

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
            if len(self._buckets) >= self._sweep_at:
                self._sweep(now)
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

    def _sweep(self, now: float) -> None:
        """Drop every bucket that has refilled to ``burst``: it is indistinguishable from an absent one."""
        full = float(self.burst)
        self._buckets = {
            principal: (tokens, last)
            for principal, (tokens, last) in self._buckets.items()
            if tokens + max(0.0, now - last) * self.rps < full
        }
        self._sweep_at = max(self.SWEEP_FLOOR, 2 * len(self._buckets))

    def check(self, principal: str) -> None:
        """Raise :class:`ResourceLimitError` when ``principal`` has exhausted its token bucket."""
        if not self.allow(principal):
            raise ResourceLimitError("rate_limit_exceeded")


@dataclass(slots=True)
class _PrincipalSlots:
    """One principal's semaphore and the number of calls currently holding it or waiting on it."""

    semaphore: asyncio.Semaphore
    users: int = 0


class ConcurrencyLimiter:
    """Global plus optional per-principal in-flight concurrency guard.

    Acquiring the per-principal semaphore before the global semaphore guarantees that a single caller hitting its
    per-principal cap fails fast without exhausting the global slots reserved for other principals, and the global
    ``max_inflight`` bound keeps at least one connection free in the pool for ``/healthz`` and ``/metrics``.

    A principal's entry lives exactly as long as some call holds or waits on its semaphore, so the map is bounded by
    the calls in flight rather than by every principal ever seen. The count is taken and dropped with no ``await`` in
    between (one event loop, no preemption), so an entry is removed only when nobody can be using its semaphore: a
    new call then starts from a fresh one at full capacity, which is the state the removed one was in, and no
    principal can ever hold two semaphores at once.
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
        self._per_principal: dict[str, _PrincipalSlots] = {}

    def _enter_principal(self, principal: str) -> _PrincipalSlots | None:
        if self.per_principal_max_inflight is None:
            return None
        slots = self._per_principal.get(principal)
        if slots is None:
            slots = _PrincipalSlots(asyncio.Semaphore(self.per_principal_max_inflight))
            self._per_principal[principal] = slots
        slots.users += 1
        return slots

    def _leave_principal(self, principal: str, slots: _PrincipalSlots | None) -> None:
        if slots is None:
            return
        slots.users -= 1
        if slots.users == 0:
            del self._per_principal[principal]

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
        slots = self._enter_principal(principal)
        try:
            if slots is not None and not await self._try_acquire(slots.semaphore):
                raise ResourceLimitError("concurrency_limit_exceeded")
            try:
                if not await self._try_acquire(self._global):
                    raise ResourceLimitError("concurrency_limit_exceeded")
                try:
                    yield
                finally:
                    self._global.release()
            finally:
                if slots is not None:
                    slots.semaphore.release()
        finally:
            self._leave_principal(principal, slots)


__all__ = ["ConcurrencyLimiter", "RateLimiter", "ResourceLimitError"]
