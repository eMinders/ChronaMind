# Changelog

Summarises every substantive revision of the authoritative proposal, checklist, audit, source evidence ledger, and validation scripts up to and including the current working set. Earlier revisions (V1, V2, V3) are no longer kept as separate files on disk; the entries below are the record of what changed in each pass. The current authoritative research documents live at the Research Lab root (`research-lab/`) under their version-free filenames (`Graduation_Project_Proposal.md`, `Methodology_and_Consistency_Checklist.md`, `Research_and_Fact_Check_Audit.md`, `Source_Evidence_Ledger.md`); the current validation suite lives under `validation/`; the source evidence (fetched papers, benchmark pages, licence files) lives under `research/sources/`.

---

## 2026-10-23 — Owner direction and execution-readiness consolidation

Documentation/planning only; this entry includes the useful partial prior-agent work retained in the workspace and the continuation that reconciles its stale copies. Hybrid owner selection and S-07 disposition are recorded in `review/FEASIBILITY_REVIEW_DECISIONS.md`; no new Frozen decision. At the time of the prior verification-session completion below, RL-001 was still reported In Review; the later owner message confirms team approval and the current record is in `PLAN/PROJECT_STATUS.md` §6. No supervisor approval, publication, PR, implementation or experiment is asserted.

- Canonical engineering contracts/version policy: PLAN architecture; task prerequisites: TASK_GRAPH; graph/sequence: DEPENDENCY_MAP; progress: PROJECT_STATUS; decisions: DECISIONS_REGISTER. Updated README/review navigation and existing impact analysis (inventory/dispositions/contract blockers), not a second roadmap/report.
- Completed dependency reconciliation: RL-002 + RL-013 → RL-003 → RL-020/RL-021; RL-012 also precedes CI; RL-025 requires RL-030; RL-033 requires RL-011; RL-080 requires RL-070; RL-090 requires RL-070 for T5. Removed circular D-18/D-21/D-22 preparation/final-freeze interpretations, not research gates. Phase tracker, schedule, portfolios and affected full/compact drafts aligned; local bootstrap claim design after RL-001 removes the RL-006 publication/claim cycle without fabricating approval.
- Preserved prior engineering selections: dedicated Lab CI (alternative: add to main-app CI; isolate runtime/maintenance), component contracts and one test tree, single progress board. Clarified migration reproducibility as logical equivalence, not physical DB bytes; deterministic fixture/replay evidence is distinct from hosted-model nondeterminism. No numeric research thresholds selected; removed an arbitrary u* example from the pilot-plan draft.
- Prior-agent consolidation (already applied when this verification session began): merged and removed four redundant Markdown files: review recommended architecture → PLAN architecture §§8–10; review updated-state snapshot → status/traceability/owner record; review documentation changelog → this bounded history and inventory; compact decision draft → canonical decision register + existing publication template. Detailed full/compact task drafts, advisory framework evidence/matrix, all research/source/validation evidence retained. Inventory: 88 → 84 Markdown files repository-wide (Lab 65 → 61); no files moved.
- Historical correction record preserved from former review changelog: earlier review added advisory README/status pointers and corrected its six-way-parallelism claim (FR-001 → FR-002 → FR-006 was not flat parallel work). A subsequent follow-up corrected premature pilot wording and reported S-07 gate uncertainty and missing RL-003 mirror edges; it reported mechanical exit 0, seven intended Markdown changes, read-only relative-link/hash/whitespace checks, and no SQL/experiment execution. Those were historical reports, not checks rerun by this continuation. The owner's explicit direction now supersedes that S-07 uncertainty and any suggestion of adding a Frozen Hybrid row. FR proposals are mapped/disposed in review decisions §3, not marked executed.
- Reported actual gaps rather than repairing evidence: absent finalization script; conflicting dependency-library boundary; proposal/spec shorthand/entity/lineage ambiguities; no Lab manifest/lock or verified current runtime/PG-extension selection. Preserve normative contract and escalate real behavioral conflicts through spec §13. Root main-app guides, legacy proposal/integration blueprint and runtime prompts retained unchanged because their distinct value/ownership is not safely disposable.
- Version constraints: independent environment/lock with direct bounds + exact transitive resolution, runtime patch/tool pins, PG-extension compatibility and image digests, isolated baseline locks if necessary. Historical PG 16.15 reduced-schema and source commits are provenance only; no live latest-stable or installation/integration compatibility claim. Dated verification sources/procedure are in PLAN architecture §7.

### Continuation checks (2026-10-23)

Executed without installation or experiments: existing mechanical checker on proposal/checklist/changelog; standard-library C–P output comparison; historical SQL-log PASS count/final line/MD5 inspection (not SQL execution); local Markdown file/anchor reference scan; task-definition/map/full-and-compact-draft prerequisite comparison and topological traversal; pre/post SHA-256 preservation inventory and whitespace checks. The interrupted pass left final outcomes unrecorded; the verification-session results below now supply independently executed evidence. Earlier execution claims above remain historical reports. Saved validation logs/hashes are historical and are not overwritten. Mermaid parsing/rendering, full-engine/DB/security tests and live upstream version/build checks were not performed.

### Verification-session completion (2026-10-23)

This prior verification session began with 84 Markdown files (61 Lab), the four removals already applied, and only `?? research-lab/` in Git status. The absent originals cannot be independently compared now; no further file was deleted or moved. A fresh SHA-256 snapshot was taken before that session's edits. Its status statements predate the owner-confirmed RL-001 approval and must be read as historical, not current.

- Finished 12 existing documents: PLAN architecture, assignments, decision register, task graph, dependency map, status; full issue drafts RL-001/003/004/005; existing impact analysis; this changelog. Corrected P0 acceptance and ten-document review counts, assignment/reviewer drift, dependency summaries, D-27 numbering, and optional async-test timing. RL-001 acceptance and publication permission are distinct. RL-130 package acceptance is now a handoff to RL-130-REPRO, removing the acceptance cycle without relaxing D-P13 reproduction.
- Corrected the dedicated-CI approval claim: boundary §4 permits an added job only. Selected D-LAB-CI requires the §5 supervisor-approved dedicated-file exception before implementation; no boundary, checklist or approval record was silently rewritten. RL-004 draft now rejects unapproved allowlist entries and broad workflow-path exceptions.
- Read root dependency TOML with the standard library; selected locked versions are recorded as main-app context in PLAN architecture §7. No live upstream, install/build or runtime compatibility verification; no Lab version pins chosen.

**Checks actually executed:**

| Check | Result / limitation |
|---|---|
| `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` from Lab | Final rerun exit 0; `SUMMARY: ALL HARD CHECKS PASSED`; pre-existing soft REVIEW hits retained. An intermediate run failed the existing excluded-name guard because the temporary output directory name appeared here; that incidental path was removed and the unchanged checker rerun successfully. Output captured in a session-local temporary file, not over historical logs. |
| `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt` | Exit 0; byte-identical arithmetic output. Not an experiment. |
| SQL-log read-only count, last line and MD5 | 43 PASS; `ALL ASSERTIONS PASSED`; log `66a4dae36f20f17d19f3b75d612e61fb`, SQL `9050a08b497a0acc4e5e19d87059e185`. SQL not executed. |
| Temporary standard-library local Markdown link/heading scanner | No missing targets/anchors in current Lab Markdown links. Not external-link validation or rendering. |
| Temporary task/map/draft scanner + topological traversal | 51 task definitions, 26 full + 25 compact drafts; explicit blocking/non-blocking/conditional map edges and draft prerequisite IDs match; 51-node traversal has no cycle, including global RL-001 and conditional application edge. Decision/acceptance prose additionally reviewed manually; Mermaid not parsed/rendered. |
| `git diff --check` plus changed-file whitespace scan | No introduced trailing whitespace or missing terminal newline; Git diff alone cannot check the untracked Lab. |
| Pre/post SHA-256 snapshot of 316 workspace files (excluding VCS/dependency/agent cache trees) | Only the 12 intended Lab Markdown files changed; no file deleted/moved/added in the sampled workspace. Proposal, technical spec, methodology checklist, audit, ledger, roadmap, traceability, source corpus and validation artifacts byte-identical; all sampled non-Lab work unchanged. |

No packages installed, experiments or full-engine/DB/security tests run, commits/pushes/PRs/issues created, or approval signatures invented. Snapshot/check helpers are temporary files outside the repository; historical validation evidence remains intact.

---

## 2026-10-23 — Owner-confirmed RL-001 closure and first-wave reconciliation

Documentation/planning-only continuation in response to the owner's explicit confirmation that the team completed RL-001 review/approval. RL-001 is recorded Done in `PLAN/PROJECT_STATUS.md` §6 and its task draft; no individual names, responses, signatures, issue comments, actual review timestamp or team-reviewed revision/hash were supplied or inferred. The workspace snapshot hash in the status board is explicitly a fingerprint of the inspected pre-edit files, not evidence of the revision reviewed by the team.

- Reconciled current navigation/status in `README.md`, `PROJECT_ROADMAP.md` §K, `TRACEABILITY_MATRIX.md`, `PLAN/TASK_GRAPH.md`, `PLAN/DEPENDENCY_MAP.md`, `PLAN/PROJECT_STATUS.md`, `PLAN/ASSIGNMENTS.md`, `PLAN/SCHEDULE.md`, and `PLAN/DECISIONS_REGISTER.md`. P0 remains In Review only for RL-002's separate scaffold acceptance/merge; RL-001 no longer blocks the independent P1 preparation tasks.
- Corrected issue drafts `RL-001`, `RL-002`, `RL-003`, `RL-005`, `RL-006`, `RL-010`, `RL-012`–`RL-016`, and `RL-020`/`RL-021` to reflect the canonical approval state, actual prerequisites, blocker class and publication separation. RL-010 and RL-016 decision scopes are distinct. RL-020/RL-021 are Blocked until both RL-002 and RL-003 are accepted; they can proceed in parallel after that.
- Added `PLAN/TEAM_HANDOFF.md` as the one concise first-wave dispatch guide. It states provisional role IDs, prerequisites, deliverables, independent-review expectations, legitimate parallelism, governance exceptions and exact next dispatch order. It does not create a second task graph or claim anyone has accepted work.
- Preserved owner-selected Hybrid and the existing research contract. Did not edit proposal, technical specification, methodology checklist, H1–H4, protocol, statistical methods, ablations, novelty framing, source ledger, or Frozen decisions. D-25 and D-ENG-LANG remain open: no current-version verification, install, lockfile or compatibility acceptance was performed in this pass.
- Confirmed governance exception remains: dedicated `.github/workflows/research-lab.yaml` still requires the scoped approval under `INDEPENDENCE_BOUNDARY.md` §5; no workflow was created. RL-006 remains separately gated by foundation acceptance, confirmed identities/access and explicit owner publication permission.

**Fresh checks actually run after this continuation's edits:**

| Check | Result |
|---|---|
| `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` from `research-lab/` | Exit 0; `SUMMARY: ALL HARD CHECKS PASSED`; existing soft REVIEW hits remain. |
| `diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt` | Exit 0; byte-identical arithmetic output. |
| Temporary standard-library Markdown target/anchor scan | 62 Markdown files scanned; zero missing local targets/anchors. |
| Temporary canonical task-edge extraction and topological check | 51 tasks, 126 explicit edges; acyclic; all 126 edges synchronized with the explicit dependency-map view, including the RL-006 range and conditional/repro dependencies. |
| Edited-Markdown trailing-whitespace scan and `git diff --check` | No trailing whitespace; `git diff --check` exited 0. The Lab is untracked, so Git's check does not inspect it; the direct Markdown scan covered the edited Markdown files. |
| Historical digest comparison | Proposal and methodology checklist match saved MD5 values in `validation/document_hashes.txt`. That file does not contain a technical-specification hash, so no hash-based claim is made for that file; it was not edited in this continuation. |
| Saved SQL evidence | Log re-inspection: 43 PASS lines and final `ALL ASSERTIONS PASSED`; SQL was not executed. |

No GitHub issues, branches, commits, pushes, project settings, dependencies, new SQL, integration, security, pilot or experiment work were created or executed. No engine test is claimed.

---

## 2026-10-23 — First-wave self-assignment issues published

At the owner's explicit request, published six new GitHub issues in `Abdulrahman-Ayman-Farid/ChronaMind`; a read-only check before creation found no existing issues, so no duplicates were present. The authenticated account had repository write permission. Created only the labels needed by these six issues and a single P1 milestone; no project board, full backlog, issue assignees, branches, commits or other remote settings were created.

| Task | Issue | Labels | State |
|---|---|---|---|
| RL-010 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/1 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/M`, `rl:risk/medium` | Open; ready for evidence preparation; no supervisor disposition inferred |
| RL-012 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/2 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/S`, `rl:risk/low` | Open; compatibility choice remains unaccepted until required evidence |
| RL-013 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/3 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/S`, `rl:risk/low` | Open; runtime evidence preparation only; no install/lock/implementation |
| RL-014 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/4 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/S`, `rl:risk/low` | Open; D-15 supervisor disposition remains a gate |
| RL-015 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/5 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/S`, `rl:risk/low` | Open; no recruitment or study authorized |
| RL-016 | https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/6 | `rl:kind/task`, `rl:phase/P1`, `rl:area/decisions`, `rl:status/ready`, `rl:dep/decision-blocked`, `rl:effort/S`, `rl:risk/low` | Open; paired work required; no contributors assigned |

Each issue body was authored from the canonical task contract and then re-read from GitHub. They describe goals, scope, outputs, objective acceptance, prerequisites, blocking/non-blocking dependencies, out-of-scope work, reviewer independence, claim procedure and status updates. Issues are unassigned; publishing does not authorize blocked implementation, supervisor/ethics decisions or experiments. RL-016 remains blocking only for RL-022/023/024 as canonical; it is not a prerequisite for RL-020/021. The P1 milestone exists as milestone #1, with no due date. The only board/full-backlog publication remains gated and was not executed.

Current-workspace changes in this publication pass: expanded all six issue drafts to be self-contained; updated `PLAN/PROJECT_STATUS.md`, `PLAN/ASSIGNMENTS.md`, `PLAN/CONTRIBUTING.md`, `PLAN/GITHUB_SETUP.md`, `PLAN/TEAM_HANDOFF.md`, `PLAN/TASK_GRAPH.md`, `PLAN/DEPENDENCY_MAP.md`, `PLAN/SCHEDULE.md`, `PLAN/issue_drafts/README.md`, `RL-002.md`, `RL-020.md`, and this changelog. RL-002 now requires a base-aware check of committed, staged, unstaged and untracked non-ignored paths. Assignment rows remain provisional, RL-016 is explicitly paired, and contributor claiming/independent-review procedure is consistent across issues and handoff. No dedicated CI workflow was created; the supervisor-approved boundary exception remains required.

Validation after the documentation/remote work: mechanical checker exit 0 (`SUMMARY: ALL HARD CHECKS PASSED`); local Markdown targets/anchors; canonical task graph cycle and dependency-map sync; saved CP arithmetic comparison; `git diff --check` plus direct whitespace inspection for the untracked Lab. The six remote issue URLs, titles, labels, milestone, open status, and empty assignee lists were queried and verified. No SQL, dependency installation, engine/security/integration, pilot, recruitment, or experiments were run.

---

## Finalization pass (repository normalisation)

- Repository was restructured to a single authoritative working set.
- The V4 patch files were promoted to version-free filenames at the repository root. The four obsolete revision directories that held the V1 first revision, the V2 patch, the V3 patch, and the V4 patch were permanently deleted.
- Source evidence that those directories carried — the full-text extracts for refs [20]–[29] and the fetched benchmark/publisher/documentation pages for refs [1]–[41] — was preserved by moving the two source subdirectories into `research/sources/fulltext/` and `research/sources/pages/` **before** the parent revision directories were deleted. Every citation that pointed at the old paths was updated to the new paths.
- Validation artefacts (the reduced-schema SQL and its 43-assertion log, the Clopper–Pearson arithmetic, the mermaid syntax check, the mechanical consistency script, the bi-temporal log inspection record) were moved into `validation/` under version-free filenames.
- The mechanical consistency script's reference-list byte-identity check previously compared against the V3 proposal file. Because V3 is now deleted, the check is replaced by a self-contained SHA-256 of the 41 reference entries pinned inside the script (`REFERENCE_LIST_SHA256 = a5851b94a6385e9ddea2480abce710d1c91ca0149923e04cda600f9a4254038f`, the SHA-256 of the V3 and V4 reference entries, which were already byte-identical). The property being enforced is unchanged: the 41 reference entries must remain byte-identical to the baseline. Any legitimate reference-list change is a documented event that must bump this constant and record the diff in a new entry below.
- The original manually-cleaned proposal (previously duplicated at the repository root as `Graduation_Project_Proposal.md` and under the deleted V1 revision directory) is no longer retained as a separate file; the earlier audit's record of the diff between the original and the first revision is preserved below (sections for V2, V3, V4), and the fetched research pages used to substantiate every citation are preserved under `research/sources/pages/`.
- Status notes, cross-references, and in-text filename pointers in the proposal, checklist, audit, and ledger were updated to the version-free filenames and the new validation paths. No research methodology, hypothesis, baseline, metric, archival precondition, deletion rule, or reference list was changed; the 41-entry reference list remains byte-identical to the V3 and V4 baselines.

---

## V4 (targeted patch of V3)

Base: the V3 revision of the proposal (not retained on disk — its content and the V4 diff against it are summarised below). Output of the V4 patch at the time: `Graduation_Project_Proposal.md`. The original proposal, every V2 file, and every V3 file were preserved during the V4 session (they have since been removed in the finalization pass above). V4 was a targeted patch and not a rewrite. The 41-entry reference list was byte-identical to V3 and V2 (checked by the V4 mechanical script). Section numbering, headings, diagrams, baselines, datasets, metrics for H1, H2, H3, and H4, the judge protocol, the ethics position, and the exclusion of schedule, team-member names, individual assignments, and staffing-based feasibility arguments are preserved. H1's primary contrast remains P − A2. The persona layer remains optional. Minimal consolidation remains essential.

**S** = scientific or technical correction. **C** = clarification or specification. **P** = presentation only. **D** = decision recorded.

## 1. Historical-memory archival bug fixed (Sections 7.4 R5, 7.5 Archival precondition, 7.6 retrieval step 1, 8 invariant 10)

- **S (the bug).** V3 Section 7.5 defined the Archival precondition with two branches, "(A) Superseded or ended" and "(C) Active derived representation". Branch (A) was disjunctive: it permitted archival when the row's recorded range was closed *or* when its valid range had ended at or before the archival time. The second disjunct is the bug. A version whose valid range has ended but whose recorded range is still open is a **historical current belief** — the system's present belief about something that held in a past period (for example row v1″ of the worked example in Section 7.4: "the user was vegetarian until 15 July", committed on 20 August, no recorded_until). V3's current-state and historical-as-of retrieval both exclude Archived rows. Archiving v1″ under the V3 "valid-ended" branch therefore removed the fact from the only ordinary predicate that could return it (historical-as-of). The user asking "what was my diet on 1 April?" would see "the system forgot", even though no supersession or deletion had occurred.
- **D (the fix: policy B′).** The V4 Archival precondition keeps only two branches, with the V3 "valid-ended" sub-branch removed:
  - **(S) Superseded** — *r*'s recorded range is already closed. By definition no current-state or historical-as-of predicate can return *r* even before archival (both predicates require ρ ∈ R, which fails for a closed recorded range). Archival loses nothing retrievable to ordinary users. Only audit-as-of can reach *r*, and audit-as-of continues to include Archived rows.
  - **(C) Active derived replacement** — an Active, non-Archived derived version *r′* exists with a `MEMORY_DERIVATION` link *r′* → *r*, carrying the same user-level fact, that is itself a current belief and whose valid range covers at least the valid range of *r* that would otherwise be answerable. Both current-state (if applicable) and historical-as-of (for the valid times *r* used to cover) still return the fact, through *r′*. On deletion of the original memory, *r′* is removed or re-derived under rule R6 and the Section 7.7 derivation closure, so the deleted information does not survive through the replacement.
- **S (symmetry).** The two conditions "currently valid" and "historical" are now treated symmetrically for the purposes of archival: both are current beliefs (recorded range open), both are returned by ordinary retrieval (current-state and historical-as-of respectively), and neither may be archived unless (C) holds. Supersededness is the only sufficient condition for safe archival without a replacement, because supersededness means the row is no longer a current belief at all.
- **S (invariant 10 rewritten).** Section 8 invariant 10 is rewritten to reflect (S) and (C) only, and to list the five deterministic test cases (T1 – T5) that enforce it. The enforcement is in the transactional archival procedure (not a single-row database constraint).
- **S (Section 7.4 R5).** Rule R5 is updated so the Archival precondition it names is the V4 precondition, with the symmetry argument stated inline.
- **S (Section 7.6 retrieval step 1).** The safety argument for excluding Archived rows from current-state and historical-as-of is rewritten to reference branches (S) and (C) and to state the no-silent-loss guarantee for both currently-valid and historical current beliefs.
- **C (four-condition summary table).** A new table in Section 7.5 lists the four behaviourally distinct conditions — currently valid current belief, historical current belief, superseded, Archived — with, for each, whether current-state, historical-as-of, and audit-as-of return the row, and whether archival is eligible. This makes the no-silent-loss guarantee visible at a glance.
- **S (planned tests).** Five deterministic test cases are specified for the test harness. They are **planned, not executed** in V4; the existing SQL harness (`validation/bitemporal_checks.sql`) does not cover them. They are listed in `Methodology_and_Consistency_Checklist.md`, Section 5, and in invariant 10's test column:
  1. **T1 — currently valid, no derived replacement.** A currently-valid current belief without a (C) replacement. Expected: archival rejected by the transactional procedure (`VALIDATION_ERROR archival_precondition_unmet`); the row stays Active; current-state still returns it. Covers task item 1.
  2. **T2 — historical current belief without a derived replacement.** A version whose valid range has ended but whose recorded range is still open and no (C) replacement exists. Expected: archival rejected; the row stays Active; historical-as-of for a reference time inside the valid range still returns it; current-state does not (ρ ∉ V). Covers task item 2 — this is the V3 bug case and it is now the primary planned regression test.
  3. **T3 — superseded version.** A version whose recorded range has been closed by a later supersession. Expected: current-state and historical-as-of never return it (ρ ∉ R); archival accepted via (S); audit-as-of still returns it. Covers task item 3.
  4. **T4 — derived replacement as (C).** An Active derived version *r′* with a `MEMORY_DERIVATION` link to *r* and matching valid range. Expected: *r* may be archived via (C); the fact is still returned by current-state and historical-as-of through *r′*; the provenance chain *r′* → *r* remains traceable via `MEMORY_DERIVATION`; the archival transaction records branch (C) and the identifier of *r′*. Covers task item 4.
  5. **T5 — deletion of the original memory.** Starting from the T4 state, a deletion request for the original memory item fires rule R6 and the derivation closure of Section 7.7. Expected: every version of the memory item (including *r* and *r′*, and any further transitively derived artifacts) is physically removed; current-state, historical-as-of, and audit-as-of for the deleted key return nothing; the audit record for the deletion contains no content or hash. Covers task item 5.
- **C (status note).** The proposal's status note is updated to flag the V4 archival-precondition tests as specified-but-not-executed, so the proposal cannot be read as claiming T1 – T5 passed.
- **D (what is *not* changed in methodology).** No research question, hypothesis, endpoint, metric, baseline, dataset, suite construction rule, ablation, statistical procedure, judge protocol, or ethics position changes. H1's primary contrast remains P − A2. The persona layer remains optional. Minimal consolidation remains essential. H4's scenario-level primary bound, the three mutually-exclusive outcome rules, u*, and the positive-control detection share are all preserved from V3.

## 2. Source-ledger consistency fixed

- **S (stale reference, two sites).** V3 had two stale pointers at the V2 ledger: Section 6.1's source note and the References-section preamble. The V3 revision had replaced the V2 ledger with `Source_Evidence_Ledger.md`, so both references were out of date. V4 Section 6.1 and the V4 References-section preamble now both point at `Source_Evidence_Ledger.md`. The second site (the References preamble) was found by the V4 verifier pass after an earlier V4 draft had fixed only Section 6.1 — the exact class of regression the task was meant to eliminate — and the V4 mechanical script's stale-wording guard was extended at the same time to forbid the directive forms `"in \`Source_Evidence_Ledger_v2.md\`"` and `"in \`Source_Evidence_Ledger_v3.md\`"` so this regression cannot recur. Narrative/historical mentions of the V2 and V3 filenames (status note, V3-finding history row in the audit) remain legal because they describe what V4 fixed, not where to look things up.
- **C (reading-depth column).** `Source_Evidence_Ledger.md` introduces a dedicated **Reading depth** column in Section A (original 15 references) and Section B (added references 16 – 41) and uses the same five categories that V3 defined in prose: *metadata/abstract inspected* · *full text downloaded or retrieved* · *selected full-text passages read* · *full text read comprehensively* · *experiments independently reproduced*. Every row carries one of these labels and nothing stronger.
- **S (no promotion).** No source was promoted beyond what its evidence supports. In particular, no PDF download is relabelled as "paper read", and no paper's experiments were independently reproduced in V2, V3, or V4. The depth labels per row match the Stage 1 / Stage 2 record in Section D and the per-passage log in Section E.
- **D (unchanged).** No citation was added, altered, or removed. The 41-entry reference list is byte-identical to V3 and V2 (checked by the V4 mechanical script). The Stage 1 (16 Oct 2026) / Stage 2 (17 Oct 2026) audit record is unchanged. The row-38 (PostgreSQL docs) correction and the row-3 audit correction introduced in V3 remain in place.

## 3. Validation artifacts cleaned up and re-run on V4

- **S (stale "V2" descriptions in `mechanical_checks.py`).** The V3 copy of `mechanical_checks.py` still carried a docstring that read "Mechanical checks on the v2 Markdown deliverables" even though it was being run on V3. V4 copies the script to `validation/mechanical_checks.py` and updates the docstring to reference the V4 deliverables. The script's logic is otherwise unchanged, including the V3→V2 reference-list comparison, which V4 generalises to compare against the V3 reference list (byte-identical). This keeps the check's intent (reference-list stability across patches) without inventing a new baseline.
- **S (what was actually executed on V4, with exact commands).** The following commands were run from the repository root in this V4 patch session and their outputs are saved under `validation/`. Nothing else was executed.
  1. `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` → output in `validation/mechanical_checks.output.txt`. The exit code and the summary line are reported in the output file.
  2. `md5sum Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md Research_and_Fact_Check_Audit.md Source_Evidence_Ledger.md CHANGELOG.md` captured before any V4 edit as earlier preservation baseline (no longer retained; see `CHANGELOG.md`) and again after all V4 files were written as earlier preservation snapshot (no longer retained; see `CHANGELOG.md`), with the diff saved as part of earlier preservation report (no longer retained; see `CHANGELOG.md`). All V3, V2, and original files were byte-identical before and after.
  3. `grep -c "^PASS\|^ *PASS " validation/bitemporal_checks.output.txt`, `tail -1 validation/bitemporal_checks.output.txt`, `md5sum validation/bitemporal_checks.output.txt`, and `md5sum validation/bitemporal_checks.sql` → saved as `validation/bitemporal_log_inspection.txt`. **The SQL was not re-run in V4** (the SQL file is unchanged from V2 and V3; its MD5 matches the V3 re-inspection). The saved log reports 43 assertions passed and ends with `ALL ASSERTIONS PASSED`; this is attributed to the V2 run.
- **S (no new experimental claim).** The V4 patch does **not** execute SQL beyond the log re-inspection, does **not** execute the planned archival-precondition tests (T1 – T5), does **not** integrate the application, does **not** run any of the H1 – H4 experiments or benchmark suites, and does **not** execute pgvector, RLS, the job queue, or concurrent-writer tests. All of these items were already listed as planned-not-executed in V2/V3 and are kept that way. The archival-precondition tests are added to the planned-not-executed list in Section 7.4 "What was executed" and Section 8 invariant 10.
- **C (preservation logs).** V3's preservation log (earlier preservation report (no longer retained; see `CHANGELOG.md`)) is preserved unchanged. V4 adds a new preservation log (earlier preservation report (no longer retained; see `CHANGELOG.md`)) that covers the V3 files, the V2 files, the first-revision files, and the original proposal. The V3 log and the V4 log are clearly distinguishable by file path.
- **C (independently verified checks vs claims carried forward).** The checks executed in this V4 session are the three listed above (mechanical, preservation, SQL-log re-inspection). Every other check mentioned in the V4 proposal, checklist, audit, or ledger is a V2 or V3 execution carried forward with the saved log paths; the V4 checklist Section 5 marks each one as "V2 run, not re-executed in V4" or similar, so the reader can tell them apart.

## 4. Final report (new in V4)

A dedicated section at the end of this changelog lists:

- every change in V4,
- every check actually executed in this V4 session (with the exact command and the output file),
- every test still planned (including T1 – T5 and the already-planned items from V2/V3), and
- every unresolved decision (carried from V3 unchanged, plus the one new V4 item: when and how to execute T1 – T5).

The V4 proposal is **not presented as experimentally validated**. H1 – H4 remain hypotheses with planned experiments. Zero research experiments were run. The archival bug fix is a specification change plus a planned test harness; its correctness has not been demonstrated by executed code.

## Unresolved decisions (carried from V3, with one V4 addition)

1 – 14 as in V2.
15. (V3) Enforcement of the Archival precondition and invariant 10. Deterministic tests specified; **in V4 the specification was tightened to branches (S) and (C) only, with branch (A) removed; the tests are now five named cases T1 – T5 and remain not executed.**
16. (V3) u* and the minimum positive-control detection share remain Open and are required by the mutually-exclusive "Supported" criterion.
17. *(V4)* **When and how to execute T1 – T5.** The current reduced-schema SQL harness does not cover the transactional archival procedure, the `MEMORY_DERIVATION` table, or the derivation closure needed by T4 and T5. The test cases are specified against the full application code and against a to-be-written SQL/Python harness; the implementation plan is a pilot item.

## Not changed

- Title, section numbering, diagrams, baselines, datasets, metrics for H1, H2, H3 primary endpoints, H4 primary endpoint, the judge protocol, the ethics position, the 41 references (byte-identical), and all V2/V3 Section 10 methodology.
- The persona layer remains **optional** and off in P for H1 – H4; minimal consolidation remains **essential**.
- H1 remains `P − A2` as the primary contrast, with the two co-primary strata (history-dependent superiority and present-state non-inferiority at margin δ_pres).
- No weekly schedule, team-member names, individual assignments, or staffing-based feasibility arguments were reintroduced; the mechanical excluded-term search confirms this.
- No new citation, no fabricated result, no unsupported claim was added.
- No DOCX was produced.

## Preservation check

The MD5 of every V3 file (`the previous-revision markdown files (no longer retained; see CHANGELOG.md)`), every V2 file (`the previous-revision markdown files (no longer retained; see CHANGELOG.md)`), the first-revision files in (earlier revision directory, deleted; see `CHANGELOG.md`), and `Graduation_Project_Proposal.md` were recorded before any V4 edit (earlier preservation baseline (no longer retained; see `CHANGELOG.md`)) and re-verified after the V4 files were written (earlier preservation snapshot (no longer retained; see `CHANGELOG.md`)), with the final combined report in earlier preservation report (no longer retained; see `CHANGELOG.md`). All matched; no V3, V2, or original file was modified.

---

## Final report

### Changes made in V4 (exhaustive)

| # | Change | File(s) | Classification | Status |
|---|---|---|---|---|
| 1 | Status note updated: version string "3" → "4"; V4 scope paragraph; V4 archival-precondition tests flagged as not executed; preservation of V3 files stated | `Graduation_Project_Proposal.md` §Status note | C | Done |
| 2 | Section 6.1 source note: stale `Source_Evidence_Ledger.md` reference replaced with `Source_Evidence_Ledger.md`; new "Reading depth column" clause added | `Graduation_Project_Proposal.md` §6.1 | S | Done |
| 3 | Rule R5 (Section 7.4) rewritten: V3 "valid-ended" sub-branch of (A) removed; two branches (S) Superseded and (C) Active derived replacement defined; symmetry statement added; bug named | `Graduation_Project_Proposal.md` §7.4 R5 | S | Done |
| 4 | Section 7.5 Active→Archived lifecycle row reworded to say "any user fact — current or historical" | `Graduation_Project_Proposal.md` §7.5 lifecycle table | S | Done |
| 5 | Section 7.5 Archival precondition paragraph rewritten with (S)/(C) definitions, the historical-memory bug statement, the removal of V3 branch (A), and the planned-but-not-executed status | `Graduation_Project_Proposal.md` §7.5 | S | Done |
| 6 | New four-condition summary table in Section 7.5: for each of {currently valid current belief, historical current belief, superseded, Archived}, which predicates return the row and whether archival is eligible | `Graduation_Project_Proposal.md` §7.5 | C | Done |
| 7 | Section 7.6 retrieval step 1 rewritten to reference (S)/(C) and the no-silent-loss guarantee for both currently valid and historical current beliefs | `Graduation_Project_Proposal.md` §7.6 | S | Done |
| 8 | Section 8 invariant 10 rewritten: enforcement text updated, V3 "(A) valid-ended" removed, five planned test cases T1 – T5 enumerated | `Graduation_Project_Proposal.md` §8 invariant 10 | S | Done |
| 9 | V4 checklist copied from V3 and updated: Section 0 Archived-semantics row rewritten; Section 1 RQ4 row untouched (H4 methodology unchanged); Section 2 Archived row rewritten; Section 3 Archived-semantics check updated; Section 5 adds T1 – T5 as planned; Section 6 unresolved-decisions list updated with item 17 | `Methodology_and_Consistency_Checklist.md` | S, C | Done |
| 10 | V4 audit copied from V3 and updated: Section 9 extended with a V4 row describing the archival-bug fix; Section 1 row for archival precondition updated; Section 7 Limits updated to flag the V4 archival-precondition tests as planned; dates and depth paragraph updated | `Research_and_Fact_Check_Audit.md` | S, C | Done |
| 11 | V4 ledger copied from V3 and updated: new **Reading depth** column added to Section A and Section B; Section D unchanged except for the explicit column; Section C unchanged | `Source_Evidence_Ledger.md` | S, C | Done |
| 12 | `mechanical_checks.py` copied and docstring updated to reference V4; baseline file for reference-list byte-identity now earlier revisions of the proposal (no longer retained; see `CHANGELOG.md`) | `validation/mechanical_checks.py` | S | Done |

### Checks actually executed in this V4 session

| # | Command | Output | Result |
|---|---|---|---|
| 1 | `python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` | `validation/mechanical_checks.output.txt` | See summary line in that file |
| 2 | `md5sum Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md Research_and_Fact_Check_Audit.md Source_Evidence_Ledger.md CHANGELOG.md` before V4 edits | earlier preservation baseline (no longer retained; see `CHANGELOG.md`) | Recorded |
| 3 | Same command after V4 files written | earlier preservation snapshot (no longer retained; see `CHANGELOG.md`) | Recorded |
| 4 | `diff (preservation baseline no longer retained; see CHANGELOG.md) (preservation snapshot no longer retained; see CHANGELOG.md)` | Included in earlier preservation report (no longer retained; see `CHANGELOG.md`) | See that file |
| 5 | `grep -c "^PASS\|^ *PASS " validation/bitemporal_checks.output.txt; tail -1 ...; md5sum ... .output.txt; md5sum ... .sql` | `validation/bitemporal_log_inspection.txt` | 43 PASS lines; final line `ALL ASSERTIONS PASSED`; MD5s match the V3 re-inspection record |

Nothing else was executed in this V4 session. In particular, T1 – T5 were **not** executed, SQL was **not** re-run, pgvector was **not** installed, no RLS test was run, no job-queue test was run, no concurrent-writer test was run, no deletion suite was built, no benchmark was run, and no H1 – H4 experiment was run.

### Tests still planned (and not executed)

| # | Test | Where specified | Status |
|---|---|---|---|
| T1 | Currently-valid current belief, no derived replacement → archival rejected | §7.5 Archival precondition, §8 invariant 10, checklist §5 | Planned |
| T2 | Historical current belief without a derived replacement → archival rejected, historical-as-of still returns the row (regression test for the V3 bug) | §7.5, §8, checklist §5 | Planned |
| T3 | Superseded version → archival accepted via (S); current-state and historical-as-of never return it | §7.5, §8, checklist §5 | Planned |
| T4 | Derived replacement under (C) → fact still returned via *r′*; provenance intact; deletion lineage from *r′* back to *r* traceable | §7.5, §8, checklist §5 | Planned |
| T5 | Deletion of original memory → rule R6 and §7.7 closure remove the derived replacement; deleted information not available through derivations | §7.5, §8, §7.7, checklist §5 | Planned |
| — | Research experiments H1 – H4 | §4, §10 | Planned (unchanged from V2/V3) |
| — | Full-schema SQL run including pgvector, RLS, job queue, concurrent writers, replay-clock harness, end-to-end deletion, invariants 1/7/8/9 | §8, checklist §5 | Planned (unchanged from V2/V3) |
| — | Judge validation on development split | §10.5 | Planned (unchanged from V2/V3) |
| — | Pilot (own suites generation and verification, baseline integration, inclusion of Graphiti B4, calibration of δ, δ_pres, u*, k, retention weights, extractor confidence) | §10.7 | Planned (unchanged from V2/V3) |

### Unresolved decisions

1 – 14 (carried from V2).
15 (V3). Enforcement of the Archival precondition and invariant 10 — spec tightened in V4; tests not executed.
16 (V3). u* and minimum positive-control detection share — Open; required by the mutually-exclusive "Supported" criterion.
17 **(V4, new).** When and how to execute T1 – T5. The reduced-schema SQL harness does not cover the transactional archival procedure, `MEMORY_DERIVATION`, or the derivation closure needed by T4 and T5. The implementation plan is a pilot item and must be agreed with the supervisor.

### Not presented as validated

The V4 proposal is a research proposal. No experiment described in it has been run. The archival-bug fix is a specification change (and a planned test harness), not a demonstrated behavioural property of running code. The user-facing claim is: the V4 Archival precondition, if correctly enforced by the transactional archival procedure, cannot silently remove a user fact — currently valid or historical — from ordinary retrieval. That claim is to be verified by T1 – T5 when the harness is built.
