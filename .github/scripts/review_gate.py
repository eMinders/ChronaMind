"""Decide whether a push needs a review, and whether the run is a first review or a re-review.

Two decisions, and both are the ones the canary got wrong (`.github/workflows/README.md`, "Verifying the reviewer"):

* **Tier.** `review-pr` used to infer "am I a re-review?" from the shape of the diff. When the base branch advances the
  diff range moves, the earlier findings fall outside it, and a re-review silently presents itself as a first review —
  so the reviewer re-reported nothing, left its own threads unresolved and reviewed a fix commit as though it were the
  whole change. The tier is now read from the review history on the pull request, before the model runs, and passed to
  it as an input rather than left to be guessed.
* **Cost.** A push that changes no tree (an empty commit, a rebase onto the same tree) used to buy a full review run
  and post nothing. `synchronize` fires on the push, so the only way to know is to compare the pushed commit with the
  one that was actually reviewed — which is what the previous summary comment records.

Both are pure functions of the event payload, the review history and a `git diff`, so both are unit-tested in
`custom-memory-engine/tests/test_review_gate.py` without a network, a token or a runner.

Deliberately *not* a path filter: a change to `PLAN/V2/`, `.github/` or an instruction file is reviewable here
(`REVIEW.md` §"Documents are code here"), so the gate asks whether the tree changed at all and nothing more. An
allow list of "interesting" paths is how a governance change gets skipped as a docs change.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Authors whose summary comment is this reviewer's own record. REST reports the app as ``claude[bot]``; GraphQL
#: reports the same bot as ``claude``. Both are accepted, and neither is trusted for anything but history.
OWN_AUTHORS = frozenset({"claude", "claude[bot]"})

#: The heading the skill promises to write, and the fields this script parses back out of it.
REVIEW_HEADING = "## Code review"
_BASE_SHA = re.compile(r"\*\*Base:\*\*\s*`([0-9a-f]{7,40})`")
_HEAD_SHA = re.compile(r"\*\*Head:\*\*\s*`([0-9a-f]{7,40})`")

#: What the gate decided, and why. `reason` is machine-readable; the description is for the person reading the run.
REVIEW = "review"
SKIP = "skip"

FIRST_REVIEW = "first review"
RE_REVIEW = "re-review"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Record:
    """What one earlier review reviewed: the pair of commits whose difference it read."""

    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class History:
    """What the pull request's comment history says about earlier reviews.

    ``available`` is False when the history could not be read at all (an API failure, a missing token). That is not the
    same as "there is no history": a gate that cannot see the history must review rather than claim a first review and
    discard findings it never read.
    """

    available: bool
    earlier_review: bool
    record: Record | None


@dataclass(frozen=True)
class Decision:
    """Whether to run the review, at which tier, and the reason (machine-readable, stable, tested)."""

    review: bool
    tier: str
    reason: str

    @property
    def description(self) -> str:
        """One human-readable line for the run log and the step summary."""
        return f"{'review' if self.review else 'skip'}: {self.reason} (tier: {self.tier})"


def parse_record(body: str) -> Record | None:
    """Read the base and head commits out of one summary comment; ``None`` when it is not a parseable record.

    An incomplete summary (a run killed mid-comment, a hand-written comment that quoted the heading) yields ``None``
    rather than a guess: a half-parsed record would compare the wrong commits, and skipping on it would hide a change.
    """
    base, head = _BASE_SHA.search(body), _HEAD_SHA.search(body)
    if base is None or head is None:
        return None
    return Record(base_sha=base.group(1), head_sha=head.group(1))


def own_review_bodies(comments: Iterable[Mapping[str, Any]]) -> list[str]:
    """The summary comments this reviewer wrote, oldest first.

    Duplicates are kept on purpose: an on-demand `@claude review` and an automatic review can both comment on one head,
    and the newest parseable record wins in ``read_history`` regardless.
    """
    bodies = []
    for comment in comments:
        user = comment.get("user") or {}
        login = str(user.get("login", "")).lower()
        body = str(comment.get("body") or "")
        if login in OWN_AUTHORS and REVIEW_HEADING in body:
            bodies.append(body)
    return bodies


def read_history(comments: Iterable[Mapping[str, Any]]) -> History:
    """Classify the history, preferring the newest parseable record and tolerating the unparseable ones.

    Three outcomes, all distinct: no earlier review at all (a genuine first review), an earlier review whose record
    cannot be read (a re-review with an unknown baseline), and a readable record.
    """
    bodies = own_review_bodies(comments)
    if not bodies:
        return History(available=True, earlier_review=False, record=None)
    for body in reversed(bodies):
        record = parse_record(body)
        if record is not None:
            return History(available=True, earlier_review=True, record=record)
    return History(available=True, earlier_review=True, record=None)


def classify(
    *,
    action: str,
    history: History,
    head_sha: str,
    base_sha: str,
    changed: bool | None,
) -> Decision:
    """Decide the tier and whether this pull request needs a review now.

    ``changed`` is the result of comparing the pushed tree with the tree the last review read: ``True`` (a real change),
    ``False`` (the same tree — an empty commit, or a rebase onto itself) or ``None`` (the commits could not be compared,
    which is a reason to review, never a reason to skip).

    Every branch below is one line of the table in `.github/workflows/README.md`, and each is a test in
    `custom-memory-engine/tests/test_review_gate.py`.
    """
    if not history.available:
        return Decision(review=True, tier=UNKNOWN, reason="history_unavailable")
    if not history.earlier_review:
        return Decision(review=True, tier=FIRST_REVIEW, reason="no_earlier_review")
    tier = RE_REVIEW
    # A review of an `opened`/`ready_for_review`/`reopened` pull request is a review by definition, whatever the tree
    # comparison says: the event is a statement about the pull request's state, not about its commits.
    if action != "synchronize":
        return Decision(review=True, tier=tier, reason=f"event_{action}")
    if history.record is None:
        return Decision(review=True, tier=tier, reason="history_incomplete")
    # The reviewed baseline is a pair. A base that has advanced changes what `BASE...pr-head` means, so the merged tree
    # is a different thing to review even when the pushed tree is identical.
    if history.record.base_sha != base_sha:
        return Decision(review=True, tier=tier, reason="base_advanced")
    if history.record.head_sha == head_sha:
        return Decision(review=False, tier=tier, reason="no_reviewable_changes")
    if changed is True:
        return Decision(review=True, tier=tier, reason="push_changed_the_tree")
    if changed is False:
        return Decision(review=False, tier=tier, reason="no_reviewable_changes")
    return Decision(review=True, tier=tier, reason="comparison_unavailable")


# --- I/O: thin, and separate from the decisions above so they stay testable ---------------------------------


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def fetch_history(repository: str, pull_request: str) -> History:
    """Read the pull request's comments; an unreadable history is reported as unavailable, never as empty."""
    result = _run(["gh", "api", "--paginate", f"repos/{repository}/issues/{pull_request}/comments"])
    if result.returncode != 0:
        print(f"::warning::could not read review history: {result.stderr.strip()[:400]}")
        return History(available=False, earlier_review=False, record=None)
    try:
        comments = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("::warning::review history was not JSON")
        return History(available=False, earlier_review=False, record=None)
    return read_history(comments)


def _present(sha: str) -> bool:
    return _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def _fetch(sha: str) -> bool:
    _run(["git", "fetch", "--no-tags", "--quiet", "origin", sha])
    return _present(sha)


def tree_changed(previous: str, head: str) -> bool | None:
    """Whether the two commits differ at all; ``None`` when either cannot be read.

    A `git diff` of the trees, not of the commit messages: `--quiet` exits 0 for "identical", so an empty commit and a
    rebase that reproduces the same tree are both `False`, and a rename-only or governance-only change is `True`.
    """
    for sha in (previous, head):
        if not _present(sha) and not _fetch(sha):
            return None
    result = _run(["git", "diff", "--quiet", previous, head, "--", "."])
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Read the event from the environment, decide, and publish the answer to the workflow and the run summary.

    A gate that crashes must not silently skip a review: the alternative to a decision is a review, never a skip. The
    only loud failure is a misconfigured workflow (a missing input), which is visible on the first run and is not a
    reason to review every push forever.
    """
    del argv
    env = os.environ
    try:
        return _decide(env)
    except Exception as error:  # a gate that raises must fail open: the expensive step is upstream of the review
        print(f"::warning::review gate failed ({type(error).__name__}: {error}); reviewing anyway", file=sys.stderr)
        _publish(
            env,
            {
                "review": "true",
                "tier": UNKNOWN,
                "reason": "gate_failed",
                "previous_head_sha": "",
                "previous_base_sha": "",
            },
            summary="### Claude review: unknown\n\n- Decision: **review** — `gate_failed`; the gate could not decide.\n",
        )
        return 0


def _decide(env: Mapping[str, str]) -> int:
    """The gate proper: read the history, compare the trees, publish the decision."""
    repository = env.get("REPOSITORY") or env.get("GITHUB_REPOSITORY") or ""
    pull_request = env.get("PR_NUMBER", "")
    head_sha = env.get("HEAD_SHA", "")
    base_sha = env.get("BASE_SHA", "")
    action = env.get("ACTION", "synchronize")
    if not (repository and pull_request and head_sha and base_sha):
        print("::error::REPOSITORY, PR_NUMBER, HEAD_SHA and BASE_SHA are required", file=sys.stderr)
        return 1

    history = fetch_history(repository, pull_request)
    changed: bool | None = None
    # Only the synchronize path can skip, so the comparison is paid for only there.
    if action == "synchronize" and history.earlier_review and history.record is not None:
        changed = tree_changed(history.record.head_sha, head_sha)
    decision = classify(action=action, history=history, head_sha=head_sha, base_sha=base_sha, changed=changed)

    previous = history.record
    print(decision.description)
    _publish(
        env,
        {
            "review": "true" if decision.review else "false",
            "tier": decision.tier,
            "reason": decision.reason,
            "previous_head_sha": previous.head_sha if previous else "",
            "previous_base_sha": previous.base_sha if previous else "",
        },
        summary=_summary(decision, repository, pull_request, head_sha, base_sha, previous),
    )
    if not decision.review:
        # Not silent: the run graph shows the skipped step, and this annotation is what explains it.
        print(
            f"::notice::No reviewable changes since {previous.head_sha if previous else 'the last review'}; "
            f"the expensive review step is skipped."
        )
    return 0


def _summary(
    decision: Decision, repository: str, pull_request: str, head_sha: str, base_sha: str, previous: Record | None
) -> str:
    lines = [
        f"### Claude review: {decision.tier}",
        "",
        f"- Decision: **{'review' if decision.review else 'skip'}** — `{decision.reason}`",
        f"- Head: `{head_sha}` · Base: `{base_sha}`",
    ]
    if previous is not None:
        lines.append(f"- Last reviewed: `{previous.head_sha}` on base `{previous.base_sha}`")
    if not decision.review:
        lines += [
            "",
            f"Nothing changed in the tree since the review at `{previous.head_sha if previous else 'an earlier review'}`"
            " and the base branch is the same, so there is nothing new to read. Push a commit that changes the tree to"
            " trigger a review, or ask for one with `@claude review`.",
        ]
    return "\n".join(lines) + "\n"


def _publish(env: Mapping[str, str], outputs: Mapping[str, str], *, summary: str) -> None:
    if path := env.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")
    if path := env.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)


if __name__ == "__main__":
    sys.exit(main())
