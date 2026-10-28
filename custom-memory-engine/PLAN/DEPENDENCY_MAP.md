# Visual Dependency Map (`research-lab/`)

**Authority:** Visual companion to [`TASK_GRAPH.md`](TASK_GRAPH.md) (which holds the normative task descriptions, deliverables, and acceptance criteria).

---

## 1. Critical Path Overview

The project critical path runs through six sequential gates:

1. **Wave 1 (`P1` Decisions, open now):** `RL-010`, `RL-012`, `RL-013`, `RL-014`, `RL-016` (plus `RL-002`–`RL-005` in `P0`).
2. **Wave 2 (`P4` Core Schema & `P3` Harness):** `RL-040` (12-table DDL) + `RL-030` (`MemorySystemAdapter` harness) + `RL-020`–`RL-024` (benchmarks & suite generators).
3. **Wave 3 (`P2` Split Freeze & `P5`–`P7` Engine Subsystems):** `RL-026` (`FREEZE_LIST.md`, Gate `J5`) in parallel with `RL-042`–`RL-072` (supersession, hybrid RRF retrieval, 5-check gate, consolidation, and 7-channel lineage deletion).
4. **Wave 4 (`P9` Testing Matrix Gate):** `RL-090` (`T1`–`T5` archival harness, Gate `J6`) and `RL-091`–`RL-093` → `RL-094` (`tests/integration/report.md`, Gate `J7`).
5. **Wave 5 (`P10` Pilot Freeze Gate):** `RL-100` (dev-split pilot & parameter freeze, Gate `J8`).
6. **Wave 6 (`P10` Confirmatory Runs → `P11`–`P13`):** One-shot test runs `RL-101`–`RL-105` (Gate `J9`) → `RL-110` analysis & `RL-120`–`RL-121` PDT app → `RL-130`–`RL-132` thesis and release pack.

---

## 2. Phase P0 & P1 — Bootstrap & Open Decisions

```mermaid
flowchart LR
    RL001["RL-001 (Done)<br/>Approve Hybrid Plan"]
    RL002["RL-002<br/>Verify Scaffold"]
    RL003["RL-003<br/>pyproject & uv.lock"]
    RL004["RL-004<br/>docker-compose PG"]
    RL005["RL-005<br/>research-lab CI"]
    RL006["RL-006<br/>ruff/pyright/guard"]

    RL010["RL-010 (#1)<br/>Scope & Models (D-01..08, D-27)"]
    RL011["RL-011<br/>pilot_plan.md"]
    RL012["RL-012 (#2)<br/>PG & pgvector (D-18, D-25)"]
    RL013["RL-013 (#3)<br/>Engine Runtime (D-17, D-20)"]
    RL014["RL-014 (#4)<br/>A2b & Suite D (D-15, D-16)"]
    RL015["RL-015 (#5)<br/>H5 Ethics (D-19)"]
    RL016["RL-016 (#6)<br/>D-23, D-24, D-26"]

    RL001 --> RL002
    RL001 --> RL010
    RL001 --> RL012
    RL001 --> RL013
    RL001 --> RL014
    RL001 --> RL015
    RL001 --> RL016

    RL002 --> RL003
    RL013 --> RL003
    RL002 --> RL004
    RL012 --> RL004
    RL003 --> RL006
    RL003 --> RL005
    RL004 --> RL005
    RL006 --> RL005

    RL010 --> RL011
    RL014 --> RL011
```

---

## 3. Phase P2 & P3 — Benchmarks, Splits, Harness, & Stats

```mermaid
flowchart LR
    RL003["RL-003"]
    RL010["RL-010 (#1)"]
    RL013["RL-013 (#3)"]
    RL014["RL-014 (#4)"]
    RL016["RL-016 (#6)"]

    RL020["RL-020<br/>LongMemEval Fetch"]
    RL021["RL-021<br/>LoCoMo Fetch"]
    RL022["RL-022<br/>Suites G & I Gen"]
    RL023["RL-023<br/>Suite D Gen"]
    RL024["RL-024<br/>Suite T Gen"]
    RL025["RL-025<br/>Benchmark Adapters"]
    RL026["RL-026 (Gate J5)<br/>Freeze Splits"]

    RL030["RL-030<br/>Harness & Adapter"]
    RL031["RL-031<br/>Baselines B0–B4"]
    RL032["RL-032<br/>Replay Cache"]
    RL033["RL-033<br/>Judge Validation"]
    RL034["RL-034<br/>Stats Module"]

    RL003 --> RL030
    RL013 --> RL030
    RL030 --> RL031
    RL010 --> RL031
    RL030 --> RL032
    RL030 --> RL033
    RL010 --> RL033
    RL016 --> RL033
    RL030 --> RL034

    RL016 --> RL020
    RL010 --> RL022
    RL016 --> RL022
    RL014 --> RL023
    RL016 --> RL023
    RL014 --> RL024
    RL016 --> RL024

    RL020 --> RL025
    RL021 --> RL025
    RL030 --> RL025

    RL022 --> RL026
    RL023 --> RL026
    RL024 --> RL026
    RL025 --> RL026
```

---

## 4. Phase P4–P9 — Engine Implementation & Testing Matrix Gate

```mermaid
flowchart LR
    RL040["RL-040<br/>12-Table DDL"]
    RL041["RL-041<br/>Migrations"]
    RL042["RL-042<br/>Supersession & Archival"]
    RL043["RL-043<br/>Query Predicates"]
    RL044["RL-044<br/>Clock & Queue"]

    RL050["RL-050<br/>Hybrid RRF Retrieval"]
    RL051["RL-051<br/>Analyzer & Renderer"]

    RL060["RL-060<br/>5-Check Gate"]
    RL061["RL-061<br/>Consolidation (C)"]

    RL070["RL-070<br/>Lineage Deletion"]
    RL071["RL-071<br/>RLS & Isolation"]
    RL072["RL-072<br/>Audit Log Guard"]

    RL090["RL-090 (Gate J6)<br/>T1–T5 Archival Suite"]
    RL091["RL-091<br/>Invariants 1–9"]
    RL092["RL-092<br/>Clock & Queue Tests"]
    RL093["RL-093<br/>Concurrency & HNSW"]
    RL094["RL-094 (Gate J7)<br/>§G Matrix Sign-Off"]

    RL040 --> RL041
    RL041 --> RL042
    RL041 --> RL043
    RL041 --> RL044

    RL043 --> RL050
    RL050 --> RL051

    RL042 --> RL060
    RL044 --> RL060
    RL050 --> RL060
    RL042 --> RL061
    RL060 --> RL061

    RL042 --> RL070
    RL050 --> RL070
    RL061 --> RL070
    RL040 --> RL071
    RL070 --> RL072

    RL042 --> RL090
    RL061 --> RL090
    RL070 --> RL090
    RL072 --> RL090

    RL042 --> RL091
    RL043 --> RL091
    RL060 --> RL091
    RL070 --> RL091
    RL071 --> RL091
    RL072 --> RL091

    RL044 --> RL092
    RL044 --> RL093
    RL050 --> RL093

    RL090 --> RL094
    RL091 --> RL094
    RL092 --> RL094
    RL093 --> RL094
```

---

## 5. Phase P10–P13 — Pilot Freeze, Confirmatory Runs, PDT App, & Release

```mermaid
flowchart LR
    RL011["RL-011<br/>pilot_plan.md"]
    RL026["RL-026 (Gate J5)<br/>Frozen Splits"]
    RL094["RL-094 (Gate J7)<br/>§G Matrix Green"]

    RL100["RL-100 (Gate J8)<br/>Dev Pilot & Freeze"]
    RL101["RL-101<br/>H1 Run (Suite T)"]
    RL102["RL-102<br/>H2 Run (LongMemEval)"]
    RL103["RL-103<br/>H3 Run (Suite G)"]
    RL104["RL-104<br/>Human κ Audit"]
    RL105["RL-105<br/>H4 Run (Suite D)"]

    RL110["RL-110<br/>Confirmatory Stats"]
    RL111["RL-111<br/>Cost & Latency"]

    RL120["RL-120<br/>PDT FastAPI"]
    RL121["RL-121<br/>PDT Inspector UI"]
    RL122["RL-122 (Optional)<br/>H5 Usability"]

    RL130["RL-130<br/>Final Thesis"]
    RL131["RL-131<br/>Reproducibility Pack"]
    RL132["RL-132<br/>Suite Release"]

    RL011 --> RL100
    RL026 --> RL100
    RL094 --> RL100

    RL100 --> RL101
    RL100 --> RL102
    RL100 --> RL103
    RL100 --> RL105

    RL101 --> RL110
    RL102 --> RL110
    RL103 --> RL110
    RL104 --> RL110
    RL105 --> RL110
    RL102 --> RL111

    RL120 --> RL121
    RL121 --> RL122

    RL110 --> RL130
    RL111 --> RL130
    RL121 --> RL130
    RL130 --> RL131
    RL110 --> RL132
```
