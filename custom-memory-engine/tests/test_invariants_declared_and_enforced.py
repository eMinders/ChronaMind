"""Declared invariants must be enforced or explicitly marked as not (E-004).

The v1 situation this guards against: ``Invariant 7`` (row-level security) was declared in the
specification while ``src/custom_memory_engine/security/`` did not exist, and nothing failed. The
declarations are parsed from their source documents, so an invariant cannot be dropped from the ledger
by editing only the ledger.
"""

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "PLAN" / "V2" / "INVARIANT_LEDGER.md"
SCHEMA_README = ROOT / "src" / "custom_memory_engine" / "schema" / "README.md"
ARCHITECTURE = ROOT / "PLAN" / "V2" / "ARCHITECTURE_V2.md"
ROADMAP = ROOT / "PLAN" / "V2" / "ROADMAP_V2.md"
TASK_GRAPH = ROOT / "PLAN" / "TASK_GRAPH.md"

STATUSES = ("ENFORCED", "PARTIAL", "UNENFORCED", "PLANNED")
V1_DECLARATION = re.compile(r"^\|\s*\*\*(Invariant \d+)\*\*\s*\|", re.M)
V2_SECTION = re.compile(r"^## 11\. Invariants.*?(?=^## 12\.)", re.M | re.S)
V2_DECLARATION = re.compile(r"^\|\s*(I\d+′?)\s*\|", re.M)
TASK_ID = re.compile(r"`((?:RL|E|MA|C|A|R|SD|UM)-\d{3})`")
CODE_SPAN = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class Row:
    """One ledger row."""

    invariant: str
    status: str
    sites: tuple[str, ...]
    tests: tuple[str, ...]
    gap: str
    owners: tuple[str, ...]

    @property
    def base(self) -> str:
        """The declared invariant this row belongs to (``Invariant 7 / policies`` -> ``Invariant 7``)."""
        return self.invariant.split(" / ")[0].strip()


def parse_ledger(text: str) -> list[Row]:
    """Parse every invariant row of the ledger tables."""
    rows: list[Row] = []
    for line in text.splitlines():
        if not re.match(r"^\|\s*(Invariant \d+|I\d+′?)\b", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            raise AssertionError(f"ledger_row_must_have_six_cells: {line[:60]}")
        invariant, status, sites, tests, gap, owners = cells
        rows.append(
            Row(
                invariant,
                status.strip("`"),
                tuple(CODE_SPAN.findall(sites)),
                tuple(CODE_SPAN.findall(tests)),
                gap,
                tuple(CODE_SPAN.findall(owners)),
            )
        )
    return rows


def declared_invariants() -> set[str]:
    """Invariants declared by their source documents, never by the ledger."""
    v1 = set(V1_DECLARATION.findall(SCHEMA_README.read_text(encoding="utf-8")))
    section = V2_SECTION.search(ARCHITECTURE.read_text(encoding="utf-8"))
    assert section is not None, "architecture_section_11_not_found"
    return v1 | set(V2_DECLARATION.findall(section.group(0)))


def defined_tasks() -> set[str]:
    """Task ids a ledger row may name as owner."""
    roadmap = ROADMAP.read_text(encoding="utf-8")
    graph = TASK_GRAPH.read_text(encoding="utf-8")
    return set(TASK_ID.findall(roadmap)) | set(TASK_ID.findall(graph))


def _split(citation: str) -> tuple[str, str]:
    path, _, symbol = citation.partition("::")
    return path, symbol


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def unresolved(row: Row, root: Path = ROOT) -> list[str]:
    """Citations that do not resolve to a real file and symbol."""
    problems: list[str] = []
    for site in row.sites:
        path, symbol = _split(site)
        target = root / path
        if not target.is_file():
            problems.append(f"site_file_missing:{site}")
        elif symbol and symbol not in target.read_text(encoding="utf-8"):
            problems.append(f"site_symbol_missing:{site}")
    for test in row.tests:
        path, symbol = _split(test)
        target = root / path
        if not target.is_file():
            problems.append(f"test_file_missing:{test}")
        elif not symbol:
            problems.append(f"test_symbol_required:{test}")
        elif target.suffix == ".py" and (not symbol.startswith("test") or symbol not in _test_names(target)):
            problems.append(f"test_not_defined:{test}")
    return problems


def validate(rows: Iterable[Row], declared: set[str], tasks: set[str], root: Path = ROOT) -> list[str]:
    """Return every way the ledger fails to account for the declared invariants."""
    rows = list(rows)
    problems = [f"declared_without_ledger_row:{i}" for i in sorted(declared - {r.base for r in rows})]
    for row in rows:
        label = row.invariant
        if row.base not in declared:
            problems.append(f"ledger_row_for_undeclared_invariant:{label}")
        if row.status not in STATUSES:
            problems.append(f"unknown_status:{label}:{row.status}")
            continue
        problems += [f"{label}:{p}" for p in unresolved(row, root)]
        if row.status in ("ENFORCED", "PARTIAL") and not (row.sites and row.tests):
            problems.append(f"{label}:{row.status.lower()}_requires_site_and_test")
        if row.status in ("PARTIAL", "UNENFORCED", "PLANNED"):
            if not row.gap:
                problems.append(f"{label}:gap_required")
            if not row.owners:
                problems.append(f"{label}:owner_required")
        if row.status == "ENFORCED" and (row.gap or row.owners):
            problems.append(f"{label}:enforced_row_must_not_carry_a_gap")
        problems += [f"{label}:undefined_owner:{o}" for o in row.owners if o not in tasks]
    return problems


@pytest.fixture(scope="module")
def rows() -> list[Row]:
    return parse_ledger(LEDGER.read_text(encoding="utf-8"))


def test_declarations_are_parsed_from_their_sources() -> None:
    declared = declared_invariants()
    assert {f"Invariant {n}" for n in range(1, 11)} <= declared
    assert {f"I{n}" for n in range(2, 18)} <= declared
    assert "I1′" in declared


def test_every_declared_invariant_is_enforced_or_explicitly_marked(rows: list[Row]) -> None:
    assert validate(rows, declared_invariants(), defined_tasks()) == []


def test_v1_gaps_closed_in_r10_are_enforced_with_tests(rows: list[Row]) -> None:
    """RL-072, RL-092 and RL-090 closed the last v1 gaps (r10, V2-D55): no v1 row may fall back silently."""
    by_name = {r.invariant: r for r in rows}
    for name in (
        "Invariant 8 / content-free audit",
        "Invariant 9 / end-to-end replay",
        "Invariant 10 / T1-T5 harness",
    ):
        assert by_name[name].status == "ENFORCED" and by_name[name].tests, name
    assert all(r.status == "ENFORCED" for r in rows if r.base.startswith("Invariant ")), "every v1 row is enforced"


def test_gaps_closed_in_r7_are_enforced_with_tests(rows: list[Row]) -> None:
    """#47 (restricted runtime role) and #46 (seven-channel harness probes) closed on main in r7."""
    by_name = {r.invariant: r for r in rows}
    for name in ("Invariant 7 / runtime role", "Invariant 8 / scanner channels 3, 5-7"):
        assert by_name[name].status == "ENFORCED"
        assert by_name[name].tests


def test_r7_primitives_are_partial_until_integrated_by_w1(rows: list[Row]) -> None:
    status = {r.invariant: r.status for r in rows}
    assert status["I1′"] == status["I7"] == status["I17"] == "PARTIAL"


def test_removing_a_ledger_row_is_detected(rows: list[Row]) -> None:
    mutated = [r for r in rows if r.base != "Invariant 7"]
    assert "declared_without_ledger_row:Invariant 7" in validate(mutated, declared_invariants(), defined_tasks())


def test_removing_an_enforcement_site_is_detected(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "Invariant 1")
    broken = replace(target, sites=("src/custom_memory_engine/schema/001_full_schema.sql::no_such_trigger",))
    mutated = [broken if r is target else r for r in rows]
    problems = validate(mutated, declared_invariants(), defined_tasks())
    assert any("site_symbol_missing" in p and p.startswith("Invariant 1:") for p in problems)


def test_citing_a_test_that_does_not_exist_is_detected(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "Invariant 2")
    broken = replace(target, tests=("tests/integration/test_full_schema.py::test_this_was_deleted",))
    mutated = [broken if r is target else r for r in rows]
    assert any("test_not_defined" in p for p in validate(mutated, declared_invariants(), defined_tasks()))


def test_an_unenforced_row_must_say_what_is_missing_and_who_owns_it(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "Invariant 8 / content-free audit")
    mutated = [replace(target, status="UNENFORCED", gap="", owners=()) if r is target else r for r in rows]
    problems = validate(mutated, declared_invariants(), defined_tasks())
    assert "Invariant 8 / content-free audit:gap_required" in problems
    assert "Invariant 8 / content-free audit:owner_required" in problems


def test_an_enforced_claim_without_a_test_is_detected(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "Invariant 4")
    mutated = [replace(target, tests=()) if r is target else r for r in rows]
    assert "Invariant 4:enforced_requires_site_and_test" in validate(mutated, declared_invariants(), defined_tasks())


def test_an_owner_must_be_a_real_task(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "I8")
    mutated = [replace(target, owners=("E-999",)) if r is target else r for r in rows]
    assert "I8:undefined_owner:E-999" in validate(mutated, declared_invariants(), defined_tasks())


def test_a_ledger_row_for_an_undeclared_invariant_is_detected(rows: list[Row]) -> None:
    phantom = Row("Invariant 99", "PLANNED", (), (), "x", ("E-010",))
    assert "ledger_row_for_undeclared_invariant:Invariant 99" in validate(
        [*rows, phantom], declared_invariants(), defined_tasks()
    )


def test_citing_a_helper_instead_of_a_test_is_detected(rows: list[Row]) -> None:
    target = next(r for r in rows if r.invariant == "Invariant 2")
    broken = replace(target, tests=("tests/test_invariants_declared_and_enforced.py::parse_ledger",))
    mutated = [broken if r is target else r for r in rows]
    assert any("test_not_defined" in p for p in validate(mutated, declared_invariants(), defined_tasks()))
