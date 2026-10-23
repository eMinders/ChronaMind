# Feasibility review decisions

**Authority.** The review evidence remains advisory; the owner decision below is explicit implementation-planning direction, not supervisor acceptance or a new Frozen research decision. The V4 proposal and normative technical specification remain the implementation contract. Task execution and team acceptance follow `PLAN/CONTRIBUTING.md` and RL-001; no team signatures, issues, implementation, or experiments are recorded here.

## 1. Owner direction — F-01 / S-07 (2026-10-23)

**Evidence:** the project owner's instruction, “Engineering Decisions, Execution Readiness, and Documentation Consolidation”, in this task session explicitly selects **Hybrid** and authorizes engineering judgment and safe consolidation.

**Decision:** implement ChronaMind's research-critical mechanisms in-house; reuse stable compatible infrastructure/libraries; keep Mem0 (B3) and Graphiti (B4, conditional) isolated as evaluation baselines. Use the existing V4 specification as the contract. Hybrid is approved for implementation planning. **No new Frozen row is required or added solely to restate this selection.**

**Rationale and alternatives:** the [decision matrix](ARCHITECTURE_DECISION_MATRIX.md) and [feasibility review](TECHNICAL_FEASIBILITY_REVIEW.md) explain why extending Graphiti/Mem0 as P risks ablation control and the deletion boundary; an entirely custom infrastructure stack adds unnecessary work. Hybrid retains ownership of temporal transactions, gate, retrieval filtering/fusion switches, derivation DAG, deletion closure, isolation and invariant 10 while reusing PostgreSQL primitives and compatible libraries. Trade-off: ChronaMind still bears the full mechanism implementation and testing workload.

**S-07 disposition:** the former architecture-choice and blanket implementation-gate question is superseded by the owner's direction. S-07 is **not an additional architecture-choice or execution gate**. Academic supervisor acceptance of the review has **not** been evidenced and is not fabricated; any academic feedback belongs to the existing scope/research decisions, not a repeat architecture selection. RL-001, technical choices, component dependencies, ethics and experiment-readiness gates are not waived.

**Engineering authority:** routine choices within the contract may be made without serial approvals. Consequential choices record rationale, alternatives, trade-offs and evidence in [PLAN/ARCHITECTURE.md](../PLAN/ARCHITECTURE.md) or [PLAN/DECISIONS_REGISTER.md](../PLAN/DECISIONS_REGISTER.md). Escalate research-contract changes, safety/ethics issues, genuine governance conflicts and high-impact uncertainty. Priority: research validity; correctness/security/transactions; spec compatibility; reproducibility/testability; maintainability; simplicity/performance; suitable current stable technology.

## 2. Canonical implementation and research references

- Component boundaries, library allocation, data flow, security, failure handling and fallback controls: [PLAN/ARCHITECTURE.md](../PLAN/ARCHITECTURE.md).
- Frozen semantics and acceptance contract: [TECHNICAL_SPECIFICATION.md](../TECHNICAL_SPECIFICATION.md) §§2–11; change control §13. Decision numbering in its Dec# table is **not identical** to PLAN's roadmap-derived D-xx numbering; identify decisions by name and source, not number alone.
- Research scope/novelty, H1–H4, contrasts, statistics and freeze: proposal §§4, 6.2–6.3, 10; methodology checklist. No contribution claim changes.
- Open decisions and required supervisor input: [DECISIONS_REGISTER.md](../PLAN/DECISIONS_REGISTER.md). Former S-01→D-09; S-02→D-27; S-03→D-17; S-04→D-23/D-24; S-05→D-20; S-06→D-19. These remain open, not resolved by F-01.
- F-02/F-10 baseline-only imports and main-app independence: [INDEPENDENCE_BOUNDARY.md](../PLAN/INDEPENDENCE_BOUNDARY.md). F-03 novelty remains unchanged. F-04/F-05 source pins: ledger and spec §10. F-06–F-09 deletion, lineage, hard filtering, isolation: spec §§3–7.
- Actual progress: [PROJECT_STATUS.md](../PLAN/PROJECT_STATUS.md), not a second review-level snapshot.

## 3. Prototype ideas retained, not executed

These are optional evidence-gathering ideas, **not a second task system or prerequisites to the owner selection**. Execute later only within the relevant RL task's approved scope and budget, using isolated disposable environments; do not add baseline dependencies to P. Preserve logs and a bounded memo, and record inconclusive outcomes honestly.

| Legacy ID | Question / bounded evidence | Existing task / destination |
|---|---|---|
| P-01 | Graphiti `remove_episode`: one canary and seven-channel sweep; check propagation to sagas, communities, embeddings and lexical entries. Native propagation remains unverified. | RL-032 B4 qualification; `review/prototypes/P-01/` if independently investigated |
| P-02 | Graphiti filters against the proposal worked-example point/window/audit predicates; do not assume equivalence or extend P to match Graphiti. | Optional RL-032 diagnostic; `review/prototypes/P-02/` |
| P-03 | Full-schema two-writer supersession: exclusion violations, contention, deadlocks, replay ties. | RL-091; `implementation/tests/concurrency/` |
| P-04 | B3 OSS smoke on declared dev subset with frozen-model configuration; headline managed-platform scores are not reproduction targets. | RL-031 / RL-100; `evaluation/runs/<ts>/B3_smoke/` |
| P-05 | Reduced-schema SQL on compatible supported PG candidates; preserve original V2 log, collect version-specific logs and extension/build evidence. | RL-012; `review/prototypes/P-05/` if investigated |
| P-06 | Judge human agreement, confusion matrix, κ and perturbations on dev sample with pinned rubric/model. | RL-033 / RL-100; `evaluation/judge/` |

**Disposition of legacy FR proposals (none executed):** FR-001 team reading/concerns are incorporated into RL-001 review; FR-002 architecture-choice approval memo is superseded by §1 (not marked completed); FR-003 deletion-boundary channel mapping is part of RL-070/RL-093 acceptance; FR-004/P-01 and FR-005/P-05 remain the optional ideas above; FR-006 first-batch drafting is part of RL-006 using existing issue drafts and TASK_GRAPH.md. No FR issue, deadline, assignment or new memo is required merely to reproduce existing content.

## 4. Review-specific risks

The roadmap §I remains the execution risk register. Review-specific controls are: do not import a framework's hidden extraction/gating into P; baseline stores/processes never share P state; preserve identical replayed extraction outputs where ablations require them; verify each switch changes only its declared mechanism; prefer de-scoping optional work over altering essential mechanisms. Reduced-schema logs do not validate the full engine. Prototype output is not production code until it passes normal component review. See the matrix uncertainties U-02/U-03/U-05 and the canonical architecture's fallback controls.

## 5. Change and evidence record

[CHANGELOG.md](../CHANGELOG.md) records the consolidation, preserved historical corrections and checks. [EXISTING_WORK_IMPACT_ANALYSIS.md](EXISTING_WORK_IMPACT_ANALYSIS.md) records the documentation inventory, canonical-source assignments and unresolved contract/governance issues. No code, package installation, experiment or GitHub action is performed by this document.
