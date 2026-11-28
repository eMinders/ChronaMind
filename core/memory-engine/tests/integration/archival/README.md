# Archival regression harness (`RL-090`, Invariant 10, `D-27`)

`test_t1_to_t5.py` is the executable form of the mandatory `T1`–`T5` contract in
`TECHNICAL_SPECIFICATION.md` §6 (and `Methodology_and_Consistency_Checklist.md`). One test per row,
each asserting that row's expected outcome:

| Test | Contract row | Asserts |
|---|---|---|
| `test_t1_*` | `T1` | archiving a currently-valid current belief is refused; the row stays `active` |
| `test_t2_*` | `T2` | archiving a historical current belief is refused; it stays returned by `historical_as_of` |
| `test_t3_*` | `T3` | a superseded version archives via branch `(S)` and stays visible to `audit_as_of` |
| `test_t4_*` | `T4` | a covered version archives via branch `(C)`; queries return the replacement, provenance intact |
| `test_t5_*` | `T5` | `delete_memory(r.memory_key)` removes every version of the key and the replacement; **zero leakage on all seven channels**, measured against the engine's real retrieval, rendering, answer and log outputs, with a pre-deletion control that must see the canary |
| `test_t5_counterpart_*` | (Invariant 10) | deleting only the replacement while its `(C)` source survives is still refused |

The `T5` run found a defect when this harness was built (r10): deleting a memory whose `(C)`-archived
version had a covering replacement failed with `archival_breaks_covered_source_invariant`, because the
archival triggers judged each statement instead of the deletion's final state. `delete_memory` now defers
them for the deletion and re-checks them `IMMEDIATE` before returning (`V2-D55`).

Run: `CME_TEST_DATABASE_URL=... uv run pytest -q tests/integration/archival`.
