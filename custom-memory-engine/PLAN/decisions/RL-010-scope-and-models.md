# RL-010 — Scope, models, and archival enforcement (#1)

Prepared 2026-10-24. **Technical recommendation prepared; supervisor approval pending.**
No model inference, benchmark evaluation, or approval is claimed.

## Selected values

| Decision | Selected option | Rationale / constraints |
|---|---|---|
| D-01 | OpenAI `gpt-4.1-mini-2025-04-14` for answering and extraction; temperature 0, top_p 1, maximum completion 2,048 tokens; record seed 42 in the manifest, send a seed only if the endpoint supports it | Snapshot rather than floating alias; schema-constrained extraction support; comparatively inexpensive hosted model. Availability and schema compliance must pass the dev adapter probe before freeze. Temperature 0 is not deterministic execution. |
| D-02 | `BAAI/bge-small-en-v1.5`, revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, dimension 384 | Small CPU-capable English embedder. CLS pooling, L2 normalization, cosine distance, maximum 512 tokens including query prefix; query prefix `Represent this sentence for searching relevant passages: `; no prefix on memories. Chunk overflow rather than silently discard evidence; chunking/version recorded in future cache manifests. Pin tokenizer and weights to the same revision. |
| D-03 | OpenAI `gpt-4.1-2025-04-14`, temperature 0, top_p 1, maximum completion 1,024 tokens | Distinct model from D-01, **same family**: self-preference risk remains. No claim that larger model means valid judge. RL-016 validation and perturbation gates mandatory. |
| D-04 | Persona A10 OFF in P; SKIP exploratory implementation | V4 mandatory primary scope protection, H1–H4 do not depend on Persona. |
| D-05 | Retention A9 OFF in P; SKIP exploratory implementation | Preserve temporal admissibility and unweighted RRF in primary P. |
| D-06 | BM25 OFF / SKIP | PostgreSQL FTS with `ts_rank_cd` is the primary lexical channel; it is not BM25. Any BM25 experiment requires a dev-derived revision, not a change to frozen P. |
| D-07 | Redis/Celery/ARQ OFF / SKIP | PostgreSQL JOB + SKIP LOCKED retains a single-store deletion boundary. |
| D-08 | H5 usability study OFF | No participants, no recruitment; cognitive walkthrough using fictional data only. Supervisor may reopen only with written clearance under D-19. |
| D-27 | Transactional `archive_version` procedure plus BEFORE UPDATE trigger; restrict direct DML for runtime role | Enforce V4 `(S) ∨ (C)` against same-transaction state, not a pre-check outside the transaction. |

## Budget and hardware constraints (proposal, not funding approval)

Proposed combined dev + confirmatory provider ceiling **USD 100 and 50 million input+output tokens**, whichever is reached first. Suballocate USD 20 to dev/pilot; never implicitly replenish. D-01 rate reference: USD 0.40/1M input, 1.60/1M output; D-03: USD 2/1M input, 8/1M output (uncached standard text rates inspected 2026-10-24). Rates are observations, not immutable provider promises. RL-030 must reserve worst-case cost before concurrent dispatch, reconcile actual usage, count failed/retried calls, and stop before exceeding either cap. No charged calls occur in this P1 work. Price changes require re-budgeting and approval before use; D-22 subset selection remains pilot-derived.

Embedding on CPU, bounded batches initially 16; hosted LLMs require no local GPU. Proposed minimum development host: 4 CPU cores, 8 GiB RAM with at least 2 GiB database allocation and 5 GiB free disk. These are planning constraints, **not measured throughput guarantees**; RL-100 measures memory and latency before sizing concurrency. The embedder is English-only: multilingual suitability is not claimed. Do not silently swap models if a provider snapshot is unavailable.

## D-27 implementation contract for RL-042/RL-090

Hold the per-user transaction advisory lock; lock source and relevant replacement/derivation rows before testing the precondition. S: recorded range closed. C: distinct same-user active replacement, open recorded upper bound, derived→source link and valid-range containment. Prefer S if both hold. Reject T1/T2 when neither holds; archival changes only status and branch, never validity or recorded ranges. Trigger must cover UPDATE (including direct status writes), validate branch matches the computed predicate, and reject INSERT-as-archived bypasses through role privileges/insert guard. Replacing or archiving a C-supporting replacement must preserve coverage or supersede the dependent source safely; locking only the source is insufficient against replacement mutation. Privileged bypass is not a supported runtime operation.

T1–T5 remain future **full-engine** regressions, including T5 seven-channel deletion. This memo does not claim they have run.

## Evidence and downstream impact

Normative: Research Lab Proposal §§5, 7.2–7.8, 8–10; Technical Specification §6; frozen D-ENG-02. Provider snapshot/rate pages and pinned embedding config/model card: `../../evidence/SOURCES.md`. Applies to RL-011, RL-022, RL-031, RL-033, RL-040, RL-050, RL-080–083, RL-090, RL-100, RL-122. No pilot-derived D-09–14/D-21–22 thresholds are frozen here.

## Sign-off

Owner Member 1 (R-LEAD): pending. Peer Member 3 (R-METHOD): pending. Supervisor: pending. Prepared by coding agent, not a proxy signature. Default-disabled optional features are safe implementation defaults; **research freeze and paid execution remain blocked on approvals**.
