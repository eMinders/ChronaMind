# RL-014 — A2b and Suite D cardinality (#4)

Prepared 2026-10-24. Technical design selected; Member 3 / Member 1 review and final pilot freeze pending.

## D-15

**A2b OFF / SKIP.** Primary H1 remains P − A2 on both strata. A2b retains valid-time history without recorded_range and isolates audit behavior, not the ordinary-answer endpoint. Deterministic audit checks remain required; skipping A2b does not remove historical-as-of support in P. Protect core extraction/versioning/deletion engineering capacity. No A2b generator is implemented.

## D-16

Select **20 dev scenarios and 50 test scenarios**, independent fictional users/scenarios, disjoint seeds/entities/canaries. This is the target for RL-023 and RL-011; D-14 final sample sizing and D-13 u* remain pilot-derived and must be frozen before any test execution. No post hoc sample enlargement.

| Independent scenarios | Exact zero-leak 97.5% upper bound | Role |
|---|---|---|
| 20 | 0.168433470983 | Dev pilot, not confirmatory evidence |
| 36 | 0.097393755914 | Minimum for proposed u* = 0.10 |
| 50 | 0.071121736464 | Selected test target |
| 60 | 0.059629492286 | Alternative arithmetic, not selected |
| 72 | 0.049944083706 | Minimum for u* = 0.05 |

Formula: `1 − 0.025^(1/N_D)` for zero observed **scenario** leaks. Research Lab `validation/cp_bounds.output.txt` explicitly includes 20/36/50 and minimum counts; its table **does not contain 60**, despite the issue example. The added 60/72 values are independently computed by `zero_leak_upper` and checked against the closed form, not falsely cited as existing log entries.

Proposed `u* = 0.10` is compatible with N_D=50, but **not frozen here** (D-13/RL-100). If pilot selects u*=0.05, N_D must be at least 72 and revised before freeze. Events/probes/channels within one scenario and repeated runs never count as extra independent samples. Positive-control minimum detection share remains D-13; zero leaks alone is not proof of erasure or sufficient support for H4.

## Evidence / impact / sign-off

Proposal §§4.1, 10.2–10.6, Research Lab CP script/log and unit tests. RL-011/023/024/101/105 have explicit targets. Owner Member 3 (R-METHOD): pending. Peer Member 1 (R-LEAD): pending. No pilot data or test splits inspected.
