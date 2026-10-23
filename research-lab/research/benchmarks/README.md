# research/benchmarks/

**Scope:** mirrored public benchmarks and own-suite generators used by the H1–H4 experiments.
**Phase:** P2.
**Normative references:** [`../../Graduation_Project_Proposal.md`](../../Graduation_Project_Proposal.md) §10.2; [`../../PROJECT_ROADMAP.md`](../../PROJECT_ROADMAP.md) §D.2; [`../../Source_Evidence_Ledger.md`](../../Source_Evidence_Ledger.md) §C.

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
│   ├── splits/      # Frozen in RL-026
│   ├── MANIFEST.txt
│   └── README.md
├── own_D/           # Deletion suite with canaries (RL-023)
│   └── ...
├── own_G/           # Gate / injection suite (RL-022)
│   └── ...
└── splits/          # Freeze list (RL-026)
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

See [`../../Source_Evidence_Ledger.md`](../../Source_Evidence_Ledger.md) for full source attribution.

## Status

Not Started. Mirrors begin with `RL-020` and `RL-021`; own-suite generators begin with `RL-022` through `RL-024`; the dev/test split is frozen in `RL-026`.
