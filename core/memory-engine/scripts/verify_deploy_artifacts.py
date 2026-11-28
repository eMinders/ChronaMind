"""Static & staged verification of worker/maintenance deployment artifacts (``#253``, ``#255``).

Checks performed:

1. Cross-checks every ``CME_*`` environment variable referenced in ``deploy/cme-worker.service``,
   ``deploy/cme-maintenance.service``, ``deploy/cme-erasure-journal.service``, ``deploy/compose.worker.yaml``, and
   ``deploy/Dockerfile.worker`` (plus engine host/worker/maintenance code when present) against ``.env.example`` and
   ``deploy/README.md``.
2. Inspects ``deploy/Dockerfile.worker`` and ``.dockerignore`` for pinned base/uv SHA-256 digests, unprivileged
   ``USER chronamind``, read-only code/venv permissions, ``STOPSIGNAL SIGTERM``, queue-based ``HEALTHCHECK``, and
   exclusion of ``.env`` / ``.model-cache`` / secrets.
3. Validates ``deploy/compose.worker.yaml`` read-only container filesystem, read-only model cache mount, grace period,
   and healthcheck wiring.
4. Validates ``deploy/cme-worker.service``, ``deploy/cme-maintenance.service``, and ``deploy/cme-maintenance.timer``
   directives and runs ``systemd-analyze verify --root ...`` against a staged filesystem root when ``systemd-analyze``
   is available (or fails if ``--require-systemd`` is passed and the binary is absent).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_CME_VAR_RE = re.compile(r"\b(CME_[A-Z0-9_]+)\b")
_ENV_ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")
_SECRET_VARS = frozenset(
    {
        "CME_DATABASE_URL",
        "CME_RUNTIME_ROLE",
        "CME_HOST_TOKENS",
        "CME_PROVENANCE_KEY",
        "CME_DELETION_RECEIPT_KEY",
        "CME_LLM_API_KEY",
        "CME_LLM_BASE_URL",
    }
)
_SYSTEMD_TARGETS = (
    "sysinit.target",
    "basic.target",
    "multi-user.target",
    "timers.target",
    "network-online.target",
    "shutdown.target",
    "sockets.target",
    "paths.target",
    "slices.target",
)


@dataclass(frozen=True, slots=True)
class DeployVerificationReport:
    """Result of verifying all deployment artifacts under one engine root."""

    errors: tuple[str, ...]
    checked_env_vars: tuple[str, ...]
    systemd_verified: bool

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_env_example(path: Path) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    if not path.is_file():
        return {}, [f"missing_file:{path.name}"]
    assignments: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGN_RE.match(line)
        if not match:
            errors.append(f"env_example_malformed_line:{lineno}")
            continue
        name, value = match.group(1), match.group(2).strip()
        assignments[name] = value
        if name in _SECRET_VARS and value:
            errors.append(f"env_example_secret_must_be_empty:{name}")
    return assignments, errors


def _extract_cme_vars(text: str) -> set[str]:
    return set(_CME_VAR_RE.findall(text))


def _check_docker_artifacts(root: Path) -> list[str]:
    errors: list[str] = []
    dockerignore_path = root / ".dockerignore"
    if not dockerignore_path.is_file():
        errors.append("missing_file:.dockerignore")
    else:
        ignored = {
            line.strip()
            for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        for required in (".env", ".env.*", ".model-cache", ".git", ".venv", "tests/"):
            if required not in ignored:
                errors.append(f"dockerignore_missing_entry:{required}")

    dockerfile_path = root / "deploy" / "Dockerfile.worker"
    if not dockerfile_path.is_file():
        errors.append("missing_file:deploy/Dockerfile.worker")
        return errors

    text = dockerfile_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    from_lines = [line.strip() for line in lines if line.strip().upper().startswith("FROM ")]
    if len(from_lines) < 2:
        errors.append("dockerfile_expected_multistage_build")
    for line in from_lines:
        if not re.search(r"@sha256:[0-9a-f]{64}\b", line):
            errors.append(f"dockerfile_unpinned_base_image:{line}")

    uv_copy_lines = [line.strip() for line in lines if "ghcr.io/astral-sh/uv" in line]
    if not uv_copy_lines or any(not re.search(r"@sha256:[0-9a-f]{64}\b", line) for line in uv_copy_lines):
        errors.append("dockerfile_unpinned_uv_image")

    user_lines = [line.strip() for line in lines if re.match(r"^\s*USER\s+", line, re.IGNORECASE)]
    if not user_lines or user_lines[-1].split()[1] in {"root", "0"}:
        errors.append("dockerfile_must_end_with_non_root_user")

    if "chmod -R a-w /app/.venv /app/src /app/scripts" not in text:
        errors.append("dockerfile_missing_readonly_code_chmod")
    if "STOPSIGNAL SIGTERM" not in text:
        errors.append("dockerfile_missing_stopsignal_sigterm")
    if "HEALTHCHECK " not in text or "scripts/worker_healthcheck.py" not in text:
        errors.append("dockerfile_missing_queue_healthcheck")
    if not (root / "scripts" / "worker_healthcheck.py").is_file():
        errors.append("dockerfile_healthcheck_script_missing:scripts/worker_healthcheck.py")
    if not (root / "scripts" / "run_worker.py").is_file():
        errors.append("dockerfile_cmd_script_missing:scripts/run_worker.py")

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"\b(?:COPY|ADD)\b.*\.(?:env|model-cache)\b", stripped):
            errors.append(f"dockerfile_copies_forbidden_asset:{stripped}")
        if re.search(r"(?:API_KEY|PASSWORD|PROVENANCE_KEY|DELETION_RECEIPT_KEY)\s*=\s*\S+", stripped):
            errors.append(f"dockerfile_hardcoded_secret:{stripped}")

    return errors


def _check_compose_worker(root: Path) -> list[str]:
    errors: list[str] = []
    compose_path = root / "deploy" / "compose.worker.yaml"
    if not compose_path.is_file():
        return ["missing_file:deploy/compose.worker.yaml"]
    text = compose_path.read_text(encoding="utf-8")
    if "read_only: true" not in text:
        errors.append("compose_missing_read_only_root")
    if ":/models:ro" not in text:
        errors.append("compose_model_cache_must_be_ro")
    if "stop_grace_period:" not in text:
        errors.append("compose_missing_stop_grace_period")
    if "scripts/worker_healthcheck.py" not in text:
        errors.append("compose_missing_worker_healthcheck")
    if "dockerfile: deploy/Dockerfile.worker" not in text:
        errors.append("compose_missing_dockerfile_reference")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for secret_key in _SECRET_VARS:
            match = re.match(rf"^{secret_key}:\s*(.+)$", stripped)
            if match:
                val = match.group(1).strip()
                if not val.startswith("${"):
                    errors.append(f"compose_hardcoded_secret:{secret_key}")
    return errors


def _check_unit_contents(root: Path) -> list[str]:
    errors: list[str] = []
    deploy_dir = root / "deploy"
    worker_unit = deploy_dir / "cme-worker.service"
    maint_unit = deploy_dir / "cme-maintenance.service"
    maint_timer = deploy_dir / "cme-maintenance.timer"
    journal_unit = deploy_dir / "cme-erasure-journal.service"
    journal_timer = deploy_dir / "cme-erasure-journal.timer"

    for unit in (worker_unit, maint_unit, maint_timer, journal_unit, journal_timer):
        if not unit.is_file():
            errors.append(f"missing_file:deploy/{unit.name}")

    if worker_unit.is_file():
        w_text = worker_unit.read_text(encoding="utf-8")
        for required in (
            "[Unit]",
            "[Service]",
            "[Install]",
            "User=chronamind",
            "Group=chronamind",
            "Restart=always",
            "KillSignal=SIGTERM",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "EnvironmentFile=/etc/chronamind/worker.env",
            "ReadOnlyPaths=/opt/chronamind/core/memory-engine/.model-cache",
        ):
            if required not in w_text:
                errors.append(f"worker_unit_missing_directive:{required}")
        if re.search(r"^\s*ExecStartPost=", w_text, re.M):
            errors.append("worker_unit_forbids_exec_start_post")
        exec_match = re.search(r"^\s*ExecStart=\S+\s+(\S+)", w_text, re.M)
        if not exec_match or not (root / exec_match.group(1)).is_file():
            errors.append("worker_unit_exec_script_missing")

    if maint_unit.is_file():
        m_text = maint_unit.read_text(encoding="utf-8")
        for required in (
            "[Unit]",
            "[Service]",
            "[Install]",
            "Type=oneshot",
            "User=chronamind",
            "Group=chronamind",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "EnvironmentFile=/etc/chronamind/worker.env",
        ):
            if required not in m_text:
                errors.append(f"maintenance_unit_missing_directive:{required}")
        exec_match = re.search(r"^\s*ExecStart=\S+\s+(\S+)", m_text, re.M)
        if not exec_match or not (root / exec_match.group(1)).is_file():
            errors.append("maintenance_unit_exec_script_missing")

    if journal_unit.is_file():
        j_text = journal_unit.read_text(encoding="utf-8")
        for required in (
            "[Unit]",
            "[Service]",
            "[Install]",
            "Type=oneshot",
            # its own transient user: the BYPASSRLS credential must not be readable as the worker's user
            "DynamicUser=yes",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "StateDirectory=chronamind-journal",
            # the BYPASSRLS journal credential must never share the worker's environment file
            "EnvironmentFile=/etc/chronamind/journal.env",
        ):
            if required not in j_text:
                errors.append(f"journal_unit_missing_directive:{required}")
        if "/etc/chronamind/worker.env" in j_text:
            errors.append("journal_unit_must_not_read_worker_env")
        if re.search(r"^\s*User=chronamind\s*$", j_text, re.M):
            errors.append("journal_unit_must_not_run_as_the_worker_user")
        exec_match = re.search(r"^\s*ExecStart=\S+\s+(\S+)", j_text, re.M)
        if not exec_match or not (root / exec_match.group(1)).is_file():
            errors.append("journal_unit_exec_script_missing")

    for timer, label in ((maint_timer, "maintenance"), (journal_timer, "journal")):
        if not timer.is_file():
            continue
        t_text = timer.read_text(encoding="utf-8")
        for required in ("[Unit]", "[Timer]", "[Install]", "OnCalendar=", "Persistent=true"):
            if required not in t_text:
                errors.append(f"{label}_timer_missing_directive:{required}")
        unit_match = re.search(r"^\s*Unit=(\S+)", t_text, re.M)
        if not unit_match or not (deploy_dir / unit_match.group(1)).is_file():
            errors.append(f"{label}_timer_target_unit_missing")

    return errors


def _run_systemd_analyze(root: Path, *, require_systemd: bool) -> tuple[bool, list[str]]:
    binary = shutil.which("systemd-analyze")
    if binary is None:
        if require_systemd:
            return False, ["systemd_analyze_binary_missing"]
        return False, []

    deploy_dir = root / "deploy"
    unit_names = (
        "cme-worker.service",
        "cme-maintenance.service",
        "cme-maintenance.timer",
        "cme-erasure-journal.service",
        "cme-erasure-journal.timer",
    )
    for name in unit_names:
        if not (deploy_dir / name).is_file():
            return False, [f"missing_unit_for_systemd_analyze:{name}"]

    with tempfile.TemporaryDirectory(prefix="cme_systemd_verify_") as tmp:
        stage = Path(tmp)
        etc_systemd = stage / "etc" / "systemd" / "system"
        etc_systemd.mkdir(parents=True)
        (stage / "etc" / "chronamind").mkdir(parents=True)
        (stage / "etc" / "chronamind" / "worker.env").write_text("", encoding="utf-8")
        (stage / "etc" / "chronamind" / "journal.env").write_text("", encoding="utf-8")

        app_root = stage / "opt" / "chronamind" / "core/memory-engine"
        (app_root / ".venv" / "bin").mkdir(parents=True)
        (app_root / "scripts").mkdir(parents=True)
        (app_root / "deploy").mkdir(parents=True)
        (app_root / ".model-cache").mkdir(parents=True)

        py = app_root / ".venv" / "bin" / "python"
        py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        py.chmod(0o755)

        for script_name in ("run_worker.py", "run_maintenance.py", "worker_healthcheck.py", "erasure_journal.py"):
            src_script = root / "scripts" / script_name
            if src_script.is_file():
                shutil.copy2(src_script, app_root / "scripts" / script_name)

        readme = deploy_dir / "README.md"
        if readme.is_file():
            shutil.copy2(readme, app_root / "deploy" / "README.md")

        sys_units = stage / "usr" / "lib" / "systemd" / "system"
        sys_units.mkdir(parents=True)
        for target in _SYSTEMD_TARGETS:
            (sys_units / target).write_text(f"[Unit]\nDescription={target}\n", encoding="utf-8")

        staged_units: list[str] = []
        for name in unit_names:
            dst = etc_systemd / name
            shutil.copy2(deploy_dir / name, dst)
            staged_units.append(str(dst))

        proc = subprocess.run(
            [binary, "verify", "--root", str(stage), *staged_units],
            capture_output=True,
            text=True,
            check=False,
        )
        errors: list[str] = []
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip() or f"exit_code_{proc.returncode}"
            errors.append(f"systemd_analyze_verify_failed:{detail}")
        elif proc.stderr.strip():
            errors.append(f"systemd_analyze_verify_warnings:{proc.stderr.strip()}")
        return True, errors


def verify_deploy_artifacts(root: Path = DEFAULT_ROOT, *, require_systemd: bool = False) -> DeployVerificationReport:
    """Validate deployment unit files, Compose/Docker specs, env inventories, and systemd verification."""
    errors: list[str] = []
    root = root.resolve()

    env_assignments, env_errors = _parse_env_example(root / ".env.example")
    errors.extend(env_errors)

    readme_path = root / "deploy" / "README.md"
    if not readme_path.is_file():
        errors.append("missing_file:deploy/README.md")
        readme_vars: set[str] = set()
    else:
        readme_vars = _extract_cme_vars(readme_path.read_text(encoding="utf-8"))

    deploy_sources = (
        root / "deploy" / "cme-worker.service",
        root / "deploy" / "cme-maintenance.service",
        root / "deploy" / "cme-erasure-journal.service",
        root / "deploy" / "compose.worker.yaml",
        root / "deploy" / "Dockerfile.worker",
    )
    checked_vars: set[str] = set()
    for source_path in deploy_sources:
        if not source_path.is_file():
            continue
        vars_in_file = _extract_cme_vars(source_path.read_text(encoding="utf-8"))
        checked_vars.update(vars_in_file)
        rel = source_path.relative_to(root).as_posix()
        for var in sorted(vars_in_file):
            if var not in env_assignments:
                errors.append(f"env_var_missing_from_env_example:{rel}:{var}")
            if var not in readme_vars:
                errors.append(f"env_var_missing_from_deploy_readme:{rel}:{var}")

    code_sources = (
        root / "src" / "custom_memory_engine" / "host" / "service.py",
        root / "src" / "custom_memory_engine" / "worker" / "loop.py",
        root / "src" / "custom_memory_engine" / "worker" / "maintenance.py",
        root / "scripts" / "run_host.py",
        root / "scripts" / "run_worker.py",
        root / "scripts" / "run_maintenance.py",
        root / "scripts" / "worker_healthcheck.py",
    )
    for code_path in code_sources:
        if not code_path.is_file():
            continue
        vars_in_code = _extract_cme_vars(code_path.read_text(encoding="utf-8"))
        checked_vars.update(vars_in_code)
        rel = code_path.relative_to(root).as_posix()
        for var in sorted(vars_in_code):
            if var not in env_assignments:
                errors.append(f"code_env_var_missing_from_env_example:{rel}:{var}")
            if var not in readme_vars:
                errors.append(f"code_env_var_missing_from_deploy_readme:{rel}:{var}")

    errors.extend(_check_docker_artifacts(root))
    errors.extend(_check_compose_worker(root))
    errors.extend(_check_unit_contents(root))
    systemd_verified, systemd_errors = _run_systemd_analyze(root, require_systemd=require_systemd)
    errors.extend(systemd_errors)

    return DeployVerificationReport(
        errors=tuple(errors),
        checked_env_vars=tuple(sorted(checked_vars)),
        systemd_verified=systemd_verified,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify CME deployment artifacts, env cross-checks, and units.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Engine root directory.")
    parser.add_argument("--require-systemd", action="store_true", help="Fail if systemd-analyze is unavailable.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point returning 0 on success and 1 on any deployment-artifact error."""
    args = _parse_args(argv)
    report = verify_deploy_artifacts(args.root, require_systemd=args.require_systemd)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "systemd_verified": report.systemd_verified,
                    "checked_env_vars": list(report.checked_env_vars),
                    "errors": list(report.errors),
                }
            )
        )
        return 0 if report.ok else 1

    console = Console()
    if report.ok:
        console.print(
            f"[green]deploy verification passed[/green]: "
            f"{len(report.checked_env_vars)} CME_* vars verified; systemd_verified={report.systemd_verified}"
        )
        return 0
    err_console = Console(stderr=True)
    for error in report.errors:
        err_console.print(f"[red]ERROR[/red]: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
