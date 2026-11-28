# evaluation/

**Scope:** evaluation harness, baselines, judge, statistics module, run artefacts.
**Phases:** P3 (harness + baselines + judge + stats), P10 (pilot + H1–H4 runs), P11 (statistics + error analysis).
**Normative references:** [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §10; [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §D.3, §D.10, §D.11; [`../TRACEABILITY_MATRIX.md`](../TRACEABILITY_MATRIX.md) §1.

## RL-030 implemented foundation

[`harness/README.md`](harness/README.md) documents the seven-method adapter,
typed configuration/replay, immutable artifacts, persistent budget reservations,
signed-freeze/one-shot guard, and provider-free CLI smoke example. The
[`stats/README.md`](stats/README.md) documents paired inferential primitives and
the pending H3 decision clarification in RL-034. Verification uses synthetic
temporary fixtures, not a pilot or held-out benchmark run.

## Planned layout (populated by `RL-030` onward)

```
evaluation/
├── harness/     # Config layer, run manifest, scorer invocation (RL-030)
├── baselines/   # B0, B1, B2, B2h, B3 (Mem0 pinned), B4 (Graphiti conditional)
│   ├── b0_no_memory/
│   ├── b1_full_history/
│   ├── b2_dense_rag/
│   ├── b2h_hybrid_rag/
│   ├── b3_mem0/        # Pinned commit
│   └── b4_graphiti/    # Conditional on D-21 pilot outcome (RL-032)
├── judge/       # LLM-judge harness + perturbation calibration from [36] (RL-033)
├── stats/       # Cluster bootstrap, sign-flip, Holm, Clopper–Pearson (RL-034)
└── runs/        # Per-run output tree (gitignored except manifests)
    └── <ts>/<config>/<suite>/{answers.jsonl, scores.jsonl, per_cluster.jsonl, manifest.json}
```

## Reproducibility invariants

- Every run writes a `manifest.json` with: config hash, data hash, seed, timing, cost, environment capture.
- Deterministic fixture/record-and-replay inputs produce byte-identical scores; hosted-model seeds alone do not guarantee identical outputs. Repeated-run aggregation follows proposal §10.6.
- Run outputs are **not** committed in bulk — only the manifest and a short score summary.
- Pilot runs touch the development split only. Test-split runs happen **once** per configuration (proposal §10.7).

## Independence

- Baselines B3 (`mem0ai`) and B4 (`graphiti`) are allowed as **pinned** external dependencies **inside baseline adapters only**; they must not leak into other modules. See [`../PLAN/INDEPENDENCE_BOUNDARY.md`](../PLAN/INDEPENDENCE_BOUNDARY.md) §3.2.
