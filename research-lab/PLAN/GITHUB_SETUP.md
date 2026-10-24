# GitHub Issues, Labels, & Milestones Setup (`research-lab/`)

**Scope:** Reference for managing GitHub milestones, labels, and issues for the Research Lab using the GitHub CLI (`gh`).

---

## 1. Label Taxonomy

Keep issue labels concise and actionable. Every Research Lab issue uses:

| Label | Category | When to Apply |
|---|---|---|
| `decision` | Kind | Phase P1 architecture, methodology, or scope decision tasks (`RL-010`, `RL-012`–`RL-016`). |
| `enhancement` | Kind | Implementation, benchmark, harness, or experiment tasks (`RL-002`–`RL-006`, `RL-011`, `RL-020`–`RL-132`). |
| `documentation` | Kind | Documentation, report, or thesis tasks (`RL-110`, `RL-130`). |
| `bug` | Kind | Defect in schema, engine, or evaluation harness. |
| `ready` | Status | All prerequisites (`Blocked By`) are complete; can be claimed immediately. |
| `paired` | Coordination | Co-owned task requiring two team members to sign off together (e.g., `RL-016` `#6`). |

*(Avoid adding redundant meta-labels such as `help wanted` or `question` alongside `decision` + `ready` unless requesting external assistance.)*

---

## 2. Milestones (`P0`–`P13`)

Each phase in [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §C maps to one GitHub milestone:
- `P0 Repository, environment, and CI`
- `P1 Resolve open decisions`
- `P2 Benchmarks and own suites`
- `P3 Harness, baselines, judge, statistics`
- `P4 Schema and engine core`
- `P5 Retrieval, fusion, rendering`
- `P6 Validation gate and consolidation`
- `P7 Deletion and security`
- `P8 Optional components`
- `P9 Testing matrix`
- `P10 Pilot and experiments`
- `P11 Reporting and analysis`
- `P12 Personal Digital Twin application`
- `P13 Final packaging`

---

## 3. Published vs. Draft Issues

- **Currently Published on GitHub (`P1 Resolve open decisions`):**
  - `#1` — `[RL-010] Prepare research-scope and supervisor decision records`
  - `#2` — `[RL-012] Verify PostgreSQL and pgvector compatibility evidence`
  - `#3` — `[RL-013] Select and verify the engine runtime`
  - `#4` — `[RL-014] Prepare A2b and Suite D cardinality decisions`
  - `#5` — `[RL-015] Determine ethics requirements for the optional usability study`
  - `#6` — `[RL-016] Prepare paired D-23/D-24 and suite-release decisions`
- **Local Issue Drafts ([`issue_drafts/`](issue_drafts/)):**
  - Individual draft files (`RL-001.md`–`RL-026.md`, `RL-030.md`, `RL-040.md`, `RL-090.md`, `RL-094.md`, `RL-100.md`, `RL-105.md`) and consolidated phase specs (`issue_drafts/_bulk_tasks.md`) are maintained in sync with [`TASK_GRAPH.md`](TASK_GRAPH.md).
  - Publish subsequent waves as their upstream blockers close using:
    ```bash
    gh issue create \
      --title "[RL-xxx] <Title>" \
      --milestone "<Milestone Name>" \
      --label "enhancement,ready" \
      --body-file research-lab/PLAN/issue_drafts/RL-xxx.md
    ```
