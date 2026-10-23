# TECHNICAL_FEASIBILITY_REVIEW.md

**Status.** Advisory evidence from the earlier review, not a current progress board. Owner-selected Hybrid/S-07 disposition: [review decisions](FEASIBILITY_REVIEW_DECISIONS.md). No V4-frozen decision is altered here. All findings cite files in this repository or official framework sources already saved under `../research/sources/pages/`.

**Scope.** Determine which of three architectures best supports the project's research contribution, reproducibility, feasibility, and remaining implementation workload: (1) build the memory engine largely from scratch, (2) extend an existing memory framework (primarily **Graphiti** or **Mem0**), or (3) a **hybrid** architecture that reuses selected components while implementing the research-specific mechanisms in-house.

**How to read.** §1 pins the current state of the Lab. §2 maps what Graphiti and Mem0 provide, with explicit "native / configurable / custom extension / absent-or-unverified" labels. §3 maps H1 – H4 to the mechanisms and measurements they require, so that framework reuse is argued at the hypothesis level rather than at the "feature list" level. §4 collects research-level risks that any choice must manage. §5 lists unresolved questions. The architecture comparison itself is in [`ARCHITECTURE_DECISION_MATRIX.md`](ARCHITECTURE_DECISION_MATRIX.md) and engineering design is canonical in [PLAN/ARCHITECTURE.md](../PLAN/ARCHITECTURE.md).

---

## 1. Current state (verified against the repository)

The point of this section is to prevent the review from conflating planned mechanisms with implemented ones.

### 1.1 Implemented and verified

The following items have artefacts in the repository that have been executed and whose outputs are preserved. Evidence paths are relative to `research-lab/`.

| Item | Evidence | Scope of what was executed |
|---|---|---|
| Mechanical consistency of the authoritative documents (proposal, checklist, changelog). | `validation/mechanical_checks.output.txt` ends with `SUMMARY: ALL HARD CHECKS PASSED`. | Citations, cross-references, anchors, tables, ablations, stale-wording guard, H1/H4 wording, persona, excluded terms, fabricated-results guard, reference-list byte-identity. |
| Bi-temporal predicate design on a **reduced** schema. | `validation/bitemporal_checks.sql` + `validation/bitemporal_checks.output.txt`; 43 PASS lines, final line `ALL ASSERTIONS PASSED`; MD5 `66a4dae36f20f17d19f3b75d612e61fb`. | Rules R1, R3, R4, R5 status-only branch, R6, same-day branch of R2, R8 (`t > ρ` check); exclusion constraint on single-valued predicates in both ranges; worked example of §7.4. PostgreSQL 16.15 (`postgres:16-alpine`). |
| Clopper – Pearson arithmetic for the H4 zero-leak bound. | `validation/cp_bounds.py` + `validation/cp_bounds.output.txt`; byte-identically reproducible. | Zero-leak upper bounds and the scenario-vs-event clustering illustration cited in `Graduation_Project_Proposal.md` §10.6. |
| Source corpus for refs [20] – [29] with depth labels. | `research/sources/fulltext/MANIFEST.txt`; `Source_Evidence_Ledger.md` §E. | Full-text PDFs plus `pdftotext -layout` extracts; the depth to which each paper was actually read is recorded per row. |
| Research-Lab scaffold and planning package. | `PLAN/ARCHITECTURE.md`, `PLAN/TASK_GRAPH.md`, `PLAN/ASSIGNMENTS.md`, `PLAN/DECISIONS_REGISTER.md`, `PLAN/DEPENDENCY_MAP.md`, `PLAN/INDEPENDENCE_BOUNDARY.md`, `PLAN/SCHEDULE.md`, `PLAN/CONTRIBUTING.md`, `PLAN/GITHUB_SETUP.md`, `PLAN/PROJECT_STATUS.md`, `PLAN/issue_drafts/`. | Fourteen phase-level deliverables (`D-P0`..`D-P13`) and 51 work packages (`RL-001`..`RL-131` + `RL-130-REPRO`). This review's status snapshot predates the owner-confirmed RL-001 approval now recorded in `PLAN/PROJECT_STATUS.md` §6. No GitHub issues are published. |

### 1.2 Planned but unimplemented

Everything else that the proposal describes is **specification only**. The reduced-schema SQL check deliberately does **not** cover:

- The full application schema of `Graduation_Project_Proposal.md` §8 (so no `MEMORY_DERIVATION`, no `DELETION_LOG`, no `JOB`, no `AUDIT_EVENT`, no derived-artifact lineage, no vector or lexical indices in the test).
- pgvector-backed dense retrieval and the hybrid dense + lexical + RRF retrieval of §7.6.
- Row-level security (invariant 7); the service-boundary user-scoping check; the per-user partition strategy.
- The PostgreSQL job queue with `FOR UPDATE SKIP LOCKED`, idempotency keys, and the per-user advisory lock (invariant 9).
- Concurrent writers; replay-clock behaviour under the harness (invariant 3 under replay).
- The validation gate (§7.5), the assertion-mode classifier, the conflict-relation classifier, and the Candidate/Conflict-Pending/Rejected lifecycle.
- Minimal consolidation and the derivation DAG required by H4.
- The end-to-end deletion procedure of §7.7 and its channel-level verification (invariant 8).
- The archival-precondition harness **T1 – T5** for invariant 10 (currently blocked on open decision 17).
- All adapters and baselines (`B0`..`B4`), the LLM-judge harness, the pilot, and every H1 – H4 experiment.

See also `PROJECT_ROADMAP.md` §B.3 and §B.4, which say the same thing in the roadmap's own vocabulary.

### 1.3 Design checks vs end-to-end tests

The 43-PASS SQL log is a **design-level** check against a reduced schema. In particular:

- It uses an emulated clock, not the real injected `now()` clock the engine is supposed to use in production and the replay clock used in evaluation (§7.4, "Clocks in evaluation replay").
- It has no concurrent writers, no pgvector, no row-level security, no job queue, no `btree_gist` interactions with pgvector tables, and no derivation DAG. Any claim about concurrent-writer behaviour of the `(V, R)` exclusion constraint under contention remains an open pilot item (`Methodology_and_Consistency_Checklist.md` §6 item 6 ≡ `PROJECT_ROADMAP.md` §E item 18 ≡ `DECISIONS_REGISTER.md` D-18).
- It does not exercise the archival precondition `(S) ∨ (C)` (invariant 10). The five regression tests **T1 – T5** are listed in `TECHNICAL_SPECIFICATION.md` §4.1 but no code to execute them exists; `TRACEABILITY_MATRIX.md` §2 marks the row **BLOCKED**.

### 1.4 Hypotheses vs demonstrated results

**No experiment described in the proposal has been run.** This review will not treat H1 – H4 as having any observed outcome. The hypothesis statements are frozen in `Graduation_Project_Proposal.md` §4 and §4.1 and the decision rules are in §4.1 and §10.6. The main-app `docs/memory.md` describes a Mem0-backed in-flight demo, but running that demo is explicitly **not** evidence for H1 – H4 (`PLAN/INDEPENDENCE_BOUNDARY.md` §3 rule 8).

### 1.5 Why this matters for the choice of architecture

Because essentially every mechanism the research measures is still unimplemented, the choice of architecture is a greenfield decision in the sense of "what should we build next". It is **not** a port of an existing engine. That matters because the trade-off between "integration cost of a framework" and "re-implementation cost from scratch" is not weighted by sunk code — the only sunk investments are the 43-PASS SQL design check, the Clopper – Pearson arithmetic, the planning package, and the source corpus. These are all portable across architectures.

---

## 2. Candidate framework evaluation

Each candidate is evaluated against the ten axes requested in the task. Capabilities are labelled:

- **Native** — supported by the framework out of the box, documented, with code that implements it.
- **Configurable** — present but requires non-trivial configuration or a public extension point.
- **Custom extension** — would require writing code inside or alongside the framework.
- **Absent or not verified** — not found during this review; requires either a code-level inspection or a prototype to confirm.

Where a capability is labelled **not verified**, it is **not** the same as "absent". The task constraints forbid installing the framework, so a prototype is explicitly out of scope here.

### 2.1 Graphiti (`getzep/graphiti`, Apache-2.0)

**Pinned commit for this review.** `d50741d1bf463cbe3859eb73914cbff1b66839e6` (15 Oct 2026), as recorded in `research/sources/pages/graphiti_commit.txt` and `Source_Evidence_Ledger.md` §B. All claims below are tied to this commit; a later version may differ.

**Primary sources inspected.**

- `research/sources/pages/graphiti_readme.txt` (README of the pinned repo).
- `research/sources/pages/graphiti_code.txt` (relevant portions of `graphiti_core/graphiti.py`).
- `research/sources/pages/zep_full.txt` — paper "Zep: A Temporal Knowledge Graph Architecture for Agent Memory" (ref [5]).
- `research/sources/pages/graphiti_license.txt` — Apache-2.0 license file.

| Axis | Finding | Label |
|---|---|---|
| Data model and memory lifecycle | Nodes = entities (people, products, concepts). Edges = facts/relationships as `(subject, relation, object)` triplets. Episodes = raw ingested data; "every derived fact traces back here" (README "What is a Context Graph?"). Facts have **validity windows** on edges (`valid_at`, `invalid_at` are visible in `graphiti_code.txt`, e.g. lines 1457, 1689, 2141). | **Native** |
| Temporal representation and historical queries | Bi-temporal, per the paper abstract and README ("Explicit bi-temporal tracking with automatic fact invalidation"). Edges carry a validity window; `invalid_at` is set by LLM-driven fact invalidation (`graphiti_code.txt` references `invalidated_edges` throughout, e.g. lines 934, 1239, 2156). Episodes carry `valid_at` (reference time) and `created_at` (ingestion time); `last_summarized_at` and `last_summarized_episode_valid_at` are explicitly separated (`graphiti_code.txt` §`summarize_saga`, lines 600–606). | **Native** (edges-level); historical-as-of as a formal predicate on edges is **configurable** via search filters. |
| Hybrid retrieval and temporal filtering | README: "Hybrid Retrieval: Combines semantic embeddings, keyword (**BM25**), and graph traversal". Search recipes (`EDGE_HYBRID_SEARCH_RRF`, `COMBINED_HYBRID_SEARCH_CROSS_ENCODER`, `EDGE_HYBRID_SEARCH_NODE_DISTANCE`) are imported in `graphiti_code.txt` lines 73–77. `SearchFilters` exist (same imports). | **Native** (hybrid search); temporal **filter** on edges is **native** by edge validity; mapping to the proposal's three predicates (current-state, historical-as-of point/window, audit-as-of) is **custom extension**. |
| Write-time validation and policy enforcement | The pipeline does LLM-driven *extraction* and *resolution* (`extract_edges`, `resolve_extracted_edges` in `graphiti_code.txt` lines 103–108) and detects contradictions by invalidating prior edges. There is no documented *gate* with the proposal's assertion-mode / instruction-screen / evidence-support / confidence-calibration semantics (`Graduation_Project_Proposal.md` §7.5). The only validation gates visible are schema/entity-type validators (`validate_group_id`, `validate_entity_types`, structured-output enforcement). | **Absent (as the proposal defines it)** — would need a **custom extension** to add a true pre-commit gate with reason codes. |
| Provenance, lineage, and deletion of derived artifacts | Episodes are the provenance anchor. `remove_episode(episode_uuid)` (`graphiti_code.txt` lines 2164–2192) deletes the episode, deletes edges created by that episode, and deletes nodes "only mentioned in the deleted episode". **Communities** and **sagas** (summaries) are present; the code shows community lifecycle (`build_communities`, `remove_communities`, `update_community`) and saga-summary watermarks, but the review did **not** verify that `remove_episode` propagates to community rebuild, saga-summary re-derivation, embedding deletion, and lexical-index entries in a single transaction. | **Partial — not verified at the depth H4 needs.** The channel-level deletion closure of §7.7 is **custom extension** on top of what the framework provides. |
| User isolation and security boundaries | Graphiti uses `group_id` as a tenant/namespace key (`graphiti_code.txt` lines 488, 518, 920, 988, 1253). The scoping is enforced at the query layer by passing `group_ids` through `handle_multiple_group_ids` (line 29, 1248). There is **no RLS**: the graph database is Neo4j, FalkorDB, Amazon Neptune, or (deprecated) Kuzu — not PostgreSQL — so the proposal's RLS (invariant 7) does not transfer. The caller must correctly scope every query. The README's "Zep vs Graphiti" table lists "User & conversation management: Built-in (Zep) / Build your own (Graphiti)." | **Configurable** (group_id); **absent** at the DB level (no RLS equivalent). |
| Extensibility and integration cost | Pluggable graph backends; pluggable LLM clients (OpenAI, Azure, Anthropic, Gemini, Groq, OpenAI-compatible via `OpenAIGenericClient`); pluggable embedders; pluggable cross-encoders; configurable prompts (`create_prompt_library`); per-prompt model routing via `LLMRuntime`. **Requires a graph database service.** README quickstart: Neo4j via Docker, or FalkorDB `docker run -p 6379:6379 -p 3000:3000 ... falkordb/falkordb:latest`. Kuzu is **deprecated** in this pinned version. | **Native** extensibility; **high** infrastructure cost compared with a PostgreSQL-only stack. |
| Testing, reproducibility, and baseline suitability | README badges show lint, unit test, and typecheck CI. Telemetry is opt-out (`GRAPHITI_TELEMETRY_ENABLED=false`). Reproducibility of the pinned commit for the **baseline role** (`B4` in `Graduation_Project_Proposal.md` §10.1) is viable, subject to the pilot test (`PROJECT_ROADMAP.md` §E item 21 / D-21 in `DECISIONS_REGISTER.md`). | **Suitable as a baseline, conditional.** |
| Infrastructure, operational complexity, and dependencies | Neo4j 5.26 **or** FalkorDB 1.1.2 **or** Amazon Neptune + Amazon OpenSearch Serverless **or** Kuzu 0.11.2 (deprecated). OpenAI (or compatible) LLM and embeddings. Semaphore-limited LLM concurrency (`SEMAPHORE_LIMIT`, default 10) because ingestion makes LLM calls per episode. | **High** — adds a graph DB and LLM calls per episode on top of the PostgreSQL store the proposal already requires. |
| License and attribution | Apache-2.0 (`graphiti_license.txt`). Compatible with the project's current and expected release licence. Attribution per Apache-2.0 §4. | **Compatible.** |

**What Graphiti clearly gives us.** A production-level bi-temporal graph of facts with automatic LLM-driven invalidation, episode-grounded provenance, native hybrid retrieval, and a well-documented API.

**What Graphiti does not natively give us.**

- The write-time **validation gate** of §7.5 with the specific reason codes, assertion-mode classifier, and gate decision log the H3 experiment is defined on.
- The **three distinct retrieval predicates** of §7.4 (current-state, historical-as-of in point and window form, audit-as-of) as named, enforced query functions. Edge validity windows can be filtered on, but the proposal measures correctness on each predicate separately (`TAA` per stratum; §10.3 "Audit-as-of correctness").
- A **PostgreSQL-native** storage boundary for the §7.7 deletion closure. The proposal's deletion boundary is defined relative to one transactional store; Graphiti puts the belief graph in Neo4j/FalkorDB/Neptune, which **expands** the boundary and the closure surface rather than contracting it.
- **Lineage-tracked deletion** for derived artifacts (sagas/communities) at the fidelity the proposal and H4 need. The code evidence inspected shows `remove_episode` propagates to edges and orphan nodes; propagation to sagas/communities and to their embeddings and lexical entries in one transaction is **not verified** here.
- A **row-level** or policy-enforced per-user isolation (invariant 7).

### 2.2 Mem0 (`mem0ai/mem0`, Apache-2.0)

**Pinned commit for this review.** `0724291bd864cf807e88ee8981c8226644e8fb42` (14 Oct 2026), as recorded in `research/sources/pages/mem0_commit.txt` and `Source_Evidence_Ledger.md` §B. All claims below are tied to this commit.

**Primary sources inspected.**

- `research/sources/pages/mem0_readme.txt` (pinned README).
- `research/sources/pages/mem0_code.txt` (relevant portions of `mem0/memory/main.py`).
- `research/sources/pages/mem0_full.txt` — the Chhikara et al. 2025 Mem0 paper (ref [6]).
- `research/sources/pages/mem0_license.txt` — Apache-2.0.

| Axis | Finding | Label |
|---|---|---|
| Data model and memory lifecycle | A flat memory record per fact with content, embedding, metadata, and a timestamp. Entities are extracted and linked across memories (README "New Memory Algorithm (April 2026)", bullet 3). History is kept in a local `SQLiteManager(self.config.history_db_path)` (`mem0_code.txt` line 500). | **Native** (flat); **not** bi-temporal in the §7.4 sense. |
| Temporal representation and historical queries | The 2025 paper describes `ADD` / `UPDATE` / `DELETE` operations (`mem0_full.txt` lines 338 – 341, 466, 1919 – 2030). The **currently pinned** README (14 Oct 2026) says: "Single-pass ADD-only extraction — one LLM call, no UPDATE/DELETE. Memories accumulate; nothing is overwritten." It also lists a new "Temporal Reasoning" ranking signal ("time-aware retrieval that ranks the right dated instance for queries about current state, past events, and upcoming plans"). The two descriptions diverge, and the proposal already pins the relevant Mem0 claim to the current version (`Source_Evidence_Ledger.md` §C "Mem0's current open-source algorithm is ADD-only"). | **Partial**: a timestamp per memory is **native**; validity intervals and the four §7.4-derived conditions are **absent**. |
| Hybrid retrieval and temporal filtering | README: "Multi-signal retrieval — semantic, BM25 keyword, and entity matching scored in parallel and fused." Scoring utilities are present in code (`mem0.utils.scoring`, imports in `mem0_code.txt` lines 68 – 73). Temporal filtering is a *ranking* signal, not a hard temporal predicate. | **Native** multi-signal fusion; **absent** as a hard temporal predicate matching the proposal's three predicates. |
| Write-time validation and policy enforcement | The current ADD-only algorithm removes the previous UPDATE/DELETE classifier. There is no documented gate with the proposal's reason codes. The code performs identity-key validation (`_strip_identity_keys`, `_reject_top_level_entity_params`) and tenant scoping, not content-level validation. | **Absent (as the proposal defines it)**; **custom extension** would be needed for a §7.5 gate. |
| Provenance, lineage, and deletion of derived artifacts | A memory record stores the content and metadata; derived artifacts (summaries, derived semantic memories, persona traits) and a `MEMORY_DERIVATION` table are not part of the OSS data model. `delete`, `delete_all`, and `update` exist on the base `MemoryBase` interface and operate per record. The 2025 paper (`mem0_full.txt` line 466) says the UPDATE/DELETE classifier "marks relationships obsolete rather than physically removing them", which contradicts the pinned README's ADD-only description — this is a known tension the proposal flags. | **Absent** at the level H4 needs. |
| User isolation and security boundaries | Isolation is per `user_id` / `agent_id` / `run_id` passed through `filters={"user_id": ...}` (`mem0_code.txt` lines 171, 376 – 395). Identity keys cannot be set via metadata (`_IDENTITY_KEYS`, lines 139 – 155). No RLS; the application must correctly scope every call. `docs/memory.md` in this repo already uses the `user_id` scoping. | **Configurable**; **absent** at the DB level. |
| Extensibility and integration cost | Pluggable LLM / embedder / vector-store / reranker factories (`LlmFactory`, `EmbedderFactory`, `VectorStoreFactory`, `RerankerFactory` in `mem0_code.txt` lines 60 – 66). Many vector-store integrations. Hybrid-search features require `pip install mem0ai[nlp]` and a spaCy model (`mem0_readme.txt` "install with NLP support"). | **Native** extensibility; **low** infrastructure cost (works on PostgreSQL + pgvector). |
| Testing, reproducibility, and baseline suitability | Reported LoCoMo 92.5, LongMemEval 94.4 (README). Scores "reflect Mem0's managed platform, which includes proprietary optimisations not available in the open-source SDK; open-source users should expect directionally similar gains but not identical numbers." So the open-source scores at the pinned commit may differ from the headline numbers. | **Suitable as a baseline** (`B3` in `Graduation_Project_Proposal.md` §10.1), with the caveat that platform ≠ OSS. |
| Infrastructure, operational complexity, and dependencies | Python + LLM provider + vector store (Qdrant default; many alternatives). Lightest of the three options. | **Low.** |
| License and attribution | Apache-2.0 (`mem0_license.txt`). | **Compatible.** |

**What Mem0 clearly gives us.** A production-ready flat memory store with hybrid retrieval, mature per-tenant scoping, and a well-defined baseline role (`B3`).

**What Mem0 does not natively give us.** Essentially every mechanism that the three research hypotheses measure beyond "retrieve a relevant memory": no bi-temporal versioning in the §7.4 sense, no three distinct temporal predicates, no write-time gate with the §7.5 reason codes, no `MEMORY_DERIVATION` lineage, no deletion closure over derived artifacts. The ADD-only shift in the pinned repo actively moves Mem0 **away from** the semantics H1 depends on, because nothing is overwritten and nothing is explicitly invalidated.

### 2.3 Side-by-side summary

| Capability needed by the proposal | From-scratch (us) | Graphiti native | Mem0 native | Notes |
|---|---|---|---|---|
| Bi-temporal `(V, R)` with four derived conditions (§7.4) | To build | **Partial** (edge-level validity + ingestion time) | **Absent** | Graphiti's semantics are close but edge-centric, not row-centric, and are not the proposal's named predicates. |
| Three retrieval predicates (current, historical-as-of point/window, audit-as-of) | To build | **Custom extension** on top of filters | **Custom extension** | Audit-as-of in particular is specific to the proposal's recorded-time model. |
| Hybrid dense + lexical + RRF under temporal predicate (§7.6) | To build (`pgvector` + PG FTS + RRF, k = 60 per [12]) | **Native** (semantic + BM25 + graph) | **Native** (semantic + BM25 + entity) | Framework hybrid signals differ from the proposal's; equivalence is not automatic. |
| Write-time validation gate (§7.5) with reason codes | To build | **Absent** | **Absent** | Both frameworks delegate "is this writable?" to extraction/schema. |
| Minimal consolidation with `MEMORY_DERIVATION` DAG | To build | **Partial** (sagas, communities) | **Absent** | Graphiti has derived artifacts; whether their lineage traversal matches §7.7 is **not verified**. |
| Lineage-tracked deletion over channel-level in-boundary closure (§7.7) | To build | **Partial** (`remove_episode` propagates to edges, orphan nodes) | **Absent at this fidelity** | Neither has been verified against the seven in-boundary channels of §10.3 "Deletion channels". |
| Per-user isolation with RLS as a second layer (invariant 7) | **Native** on PostgreSQL per `postgres_rls.txt` | **Absent** at DB level (`group_id` is app-level) | **Absent** at DB level (`user_id` is app-level) | Depends on PostgreSQL. Graphiti and Mem0 can be scoped at the application layer but do not use RLS. |
| PostgreSQL + pgvector + `btree_gist` single-store boundary | **Native** | **Not applicable** (graph DB) | **Supported** (pgvector among many stores) | The §7.7 boundary is defined relative to one transactional store; Graphiti splits that store. |
| Idempotent async job queue (`FOR UPDATE SKIP LOCKED`, per-user advisory lock) | **To build** (standard PG pattern per `postgres_select.txt`) | **N/A** at this level | **N/A** at this level | Both frameworks run their own worker loops. |
| Suitable as a declared baseline | — | **Yes (B4)**, conditional on pilot cost | **Yes (B3)**, pinned | Both are already listed in §10.1 of the proposal. |

**Headline reading.** Graphiti is the closest existing system to the proposal's design and is the right **B4** baseline. Mem0 is the right **B3** baseline. Neither framework provides, out of the box, the **named mechanisms** on which H1, H3, and H4 are decided. Section 3 below is the detailed argument for that conclusion at the hypothesis level.

---

## 3. Protecting the research contribution (H1 – H4 mapping)

For each hypothesis, we list the minimum functionality the experiment requires, what can be reused without changing the semantics of the experimental treatment, what must stay under the project's control, the baseline that the proposal already names, the confounders a framework would introduce, and the evidence shape required by `Graduation_Project_Proposal.md` §4.1.

### 3.1 Novelty positioning (do first, so the rest reads correctly)

`Graduation_Project_Proposal.md` §6.2 is explicit:

> "Bi-temporal versioning … Established technique reused; engineering contribution … **No inspected full text reports that separation**, but the survey is limited and this is not a novelty claim."
>
> "Validation gate … Established idea; evaluation contribution. … Novelty of the algorithm is not claimed."
>
> "Lineage-based deletion … Engineering contribution; novelty unverified."

Together with §6.3 ("The smallest defensible research contribution is **the measured, ablation-isolated effect of three mechanisms** … in one open implementation, under a predeclared protocol that reports both benefits and costs"), the research contribution is **evaluation**, not invention. Graphiti already provides bi-temporal versioning and hybrid retrieval, and the project must not present those as its contributions.

This has a direct architectural consequence: reusing bi-temporal versioning from a framework is **not** a threat to novelty, because the project does not claim novelty on versioning. It is a threat only if the reuse makes the ablation impossible or the result unmeasurable — see §3.2 – §3.5 below.

### 3.2 H1 — retained bi-temporal history vs valid-time filtering (`P` vs `A2`)

- **Required functionality.** A system that stores bi-temporal rows with the four derived conditions of §7.4; that answers three distinct predicates (current-state, historical-as-of, audit-as-of) correctly; and that supports an ablation in which supersession is **replaced by overwrite** (`A2`, Section 10.4), including the deletion of superseded and valid-ended rows and the elimination of recorded-time history.
- **Reusable without invalidating the experiment.** Any dense-retrieval, lexical-retrieval, or RRF-fusion library can be reused provided it is identical in `P` and `A2`. The LLM extractor and prompts can be reused if they are frozen and identical across configurations. The scoring stack, judge, and statistics are reused as a matter of principle (§10.1 fair comparison).
- **Must remain under our control.** The **supersession transaction** (R1 close-then-insert) and the **ablation knob** that turns it off. If the framework's "update" is a semantics the project does not control, then the ablation cannot be constructed, and H1 cannot be run inside that framework in a form the proposal would accept.
- **Baseline.** `A2` is defined relative to `P`. External baselines (`B0`, `B1`, `B2`, `B2h`, `B3`, `B4` conditional) are reported descriptively and are not the H1 contrast.
- **Confounders a framework may introduce.**
  - Graphiti would supply bi-temporal edges with LLM-driven invalidation. If `P` is a Graphiti instance and `A2` is a Graphiti instance with "invalidation disabled", the ablation changes two things at once (invalidation logic *and* the way history is kept), confounding the contrast.
  - Mem0 is not a drop-in H1 treatment: ADD-only accumulation is not A2's specified overwrite semantics; the controlled contrast requires ChronaMind-owned versioning/overwrite behavior.
- **Evidence required by §4.1.** Two co-primary strata on suite T, test split, with the decision rule in `Graduation_Project_Proposal.md` §4.1 (history-dependent superiority with lower bound > 0; present-state non-inferiority at δ_pres).

**Conclusion for H1.** The supersession procedure, the three retrieval predicates, and the ablation knob must stay in the project's codebase. Hybrid-retrieval and extraction components can be reused if frozen and shared across P and A2.

### 3.3 H2 — hybrid retrieval vs single-channel ablations

- **Required functionality.** Dense retrieval, lexical retrieval, fusion (RRF with k = 60 per [12], per `TECHNICAL_SPECIFICATION.md` §2 Dec#12 (Frozen RRF setting)), under the temporal predicate.
- **Reusable without invalidating the experiment.** Everything here is reusable, because `A5` (dense-only) and `A6` (lexical-only) are **channel removals from P**. The only requirement is that the removal be clean (removing only the lexical channel in `A5`, only the dense channel in `A6`).
- **Must remain under our control.** The fusion function, the k = 60 setting (frozen), and the ability to turn off exactly one channel.
- **Baseline.** `P` vs `A5` and `P` vs `A6` on LongMemEval_S test split (`Graduation_Project_Proposal.md` §10.2).
- **Confounders.** If a framework fuses signals in a way the project cannot cleanly disable, the ablation becomes ill-defined. Mem0's "semantic + BM25 + entity" (README) and Graphiti's "semantic + BM25 + graph traversal" (README) include signals the proposal's P does not. If we were to inherit a framework's fusion we would have to either (a) match P's signal set to the framework's (changing P), or (b) decouple the channels inside the framework (custom extension). Both are feasible but the first changes the research artefact.
- **Evidence required by §4.1.** Intersection-union: both contrasts favour `P` and the larger Holm-adjusted p-value is below 0.05.

**Conclusion for H2.** The retrieval stack can be built on `pgvector` + PostgreSQL FTS + RRF per `TECHNICAL_SPECIFICATION.md` §2 D-12 and D-13 (both **Frozen**). Graphiti's or Mem0's hybrid search is **not** interchangeable with P's because the signal sets differ.

### 3.4 H3 — validation gate (ASR and LUAR, both co-primary)

- **Required functionality.** The validation gate with the §7.5 checks (evidence support, assertion mode, instruction screen, conflict relation, extractor confidence), a logged decision per candidate with a reason code, and the ability to turn the gate off (`A3` = P-nogate) and to turn off individual checks (`A4` sub-checks).
- **Reusable without invalidating the experiment.** Extraction can be reused if the extracted candidates are routed through our gate. The downstream commit and the retrieval can be reused.
- **Must remain under our control.** The entire gate (classifier, reason codes, decision log) and the ablation knobs.
- **Baseline.** `P` vs `A3`, with `A4` as a diagnostic.
- **Confounders.** Neither Graphiti nor Mem0 has a documented gate at the proposal's fidelity. Any gate added to Graphiti would run **before** Graphiti's own extraction/resolution, so what the gate blocks is not what the framework's internals subsequently build. This is a composability concern, not a blocker, but it must be designed carefully.
- **Evidence required by §4.1.** ASR reduction significant after Holm; LUAR non-inferior at δ (δ is Open, pilot item, D-11).

**Conclusion for H3.** The gate must be our own code. Framework reuse here is confined to extraction (optional) and to the downstream store.

### 3.5 H4 — lineage-tracked deletion (scenario-level C–P upper bound)

- **Required functionality.** `MEMORY_DERIVATION` DAG with transitive-closure traversal in a single transaction; §7.7 in-boundary channel sweep (seven channels in §10.3 "Deletion channels"); `A8` positive control that detects at least the predeclared share; `A2`-style supersession must also be removable so canaries can be tested against superseded facts.
- **Reusable without invalidating the experiment.** The suite D harness and the scoring are reused by definition. The LLM extractor can be reused.
- **Must remain under our control.** The derivation DAG, the deletion procedure, the storage boundary. These **define** the H4 result; outsourcing them to a framework would move the measurement target.
- **Baseline.** `P` vs `A8` (positive control) + exact Clopper–Pearson at scenario level; external systems reported descriptively.
- **Confounders.**
  - Graphiti's `remove_episode` is close to the right primitive, but H4 is defined on scenario-level channel sweeps, including vector-index entries, lexical-index text, queue payloads, and cache entries. Whether Graphiti's deletion propagates into **all** these channels within one transaction is **not verified** here and the framework's underlying graph DB (Neo4j/FalkorDB/Neptune) adds channels the proposal's PostgreSQL boundary does not include.
  - If the storage boundary becomes "PostgreSQL for most of P, Neo4j for the belief graph", the §7.7 boundary definition must be rewritten and the channel enumeration in §10.3 must be re-done. This is a frozen specification change (§13 "Change control" in `TECHNICAL_SPECIFICATION.md`) and would require re-running the fact-check audit.
- **Evidence required by §4.1.** Three-conjunct Supported rule: zero leaks ∧ scenario-level upper bound ≤ u\* ∧ positive-control detection ≥ share. u\* and share are Open (D-12, D-13).

**Conclusion for H4.** Deletion must be our own code against our own storage boundary. Any framework reuse here would (a) widen the boundary we must sweep, (b) force a re-specification of invariant 10 and §7.7, and (c) jeopardise the single-transaction semantics that `MEMORY_DERIVATION` closure assumes.

### 3.6 Which mechanisms are novel enough to protect

- **Bi-temporal versioning** — **not** claimed as novel (§6.2). Reuse would not harm the novelty claim, but see §3.2: the ablation knob must stay ours.
- **Hybrid retrieval under temporal predicate** — **not** claimed as novel. See §3.3.
- **Validation gate** — algorithm **not** claimed as novel; **evaluation** is the contribution. Must stay ours (§3.4).
- **Lineage-tracked deletion with channel-level leakage measurement** — "engineering contribution; novelty unverified" per §6.2. Must stay ours (§3.5).
- **Scenario-level Clopper–Pearson at the frozen informativeness limit, with a positive control, applied to deletion** — contribution is the **predeclared evaluation protocol**, not the statistic. Must stay in `evaluation/` and in `validation/cp_bounds.py`, not in any framework.

### 3.7 Where novelty cannot be presumed and must be earned

Three features need **explicit** justification against existing systems:

1. **Audit-as-of as a separately verified predicate** (`Graduation_Project_Proposal.md` §10.3 "Audit-as-of correctness"). Graphiti's `last_summarized_at` and `created_at` give ingestion-time ordering on episodes; whether that constitutes an audit-as-of predicate at the fidelity of §7.4 (asking "what did the system believe at β about valid time t?") is **not verified**. A prototype on top of Graphiti would be needed to tell.
2. **Archival precondition `(S) ∨ (C)`** (invariant 10, V4 fix). The V3 "valid-ended" branch bug is specific to the proposal's model; no framework has an equivalent invariant.
3. **Deletion closure measured by seven in-boundary channels including the assembled context and the final answer** (§10.3 "Deletion channels"). Framework deletions address storage; §10.3 extends to retrieval and answering. This is a measurement contribution and belongs to `evaluation/`, not to a framework.

---

## 4. Research-level risks introduced by framework choice

| Risk | From-scratch | Graphiti | Mem0 | Mitigation |
|---|---|---|---|---|
| **R-A Novelty erosion.** Framework reuse is argued *as* the research contribution. | Low | Medium (edge validity windows and automatic invalidation are already Graphiti features). | Low (Mem0 does not have bi-temporal semantics). | Keep the §6.3 "smallest defensible contribution" sentence as the test. Novelty is in the *evaluation protocol*, not in the mechanisms. |
| **R-B Experimental confounding.** Ablation `A2`/`A3`/`A5`/`A6`/`A8` cannot be constructed cleanly. | Low | Medium – High for H1 (`A2`) and H4 (`A8`). | High (no supersession to disable; no derivation DAG to disable). | §3 above. If the ablation is not clean, H1/H3/H4 cannot be run inside that framework. |
| **R-C Storage-boundary widening** (H4). | Low | High (adds Neo4j/FalkorDB/Neptune to the boundary). | Low (PG + pgvector). | The §7.7 boundary definition is a spec item; widening it is a V5 change requiring the fact-check audit (`TECHNICAL_SPECIFICATION.md` §13). |
| **R-D Invariant 10 (archival `(S) ∨ (C)`).** | Low (we enforce). | Medium (no analogous invariant; must be added as a custom extension to Graphiti). | High (no analogue). | Keep T1 – T5 harness as the gate for invariant 10 regardless of framework. |
| **R-E Reproducibility and pinning.** | Low (project owns the versions). | Low (Apache-2.0, pinned commit). | Low (Apache-2.0, pinned commit). | Pin every commit; capture in run manifests per `PROJECT_ROADMAP.md` §D.3. |
| **R-F Infrastructure complexity.** | Low (PG only). | High (add graph DB; LLM calls per episode). | Low – Medium. | Factor into pilot budget (D-20). |
| **R-G Operational isolation (invariant 7).** | Low (RLS native on PG). | Medium (application-layer scoping only; mitigation = per-user partitions). | Medium. | Enforce user scoping at the service boundary **and** use RLS where the store supports it; add cross-user tests regardless. |
| **R-H Framework behaviour drift.** Pinned release diverges from documented behaviour in paper vs README (already observed for Mem0; see §2.2). | None. | Low (CI badges, pinned commit). | Medium (ADD-only vs ADD/UPDATE/DELETE divergence between the 2025 paper and the pinned README). | Already mitigated by pinning the commit and depth-labelling the ledger. |

The two sharpest risks are **R-B (experimental confounding)** and **R-C (storage-boundary widening)**. Both are reasons to **keep the research-critical mechanisms under our control** and to **confine framework reuse to roles that are evaluated separately** (B3, B4) rather than built into P.

---

## 5. Unresolved questions

Items here are not for this review to decide. They are listed so that the Decision Matrix and the Recommended Architecture explicitly label them.

1. **Does Graphiti's `remove_episode` propagate to sagas/communities and to their embeddings and lexical entries in one transaction?** Not verified from the inspected code. Needs a small prototype or a code-level inspection of `remove_communities` + `update_community` + driver-level deletion of embeddings. See `graphiti_code.txt` lines 2164 – 2192 for the method and lines 103 – 107 for the imports.
2. **Can Graphiti's edge-level validity semantics be lifted to the proposal's three retrieval predicates without re-implementing the data model?** Not verified. The edge-filter API via `SearchFilters` exists (`graphiti_code.txt` lines 76, 80), but the proposal's named predicates (current-state, historical-as-of point/window, audit-as-of) have additional invariants (R8 `t > ρ` check; audit-as-of includes Archived rows while the other two exclude them; §7.4 "Query predicates" table).
3. **PostgreSQL version for the full schema.** The reduced-schema SQL ran on 16.15. Decisions D-25 / D-18 in `DECISIONS_REGISTER.md` are still Open. This affects `btree_gist` and `pgvector` behaviour and is independent of the architecture choice.
4. **Mem0 OSS parity with the headline platform scores.** The README notes "open-source users should expect directionally similar gains but not identical numbers." The baseline role (`B3`) is still viable, but the number we report for `B3` must be the number we **measure** on the pinned OSS commit, not the headline.
5. **Will the supervisor accept the framing that bi-temporal versioning is reused rather than invented?** `DECISIONS_REGISTER.md` D-09 (H1 scope) is Open. The proposal's §6.2 already states the framing; a supervisor memo should confirm.
6. **If Graphiti is included as B4, is the pilot cost affordable?** D-21 is Open. This is independent of the P-architecture choice (Graphiti is only B4 either way).
7. **Does the §7.7 storage boundary need to re-enumerate channels if we adopt pgvector + per-user partition + PG FTS exactly as the proposal already specifies?** The seven channels in §10.3 "Deletion channels" are already pointed at a PostgreSQL-only boundary; no re-enumeration is needed as long as the engine stays on PostgreSQL. This is a reason to prefer PostgreSQL over moving part of the belief graph into Neo4j/FalkorDB.

---

## 6. What this review does not do

- It does not run any experiment. H1 – H4 are not evaluated.
- It does not install Graphiti, Mem0, or any dependency.
- It does not publish, assign, or modify GitHub Issues or Projects.
- It does not change any Frozen V4 decision. Where the recommendation touches a Frozen row, it says so and names the row.
- It does not substitute for a code-level inspection of Graphiti's deletion propagation into sagas/communities/embeddings/lexical entries. That is a prototype item in [`FEASIBILITY_REVIEW_DECISIONS.md`](FEASIBILITY_REVIEW_DECISIONS.md).

See [`ARCHITECTURE_DECISION_MATRIX.md`](ARCHITECTURE_DECISION_MATRIX.md) for the explicit per-criterion comparison and [PLAN/ARCHITECTURE.md](../PLAN/ARCHITECTURE.md) for canonical component boundaries.
