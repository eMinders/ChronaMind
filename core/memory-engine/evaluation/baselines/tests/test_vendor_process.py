"""Parent lifecycle guarantees independent of optional vendor SDK availability."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from evaluation.baselines.tests.helpers import FakeServices, settings
from evaluation.baselines.vendor import VendorProcess


def test_unexpected_clean_worker_exit_still_recovers_retained_graph_scope(monkeypatch):
    selected = settings()
    worker = VendorProcess(
        "b4_graphiti", Path(sys.executable), selected, FakeServices(selected), {"host": "127.0.0.1", "port": 16389}
    )
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    assert process.wait(timeout=15) == 0
    worker.process = process
    tenant = "cme_" + "a" * 64
    worker._graph_tenants.add(tenant)
    recovered = []
    monkeypatch.setattr(worker, "_recover_graphs", lambda: recovered.append(set(worker._graph_tenants)))
    worker.close()
    assert recovered == [{tenant}]
    assert not worker._graph_tenants


@pytest.mark.parametrize("recovery_fails_once", [False, True])
def test_temporary_cleanup_failure_preserves_pending_graph_recovery(monkeypatch, recovery_fails_once):
    selected = settings()
    worker = VendorProcess(
        "b4_graphiti", Path(sys.executable), selected, FakeServices(selected), {"host": "127.0.0.1", "port": 16389}
    )
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    assert process.wait(timeout=15) == 0
    worker.process = process
    temporary = tempfile.TemporaryDirectory(prefix="cme-cleanup-retry-test-")
    worker._temporary = temporary
    actual_cleanup = temporary.cleanup
    temporary_attempts = 0
    tenant = "cme_" + "b" * 64
    worker._graph_tenants.add(tenant)
    recovered = []

    def cleanup():
        nonlocal temporary_attempts
        temporary_attempts += 1
        if temporary_attempts == 1:
            raise PermissionError("fixture_transient_file_lock")
        actual_cleanup()

    def recover():
        recovered.append(set(worker._graph_tenants))
        if recovery_fails_once and len(recovered) == 1:
            raise RuntimeError("owned_graph_cleanup_failed")

    monkeypatch.setattr(temporary, "cleanup", cleanup)
    monkeypatch.setattr(worker, "_recover_graphs", recover)
    try:
        with pytest.raises(PermissionError, match="fixture_transient_file_lock"):
            worker.close()
        assert worker.process is None
        assert worker._temporary is temporary
        assert worker._cleanup_required and worker._graph_tenants == {tenant}
        assert recovered == []
        if recovery_fails_once:
            with pytest.raises(RuntimeError, match="owned_graph_cleanup_failed"):
                worker.close()
            assert worker._cleanup_required and worker._graph_tenants == {tenant}
        worker.close()
        assert recovered == [{tenant}] * (2 if recovery_fails_once else 1)
        assert temporary_attempts == 2
        assert worker._temporary is None
        assert not worker._cleanup_required and not worker._graph_tenants
        worker.close()
        assert len(recovered) == (2 if recovery_fails_once else 1)
    finally:
        worker.close()
