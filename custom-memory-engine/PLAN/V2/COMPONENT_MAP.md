# Component map (`W3.5`): reuse, replace, adapt, build, defer

The question this document answers is the one the review asked and the plan did not: **for every capability the engine
needs now or will need, is there already a proven implementation we should use, and if not, why are we writing one?**

Three rules decided every row, in this order:

1. **Nothing is adopted wholesale.** No reference engine inspected in the 2026 comparison combines bi-temporal exclusion,
   PostgreSQL `FORCE ROW LEVEL SECURITY`, cryptographic deletion receipts and channel-enforced epistemic separation.
   Adopting one would trade the guarantees for the feature, so adoption is always *component-level*: a mechanism, a
   statistical method, a schema shape — reimplemented against this engine's invariants, never a dependency that owns the
   store.
2. **A popular choice is not evidence.** Every "reuse" row names what was inspected (source path or paper) and what it
   actually does. The 2026 comparison found that half the widely cited components are broken in the shipped code — Mem0's
   OSS engine has no graph and leaks deleted plaintext, Graphiti's entity dedup is ASCII-only, Hindsight's temporal query
   analyser silently drops non-English queries. A row citing such a component says so.
3. **Custom code is a cost, so it needs a reason.** Where we build, the reason is one of: *the guarantee is ours and
   cannot be delegated* (governance, deletion, channels), *no maintained implementation exists in our storage* (a
   principal-scoped open slot key), or *the dependency would own the data path* (a graph database).

**Legend.** `have` — implemented and tested here. `reuse` — take the proven component as-is (a library, an extension).
`adapt` — take the mechanism, reimplement against our invariants. `replace` — the current implementation is inferior to
an available one and is being replaced. `build` — no adequate implementation exists for our constraints. `defer` — needed
by a later wave, not now, with the wave named.

---

## 1. Storage and indexing

| Capability | Decision | Component / evidence | Why this and not the alternative |
|---|---|---|---|
| Relational store, RLS, exclusion constraints | `have` | PostgreSQL 16 + `btree_gist`; `FORCE ROW LEVEL SECURITY` with a `NOSUPERUSER NOBYPASSRLS` runtime role | Every reference engine that stores in a graph database pays a second datastore for isolation it does not get: Graphiti and MIRIX have no per-row policy at all, and both leak on deletion. PostgreSQL + RLS is the only substrate here that makes tenancy a database property. |
| Dense vectors | `reuse` | `pgvector` 0.8.7 — `halfvec` slots + HNSW (migration 23), exact scan for the legacy 384 slot | Considered: Qdrant/Milvus (Mem0, MemOS) and LanceDB (Cognee). Rejected because they put the vector outside the transaction and the deletion boundary — `Cognee` needs `ref-counted` cleanup across three databases, and `MIRIX` never deletes Neo4j at all. `pgvector` 0.8's `halfvec` + HNSW closes the recall gap that made a separate store tempting. |
| Lexical search | `reuse` | PostgreSQL `to_tsvector('simple', …)` + `pg_trgm` (migration 23) | Considered: ParadeDB `pg_search` (BM25/ICU) and PGroonga. Both are stronger for BM25, and both are extra extensions whose tokeniser configuration is another deployment step. `'simple'` + trigram needs no language and no configuration, which is the property that matters for a corpus whose language is per-row; BM25 remains a **`defer`** for `W6` if a relevance study shows it pays. |
| Graph traversal | `have` | `association` + bounded recursive CTE (`engine/expansion.py`) | Considered: Neo4j/FalkorDB/Kuzu (Graphiti, Cognee, MIRIX). A second database would move association integrity outside RLS and outside the deletion closure, and Graphiti's own `remove_episode` fails to un-invalidate edges. One hop is bounded on purpose (`V2-D79`). |
| Embedding slots | `have` | migration 23: `vector(384)` + `halfvec` 768/1024/1536/3072, one partial HNSW index each | A single "configurable dimension" column cannot be ANN-indexed (`vector` without a typmod has no index, and HNSW is one fixed width). Slots give coexistence during a re-embedding rollout without a second database or a per-deployment migration parameter. A new class is a one-paragraph migration. |
| Embedders | `reuse` | Not a dependency: `semantic.contracts.Embedder` is a protocol. Recommended implementations `BAAI/bge-m3` (1024, multilingual), `intfloat/multilingual-e5-large`, `text-embedding-3-small/large`, `gemini-embedding-001` | The engine loads no model (`V2-D88`), so the choice is the deployment's and can change without a code change. |

## 2. Semantic understanding

| Capability | Decision | Component / evidence | Why this and not the alternative |
|---|---|---|---|
| Atomic claim extraction, modality, entities, relative time | `have` contract, `reuse` implementation | `semantic/contracts.py` (`StructuredExtractor` → typed `ExtractionResult`); any structured-output LLM in the deployment | Considered: spaCy NER + rules (Mem0), GLiNER fixed label banks (Cognee), static `predicate_schema` (ChronaMind v1). All three are language- and domain-locked by construction; the review's own comparison shows nine of twelve engines hardcode English NLP and the tenth is single-node. |
| Quote grounding | `adapt` | Quipu (`MIT`, `fact_updates.zig`, quote-grounded assertions) + Veracium (`MIT`, provenance-first) → `semantic/grounding.py` | Reimplemented, not vendored: Quipu is Zig and Veracium is single-tenant SQLite, so the *mechanism* transfers and the code does not. Ours adds the two properties neither has — NFC + whitespace-only folding (no case, no diacritics) and a **per-fragment** specifics check so a number from one document cannot licence a quote from another. |
| Specifics grounding (numbers, dates, IDs) | `adapt` | Veracium (`arXiv:2607.21962`) → `semantic/grounding.specifics_in` | Ours is script-agnostic (`\d` = `Nd`, so Arabic-Indic digits count) and reports only the maximal missing token (`v2.3.1`, not also `2.3.1`). |
| Authority ladder + quarantine | `adapt` | Veracium → `semantic/authority.py` + migration 24's **generated** `authority_rank` | Reimplemented as a *derived database column* rather than a code path: Veracium computes authority in Python, and a compromised runtime could then disagree with it. A generated column cannot be forged. |
| Prompt-injection defence | `replace` | Was: 12 hardcoded English substrings (`engine.gate._INSTRUCTION_PATTERNS`). Now: structural quarantine by source, with the phrase list demoted to a narrow belt-and-braces check | The substring list is defeated by one paraphrase in any language and trips honest claims ("never tell the user the meeting moved"). Source-based quarantine cannot be paraphrased around. |
| Entity resolution | `adapt` | Graphiti (`Apache-2.0`, cascaded exact → MinHash/LSH → batched LLM) → `semantic/entities.py` | Cascade kept, ASCII stripping **not**: Graphiti's `[^a-z0-9' ]` deletes every non-Latin script, so "أحمد" becomes the empty string and every Arabic mention collides. Ours keeps script and diacritics and reports ambiguity instead of guessing. |
| Conflict / supersession | `adapt` | Quipu (propose → isolated verify → deterministic check) + Graphiti (non-destructive invalidation) → `semantic/conflict.py` | Reimplemented on ChronaMind's `(s, k, u)`-free but bi-temporal substrate: the deterministic commit is the GiST constraint and close-then-insert, which neither reference has. Recall, not the LLM, decides membership. |
| Slot normalization | `build` | `semantic/slots.py` + the schema `CHECK` | Nothing to reuse: this is a project-specific key with a project-specific exclusion constraint, and the two must agree. The Python twin is deliberate (`INVARIANT_LEDGER`, "two implementations of one rule"). |
| Temporal resolution of a date phrase | `defer` to the model, `have` validation | `semantic/temporal.py`; the model resolves, the engine checks | Considered: `dateparser` (Hindsight), `languages=["en"]` hints (Cognee). Both are English-pinned and both *silently drop* the queries they cannot parse — the failure mode is invisible. Ours refuses with a static code. |
| Cross-encoder reranking | `defer` (seam exists) | `engine.hybrid.Reranker` protocol; candidate implementations `BAAI/bge-reranker-v2-m3` | Deliberately not shipped: a reranker is a model file, a runtime and a latency budget, and the fused three-arm page is a defensible hot path at `charged_calls = 0`. The seam exists so a deployment that measures a gain can take it. |

## 3. Cognition (Tier B — `W4`/`W5`)

| Capability | Decision | Component / evidence | Why |
|---|---|---|---|
| Statistics: CP bound, hierarchical pooling, always-valid looks, dependence correction | `have` / `adapt` | `scipy` 1.18 (`clopper_pearson` path in `evaluation/stats/`), `STATISTICS.md` §5; partial pooling follows standard beta-binomial practice | The mathematics is not a product to buy. The *implementations* are `scipy` where a function exists and first-principles code where the method is a research contribution (`SD-002`, `SD-003`, `SD-008`). |
| Pattern proposal | `adapt` | Quipu `patterns.zig` (`p <= 0.06`): LLM propose → blind score → exact test → `C-020` amended (`V2-D92`) | Quipu tests a `p`-value; ChronaMind bounds an interval with a dependence correction and sticky dissent, which is strictly stronger. The *proposal* shape is what we take. |
| Candidate recall for cognition | `have` | `semantic.conflict.recall_neighbours` and the deterministic operators retained as recall arms | Non-LLM co-occurrence alone is blind to paraphrase, which is why `C-020` was amended rather than kept. |
| Statement rendering | `defer` (`C-022`) | Injected LLM | Rendering cannot alter the predicate; the engine attaches the bound. |
| Deep multi-hop reflection | `defer` (`C-021`/`C-025` slow path) | Hindsight `reflect` as the pattern | Opt-in and budgeted; the hot path must never pay for it. |

## 4. Operations, security and lifecycle

| Capability | Decision | Component / evidence | Why |
|---|---|---|---|
| Deletion closure, 11-channel leak scan, HMAC receipts | `have` | `engine/deletion.py`, `deletion/scanner.py`, `engine/staleness.py` | No reference engine has it. Mem0 leaks deleted plaintext in SQLite `history`/`messages`; MIRIX never deletes Neo4j rows and its `delete_user_memories` spans orgs; Graphiti's `remove_episode` leaves edges invalidated. This is the moat and it stays hand-written. |
| Idempotency, retry, backoff | `reuse` | `tenacity` (retry), per-operation `api_idempotency` rows | Standard libraries where the semantics are standard. |
| Structured logging with content-free events | `have` | `structlog` + `security/audit_guard.py` (channel 7) | A library cannot know what "content" is for this engine. |
| Rate limiting, metrics | N/A here | `slowapi`, `prometheus-client` in the root app | The engine is a library; the HTTP surface owns that (root `AGENTS.md`). |
| Runtime role provisioning | `have` | `security/provisioning.py`, `security/rls.sql` | Least-privilege grants are a per-table matrix; no tool derives it safely. |

## 5. Reference engines: what was taken, what was refused

| Engine (license) | Taken | Refused, and why |
|---|---|---|
| **Hindsight** (MIT) | 4-arm Postgres recall shape, RRF, opt-in `reflect` | No RLS anywhere; `DateparserQueryAnalyzer` silently drops non-English temporal queries; `mental_models` hardcode `'english'` FTS. Copying the store would import those. |
| **Quipu** (MIT) | quote-grounded assertions; propose → isolated verify → deterministic check; statistical pattern test | Zig + embedded LatticeDB, single-node, ASCII `isAlphanumeric`, English-only rules in `temporal.zig` / `fact_updates.zig`. Mechanism only. |
| **Veracium** (MIT) | authority ladder, quarantine, specifics grounding, evidence-grounded abstention | Single-tenant SQLite with English-only regexes and a static `DEFAULT_RELATIONS`. Mechanism only, and the ladder re-expressed as a generated column. |
| **Graphiti / Zep** (Apache-2.0) | cascaded entity resolution; non-destructive bi-temporal invalidation | Separate graph database, brute-force vector scans, `O(N)` LLM default reranker, `[^a-z0-9' ]` ASCII stripping, `remove_episode` leaving invalidation behind. |
| **Honcho** (AGPL-3.0) | *design only*: cite-first async derivation, zero-LLM hot path vs opt-in dialectic, Dreamer workers | **No code, ever**: AGPL-3.0 is viral for a network service, and no bi-temporal exclusion or audit clocks. Ideas are not licensable; text is. |
| **Cognee** (Apache-2.0) | `FactConflict` review concept | Three-database stack (SQL + LanceDB subprocess + Kuzu subprocess), LLM on default reads, English `query_router`, `[^a-z0-9]+` dedup. |
| **Letta Code** (Apache-2.0) | Git-worktree reflection concept | Markdown files in git: no structured temporal model, no multi-tenant governance, and "forgetting" cannot erase git history. |
| **Mem0** (Apache-2.0) | nothing adopted | ADD-only writes, no graph in OSS, no temporal timestamps, deleted plaintext retained in SQLite, no tenant auth on `get`/`delete(id)`. |
| **MemOS** (Apache-2.0) | fast/slow dual-ingest *pattern* | Binary EN/ZH tokenisation that drops Arabic and Cyrillic; `LoRAMemory` is a placeholder stub; maintenance unimplemented. |
| **Memobase** (Apache-2.0) | token-buffered flush *pattern* | Calls an LLM on the read path, destructive profile overwrite, deletes raw chat blobs by default (no provenance at all). |
| **MIRIX** (Apache-2.0) | a warning, quoted in its own comments: naive flat-PG merging cost `-7.7` QA on LongMemEval-S | Heavy multi-agent ingest, Neo4j never deleted, `delete_user_memories` crosses orgs. |
| **LangMem** (MIT) | the observation that language neutrality is achievable by having no NLP heuristics at all | Destructive in-place overwrites; store-level namespaces only, no provenance or cascading deletion. |

## 6. Dependency decisions taken in this phase

| Dependency | Change | Reason |
|---|---|---|
| `pg_trgm` | **added** (PostgreSQL extension, bootstrapped in `public` by `migrate.EXTENSIONS`) | The only language-agnostic substring/similarity arm available in-core, with a usable GIN operator class. It ships with PostgreSQL, so it adds no package. |
| `pgvector` `halfvec` + HNSW | **used** (already a dependency; 0.8.7 pinned) | Halves slot storage, supports 3072 dimensions (which `vector` cannot index), and recall loss is under a point for cosine. |
| Provider SDKs (OpenAI, Anthropic, Google) | **refused** | `semantic/contracts.py` imports none. The engine stays provider-free; the deployment injects. |
| A graph database | **refused** | Would move association integrity outside RLS and the deletion closure. |
| A search server (Elastic/Meilisearch/ParadeDB) | **deferred to `W6`** | Only worth a second deployment step if a relevance study shows `'simple'` + trigram is insufficient. The benchmark harness (`scripts/bench_hybrid.py`) is what would answer it. |
| A reranker model | **deferred, seam shipped** | `engine.hybrid.Reranker`. |

## 7. What is genuinely custom, and why that is correct

Five things are written here and nowhere else, and each has a reason that survives the "could we have used X?" test:

1. **The governance substrate** — RLS matrix, exclusion constraints, provenance triggers, lifecycle table. This *is* the
   project; every alternative inspected fails at least one of them.
2. **The deletion closure and leak scanner** — no reference engine has unbounded recursive closure with an 11-channel
   verification and a keyed receipt.
3. **The channel router** — a pure function of an artifact's own tuple, so no score can launder a hypothesis into a fact.
4. **The open slot and its exclusion key** — the Python twin and the schema `CHECK` must agree, and the exclusion key
   must be the one the write path compares against.
5. **The statistical admission rule** — the `(s, k, u)` partial-identification bound with a dependence correction is the
   research contribution; there is nothing to import.

Everything else in this map is either an existing library, an extension, or a mechanism adapted from a reference engine
with its defects named.

## 8. What landed after the map was written (2026-11-21)

| capability | component | verdict | notes |
|---|---|---|---|
| HTTP transport | **build**: `host/` (`starlette` + `uvicorn`, optional extra) | *not reused* — there is no established engine-side HTTP contract to adopt, and a framework's own request model would be a second source of truth against `API_V2.md` | 18 end-to-end tests; `forbidden` covers absent and unreadable alike |
| deployment wiring | **build**: `api/wiring.wire_engine` + `UNWIRED_OPERATIONS` | *replaces* six hand-ordered `register_*` calls | a missing registration used to look like a permission bug |
| refutation | **build**: `api/lifecycle_handlers.refute` | *completes* a contract operation with no handler (`C-026`) | owns `inferred -> retired\|contested`, records a content-free audit row |
| asynchronous lane | **adapt**: `queue` + `semantic/slow_lane.py` | the durable queue already existed (`D-18`); the model call must live outside its transaction | `claim_one`/`complete_one`/`recover_stale` |
| model transport | **build**: `integrations/` (`wire`, `openai_compatible`, `anthropic_messages`, `provider`) | *refused* the provider SDKs: two protocols, no dependency tree, no vendor pin | learns per-model parameter refusals instead of encoding a matrix |
| embeddings | **reuse + run**: `onnxruntime` + `tokenizers` with a pinned `bge-m3` ONNX file | *reused* the runner the evaluation baselines already use | the provider serves no `/embeddings`; measured in `reports/retrieval/` |
| ranking | **build**: RRF over three arms, `hybrid.DENSE_SIMILARITY_FLOOR` | *refused* BM25 and a host-side reranker | both measured: recall@5 1.00 without them; an LLM rerank bought 0.69 -> 0.71 top-1 |

