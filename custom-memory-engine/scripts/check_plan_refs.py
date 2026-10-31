"""Fail on unresolved cross-references inside PLAN/V2 (MA-018).

Checks every ``*.md`` under ``PLAN/V2/``:

* task ids (``E-``, ``MA-``, ``C-``, ``A-``, ``R-``, ``SD-``, ``UM-`` + three digits) are defined as a
  ROADMAP_V2 table row or tracker bullet, or recorded there as a **retired alias** (see below);
* gate ids (``G0`` ... ``G8``, ``G4b`` ...) are defined as a bold gate label in ROADMAP_V2;
* decision ids (``V2-D<n>``) are defined as a heading or table row in DECISIONS_V2 or STATISTICS, or
  recorded there as explicitly resolved;
* migration ids (``M1`` ... ``M9``) are defined in ARCHITECTURE_V2;
* no heading is duplicated within one file;
* relative markdown links resolve;
* the superseded r1/r2 admission bound ``1 − CP_upper(k, o)`` appears only on lines that mark it;
* operation names (MA-012 criterion 5): in ROADMAP_V2 and ARCHITECTURE_V2, every call-form name
  ```name(``` is an operation of the API_V2 §3 matrix, a selector of its §6 table, or a name its §7.1 table
  declares (composition or internal function); a name §7.1 marks ``retired`` (``retract``) may appear only on a
  line that also says ``retired``, ``superseded``, ``does not exist`` or ``never``. Skipped when API_V2.md is
  absent.

Retired aliases. A task id that was renamed or moved is defined by any of these ROADMAP_V2 forms:
``(was `C-013`)``, ``` `C-024` renamed ```, ``moved from `R-014` ``, ``formerly `X` `` and
``` `R-014` → `E-037` ```.

Marking the superseded formula. A line may contain ``1 − CP_upper(k, o)`` only if it also contains one of
these whole-word markers (case-insensitive): ``superseded``, ``correction``, ``r1``, ``r2``,
``X-ignore-undetermined``, ``ablation``, ``positive control``, ``CRITICAL FIX``, or a description of the
defect itself, ``as (a) success(es)``. Ordinary words such as "not" or "correctly" are deliberately **not**
markers: a current-use statement must never pass because it happens to contain them.

Fenced code blocks (including mermaid) are ignored, so diagram node labels are not references.
Every finding prints ``file:line: kind: detail``.

Run:  uv run python scripts/check_plan_refs.py [PLAN/V2]
Exit: 0 when clean, 1 when any finding is reported.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "PLAN" / "V2"

TASK = r"(?:E|MA|C|A|R|SD|UM)-\d{3}"
TASK_REF = re.compile(rf"(?<![\w-])({TASK})(?![\w-])")
TASK_DEF = re.compile(rf"^\s*(?:\|\s*|-\s*)`({TASK})`", re.M)
TASK_ALIAS = re.compile(
    rf"\(was `({TASK})`\)|`({TASK})` renamed|moved from `({TASK})`|formerly `({TASK})`|`({TASK})`\s*→\s*`{TASK}`"
)
GATE_REF = re.compile(r"(?<![\w-])(G\d+b?)(?![\w-])")
GATE_DEF = re.compile(r"\*\*(G\d+b?)[ .(]")
DECISION_REF = re.compile(r"(?<![\w-])(V2-D\d+[a-z]?)(?![\w-])")
DECISION_DEF = re.compile(r"^(?:#{2,4}\s+|\|\s*)(V2-D\d+[a-z]?)\b", re.M)
DECISION_DISPOSED = re.compile(r"`(V2-D\d+[a-z]?)`\s+is\s+\*\*resolved\*\*|[Rr]esolves\s+`(V2-D\d+[a-z]?)`")
MIGRATION_REF = re.compile(r"(?<![\w-])(M[1-9])(?![\w-])")
MIGRATION_DEF = re.compile(r"^(?:#{2,4}\s+|\|\s*\*{0,2})(M[1-9])\b", re.M)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK = re.compile(r"\]\((?!https?:|mailto:|#)([^)\s#]+)")
FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.M | re.S)

OPERATION_SCOPE = ("ROADMAP_V2.md", "ARCHITECTURE_V2.md")
CALL_FORM = re.compile(r"`([a-z][a-z0-9_]*)\(")
MATRIX_ROW = re.compile(r"^\|\s*\d+\s*\|[^|]*\|\s*`([a-z][a-z0-9_]*)`\s*\|", re.M)
SELECTOR_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)\(", re.M)
NAME_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|\s*(composition|internal function|retired)\s*\|", re.M)
RETIRED_MARKER = re.compile(r"\bretired\b|\bsuperseded\b|does not exist|\bnever\b", re.I)
STALE_FORMULA = re.compile(r"1\s*[-−]\s*CP_upper\(k,\s*o\)")
STALE_MARKER = re.compile(
    r"\bsuperseded\b|\bcorrection\b|\br[12]\b|X-ignore-undetermined|\bablation\b|\bpositive control\b"
    r"|\bCRITICAL FIX\b|\bas (?:a )?success(?:es)?\b",
    re.I,
)


@dataclass(frozen=True)
class Finding:
    """One reported problem; ``line`` is 1-based (0 only for directory-level findings)."""

    path: str
    kind: str
    detail: str
    line: int = 0

    def render(self) -> str:
        """Return a single printable line: ``file:line: kind: detail``."""
        return f"{self.path}:{self.line}: {self.kind}: {self.detail}"


def strip_fences(text: str) -> str:
    """Blank out fenced code blocks while preserving line numbers."""
    return FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def load_documents(directory: Path) -> dict[str, str]:
    """Read every markdown file in ``directory`` with fences removed."""
    return {p.name: strip_fences(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.md"))}


def _groups(pattern: re.Pattern[str], text: str) -> set[str]:
    """Every non-empty capture of ``pattern`` (patterns with alternatives capture in different groups)."""
    found: set[str] = set()
    for match in pattern.finditer(text):
        found.update(g for g in match.groups() if g)
    return found


def _decision_ids(text: str) -> set[str]:
    """Decisions defined by heading/table row, plus ones whose resolution is recorded explicitly."""
    return set(DECISION_DEF.findall(text)) | _groups(DECISION_DISPOSED, text)


def defined_ids(docs: dict[str, str]) -> dict[str, set[str]]:
    """Collect the ids each kind is allowed to reference."""
    roadmap = docs.get("ROADMAP_V2.md", "")
    return {
        "task": set(TASK_DEF.findall(roadmap)) | _groups(TASK_ALIAS, roadmap),
        "gate": set(GATE_DEF.findall(roadmap)),
        "decision": _decision_ids(docs.get("DECISIONS_V2.md", "")) | _decision_ids(docs.get("STATISTICS.md", "")),
        "migration": set(MIGRATION_DEF.findall(docs.get("ARCHITECTURE_V2.md", ""))),
    }


def check_references(docs: dict[str, str]) -> list[Finding]:
    """Report ids that are used but never defined, at the line of their first use in each file."""
    defined = defined_ids(docs)
    patterns = {
        "task": TASK_REF,
        "gate": GATE_REF,
        "decision": DECISION_REF,
        "migration": MIGRATION_REF,
    }
    findings: list[Finding] = []
    for name, text in docs.items():
        reported: set[tuple[str, str]] = set()
        for number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in patterns.items():
                for ref in pattern.findall(line):
                    if ref in defined[kind] or (kind, ref) in reported:
                        continue
                    reported.add((kind, ref))
                    findings.append(Finding(name, f"undefined_{kind}", ref, number))
    return findings


def check_headings(docs: dict[str, str]) -> list[Finding]:
    """Report every repeat of a heading already seen in the same file, at the repeat's line."""
    findings: list[Finding] = []
    for name, text in docs.items():
        seen: set[str] = set()
        for number, line in enumerate(text.splitlines(), start=1):
            match = HEADING.match(line)
            if not match:
                continue
            heading = f"{match.group(1)} {match.group(2)}"
            if heading in seen:
                findings.append(Finding(name, "duplicate_heading", heading, number))
            seen.add(heading)
    return findings


def check_links(directory: Path, docs: dict[str, str]) -> list[Finding]:
    """Report relative links whose target does not exist."""
    findings: list[Finding] = []
    for name, text in docs.items():
        for number, line in enumerate(text.splitlines(), start=1):
            for target in LINK.findall(line):
                if not (directory / target).resolve().exists():
                    findings.append(Finding(name, "broken_link", target, number))
    return findings


def check_stale_formulas(docs: dict[str, str]) -> list[Finding]:
    """Report the superseded admission bound on any line that does not mark it (see module docstring)."""
    findings: list[Finding] = []
    for name, text in docs.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if STALE_FORMULA.search(line) and not STALE_MARKER.search(line):
                findings.append(Finding(name, "stale_formula", "1 − CP_upper(k, o) used without a marker", number))
    return findings


def api_names(api_text: str) -> tuple[set[str], set[str]]:
    """Return (known names, retired names) declared by API_V2.md (see module docstring)."""
    known = set(MATRIX_ROW.findall(api_text)) | set(SELECTOR_ROW.findall(api_text))
    retired: set[str] = set()
    for name, kind in NAME_ROW.findall(api_text):
        (retired if kind == "retired" else known).add(name)
    return known, retired


def check_operations(docs: dict[str, str]) -> list[Finding]:
    """Report undefined call-form operation names and unmarked uses of retired operations."""
    if "API_V2.md" not in docs:
        return []
    known, retired = api_names(docs["API_V2.md"])
    retired_ref = re.compile(r"`(" + "|".join(map(re.escape, sorted(retired))) + r")`") if retired else None
    findings: list[Finding] = []
    for name in OPERATION_SCOPE:
        for number, line in enumerate(docs.get(name, "").splitlines(), start=1):
            for called in CALL_FORM.findall(line):
                if called not in known and called not in retired:
                    findings.append(Finding(name, "undefined_operation", called, number))
            hits = set(CALL_FORM.findall(line)) & retired
            if retired_ref is not None:
                hits |= set(retired_ref.findall(line))
            if hits and not RETIRED_MARKER.search(line):
                findings.extend(Finding(name, "retired_operation", hit, number) for hit in sorted(hits))
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
        *check_operations(docs),
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
