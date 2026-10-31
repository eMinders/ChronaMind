# ChronaMind V2 — Decision Records

**Convention.** `V2-Dnn` = new decision. `SUPERSEDES` entries state exactly which v1
decision they replace, and what remains in force. `PLAN/DECISIONS_REGISTER.md` is not edited
apart from one additive direction notice at the top; it remains the v1 record.

**Scope of "ADOPTED" (r4.1, updated r4.2).** `ADOPTED` means the review rounds settled the
decision. When r4.1 was written, V2 was still `Proposed`, so these records were not yet
binding. On **2026-10-31** the Project Owner approved V2 as the canonical implementation plan
([`APPROVAL.md`](APPROVAL.md)). Every `ADOPTED` record here is now **binding for
post-v1.0.0 work**, and so is "Normative" in `STATISTICS.md` and `USER_MODEL.md`.
Decisions marked open (`V2-D13`, `V2-D14`, `V2-D16`, `V2-D17`, `V2-D30`–`V2-D32`, `V2-D34`)
remain open. Approving the plan does not resolve them.

---

## V2-D01 — Orthogonal memory axes (kind × retention × lifecycle)

**Status:** ADOPTED · **Supersedes:** the `layer` enum in `MemoryLayer`
(`transactions.py:23`) and `memory_version.layer` (`001_full_schema.sql:53`); the
`persona` layer and ablation `A10`; `D-04` (persona OFF) is moot because `persona` is
no longer a layer — user modelling becomes `kind='abstraction'` produced by Tier B.

**Decision.** A memory is a point in `(kind, retention, lifecycle, scope, derivation)`.
`kind ∈ {episode, claim, procedure, abstraction}`;
`retention ∈ {working, session, durable}`;
`lifecycle ∈ {observed, asserted, inferred, contested, retired}`.

**Rationale.** The six requested memory types are not peers: two describe kind, two
describe retention, one describes lifecycle, one describes belief production. A single
enum forces a 6-way dispatch in every query and makes adding a new capability a schema
migration. See `CRITIQUE.md` F1.

**Consequences.**
- `archived` survives as a *retention* value only, for backward compatibility.
- Query code selects on independent predicates rather than one enum.
- `A10` is retired as an ablation; the persona feature becomes "user-model
  abstractions" and is gated by the F3 verification rules instead.
- Estimated cost: 1 migration wave (W1), one backfill, one conformance suite.

**Rejected alternatives.**
- *Add `working`/`procedural` to the existing enum.* Rejected: reproduces F1 at scale.
- *Six separate tables, one per type.* Rejected: temporal predicates, exclusion
  constraints, RLS, and deletion closure would be implemented six times, and
  cross-type lineage would need a union type.
- *Keep `semantic` and add an `inferred` boolean.* Rejected: conflates epistemic status
  with kind, which is precisely the error the vision names.

---

## V2-D02 — Two admission policies: grounding and soundness

**Status:** ADOPTED · **Amends:** v1 gate (`RL-060`, `engine/gate.py`) and v1 Invariant
1 (traceability). The v1 gate becomes the *grounding* policy, generalised over source
types; a new *soundness* policy governs inferred artifacts.

**Decision.** Grounded artifacts (observed/asserted) must satisfy evidence support,
assertion mode, instruction screen, and conflict relation — the v1 checks, unchanged
in substance. Inferred artifacts must satisfy: ≥1 source memory, a registered
method + version + parameter hash, recorded support **and** counter-support sets, and
calibrated confidence. No path exists from `inferred` to `asserted`.

**Rationale.** In v1, derived rows bypass the gate entirely (`commit_initial_version`),
so the least-validated content is the most speculative. For a conversational assistant
that is tolerable; for an engine whose differentiator is reflection it inverts the
guarantee.

**Consequences.** The `METHOD` registry (physical table; `INFERENCE_METHOD` was the
first-draft name, retired in `ARCHITECTURE_V2.md` §2.1) becomes append-only and versioned, which
is what makes cognition ablations replayable (same principle as `RL-032`'s extraction
cache). Promotion from hypothesis to fact requires *new evidence* creating a new
artifact plus a `corroborated_by` association — never a status flip.

**Rejected alternative.** *Gate inferred content with the same checks.* Impossible: it
has no turn evidence by construction. *Let inferred content through and rely on
labelling.* Rejected: labels are what failed in v1-era systems; enforcement must be
structural (I1′).

---

## V2-D03 — Abstraction is a first-class artifact with mandatory counter-support

**Status:** ADOPTED · **Extends:** `RL-061` consolidation (which remains valid for
*compaction*, its actual purpose) and Invariant 10 branch `(C)`.

**Decision.** `kind='abstraction'` carries structured predicate, support set with
weights, counter set (non-empty required), method record, calibrated confidence,
verification status, verification split id, and expiry. Verification requires a
held-out split proven disjoint from the support set, plus a statistical win over the
abstraction's own support baseline.

**Rationale.** `CRITIQUE.md` F3: consolidation as implemented compacts `(subject,
predicate)` pairs and cannot express a conditional, quantitative, cross-subject claim.
Without support/counter/verification fields, "reflection" can only produce
unfalsifiable prose.

**Consequences.** Abstraction creation cost is dominated by verification, not
generation — deliberately, since generation is cheap and error-prone. Expiry is
mandatory because a preference from two years ago is not a current preference.

**Rejected alternative.** *Store abstractions as `semantic` rows with a free-text
insight.* That is `X-freeform` (`C-015`), retained only as the baseline to beat.

---

## V2-D04 — Association graph is separate from provenance

**Status:** ADOPTED · **Extends:** `MEMORY_DERIVATION` (unchanged, still the only
deletion-cascading edge) with a new `ASSOCIATION` table.

**Decision.** Provenance edges cascade under deletion. Associations do not; they are
deleted only when an endpoint is deleted. Retrieval may traverse associations; it may
also traverse provenance downward for drill-down, always rendering provenance.

**Rationale.** Conflating them either makes deletion accidentally transitive through
semantic relatedness (over-deletion, data loss) or makes the retrieval graph
deletion-safe only by being useless.

**Consequences.** Two edge tables, two traversal APIs, two test suites. Association
growth is bounded by a profile-declared cap per artifact to prevent quadratic blow-up.

---

## V2-D05 — Scope hierarchy replaces bare `user_id`

**Status:** ADOPTED · **Preserves:** Invariant 7 (RLS), suite `I`, cross-user exposure
= 0.

**Decision.** `scope.path` materialised path (`/u/<id>/p/<project>`), `scope.kind`,
`visibility ∈ {private, project, shared}`; RLS on path prefix; `user_id` retained as a
generated column for v1 compatibility.

**Rationale.** `CRITIQUE.md` F5: multi-agent and per-repository memory cannot be
expressed without abusing the tenant key, which then breaks the isolation claim.

**Rejected alternative.** *Add `project_id` as a second tenant key.* Rejected: nested
scopes (task within project within user) need a hierarchy, and a flat second key does
not compose.

---

## V2-D06 — Three clocks (event / observation / belief)

**Status:** ADOPTED · **Renames:** `valid_range` → `event_range`; adds `observed_at`.

**Decision.** Event time = when it was true; observation time = when the system first
saw the source; belief time = when the system held it (transaction clock only, never
caller-supplied).

**Rationale.** Non-conversational agents routinely ingest things that happened before
they were observed, and reflection needs an abstraction's belief interval independent
of its evidence's. v1 fuses event and observation time, which is invisible in chat and
wrong for tools.

**Consequences.** Four v1 predicates survive with a column rename; v1 tests remain the
regression suite. Epicentre of risk: the exclusion constraint now references
`event_range`, so M3 must be applied with the constraint dropped and recreated in one
transaction, and `validation/bitemporal_checks.sql` must be replayed against it.

---

## V2-D07 — Cognition is a client, not a privileged component

**Status:** ADOPTED · **Extends:** `D-ARCH` / `PLAN/INDEPENDENCE_BOUNDARY.md` from a
repo-boundary rule to a **tier** rule.

**Decision.** Tier B (reflection, consolidation, pattern discovery, user modelling)
uses only: `query`, `infer`, `verify`, `retract`, `stats`. It has no DB credentials
beyond the public API's grants, no engine imports, and no privileged read path.

**Rationale.** This is what keeps the engine general. If reflection needed a special
table or a private index, "reflection" would be part of the product definition and the
product would stop serving agents that do not want it.

**Consequences.** Mechanical CI enforcement (import boundary + DB role permission
audit, G7). Tier B is deletable; the engine still ships. Every cognition feature must
be expressible through five API calls, which will feel restrictive and is the point.

**Rejected alternative.** *Make cognition a first-class module sharing the schema.*
Cheaper to build, and it permanently couples the engine to one theory of cognition.

---

## V2-D08 — Profiles as data, never forks

**Status:** ADOPTED. **Decision.** A profile is config: kinds in use, extractors,
inference operators, retention defaults, scope root, render conventions. No code path
branches on profile name.

**Rationale.** "General-purpose" is only demonstrable if the difference between
applications is configuration. G2's fork audit enforces it mechanically.

---

## V2-D09 — Deletion closure is correctness-unbounded

**Status:** ADOPTED · **Supersedes:** `MAX_LINEAGE_DEPTH = 3` (`engine/deletion.py:40`)
as a *correctness* bound.

**Decision.** Depth remains as a performance parameter, but closure completeness must
hold for arbitrary chains; a test builds a chain longer than any configured bound and
asserts the closure is complete. Abstractions whose support falls below the
verification threshold become `stale`, and are re-derived or retired.

**Rationale.** With depth 3, an abstraction at depth 4 silently survives deletion of its
sources — the exact failure class Invariant 8 exists to prevent, and the one most likely
to appear once cognition adds derivation layers.

**Consequences.** Deletion cost grows with closure size; that is why the bound existed.
Mitigation: iterative breadth-first walk with an index on `derivation(source)` and a
reported closure-size histogram. If closure sizes prove pathological, the correct fix is
asynchronous closure with a completion barrier, **not** a silent depth cap.

---

## V2-D10 — Retention is orthogonal to answerability

**Status:** ADOPTED · **Generalises:** Invariant 10 `(S)∨(C)` from archival to retention
and from claims to all kinds.

**Decision.** Changing retention never changes what a returned-time query can answer.
`working` → `session` → `durable` promotion is an economic decision with a recorded
reason; demotion is only permitted if the artifact remains answerable through a
compaction artifact (the `(C)` branch, generalised).

**Rationale.** `CRITIQUE.md` F6: `archived` currently serves both belief and cost. Once
those are separated, the no-silent-loss guarantee must be restated in terms of retention
or the separation re-creates the v1 bug in a new place.

---

## V2-D11 — Per-stage retrieval with explicit channel separation

**Status:** ADOPTED · **Extends:** `RL-050`/`RL-051`, `D-ENG-03` (RRF k=60 retained as
one fusion channel, not the whole retrieval design).

**Decision.** Five stages (plan → candidate generation → expand → filter/re-rank →
assemble), three output channels (`facts`, `hypotheses`, `procedures`), provenance bundle
and cost always returned.

**Rationale.** `CRITIQUE.md` F7: single-shot flat retrieval cannot answer
"what did we try before," cannot drill down from an abstraction, and cannot enforce the
fact/hypothesis distinction at the result boundary.

**Consequences.** RRF is preserved as the fusion operator inside candidate generation and
remains measurable as `A7`-style ablation; the v1 measured latencies become the baseline
that G3 must not regress by more than 2×.

---

## V2-D12 — Verification precedes feature

**Status:** ADOPTED (process decision). **Decision.** The measurement instrument for
abstraction (metrics module + generative-process suite + null suite, W4) ships **before**
the cognition runtime (W5). No reflection code merges before `C-013` and `C-012` exist.

**Rationale.** Every system in the reviewed literature that reports reflection quality
reports it qualitatively. Building the ruler first is the only way to make an F3 claim at
all, and it prevents shipping a feature whose error rate is unknown.

**Consequences.** W4 is not deferrable and not parallelisable with W5. In the de-scope
ladder it appears only after every application task.

---

---

## 2. Decisions added or amended in the consistency review


These amend §1 of this document and are the authoritative versions where they conflict.
Each cites the evidence that forced it.

### V2-D18 — Three-state evidence ledger (amends V2-D02, V2-D03)

**Supersedes:** the mandatory non-empty counter-set rule, and the `counter_count` /
`counter_set` fields as specified.

**Decision.** Every abstraction carries `support` (`s`), `counter` (`k`), `undetermined`
(`u`), and `opportunity` (`o = s + k + u`) counts; `coverage = (s+k)/o`; and a recorded
`refutation_attempts` count. **Counter-support must be searched for and recorded, never
required to be non-empty.**

**Undetermined outcomes are missing data and are counted as failures in every upper
bound** (`STATISTICS.md` §2.2). The estimand is the failure probability conditional on
an opportunity arising, over the declared reference window; no missing-at-random
assumption is made. Admission depends on `o`, `k`, `u`, `coverage`, and attempted
refutations — never on `k` and `o` alone.

**Rationale.** Mandatory counters (i) conflate absence-of-opportunity with
counterevidence, (ii) are unsatisfiable for genuinely stable preferences, and (iii)
incentivise an LLM under pressure to manufacture counterexamples. Zero counters with
`o = 8` and zero counters with `o = 40` are materially different facts, and only the
opportunity count distinguishes them.

**Consequences.** `counter_absence_basis ∈ {high_opportunity, low_opportunity}` is
recorded and rendered. `S10` is re-stated to test opportunity accounting rather than
mandatory counters. Metrics gain `coverage` and `refutation_attempts`.

### V2-D19 — Admission by exact finite-sample bound (supersedes the ECE gate)

**Decision.** `confidence_lower = 1 − CP_upper(k + u, o)`, computed with the repo's
existing Clopper–Pearson implementation at **one-sided 97.5%** (the same convention as
the v1 deletion guarantee). Admission requires `confidence_lower ≥ τ`,
`coverage ≥ τ_coverage`, `refutation_attempts ≥ 3`, a clean negative-control slice, and
(when `k = 0`) `CP_upper(u, o) ≤ 1 − τ` — with closed
multiple testing across candidates, reusing the existing Holm machinery.

**Correction (r3):** the r1/r2 form `1 − CP_upper(k, o)` treated `u` as successes and
admitted `s=5, k=0, u=45` at `τ = 0.90`; the correct bound there is 0.033.
Retrospective **exactness** is claimed only under exchangeability; under temporal
autocorrelation the bound is not exact and the design-effect or block-bootstrap
correction in `STATISTICS.md` §5.4 applies.

**Rationale.** ECE is a *set-level* statistic, binning-dependent at small `n`,
circular as an admission rule (calibration needs verification outcomes, which need
admission), and trivially gamed (always predict 0.5). It cannot decide single items.

**Consequences.** ECE and Brier survive as **monitoring** metrics that trigger
recalibration and operator review — never admission. This also makes the inference
epistemology identical to the deletion guarantee's, which the first draft lacked.

### V2-D20 — Repeated looks must be always-valid

**Decision.** The reflection scheduler uses alpha-spending over a declared look count or
always-valid confidence sequences. Fixed-α re-testing of the same hypothesis is
forbidden.

**Rationale.** Continuous nightly re-evaluation with `p < 0.05` inflates false positives
without bound — the exact multiplicity error the v1 protocol exists to avoid.

### V2-D21 — Verification is temporally/contextually disjoint with two baselines

**Decision.** A verification split must be disjoint **in time and context**, not only in
items. Verification compares against (a) the base-rate predictor and (b) a
**block-permutation null** preserving prevalence and autocorrelation, and requires a
clean negative-control slice.

**Rationale.** Item-disjointness admits near-duplicate leakage; iid permutation of
temporally correlated data is anti-conservative; and without a negative-control slice an
abstraction that predicts everywhere passes.

### V2-D22 — Sparse users via hierarchical partial pooling

**Decision.** Per-user rates are shrunk toward a facet-level prior (`Beta(μ_facet·κ_facet,
(1−μ_facet)·κ_facet)`) with `μ`, `κ` estimated across users **in aggregate only**.

**Rationale.** No single-user method fixes `n ≈ 5–50`. Hierarchy is the only
statistically valid way to borrow strength, it self-disables as data accumulates, and it
makes cold start honestly wide rather than falsely confident.

**Consequences.** Cross-user learning enters the engine only as facet-level parameters,
never as per-item transfer, and this is a privacy boundary as much as a statistical one.

### V2-D23 — Conformal only in blocked/adaptive form, and never for admission

**Decision.** Blocked calibration + adaptive conformal inference (+ weighted conformal
under covariate shift) for **interval prediction**; never as the admission rule.

**Rationale.** Split conformal assumes exchangeability, which time-ordered user data
violates; ACI restores long-run coverage under drift. Conformal gives prediction sets,
not per-hypothesis error control, so it is the wrong instrument for admission.
Resolves `V2-D15`.

### V2-D24 — `stale` is derived from the provenance closure

**Decision.** `stale` is computed (`closure_hash(sources_now) ≠ closed_over_hash`, or
support below threshold, or source retired, or method version changed), never stored as
`verification_status`.

**Rationale.** A stored staleness flag can disagree with the graph, and deletion is
exactly what creates the disagreement — so storing it lets a `verified` abstraction
survive deletion of its sources.

### V2-D25 — Entities are principal-scoped and opaque

**Decision.** Entity ids are opaque, created only through the API, scoped to a
principal; attributes are never global; cross-scope linking requires caller
authorization on **both** scopes; entity existence is not queryable outside the caller's
read set.

**Rationale.** The first draft called `ENTITY` "canonical referents" with no tenant
scoping — a cross-tenant existence and attribute-count oracle.

### V2-D26 — The evolving user model is core, and its capability is uncuttable

**Decision.** F4 is a research family; `UM-010`–`UM-014`, `UM-001`, `UM-002`, and the
trust ladder are **not** de-scopeable. Only surfaces are (PDT web UI → API/CLI;
recommendations → decision support). The user model is a view over Tier-A artifacts, not
a store.

**Rationale.** The project's stated purpose is a Personal Digital Twin. The first draft
placed user-model inference at de-scope step 4, which would have removed the capability
while keeping the architecture that exists to support it — a plan that could succeed at
its own subgoals and fail its goal.

**Consequences.** New `own_U` instrument and metrics; new F4 hypotheses; a new gate
(G4b) that validates the instrument; and a new failure mode declared in advance (`S14`
fails ⇒ the stated purpose is not achieved).

### V2-D27 — Scope paths carry a mandatory trailing separator

**Decision.** `scope.path` ends with `/`; `CHECK (path LIKE '/%')` and
`path = '/' OR path LIKE '%/'`; prefix matching uses `path_with_prefix` with
`text_pattern_ops`.

**Rationale.** Without it `/u/4/` matches `/u/42/…` — a cross-tenant read, the most
severe defect found in this review.

### V2-D28 — Four retrieval channels, membership derived

**Decision.** `facts`, `hypotheses`, `procedures`, `conflicts`. Membership is a pure
function of `(kind, lifecycle, verification, staleness, association state)`. Contested
content appears **only** in `conflicts` (consistent with v1 Invariant 6). A verified
abstraction appears in `facts` only rendered **with its bound**.

**Rationale.** The first draft had three channels and no home for contradictions, and
left ambiguous whether a verified abstraction is a fact or a hypothesis.

### V2-D29 — Retention floors by kind

**Decision.** Only `episode` and `procedure` may be `working` or `session`; `claim` and
`abstraction` are always `durable` until explicitly `retired`.

**Rationale.** V2-D10 declares retention orthogonal to answerability; without a floor,
`working` expiry silently deletes knowledge, re-creating the v1 archival bug in a new
place.

### V2-D33 — Sticky dissent: a refuted user-model element cannot be silently re-learned

**Status:** ADOPTED (safety-critical).

**Decision.** When a user (or an authorized adjudicator) rejects an inferred user-model
element, the artifact becomes `refuted` with `reason='user_dissent'`, and a
`NEGATIVE_CONSTRAINT` row is written binding `(predicate, scope, method_family)`. The
reflection scheduler must consult `NEGATIVE_CONSTRAINT` before proposing a candidate over
that binding, and may only lift the constraint when **new grounded evidence** arrives
after the dissent timestamp. Lifting is recorded.

**Rationale.** Without this, the nightly reflection job re-infers the rejected
preference the next night, and the model becomes adversarial: the user's correction has
no effect. This is the single highest-risk failure mode of a self-improving user model,
and v1 had no equivalent because it had no inference.

**Consequences.** `NEGATIVE_CONSTRAINT` enters migration M6. `UM8` tests exactly this
and must be 0 within `K = 7` reflection cycles. `X-nosticky` (`UM-015`) exists to prove
the mechanism is what produces the result.

---

---

## 3. Decisions carried forward unchanged

| v1 decision | Status under V2 | Note |
|---|---|---|
| `D-ARCH` Option C | **In force**, reinterpreted as tier separation (V2-D07) | Same isolation principle, one level up |
| `D-ENG-01` (`P − A2`) | In force as `S1` | Reported as a frontier (§3.1 of research program) |
| `D-ENG-02` `(S)∨(C)` | In force, generalised (V2-D10) | |
| `D-ENG-03` RRF k=60 | In force as one fusion operator | |
| `D-ENG-04` deletion boundary | In force, channels extended | |
| `D-01`–`D-03` (models) | In force | Frozen models still required for replay |
| `D-09`–`D-14` (pilot params) | In force | Same freeze discipline |
| `D-17`, `D-18`, `D-25` (stack, concurrency, versions) | In force | |
| `D-23`, `D-24`, `D-26` (timestamps, judge, licence) | In force | |
| `D-16` `N_D` | In force; **revisit** if abstraction channels change per-scenario leak probability | |

---

## 4. Open decisions after the review
| ID | Question | Owner | Needed before |
|---|---|---|---|
| V2-D13 | Association-types cap per artifact (default suggestion: 32) | engine lead | W1 |
| V2-D14 | Retention default per profile and abstraction expiry (suggest: 180 d for preferences, 365 d for constraints and decision patterns, 14 d for context; re-verified rather than renewed) | product + methods | W5 |
| V2-D16 | Whether `S8` (fact/hypothesis labelling detectability) runs at all; ethics determination required first | supervisor | W7 |
| V2-D17 | Public API stability policy: semver with deprecation windows vs "experimental" prefix for Tier B | engine lead | W7 |
| V2-D30 | `tau_confidence` and `tau_coverage` defaults (suggest 0.90 / 0.60, frozen in the pilot like v1's `D-09`-`D-14`) | methods | W4 |
| V2-D31 | `mu`/`kappa` estimation window for hierarchical pooling: all users, or a rolling window to handle population drift | methods | W4 |
| V2-D32 | Whether abstraction expiry triggers automatic re-derivation or a user prompt in the PDT profile (privacy-relevant in the personal profile) | product | W7 |
| V2-D34 | `K` (dissent persistence window) confirmation: default 7 reflection cycles, frozen pre-test | methods | W4 |

`V2-D15` is **resolved** by `V2-D23` (conformal is for intervals only; admission uses CP bounds).
