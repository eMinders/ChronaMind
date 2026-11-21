"""Resolve the review threads the reviewer verified, with the job's own token.

Why this is a script and not a sentence in the skill: `resolveReviewThread` is gated on **Contents: write**, and the
token the model runs with is the Claude App installation's, which this repository cannot grant (measured on #236 and
again on #238: `viewerCanResolve: false`, reported truthfully as `thread open: not permitted`). The job's own
`GITHUB_TOKEN` does hold it — a probe workflow resolved a real thread with it and got `isResolved: true` — so the
decision stays with the reviewer and the mutation happens here, one layer down, with a token the repository controls.

The seam is a marker the reviewer writes at the end of its summary:

    <!-- resolve-threads: PRRT_kwDOVGQehs66NM66,PRRT_kwDOVGQehs66VoOt -->

Only ids the reviewer verified as fixed appear in it. Nothing here decides whether a fix is real; it enforces the three
things a model should not be trusted with:

* the marker must come from this reviewer's own comment on this pull request (the autocomplete for "resolve" is not
  someone else's);
* every id must be a thread on this pull request whose authorship is this reviewer's and which is still open — an
  unknown id, a human's thread, another bot's, or one already resolved is skipped with a warning, never resolved;
* the result must come back `isResolved: true`, and the run log says so per thread, so a claimed resolution is never
  just a claim.

Fail-soft by construction: a resolution that cannot be performed is a warning on the run and an open thread, which is
what a human would see anyway. It never fails the review, and it never blocks the job that already posted the findings.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Authors whose comment may carry the marker: REST spells the app `claude[bot]`, GraphQL spells it `claude`.
OWN_AUTHORS = frozenset({"claude", "claude[bot]"})

#: The marker, on one line, in an HTML comment so it is invisible in the rendered review.
MARKER = re.compile(r"<!--\s*resolve-threads:\s*([^>]*?)\s*-->")

#: GitHub's thread ids are opaque base64-ish node ids; anything else is not one and is not passed to the mutation.
THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

THREADS_QUERY = """query($o:String!,$n:String!,$p:Int!){
  repository(owner:$o,name:$n){pullRequest(number:$p){
    reviewThreads(first:100){nodes{id isResolved path comments(first:5){nodes{author{login}}}}}}}}\n"""

RESOLVE_MUTATION = """mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{id isResolved}}}\n"""


def parse_markers(body: str) -> list[str]:
    """Every thread id the marker names, in order, de-duplicated; ``[]`` when there is no marker.

    Only the first marker in a body is read: a review that emits two is a review that contradicted itself, and guessing
    which one it meant is worse than resolving none of them.
    """
    match = MARKER.search(body)
    if match is None:
        return []
    seen: dict[str, None] = {}
    for candidate in match.group(1).split(","):
        thread = candidate.strip()
        if THREAD_ID.match(thread):
            seen.setdefault(thread, None)
    return list(seen)


def own_claims(comments: Iterable[Mapping[str, Any]], since: str | None = None) -> list[str]:
    """Thread ids claimed by this reviewer's own comments, oldest comment first.

    ``since`` is an ISO-8601 timestamp: a marker left by an earlier run was already acted on, and re-acting on it would
    resolve a thread this run never verified.

    A login alone is not enough. `claude` is a legal human username, and a marker in such an account's comment would
    otherwise resolve threads on the bot's behalf, so REST's ``user.type`` must say ``Bot`` when it is present (GraphQL
    payloads omit it, and the pull-request check below still requires a review thread of that author).
    """
    claims: list[str] = []
    for comment in comments:
        user = comment.get("user") or {}
        if str(user.get("login", "")).lower() not in OWN_AUTHORS:
            continue
        kind = str(user.get("type") or "")
        if kind and kind.lower() != "bot":
            continue
        if since and str(comment.get("created_at") or "") < since:
            continue
        claims.extend(parse_markers(str(comment.get("body") or "")))
    return claims


def resoluble(threads: Iterable[Mapping[str, Any]], claimed: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split the claimed ids into those this reviewer may resolve and those it may not, with a reason for each."""
    by_id = {str(thread.get("id")): thread for thread in threads}
    allowed: list[str] = []
    refused: list[str] = []
    for thread_id in claimed:
        thread = by_id.get(thread_id)
        if thread is None:
            refused.append(f"{thread_id}: not a thread on this pull request")
            continue
        if bool(thread.get("isResolved")):
            refused.append(f"{thread_id}: already resolved")
            continue
        authors = {
            str((node.get("author") or {}).get("login", "")).lower()
            for node in ((thread.get("comments") or {}).get("nodes") or [])
        }
        if not authors or not authors <= OWN_AUTHORS:
            refused.append(f"{thread_id}: not this reviewer's thread ({', '.join(sorted(authors)) or 'no author'})")
            continue
        allowed.append(thread_id)
    return allowed, refused


# --- I/O: thin, so the decisions above stay testable without a token ----------------------------------------------------


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _api_json(arguments: Sequence[str]) -> Any | None:
    result = _run(["gh", "api", *arguments])
    if result.returncode != 0:
        print(f"::warning::resolve-threads: api call failed: {result.stderr.strip()[:300]}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("::warning::resolve-threads: api returned non-JSON")
        return None


def fetch_claims(repository: str, pull_request: str, since: str | None) -> list[str]:
    """The thread ids this reviewer's own comments claim, since the given timestamp."""
    comments = _api_json(["--paginate", f"repos/{repository}/issues/{pull_request}/comments"])
    if not isinstance(comments, list):
        return []
    return own_claims(comments, since)


def fetch_threads(repository: str, pull_request: str) -> list[dict[str, Any]]:
    """Every review thread on the pull request, with its author and state."""
    owner, _, name = repository.partition("/")
    payload = _api_json(
        [
            "graphql",
            "-f",
            f"query={THREADS_QUERY}",
            "-F",
            f"o={owner}",
            "-F",
            f"n={name}",
            "-F",
            f"p={pull_request}",
        ]
    )
    nodes = ((((payload or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get(
        "reviewThreads", {}
    )
    if not isinstance(nodes, dict):
        return []
    return [thread for thread in (nodes.get("nodes") or []) if isinstance(thread, dict)]


def resolve(thread_id: str) -> bool:
    """Resolve one thread, returning whether GitHub answered ``isResolved: true``."""
    result = _run(["gh", "api", "graphql", "-f", f"query={RESOLVE_MUTATION}", "-F", f"t={thread_id}"])
    if result.returncode != 0:
        print(f"::warning::resolve-threads: {thread_id} not resolved: {result.stderr.strip()[:300]}")
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    thread = ((payload.get("data") or {}).get("resolveReviewThread") or {}).get("thread") or {}
    return thread.get("isResolved") is True


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve what the reviewer verified; never fail the run, never resolve something it did not verify."""
    del argv
    env = os.environ
    repository = env.get("REPOSITORY") or env.get("GITHUB_REPOSITORY") or ""
    pull_request = env.get("PR_NUMBER", "")
    since = env.get("SINCE") or None
    if not (repository and pull_request):
        print("::error::resolve-threads: REPOSITORY and PR_NUMBER are required", file=sys.stderr)
        return 1
    claims = fetch_claims(repository, pull_request, since)
    if not claims:
        print("resolve-threads: nothing claimed")
        return 0
    allowed, refused = resoluble(fetch_threads(repository, pull_request), claims)
    for reason in refused:
        print(f"::notice::resolve-threads: skipped {reason}")
    for thread_id in allowed:
        if resolve(thread_id):
            print(f"::notice::resolve-threads: resolved {thread_id} (GitHub confirms isResolved: true)")
        else:
            print(f"::warning::resolve-threads: {thread_id} is still open; a human can resolve it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
