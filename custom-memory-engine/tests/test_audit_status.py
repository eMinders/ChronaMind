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


def _registry_with(tmp_path: Path, entry: dict) -> Path:
    """A copy of the real registry with RL-001 replaced, written to ``tmp_path``."""
    data = json.loads(audit.REGISTRY.read_text(encoding="utf-8"))
    data["tasks"]["RL-001"] = entry
    path = tmp_path / "status_evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("field", ["files", "tests"])
def test_a_missing_cited_evidence_path_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    entry = {"kind": "code", "files": ["README.md"], "tests": ["tests/test_audit_status.py"]}
    entry[field] = ["does/not/exist.py"]
    monkeypatch.setattr(audit, "REGISTRY", _registry_with(tmp_path, entry))
    assert audit.main(["--check"]) == 1
    assert "missing_evidence_path:RL-001:does/not/exist.py" in capsys.readouterr().out


def test_expected_deliverables_of_a_not_started_task_may_be_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = {"kind": "none", "files": ["src/not_built_yet.py"]}
    monkeypatch.setattr(audit, "REGISTRY", _registry_with(tmp_path, entry))
    assert audit.main(["--check"]) == 0


def test_removed_task_and_unknown_id_exit_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads(audit.REGISTRY.read_text(encoding="utf-8"))
    del data["tasks"]["RL-001"]
    data["tasks"]["RL-999"] = {"kind": "none", "files": []}
    path = tmp_path / "status_evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(audit, "REGISTRY", path)
    assert audit.main(["--check"]) == 1


def test_report_records_its_generation_environment(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "model.bin").write_text("x", encoding="utf-8")
    rows = dict(audit.environment({"CME_BASELINE_TEST_ASSETS": str(assets)}, root=tmp_path))
    assert "not set" in rows["Database (`CME_TEST_DATABASE_URL`)"]
    assert "not configured" in rows["FalkorDB"]
    assert rows["Prepared model assets (`CME_BASELINE_TEST_ASSETS`)"] == "present"
    assert "absent" in rows["Benchmark corpus `longmemeval`"]


def test_rendering_is_deterministic_for_the_same_inputs() -> None:
    claims = audit.parse_task_graph(audit.TASK_GRAPH.read_text(encoding="utf-8"))
    registry = audit.load_registry(audit.REGISTRY)
    junit = {"tests.test_audit_status": audit.Outcome(passed=1, ran=True)}
    env_rows = [("Database (`CME_TEST_DATABASE_URL`)", "set, reachable")]
    first = audit.render(audit.audit(claims, registry, junit), True, env_rows)
    second = audit.render(audit.audit(claims, registry, junit), True, env_rows)
    assert first == second
    assert "## Generation environment" in first
