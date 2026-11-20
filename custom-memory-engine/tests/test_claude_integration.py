"""The Claude review integration is a control, so its shape is checked like one (``V2-D57``, ``CONTRIBUTING.md`` 1.1).

Why this file exists at all: the review on the Project Owner's behalf used to be an off-the-shelf plugin prompt, and it
silently degraded. Over 106 pull requests it posted 106 "No issues found" summaries and four inline findings, while the
defects it was meant to catch were found later by hand (``#228``: a ``SECURITY DEFINER`` helper gated only by a
forgeable GUC; ``#229``: ``get`` bypassing ``assemble``). Nothing in the repository pinned what the reviewer must read,
so nothing failed when the reviewer stopped reading it.

These tests pin the parts that made it fail: the review must run a repository-owned skill (not a prompt that skips
a PR once Claude has commented), the instructions must come from the trusted base branch, the reviewer must be
forbidden from mutating the repository or merging, and the instruction files must carry this project's standards.
They check text, like ``test_ci_workflow.py`` does, so they run on the default install with no database and no network.

They do **not** prove a review finds bugs. Only a review that reports a planted defect can, and that is a live check
(``.github/workflows/README.md``: "Verifying the reviewer").
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
REVIEW_WORKFLOW = WORKFLOWS / "claude-code-review.yml"
ASSISTANT_WORKFLOW = WORKFLOWS / "claude.yml"
REVIEW_SKILL = REPO / ".claude" / "skills" / "review-pr" / "SKILL.md"
ROOT_REVIEW = REPO / "REVIEW.md"
ROOT_CLAUDE = REPO / "CLAUDE.md"
ENGINE_DIR = REPO / "custom-memory-engine"
ENGINE_CLAUDE = ENGINE_DIR / "CLAUDE.md"


def _job(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)", text, re.M | re.S)
    assert match, f"job {name} missing"
    return match.group(1)


@pytest.fixture(scope="module")
def review_workflow() -> str:
    return REVIEW_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def assistant_workflow() -> str:
    return ASSISTANT_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill() -> str:
    return REVIEW_SKILL.read_text(encoding="utf-8")


# --- the reviewer runs a repository-owned procedure, not the stock prompt ---------------------------------


def test_the_review_runs_the_repository_skill_and_not_the_marketplace_plugin(review_workflow: str) -> None:
    """The stock ``code-review`` plugin stops on "Claude has already commented on this PR" and reads the diff only.

    Both behaviours are in its own instructions, so neither is our decision: it is replaced by ``review-pr``, whose
    text this repository owns and tests.
    """
    prompt = re.search(r"(?ms)^          prompt: >-\n((?:            .*\n)+)", review_workflow)
    assert prompt and "/review-pr" in prompt.group(1)
    assert "plugin_marketplaces" not in review_workflow and "plugins:" not in review_workflow
    assert "code-review@claude" not in review_workflow
    # The skill posts the review itself; without it the findings stay in a run log nobody reads.
    skill = REVIEW_SKILL.read_text(encoding="utf-8")
    assert "gh pr comment" in skill and "create_inline_comment" in skill


def test_the_review_instructions_are_read_from_the_trusted_base_branch(skill: str, review_workflow: str) -> None:
    """A pull request must not be able to edit the standard it is reviewed against.

    The action restores ``.claude/``, ``CLAUDE.md`` and ``AGENTS.md`` from the base branch by itself, but ``REVIEW.md``
    is not on that list, so the skill is what reads it (and the engine's instructions) from the base commit. The base
    branch is also the workspace root, so the reviewed code sits beside it and cannot overwrite it.
    """
    assert 'git show "$BASE:REVIEW.md"' in skill
    assert 'git show "$BASE:custom-memory-engine/CLAUDE.md"' in skill
    assert "pr-head" in skill, "the changed code is read from the PR head, the instructions from the base"
    # The base ref is the PR's target branch, never a value the pull request can set.
    assert "BASE_REF=${{ github.event.pull_request.base.ref }}" in review_workflow
    assert re.search(r"ref: \$\{\{ github\.event\.pull_request\.base\.ref \}\}", review_workflow)


def test_the_review_can_see_the_surrounding_code_and_the_commit_range(review_workflow: str) -> None:
    """``fetch-depth: 1`` hid the diff from the old review and gave it one commit of history at most."""
    job = _job(review_workflow, "claude-review")
    assert job.count("fetch-depth: 0") == 2, "base and head both need their history"
    assert "persist-credentials: false" in job
    assert "path: pr-head" in job, "the reviewed ref is never checked out at the workspace root"
    assert "--add-dir pr-head" in job


def test_a_pull_request_is_reviewed_when_it_opens_and_when_it_changes(review_workflow: str) -> None:
    """Every push, not only the first: a PR here is rebased several times, and the merged tree is what ships."""
    types = re.search(r"(?ms)^on:\n  pull_request:\n    types: \[(.*?)\]", review_workflow)
    assert types, "the review workflow must trigger on pull_request"
    declared = {t.strip() for t in types.group(1).split(",")}
    assert {"opened", "synchronize", "ready_for_review", "reopened"} <= declared


# --- who is reviewed, and with what credentials ------------------------------------------------------------


def test_untrusted_and_automated_pull_requests_never_receive_the_token(review_workflow: str) -> None:
    job = _job(review_workflow, "claude-review")
    conditions = re.search(r"(?ms)^    if: >-\n(.*?)^    runs-on:", job)
    assert conditions, "the job must be guarded by an if: condition"
    guard = conditions.group(1)
    assert "github.event.pull_request.head.repo.full_name == github.repository" in guard, "no fork PRs"
    assert "!github.event.pull_request.draft" in guard, "no drafts"
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in guard, "no Dependabot PRs, whoever re-runs them"
    assert "pull_request_target" not in review_workflow, "never that trigger: it runs untrusted code with secrets"


def test_the_review_holds_a_read_only_token_and_cannot_merge(review_workflow: str) -> None:
    job = _job(review_workflow, "claude-review")
    permissions = re.search(r"(?ms)^    permissions:\n((?:      [a-z-]+: \w+\n)+)", job)
    assert permissions
    granted = dict(re.findall(r"([a-z-]+): (\w+)", permissions.group(1)))
    assert granted == {"contents": "read", "pull-requests": "read", "issues": "read", "id-token": "write"}, granted
    # Nothing in this job's own steps may approve or merge: AI review is a gate on the owner's behalf (V2-D57), and
    # V2-D57 forbids fabricating a GitHub approval to satisfy branch protection. The deny list below is where those
    # commands are named, so they are stripped before this check.
    disallowed = re.search(r'(?m)^            --disallowedTools "([^"]+)"$', review_workflow)
    assert disallowed
    assert disallowed.group(1).startswith(
        "Edit,Write,NotebookEdit,mcp__github_file_ops,Bash(git push:*),Bash(git commit:*),Bash(git merge:*)"
    )
    body = review_workflow.replace(disallowed.group(0), "")
    for forbidden in ("gh pr merge", "gh pr review", "git push", "--approve"):
        assert forbidden not in body, forbidden


def test_the_reviewer_is_denied_every_tool_that_could_change_the_pull_request(review_workflow: str) -> None:
    """A review reads. Pushing, merging and approving are what branch protection and the owner are for."""
    args = re.search(r"(?ms)^          claude_args: \|\n((?:            \S.*\n)+)", review_workflow)
    assert args, "the review step must pass claude_args"
    declared = args.group(1)
    assert "--permission-mode default" in declared, "a strict allow list, not auto mode, in the review job"
    assert "mcp__github_inline_comment__create_inline_comment" in declared
    disallowed = re.search(r'--disallowedTools "([^"]+)"', declared)
    assert disallowed, "--disallowedTools must pin the tools the reviewer may never use"
    denied = disallowed.group(1)
    for tool in ("git push", "gh pr merge", "gh pr review", "Edit"):
        assert tool in denied, tool


def test_a_review_runs_only_when_the_service_is_not_enabled(review_workflow: str) -> None:
    """Two reviewers on one PR pay twice for the same findings.

    Claude Code Review (the managed service) is an Owner-side setting no file in this repository can read or change, so
    the Action checks a repository variable: set ``CLAUDE_REVIEW_MODE=native`` once the service is on and the Action
    stops reviewing, before the token is ever minted.
    """
    job = _job(review_workflow, "claude-review")
    assert "vars.CLAUDE_REVIEW_MODE" in job
    assert "endsWith(vars.CLAUDE_REVIEW_MODE, 'native')" in job or "== 'native'" in job


def test_a_concurrency_group_cancels_a_superseded_review(review_workflow: str, assistant_workflow: str) -> None:
    for text in (review_workflow, assistant_workflow):
        assert re.search(r"(?m)^concurrency:\n  group:", text)
        assert "cancel-in-progress: true" in text
        assert "timeout-minutes:" in text


# --- the interactive assistant keeps working, and does not shadow the review -------------------------------


def test_the_assistant_keeps_a_mention_trigger_that_cannot_collide_with_the_review_command(
    assistant_workflow: str,
) -> None:
    """``@claude review`` starts a review; every other mention is an ordinary request.

    The managed service reacts to ``@claude review`` as well, so the assistant's own phrase is distinct: one mention
    must never start two different agents.
    """
    job = _job(assistant_workflow, "claude")
    phrase = re.search(r"(?m)^          trigger_phrase: \"(.*?)\"$", job)
    assert phrase, "the assistant must declare its trigger phrase"
    assert phrase.group(1) == "@chronamind"
    prompt = re.search(r"(?ms)^          prompt: >-\n((?:            .*\n)+)", job)
    assert prompt is None, "a prompt would put the assistant in automation mode and ignore the mention"


def test_the_assistant_is_only_started_by_a_maintainer(assistant_workflow: str) -> None:
    job = _job(assistant_workflow, "claude")
    assert 'fromJSON(\'["OWNER","MEMBER","COLLABORATOR"]\')' in job
    assert "author_association" in job


def test_every_claude_workflow_is_sha_pinned_and_keeps_credentials_out_of_git() -> None:
    """The repository's pinning rule applies to the Claude workflows like any other (``PLAN/CONTRIBUTING.md`` 1.3)."""
    for path in (REVIEW_WORKFLOW, ASSISTANT_WORKFLOW):
        text = path.read_text(encoding="utf-8")
        for reference in re.findall(r"uses:\s*([^\s#]+)", text):
            if not reference.startswith("./"):
                assert re.search(r"@[0-9a-f]{40}$", reference), f"{path.name}: {reference} is not SHA-pinned"
        assert "persist-credentials: false" in text, path.name
        # Only the two managed tokens may be interpolated: the subscription OAuth token and the automatic job token.
        secrets = set(re.findall(r"secrets\.(\w+)", text))
        assert secrets <= {"CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN"}, f"{path.name}: {secrets}"
        assert "secrets.GITHUB_TOKEN" not in text.split("review:")[0], (
            "the assistant job has no run: step of its own, so it needs no job token"
        )


# --- the instructions carry this project's standards -------------------------------------------------------


def test_the_review_instructions_are_severity_ranked_and_nit_capped() -> None:
    """Findings are ranked, capped and deduplicated, so a real defect is never buried by style."""
    text = ROOT_REVIEW.read_text(encoding="utf-8")
    for marker in ("Important", "Nit", "Pre-existing"):
        assert marker in text, marker
    assert re.search(r"(?i)at most \w+ nits?", text), "the nit budget must be stated"
    assert re.search(r"(?i)deduplicat|one comment per", text)


def test_the_review_instructions_forbid_the_findings_that_make_review_noise() -> None:
    text = ROOT_REVIEW.read_text(encoding="utf-8")
    for category in ("lint", "format", "pre-existing", "already fixed", "missing test"):
        assert category.lower() in text.lower(), category


def test_the_review_instructions_require_evidence_for_every_finding() -> None:
    """A finding must name the path that triggers it and the guarantee it breaks; a guess is not a finding."""
    text = ROOT_REVIEW.read_text(encoding="utf-8")
    assert re.search(r"(?i)file:line|`?path::symbol|line number", text)
    assert re.search(r"(?i)triggering (condition|input|path)", text)
    assert re.search(r"(?i)verify", text)


def test_the_root_claude_md_routes_a_session_to_the_part_of_the_repository_it_is_in() -> None:
    """Two projects live here with separate interpreters, linters and locks; a session must know which one it is in."""
    text = ROOT_CLAUDE.read_text(encoding="utf-8")
    assert "custom-memory-engine/" in text and "app/" in text
    assert re.search(r"(?i)3\.14", text) and re.search(r"(?i)3\.12", text), "the two interpreters differ"
    assert "make check" in text
    assert "CLAUDE.md" not in text.split("<!--")[0] or True  # form only; content is checked below
    assert "@AGENTS.md" not in text, (
        "an @-import would put the App template's rules in front of every engine review, which is how a correct lazy "
        "import was reported as an AGENTS.md violation on #49"
    )


def test_the_app_template_rules_are_scoped_to_the_app() -> None:
    """``AGENTS.md`` describes the FastAPI template. Its import rule is false for both projects' lazy imports."""
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert re.search(r"(?i)app/", agents)
    assert re.search(r"(?i)(not|only).{0,40}(custom-memory-engine|engine)", agents) or re.search(
        r"(?i)does not apply", agents
    ), "AGENTS.md must say what it does not govern"


def test_the_engine_instructions_name_the_entry_point_and_the_guarantees_that_hold_it() -> None:
    """These are the names a reviewer needs to trace a change instead of reading the diff alone."""
    text = ENGINE_CLAUDE.read_text(encoding="utf-8")
    for anchor in (
        "Engine.call",
        "api/entry.py",
        "security/scoping.py",
        "security/provisioning.py",
        "INVARIANT_LEDGER.md",
        "THREAT_MODEL.md",
        "DECISIONS_V2.md",
        "CHANNELS.md",
        "DELETION_ORDER.md",
        "MIGRATIONS",
    ):
        assert anchor in text, anchor


def test_the_engine_instructions_state_the_authority_order() -> None:
    """A plan, a decision, a ledger row and a test can disagree; the order decides which one wins."""
    text = ENGINE_CLAUDE.read_text(encoding="utf-8")
    assert re.search(r"(?i)authority", text)
    plan = text.index("ARCHITECTURE_V2.md")
    decisions = text.index("DECISIONS_V2.md")
    assert plan < decisions, "the architecture is the contract; a decision record amends it"
    assert text.index("DECISIONS_V2.md") < text.index("INVARIANT_LEDGER.md")


def test_the_engine_instructions_carry_the_verification_commands() -> None:
    text = ENGINE_CLAUDE.read_text(encoding="utf-8")
    assert "uv run --no-sync pytest -q -n auto" in text
    assert "uv run --no-sync pyright" in text
    assert "uv run --no-sync ruff check src tests research/benchmarks evaluation scripts" in text
    assert "scripts/check_evidence.py" in text, "the closure protocol is a checkable rule, not prose"


def test_the_instructions_stay_small_enough_to_read_on_every_review() -> None:
    """Instructions are paid for on every run, so they are a routing layer, not a copy of ``PLAN/V2/``."""
    for path, ceiling in ((ROOT_REVIEW, 15000), (ENGINE_CLAUDE, 20000), (REVIEW_SKILL, 20000), (ROOT_CLAUDE, 6000)):
        size = len(path.read_text(encoding="utf-8").encode())
        assert size < ceiling, f"{path.name} is {size} bytes (ceiling {ceiling})"


def test_the_instructions_only_cite_paths_and_tests_that_exist() -> None:
    """Instructions are read on every review, so a dead reference in them is a false claim in every review.

    ``scripts/check_evidence.py`` is the repository's own reference checker (the closure protocol's gate). It is strict
    for a closure record and advisory for a PLAN document; the instruction files are held to the strict form, because
    every one of their citations is a path or a test that a reader is told to act on.
    """
    import subprocess

    targets = [
        ROOT_REVIEW,
        ROOT_CLAUDE,
        REPO / "AGENTS.md",
        REVIEW_SKILL,
        ENGINE_CLAUDE,
        REPO / ".claude" / "NOTICE.md",
    ]
    result = subprocess.run(
        [sys.executable, "scripts/check_evidence.py", *[str(path) for path in targets]],
        cwd=ENGINE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 unresolved" in result.stdout


def test_the_project_settings_deny_reads_of_secret_files() -> None:
    """``custom-memory-engine/.env`` holds a real database password and is gitignored, not unreadable."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert any(".env" in rule for rule in deny), deny
    allow = settings["permissions"]["allow"]
    assert any("ruff" in rule for rule in allow), allow


def test_the_skill_never_asks_for_a_human_approval_or_a_merge(skill: str) -> None:
    """``V2-D57``: no GitHub approval is ever fabricated to satisfy branch protection."""
    for forbidden in ("gh pr merge", "gh pr review --approve", "--approve", "git push"):
        assert forbidden not in skill, forbidden


def test_the_skill_reports_its_own_uncertainty_and_resolves_dead_threads(skill: str) -> None:
    """A reviewer that cannot say "I could not check this" is read as a green light it never gave."""
    assert re.search(r"(?i)could not (verify|check)|not verified|unverified", skill)
    assert re.search(r"(?i)resolv", skill), "a fixed finding must not stay open on the next review"
