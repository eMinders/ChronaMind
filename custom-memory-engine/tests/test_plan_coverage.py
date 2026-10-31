"""MA-017: every architecture element has an owning task (``scripts/plan_coverage.py``).

Elements are extracted from ``ARCHITECTURE_V2.md`` mechanically. The mutation tests add a fake element of
each class to a copy of the plan and assert the check fails **naming that element**; others break the
ownership rules. ``SYNC_LOG.md`` is read, never written.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "PLAN" / "V2"
_spec = importlib.util.spec_from_file_location("plan_coverage", ROOT / "scripts" / "plan_coverage.py")
assert _spec is not None and _spec.loader is not None
coverage = importlib.util.module_from_spec(_spec)
sys.modules["plan_coverage"] = coverage
_spec.loader.exec_module(coverage)

FILES = ("ARCHITECTURE_V2.md", "ROADMAP_V2.md", "COVERAGE.md", "INVARIANT_LEDGER.md", "SYNC_LOG.md")


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    for name in FILES:
        shutil.copy(PLAN / name, tmp_path / name)
    return tmp_path


def _edit(plan: Path, name: str, old: str, new: str) -> None:
    path = plan / name
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def _unowned(plan: Path) -> set[tuple[str, str]]:
    return {(f.element.kind, f.element.name) for f in coverage.resolve(plan)[2]}


def test_every_element_on_main_is_owned() -> None:
    elements, owners, findings = coverage.resolve(PLAN)
    assert [f.render() for f in findings] == []
    counts = {kind: sum(e.kind == kind for e in elements) for kind in coverage.CLASSES}
    assert counts == {"kind": 4, "table": 7, "invariant": 17, "channel": 4, "operation": 29, "migration": 9}
    assert all(owners[e] for e in elements)


def test_generated_matrix_is_current() -> None:
    assert (PLAN / "COVERAGE_MATRIX.md").read_text(encoding="utf-8") == coverage.render(PLAN)


def test_a_fake_invariant_row_is_reported_by_name(plan: Path) -> None:
    _edit(plan, "ARCHITECTURE_V2.md", "\n---\n\n## 12.", "\n| I18 | A new invariant. | none |\n\n---\n\n## 12.")
    assert _unowned(plan) == {("invariant", "I18")}
    rendered = [f.render() for f in coverage.resolve(plan)[2]]
    assert rendered == ["ARCHITECTURE_V2.md §11: invariant `I18`: no owner row"]


def test_a_fake_table_is_reported_by_name(plan: Path) -> None:
    _edit(plan, "ARCHITECTURE_V2.md", "METHOD       append-only", "LEDGER_X     new table\nMETHOD       append-only")
    assert _unowned(plan) == {("table", "LEDGER_X")}


def test_a_fake_operation_is_reported_by_name(plan: Path) -> None:
    _edit(
        plan,
        "ARCHITECTURE_V2.md",
        "| **Observe** | `stats`, `cost` |",
        "| **Observe** | `stats`, `cost`, `teleport` |",
    )
    assert _unowned(plan) == {("operation", "teleport")}


def test_a_fake_migration_is_reported_by_name(plan: Path) -> None:
    _edit(
        plan,
        "ARCHITECTURE_V2.md",
        "\n**Fairness requirement:**",
        "| M10 | A new step. | yes |\n\n**Fairness requirement:**",
    )
    assert _unowned(plan) == {("migration", "M10")}


def test_a_fake_channel_is_reported_by_name(plan: Path) -> None:
    _edit(plan, "ARCHITECTURE_V2.md", "| **`conflicts`** |", "| **`rumours`** | x | y |\n| **`conflicts`** |")
    assert _unowned(plan) == {("channel", "rumours")}


def test_an_owner_that_is_not_a_roadmap_task_is_reported(plan: Path) -> None:
    _edit(plan, "COVERAGE.md", "| `M8` | migration | `E-020` |", "| `M8` | migration | `E-999` |")
    findings = coverage.resolve(plan)[2]
    assert [(f.element.name, f.problem) for f in findings] == [("M8", "owner is not a ROADMAP_V2 task: E-999")]


def test_deferral_needs_a_reason_and_a_decision(plan: Path) -> None:
    _edit(plan, "COVERAGE.md", "| `M9` | migration | `E-011` | |", "| `M9` | migration | deferred | later |")
    assert _unowned(plan) == {("migration", "M9")}


def test_removing_an_invariant_owner_from_the_ledger_is_reported(plan: Path) -> None:
    text = (plan / "INVARIANT_LEDGER.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| I12 |"))
    _edit(plan, "INVARIANT_LEDGER.md", row, row.rsplit("|", 2)[0] + "| |")
    assert _unowned(plan) == {("invariant", "I12")}


def test_a_row_for_an_undeclared_element_is_reported(plan: Path) -> None:
    _edit(
        plan,
        "COVERAGE.md",
        "| `M9` | migration | `E-011` | |",
        "| `M9` | migration | `E-011` | |\n| `M11` | migration | `E-011` | |",
    )
    assert _unowned(plan) == {("migration", "M11")}


def test_m5_is_explicitly_deferred_and_m7_is_owned() -> None:
    _, owners, _ = coverage.resolve(PLAN)
    by_name = {e.name: o for e, o in owners.items()}
    assert by_name["M5"] == ["deferred"]
    assert by_name["M7"] == ["E-010"]


def test_sync_log_is_compared_not_modified(plan: Path) -> None:
    before = (PLAN / "SYNC_LOG.md").read_bytes()
    differences = coverage.sync_log_differences(PLAN, coverage.resolve(PLAN)[1])
    assert any("M5" in line for line in differences) and any("M7" in line for line in differences)
    assert (PLAN / "SYNC_LOG.md").read_bytes() == before
