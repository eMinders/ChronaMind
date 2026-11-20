"""Unit coverage for deletion policy and leak-scanner contracts."""

import pytest

from custom_memory_engine.deletion.closure import (
    MAX_LINEAGE_DEPTH,
    CacheInvalidationError,
    classify_deletion_closure,
    invalidate_cache_entries,
    lineage_closure,
    should_follow_lineage,
)
from custom_memory_engine.deletion.scanner import (
    ALL_CHANNELS,
    CHANNELS,
    ChannelProbe,
    LeakChannel,
    ScanContext,
    scan_deleted_key,
)


def test_lineage_closure_includes_depth_three_but_not_depth_four() -> None:
    """Explicit ``max_depth=MAX_LINEAGE_DEPTH`` bounds a walk; the default is unbounded (``V2-D09``, E-037)."""
    edges = tuple((f"v{depth}", f"v{depth - 1}") for depth in range(1, 5))

    assert lineage_closure(("v0",), edges, max_depth=MAX_LINEAGE_DEPTH) == ("v1", "v2", "v3")
    assert lineage_closure(("v0",), edges) == ("v1", "v2", "v3", "v4")


def test_lineage_closure_is_unbounded_and_terminates_on_injected_cycles() -> None:
    chain = tuple((f"v{depth}", f"v{depth - 1}") for depth in range(1, 12))
    assert lineage_closure(("v0",), chain) == tuple(f"v{depth}" for depth in range(1, 12))
    cyclic = (("v1", "v0"), ("v2", "v1"), ("v1", "v2"), ("v0", "v2"))
    assert lineage_closure(("v0",), cyclic) == ("v1", "v2")


def test_classify_deletion_closure_orders_sources_first_and_applies_dependents_first() -> None:
    # F1 counterexample: 1 <- 0, 2 <- {0, 1}; delete 0 -> all deleted, 2 applied before 1 before 0.
    plan = classify_deletion_closure(
        ("0",),
        {"1": ("0",), "2": ("0", "1")},
        active_nodes={"0", "1", "2"},
        threshold=1,
        has_builder=lambda _n: True,
    )
    assert plan.fates == {"0": "delete", "1": "delete", "2": "delete"}
    assert plan.classification_order == ("0", "1", "2")
    assert plan.apply_order == ("2", "1", "0")
    assert plan.to_delete == ("2", "1", "0")


@pytest.mark.asyncio
async def test_eleven_channel_scan_distinguishes_measured_and_unmeasured() -> None:
    async def clean_probe(context: ScanContext) -> int:
        _ = context
        return 0

    probes: dict[LeakChannel, ChannelProbe] = {ch: clean_probe for ch in ALL_CHANNELS}
    report = await scan_deleted_key(
        ScanContext("user-1", "deleted-key", "CANARY-11", deletion_epoch=5),
        probes,
        channels=ALL_CHANNELS,
    )
    assert len(report.findings) == 11
    assert not report.leaks
    assert not report.inconclusive

    partial = await scan_deleted_key(
        ScanContext("user-1", "deleted-key", "CANARY-11", deletion_epoch=5),
        {},
        channels=ALL_CHANNELS,
    )
    assert len(partial.findings) == 11
    assert tuple(f.channel for f in partial.inconclusive) == ALL_CHANNELS
    assert all(f.measured is False and f.residual_count is None for f in partial.findings)


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
