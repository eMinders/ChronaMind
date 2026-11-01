"""RL-072: content-free audit details and log redaction (Invariant 8, Channel 7)."""

import ast
from pathlib import Path

import pytest

from custom_memory_engine.security.audit_guard import (
    AUDIT_DETAIL_KEYS,
    CONTENT_KEYS,
    REDACTED,
    AuditContentError,
    content_free_details,
    redact_log_event,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
# Jsonb payloads that are not audit details: the job queue stores the caller's job payload, which
# deletion purges (``engine/deletion.py``) and Channel 1 scans.
NON_AUDIT_JSONB = {("queue/worker.py", "job.payload"), ("queue/worker.py", "pinned.payload")}

# The detail shape of every engine audit event, as written today.
ENGINE_DETAILS = [
    {"version_id": "t-1:0", "ablation": "P"},
    {"candidate_version_id": "v2", "existing_version_id": "v1", "conflict_id": "conflict-ab12"},
    {
        "ablation": "A2",
        "closed_version_ids": ["v1"],
        "deleted_version_ids": [],
        "reissued_version_id": None,
        "inserted_version_id": "v2",
    },
    {"closed_version_ids": ["a", "b"], "reissued_prior_version_id": "a2", "reissued_later_version_id": "b2"},
    {"candidate_version_id": "v2", "closed_version_ids": ("v1",), "reissued_version_id": "v1r", "conflict_id": "c"},
    {"ablation": "P", "closed_version_id": "v1", "deleted_version_id": None, "truncated_version_id": "v1t"},
    {"version_id": "v1", "archival_branch": "C"},
    {"scope": "memory", "deletion_epoch": 3, "deleted_versions": 2, "dependents": 1, "re_derived_versions": 0},
    {"purged_jobs": 0, "job_id": "j1", "kind": "extract", "epoch_start": 1, "current_epoch": 2},
    {"clock_mode": "replay", "transaction_id": "748"},
]


@pytest.mark.parametrize("details", ENGINE_DETAILS)
def test_every_engine_audit_shape_is_accepted(details: dict[str, object]) -> None:
    assert content_free_details(details).keys() == details.keys()


def test_content_keys_can_never_be_audit_keys() -> None:
    assert not CONTENT_KEYS & AUDIT_DETAIL_KEYS
    for key in CONTENT_KEYS:
        with pytest.raises(AuditContentError) as error:
            content_free_details({key: "x"})
        assert error.value.code == "audit_detail_key_not_allowed"


@pytest.mark.parametrize(
    "value",
    [
        "I was diagnosed with asthma",  # prose: whitespace
        "x" * 201,  # over-long
        {"nested": "dict"},
        1.5,
        ["ok", "not ok"],
        "tab\tseparated",
        "",
    ],
)
def test_values_that_could_carry_content_are_rejected(value: object) -> None:
    with pytest.raises(AuditContentError) as error:
        content_free_details({"version_id": value})
    assert error.value.code == "audit_detail_value_not_identifier_shaped"
    assert "asthma" not in str(error.value)


def test_the_v1_memory_key_leak_is_now_refused() -> None:
    """``version_committed`` used to audit ``memory_key`` (``subject:predicate`` in the P engine), which survives
    deletion. The guard refuses it; ``test_every_literal_audit_dict_uses_only_allowed_keys`` keeps it out."""
    with pytest.raises(AuditContentError):
        content_free_details({"version_id": "v1", "memory_key": "user:health_condition", "ablation": "P"})


def test_every_literal_audit_dict_uses_only_allowed_keys() -> None:
    """Static rule: dicts passed as ``details=`` or into ``content_free_details`` use allowlisted keys only."""
    found, bad = 0, []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            dicts = [k.value for k in node.keywords if k.arg == "details"]
            if isinstance(node.func, ast.Name) and node.func.id == "content_free_details":
                dicts += node.args[:1]
            for literal in (d for d in dicts if isinstance(d, ast.Dict)):
                found += 1
                keys = {k.value for k in literal.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                bad += [f"{path.name}:{literal.lineno}:{key}" for key in sorted(keys - AUDIT_DETAIL_KEYS)]
    assert found >= 13, "every engine audit writer is a literal dict"
    assert bad == []


def test_log_redaction_replaces_only_content_bearing_values() -> None:
    event = {"event": "candidate_reinforced", "version_id": "v1", "content": "secret", "subject_key": "user"}
    assert redact_log_event(None, "info", event) == {
        "event": "candidate_reinforced",
        "version_id": "v1",
        "content": REDACTED,
        "subject_key": REDACTED,
    }


def test_every_jsonb_audit_payload_in_the_engine_passes_the_guard() -> None:
    """Static rule: a new audit writer that skips ``content_free_details`` fails here."""
    unguarded: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Jsonb"):
                continue
            argument = node.args[0] if node.args else None
            guarded = (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "content_free_details"
            )
            if not guarded and (relative, ast.unparse(argument) if argument else "") not in NON_AUDIT_JSONB:
                unguarded.append(f"{relative}:{node.lineno}")
    assert unguarded == []


def test_engine_log_calls_never_pass_content_keys() -> None:
    """Static rule over every structlog call in the engine (Channel 7)."""
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                offenders += [f"{path.name}:{node.lineno}:{k.arg}" for k in node.keywords if k.arg in CONTENT_KEYS]
    assert offenders == []
