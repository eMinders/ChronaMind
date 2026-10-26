"""Parent lifecycle guarantees independent of optional vendor SDK availability."""

import subprocess
import sys
from pathlib import Path

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
