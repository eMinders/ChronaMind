"""The review gate's two decisions are mechanical, so they are pinned here rather than trusted to a prompt.

Why this file exists: the canary (`.github/workflows/README.md`, "Verifying the reviewer") found the reviewer reasoning
well and classifying badly. After a fix push it called itself a *first review* — the base branch had advanced, the diff
range moved, and the earlier findings fell outside it — and a push that changed no tree bought a full run that posted
nothing. Both are facts about the pull request's history and its commits, not about the change, so both are decided
before the model runs and tested here without a token, a network or a runner.

The last two tests are the seam: they hold the workflow to actually asking the gate, and hold the summary format the
skill writes to the one this script parses.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / ".github" / "scripts" / "review_gate.py"
REVIEW_WORKFLOW = REPO / ".github" / "workflows" / "claude-code-review.yml"
REVIEW_SKILL = REPO / ".claude" / "skills" / "review-pr" / "SKILL.md"

HEAD = "a" * 40
BASE = "b" * 40
OLDER = "c" * 40
OLDER_BASE = "d" * 40


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_gate", GATE)
    assert spec is not None and spec.loader is not None, f"{GATE} is not loadable"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass resolves its own module while the class body runs
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _history(record: object | None = None, *, earlier: bool | None = None, available: bool = True) -> object:
    """A `History` with an implicit ``earlier_review`` (a record implies one)."""
    return gate.History(
        available=available, earlier_review=earlier if earlier is not None else record is not None, record=record
    )


def _summary(base: str, head: str, *, extra: str = "") -> str:
    return f"## Code review\n\n**Base:** `{base}` · **Head:** `{head}` · **Reviewed:** `1` files{extra}\n"


def _comment(login: str, body: str) -> dict[str, object]:
    return {"user": {"login": login}, "body": body}


# --- the tier: what the canary got wrong -----------------------------------------------------------------


def test_a_first_review_is_not_a_re_review() -> None:
    decision = gate.classify(action="opened", history=_history(), head_sha=HEAD, base_sha=BASE, changed=None)
    assert (decision.review, decision.tier, decision.reason) == (True, "first review", "no_earlier_review")


def test_a_normal_push_is_a_re_review_and_is_reviewed() -> None:
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=True,
    )
    assert (decision.review, decision.tier, decision.reason) == (True, "re-review", "push_changed_the_tree")


def test_a_fix_only_push_is_reviewed_at_the_re_review_tier() -> None:
    """A push whose whole content is the correction of an earlier finding is the case a re-review exists for."""
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=True,
    )
    assert decision.review and decision.tier == "re-review"


def test_a_regression_introduced_while_fixing_an_earlier_finding_is_reviewed() -> None:
    """The gate cannot tell a fix from a regression, and must not try: any tree change is reviewed.

    Deciding that a push "only fixes" something would need the reviewer's own judgement, in the gate, before the review
    runs — which is how a new defect would be skipped as a follow-up.
    """
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=True,
    )
    assert decision.review is True


def test_a_push_that_changes_no_tree_skips_the_expensive_review() -> None:
    """The canary's empty commit: a new head sha, an identical tree, a full run, no output."""
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=False,
    )
    assert (decision.review, decision.tier, decision.reason) == (False, "re-review", "no_reviewable_changes")


def test_an_unchanged_head_on_the_same_base_skips_without_comparing_trees() -> None:
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=HEAD)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=None,
    )
    assert (decision.review, decision.reason) == (False, "no_reviewable_changes")


def test_a_base_branch_that_advances_is_reviewed_again_even_with_an_identical_tree() -> None:
    """`BASE...pr-head` changes meaning when the base moves, so the merged tree is a different thing to review."""
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=OLDER_BASE, head_sha=HEAD)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=False,
    )
    assert (decision.review, decision.reason) == (True, "base_advanced")


def test_a_base_merge_into_the_branch_is_reviewed() -> None:
    """The canary's second review: a base merge moved the base *and* the head, and the finding left the diff range."""
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=OLDER_BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=True,
    )
    assert (decision.review, decision.reason) == (True, "base_advanced")


def test_an_opened_ready_or_reopened_event_is_always_reviewed() -> None:
    """The event is a statement about the pull request, so it outranks a tree comparison."""
    for action in ("opened", "ready_for_review", "reopened"):
        decision = gate.classify(
            action=action,
            history=_history(gate.Record(base_sha=BASE, head_sha=HEAD)),
            head_sha=HEAD,
            base_sha=BASE,
            changed=False,
        )
        assert decision.review is True, action
        assert decision.reason == f"event_{action}"


# --- history that is missing, incomplete or unreadable ----------------------------------------------------


def test_unreadable_history_is_reviewed_and_never_reported_as_a_first_review() -> None:
    """A gate that cannot see the history must not claim there is none: that discards findings it never read."""
    decision = gate.classify(
        action="synchronize", history=_history(available=False), head_sha=HEAD, base_sha=BASE, changed=None
    )
    assert (decision.review, decision.tier, decision.reason) == (True, "unknown", "history_unavailable")


def test_an_unparseable_previous_record_is_reviewed_as_a_re_review() -> None:
    """An earlier review exists but its baseline does not, so the tier is known and the comparison is not."""
    decision = gate.classify(
        action="synchronize",
        history=_history(record=None, earlier=True),
        head_sha=HEAD,
        base_sha=BASE,
        changed=None,
    )
    assert (decision.review, decision.tier, decision.reason) == (True, "re-review", "history_incomplete")


def test_an_unavailable_comparison_is_reviewed_rather_than_skipped() -> None:
    """A commit that cannot be fetched is not evidence of no change."""
    decision = gate.classify(
        action="synchronize",
        history=_history(gate.Record(base_sha=BASE, head_sha=OLDER)),
        head_sha=HEAD,
        base_sha=BASE,
        changed=None,
    )
    assert (decision.review, decision.reason) == (True, "comparison_unavailable")


# --- reading the record out of the summary comments -------------------------------------------------------


def test_the_newest_parseable_summary_is_the_baseline() -> None:
    comments = [
        _comment("claude[bot]", _summary(OLDER_BASE, OLDER)),
        _comment("claude[bot]", _summary(BASE, HEAD)),
    ]
    assert gate.read_history(comments).record == gate.Record(base_sha=BASE, head_sha=HEAD)


def test_duplicated_summaries_for_one_head_do_not_confuse_the_baseline() -> None:
    """An on-demand review and an automatic one can both comment on the same head."""
    comments = [
        _comment("claude[bot]", _summary(BASE, OLDER)),
        _comment("claude[bot]", _summary(BASE, HEAD)),
        _comment("claude", _summary(BASE, HEAD)),
    ]
    assert gate.read_history(comments).record == gate.Record(base_sha=BASE, head_sha=HEAD)


def test_both_spellings_of_the_bot_author_are_recognised() -> None:
    """REST reports the Claude app as `claude[bot]`; GraphQL reports the same bot as `claude`."""
    for login in ("claude", "claude[bot]", "Claude[bot]"):
        assert gate.read_history([_comment(login, _summary(BASE, HEAD))]).earlier_review is True, login


def test_a_human_comment_that_quotes_the_heading_is_not_a_record() -> None:
    """Only this reviewer's own summaries are evidence of a review, or a quoted template becomes a false baseline."""
    comments = [
        _comment("elewashy", _summary(BASE, HEAD)),
        _comment("dependabot[bot]", _summary(BASE, HEAD)),
    ]
    history = gate.read_history(comments)
    assert (history.earlier_review, history.record) == (False, None)


def test_a_bot_comment_without_the_heading_but_with_both_commits_is_still_a_record() -> None:
    """The live reviewer wrote exactly this on #235: the template's *content*, flattened into one paragraph.

    Requiring the heading threw that away, so the gate saw no earlier review at all — and a re-review announced itself
    as a first review. A body that still names both commits is a record whatever its shape; only a body that names
    neither is not.
    """
    flattened = (
        "Code review. Base 615ad97999e75ffb562e298c0b235e0dca972959, Head "
        "6cfac7e31b8887659bd735e06b0684a47664c443. Reviewed 1 file, +5/-5. Tier: first review."
    )
    history = gate.read_history([_comment("claude[bot]", flattened)])
    assert history.earlier_review is True
    assert history.record == gate.Record(
        base_sha="615ad97999e75ffb562e298c0b235e0dca972959", head_sha="6cfac7e31b8887659bd735e06b0684a47664c443"
    )


def test_a_bot_comment_that_names_neither_commit_is_not_a_record() -> None:
    """Prose is not a record. Only this reviewer's own summaries count, and only when they say what was read."""
    assert gate.read_history([_comment("claude", "No blocking findings.")]).record is None


def test_a_short_hex_string_in_prose_is_not_mistaken_for_a_commit() -> None:
    """The loose patterns need a full sha: a finding quoting a 7-character prefix is prose, not a baseline."""
    assert gate.parse_record("## Code review\n\nline 157, prefix 6cfac7e, Base b)") is None


def test_the_timeline_recovers_the_tier_when_the_summary_is_unreadable() -> None:
    """GitHub's event stream is the fallback: the model does not write it, so a format change cannot break it."""
    events = [
        {"event": "committed", "sha": OLDER},
        {"event": "reviewed", "commit_id": OLDER, "user": {"login": "claude"}},
        {"event": "commented", "user": {"login": "claude[bot]"}, "body": "one flattened paragraph"},
    ]
    history = gate.read_timeline(events)
    assert history is not None
    assert (history.earlier_review, history.record) == (True, gate.Record(base_sha="", head_sha=OLDER))


def test_the_timeline_falls_back_to_the_last_commit_when_a_summary_only_review_has_no_commit_id() -> None:
    """A review with no inline findings has no ``reviewed`` event; the commit that arrived before its summary is it."""
    events = [
        {"event": "committed", "sha": OLDER_BASE},
        {"event": "committed", "sha": HEAD},
        {"event": "commented", "user": {"login": "claude[bot]"}, "body": "No blocking findings."},
    ]
    history = gate.read_timeline(events)
    assert history is not None and history.record is not None
    assert history.record.head_sha == HEAD


def test_another_authors_review_event_is_not_this_reviewers_history() -> None:
    events = [
        {"event": "committed", "sha": OLDER},
        {"event": "reviewed", "commit_id": OLDER, "user": {"login": "elewashy"}},
        {"event": "commented", "user": {"login": "elewashy"}, "body": "@claude review"},
    ]
    assert gate.read_timeline(events) is None


def test_a_timeline_record_never_licenses_a_skip() -> None:
    """The base is not in the timeline. A recovered head is enough for the tier and not enough to skip a review."""
    history = gate.History(available=True, earlier_review=True, record=gate.Record(base_sha="", head_sha=HEAD))
    decision = gate.classify(action="synchronize", history=history, head_sha=HEAD, base_sha=BASE, changed=False)
    assert decision == gate.Decision(review=True, tier=gate.RE_REVIEW, reason="history_incomplete")


def test_an_incomplete_summary_leaves_the_baseline_unknown_but_the_tier_a_re_review() -> None:
    """A run killed mid-comment still proves a review happened; it just cannot say what it read."""
    history = gate.read_history([_comment("claude[bot]", _summary(BASE, HEAD).replace(f"`{HEAD}`", ""))])
    assert (history.earlier_review, history.record) == (True, None)


def test_a_record_round_trips_through_the_summary_format() -> None:
    assert gate.parse_record(_summary(OLDER_BASE, OLDER)) == gate.Record(base_sha=OLDER_BASE, head_sha=OLDER)
    # The skill must also say *how* to post it: a one-line `--body` string is what flattened the live summary on #235.
    skill = REVIEW_SKILL.read_text(encoding="utf-8")
    assert "--body-file" in skill


# --- the tree comparison, against a real repository -------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "--quiet", "-b", "main")
    _git(tmp_path, "config", "user.email", "gate@example.invalid")
    _git(tmp_path, "config", "user.name", "gate")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "one")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_an_empty_commit_is_not_a_reviewable_change_and_an_edit_is(repo: Path) -> None:
    """The canary's push was an empty commit; a one-byte edit is the same comparison answering the other way."""
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    _git(repo, "commit", "--quiet", "--allow-empty", "-m", "nothing changed")
    empty = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    assert empty.stdout != first.stdout, "the empty commit must move the head, or the test proves nothing"
    assert gate.tree_changed(first.stdout.strip(), empty.stdout.strip()) is False

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "--quiet", "-am", "a real change")
    edited = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    assert gate.tree_changed(empty.stdout.strip(), edited.stdout.strip()) is True


def test_a_governance_or_workflow_change_counts_as_reviewable(repo: Path) -> None:
    """No path filter, on purpose: a PLAN, workflow or instruction change is reviewable here.

    `REVIEW.md` reviews documents as assertions, so an allow list of "code" extensions would skip exactly the changes
    that move the standard a later review is held to.
    """
    for name in ("PLAN/V2/ARCHITECTURE_V2.md", ".github/workflows/ci.yaml", "REVIEW.md"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "--quiet", "-m", name)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
        assert gate.tree_changed(f"{head.stdout.strip()}~1", head.stdout.strip()) is True, name


def test_a_comparison_with_a_missing_commit_is_unavailable_not_unchanged(repo: Path) -> None:
    """The unsafe default would be `False`: it reads as "nothing changed" and skips a review that never happened."""
    assert gate.tree_changed("0" * 40, "1" * 40) is None


# --- the seam: the workflow asks, and the skill writes what this parses ------------------------------------


def test_the_workflow_asks_the_gate_before_the_review_runs() -> None:
    workflow = REVIEW_WORKFLOW.read_text(encoding="utf-8")
    gate_step = re.search(r"(?ms)^      - name: .*\n        id: gate\n(.*?)^      - name: ", workflow)
    assert gate_step, "the review workflow must run the gate as a step before the review"
    assert ".github/scripts/review_gate.py" in gate_step.group(1)
    assert "REPOSITORY: ${{ github.repository }}" in gate_step.group(1), "the gate needs the canonical identity"
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in gate_step.group(1)
    # The expensive step is conditional on the gate's answer, or the gate is decoration.
    review_step = re.search(r"(?ms)^      - name: Run the review\n(.*?)(?=^      - |\Z)", workflow)
    assert review_step, "the review step must exist"
    assert "if: steps.gate.outputs.review == 'true'" in review_step.group(1)


def test_the_gate_parses_the_summary_format_the_skill_promises_to_write() -> None:
    """The contract between the two files, checked rather than assumed.

    The gate reads the baseline out of `**Base:** … · **Head:** …`. If the skill's template is reworded, the gate keeps
    working and silently loses every baseline — a first review on every push — so the format is pinned from both ends.
    """
    skill = REVIEW_SKILL.read_text(encoding="utf-8")
    template = re.search(r"(?m)^\*\*Base:\*\* `.*$", skill)
    assert template, "the skill must give the summary's header line"
    rendered = template.group(0).replace("<base sha>", OLDER_BASE).replace("<head sha>", OLDER)
    assert gate.parse_record(rendered) == gate.Record(base_sha=OLDER_BASE, head_sha=OLDER), rendered


# --- the gate's own failure modes, through its real entry point -------------------------------------------


def _write_outputs(tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    output = tmp_path / "out"
    summary = tmp_path / "summary"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert gate.main([]) == 0, "the gate never fails the job: a decision it cannot make is a review"
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)


def _event(head: str = HEAD, base: str = BASE, action: str = "synchronize") -> dict[str, str]:
    return {
        "REPOSITORY": "eMinders/ChronaMind",
        "PR_NUMBER": "231",
        "HEAD_SHA": head,
        "BASE_SHA": base,
        "ACTION": action,
    }


def test_the_decision_reaches_the_workflow_outputs_the_review_step_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`steps.gate.outputs.*` names are a contract with the workflow; a rename here silently skips reviews."""
    monkeypatch.setattr(gate, "fetch_history", lambda *_: gate.History(True, False, None))
    outputs = _write_outputs(tmp_path, _event(action="opened"), monkeypatch)
    assert outputs["review"] == "true"
    assert outputs["tier"] == "first review"
    assert outputs["reason"] == "no_earlier_review"
    assert set(outputs) == {"review", "tier", "reason", "previous_head_sha", "previous_base_sha"}


def test_a_crashed_gate_reviews_rather_than_skipping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worst failure mode is a gate that cannot decide and is read as "nothing to review"."""

    def explode(*_: object) -> None:
        raise RuntimeError("gh is not installed")

    monkeypatch.setattr(gate, "fetch_history", explode)
    outputs = _write_outputs(tmp_path, _event(), monkeypatch)
    assert (outputs["review"], outputs["tier"], outputs["reason"]) == ("true", "unknown", "gate_failed")


def test_the_gate_skips_only_when_it_has_a_baseline_and_the_tree_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "fetch_history", lambda *_: gate.History(True, True, gate.Record(BASE, OLDER)))
    monkeypatch.setattr(gate, "tree_changed", lambda *_: False)
    outputs = _write_outputs(tmp_path, _event(), monkeypatch)
    assert (outputs["review"], outputs["reason"]) == ("false", "no_reviewable_changes")
    assert outputs["previous_head_sha"] == OLDER


# --- end to end, as the workflow runs it: real git, a stub `gh`, the real script ---------------------------

_STUB_GH = """#!/bin/sh
# A stand-in for `gh`, so the gate's real command paths run without a network or a token.
case "$*" in
  *"/issues/"*"/comments"*) cat "$STUB_COMMENTS" ;;
  *"/pulls/"*"/files"*)     cat "$STUB_FILES" ;;
  *)                        echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


def _run_gate(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    comments: list[dict[str, object]],
    files: list[str] | None = None,
    action: str = "synchronize",
    base: str = "b" * 40,
) -> tuple[dict[str, str], str]:
    """Run the script the way the workflow does: as a subprocess, with `gh` on PATH and outputs to files."""
    stub_dir = repo / "stub"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "gh"
    stub.write_text(_STUB_GH, encoding="utf-8")
    stub.chmod(0o755)
    (repo / "comments.json").write_text(json.dumps(comments), encoding="utf-8")
    (repo / "files.json").write_text(json.dumps([{"filename": f} for f in (files or [])]), encoding="utf-8")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    out, summary = repo / "out", repo / "summary"
    env = {
        "PATH": f"{stub_dir}:/usr/bin:/bin",
        "STUB_COMMENTS": str(repo / "comments.json"),
        "STUB_FILES": str(repo / "files.json"),
        "REPOSITORY": "eMinders/ChronaMind",
        "PR_NUMBER": "1",
        "HEAD_SHA": head.stdout.strip(),
        "BASE_SHA": base,
        "ACTION": action,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    monkeypatch.chdir(repo)
    completed = subprocess.run([sys.executable, str(GATE)], env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
    return outputs, summary.read_text(encoding="utf-8")


def test_an_identical_tree_push_writes_review_false_end_to_end(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The canary's exact push, through the script the workflow runs rather than through `classify`.

    The reviewed head is the current head, and the tree is identical, so the gate must answer "do not review" — and it
    must do that *before* the review step, which is what `steps.gate.outputs.review == 'true'` gates. That the action
    is never invoked (and so no token is minted) follows from the workflow, pinned separately below.
    """
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout
    outputs, summary = _run_gate(
        repo, monkeypatch, comments=[_comment("claude[bot]", _summary("b" * 40, head.strip()))]
    )
    assert (outputs["review"], outputs["reason"], outputs["tier"]) == ("false", "no_reviewable_changes", "re-review")
    assert "skip" in summary.lower() and "nothing new to read" in summary.lower(), summary


def test_a_push_that_changes_the_tree_writes_review_true_end_to_end(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same comparison: a one-line edit must be reviewed, at the re-review tier."""
    previous = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "--quiet", "-am", "a change")
    outputs, _ = _run_gate(
        repo, monkeypatch, comments=[_comment("claude", _summary("b" * 40, previous.stdout.strip()))]
    )
    assert (outputs["review"], outputs["reason"]) == ("true", "push_changed_the_tree")
    assert outputs["previous_head_sha"] == previous.stdout.strip()


def test_a_pull_request_that_edits_the_review_workflow_is_flagged_as_unreviewable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The action skips when the running workflow file differs from the default branch's, and says nothing.

    That gap is why a workflow change cannot be reviewed automatically. The gate cannot lift the platform rule, so it
    reports it and names the path that works: `@claude review`, an `issue_comment` event whose workflow *is* the
    default branch's. Silence here is how a workflow change ships reviewed by nobody.
    """
    outputs, summary = _run_gate(
        repo,
        monkeypatch,
        comments=[],
        files=[".github/workflows/claude-code-review.yml", "REVIEW.md"],
        action="opened",
    )
    assert outputs["review"] == "true", "the gate still decides to review; the platform is what skips"
    assert "No automatic review for this pull request" in summary
    assert "@claude review" in summary
    assert "claude-code-review.yml" in summary
    # A sentence, not a comma splice: this text is read by a maintainer under a green check.
    assert "claude.yml, so the action" not in summary
    assert gate._and_list(["a"]) == "`a`"
    assert gate._and_list(["a", "b"]) == "`a` and `b`"
    assert gate._and_list(["a", "b", "c"]) == "`a`, `b` and `c`"


def test_a_pull_request_that_does_not_edit_the_workflow_gets_no_such_warning(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No false alarm on the ordinary case, or the warning becomes noise nobody reads."""
    _, summary = _run_gate(repo, monkeypatch, comments=[], files=["custom-memory-engine/src/x.py"], action="opened")
    assert "No automatic review" not in summary


def test_an_unreadable_file_list_does_not_block_the_review(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The warning is an extra; the decision must not depend on it."""
    stub_dir = repo / "stub2"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\ncase "$*" in *"/comments"*) echo "[]" ;; *) exit 1 ;; esac\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {
        "PATH": f"{stub_dir}:/usr/bin:/bin",
        "REPOSITORY": "eMinders/ChronaMind",
        "PR_NUMBER": "1",
        "HEAD_SHA": "a" * 40,
        "BASE_SHA": "b" * 40,
        "ACTION": "opened",
        "GITHUB_OUTPUT": str(repo / "out2"),
        "GITHUB_STEP_SUMMARY": str(repo / "sum2"),
    }
    monkeypatch.chdir(repo)
    completed = subprocess.run([sys.executable, str(GATE)], env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "review=true" in (repo / "out2").read_text(encoding="utf-8")


def test_the_gate_script_is_python_312_compatible() -> None:
    """The runner's `python3` is 3.12, while the root project's Ruff targets 3.14 and rewrites code as such.

    Ruff 0.17 with a 3.14 target strips the parentheses from `except (A, B, C):` — valid 3.14 (PEP 758), a SyntaxError
    on 3.12. This exact slip happened while writing the gate, and the failure mode is a red job on every pull request.
    """
    source = GATE.read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 12))
    assert "def " in source
