# ChronaMind V2 — Evolving User Models (Personal Digital Twin)

**Status:** Normative and binding. V2 was approved as the canonical plan on 2026-10-31 (see `APPROVAL.md`). *Until r4.2 this read "within the V2 proposal (V2 overall: Proposed)".* Defines the user-model artifact, the personalization capability
(F4), and the evaluation instrument that makes "evolving" a measurable word.
**Supersedes:** r1's treatment of the PDT as an optional demonstration — specifically
`A-011` being cuttable and de-scope ladder item 4 removing user-model inference. The
formal record is `V2-D26` (capability is core) and `V2-D33` (sticky dissent). See
`SYNC_LOG.md` §2 for the audit trail.

---

## 1. What the user model is — and what it is not

The user model is a **structured, evidence-grounded, temporally versioned model of a
person's stated and inferred preferences, goals, constraints, context, and decision
patterns.** It exists so that an agent can act *more appropriately for that person*
over time, and so the person can inspect, contest, and correct it.

It is explicitly **not**:

| Not this | Why |
|---|---|
| A behavioural simulator | Predicting arbitrary behaviour is AI-complete, unfalsifiable at useful precision, and ethically hazardous. We never claim it. |
| A personality profile | We store *decision-relevant* regularities with support and scope, not trait scores. No Big-Five, no "you are an introvert." |
| A static profile | It is versioned, decaying, and revisable; "evolving" is the point. |
| A hidden model | Every element is inspectable, contestable, and correctable; correction latency is measured (§6). |
| The engine | It is a Tier-B capability and a Tier-C product surface; the engine (Tier A) does not know what a user model is. |

**The distinction the user asked for, made operational:** the line is not
"preferences good, prediction bad." It is **decision relevance + provenance +
contestability**. A claim may enter the user model only if (a) it is about what the
user prefers/wants/needs/constrains/does repeatedly, (b) it can carry support and
scope, and (c) the user can see and reject it in one action.

---

## 2. Structure of the user model

The user model is **not a table**. It is a named view over Tier-A artifacts plus a
small number of user-model-specific records. This matters: if the user model were its
own table, deletion, provenance, and lifecycle would be reimplemented, and the engine
would stop being general.

| Facet | Content | Artifact form | Typical lifecycle |
|---|---|---|---|
| **Preferences (stated)** | "prefers terse code-review feedback" | `claim`, `facet=preference` | asserted → retired on change |
| **Preferences (inferred)** | "prefers bullets in morning sessions" | `abstraction`, `facet=preference` | inferred → verified → stale |
| **Goals** | "shipping a thesis by June" | `claim` (asserted, with event range) | asserted → retired/expired |
| **Constraints** | "no meetings before 10:00" | `claim` (asserted) + `procedure` for compliance | asserted |
| **Context** | "current project = ChronaMind; role = research lead" | `claim` in scope, short-lived | session → durable promotion |
| **Decision patterns** | "consistently rejects scope cuts that remove evaluation rigor" | `abstraction` over dismissed/accepted proposals | inferred → verified |
| **Meta-preferences** | "wants to be told when a preference conflicts with a goal" | `claim` (asserted) | asserted |

### 2.1 Canonical facet vocabulary (single source of truth)

**Six facets, zero new tables.** Every Tier-A artifact may carry a nullable
`um_facet`:

```text
preference | goal | constraint | context | decision_pattern | meta_preference
```

**This list is normative and appears nowhere else.** `ARCHITECTURE_V2.md` refers to it
by pointer; `E-018`'s conformance test asserts the enum has exactly these six values;
any document that names a seventh is wrong.

**`inferred_preference` is deliberately not a facet** (it was in r1 and r2, and it was a
modelling error). Inference is a *lifecycle*, not a facet: an inferred preference is
`um_facet='preference' ∧ lifecycle='inferred'`. Keeping it as a facet would have
duplicated the lifecycle axis inside the facet axis, made the two independently
assignable, and thereby allowed the contradictory state
`um_facet='inferred_preference' ∧ lifecycle='asserted'` — an inferred preference asserted
as fact, which is precisely what `I1′` forbids. The row below that used to say
"Inferred preferences | `abstraction`" is now expressed as
`facet=preference, kind=abstraction`.

**Why facets rather than kinds.** A goal and a preference have the same storage
semantics and different *use* semantics (goals are checked for conflict; constraints
are checked before proposing actions). That is a query-time concern, so it belongs in
a tag, not in the kind axis. Adding them as kinds would resurrect `CRITIQUE.md` F1.

---

## 3. Lifecycle: what "evolving" requires

An evolving model needs more than create/delete. It needs four operations that V2 did
not previously specify:

| Operation | Semantics | Recorded |
|---|---|---|
| **Strengthen** | New evidence raises confidence; support set grows. | Evidence row + confidence delta |
| **Weaken** | New counterevidence lowers confidence below threshold → `provisional`, or → `refuted`. | Counter row + delta |
| **Revise** | Scope narrows/widens ("always" → "in code review"). Creates a *new* artifact, retires the old, links `refines`. | New artifact + association |
| **Contest** | User assertion overrides an inference. The user's `dissent` operation (`API_V2.md` 4.6, `V2-D46`) moves the inference `inferred → retired` with `reason='user_dissent'` and records the negative constraint; a new asserted claim is created with `assert` when the user states the correction. | Adjudication record |

**The critical rule (this is the one that makes it trustworthy):**

> **User dissent is authoritative and sticky.** When a user rejects an inferred
> preference, the inference is `refuted` with `reason='user_dissent'`, and the
> producing *operator* records a **negative constraint** that suppresses re-derivation
> of the same predicate over the same scope until new grounded evidence arrives.
> Without this, the daily reflection job re-infers the rejected preference overnight,
> and the model becomes adversarial.

This is measured (§6, `P7`) and is the single highest-risk failure mode of a
self-improving user model. v1 had no equivalent because it had no inference.

**Decay.** Inferred elements carry `half_life` by facet (preferences ≈ 180 d,
constraints ≈ 365 d, context ≈ 14 d, decision patterns ≈ 365 d). Decay changes
*retrieval salience and confidence*, never lifecycle by itself; an expired inference
becomes `stale`, is re-verified or retired, and is never silently deleted.

---

## 4. Personalization semantics (what the model is for)

Three distinct uses, with distinct failure modes — they must not be conflated into
"personalization quality":

| Use | Definition | Failure if wrong |
|---|---|---|
| **Contextual personalization** | Adapting *form* (length, format, timing, channel) to the user's stated/inferred preferences. | Annoyance; low cost; correctable. |
| **Recommendation** | Proactively suggesting an option the user did not ask for. | Distraction; wasted attention; erodes trust if wrong repeatedly. |
| **Decision support** | Surfacing relevant precedents, constraints, and goals when the user faces a choice — **not** making the choice. | Highest cost: a wrong precedent can mislead. Therefore decision support requires `verified` abstractions only. |

**Escalation rule:** `provisional` abstractions may influence form; only `verified`
abstractions may influence recommendations; decision support additionally requires the
artifact to be `verified`, unexpired, and to render with a visible provenance summary.
This is a three-level trust ladder matched to three-level consequence — the engine
enforces it, not the prompt.

---

## 5. Evaluation instrument (`own_U`, new suite)

The user asked whether the model *improves over time*. That is a longitudinal claim,
and no public benchmark measures it. We build the instrument (this is the W4
ruler-first principle applied to F4).

### 5.1 Generator

Script-first, seeded, with a **planted latent user** that exists in the generator and
is never visible to the system:

```text
latent_user = {
  stable_preferences:   {id: (value, scope, strength)},
  evolving_preferences: {id: [(t_from, value)]},     # ground-truth change points
  goals:                [{goal, deadline, priority}],
  constraints:          [{constraint, condition}],
  decision_policy:      f(context) -> choice      # deterministic, auditable
}
```

The event stream is generated by sampling *behaviours from the decision policy*, not
by writing dialogue that states preferences. This is what makes it a test of inference
rather than of extraction: the system must recover the latent structure from what the
user *does*, with only a minority of preferences ever stated explicitly.

Rendered at three noise levels: (a) consistent, (b) 10% policy-violating noise
(users are inconsistent), (c) 10% change points (users evolve). **All metrics are
reported per noise level** — a model that is precise only on (a) is not an evolving
user model.

### 5.2 Metrics (each defined once, in code)

| ID | Metric | Definition | Target direction |
|---|---|---|---|
| `UM1` | **Preference recovery** | Precision/recall of recovered preferences at facet level, vs planted latent set, at each time point. | ↑ |
| `UM2` | **Change detection latency** | Turns (or days) between a ground-truth change point and the model's revision. | ↓ |
| `UM3` | **Post-change accuracy** | Preference accuracy measured *only* after a change point, at t+Δ for Δ ∈ {1, 5, 20} interactions. | ↑ |
| `UM4` | **Stale-harm rate** | Fraction of personalization actions that used a preference the user has since abandoned. | ↓ |
| `UM5` | **Personalization utility** | On held-out decision episodes: does the agent's chosen action match what the latent decision policy prefers, vs an unpersonalized baseline? | ↑ |
| `UM6` | **Recommendation acceptance** | Fraction of recommendations the latent policy accepts, vs baseline. | ↑ |
| `UM7` | **Decision-support agreement** | When the agent surfaces precedents/constraints for a choice, does the surfaced set contain the ones the latent policy uses? | ↑ |
| `UM8` | **Correction persistence** | After a user dissent, does the model re-infer the same rejected element within `K = 7` reflection cycles? **Must be 0.** | ↓ (0) |
| `UM9` | **Drift robustness** | `UM1`/`UM5` under a regime switch (user's policy changes wholesale at t = T). | ↑ |
| `UM10` | **Exposure/utility frontier** | Utility as a function of number of interventions — does a more active model earn its intrusions? | frontier |

**`K = 7` is pre-declared and frozen before the test run** (it is one `D-*`-style pilot
constant, not a free parameter). If the reflection scheduler runs more often than daily,
`K` is expressed in cycles rather than days so the test is scheduler-independent.

`UM5`–`UM7` are the user's actual concern: *does it help?* `UM1`–`UM4` explain why or
why not. `UM8` is the safety gate (a model that ignores correction is worse than no
model). `UM10` prevents "more personalization is always better" from being assumed:
every intervention is a cost, and the frontier makes the trade visible.

### 5.3 Human validation (optional, ethics-gated)

A small study validates that `UM5`/`UM7` proxies track human judgement of
"appropriate for me." **Not required for any claim** — the latent-user instrument
carries the quantitative weight, and the study only checks construct validity. This
replaces the old `S8` trust study with something narrower and better defined.

---

## 6. Safety, privacy, and the honest limits of the PDT

| Risk | Mitigation | Measured by |
|---|---|---|
| **Over-personalization / filter bubble** | `UM10` frontier; a declared diversity floor on recommendations. | `UM10`, diversity audit |
| **Confident wrong inference about a person** | Three-level trust ladder; `provisional` never reaches recommendations; every element shows support. | `UM1` precision at each level |
| **Correction ignored / re-learned** | `V2-D33` sticky dissent + negative constraints. | `UM8` (must be 0 within `K = 7` reflection cycles) |
| **Sensitive-attribute inference** | The engine refuses to create abstractions over protected attributes (a declared deny-list on `ENTITY`/predicate space), and the profile ships with it on. | Conformance test `E-018` |
| **Cross-scope leakage of personal data** | Scope isolation + entity opacity (`ARCHITECTURE_V2.md` §5.2). | Suite `I` |
| **Deletion leaves the model intact** | The user model is *views over artifacts*, so deletion closure applies automatically — there is no separate model store to miss. | Suite `D` extension |
| **User cannot see what is inferred** | PDT hypothesis panel; export as a plain-language "what I think I know about you, and why." | `A-011` acceptance |

**Explicit limit, stated in the product:** the PDT is a *record of what the system has
learned and inferred about you*, with confidence and provenance. It is not a copy of
you, and it does not speak for you. Every element is editable and deletable.

---

## 7. Research hypotheses (F4 family)

| ID | Hypothesis | Endpoint | Contrast |
|---|---|---|---|
| **S14** | Evidence-grounded user models improve personalization utility over time versus a static profile and versus no model. | `UM5` at t ≥ 20 interactions | `P` vs `X-static` vs `X-nomodel` |
| **S15** | Verified abstractions improve recommendation and decision support over asserted-only models. | `UM6`, `UM7` | `P` vs `X-asserted-only` |
| **S16** | An explicitly evolving model tracks preference change faster than a model with no revision mechanism. | `UM2`, `UM3` | `P` vs `X-frozen` |
| **S17** | The model does not degrade under noise; over-personalization is bounded. | `UM1`/`UM5` at noise (b); `UM10` frontier | `P` at noise levels (a)/(b)/(c) |
| **S18** | Sticky-dissent makes correction reliable. | `UM8` = 0 within K cycles | `P` vs `X-nosticky` |
| **S19** | The model is robust to wholesale regime change. | `UM9` | `P` vs `X-frozen` |

`S14` is the load-bearing claim for the project's stated purpose. If `S14` fails — the
model recovers preferences but does not improve decisions — that is a major, reportable
finding, and the plan says so before running it.

**Gate G5b (new, F4):** `UM5` improvement over `X-nomodel` is statistically positive
(Holm-adjusted across the F4 family, cluster = simulated user), `UM8 = 0`, and `UM4`
does not increase between noise levels (a) and (c) by more than a pre-declared margin.

---

## 8. De-scope protection

The user was right that the PDT must not vanish. The corrected ladder rule:

> **No scope cut may remove a *capability* required by a research hypothesis.** Only
> *surfaces* are cuttable.

| Element | Cuttable? | Reason |
|---|---|---|
| `C-024` user-model inference | **No** | Required by `S14`–`S19` |
| `own_U` generator + metrics | **No** | It is the only instrument for `S14`–`S19` |
| Sticky-dissent mechanism | **No** | Safety + `S18`; removing it makes the product adversarial |
| PDT web UI (`A-011`) | Yes → API + CLI inspector | Capability survives; polish does not |
| Recommendation surface | Yes → decision support only | Reduces scope, keeps the harder claim |
| Human validation study | Yes | Construct validity only; not a claim |
