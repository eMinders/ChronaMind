# Documentation inventory and existing-work impact

**Status (2026-10-23):** documentation-planning review, not implementation acceptance. This verification session began with an untracked Lab containing **61 Markdown files (84 repository-wide)** and the consolidation below already applied. Git cannot supply a prior-agent diff. A fresh pre-edit SHA-256 snapshot was taken for this session; removed documents cannot now be independently inspected, so their merge rationale is a retained prior-agent record, not a newly verified deletion. Owner direction: [F-01/S-07](FEASIBILITY_REVIEW_DECISIONS.md). Current progress: [PROJECT_STATUS.md](../PLAN/PROJECT_STATUS.md). Change/check history: [CHANGELOG.md](../CHANGELOG.md).

## 1. Authority and canonical sources

| Topic | Canonical source | Disposition / unique value |
|---|---|---|
| Scope, novelty, H1–H4, contrasts, experimental protocol | `Graduation_Project_Proposal.md` | Retain byte-identical; §§4/4.1/6/10 govern research. |
| Normative mechanism contract and change control | `TECHNICAL_SPECIFICATION.md` | Retain byte-identical; §§3–8/13 govern implementation and controlled changes. |
| Research consistency and unresolved research items | `Methodology_and_Consistency_Checklist.md` | Retain byte-identical; not a progress board. |
| Citation verification and reading depth | `Research_and_Fact_Check_Audit.md`, `Source_Evidence_Ledger.md` | Retain byte-identical; audit findings vs per-source provenance are distinct. |
| Phase purpose, research checkpoints and acceptance matrix | `PROJECT_ROADMAP.md` | Retain; repair planning/navigation only. No change to §F or mandatory §G acceptance. |
| Claim → mechanism → test → evidence mapping | `TRACEABILITY_MATRIX.md` | Retain; repair decision-number and obsolete navigation references only. |
| Engineering boundaries, versions, interfaces and fallback controls | `PLAN/ARCHITECTURE.md` | Canonical destination of former review architecture. Planned contracts, not implemented APIs. |
| Task prerequisites and task acceptance | `PLAN/TASK_GRAPH.md` | Canonical task system. `DEPENDENCY_MAP.md` is its synchronized graph/sequence view. |
| Actual status and approval evidence | `PLAN/PROJECT_STATUS.md` | Single hand-updated board; no duplicate review snapshot. |
| Open decisions and engineering rationale | `PLAN/DECISIONS_REGISTER.md` | Research state mirrors named authoritative decisions; no inference from matching numbers. |
| People/review pairings, estimates, workflow/publication | `PLAN/ASSIGNMENTS.md`, `SCHEDULE.md`, `CONTRIBUTING.md`, `GITHUB_SETUP.md` | Distinct purposes; retain. RL-001 team approval is owner-confirmed; individual responses, identities and availability remain unavailable/provisional. |
| Lab/main-app boundary | `PLAN/INDEPENDENCE_BOUNDARY.md` | Retain; flag real dependency-policy conflict rather than silently weaken it. |
| Advisory framework capability evidence | `review/TECHNICAL_FEASIBILITY_REVIEW.md` | Retain source-linked comparison and research confounder analysis. Historical snapshot, not current progress. |
| Alternatives and uncertainty ratings | `review/ARCHITECTURE_DECISION_MATRIX.md` | Retain qualitative criteria and U-01–U-10, separate from normative decisions. |
| Owner architectural direction and legacy review proposal disposition | `review/FEASIBILITY_REVIEW_DECISIONS.md` | Retain F-01/S-07 evidence and mapping of unexecuted FR/P ideas into RL tasks. |
| Navigation and documentation dispositions | Lab README, review README, this file | Retain; replace repeated architecture/status prose with links. |
| Change history | `CHANGELOG.md` | One history; older useful correction records merged as a bounded historical note. |

Paths in the table are relative to `research-lab/`. There is no new roadmap, task system, research freeze or version decision.

## 2. Markdown inventory and consolidation performed

The retained prior-agent inventory reports **88 Markdown files**, of which **65** were under the Lab, before its four deletions. It reports categorizing every path, reading deletion candidates completely and searching inbound references (including tooling). This session confirms the resulting 84/61 counts and checks current references, but cannot re-read the absent originals. This is not a claim that every unrelated main-app guide was audited for factual accuracy.

| Files / family | Purpose, references and action |
|---|---|
| Lab root (9 files) | Eight standing research/roadmap/traceability/history documents plus navigation README; retain distinct authority as §1. |
| `PLAN/*.md` (10 files) | Planning views above; retain, align dependencies and honest approvals. No member identity inferred from legacy main-app docs. |
| `PLAN/templates/*.md` (4 files) | Issue/task/bug/decision and PR publication formats; retain for RL-006 tooling/workflow. |
| Individual issue drafts (26 files) | Retain detailed acceptance/claim context not fully duplicated by task cards. Correct affected dependencies and false Ready/current-PR assertions. Local only. |
| `PLAN/issue_drafts/_bulk_tasks.md` | Retain unique unexpanded task deliverables/acceptance for RL-006; synchronize RL-033/080 and migration criterion. |
| `PLAN/issue_drafts/_decisions.md` | **Removed** after comparison with register: only a stale mirror, no unique evidence. RL-006 generates decision bodies from the register + existing decision template. |
| Issue-draft README | Retain; accurate full/compact coverage and publication procedure. |
| `implementation/`, `evaluation/`, `application/`, `reports/`, `research/benchmarks/` READMEs (5) | Retain local module contracts, future artifacts and licenses; useful landing pages, not engine completion evidence. |
| `review/RECOMMENDED_ARCHITECTURE.md` | **Merged then removed**: contracts/reuse/data flow/testing/fallback controls are in PLAN architecture §§8–10. Correct stale A2 description (overwrite, not merely disable invalidation), test-tree placement, deletion boundary and false NumPy/SciPy claim; do not copy those defects. |
| `review/UPDATED_PROJECT_STATE.md` | **Merged then removed**: status/evidence limits in status board, validation/traceability, owner record and this inventory; no second mutable snapshot. Its mixed D-numbering and obsolete S-07 question are superseded. |
| `review/DOCUMENTATION_CHANGELOG.md` | **Merged then removed**: useful historical correction/change/check summary in root changelog. Repeated file-by-file no-change lists are replaced by this inventory. Historical seven-file preservation claim remains a prior report, not a check performed now. |
| Remaining review files (5) | Retain advisory evidence/matrix, owner record, this inventory and short review navigation. No new review report created. |
| Repository-root `README.md`, `AGENTS.md`, `CLAUDE.md`, `SECURITY.md` | Main-app onboarding, agent rules and security policy; retain unchanged. Not Lab research authority. |
| Main-app `docs/*.md` (12) | Operational guides plus legacy proposal/integration blueprint; retain unchanged. Legacy proposal contains named individuals and older framing; blueprint contains LangGraph/MCP and production integration concepts beyond Lab scope. They are not duplicates safe to delete or promote to V4 authority. |
| `evals/metrics/prompts/*.md` (5), `app/core/prompts/*.md` (2) | Tool/runtime inputs, not expendable documentation; retain unchanged. |

No source code, source paper, fetched source, test data, or validation artifact was deleted or moved. No documents were moved; four redundant Markdown files were removed after preserving useful content. Remaining Markdown count: **84**. The saved hashes/logs remain historical evidence rather than being regenerated to disguise drift.

## 3. Prior-agent review and corrections

The partial pass had already selected dedicated Lab CI, component contracts, independent version-selection policy and several dependency fixes in the register/architecture/task graph/map. Continuation retains those changes and finishes inconsistent copies:

- Mirrors require RL-002 + RL-003; RL-003 also requires runtime RL-013. Only the two mirrors can run in parallel after that chain.
- RL-025 requires the harness interface RL-030; RL-033 needs the RL-011 development-validation procedure; RL-080 needs RL-070 deletion/epoch integration; RL-090 needs actual RL-070 for T5.
- D-18 evidence is produced by RL-091, not a prerequisite of authoring the schema it tests. D-21 final B4 inclusion and D-22 final freeze do not block development qualification/validation of their own prerequisites.
- Phase completion vs preparatory task readiness is distinguished. No pilot-dependent margin is used to block DDL. No initial skeleton claims seeded hosted-model determinism.
- Corrected assignment copies to the existing task-graph ownership: M1 release author, M4 independent reproducer. No reassignment of named people or availability invented.
- Local bootstrap claims were designed for use after RL-001 approval; the owner has since confirmed that approval. The procedure removes the cycle where RL-006 required foundation tasks that could not be claimed until RL-006. No claim or publication was executed.
- Missing finalization script is explicitly reported. Historical script/obsolete directory assertions are not current acceptance evidence.

## 4. Unresolved contract issues and actionable blockers

1. **RL-001:** owner-confirmed team approval is recorded in `PLAN/PROJECT_STATUS.md` §6. Individual responses, reviewer identities, signatures, reviewed revision/hash, actual review date/time and issue comments were not supplied. These are audit limitations, not an open approval gate. Keep RL-006 publication authorization separate.
2. **Technology:** no Lab manifest/lock or selected runtime/PG-extension pin; no live release/build/integration check. RL-012/013/003 must supply dated upstream/build/license evidence and independent reproducible locks. Historical baseline commits and PG 16.15 evidence are not current compatibility certification.
3. **Normative shorthand conflict:** proposal §7.4 ordinary historical-as-of uses β=ρ, whereas spec §3.5 exposes optional β/t≤β. Proposal R7 is multi-valued handling; spec summary calls R7 isolation and its R8 misses the audit β check. Spec entity summaries (`MEMORY`/`PREDICATE`/`DELETION_LOG`) differ from proposal table names; lineage 'incoming' wording differs from derived→source edge direction. Do not silently edit either contract. RL-040 design review must map named semantics/entities to authoritative passages and escalate any real behavioral conflict under spec §13 before acceptance. No public API that blurs historical vs audit queries.
4. **Dependency-policy conflict:** INDEPENDENCE_BOUNDARY.md §3.2 prohibits FastAPI/SQLModel/Langfuse/Prometheus even as independently selected Lab dependencies, while proposal §9 names FastAPI/Pydantic and project agent rules require LLM tracing. Hybrid is not permission to violate that boundary. RL-040/design review must settle any scoped library allowance through the applicable supervisor process; RL-001 approval does not settle this conflict. Until authorized, use permitted standalone planning/interfaces, not prohibited imports. Existing CI exception in §4 permits only adding a Lab job to main-app CI; it does **not** expressly permit the selected dedicated workflow. D-LAB-CI/RL-005 now require the §5 supervisor-approved scoped exception before implementation. RL-004 must reject unapproved allowlist entries and other out-of-Lab diffs. Boundary document and authoritative checklist were retained unchanged; no approval was invented.
5. **Research/safety decisions:** D-09/17/19/20/23/24/27 and pilot-dependent values remain open. Essential mechanisms and mandatory validation are not waivable through routine engineering judgment. Failure of newest technology means a justified compatible fallback, not altered experiments.

## 5. Verification-session finishing changes

Retained the existing consolidation; no further deletion or move was justified. Corrected remaining P0 acceptance wording and ten-document review counts, RL-001 approval/publication distinction, assignment/reviewer copies (M1 release author, M4 independent reproducer), RL-061/RL-025 prerequisite summaries, D-27 source numbering and optional async-test dependency timing. Separated RL-130 package handoff from downstream RL-130-REPRO validation to avoid an acceptance cycle while retaining the phase reproduction gate. Recorded local main-app lock versions as contextual TOML evidence only. Final executed checks are in CHANGELOG.md; no historical evidence artifact was rewritten.

Immediate sequence is canonical in DEPENDENCY_MAP.md §7. Full schema, T1–T5, concurrent writers, RLS, queue, deletion closure, judge, baselines and H1–H4 are still unimplemented/unexecuted. This review does not certify paper results, legal compliance, package compatibility or experiments.
