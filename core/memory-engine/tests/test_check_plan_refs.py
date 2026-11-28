"""Tests for scripts/check_plan_refs.py (MA-018)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_plan_refs", ROOT / "scripts" / "check_plan_refs.py")
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_plan_refs"] = checker
_spec.loader.exec_module(checker)

ROADMAP = """# Roadmap
| ID | Task |
|---|---|
| `E-001` | audit |
| `MA-018` | checker |

**G0.** baseline done.
"""
DECISIONS = """# Decisions
## V2-D01 - axes
| V2-D14 | open one |
`V2-D15` is **resolved** by `V2-D01`.
"""
ARCH = """# Arch
## M1 - columns
"""


def _write(directory: Path, **files: str) -> Path:
    for name, text in files.items():
        (directory / f"{name}.md").write_text(text, encoding="utf-8")
    return directory


def _kinds(directory: Path) -> set[tuple[str, str]]:
    return {(f.kind, f.detail) for f in checker.run_checks(directory)}


def _base(tmp_path: Path, extra: str = "") -> Path:
    return _write(tmp_path, ROADMAP_V2=ROADMAP + extra, DECISIONS_V2=DECISIONS, ARCHITECTURE_V2=ARCH)


def test_clean_documents_have_no_findings(tmp_path: Path) -> None:
    assert _kinds(_base(tmp_path, "See `E-001`, G0, V2-D01, V2-D15 and M1.\n")) == set()


@pytest.mark.parametrize(
    ("reference", "kind", "detail"),
    [
        ("E-999", "undefined_task", "E-999"),
        ("UM-777", "undefined_task", "UM-777"),
        ("G9", "undefined_gate", "G9"),
        ("G4b", "undefined_gate", "G4b"),
        ("V2-D99", "undefined_decision", "V2-D99"),
        ("M7", "undefined_migration", "M7"),
    ],
)
def test_undefined_reference_is_reported(tmp_path: Path, reference: str, kind: str, detail: str) -> None:
    assert (kind, detail) in _kinds(_base(tmp_path, f"Depends on {reference}.\n"))


def test_legacy_v1_identifiers_are_not_references(tmp_path: Path) -> None:
    assert _kinds(_base(tmp_path, "Legacy RL-070, ablation A8, metric UM5 and scenario S12.\n")) == set()


def test_fenced_blocks_are_ignored(tmp_path: Path) -> None:
    fenced = "```mermaid\nG1 --> G1b\n```\n"
    assert _kinds(_base(tmp_path, fenced)) == set()


def test_duplicate_heading_is_reported(tmp_path: Path) -> None:
    kinds = _kinds(_base(tmp_path, "## Scope\n\ntext\n\n## Scope\n"))
    assert ("duplicate_heading", "## Scope") in kinds


def test_broken_relative_link_is_reported(tmp_path: Path) -> None:
    kinds = _kinds(_base(tmp_path, "[x](MISSING.md) and [ok](ROADMAP_V2.md) and [web](https://example.com)\n"))
    assert ("broken_link", "MISSING.md") in kinds
    assert ("broken_link", "ROADMAP_V2.md") not in kinds


def test_stale_formula_flagged_unless_marked_superseded(tmp_path: Path) -> None:
    bad = "Admission uses 1 \u2212 CP_upper(k, o) here.\n"
    good = "The r2 rule 1 \u2212 CP_upper(k, o) is retained as a positive control.\n"
    assert any(k == "stale_formula" for k, _ in _kinds(_base(tmp_path, bad)))
    other = tmp_path / "other"
    other.mkdir()
    assert not any(k == "stale_formula" for k, _ in _kinds(_base(other, good)))


def test_empty_directory_is_a_finding(tmp_path: Path) -> None:
    assert any(k == "no_documents" for k, _ in _kinds(tmp_path))


def test_committed_v2_plan_is_consistent() -> None:
    findings = checker.run_checks(ROOT / "PLAN" / "V2")
    assert [f.render() for f in findings] == []


@pytest.mark.parametrize(
    "line",
    [
        # Review finding B1: current-use statements that merely contain "not" or "correctly".
        "Admission rule (current, normative): it does not change; confidence_lower = 1 \u2212 CP_upper(k, o).\n",
        "The bound is correctly 1 \u2212 CP_upper(k, o) for every artifact.\n",
        "Admission treats the bound 1 \u2212 CP_upper(k, o) as final.\n",
    ],
)
def test_current_use_of_the_superseded_formula_is_not_excused_by_ordinary_words(tmp_path: Path, line: str) -> None:
    assert any(k == "stale_formula" for k, _ in _kinds(_base(tmp_path, line)))


@pytest.mark.parametrize(
    "line",
    [
        "**Correction (r3):** the r1/r2 form `1 \u2212 CP_upper(k, o)` treated `u` as successes and\n",
        "| `SD-006` | `X-ignore-undetermined` ablation: the r2 rule (`1 \u2212 CP_upper(k, o)`) |\n",
        "`1 \u2212 CP_upper(k, o)`, which treats every undetermined item as a success. That is not a\n",
        "| **r3:** `1 \u2212 CP_upper(k, o)` used `u` as success | admission bound | **CRITICAL FIX** |\n",
    ],
)
def test_marked_uses_of_the_superseded_formula_pass(tmp_path: Path, line: str) -> None:
    assert not any(k == "stale_formula" for k, _ in _kinds(_base(tmp_path, line)))


def test_every_finding_kind_reports_file_and_line(tmp_path: Path) -> None:
    extra = "\nSee E-999.\n\n## Scope\n\n## Scope\n\n[x](MISSING.md)\n\nUses 1 \u2212 CP_upper(k, o).\n"
    findings = checker.run_checks(_base(tmp_path, extra))
    by_kind = {f.kind: f for f in findings}
    assert set(by_kind) == {"undefined_task", "duplicate_heading", "broken_link", "stale_formula"}
    lines = ROADMAP.count("\n") + 1  # first line of ``extra`` in ROADMAP_V2.md
    assert by_kind["undefined_task"].line == lines + 1
    assert by_kind["duplicate_heading"].line == lines + 5
    assert by_kind["broken_link"].line == lines + 7
    assert by_kind["stale_formula"].line == lines + 9
    for finding in findings:
        assert finding.render().startswith(f"ROADMAP_V2.md:{finding.line}: {finding.kind}: ")


@pytest.mark.parametrize(
    "alias_row",
    [
        "| `SD-001` | metrics module (was `C-013`) |\n",
        "| `UM-010` | user model (`C-013` renamed) |\n",
        "| `E-037` | deletion *(moved from `C-013`/W6)* |\n",
        "| `E-037` | deletion, formerly `C-013` |\n",
        "Moves: `C-013` \u2192 `E-001` in W3.\n",
    ],
)
def test_retired_aliases_are_defined_only_by_an_explicit_alias_record(tmp_path: Path, alias_row: str) -> None:
    reference = "Later text cites C-013.\n"
    assert ("undefined_task", "C-013") in _kinds(_base(tmp_path, reference))
    other = tmp_path / "aliased"
    other.mkdir()
    assert _kinds(_base(other, alias_row + reference)) == set()


API = """# API
## 3. Operations
| # | Class | Operation | Tier B | Tier C | Admin | Conditions |
|---|---|---|:-:|:-:|:-:|---|
| 1 | Read | `query` | ✓ | ✓ | ✓ | — |
| 2 | Adjudicate | `dissent` | — | ✓ | ✓ | — |
## 6. Selectors
| Selector | Meaning |
|---|---|
| `subtree(path)` | below |
## 7.1 Names
| Name | Kind | Meaning |
|---|---|---|
| `move` | composition | copy + retract_own |
| `ground` | internal function | policy |
| `retract` | retired | use retract_own |
"""


def _with_api(tmp_path: Path, roadmap_extra: str) -> Path:
    return _write(
        tmp_path, ROADMAP_V2=ROADMAP + roadmap_extra, DECISIONS_V2=DECISIONS, ARCHITECTURE_V2=ARCH, API_V2=API
    )


@pytest.mark.parametrize(
    "line", ["Calls `query(request)`.", "`dissent(id)` by the user.", "`subtree(p)`", "`move(x)`", "`ground()`"]
)
def test_declared_operations_selectors_and_names_resolve(tmp_path: Path, line: str) -> None:
    assert _kinds(_with_api(tmp_path, line + "\n")) == set()


def test_an_undeclared_operation_name_in_call_form_is_reported(tmp_path: Path) -> None:
    """The MA-012 criterion-5 mutation: `UM-012` used to say `refuted(reason=...)`."""
    assert _kinds(_with_api(tmp_path, "rejection → `refuted(reason='user_dissent')`\n")) == {
        ("undefined_operation", "refuted")
    }


def test_a_retired_operation_is_reported_unless_the_line_marks_it(tmp_path: Path) -> None:
    assert _kinds(_with_api(tmp_path, "Tier B calls `retract` on its own work.\n")) == {
        ("retired_operation", "retract")
    }
    assert _kinds(_with_api(tmp_path, "`retract` is retired; it never existed.\n")) == set()


def test_operation_check_is_skipped_without_api_v2(tmp_path: Path) -> None:
    assert (
        _kinds(_write(tmp_path, ROADMAP_V2=ROADMAP + "`refuted(x)`\n", DECISIONS_V2=DECISIONS, ARCHITECTURE_V2=ARCH))
        == set()
    )
