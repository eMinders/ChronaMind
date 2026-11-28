"""Synthetic temporary Git records exercise freeze/one-shot/path safeguards only."""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from evaluation.harness.config import HarnessConfig
from evaluation.harness.dummy import DummyAdapter, dummy_score
from evaluation.harness.guard import Authorization, authorize_run, claim_test_run
from evaluation.harness.paths import run_destination
from evaluation.harness.runner import run_evaluation
from evaluation.harness.tests.test_budget import directory_link
from evaluation.harness.tests.test_config_contract import config_data


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, timeout=20, check=True
    )
    return result.stdout.strip()


@dataclass
class FrozenFixture:
    root: Path
    split: Path
    freeze: Path
    signed_list: Path
    config: HarnessConfig
    document: dict[str, Any]

    def write_freeze(self) -> None:
        self.freeze.write_bytes((json.dumps(self.document, sort_keys=True) + "\n").encode("utf-8"))

    def retag(self) -> None:
        self.write_freeze()
        _git(self.root, "add", "--", ".")
        _git(
            self.root,
            "-c",
            "user.name=Synthetic Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "synthetic guard fixture only",
        )
        _git(self.root, "tag", "--force", "fixture-freeze")

    def authorize(self, *, confirm: bool = True) -> Authorization:
        return authorize_run(
            config=self.config,
            split="test",
            suite_id="T",
            split_path=self.split,
            repo_root=self.root,
            confirm_one_shot_test_run=confirm,
            pilot_freeze_path=self.freeze,
        )


@pytest.fixture
def frozen(tmp_path: Path) -> FrozenFixture:
    root = tmp_path / "synthetic-repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    split = root / "synthetic-test.json"
    split.write_bytes(b'{"synthetic":"no benchmark or research split"}\n')
    signed_list = root / "FREEZE_LIST.md"
    signed_list.write_bytes(
        b"# Synthetic guard fixture, no actual approval\n"
        b"Fictional methodology owner, project lead and supervisor attestations for software testing.\n"
    )
    config = HarnessConfig.model_validate_json(json.dumps(config_data()))
    document: dict[str, Any] = {
        "schema_version": 1,
        "task": "RL-100",
        "status": "frozen",
        "git_tag": "fixture-freeze",
        "config_sha256": config.execution_sha256(),
        "dataset_split_sha256": {"T": hashlib.sha256(split.read_bytes()).hexdigest()},
        "freeze_list_path": "FREEZE_LIST.md",
        "freeze_list_sha256": hashlib.sha256(signed_list.read_bytes()).hexdigest(),
        "signatures": [
            {"role": role, "signer": signer, "signed_at": "2020-01-01T00:00:00Z"}
            for role, signer in (
                ("methodology_owner", "fictional-method"),
                ("project_lead", "fictional-lead"),
                ("supervisor", "fictional-supervisor"),
            )
        ],
    }
    result = FrozenFixture(root, split, root / "pilot_freeze.json", signed_list, config, document)
    result.retag()
    return result


def forbid_split_read(monkeypatch: pytest.MonkeyPatch, split: Path) -> None:
    original = Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path.absolute() == split.absolute():
            pytest.fail("Denied attempt read synthetic test split contents")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)


def test_confirmation_is_checked_before_even_the_freeze_is_read(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(path: Path) -> bytes:
        pytest.fail("Unconfirmed attempt read an input file")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(ValueError, match="test_run_requires_confirmation_and_freeze"):
        frozen.authorize(confirm=False)
    assert not (frozen.root / "runs").exists()


@pytest.mark.parametrize("confirm", [False, True])
def test_public_runner_denial_has_no_loader_adapter_or_output_side_effects(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
    confirm: bool,
) -> None:
    def forbidden_loader() -> tuple:
        pytest.fail("Denied attempt invoked the case loader")

    def forbidden_factory(config: HarnessConfig) -> DummyAdapter:
        pytest.fail("Denied attempt constructed an adapter")

    def forbidden_read(path: Path) -> bytes:
        pytest.fail("Denied attempt read an input file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    output = frozen.root / "runs"
    with pytest.raises(ValueError, match="test_run_requires_confirmation_and_freeze"):
        run_evaluation(
            config=frozen.config,
            suite_id="T",
            split="test",
            split_path=frozen.split,
            case_loader=forbidden_loader,
            adapter_factory=forbidden_factory,
            scorer=dummy_score,
            run_id="forbidden",
            output_root=output,
            repo_root=frozen.root,
            confirm_one_shot_test_run=confirm,
            pilot_freeze_path=None,
        )
    assert not output.exists()


@pytest.mark.parametrize("raw", [b"not JSON", b"{}", b'{"status":"pending"}'])
def test_malformed_envelope_cannot_reach_the_test_split(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    frozen.freeze.write_bytes(raw)
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError):
        frozen.authorize()


@pytest.mark.parametrize("problem", ["missing", "pending", "same-person", "duplicate-role", "future"])
def test_incomplete_or_ambiguous_signoffs_deny_before_split_access(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    signatures = frozen.document["signatures"]
    if problem == "missing":
        signatures.pop()
    elif problem == "pending":
        signatures[-1]["signer"] = "pending"
    elif problem == "same-person":
        signatures[-1]["signer"] = signatures[0]["signer"].upper()
    elif problem == "duplicate-role":
        signatures[-1]["role"] = signatures[0]["role"]
    else:
        signatures[-1]["signed_at"] = "2999-01-01T00:00:00Z"
    frozen.write_freeze()
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError):
        frozen.authorize()


@pytest.mark.parametrize("problem", ["configuration", "suite", "tag", "uncommitted", "list-hash"])
def test_envelope_binding_failures_deny_before_split_access(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    if problem == "configuration":
        frozen.document["config_sha256"] = "b" * 64
    elif problem == "suite":
        frozen.document["dataset_split_sha256"] = {"G": "a" * 64}
    elif problem == "tag":
        frozen.document["git_tag"] = "missing-tag"
    elif problem == "uncommitted":
        frozen.document["signatures"][0]["signer"] = "different-fictional-method"
    else:
        frozen.document["freeze_list_sha256"] = "c" * 64
        frozen.retag()
    frozen.write_freeze()
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError):
        frozen.authorize()


def test_changed_signed_list_fails_before_split_access(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen.signed_list.write_bytes(b"changed uncommitted human-readable record\n")
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError, match="signed_freeze_list_hash_mismatch"):
        frozen.authorize()


def test_list_hash_alone_cannot_substitute_for_committed_tagged_record(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen.signed_list.write_bytes(b"changed synthetic record\n")
    frozen.document["freeze_list_sha256"] = hashlib.sha256(frozen.signed_list.read_bytes()).hexdigest()
    frozen.write_freeze()
    _git(frozen.root, "add", "--", "pilot_freeze.json")
    _git(
        frozen.root,
        "-c",
        "user.name=Synthetic Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "change envelope only",
    )
    _git(frozen.root, "tag", "--force", "fixture-freeze")
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError, match="signed_freeze_list_not_committed_at_tag"):
        frozen.authorize()


def test_exact_tagged_synthetic_freeze_authorizes_and_records_identity(frozen: FrozenFixture) -> None:
    authorization = frozen.authorize()
    assert authorization.split == "test"
    assert authorization.suite_id == "T"
    assert authorization.config_sha256 == frozen.config.execution_sha256()
    assert authorization.dataset_split_sha256 == hashlib.sha256(frozen.split.read_bytes()).hexdigest()
    assert authorization.pilot_freeze_sha256 == hashlib.sha256(frozen.freeze.read_bytes()).hexdigest()
    assert authorization.freeze_git_commit == _git(frozen.root, "rev-parse", "HEAD")
    assert authorization.freeze_git_tag == "fixture-freeze"
    assert not (frozen.root / "runs").exists()


def test_changed_test_split_is_rejected_by_exact_digest(frozen: FrozenFixture) -> None:
    frozen.split.write_bytes(b'{"synthetic":"changed input"}\n')
    with pytest.raises(ValueError, match="frozen_dataset_split_hash_mismatch"):
        frozen.authorize()


def test_matching_digest_still_requires_split_committed_at_tag(frozen: FrozenFixture) -> None:
    frozen.split.write_bytes(b'{"synthetic":"changed input"}\n')
    frozen.document["dataset_split_sha256"]["T"] = hashlib.sha256(frozen.split.read_bytes()).hexdigest()
    frozen.write_freeze()
    _git(frozen.root, "add", "--", "pilot_freeze.json")
    _git(
        frozen.root,
        "-c",
        "user.name=Synthetic Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "change envelope only",
    )
    _git(frozen.root, "tag", "--force", "fixture-freeze")
    with pytest.raises(ValueError, match="dataset_split_not_committed_at_tag"):
        frozen.authorize()


def test_persistent_claim_cannot_be_bypassed_by_renaming_display_configuration(frozen: FrozenFixture) -> None:
    root = frozen.root / "runs"
    first = frozen.authorize()
    claim = claim_test_run(root, first)
    assert claim is not None and claim.is_file()
    old_bytes = claim.read_bytes()
    alternate = {**config_data(), "config_id": "different-name", "budget_id": "different-budget-name"}
    frozen.config = HarnessConfig.model_validate_json(json.dumps(alternate))
    same_execution = frozen.authorize()
    with pytest.raises(ValueError, match="one_shot_test_run_already_claimed"):
        claim_test_run(root, same_execution)
    assert claim.read_bytes() == old_bytes


def test_development_authorization_does_not_create_a_one_shot_claim(frozen: FrozenFixture) -> None:
    authorization = authorize_run(
        config=frozen.config,
        split="dev",
        suite_id="T",
        split_path=frozen.split,
        repo_root=frozen.root,
    )
    root = frozen.root / "runs"
    assert claim_test_run(root, authorization) is None
    assert not root.exists()


@pytest.mark.parametrize("component", ["../escape", "/absolute", "C:\\escape", "CON", "a/b", ".", ""])
def test_portable_run_components_refuse_escape_without_creating_directories(tmp_path: Path, component: str) -> None:
    root = tmp_path / "runs"
    with pytest.raises(ValueError, match="unsafe_artifact_identifier"):
        run_destination(root, component, "config", "T")
    assert not root.exists()


def test_existing_run_is_refused_without_touching_content(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    existing = root / "run/config/T"
    existing.mkdir(parents=True)
    marker = existing / "manifest.json"
    marker.write_bytes(b"unrelated existing run\n")
    with pytest.raises(FileExistsError, match="immutable_run_already_exists"):
        run_destination(root, "run", "config", "T")
    assert marker.read_bytes() == b"unrelated existing run\n"


@pytest.mark.parametrize("relative", ["runs", "runs/linked-run"])
def test_linked_output_ancestors_are_refused_without_writing_target(tmp_path: Path, relative: str) -> None:
    link = tmp_path / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "unrelated-target"
    target.mkdir()
    marker = target / "keep"
    marker.write_bytes(b"keep unrelated fixture\n")
    directory_link(link, target)
    try:
        with pytest.raises(ValueError, match="linked_artifact_path_not_allowed"):
            run_destination(tmp_path / "runs", "linked-run", "config", "T")
        assert list(target.iterdir()) == [marker]
        assert marker.read_bytes() == b"keep unrelated fixture\n"
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_linked_claim_directory_is_refused_without_writing_target(frozen: FrozenFixture, tmp_path: Path) -> None:
    authorization = frozen.authorize()
    root = frozen.root / "runs"
    root.mkdir()
    target = tmp_path / "unrelated-claim-target"
    target.mkdir()
    link = root / ".one-shot"
    directory_link(link, target)
    try:
        with pytest.raises(ValueError, match="linked_artifact_path_not_allowed"):
            claim_test_run(root, authorization)
        assert not list(target.iterdir())
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_freeze_cannot_reference_a_parent_path(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen.document["freeze_list_path"] = "../outside-list.md"
    frozen.retag()
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError, match="parent_traversal_not_allowed"):
        frozen.authorize()


def test_freeze_cannot_reference_an_absolute_outside_path(
    frozen: FrozenFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen.document["freeze_list_path"] = str(tmp_path / "outside-list.md")
    frozen.retag()
    forbid_split_read(monkeypatch, frozen.split)
    with pytest.raises(ValueError):
        frozen.authorize()
