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
    "python -m tests.engine_source_identity",
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


def test_the_engine_jobs_exist(engine: str) -> None:
    for job in ("static", "verify", "default-install", "dual-profile"):
        _job(engine, job)


@pytest.mark.parametrize("gate", STATIC_GATES)
def test_static_job_runs_every_gate(engine: str, gate: str) -> None:
    assert gate in _job(engine, "static")


def test_source_identity_gate_runs_directly_and_propagates_failure(engine: str) -> None:
    """A comment, echoed command, or shell failure suppression must not satisfy the gate."""
    static = _job(engine, "static")
    steps = re.findall(r"(?ms)^      - name: Engine source identity\n(.*?)(?=^      - |\Z)", static)
    assert len(steps) == 1, "exactly one source identity step is required"
    assert re.search(r"(?m)^        run: uv run --no-sync python -m tests\.engine_source_identity[ \t]*$", steps[0])
    assert not re.search(r"(?m)^        (?:if|continue-on-error):", steps[0]), "the gate must not be suppressed"


@pytest.mark.parametrize("suppression", ["if: false", "continue-on-error: true"])
def test_source_identity_gate_rejects_suppressed_step(engine: str, suppression: str) -> None:
    mutated = engine.replace(
        "      - name: Engine source identity\n", f"      - name: Engine source identity\n        {suppression}\n"
    )
    assert mutated != engine
    with pytest.raises(AssertionError):
        test_source_identity_gate_runs_directly_and_propagates_failure(mutated)


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


def test_dual_profile_job_runs_both_profiles_on_one_migrated_database(engine: str) -> None:
    """E-023: one migration invocation, one database, both profile suites, and the isolation module that proves it."""
    job = _job(engine, "dual-profile")
    assert "needs: changes" in job and "if: needs.changes.outputs.engine == 'true'" in job
    assert re.search(r"pgvector/pgvector:0\.8\.7-pg16-bookworm@sha256:[0-9a-f]{64}", job)
    assert "falkordb" not in job, "no suite in this job needs FalkorDB; do not carry the service for symmetry"
    assert job.count("python -m custom_memory_engine.schema.migrate apply") == 1
    assert "python -m custom_memory_engine.schema.migrate status" in job
    # `migrate status` exits 0 whatever it reports, so the job must gate on the state it prints.
    assert '.state == "applied"' in job
    assert 'CME_TEST_PREMIGRATED_DATABASE: "1"' in job
    # The suites skip when the URL is unset, so the step that would fail first must be wired to it.
    assert "RL_DATABASE_URL: ${{ env.CME_TEST_DATABASE_URL }}" in job
    # A job that only names the variable can still run every suite as a skip; the value must be a real URL.
    assert "CME_TEST_DATABASE_URL: postgresql://" in job
    for suite in (
        "tests/integration/test_dual_profile_db.py",
        "tests/integration/test_engine_adapter_db.py",
        "tests/integration/test_coding_profile_db.py",
        "tests/integration/test_rls_isolation.py",
    ):
        assert suite in job, suite
    # A premigrated run copies the configured database, so an xdist worker would copy an unmigrated one.
    assert not re.search(r"pytest[^\n]*\s-n\b", job)
    assert not re.search(r"(?m)^        (?:if|continue-on-error):", job), "the job's steps must not be suppressed"


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


def test_check_workflows_do_not_leave_git_credentials_behind() -> None:
    """No check job uses git credentials after cloning, so none may persist the token in .git/config."""
    for workflow in ("custom-memory-engine.yaml", "ci.yaml", "app-image.yaml", "deploy.yaml"):
        text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
        checkout = r"uses: actions/checkout@[0-9a-f]{40}[^\n]*\n((?:        [^\n]*\n)*)"
        checkouts = re.findall(checkout, text)
        assert checkouts, workflow
        assert all("persist-credentials: false" in block for block in checkouts), workflow


def test_the_engine_interpreter_has_one_source_on_its_supported_line() -> None:
    engine = Path(__file__).resolve().parents[1]
    version = (engine / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.12\.\d+", version), "RL-013: the engine stays on the CPython 3.12 line"
    assert 'requires-python = ">=3.12,<3.13"' in (engine / "pyproject.toml").read_text(encoding="utf-8")
    assert "--python" not in SETUP.read_text(encoding="utf-8"), "CI must take the interpreter from .python-version"


def test_dependabot_never_proposes_a_frozen_baseline_vendor() -> None:
    """Every package of the `baselines` extra is a frozen research input (evidence/RL-031-verification.md)."""
    engine = Path(__file__).resolve().parents[1]
    pyproject = (engine / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^baselines = \[\n(.*?)^\]", pyproject, re.M | re.S)
    assert block
    frozen = {re.split(r"[\[=<>!~ ]", item, maxsplit=1)[0] for item in re.findall(r'"([^"]+)"', block.group(1))}
    dependabot = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    ignored = set(re.findall(r"- dependency-name: ([A-Za-z0-9_.-]+)", dependabot))
    assert frozen and frozen <= ignored, frozen - ignored


def test_dependabot_respects_the_documented_upgrade_blockers() -> None:
    """Langfuse stays below 5 (evals/ needs api.trace.list) and the image's Python minor follows .python-version."""
    dependabot = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    major, minor = '"version-update:semver-major"', '"version-update:semver-minor"'
    assert f"- dependency-name: langfuse\n        update-types: [{major}]" in dependabot
    assert f"- dependency-name: python\n        update-types: [{major}, {minor}]" in dependabot
    assert '"langfuse>=4.18.0,<5"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")
