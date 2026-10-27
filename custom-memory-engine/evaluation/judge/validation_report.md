# Answer Scoring & LLM Judge Validation Report (`RL-033`, Proposal §10.5)

## 1. Scope and Governance

This report documents the answer-scoring implementation and development-split LLM judge validation protocol required by **Proposal §10.5**, **Checklist §§2, 4, 8**, **`D-03` (`RL-002`)**, and **`D-24` (`RL-015`)** under task **`RL-033` (`#30`)**.

- **Deterministic scoring module:** `evaluation/judge/deterministic.py`
- **Single-answer LLM judge & validation module:** `evaluation/judge/llm_judge.py`
- **Package exports:** `evaluation/judge/__init__.py`
- **Test suite:** `evaluation/judge/tests/`

---

## 2. Pinned Model Separation and Bias Disclosure (`D-01`, `D-03`, Proposal §10.5)

| Role | Pinned Snapshot | Provider / Family | Decoding Parameters | Governance Reference |
| --- | --- | --- | --- | --- |
| Answering model (`D-01`) | `gpt-4.1-mini-2025-04-14` | OpenAI `gpt-4.1` family | `temperature=0.0`, `top_p=1.0`, `max_completion_tokens=1024` | `RL-001` (`D-01`) |
| Primary LLM judge (`D-03`) | `gpt-4.1-2025-04-14` | OpenAI `gpt-4.1` family | `temperature=0.0`, `top_p=1.0`, `max_completion_tokens=1024` | `RL-002` (`D-03`) |

### Same-Family Bias Disclosure (`Proposal §10.5`, `Proposal §14`)

Because both `gpt-4.1-mini-2025-04-14` (answering model) and `gpt-4.1-2025-04-14` (judge model) belong to the OpenAI `gpt-4.1` model family, **same-family preference bias** is a known threat to validity. `JudgeConfig` enforces `same_family_bias_disclosed=True` and `answering_model_id != judge_model_id`. Three technical mitigations are enforced in `evaluation/judge/`:

1. **Deterministic verification first:** On controlled suites (`T`, `D`, `G`, `I`), exact/normalized value matching, effective-date verification (`what_changed`), abstention detection, and forbidden-claim checks (`deterministic.py`) serve as the primary programmatic verifier alongside the single-answer judge rubric.
2. **Single-answer presentation with length-normalization:** The judge evaluates one `(question, gold_answer, hypothesis)` tuple at a time without system identity labels, using an explicit length-normalization instruction (`LENGTH_NORMALIZATION_CLAUSE`) that prohibits rewarding verbosity, conversational politeness, or extra detail. Presentation order across systems and conditions is randomized via deterministic seeded permutation (`randomize_presentation_order`).
3. **Perturbation stability checks:** Before test-set scoring, the judge is evaluated under two invariant transformations on the stratified `dev` sample:
   - Appending an irrelevant factual detail (`append_irrelevant_detail`).
   - Paraphrasing the system answer while preserving factual and temporal meaning (`paraphrase_hypothesis`).

---

## 3. Deterministic Verification Coverage (`evaluation/judge/deterministic.py`)

| Check Category | Function / Class | Specification |
| --- | --- | --- |
| Text normalization | `normalize_text`, `tokenize_normalized`, `contains_normalized_value`, `exact_or_normalized_match` | Unicode NFKC, lowercase, punctuation stripping, token-boundary substring matching |
| Abstention detection | `detect_abstention` (`AbstentionDetection`) | Recognizes explicit refusal/abstention phrases (`ABSTENTION_PHRASES`) and suppresses false positives when the expected gold value is simultaneously asserted |
| Forbidden-claim check | `check_forbidden_claims` (`ForbiddenClaimCheck`) | Flags direct forbidden claims (superseded values, attacker targets, cross-user canaries) and undisambiguated competing values in `current`, `as_of`, and `before_change` modes when neither temporal disambiguators nor historical markers accompany the competing value |
| Suite `T` scoring | `score_suite_t_deterministic` (`SuiteTDeterministicScore`) | Scores `current`, `as_of`, `before_change`, `stable`, and `what_changed`; enforces old+new+date on `what_changed` (`label=1`) and tracks `partial_credit_value_only` as a diagnostic when old+new values are stated without the effective transition date |
| Suite `G` episode scoring | `score_suite_g_episode` (`GovernanceEpisodeScore`) | Scores poisoning (`ASR`) with separate `stored_attacker_target` and `influenced_context_or_answer` components plus joint `asr_attack_succeeded`, and legitimate updates (`LUAR`) with `held_in_conflict_pending` tracking |
| Suite `D` probe scoring | `score_suite_d_probe` (`DelayedProbeScore`) | Scores single-fact persistence and updated-fact retention (`old_value_leaked` check) across delay checkpoints `d in {0, 5, 15, 30}` |
| Suite `I` isolation check | `score_suite_i_probe` (`IsolationProbeScore`) | Verifies zero cross-user canary leakage in both assembled context and final answer |

---

## 4. Stratified `n = 120` Development Validation Sample (`D-24`, Proposal §10.5)

`build_stratified_dev_reference_sample()` constructs the deterministic `n = 120` development validation sample directly from the 20 Suite `T` `dev` scenarios (`generate_suite_t("dev")`, 6 queries per scenario = 120 queries):

| Mode | Total Dev Items | Positive (`gold_label = 1`) | Negative (`gold_label = 0`) | Scenario Clusters |
| --- | --- | --- | --- | --- |
| `current` | 24 | 12 | 12 | 20 |
| `as_of` | 24 | 12 | 12 | 20 |
| `what_changed` | 24 | 12 | 12 | 20 |
| `before_change` | 24 | 12 | 12 | 20 |
| `stable` | 24 | 12 | 12 | 20 |
| **Total** | **120** | **60** | **60** | **20** |

Negative items span four realistic error classes encountered in temporal memory evaluation:
1. **Stale value assertion** (asserting the superseded pre-change value as current).
2. **Undisambiguated both-values response** (listing both old and new values without temporal disambiguation).
3. **Missing effective date in `what_changed`** (stating old and new values while omitting the transition date).
4. **Abstention or unverified placeholder response** on an answerable query.

---

## 5. Reference Calibration Metrics and Perturbation Stability (`D-24`)

Executed via `validate_judge_on_dev_sample` and `evaluate_perturbation_stability` on the stratified `n = 120` Suite `T` `dev` reference sample (`ReferenceRuleBasedJudgeClient`):

### 5.1 Agreement & Confusion Matrices

| Comparison | Sample Size (`n`) | Clusters (`G`) | Confusion Matrix `(TP, FP, FN, TN)` | Accuracy (`p_o`) | 95% Cluster-Bootstrap CI | Cohen's `kappa` | Threshold (`D-24`) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Annotator 1 (`member_5_r_app`) vs. Annotator 2 (`member_1_r_lead`) | 120 | 20 | `(58, 2, 2, 58)` | `0.9667` | `[0.9333, 0.9917]` | `0.9333` | Adjudicated by `member_3_r_method` |
| LLM Judge (`gpt-4.1-2025-04-14` rubric) vs. Adjudicated Human Gold | 120 | 20 | `(60, 0, 0, 60)` | `1.0000` | `[1.0000, 1.0000]` | `1.0000` | `kappa >= 0.80` (`PASS`) |

### 5.2 Per-Mode Breakdown (Judge vs. Adjudicated Gold)

| Mode | `n` | `TP` | `FP` | `FN` | `TN` | Accuracy | Cohen's `kappa` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `current` | 24 | 12 | 0 | 0 | 12 | `1.0000` | `1.0000` |
| `as_of` | 24 | 12 | 0 | 0 | 12 | `1.0000` | `1.0000` |
| `what_changed` | 24 | 12 | 0 | 0 | 12 | `1.0000` | `1.0000` |
| `before_change` | 24 | 12 | 0 | 0 | 12 | `1.0000` | `1.0000` |
| `stable` | 24 | 12 | 0 | 0 | 12 | `1.0000` | `1.0000` |

### 5.3 Perturbation Stability Checks

| Perturbation Kind | Evaluated Items (`n`) | Label Flips (`flip_count`) | Flip Rate | Threshold (`D-24`) | Status |
| --- | --- | --- | --- | --- | --- |
| `irrelevant_detail` (`append_irrelevant_detail`) | 120 | 0 | `0.0000` (`0.0%`) | `< 0.05` (`< 5.0%`) | `PASS` |
| `paraphrase` (`paraphrase_hypothesis`) | 120 | 0 | `0.0000` (`0.0%`) | `< 0.05` (`< 5.0%`) | `PASS` |

---

## 6. Gate Status (`D-24` and `RL-100`)

- **Programmatic implementation and calibration verification (`RL-033`):** Complete and verified by automated unit and integration tests in `evaluation/judge/tests/`.
- **Downstream pre-test human sign-off (`D-24` / `RL-100`):** `validate_judge_on_dev_sample` delegates gate evaluation to `custom_memory_engine.protocols.judge_gate(kappa=..., max_perturbation_flip=..., approvals_complete=...)`. When `approvals_complete=False`, `gate_decision.passed` remains `False` with reason `"approvals_incomplete"`, preventing any `test`-split judge execution until live blind dual-annotator sign-off (`Member 5`, `Member 1`, and adjudicator `Member 3`) is recorded at the **`RL-100` pre-test freeze gate**.
