## Graduation Project Proposal

## Personal Digital Twin Agent

## with Temporally-Aware, Governed Long-Term Memory

Abdulrahman Aymen

AI / Orchestration Lead

Abdulrahman Mohamed

Mohamed Tamer

Mazen Ehab

Moaz Loaie

Ahmed Loaie

Abdelrahman Nashaat

Memory Algorithms Lead

Backend Architecture Lead

Governance / QA / Frontend Lead

Temporal State Specialist

Infrastructure Specialist

Retrieval & Security Lead

Supervisor: ______________________________

Academic Year 2026 / 2027 · Duration: 24 weeks


## Abstract

Large language model (LLM) assistants are increasingly used across many conversations, yet they handle long-term memory poorly. They either forget everything between sessions, or store user information as an unstructured pile of text snippets that cannot tell an old preference from a current one, cannot resolve contradictions, and cannot reliably forget data when the user asks. This project designs, implements, and evaluates a temporally-aware, governed long-term memory engine for LLM agents.

The engine organizes memory into four layers (episodic, semantic, opinion/state, and persona), records every memory with full provenance, and models time with two independent clocks (valid time and recorded time) so that changed facts are versioned instead of overwritten. A contradiction-validation gate prevents unverified or conflicting information from reaching the model's answers. A retention score governs what is kept, and a hybrid retrieval score combines semantic similarity, keyword matching, retention, and temporal validity. A right-to-forget workflow removes a memory and everything derived from it, with an auditable trail.

The system separates a fast synchronous path, which answers the user immediately, from a slow asynchronous path, which extracts, consolidates, and versions memories in the background. We evaluate the engine against frozen baselines on public long-term memory benchmarks and on purpose-built temporal, contradiction, deletion, and poisoning test suites, with ablation studies and statistical significance testing. Finally, we integrate the engine into a conversational Personal Digital Twin application with a transparent memory inspector.

## Contents

- 1. Introduction and Problem Statement

- 2. Motivation and Significance

- 3. Objectives

- 4. Research Questions and Hypotheses

- 5. Scope

- 6. Related Work

- 7. Proposed System

- 8. Data Model

- 9. Technology Stack

- 10. Evaluation Methodology

- 11. The Personal Digital Twin Application

- 12. Work Plan and Milestones

- 13. Team Organization

- 14. Deliverables

- 15. Risk Management

- 16. Ethics, Privacy and Security

- 17. Feasibility and Resources

- 18. Expected Outcomes and Contributions References

## 1. Introduction and Problem Statement

Conversational assistants built on LLMs are stateless by design: each request is processed with only the text placed in its context window. Products work around this either by replaying long conversation histories, which is expensive and degrades as context grows [8], or by retrieving stored snippets with vector search (Retrieval- Augmented Generation, RAG) [7]. Neither approach treats memory as data that changes over time.

Consider a user who said in March, "I'm vegetarian," and in August, "I started eating fish again." A naive vector store holds both statements and may retrieve either, so the assistant gives inconsistent or outdated answers. The same weaknesses appear in four forms:

- 1. Temporal confusion: outdated facts are presented as current, and historical questions ("what did I prefer last year?") cannot be answered reliably.

- 2. Ungoverned writes: any extracted statement, including a misunderstanding, a joke, or a deliberately injected false claim, can become a "fact" with no validation step.


- 3. Unreliable forgetting: deleting a message does not remove embeddings, summaries, or profile traits derived from it, so deleted data can re-surface.

- 4. No transparency: users cannot see what the assistant remembers about them or why.

Problem statement. There is no open, well-evaluated memory architecture for LLM agents that simultaneously (a) versions user knowledge over time, (b) validates new memories before they influence answers, (c) guarantees complete deletion of a memory and all its derivatives, and (d) does so with latency suitable for real-time conversation.

## 2. Motivation and Significance

- Practical relevance: long-term memory is now a core feature of commercial assistants, but its behavior is opaque and hard to evaluate. An open, measurable design is valuable to researchers and developers.

- Correctness: personal assistants, tutoring systems, and healthcare or customer-support agents must distinguish what is true now from what was true.

- Regulation and trust: data-protection laws such as the GDPR give users a right to erasure [14]. Memory systems must make deletion verifiable, not best-effort.

- Security: memory is a new attack surface. Injected content can permanently corrupt an agent's knowledge (memory/knowledge poisoning) [13].

- Educational value: the project combines databases, information retrieval, NLP, distributed systems, software engineering, security, and human-centred evaluation, which suits a seven-member graduation team.

## 3. Objectives

| # Objective O1 |   | Measured by Design a four-layer memory model (episodic, semantic, opinion/state, Frozen data dictionary and schema; |
| --- | --- | --- |
| O2 never overwritten. O3 conflicting memories out of answers. O4 each signal. O5 O6 asynchronous processing. O7 O8 memory inspector. | persona) with explicit lifecycle states and provenance. | lifecycle tests Implement bi-temporal versioning so that changed facts are superseded, Current-state precision and historical-state recall Implement a contradiction-validation gate that keeps unverified or Conflict-handling accuracy; poisoning success rate Design retention and hybrid retrieval scores and show the contribution of Retrieval Recall@k / MRR; ablation deltas Implement verifiable right-to-forget, including derived data. Post-deletion leakage rate (target: 0%) Keep the user-facing response path fast by separating synchronous and P95 retrieval latency; end-to-end latency Evaluate rigorously against frozen baselines with statistical testing. Benchmark results, confidence intervals, Pareto analysis Deliver a working Personal Digital Twin application with a transparent Final demonstration; usability study |


## 4. Research Questions and Hypotheses

| RQ Question |   | Hypothesis |
| --- | --- | --- |
| RQ1 | Does explicit bi-temporal versioning improve | H1: The proposed system achieves higher current-state |
|   | answers about current and past user states | precision and historical-state recall than RAG and full-context |
|   | compared with flat vector memory? | baselines. |
| RQ2 | Which retrieval signals (dense, lexical, | H2: Removing the temporal validity signal causes the largest |
|   | retention, validity) contribute most to answer | drop on change-over-time questions; hybrid retrieval |
|   | quality? | outperforms either dense or lexical alone. |
| RQ3 | Does a validation gate reduce the impact of | H3: The gate significantly lowers poisoning success while |
|   | contradictory or adversarial inputs without | preserving at least 90% of legitimate explicit updates. |
|   | blocking legitimate updates? |   |
| RQ4 | Can verifiable deletion and asynchronous | H4: Post-deletion leakage is zero across all retrieval channels, |
|   | consolidation be achieved without hurting real- | and P95 fast-path retrieval latency stays within an interactive |
|   | time latency? | budget (target ≤ 300 ms, excluding LLM generation). |

## 5. Scope

## In scope

- Text-based conversational memory for a single assistant serving multiple isolated users.

- Memory extraction, validation, versioning, retention, retrieval, and deletion.

- Benchmarking, ablations, adversarial testing, and a small human evaluation.

- A web-based Personal Digital Twin application with memory inspector.

## Out of scope

- Training or fine-tuning a foundation LLM (we use existing models through an adapter).

- Multimodal memory (images, audio, video).

- Production-scale deployment, multi-region hosting, or commercial launch.

- Clinical, legal, or other high-stakes decision support.

## 6. Related Work

| Work | Key idea | Gap addressed by this project |
| --- | --- | --- |
| RAG (Lewis et al., | Retrieve external passages to ground | No notion of time, versioning, or user-specific |
| 2020) [7] | generation. | governance. |
| Generative Agents | Memory stream scored by recency, | No explicit validity intervals, contradiction |
| (Park et al., 2023) [2] | importance, and relevance; reflection into | handling, or deletion guarantees. |
|   | higher-level insights. |   |
| MemGPT / Letta | OS-style paging between context and external | Focus on context management rather than |
| (Packer et al., 2023) [1] | storage. | temporal correctness or governance. |
| MemoryBank (Zhong | Forgetting curve inspired by Ebbinghaus to | Decay only; no versioning of changed facts or |
| et al., 2024) [3] | update memory strength. | verifiable erasure. |
| Mem0 (Chhikara et al., | Production memory layer with | Updates may overwrite history; limited |
| 2025) [6] | add/update/delete operations, optional graph | auditability of derived data. |
|   | memory. |   |


| Work | Key idea | Gap addressed by this project |
| --- | --- | --- |
| Zep / Graphiti | Temporal knowledge graph with bi-temporal | Closest prior work; we add a validation gate, |
| (Rasmussen et al., | edges. | retention scoring, verified deletion, and an |
| 2025) [5] |   | open ablation-driven evaluation. |
| LoCoMo [4], | Benchmarks for very long-term conversational | Used as public evaluation; extended with |
| LongMemEval [9] | memory, including temporal reasoning and | deletion and poisoning suites. |
|   | knowledge updates. |   |
| PoisonedRAG (Zou et | Shows that a few injected texts can control | Motivates our validation gate and poisoning |
| al., 2025) [13] | RAG outputs. | tests. |

Temporal database theory, in particular bi-temporal modelling of valid time and transaction time [11], provides the formal basis for our versioning model.

## 7. Proposed System

## 7.1 Architecture overview

## Presentation layer

Personal Digital Twin chat UI · Memory Inspector · Right-to-forget controls · Review dashboard

## Agent API (FastAPI)

Session & turn ingestion, user

scoping, trace IDs

## Fast path (synchronous)

Query analyzer → hybrid retrieval → context builder → LLM adapter

(streaming)

## Slow path (asynchronous workers)

Extraction → validation gate → temporal state manager → consolidation → retention update

## Storage: PostgreSQL 16 + pgvector

Users, sessions, turns, memory nodes, evidence, embeddings, conflicts, audit

events, deletion requests

Cross-cutting: structured logging, latency metrics, evaluation traces, audit trail

## Redis

Job queue, idempotency keys,

caching

## 7.2 Four memory layers

## Episodic

Raw events grounded in specific turns ("On 3 May the user said they moved to Cairo"). Highest volume, fastest decay, periodically compressed.

## Semantic

Stable facts consolidated from episodes ("User works as a civil engineer"). Slower decay.

## Opinion / State

Preferences and states that legitimately change ("prefers tea"). Fully versioned with validity intervals.

## Persona

Core traits corroborated across multiple sessions. Slowest decay; promotion requires repeated evidence.

## 7.3 Fast path and slow path

Fast path: (1) the user's message is stored as a turn; (2) the query analyzer detects intent and time constraints ("now", "last year"); (3) hybrid retrieval runs dense (pgvector) and lexical (full-text/BM25-style [10]) search filtered by user and temporal validity; (4) results are fused and ranked; (5) the context builder assembles a bounded, token-budgeted context; (6) the LLM streams the answer; (7) the assistant turn is stored and the raw turn is dispatched to the worker queue. The response never waits for background processing.

Slow path: a worker extracts structured candidate memories with a schema-validated prompt; malformed output is rejected. Each candidate is compared with existing active memories on the same subject and predicate. Non- conflicting, high-confidence candidates become Active. Conflicting or low-confidence ones become Conflict- Pending until a policy or human review resolves them. Trusted explicit updates supersede older versions by


closing their validity interval. Jobs are idempotent (key = source turn + extraction version + memory fingerprint) so retries never create duplicates.

## 7.4 Bi-temporal model

Each memory version carries two independent intervals: valid time [ valid_from , valid_until ), when the fact was true in the world, and recorded time [ recorded_from , recorded_until ), when the system believed that version. This allows three classes of questions: current ("what does the user prefer now?"), historical ("what did they prefer in March?"), and audit ("what did the system believe on 1 June?"). A single-valued predicate may not have overlapping active valid intervals for the same user and subject; this is enforced by database constraints and tests.

## 7.5 Memory lifecycle

| Transition | Trigger |
| --- | --- |
| (new) → Candidate | Extracted from a conversation turn by the worker |
| Candidate → Active | Passes validation (confidence, provenance, explicitness, no conflict) |
| Candidate → Conflict-Pending | Conflicts with an active memory or has low confidence |
| Conflict-Pending → Active / | Resolved by policy or human review; rejected items keep audit metadata only |
| Archived |   |
| Active → Superseded | A trusted, explicit update replaces it; old version keeps its history and links to its |
|   | replacement |
| Active → Archived | Retention score falls below threshold, or consolidated into a higher layer |
| Any → Deleted | Right-to-forget request or retention purge; content, embeddings, and derivatives are |
|   | purged |

## 7.6 Scoring

```
Retention: S_ret(m) = w_i·I(m) + w_c·C(m) + w_f·log(1 + R(m)) − λ_type·Δt
```

I = importance, C = confidence, R = number of reinforcements (re-mentions or corroborations), Δt = time since last reinforcement, λtype = layer-specific decay rate (episodic > semantic > persona). Retention is computed offline by workers and governs archival, compression, and promotion.

```
Retrieval: S_retrieval(m,q) = α·dense(m,q) + β·lexical(m,q) + γ·S_ret(m) + δ·I_valid(m, t_q)
```

dense = cosine similarity of embeddings; lexical = normalized full-text score; Ivalid = 1 if the memory is valid at the query's target time tq. Initial weights are configured and then tuned on a development split only. Every component is logged per result to enable ablations. Reciprocal Rank Fusion [12] is evaluated as an alternative fusion method.

## 7.7 Right to forget

A deletion request removes the memory's content, all its embeddings, any summaries or consolidated memories derived from it, and persona references that depended on it. The audit trail retains only non-content metadata (identifiers, timestamps, event type) proving that deletion occurred. Deletion is verified at three levels: direct SQL lookup, lexical and vector search, and derived-memory lookup.

## 7.8 Security and isolation

- Every query is scoped by user identity; unscoped queries are rejected at the service boundary.

- Candidates and conflict-pending memories never enter the user-facing context.

- Provenance on every memory allows tracing and rolling back any injected content.


- Adversarial test suites cover prompt-injected "facts", repeated false claims, and cross-user leakage attempts.

## 8. Data Model

## APP_USER

id · external_ref · created_at

## SESSION

id · user_id · started_at · ended_at

## TURN

id · session_id · sequence_no · role · content · occurred_at

## MEMORY_NODE

id · user_id · memory_type · status ·

subject · predicate · content ·

valid_from/until · recorded_from/until ·

confidence · importance ·

superseded_by

## MEMORY_EVIDENCE

memory_id · turn_id · evidence_role

## MEMORY_EMBEDDING

memory_id · embedding_model · embedding (vector)

## MEMORY_CONFLICT

id · candidate_memory_id · existing_memory_id · decision · resolved_at

## AUDIT_EVENT

id · memory_id · event_type · details (jsonb) · event_at

## DELETION_REQUEST

id · user_id · memory_id · status · completed_at

## Database invariants

- 1. Every persistent memory has a source session, source turn, confidence, importance, and provenance link.

- 2. valid_from < valid_until whenever valid_until is present.

- 3. Valid time and recorded time are separate clocks.

- 4. No overlapping active valid intervals for a single-valued predicate per user and subject.

- 5. Superseded versions are kept and point to their replacement; nothing is silently overwritten.

- 6. Candidate and conflict-pending memories cannot enter retrieval context.

- 7. Deletion removes content, embeddings, summaries, and persona references; audit keeps only non-content metadata.

- 8. All memory and retrieval queries are scoped by user identity.

## 9. Technology Stack

| Layer | Technology | Reason |
| --- | --- | --- |
| Language / | Python 3.12, FastAPI, Pydantic | Mature AI ecosystem; typed API contracts |
| API |   |   |
| Database | PostgreSQL 16 + pgvector | Relational integrity, temporal constraints, and vector |
|   |   | search in one transactional store, which simplifies |
|   |   | consistent deletion |
| Lexical | PostgreSQL full-text search (tsvector, GIN index) Keyword retrieval without an extra service |   |
| search |   |   |
| Queue / | Redis + Celery (or ARQ) | Retries, idempotency, dead-letter handling |
| workers |   |   |
| LLM | Provider-agnostic adapter; one frozen model for | Reproducibility; cost control |
|   | experiments (open-weight model via a local |   |
|   | runtime, or a hosted API) |   |
| Embeddings One frozen embedding model for all experiments Fair comparison across systems |   |   |
| Frontend | React (TypeScript) | Chat UI, memory inspector, review dashboard |


| Layer | Technology | Reason |
| --- | --- | --- |
| DevOps | Docker Compose, GitHub, CI (pytest), structured | One-command reproducible environment |
|   | logging |   |
| Analysis | Python (pandas, SciPy), Jupyter | Statistics and plots for the thesis |

## 10. Evaluation Methodology

## 10.1 Systems compared

All systems use the same LLM, same embedding model, same prompts for answering, and the same data; only the memory component differs.

| ID System |   | Description |
| --- | --- | --- |
| B0 | No memory | Current session only |
| B1 | Full context | Entire history placed in the prompt (truncated at model limit) |
| B2 | Vanilla RAG | Dense retrieval over raw turns; no time, no validation |
| B3 | Open-source memory | A publicly available agent-memory library (e.g., Mem0) under the same model |
|   | layer | configuration |
| P | Proposed system | Full four-layer, bi-temporal, governed memory |

## 10.2 Datasets

- Public benchmarks: LoCoMo [4] and LongMemEval [9], which cover multi-session recall, temporal reasoning, and knowledge updates.

- Temporal change suite (ours): scripted multi-session dialogues where preferences and states change, with paired current and historical questions.

- Contradiction suite (ours): ambiguous, conflicting, and hedged statements ("maybe I'll move"), testing the validation gate.

- Deletion suite (ours): forget requests followed by direct and indirect probing for the deleted information.

- Adversarial suite (ours): injected false facts, repeated false claims, and cross-user leakage attempts.

All datasets are split into development and held-out test sets, which are frozen at Gate 1. Tuning uses the development split only.

## 10.3 Metrics

| Dimension | Metric |
| --- | --- |
| Answer quality | Accuracy / F1 against references; LLM-as-judge [15] calibrated against a human-labelled sample |
| Retrieval quality | Recall@k, MRR, nDCG@k |
| Temporal correctness | Current-state precision, historical-state recall (reported separately) |
| Governance | Conflict-handling accuracy; legitimate-update acceptance rate |
| Forgetting | Post-deletion leakage rate across SQL, lexical, vector, and derived channels |
| Security | Poisoning success rate; cross-user leakage (target 0) |
| Efficiency | P50/P95 retrieval latency, end-to-end latency, tokens and cost per query, storage growth |


## 10.4 Ablation studies

Starting from the full system, we remove one component at a time: (a) temporal validity signal, (b) retention score, (c) lexical retrieval, (d) dense retrieval, (e) validation gate, (f) consolidation into semantic and persona layers, (g) bi-temporal versioning (replaced by overwrite). The change in each metric quantifies that component's contribution.

## 10.5 Statistical analysis

Each configuration is run multiple times with fixed seeds. We report means with 95% bootstrap confidence intervals and use paired significance tests (paired bootstrap or Wilcoxon signed-rank) across questions, with correction for multiple comparisons. A Pareto analysis plots answer quality against latency and cost to identify configurations that are not dominated by any other.

## 10.6 Human evaluation

A small within-subject study (target 15–20 participants) uses the Digital Twin over several sessions. Participants rate perceived memory accuracy, consistency, and trust, and complete the System Usability Scale for the memory inspector. Participation is voluntary, with informed consent and synthetic or self-chosen personal data only.

## 11. The Personal Digital Twin Application

The application phase turns the research engine into a usable product: a conversational assistant that builds an evolving, user-controlled model of the person it talks with.

- Multi-session chat with streaming responses and memory-grounded answers.

- Memory Inspector: browse memories by layer, see current vs. historical versions on a timeline, and view the source message for every memory.

- Right-to-forget controls: delete a single memory, a topic, or everything, with confirmation and a deletion receipt.

- Review queue: resolve conflict-pending memories ("Did you mean you moved, or are you planning to?").

- Persona summary: a readable profile of corroborated traits, each linked to its evidence.

- Explainability: an optional "why did you say that?" panel listing the memories used for an answer and their score components.

## 12. Work Plan and Milestones

The project runs for 24 weeks: a research phase (weeks 1–14) followed by an application phase (weeks 15–24). Development is database-first: the data model is the shared contract between all workstreams, so it is frozen before retrieval or interface work begins. Each milestone ends with a gate, a set of acceptance tests that must pass before the next milestone starts.

M8

Digital twin

application

| Milestone | Weeks Gate Main outcome |   |   |
| --- | --- | --- | --- |
| M1 Research specification & database | 1–2 | G1 | Frozen data contract, migrations, environment, fixtures, |
| foundation |   |   | frozen test splits |


| Milestone | Weeks Gate Main outcome |   |   |
| --- | --- | --- | --- |
| M2 Core memory infrastructure & fast | 3–4 | G2 | Episodic ingestion and synchronous retrieval end to end |
| path |   |   |   |
| M3 Retention & temporal memory | 5–6 | G3 Asynchronous consolidation and versioned opinion state |   |
| M4 Governance, contradiction & persona 7–8 |   | G4 Four layers, conflict gate, persona promotion, hybrid |   |
|   |   |   | retrieval integrated |
| M5 Forgetting, audit & research freeze | 9–10 G5 Verified deletion, audit trail, frozen baselines and proposed |   |   |
|   |   |   | system |
| M6 Comparative benchmark & ablations | 11–13 | – | Baseline comparison, ablations, adversarial tests, Pareto |
|   |   |   | analysis |
| M7 Research completion | 14 | G6 Thesis draft, statistics, reproducible repository, research |   |
|   |   |   | defense |
| M8 Digital twin application | 15–24 G7 |   | Integrated application, user study, final demonstration |

## M1: Research specification & database foundation (weeks 1–2)

| ID | Owner | Task | Reviewer | Evidence |
| --- | --- | --- | --- | --- |
| M1-01 | Mohamed Tamer | PostgreSQL schema, migration order, | Ahmed Loaie | Clean migrate / |
|   |   | foreign keys, enums, indexes, rollback |   | rollback / reapply |
|   |   | scripts |   | log |
| M1-02 | Moaz Loaie | Valid/recorded time semantics, temporal | Abdulrahman | Temporal rules |
|   |   | invariants, supersession rules, conflict | Mohamed | document + |
|   |   | states |   | constraint tests |
| M1-03 | Abdulrahman Mohamed | Memory taxonomy, lifecycle states, | Moaz Loaie | Data dictionary + |
|   |   | retention/retrieval fields, predicate |   | transition examples |
|   |   | conventions |   |   |
| M1-04 | Abdelrahman Nashaat | Vector and lexical fields, index strategy, | Mohamed | Query-plan notes + |
|   |   | user isolation checks, poisoning audit | Tamer | security test cases |
|   |   | fields |   |   |
| M1-05 | Ahmed Loaie | Docker Compose (PostgreSQL, pgvector, | Mohamed | One-command |
|   |   | Redis), health checks, env template, | Tamer | startup + health |
|   |   | backup/restore test |   | output |
| M1-06 | Mazen Ehab | Audit-event vocabulary, right-to-forget | Abdelrahman | Traceable test |
|   |   | behaviour, test matrix, QA checklist | Nashaat | matrix + deletion |
|   |   |   |   | tests |
| M1-07 | Abdulrahman Aymen | Service/API contracts, session/turn | Abdulrahman | Contract examples |
|   |   | payloads, fixture format; dataset selection | Mohamed | + client smoke test |
|   |   | and frozen splits |   |   |

Gate 1: a fresh machine can start the database, apply migrations, load fixtures, and demonstrate session/turn creation, memory with evidence and embedding, preference change creating a new version, a conflict held for review, and a deletion leaving nothing retrievable. Benchmark splits are frozen.


## M2: Core memory infrastructure & fast path (weeks 3–4)

| ID | Owner | Task | Evidence |
| --- | --- | --- | --- |
| M2-01 | Mohamed Tamer | MemoryStore , session/turn repositories, transaction | CRUD and transaction |
|   |   | boundaries, integration tests | tests pass |
| M2-02 | Abdulrahman Mohamed | Typed memory creation and episodic ingestion; keep | Traceable episodic |
|   |   | candidates out of retrieval | records from fixtures |
| M2-03 | Abdelrahman Nashaat | Embedding adapter, pgvector similarity, lexical adapter, | Deterministic retrieval test |
|   |   | retrieval result schema |   |
| M2-04 | Abdulrahman Aymen | Query analyzer, retrieval router, context builder, token | Fast-path request with |
|   |   | budget, streaming LLM adapter | bounded context |
| M2-05 | Ahmed Loaie | Service container, Redis connection, health/readiness | Health checks + P95 |
|   |   | endpoints, structured logs, latency timing | timing trace |
| M2-06 | Mazen Ehab | QA matrix (single/cross-session, irrelevant, empty | Test report + inspector |
|   |   | memory); memory inspector read view | output |
| M2-07 | Moaz Loaie | Validate valid-time filtering on the fast path; | Old/current temporal |
|   |   | superseded memory never treated as current | retrieval tests |

Gate 2: ordered turns; dense + lexical retrieval within a token budget; superseded records excluded from current answers; response never waits for workers; every result has memory ID, source turn, score components, and trace ID; deterministic replay with a mock LLM.

## M3: Retention & temporal memory (weeks 5–6)

| ID | Owner | Task | Evidence |
| --- | --- | --- | --- |
| M3-01 | Ahmed Loaie | Redis + Celery/ARQ workers, retries, idempotency | Safe retries with no |
|   |   | keys, dead-letter handling, worker metrics | duplicate versions |
| M3-02 | Abdulrahman Mohamed | Retention score, layer-specific decay, reinforcement, | Score fixtures reproduce |
|   |   | compression triggers, promotion/demotion | expected ordering |
| M3-03 | Moaz Loaie | TemporalStateManager : interval updates, | Temporal transition suite |
|   |   | supersession, conflict-pending state | passes |
| M3-04 | Abdulrahman Aymen | Structured extraction prompts, schema validation, | Malformed extraction |
|   |   | turn-to-candidate dispatch | rejected and traced |
| M3-05 | Mohamed Tamer | Consolidation transactions, optimistic locking, | Concurrent consolidation |
|   |   | idempotent upserts, audit persistence | stays consistent |
| M3-06 | Abdelrahman Nashaat | Retention and temporal gate in ranking; index tuning; | Ranking uses all four |
|   |   | query-plan benchmarks | signals |
| M3-07 | Mazen Ehab | Temporal test data, candidate/conflict review | Old/current/history views |
|   |   | dashboard, QA of deletion through worker-derived | verified |
|   |   | data |   |

Gate 3: idempotent candidate creation; conflicting candidates not auto-activated; trusted changes preserve history; score traces stored; retries never duplicate; deletion cascades through derivatives; current-state precision and historical-state recall reported separately.


## M4: Governance, contradiction & persona (weeks 7–8)

| ID | Owner | Task | Evidence |
| --- | --- | --- | --- |
| M4-01 | Moaz Loaie | Contradiction-validation gate: confidence, provenance, | Conflict suite accuracy |
|   |   | and explicitness rules; resolution policies | report |
| M4-02 | Abdulrahman Mohamed | Semantic consolidation and persona promotion with | Promotion fixtures pass |
|   |   | corroboration thresholds; demotion rules |   |
| M4-03 | Abdelrahman Nashaat | Hybrid retrieval fusion (weighted vs. RRF), per-layer | Fusion comparison + first |
|   |   | retrieval, poisoning test harness | poisoning results |
| M4-04 | Abdulrahman Aymen | Layer-aware context builder; review-question | Bounded multi-layer |
|   |   | generation for conflict-pending items | context tests |
| M4-05 | Mohamed Tamer | Persona and conflict tables/APIs, migration review, | Migration and API tests |
|   |   | provenance queries |   |
| M4-06 | Mazen Ehab | Review dashboard for conflicts; persona view in | End-to-end review flow |
|   |   | inspector; governance QA | demo |
| M4-07 | Ahmed Loaie | Worker scaling, queue separation (fast vs. slow), load | P95 under load report |
|   |   | test of the fast path |   |

Gate 4: four layers integrated; conflicts held and resolvable; persona promotion requires corroboration; hybrid retrieval running with logged components.

## M5: Forgetting, audit & research freeze (weeks 9–10)

| ID | Owner | Task | Evidence |
| --- | --- | --- | --- |
| M5-01 | Mazen Ehab | Right-to-forget workflow (single, topic, all) with deletion | Zero leakage report |
|   |   | receipts; three-level deletion test |   |
| M5-02 | Mohamed Tamer | Cascading purge of embeddings, summaries, persona | Purge transaction |
|   |   | references; non-content audit trail | tests |
| M5-03 | Abdelrahman Nashaat | Vector index scans after deletion; cross-user isolation tests Security test report |   |
| M5-04 | Abdulrahman Aymen | Implement baselines B0–B3 behind the same evaluation | Baselines runnable on |
|   |   | interface | fixtures |
| M5-05 | Abdulrahman Mohamed | Tune weights on development split only; freeze | Frozen config file + |
|   |   | configuration | tuning log |
| M5-06 | Moaz Loaie | Finalize temporal, contradiction, and deletion test suites | Versioned frozen |
|   |   |   | suites |
| M5-07 | Ahmed Loaie | Reproducible experiment runner (one command), result | Dry-run of full |
|   |   | storage, seeds | evaluation |

Gate 5: verified deletion with zero leakage; audit trail complete; proposed system and all baselines frozen and tagged in the repository.

## M6: Comparative benchmark & ablations (weeks 11–13)

- Abdulrahman Aymen: run all systems on LoCoMo and LongMemEval; LLM-as-judge calibration.

- Moaz Loaie: temporal and contradiction suites; current vs. historical analysis.

- Abdulrahman Mohamed: ablation runs (a)–(g) and analysis of each component's contribution.


- Abdelrahman Nashaat: adversarial and poisoning experiments; retrieval metrics.

- Ahmed Loaie: latency, cost, and storage measurements; Pareto analysis.

- Mazen Ehab: deletion experiments; human-label sample for judge calibration.

- Mohamed Tamer: result database, statistical scripts, figures and tables for the thesis.

## M7: Research completion (week 14)

Statistical significance testing, thesis draft (method, experiments, results, discussion, limitations), reproducible repository with instructions, and the research-phase defense. Gate 6: supervisor approval of the research results.

*M8: Digital twin application (weeks 15–24)*

| Weeks Focus | Lead(s) |
| --- | --- |
| 15–16 UX design, application architecture, authentication, user onboarding | Mazen Ehab, Mohamed Tamer |
| 17–19 Chat UI, memory inspector with timeline, review queue, forget controls Mazen Ehab, Abdulrahman Aymen |   |
| 18–20 Persona summary, "why did you say that?" explainability panel | Abdulrahman Mohamed, Moaz Loaie |
| 19–21 Hardening: security review, rate limits, monitoring, deployment | Abdelrahman Nashaat, Ahmed Loaie |
| 21–22 Human evaluation study (consent, sessions, questionnaires, SUS) | Mazen Ehab, all members |
| 23–24 Final thesis, documentation, demo video, final presentation | All members |

Gate 7: integrated application demonstrated end to end; user study completed; final thesis and repository submitted.

## 13. Team Organization

| Member | Role | Primary ownership | Signs off on |
| --- | --- | --- | --- |
| Abdulrahman Aymen | AI / Orchestration Lead LLM adapter, prompts, fast-path |   | API support for |
|   |   | orchestration, evaluation orchestration | agent flow |
| Abdulrahman Mohamed | Memory Algorithms | Lifecycle, retention and retrieval scores, | Memory semantics |
|   | Lead | consolidation, ablations |   |
| Mohamed Tamer | Backend Architecture | FastAPI, PostgreSQL/pgvector schema, | All migrations |
|   | Lead | migrations, provenance |   |
| Mazen Ehab | Governance / QA / | Inspector, forget workflow, QA, human | Auditability and |
|   | Frontend Lead | evaluation, UI | deletion |
| Moaz Loaie | Temporal State | Validity intervals, state engine, contradiction | Temporal invariants |
|   | Specialist | gate |   |
| Ahmed Loaie | Infrastructure | Docker, Redis, workers, observability, latency, | Reproducible |
|   | Specialist | experiment runner | environment |
| Abdelrahman Nashaat | Retrieval & Security | Dense/lexical retrieval, indexes, poisoning and | Retrieval and |
|   | Lead | isolation tests | security |

Working rules: every task has one owner and one reviewer who tests the interface. Any schema change after Gate 1 requires a migration, an updated data dictionary, updated fixtures, and approval from the backend and temporal owners. Weekly team meetings and a bi-weekly progress report to the supervisor; decisions are recorded in a versioned decision log.


## 14. Deliverables

- 1. Memory engine source code (open repository) with automated tests and one-command setup.

- 2. Database schema, migrations, and data dictionary.

- 3. Evaluation framework, frozen datasets/splits, and reproducible experiment scripts.

- 4. Experimental results: benchmark comparison, ablations, adversarial and deletion tests, Pareto analysis.

- 5. Personal Digital Twin web application with memory inspector and right-to-forget controls.

- 6. Graduation thesis, research-phase report, user-study report, demo video, and final presentation.

## 15. Risk Management

| Risk | Likelihood Impact Mitigation |   |
| --- | --- | --- |
| Schema keeps | Medium High | Database-first plan; single migration owner; data dictionary review |
| changing |   |   |
| Temporal semantics | Medium High | Dedicated temporal owner; invariant tests from week 1 |
| confusion |   |   |
| LLM extraction errors High |   | Medium Schema validation, confidence thresholds, conflict-pending state |
| Duplicate background | Medium Medium Idempotency keys, optimistic locking |   |
| jobs |   |   |
| Deleted data leaks | Low High | Three-level deletion tests; vector index scans |
| back |   |   |
| Slow responses | Medium Medium Separate fast/slow queues; P95 monitoring |   |
| API cost or model | Medium Medium Provider-agnostic adapter; option of a local open-weight model; frozen |   |
| changes |   | model for experiments; response caching |
| Test-set contamination Low | High | Splits frozen at Gate 1; tuning on development split only |
| Schedule slip | Medium Medium Gates with explicit criteria; application features prioritized (must / |   |
|   |   | should / could) |

## 16. Ethics, Privacy and Security

- Data minimisation: development and benchmarks use public or synthetic data; no real personal data is collected outside the consented user study.

- Informed consent: user-study participants receive an information sheet, may withdraw at any time, and can delete all their data through the application.

- User control: users can see every stored memory and its source, and delete any of it; deletion is verifiable.

- Isolation: strict per-user scoping at the service boundary and in tests.

- Transparency: the assistant can explain which memories informed an answer.

- Responsible scope: the system is not intended for high-stakes decisions; limitations are documented in the thesis.

## 17. Feasibility and Resources

The project is feasible within 24 weeks for a seven-member team because:

- It builds on mature open-source components (PostgreSQL, pgvector, FastAPI, Redis, React) rather than training models.


- Work is divided into seven clear areas of ownership, with interfaces fixed early, so members can work in parallel.

- Gates provide early detection of slippage; the application phase can drop "could-have" features without affecting the research results.

| Resource | Requirement |
| --- | --- |
| Hardware | Team laptops for development; one GPU machine or cloud credits if a local open-weight model is used |
| LLM | Hosted API with a capped monthly budget, or a local model; caching to reduce repeated calls during |
| access | experiments |
| Software | Open-source only (PostgreSQL, pgvector, Redis, Python, React, Docker) |
| Data | Public benchmarks (LoCoMo, LongMemEval) plus team-built synthetic suites |
| Participants 15–20 volunteers for the user study |   |

## 18. Expected Outcomes and Contributions

- 1. Architecture: an open four-layer, bi-temporal, governed memory design for LLM agents.

- 2. Algorithms: retention and hybrid retrieval scores with logged components, plus a contradiction-validation gate.

- 3. Evidence: a controlled comparison with baselines, ablations showing each component's contribution, and security and deletion results.

- 4. Evaluation resources: reusable temporal-change, contradiction, deletion, and poisoning test suites.

- 5. Application: a working Personal Digital Twin that demonstrates transparent, user-controlled AI memory.

We expect the proposed system to answer current-state questions more precisely than flat RAG, to answer historical questions that baselines cannot, to resist injected false information better, and to guarantee deletion, while keeping response latency suitable for real-time conversation.

## References

- 1. C. Packer, S. Wooders, K. Lin, V. Fang, S. G. Patil, I. Stoica, J. E. Gonzalez. "MemGPT: Towards LLMs as Operating Systems." arXiv:2310.08560, 2023.

- 2. J. S. Park, J. O'Brien, C. J. Cai, M. R. Morris, P. Liang, M. S. Bernstein. "Generative Agents: Interactive Simulacra of Human Behavior." UIST, 2023.

- 3. W. Zhong, L. Guo, Q. Gao, H. Ye, Y. Wang. "MemoryBank: Enhancing Large Language Models with Long-Term Memory." AAAI, 2024.

- 4. A. Maharana, D.-H. Lee, S. Tulyakov, M. Bansal, F. Barbieri, Y. Fang. "Evaluating Very Long-Term Conversational Memory of LLM Agents" (LoCoMo). ACL, 2024.

- 5. P. Rasmussen et al. "Zep: A Temporal Knowledge Graph Architecture for Agent Memory." arXiv:2501.13956, 2025.

- 6. P. Chhikara et al. "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory." arXiv:2504.19413, 2025.

- 7. P. Lewis et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." NeurIPS, 2020.

- 8. N. F. Liu et al. "Lost in the Middle: How Language Models Use Long Contexts." TACL, 2024.

- 9. D. Wu et al. "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory." ICLR, 2025.

- 10. S. Robertson, H. Zaragoza. "The Probabilistic Relevance Framework: BM25 and Beyond." Foundations and Trends in IR, 2009.

- 11. R. T. Snodgrass. Developing Time-Oriented Database Applications in SQL. Morgan Kaufmann, 2000.

- 12. G. V. Cormack, C. L. A. Clarke, S. Büttcher. "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." SIGIR, 2009.

- 13. W. Zou, R. Geng, B. Wang, J. Jia. "PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation of Large Language Models." USENIX Security, 2025.


14. Regulation (EU) 2016/679 (General Data Protection Regulation), Article 17: Right to erasure. 15. L. Zheng et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." NeurIPS Datasets and Benchmarks,

2023.
