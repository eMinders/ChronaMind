"""RL-005: the engine CI workflow keeps every required gate (no remote run needed to know it).

The workflow is checked as text, so this runs on the default install. Removing a gate, unpinning an action or
widening the token's permissions fails here before it can reach ``main``.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ENGINE = WORKFLOWS / "custom-memory-engine.yaml"
SETUP = REPO / ".github" / "actions" / "setup-cme" / "action.yml"

STATIC_GATES = (
    "ruff check src tests research/benchmarks evaluation scripts",
    "ruff format --check src tests research/benchmarks evaluation scripts",
    "pyright",
    "md5sum -c validation/document_hashes.txt",
    "validation/mechanical_checks.py",
    "diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt",
    "scripts/check_statistics.py",
    "scripts/check_test_split_gate.py",
    "scripts/check_plan_refs.py",
    "scripts/audit_status.py --check",
    "scripts/plan_coverage.py --check",
)


def _job(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", text, re.M | re.S)
    assert match, f"job {name} missing"
    return match.group(1)


@pytest.fixture(scope="module")
def engine() -> str:
    return ENGINE.read_text(encoding="utf-8")


def test_the_three_engine_jobs_exist(engine: str) -> None:
    for job in ("static", "verify", "default-install"):
        _job(engine, job)


@pytest.mark.parametrize("gate", STATIC_GATES)
def test_static_job_runs_every_gate(engine: str, gate: str) -> None:
    assert gate in _job(engine, "static")


def test_verify_runs_the_full_suite_on_pinned_postgres_and_falkordb(engine: str) -> None:
    verify = _job(engine, "verify")
    assert "uv run --no-sync pytest -q" in verify
    assert "validation/bitemporal_checks.sql" in verify
    assert re.search(r"pgvector/pgvector:0\.8\.7-pg16-bookworm@sha256:[0-9a-f]{64}", verify)
    assert re.search(r"falkordb/falkordb:[0-9.]+@sha256:[0-9a-f]{64}", verify)
    assert "CME_TEST_DATABASE_URL" in verify and "--extra" not in verify  # extras come from setup-cme


def test_default_install_job_syncs_no_extras(engine: str) -> None:
    job = _job(engine, "default-install")
    assert "uses: ./.github/actions/setup-cme" in job and "extras:" not in job


def test_every_external_action_is_pinned_to_a_commit_sha() -> None:
    unpinned = []
    for path in [*WORKFLOWS.glob("*.y*ml"), SETUP]:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"uses:\s*([^\s#]+)", line)
            if match and not match.group(1).startswith("./") and not re.search(r"@[0-9a-f]{40}$", match.group(1)):
                unpinned.append(f"{path.name}: {match.group(1)}")
    assert unpinned == []


@pytest.mark.parametrize("workflow", ["custom-memory-engine.yaml", "ci.yaml"])
def test_check_workflows_hold_a_read_only_token(workflow: str) -> None:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n(?!  \w)", text, re.M)


def test_workflow_files_are_linted_in_ci() -> None:
    assert re.search(r"rhysd/actionlint@sha256:[0-9a-f]{64}", (WORKFLOWS / "ci.yaml").read_text(encoding="utf-8"))


def test_verify_runs_in_parallel_on_a_disposable_fast_database(engine: str) -> None:
    """Per-worker databases and per-test clones (conftest.py, tests/integration/schema.py) are what CI exercises."""
    verify = _job(engine, "verify")
    assert re.search(r"pytest -q -n auto\b", verify)
    for setting in ("fsync", "synchronous_commit", "full_page_writes"):
        assert f"ALTER SYSTEM SET {setting} = off" in verify
