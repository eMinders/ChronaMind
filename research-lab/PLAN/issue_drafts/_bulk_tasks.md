# _bulk_tasks.md — Compact task drafts

**Status:** compact drafts for issues not yet expanded into individual files. At publication time (`RL-006`), Member 6 expands each entry below into a full issue body using [`../templates/issue_task.md`](../templates/issue_task.md).

Fields: ID · title · phase · proposed owner · depends on · blocks · deliverables · acceptance (short) · dependency class · effort · risk.

---

## P3

### RL-031 — Baselines B0, B1, B2, B2h, B3 (Mem0 pinned)
- Phase: P3. Parent deliverable: `D-P3`. Owner: Member 5 (R-APP).
- Depends on: `RL-030`. Non-blocking: `RL-014`.
- Blocks: `RL-100`.
- Deliverables: `evaluation/baselines/{b0,b1,b2,b2h,b3_mem0}/` with pinned commit SHAs recorded.
- Acceptance: smoke scores on dummy suite; manifests record model id, temperature, seed, dependency SHA.
- Dep class: Blocking. Effort: L. Risk: medium.

### RL-032 — B4 Graphiti baseline (conditional)
- Phase: P3. Parent deliverable: `D-P3`. Owner: Member 5 (R-APP).
- Depends on: `RL-031`, `RL-011` (development qualification plan).
- Final D-21 inclusion is frozen in RL-100, not an adapter-preparation prerequisite.
- Blocks: — (optional in experiments).
- Deliverables: `evaluation/baselines/b4_graphiti/` disabled by default; enabled on pilot approval.
- Acceptance: smoke scores on dummy suite when enabled; manifest records pinned commit.
- Dep class: Blocking + Decision. Effort: M. Risk: medium.

### RL-033 — LLM-judge harness and perturbation calibration
- Phase: P3. Parent deliverable: `D-P3`. Owner: Member 5 (R-APP, harness track).
- Depends on: `RL-030`, `RL-020`, `RL-011` (development-validation procedure).
- Blocks: `RL-101`, `RL-102`, `RL-103`.
- Deliverables: `evaluation/judge/` with reference-based grading and `[36]` perturbation calibration.
- Acceptance: human development agreement/confusion matrix/κ and perturbation evidence under RL-011 procedures; RL-100 freezes final D-22 criterion before test scoring, not before constructing its prerequisites.
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-034 — Statistics module
- Phase: P3. Parent deliverable: `D-P3`. Owner: Member 1 (R-INFRA, reproducibility-centric; independent of engine).
- Depends on: `RL-030`.
- Blocks: `RL-110`.
- Deliverables: `evaluation/stats/` with cluster bootstrap, sign-flip permutation, Holm correction, Clopper–Pearson, non-inferiority.
- Acceptance: reproduces `validation/cp_bounds.output.txt`; unit tests per statistic.
- Dep class: Blocking. Effort: M. Risk: low.

---

## P4

### RL-041 — Migrations
- Phase: P4. Owner: Member 4 (R-DB).
- Depends on: `RL-040`.
- Blocks: `RL-050`, `RL-060`, `RL-070`, `RL-080`.
- Deliverables: `implementation/migrations/` with up/down scripts.
- Acceptance: up→down→up restores logical schema/data/constraint equivalence to a fresh up; no physical byte-identity claim.
- Dep class: Blocking. Effort: S–M. Risk: medium.

### RL-042 — Transactional supersession, archival, deletion procedures
- Phase: P4. Owner: Member 2 (R-ENGINE).
- Depends on: `RL-040`.
- Blocks: `RL-050`, `RL-061`, `RL-070`, `RL-090`.
- Deliverables: Procedures under `implementation/engine/` enforcing invariant 10 (S) ∨ (C).
- Acceptance: returns `VALIDATION_ERROR archival_precondition_unmet` on T1/T2 setups; supersession runs atomically.
- Dep class: Blocking. Effort: M. Risk: high.

### RL-043 — Retrieval predicates (current / historical / audit-as-of)
- Phase: P4. Owner: Member 2 (R-ENGINE).
- Depends on: `RL-040`.
- Blocks: `RL-060`, `RL-120`.
- Deliverables: Query functions matching `TECHNICAL_SPECIFICATION.md §3.5`.
- Acceptance: SQL/Python assertions reproduce the §7.4 worked example at full schema.
- Dep class: Blocking. Effort: M. Risk: medium.

---

## P5

### RL-050 — Validation gate + assertion-mode classifier
- Phase: P5. Owner: Member 3 (R-METHOD).
- Depends on: `RL-041`, `RL-042`.
- Blocks: `RL-051`, `RL-070`, `RL-092`, `RL-103`.
- Deliverables: `implementation/engine/gate/` with reason-coded decisions per §7.5.
- Acceptance: gate assigns labelled outcome on synthetic candidates at ≥ pilot-determined accuracy; idempotency property-tests pass.
- Dep class: Blocking. Effort: L. Risk: high.

### RL-051 — Conflict detector + supersession wiring
- Phase: P5. Owner: Member 3 (R-METHOD).
- Depends on: `RL-042`, `RL-050`.
- Blocks: `RL-060`, `RL-101`.
- Deliverables: Conflict detector integrated with the supersession procedure.
- Acceptance: a known contradiction triggers supersession once and only once.
- Dep class: Blocking. Effort: M. Risk: medium.

---

## P6

### RL-060 — Dense + lexical + RRF retrieval
- Phase: P6. Parent deliverable: `D-P6`. Owner: Member 5 (R-APP).
- Depends on: `RL-043`, `RL-051`.
- Blocks: `RL-070`, `RL-080`, `RL-102`, `RL-120`.
- Deliverables: `implementation/engine/retrieval/` with pgvector exact per user, PostgreSQL FTS (not BM25), RRF k=60, HNSW only in scalability tests.
- Acceptance: Recall@k, MRR match harness definitions; snapshot-consistency test passes.
- Dep class: Blocking. Effort: L. Risk: high.

### RL-061 — Minimal consolidation + MEMORY_DERIVATION lineage
- Phase: P6. Owner: Member 2 (R-ENGINE).
- Depends on: `RL-040`, `RL-042`.
- Blocks: `RL-070`, `RL-090`, `RL-105`, `RL-120`.
- Deliverables: `implementation/engine/consolidation/` with derivation edges per §7.1.
- Acceptance: lineage query returns the expected chain from a derived version back to source versions.
- Dep class: Blocking. Effort: M. Risk: medium.

---

## P7

### RL-070 — Deletion closure §7.7 in-boundary sweep
- Phase: P7. Owner: Member 3 (R-METHOD).
- Depends on: `RL-042`, `RL-050`, `RL-060`, `RL-061`.
- Blocks: `RL-093`, `RL-105`, `RL-120`.
- Deliverables: `implementation/engine/deletion/` performing the transitive derivation closure, boundary-free audit, cache invalidation, deletion-epoch re-ingestion ban.
- Acceptance: T5 passes; zero residual retrievable copy on any §7.7 in-boundary channel; never-stated probe never answered.
- Dep class: Blocking. Effort: L. Risk: very high (gates H4).

---

## P8

### RL-080 — Fast + slow paths; PostgreSQL job queue
- Phase: P8. Parent deliverable: `D-P8`. Owner: Member 5 (R-APP).
- Depends on: `RL-041`, `RL-050`, `RL-060`, `RL-070` (deletion/epoch integration).
- Blocks: `RL-091`, `RL-120`.
- Deliverables: `implementation/engine/paths/` with `FOR UPDATE SKIP LOCKED`, idempotency keys, per-user advisory locks, dead-letter table.
- Acceptance: idempotency test — same event re-enqueued N times produces exactly one belief transition.
- Dep class: Blocking. Effort: L. Risk: high.

### RL-081 — Replay-clock harness and visibility-lag instrumentation
- Phase: P8. Parent deliverable: `D-P8`. Owner: Member 1 (R-INFRA, reproducibility tooling).
- Depends on: `RL-080`.
- Blocks: `RL-091`.
- Deliverables: Replay-clock utility; visibility-lag metric.
- Acceptance: descriptive measurement per §10 O6.
- Dep class: Blocking. Effort: M. Risk: low.

---

## P9

### RL-091 — Concurrency + replay-clock tests (invariants 3, 4, 9)
- Phase: P9. Owner: Member 4 (R-DB).
- Depends on: `RL-040`, `RL-080`, `RL-081`.
- Blocks: `RL-094`.
- Deliverables: Two-writer supersession tests; replay-clock tie tests; exclusion-constraint under contention (D-18 pilot).
- Acceptance: no invariant violation; `recorded_from` strictly increasing.
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-092 — Cross-user isolation + RLS tests (invariant 7)
- Phase: P9. Owner: Member 3 (R-METHOD).
- Depends on: `RL-040`, `RL-050`.
- Blocks: `RL-094`.
- Deliverables: Per-user partition tests; RLS second-layer tests with BYPASSRLS caveats.
- Acceptance: No cross-user content leaks.
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-093 — Deletion-closure tests (invariant 8)
- Phase: P9. Owner: Member 3 (R-METHOD).
- Depends on: `RL-070`.
- Blocks: `RL-094`.
- Deliverables: §7.7 in-boundary channel sweep tests.
- Acceptance: zero residual on every in-boundary channel.
- Dep class: Blocking. Effort: M. Risk: high.

---

## P10 (remaining experiments)

### RL-101 — H1 experiment
- Phase: P10. Owner: Member 3 (R-METHOD).
- Depends on: `RL-100`, `RL-024`, `RL-051`.
- Blocks: `RL-110`, `RL-130`.
- Deliverables: `evaluation/runs/<ts>/H1/report.md` + `per_cluster.jsonl`.
- Acceptance: decision per §4.1 for each stratum.
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-102 — H2 experiment
- Phase: P10. Owner: Member 4 (R-DB).
- Depends on: `RL-100`, `RL-020`, `RL-025`, `RL-060`.
- Blocks: `RL-110`, `RL-130`.
- Deliverables: `evaluation/runs/<ts>/H2/report.md` with Recall@k, MRR, nDCG.
- Acceptance: decision per §4.1 (intersection-union of both contrasts).
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-103 — H3 experiment
- Phase: P10. Owner: Member 3 (R-METHOD).
- Depends on: `RL-100`, `RL-022`, `RL-050`.
- Blocks: `RL-110`, `RL-130`.
- Deliverables: `evaluation/runs/<ts>/H3/report.md` with ASR, LUAR, gate-decision breakdown.
- Acceptance: decision per §4.1 (ASR AND LUAR).
- Dep class: Blocking. Effort: M. Risk: medium.

---

## P11, P12, P13

### RL-110 — Statistical reports + error analysis per RQ
- Phase: P11. Parent deliverable: `D-P11`. Co-authors: Member 3 (H1 + H3 sections), Member 4 (H2 section), Member 2 (H4 section and integration), Member 1 (stats glue + error-analysis cross-cut).
- Depends on: `RL-101`, `RL-102`, `RL-103`, `RL-105`, `RL-034`.
- Blocks: `RL-130`, `RL-131`.
- Deliverables: `reports/H{1,2,3,4}.md` + `reports/error_analysis.md`.
- Acceptance: §10.6 statistics module output matches the frozen rule; error analysis covers answered and unanswered items per stratum; each co-author signs their section.
- Dep class: Blocking. Effort: M. Risk: medium.

### RL-120 — PDT inspector, correction, historical, deletion flows
- Phase: P12. Parent deliverable: `D-P12`. Owner: Member 5 (R-APP). Optional pairing with Member 1 if load warrants.
- Depends on: `RL-042`, `RL-043`, `RL-050`, `RL-060`, `RL-061`, `RL-070`, `RL-080`.
- Blocks: `RL-130` (if application track is Mandatory).
- Deliverables: `application/` with the six §11 flows plus observability dashboard.
- Acceptance: each §11 user flow has an acceptance test; cross-user privacy tests pass.
- Dep class: Blocking (within application track). Effort: L. Risk: medium.
- Note: can start in parallel with P10 once P8 stable.

### RL-130 — Release package
- Phase: P13. Parent deliverable: `D-P13`. Owner: Member 1 (R-INFRA, release engineering).
- Depends on: `RL-101`, `RL-102`, `RL-103`, `RL-105`, `RL-110`, `RL-094`, (`RL-120` if mandatory).
- Blocks: `RL-130-REPRO`, `RL-131`.
- Deliverables: `release/vX.Y.Z/` with schema, code, suites, configs, docs, licence files.
- Acceptance: structural checks pass; `RL-130-REPRO` subsequently confirms the smoke evaluation reproduces.
- Dep class: Blocking. Effort: L. Risk: medium.

### RL-130-REPRO — Independent reproduction run of the release
- Phase: P13. Parent deliverable: `D-P13`. Owner: Member 4 (R-DB; intentionally not the release author).
- Depends on: `RL-130`.
- Blocks: `RL-131`.
- Deliverables: `release/vX.Y.Z/REPRODUCTION.md` showing the smoke evaluation re-run on an independent PostgreSQL instance.
- Acceptance: smoke summary matches `RL-130`'s published summary to within the declared tolerance.
- Dep class: Blocking (review gate). Effort: M. Risk: medium.

### RL-131 — Final report + defence deck
- Phase: P13. Parent deliverable: `D-P13`. Lead author: Member 6; co-authors: Members 2, 3, 4 (one hypothesis each); reviewers: Members 1 + 5 + supervisor.
- Depends on: `RL-110`, `RL-130`, `RL-130-REPRO`.
- Blocks: — (final).
- Deliverables: `reports/final_report.md`, `reports/defence_deck_notes/`.
- Acceptance: covers every decision (Supported / Not supported / Inconclusive) with evidence; reviewers sign off.
- Dep class: Blocking. Effort: L. Risk: medium.
