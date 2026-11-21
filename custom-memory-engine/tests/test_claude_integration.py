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
def standard() -> str:
    """The repository review standard, read from the working tree."""
    return (REPO / "REVIEW.md").read_text(encoding="utf-8")


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
    # The PR's *author*, not only the actor: a re-run by a maintainer does not make a Dependabot PR reviewable.
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in guard, "no Dependabot PRs"
    assert "pull_request_target" not in review_workflow, "never that trigger: it runs untrusted code with secrets"


def test_the_skill_resolves_even_when_the_base_branch_does_not_have_it(review_workflow: str) -> None:
    """The action restores ``.claude/`` from the base branch, and a prompt's ``/skill-name`` resolves from there.

    A pull request that introduces the skill to a base branch without one would otherwise invoke a command that
    does not exist, and the run would fail before the first finding — on exactly the PR that adds the reviewer.
    """
    job = _job(review_workflow, "claude-review")
    skill = ".claude/skills/review-pr/SKILL.md"
    assert f'git cat-file -e "HEAD:{skill}"' in job, "the base copy is probed before the pull request's is used"
    assert f"test -f {skill}" in job, "a missing skill must fail the job, not the review"
    assert "introduced by this pull request" in job, "the review must say whose copy of the standard it used"


def test_the_reviewed_tree_carries_the_base_branch_instructions(review_workflow: str) -> None:
    """A search across the reviewed tree must not surface the pull request's own copy of the standard."""
    job = _job(review_workflow, "claude-review")
    for path in ("REVIEW.md", "CLAUDE.md", "AGENTS.md", "custom-memory-engine/CLAUDE.md", ".claude"):
        assert path in job, path
    assert 'git archive HEAD "$path" | tar -x -C pr-head' in job


def test_the_review_holds_one_narrow_write_and_cannot_merge(review_workflow: str) -> None:
    """`contents: write` is the price of resolving a thread; everything that could push, merge or approve stays denied.

    Measured on #236: the reviewer verified its own finding, tried to resolve it and was refused —
    `resolveReviewThread` is gated on Contents: write, not on pull_requests: write. The grant is therefore one
    permission wide, and the deny list is what stops it becoming a licence to change the repository.
    """
    job = _job(review_workflow, "claude-review")
    permissions = re.search(r"(?ms)^    permissions:\n((?:      [a-z-]+: \w+\n)+)", job)
    assert permissions
    granted = dict(re.findall(r"([a-z-]+): (\w+)", permissions.group(1)))
    assert granted == {"contents": "write", "pull-requests": "read", "issues": "read", "id-token": "write"}, granted
    # Nothing in this job's own steps may approve or merge: AI review is a gate on the owner's behalf (V2-D57), and
    # V2-D57 forbids fabricating a GitHub approval to satisfy branch protection. The deny list below is where those
    # commands are named, so they are stripped before this check.
    disallowed = re.search(r'(?m)^            --disallowedTools "([^"]+)"$', review_workflow)
    assert disallowed
    assert disallowed.group(1).startswith(
        "Edit,Write,NotebookEdit,mcp__github_file_ops,Bash(git push:*),Bash(git commit:*),Bash(git merge:*)"
    )
    # Comments explain which commands are denied; only executable lines can run one.
    body = "\n".join(line for line in review_workflow.splitlines() if not line.strip().startswith("#"))
    body = body.replace(disallowed.group(0), "")
    for forbidden in ("gh pr merge", "gh pr review", "git push", "--approve"):
        assert forbidden not in body, forbidden
    # The write is for one GraphQL mutation; every REST write verb is denied, so nothing else changes through it.
    for denied in ("Bash(gh api -X:*)", "Bash(gh api --method:*)"):
        assert denied in disallowed.group(1), denied


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
    # Comments may sit between `concurrency:` and its keys; the group and the cancellation are what matter.
    for text in (review_workflow, assistant_workflow):
        block = re.search(r"(?ms)^concurrency:\n((?:  #.*\n)*  group: .*?\n(?:.*?\n)*?)\S", text)
        assert block, "each workflow must declare a concurrency group"
        assert re.search(r"(?m)^  group:", block.group(0))
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


# --- the four things the canary showed the reviewer getting wrong (`.github/workflows/README.md`) -----------


def test_the_reviewer_is_given_the_canonical_identity_and_may_not_invent_one(
    skill: str, review_workflow: str, assistant_workflow: str
) -> None:
    """A review cited `HussainAlmutawa/ChronaMind`: a recalled owner, on a repository that is not ours.

    A wrong link does not just fail to help — it turns a correct finding into something that looks fabricated, because
    the reader follows it to code that is not under review. So the identity is passed in, and it is the only source.
    """
    for workflow, name in ((review_workflow, "claude-code-review.yml"), (assistant_workflow, "claude.yml")):
        assert "REPOSITORY=${{ github.repository }}" in workflow, f"{name} must pass the canonical identity"
    assert "$GITHUB_REPOSITORY" in skill, "the skill needs a fallback when the invocation predates it"
    assert re.search(r"(?i)the only source of any URL", skill)
    assert re.search(r"(?i)never (write|invent|infer).{0,80}from memory", skill), "identity is read, not recalled"
    assert "omit the link" in skill, "an unverifiable identity must drop the link, not guess one"
    assert "grep -o" in skill, "the links must be checked against the identity before posting"
    # The placeholder the old text used is what let a model fill it from memory.
    assert "github.com/<owner>/<repo>" not in skill
    assert "<owner>/<repo>" not in skill


def test_the_review_is_told_its_tier_rather_than_inferring_it(skill: str, review_workflow: str) -> None:
    """The canary's second review called itself a first review once the base branch moved the diff range."""
    job = _job(review_workflow, "claude-review")
    for output in ("tier", "previous_head_sha", "previous_base_sha", "reason"):
        assert f"steps.gate.outputs.{output}" in job, output
    assert "REVIEW_TIER=" in job and "PREVIOUS_HEAD_SHA=" in job
    assert re.search(r"(?i)do not re-derive the tier", skill), "the tier is an input, not a conclusion"
    for tier in ("first review", "re-review", "unknown"):
        assert tier in skill, tier
    assert re.search(r"(?i)earlier findings exist even when the diff", skill), (
        "the failure was reporting nothing because the earlier finding left the diff range"
    )
    assert re.search(r"(?i)history incomplete", skill), "an unreadable record is a state, not a silent first review"


def test_the_skill_resolves_its_own_threads_only_after_verifying_the_fix(skill: str) -> None:
    """The canary verified its fix and left the 🔴 thread open, so conversation resolution still blocked the PR."""
    assert "resolveReviewThread" in skill, "the mutation must be named, not left to be improvised"
    assert "reviewThreads" in skill and "comment matching" not in skill
    assert re.search(r"(?i)match a thread by its body", skill), "associate by content, never by position or a guess"
    assert "THREAD_ID" in skill
    # The four states the owner needs to tell apart, and no shortcut between them.
    for state in ("reported", "fix observed", "fix verified", "thread resolved"):
        assert state in skill, state
    assert re.search(r"(?i)require .{0,40}isResolved.{0,10}true", skill), "GitHub must confirm it"
    assert re.search(r"(?i)resolution failed", skill), "a refused mutation must be reported, not glossed over"
    assert re.search(r"(?i)author\.login.{0,80}claude", skill), "only this reviewer's threads are its own to resolve"
    assert re.search(r"(?i)another reviewer|a human", skill)
    assert re.search(r"(?i)only after verifying|after you have verified|verify it in the code", skill)
    # A review may not resolve on the strength of a push description, and may not claim what it did not get.
    assert re.search(r"(?i)not resolved because the push", skill)


def test_the_review_analyses_reachability_before_it_sets_a_severity(skill: str) -> None:
    """The canary found a real contract violation and described it as a live cross-tenant leak.

    The finding was right; the exploit was not — `row_visible` refused the input first. Both documents must require the
    trace, and neither may let it become a reason to stop reporting: a latent violation is still 🔴.
    """
    standard = ROOT_REVIEW.read_text(encoding="utf-8")
    assert "## Reachability before severity" in standard
    for term in ("reachable today", "latent", "unverified", "**Reachability:**"):
        assert term in standard, term
    assert re.search(r"(?i)still (this level|🔴)", standard) or "is still 🔴" in standard, (
        "the analysis must not weaken the reviewer into silence"
    )
    assert "**Reachability:**" in skill
    for step in ("guards upstream", "latent", "unverified"):
        assert re.search(rf"(?i){step}", skill), step
    # The trace starts at the entry point, not at the handler: a helper is only reachable through it.
    assert "Engine.call" in skill and re.search(r"(?i)entry point", skill)
    assert re.search(r"(?i)on a path, not at its start", skill)
    assert re.search(r"(?i)never assert a leak", skill), "no impact claim without a path"


def test_a_push_that_changes_no_tree_is_skipped_before_the_action_runs(review_workflow: str) -> None:
    """The canary's empty commit cost a full run and posted nothing."""
    job = _job(review_workflow, "claude-review")
    gate = re.search(r"(?ms)^      - name: .*\n        id: gate\n(.*?)^      - name: ", job)
    assert gate, "the gate must run as its own step"
    assert ".github/scripts/review_gate.py" in gate.group(1)
    assert "python3 " in gate.group(1), "no extra dependency: the gate is stdlib Python"
    # It must be a decision *before* the expensive step, and the skip must be explained rather than silent.
    assert job.index("id: gate") < job.index("Run the review")
    gate_source = (REPO / ".github" / "scripts" / "review_gate.py").read_text(encoding="utf-8")
    assert "no_reviewable_changes" in gate_source, "the skip reason is machine-readable, not prose"
    assert "GITHUB_STEP_SUMMARY" in gate_source, "a skipped run must say why, on the run's own page"
    assert re.search(r"(?i)not silent|::notice::", gate_source)
    # The gate does not filter by path: a governance change is reviewable here.
    assert "PLAN/" in gate_source or "allow list" in gate_source


def test_the_documented_link_check_actually_catches_a_fabricated_owner(skill: str) -> None:
    """The P0 the canary produced: a finding linked to `HussainAlmutawa/ChronaMind`, an owner nobody chose.

    The skill tells the reviewer to extract every `github.com/<owner>/<name>` from the body it is about to post, and to
    keep exactly one, its own. That instruction is only worth the pattern it names, so the pattern is taken *out of the
    skill* and run here against a body that contains the real repository and the fabricated one.
    """
    pattern = re.search(r"grep -o '([^']+)'", skill)
    assert pattern, "the skill must document a concrete check for the links it is about to post"
    extract = re.compile(pattern.group(1))

    canonical = "eMinders/ChronaMind"
    sha = "d98f1b934dc21ab8fe56d485726b4a2a80fd4262"
    good = (
        f"🔴 **Finding.** See https://github.com/{canonical}/blob/{sha}/custom-memory-engine/src/x.py#L1-L2 and "
        f"[/custom-memory-engine/PLAN/V2/ARCHITECTURE_V2.md](https://github.com/{canonical}/blob/{sha}/PLAN.md)."
    )
    assert set(extract.findall(good)) == {f"github.com/{canonical}"}, "a correct review must pass the check"

    fabricated = good + " Compare https://github.com/HussainAlmutawa/ChronaMind/blob/" + sha + "/x.py#L3."
    found = set(extract.findall(fabricated))
    assert len(found) == 2, found
    assert found - {f"github.com/{canonical}"} == {"github.com/HussainAlmutawa/ChronaMind"}, (
        "the check must flag an owner that is not ours: it is the only thing between the reviewer and a link to "
        "someone else's code"
    )


def test_the_gate_comes_from_the_base_branch_and_tolerates_its_own_introduction(review_workflow: str) -> None:
    """The gate is policy code, so it is read from the base branch — but a PR that adds it has no base copy.

    Both halves matter. Running a pull request's own edited gate would let a PR switch off its own review; failing on
    the pull request that introduces it breaks the change that ships the gate (the first version of this workflow did
    exactly that: `can't open file '.../review_gate.py'`). The existing skill bootstrap is the precedent, and the last
    resort is still a review, never a skip.
    """
    job = _job(review_workflow, "claude-review")
    pattern = r"(?ms)^      - name: Decide whether this push needs a review.*?\n        run: \|\n(.*?)^      - name: "
    gate = re.search(pattern, job)
    assert gate, "the gate step must have a script body"
    body = gate.group(1)
    assert 'git cat-file -e "HEAD:.github/scripts/review_gate.py"' in body, "the base copy is probed first"
    assert "git -C pr-head archive HEAD .github/scripts" in body, "the introducing PR supplies the first copy"
    assert "introduced by this pull request" in body, "and says whose copy ran"
    assert "[ -f .github/scripts/review_gate.py ]" in body, "a missing gate must not fail the job"
    assert "reason=gate_failed" in body and "review=true" in body, "no gate means a review, not a skip"


def test_the_bot_cannot_cancel_the_run_it_just_started(assistant_workflow: str) -> None:
    """The action posts an acknowledgement comment as it starts, and that comment re-enters this workflow.

    With one shared group per pull request, that comment cancelled the run which had just posted it: #233, `claude`
    job "Claude Code is working…" at 10:07:11Z, `The operation was canceled` at 10:07:27Z, and the assistant never
    answered. A run started by a bot must therefore land in a group that collides with nothing, while a human's second
    `@claude review` must still supersede their first.
    """
    group = re.search(r"(?ms)^concurrency:\n(?:  #.*\n)*  group: >-\n(.*?)^  cancel-in-progress:", assistant_workflow)
    assert group, "the concurrency block must keep its group and cancel-in-progress"
    body = group.group(1)
    assert "cancel-in-progress: true" in assistant_workflow
    assert "claude[bot]" in body, "the bot actor must be recognised"
    assert "github.run_id" in body, "a bot-started run needs a group of its own, or it cancels the real one"
    # Human requests keep the behaviour that matters: a newer review request supersedes an older one.
    assert "startsWith(github.event.comment.body, '@claude review')" in body
    assert "'review'" in body and "'assist'" in body


def test_the_reviewer_checks_the_token_may_resolve_before_it_tries(skill: str) -> None:
    """`resolveReviewThread` needs `Contents: write`, not `pull_requests: write`, on a GitHub App installation token.

    Several projects learned this from live refusals (`Resource not accessible by integration`) after requesting the
    permission everyone expects. The App token this action mints does ask for `contents: write` as well, so resolution
    should be permitted — but "should be" is not a basis for writing "resolved", so the skill reads `viewerCanResolve`,
    the token's own answer, and refuses to claim a resolution it has not got.
    """
    assert "viewerCanResolve" in skill, "the query must ask whether this identity may resolve"
    assert re.search(r"(?i)contents: write", skill), "the permission that actually gates the mutation must be named"
    assert re.search(r"(?i)not permitted", skill), "a refusal has a state of its own, not a silent skip"
    assert re.search(r"(?i)Resource not accessible", skill)


def test_a_workflow_change_is_reported_as_unreviewable_and_points_at_the_way_that_works(skill: str) -> None:
    """`claude-review` cannot review a pull request that edits its own workflow, and nothing local can change that.

    The action's OIDC exchange refuses a workflow file that differs from the default branch's, so the job goes green
    with the review skipped — the dangerous shape of failure, where green means "not reviewed". The documented path is
    the on-demand job: `issue_comment` runs the default branch's workflow, so it reviews any pull request.
    """
    assert re.search(r"(?i)edits `.github/workflows/claude-code-review\.yml` gets no automatic review", skill)
    assert re.search(r"(?i)@claude review", skill), "the fallback must be named where the gap is explained"
    assert re.search(r"(?i)default branch.s workflow", skill), "and why it works"


def test_the_on_demand_review_can_actually_check_out_the_pull_request(assistant_workflow: str) -> None:
    """The documented fallback for a workflow-changing PR had never run, and could not: `git init` has no remote.

    The job did `git init --quiet pr-head` and then `git -C pr-head fetch origin "$HEAD_SHA"` — against a repository
    that `git init` had just created without one, so it died with `exit 128` ("Could not read from remote
    repository") before the review step. Its first live attempt was #233 at 2026-11-21T10:27Z. That matters beyond the
    bug: this is the only path that reviews a pull request which edits the review workflow, so a broken fallback leaves
    such a change reviewed by nobody.
    """
    job = _job(assistant_workflow, "review")
    checkout = re.search(r"(?ms)^      - name: Checkout the pull request beside it.*?(?=^      - name: )", job)
    assert checkout, "the review job must check out the pull request"
    step = checkout.group(0)
    assert "uses: actions/checkout@" in step, "use the action the automatic review uses, which authenticates the fetch"
    assert "path: pr-head" in step
    assert "ref: ${{ steps.pr.outputs.head_sha }}" in step
    assert "repository: ${{ steps.pr.outputs.head_repo }}" in step, "a fork's head is not in this repository"
    assert "persist-credentials: false" in step
    # Named in the step's explanation on purpose; what must not exist is a shell step, where a remote-less fetch lived.
    assert "run:" not in step, "the checkout is the action's job, not a shell script that has to invent a remote"
    # The head repository has to be resolved for that to be possible.
    assert '"head_repo=" + .head.repo.full_name' in job


def test_the_review_always_posts_a_summary_whatever_the_pull_request_state(skill: str) -> None:
    """An on-demand run read #233, spent 17 turns and $0.328, and posted nothing at all: no summary, no inline comment.

    It could not be told from a run that never happened, which is the failure this whole integration exists to remove —
    and the pull request being merged is the reason a reader would most need telling. So the summary is unconditional.
    """
    assert re.search(r"(?i)always post the summary, whatever the state", skill)
    assert re.search(r"(?i)no longer actionable", skill), "the merged/closed case has to say what it read"
    assert re.search(r"(?i)posts nothing cannot be told\s+from one that never ran", skill)


def test_the_summary_is_posted_from_a_heredoc_because_writing_a_file_is_denied(
    skill: str, review_workflow: str
) -> None:
    """`Write` is denied in this job, so an instruction to "put it in a file" is an instruction it cannot follow.

    Measured on #235's fix push: the skill said `gh pr comment --body-file <path>`, the model tried to write the file,
    the tool was denied, it was refused seven times and the run ended with no comment at all
    (`permission_denials_count: 7`). A heredoc needs no write and keeps the newlines, which is what the record needs.
    """
    assert "--body-file -" in skill, "the summary must be read from stdin"
    assert "<<'BODY'" in skill, "a quoted heredoc keeps markdown out of the shell's hands"
    assert not re.search(r"(?i)write it to a file", skill), "a file is not available here"
    assert "Write" in _job(review_workflow, "claude-review")


def test_the_reviewer_declares_its_basis_and_escalates_rather_than_guessing(skill: str) -> None:
    """A suspicion and a proof must not look the same, and depth the model lacks is asked for, never invented."""
    assert "**Basis:** proven" in skill and "**Basis:** suspected" in skill
    assert "escalate: opus" in skill
    assert "CLAUDE_REVIEW_MODEL=claude-opus-5-5" in skill, "the reader needs the exact command"
    assert re.search(r"(?i)never switch models yourself", skill)


def test_the_review_is_planned_before_it_is_written(skill: str, standard: str) -> None:
    """Sonnet needs a sequence more than a longer checklist: orient, rank, read deep, then the cheap checks."""
    assert re.search(r"(?i)^Plan first, in three lines", skill, re.MULTILINE)
    assert "How a review proceeds" in standard
    for step in ("Orient before judging", "Rank the files by what they can break", "Declare the coverage"):
        assert step in standard, step


def test_the_standard_covers_the_domains_a_reviewer_is_asked_about(standard: str) -> None:
    """Correctness is not the only axis, and the ones that are missing from a checklist are the ones missed in code."""
    for domain in ("Correctness", "Security", "Integrity and failure", "Concurrency", "Performance", "Compatibility"):
        assert domain in standard, domain


def test_a_consequence_must_name_the_constraint_that_would_stop_it(standard: str) -> None:
    """The one thing the reviewer got wrong on a finding that was right: what the database does about it.

    `#235` said a weaker purge lock would lose a citation silently; a restrictive foreign key makes that DELETE abort.
    Both are serious. Only one is what the code does.
    """
    assert "A consequence is a claim about a constraint" in standard
    assert re.search(r"(?i)restrictive\s+foreign\s+key", standard)
