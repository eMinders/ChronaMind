# implementation/

**Scope:** the proposed memory-engine implementation.
**Phases:** P4 (schema + engine core), P5 (gate + lifecycle), P6 (retrieval + consolidation + lineage), P7 (deletion closure), P8 (fast/slow paths + job queue).
**Normative references:** [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §7, §8; [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §3; [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §D.4–§D.9.

## Planned layout (populated by `RL-040` onward)

```
implementation/
├── schema/          # Full-schema DDL (RL-040)
├── migrations/      # Up/down migrations (RL-041)
├── engine/          # Sub-modules decided in P4 design review (RL-042+)
│   ├── gate/              # Validation gate (RL-050)
│   ├── retrieval/         # Dense + lexical + RRF (RL-060)
│   ├── consolidation/     # Minimal consolidation + MEMORY_DERIVATION lineage (RL-061)
│   ├── deletion/          # §7.7 closure (RL-070)
│   ├── paths/             # Fast/slow paths + job queue (RL-080)
│   └── temporal_state_manager/  # Retrieval predicates (RL-043)
└── tests/
    ├── archival/    # T1 – T5 regression harness (RL-090)
    ├── concurrency/ # Replay-clock + exclusion-constraint under contention (RL-091)
    ├── isolation/   # Cross-user + RLS (RL-092)
    └── deletion/    # Deletion-closure in-boundary sweep (RL-093)
```

## Independence

- No file in this directory may import `app.*` from the main application. See [`../PLAN/INDEPENDENCE_BOUNDARY.md`](../PLAN/INDEPENDENCE_BOUNDARY.md) §3.
- The schema and migrations here are the Research Lab's own; they do not reuse `../../alembic/`.

## Status

**Not Started.** Open decisions `D-PG-VER` (#25) and `D-ENG-LANG` must be resolved first (see [`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md)).
