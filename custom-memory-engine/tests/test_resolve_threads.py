"""The resolver acts on what the reviewer verified, and on nothing else.

Why it exists at all: the model's token cannot run `resolveReviewThread` (Contents: write; measured on #236 and #238,
`viewerCanResolve: false`), while the job's own token can — a probe resolved a real thread with it and got
`isResolved: true`. So the decision stays with the reviewer, which names the threads in a marker, and the mutation
happens one layer down. That makes the marker the trust boundary, and these are the tests for it:

* a marker is read only from this reviewer's own comment on this pull request;
* an id is resolved only when it is an unresolved thread whose comments all belong to this reviewer;
* a resolution is reported only when GitHub answers `isResolved: true`;
* nothing here fails a run, because the findings have already been posted by then.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github" / "scripts" / "resolve_threads.py"

THREAD = "PRRT_kwDOVGQehs66NM66"
OTHER_THREAD = "PRRT_kwDOVGQehs66VoOt"

#: Markdown that passes around it: the marker is an HTML comment and must survive being written inside prose.
BODY = f"""## Code review

**Base:** `{"b" * 40}` · **Head:** `{"a" * 40}` · **Tier:** re-review

### Earlier findings
1. 🔴 `entry.py::guard` — fix verified, named for resolution.

<!-- resolve-threads: {THREAD} -->
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("resolve_threads", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load()


def _comment(login: str, body: str, created: str = "2026-11-21T12:00:00Z") -> dict[str, object]:
    return {"user": {"login": login}, "body": body, "created_at": created}


def _thread(
    authors: tuple[str, ...] = ("claude",), *, resolved: bool = False, thread: str = THREAD
) -> dict[str, object]:
    return {
        "id": thread,
        "isResolved": resolved,
        "path": "custom-memory-engine/src/x.py",
        "comments": {"nodes": [{"author": {"login": author}} for author in authors]},
    }


def test_the_marker_is_read_from_the_summary() -> None:
    """The marker is an HTML comment so it never shows in the rendered review."""
    assert resolver.parse_markers(BODY) == [THREAD]


def test_a_body_without_a_marker_claims_nothing() -> None:
    """A summary with no earlier findings says nothing about threads."""
    assert resolver.parse_markers("## Code review\n\nNo blocking findings.\n") == []


def test_several_ids_are_de_duplicated_and_whitespace_is_tolerated() -> None:
    """A model may write the list in any spacing; the ids are what matter."""
    body = f"<!--  resolve-threads:  {THREAD} , {OTHER_THREAD},{THREAD}  -->"
    assert resolver.parse_markers(body) == [THREAD, OTHER_THREAD]


def test_something_that_is_not_a_thread_id_is_never_passed_to_the_mutation() -> None:
    """The marker is model-authored text on its way to a GraphQL id. It is validated before it is used."""
    body = "<!-- resolve-threads: PRRT_ok-123,hello world;rm -rf /,../../etc,PRRT_# -->"
    assert resolver.parse_markers(body) == ["PRRT_ok-123"]


def test_only_this_reviewers_comment_can_claim_a_thread() -> None:
    """A human quoting the marker must not make it theirs to resolve."""
    claims = resolver.own_claims([_comment("elewashy", BODY), _comment("dependabot[bot]", BODY)])
    assert claims == []
    assert resolver.own_claims([_comment("claude[bot]", BODY), _comment("claude", BODY)]) == [THREAD, THREAD]


def test_a_human_account_with_the_bots_name_cannot_claim_a_thread() -> None:
    """`claude` is a legal username. The login matches; the account type does not, and that is what decides."""
    human = {"user": {"login": "claude", "type": "User"}, "body": BODY, "created_at": "2026-11-21T12:00:00Z"}
    bot = {"user": {"login": "claude[bot]", "type": "Bot"}, "body": BODY, "created_at": "2026-11-21T12:00:00Z"}
    assert resolver.own_claims([human]) == []
    assert resolver.own_claims([bot]) == [THREAD]


def test_a_marker_from_an_earlier_run_is_ignored_when_a_start_time_is_given() -> None:
    """A stale marker would resolve a thread this run never looked at."""
    old = _comment("claude[bot]", BODY, created="2026-11-21T10:00:00Z")
    new = _comment("claude[bot]", BODY, created="2026-11-21T12:00:00Z")
    assert resolver.own_claims([old, new], "2026-11-21T11:00:00Z") == [THREAD]
    assert resolver.own_claims([old, new], None) == [THREAD, THREAD]


def test_this_reviewers_open_thread_may_be_resolved() -> None:
    """The normal case: its own thread, still open."""
    allowed, refused = resolver.resoluble([_thread()], [THREAD])
    assert (allowed, refused) == ([THREAD], [])


def test_another_authors_thread_is_refused_with_a_reason() -> None:
    """Resolving someone else's conversation is not this step's business."""
    allowed, refused = resolver.resoluble([_thread(("elewashy",))], [THREAD])
    assert allowed == []
    assert refused and "not this reviewer's thread" in refused[0]


def test_a_thread_already_resolved_is_not_resolved_twice() -> None:
    """Idempotent: a marker left by an earlier run is harmless."""
    allowed, refused = resolver.resoluble([_thread(resolved=True)], [THREAD])
    assert allowed == []
    assert refused == [f"{THREAD}: already resolved"]


def test_an_id_that_is_not_a_thread_here_is_refused() -> None:
    """A guessed or stale id is refused, never sent to the mutation."""
    allowed, refused = resolver.resoluble([_thread()], [OTHER_THREAD])
    assert allowed == []
    assert "not a thread on this pull request" in refused[0]


def test_a_thread_whose_authors_cannot_be_read_is_refused() -> None:
    """No author means no evidence that the thread is this reviewer's: refuse rather than assume."""
    allowed, refused = resolver.resoluble([_thread(())], [THREAD])
    assert allowed == [] and "no author" in refused[0]


def test_the_mutation_is_reported_only_when_github_confirms_it(monkeypatch) -> None:
    """The success message is GitHub's answer, not the HTTP status."""
    answers = iter(
        [
            {"data": {"resolveReviewThread": {"thread": {"id": THREAD, "isResolved": True}}}},
            {"data": {"resolveReviewThread": {"thread": {"id": OTHER_THREAD, "isResolved": False}}}},
        ]
    )

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(command: list[str]) -> Result:
        result = Result()
        result.stdout = json.dumps(next(answers))
        return result

    monkeypatch.setattr(resolver, "_run", fake_run)
    assert resolver.resolve(THREAD) is True
    assert resolver.resolve(OTHER_THREAD) is False


def test_a_failed_api_call_is_reported_and_never_raises(monkeypatch, capsys) -> None:
    """A refusal is a warning on the run; the findings are already posted."""

    class Result:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403: Resource not accessible by integration"

    monkeypatch.setattr(resolver, "_run", lambda command: Result())
    assert resolver.resolve(THREAD) is False
    assert "Resource not accessible" in capsys.readouterr().out


def test_nothing_to_resolve_is_a_quiet_success(monkeypatch) -> None:
    """No marker is the normal case on a first review."""
    monkeypatch.setenv("REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "1")
    monkeypatch.setattr(resolver, "fetch_claims", lambda repository, pull_request, since: [])
    assert resolver.main([]) == 0


def test_a_missing_pull_request_number_is_the_only_loud_failure() -> None:
    """The review has already been posted by the time this runs; a misconfiguration is a workflow bug, not a review."""
    assert resolver.main([]) == 1


def test_the_script_is_python_312_compatible() -> None:
    """The runner is 3.12 and the root formatter targets 3.14, which will happily write 3.14-only syntax."""
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 12))
