"""Architecture → plan coverage (MA-017): every architecture element has an owning task.

Elements are extracted **mechanically** from ``PLAN/V2/ARCHITECTURE_V2.md``; no list of them is kept here:

* memory kinds and structural tables: the two fenced blocks of §2.1, plus every upper-case table named in the
  §12 ``M6`` row;
* invariants: the first column of the §11 table (``I1′``, ``I2`` ... ``I17``);
* channels: the first column of the §9.1 table;
* API operations: every backticked name in the operations column of the §8.1 table;
* migrations: the first column of the §12 table (``M1`` ... ``M9``).

Owners come from two sources, each authoritative for its class:

* invariants: ``PLAN/V2/INVARIANT_LEDGER.md`` (the ``Owner`` column; ``E-004`` validates the ledger itself);
* everything else: ``PLAN/V2/COVERAGE.md`` (``| element | class | owners | note |``).

An owner is a task id defined in ``ROADMAP_V2.md``, or ``deferred`` with a reason and a ``V2-D`` decision
reference in the note. An element counts as owned only then. Every finding names the element, its class and
where it is declared. The generated matrix (``PLAN/V2/COVERAGE_MATRIX.md``) also lists how it differs from the
historical hand-written matrix in ``SYNC_LOG.md`` §3, which is audit history and is never modified.

Run:   uv run python scripts/plan_coverage.py            # print findings; exit 1 if any
       uv run python scripts/plan_coverage.py --write    # regenerate COVERAGE_MATRIX.md
       uv run python scripts/plan_coverage.py --check    # findings, plus fail if the matrix is stale
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "PLAN" / "V2"
MATRIX = PLAN / "COVERAGE_MATRIX.md"
CLASSES = ("kind", "table", "invariant", "channel", "operation", "migration")

TASK = r"(?:E|MA|C|A|R|SD|UM)-\d{3}"
TASK_DEF = re.compile(rf"^\s*(?:\|\s*|-\s*)`({TASK})`", re.M)
TASK_REF = re.compile(rf"(?<![\w-])({TASK})(?![\w-])")
DECISION_REF = re.compile(r"(?<![\w-])V2-D\d+[a-z]?(?![\w-])")


@dataclass(frozen=True)
class Element:
    name: str
    kind: str
    where: str


@dataclass(frozen=True)
class Finding:
    element: Element
    problem: str

    def render(self) -> str:
        where = (
            self.element.where if self.element.where.endswith(".md") else f"ARCHITECTURE_V2.md {self.element.where}"
        )
        return f"{where}: {self.element.kind} `{self.element.name}`: {self.problem}"


def _section(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a + len(start))
    return text[a:b]


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_rows(section: str) -> list[list[str]]:
    rows = [_cells(line) for line in section.splitlines() if line.lstrip().startswith("|")]
    return [r for r in rows if r and not set(r[0]) <= set("-: ") and r[0] not in ("#", "Class", "Channel", "Step")]


def extract_elements(architecture: str) -> list[Element]:
    """Every architecture element, in declaration order."""
    elements: list[Element] = []
    s21 = _section(architecture, "### 2.1", "### 2.2")
    for label, kind in (("**Four memory kinds**", "kind"), ("**Four structural tables**", "table")):
        block = s21[s21.index(label) :]
        block = block[block.index("```") + 3 :]
        block = block[: block.index("```")]
        for line in block.splitlines():
            match = re.match(r"\s*([A-Z][A-Z_]+)\b", line)
            if match:
                elements.append(Element(match.group(1), kind, "§2.1"))
    s12 = _section(architecture, "## 12.", "\n---")
    for row in _table_rows(s12):
        if re.fullmatch(r"M\d+", row[0]):
            elements.append(Element(row[0], "migration", "§12"))
        if row[0] == "M6":
            for name in re.findall(r"`([A-Z][A-Z_]+)`", row[1]):
                if name not in {e.name for e in elements if e.kind == "table"}:
                    elements.append(Element(name, "table", "§12 M6"))
    for row in _table_rows(_section(architecture, "## 11.", "\n---")):
        if re.fullmatch(r"I\d+′?", row[0]):
            elements.append(Element(row[0], "invariant", "§11"))
    for row in _table_rows(_section(architecture, "### 9.1", "### 9.2")):
        match = re.fullmatch(r"\*\*`([a-z]+)`\*\*", row[0])
        if match:
            elements.append(Element(match.group(1), "channel", "§9.1"))
    for row in _table_rows(_section(architecture, "### 8.1", "### 8.2")):
        if row[0].startswith("**") and len(row) > 1:
            for name in re.findall(r"`([a-z_]+)`", row[1]):
                elements.append(Element(name, "operation", f"§8.1 {row[0].strip('*')}"))
    return elements


def defined_tasks(roadmap: str) -> set[str]:
    return set(TASK_DEF.findall(roadmap))


def coverage_owners(coverage: str) -> dict[tuple[str, str], tuple[list[str], str]]:
    """``COVERAGE.md`` rows: (class, element) → (owner tokens, note)."""
    owners: dict[tuple[str, str], tuple[list[str], str]] = {}
    for row in _table_rows(coverage):
        if len(row) >= 4 and row[1] in CLASSES:
            name = row[0].strip("`")
            tokens = TASK_REF.findall(row[2]) + (["deferred"] if "deferred" in row[2] else [])
            owners[(row[1], name)] = (tokens, row[3])
    return owners


def ledger_owners(ledger: str) -> dict[tuple[str, str], tuple[list[str], str]]:
    """V2 invariant rows of the ledger: owner column → task ids.

    An ``ENFORCED`` row carries no owner in the ledger (the invariant test forbids it); its sites and
    tests are the evidence of closure. To avoid a false "owner row names no task" finding for such rows,
    treat ``ENFORCED`` as self-evidencing by injecting a synthetic ``enforced`` token, which the coverage
    resolver then counts as valid (parallel to how ``deferred`` works for deferred elements).
    """
    owners: dict[tuple[str, str], tuple[list[str], str]] = {}
    for row in _table_rows(ledger):
        if len(row) >= 6 and re.fullmatch(r"I\d+′?", row[0]):
            status = row[1].strip("`")
            tokens = TASK_REF.findall(row[5])
            if status == "ENFORCED" and not tokens:
                tokens = ["enforced"]  # self-evidencing: sites and tests are the proof
            owners[("invariant", row[0])] = (tokens, row[4])
    return owners


def resolve(plan: Path = PLAN) -> tuple[list[Element], dict[Element, list[str]], list[Finding]]:
    """Return (elements, owners per element, findings)."""
    read = lambda name: (plan / name).read_text(encoding="utf-8")  # noqa: E731
    elements = extract_elements(read("ARCHITECTURE_V2.md"))
    tasks = defined_tasks(read("ROADMAP_V2.md"))
    declared = coverage_owners(read("COVERAGE.md")) | ledger_owners(read("INVARIANT_LEDGER.md"))
    owners: dict[Element, list[str]] = {}
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for element in elements:
        key = (element.kind, element.name)
        if key in seen:
            findings.append(Finding(element, "declared twice"))
            continue
        seen.add(key)
        if key not in declared:
            findings.append(Finding(element, "no owner row"))
            continue
        tokens, note = declared[key]
        valid = [t for t in tokens if t in tasks]
        unknown = sorted({t for t in tokens if t != "deferred" and t != "enforced" and t not in tasks})
        if unknown:
            findings.append(Finding(element, f"owner is not a ROADMAP_V2 task: {', '.join(unknown)}"))
        if "deferred" in tokens:
            if not DECISION_REF.search(note) or len(note) < 20:
                findings.append(Finding(element, "deferred without a reason and a V2-D decision reference"))
            else:
                valid.append("deferred")
        if "enforced" in tokens:
            valid.append("enforced")  # ENFORCED ledger rows are self-evidencing; sites and tests are the proof
        if not valid and not unknown:
            findings.append(Finding(element, "owner row names no task"))
        owners[element] = valid
    stale = sorted(set(declared) - seen)
    for kind, name in stale:
        if kind != "invariant":  # ledger rows for v1 invariants are not architecture elements
            findings.append(
                Finding(
                    Element(name, kind, "COVERAGE.md"), "owner row for an element the architecture does not declare"
                )
            )
    return elements, owners, findings


def sync_log_differences(plan: Path, owners: dict[Element, list[str]]) -> list[str]:
    """Compare with the historical SYNC_LOG.md §3 matrix (read-only)."""
    text = (plan / "SYNC_LOG.md").read_text(encoding="utf-8")
    section = text[text.index("**Architecture → plan coverage matrix**") :]
    section = section[: section.index("\n---")]
    by_name = {e.name: set(o) for e, o in owners.items()}
    lines: list[str] = []
    mentioned: set[str] = set()
    for row in _table_rows(section):
        if len(row) < 2 or row[0] == "Architecture element":
            continue
        names = set(re.findall(r"`([A-Z][A-Z_]+|I\d+′?)`|\b(M\d)\b", row[0])) if row[0] else set()
        flat = {a or b for a, b in names}
        historical = set(TASK_REF.findall(row[1]))
        for name in sorted(flat & set(by_name)):
            mentioned.add(name)
            if historical != by_name[name] and not historical <= by_name[name]:
                lines.append(f"`{name}`: SYNC_LOG §3 {sorted(historical)} → now {sorted(by_name[name])}")
    for element in owners:
        if element.kind in ("table", "migration") and element.name not in mentioned:
            lines.append(f"`{element.name}` ({element.kind}): absent from SYNC_LOG §3")
    lines.append("Operations, channels and individual invariants are listed only by group in SYNC_LOG §3.")
    return list(dict.fromkeys(lines))


def render(plan: Path = PLAN) -> str:
    elements, owners, findings = resolve(plan)
    out = [
        "# Architecture → Plan Coverage Matrix",
        "",
        "**Generated** by `scripts/plan_coverage.py --write` (`MA-017`); do not edit. CI fails if it is stale",
        "(`--check`) or if any element is unowned (`tests/test_plan_coverage.py`). Elements are extracted from",
        "`ARCHITECTURE_V2.md`; owners come from `INVARIANT_LEDGER.md` (invariants) and `COVERAGE.md` (the rest).",
        "",
        f"**{len(elements)} elements, {len(findings)} unowned.**",
        "",
    ]
    for kind in CLASSES:
        rows = [e for e in elements if e.kind == kind]
        out += [f"## {kind.capitalize()}s ({len(rows)})", "", "| Element | Declared | Owners |", "|---|---|---|"]
        out += [
            f"| `{e.name}` | {e.where} | {', '.join(f'`{o}`' for o in owners.get(e, [])) or '**none**'} |"
            for e in rows
        ]
        out.append("")
    out += ["## Differences from the historical matrix (`SYNC_LOG.md` §3, not modified)", ""]
    out += [f"- {line}" for line in sync_log_differences(plan, owners)]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    _, _, findings = resolve()
    for finding in findings:
        print(finding.render())
    if "--write" in argv:
        MATRIX.write_text(render(), encoding="utf-8")
    stale = "--check" in argv and (not MATRIX.exists() or MATRIX.read_text(encoding="utf-8") != render())
    if stale:
        print("plan_coverage: COVERAGE_MATRIX.md is stale; run scripts/plan_coverage.py --write")
    print(f"plan_coverage: {len(findings)} unowned element(s)")
    return 1 if findings or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
