"""Shared campaign reservations account for failed and concurrent provider work."""

import ctypes
import ctypes.wintypes
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from evaluation.harness.budget import AccountedOperationError, BudgetExceeded, BudgetLedger
from evaluation.harness.config import HarnessConfig, OperationLimits
from evaluation.harness.models import Usage


def usage(*, inputs: int = 0, outputs: int = 0, embeddings: int = 0, cost: str = "0") -> Usage:
    """Specify independently calculated synthetic provider usage."""
    return Usage(
        input_tokens=inputs,
        output_tokens=outputs,
        embedding_tokens=embeddings,
        cost_usd=Decimal(cost),
    )


def config(**changes: object) -> HarnessConfig:
    """Use deliberately small caps so boundary behavior has exact expectations."""
    zero = Usage.zero()
    values = {
        "config_id": "budget-test",
        "system_id": "synthetic-budget-fixture",
        "adapter_version": "fixture-v1",
        "model_id": "synthetic-answer-1",
        "embedding_model_id": "synthetic-embed-1",
        "judge_model_id": "synthetic-judge-1",
        "temperature": 0.0,
        "seed": 42,
        "B_mem": 100,
        "prompt_sha256": "a" * 64,
        "tokenizer_id": "synthetic-tokenizer-1",
        "budget_id": "shared-campaign",
        "token_cap": 10,
        "cost_cap_usd": Decimal("10"),
        "dev_cost_cap_usd": Decimal("2"),
        "operation_limits": OperationLimits(
            ingest_turn=zero, drain_queue=zero, retrieve=zero, answer=zero, delete=zero
        ),
    }
    values.update(changes)
    return HarnessConfig.model_validate(values)


def directory_link(link: Path, target: Path) -> None:
    """Create a real link fixture; Windows junctions require no symlink privilege."""
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    link.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.HANDLE,
    ]
    create.restype = ctypes.wintypes.HANDLE
    control = kernel.DeviceIoControl
    control.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.c_void_p,
    ]
    control.restype = ctypes.wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [ctypes.wintypes.HANDLE]
    close.restype = ctypes.wintypes.BOOL
    handle = create(str(link), 0x40000000, 0, None, 3, 0x02200000, None)
    if handle == ctypes.wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    printable = str(target).encode("utf-16-le")
    names = substitute + b"\x00\x00" + printable + b"\x00\x00"
    reparse = struct.pack(
        "<LHHHHHH", 0xA0000003, 8 + len(names), 0, 0, len(substitute), len(substitute) + 2, len(printable)
    )
    buffer = ctypes.create_string_buffer(reparse + names)
    returned = ctypes.wintypes.DWORD()
    try:
        if not control(handle, 0x000900A4, buffer, len(reparse + names), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def test_reservations_and_spending_survive_new_runner_instances(tmp_path: Path) -> None:
    first = BudgetLedger(tmp_path, config())
    reservation = first.reserve(usage(inputs=4, outputs=3, cost="6.25"), "test")
    resumed = BudgetLedger(tmp_path, config())
    assert resumed.snapshot().reserved == usage(inputs=4, outputs=3, cost="6.25")
    with pytest.raises(BudgetExceeded):
        resumed.reserve(usage(outputs=4), "test")
    resumed.reconcile(reservation, usage(inputs=2, outputs=1, cost="1.125"))
    assert first.snapshot().spent == usage(inputs=2, outputs=1, cost="1.125")
    assert first.snapshot().reserved == Usage.zero()
    assert first.snapshot().completed_operations == 1
    assert not first.snapshot().breached


@pytest.mark.parametrize(
    "maximum",
    [usage(inputs=6, outputs=5), usage(cost="10.0000000000000000001")],
)
def test_combined_input_output_or_exact_currency_cap_stops_dispatch(tmp_path: Path, maximum: Usage) -> None:
    ledger = BudgetLedger(tmp_path, config())
    with pytest.raises(BudgetExceeded):
        ledger.reserve(maximum, "test")
    assert ledger.snapshot().reserved == Usage.zero()


def test_currency_beyond_default_decimal_precision_cannot_round_away_an_overrun(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    almost = usage(cost="9.9999999999999999999999999999999999999999")
    ledger.reserve(almost, "test")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(usage(cost="0.0000000000000000000000000000000000000002"), "test")
    assert ledger.snapshot().reserved.cost_usd == almost.cost_usd


def test_embedding_tokens_do_not_replace_the_input_plus_output_cap(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    reservation = ledger.reserve(usage(inputs=5, outputs=5, embeddings=1_000_000), "test")
    ledger.reconcile(reservation, usage(inputs=5, outputs=5, embeddings=1_000_000))
    assert ledger.snapshot().spent.tokens == 10
    assert ledger.snapshot().spent.embedding_tokens == 1_000_000
    with pytest.raises(BudgetExceeded):
        ledger.reserve(usage(inputs=1), "test")


def test_two_concurrent_instances_share_one_atomic_cap(tmp_path: Path) -> None:
    ledgers = [BudgetLedger(tmp_path, config()), BudgetLedger(tmp_path, config())]
    start = Barrier(2)

    def reserve(ledger: BudgetLedger) -> str | None:
        start.wait(timeout=5)
        try:
            return ledger.reserve(usage(inputs=6, cost="6"), "test")
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(reserve, ledgers))
    assert sum(result is not None for result in results) == 1
    assert ledgers[0].snapshot().reserved == usage(inputs=6, cost="6")
    assert ledgers[1].snapshot().reserved == usage(inputs=6, cost="6")


def test_unknown_failed_usage_is_charged_at_the_reserved_bound(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    maximum = usage(inputs=2, outputs=3, embeddings=8, cost="1.9")
    ledger.reconcile(ledger.reserve(maximum, "dev"), None, failed=True)
    snapshot = BudgetLedger(tmp_path, config()).snapshot()
    assert snapshot.spent == snapshot.dev_spent == maximum
    assert snapshot.reserved == snapshot.dev_reserved == Usage.zero()
    assert snapshot.unknown_operations == snapshot.failed_operations == snapshot.completed_operations == 1
    with pytest.raises(BudgetExceeded):
        ledger.reserve(usage(cost="0.2"), "dev")


def test_known_failed_and_retried_usage_is_not_discarded(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    # The adapter reports all attempts, including the failure before its own retry.
    attempts = usage(inputs=3, outputs=2, embeddings=4, cost="1.123456789")
    error = AccountedOperationError("provider_attempts_failed", attempts)
    reservation = ledger.reserve(usage(inputs=5, outputs=5, embeddings=10, cost="2"), "dev")
    ledger.reconcile(reservation, error.usage, failed=True)
    snapshot = ledger.snapshot()
    assert snapshot.spent == snapshot.dev_spent == attempts
    assert snapshot.failed_operations == snapshot.completed_operations == 1
    assert snapshot.unknown_operations == 0


def test_known_zero_actual_usage_releases_the_entire_reservation(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    reservation = ledger.reserve(usage(inputs=10, cost="10"), "test")
    ledger.reconcile(reservation, Usage.zero())
    assert ledger.snapshot().spent == ledger.snapshot().reserved == Usage.zero()
    assert ledger.snapshot().unknown_operations == 0
    ledger.reserve(usage(inputs=10, cost="10"), "test")


@pytest.mark.parametrize(
    "changes",
    [dict(token_cap=9), dict(cost_cap_usd=Decimal("9")), dict(dev_cost_cap_usd=Decimal("1"))],
)
def test_existing_campaign_cannot_silently_change_or_replenish_limits(tmp_path: Path, changes: dict) -> None:
    ledger = BudgetLedger(tmp_path, config())
    ledger.reconcile(ledger.reserve(usage(inputs=8, cost="8"), "test"), usage(inputs=8, cost="8"))
    with pytest.raises(ValueError, match="budget_limits_changed"):
        BudgetLedger(tmp_path, config(**changes))
    assert BudgetLedger(tmp_path, config()).snapshot().spent == usage(inputs=8, cost="8")
    with pytest.raises(BudgetExceeded):
        BudgetLedger(tmp_path, config()).reserve(usage(inputs=3), "test")


def test_dev_and_test_share_total_caps_but_only_dev_uses_suballocation(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    test = ledger.reserve(usage(cost="6"), "test")
    dev = ledger.reserve(usage(cost="2"), "dev")
    assert ledger.snapshot().dev_reserved == usage(cost="2")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(usage(cost="0.01"), "dev")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(usage(cost="2.01"), "test")
    ledger.reconcile(test, usage(cost="5"))
    ledger.reconcile(dev, usage(cost="1"))
    assert ledger.snapshot().spent == usage(cost="6")
    assert ledger.snapshot().dev_spent == usage(cost="1")
    ledger.reserve(usage(cost="1"), "dev")


@pytest.mark.parametrize(
    "actual",
    [usage(inputs=2), usage(outputs=2), usage(embeddings=2), usage(cost="1.01")],
)
def test_overreported_actual_usage_is_persisted_and_breaches_the_campaign(tmp_path: Path, actual: Usage) -> None:
    ledger = BudgetLedger(tmp_path, config())
    reservation = ledger.reserve(usage(inputs=1, outputs=1, embeddings=1, cost="1"), "dev")
    ledger.reconcile(reservation, actual)
    resumed = BudgetLedger(tmp_path, config())
    assert resumed.snapshot().spent == resumed.snapshot().dev_spent == actual
    assert resumed.snapshot().reserved == Usage.zero()
    assert resumed.snapshot().breached
    with pytest.raises(BudgetExceeded):
        resumed.reserve(Usage.zero(), "test")


def test_external_overspend_is_not_hidden_and_existing_reservations_can_still_reconcile(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    first = ledger.reserve(usage(inputs=4, cost="4"), "test")
    second = ledger.reserve(usage(inputs=4, cost="4"), "test")
    ledger.reconcile(first, usage(inputs=12, cost="12"), failed=True)
    ledger.reconcile(second, usage(inputs=1, cost="1"))
    snapshot = ledger.snapshot()
    assert snapshot.spent == usage(inputs=13, cost="13")
    assert snapshot.breached and snapshot.completed_operations == 2
    assert snapshot.failed_operations == 1 and snapshot.reserved == Usage.zero()


def test_reconciliation_is_exactly_once(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    reservation = ledger.reserve(usage(inputs=1, cost="1"), "test")
    ledger.reconcile(reservation, usage(inputs=1, cost="1"))
    with pytest.raises(ValueError, match="unknown_or_completed_reservation"):
        ledger.reconcile(reservation, usage(inputs=1, cost="1"))
    assert ledger.snapshot().spent == usage(inputs=1, cost="1")
    assert ledger.snapshot().completed_operations == 1


@pytest.mark.parametrize("split", ["development", "", None])
def test_invalid_split_never_creates_a_reservation(tmp_path: Path, split: str) -> None:
    ledger = BudgetLedger(tmp_path, config())
    with pytest.raises(ValueError, match="invalid_budget_split"):
        ledger.reserve(Usage.zero(), split)  # type: ignore[arg-type]
    assert ledger.snapshot().reserved == Usage.zero()


@pytest.mark.parametrize("relative", [".budgets", ".budgets/shared-campaign"])
def test_file_cannot_replace_a_budget_directory(tmp_path: Path, relative: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unrelated", encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe_budget_path"):
        BudgetLedger(tmp_path, config())
    assert target.read_text(encoding="utf-8") == "unrelated"


@pytest.mark.parametrize("content", ["not json", "{}", '{"version":1,"version":1}'])
def test_invalid_existing_state_is_never_reset(tmp_path: Path, content: str) -> None:
    state = tmp_path / ".budgets/shared-campaign/ledger.json"
    state.parent.mkdir(parents=True)
    state.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_budget_state"):
        BudgetLedger(tmp_path, config())
    assert state.read_text(encoding="utf-8") == content


def test_existing_campaign_with_missing_state_is_not_replenished(tmp_path: Path) -> None:
    campaign = tmp_path / ".budgets/shared-campaign"
    campaign.mkdir(parents=True)
    with pytest.raises(ValueError, match="invalid_budget_state"):
        BudgetLedger(tmp_path, config())
    assert not (campaign / "ledger.json").exists()


def test_existing_lock_is_bounded_and_never_automatically_removed(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path, config())
    lock = tmp_path / ".budgets/shared-campaign.lock"
    lock.mkdir()
    marker = lock / "keep"
    marker.write_text("another owner", encoding="utf-8")
    with pytest.raises(ValueError, match="budget_lock_timeout"):
        ledger.snapshot()
    assert marker.read_text(encoding="utf-8") == "another owner"


@pytest.mark.parametrize(
    "relative",
    [".budgets", ".budgets/shared-campaign", ".budgets/shared-campaign.lock", ".budgets/shared-campaign/ledger.json"],
)
def test_linked_budget_paths_are_rejected_without_touching_the_target(tmp_path: Path, relative: str) -> None:
    link = tmp_path / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "unrelated-directory"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("private-unrelated-data", encoding="utf-8")
    directory_link(link, target)
    try:
        with pytest.raises(ValueError, match="unsafe_budget_path"):
            BudgetLedger(tmp_path, config())
        assert marker.read_text(encoding="utf-8") == "private-unrelated-data"
    finally:
        # Remove only this fixture's link; the unrelated target remains intact.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
