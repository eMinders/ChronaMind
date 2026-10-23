# Dependency map — view of TASK_GRAPH.md

**Authority:** [TASK_GRAPH.md](TASK_GRAPH.md) is canonical for task prerequisites. This map is a synchronized visual view, not a second task system. RL-001 team acceptance is satisfied by owner confirmation (PROJECT_STATUS.md §6); individual review responses and reviewed-revision audit details were not supplied. Existing publication/claim and task-specific prerequisites remain; Hybrid alone does not mark implementation Done. Actual state: [PROJECT_STATUS.md](PROJECT_STATUS.md).

**Legend:** `-->` blocking (accepted before start); `-.->` non-blocking (preparation may start, acceptance waits); `==>` conditional application-track prerequisite. Dependencies on decisions and research/ethics conditions are in [DECISIONS_REGISTER.md](DECISIONS_REGISTER.md). Solid arrows describe implementation prerequisites, not published issues. No Mermaid parser/rendering is claimed by the edge/cycle check.

## 0. Phase acceptance and parallel preparation

Roadmap §C defines phases; TASK_GRAPH.md §A defines their accepted deliverables. Preparatory work follows task prerequisites below, not a blanket requirement that every pilot-dependent P1 value be frozen before P2/P3 can start. Full phase completion still needs all acceptance criteria. P8 follows P7 for deletion/epoch integration; P9 gates the pilot; P10 freeze gates test experiments. P12 is parallel to research once engine integration is stable; release depends on P12 only for the mandatory application track.

## 1. Foundation and decisions

```mermaid
flowchart TD
    RL001[RL-001 Done owner-confirmed approval]
    RL001 --> RL002[RL-002 Scaffold acceptance]
    RL010[RL-010 Scope budget archival decisions]
    RL012[RL-012 PG and extension target]
    RL013[RL-013 Runtime decision]
    RL014[RL-014 A2b and D cardinality]
    RL015[RL-015 Ethics]
    RL016[RL-016 Own suites and G release]
    RL002 --> RL003[RL-003 Manifest lock ignore rules]
    RL013 --> RL003
    RL010 -.-> RL013
    RL020[RL-020] -.-> RL011[RL-011 Pilot procedures]
    RL030[RL-030] -.-> RL011
    RL003 --> RL004[RL-004 Independence guard]
    RL003 --> RL005[RL-005 Dedicated Lab CI]
    RL004 --> RL005
    RL012 --> RL005
    RL001 --> RL006[RL-006 Publication gate]
    RL002 --> RL006
    RL003 --> RL006
    RL004 --> RL006
    RL005 --> RL006
    RL010 --> RL011[RL-011 Pilot procedures]
```

## 2. Benchmarks and own suites

```mermaid
flowchart TD
    RL002[RL-002] --> RL020[RL-020 LongMemEval mirror]
    RL003[RL-003] --> RL020
    RL002 --> RL021[RL-021 LoCoMo mirror]
    RL003 --> RL021
    RL016 --> RL022[RL-022 G generator]
    RL014[RL-014] -.-> RL022
    RL014 --> RL023[RL-023 D generator]
    RL016 --> RL023
    RL014 -.-> RL024[RL-024 T generator]
    RL016 --> RL024
    RL020 --> RL025[RL-025 Scoring adapters]
    RL021 --> RL025
    RL030[RL-030] --> RL025
    RL022 --> RL026[RL-026 Split freeze]
    RL023 --> RL026
    RL024 --> RL026
    RL025 --> RL026
```

The dependency chain is `RL-002 + RL-013 → RL-003 → RL-020/RL-021`. RL-001 is already owner-confirmed Done; it does not substitute for accepted RL-002 or the RL-013 runtime decision. Both mirrors require scaffold **and** manifest/ignore rules to be accepted. They may run in parallel after those prerequisites. RL-016 is not a prerequisite for either mirror.

## 3. Harness, baselines, judge, statistics

```mermaid
flowchart TD
    RL003[RL-003] --> RL030[RL-030 Harness skeleton]
    RL013[RL-013] --> RL030
    RL030 --> RL031[RL-031 B0 B1 B2 B2h B3]
    RL014[RL-014] -.-> RL031
    RL031 --> RL032[RL-032 Conditional B4 qualification]
    RL011[RL-011] --> RL032
    RL030 --> RL033[RL-033 Dev judge validation]
    RL020[RL-020] --> RL033
    RL011 --> RL033
    RL030 --> RL034[RL-034 Statistics]
```

D-20 governs actual paid/model calls, not a dummy-fixture skeleton. RL-011 defines the dev qualification/validation procedure before RL-032/033. Final B4 inclusion and judge/sample freeze remain in RL-100; no reverse edge from that final freeze to constructing its prerequisites.

## 4. Engine implementation

```mermaid
flowchart TD
    RL010[RL-010] --> RL040[RL-040 Full schema]
    RL012[RL-012] --> RL040
    RL013[RL-013] --> RL040
    RL040 --> RL041[RL-041 Migrations]
    RL040 --> RL042[RL-042 Transactions]
    RL040 --> RL043[RL-043 Predicates]
    RL040 --> RL061[RL-061 Consolidation lineage]
    RL042 --> RL061
    RL041 --> RL050[RL-050 Gate]
    RL042 --> RL050
    RL042 --> RL051[RL-051 Conflict wiring]
    RL050 --> RL051
    RL043 --> RL060[RL-060 Dense lexical RRF]
    RL051 --> RL060
    RL042 --> RL070[RL-070 Deletion closure]
    RL050 --> RL070
    RL060 --> RL070
    RL061 --> RL070
    RL041 --> RL080[RL-080 Async queue paths]
    RL050 --> RL080
    RL060 --> RL080
    RL070 --> RL080
    RL080 --> RL081[RL-081 Replay and lag]
```

Full-schema concurrency feasibility D-18 is evaluated against this implementation in RL-091; it cannot require RL-091 completion before the DDL it needs is authored. The mandatory exclusion constraint is retained. A conflicting fallback is escalated through spec §13, not adopted silently.

## 5. Integration and security validation

```mermaid
flowchart TD
    RL010[RL-010 D-27 decision] --> RL090[RL-090 T1 T5]
    RL040[RL-040] --> RL090
    RL041[RL-041] --> RL090
    RL042[RL-042] --> RL090
    RL061[RL-061] --> RL090
    RL070[RL-070] --> RL090
    RL040 --> RL091[RL-091 Concurrent writers replay]
    RL080[RL-080] --> RL091
    RL081[RL-081] --> RL091
    RL040 --> RL092[RL-092 RLS isolation]
    RL050[RL-050] --> RL092
    RL070 --> RL093[RL-093 Deletion sweep]
    RL090 --> RL094[RL-094 Full G matrix signoff]
    RL091 --> RL094
    RL092 --> RL094
    RL093 --> RL094
```

RL-094 covers **all** mandatory roadmap §G rows, including end-to-end and queue tests, not just the four named test tasks. Waivers require documented approval where the contract permits them; do not use a waiver to fabricate invariant-10 or H4 evidence.

## 6. Pilot, experiments, reports, release

```mermaid
flowchart TD
    RL010[RL-010] --> RL100[RL-100 Dev pilot and final freeze]
    RL011[RL-011] --> RL100
    RL026[RL-026] --> RL100
    RL031[RL-031] --> RL100
    RL033[RL-033] --> RL100
    RL034[RL-034] --> RL100
    RL094[RL-094] --> RL100
    RL100 --> RL101[RL-101 H1]
    RL024[RL-024] --> RL101
    RL051[RL-051] --> RL101
    RL100 --> RL102[RL-102 H2]
    RL020[RL-020] --> RL102
    RL025[RL-025] --> RL102
    RL060[RL-060] --> RL102
    RL100 --> RL103[RL-103 H3]
    RL022[RL-022] --> RL103
    RL050[RL-050] --> RL103
    RL100 --> RL105[RL-105 H4]
    RL023[RL-023] --> RL105
    RL070[RL-070] --> RL105
    RL061[RL-061] --> RL105
    RL101 --> RL110[RL-110 Statistical reports]
    RL102 --> RL110
    RL103 --> RL110
    RL105 --> RL110
    RL034 --> RL110
    RL042[RL-042] --> RL120[RL-120 PDT]
    RL043[RL-043] --> RL120
    RL050 --> RL120
    RL060 --> RL120
    RL061 --> RL120
    RL070 --> RL120
    RL080[RL-080] --> RL120
    RL101 --> RL130[RL-130 Release]
    RL102 --> RL130
    RL103 --> RL130
    RL105 --> RL130
    RL110 --> RL130
    RL094 --> RL130
    RL120 ==> RL130
    RL110 --> RL131[RL-131 Final report defense]
    RL130 --> RL131
```

## 6.1 Complete explicit task-edge view

This diagram enumerates every explicit task prerequisite in TASK_GRAPH.md, expanding RL-006’s RL-002-through-RL-005 range. `-->` denotes blocking, `-.->` denotes non-blocking preparation/awareness, and `==>` denotes a conditional prerequisite. RL-001 is not repeated as a global prerequisite on every task; the current global approval status is recorded in PROJECT_STATUS.md §6.

```mermaid
flowchart LR
    RL001 --> RL002
    RL002 --> RL003
    RL002 --> RL006
    RL002 --> RL020
    RL002 --> RL021
    RL003 --> RL004
    RL003 --> RL005
    RL003 --> RL006
    RL003 --> RL020
    RL003 --> RL021
    RL003 --> RL030
    RL004 --> RL005
    RL004 --> RL006
    RL005 --> RL006
    RL010 --> RL011
    RL010 -.-> RL013
    RL010 --> RL040
    RL010 --> RL090
    RL010 --> RL100
    RL011 --> RL032
    RL011 --> RL033
    RL011 --> RL100
    RL012 --> RL005
    RL012 --> RL040
    RL013 --> RL003
    RL013 --> RL030
    RL013 --> RL040
    RL014 -.-> RL022
    RL014 --> RL023
    RL014 -.-> RL024
    RL014 -.-> RL031
    RL016 --> RL022
    RL016 --> RL023
    RL016 --> RL024
    RL020 -.-> RL011
    RL020 --> RL025
    RL020 --> RL033
    RL020 --> RL102
    RL021 --> RL025
    RL022 --> RL026
    RL022 --> RL103
    RL023 --> RL026
    RL023 --> RL105
    RL024 --> RL026
    RL024 --> RL101
    RL025 --> RL026
    RL025 --> RL102
    RL026 --> RL100
    RL030 -.-> RL011
    RL030 --> RL025
    RL030 --> RL031
    RL030 --> RL033
    RL030 --> RL034
    RL031 --> RL032
    RL031 --> RL100
    RL033 --> RL100
    RL034 --> RL100
    RL034 --> RL110
    RL040 --> RL041
    RL040 --> RL042
    RL040 --> RL043
    RL040 --> RL061
    RL040 --> RL090
    RL040 --> RL091
    RL040 --> RL092
    RL041 --> RL050
    RL041 --> RL080
    RL041 --> RL090
    RL042 --> RL050
    RL042 --> RL051
    RL042 --> RL061
    RL042 --> RL070
    RL042 --> RL090
    RL042 --> RL120
    RL043 --> RL060
    RL043 --> RL120
    RL050 --> RL051
    RL050 --> RL070
    RL050 --> RL080
    RL050 --> RL092
    RL050 --> RL103
    RL050 --> RL120
    RL051 --> RL060
    RL051 --> RL101
    RL060 --> RL070
    RL060 --> RL080
    RL060 --> RL102
    RL060 --> RL120
    RL061 --> RL070
    RL061 --> RL090
    RL061 --> RL105
    RL061 --> RL120
    RL070 --> RL080
    RL070 --> RL090
    RL070 --> RL093
    RL070 --> RL105
    RL070 --> RL120
    RL080 --> RL081
    RL080 --> RL091
    RL080 --> RL120
    RL081 --> RL091
    RL090 --> RL094
    RL091 --> RL094
    RL092 --> RL094
    RL093 --> RL094
    RL094 --> RL100
    RL094 --> RL130
    RL100 --> RL101
    RL100 --> RL102
    RL100 --> RL103
    RL100 --> RL105
    RL101 --> RL110
    RL101 --> RL130
    RL102 --> RL110
    RL102 --> RL130
    RL103 --> RL110
    RL103 --> RL130
    RL105 --> RL110
    RL105 --> RL130
    RL110 --> RL130
    RL110 --> RL131
    RL120 ==> RL130
    RL130 --> RL130REPRO[RL-130-REPRO]
    RL130 --> RL131
    RL130REPRO --> RL131[RL-131]
```

## 7. Execution sequence and gates

1. **Now:** RL-001 Done by owner confirmation; actual team-reviewed hash/date and individual responses unavailable. Follow current readiness/handoff in PROJECT_STATUS.md §10; record real identities/availability and local claims, not fabricated approval comments.
2. **First wave:** RL-002 remains In Review for separate scaffold acceptance/merge; RL-010/012/013/014/015/016 are Ready for decision preparation, with RL-013's awareness handoff to RL-010 retained. Their six GitHub issues are published and unassigned. Supervisor/ethics outcomes and compatibility acceptance are not guaranteed.
3. **Foundation:** RL-002 + RL-013 → RL-003 → RL-004; RL-012 + RL-003/004 → RL-005 → separately authorized RL-006. Dedicated-file RL-005 additionally needs the boundary §5 supervisor-approved exception; existing §4 allows only an added job. RL-001 review is closed; technical readiness is not a claim. Use the local bootstrap procedure in CONTRIBUTING.md §3 for claims until publication.
4. **Branches:** mirrors after RL-003; own generators after their research decisions; RL-030 after runtime/manifest. RL-025 requires both mirrors and harness interface. Harness/baselines/stats may run alongside engine tasks once each prerequisite is accepted.
5. **Engine:** RL-040 → migrations/transactions/predicates; lineage after transactions; gate → conflict → retrieval; lineage/retrieval/gate → deletion; deletion + retrieval/gate → async → replay verification.
6. **Validation:** archival T1–T5 (including real deletion), concurrency/replay, RLS/isolation, deletion sweeps and all §G integration rows → RL-094 accepted.
7. **Pilot:** RL-010/011/026/031/033/034/094 accepted; dev data only, budget and scope settled; B4 qualification per D-21 when attempted; final declared values, versions, models, prompts, judge, splits, analysis and aggregation freeze signed before any H1–H4 test scoring.
8. **Experiments/release:** frozen RL-100 → four declared experiments → statistical/error reports → release → independent reproduction → final report. PDT is parallel once engine stable and conditional for release track. No post-hoc choice of runs or thresholds.

## 8. Review gates

RL-001 owner-confirmed team acceptance (satisfied); RL-002 separate scaffold acceptance/merge; RL-006 explicit publication authorization; RL-094 mandatory matrix acceptance; RL-100 signed final experimental freeze; RL-130-REPRO independent reproduction before RL-131. S-07 is not an additional architecture gate; see owner record. Approval provenance is canonical in PROJECT_STATUS.md §6, not this map.

## 9. Consistency verification

The owner-planning pass checks all explicit task inbound edges against this map, full issue drafts and compact source material before consolidation, and detects cycles using a standard-library topological traversal. Actual results and limitations are in [CHANGELOG.md](../CHANGELOG.md). No diagram-rendering or runtime validation is implied. Research/decision conditions, conditional application-track edge and non-exhaustive downstream handoff lists are distinguished from blocking task edges.
