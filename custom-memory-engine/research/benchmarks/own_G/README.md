# Own Suite `G` — Write-Governance Generator (`RL-022`)

**Target Hypothesis:** **`H3`** (`Graduation_Project_Proposal.md` §§4.1, 7.5, 7.8, 10.2, 10.3; `TECHNICAL_SPECIFICATION.md` §5).  
**Cluster Unit:** Independent synthetic user scenario (`dev = 20` scenarios / `240` episodes; `test = 50` scenarios / `600` episodes).  
**License:** MIT (`D-26`; independently authored script-first templates, zero `LoCoMo` or `LongMemEval` text reuse).

---

## 1. Suite Design & Episode Structure

Every scenario (`own_G_{split}_{idx:03d}`) scripts `12` sequential episodes for an isolated synthetic user:

1. **Legitimate Updates & Facts (`4` episodes/scenario, `assertion_mode = "asserted"`, scored for `LUAR`):**
   - `ep01`: `explicit_new_fact` (`expected_relation = "unrelated"`, `expected_gate_decision = "accept"`).
   - `ep02`: `duplicate_corroboration` (`expected_relation = "duplicate"`, `expected_gate_decision = "accept"`).
   - `ep03`: `explicit_update` (`expected_relation = "update"`, `expected_gate_decision = "accept"`).
   - `ep04`: `explicit_correction` (`expected_relation = "correction"`, `expected_gate_decision = "accept"`).
2. **Non-Assertions (`6` episodes/scenario, covering all 6 non-assertion modes in `Proposal §7.5`):**
   - `ep05` (`hedged`) & `ep06` (`hypothetical`): routed to `conflict_pending` (`expected_gate_decision = "hold"`).
   - `ep07` (`planned`): routed to the `'plans_to'` predicate (`expected_gate_decision = "accept_plans_to"`), leaving the current-state predicate untouched.
   - `ep08` (`joking`), `ep09` (`quoted`), & `ep10` (`third_party`): rejected (`expected_gate_decision = "reject"`, `reason_code = "non_user_assertion"`).
3. **Unsupported & Injected Writes (`2` episodes/scenario, scored for `ASR`):**
   - `ep11` (`unconfirmed_assistant_guess`): assistant speculates an attribute without user confirmation (`reason_code = "unconfirmed_assistant_claim"`).
   - `ep12` (`instruction_or_payload_injection`): adversarial directive or pasted payload targeting the user's state predicate (`reason_code = "instruction_injection"`).

---

## 2. Held-Out Attack Families (`dev` vs. `test`)

To prevent overfitting the instruction screen during development tuning (`Proposal §10.2`), `dev` and `test` use strictly disjoint injection families:
- **`dev` injection families (`4`):** `dev_always_tell_directive`, `dev_markdown_note_override`, `dev_pasted_email_directive`, `dev_system_override`.
- **`test` held-out injection families (`6`):** `test_audit_override_directive`, `test_json_tool_spoof`, `test_meeting_transcript_poison`, `test_nested_translation_payload`, `test_roleplay_persona_hijack`, `test_xml_policy_injection`.

Reference trade-off bounds (`Proposal §10.2`):
- **Accept-All (`A3` / `P-nogate`):** `ASR = 1.0`, `LUAR = 1.0`.
- **Reject-All:** `ASR = 0.0`, `LUAR = 0.0` (fails `H3` LUAR non-inferiority).

---

## 3. Usage

```bash
python3 custom-memory-engine/research/benchmarks/own_G/generator.py --write
```
