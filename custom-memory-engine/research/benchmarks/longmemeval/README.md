# `LongMemEval` Benchmark Acquisition & Verification (`RL-020`)

**Source:** [`xiaowu0162/longmemeval-cleaned`](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) at commit `98d7416c24c778c2fee6e6f3006e7a073259d48f` (`[9]`, Wu et al., ICLR 2025).  
**License:** MIT (see [`LICENSE`](LICENSE) and [`../../../reference/Source_Evidence_Ledger.md`](../../../reference/Source_Evidence_Ledger.md) row `[9]`).  
**Evaluation Role (`Graduation_Project_Proposal.md` §§7.4, 10.2, 10.3):**
- Primary confirmatory dataset for **`H2`** (`Evidence Recall@k` on `longmemeval_s_cleaned.json` over the `414` answerable non-assistant histories).
- Secondary descriptive outcome for end-to-end QA accuracy and `knowledge-update` / `temporal-reasoning` slices.
- **Not** used for `H1` (lacks per-fact validity interval labels), `H3` (poisoning), or `H4` (deletion).

---

## 1. Checksum Manifest (`MANIFEST.txt`)

Raw JSON files are stored under `data/` (gitignored) and verified against [`MANIFEST.txt`](MANIFEST.txt):

| Variant | File Name | Size (Bytes) | SHA-256 Checksum |
|---|---|---:|---|
| `oracle` | `longmemeval_oracle.json` | `15,388,478` | `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` |
| `S` (cleaned) | `longmemeval_s_cleaned.json` | `277,383,467` | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| `M` (cleaned) | `longmemeval_m_cleaned.json` | `2,737,100,077` | `9d79e5524794a2e6900a3aa9cb7d9152c5a3e8319c9a87c25494ba1eacee495f` |

---

## 2. Empirically Verified Dataset & Timestamp Anomaly Counts (`D-23`)

All timestamps use the format `YYYY/MM/DD (Day) HH:MM` (parsed as UTC per `D-23`). Both `longmemeval_oracle.json` and `longmemeval_s_cleaned.json` contain the exact same set of `500` `question_id` items:

| Metric / Anomaly Check | `longmemeval_oracle.json` | `longmemeval_s_cleaned.json` | Protocol Handling (`D-23` / `Proposal` §§7.4, 10.3) |
|---|---:|---:|---|
| Total questions / histories | `500` | `500` | Stratified by `question_type` into `dev` and `test` (`RL-026`). |
| Abstention questions (`*_abs`) | `30` | `30` | Excluded from `Evidence Recall@k` (no gold evidence); scored separately for abstention accuracy. |
| `single-session-assistant` questions | `56` | `56` | Excluded from primary `Evidence Recall@k` (`Proposal §10.3`: gold evidence is in assistant turns); reported separately. |
| Answerable questions (`500 − 30`) | `470` | `470` | Answerable slice across all 6 question types. |
| Primary `Evidence Recall@k` eligible (`500 − 30 − 56`) | `414` | `414` | Primary `H2` population before dev/test split. |
| Out-of-order `haystack_dates` (evidence sessions `answer_session_ids`) | `34` | `39` | `oracle` matches `Proposal §7.4` (`34/500`); `S` has `39/500` on `answer_session_ids`. Handled by stable sort on `(session_timestamp, original_session_index)`. |
| `question_date < max(session_date)` (evidence sessions `answer_session_ids`) | `43` | `44` | `oracle` matches `Proposal §7.4` (`43/500`); `S` has `44/500` on `answer_session_ids`. Handled by `ρ = max(question_date, last_recorded_turn_clock)` + exclusion sensitivity check. |
| Out-of-order `haystack_dates` (all haystack sessions incl. filler) | `34` | `211` | Filler sessions in `S` introduce additional out-of-order session lists; resolved by `D-23` stable session sort. |
| `question_date < max(session_date)` (all haystack sessions incl. filler) | `43` | `76` | Filler sessions in `S` extend past `question_date` in 33 additional histories; clamped by `ρ = max(question_date, last_recorded_turn_clock)`. |
| Histories with tied session timestamps | `0` | `95` | `S` contains 95 histories with identical session timestamps; resolved by `R3` `1 µs` monotonic per-user clock increments. |

---

## 3. Usage

```bash
# Populate and verify oracle and S variants (offline cache + SHA-256 + anomaly checks)
python3 custom-memory-engine/research/benchmarks/longmemeval/fetch.py --variants oracle s
```

## RL-025 source adapter

The read-only `adapter.py` source facade is documented in [ADAPTERS.md](../ADAPTERS.md), including guarded selections, clocks, evidence/scorer provenance and synthetic verification. Acquisition above is separate from evaluation; adapters never invoke fetch or repair.
