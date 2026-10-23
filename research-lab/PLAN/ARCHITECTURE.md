# ARCHITECTURE.md — Research Lab layout and rationale

**Status:** canonical engineering architecture for the Research Lab. Hybrid is owner-selected for implementation planning; evidence and S-07 disposition are recorded in [F-01](../review/FEASIBILITY_REVIEW_DECISIONS.md). No new Frozen research decision. RL-001 team acceptance is owner-confirmed; individual response and reviewed-revision audit limitations are in PROJECT_STATUS.md §6. RL-002 separate scaffold acceptance/merge remains pending.
**Normative references:** [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §7, §8, §10; [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §3; [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §C, §D, §K.
**Companion document:** [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md).

This document records the Lab layout that this initialization actually creates, the trade-offs behind it, and the parts that are deliberately deferred.

---

## 1. Design principles

1. **Layout follows the roadmap, not a generic template.** Every top-level directory corresponds to a phase family in [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §C.
2. **Minimal scaffolding now; implementation later.** At initialization each code directory contains a `README.md` describing its contract, its roadmap phase, its acceptance criteria, and the task IDs that populate it. No speculative Python modules, no placeholder engine code, no fake results.
3. **One authoritative progress source.** [`PROJECT_STATUS.md`](PROJECT_STATUS.md) is the only hand-updated progress document. GitHub Issues (once published) carry the fine-grained claim/handoff state. The roadmap §K execution tracker in [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) remains the research-level checkpoint record and is updated only on phase gates.
4. **Reuse before duplication.** The Research Lab reuses the existing authoritative documents at `research-lab/*.md` (proposal, checklist, audit, ledger, changelog, roadmap, technical specification, traceability matrix). It does not duplicate them under `PLAN/`.
5. **Reproducibility over cleverness.** Every experiment artefact lives under a timestamped `runs/<ts>/` subtree with a manifest and seed capture (roadmap §D.3 "Technical design"). No implicit state.
6. **Independence from the main app** is enforced per [`INDEPENDENCE_BOUNDARY.md`](INDEPENDENCE_BOUNDARY.md) §3.

---

## 2. Directory layout

```
research-lab/
├── Graduation_Project_Proposal.md         # Authoritative (unchanged)
├── Methodology_and_Consistency_Checklist.md
├── Research_and_Fact_Check_Audit.md
├── Source_Evidence_Ledger.md
├── CHANGELOG.md
├── PROJECT_ROADMAP.md
├── TECHNICAL_SPECIFICATION.md
├── TRACEABILITY_MATRIX.md
├── README.md
│
├── validation/                            # Spec-level validation (COMPLETE for reduced schema)
│   ├── bitemporal_checks.sql
│   ├── bitemporal_checks.output.txt
│   ├── bitemporal_log_inspection.txt
│   ├── cp_bounds.py
│   ├── cp_bounds.output.txt
│   ├── mechanical_checks.py
│   ├── mechanical_checks.output.txt
│   ├── mermaid_parse.cjs
│   ├── mermaid_parse.output.txt
│   └── document_hashes.txt
│
├── research/
│   ├── sources/                           # Source-evidence corpus (COMPLETE)
│   │   ├── fulltext/
│   │   └── pages/
│   └── benchmarks/                        # Phase P2 output (NOT STARTED)
│       └── README.md
│
├── implementation/                         # Phase P4–P8 output (NOT STARTED)
│   └── README.md
│
├── evaluation/                             # Phase P3, P10, P11 output (NOT STARTED)
│   └── README.md
│
├── application/                            # Phase P12 PDT demo (NOT STARTED)
│   └── README.md
│
├── reports/                                # Phase P11, P13 (NOT STARTED)
│   └── README.md
│
├── review/                                 # Advisory evidence, owner direction, documentation history
│   └── README.md
└── PLAN/                                   # Planning and execution package
    ├── ARCHITECTURE.md                     # (this file)
    ├── INDEPENDENCE_BOUNDARY.md
    ├── TASK_GRAPH.md                       # Actionable task breakdown
    ├── ASSIGNMENTS.md                      # Six-person assignment plan (provisional)
    ├── DEPENDENCY_MAP.md                   # Mermaid dependency graph + review gates
    ├── DECISIONS_REGISTER.md               # Mirror of §E Open decisions, with owner proposal
    ├── PROJECT_STATUS.md                   # Authoritative hand-updated progress board
    ├── CONTRIBUTING.md                     # Workflow, claim protocol, handoff protocol
    ├── GITHUB_SETUP.md                     # Proposed labels, milestones, Project fields
    ├── SCHEDULE.md                         # Deadline + proposed research schedule
    ├── templates/
    │   ├── issue_task.md                   # Issue template for a research task
    │   ├── issue_decision.md               # Issue template for an Open decision
    │   ├── issue_bug.md
    │   └── pull_request_template.md
    └── issue_drafts/                       # Unpublished local task bodies; readiness in PROJECT_STATUS
        ├── README.md
        ├── RL-001.md
        ├── RL-002.md
        └── ...
```

---

## 3. Module boundaries and why they exist

Each top-level technical directory maps to a phase and to a module boundary from the proposal.

| Directory | Phase | Proposal / Spec reference | Why it is a separate module |
|---|---|---|---|
| `validation/` | P0 | roadmap §B.2 | Spec-level checks that must pass before any claim; independent of the engine implementation so they can keep running during P4–P12. |
| `research/sources/` | P0 | ledger | Immutable source corpus with SHA-256 manifest; separated from benchmarks because benchmarks are external, versioned datasets with their own licences. |
| `research/benchmarks/` | P2 | roadmap §D.2 | LongMemEval, LoCoMo, and own suites `T`, `D`, `G` live here with licences, manifests, adapters, generators, splits. Separated from `evaluation/` so that datasets can be mirrored and hash-verified without pulling in the harness. |
| `implementation/schema/` | P4 | proposal §8, spec §3 | DDL only. Separated from the engine so that invariants 1–10 can be reviewed independently of application code and the T1–T5 SQL harness can run against the schema in isolation. |
| `implementation/migrations/` | P4 | roadmap §D.4 | Up/down migrations, versioned. Separated from `schema/` so that the DDL remains a readable spec. |
| `implementation/engine/` | P4–P8 | proposal §7 | The research engine. Sub-modules (gate, retrieval, consolidation, deletion, paths, temporal_state_manager) are created incrementally as their phase starts; not all pre-materialised at initialization to avoid dead stubs. |
| `implementation/tests/` | P9 | roadmap §G | Unit, regression, concurrency, security tests. The archival-precondition harness T1–T5 lives at `implementation/tests/archival/` and is a **blocker** for invariant 10 claims (traceability matrix row). |
| `evaluation/harness/` | P3 | roadmap §D.3 | Config layer, run manifest, scorer invocation. Separated from baselines so that any baseline can be swapped behind the same interface. |
| `evaluation/baselines/` | P3 | roadmap §D.3 | B0, B1, B2, B2h, B3 (Mem0 pinned), B4 (Graphiti conditional). Each adapter is isolated so that a baseline failure does not cascade. |
| `evaluation/judge/` | P3 | proposal §10.5 | LLM-judge harness and perturbation calibration. Separate module so the judge can be re-validated independently (per `[36]`). |
| `evaluation/stats/` | P3 | proposal §10.6 | Cluster bootstrap, sign-flip permutation, Holm correction, Clopper–Pearson. Separate module so that statistics can be re-run on archived raw scores without re-running experiments. |
| `evaluation/runs/<ts>/` | P10, P11 | roadmap §D.3 | Per-run output tree. Generated artefact; **not** committed in bulk (see §4 below). |
| `application/` | P12 | proposal §11 | PDT demonstration. Separated from `implementation/engine/` so that application work can proceed in parallel with evaluation work (per roadmap §C, P12 can run in parallel with P10). |
| `reports/` | P11, P13 | roadmap §J | H1–H4 reports, error analysis, final report. Separated from `evaluation/` because reports are human-authored narratives, not generated. |

### Decisions deferred by design

- **Engine implementation language.** The proposal and spec do not pin a language ("Python or chosen language" in roadmap §D.4). This is Open decision `D-ENG-LANG` (added in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md)). Python versions in earlier drafts are assumptions, not pins; RL-013 applies the compatibility evidence procedure in §7 without inheriting the main-app runtime.
- **PostgreSQL version.** Open decision #25 in roadmap §E (14, 16, 17, or 18). The reduced-schema SQL ran on 16.15. The decision is deferred to P1.
- **Internal engine sub-module names.** `fast_path/`, `slow_path/`, `temporal_state_manager/`, etc. are referenced in the traceability matrix but not yet created, because their final granularity depends on P4 design review.

---

## 4. What is deliberately NOT in Git

The Lab avoids committing generated or heavy artefacts; it commits manifests and hashes instead. The pattern is already used under `validation/` (`document_hashes.txt`, SQL output logs that are intentionally short and auditable).

| Artefact | Where | Why not committed |
|---|---|---|
| Benchmark raw files (LongMemEval, LoCoMo) | `research/benchmarks/<name>/data/` (gitignored) | Covered by their own licences; mirrored with `MANIFEST.txt` + SHA-256 hashes (roadmap §D.2 acceptance). |
| Per-run outputs | `evaluation/runs/<ts>/*.jsonl` (gitignored except manifests) | Potentially large; the manifest + score summary is committed, raw per-item answers are not. |
| PDF corpus | already handled under `research/sources/fulltext/` with `MANIFEST.txt`; kept in Git today for provenance | Preserved from P0; policy inherited. |
| Virtual environments, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/` | — | Standard. |

A dedicated `.gitignore` for `research-lab/` will be added by task `RL-003` to make this explicit. The repository-root `.gitignore` already covers Python caches.

---

## 5. Tooling

Chosen so the Lab is runnable with the minimum friction.

- **Package manager:** `uv` selected for independent lock/frozen installs; `command -v uv` returned no executable in this closure environment, so tool installation/version verification belongs to RL-003. The Lab's `pyproject.toml` is independent; it is **not** a workspace member of the main app's `pyproject.toml`.
- **Lint / format:** `ruff` (consistent with the main app's existing `.pre-commit-config.yaml` conventions, so contributors face one toolchain).
- **Type check:** `pyright` in standard mode (consistent with `make typecheck` convention in `AGENTS.md`).
- **Tests:** `pytest`; add `pytest-asyncio` when async tests first appear (including async database code in P4), not automatically in the initial dev-only manifest.
- **Database:** no current runtime version selected. PostgreSQL 16.15 is historical reduced-schema evidence only. D-25/RL-012 selects the newest verified compatible supported stable version, not an automatic default.
- **CI:** dedicated `.github/workflows/research-lab.yaml` is the selected engineering target under D-LAB-CI, not implemented. Boundary §4 currently permits only adding a Lab job to existing CI; dedicated-file hosting requires the §5 supervisor-approved exception before RL-005 implements it. Keep path-filtered Lab-only checks and an independent environment in either case.

Any tool added later must be justified in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md). No Lab dependency manifest or lockfile currently exists; the main-app manifest/lock is not the Lab environment. See §7 for version constraints and verification procedure.

---

## 6. Open architecture decisions

| ID | Decision | Status | Owner proposal | Blocks |
|---|---|---|---|---|
| `D-ENG-LANG` | Supported stable language/runtime target | Open; §7 verification required | Member 2 | RL-003/030/040 |
| `D-PG-VER` | PostgreSQL + extension target — roadmap item 25 | Open; §7 verification required | Member 4 | RL-005/040/041 |
| `D-ENGINE-SUBMOD` | Component contracts §8 | Owner-authorized engineering selection; APIs reviewed in P4 | Member 2 | Task-specific component handoffs |
| `D-LAB-CI` | Dedicated Lab-only workflow | Owner-selected target; boundary exception pending, not implemented | Member 1 | RL-005 prerequisites and boundary §5 approval |

Decision states and owners are canonical in [`DECISIONS_REGISTER.md`](DECISIONS_REGISTER.md); this table is a navigation view, not a second approval record.

## 7. Dependency selection and reproducible environments

**Owner policy (2026-10-23):** choose the latest stable, compatible, supportable technology at implementation start, not the highest version number. Read-only upstream metadata verification was performed in the closure pass below. No install/build/integration verification or dependency installation; no Lab pin is selected or certified compatible.

- RL-013 chooses the runtime before RL-003 creates the manifest. Python is the preferred ecosystem; 3.12 in proposal §9 and 3.13 in earlier Lab drafts are planning assumptions, not verified pins. Compare supported stable Python releases against actual OS/architecture and package `Requires-Python`, binary wheels/build tools and baseline constraints. Do not inherit the main-app `>=3.13` or its lockfile as compatibility evidence.
- RL-012 selects PostgreSQL **and a compatible pgvector extension candidate** together before database CI/DDL. Check support lifecycle, image/build availability, extension license and PG major compatibility, `btree_gist`, `tstzrange`, exclusion predicates, RLS restricted-role/BYPASSRLS behavior, SKIP LOCKED, advisory locks and transaction/replay clocks. Before acceptance run the reduced schema on the selected image, preserving the V2 files unchanged; full-engine/concurrency evidence still comes later.
- RL-003 creates independent `research-lab/pyproject.toml` and `uv.lock`: bounded direct requirements justified per component; exact resolved transitive versions/hashes in the lock; pin Python patch/runtime and uv version in reproducibility metadata. Use frozen-lock installs in CI/runs once implemented. Separate baseline environments/locks if incompatible constraints require them; record their differences rather than forcing silent parity.
- Pin PostgreSQL/extension container tags **and image digests**, model snapshots/prompts/decoding parameters, dataset hashes, baseline full commits, hardware and env metadata in run manifests. No `latest` image or unbounded environment at experimental freeze. Update pins only before freeze or through the declared deviations process.
- For each choice record dated upstream source URL/release metadata, actual installed/build target, constraints, alternatives, breaking changes, test evidence and fallback reason in DECISIONS_REGISTER.md and the owning task evidence. A package metadata check is not an installation/integration check. If newest stable breaks required behavior, use newest compatible stable with the reason recorded.

**Local evidence inspected on 2026-10-23:** the repository-root `pyproject.toml` declares Python `>=3.13`; its `uv.lock` records FastAPI 0.121.0, Pydantic 2.11.1, SQLModel 0.0.38, psycopg 3.3.2, Mem0 1.0.0, LangGraph 1.0.2, Langfuse 3.9.1 and tenacity 9.1.2. These were read from TOML, not installed or runtime-tested. They describe the main application only; they are neither Lab selections nor evidence of latest stable availability. Historical reduced-schema PostgreSQL 16.15 and full baseline source commits remain provenance in the ledger/review, not current target certification. No Lab pin is selected in this documentation pass.

**Closure metadata observations (2026-10-23; not selected pins):** inspected Linux x86_64/glibc 2.43 with Python 3.14.4. The upstream observations below are dated HTTP/PyPI facts only. Choose newest **verified compatible** stable at RL-012/013 acceptance; compare older supported alternatives when required behavior/wheels/baseline constraints fail.

| Source checked read-only | Observation | Missing acceptance evidence |
|---|---|---|
| `https://www.postgresql.org/support/versioning/` | Supported: 18.6 (EOL 2030-11-14), 17.11, 16.15, 15.19; 14.24 EOL 2026-11-12. Page advertises 19 RC1, not a stable target. | Actual image digest/build, pgvector extension install, ranges/exclusion/RLS/SKIP LOCKED/advisory/replay behavior. 18.6 first candidate, not pin. |
| `https://www.python.org/downloads/` | Lists stable 3.15.1 (2026-10-21), 3.14.9, 3.13.17. Local Python is 3.14.4, not a selected runtime. | Supported-lifecycle page returned HTTP 403; required packages/transitives/tooling/baselines/wheels on each target, breaking changes and actual builds. Do not pick 3.15.1 just by number. |
| `https://api.github.com/repos/pgvector/pgvector/tags?per_page=10` and commit-pinned README | Tag v0.8.7 at `f37c13f68b57d2c3472b2214fbcff699d6d34876`; README says Postgres 13+ and documents `0.8.7-pg18` images. `/releases/latest` returned 404; this is tag/documentation evidence, not a release API result. | Confirm image availability/digest/architecture, license and build/test behavior; no container pull or extension run. README SHA-256 `19cebadeeb8d3181d01ef00399674ec41bd2f0363850cdccc7685d4260c5a1c6`. |
| `https://pypi.org/pypi/{psycopg,psycopg-binary,pydantic,pydantic-core,pgvector}/json` | psycopg/binary 3.3.6, Pydantic 2.14.1 with core==2.50.1, Python pgvector client 0.5.1: metadata Requires-Python >=3.10. Binary psycopg and core provide cp314 manylinux x86_64 wheels. Client version is not server extension version. | Exact transitive resolution/hash/license and compatibility with 3.15/other candidate runtimes, driver/server behavior, baseline constraints and actual installs. Metadata/wheel filenames alone do not certify compatibility. |

Retrieved bytes were captured in session-local temporary storage, not over research evidence. PostgreSQL page SHA-256 `2cad77c248e575372245d33197385a7a3727dffdcebc87a3eb5972c149d3ed8a`; PyPI psycopg JSON `a4806149d2b8bcfcd0ba7b96f202a79d7e02818dd8e3525105e53417fa120cfd`. Python response was gzip-encoded and decoded for inspection. No Baseline head update, model choice, lockfile or final research threshold was made.

**Verification sources to use at implementation (recheck observations above):** Python release/status pages (`python.org/downloads/`, `devguide.python.org/versions/`); PostgreSQL support policy and major-version docs (`postgresql.org/support/versioning/`); pgvector tagged README/releases (`github.com/pgvector/pgvector`); PyPI release JSON/Requires-Python and upstream changelogs for FastAPI/Pydantic/driver/SQLModel/NumPy/SciPy/tooling; uv locking documentation (`docs.astral.sh/uv/`). Baseline source provenance remains in Source_Evidence_Ledger.md and saved commit/license pages; those dates are historical, not proof of current head.

## 8. Component contracts and integration

All rows are **planned**, not implemented. Derive exact schema and semantics from proposal §8 and spec §§3–8. DTOs include authenticated user scope; reject absent/mismatched scope before I/O. Every stored user-data relation and query follows invariant 7. Tests live under `implementation/tests/{schema,archival,concurrency,isolation,deletion}/`; never under a duplicate `engine/tests/` tree.

| Component / tasks | Inputs → outputs / responsibility | Dependencies and invariants | Acceptance / integration / failure behavior |
|---|---|---|---|
| Schema + migrations RL-040/041 | Proposal entities → versioned DDL and up/down migrations | RL-010/012/013; invariants 1–10 enforcement sites | Restricted app role, per-user keys, CHECK/exclusion/clock guards; reduced-schema parity on full schema; migration roundtrip logical schema/data equivalence (not physical DB byte identity); fail/rollback incomplete migration. |
| Temporal state manager RL-042/043 | Validated candidate/retraction and clock → atomic close-then-insert versions; scoped query/time → version sets | RL-040; invariants 2–6, 10; R1–R8 | Worked-example set equality, same-day/retroactive/held promotion, staging invisibility, audit visibility; T1/T2 rejection; use one transaction, reject invalid/future time parameters and unsupported writes, rollback conflicts. Canonical proposal predicates govern any shorthand ambiguity. |
| Gate + conflict detector RL-050/051 | Extracted candidate, literal user evidence, assertion mode, existing scoped versions → accept/hold/reject + relation/reason | RL-041/042; invariants 1–6 | All §7.5 categories, unsupported assistant guesses/instructions, uncertain holds, idempotent supersession; numeric confidence only after dev calibration. P/A3 and A4 switches do not change extraction or other treatment variables. |
| Retrieval + context RL-060 | Authenticated user, mode/reference time, query embedding/text → ranked version IDs then token-budgeted context | RL-043/051; invariants 6/7; spec hard predicates | Exact-per-user dense + FTS `ts_rank_cd` + RRF k=60; deterministic tie policy, A5/A6 clean channel removals; snapshots prevent partial writes; current context excludes historical and all ordinary context excludes superseded. Audit only inspector/API, not answer context. Epoch checks prevent post-delete assembly; no cache outside deletion contract. |
| Consolidation + lineage RL-061 | Accepted source versions → derived version + source edges | RL-040/042; invariants 1/7/8/10 | Cycle rejection, bidirectional DAG queries, same-user edges, mandatory provenance; rollback missing/invalid support; deletion cannot leave unchanged text embedding removed support. Optional persona off in confirmatory P. |
| Deletion RL-070 | Scoped memory/topic/all request → epoch, closure removal/re-derivation, content-free random-ID receipt | RL-042/050/060/061; invariant 8, R6 | All versions plus transitive derivations, embeddings/FTS/queue/cache covered atomically; seven **measurement channels** in proposal §10.3 mapped to storage, context, answers and logs. T5 + direct/indirect canaries + never-stated control; no content/hash in audit; source epoch recheck prevents races. Re-learning from independent turns and exclusions disclosed, no absolute erasure claim. |
| Async paths + replay RL-080/081 | Turn/job + authenticated user + clock mode → streamed answer and staged/committed writes | RL-041/050/060/**070**; invariants 3/7/8/9 | SKIP LOCKED, bounded tenacity backoff, durable idempotency, per-user ordering, dead-letter inspection; repeated jobs create one transition, other users progress on failures. One injected clock: implementation interface in P4, replay harness/instrumentation verified RL-081. No memory content in external observability. |
| Harness / baselines / judge / stats RL-030–034 | Config + pinned models, data/script and replayed extraction → answers/scores/cluster summaries/manifests | RL-003/013; adapters RL-025; D-20 for real calls | Dummy fixtures before paid calls; baseline processes/stores isolated from P; `ingest`, `retrieve`, `delete` where supported and shared scorer `(system, question, gold) → score`; unsupported capability explicit. Reproducible replay scores, not a claim of seeded hosted-LLM determinism. Human dev judge validation and perturbations before test use; cluster/statistic code per protocol. |
| PDT RL-120 | User actions → chat, inspector, evidence, correction, historical/audit picker and delete receipt | Stable RL-042/043/050/060/061/070/080 | Standalone Lab client; six core flows and cross-user tests; audit-as-of explicitly separate from ordinary historical conversation. No recruitment before D-19; optional polish must not crowd research. |

**Library allocation:** PostgreSQL ranges/`btree_gist`, pgvector exact search, FTS and recursive SQL supply primitives. ChronaMind owns schema/transactions, gate, hard temporal filtering, RRF/channel switches, DAG/deletion and epoch checks. Compatible typed DTO, driver, retry and statistical utilities may be selected independently under §7; no main-app imports or framework-defined memory/gate/deletion in P. Current INDEPENDENCE_BOUNDARY.md library restrictions need reconciliation before adding prohibited packages; see impact analysis §4.

**Data flow:** turn ingestion → fast-path query analysis → scoped hard filter → dense/lexical fusion → snapshot/epoch-checked context → LLM stream; slow job → schema-validated extractor → gate/relation → transactional version commit/evidence/lineage. Deletion shares the writer epoch/transaction discipline. Evaluation drains the queue before probes and measures lag separately. LLM/embedding provider retention, WAL/replicas/backups and delivered text keep the proposal's exclusions; content-bearing evaluation caches/traces remain subject to its boundary, not automatically exempt.

## 9. Fallback controls

Migrated from the former review architecture, with the specification as contract:

- **F1:** concurrency/constraint failure requires evidence from RL-091; investigate transaction ordering first. The proposal describes serialisable enforcement as a possible fallback, but spec §3.2 mandates exclusion constraints. Do **not** remove/weaken that MUST as a routine engineering choice: record the conflict and propose §13-controlled change if necessary.
- **F2:** retain exact-per-user search in confirmatory P. HNSW is scalability-only with recall-against-exact diagnostics; iterative scans are not permission to change the confirmatory retrieval treatment.
- **F3:** B4 qualification fails frozen-model end-to-end/cost condition → record measured obstacle and exclude B4 under D-21. No effect on confirmatory contrasts; B3 remains subject to its own readiness.
- **F4:** workload/cost pressure → de-scope optional retention/persona/usability/polish, not essential H1–H4 mechanisms. Replacing P with Graphiti/Mem0 is not a silent fallback.
- **Research change control:** spec §13 controls Frozen rows, invariants, rules, predicates, archival/deletion and acceptance criteria; record affected citations, fact-check audit, mechanical recheck and traceability update. Owner engineering authority does not waive this.

## 10. Ablation and validation boundaries

Migrated from the former review architecture; all implementation paths are planned. A2 **replaces supersession with overwrite**, removing superseded/valid-ended history while retaining the declared valid-time behavior (proposal §10.4). Merely disabling invalidation would produce a different treatment. P/A2/A3/A5/A6 reuse frozen recorded extraction outputs and unchanged non-treatment settings. A8 removes the target only and intentionally omits closure as the H4 positive control. Optional persona and retention ranking remain off in confirmatory P.

Engine tests do not require a live baseline; harness tests use dummy configurations without the engine. Experiments combine accepted components only after RL-094 and signed RL-100 freeze. Test locations are `implementation/tests/`, suite generators `research/benchmarks/own_*`, judge/statistics `evaluation/`. The existing `validation/cp_bounds.py` is standard-library arithmetic, **not** evidence that NumPy/SciPy is already installed or selected. Hosted calls are not made deterministic by setting a seed; deterministic fixture/replay scores and documented repeated-run aggregation implement reproducibility.

Baseline processes, credentials and stores are isolated from P; actual backend/version selections are verified in their own locks and manifests. Framework adapter interface is proposal §10.1 `ingest`, `retrieve`, `delete` where supported; the scorer interface is `(system, question, gold) → score`. They are distinct contracts. Unsupported capabilities are reported, not implemented by silently changing baseline internals.

Observability carries IDs, reason codes and metrics, not memory content. Provider retention and already-delivered text remain excluded as declared; any content-bearing project evaluation cache/trace is in-boundary and must be covered by deletion. Restricted-role, migration, transaction, retry/idempotency, queue/replay, epoch-race and seven-channel tests are mandatory as mapped by roadmap §G; reduced-schema logs do not replace them. Contract ambiguities requiring review are recorded in [impact analysis §4](../review/EXISTING_WORK_IMPACT_ANALYSIS.md#4-unresolved-contract-issues-and-actionable-blockers).

