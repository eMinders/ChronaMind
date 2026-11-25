"""Tests for deployment artifact verification and mutation controls (``#253``, ``#255``)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_deploy_artifacts.py"


def _run_verifier(root: Path, *, require_systemd: bool = False) -> tuple[int, dict[str, object]]:
    cmd = [sys.executable, str(SCRIPT), "--root", str(root), "--json"]
    if require_systemd:
        cmd.append("--require-systemd")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    payload = json.loads(proc.stdout)
    return proc.returncode, payload


def _copy_deploy_tree(dst: Path) -> None:
    shutil.copy2(ROOT / ".env.example", dst / ".env.example")
    shutil.copy2(ROOT / ".dockerignore", dst / ".dockerignore")
    shutil.copytree(ROOT / "deploy", dst / "deploy")
    (dst / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("run_worker.py", "run_maintenance.py", "worker_healthcheck.py", "run_host.py"):
        shutil.copy2(ROOT / "scripts" / name, dst / "scripts" / name)


def test_real_deploy_artifacts_verify_cleanly() -> None:
    has_systemd = shutil.which("systemd-analyze") is not None
    code, payload = _run_verifier(ROOT, require_systemd=has_systemd)
    assert code == 0, payload
    assert payload["ok"] is True
    assert payload["systemd_verified"] is has_systemd
    checked = set(payload["checked_env_vars"])  # type: ignore[arg-type]
    assert {
        "CME_DATABASE_URL",
        "CME_RUNTIME_ROLE",
        "CME_WORKER_USERS",
        "CME_MAINTENANCE_INDEXES",
        "CME_HOST_MAX_INFLIGHT",
        "CME_MODEL_CACHE",
    } <= checked


def test_bogus_execstart_in_systemd_unit_fails_loudly(tmp_path: Path) -> None:
    _copy_deploy_tree(tmp_path)
    unit = tmp_path / "deploy" / "cme-worker.service"
    unit.write_text(
        unit.read_text(encoding="utf-8").replace(
            "scripts/run_worker.py",
            "scripts/does_not_exist_worker.py",
        ),
        encoding="utf-8",
    )
    code, payload = _run_verifier(tmp_path)
    assert code == 1
    assert payload["ok"] is False
    assert any("worker_unit_exec_script_missing" in err for err in payload["errors"])  # type: ignore[attr-defined]


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not installed")
def test_bogus_systemd_directive_or_binary_fails_systemd_analyze(tmp_path: Path) -> None:
    _copy_deploy_tree(tmp_path)
    unit = tmp_path / "deploy" / "cme-worker.service"
    unit.write_text(
        unit.read_text(encoding="utf-8").replace(
            "/opt/chronamind/custom-memory-engine/.venv/bin/python",
            "/opt/chronamind/missing/python",
        ),
        encoding="utf-8",
    )
    code, payload = _run_verifier(tmp_path, require_systemd=True)
    assert code == 1
    assert payload["ok"] is False
    assert any("systemd_analyze_verify_failed" in err for err in payload["errors"])  # type: ignore[attr-defined]


def test_botched_cme_env_var_in_compose_or_unit_fails_loudly(tmp_path: Path) -> None:
    _copy_deploy_tree(tmp_path)
    compose = tmp_path / "deploy" / "compose.worker.yaml"
    compose.write_text(
        compose.read_text(encoding="utf-8").replace("CME_WORKER_IDLE_POLL", "CME_TYPO_IDLE_POLL"),
        encoding="utf-8",
    )
    code, payload = _run_verifier(tmp_path)
    assert code == 1
    assert payload["ok"] is False
    errors: list[str] = payload["errors"]  # type: ignore[assignment]
    assert any("CME_TYPO_IDLE_POLL" in err for err in errors)


def test_dockerfile_and_dockerignore_regressions_fail_loudly(tmp_path: Path) -> None:
    _copy_deploy_tree(tmp_path)
    dockerfile = tmp_path / "deploy" / "Dockerfile.worker"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace("USER chronamind", "USER root"),
        encoding="utf-8",
    )
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text(".git\n", encoding="utf-8")

    code, payload = _run_verifier(tmp_path)
    assert code == 1
    errors: list[str] = payload["errors"]  # type: ignore[assignment]
    assert "dockerfile_must_end_with_non_root_user" in errors
    assert "dockerignore_missing_entry:.env" in errors
    assert "dockerignore_missing_entry:.model-cache" in errors
