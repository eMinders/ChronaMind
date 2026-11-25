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
    ("kind", "claim", "missing", "total", "outcome", "tests_valid", "expected"),
    [
        ("code", "Complete (#1)", (), 2, PASSED, True, "code-verified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(passed=3, skipped=1, ran=True), True, "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(passed=3, failed=1, ran=True), True, "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, audit.Outcome(skipped=4, ran=True), True, "claimed-unverified"),
        (
            "code",
            "Complete (#1)",
            (),
            2,
            audit.Outcome(passed=1, missing=("t.py",), ran=True),
            True,
            "claimed-unverified",
        ),
        ("code", "Complete (#1)", (), 2, audit.Outcome(), True, "claimed-unverified"),
        ("code", "Complete (#1)", (), 2, PASSED, False, "no-test"),
        ("code", "Complete (#1)", ("a.py",), 2, PASSED, True, "claimed-unverified"),
        ("code", "Open (#2)", ("a.py",), 2, audit.Outcome(), True, "not-started"),
        ("doc", "Done", (), 1, audit.Outcome(), True, "doc-only"),
        ("doc", "Done", ("m.md",), 1, audit.Outcome(), True, "claimed-unverified"),
        ("config", "Complete (#3)", (), 1, audit.Outcome(), True, "claimed-unverified"),
        ("none", "Blocked", ("a",), 1, audit.Outcome(), True, "not-started"),
        ("none", "Blocked", (), 1, audit.Outcome(), True, "claimed-unverified"),
    ],
)
def test_classification_rule(
    kind: str,
    claim: str,
    missing: tuple[str, ...],
    total: int,
    outcome: object,
    tests_valid: bool,
    expected: str,
) -> None:
    assert audit.classify(kind, claim, missing, total, outcome, tests_valid=tests_valid) == expected


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


def test_ast_path_symbol_resolution_supports_files_functions_async_and_methods(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    sample = tmp_path / "tests" / "test_sample.py"
    sample.write_text(
        "def helper():\n    pass\n\n"
        "def test_sync():\n    pass\n\n"
        "async def test_async():\n    pass\n\n"
        "class TestSuite:\n    def test_method(self):\n        pass\n",
        encoding="utf-8",
    )
    assert audit.resolve_test_citation("tests/test_sample.py", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::test_sync", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::test_sync[case_a]", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::test_async", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::test_method", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::TestSuite::test_method", root=tmp_path) is True
    assert audit.resolve_test_citation("tests/test_sample.py::helper", root=tmp_path) is False
    assert audit.resolve_test_citation("tests/test_sample.py::test_missing", root=tmp_path) is False
    assert audit.resolve_test_citation("tests/no_such.py::test_sync", root=tmp_path) is False


def test_audit_classifies_all_five_classes_including_no_test(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs" / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (tmp_path / "tests" / "test_mod.py").write_text(
        "def test_pass():\n    pass\n\ndef test_skip():\n    pass\n",
        encoding="utf-8",
    )
    xml = tmp_path / "junit.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_mod" name="test_pass"/>'
        '<testcase classname="tests.test_mod" name="test_skip"><skipped message="db"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    junit = audit.read_junit(xml)

    claims = {
        "RL-001": "Complete (#1)",
        "RL-002": "Done",
        "RL-003": "Complete (#3)",
        "RL-004": "Complete (#4)",
        "RL-005": "Complete (#5)",
        "RL-006": "Blocked",
    }
    registry = {
        "RL-001": {"kind": "code", "files": ["src/mod.py"], "tests": ["tests/test_mod.py::test_pass"]},
        "RL-002": {"kind": "doc", "files": ["docs/spec.md"]},
        "RL-003": {"kind": "code", "files": ["src/mod.py"], "tests": ["tests/test_mod.py::test_skip"]},
        "RL-004": {"kind": "code", "files": ["src/mod.py"], "tests": []},
        "RL-005": {"kind": "code", "files": ["src/mod.py"], "tests": ["tests/test_mod.py::test_typo"]},
        "RL-006": {"kind": "none", "files": ["src/future.py"]},
    }
    by_task = {r.task: r for r in audit.audit(claims, registry, junit, root=tmp_path)}
    assert by_task["RL-001"].classification == "code-verified"
    assert by_task["RL-002"].classification == "doc-only"
    assert by_task["RL-003"].classification == "claimed-unverified"
    assert by_task["RL-004"].classification == "no-test"
    assert by_task["RL-005"].classification == "no-test"
    assert by_task["RL-006"].classification == "not-started"


def test_check_mode_fails_on_unresolved_test_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {
        "kind": "code",
        "files": ["README.md"],
        "tests": ["tests/test_audit_status.py::test_nonexistent_symbol"],
    }
    monkeypatch.setattr(audit, "REGISTRY", _registry_with(tmp_path, entry))
    assert audit.main(["--check"]) == 1
    expected_msg = "missing_evidence_symbol:RL-001:tests/test_audit_status.py::test_nonexistent_symbol"
    assert expected_msg in capsys.readouterr().out


def test_junit_node_level_matching_avoids_double_counting_parametrized_tests(tmp_path: Path) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_a" name="test_param[0]"/>'
        '<testcase classname="tests.test_a" name="test_param[1]"/>'
        '<testcase classname="tests.test_a.TestGrp" name="test_m"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    junit = audit.read_junit(xml)
    mod_out = audit.outcome_for(("tests/test_a.py",), junit)
    node_out = audit.outcome_for(("tests/test_a.py::test_param",), junit)
    cls_node_out = audit.outcome_for(("tests/test_a.py::TestGrp::test_m",), junit)
    assert (mod_out.passed, mod_out.failed, mod_out.skipped) == (3, 0, 0)
    assert (node_out.passed, node_out.failed, node_out.skipped) == (2, 0, 0)
    assert (cls_node_out.passed, cls_node_out.failed, cls_node_out.skipped) == (1, 0, 0)


def test_main_with_junit_records_timestamp_and_revision_in_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_audit_status" name="test_ok"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    out_file = tmp_path / "STATUS_AUDIT.md"
    monkeypatch.setattr(audit, "_git_sha", lambda: "abc1234")
    monkeypatch.setattr(audit, "environment", lambda: [("Database (`CME_TEST_DATABASE_URL`)", "set, reachable")])
    code = audit.main(["--junit", str(xml), "--out", str(out_file), "--timestamp", "2026-11-25T19:30:00Z"])
    assert code == 0
    rendered = out_file.read_text(encoding="utf-8")
    assert "- Source commit: `abc1234`" in rendered
    assert "- Generated at (UTC): `2026-11-25T19:30:00Z`" in rendered
    assert "| `no-test` | 0 |" in rendered


def test_pytest_crash_and_malformed_xml_leave_output_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "STATUS_AUDIT.md"
    sentinel = "DO NOT OVERWRITE THIS EXISTING AUDIT REPORT\n"
    out_file.write_text(sentinel, encoding="utf-8")

    # Case 1: subprocess returns pytest crash exit code 2
    class _CrashedProc:
        returncode = 2
        stdout = ""
        stderr = "INTERNALERROR> SyntaxError"

    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: _CrashedProc())
    assert audit.main(["--out", str(out_file)]) == 1
    assert out_file.read_text(encoding="utf-8") == sentinel
    assert "pytest crashed (exit 2)" in capsys.readouterr().err

    # Case 2: subprocess exits 0 but fails to write junit.xml
    class _NoXmlProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: _NoXmlProc())
    assert audit.main(["--out", str(out_file)]) == 1
    assert out_file.read_text(encoding="utf-8") == sentinel
    assert "did not produce" in capsys.readouterr().err

    # Case 3: --junit given malformed XML
    bad_xml = tmp_path / "bad.xml"
    bad_xml.write_text("<testsuites><unclosed", encoding="utf-8")
    assert audit.main(["--junit", str(bad_xml), "--out", str(out_file)]) == 1
    assert out_file.read_text(encoding="utf-8") == sentinel
