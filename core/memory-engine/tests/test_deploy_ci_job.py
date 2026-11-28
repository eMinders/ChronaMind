"""The deployment artefacts are executed in CI, not only read (``#253``): the workflow runs ``deploy/ci_smoke.sh``.

The script is what executes systemd, the image and compose; these tests keep the job that runs it from being removed,
suppressed or narrowed, and keep the script's missing-variable sweep equal to the variables the worker requires.
"""

import re
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
WORKFLOW = ENGINE.parent.parent / ".github" / "workflows" / "custom-memory-engine.yaml"
SMOKE = ENGINE / "deploy" / "ci_smoke.sh"
README = ENGINE / "deploy" / "README.md"


def _job(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", text, re.M | re.S)
    assert match, f"job {name} missing"
    return match.group(1)


def _check_deploy_smoke_job(workflow: str) -> None:
    job = _job(workflow, "deploy-smoke")
    assert "needs: changes" in job and "if: needs.changes.outputs.engine == 'true'" in job
    assert re.search(r"pgvector/pgvector:0\.8\.7-pg16-bookworm@sha256:[0-9a-f]{64}", job)
    assert "CME_TEST_DATABASE_URL: postgresql://" in job, "the script needs a real owner-level URL"
    assert "uses: ./.github/actions/setup-cme" in job
    assert re.findall(r"(?m)^        run: ([^\n]+)$", job) == ["bash deploy/ci_smoke.sh"], "must run the smoke script"
    assert not re.search(r"(?m)^        (?:if|continue-on-error):", job), "the smoke step must not be suppressed"


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def smoke() -> str:
    return SMOKE.read_text(encoding="utf-8")


def test_the_engine_workflow_runs_the_deploy_smoke_script_unsuppressed(workflow: str) -> None:
    _check_deploy_smoke_job(workflow)


_SMOKE_STEP = "        run: bash deploy/ci_smoke.sh\n"


@pytest.mark.parametrize(
    ("anchor", "replacement"),
    [
        (_SMOKE_STEP, "        continue-on-error: true\n" + _SMOKE_STEP),
        (_SMOKE_STEP, "        run: bash deploy/ci_smoke.sh || true\n"),
        ("  deploy-smoke:\n", "  deploy-smoke-renamed:\n"),
    ],
    ids=["continue-on-error", "masked-failure", "job-removed"],
)
def test_the_deploy_smoke_job_check_rejects_a_weakened_job(workflow: str, anchor: str, replacement: str) -> None:
    mutated = workflow.replace(anchor, replacement)
    assert mutated != workflow
    with pytest.raises(AssertionError):
        _check_deploy_smoke_job(mutated)


def test_the_smoke_script_fails_on_the_first_error_and_requires_systemd(smoke: str) -> None:
    assert "set -euo pipefail" in smoke
    assert "scripts/verify_deploy_artifacts.py --require-systemd" in smoke
    assert "docker build --quiet -f deploy/Dockerfile.worker" in smoke
    assert "-f deploy/compose.worker.yaml" in smoke
    assert "python -m custom_memory_engine.schema.migrate apply" in smoke
    assert "scripts/provision_role.py" in smoke
    assert "scripts/worker_healthcheck.py" in smoke
    assert "trap cleanup EXIT" in smoke


def test_the_smoke_script_sweeps_every_variable_the_worker_requires(smoke: str) -> None:
    """A required variable added to the worker must also be proven to exit 2 from the image."""
    loop = (ENGINE / "src" / "custom_memory_engine" / "worker" / "loop.py").read_text(encoding="utf-8")
    settings = loop[loop.index("def settings_from_environment") :]
    process = (ENGINE / "scripts" / "run_worker.py").read_text(encoding="utf-8")
    required = set(re.findall(r'required\("(CME_[A-Z0-9_]+)"\)', settings))
    for text in (settings, process):
        required |= {code.upper() for code in re.findall(r'"(cme_[a-z0-9_]+)_required"', text)}
    swept = re.search(r"^REQUIRED_VARS=\(([^)]*)\)$", smoke, re.M)
    assert swept, "REQUIRED_VARS missing from deploy/ci_smoke.sh"
    assert set(swept.group(1).split()) == required
    assert {"CME_DATABASE_URL", "CME_RUNTIME_ROLE", "CME_WORKER_USERS"} <= required, "control: the scan found nothing"


def test_the_deploy_readme_no_longer_says_the_image_is_not_executed_in_ci() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "What is still not executed is systemd or Docker itself" not in readme
    assert "verified locally rather than inside GitHub Actions" not in readme
    assert "`deploy-smoke` job" in readme and "deploy/ci_smoke.sh" in readme
    # what still is not executed stays stated
    assert "**Not executed in CI:**" in readme and "no unit is started under a live PID 1" in readme
