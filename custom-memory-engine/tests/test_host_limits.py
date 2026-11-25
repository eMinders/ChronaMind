"""Unit tests for host transport rate limits, concurrency isolation, and settings validation (``#251``)."""

from __future__ import annotations

import asyncio

import pytest

from custom_memory_engine.api.entry import EntryError
from custom_memory_engine.host.limits import ConcurrencyLimiter, RateLimiter, ResourceLimitError
from custom_memory_engine.host.service import HostSettings, settings_from_environment

BASE_ENV = {
    "CME_DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/db",
    "CME_RUNTIME_ROLE": "cme_rt",
}


class FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_rate_limiter_disabled_at_zero_rps() -> None:
    limiter = RateLimiter(rps=0.0, burst=1)
    assert limiter.enabled is False
    for _ in range(100):
        limiter.check("alice")


def test_rate_limiter_token_bucket_refills_and_isolates_principals() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rps=2.0, burst=3, clock=clock)
    assert limiter.enabled is True

    # Burst of 3 succeeds for alice.
    for _ in range(3):
        limiter.check("alice")

    # 4th call fails with static 429 ResourceLimitError.
    with pytest.raises(ResourceLimitError) as refused:
        limiter.check("alice")
    assert (refused.value.code, refused.value.detail) == ("resource_limit", "rate_limit_exceeded")

    # Bob's bucket is completely independent.
    limiter.check("bob")

    # Advance 0.6s -> 1.2 tokens refilled -> 1 call succeeds, next fails.
    clock.advance(0.6)
    limiter.check("alice")
    with pytest.raises(ResourceLimitError):
        limiter.check("alice")


@pytest.mark.parametrize(
    ("rps", "burst", "expected"),
    [
        (-1.0, 5, "rate_limit_rps_invalid"),
        (float("nan"), 5, "rate_limit_rps_invalid"),
        (float("inf"), 5, "rate_limit_rps_invalid"),
        (True, 5, "rate_limit_rps_invalid"),
        (1.0, 0, "rate_limit_burst_invalid"),
        (1.0, True, "rate_limit_burst_invalid"),
    ],
)
def test_rate_limiter_rejects_invalid_configuration(rps: object, burst: object, expected: str) -> None:
    with pytest.raises(EntryError) as err:
        RateLimiter(rps=rps, burst=burst)  # type: ignore[arg-type]
    assert (err.value.code, err.value.detail) == ("validation_error", expected)


async def test_concurrency_limiter_isolates_per_principal_saturation() -> None:
    limiter = ConcurrencyLimiter(max_inflight=3, per_principal_max_inflight=2, acquire_timeout=0.02)
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def hold() -> None:
        nonlocal active
        async with limiter.acquire("alice"):
            active += 1
            if active == 2:
                entered.set()
            await release.wait()

    t1 = asyncio.create_task(hold())
    t2 = asyncio.create_task(hold())
    await entered.wait()

    # Alice hits her per-principal cap (2) immediately.
    with pytest.raises(ResourceLimitError) as refused:
        async with limiter.acquire("alice"):
            pass
    assert (refused.value.code, refused.value.detail) == ("resource_limit", "concurrency_limit_exceeded")

    # Bob still gets the remaining global slot (3rd slot) unblocked.
    async with limiter.acquire("bob"):
        pass

    release.set()
    await asyncio.gather(t1, t2)

    # Slots are cleanly released after exit.
    async with limiter.acquire("alice"):
        pass


async def test_concurrency_limiter_releases_slot_on_exception() -> None:
    limiter = ConcurrencyLimiter(max_inflight=1, per_principal_max_inflight=1, acquire_timeout=0.0)
    with pytest.raises(RuntimeError, match="boom"):
        async with limiter.acquire("alice"):
            raise RuntimeError("boom")

    async with limiter.acquire("alice"):
        pass


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"max_inflight": 0}, "max_inflight_invalid"),
        ({"max_inflight": True}, "max_inflight_invalid"),
        ({"max_inflight": 2, "per_principal_max_inflight": 0}, "per_principal_max_inflight_invalid"),
        ({"max_inflight": 2, "per_principal_max_inflight": 3}, "per_principal_max_inflight_invalid"),
        ({"max_inflight": 2, "acquire_timeout": -0.1}, "inflight_timeout_invalid"),
        ({"max_inflight": 2, "acquire_timeout": float("nan")}, "inflight_timeout_invalid"),
    ],
)
def test_concurrency_limiter_rejects_invalid_configuration(kwargs: dict[str, object], expected: str) -> None:
    with pytest.raises(EntryError) as err:
        ConcurrencyLimiter(**kwargs)  # type: ignore[arg-type]
    assert (err.value.code, err.value.detail) == ("validation_error", expected)


def test_host_settings_enforces_pool_headroom_and_default_principal_isolation() -> None:
    default_settings = HostSettings(dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt")
    assert default_settings.pool_max == 8
    assert default_settings.max_inflight == 6
    # Default per-principal bound leaves at least 1 slot for a second caller when max_inflight >= 2.
    assert default_settings.per_principal_max_inflight == 5

    small = HostSettings(dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt", pool_max=3, max_inflight=2)
    assert small.per_principal_max_inflight == 1

    single = HostSettings(dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt", pool_max=2, max_inflight=1)
    assert single.per_principal_max_inflight == 1

    # Explicit <= max_inflight is accepted; > max_inflight is rejected; pool_max < max_inflight + 1 is rejected.
    explicit = HostSettings(
        dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt", pool_max=5, max_inflight=4, per_principal_max_inflight=4
    )
    assert explicit.per_principal_max_inflight == 4

    with pytest.raises(EntryError) as pool_err:
        HostSettings(dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt", pool_max=4, max_inflight=4)
    assert (pool_err.value.code, pool_err.value.detail) == ("validation_error", "pool_max_must_exceed_max_inflight")

    with pytest.raises(EntryError) as cap_err:
        HostSettings(
            dsn=BASE_ENV["CME_DATABASE_URL"], role="cme_rt", pool_max=6, max_inflight=4, per_principal_max_inflight=5
        )
    assert (cap_err.value.code, cap_err.value.detail) == ("validation_error", "per_principal_max_inflight_invalid")


def test_settings_from_environment_parses_limits_and_never_leaks_secrets() -> None:
    parsed = settings_from_environment(
        {
            **BASE_ENV,
            "CME_HOST_POOL_MIN": "2",
            "CME_HOST_POOL_MAX": "10",
            "CME_HOST_RATE_LIMIT_RPS": "12.5",
            "CME_HOST_RATE_LIMIT_BURST": "25",
            "CME_HOST_MAX_INFLIGHT": "7",
            "CME_HOST_PER_PRINCIPAL_MAX_INFLIGHT": "3",
            "CME_HOST_INFLIGHT_TIMEOUT_SECONDS": "0.1",
        }
    )
    assert parsed.pool_min == 2
    assert parsed.pool_max == 10
    assert parsed.rate_limit_rps == 12.5
    assert parsed.rate_limit_burst == 25
    assert parsed.max_inflight == 7
    assert parsed.per_principal_max_inflight == 3
    assert parsed.inflight_acquire_timeout == 0.1

    with pytest.raises(EntryError) as missing:
        settings_from_environment({"CME_DATABASE_URL": "postgresql://secret-user:secret-pass@127.0.0.1:5432/db"})
    assert (missing.value.code, missing.value.detail) == ("validation_error", "cme_runtime_role_required")
    assert "secret-pass" not in str(missing.value)
