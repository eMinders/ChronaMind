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

Both originally read a summary comment that matched the template in the skill *exactly*. On the first live run of the
finished reviewer it did not: the model posted the same content as one flattened paragraph, `Code review. Base <sha>,
Head <sha>. …`, with no heading and no bold field names. The gate then saw no earlier review at all — so a re-review
announced itself as a first review (the failure of #231, reintroduced by a formatting difference) and no empty push
could ever be skipped. Trusting a model's formatting for a **policy** decision is the mistake: the tier is now also read
from GitHub's own event stream, which the model does not write (`.github/workflows/README.md`, "Where the baseline comes
from"). The summary is parsed first because it is exact, and the timeline is the fallback that keeps the tier right when
the prose is not.

Both are pure functions of the event payload, the review history and a `git diff`, so both are unit-tested in
`core/memory-engine/tests/test_review_gate.py` without a network, a token or a runner.

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
#: The same two fields written as prose (`Base 615ad97…, Head 6cfac7e…`) — the shape a model produces when it builds one
#: long `--body` string instead of passing a file. A full 40-character sha is required, so a short hex word in a finding
#: ("a 7-character prefix") cannot be mistaken for one of these.
_BASE_ANY = re.compile(r"\bBase\b[^\n]{0,16}?\b([0-9a-f]{40})\b")
_HEAD_ANY = re.compile(r"\bHead\b[^\n]{0,16}?\b([0-9a-f]{40})\b")

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
    base, head = _BASE_SHA.search(body) or _BASE_ANY.search(body), _HEAD_SHA.search(body) or _HEAD_ANY.search(body)
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
        # The heading promised by the skill, *or* a body that still names both commits: the live reviewer flattened one
        # summary into a single paragraph (`Code review. Base <sha>, Head <sha>. …`) and the heading test alone threw
        # away the only record of what it had read.
        if login in OWN_AUTHORS and (REVIEW_HEADING in body or parse_record(body) is not None):
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


def read_timeline(events: Iterable[Mapping[str, Any]]) -> History | None:
    """The same history, read from GitHub's event stream — the path that does not depend on how the summary was written.

    Two event kinds matter, and both are GitHub's own record rather than the model's prose:

    * ``committed``, which carries the sha of each commit as it reaches the pull request;
    * ``reviewed`` / ``commented`` by this reviewer, in order.

    The head a review read is therefore the last commit to arrive before that review's event. A ``reviewed`` event
    carries ``commit_id`` outright, which is exact — it is the commit the review object was submitted against — and it
    is preferred. ``None`` means the stream says nothing about this reviewer.

    The base commit is *not* in the timeline, so a record recovered here carries ``base_sha=""``. The caller treats that
    as an unknown baseline and reviews: a tier is recoverable without the base, a *skip* is not, and a gate that guessed
    the base could skip a review whose base had moved.
    """
    last_commit: str | None = None
    record: Record | None = None
    for event in events:
        kind = str(event.get("event") or "")
        if kind == "committed":
            sha = str(event.get("sha") or "")
            if sha:
                last_commit = sha
            continue
        if kind not in {"reviewed", "commented"}:
            continue
        user = event.get("user") or {}
        if str(user.get("login", "")).lower() not in OWN_AUTHORS:
            continue
        head = str(event.get("commit_id") or last_commit or "")
        if head:
            record = Record(base_sha="", head_sha=head)
    if record is None:
        return None
    return History(available=True, earlier_review=True, record=record)


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
    `core/memory-engine/tests/test_review_gate.py`.
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
    if not history.record.base_sha:
        # Recovered from the timeline: the head is known and the base is not. Correct tier, no skip.
        return Decision(review=True, tier=tier, reason="history_incomplete")
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


def _api_list(repository: str, path: str, what: str) -> list[Any] | None:
    """One paginated REST list, or ``None`` (with a logged reason) when it cannot be read."""
    result = _run(["gh", "api", "--paginate", f"repos/{repository}/{path}"])
    if result.returncode != 0:
        print(f"::warning::could not read {what}: {result.stderr.strip()[:400]}")
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"::warning::{what} was not JSON")
        return None
    return payload if isinstance(payload, list) else None


def fetch_history(repository: str, pull_request: str) -> History:
    """Read the review history: the summary comments first, GitHub's event stream when they do not answer.

    The comments are authoritative because they carry the *base* as well as the head. The timeline is the fallback, and
    it is what keeps the tier right when the summary is written in a shape this script does not recognise — a formatting
    difference must never turn a re-review into a first review again (see the module docstring).
    """
    comments = _api_list(repository, f"issues/{pull_request}/comments", "review history")
    if comments is None:
        return History(available=False, earlier_review=False, record=None)
    history = read_history(comments)
    if history.earlier_review:
        return history
    events = _api_list(repository, f"issues/{pull_request}/timeline", "the pull request timeline")
    if events is None:
        # Not "no earlier review": the comments did not answer and neither did the timeline, so this is an unreadable
        # history, which `classify` turns into an unknown tier and a review. Reporting a first review here would let a
        # transient 500 re-create the #231 failure — the summary was unrecognisable *and* the fallback failed.
        return History(available=False, earlier_review=False, record=None)
    return read_timeline(events) or history


def fetch_changed_files(repository: str, pull_request: str) -> list[str] | None:
    """The paths the pull request changes, or ``None`` when the API will not say.

    From the API rather than from `git diff`: the base and head are separate checkouts, so the base clone need not hold
    the head commit. Used only to warn about the one platform limitation below, never to decide whether to review.
    """
    result = _run(["gh", "api", f"repos/{repository}/pulls/{pull_request}/files", "--paginate"])
    if result.returncode != 0:
        return None
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    return [str(entry["filename"]) for entry in entries if isinstance(entry, dict) and "filename" in entry]


#: The workflow whose content must match the default branch for the action to mint a token at all.
REVIEW_WORKFLOW_PATHS = (".github/workflows/claude-code-review.yml", ".github/workflows/claude.yml")


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
    platform_gap = _platform_gap(decision, repository, pull_request)
    _publish(
        env,
        {
            "review": "true" if decision.review else "false",
            "tier": decision.tier,
            "reason": decision.reason,
            "previous_head_sha": previous.head_sha if previous else "",
            "previous_base_sha": previous.base_sha if previous else "",
        },
        summary=_summary(decision, repository, pull_request, head_sha, base_sha, previous, platform_gap),
    )
    if not decision.review:
        # Not silent: the run graph shows the skipped step, and this annotation is what explains it.
        print(
            f"::notice::No reviewable changes since {previous.head_sha if previous else 'the last review'}; "
            f"the expensive review step is skipped."
        )
    if platform_gap:
        print(f"::warning::{platform_gap}")
    return 0


def _platform_gap(decision: Decision, repository: str, pull_request: str) -> str | None:
    """Warn when this pull request changes the review workflow, which the action cannot review.

    The action mints its token through an OIDC exchange that GitHub refuses unless the running workflow file is
    *identical to the default branch's* (`src/github/token.ts`, `workflow_not_found_on_default_branch`). So a pull
    request that edits `.github/workflows/claude-code-review.yml` is never reviewed automatically — the job goes green
    with the action skipped. Nothing in this workflow can lift that, and buying around it by weakening the check is not
    an option, so the reviewer has to be asked for by a maintainer: the `@claude review` job runs on an `issue_comment`
    event, whose workflow *is* the default branch's, so it reviews any pull request including this one.
    """
    if not decision.review:
        return None
    changed = fetch_changed_files(repository, pull_request)
    if changed is None:
        return None
    edited = sorted(set(changed) & set(REVIEW_WORKFLOW_PATHS))
    if not edited:
        return None
    return (
        f"this pull request edits {_and_list(edited)}, so the action will skip and no automatic review will run; ask "
        "for one with a `@claude review` comment, which reviews through the default branch's workflow"
    )


def _and_list(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c` — a sentence, not a comma splice."""
    quoted = [f"`{item}`" for item in items]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _summary(
    decision: Decision,
    repository: str,
    pull_request: str,
    head_sha: str,
    base_sha: str,
    previous: Record | None,
    platform_gap: str | None = None,
) -> str:
    lines = [
        f"### Claude review: {decision.tier}",
        "",
        f"- Decision: **{'review' if decision.review else 'skip'}** — `{decision.reason}`",
        f"- Head: `{head_sha}` · Base: `{base_sha}`",
    ]
    if previous is not None:
        lines.append(f"- Last reviewed: `{previous.head_sha}` on base `{previous.base_sha}`")
    if platform_gap:
        lines += ["", f"> **No automatic review for this pull request.** {platform_gap[0].upper()}{platform_gap[1:]}."]
    if not decision.review:
        lines += [
            "",
            f"Nothing changed in the tree since `{previous.head_sha if previous else 'the last review'}`"
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
