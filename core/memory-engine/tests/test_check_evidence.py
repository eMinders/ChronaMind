"""``scripts/check_evidence.py``: a closure record's references must resolve (W3 closure protocol).

The defect this prevents is not hypothetical. #200's closure summary cited a misspelt test name, and two PR bodies
cited a check ("audit oracles") that exists nowhere. A human reviewer caught neither; the first is mechanical, so it is
checked mechanically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
SCRIPTS = ENGINE / "scripts"
sys.path.insert(0, str(SCRIPTS))

check_evidence = __import__("check_evidence")

KNOWN = {"test_a_real_one", "test_another"}


def problems(text: str) -> list[str]:
    found, _ = check_evidence.check_text(text, "record.md", KNOWN)
    return [str(problem) for problem in found]


def test_a_real_test_name_resolves() -> None:
    assert problems("| 1 | x | `test_a_real_one` | |") == []


def test_a_parametrised_test_name_resolves() -> None:
    assert problems("`test_a_real_one[assert]`") == []


def test_a_misspelt_test_name_is_reported() -> None:
    assert problems("`test_a_real_ones`") == ["record.md: `test_a_real_ones` -- no test of this name exists"]


def test_a_real_path_resolves() -> None:
    assert problems("`src/custom_memory_engine/engine/planning.py`") == []


def test_a_missing_path_is_reported() -> None:
    assert problems("`src/custom_memory_engine/engine/no_such_module.py`") == [
        "record.md: `src/custom_memory_engine/engine/no_such_module.py` -- file does not exist"
    ]


def test_a_file_and_test_node_both_resolve() -> None:
    """The node form checks the *file*, not just the name, so a name real elsewhere but absent there is caught."""
    assert problems("`tests/test_planning.py::test_the_stages_are_named_for_e036`") == []
    assert problems("`tests/test_planning.py::test_a_real_one`") == [
        "record.md: `tests/test_planning.py::test_a_real_one` -- file does not define that test"
    ]


def test_prose_in_backticks_is_not_a_reference() -> None:
    """A short backticked span that is not a test or a path is prose and must not be flagged."""
    assert problems("`stale` and `V2-D24` and `E-038-F5`") == []


def test_a_quoted_wrong_name_in_a_correction_is_not_a_claim() -> None:
    """A correction record must be able to write the wrong name next to the right one."""
    record = "**Correction.** The row cited `test_a_real_ones`; it is `test_a_real_one`."
    assert problems(record) == []
    struck = "The row cited ~~`test_a_real_ones`~~ and the name is `test_a_real_one`."
    assert problems(struck) == []


def test_the_same_wrong_name_outside_a_correction_is_still_reported() -> None:
    """The exemption is scoped to corrections; an ordinary claim is still checked."""
    assert problems("| 10 | criterion | `test_a_real_ones` | |") != []


@pytest.mark.parametrize("missing", ["tests/test_no_such_file.py", "scripts/no_such_script.py"])
def test_a_record_citing_a_missing_file_fails(missing: str) -> None:
    assert problems(f"`{missing}`") == [f"record.md: `{missing}` -- file does not exist"]


def test_main_exits_nonzero_on_an_unresolved_reference(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = tmp_path / "record.md"
    record.write_text("`test_a_real_ones`\n", encoding="utf-8")
    assert check_evidence.main([str(record)]) == 1
    assert "no test of this name exists" in capsys.readouterr().out


def test_main_exits_zero_when_every_reference_resolves(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = tmp_path / "record.md"
    record.write_text(
        "`test_the_stages_are_named_for_e036`\n`src/custom_memory_engine/engine/planning.py`\n", encoding="utf-8"
    )
    assert check_evidence.main([str(record)]) == 0
    assert "0 unresolved" in capsys.readouterr().out


def test_plan_document_mode_checks_test_names_only() -> None:
    """A PLAN document cites historical paths; only its test claims must point at code that exists."""
    problems, checked = check_evidence._check_tests_only(
        "`audits/CLOSED_REPORT.md` and `test_a_real_one` and `test_a_real_ones`", "plan.md", KNOWN
    )
    assert checked == 2  # the path is not checked in this mode
    assert [str(p) for p in problems] == ["plan.md: `test_a_real_ones` -- no test of this name exists"]


def test_plan_document_mode_respects_correction_sections() -> None:
    """The `V2-D55` correction quotes a deleted test name on purpose."""
    record = "**Correction.** This cited `test_deleted_last_year`; it is `test_a_real_one`."
    problems, _ = check_evidence._check_tests_only(record, "plan.md", KNOWN)
    assert problems == []


def test_a_blockquoted_correction_skips_its_quoted_name() -> None:
    """A correction is usually a blockquote; its quoted wrong name must not be reported (regression: `V2-D55`).

    Regression: `V2-D55`'s correction is written as `> **Correction.** ... > `test_x``, and the heading scan did not
    strip the `>` markers, so the deliberately-quoted deleted name was reported as missing.
    """
    record = (
        "**Verification.** `test_a_real_one`.\n"
        "\n"
        "> **Correction (2026-11-17).** This line previously cited\n"
        "> `test_deleted_last_year`, which was removed because the gap closed.\n"
    )
    problems, checked = check_evidence.check_text(record, "record.md", KNOWN)
    assert problems == []
    assert checked == 1  # the quoted name is excused and so is not counted as checked
