> **Direction notice (2026-10-31).** The project direction has been revised and **approved** (Project Owner, 2026-10-31; [`PLAN/V2/APPROVAL.md`](PLAN/V2/APPROVAL.md)): ChronaMind is being rebuilt as a **general-purpose cognitive memory engine** with a strict substrate / cognition / application separation. The canonical implementation plan is [`PLAN/V2/`](PLAN/V2/README.md). Roadmap implementation will be authorised separately. Everything below remains accurate as the **v1 record** (specification, benchmarks, harness, engine, evaluands) and as the v1.0.0 substrate baseline; the phase plan in `PROJECT_ROADMAP.md` and `PLAN/` is superseded for post-v1 work. `Graduation_Project_Proposal.md` is unchanged and still governs the `H1`–`H4` protocol.

---

# Custom Memory Engine (`custom-memory-engine/`)

Unified research specification, benchmark corpus, evaluation harness, and implementation workspace for **ChronaMind**: *A Bi-Temporal, Write-Governed, Lineage-Deletable Memory Engine for Long-Term Conversational Agents*.

All specifications, literature references, decision records, public/synthetic benchmarks, evaluation harnesses, and engine modules are consolidated in this single self-contained directory, maintaining strict code, dependency, database, and runtime isolation from the root `app/` service.

**Cross-domain entity rule (E-024):** entities are scope-local, so the same person has separate opaque ids in separate scopes. An explicit `link_entity` relation can connect those ids only when the caller can read both scopes; neither id's claims or attributes become readable from the other scope. This avoids a global identity index. See [`ARCHITECTURE_V2.md` §5.2](PLAN/V2/ARCHITECTURE_V2.md), [`API_V2.md` §4.5](PLAN/V2/API_V2.md), and the [cross-domain integration scenario](tests/integration/test_cross_domain_entity.py).

---

## 1. Completed & Verified Deliverables (`P0`, `P1`, and Wave 2 `#14`–`#19`)

| GitHub | Task | Canonical record / deliverable | Status / remaining gate |
|---|---|---|---|
| #1–#6 | `RL-010`, `RL-012`–`RL-016` | [Scope/models](decisions/RL-010-scope-and-models.md), [PostgreSQL/pgvector](decisions/RL-012-postgres-pgvector.md), [Runtime](decisions/RL-013-engine-runtime.md), [A2b/Suite D](decisions/RL-014-a2b-and-suite-d.md), [H5 ethics](decisions/RL-015-ethics-h5.md), [Paired D-23/24/26](decisions/RL-016-paired-d23-d24-d26.md) | Closed (owner-reviewed); supervisor & pilot freeze (`RL-100`) downstream |
| #9–#13 | `RL-002`–`RL-006` | Unified scaffold, `pyproject.toml`/`uv.lock`/`.env.example`, `compose.yaml`, CI workflow, AST boundary guard (`tests/test_boundaries.py`, `tests/integration/test_independence_boundary.py`) | Closed & hardened |
| #14 | `RL-011` pilot protocol | [Pre-registered pilot plan](evaluation/pilot_plan.md) & [`pilot_rules.py`](src/custom_memory_engine/pilot_rules.py) | Implemented & unit-tested |
| #15 | `RL-020` `LongMemEval` | [`longmemeval/fetch.py`](research/benchmarks/longmemeval/fetch.py), [README](research/benchmarks/longmemeval/README.md), [MANIFEST](research/benchmarks/longmemeval/MANIFEST.txt) | Verified on `oracle` & `S` (`500 / 30 / 56`) |
| #16 | `RL-021` `LoCoMo` | [`locomo/fetch.py`](research/benchmarks/locomo/fetch.py), [README](research/benchmarks/locomo/README.md), [MANIFEST](research/benchmarks/locomo/MANIFEST.txt) | Verified on `locomo10.json` (`10 / 1,986 / 4`) |
| #17 | `RL-022` Suites `G` & `I` | [`own_G/generator.py`](research/benchmarks/own_G/generator.py), [`own_I/generator.py`](research/benchmarks/own_I/generator.py) | Deterministic `dev` (`20`) & `test` (`50`) verified |
| #18 | `RL-023` Suite `D` | [`own_D/generator.py`](research/benchmarks/own_D/generator.py), [README](research/benchmarks/own_D/README.md), [MANIFEST](research/benchmarks/own_D/MANIFEST.txt) | Deterministic `dev` (`20`) & `test` (`50`) verified |
| #19 | `RL-024` Suite `T` | [`own_T/generator.py`](research/benchmarks/own_T/generator.py), [README](research/benchmarks/own_T/README.md), [MANIFEST](research/benchmarks/own_T/MANIFEST.txt) | Deterministic `dev` (`20`) & `test` (`50`) verified |

---

## 2. Document Map & Single Sources of Truth

| Topic | Authoritative File | Purpose |
|---|---|---|
| **Baseline adapters (`RL-031`)** | [`evaluation/baselines/README.md`](evaluation/baselines/README.md) | Six operational adapters, frozen assets, public factory, software verification and conditional B4 limits. |
| **Research Proposal & Hypotheses (`H1`–`H4`)** | [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) | Frozen V4 research contract: problem statement, related work, bi-temporal model (§7), data model (§8), evaluation methodology (§10), and deliverables (§12). |
| **Methodology & Consistency Checklist** | [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md) | Audit checklist verifying statistical rules, scope exclusions, and the `T1`–`T5` archival precondition contract. |
| **Technical Specification** | [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md) | Concrete implementation contract: DDL table schemas, `R1`–`R8` rules, SQL query predicates, validation gate, RRF fusion, deletion closure, and `MemorySystemAdapter` protocol. |
| **Project Roadmap & Testing Matrix** | [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) | Phases `P0`–`P13`, risk register (§F), mandatory testing matrix (§G), reproducibility rules (§H), and Definition of Done (§I). |
| **Traceability Matrix** | [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md) | End-to-end mapping from audit findings, database invariants (`1`–`10`), and hypotheses (`H1`–`H4`) to implementation tasks and verification artifacts. |
| **Revision History** | [`CHANGELOG.md`](CHANGELOG.md) | Chronological record of V1–V4 proposal revisions and repository structure updates. |
| **Decisions Register (`D-01`–`D-27`)** | [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md) | Single registry of all open, supervisor-gated, pilot-derived, and frozen project decisions. |
| **Task Graph (`RL-001`–`RL-132`)** | [`PLAN/TASK_GRAPH.md`](PLAN/TASK_GRAPH.md) | Detailed task definitions, deliverables, acceptance criteria, and dependencies across Phases `P0`–`P13`. |
| **Literature & Evidence Ledger** | [`reference/Source_Evidence_Ledger.md`](reference/Source_Evidence_Ledger.md), [`evidence/SOURCES.md`](evidence/SOURCES.md) | Citation-by-citation verification ledger (`[1]`–`[41]`) and P1 primary-source verification hashes. |

---

## 3. Unified Directory Layout

```text
custom-memory-engine/
├── REFACTORING.md                          # Handoff: architecture, decisions, benchmarks, open work
├── Graduation_Project_Proposal.md          # Normative V4 research proposal
├── Methodology_and_Consistency_Checklist.md # Normative V4 verification checklist
├── TECHNICAL_SPECIFICATION.md              # Concrete engineering & SQL specification
├── PROJECT_ROADMAP.md                      # Phases P0–P13, risks, and §G testing matrix
├── TRACEABILITY_MATRIX.md                  # Invariant, hypothesis, and deliverable traceability
├── CHANGELOG.md                            # V1–V4 and structural changelog
├── pyproject.toml / uv.lock                # Standalone CPython 3.12 dependency graph
├── compose.yaml                            # Isolated PostgreSQL 16.15 + pgvector + btree_gist
├── src/custom_memory_engine/               # THE engine: engine/, schema/, clock/, queue/
├── tests/                                  # Contract tests; tests/integration/ needs PostgreSQL
├── scripts/                                # bench_retrieval.py (measured latency)
├── decisions/                              # Phase P1 decision memos (RL-010, RL-012..RL-016)
├── evidence/                               # Source ledger, verification report, captured outputs
├── PLAN/                                   # Execution plans, decisions register, issue drafts
├── reference/                              # Literature audit, feasibility review, paper corpus
├── validation/                             # bitemporal_checks.sql, cp_bounds.py, mechanical_checks.py
├── research/benchmarks/                    # LongMemEval, LoCoMo, and Own Suites G, I, D, T
├── evaluation/                             # Pilot plan, harness, baselines, judge, stats
├── application/                            # Personal Digital Twin (PDT) inspector API & UI (P12)
└── reports/                                # Thesis tables, figures, measured benchmarks
```

---

## 4. Reproducing Verification

Install `uv` and CPython 3.12, then run from `custom-memory-engine/`:

```bash
cd custom-memory-engine
# Install the optional `baselines` extra so `pyright` can resolve the B3/B4
# vendor imports (mem0ai, graphiti-core, redis); the default sync omits them.
uv sync --frozen --extra baselines   # CPython 3.12.x from .python-version
uv run ruff check src tests research/benchmarks evaluation
uv run ruff format --check src tests research/benchmarks evaluation
uv run pyright
uv run pytest -q -m 'not integration'          # fast feedback: no database needed (seconds)

# Start disposable PostgreSQL 16.15 + pgvector 0.8.7 + btree_gist 1.7 container
# (durability is off in compose.yaml: the data is throwaway and the suite runs about twice as fast):
docker compose --env-file .env -p cme-verify up -d --wait
uv run --env-file .env pytest -q -n auto        # the full suite, one worker per core (what CI `verify` runs)
uv run --env-file .env pytest -q tests/integration/test_ingest_db.py   # one module, serial
```

How the suite stays fast without sharing state: each xdist worker gets its own database (`conftest.py`); the
production migration runner migrates a template from it once; and each database test runs in a private clone of
that template (`tests/integration/schema.py::engine_database`, about 0.03 s instead of a 0.9 s migration). Clones,
templates, worker databases and the roles a test grants are all dropped afterwards. Use `engine_database()` for any
new test that needs the current schema; `apply_migrations(through=...)` is for tests of partial migration chains.

```bash

# Run reduced-schema bitemporal SQL verification (43 assertions):
docker compose --env-file .env -p cme-verify exec -T db psql -U cme_test -d cme_compatibility \
  -v ON_ERROR_STOP=1 < validation/bitemporal_checks.sql
docker compose --env-file .env -p cme-verify down -v

# Run proposal/checklist/changelog mechanical checks, MD5 integrity, and Clopper-Pearson check:
md5sum -c validation/document_hashes.txt
python3 validation/mechanical_checks.py Graduation_Project_Proposal.md \
  Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt
```

See [verification results and limitations](evidence/VERIFICATION.md).
