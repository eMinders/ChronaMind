"""Tests for scripts/audit_status.py (E-001)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_status", ROOT / "scripts" / "audit_status.py")
assert _spec is not None and _spec.loader is not None
audit = importlib.util.module_from_spec(_spec)
sys.modules["audit_status"] = audit
_spec.loader.exec_module(audit)

PASSED = audit.Outcome(passed=3, ran=True)


@pytest.mark.parametrize(
    ("kind", "claim", "missing", "total", "outcome", "expected"),
    [
        ("code", "Complete (#1)", (), 2, PASSED, "code-verified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(passed=3, skipped=1, ran=True), "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(passed=3, failed=1, ran=True), "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(skipped=4, ran=True), "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(passed=1, missing=("t.py",), ran=True), "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(), "claimed-unverified"),
        ("code", "Complete (#1)", ("a.py",), 2, PASSED, "claimed-unverified"),
        ("code", "Open (#2)", ("a.py",), 2, audit.Outcome(), "not-started"),
        ("doc", "Done", (), 1, audit.Outcome(), "doc-only"),
        ("doc", "Done", ("m.md",), 1, audit.Outcome(), "claimed-unverified"),
        ("config", "Complete (#3)", (), 1, audit.Outcome(), "claimed-unverified"),
        ("none", "Blocked", ("a",), 1, audit.Outcome(), "not-started"),
        ("none", "Blocked", (), 1, audit.Outcome(), "claimed-unverified"),
    ],
)
def test_classification_rule(
    kind: str, claim: str, missing: tuple[str, ...], total: int, outcome: object, expected: str
) -> None:
    assert audit.classify(kind, claim, missing, total, outcome) == expected


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown_kind"):
        audit.classify("magic", "Done", (), 0, audit.Outcome())


@pytest.mark.parametrize(
    ("claim", "group"),
    [
        ("`Complete (#9)`", "done"),
        ("Done", "done"),
        ("Implemented; PR open (#45)", "partial"),
        ("Partial: closure (#46)", "partial"),
        ("Open (#47)", "open"),
        ("Blocked", "open"),
        ("Gated (Skip by default)", "open"),
    ],
)
def test_claim_group(claim: str, group: str) -> None:
    assert audit.claim_group(claim) == group


def test_task_graph_rows_are_parsed() -> None:
    text = "| ID | Status |\n|---|---|\n| `RL-002` | T | o | S | `Complete (#9)` | — |\nprose RL-999\n"
    assert audit.parse_task_graph(text) == {"RL-002": "`Complete (#9)`"}


def test_unclassified_and_stale_registry_entries_are_reported() -> None:
    claims = {"RL-001": "Done", "RL-002": "Done"}
    registry = {"RL-001": {"kind": "doc"}, "RL-009": {"kind": "bogus"}}
    problems = audit.check_registry(claims, registry)
    assert "unclassified_task:RL-002" in problems
    assert "registry_entry_without_task:RL-009" in problems
    assert "unknown_kind:RL-009:bogus" in problems


def test_junit_results_are_aggregated_per_named_module(tmp_path: Path) -> None:
    xml = tmp_path / "j.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_a" name="t1"/>'
        '<testcase classname="tests.test_a.TestX" name="t2"><skipped message="db"/></testcase>'
        '<testcase classname="tests.test_b" name="t3"><failure message="x"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    junit = audit.read_junit(xml)
    outcome = audit.outcome_for(("tests/test_a.py", "tests/test_b.py", "tests/test_c.py"), junit)
    assert (outcome.passed, outcome.failed, outcome.skipped) == (1, 1, 1)
    assert outcome.missing == ("tests/test_c.py",)


def test_audit_marks_done_claim_without_evidence_as_discrepancy() -> None:
    claims = {"RL-777": "Complete (#7)"}
    registry = {"RL-777": {"kind": "code", "files": ["no/such/file.py"], "tests": ["tests/test_nothing.py"]}}
    results = audit.audit(claims, registry, {})
    assert results[0].classification == "claimed-unverified"
    assert any("RL-777" in line for line in audit.discrepancies(results))


def test_committed_registry_covers_every_task_graph_row() -> None:
    claims = audit.parse_task_graph(audit.TASK_GRAPH.read_text(encoding="utf-8"))
    registry = json.loads(audit.REGISTRY.read_text(encoding="utf-8"))["tasks"]
    assert claims
    assert audit.check_registry(claims, registry) == []


def test_reconciliation_covers_every_transition_audit_row() -> None:
    claims = audit.parse_task_graph(audit.TASK_GRAPH.read_text(encoding="utf-8"))
    registry = json.loads(audit.REGISTRY.read_text(encoding="utf-8"))["tasks"]
    text = "\n".join(audit.reconciliation(audit.audit(claims, registry, {})))
    for marker in ("PROJECT_STATUS", "PROJECT_ROADMAP", "SCHEDULE", "TASK_GRAPH", "Issue #12", "REFACTORING"):
        assert marker in text


def test_every_audited_row_cites_a_test_or_says_unverified() -> None:
    claims = audit.parse_task_graph(audit.TASK_GRAPH.read_text(encoding="utf-8"))
    registry = json.loads(audit.REGISTRY.read_text(encoding="utf-8"))["tasks"]
    for result in audit.audit(claims, registry, {}):
        cell = audit._cell(result)
        assert "tests:" in cell or cell.startswith("UNVERIFIED"), result.task
