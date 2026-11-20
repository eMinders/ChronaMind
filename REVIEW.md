# Review instructions

Review-only rules for this repository. `CLAUDE.md` holds project context that every session reads; this file says
what a **pull-request review** flags, at what severity, and what it must not flag. It is read by the managed Claude Code
Review service and by this repository's review workflow (`.claude/skills/review-pr/SKILL.md`), both from the **base
branch**, never from the pull request's own changes.

Two projects live in this repository with separate interpreters, linters, locks and CI:

| Path | What it is | Review instructions |
|---|---|---|
| `app/`, `evals/`, `docs/`, `alembic/` | FastAPI + LangGraph reference application (CPython 3.14) | `AGENTS.md` applies here and only here |
| `custom-memory-engine/` | The ChronaMind cognitive memory engine (CPython 3.12) and its `PLAN/V2` governance | `custom-memory-engine/CLAUDE.md` |

---

## Severity: what each level means here

| Level | Meaning in this repository |
|---|---|
| 🔴 **Important** | A wrong result, a lost or leaked row, a violated guarantee, a broken contract, or a duplicate side effect that a caller will actually reach. **Anything that makes a persisted guarantee untrue** is this level, however small the diff. |
| 🟡 **Nit** | Worth fixing, not blocking: a misleading name, a missing `raise ... from None`, a stale reference in a comment. |
| 🟣 **Pre-existing** | A real defect in code this pull request did not introduce. Report it once, at most, and say it is out of scope — or say nothing. Never let it decide the verdict. |

Style, formatting, naming preference and refactoring taste are **not** findings at any level. If a change cannot make
a claim in this repository's documents false and cannot change what a caller observes, it is not Important.

## The evidence bar

A finding is posted only when all six of these can be written down. If any is missing, do not post it.

1. **Location** — `path::symbol` or `path:line` in the pull request's own code.
2. **Triggering condition** — the concrete input, sequence or state that reaches it. "Could be wrong if…" is not
   a trigger.
3. **Wrong behaviour** — what happens instead, in the vocabulary of the project: a channel, an error code, an
   invariant, a grant, a closure, a clock.
4. **Consequence** — who or what is affected: a caller, a tenant, a later deletion, a verification result.
5. **Why the existing safeguards do not stop it** — name the test, constraint, trigger or repository that should have
   caught it and say why it does not. If you cannot name one, you have not finished looking.
6. **The correction** — what to change. A committable suggestion only when applying it fixes the issue completely.

Behaviour claims need a citation from the source, not an inference from a name. "This looks like it bypasses
authorisation" is not a finding; "`get` builds its rows without `assemble`, so `I14`'s bound is never attached
(`api/read_handlers.py`, `engine/channels.py::assemble`)" is.

**Say so when you could not verify something.** A gap in the review is honest; a silent gap reads as a pass. If a check
needed a database, a network call or a run you could not make, list it under "Not verified" in the summary.

## Investigate the change, not only the diff

The defects that have reached `main` here were all invisible in a single diff:

- `#228`: a `SECURITY DEFINER` helper gated only by `app.deletion_active`, an ordinary GUC any session can set.
- `#229`: `get` assembling its own response instead of routing through `assemble`, so a verified abstraction could
  render without its bound.
- A ledger row reverted by merge order across four branches that each carried a full copy of the same file.
- A closure whose test names passed on a scratch table while the migrated schema still had no policy.

So, before writing the verdict:

- **Read the code that calls what changed.** A changed signature, a changed error code or a changed default is a change
  to every caller. Grep them.
- **Look for the sibling.** The same rule is often implemented twice — write path and read path, v1 module and v2
  handler, SQL guard and Python twin. A fix applied to one sibling and not the other is the most common real bug here.
- **Check the pair.** Where this repository keeps a mechanism and its proof in two places (`MIGRATIONS` and
  `rollback/<n>_*.sql`, a guard function and `provisioning.TABLE_GRANTS`, a channel and its membership test,
  `INVARIANT_LEDGER.md` and its `path::symbol` citations, a claim in a plan document and its `scripts/check_evidence.py`
  reference), a change to one side is a change that must be visible on the other.
- **Ask what the change makes untrue.** List the statements in `PLAN/V2/` that mention the behaviour, and check whether
  this pull request updates them in the same commit. This project's rule is that registers move with the change.
- **Check the sequence, not only the pull request.** If several open branches touch one file (a ledger, a roadmap, a
  migration list), the merge order decides the result, and per-PR green CI cannot see it.

Changes of 200+ added or removed lines: take each behaviour the diff changes and search outside the diff for every
other place that assumes the old behaviour.

## Documents are code here

`PLAN/V2/` is the implementation plan and it is enforced by scripts that run in CI. A review of a documentation change
is a review of an assertion:

- A status claim ("`ENFORCED`", "wired", "closed", "Done") must be backed by a mechanism **and** a test that exists at
  the cited path, on the **migrated** schema. A green suite is evidence about the tests, not about the acceptance
  criteria (`PLAN/CONTRIBUTING.md` §1.1, `PLAN/V2/CLOSURE_PROTOCOL.md`).
- A new or changed `V2-D##` decision is authoritative only for what it decides, and it must not contradict
  `ARCHITECTURE_V2.md` without saying which text it supersedes.
- A closure record must be **posted** on the issue, not only written: `gh issue close` silently ignores a body.
- A citation in a plan document must resolve. `scripts/check_evidence.py` decides.
- A claim of a measured result must name the artifact that holds the measurement (`reports/`, `validation/`), and a
  confirmatory (`test`) split must not have been run: `G-TS` is a gate, and `scripts/check_test_split_gate.py` enforces
  it.

A stale claim is 🟡; a stale claim that a script gates CI on is 🔴; a test weakened, skipped or deleted to make a suite
pass is always 🔴.

## Do not report

- **What CI already decides**: `ruff check`, `ruff format --check`, `pyright`, `pytest`, actionlint, and the engine's
  mechanical/plan/evidence/statistics scripts. If they pass, the concern is not a finding; if they would fail, the PR
  is red on its own.
- **Lockfiles, hashes and generated output**: `uv.lock`, `validation/document_hashes.txt` (frozen by design), `reports/`
  artifacts a script produced, `reference/sources/`, `__pycache__`.
- **Docs-only and comment-only nits** in a PR that is not about that document.
- **A missing test alone**, unless you name the failure mode it would not catch. "Add tests" is not a finding;
  "`delete`'s two-phase ordering has no test that a *re-derived* dependent keeps its `verified` status — the existing
  test covers retirement only" is.
- **A pre-existing defect the PR does not touch** — except once, as Pre-existing, if the PR makes it reachable.
- **Anything the pull request already fixed later in the same diff.**
- **A second comment on the same root cause.** Findings are deduplicated: one comment per cause, at the best
  location.

## Cap and shape

- At most 5 nits in one review. Beyond that, post the five highest-value ones and summarise the rest as one pattern.
- At most **8 findings** in total on a first review; if there are more, say so and report the most severe.
- Each finding: ≤ 120 words. The summary: ≤ 400 words.
- Rank by severity. A review whose first comment is about naming, on a PR that changes a guard, has failed.

## Re-review (on a push)

A review of later commits is **stricter than the first one**: report only regressions introduced since the previous
review, plus any earlier Important finding that is still not fixed. Stay silent on nits. When you judge an earlier
finding fixed, resolve its thread rather than posting a new comment saying it is fixed.

---

## How to write it

- **Inline comments** on the affected lines, one per root cause.
- **The review body** carries: what the change does (two sentences, from the code, not the description), a table of
  findings by severity, what you verified and how, and what you could not verify.
- **No praise, no restating the pull request description, no "overall this looks good".** The verdict is the finding
  list.
- Say what you read. A review that names the files it traced is checkable; one that names the diff is not.
- Never approve and never merge. This repository's review is advisory to the owner (`V2-D57`), branch protection
  requires no GitHub approval, and no approval may be fabricated.
