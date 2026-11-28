# ChronaMind V2 — Research Program

**Purpose.** A defensible thesis (a) with a contribution that is measurable, (b) that
does not claim more than the statistics support, and (c) whose *stated purpose* —
personalization through an evolving user model — is actually tested. The first draft
failed (c): it had three families and the PDT was not among them. That is corrected
here as **F4**, and it is no longer de-scopeable.

**How the identity changed twice.** v1: *"ablation-isolated measurement of three
substrate mechanisms in one open system."* V2 draft: *"plus verifiable abstraction."*
V2 final: *"plus verifiable abstraction **and** an evidence-grounded evolving user
model whose utility for personalization and decision support is measured."* The third
clause is the one the project's title promised and the first draft quietly dropped.

---

## 1. Four contribution families

| Family | Claim | Why the prior work does not settle it | Measured by |
|---|---|---|---|
| **F1 — Substrate** | A bitemporal, evidence-grounded, lineage-deletable store is realisable with database-enforced invariants, measurable cost, and **no forking across agent domains**. | v1 showed the first half; nobody has published the generality evidence. | `S1`–`S6`, G2 |
| **F2 — Epistemic separation** | Kind, retention, and epistemic lifecycle can be enforced as independent axes, so inferred artifacts **structurally cannot** masquerade as facts. | No inspected system separates the axes or gates rendering on admission policy. Eywa/MemTX govern *writes*, not *inference provenance*. | `S7`, `S8`, G1, G3 |
| **F3 — Verifiable abstraction** | Pattern discovery can be made falsifiable: support/opportunity ledgers, finite-sample confidence bounds, active refutation, held-out verification — and this beats free-form LLM reflection. | Reflection literature reports no precision against ground truth and no counter-machinery. | `S9`–`S13`, G4, G5 |
| **F4 — Evolving user model** | An evidence-grounded user model **improves contextual personalization, recommendation, and decision support over time**, tracks preference change, resists correction failure, and does not degrade under noise. | Personalization literature evaluates at a point in time, not longitudinally; user-modelling work rarely carries provenance or contestability; nobody measures stale-harm. | `S14`–`S19`, G4b, G5b |

**Thesis contribution in one sentence:**

> We show that separating memory kind, retention, and epistemic lifecycle into enforced
> axes lets one memory engine serve heterogeneous agents; that gate-then-verify
> discipline turns reflection into a measurable abstraction layer with bounded error;
> and that an evidence-grounded user model built this way measurably improves
> personalization over a static profile — while never presenting an inference as a fact.

---

## 2. Hypotheses

Primary endpoints are fixed before any test run (v1 freeze discipline). Contrasts named
`X-*` are ablations; all are registered before the test split is touched.

### F1 — Substrate

| ID | Hypothesis | Endpoint | Contrast | Note |
|---|---|---|---|---|
| `S1` | Retaining bi-temporal history helps questions about changed/past state; the present-state cost is bounded. | `TAA` per stratum, suite `T` | `P − A2` | **Reported as a frontier**, not a superiority test — the proposal concedes `A2` cannot answer these by construction. |
| `S2` | Hybrid dense+lexical retrieval recovers more gold evidence than either channel. | `Evidence Recall@k`, LongMemEval_S | `P − A5`, `P − A6` | Unchanged from v1 `H2`. |
| `S3` | The grounding policy reduces injected-write success without unacceptable legitimate rejection. | `ASR`, `LUAR`, suite `G` | `P − A3` | Unchanged from v1 `H3`. |
| `S4` | Deletion leaves no leak on enumerated in-boundary channels, **including through abstractions**. | Scenario-level leak rate, exact CP bound, 11 channels | `P` vs `A8` (must leak through an abstraction) | Extended from v1 `H4`. |
| `S5` | One schema + one engine serves two structurally different agent profiles with zero forks. | Dual-profile parity; isolation exposure = 0 | `P` conversational vs `P` coding | New. |
| `S6` | Retention tier never changes answerability; durability is purely economic. | Reachability-matrix equality across retention settings | `durable` vs forced-`working` | New; tested by `E-026`. |

### F2 — Epistemic separation

| ID | Hypothesis | Endpoint | Contrast |
|---|---|---|---|
| `S7` | The channel contract is airtight: no unverified inferred artifact reaches `facts` through any public API path. | Escape count under fuzz + red-team misuse | `P` vs `X-chan` (channels merged) |
| `S8` | Structural fact/hypothesis labelling improves **human trust calibration**: a planted false inference is detected more often than in a merged view. | Detection rate on a scripted false-inference set | Labelling on vs off |

**`S8` is optional and ethics-gated**, and the first draft replaced it with the
narrower `UM`-style construct validation. **Reinstated** at the user's implicit request
(the PDT is core), on the narrowest defensible form: not "does the user trust the
system" but "can the user *see* the difference." If ethics approval is unavailable, it
is dropped and `S7` carries F2 alone — stated as a limitation, not silently.

### F3 — Verifiable abstraction

| ID | Hypothesis | Endpoint | Contrast |
|---|---|---|---|
| `S9` | Gate-then-verify abstractions have higher precision than free-form reflection. | Precision@verified on `own_P` test split, cluster = scenario | `P` vs `X-freeform` |
| `S10` | **Opportunity accounting** (not mandatory counters) reduces false abstractions. | `FAR_τ` at `confidence_lower ≥ τ`, null suite + `own_P` | `P` vs `X-noopportunity` |
| `S11` | Active refutation + held-out verification removes overfit abstractions without materially reducing true-pattern recall. | Δprecision > 0, Δrecall ≥ −δ | `P` vs `X-noverify` |
| `S12` | The partially-identified bound is honest: the realized failure rate does **not exceed** `1 − confidence_lower` for admitted abstractions, measured on held-out data. | Empirical violation rate of the bound, with a binomial upper limit; and the gap between nominal and realized coverage | `P` vs `X-ignore-undetermined` (which admits on `k, o` only) |
| `S13` | Verified abstractions improve downstream task accuracy. | Task accuracy with vs without `hypotheses` channel | `P` vs `X-noabstraction` |

`S10` was **rewritten**: the first draft tested "mandatory counter-support," which is
the rule `STATISTICS.md` §2 rejects. The defensible claim is that *modelling
opportunity* reduces false abstractions — and it is testable against a system that only
counts support.

### F4 — Evolving user model

| ID | Hypothesis | Endpoint | Contrast |
|---|---|---|---|
| `S14` | Evidence-grounded user models improve personalization utility **over time** vs a static profile and vs no model. | `UM5` at t ≥ 20 interactions | `P` vs `X-static` vs `X-nomodel` |
| `S15` | Verified abstractions improve recommendation and decision support over asserted-only models. | `UM6`, `UM7` | `P` vs `X-asserted-only` |
| `S16` | An explicitly evolving model tracks preference change faster than a model with no revision. | `UM2` (latency), `UM3` (post-change accuracy at Δ ∈ {1,5,20}) | `P` vs `X-frozen` |
| `S17` | The model does not degrade under realistic noise, and over-personalization is bounded. | `UM1`/`UM5` at noise (b); `UM10` frontier vs diversity floor | `P` at (a)/(b)/(c) |
| `S18` | Sticky dissent makes correction reliable: a rejected element is not re-inferred. | `UM8 = 0` within K reflection cycles | `P` vs `X-nosticky` |
| `S19` | The model survives wholesale regime change. | `UM9` | `P` vs `X-frozen` |

**`S14` is the load-bearing claim for the project's stated purpose.** If the model
recovers preferences but does not improve decisions, that is a major reportable finding
and the plan says so before running it.

---

## 3. Methodological corrections (consolidated)

All statistical detail now lives in `STATISTICS.md`; this section states only what
changed and why, so the research program is auditable against its own earlier draft.

| # | First-draft position | Corrected position | Why |
|---|---|---|---|
| 1 | Mandatory non-empty counter-support | **Three-state ledger** (support/counter/undetermined/opportunity) + active refutation | Mandatory counters conflate absence-of-opportunity with counterevidence, are unsatisfiable for stable preferences, and incentivise fabrication |
| 2 | ECE as an admission gate | **Exact CP upper bound on failure rate** + closed testing; ECE demoted to monitoring | ECE is a batch statistic, circular as an admission rule, gameable, and undefined at n ≈ 12 |
| 3 | Split conformal as the verification check | **Blocked/adaptive conformal for intervals only**; never for admission; **hierarchical beta-binomial** for sparse users | Exchangeability fails under temporal correlation; single-user data is too sparse for any non-hierarchical method |
| 4 | "Held-out split disjoint from support" | **Temporally and contextually disjoint**, plus base-rate baseline, block-permutation null, and a clean negative-control slice | Item-disjointness admits near-duplicate leakage and cannot detect a claim that predicts everywhere |
| 9 | **r3:** `u` treated as success in the admission bound | **Partial identification**: `u` counted as failure in the upper bound; `coverage` is a gate | Unknown observations must never be credited. `s=5,k=0,u=45` was admitted at `τ=0.90`; correct bound 0.033 |
| 10 | **r3:** one-sided 95% and two-sided 95% mixed | **One convention: one-sided 97.5%**, shared with v1 deletion | Mixed conventions moved the zero-counter threshold 36→29 and made inference and deletion incomparable |
| 11 | **r3:** "exact" claimed for CP under autocorrelation | **Qualifiers mandatory** (`STATISTICS.md` §5.4) | CP is exchangeability-exact only; positively autocorrelated data make it anti-conservative |
| 5 | Fixed-α re-testing each night | **Always-valid inference** (alpha-spending or confidence sequences) | Continuous monitoring invalidates fixed-α tests — the same multiplicity error v1's protocol was built to avoid |
| 6 | `S1` as a superiority test | **Frontier** (quality vs storage/latency/present-state cost) | The effect is near-tautological; the interesting quantity is the cost |
| 7 | `H4` channels = 7 | **11**, including abstraction statements and verification records; positive control must leak **through an abstraction** | Cognition adds leakage paths; v1's depth-3 bound would leave them alive |
| 8 | Cognition quality measured by precision only | Precision **with its base rate**, `FAR_τ`, Brier, per-facet reliability | Precision alone is uninterpretable |

---

## 4. What would falsify the direction

Stated in advance so the plan cannot quietly redefine success.

| Failure | Consequence |
|---|---|
| G2 fails — coding profile needs engine changes | Generality narrows to text-grounded domains; document why |
| G4 fails — confabulation not suppressed on the null suite | Abstraction layer is not shippable; F3 withdrawn |
| G4b fails — the `own_U` instrument cannot discriminate its own baselines | F4 has no measurement; it is reported as unmeasured, not as supported |
| `S9` fails — free-form reflection matches gated abstraction | Verification apparatus is not earning its cost; F3 becomes "we measured that it does not help" |
| **`S14` fails — the model does not improve decisions** | **The project's stated purpose is not achieved.** F4 becomes a negative result: the model recovers preferences but personalization utility is nil. This is the most consequential possible outcome and must be reported prominently, not buried. |
| `S18` fails — corrections are re-learned | Safety defect; blocks shipping the user model at all |
| `S13` fails — abstractions do not help tasks | Cognitive layer removed from the product; engine ships as substrate; F3 negative |
| `S7` fails under fuzzing | Correctness bug; fix before any claim |
| `S12` fails — bounds are systematically exceeded on held-out data | Statistics are wrong (or dependence was under-corrected); all F3/F4 numbers are void until fixed |

Any of these is a legitimate thesis outcome. Preserving v1's best quality — that a null
result is publishable — is a requirement, not a nicety.

---

## 5. Evaluation inventory

| Suite / instrument | Status | Purpose |
|---|---|---|
| `T` temporal-change | exists | `S1` |
| `G` write-governance | exists | `S3` |
| `D` deletion | exists, **extended to 11 channels** | `S4` |
| `I` isolation | exists, **extended** (path-prefix, entity opacity) | `S5`, `I7` |
| LongMemEval / LoCoMo | exists | `S2`, descriptive |
| `own_P` generative-process | **new** (`C-011`) | `S9`–`S13` |
| Null suite | **new** (`C-012`) | G4, `S10` |
| `own_U` user-model simulator | **new** (`UM-001`) | `S14`–`S19` |
| Channel-escape fuzz | **new** (`E-035`) | `S7` |
| CODING benchmark | **new** (`E-022`) | `S5` |
| False-inference trust set | **new**, optional (`S8`) | fact/hypothesis labelling detectability |
| Substrate statistics (`SD-001`–`SD-005`) | **new** | F3/F4 validity |
| `scripts/check_statistics.py` | **new** | Reproduces every bound quoted in `STATISTICS.md` §10; ties the new inference statistics to the verified v1 `cp_bounds.py` |
| `X-ignore-undetermined` ablation | **new** (`SD-006`) | `S12` positive control: the corrected bound must beat the one that credits unknowns |

All new suites keep the script-first discipline: seeded generator → scripted ground
truth → rendered text → verification that every scripted element is expressed → sample
human check. No gold label is ever derived from rendered text. **The user-model
simulator adds one requirement: the latent user is provably recoverable** (an oracle
solver recovers it from the same event stream), or the suite is not solvable and `S14`
would be measuring noise.

---

## 6. Deliberate omissions

- No claim about understanding, sentience, or behavioural simulation.
- No claim that inferred user models are true; only that they are bounded,
  falsifiable, contestable, and *useful* (or measurably not).
- No comparison against 2026 preprints without pinned runnable releases, beyond the
  descriptive positioning in the v1 evidence ledger.
- No inference over model parameters.
- No multi-principal access-control claims beyond scope isolation.
- **No claim that personalization is universally good.** `UM10` exists precisely to
  make the intervention cost visible, and a finding that a quieter model is preferred is
  a legitimate result.
