"""Unit coverage for deletion policy and leak-scanner contracts."""

import pytest

from custom_memory_engine.deletion.closure import (
    CacheInvalidationError,
    invalidate_cache_entries,
    lineage_closure,
    should_follow_lineage,
)
from custom_memory_engine.deletion.scanner import CHANNELS, ScanContext, scan_deleted_key


def test_lineage_closure_includes_depth_three_but_not_depth_four() -> None:
    edges = tuple((f"v{depth}", f"v{depth - 1}") for depth in range(1, 5))

    assert lineage_closure(("v0",), edges) == ("v1", "v2", "v3")


def test_a8_is_the_only_lineage_disabling_ablation() -> None:
    assert should_follow_lineage("P") is True
    assert should_follow_lineage("A8") is False
    assert lineage_closure(("v0",), (("v1", "v0"),), follow_lineage=False) == ()


@pytest.mark.asyncio
async def test_epoch_cache_invalidation_is_idempotently_addressed() -> None:
    class Cache:
        def __init__(self) -> None:
            self.epochs: list[tuple[str, int]] = []

        async def invalidate_before_epoch(self, user_id: str, deletion_epoch: int) -> None:
            self.epochs.append((user_id, deletion_epoch))

    cache = Cache()
    await invalidate_cache_entries(cache, "user-1", 7)
    await invalidate_cache_entries(cache, "user-1", 7)

    assert cache.epochs == [("user-1", 7), ("user-1", 7)]


@pytest.mark.asyncio
async def test_cache_invalidation_failure_is_explicit() -> None:
    class BrokenCache:
        async def invalidate_before_epoch(self, user_id: str, deletion_epoch: int) -> None:
            assert user_id == "user-1"
            assert deletion_epoch == 2
            raise OSError("offline")

    with pytest.raises(CacheInvalidationError, match="cache_epoch_invalidation_failed"):
        await invalidate_cache_entries(BrokenCache(), "user-1", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("leaking_channel", CHANNELS)
async def test_scanner_reports_leftovers_in_each_channel(leaking_channel: str) -> None:
    def make_probe(channel: str):
        async def probe(_context: ScanContext) -> int:
            return int(channel == leaking_channel)

        return probe

    probes = {channel: make_probe(channel) for channel in CHANNELS}
    report = await scan_deleted_key(
        ScanContext("user-1", "deleted-key", "CANARY-X9", deletion_epoch=3),
        probes,  # type: ignore[arg-type]
    )

    assert len(report.findings) == 7
    assert [finding.channel for finding in report.leaks] == [leaking_channel]
    assert not report.inconclusive


@pytest.mark.asyncio
async def test_scanner_marks_missing_channels_inconclusive() -> None:
    report = await scan_deleted_key(ScanContext("user-1", "key", "CANARY", 1), {})

    assert report.leaks == ()
    assert tuple(finding.channel for finding in report.inconclusive) == CHANNELS


@pytest.mark.asyncio
async def test_scanner_reports_clean_when_all_seven_channels_measure_zero() -> None:
    async def clean_probe(_context: ScanContext) -> int:
        return 0

    probes = {channel: clean_probe for channel in CHANNELS}
    report = await scan_deleted_key(
        ScanContext("user-1", "deleted-key", "CANARY", deletion_epoch=4),
        probes,  # type: ignore[arg-type]
    )

    assert len(report.findings) == 7
    assert not report.leaks
    assert not report.inconclusive
