# ChronaMind Research Lab

This directory contains the research specification, technical design, evaluation methodology, and implementation workspace for the graduation project: **A Bi-Temporal, Write-Governed, Lineage-Deletable Memory Engine for Long-Term Conversational Agents**.

---

## 1. Document Map & Single Sources of Truth

Each major topic has a single authoritative document:

| Topic | Authoritative File | Purpose |
|---|---|---|
| **Research Proposal & Hypotheses (`H1`–`H4`)** | [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) | Frozen V4 research contract: problem statement, related work, bi-temporal model (§7), data model (§8), evaluation methodology (§10), and deliverables (§12). |
| **Methodology & Consistency Checklist** | [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md) | Audit checklist verifying statistical rules, scope exclusions, and the `T1`–`T5` archival precondition contract. |
| **Technical Specification** | [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md) | Concrete implementation contract: DDL table schemas, `R1`–`R8` rules, SQL query predicates, validation gate, RRF fusion, deletion closure, and `MemorySystemAdapter` protocol. |
| **Project Roadmap & Testing Matrix** | [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) | Phases `P0`–`P13`, risk register (§F), mandatory testing matrix (§G), reproducibility rules (§H), and Definition of Done (§I). |
| **Traceability Matrix** | [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md) | End-to-end mapping from audit findings, database invariants (`1`–`10`), and hypotheses (`H1`–`H4`) to implementation tasks and verification artifacts. |
| **Revision History** | [`CHANGELOG.md`](CHANGELOG.md) | Chronological record of V1–V4 proposal revisions and repository structure updates. |

### Team Execution & Planning (`PLAN/`)

| Topic | File | Purpose |
|---|---|---|
| **Project Status & First-Wave Dispatch** | [`PLAN/PROJECT_STATUS.md`](PLAN/PROJECT_STATUS.md) | Current execution state, completed vs. pending work, and active first-wave issues (`RL-010`, `RL-012`–`RL-016`). |
| **System Architecture (Hybrid `D-ARCH`)** | [`PLAN/ARCHITECTURE.md`](PLAN/ARCHITECTURE.md) | Package layout (`implementation/`, `evaluation/`, `application/`), data flow, and tech stack pins. |
| **Independence Boundary** | [`PLAN/INDEPENDENCE_BOUNDARY.md`](PLAN/INDEPENDENCE_BOUNDARY.md) | Code, dependency, database, and CI isolation rules between `research-lab/` and the root `app/` service. |
| **Decisions Register (`D-01`–`D-27`)** | [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md) | Single registry of all open, supervisor-gated, pilot-derived, and frozen project decisions. |
| **Task Graph (`RL-001`–`RL-132`)** | [`PLAN/TASK_GRAPH.md`](PLAN/TASK_GRAPH.md) | Detailed task definitions, deliverables, acceptance criteria, and dependencies across Phases `P0`–`P13`. |
| **Visual Dependency Map** | [`PLAN/DEPENDENCY_MAP.md`](PLAN/DEPENDENCY_MAP.md) | Mermaid phase and task dependency graphs plus critical-path summary. |
| **Team Roles & Schedule** | [`PLAN/ASSIGNMENTS.md`](PLAN/ASSIGNMENTS.md), [`PLAN/SCHEDULE.md`](PLAN/SCHEDULE.md) | 5-member role ownership (`R-LEAD`, `R-ENGINE`, `R-METHOD`, `R-DB`, `R-APP`) and 24-week milestone schedule. |
| **Contributor & GitHub Workflow** | [`PLAN/CONTRIBUTING.md`](PLAN/CONTRIBUTING.md), [`PLAN/GITHUB_SETUP.md`](PLAN/GITHUB_SETUP.md) | Branching, PR review checklists, issue claiming workflow, and `gh` CLI issue management. |
| **Issue Drafts** | [`PLAN/issue_drafts/`](PLAN/issue_drafts/) | Ready-to-publish GitHub issue specifications for tasks `RL-001`–`RL-132`. |

### Research Reference & Literature Corpus (`reference/`)

Research agents and reviewers should consult [`reference/README.md`](reference/README.md) for:
- [`reference/Source_Evidence_Ledger.md`](reference/Source_Evidence_Ledger.md) — Citation-by-citation verification ledger (`[1]`–`[41]`) with per-source reading depth.
- [`reference/Research_and_Fact_Check_Audit.md`](reference/Research_and_Fact_Check_Audit.md) — V1–V4 audit trail and literature positioning analysis.
- [`reference/TECHNICAL_FEASIBILITY_REVIEW.md`](reference/TECHNICAL_FEASIBILITY_REVIEW.md) & [`reference/ARCHITECTURE_DECISION_MATRIX.md`](reference/ARCHITECTURE_DECISION_MATRIX.md) — Engineering feasibility review and architectural trade-off matrix.
- [`reference/sources/`](reference/sources/) — Offline paper and documentation corpus (`fulltext/` and `pages/`).

---

## 2. Directory Layout

```text
research-lab/
├── Graduation_Project_Proposal.md          # Normative V4 research proposal
├── Methodology_and_Consistency_Checklist.md # Normative V4 verification checklist
├── TECHNICAL_SPECIFICATION.md              # Concrete engineering & SQL specification
├── PROJECT_ROADMAP.md                      # Phases P0–P13, risks, and §G testing matrix
├── TRACEABILITY_MATRIX.md                  # Invariant, hypothesis, and deliverable traceability
├── CHANGELOG.md                            # V1–V4 and structural changelog
├── PLAN/                                   # Team execution plans, decisions, and issue drafts
│   ├── decisions/                          # Signed decision memos (produced in P1)
│   ├── issue_drafts/                       # Task specs (RL-001..RL-132)
│   └── templates/                          # GitHub issue and PR templates
├── reference/                              # Centralized research reference & source corpus
│   ├── README.md
│   ├── Source_Evidence_Ledger.md
│   ├── Research_and_Fact_Check_Audit.md
│   ├── TECHNICAL_FEASIBILITY_REVIEW.md
│   ├── ARCHITECTURE_DECISION_MATRIX.md
│   └── sources/
├── validation/                             # Executed V2/V4 design checks & mechanical verifiers
│   ├── bitemporal_checks.sql               # 43-assertion reduced-schema PostgreSQL check
│   ├── bitemporal_checks.output.txt        # Saved PostgreSQL 16.15 execution log
│   ├── cp_bounds.py                        # Exact Clopper–Pearson upper-bound script
│   ├── cp_bounds.output.txt                # Saved Clopper–Pearson output
│   ├── mechanical_checks.py                # Automated proposal/checklist/changelog verifier
│   └── document_hashes.txt                 # MD5 integrity baseline for core V4 documents
├── research/
│   └── benchmarks/                         # Benchmark fetchers, adapters, own suites (T, G, D, I)
├── implementation/                         # Bi-temporal memory engine package (P4–P9)
├── evaluation/                             # Evaluation harness, baselines, judge, stats (P3, P10)
├── application/                            # Personal Digital Twin (PDT) inspector API & UI (P12)
└── reports/                                # Thesis tables, figures, and reproducibility pack (P11, P13)
```

---

## 3. Current Execution Baseline & Verification Commands

### What Has Been Executed vs. Planned
- **Executed at proposal stage:**
  1. Reduced-schema bitemporal SQL verification (`validation/bitemporal_checks.sql` on PostgreSQL `16.15`, 43 assertions passed; see `validation/bitemporal_checks.output.txt`).
  2. Exact Clopper–Pearson scenario-level upper-bound calculation (`validation/cp_bounds.py`; see `validation/cp_bounds.output.txt`).
  3. Automated document consistency and citation SHA-256 verification (`validation/mechanical_checks.py`).
- **Not yet executed (scheduled in Phases `P1`–`P13`):** Full 12-table PostgreSQL schema (`RL-040`), `T1`–`T5` archival-precondition harness (`RL-090`), row-level security (`RL-071`), `SKIP LOCKED` worker queue (`RL-044`), hybrid `pgvector` + FTS retrieval (`RL-050`), validation gate (`RL-060`), lineage deletion closure (`RL-070`), benchmark ingestion, pilot freeze (`RL-100`), and confirmatory experiments `H1`–`H4` (`RL-101`–`RL-105`).

### Running the Verification Suite

Run from `research-lab/`:

```bash
# 1. Verify proposal, checklist, and changelog mechanical invariants
python3 validation/mechanical_checks.py \
  Graduation_Project_Proposal.md \
  Methodology_and_Consistency_Checklist.md \
  CHANGELOG.md

# 2. Verify Clopper-Pearson bound determinism
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt

# 3. Verify MD5 integrity of frozen V4 documents
md5sum -c validation/document_hashes.txt

# 4. Re-run the 43-assertion reduced-schema bitemporal check (requires Docker)
docker run --rm --name pg-bitemporal-check -e POSTGRES_PASSWORD=postgres -d postgres:16-alpine
docker exec -i pg-bitemporal-check psql -U postgres -v ON_ERROR_STOP=1 < validation/bitemporal_checks.sql
docker stop pg-bitemporal-check
```
