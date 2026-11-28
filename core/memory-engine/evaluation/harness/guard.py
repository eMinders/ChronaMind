"""Fail-closed test authorization and durable local one-shot claims.

Attestations are reviewed human records, not cryptographic identity verification.
RL-100 owns the actual pilot, signed FREEZE_LIST, parameters and research gates.
"""

import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from evaluation.harness.config import HarnessConfig, canonical_json
from evaluation.harness.models import Record, Split, SuiteId
from evaluation.harness.paths import directory, reject_links


class Signoff(Record):
    role: Literal["methodology_owner", "project_lead", "supervisor"]
    signer: str = Field(min_length=1)
    signed_at: AwareDatetime

    @field_validator("signer")
    @classmethod
    def completed_attestation(cls, value: str) -> str:
        if not value.strip() or value.strip().lower() in {"pending", "todo", "unsigned", "n/a", "none"}:
            raise ValueError("completed_human_attestation_required")
        return value


class PilotFreeze(Record):
    """Versioned envelope accompanying RL-100's signed human-readable FREEZE_LIST."""

    schema_version: Literal[1]
    task: Literal["RL-100"]
    status: Literal["frozen"]
    git_tag: str = Field(min_length=1)
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_split_sha256: dict[SuiteId, str]
    freeze_list_path: str = Field(min_length=1)
    freeze_list_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signatures: tuple[Signoff, ...]

    @model_validator(mode="after")
    def require_all_signatures(self) -> "PilotFreeze":
        if {s.role for s in self.signatures} != {"methodology_owner", "project_lead", "supervisor"}:
            raise ValueError("all_rl100_signoffs_required")
        if len(self.signatures) != 3 or len({s.signer.strip().casefold() for s in self.signatures}) != 3:
            raise ValueError("distinct_human_signoffs_required")
        if not self.dataset_split_sha256 or any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in self.dataset_split_sha256.values()
        ):
            raise ValueError("invalid_frozen_split_digest")
        return self


class Authorization(Record):
    split: Split
    suite_id: SuiteId
    config_sha256: str
    dataset_split_sha256: str
    pilot_freeze_sha256: str | None = None
    freeze_git_tag: str | None = None
    freeze_git_commit: str | None = None


def _inside(path: Path, repo_root: Path) -> str:
    reject_links(path)
    return path.absolute().relative_to(repo_root.absolute()).as_posix()


def _git(repo_root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=repo_root, capture_output=True, timeout=15, check=False)
    if result.returncode or len(result.stdout) > 2_000_000:
        raise ValueError("freeze_git_verification_failed")
    return result.stdout


def authorize_run(
    *,
    config: HarnessConfig,
    split: Split,
    suite_id: SuiteId,
    split_path: Path,
    repo_root: Path,
    confirm_one_shot_test_run: bool = False,
    pilot_freeze_path: Path | None = None,
) -> Authorization:
    """Check explicit confirmation before accessing the test split at all."""
    if split not in {"dev", "test"}:
        raise ValueError("invalid_split")
    if split == "test" and (confirm_one_shot_test_run is not True or pilot_freeze_path is None):
        raise ValueError("test_run_requires_confirmation_and_freeze")
    config_sha = config.execution_sha256()
    split_relative = _inside(split_path, repo_root)
    if split == "dev":
        return Authorization(
            split=split,
            suite_id=suite_id,
            config_sha256=config_sha,
            dataset_split_sha256=hashlib.sha256(split_path.read_bytes()).hexdigest(),
        )
    if pilot_freeze_path is None:  # Also narrows the public union for type checkers.
        raise ValueError("pilot_freeze_required")
    freeze_relative = _inside(pilot_freeze_path, repo_root)
    if pilot_freeze_path.stat().st_size > 65_536:
        raise ValueError("pilot_freeze_envelope_too_large")
    raw = pilot_freeze_path.read_bytes()
    freeze = PilotFreeze.model_validate_json(raw)
    if freeze.config_sha256 != config_sha:
        raise ValueError("pilot_freeze_config_mismatch")
    if suite_id not in freeze.dataset_split_sha256:
        raise ValueError("suite_not_frozen")
    if any(s.signed_at > datetime.now(UTC) for s in freeze.signatures):
        raise ValueError("future_signoff_not_allowed")
    tag = f"refs/tags/{freeze.git_tag}"
    _git(repo_root, "check-ref-format", tag)
    commit = _git(repo_root, "rev-parse", "--verify", f"{tag}^{{commit}}").decode("ascii").strip()
    if _git(repo_root, "show", f"{tag}:{freeze_relative}") != raw:
        raise ValueError("freeze_envelope_not_committed_at_tag")
    freeze_list = repo_root / freeze.freeze_list_path
    list_relative = _inside(freeze_list, repo_root)
    list_bytes = freeze_list.read_bytes()
    if hashlib.sha256(list_bytes).hexdigest() != freeze.freeze_list_sha256:
        raise ValueError("signed_freeze_list_hash_mismatch")
    if _git(repo_root, "show", f"{tag}:{list_relative}") != list_bytes:
        raise ValueError("signed_freeze_list_not_committed_at_tag")
    # This is the first test-split content read, after confirmation and human records.
    split_bytes = split_path.read_bytes()
    split_sha = hashlib.sha256(split_bytes).hexdigest()
    if split_sha != freeze.dataset_split_sha256[suite_id]:
        raise ValueError("frozen_dataset_split_hash_mismatch")
    if _git(repo_root, "show", f"{tag}:{split_relative}") != split_bytes:
        raise ValueError("dataset_split_not_committed_at_tag")
    return Authorization(
        split=split,
        suite_id=suite_id,
        config_sha256=config_sha,
        dataset_split_sha256=split_sha,
        pilot_freeze_sha256=hashlib.sha256(raw).hexdigest(),
        freeze_git_tag=freeze.git_tag,
        freeze_git_commit=commit,
    )


def claim_test_run(root: Path, authorization: Authorization) -> Path | None:
    """Persist a claim before dispatch; crashes do not silently authorize a rerun.

    A canonical shared runs root is required. This is local coordination, not a
    distributed lock or a substitute for human one-shot research authorization.
    """
    if authorization.split == "dev":
        return None
    identity = {
        "config_sha256": authorization.config_sha256,
        "dataset_split_sha256": authorization.dataset_split_sha256,
        "suite_id": authorization.suite_id,
    }
    digest = hashlib.sha256(canonical_json(identity)).hexdigest()
    destination = directory(root / ".one-shot") / f"{digest}.json"
    reject_links(destination)
    try:
        with destination.open("xb") as stream:
            stream.write(canonical_json(authorization.model_dump(mode="json")))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ValueError("one_shot_test_run_already_claimed") from exc
    if os.name != "nt":
        descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return destination
