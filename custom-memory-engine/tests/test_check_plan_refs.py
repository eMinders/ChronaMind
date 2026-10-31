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
