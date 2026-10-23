# ARCHITECTURE_DECISION_MATRIX.md

**Status.** Advisory. Does not supersede any Frozen V4 decision. Companion to [`TECHNICAL_FEASIBILITY_REVIEW.md`](TECHNICAL_FEASIBILITY_REVIEW.md).

**Purpose.** Compare three architectures for the memory engine against the same criteria, with evidence for each rating and an explicit record of assumptions and uncertainties. **No arbitrary numeric score is assigned.** Instead each cell is a short rating phrase plus the one-line evidence or caveat that justifies it; the final recommendation reads from the pattern of ratings and from §3 of the feasibility review.

**Candidates.**

- **A. From-scratch.** The project implements the full engine of `Graduation_Project_Proposal.md` §7–§8 against PostgreSQL + pgvector + PG FTS, using no third-party memory-engine framework. Baselines `B3` (Mem0) and `B4` (Graphiti, conditional) are still integrated as external systems in `evaluation/baselines/`.
- **B. Extend an existing framework.** The project adopts Graphiti (or Mem0) as the memory engine of `P` and *extends* it with the project-specific gate, derivation DAG, deletion closure, and audit-as-of predicate. The framework becomes a dependency of P, not just a baseline.
- **C. Hybrid.** The project implements the research-critical mechanisms (bi-temporal versioning with the three predicates, validation gate, derivation DAG, deletion closure, invariant 10 `(S) ∨ (C)`, isolation) in its own code; and reuses *selected* libraries and components (`pgvector`, PG FTS, `btree_gist`, standard RRF implementation, LLM adapter, tenacity, Pydantic/SQLModel). Graphiti and Mem0 remain baselines (`B4`/`B3`).

**Rating vocabulary.** For each criterion a cell is one of:

- **Supports** — the candidate meets the criterion without additional risk.
- **Supports with cost** — the candidate meets the criterion but adds significant implementation or operational cost.
- **Partial** — meets some but not all facets of the criterion; the gap is identified in the evidence column.
- **Threatens** — the candidate makes the criterion materially harder to meet.
- **Not verified** — the review cannot establish the answer without a prototype or code-level inspection.

---

## 1. Criteria

| ID | Criterion | Source of truth |
|---|---|---|
| C-01 | **Research contribution preserved.** The ablations A1, **A2**, A2b, **A3**, A4, **A5**, **A6**, A7, **A8**, A9, A10 can be constructed cleanly (confirmatory ones in bold). | `Graduation_Project_Proposal.md` §10.4 |
| C-02 | **Bi-temporal semantics (four derived conditions) implementable** with the three retrieval predicates and the R1–R8 state transitions. | `Graduation_Project_Proposal.md` §7.4; `TECHNICAL_SPECIFICATION.md` §3.3, §3.4, §3.5 |
| C-03 | **Write-time gate with the §7.5 checks** and the reason-code log, plus the ability to disable it (`A3`) and to disable each check (`A4`). | `Graduation_Project_Proposal.md` §7.5 |
| C-04 | **Derivation DAG and deletion closure over the §10.3 seven channels** within one transaction (invariant 8); R6 removes every version of the memory item. | `Graduation_Project_Proposal.md` §7.7; §10.3 "Deletion channels"; `TECHNICAL_SPECIFICATION.md` §7 |
| C-05 | **Invariant 10 archival precondition `(S) ∨ (C)`** enforced by a transactional procedure; T1 – T5 regression tests pass. | `Graduation_Project_Proposal.md` §7.5 "Archival precondition"; `TECHNICAL_SPECIFICATION.md` §4 |
| C-06 | **Per-user isolation (invariant 7)** with RLS as a second layer where the store allows it, plus cross-user tests. | `Graduation_Project_Proposal.md` §7.8, §8 invariant 7; `research/sources/pages/postgres_rls.txt` |
| C-07 | **Hybrid retrieval under the temporal predicate** with pgvector exact per user, PG FTS, and RRF k = 60 per `[12]`. | `Graduation_Project_Proposal.md` §7.6; `TECHNICAL_SPECIFICATION.md` §2 D-12, D-13 |
| C-08 | **Asynchronous paths with the PostgreSQL job queue** (`FOR UPDATE SKIP LOCKED`, idempotency keys, per-user advisory lock, dead-letter). | `Graduation_Project_Proposal.md` §7.1 – §7.3; `TECHNICAL_SPECIFICATION.md` §8 |
| C-09 | **Reproducibility and baseline parity** (`§10.1` controlled vs system-specific split; run manifests; pinned commits). | `Graduation_Project_Proposal.md` §10.1; `PROJECT_ROADMAP.md` §D.3 |
| C-10 | **Statistical analysis integrity** — scenario-level Clopper–Pearson for H4; cluster bootstrap + sign-flip permutation + Holm; non-inferiority at δ_pres and δ. | `Graduation_Project_Proposal.md` §10.6 |
| C-11 | **Operational complexity and infrastructure cost.** Services the Lab must stand up, LLM cost per episode, hardware. | `Graduation_Project_Proposal.md` §9, §15 |
| C-12 | **Licence and attribution.** Compatible with release expectations in roadmap Phase P13. | `research-lab/README.md` "Licence and attribution"; `research/sources/pages/graphiti_license.txt`, `mem0_license.txt` |
| C-13 | **Remaining implementation workload.** Measured by uncovered mechanisms × their enforcement sites in `TRACEABILITY_MATRIX.md` §2. | `TRACEABILITY_MATRIX.md`; `PROJECT_ROADMAP.md` §K |
| C-14 | **Fit with the frozen specification** (`TECHNICAL_SPECIFICATION.md` §2 Frozen rows and `Methodology_and_Consistency_Checklist.md` §0 "frozen in V4"). | `TECHNICAL_SPECIFICATION.md` §2; `Methodology_and_Consistency_Checklist.md` §0 |
| C-15 | **Team workload shape and six-way parallelism** (`ASSIGNMENTS.md` §5 phase matrix). | `PLAN/ASSIGNMENTS.md`; `PLAN/TASK_GRAPH.md` |

---

## 2. Decision matrix

| # | Criterion | A. From scratch | B. Extend framework (Graphiti) | B. Extend framework (Mem0) | C. Hybrid |
|---|---|---|---|---|---|
| C-01 | Ablations preserved | **Supports.** We own every switch. | **Threatens** for `A2` and `A8`; `A5`/`A6` would need to disable Graphiti channels independently. Edge-centric model differs from the row-centric ablation surface in §10.4. | **Threatens** H1 outright: ADD-only has no supersession to disable. H3/H4 have no analogue. | **Supports.** Research-critical switches live in our code; reused libraries are deterministic. |
| C-02 | Bi-temporal semantics with three predicates | **Supports with cost.** Full schema + CHECK/trigger/exclusion-constraint work. Already partly validated for reduced schema (`validation/bitemporal_checks.output.txt`, 43 PASS). | **Partial / Not verified.** Edge validity windows exist, but mapping to current/historical-as-of/audit-as-of as named predicates with R8 and the Archived-row rules of §7.4 would need custom extension and is **not verified**. | **Threatens.** Timestamps per record only; no four conditions. | **Supports with cost.** Same cost as A for the predicate work; benefits from reusing `pgvector`, `btree_gist`, PG FTS, and standard libraries. |
| C-03 | Write-time gate with §7.5 reason codes | **Supports.** Our code throughout. | **Threatens.** No native gate at the proposal's fidelity. Would need a custom pre-commit layer that runs before Graphiti's extraction/resolution. Composability with Graphiti's own invalidation decisions is non-trivial. | **Threatens.** No native gate. | **Supports.** Gate lives in our `implementation/engine/gate/`; frameworks only enter as baselines. |
| C-04 | Derivation DAG + §10.3 deletion closure | **Supports.** Boundary stays inside one PostgreSQL store, so the §10.3 channel list is directly implementable. | **Threatens.** Adds a graph DB to the boundary. `remove_episode` propagation to sagas/communities/embeddings/lexical entries in one transaction is **Not verified**. Even if verified, the invariant 10 precondition and the H4 positive control still need custom work. | **Threatens.** No lineage table in the OSS data model. | **Supports.** Same as A, with the option to reuse an off-the-shelf transitive-closure utility (e.g. the PG `WITH RECURSIVE` pattern). |
| C-05 | Invariant 10 `(S) ∨ (C)` and T1 – T5 | **Supports.** Transactional procedure we author. | **Threatens.** No analogue; would need to be built as a custom extension and the semantics mapped onto edges rather than versions. | **Threatens.** No analogue. | **Supports.** T1 – T5 harness unchanged from `TECHNICAL_SPECIFICATION.md` §4.1. |
| C-06 | Per-user isolation + RLS | **Supports.** RLS native on PostgreSQL (`postgres_rls.txt`). | **Partial.** `group_id` scoping is application-layer only; graph DB does not provide RLS. Per-user partition is the mitigation Graphiti offers for scale, not for policy enforcement. | **Partial.** `user_id`/`agent_id`/`run_id` scoping is application-layer. | **Supports.** Same as A. |
| C-07 | Hybrid retrieval under temporal predicate | **Supports.** Build with `pgvector` + PG FTS + RRF k = 60. The reduced-schema SQL did not run this, but each component is a mature library used in isolation. | **Partial.** Graphiti's hybrid is semantic + BM25 + graph; signal set differs from the proposal's. Equivalence to P requires either changing P's signal set or disabling the graph signal (changes the artefact under test). | **Partial.** Mem0's hybrid is semantic + BM25 + entity. Same point. | **Supports.** Build the retrieval layer ourselves; reuse `pgvector` and PG FTS as libraries, not as framework behaviour. |
| C-08 | Async paths + PG job queue | **Supports.** Standard pattern (`research/sources/pages/postgres_select.txt`). | **Partial / Supports with cost.** Graphiti has its own ingestion loop with `SEMAPHORE_LIMIT`; integrating with a PG-queue backbone is custom. | **Partial.** Mem0 asynchronous surface is available; different semantics. | **Supports.** Same as A. |
| C-09 | Reproducibility and baseline parity | **Supports.** All pins owned by the project. | **Supports with cost.** Pinned commit `d50741d`; graph DB version must also be pinned. Baseline parity with B3/B4 is automatic (we are not the baseline). | **Supports with cost.** Pinned commit `0724291`; parity with headline platform scores is explicitly not expected. | **Supports.** Combines A's clarity on P with the pinning on B3/B4. |
| C-10 | Statistical analysis integrity | **Supports.** `validation/cp_bounds.py` is already byte-reproducible. | **Supports** (stats are in `evaluation/stats/`, independent of the engine). | **Supports.** | **Supports.** |
| C-11 | Operational complexity | **Supports with cost.** PG + pgvector + btree_gist + an LLM provider. One service family. | **Threatens.** Add graph DB (Neo4j/FalkorDB/Neptune) + OpenAI-compatible LLM; LLM calls per episode in Graphiti's ingestion. | **Partial / Supports.** Lightest extra dependency (vector store + LLM). | **Supports with cost.** Same as A for P; adds graph DB only when B4 runs. |
| C-12 | Licence | **Supports.** We choose at release (`PROJECT_ROADMAP.md` J-row 13). | **Supports.** Apache-2.0; attribution required. Compatible. | **Supports.** Apache-2.0; attribution required. Compatible. | **Supports.** |
| C-13 | Remaining implementation workload | **Supports with cost.** Highest line count; but every line maps to a `TRACEABILITY_MATRIX.md` cell. | **Partial.** Lower greenfield code; higher "glue + override" code; higher integration-debug cost. **Not verified** that the net effort is lower once C-01 – C-05 gaps are added as custom extensions. | **Partial.** Low greenfield code, but H1 is not feasible, so the effort saved does not count. | **Supports with cost.** Same uncovered mechanisms as A; reuses mature libraries so the greenfield work is only inside well-defined boundaries. |
| C-14 | Fit with frozen specification | **Supports.** Zero spec drift. | **Threatens.** Likely requires a V5 patch to the storage boundary (§7.7), to the retrieval predicates (§7.6 under the framework's query surface), and to invariant 10. Any such change requires the fact-check audit per `TECHNICAL_SPECIFICATION.md` §13 and is a Frozen-row touch — explicit supervisor approval required. | **Threatens.** Would require re-opening Frozen rows D-02 (two-stratum H1) and D-04 (scenario-level C-P). | **Supports.** No Frozen row touched. |
| C-15 | Six-way parallel team workload | **Supports with cost.** Work partitions neatly by phase (DDL, procedures, gate, retrieval, async, deletion, tests). Matches `ASSIGNMENTS.md` §5. | **Threatens.** Framework integration tends to centralise work on whoever owns the integration, reducing parallelism. | **Threatens** (and C-01 is already Threatens). | **Supports.** Partitioning is identical to A. |

---

## 3. Patterns and reading

- **No candidate has only "Supports" cells.** Every option carries explicit costs.
- **A. From scratch** carries cost on C-11 (operational complexity, which is in fact the proposal's own baseline infrastructure) and C-13 (implementation workload). It does not threaten any criterion.
- **B. Extend-Graphiti** threatens C-01 (ablations), C-03 (gate), C-04 (deletion closure), C-05 (invariant 10), C-11 (infra), C-13 is only *Partial* ("not verified that net effort is lower"), C-14 (spec drift), and C-15 (team parallelism). The threats are concentrated on the research-critical criteria.
- **B. Extend-Mem0** threatens C-01, C-03, C-04, C-05, and (implicitly) C-02. It is not a candidate for extending the engine; it is a candidate for the `B3` baseline role only, which it already fills in `Graduation_Project_Proposal.md` §10.1.
- **C. Hybrid** does not threaten any criterion. It inherits A's cost on C-11 and C-13 but reduces them by reusing mature libraries where their behaviour is well understood (pgvector, PG FTS, btree_gist, RRF, LLM adapter, Pydantic). It also matches `TECHNICAL_SPECIFICATION.md` §2 Decision rows D-12 (RRF k=60 Frozen) and D-13 (PG FTS Frozen) directly, which the Extend-framework options do not.

---

## 4. Assumptions and uncertainties

Every rating above rests on one or more of the following. These must be revisited if any assumption proves wrong.

| ID | Assumption / Uncertainty | How to resolve |
|---|---|---|
| U-01 | **The §7.7 storage boundary is fixed to a single transactional PostgreSQL store.** If the team chooses to split the boundary, the C-04 rating for from-scratch becomes "Partial" and the Extend-Graphiti rating does not improve, because the invariant 10 and derivation-DAG gaps remain. | Confirm D-15 (deletion storage boundary Frozen) stands. |
| U-02 | **Graphiti's `remove_episode` propagation to sagas, communities, embeddings, and lexical entries is sufficient for H4's seven in-boundary channels.** Not verified in the source we inspected. | Small prototype on a disposable graph DB that constructs a canary scenario and runs the §10.3 channel sweep. |
| U-03 | **Graphiti's edge-filter API can be composed into the proposal's three predicates without re-implementing the storage model.** Not verified. | Prototype that implements `historical_as_of(user_id, t, β)` as a thin layer over Graphiti search; measure whether it matches `TECHNICAL_SPECIFICATION.md` §3.5. |
| U-04 | **The exclusion constraint on single-valued predicates holds under concurrent writers on the full schema.** Open — D-18 pilot. | Pilot in P4 / P9 (`RL-091`). |
| U-05 | **Mem0 OSS parity on LongMemEval / LoCoMo is directionally similar to the headline platform numbers.** Not verified at the pinned commit. | B3 pilot in P3. |
| U-06 | **Supervisor accepts the §6.2/§6.3 framing that bi-temporal versioning is reused rather than invented.** D-09 Open. | Supervisor sign-off memo (`RL-010`). |
| U-07 | **Graphiti's LLM-call ingestion cost fits the pilot budget** (D-21 condition for B4 inclusion). | B4 pilot (`RL-032`, `RL-011`). |
| U-08 | **The team can staff six-way parallel work.** `ASSIGNMENTS.md` is explicitly provisional. | `RL-001` approval; replace Member 1..6 placeholders. |
| U-09 | **Retention scoring and persona layer remain optional** (D-03, D-08 Frozen). | Already Frozen. Cell C-01 ratings assume this; a change here would re-open H1. |
| U-10 | **PostgreSQL version decision (D-25) is still open.** Reduced-schema SQL ran on 16.15; invariant-4 feasibility under concurrency is tied to this. | `RL-012`. |

---

## 5. Recommendation (reading from the matrix)

The matrix provides the alternatives/rationale for the owner direction in FEASIBILITY_REVIEW_DECISIONS.md; PLAN/ARCHITECTURE.md is the canonical engineering design.

1. **"Extend an existing framework" is not viable for P.** Extend-Graphiti threatens C-01, C-04, C-05, C-14, and C-15 — a set that includes every criterion directly tied to a confirmatory H1 – H4 result. Extend-Mem0 is harder still (threatens H1 outright). Neither should become the engine behind `P`.
2. **"From scratch" is viable** and keeps every research-critical mechanism under the project's control. It does carry the largest raw implementation cost, which the proposal already anticipates in §15 and in `PROJECT_ROADMAP.md` §C.
3. **"Hybrid"** is the same architecture as from-scratch at the *mechanism* level and the same as "extend-framework" at the *baseline* level: it builds the engine itself (schema, procedures, gate, retrieval, deletion) in-house, while reusing mature libraries where the behaviour is well-defined (pgvector, PG FTS, btree_gist, RRF, LLM adapter, structured logging via structlog per `AGENTS.md`) and keeping Graphiti and Mem0 strictly in the `evaluation/baselines/` role already specified in `Graduation_Project_Proposal.md` §10.1.

The matrix therefore favours **C. Hybrid**, understood as: **own the research-critical mechanisms; reuse libraries, not framework-defined behaviour; keep Mem0 and Graphiti as baselines, not as substrates of P.** This is the architecture detailed in [PLAN/ARCHITECTURE.md](../PLAN/ARCHITECTURE.md).

The matrix does **not** justify a weaker reading in which Graphiti is adopted as the backing store for P with "a few extensions". That is Extend-Graphiti, and it threatens C-01, C-04, C-05, and C-14.
