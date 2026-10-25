# Own Suite `T` — Temporal-Change & Bi-Temporal Audit Generator (`RL-024`)

**Target Hypothesis:** **`H1`** (`Graduation_Project_Proposal.md` §§4.1, 7.4, 10.2, 10.3, 10.6; `TECHNICAL_SPECIFICATION.md` §3).  
**Cluster Unit:** Independent synthetic user scenario (`dev = 20` scenarios / `120` questions / `80` audit probes; `test = 50` scenarios / `300` questions / `200` audit probes).  
**License:** MIT (`D-26`; independently authored script-first templates, zero `LoCoMo` or `LongMemEval` text reuse).

---

## 1. Scripted Bi-Temporal Timeline (`E1`–`E3`, `R1`–`R8`)

Every scenario (`own_T_{split}_{idx:03d}`) scripts a 4-session history for an isolated user with reference time `ρ`:
1. **Session 1 (`t1`):**
   - `E1`: Initial single-valued state `v1_old` with explicit start date `t1` (`valid_range = [t1, ∞)`, `recorded_range = [t1, t2)`).
   - Unchanged control fact `v_stable` (`valid_range = [t1, ∞)`, `recorded_range = [t1, ∞)`).
   - Secondary attribute with uncertain start time (`valid_from_basis = "inferred"`) and an immediate same-day correction (`R2` with `v_old = v_new`, closing the mistaken row in recorded time without inserting a truncated re-issue row).
   - Two multi-valued memberships (`mv_keep` and `mv_end`, `single_valued = False`).
2. **Session 2 (`t2`):**
   - `E2`: User reports switching to `v2_new` (`R1`/`R2` close `v1` at `t2`, insert truncated re-issue `v1'` over `[t1, t2)` and `v2` over `[t2, ∞)`).
3. **Session 3 (`t3`):**
   - `E3`: Delayed / retroactive timing revision clarifying that the transition to `v2_new` actually took effect earlier on `t_retro` (`t1 < t_retro < t2`), closing `v1'` and `v2` at `t3` and inserting `v1''` (`valid_range = [t1, t_retro)`, `recorded_range = [t3, ∞)`) and `v2'` (`valid_range = [t_retro, ∞)`, `recorded_range = [t3, ∞)`).
4. **Session 4 (`t4`):**
   - `R7`: User ends multi-valued membership `mv_end` (`valid_range = [t1, t4)`), while keeping `mv_keep` (`valid_range = [t1, ∞)`).

---

## 2. Mechanical Stratum Assignment (`50% / 50%` Split) & Heuristic Guards

Every scenario generates `6` conversational questions across all `5` modes (`current`, `stable`, `as_of` current period, `as_of` earlier period, `before_change`, `what_changed`):
- **`present-state` stratum (`3` questions/scenario):** `q01_current`, `q02_stable`, `q03_as_of_current_period` (gold answer derivable from versions currently valid at `ρ`; both `P` and `A2` `P-nohistory` can answer).
- **`history-dependent` stratum (`3` questions/scenario):** `q04_as_of_earlier_period`, `q05_before_change`, `q06_what_changed` (gold answer requires non-currently-valid versions `v1''`; `P` retains `v1''` while `A2` discards it).
- Naive heuristics ("always return latest value" or "always return previous value") achieve at most `50%` (`3/6`) accuracy on every scenario.

---

## 3. Deterministic `audit_as_of(user_id, valid_time, belief_time)` Grid

Each scenario includes `4` API-level `(valid_time, belief_time)` probes (`audit_01`–`audit_04`). In particular, `audit_02` and `audit_03` query the **exact same `valid_time` (`t_query_between ∈ [t_retro, t2)`)** at two different `belief_time` instants (`t2 + 2d` vs. `t3 + 2d`), verifying that `audit_as_of` returns `v1'` before the retroactive correction `E3` and `v2'` after `E3`.

---

## 4. Usage

```bash
python3 custom-memory-engine/research/benchmarks/own_T/generator.py --write
```
