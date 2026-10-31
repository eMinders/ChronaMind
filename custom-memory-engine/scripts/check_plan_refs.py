"""Fail on unresolved cross-references inside PLAN/V2 (MA-018).

Checks every ``*.md`` under ``PLAN/V2/``:

* task ids (``E-``, ``MA-``, ``C-``, ``A-``, ``R-``, ``SD-``, ``UM-`` + three digits) are defined as a
  ROADMAP_V2 table row or tracker bullet;
* gate ids (``G0`` ... ``G8``, ``G4b`` ...) are defined as a bold gate label in ROADMAP_V2;
* decision ids (``V2-D<n>``) are defined as a heading or table row in DECISIONS_V2, or recorded there as
  explicitly resolved;
* migration ids (``M1`` ... ``M9``) are defined in ARCHITECTURE_V2;
* no heading is duplicated within one file;
* relative markdown links resolve;
* superseded formulas appear only on lines that say they are superseded or retained as a control.

Fenced code blocks (including mermaid) are ignored, so diagram node labels are not references.

Run:  uv run python scripts/check_plan_refs.py [PLAN/V2]
Exit: 0 when clean, 1 when any finding is reported.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "PLAN" / "V2"

TASK = r"(?:E|MA|C|A|R|SD|UM)-\d{3}"
TASK_REF = re.compile(rf"(?<![\w-])({TASK})(?![\w-])")
TASK_DEF = re.compile(rf"^\s*(?:\|\s*|-\s*)`({TASK})`", re.M)
GATE_REF = re.compile(r"(?<![\w-])(G\d+b?)(?![\w-])")
GATE_DEF = re.compile(r"\*\*(G\d+b?)[ .(]")
DECISION_REF = re.compile(r"(?<![\w-])(V2-D\d+[a-z]?)(?![\w-])")
DECISION_DEF = re.compile(r"^(?:#{2,4}\s+|\|\s*)(V2-D\d+[a-z]?)\b", re.M)
DECISION_DISPOSED = re.compile(r"`(V2-D\d+[a-z]?)`\s+is\s+\*\*resolved\*\*|[Rr]esolves\s+`(V2-D\d+[a-z]?)`")
MIGRATION_REF = re.compile(r"(?<![\w-])(M[1-9])(?![\w-])")
MIGRATION_DEF = re.compile(r"^(?:#{2,4}\s+|\|\s*\*{0,2})(M[1-9])\b", re.M)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
LINK = re.compile(r"\]\((?!https?:|mailto:|#)([^)\s#]+)")
FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.M | re.S)

STALE_FORMULA = re.compile(r"1\s*[-−]\s*CP_upper\(k,\s*o\)")
STALE_EXCUSE = re.compile(
    r"r1|r2|r3|superseded|correct|regression|ablation|positive control"
    r"|X-ignore-undetermined|CRITICAL|treats|treated|not\b",
    re.I,
)


@dataclass(frozen=True)
class Finding:
    """One reported problem."""

    path: str
    kind: str
    detail: str

    def render(self) -> str:
        """Return a single printable line."""
        return f"{self.path}: {self.kind}: {self.detail}"


def strip_fences(text: str) -> str:
    """Blank out fenced code blocks while preserving line numbers."""
    return FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def load_documents(directory: Path) -> dict[str, str]:
    """Read every markdown file in ``directory`` with fences removed."""
    return {p.name: strip_fences(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.md"))}


def _decision_ids(text: str) -> set[str]:
    """Decisions defined by heading/table row, plus ones whose resolution is recorded explicitly."""
    disposed = {a or b for a, b in DECISION_DISPOSED.findall(text)}
    return set(DECISION_DEF.findall(text)) | disposed


def defined_ids(docs: dict[str, str]) -> dict[str, set[str]]:
    """Collect the ids each kind is allowed to reference."""
    roadmap = docs.get("ROADMAP_V2.md", "")
    return {
        "task": set(TASK_DEF.findall(roadmap)),
        "gate": set(GATE_DEF.findall(roadmap)),
        "decision": _decision_ids(docs.get("DECISIONS_V2.md", "")),
        "migration": set(MIGRATION_DEF.findall(docs.get("ARCHITECTURE_V2.md", ""))),
    }


def check_references(docs: dict[str, str]) -> list[Finding]:
    """Report ids that are used but never defined."""
    defined = defined_ids(docs)
    patterns = {
        "task": TASK_REF,
        "gate": GATE_REF,
        "decision": DECISION_REF,
        "migration": MIGRATION_REF,
    }
    findings: list[Finding] = []
    for name, text in docs.items():
        for kind, pattern in patterns.items():
            for ref in sorted(set(pattern.findall(text)) - defined[kind]):
                findings.append(Finding(name, f"undefined_{kind}", ref))
    return findings


def check_headings(docs: dict[str, str]) -> list[Finding]:
    """Report headings that occur more than once in a file."""
    findings: list[Finding] = []
    for name, text in docs.items():
        counts = Counter(f"{m.group(1)} {m.group(2)}" for m in HEADING.finditer(text))
        findings.extend(Finding(name, "duplicate_heading", h) for h, n in counts.items() if n > 1)
    return findings


def check_links(directory: Path, docs: dict[str, str]) -> list[Finding]:
    """Report relative links whose target does not exist."""
    findings: list[Finding] = []
    for name, text in docs.items():
        for target in sorted(set(LINK.findall(text))):
            if not (directory / target).resolve().exists():
                findings.append(Finding(name, "broken_link", target))
    return findings


def check_stale_formulas(docs: dict[str, str]) -> list[Finding]:
    """Report the superseded admission bound when a line does not mark it as superseded."""
    findings: list[Finding] = []
    for name, text in docs.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if STALE_FORMULA.search(line) and not STALE_EXCUSE.search(line):
                findings.append(Finding(name, "stale_formula", f"line {number}"))
    return findings


def run_checks(directory: Path) -> list[Finding]:
    """Run every check over ``directory`` and return all findings."""
    docs = load_documents(directory)
    if not docs:
        return [Finding(str(directory), "no_documents", "no markdown files found")]
    return [
        *check_references(docs),
        *check_headings(docs),
        *check_links(directory, docs),
        *check_stale_formulas(docs),
    ]


def main(argv: list[str]) -> int:
    """CLI entry point."""
    directory = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_DIR
    findings = run_checks(directory)
    for finding in findings:
        print(finding.render())
    print(f"check_plan_refs: {len(findings)} finding(s) in {directory}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
