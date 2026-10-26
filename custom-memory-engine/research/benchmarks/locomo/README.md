# `LoCoMo` Benchmark Acquisition & Verification (`RL-021`)

**Source:** [`snap-research/locomo`](https://github.com/snap-research/locomo) at commit `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376` (`data/locomo10.json`, `[4]`, Maharana et al., ACL 2024).  
**License:** **CC BY-NC 4.0** (Attribution-NonCommercial 4.0 International; see [`LICENSE`](LICENSE) and [`../../../reference/Source_Evidence_Ledger.md`](../../../reference/Source_Evidence_Ledger.md) row `[4]`).

---

## 1. License & Methodological Restrictions (`Proposal` §§10.2, 10.6; `D-26`)

1. **Non-Commercial Research Only (`CC BY-NC 4.0`):** `locomo10.json` is used strictly for non-commercial academic evaluation.
2. **Zero Content or Schema Reuse in Own Suites (`D-26`):** Suites `T`, `G`, `D`, and `I` are generated from independently authored templates and carry zero text, persona, question, or schema overlap with `LoCoMo`.
3. **Descriptive Replication Only (Not Confirmatory):** Because `locomo10.json` contains only **`10` independent conversations** (clusters), cluster-aware statistical inference has insufficient degrees of freedom for confirmatory hypothesis testing (`Graduation_Project_Proposal.md` §§10.2, 10.6). Results on `LoCoMo` are reported descriptively per conversation and by numeric category (`1`–`5`).

---

## 2. Checksum & Verified Dataset Counts (`MANIFEST.txt`)

| Metric | Verified Value | Notes |
|---|---|---|
| File Name | `locomo10.json` | Stored under gitignored `data/locomo10.json`. |
| File Size (Bytes) | `2,805,274` | Exact upstream byte length. |
| SHA-256 Checksum | `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4` | Verified against [`MANIFEST.txt`](MANIFEST.txt). |
| Conversations (Clusters) | `10` | `conv-26`, `conv-30`, `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`. |
| Total QA Pairs | `1,986` | `282 + 321 + 96 + 841 + 446 = 1,986`. |
| QA Pairs Without `evidence` IDs | `4` | Empty `evidence` list (`[]`); tracked explicitly per `Proposal §10.2`. |
| Numeric Category `1` Count | `282` | Reported by numeric category ID per `Proposal §10.2`. |
| Numeric Category `2` Count | `321` | Reported by numeric category ID per `Proposal §10.2`. |
| Numeric Category `3` Count | `96` | Reported by numeric category ID per `Proposal §10.2`. |
| Numeric Category `4` Count | `841` | Reported by numeric category ID per `Proposal §10.2`. |
| Numeric Category `5` Count | `446` | Adversarial / abstention category (`446` items). |

---

## 3. Usage

```bash
python3 custom-memory-engine/research/benchmarks/locomo/fetch.py
```

## RL-025 source adapter

The read-only `adapter.py` source facade is documented in [ADAPTERS.md](../ADAPTERS.md), including guarded selections, clocks, evidence/scorer provenance and synthetic verification. Acquisition above is separate from evaluation; adapters never invoke fetch or repair.

The RL-025 mechanical schema audit also identified 16 date-only metadata keys and 5 questions with unresolved evidence annotations. The adapter preserves all 10 conversations/1,986 questions and flags those evidence metrics as unscored; it never invents missing turns. Compound well-formed evidence references are resolved explicitly. These are source-format checks, not QA evaluation results.
