"""Owned graph recovery uses real Redis/Falkor commands, never Graphiti/model code."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis", reason="install the locked baselines extra for graph cleanup")


@pytest.mark.parametrize(
    ("graph", "tenants"),
    [
        ({"host": "remote.example", "port": 16389}, ["cme_" + "a" * 64]),
        ({"host": "127.0.0.1", "port": True}, ["cme_" + "a" * 64]),
        ({"host": "127.0.0.1", "port": 16389, "password": "never-accepted"}, ["cme_" + "a" * 64]),
        ({"host": "127.0.0.1", "port": 16389}, ["cme_" + "a" * 64, "foreign_graph"]),
    ],
)
def test_cleanup_validates_entire_scope_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, graph: dict[str, Any], tenants: list[str]
) -> None:
    module = importlib.import_module("evaluation.baselines.graph_cleanup")

    def unexpected_connection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unvalidated_cleanup_connected")

    monkeypatch.setattr(module.redis, "Redis", unexpected_connection)
    with pytest.raises(ValueError):
        module.cleanup_owned_graphs(graph, tenants)


def test_cleanup_cli_error_is_static_and_does_not_echo_input() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.baselines.graph_cleanup"],
        input=b'{"private":"never-echo-this"}',
        capture_output=True,
        timeout=15,
        cwd=Path(__file__).resolve().parents[3],
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"ok": False, "error": "owned_graph_cleanup_failed"}
    assert result.stderr == b""
    assert b"never-echo" not in result.stdout


def test_real_graph_cleanup_preserves_foreign_graph_and_cli_is_model_free() -> None:
    port = os.getenv("CME_BASELINE_TEST_FALKOR_PORT")
    if port is None:
        pytest.skip("CME_BASELINE_TEST_FALKOR_PORT must name a disposable local FalkorDB service")
    module = importlib.import_module("evaluation.baselines.graph_cleanup")
    owned = "cme_" + uuid4().hex * 2
    foreign = "cme_cleanup_foreign_" + uuid4().hex
    graph = {"host": "127.0.0.1", "port": int(port)}
    with redis.Redis(host="127.0.0.1", port=int(port), socket_timeout=3, socket_connect_timeout=3) as connection:
        for name in (owned, foreign):
            connection.execute_command("GRAPH.QUERY", name, "CREATE (:Fixture {value: 'preserve'})")
        try:
            # A later invalid scope must prevent the first valid name being erased.
            with pytest.raises(ValueError):
                module.cleanup_owned_graphs(graph, [owned, foreign])
            assert connection.exists(owned) and connection.exists(foreign)
            result = subprocess.run(
                [sys.executable, "-m", "evaluation.baselines.graph_cleanup"],
                input=json.dumps({"graph": graph, "tenants": [owned, owned]}).encode(),
                capture_output=True,
                timeout=15,
                cwd=Path(__file__).resolve().parents[3],
                check=False,
            )
            assert result.returncode == 0
            assert json.loads(result.stdout) == {"ok": True}
            assert result.stderr == b""
            assert not connection.exists(owned)
            assert connection.exists(foreign)
            module.cleanup_owned_graphs(graph, [owned])  # idempotent after an ordinary worker cleanup
        finally:
            for name in (owned, foreign):
                if connection.exists(name):
                    connection.execute_command("GRAPH.DELETE", name)
