# research/benchmarks/

**Scope:** mirrored public benchmarks and own-suite generators used by the H1–H4 experiments.
**Phase:** P2.
**Normative references:** [`../../Graduation_Project_Proposal.md`](../../Graduation_Project_Proposal.md) §10.2; [`../../PROJECT_ROADMAP.md`](../../PROJECT_ROADMAP.md) §D.2; [`../../reference/Source_Evidence_Ledger.md`](../../reference/Source_Evidence_Ledger.md) §C.

## Planned layout

```
research/benchmarks/
├── longmemeval/     # MIT; pinned commit 98d7416 (19 Sep 2025)
│   ├── MANIFEST.txt
│   ├── LICENSE
│   ├── README.md
│   ├── adapter.py   # Written in RL-025
│   └── data/        # Gitignored
├── locomo/          # CC BY-NC 4.0; locomo10.json
│   ├── MANIFEST.txt
│   ├── LICENSE
│   ├── README.md
│   ├── adapter.py
│   └── data/        # Gitignored
├── own_T/           # Temporal suite (RL-024)
│   ├── generator.py
│   ├── MANIFEST.txt
│   └── README.md
├── own_D/           # Deletion suite with canaries (RL-023)
│   └── ...
├── own_G/           # Gate / injection suite (RL-022)
│   └── ...
├── own_I/           # Cross-user isolation suite (RL-022)
│   └── ...
└── splits/          # Frozen dev/test splits & Gate J5 FREEZE_LIST.md (RL-026)
    ├── FREEZE_LIST.md
    ├── freeze.py
    ├── longmemeval.yaml
    ├── locomo.yaml
    ├── own_T.yaml
    ├── own_G.yaml
    ├── own_D.yaml
    └── own_I.yaml
```

## Hashes and oracle counts

Per the Source Evidence Ledger §C:

- **LongMemEval**: 30 abstention, 56 single-session-assistant, 470 answerable (cleaned release at commit `98d7416`).
- **LoCoMo**: 10 conversations, 1,986 questions; four questions have empty evidence.

Any deviation at adapter time is a bug and triggers `[RL-BUG]` reporting per [`../../PLAN/templates/issue_bug.md`](../../PLAN/templates/issue_bug.md).

## Licence posture

- LongMemEval: MIT — mirrored, redistributable with attribution.
- LoCoMo: CC BY-NC 4.0 — mirrored, non-commercial, attribution required; the Research Lab's eventual release honours NC.
- Mem0: Apache-2.0 (used as baseline B3).
- Graphiti: Apache-2.0 (conditional baseline B4).

See [`../../reference/Source_Evidence_Ledger.md`](../../reference/Source_Evidence_Ledger.md) for full source attribution.

## Status

Complete (`RL-020`–`RL-026`). Public benchmark mirrors (`RL-020`, `RL-021`), own-suite generators (`RL-022`–`RL-024`), benchmark adapters (`RL-025`), and Gate `J5` dev/test splits ([`splits/FREEZE_LIST.md`](splits/FREEZE_LIST.md), `RL-026`) are verified and frozen.

## Benchmark adapters

See [ADAPTERS.md](ADAPTERS.md) for the RL-025 checksum-bound sources and official scorer wrappers. Raw data remains ignored and unchanged; source loading, real split freezes, model providers and scientific evaluation retain their separate gates.
