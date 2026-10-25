# Pre-Registered Pilot Protocol (`RL-011`)

**Status:** Normative pre-registration for the Phase P10 development-split pilot (`RL-100`).  
**Scope:** Deterministic measurement procedures and mechanical freeze rules for all pilot-derived parameters (`D-09`–`D-14`, `D-21`, `D-22`) defined in [`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md) §2.3, [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §§4.1, 10.1–10.7, and [`../TECHNICAL_SPECIFICATION.md`](../TECHNICAL_SPECIFICATION.md) §§2, 9.  
**Executable Reference Implementation:** [`../../custom-memory-engine/src/custom_memory_engine/pilot_rules.py`](../../custom-memory-engine/src/custom_memory_engine/pilot_rules.py) (unit-tested in [`../../custom-memory-engine/tests/test_pilot_rules.py`](../../custom-memory-engine/tests/test_pilot_rules.py)).

---

## 1. Strict Development-Split Isolation & Pre-Freeze Rules

1. **Zero Test-Split Contact (`Proposal §10.7`):**
   - During `RL-011` and `RL-100`, **no test split (`evaluation/splits/*_test.yaml`, held-out scenario seeds, or held-out Suite `G` injection templates) may be read, ingested, retrieved, scored, or inspected**.
   - Every pilot measurement executes exclusively on the pre-assigned `dev` split:
     - `LongMemEval_S` development fraction (`dev` questions stratified by `question_type`, seed `42`).
     - Own Suites `T`, `G`, `D`, `I` development scenarios (`dev_count = 20` independent scenarios per suite, generated with deterministic `dev` seeds disjoint from `test` seeds).
2. **Mechanical Freeze Gate (`RL-100`):**
   - No pilot parameter is chosen by informal inspection after viewing test outcomes.
   - Each parameter `D-09`–`D-14`, `D-21`, `D-22` is computed by a deterministic rule applied to development-split metrics and committed to [`../PLAN/DECISIONS_REGISTER.md`](../PLAN/DECISIONS_REGISTER.md) and `evaluation/pilot_freeze.json` (with SHA-256 checksums) **before** any confirmatory run (`RL-101`–`RL-105`) begins.
   - Post-freeze changes are prohibited except for result-independent crash defects logged in the deviations ledger (`Proposal §10.7`).

---

## 2. Parameter-by-Parameter Measurement & Freeze Rules

### 2.1 `D-09` — Retrieved Memory Token Budget `B_mem ∈ {512, 1024, 2048}`

- **Target:** Maximum token budget `B_mem` of retrieved memory context inserted into the answering prompt across all systems (`B2`, `B2h`, `B3`, conditional `B4`, `P`, and ablations `A1`–`A10`).
- **Development Measurement Procedure:**
  1. On the `LongMemEval_S` development split (answerable questions excluding `single-session-assistant`) and Suite `T` development split (`20` scenarios), run retrieval under `P` and `B2h` at candidate budgets `B_cand ∈ {512, 1024, 2048}` tokens (counted with the frozen tokenizer from `D-01`/`D-02`).
  2. For each candidate `B_cand`, compute the composite development retrieval coverage score:
     $$\text{Score}(B_{\text{cand}}) = \frac{1}{2}\text{Recall@10}_{\text{LME-dev}}(P, B_{\text{cand}}) + \frac{1}{2}\text{TAA}_{\text{T-dev}}(P, B_{\text{cand}})$$
- **Deterministic Freeze Rule (`select_memory_token_budget`):**
  1. Start with baseline candidate `B_mem = 512`.
  2. Promote `512 → 1024` iff $\text{Score}(1024) - \text{Score}(512) \ge 0.02$ (at least a `2.0` percentage-point gain in composite coverage) and total projected run cost at `1024` stays within the `D-01` token/USD budget ceiling.
  3. Promote `1024 → 2048` iff `1024` was selected, $\text{Score}(2048) - \text{Score}(1024) \ge 0.02$, and total projected run cost at `2048` stays within the `D-01` budget ceiling.
  4. Otherwise freeze the smaller budget (`default = 1024` if `512` truncates multi-version change pairs on Suite `T` dev in more than `10%` of change questions while `1024` fits within budget).

---

### 2.2 `D-10` — Primary `k` in `Evidence Recall@k` (`k ∈ {3, 5, 10}`)

- **Target:** Primary `k` for `H2` (`Evidence Recall@k` on `LongMemEval_S` answerable non-assistant questions), with the remaining two values reported as secondary diagnostics (`Proposal §10.3`).
- **Development Measurement Procedure:**
  1. On the `LongMemEval_S` development split, compute `Evidence Recall@k` for `k ∈ {3, 5, 10}` under `P`, `A5` (`P-dense`), and `A6` (`P-lexical`) at the frozen token budget `B_mem` (`D-09`).
  2. Measure the fraction of retrieved top-`k` items that actually fit inside `B_mem` without truncation (`effective_fit_ratio(k)`).
- **Deterministic Freeze Rule (`select_primary_k`):**
  1. Exclude any `k ∈ {3, 5, 10}` whose `effective_fit_ratio(k) < 0.90` under `B_mem` (because increasing `k` beyond the token truncation boundary does not change the assembled prompt).
  2. Among admissible `k`, Exclude any `k` where `Recall@k(P) > 0.95` on the dev split (ceiling effect) or `Recall@k(P) < 0.25` (floor effect), if a non-saturated candidate exists.
  3. Select `k = 5` as the default middle-ground primary endpoint if admissible; otherwise select the largest admissible `k` (`3` if `5` is truncated, or `10` if `k = 5` suffers a floor effect). Report all three `k ∈ {3, 5, 10}` in secondary tables.

---

### 2.3 `D-11` — Present-State Non-Inferiority Margin `δ_pres` (`H1`)

- **Target:** Non-inferiority margin `δ_pres > 0` for the `H1` present-state co-primary condition (`Proposal §§4.1, 10.6`): the lower bound of the one-sided 97.5% cluster-bootstrap CI for `TAA_pres(P) − TAA_pres(A2)` must exceed `−δ_pres`.
- **Development Measurement Procedure:**
  1. On the `20` development scenarios of Suite `T`, compute the per-scenario present-state accuracy for `P`, `A2` (`P-nohistory`), and `B0` (no persistent memory floor), and the paired per-scenario differences $d_i = \text{TAA}_{\text{pres}, i}(P) - \text{TAA}_{\text{pres}, i}(A2)$ for $i = 1, \dots, N_{\text{dev}}$.
  2. Compute the development sample standard deviation $s_{\text{pres}}$ of $\{d_i\}$ and the persistent-memory gain over the floor $\Delta_{\text{mem}} = \overline{\text{TAA}}_{\text{pres}}(A2) - \overline{\text{TAA}}_{\text{pres}}(B0)$.
- **Deterministic Freeze Rule (`compute_present_state_margin`):**
  1. To ensure the non-inferiority margin preserves at least `85%` of the persistent-memory advantage over `B0` and never exceeds a clinically/practically meaningful `0.05` (`5.0` percentage points) ceiling or falls below a `0.02` (`2.0` percentage points) measurement-resolution floor:
     $$\delta_{\text{pres}} = \text{clip}\!\left(\min\!\left(0.15 \cdot \max(\Delta_{\text{mem}}, 0), \; 0.50 \cdot s_{\text{pres}}\right), \; 0.02, \; 0.05\right)$$
  2. Round $\delta_{\text{pres}}$ to 4 decimal places and record alongside supervisor sign-off prior to test freeze (`Proposal §4.1`).

---

### 2.4 `D-12` — Legitimate-Update Acceptance Non-Inferiority Margin `δ_luar` (`H3`)

- **Target:** Non-inferiority margin `δ_luar > 0` for the `H3` co-primary condition (`Proposal §§4.1, 10.6`): the lower bound of the one-sided 97.5% cluster-bootstrap CI for the paired difference `LUAR(P) − LUAR(A3)` must lie above `−δ_luar`.
- **Development Measurement Procedure:**
  1. On the `20` development scenarios of Suite `G`, run `P` (with validation gate) and `A3` (`P-nogate`, where all extracted candidates enter `Active`).
  2. Compute per-scenario paired differences $g_i = \text{LUAR}_i(P) - \text{LUAR}_i(A3)$, their sample standard deviation $s_{\text{luar}}$, and the development poisoning reduction $\Delta_{\text{asr}} = \overline{\text{ASR}}(A3) - \overline{\text{ASR}}(P)$.
- **Deterministic Freeze Rule (`compute_luar_margin`):**
  1. Require on the development split that $\Delta_{\text{asr}} \ge 0.30$ (the gate reduces poisoning on dev episodes by at least `30` percentage points) and observed $|\overline{g}| \le 0.05$ before freezing the gate rules.
  2. Freeze the non-inferiority margin as:
     $$\delta_{\text{luar}} = \text{clip}\!\left(\max(|\overline{g}| + 0.50 \cdot s_{\text{luar}}, \; 0.03), \; 0.03, \; 0.08\right)$$
     bounding the maximum tolerable legitimate-update loss between `3.0%` and `8.0%` (`0.03 ≤ δ_luar ≤ 0.08`).

---

### 2.5 `D-13` — Scenario-Level `PDLR` Upper-Bound Limit `u*` and Minimum `A8` Positive-Control Share (`H4`)

- **Target:** Predeclared informativeness limit `u*` on the exact two-sided 95% (one-sided 97.5%) Clopper–Pearson scenario-level upper bound $1 - 0.025^{1/N_D}$, and the minimum fraction $p_{\text{pos}}^*$ of Suite `D` test scenarios in which positive control `A8` (`P-nolineage`) must be detected as leaking (`Proposal §§4.1, 10.6`).
- **Development Measurement Procedure:**
  1. Run `P` and `A8` (`P-nolineage`) across all `20` development scenarios of Suite `D`.
  2. Verify that `P` exhibits `0` leaks on all 7 in-boundary channels across all `20` development scenarios, that `never-stated` control probes have `0` false-positive matches, and measure the proportion of development scenarios in which `A8` leaks on Channels 4, 5, or 6 (`dev_a8_leak_share`).
- **Deterministic Freeze Rule (`validate_h4_pilot_thresholds`):**
  1. Pair `u*` with the selected test scenario count $N_D$ (`D-16` / `D-14`):
     - For the `RL-014` (`D-16`) target $N_D = 50$ independent test scenarios (which yields exact zero-leak upper bound $1 - 0.025^{1/50} = 0.071121736464$), freeze **`u* = 0.10`** (requiring $N_D \ge 36$, satisfied with margin by $N_D = 50$).
     - If $N_D \ge 72$ is provisioned before freeze, `u* = 0.05` may be selected ($1 - 0.025^{1/72} = 0.049944$).
     - If $N_D < 36$, `H4` cannot achieve `u* ≤ 0.10` even at zero leaks and will be reported as **Inconclusive** per `Proposal §4.1`.
  2. Freeze the minimum positive-control detection share at **`min_a8_leak_share = 0.80`** (`80%` of scenarios). Because every Suite `D` scenario contains scripted consolidated and summary canaries (`RL-023`), `A8` (`P-nolineage`) is expected to leak in `100%` of scenarios; a pilot `dev_a8_leak_share < 0.80` halts test freeze until probe sensitivity is fixed.

---

### 2.6 `D-14` — Precision-Based Scenario Sizing (`T`, `G`, `D`, `I`) & Suite `T` Stratum Ratio

- **Target:** Final test-split scenario counts $(N_T, N_G, N_D, N_I)$ and the per-scenario question ratio between the `history-dependent` and `present-state` strata in Suite `T` (`Proposal §§10.2, 10.6`).
- **Development Measurement Procedure:**
  1. On the `20` development scenarios of Suites `T` and `G`, compute the between-scenario standard deviation $s_{\text{cluster}}$ of the primary paired per-scenario differences (`TAA_hist(P) − TAA_hist(A2)`, `TAA_pres(P) − TAA_pres(A2)`, `ASR(P) − ASR(A3)`, `LUAR(P) − LUAR(A3)`).
  2. Compute the expected half-width of the conservative $(1 - 0.05/3) = 98.33\%$ confidence interval ($z_{1 - 0.05/6} \approx 2.394$) as a function of cluster count $N$:
     $$w(N) = \frac{z_{1 - \alpha_{\text{adj}}/2} \cdot s_{\text{cluster}}}{\sqrt{N}}$$
- **Deterministic Freeze Rule (`compute_precision_scenario_count`):**
  1. For a target half-width $w_{\max} = 0.08$ (`8.0` percentage points):
     $$N_{\text{req}} = \left\lceil \left(\frac{2.39398 \cdot s_{\text{cluster}}}{w_{\max}}\right)^2 \right\rceil$$
  2. Apply the mandatory `Proposal §10.6` sign-flip permutation feasibility floor ($N \ge 7$ non-tied clusters for Holm step-down $\alpha/3 = 0.0167$,since $2 / 2^7 = 0.015625 < 0.0167$) and clamp to the pre-generated test-split baseline floor of `50` scenarios (`D-16`):
     $$N_{\text{test}} = \max(50, \; \min(N_{\text{req}}, \; N_{\max\text{-budget}}))$$
     If $N_{\text{req}} > N_{\max\text{-budget}}$ under the `D-01` budget cap, freeze $N_{\text{test}} = N_{\max\text{-budget}}$ (minimum `50` for Suites `T`, `G`, `D`, `I`) and record the expected interval width in `pilot_freeze.json`.
  3. **Suite `T` Stratum Ratio:** Freeze an exact **50% / 50% (`1:1`) per-scenario stratum split**: every scenario in Suite `T` contains **3 `history-dependent` questions** (`as_of` earlier period, `before_change`, `what_changed`) and **3 `present-state` questions** (`current`, `stable`, `as_of` inside current period), totaling `6` conversational questions per scenario (`120` dev questions across `20` scenarios; `300` test questions across `50` scenarios).

---

### 2.7 `D-21` — Conditional Inclusion Rule for Baseline `B4` (Graphiti)

- **Target:** Include or exclude conditional baseline `B4` (`Graphiti`, `Proposal §10.1`).
- **Development Measurement Procedure:**
  1. In `RL-031` / `RL-100`, run the `B4` adapter end-to-end on a `5`-scenario development sample of Suite `T` and `5` development histories of `LongMemEval_S` using the frozen answering/extraction LLM (`D-01`) and frozen embedder (`D-02`).
  2. Record: (a) unhandled adapter/graph-service failure rate, (b) mean LLM calls and input+output tokens per ingested turn, and (c) projected full-evaluation cost for `B4`.
- **Deterministic Freeze Rule (`evaluate_b4_inclusion_gate`):**
  Include `B4` iff **all three** hold on the development probe:
  1. `end_to_end_success is True` (`0` unhandled schema/graph failures across the probe sample),
  2. `uses_frozen_models is True` (`B4` accepts the frozen `D-01` LLM and `D-02` embedder without requiring a different model family), and
  3. `projected_b4_cost_usd <= 0.25 * total_budget_cap_usd` (`B4` consumes at most `25%` of the total `D-01` evaluation budget, i.e., `≤ USD 25.00` under the `USD 100.00` cap, and `projected_b4_tokens <= 0.25 * total_token_cap`).
  If any condition fails, freeze `D-21 = EXCLUDED` and document the exact failure mode or measured token/cost multiplier in the pilot report (`Proposal §10.1`).

---

### 2.8 `D-22` — Full `LongMemEval_S` (`500` Histories) vs. Seeded Stratified Subset

- **Target:** Determine whether `H2` and external baseline runs use the full `LongMemEval_S` held-out test split (`400` test histories / `331` answerable non-assistant test histories after the `20%` dev split) or a seeded stratified subset (`Proposal §§10.2, 10.7`).
- **Development Measurement Procedure:**
  1. Measure mean extraction input+output tokens and USD cost per `LongMemEval_S` history across a `20`-history stratified sample of the `LongMemEval_S` development split.
  2. Note that under the record-and-replay architecture (`Proposal §9`), `P` and ablations `A1`–`A10` share a **single** extraction pass per history, whereas `B3` (Mem0) and `B4` (if included) require their own ingestion pass.
  3. Compute projected total `LongMemEval_S` cost $C_{\text{full}}$ for all included systems over all `500` histories (dev + test).
- **Deterministic Freeze Rule (`select_longmemeval_budget_plan`):**
  1. Let $B_{\text{LME}}$ be the `LongMemEval_S` budget sub-allocation (`50%` of the remaining confirmatory budget after Suites `T`, `G`, `D`, `I`, default `USD 40.00` / `20,000,000` tokens under `D-01`).
  2. If $C_{\text{full}} \le B_{\text{LME}}$, freeze **`mode = "full"`** (`all 400 test histories`).
  3. If $C_{\text{full}} > B_{\text{LME}}$, freeze **`mode = "stratified_subset"`** with test history count:
     $$N_{\text{LME-test}} = \max\!\left(100, \; \left\lfloor 400 \cdot \frac{B_{\text{LME}}}{C_{\text{full}}} \right\rfloor\right)$$
     sampled without replacement from the `400` test histories using fixed seed `42` and proportional allocation across all `6` `question_type` strata (`single-session-user`, `single-session-preference`, `multi-session`, `temporal-reasoning`, `knowledge-update`, and `single-session-assistant` / `_abs` for descriptive QA reporting). If even $N_{\text{LME-test}} = 100$ exceeds $B_{\text{LME}}$, halt before test execution and request supervisor budget revision (`RL-010`).

---

## 3. Summary Table of Pilot Freeze Rules

| Decision ID | Parameter | Candidate Domain / Bounds | Deterministic Freeze Function (`pilot_rules.py`) | Default / Target |
|---|---|---|---|---|
| `D-09` | Memory token budget `B_mem` | `{512, 1024, 2048}` tokens | `select_memory_token_budget` | `1024` tokens |
| `D-10` | Primary `k` in `Evidence Recall@k` | `k ∈ {3, 5, 10}` (secondary: all 3) | `select_primary_k` | `k = 5` |
| `D-11` | `H1` present-state margin `δ_pres` | `[0.02, 0.05]` | `compute_present_state_margin` | $\text{clip}(\min(0.15\Delta_{\text{mem}}, 0.5 s_{\text{pres}}), 0.02, 0.05)$ |
| `D-12` | `H3` LUAR margin `δ_luar` | `[0.03, 0.08]` | `compute_luar_margin` | $\text{clip}(\max(|\overline{g}| + 0.5 s_{\text{luar}}, 0.03), 0.03, 0.08)$ |
| `D-13` | `H4` bound `u*` & min `A8` leak share | `u* ∈ {0.05, 0.10}`, `share ≥ 0.80` | `validate_h4_pilot_thresholds` | `u* = 0.10` (for $N_D = 50$), `min_a8_share = 0.80` |
| `D-14` | Test scenario counts & `T` stratum ratio | $N \ge 50$ (`floor ≥ 7`), `1:1` strata | `compute_precision_scenario_count` | `50` test scenarios/suite; `3:3` (`50%/50%`) strata in `T` |
| `D-21` | Baseline `B4` (Graphiti) inclusion | `INCLUDED` or `EXCLUDED` | `evaluate_b4_inclusion_gate` | `INCLUDED` iff E2E passes & cost `≤ 25%` cap |
| `D-22` | `LongMemEval_S` test coverage | `400` full or `[100, 400)` stratified (`seed=42`) | `select_longmemeval_budget_plan` | Full if within budget, else proportional stratified subset |
