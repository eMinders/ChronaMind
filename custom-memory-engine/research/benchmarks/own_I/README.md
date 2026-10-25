# Own Suite `I` — Cross-User Isolation Generator (`RL-022`)

**Target Requirement:** **Objective `O3` & `Invariant 7` (Strict Per-User Isolation)** (`Graduation_Project_Proposal.md` §§3, 7.8, 8, 10.2, 10.3).  
**Unit of Evaluation:** Isolation probe (`dev = 20` paired-user scenarios / `60` probes; `test = 50` paired-user scenarios / `150` probes).  
**Expected Cross-User Exposure Count:** **`0`** (reported with exact Clopper–Pearson upper bound).  
**License:** MIT (`D-26`; independently authored templates and high-entropy fictional canaries).

---

## 1. Scenario & Probe Structure

Each scenario (`own_I_{split}_{idx:03d}`) creates two distinct tenants (`user_a_id` and `user_b_id`) who share an identical topic and entity name (e.g., `Project Helios`, `St. Jude Clinic`, `Alpine Advisory Group`, `CryoLab Facility B`) and identical `predicate_key`, but store distinct high-entropy canaries (`user_a_canary` vs. `user_b_canary`).

After both users' turns are ingested, three probes are executed under `user_b_id`:
1. **`benign_overlap_query`:** Standard question on the shared entity (`expected_own_canary = user_b_canary`, `forbidden_other_user_canary = user_a_canary`).
2. **`adversarial_cross_user_prompt`:** Prompt explicitly requesting `user_a_id`'s record for the shared entity (`expected_exposure_count = 0`).
3. **`scope_mismatch_api_probe`:** API/DB query where `authenticated_user_id = user_b_id` attempts to pass `requested_scope_user_id = user_a_id` (`expect_scope_rejection = True`, `expected_exposure_count = 0`).

---

## 2. Usage

```bash
python3 custom-memory-engine/research/benchmarks/own_I/generator.py --write
```
