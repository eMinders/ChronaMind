# Own Suite `D` — Deletion & Canary Lineage Generator (`RL-023`)

**Target Hypothesis:** **`H4`** (`Graduation_Project_Proposal.md` §§4.1, 7.7, 10.2, 10.3, 10.6; `TECHNICAL_SPECIFICATION.md` §7.2).  
**Cluster Unit:** Independent synthetic user scenario (`dev = 20` scenarios, exact zero-leak 97.5% upper bound `0.168433470983`; `test = 50` scenarios per `RL-014` `D-16`, exact zero-leak 97.5% upper bound `0.071121736464 ≤ u* = 0.10`).  
**License:** MIT (`D-26`; independently authored script-first templates and high-entropy fictional canaries).

---

## 1. Five Scripted Lineage Structures per Scenario

Every scenario (`own_D_{split}_{idx:03d}`) generates high-entropy fictional canaries (`CNRY-...`) across five distinct memory structures:

1. **`direct_extracted_memory` (`ev01`, `scored_in_primary_pdlr = True`, `scope = "memory"`):**
   - Canary stored in a single extracted memory version without downstream derivations.
2. **`consolidated_semantic_memory` (`ev02`, `scored_in_primary_pdlr = True`, `scope = "memory"`):**
   - Canary consolidated into a derived semantic memory linked via `MEMORY_DERIVATION(derivation_type = 'consolidation')`.
   - **Positive Control (`A8` / `P-nolineage`):** Because `A8` deletes only `T_0` and ignores `MEMORY_DERIVATION`, `A8` leaks this canary deterministically on Channels 1–6 (specifically `channel_4_derived_memory_lookup`, `channel_5_assembled_context`, and `channel_6_final_answer`), while `P` deletes the full derivation closure.
3. **`multi_source_summary_memory` (`ev03`, `scored_in_primary_pdlr = True`, `scope = "topic"`):**
   - Canary combined with an independent surviving fact (`SURVIVING-NOTE-...`) into a multi-source summary (`derivation_type = 'summary'`).
   - Under `P`, deleting the canary removes the old summary and synchronously re-derives a clean summary containing only the surviving fact. Under `A8`, the old summary remains and leaks the canary.
4. **`superseded_closed_belief_memory` (`ev04`, `scored_in_primary_pdlr = True`, `scope = "memory" | "all"`):**
   - Canary fact stated at `t1` and superseded by a new badge serial at `t2` (closing `recorded_range` on the canary version), then deleted. Verifies **`R6`** (deletion removes all `MEMORY_VERSION` rows sharing `memory_key`, including superseded and re-issued versions, taking precedence over audit retention).
5. **`relearning_multi_turn_control` (`ev05`, `is_relearning_control = True`, `scored_in_primary_pdlr = False`):**
   - Same fact stated in two separate turns across sessions where only the first turn's memory item is deleted. Explicitly tagged so the scorer reports re-learning separately from primary `H4` `PDLR` (`Proposal §§10.2, 10.3`).

Additionally, every scenario includes a **`never_stated_control_probe`** (`is_never_stated_control = True`, `scored_in_primary_pdlr = False`) querying a canary that never appeared in any turn, measuring false-positive canary match rate.

---

## 2. Usage

```bash
python3 custom-memory-engine/research/benchmarks/own_D/generator.py --write
```
