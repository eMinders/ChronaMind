"""Fail when an evidence record cites a test or a file that does not exist (W3 closure protocol).

An issue is closed on the strength of a written record: criterion -> test -> result. That record is only as good as
its references. W3's first three closures showed the two ways it goes wrong without anyone noticing:

* a cited test name was misspelt (``..._for_e036_stages`` for ``..._for_e_036_stages``), so the record pointed at a
  test that does not exist;
* a verification line named a check ("audit oracles") that exists nowhere in the repository.

This script makes the first mechanical. Given one or more markdown files (a PR body, a closure comment saved to a
file), it extracts every backticked reference and checks it:

* ``test_name`` or ``test_name[param]`` -- a ``def``/``async def`` of that name exists under ``tests/`` or
  ``evaluation/``;
* ``path/to/file.py::test_name`` -- that file exists and defines that test;
* ``path/with/a/slash.ext`` -- the file exists, resolved against the engine root, ``src/custom_memory_engine/`` or the
  repository root.

It cannot know whether a check *named in prose* exists, so the protocol (``PLAN/V2/CLOSURE_PROTOCOL.md``) requires
every verification line to name a runnable command or a test, which this script then checks.

**Scope: closure records, not every document.** Use the default strict mode on a record written to close an issue or
open a PR — that is where a false citation becomes a false claim. ``--plan-document`` checks only the test-name claims
and is **advisory**: a PLAN document legitimately cites files that no longer exist (closed audit reports) and tests
that are *specified but not yet written* (``DELETION_ORDER.md``'s F5/F7/F8/F9/F10 name the tests those failure modes
will need). Do not gate CI on a PLAN scan; do gate a closure on the strict one.

Exit status: 0 when every reference resolves, 1 otherwise. ``--quiet`` prints only failures.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
REPO = ENGINE.parent
TEST_ROOTS = (ENGINE / "tests", ENGINE / "evaluation")
#: A relative path in a record may be root-relative, ``src/``-relative, repo-relative, or relative to the schema
#: directory -- decision records cite both ``schema/rollback/014_*.sql`` and ``rollback/014_*.sql``.
PATH_ROOTS = (
    ENGINE,
    ENGINE / "src" / "custom_memory_engine",
    ENGINE / "src" / "custom_memory_engine" / "schema",
    REPO,
)

#: a backticked span, without the backticks
_SPAN = re.compile(r"`([^`\n]+)`")
#: a span that is being *quoted as wrong* (a correction record) is not a claim; see ``_is_quoted``
_CORRECTION = re.compile(
    r"(?i)~~|\bmisspelt?\b|\bmistyped\b|\bwrong(?:ly)?\b|\bcorrect(?:ed|ion)\b|\bshould be\b|\btypo\b"
)
#: a test name, optionally parametrised (``test_x[assert]``)
_TEST = re.compile(r"^(test_[A-Za-z0-9_]+)(\[[^\]]*\])?$")
#: ``some/file.py::test_x`` (optionally parametrised)
_NODE = re.compile(r"^([A-Za-z0-9_./-]+\.py)::(test_[A-Za-z0-9_]+)(\[[^\]]*\])?$")
#: a relative file path: at least one slash and a known extension, no spaces or wildcards
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+\.(py|sql|md|json|yaml|yml|toml|txt)$")


@dataclass(frozen=True, slots=True)
class Problem:
    source: str
    reference: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}: `{self.reference}` -- {self.reason}"


def defined_tests(roots: Iterable[Path] = TEST_ROOTS) -> set[str]:
    """Every ``test_*`` function name defined under ``roots`` (sync or async)."""
    pattern = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
    names: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if ".venv" in path.parts:
                continue
            names.update(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def _resolve(relative: str, roots: Sequence[Path] = PATH_ROOTS) -> Path | None:
    for root in roots:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def _is_quoted(span: str, before: str) -> bool:
    """Is this span inside a sentence that *documents* it as wrong, rather than claiming it?

    A correction record legitimately writes the wrong name next to the right one ("``a_b`` was misspelt; it is
    ``a_bb``"). Flagging the wrong one would make it impossible to record a correction, so a span is skipped when its
    clause (the text since the previous clause break) or its own markdown is a correction marker, and a whole section
    under a heading that is one counts as well (a "## Correction" section quotes wrong names on purpose).
    """
    clause = re.split(r"[.;\n]", before)[-1]
    if _CORRECTION.search(clause) or _CORRECTION.search(span) or span.startswith("~~"):
        return True
    return _in_correction_section(before)


def _in_correction_section(before: str) -> bool:
    """Is the cursor in a section whose heading is a correction marker? Headings, not body text, decide it."""
    heading = ""
    for line in before.splitlines():
        # strip blockquote markers first: a correction is often written as `> **Correction.** ...`
        stripped = line.lstrip().lstrip(">").lstrip()
        # `#`/`##` headings and **bold** pseudo-headings both scope a section in these records
        if stripped.startswith("#") or stripped.startswith("**"):
            heading = stripped
    return bool(_CORRECTION.search(heading))


def check_text(text: str, source: str, tests: set[str]) -> tuple[list[Problem], int]:
    """The problems in one record, and how many references were checked."""
    problems: list[Problem] = []
    checked = 0
    for match in _SPAN.finditer(text):
        span = match.group(1)
        if _is_quoted(span, text[: match.start()]):
            continue
        reference = span.strip()
        node = _NODE.match(reference)
        if node:
            checked += 1
            path = _resolve(node.group(1))
            if path is None:
                problems.append(Problem(source, reference, "file does not exist"))
            elif not re.search(rf"def\s+{re.escape(node.group(2))}\s*\(", path.read_text(encoding="utf-8")):
                problems.append(Problem(source, reference, "file does not define that test"))
            continue
        test = _TEST.match(reference)
        if test:
            checked += 1
            if test.group(1) not in tests:
                problems.append(Problem(source, reference, "no test of this name exists"))
            continue
        if _PATH.match(reference):
            checked += 1
            if _resolve(reference) is None:
                problems.append(Problem(source, reference, "file does not exist"))
    return problems, checked


def main(argv: Sequence[str] | None = None) -> int:
    summary = (__doc__ or "").strip().splitlines()
    parser = argparse.ArgumentParser(description=summary[0] if summary else None)
    parser.add_argument("records", nargs="+", type=Path, help="markdown evidence records to check")
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    parser.add_argument(
        "--plan-document",
        action="store_true",
        help="check only test-name claims (PLAN documents cite historical paths on purpose)",
    )
    args = parser.parse_args(argv)
    tests = defined_tests()
    problems: list[Problem] = []
    total = 0
    for record in args.records:
        text = record.read_text(encoding="utf-8")
        if args.plan_document:
            # a PLAN document mixes evidence with history: it cites files that legitimately no longer exist (closed
            # audit reports, tests deleted when the gap they reported was closed). Only test names are claims about
            # code that must be current, so a PLAN scan checks those and treats paths as advisory.
            found, checked = _check_tests_only(text, str(record), tests)
        else:
            found, checked = check_text(text, str(record), tests)
        problems.extend(found)
        total += checked
    for problem in problems:
        print(f"FAIL {problem}")
    if not args.quiet:
        print(f"check_evidence: {total} reference(s) checked, {len(problems)} unresolved")
    return 1 if problems else 0


def _check_tests_only(text: str, source: str, tests: set[str]) -> tuple[list[Problem], int]:
    """Only the test-name claims; used for PLAN documents, whose path citations are historical."""
    problems: list[Problem] = []
    checked = 0
    for match in _SPAN.finditer(text):
        span = match.group(1)
        if _is_quoted(span, text[: match.start()]):
            continue
        test = _TEST.match(span.strip())
        if test is None:
            continue
        checked += 1
        if test.group(1) not in tests:
            problems.append(Problem(source, span.strip(), "no test of this name exists"))
    return problems, checked


if __name__ == "__main__":
    sys.exit(main())
