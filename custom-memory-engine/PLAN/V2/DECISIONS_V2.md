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
- ~~`archived` survives as a *retention* value only, for backward compatibility.~~ **Superseded by `V2-D38` (r7):** v1 `archived` maps to `lifecycle='retired'`, `retention='durable'`; the retention enum has no `archived`.
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

> **Amended by `V2-D39` (r7, 2026-10-31).** The enumeration below is superseded by
> `ARCHITECTURE_V2.md` §8.1 / `API_V2.md` (23 operations, 17 Tier B); `retract` never
> existed (the operation is `retract_own`). The invariant, the credential rule and the
> prohibitions are unchanged. Original text preserved:

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
| `D-16` `N_D` | In force; **revisit** if abstraction channels change per-scenario leak probability — **revisited in `V2-D84`**: the bound is on the scenario, so eleven channels make a zero stricter and `N_D` is unchanged | |

---

## 4. Open decisions after the review
| ID | Question | Owner | Needed before |
|---|---|---|---|
| ~~V2-D13~~ | ~~Association-types cap per artifact (default suggestion: 32)~~ — **resolved (r7)**, see §5 | engine lead | W1 |
| ~~V2-D14~~ | ~~Retention default per profile and abstraction expiry~~ — **resolved (2026-11-25)**, see §4.1 | product + methods | W5 |
| V2-D16 | Whether `S8` (fact/hypothesis labelling detectability) runs at all; ethics determination required first | supervisor | W7 |
| V2-D17 | Public API stability policy: semver with deprecation windows vs "experimental" prefix for Tier B | engine lead | W7 |
| ~~V2-D30~~ | ~~`tau_confidence` and `tau_coverage` defaults~~ — **resolved (2026-11-25)**, see §4.1 | methods | W4 |
| ~~V2-D31~~ | ~~`mu`/`kappa` estimation window for hierarchical pooling~~ — **resolved (2026-11-25)**, see §4.1 | methods | W4 |
| V2-D32 | Whether abstraction expiry triggers automatic re-derivation or a user prompt in the PDT profile (privacy-relevant in the personal profile) | product | W7 |
| ~~V2-D34~~ | ~~`K` (dissent persistence window) confirmation~~ — **resolved (2026-11-25)**, see §4.1 | methods | W4 |

`V2-D15` is **resolved** by `V2-D23` (conformal is for intervals only; admission uses CP bounds).
`V2-D13` is **resolved** in §5 (r7). `V2-D14`, `V2-D30`, `V2-D31`, and `V2-D34` are **resolved** in §4.1 (2026-11-25).

### 4.1 Open decisions resolved for W4/W5/post-W3.7 (2026-11-25)

#### V2-D30 — Default confidence and coverage thresholds (`tau_confidence = 0.90`, `tau_coverage = 0.60`)
Matches `STATISTICS.md` §4.2, `soundness.admission`, and `scripts/check_statistics.py`; one-sided 97.5% Clopper–Pearson threshold (`o >= 36` at zero counters/unknowns).

#### V2-D31 — Hierarchical pooling estimation window (90-day rolling window)
Hierarchical beta-binomial pooling (`mu`, `kappa`) uses a rolling 90-day aggregate window across users with a minimum cohort of 10 users; below 10 users, each facet falls back to an unpooled Beta(1, 1) prior.

#### V2-D34 — Dissent persistence window (`K = 7` reflection cycles)
Dissent persistence window is fixed at `K = 7` scheduler reflection cycles (`UM8 = 0` throughout).

#### V2-D14 — Retention and per-facet abstraction expiry defaults
Profile abstraction expiry defaults: `preference` = 180 days, `goal` = 90 days, `constraint` = 365 days, `context` = 14 days, `decision_pattern` = 365 days, `meta_preference` = 180 days; expired abstractions demote to `hypotheses` and must be re-verified rather than silently renewed.


---

## 5. Decisions taken under Project Owner authorization (r7, 2026-10-31)

The Project Owner authorized resolving blockers B-01–B-09
(`audits/OPEN_DECISIONS_AND_BLOCKERS.md`) using the approved Charter, Architecture,
Statistics and existing code as the governing constraints, choosing the least
scope-expanding option consistent with them. Each decision below names what it resolves,
its implementation, and the test that verifies it. **Status legend:** *Implemented and
verified* = code and tests on `main`, CI green. *W1 integration* = the primitive is done; the
named W1 task wires it into structures that do not exist yet (they are created by `M1`–`M9`).

### V2-D35 — Scope matching is literal prefix matching; `LIKE` is forbidden (resolves B-01; extends V2-D27)

**Decision.** Subtree membership is decided by `pg_catalog.starts_with` over **canonical**
paths (leading and trailing `/`; segments that contain no `/`, NUL or control
characters; engine scope segments restricted to `[a-z0-9-]`). `LIKE`/`ILIKE` must never be
used for scope filtering. Selector sets are non-empty and capped at 16 (§5.1) and are never
silently truncated.
**Rationale.** `LIKE` treats `_`/`%` as wildcards, so `/u/a_b/` matched tenant `/u/aXb/`
(reproduced on PostgreSQL 16.15). The `V2-D27` trailing separator is necessary but not
sufficient: the wildcard sits *inside* the identifier.
**Implementation.** `custom_memory_engine.scope.paths` (`ScopePath`, `parse_scope`,
`subtree_predicate` → `psycopg.sql.Composed`).
**Verification.** `tests/test_scope_paths.py`, `tests/integration/test_scope_matching_db.py`
(includes the reproduced leak). **Status:** implemented and verified; `E-011` uses
`subtree_predicate` for the RLS/read predicates.

### V2-D36 — Minimal scope-membership ACL (resolves B-02)

**Decision.** Migration 2 adds `scope_grant(owner, principal, scope_path, role='member',
granted_at, revoked_at)`. Only the owner of `/u/<owner>/` may grant inside it (no implicit
or cross-tenant sharing); no self-grant; revocation is append-only; forced RLS shows a
grant only to its owner and grantee. Read sets: own namespace always; another namespace
only through an active grant whose subtree contains the selector; `private` never across
users; a selector broader than every grant is **denied, not narrowed**; a `private` row
matches only a selector equal to its own path (§5 rule 3).
**Rationale.** §5 rules 2–3 required membership that had no storage. Option 2 (defer
sharing) was rejected because it leaves normative rules unimplementable; broader
collaboration features (roles beyond `member`, delegation) are out of scope.
**Implementation.** `schema/002_scope_grants.sql`; multi-version runner (`schema/migrate.py`,
the `MA-016` X1 prerequisite); `custom_memory_engine.scope.access` (`resolve_read_set`,
`row_visible`, `read_predicate`); restricted role covers `scope_grant` (12 tables).
**Verification.** `tests/test_scope_access.py`; `tests/integration/test_scope_access_db.py`
(under the restricted role: SQL predicate = reference semantics on every row; forged,
cross-namespace, wildcard and self grants rejected; revocation final);
`tests/integration/test_migrations.py` (three-version history, drift, gaps, upgrade path).
**Status:** storage and authorization implemented and verified. *W1 integration:* `E-011`
applies `read_predicate` to rows once `M2` adds `scope_path`/`visibility`.

### V2-D37 — Unreadable provenance renders as an opaque handle (resolves B-03; corrects §9.3)

**Decision.** A source the caller cannot read renders as `OpaqueReference(opaque_ref,
derivation_type)` only, where `opaque_ref = HMAC-SHA256(key, caller ‖ NUL ‖ source_id)`
truncated to 128 bits: stable per caller (lineage), uncorrelatable across callers,
non-invertible. No scope, source id, source type or content. Opaque handles are never
accepted as identifiers; lookups answer `forbidden` exactly as for unknown ids. The key is
mandatory (≥ 32 bytes) and never printed.
**Rationale.** §9.3's "id + scope + type" contradicts `I17` ("no existence oracle") and the
§5.2 entity precedent; `I17` is the stronger, more specific rule.
**Implementation.** `custom_memory_engine.scope.provenance`.
**Verification.** `tests/test_scope_provenance.py` (adversarial three-tenant cases).
**Status:** implemented and verified. *W1 integration:* `E-017` renders bundles with it.

### V2-D38 — `archived` maps to `retired`; retention is never used for belief (resolves B-05; corrects M7)

**Decision.** v1 `archived` → `lifecycle='retired'`, `retention='durable'`.
`conflict_pending` → `contested`; `rejected` and unadjudicated `candidate` → `retired`;
`active` → the `M1` layer mapping. The closed §2.3 transition table (including rule 1's
`inferred → contested`) is executable. **M7 is owned by `E-010`** using `V1_BACKFILL_SQL`.
**Rationale.** The retention enum has three values; `retired` is defined as exactly what v1
`archived` meant (not believed, auditable, inside the deletion closure); `V2-D04`
separates belief from cost.
**Implementation.** `custom_memory_engine.model.axes`.
**Verification.** `tests/test_model_axes.py`; `tests/integration/test_model_backfill_db.py`
(SQL backfill = reference mapping on all 40 inputs). **Status:** implemented and verified.
*W1 integration:* `E-010` adds the axis columns and runs the backfill.

### V2-D39 — The public API is §8.1 / `API_V2.md`; `V2-D07` enumeration superseded (resolves B-04)

**Decision.** `V2-D07`'s invariant and prohibitions stand; its five-call list does not.
The 23 operations of §8.1 are canonical, `retract` does not exist, and unknown operations
are `forbidden` exactly like unpermitted ones. `API_V2.md` (`MA-012`, from PR #92) is the
reference document.
**Implementation.** `custom_memory_engine.api.contract` (`OPERATIONS`, `authorize`,
`validate_contract` enforcing TB1–TB4 and legal transitions).
**Verification.** `tests/test_api_contract.py` parses both documents (tier matrix and all 23
signatures) and requires exact equality. **Status:** implemented and verified.

### V2-D40 — The CP bound gates verification, not creation (resolves B-06)

**Decision.** `infer` stores a structurally sound candidate as `inferred`,
`verified=false`, in `hypotheses`. `inferred → inferred(verified=true)` requires
`STATISTICS.md` §4.2 admissibility **and** held-out verification (`V2-D21`). §2.2 G2's
"rejected" branch applies to structural soundness failures only.
**Rationale.** `STATISTICS.md` §4.1 point 3 (an abstraction must be stored to be tested)
and §4.2's "hypothesis only" rendering. Nothing is weakened: one-sided 97.5%,
`confidence_lower = 1 − CP_upper(k + u, o)`, coverage and refutation gates, documented
default thresholds (`V2-D30` remains open on final values).
**Implementation.** `custom_memory_engine.soundness.admission` (exact bound, no
`evaluation/` import). **Verification.** `tests/test_soundness_admission.py` (equality with
both reference implementations on 104 `(k, n)` pairs; §4.2 table; `o = 35/36` boundary; r3
regression). **Status:** implemented and verified. *W1 integration:* `E-015`.

### V2-D41 — Grounding per declared source type (resolves B-07)

**Decision.** Closed types: `turn` (declared fact; exactly the v1 rule), `external`
(imported; ref, captured content matching the declared canonical SHA-256, verbatim span,
aware `fetched_at` not in the future and within the caller's `max_age`; outside the
deletion boundary), `artifact` (inferred; never evidence). Any other type fails closed;
every item must ground. `content_hash` canonicalisation: NFC, LF line endings, UTF-8,
SHA-256 hex.
**Implementation.** `custom_memory_engine.grounding.sources`. **Verification.**
`tests/test_grounding_sources.py` (turn grounding proven identical to the v1 gate; valid,
incomplete, contradictory, stale, future, inferred, unsupported). **Status:** implemented and
verified. *W1 integration:* `E-013` (`M4` `SOURCE`) and `E-015`.

### V2-D13 — Association degree cap (resolved, r7; resolves B-08)

**Decision.** The cap bounds an artifact's association **degree** (rows where it is either
endpoint); a cap on *types* would be vacuous (closed set of 10). Default **32**, the plan's
recorded suggestion; profiles may declare 1–1024. **The value is a resource bound, not a
derived research result**: no approved document establishes a number, and `V2-D04` only
requires that one exist and be profile-declared. Enforced in PostgreSQL by
`enforce_association_cap()` (migration 3): self-loops rejected, writers serialized per
endpoint by a transaction advisory lock, both directions counted, `UPDATE` covered.
**Verification.** `tests/integration/test_association_cap_db.py` (20 concurrent writers commit
exactly the cap; removing the lock was shown to overshoot). **Status:** implemented and
verified. *W1 integration:* `E-014` attaches it via `model.association.attach_cap_trigger`.

### V2-D42 — Gate G-TS: J5/J7/J8 remain in force before any confirmatory run (resolves B-09)

**Decision.** G-TS = J5 (`verify_frozen_splits`) + coverage (every `SuiteId` frozen) + J7/J8
(signed `PilotFreeze` at a git tag) + one-shot claim. It is required **before G5** (W5 consumes
the `own_P` test split), not only before W6.
**Implementation.** `evaluation/harness/split_gate.py`; `scripts/check_test_split_gate.py`
(CI). **Verification.** `evaluation/harness/tests/test_split_gate.py`. **Status:** implemented
and verified; no confirmatory run is authorized (no signed `PilotFreeze` exists).

### V2-D43 — M5 deferred; M7 owned (G0b satisfiability, B-08)

**Decision.** `M5` (optional `predicate_schema`) is **deferred** past W1 with an explicit
marker: it weakens `I5` and is one-way, and no W1 task needs it. `predicate_schema` stays
mandatory. Revisit with the coding profile (`E-021`, W2). `M7` is owned by `E-010`
(`V2-D38`). With `V2-D13` resolved, every review item has a recorded decision, so G0b is
satisfiable once `MA-017`/`MA-018` run green on that basis.

---

## 6. Decisions taken under Project Owner authorization (r9, 2026-11-01)

The Project Owner approved the decisions that r8 left open and authorized the maintainer to make the remaining
pre-W1 decisions (`APPROVAL.md` Amendment 2). As in §5, each record names what it resolves, its implementation
and its verification. **W1 is not started by any of them:** implementations here are specifications, pure
contract modules and tests; database and runtime work stays with the named W1 task.

### V2-D44 — v1 baseline: `cme-v1.0.0` at `490904d`; E-004 criterion 3 superseded (resolves #51, #52)

**Decision.** The frozen substrate baseline is the annotated tag `cme-v1.0.0` on
`490904d632518b86e0f52ced1f3266b895c3c36b`. The engine package version at the tag stays `0.1.0` (a tag cannot
change an existing commit; the tag is a baseline, not a release). E-004's criterion "Invariants 7 and 8 are not
`ENFORCED`" is superseded: #46/#47 closed those halves in r7, and the ledger reports the remaining halves.
**Rationale.** `490904d` is the commit every `BASELINE.md` figure was measured at; r7's migrations become
post-tag additive changes, which the additivity rule requires. `cme-` avoids implying a version of the whole
monorepo.
**Implementation.** `BASELINE.md` §7.1; `INVARIANT_LEDGER.md` (criterion note); tag on `origin`.
**Verification.** `git rev-parse 'cme-v1.0.0^{commit}'`; `tests/test_invariants_declared_and_enforced.py`
(`test_gaps_closed_in_r7_are_enforced_with_tests`, and for the reporting behaviour
`test_an_unenforced_row_must_say_what_is_missing_and_who_owns_it`).

> **Correction (2026-11-17, W3 integrity pass).** This line previously cited
> `test_known_v1_gaps_are_reported_not_silently_passed`, which `bae2988` (`V2-D55`) deleted **because the gaps it
> reported were closed** — so the citation outlived the thing it pointed at. The behaviour it was cited for is still
> enforced, by `validate`'s `gap_required`/`owner_required` rules and the test above. Found by
> `scripts/check_evidence.py`.
**Status:** decided and verified.

### V2-D45 — Scope isolation design frozen; RLS enforces the ACL, not the selectors (resolves #54 / MA-011; amends §5.1)

**Decision.** (a) Matching is `starts_with` over canonical paths, never `LIKE` (`V2-D35`, unchanged).
(b) Grammar: as in `API_V2.md` §6 (`scope.paths`): no case folding or normalisation, anything outside it is a
`validation_error`. (c) ACL storage is `scope_grant` (`V2-D36`, unchanged). New in r9: **`exact(path)`** is a
selector mode; the **write rule** authorizes `(scope, visibility)` only if the caller could read the row back
through `exact(scope)` (**amended by `V2-D53`**: own namespace only; grants are read-only); the **RLS layer enforces the caller's ACL** (own namespace; `project`/`shared` inside an
active grant), computed in PostgreSQL from `app.user_id` and `scope_grant`, and does **not** take the selector
set, so §5.1's `app.read_scope_prefixes` GUC is dropped. The 16-selector cap stays an application resource error.
RLS policies must qualify the row's columns with the table name. M2 requires every `app_user.id` to be a valid
user segment and adds a `CHECK` for it (`MIGRATION_RISK.md` D13).
**Rationale.** Rule 4 requires two layers, neither trusted alone. A selector GUC is set *by the application*, so
an application bug that widens the prefixes also widens RLS: the layers were not independent. The ACL-only
policy reads nothing the application computed. Measured on PostgreSQL 16.15: with an **unqualified** `scope_path`
in the grant sub-query, the column binds to `scope_grant.scope_path` and any caller holding one grant reads every
tenant's `project`/`shared` rows; qualification closes it. v1 ids may contain `/` (`validate_user_id` allows it),
which would forge a path inside another tenant's subtree at the M2 backfill.
**Implementation.** `scope.access` (`Selector.exact/subtree/with_visibility`, `authorize_write`,
`acl_readable`, `rls_read_using`); `API_V2.md` §6; `ARCHITECTURE_V2.md` §5.1 amendment; `MIGRATION_RISK.md`
D5, D13 and M2.
**Verification.** `tests/test_scope_isolation.py` and `tests/integration/test_scope_isolation.py` over the
shared cases: the r2 `LIKE` design fails cases 1 and 5, `LIKE` with the separator fails case 5, `starts_with`
passes all; the RLS layer alone returns exactly the ACL for every caller, grant set, revocation and unusable
identity; the application read set is contained in the ACL (exhaustive); the unqualified policy is shown to
leak. **Status:** decided and verified on scratch tables; `E-011` applies it to the real schema.

### V2-D46 — The API gaps are closed: six operations, two compositions (resolves #55 criterion 5; N-3)

**Decision.** `API_V2.md` §5's seven gaps: (5.1) **`dissent`** (Adjudicate, Tier C): the user rejects an
inference, `inferred → retired` with `reason='user_dissent'` and a negative constraint (`V2-D33`, `I16`); no
evidence, because the user is the authority over their own model; Tier B may not dissent for them. (5.2) `move`
and (5.7) *revise* are **compositions** of existing operations with an idempotency key per step, not
primitives. (5.3) `ingest_artifact`'s payload is confirmed. (5.4) **`create_entity`** (Structure, Tier B and
C, writable scope). (5.5) **`register_method`** (Maintain, admin-only, append-only: same `(name, version)` with
the same hash is idempotent, with a different hash `precondition_failed`). (5.6) a **Scope** class,
`create_scope`/`grant_access`/`revoke_access`, Tier C, namespace owner only; identity itself stays outside the
engine. The surface is 29 operations, 18 for Tier B.
**Rationale.** §8's principle is "nothing outside the list exists". Each gap was something another approved
document relied on (`ARCHITECTURE_V2.md` 5.2 entity creation, §4 the method registry, `USER_MODEL.md` §3 the
user's dissent, §5 grants). Compositions were kept as compositions when every step already had a primitive and a
partial run is safe; new primitives were added only where nothing could express the effect. Access changes are
Tier C so a cognition service can never widen any read set.
**Implementation.** `api.contract` (`OPERATIONS`); `API_V2.md` §§3-5, 7.1; `ARCHITECTURE_V2.md` 8.1;
`USER_MODEL.md` §3; `ROADMAP_V2.md` `UM-012`.
**Verification.** `tests/test_api_contract.py` (registry = both documents, signatures); `tests/test_tier_permissions.py`;
`scripts/check_plan_refs.py` `check_operations` (every call-form operation name in `ROADMAP_V2.md` and
`ARCHITECTURE_V2.md` resolves; it found the stale `refuted(reason='user_dissent')` in `UM-012`).
**Status:** specified and verified as a contract; runtime implementations belong to `E-014`-`E-017` and `A-017`.

### V2-D47 — Two tiers; admin is a role within Tier C; conditions are part of the matrix (resolves #58 / MA-015)

**Decision.** Callers are Tier B or Tier C, from the authenticated credential. **Admin** is a role held by some
Tier C credentials, never by Tier B. The Maintain operations (`purge_working`, `expire`, `rebuild_index`,
`register_method`) are admin-only; an admin may call every Tier C operation. Every matrix row carries the
conditions its callers must satisfy, as closed codes (`api.contract.Condition`). The structural rules TB1-TB10 and
AD1 (`API_V2.md` §3.1) are checked by `api.contract.contract_violations`.
**Rationale.** `ROADMAP_V2.md` said "admin" while §8.1 had two columns and called Maintain "administrative"; a
third tier would need its own credential path and threat analysis for no capability the role cannot express. The
r8 matrix marked conditions ("only with evidence", "own lineage") in prose that no test could see; as codes they
are machine-checked and the implementing task cannot miss one.
**Implementation.** `api.contract` (`authorize(..., admin=)`, `contract_violations`, `Condition`); `API_V2.md` §3.
**Verification.** `tests/test_tier_permissions.py`: each rule holds on the real matrix and fails with its own
code on a deliberately broken copy. **Status:** decided and verified (specification; runtime enforcement by the
implementing tasks).

### V2-D48 — Channel contract decisions C-P1..C-P6; MA-013 frozen (resolves #56; N-1, N-4)

**Decision.** All six `CHANNELS.md` §5 proposals are accepted. **C-P1:** lifecycle legality is kind-conditioned
(`axes.LEGAL_LIFECYCLE`; `require_transition(source, target, kind)`, `validate_axes`): an abstraction is never
`asserted` or `observed`, an episode never `asserted`, a procedure never `observed`. **C-P2:** a verified inferred
claim or procedure stays a hypothesis. **C-P3:** `stale` is the OR of five causes. **C-P4 (security):** only a
`contradicts` edge between two grounded endpoints, created by the grounding policy or Tier C, opens a
contradiction; a Tier B edge has no channel effect (matrix condition `grounded_endpoint_protected` on `link`).
**C-P5:** `refuted` always retires. **C-P6:** a grounded artifact with an open contradiction is in `conflicts`;
`ARCHITECTURE_V2.md` 9.1 corrected.
**Rationale.** C-P1: the kind-blind table let `inferred → contested → asserted` turn an abstraction into an
unbounded fact, against `I1′` and `I14`. C-P4: without it any Tier B service could demote any fact by linking a
`contradicts` edge, which is adjudication, denied to Tier B on purpose (TB2). The others make the membership
function total without weakening `I1′`.
**Implementation.** `model.axes`; `api.contract` (`link` condition); `CHANNELS.md` (frozen); `ARCHITECTURE_V2.md`
9.1; `API_V2.md` 4.5-4.6.
**Verification.** `tests/test_model_axes.py` (reachable = legal per kind; the kind-blind mutation reaches exactly
the four illegal pairs; abstraction cannot be confirmed); `tests/test_channel_membership_model.py` (60 legal
tuples, exactly one output each; reachable space = legal space; C-P4 and C-P3 tests); `tests/test_tier_permissions.py`.
**Status:** decided; `MA-013` frozen. Database enforcement is `E-016`, assembly `E-017` (W1).

### V2-D49 — Migration decisions D6-D12 approved (resolves #59 follow-ups)

**Decision.** `MIGRATION_RISK.md` §1.1 D6-D12 as recommended: (D6) `layer` stays stored and dual-written until the
last v1 reader is removed; (D7) backfilled `persona` rows are `abstraction/inferred` with a zero ledger, so they can
never be verified or render as facts; (D8) `observed_at` = belief start, else earliest evidencing turn, else event
start (total; the last case is counted); (D9) M3 renames in place (3.2 ms vs 48 s `ACCESS EXCLUSIVE` at 400k rows),
amending #63's literal steps; (D10) the exclusion-constraint and `I9` key stays `user_id` (the owner namespace) in
W1; (D11) `user_id` stays **stored** with a consistency `CHECK`, not generated, amending §12 M2; (D12) `job`
idempotency uniqueness is scoped per user.
**Rationale.** Each was measured or checked against v1: D11 because a generated column breaks all 23 v1 `INSERT`s
that name `user_id`; D12 because v1's own test shows a cross-tenant key collision is observable (T-13); D8 because
the earlier rule had no value for unbelieved, unevidenced rows. D10 keeps the conversational profile exactly
v1-equivalent; the global lock order (`V2-D51`) already handles cross-namespace closures under this key.
**Implementation.** `MIGRATION_RISK.md` §1.1 and M1-M3; `ARCHITECTURE_V2.md` §12 M2; `ROADMAP_V2.md` `E-011`.
**Verification.** The r8 measurements recorded in `MIGRATION_RISK.md` §4 (PostgreSQL 16.15). **Status:** decided;
applied by `E-010`-`E-012`.

### V2-D50 — Deletion reconciliation rules (resolves #57 follow-ups; N-5)

**Decision.** (1) **Transitive re-derivation:** a dependent is `alive` only if every source is alive; a dependent
of a re-derived source is re-derived. (2) **Every** dependent that keeps a surviving source is re-derived, whatever
its derivation type; one with no re-derivation builder is retired. (3) **Retire-by-deletion tombstones content:**
id, scope, kind, `retired`, reason `source_deleted` and the audit row stay; content, statements, rendered bounds
and vectors are removed in the same transaction. (4) Classification is sources-first, application
dependents-first (architecture §10 wording corrected).
**Rationale.** The earlier rule left 698 nodes `alive` with a deleted node in their closure in the exhaustive model,
i.e. content derived from deleted material survived deletion. Keeping a retired artifact's text, or a
non-summary dependent's text, would let the leak scanner's storage and abstraction channels find deleted
material. The deletion boundary is a security property; audit keeps the fact and reason of retirement.
**Implementation.** `DELETION_ORDER.md` §§2-3, 7; `ARCHITECTURE_V2.md` §10.
**Verification.** `tests/test_deletion_order_model.py` (alive-with-changed-closure 698 → 0; sources-first 0
violations; dependents of re-derived sources). **Status:** decided; `E-037` implements it in the database (v1's
summary-only re-derivation and depth-3 cap are recorded there as superseded v1 behaviour).

### V2-D51 — Global lock order for cross-scope closures; W1 serialisation key (resolves #57 follow-up; `I9` for W1)

**Decision.** A transaction whose closure touches several scopes computes the closure, acquires the serialisation
lock of every key in **one global order** (UTF-8 byte order of the key) before its first write, recomputes the
closure under the locks, and retries with the larger set if it grew (bounded, tenacity; `40P01` is only the
backstop). In W1 the `I9` key of a scope is its owner namespace `/u/<user>/`, which equals v1's per-user lock.
**Rationale.** Grants (`V2-D36`) allow an inference in one namespace from a readable source in another, so
cross-namespace closures exist from W1. Discovery-order locking deadlocks on opposite closures and on three-way
cycles; a total order cannot form a cycle of waits.
**Implementation.** `scope.locks` (`serialisation_key`, `lock_order`, `must_retry`); `DELETION_ORDER.md` §6.1.
**Verification.** `tests/test_scope_locks.py`: an exhaustive interleaving model reproduces the deadlock under
discovery order (two-way and three-way) and finds none under the global order for every combination of closures
tested. **Status:** decided; primitive verified; `E-011`/`E-037` acquire the locks in the database.

### V2-D52 — Plan coverage is mechanical; `E-039` owns the public API entry point (resolves #60 / MA-017)

**Decision.** Architecture elements (4 kinds, 7 tables, 17 invariants, 4 channels, 29 operations, 9 migrations)
are extracted from `ARCHITECTURE_V2.md`; owners come from `INVARIANT_LEDGER.md` (invariants) and `COVERAGE.md`
(the rest); `scripts/plan_coverage.py` fails CI on any unowned element. The coverage run found that no task owned
the public API entry point itself (authorization, condition codes, error contract): each operation's behaviour
had a task, the dispatch layer had none. New W1 task **`E-039`** owns it. `M5` stays deferred (`V2-D43`); `M7` is
owned by `E-010`.
**Rationale.** `SYNC_LOG.md` §3's hand-written matrix had silently missed `SCOPE`, `M5` and `M7`; a matrix that is
generated and enforced cannot drift. Without `E-039`, W1 operations would each re-implement authorization, or none
would.
**Implementation.** `scripts/plan_coverage.py`; `PLAN/V2/COVERAGE.md`; generated `PLAN/V2/COVERAGE_MATRIX.md`;
`ROADMAP_V2.md` `E-039`; CI static job (`plan_coverage.py --check`).
**Verification.** `tests/test_plan_coverage.py`: a fake invariant, table, operation, migration and channel each
fail naming the element; bad owners, unreasoned deferrals and stray rows fail; the matrix is current; `SYNC_LOG.md`
is compared, not modified. **Status:** decided and verified.

### V2-D53 — Grants are read-only: writes and deletions land only in the caller's own namespace (amends V2-D45; resolves `THREAT_MODEL.md` §3.1 row 4)

**Decision.** `scope.access.authorize_write` authorizes a write into `(scope, visibility)` only inside
`/u/<caller>/`. A grant never authorizes a write; since `delete` carries `scope_writable`, a grant never authorizes
a deletion either. Deletion receipts and epochs are therefore per namespace and count only the owner's deletions.
**Rationale.** `V2-D45`'s first wording ("the caller could read the row back") let a grantee write `project`/
`shared` rows into the owner's namespace and, through `delete`, remove the owner's project data, contradicting
`V2-D36` ("read membership"). Found while resolving T-22 row 4. Collaborative writing into a shared project, if
ever wanted, needs its own grant role and decision; nothing in W1 requires it.
**Implementation.** `scope.access.authorize_write`; `API_V2.md` §6; `ARCHITECTURE_V2.md` §5 rule 1;
`THREAT_MODEL.md` §3.1 rows 3-4 and T-22.
**Verification.** `tests/test_scope_isolation.py`: `test_a_grant_never_authorizes_a_write_into_the_owners_namespace`
(fails on the `V2-D45` wording, which authorized it), `test_every_authorized_write_is_readable_back_by_its_writer`.
**Status:** decided and verified (primitive; `E-011`/`E-039` enforce it at runtime).

## 7. Pre-W1 independent audit (r10)

### V2-D54 — Tenant integrity is enforced by the database: composite tenant keys, pinned function search paths, exact runtime privileges

**Decision.** Migration 4 (`004_tenant_integrity.sql`) makes every foreign key between two tables that carry
`user_id` composite on `(id, user_id)`, pins every engine function to `search_path = <schema>, pg_catalog,
pg_temp`, and drops the owner `DELETE` policy on `scope_grant`. The runtime role holds an exact per-table
privilege matrix (`provisioning.TABLE_GRANTS`, mirrored by `rls.sql`): audit events, deletion receipts, turns,
sessions, users and evidence are insert-only. The public entry point (`E-039`) generates every primary key
server-side and never accepts a caller-chosen one (T-27).
**Rationale.** The r10 pre-W1 independent audit (findings S-01 to S-03) measured, under the restricted role and
through `commit_initial_version`: a tenant could cite another tenant's turn and derive from another tenant's
version (PostgreSQL referential checks bypass RLS), learn whether any id exists, and **block that tenant's own
`delete_memory(scope='all')`** with a foreign-key violation it could neither see nor clear (S-01, Critical). A
temporary table shadowed `memory_evidence` inside `enforce_provenance` and committed an active version with no
evidence (S-02, High). The runtime could rewrite audit history and erase deletion receipts (S-03).
**Implementation.** `schema/004_tenant_integrity.sql`; `migrate.MIGRATIONS`; `security/rls.sql`,
`security/provisioning.py`; `tests/integration/schema.py` (every integration suite now builds the schema from
`migrate.MIGRATIONS`; nine suites previously ran on migration 1 only); `THREAT_MODEL.md` T-24-T-30;
`MIGRATION_RISK.md` D14.
**Verification.** `tests/integration/test_tenant_integrity_db.py`: each attack is refused, with a control on the
pre-migration-4 schema in which the same attack succeeds; two catalog rules (tenant keys carry `user_id`;
functions pin `search_path`) fail on the pre-fix catalog and bind every later migration;
`tests/test_security_scoping.py::test_rls_sql_grants_equal_the_least_privilege_matrix_exactly`;
`tests/integration/test_migrations.py` upgrades a migration-1 install through migration 4 without data loss.
**Status:** decided and verified.

### V2-D55 — The last v1 invariant gaps are closed before W1 (RL-072, RL-090, RL-092), with the defects they exposed fixed

**Decision.** `RL-072`, `RL-090` and `RL-092` are completed in the pre-W1 window instead of being deferred: W1's
M1-M4 migrations rewrite exactly the lifecycle, archival, clock and audit surfaces these tasks guard, so their
oracles must exist first. (1) Every engine audit writer passes `security.audit_guard.content_free_details`, an
allowlist (keys, identifier-shaped values); `redact_log_event` is the log processor; Channel 1 of the deletion
scanner also scans `audit_event`. (2) `tests/integration/archival/test_t1_to_t5.py` is the `T1`-`T5` contract,
`T5` measured on all seven channels against the engine's real outputs with a pre-deletion control.
(3) `tests/integration/test_replay_gate_db.py` replays a whole out-of-order session twice through the real queue
and engine, and pins every `RL-092` clause to the test that proves it.
**Defects found and fixed while building them.** (a) `version_committed` audited `memory_key`, which the P engine
builds as `subject:predicate` and which survives deletion; the consolidator logged it too. (b) **A memory archived
via branch `(C)` could not be deleted**: `delete_memory` failed with `archival_breaks_covered_source_invariant`
because the archival constraint triggers judged each statement, not the deletion's final state; deletion now
defers them and re-checks them `IMMEDIATE` before returning, and deleting only the replacement is still refused.
(c) `scan_deleted_key` let a caller's channel-1 probe *replace* the SQL storage scan, so supplying a cache probe
reported channel 1 "complete, zero" without scanning storage; it now supplements it (the Suite D harness already
composed both, so recorded `H4` results are unaffected: `reports/deletion/suite_d_dev_{P,A8}.json` re-run
byte-identically). (d) The Suite D harness built migration 1 only; it now builds the shipped schema.
**Verification.** `tests/test_audit_guard.py` (static rules fail on the removed `memory_key` field),
`tests/integration/test_audit_content_db.py`, `tests/integration/archival/test_t1_to_t5.py` (`T5` fails without
the deferral), `tests/integration/test_leak_scanner_db.py::test_a_cache_probe_supplements_and_never_replaces_the_storage_scan`,
`tests/integration/test_replay_gate_db.py`; `INVARIANT_LEDGER.md`: every v1 row is `ENFORCED`
(`test_v1_gaps_closed_in_r10_are_enforced_with_tests`). **Status:** decided and verified.

### V2-D56 — W1 is specified and gated by executable requirements; status claims match evidence

**Decision.** (1) Every W1 task is bound by the r10 requirements in `ROADMAP_V2.md` section 4 (migration numbering
from `005`, composite tenant keys, pinned function `search_path`, exact privilege matrix, audit-key allowlist,
deferrable archival triggers, and the Invariant 7-10 oracles), each enforced by a test that fails when a W1 change
omits it; task-specific obligations are added to `E-012`, `E-039` and `E-037`. (2) `RL-091` (full-schema invariant
suite) is satisfied by `INVARIANT_LEDGER.md` and `tests/test_invariants_declared_and_enforced.py`, which resolve a
site and a CI-run test for every v1 invariant on the fully migrated schema; a second, duplicate suite would add
maintenance without evidence. (3) `RL-005` (engine CI) is verified locally by `tests/test_ci_workflow.py` (every
gate present, actions and images pinned, read-only token, workflows linted). (4) `RL-071`'s stale `Open` claim is
corrected. **Verification.** `scripts/audit_status.py`: 0 `claimed-unverified`, no discrepancies.
**Status:** decided and verified.

### V2-D57 — AI review is the project's review gate

**Decision.** The AI reviewer, acting on the Project Owner's behalf, is the mandatory review gate for every change
to the engine: code, schema, migration, plan document and architecture decision. Human peer review is **not** a
prerequisite for merging and is never recorded as though it happened. The review standard is `PLAN/CONTRIBUTING.md`
§1.1: read the code rather than the description, reproduce the claims that matter, treat a stated guarantee as
unproven until a mutation shows its test fails without it, review the *sequence* of merges where several branches
touch one file, adapt but never weaken a failing test, and leave a partially satisfied issue open with its gap
recorded.
**Why.** The r11 W1 review is the evidence. Six tasks were implemented and merged, and the review found: an audit
key that collided with the r10 allowlist (#112), an invariant ledger silently reverted by merge order (fixed in
#123), a property test that ran 315 ledgers where its issue required 1,000 (#125), a missing `T-30` enforcement
that let a namespace owner test whether a user id exists (#122), stale migration numbering across four branches,
and rollbacks that restored a function without its `search_path` pin. None of these were visible in any single PR's
green CI. A review that only reads the description would have missed all of them.
**What this does not mean.** It is not a claim of human sign-off, and it does not relax any research gate: G-TS,
the frozen records and the confirmatory-run restrictions remain exactly as they were. Where a platform requires a
GitHub approval, that requirement is a platform setting and no approval is ever fabricated for it.
**Implementation.** `PLAN/CONTRIBUTING.md` §1.1 (the standard), this decision (the authority), the PR body (the
record of each review), `DECISIONS_V2.md` (the decisions it produces), and `INVARIANT_LEDGER.md`/issues (the gaps
it finds). `PLAN/V2/audits/` keeps the historical reports as evidence; no new one is written per change.
**Status:** decided and in force from r11.

### V2-D58 — `internal_error` is the seventh code of the public error contract (corrects `V2-D39`/`E-039`)

**Decision.** `API_V2.md` section 1.1 enumerates **seven** static codes: the six it already listed plus
`internal_error`. `internal_error` is the entry point's last resort — an engine defect or an unexpected driver error
that none of the other six describes. It is returned only by `Engine.call`, never by an operation's own logic, so
section 3 does not list it per operation and it is **not** added to each `Operation.errors` set. It carries a code and
no detail, message, argument or traceback. The table is now declared complete, and `tests/test_api_contract.py` holds
it equal to the executable contract (`every operation's codes | {internal_error}`), so a code the implementation can
return and the table omits fails CI.
**Why.** `E-039` already returned `internal_error` for any unrecognised exception and `tests/test_api_entry.py` already
asserted it, but section 1.1 did not list it and `entry.py` carried a comment saying so. A frozen contract that omits a
code the entry point demonstrably returns is not a contract: a client generated from section 1.1 would have no branch
for it, and a future reviewer reading only the table would treat the code as a violation of the surface rather than a
part of it. The alternative — mapping unexpected failures onto `validation_error` or `precondition_failed` — was
rejected because it tells the caller its input was wrong when the engine was, and would hide defects behind a
plausible code. Leaving it undocumented was the third option and is what this corrects.
**What it does not change.** No operation's `errors` set changes, so no tier, condition or `forbidden` rule moves; the
API surface (`V2-D46`, `V2-D47`) is unchanged. Only the enumeration in section 1.1 and its executable pin differ.
**Implementation.** `API_V2.md` section 1.1 (the row and the completeness claim), `api/entry.py` (the module
docstring, which said the code was *not* in section 1.1), `tests/test_api_contract.py`
(`documented_error_codes`, `test_error_contract_section_1_1_is_the_complete_code_set`, and the mutation control
`test_the_error_contract_catches_a_code_the_document_omits`).
**Verification.** The completeness test fails when a row is renamed (`assert {'not_internal'}=={'internal_error'}`,
reproduced) and when an undocumented code is added; `tests/test_api_entry.py` (28 contract tests) and
`tests/test_tier_permissions.py` remain green. **Status:** decided and verified.

### V2-D59 — `I10` applies to explicit withdrawal, at no cost (resolves issue #67 question (a))

**Decision.** `I10` governs **every** retirement, explicit withdrawal included, and the `(S)∨(C)` precondition is
required for it without exception. There is no separate rule for user-initiated retirement: I10's own wording forbids
retirement that *silently* removes an answerable fact, and an explicit withdrawal is not an exception to that — it is
the case the precondition was written for. Implementing it costs nothing, because the two are the same act: closing
the belief interval *is* branch `(S)`. The only way to attempt `asserted`/`observed -> retired` without `(S)` is to
leave recorded time open, and that is precisely a silent removal, which the guard already refuses with
`archival_precondition_unmet`. `I10`'s second half still holds after the withdrawal: the fact remains answerable to
`audit_as_of` at a belief time before it, while the ordinary channels stop returning it — which is what withdrawing it
was for.
**Why.** Issue #67 left this open ("does `I10` apply to explicit withdrawal?") and the migration 7 header recorded it as
unmade, implementing the conservative half only (system-initiated retirement). Leaving it undecided keeps `I10` at
`PLANNED` for a case the schema already enforces, and invites a future writer to add an exemption for "the user asked
for it" that would let a withdrawal delete answerability. The measurement settles it: branch `(S)` is the recorded-time
close, so a real withdrawal always satisfies it, and the guard refuses only the silent form.
**What it does not mean.** It is not a new constraint and no migration changes. A user who wants the content *gone*
uses `delete`, not retirement: retirement keeps the fact answerable to audit (`ARCHITECTURE_V2.md` §2.3 rule 2), and
deletion is the operation that removes it (`V2-D50`, `E-037`). Conflating the two would be the disclosure bug I10
exists to prevent.
**Implementation.** No code change: `schema/001_full_schema.sql::enforce_archival_invariant` already implements it, and
`schema/007_lifecycle_guard.sql`'s header comment (which recorded the question as unmade) now cites this decision.
`INVARIANT_LEDGER.md` moves `I10`'s retirement half from `PLANNED` to `PARTIAL`.
**Verification.** `tests/integration/test_lifecycle_transitions.py::test_i10_holds_for_explicit_withdrawal_because_the_withdrawal_is_itself_the_recorded_close`
(three parts: the silent form is refused, the recorded close permits the transition, the fact stays answerable to
`audit_as_of` and leaves the ordinary channels). **Mutation-checked:** neutering the precondition
(`IF NOT has_s AND NOT has_c` → `IF FALSE`) makes the test fail with
`archival_branch_must_be_C_when_consolidated_only`, so it is a real control and not a restatement.
**Status:** decided and verified.

### V2-D60 — The migration runner lifts `FORCE ROW LEVEL SECURITY` around a migration body (resolves the E-014 review finding)

**Decision.** `MigrationRunner` lifts `FORCE ROW LEVEL SECURITY` on the relations of the schema it manages, executes one
migration source, and restores `FORCE` on exactly the relations it lifted, before recording that migration's catalog
digest. It also dispatches any trigger events the body left deferred (`SET CONSTRAINTS ALL IMMEDIATE`) and puts each
deferrable trigger back to the mode its own DDL declares, because `ALTER TABLE` is refused while a relation has pending
events.

**Why.** `FORCE` binds the table *owner*, and a migration is applied by that owner. A backfill written as an
unqualified `UPDATE … WHERE new_column IS NULL` or `INSERT … SELECT` therefore matches **zero rows** when it runs as
any role that does not bypass RLS, and the `SET NOT NULL` that follows refuses the whole migration. Measured on
migrations 5 and 8 (SQLSTATE `23502`), and 9 sets `observed_at`/`event_basis` `NOT NULL` the same way: a schema that
held data could not be upgraded by its owner at all. It could not be fixed in the migrations, because their sources are
checksum-bound and `status` reports any edit as drift. The fresh-install path never showed it, because those tables are
empty on a first apply, and the test suite connects as a superuser, which bypasses RLS.

**What it does not mean.** It is not an RLS bypass at rest: the lift exists only inside the migration transaction, the
restore is unconditional and covers only relations that already had `FORCE`, and a failing body rolls the lift back with
everything else. `clock_watermark`, which migration 9 deliberately leaves non-forced, is never forced. Nothing about the
runtime role changes; `E-011`'s read policy is unaffected. Migration 10 lifts `FORCE` for its own two backfills, so it
stays correct when applied outside the runner; the runner's restore is then a no-op for it.

**Implementation.** `schema/migrate.py::MigrationRunner._release_force`, `_restore_force` and `_run_source`; both
`_install` and `_apply_from` execute through `_run_source`. Table names come from the catalog (`relforcerowsecurity`),
never from a list, so a migration that creates new tables is covered by the next body automatically.
**Verification.** `tests/integration/test_migration_owner.py`: migrations 5, 8, 9 and 10 each upgrade a populated schema
owned by a `NOSUPERUSER NOBYPASSRLS` role, and `FORCE` is asserted restored on every relation afterwards with
`clock_watermark` excepted. **Mutation-checked:** running the same suite against the pre-change runner fails `[5]`, `[8]`
and `[9]` with `migration_database_failure`; `[10]` passes either way, because #130 lifts `FORCE` itself.
**Status:** decided and verified.

### V2-D61 — The records of entry into the lifecycle bind the application role; a re-record carries its belief's decision (closes issue #67)

**Decision.** Migration 14 (`014_entry_records.sql`) enforces the creation rows of `ARCHITECTURE_V2.md` §2.3 at commit,
with two deferred constraint triggers, `trg_memory_version_record_of_creation` and
`trg_memory_version_record_of_entry`. Both are listed in `engine.deletion.ARCHIVAL_GUARD_TRIGGERS`, as r10 requires.
(1) `— → asserted` needs its evidence (migration 7) and an `accept` gate decision for that version in
`memory_conflict`. `— → contested` needs a `hold` decision. `— → inferred` needs a derivation and a registered method
(migrations 7 and 10). `— → observed` needs its evidence. (2) The two v1 ways into the lifecycle that are not
creations need the adjudication that v1's `resolve_conflict_pending` already writes: a staged `candidate` promoted
to a belief, and a held candidate accepted as an inference. Both need an `accept` decision, and the inference also
needs a derivation. (3) **A re-record is not a new assertion.** Close-then-insert (R1–R3, E3, R7) closes a belief at
tick `c` and inserts a copy at `[c, NULL)`. That copy is admitted without a decision of its own only when a version
with the same memory key, subject, predicate, kind and content was closed at exactly `c`, and when its event range
lies inside what was believed for that subject and predicate until `c`. A copy that claims new content or new event
time is a new assertion and needs the gate. (4) **Who is bound:** every role that is not a superuser, which includes
the runtime role and a non-superuser schema owner. (5) The v1 `persona` exemption from the derivation half of `I1′`
(migration 7) is narrowed by the same rule. A persona row the application role creates, or brings into the lifecycle,
needs a derivation. Existing persona rows and later writes to them keep the exemption, so the D7 backfill
(`V2-D49`) is unaffected.

**Why.** Issue #67 criterion 4 ("each allowed transition attempted without its record raises") was met for every
transition between lifecycles by migration 10. The two creation rows that need a gate decision were not enforced,
because v1 direct commits (`commit_initial_version`) write no decision row. The same issue also requires that the v1
archival and immutability tests pass **unchanged**, and those tests (`test_full_schema.py`,
`archival/test_t1_to_t5.py`) create asserted claims directly while connected as a superuser. A superuser can disable
any trigger and bypasses row security, so no trigger can bind it. The issue names the application role as the
boundary ("enforcement holds for direct SQL under the application role"), and every engine path that role reaches
already records its decision in the same transaction (`engine.ingest.apply_candidate`, the API). The Project Owner
chose this scope over two alternatives:
- Making the commit primitives write an `accept` themselves was rejected. It would record "grounding passed" when no
  gate ran, which is the silent coercion #67 forbids.
- Binding every role was rejected because it would break the frozen v1 tests.

The re-record rule is needed because supersession, boundary revision and multi-valued end each insert a copy of an
existing belief, and requiring a fresh decision for that copy would invent one.

**What it does not mean.** It is not an exemption for the engine: its gate paths are tested under the runtime role.
It does not cover the ablations that **delete** the closed version (`A2`, `A2b`). Those leave no belief to continue,
so under the application role they are refused. They are evaluation-only and run by the operator. The record is
checked for presence, not for truth: the runtime role may insert `memory_conflict` rows, as it must for the gate to
record its decisions, exactly as for the adjudication records of migration 10.

**Implementation.** `schema/014_entry_records.sql::enforce_entry_records`, `rollback/014_entry_records.sql`,
`schema/migrate.py`, `engine/deletion.py::ARCHIVAL_GUARD_TRIGGERS`, `schema/bitemporal_on_full.sql` (the frozen
design-check script steps the two triggers aside, as it does for the others), and `schema/README.md`.
`INVARIANT_LEDGER.md` `I1′` names the new site and tests. `I1′` stays `PARTIAL` only for the channel contract
(`E-017`, `E-035`).

**Verification.** `tests/integration/test_lifecycle_transitions.py`:
- `test_each_creation_needs_its_record_under_the_application_role`: no decision, or the wrong one, refuses the commit.
- `test_a_staged_or_held_candidate_enters_the_lifecycle_only_with_its_adjudication`
- `test_every_engine_write_path_satisfies_the_entry_records_under_the_application_role`: accept, supersession with a
  re-record, reject, hold and its adjudication, boundary revision and multi-valued end all commit as the runtime role,
  and `A2` is refused.
- `test_a_re_record_may_claim_neither_new_event_time_nor_new_content`
- `test_a_superuser_is_not_bound_by_the_entry_records`
- `test_the_entry_record_triggers_are_deferrable_and_listed_for_deletion`
- `test_rollback_restores_version_13_exactly_and_reapply_works`

**Mutation-checked.** Each of the following makes at least one of these tests fail:
- removing the gate-decision raise
- the event-range containment
- the content match
- the re-record path
- the adjudication raise
- the derivation raise

**Status:** decided by the Project Owner (scope), and verified.

---

### V2-D62 — E-018 conformance suite closed at its W1 scope; three assertions deferred to W3 (closes #69 after W1)

**Context.** The E-018 conformance suite (`tests/integration/test_type_completeness.py`) is green on `main` and passes
all four properties (storable, queryable, deletable, routed) for every legal `(kind × retention × lifecycle × facet)`
tuple against the real migrated schema under a `NOSUPERUSER NOBYPASSRLS` role. Both ledger entries E-018 owns are
`ENFORCED` (`I3` method registry, `I6` retention orthogonality). The v1 regression wall is intact.

**Gap.** Three conformance assertions cannot drive through `Engine.call` because the operations they require are not
yet wired — they are W3 work:

| Property | Blocks on | W3 task |
|---|---|---|
| deletable via `Engine.call` | `delete` operation | `E-037` |
| queryable via `Engine.call` | `query` operation | `E-033` |
| procedural memory via `Engine.call` | procedure creation (`infer`) | `E-030` |

The suite reports each of these as "not yet testable" via a positive assertion
(`test_unwired_operations_are_reported_as_not_yet_testable_never_as_passes`), never as a silent pass.

**Decision.** E-018 is closed at its W1 scope. The three deferred assertions are carried forward as a named
acceptance criterion on each of `E-030`, `E-033`, and `E-037`: when those operations land, the implementer
must re-point the suite's corresponding assertions at `Engine.call` and confirm the conformance suite
`tests/integration/test_type_completeness.py` drives those properties end-to-end through the entry point.

The condition is recorded in `ROADMAP_V2.md` §4 (G1 status block) and in the W3 tracker (#73). G1 is already green
for the properties that can be tested today; the entry-point criterion for the remaining three is G3's responsibility.

**What it does not mean.** It is not permission to skip these properties. Each W3 issue must include an explicit
acceptance criterion requiring the conformance suite re-point. An implementer who lands `E-033` without updating
`test_type_completeness.py` has not met their AC.

**Status:** decided; binding on `E-030`, `E-033`, `E-037`.

---

### V2-D63 — The `E-039` deferral register: every unwired operation has a named owner and an entry-point acceptance criterion

**Context.** `E-039` (#97) is the one runtime door to the 29 operations of `API_V2.md`. On `main` it enforces
authorisation, every condition code and the static error contract, and it dispatches to whatever handlers are
registered — but only **11 of 29** operations were wired as recorded at r13 (12 now, after `E-040` wired `create_scope`): `register_method`, `create_entity`, `link`, `unlink`,
`adjudicate`, `retract_own`, `grant_access`, `revoke_access` (`api/handlers.py`, #132) and `ingest_episode`,
`assert`, `ingest_artifact` (`api/ingest_handlers.py`, #133). The remaining **18** return `forbidden`
(`operation_not_wired`) before any statement runs.

`E-039`'s criterion 5 — *"`E-018`'s conformance suite drives the engine only through this entry point"* — is
therefore a **forward obligation that no single task owns**. `V2-D62` named only three of the blocking operations
(`delete`, `query`, procedure creation); the other fifteen were named nowhere, so an implementer landing one of them
could satisfy their own acceptance criteria and still leave the conformance suite unable to reach the entry point.

**Gap this record closes.** Two defects were found while auditing the deferral:

1. **Fifteen operations had no entry-point criterion at all.** `V2-D62` binds `E-030`, `E-033` and `E-037`; nothing
   bound `refine`, `get`, `provenance`, `infer`, `verify`, `refute`, `link_entity`, `record_outcome`, `dissent`,
   `purge_working`, `expire`, `rebuild_index`, `export`, `stats`, or `cost`.
2. **The operation owners are not all represented as issues.** Every W3 task (`E-030`–`E-038`) and most W4/W5/W7
   tasks read "Child issue: *not yet created*" in their trackers (#73, #74, #75, #79), per the trackers' own rule
   that a child issue is opened **when its wave opens**. `A-014`/`A-015` have no issue either: the earlier
   appearance of `#57`/`#58` as their owners was a false match on `MA-014`/`MA-015`.

**Decision.**

1. **The owner of an unwired operation is the task that builds its behaviour**, exactly as `COVERAGE.md` already
   declares it. Ownership is authoritative in the plan, not in the issue tracker; between them, `COVERAGE.md` and
   `COVERAGE_MATRIX.md` bind every operation to a roadmap task, and `plan_coverage.py --check` fails if any
   operation is unowned. The plan is the owner of record until the wave opens and its child issue exists.
2. **Every one of the 18 carries the same three-part entry-point acceptance criterion**, stated per operation in
   the deferral register of `ROADMAP_V2.md` §4.1 and summarised here: (a) the operation is wired through
   `Engine.call` and reachable only through it; (b) the `E-018` conformance suite is updated to exercise that
   operation **exclusively** through `Engine.call`; (c) the queryable, deletable and procedural properties it
   touches are verified **through the public entry point**, not below it.
3. **`E-039` stays open** until criterion 5 is genuinely satisfied, i.e. until all 29 operations are wired and the
   conformance suite drives the engine only through the entry point. `E-039` does **not** implement any of the 18;
   its remaining work is the criterion, not the operations.
4. **`create_scope` gets a new owning task**, `E-040` (#148), because its declared owner `E-011` (#62) closed on its
   migration scope with the operation deliberately unwired (`api/handlers.py`: "while every scope is personal").
   Leaving it on a closed issue would have left one operation with no open owner.
5. **No duplicate issues.** This record does not open W3/W4/W5/W7 children ahead of their wave gates; the
   trackers keep them "not yet created" and gain the deferral criterion to apply when they are written.

**What it does not mean.** It is not permission to wire an operation under `E-039`, and it is not permission to skip
a criterion 5 obligation because a task is not yet an issue. It also does not reopen `E-018` (#69), which `V2-D62`
closed at its W1 scope: the suite's re-point is the owning tasks' work, verified against `E-039`.

**Consequence for the gates.** `G1` is already green for the properties testable today (`V2-D62`). Criterion 5, and
therefore the last of `E-039`'s five, completes with the last of the 18 operations; the binding obligations are
listed in `ROADMAP_V2.md` §4.1, and the three gate-critical ones (`delete`, `query`, procedure creation) remain the
`G3` conditions `V2-D62` recorded.

**Status:** decided; binding on `E-039` (#97), on all 18 owner tasks in `ROADMAP_V2.md` §4.1, on `E-040`, and on the
W2/W3/W4/W5/W7 trackers (#72, #73, #74, #75, #79).
### V2-D64 — Sub-scopes are admissible; the enterprise root is a sub-scope, and `I5`/`I4` key on the scope (closes the E-011 residual items)

**Decision.** Migration 15 (`015_subscopes.sql`) takes the decision migration 11 deferred to "the per-scope `I9`
decision". Four parts:

1. **A scoped row may lie anywhere inside its owner's namespace.** `<table>_scope_matches_user`
   (`scope_path = '/u/' || user_id || '/'`) becomes `<table>_scope_within_user`
   (`pg_catalog.starts_with(scope_path, '/u/' || user_id || '/')`) on the thirteen tables migration 11 pinned to the
   personal root. The isolation property is preserved, not weakened: `D13` forbids the separator in `app_user.id`, so
   `/u/4/` still does not contain `/u/42/`, and a row can never be placed inside another tenant's namespace. What
   becomes possible is only what the plan already names: `/u/<id>/p/<repo>/` (the coding profile), `/u/<id>/o/<org>/`
   (the enterprise profile), `/u/<id>/p/<topic>/` (research), `/u/<id>/s/<task>/`.
2. **`I5` keys on the scope.** `no_bitemporal_overlap` moves from `user_id WITH =` to `scope_path WITH =`. `V2-D49`
   D10 said the key "stays `user_id` (the owner namespace) in W1"; while every scope *was* the personal root the two
   were equivalent, so that cost nothing. With sub-scopes they are not equivalent: two distinct scopes of one user
   must each be able to hold the same single-valued predicate, and a `user_id` key would refuse the second. `I5`
   declares exclusivity "within a scope", so the constraint now states the invariant instead of an approximation.
3. **`I4` keys on the scope.** `clock_watermark` is keyed by `scope_path` instead of `user_id`, so a sub-scope's
   belief timeline cannot be walked backwards by writing a sibling scope. The watermark is otherwise unchanged.
4. **The enterprise root `/o/<org>/` is a sub-scope, not a namespace.** It is `/u/<owner>/o/<org>/`. A top-level
   `/o/` namespace is *not* admitted, and `scope.paths.parse_scope` continues to reject it. The reason is that both
   ACL and RLS key on a **user** namespace: `scope_grant.owner_user_id` references `app_user`, the read policy matches
   `/u/<caller>/`, and `authorize_write` requires the caller's own namespace. An `/o/<org>/` row would therefore have
   no owner able to grant it and no policy able to return it - representable on disk but unreadable and unwritable,
   which is worse than refused. Organisational sharing uses the mechanism that already exists: the owner creates
   `/u/<owner>/o/<org>/` and shares it with colleagues through `scope_grant`, exactly as any project scope. Admitting
   a first-class org principal would need an org table, an org-membership ACL and a policy that resolves both
   principal kinds; no task owns that, and W2's generality claim (`ARCHITECTURE_V2.md` §13, "no row of this table
   requires a schema change") is satisfied without it.

**Why.** Issue #62 named three residual items and left the fourth implicit: the missing `scope_read` audit path, the
`um_facet`/`clock_watermark` index ownership notes that still pointed at `E-011`, the `I9`/`D10` exclusion-key review,
and the enterprise root, which `parse_scope` rejected. All four turn on the same fact - the substrate could not
represent a scope below the personal root - so resolving them separately would have meant three migrations that each
partly duplicated the others. The `um_facet` index moves to `(scope_path text_pattern_ops, um_facet)` in the same
migration, since facet-filtered reads are scope-filtered reads.

**What it does not mean.** It does not admit a first-class organisational principal (above). It does not change
`authorize_write`: a write still lands only in the caller's own namespace (`V2-D53`), so a sub-scope is not a way to
write into someone else's tree. It does not reopen `D13` or the path grammar: the shape `CHECK` is unchanged and a
segment is still `[a-z0-9][a-z0-9-]*`. And it does not flatten anything on rollback: `rollback/015_subscopes.sql`
**refuses** if a sub-scope row exists, because version 14 cannot represent one and silently moving it to the owner's
root would merge two scopes' beliefs.

**Implementation.** `src/custom_memory_engine/schema/015_subscopes.sql`, `schema/rollback/015_subscopes.sql`,
`schema/migrate.py` (version 15), `schema/README.md`; `ARCHITECTURE_V2.md` §5 rule 4 note and §13.

**Verification.** `tests/integration/test_migration_m15.py` (30):
- `test_every_scoped_table_admits_a_sub_scope_inside_its_owner_namespace` (13 tables)
- `test_a_sub_scope_inside_the_owners_namespace_is_accepted`; `..._inside_another_tenants_namespace_is_refused`
- `test_the_prefix_boundary_holds_for_ids_that_share_a_prefix` (`/u/4/` vs `/u/42/`)
- `test_the_exclusion_key_is_the_scope_not_the_user`; `test_a_v1_row_in_the_personal_root_still_excludes`;
  `test_two_users_with_the_same_sub_scope_do_not_collide`
- `test_the_watermark_is_keyed_by_scope`; `test_a_sibling_scope_cannot_walk_belief_time_backwards`;
  `test_belief_time_still_cannot_move_backwards_within_one_scope`
- `test_the_facet_index_is_led_by_the_scope_path`; `test_the_facet_index_serves_a_scope_and_facet_filtered_read`
- `test_a_top_level_org_root_is_still_not_a_namespace`; `test_the_resolver_grammar_admits_the_shapes_the_schema_now_accepts`
- `test_rollback_restores_version_14_exactly_and_reapply_works` (catalog fingerprint identical);
  `test_rollback_refuses_while_a_sub_scope_row_exists`

**Mutation-checked.** Each of the following makes at least one test fail: restoring the equality `CHECK`, keying the
exclusion constraint on `user_id`, keying the watermark on `user_id`, leading the facet index with `user_id`, and
dropping the rollback's sub-scope guard.

**Status:** decided and verified.

### V2-D65 — `create_scope` ships now, and a malformed scope argument is a grammar error, not `forbidden` (closes #148)

**Decision.** Two parts, the first answering the open question issue #148 required to be settled before code.

1. **`create_scope` ships now** (option (a) of that issue), because the premise of its deferral is gone. `E-011` left it
   unwired on the grounds that "while every scope is personal a sub-scope cannot hold a row, so creating one would
   record a scope nothing can use". Migration 15 (`V2-D64`) admits a row anywhere inside its owner's namespace on all
   thirteen scoped tables, so a created sub-scope is immediately usable for entities, sessions and memories alike, and
   the operation does exactly what `API_V2.md` §4.10 says. It is not a writer for `project`/`shared` *visibility*, which
   stays `I7`'s remaining gap: a scope can now exist and hold `private` rows.
2. **A malformed scope argument is `validation_error`, not `forbidden`.** `API_V2.md` §4.10 separates the two:
   `validation_error` for the §6 grammar, `forbidden` for a target outside the caller's namespace. The
   `namespace_owner_only` check previously answered `forbidden` for an unparseable path, which told a caller its input
   was well formed. It now parses first and refuses grammar faults as `validation_error`/`scope_invalid`, then applies
   the namespace rule. The mutation test in `tests/test_api_entry.py` still removes the namespace check and shows the
   same call succeeding, so the authorisation half is still the reason `/u/bob/` is refused.

**Why a scope needs no row.** A scope *is* its canonical path: `scope_grant` records read membership of a subtree, and
`memory_version.scope_path` records where a row lives. A `SCOPE` table would duplicate the path in every row that
already carries one and would need its own RLS, its own consistency checks against thirteen tables, and a cascade story
for revocation. Creating a scope is therefore validation plus an idempotency entry, and a content-free audit row.

**Consequential change.** `namespace_owner_only` is shared with `grant_access` and `revoke_access`, so those now also
answer `validation_error` for a malformed scope instead of `forbidden`. That is the contract's own distinction and the
tests for both are green; a well-formed target outside the namespace is still exactly `forbidden`, so nothing about
existence leaks.

**Implementation.** `api/handlers.py::Handlers.create_scope`, registered in `register_structure_handlers`;
`api/entry.py::_namespace_owner_only`; `security/audit_guard.py` gains `scope_kind` and `depth` (a declared word and a
small integer, so neither can carry a path segment, a user or a scope).

**Verification.** `tests/integration/test_create_scope.py` (26): wired and absent from `unwired_operations`; every
creatable kind; `personal` refused; nesting below an existing sub-scope; a Tier B caller refused before any statement;
a parent outside the namespace refused with the same `forbidden` as an unknown one; eight malformed-argument cases;
depth limit; replayed key returns the original path with one idempotency row and one audit row; a second key for the
same path succeeds; another caller's key never leaks a scope; the audit row carries the kind and depth but no path,
segment or user; and the created scope really holds an entity, a session row and a memory version.
`tests/integration/test_type_completeness.py` drives `create_scope` through `Engine.call` and asserts it has left the
unwired set. `tests/test_api_entry.py` keeps the mutation case for `namespace_owner_only`.

**Status:** decided and verified.

---

### V2-D66 — Early W3/W5 execution for E-021 acceptance criteria 4 and 5 (Option B; Project Owner authorization 2026-11-04)

**Context.** Issue #136 (`E-021`, W2) cannot satisfy its acceptance criteria 4 and 5 with the operations wired at the close of W1. The dependency analysis — recorded as `E-021-F5` on #72 — established the following:

- AC4 requires six coding fixtures to be stored **and queried** through the generic public API. `query` and `get` are unwired; `Engine.call` answers `forbidden(operation_not_wired)` for both.
- AC4 also requires a `ci_procedure` (`kind='procedure'`) fixture to be stored. `api/ingest_handlers.py::_assert` refuses any kind other than `claim` (`kind_not_supported`).
- AC4 requires a `recurring_failure_mode` abstraction (`kind='abstraction'`, `lifecycle='inferred'`) to be generated. The `infer` operation is unwired.
- AC5 requires each fixture to be observed routing to its declared channel from a public API response, not only from a stored-row read.
- The source-type constraint in migration 8 (`source_type IN ('turn','external')`) and the handler (`_SOURCE_TYPES = ("turn","external")`) reject the declared profile source types `tool`, `file`, and `event`.
- `api/ingest_handlers.py` never reads its declared `scope` argument; a call requesting `/u/alice/p/chrona/` stores `/u/alice/`. The transaction layer is ready (PR #153 wired `CandidateVersionInput.scope_path`); only the handler hookup is missing.

Ordinarily `E-033` (`query`/`get`), `E-030` (procedure creation/`record_outcome`), and `C-021` (`infer`) are gated behind `G2`, which itself requires `E-021` to be complete. This is a circular dependency that cannot be resolved by configuration alone, and the `E-021` scope guard (`W6`/`W7`) forbids engine changes inside that issue.

**Decision.** The Project Owner explicitly authorized **Option B** (2026-11-04): execute the minimum W3 and W5 operations required by E-021 acceptance criteria 4 and 5 early, ahead of the `G2`/`G4` gate sequence that would ordinarily govern them. This resolves the circular dependency without dropping any acceptance criterion.

**Scope of the early execution.** The following and only the following operations are authorized to land before their wave gates for the purpose of satisfying E-021:

| Operation | Owning task | Reason needed |
|---|---|---|
| `ingest_episode` scope propagation | `E-039`/`E-011` residual | repository-scoped episode writes |
| `assert` for `procedure` kind | `E-030` partial | store `ci_procedure` fixture |
| `query` | `E-033` partial | retrieve all six fixtures through public API |
| `get` | `E-033` partial | single-artifact retrieval |
| `infer` for `abstraction` (minimal) | `C-021` partial | generate `recurring_failure_mode` fixture |
| `tool`/`file`/`event` source types | `V2-D41` extension | declared coding-profile source types |

Every other W3/W5/W7 operation remains gated behind its wave entry condition. The register entry (`ROADMAP_V2.md` §4.1) for each operation is **not** removed; the full behaviour its owning wave requires remains an open obligation. This decision records the early-landing permission only.

**What this decision does not change.**
- `G2` definition is unchanged: zero-fork dual-profile, cross-tenant exposure zero, `E-026` retention-floor proof. Nothing in this decision is evidence for `G2`; `G2` requires all W2 issues complete and their own gates.
- The full-scope behaviour of `query` (channel budgets, cost report, provenance bundle, temporal predicates, `refine`) and `infer` (statistical ledger, CP-bound admission, dependence correction) belong to their owning W3/W5 tasks.
- The admission-placement question for `C-021` (whether the CP bound rejects at `infer` time or only gates `verify`) must still be settled before `C-021`'s full W5 implementation. The minimal `infer` landing here registers the method call and creates an `inferred` abstraction; it does not implement the statistical verifier.
- Source-type additions (`tool`/`file`/`event`) require a schema migration and grounding-policy update consistent with `V2-D41`'s canonicalization rules.
- All W1 quality rules (`W1`–`W8`), the `PLAN/CONTRIBUTING.md` workflow, regression-wall integrity, and the security/isolation invariants apply to every PR in this workstream.

**Validation conditions.** This decision is satisfied when:
1. All six coding fixtures (tool_call, diff, test_run, repo_claim, ci_procedure, recurring_failure_mode) can be created through `Engine.call` under `src/custom_memory_engine/profiles/coding.json`.
2. Each fixture can be retrieved through `Engine.call(query)` or `Engine.call(get)` with the correct channel and scope.
3. Repository-scope writes (`/u/<user>/p/<repo>/`) persist correctly.
4. The full CI-scope suite (`verify`) is green on every merged commit.
5. `E-021` issue #136 acceptance criteria 1–10 all hold simultaneously, verified in `tests/integration/test_coding_profile_db.py`.

**Implementation PRs.** Each of the following will be opened, reviewed, and merged in order before the E-021 PR (#143) is finalised:
1. **E-039 scope propagation** — ingest handlers read the `scope` argument and pass it to `CandidateVersionInput.scope_path`.
2. **Source-type extension (V2-D41)** — migration 16 extends the DB constraint; grounding updated; `tool`/`file`/`event` accepted in handlers. *Correction:* migration 16 left the shape rule (`memory_evidence_turn_source_check`) and the source trigger knowing only `external`, so the handlers recorded every such source as `external` and the declared type was lost. Migration 17 widens both; the rollback refuses while a declared row exists. Earlier `external` rows keep their recorded type (the declared one was never stored and is not invented).
3. **E-030 minimal — `assert` for procedure** — `_assert` accepts `kind='procedure'`; retention floor applied.
4. **E-033 minimal — `query` and `get`** — read path wired through `Engine.call`; channel membership verified from response.
5. **C-021 minimal — `infer` for abstraction** — creates an `inferred` abstraction via registered method; abstraction carries its bound stub pending W5 statistical verifier.
6. **E-021 final** — merge `main` into PR #143 (not a rebase: the branch carries another contributor's commits, which are not rewritten); all six fixtures exercised end-to-end.

**Links.** Issue #136, PR #143, tracker #72, E-021-F5 on #72, W3 tracker #73, W5 tracker #75, E-039 issue #97.

**Outcome (2026-11-04).** All five validation conditions hold. The implementation PRs merged in order:
- #159: scope propagation, `procedure` through `assert`, and the source types.
- #160: `query`/`get` (E-033 minimal, #156).
- #161: `infer` (C-021 minimal, #158).
- #162: migration 17, the correction under item 2.
- #143: E-021, merge `11ea247`.

The `verify` job was green on every merge. `tests/integration/test_coding_profile_db.py` exercises all six fixtures end to end.

Two statements above were overtaken during implementation:
- The `C-021` admission-placement question was already settled by `V2-D40` (adopted and implemented: the bound gates verification, not creation). The minimal `infer` applies only the creation verdict of `engine.admission.assess_soundness`.
- The minimal `infer` stores the ledger the caller supplies, not a computed one. The full `C-021` row of W5 (#75) owns ledger counting, the dependence correction and `verify`/`refute`.

Deferred work surfaced by E-021 is tracked separately:
- #163: the `promote_to_durable` runtime consumer, E-021-F4.
- #164: enforcement of a profile's extractor source types.

**Status:** decided and implemented (Project Owner authorization 2026-11-04).

---

### V2-D67 — A profile is bound when the generic handlers are registered, not per call (E-021 requirement 4.6)

**Context.** #136 requirement 4.6 says every fixture artifact is "created, queried and read through the public entry point / generic engine API, with the profile passed as a parameter". The handlers take the profile at registration: `register_ingest_handlers(engine, runtime, profile)` and `register_infer_handlers(..., profile=)`. `Engine.call` does not take it. Read literally, the sentence could ask for a per-call profile.

**Decision.** Binding at registration satisfies the requirement. A per-call profile argument is not added.

**Rationale.**
1. **The contract forbids it.** `Engine.call` admits only an operation's contract inputs (`api/entry.py`, step 2, `unknown_argument`), and no `API_V2.md` operation declares a profile input. A per-call profile would change the public contract, which E-021's scope guard does not allow.
2. **It would weaken a guarantee.** `V2-D08` makes a profile configuration of the one engine. If the profile came per call, a Tier B/C caller could choose its own `kinds` and retention defaults, and a deployment guarantee would become a caller choice.
3. **The requirement's purpose is met.** Every artifact enters only through `Engine.call`. The profile is a parameter of the generic API. No helper special-cases a profile (the E-019 no-branching scanner and the name-literal scan).
4. **It is the established pattern.** The E-018 conformance suite (closed under `V2-D62`) proves the other profile-defined memory types with exactly this binding.

**Consequence.** One engine serves one profile. A deployment that serves several profiles builds one engine per profile from the same code. That is what `E-023`'s dual-profile job and `E-025`'s fork audit measure.

**Status:** decided under the Project Owner's delegation (2026-11-04). Evidence: #136 closing comment, PR #143.

---

### V2-D68 — A write names its scope as an explicit full path; a profile's `scope_root` is never applied to a request (#97)

**Context.** The r14 thread on #97 asked an open question. A profile declares `scope_root` (for the coding profile, `/u/{user}/p/{repo}/`). Should a handler apply it to a request, expanding or defaulting the `scope` argument, or must the caller always send the full path?

**Decision.** The caller sends the **full scope path**. Handlers parse it, check it, and store it exactly as given. They never construct, expand, prefix or default it from `scope_root`.

`scope_root` is **deployment-level metadata**:
- It is the shape a deployment's scopes take, validated against the scope grammar (`profile.py::_scope_root`).
- It is consumed by deployment tooling and by tests that substitute its placeholders.
- It is never consumed by a request.

`scope_path` is **request-level and row-level**: the one path a write lands in.

**Rationale.**
1. **The contract already says so.** Every writing operation takes `scope` as an explicit input (`API_V2.md` §3–§4). `API_V2.md` §1 and `ARCHITECTURE_V2.md` §5 rule 2 add "nothing is implicit".
2. **The engine cannot fill the template.** `{repo}` is known only to the caller, so any expansion would need a second, non-contract argument. That is a contract change the frozen API does not allow (`V2-D46`, `V2-D47`).
3. **It keeps the security decision in one place.** `scope_writable` (`scope.access.authorize_write`) judges the exact path that is stored.
   - With expansion, the path being authorized and the path being stored could differ.
   - Two profiles could also map one request to different scopes. That is behaviour branching on configuration in the write path, against the spirit of `V2-D08`.
4. **Reads are already explicit.** A read selector must equal a private row's scope (§5 rule 4, T-06). A caller that writes `/u/alice/p/chrona/` therefore already has to know that full path to read it back. An implicit write path would make writes and reads asymmetric.
5. **No existing behaviour changes.** This is what #159 implemented, what #168 pins, and what E-021 (#143) uses.

**Security implications.**
- **Namespace:** a path outside `/u/<caller>/` is `forbidden` before any statement runs (`scope_writable`). The handler re-checks it as defence in depth.
- **Omitted scope:** refused with a static `validation_error/scope_required`, never silently defaulted to the personal root or to `scope_root`.
- **Malformed path:** refused with `validation_error/scope_invalid`, never normalised into a different scope.
- **Errors:** carry no path (`API_V2.md` §1.1).

**Examples.** Coding profile, caller `alice`:

| Request `scope` | Result |
|---|---|
| `/u/alice/p/chrona/` | Stored at `/u/alice/p/chrona/` |
| `/u/alice/` | Stored at `/u/alice/`, not expanded to a repository |
| *(omitted)* | `validation_error/scope_required`, nothing written |
| `/u/bob/p/chrona/` | `forbidden`, nothing written |
| `chrona` (relative) | `validation_error`, nothing written |

**Evidence.**
- `tests/integration/test_api_wiring_ingest.py`:
  - `::test_both_writers_store_the_scope_the_caller_names`
  - `::test_both_writers_refuse_a_foreign_or_missing_scope_before_writing`
  - `::test_a_profile_scope_root_never_rewrites_the_scope_the_caller_names`: two profiles with different roots store the same request identically. A handler mutant that expands `scope_root` fails it.
- `tests/integration/test_coding_profile_db.py` exercises the same behaviour end to end.

**Status:** decided under the Project Owner's delegation (2026-11-05). This answers the open question on #97; it does not affect #97's criterion 5.

---

### V2-D69 — A bound profile admits only the source types its extractors declare (#164)

**Context.** A profile declares `extractors[].source_types` (`E-019`). `V2-D08` makes extractors profile configuration. The ingest path did not use the declaration: under the coding profile, an `external` source was accepted although no coding extractor reads one.

**Decision.** When an ingest handler is bound to a profile (`V2-D67`), `ingest_episode`, `assert` and `ingest_artifact` refuse any source or evidence whose type no extractor of that profile declares. The refusal is a static `validation_error/source_type_not_enabled_by_profile`, raised before any statement runs. Without a bound profile nothing is narrowed: the `API_V2.md` §2 declared types apply, as before. The rule mirrors `kind_not_enabled_by_profile` and branches only on profile data, never on a profile's name.

**Compatibility.**
- **Conversational profile:** declares `turn` only. It is the v1 equivalent, whose writer is chat turns, so its accepted inputs are unchanged.
- **Coding profile:** declares `tool`/`file`/`event`/`turn`, so `external` is now refused under it. No coding fixture uses `external`.
- **Unbound engines:** every engine wired without a profile, including the v1 regression wall, is unaffected.
- **Tests:** three integration tests had ingested `external` under a profile that does not declare it, as a convenience. They now use declared types.

**Evidence.** `tests/integration/test_api_wiring_ingest.py`:
- `::test_a_bound_profile_admits_only_the_source_types_its_extractors_declare` covers both writers. The refused calls write nothing, and they succeed once the profile's extractors declare the types. A mutant that drops the check fails it.
- `::test_without_a_profile_every_declared_source_type_is_admitted`
### V2-D70 — The promotion event: a durable artifact derived from a session source makes that source durable (#163, E-021-F4)

**Context.**
- A profile declares `retention.promote_to_durable`. The coding profile sets it, and §13's coding row reads "session→durable on promotion".
- Nothing read it, which was finding E-021-F4.
- `ARCHITECTURE_V2.md` §1 makes retention mutable "(promotion/demotion)" and says promotion is "an explicit, recorded operation". `V2-D10` requires "a recorded reason".
- `V2-D14` (default values and expiry) stays open. It owns the numbers, not the event.

**Decision.** The promotion event is **a durable artifact coming to rest on a `session` source**. Within the W2 operations, that is `infer` deriving an abstraction (always `durable`, by the `V2-D10` floor) from its sources. When the bound profile sets `promote_to_durable`:
- each of those sources whose retention is `session` becomes `durable`, in the same transaction as the derivation;
- each promotion is recorded as a content-free `audit_event` (`event_type='retention_promoted'`, `reason_code='derived_into_durable'`, the source's `kind`).

Without the flag, the same calls leave the source `session`. Only `session` is promoted: `working` keeps its own purge semantics (`E-026`). Nothing is demoted.

**Rationale.**
1. **It closes a real hole.** `V2-D10` says retention must never change answerability. If a durable abstraction rested on a session episode, then expiring that session would leave durable knowledge whose provenance had gone. Promoting the source at the moment the durable artifact starts depending on it closes that.
2. **The frozen API has no `promote` operation**, and adding one is a contract change (`V2-D46`, `V2-D47`). The event therefore lives inside an existing operation, and it is explicit in its trigger and recorded with its reason, as §1 requires.
3. **It is configuration, not code.** The handler reads `profile.retention.promote_to_durable` and never a profile's name (`V2-D08`). The database still enforces the floors.

**Not decided here.** Other promotion triggers, such as procedure reuse through `record_outcome` (`E-027`, unwired) or reinforcement counts, extend this rule when their operations land. They are not part of #163.

**Evidence.** `tests/integration/test_retention_promotion_db.py`, all through `Engine.call` under a restricted role:
- the same events promote under one profile and not under another;
- a `working` source is never promoted;
- a replayed `infer` records no second promotion.

Two controls each fail at least one test: a mutant that ignores the flag, and a mutant that never promotes.

**Status:** decided and implemented under the Project Owner's delegation (2026-11-05).

---

### V2-D71 — Cross-scope entity identity is an explicit link between scope-local ids; a claim names its entity through `assert`'s optional `subject_entity` (#139, E-024)

**Context.**
- `ARCHITECTURE_V2.md` §5.2 requires four things. Entity ids are opaque and created only through the API. An entity's attributes are never global. A cross-scope `link_entity(a, b)` needs authorisation on **both** scopes. There is no existence oracle.
- The schema already places each entity in **one** scope of its principal (`entity.scope_path`, migration 10), and its label follows that scope's read rules.
- Two things were missing. Nothing could persist a link, because `link_entity` was unwired with no table behind it. And no claim could say which entity it is about, because `assert` always wrote `subject_key='self'`.
- The first E-024 branch also added a persisted `scope` table, and with it guards that refused writes into any sub-scope lacking a row. That reversed `V2-D65` ("a scope *is* its canonical path") without a record. It broke existing callers: four `test_retention_promotion_db.py` tests, plus four other suites that had to be patched to call `create_scope` first.

**Decision.**
1. **Two scopes, two ids, one explicit link.** The same person referenced from two scopes is two opaque entity ids. Migration 18 adds `entity_link` (forced RLS, `SELECT, INSERT` only, composite `(id, user_id)` foreign keys, one row per unordered pair and relation). `link_entity` writes it under `targets_readable`, so both endpoints must be in the caller's read set, and an unreadable endpoint answers exactly like a missing one (T-09). A link merges nothing.
2. **`assert` gains one optional input, `subject_entity`.** When it is absent the call is unchanged (`subject_key='self'`). When present, the entity must be readable (`targets_readable`, now on `assert`) and must lie in the scope the claim is written to; otherwise the answer is `forbidden`, the same as for an unknown id. This is an additive contract change, recorded here and in `API_V2.md` §4.3.
3. **`V2-D65` stands.** There is no scope table. `create_scope` stays validation plus an idempotency entry plus a content-free audit row, and a write into a valid sub-scope needs no prior row.

**Rationale.** (1) satisfies every §5.2 bullet with the schema the plan already has, and adds no global identity index, which would be the cross-tenant side channel `V2-D25` exists to prevent. (2) is the smallest change that lets "a claim in scope A naming entity `e`" exist at all; making it optional keeps every existing caller byte-for-byte the same. (3) keeps one source of truth for where a row lives, its `scope_path`. A second one would need its own RLS, consistency checks against thirteen tables, and a revocation story (`V2-D65`).

**Evidence.** `tests/integration/test_cross_domain_entity.py` runs under the restricted runtime role, through `Engine.call`, in both call orders:
- claims stay readable only in their own scope;
- the link is idempotent;
- an unreadable entity and a missing one give identical `forbidden` answers;
- cross-scope provenance renders only the `V2-D37` handle;
- a `subject_entity` from another scope is refused.

Mutation controls each fail at least one test: removing `targets_readable` from `link_entity`, and letting `subject_entity` cross scopes. The four `test_retention_promotion_db.py` tests pass unchanged.

---

### V2-D72 — A working-scope purge deletes only episodes no surviving row cites as evidence (E-026, rule PW-1; closes `E-026-F1`)

**Context.**
- `ARCHITECTURE_V2.md` §1 says a workspace reset "may purge working-scope episodes, and that purge is recorded as a scope event, **not** a deletion of knowledge", and that working memory is "still evidence-bearing".
- `DELETION_ORDER.md` / `V2-D09` make a *deletion* remove or re-derive everything derived from what it deletes.
- A working episode that is the **evidence** for a durable artifact sits between those two statements. E-026 §4 required the choice to be written down before code, and logged it as `E-026-F1` (a purge could retain cited episodes indefinitely, so working memory is not strictly bounded).

**Decision (rule PW-1, `engine/purge.py::plan_purge`).** `purge_working(ctx, scope)` physically deletes every
`kind='episode'`, `retention='working'` row in the scope's subtree, **except** an episode that a surviving row cites as
evidence. The citations are:
- a `memory_derivation` source;
- a `negative_constraint` lift evidence;
- a compared version of a `memory_conflict`;
- an endpoint of a `contradicts` association whose origin routes it to `conflicts` (`V2-D48` C-P4).

Such an episode is retained exactly as it is: not promoted (`V2-D70` promotes `session` only) and not demoted. No
other row changes, and a second purge purges nothing while the citation stands. The purge records one content-free
`audit_event` (`working_purged`) carrying two counts.

**Rationale.**
1. **Following the closure makes the purge a deletion of knowledge.** `V2-D10`/`I10` forbid that; it is the guarantee this task exists to prove.
2. **Dropping the pointer is worse.** It leaves an `inferred` row with no derivation (`I1'`), and a later lineage deletion of the episode's source can no longer reach the inference built on it (`V2-D09`, `I8`). Retaining the episode breaks neither.
3. **A grounded claim never points at an episode.** `assert` grounds it on the turn the episode recorded, so purging the episode leaves the claim and its evidence intact by construction.
4. **The contradiction case is a real cascade.** An association cascades with its endpoint, so purging an episode at one end of a contradiction-opening edge would silently move a durable claim from `conflicts` back to `facts`. Retaining that episode is the only local answer.

**Consequences, recorded rather than hidden.** Working memory is not strictly bounded: an episode cited by a surviving artifact stays until that artifact is deleted. Reclaiming those episodes needs a demotion or re-derivation rule, which `V2-D14` (defaults and expiry) or `E-027` (outcome-driven retention) would have to state. `E-026-F1` is thereby answered on semantics and stays open on the bound.

**Evidence.** `tests/integration/test_purge_working_db.py` through `Engine.call` under the restricted role:
- only the unreferenced working episodes of the scope go (every table diffed before and after);
- the durable claim and the inferred abstraction answer exactly as before, in the same channels;
- a purged episode is absent, not a tombstone, and answers like an unknown id;
- the retained evidence still lets the deletion closure reach the inference;
- a `contradicts` edge keeps its artifact in `conflicts`;
- an admin-only, own-namespace purge is refused for every other credential with no row changed.

`tests/test_purge_rule.py` covers the rule as a pure function, including the fixpoint. Controls: a destructive plan (cascading to dependents) fails the answerability test, and a citation-blind plan is refused by the restrictive derivation key.

**Status:** decided and implemented under the Project Owner's delegation (2026-11-11).

---

### V2-D73 — `query` reads time through the engine's own clock, and the reference instant is never caller-supplied (E-031; closes `E-026` rows 5 and 13)

**Context.**
- `API_V2.md` §4.2 declared `QueryRequest.mode` from the start, and `TECHNICAL_SPECIFICATION.md` §3.5 specified all
  four predicates in SQL. `engine/predicates.py` implemented them (`compile_predicate`, `fetch_versions`) and
  `engine/retrieval.py` consumed them for ranking, but the public `query` operation had no `mode` at all: it always
  returned the current view. `E-026` §9 rows 5 and 13, `E-024`'s controls and any caller that asked "what did the
  engine believe then" were blocked on this, and two tests fell back to calling `fetch_versions` in-process.
- The obvious wiring (read `reference_time` from the request, default to the host clock) is wrong twice over:
  - **`I4` says belief time is never caller-supplied.** A request that picks ρ can ask what the engine believed at an
    instant it never observed, including a future one.
  - **The host clock and the database clock are different clocks.** The v1 replay harness pins `app.clock`; production
    does not. A defaulted reference instant therefore drifts from the `recorded_range` it is compared against, and
    a row recorded microseconds earlier reads as "not yet current". This was not theoretical: the first draft of
    this change defaulted to `datetime.now(UTC)` in Python and every temporal assertion in the new suite returned
    nothing, because `recorded_range` had opened at the database's clock, later than the host's answer.

**Decision.**
1. **ρ is the engine's clock, read inside the caller's transaction.** `engine.predicates.reference_instant` returns
   `transaction_timestamp()`, which is the clock `clock/allocation.py::allocate_clock` writes `recorded_range`
   against. Using the writer's clock is what makes "current" mean the same thing on both sides.
2. **`reference_time` is refused.** A request carrying one is `validation_error/reference_time_is_the_engine_clock`.
   This is a *removal* of an input that never worked, not a behaviour change for a caller.
3. **The temporal predicate and the caller's read predicate are one statement.** `engine.predicates.fetch_scoped_rows`
   composes `compile_predicate` with `scope.access.read_predicate_named`. Applying the read set afterwards in Python
   would make `limit` count rows the caller cannot read, so a caller could receive fewer than `limit` rows while more
   readable ones existed. `read_predicate` and `read_predicate_named` share one clause builder for exactly the reason
   they are security-critical: two copies would drift.
4. **`audit_as_of` results go in `audit[]`, never in a channel.** `TECHNICAL_SPECIFICATION.md` §3.5 forbids using
   them as answer context, and `channel_of` maps a retired artifact to `none` — so routing audit rows through the
   channels would silently drop exactly the withdrawn rows an audit exists to show. `QueryResult.is_audit` marks the
   result, and Tier B is refused before any row is read.
5. **`change` stays unwired** (`validation_error/mode_change_not_wired`). It is a diff of two temporal views, which is
   a rendering concern owned by `E-033`, not a predicate.

**Consequences, recorded rather than hidden.**
- `E-026` §9 rows 5 and 13 now hold *through the public entry point*: `test_purge_working_db.py::temporal_query`
  calls `Engine.call` in both modes and the answerability assertion is made only on that result.
- Two latent bugs surfaced and are fixed with the change, both pre-existing:
  - `retract_own` closed `recorded_range` at `clock_now()` without allocating a clock tick, so it could invert the
    range against the row's own opening bound and the `guard_update` trigger refused the withdrawal. It now allocates
    a tick like every other writer (`R3`).
  - `evaluation/harness/budget.py::_locked` treated a lock released by its holder between `mkdir` and the
    re-validation as an *unsafe path* (`ValueError`), and its release path raised inside `finally`, leaving the lock
    directory behind and wedging the ledger. Both are fixed, with a regression test per defect verified by mutation.
- Channel **budgets**, **ranking**, the **cost** report and `refine` remain `E-033`'s, and temporal reads currently
  order by `lower(recorded_range) DESC, id` rather than by relevance. A caller that wants relevance must wait for
  `E-033`; nothing here pretends to rank.

**Evidence.** `tests/integration/test_temporal_query_db.py` (20 tests, every mode plus its controls: a target before
the scenario returns nothing, a supplied `reference_time` is refused, a withdrawn claim is invisible to
`current_state` and visible to `audit_as_of`, no mode crosses a tenant boundary, and the read-set semantics are shown
to be sensitive). `tests/integration/test_purge_working_db.py` now asserts answerability only through `Engine.call`.
Controls: widening `current_state`'s event filter fails the PW-1 test; making `historical_as_of` ignore its target
fails 3 tests.

**Status:** decided and implemented under the Project Owner's delegation (2026-11-12).

---

### V2-D74 — An entity subject is a foreign key, not a string that looks like one (E-024 §10 row 10; supersedes the waiver)

**Context.** `V2-D71` let `assert` name the entity a claim is about through an optional `subject_entity`, and stored
that id in `memory_version.subject_key`. Two properties were enforced, correctly, only in the handler
(`api/ingest_handlers.py::_assert`): the entity must exist in the caller's read set (`targets_readable`), and it must
lie in the claim's own scope. The schema could not tell an entity id from the literal `'self'` every v1 claim
carries: there was no foreign key, so a claim could cite a deleted entity, and the same-scope rule lived in exactly
one Python function. `E-024` §10 row 10 recorded "no engine-source diff" as **not met, by authorisation** — the owner
had authorized the substrate. That waiver is now re-examined rather than inherited: the substrate is legitimately
`entity_link` (migration 18) plus this.

**Decision.** Migration 19 makes both properties structural, and nothing is backfilled.
1. `memory_version.subject_entity_id` is a typed column: `NULL` means the subject is `subject_key` itself
   (`'self'` today); a value means the subject *is* that entity. `subject_key` keeps holding the same id, because it
   is the column the `no_bitemporal_overlap` exclusion and the supersession rules compare.
2. Existence, principal and scope are **one composite foreign key**:
   `(subject_entity_id, user_id, scope_path) REFERENCES entity (id, user_id, scope_path) ON DELETE RESTRICT`. A claim
   therefore cannot name another scope's entity, another principal's entity, or a missing one, and a cited entity
   cannot be removed while the claim stands. A link (migration 18) cascades because a link *is* the relation; a
   claim's subject is part of the claim, so it restricts.
3. `CHECK (subject_entity_id IS NULL OR subject_entity_id = subject_key)` keeps the two spellings from disagreeing,
   so a row cannot be excluded from supersession under one id while citing another.
4. `guard_update` is replaced to treat the new column as belief content: re-pointing a claim would rewrite what the
   claim is about. The replacement re-pins the function's `search_path`, which `CREATE OR REPLACE` resets — a
   security property migration 4 establishes for every engine function.

**Why the waiver is not preserved.** "No engine change" was a scope guard, and the owner's instruction on the issue
superseded it. What survives from it is the *principle*: every engine change must be justified line by line, and this
one is — `subject_entity_id` exists because a Python-only invariant is one that a replay harness, a backfill or an
operator can break, and because `E-024`'s own §9 requires the cross-domain link to be explicit and authorized rather
than an opaque id in a free-text column. The migration adds no table, deletes no data, and its rollback restores
version 18's catalog fingerprint exactly.

**Evidence.** `tests/integration/test_migration_m19.py` (9 tests): a same-scope entity is accepted; another scope,
another principal and a missing entity are each refused by the foreign key; the two spellings cannot disagree; a
cited entity cannot be deleted; the column is immutable in place; a v1 row is unchanged with a NULL entity; and the
rollback restores version 18 exactly (fingerprint compared) and re-applies. The rollback restores `guard_update`
byte-for-byte from the version-18 definition.

**Status:** decided and implemented under the Project Owner's delegation (2026-11-12).

---

### V2-D75 — A procedure's ranking is its recorded outcome rate, ordered within the `procedures` channel and never across channels (E-027)

**Context.** `ARCHITECTURE_V2.md` §6 gives a procedure `outcome_stats` and says `record_outcome` updates it, but no
decision record says *what the statistics do*. `V2-D11` fixes the five retrieval stages and keeps RRF as the fusion
operator inside candidate generation; `CHANNELS.md` §1 makes channel membership a pure function of
`(kind, lifecycle, verification, stale, open_contradiction)` and puts ranking explicitly outside `E-017`. So the
question "may statistics reorder a channel, and by what formula?" was open. `E-027` could not be implemented without
answering it, and inventing an answer without recording it would have been the worse option.

Three constraints shaped the answer, and none is negotiable:

- the temporal predicate is a **filter, never a score term** (`ARCHITECTURE_V2.md` §9.3);
- membership is pure, so ranking may not move an artifact between channels (`I1'`, `CHANNELS.md` §1);
- v1 behaviour for non-procedure kinds is not altered (`rank_versions`, RRF k=60, untouched).

**Decision.** The `procedures` channel is ordered by

```
rank(procedure) = ( -smoothed_success_rate, -last_used_at, procedure_id )
smoothed_success_rate = (successes + 1) / (successes + failures + 2)
```

1. **Laplace smoothing, not a raw ratio.** A raw `successes / (successes + failures)` is undefined at 0/0, and both
   obvious repairs are wrong: scoring an unseen procedure `0` ranks it below one that has demonstrably failed, and
   scoring it `1` ranks it above one that has demonstrably worked. `(s+1)/(n+2)` puts an unseen procedure at exactly
   `0.5` — between the two — and keeps the function monotone in both counters. The property is pinned directly rather
   than only through retrieval (`tests/test_procedure_ranking.py`).
2. **Recency, then id.** An equal rate is broken by `last_used_at` descending (never-used last), then by id ascending.
   Ids are unique, so the order is **total**: no query can depend on DB row order, and the same rows always produce the
   same sequence.
3. **Applied after routing, never before.** `api.read_handlers` orders `buckets["procedures"]` after `channel_of` has
   decided membership. A `contested` or `retired` procedure therefore cannot be promoted by a perfect record: it never
   reached the bucket. This is asserted per item, not argued.
4. **Cost is recorded, not ranked.** `mean_cost` is persisted and returned because §6 asks for it, but it does not
   order procedures. Cost against success rate has no agreed trade-off here; inventing a weight would make the order
   depend on a number nobody chose. `E-033` owns budgets and may consume it.

**Consequences, recorded rather than hidden.**
- `E-027` needed a substrate that did not exist: migration 20 adds one `outcome_stats` side table, the shape
  `abstraction_stats` established. It is the **minimum E-030 extends** — `E-030` owns procedure storage and adds
  `preconditions`/`steps` as columns on that table. **Superseded by `V2-D77`:** they live in their own side table,
  `procedure_shape` (migration 21), for the reasons recorded there. Recorded as `E-027-F1` on #72 because it means a W2 task consumes a
  W3 substrate, and the roadmap owner should decide whether to move the task or the substrate.
- `record_outcome` is **not idempotent by design** — it takes no `idempotency_key` in `API_V2.md` §4.5, and an
  increment has no identity to deduplicate on. Replaying it records a second outcome, which is asserted.
- The counters are monotone in the **schema** (`guard_outcome_stats`), not only in the handler: rewriting a recorded
  outcome would rewrite history (`I3`).
- `mean_cost` is stored as sum + count and exposed as their quotient. A stored running mean would make the persisted
  value depend on arrival order, so two identical histories could disagree.

**Evidence.** `tests/test_procedure_ranking.py` (18 property tests: Laplace bounds and monotonicity, unseen-procedure
placement, all four tie-break rules, totality, cost irrelevance). `tests/integration/test_record_outcome_db.py`
(16 tests through `Engine.call`: the three error cases, the persisted counters, replay counting twice, the order
changing and being stable, the non-promotion of a retired procedure, channel invariance per item, and the
coding-profile recipe case). Control: removing the ordering term from `read_handlers` fails exactly the two ordering
tests and nothing else.

**Status:** decided and implemented under the Project Owner's delegation (2026-11-12).

---

### V2-D76 — The temporal predicate runs in candidate generation, and usage ordering runs in assembly: §9's diagram is corrected, not the code (E-031-F1; amends the §9 diagram, keeps `V2-D11`'s five stages)

**Context.** `ARCHITECTURE_V2.md` §9's flowchart named **"temporal"** as a candidate-generation channel **and**
**"temporal predicate"** under stage 4 (filter & re-rank), and **"recency/usage"** under stage 4 as well. The shipped
code puts the temporal clauses in stage 2's statement (`engine/predicates.py::fetch_scoped_rows`) and applies the
procedure order after routing (`V2-D75` decision 3, stage 5). `E-031` (#200) raised this as `E-031-F1` rather than pick
a side silently, because a diagram and a plan that disagree would let `E-036` (#206) measure a stage boundary that does
not exist.

**Which artifact is authoritative.** The **guarantee**, not the picture. Three records constrain the answer and none is
negotiable:

1. `V2-D11` fixes the five stages and nothing about where inside them a predicate is applied.
2. `V2-D73` decision 3 makes the temporal predicate and the caller's read predicate **one statement**, because applying
   the read set afterwards in Python would make `limit` count rows the caller cannot read. The same argument applies to
   the temporal predicate for the same reason: filtering after `LIMIT` returns fewer rows than exist.
3. `V2-D75` decision 3 puts the outcome ordering **after** routing, which is stage 5.

So the code implements the approved guarantees, and the §9 diagram is the artifact that is wrong. That is also the only
correction that does not weaken anything: moving the predicate into stage 4 would break `V2-D73`, and moving the order
into stage 4 would break `V2-D75` and `I1'`.

**Decision.**

1. **The temporal predicate is a stage-2 filter.** It is applied in the same SQL statement as the read predicate and the
   typed-artifact guard, before `LIMIT`. §9's diagram no longer names it under stage 4.
2. **Usage/recency ordering is stage 5.** It orders items **within** a channel after `channel_of` has decided
   membership, so no statistic can move an artifact between channels.
3. **What stage 4 still does**: the `V2-D45` defence-in-depth `row_visible` re-check, and (`E-030`) the procedure
   applicability filter — both of which only *remove* or *verify*, never rank.
4. **`E-036`'s stage list is unchanged**: `plan`, `candidate_generation`, `expansion`, `filter_rerank`, `assemble`
   (`engine/planning.py::STAGES`). Only which stage a stage-2 filter runs in is corrected.

**Consequences, recorded rather than hidden.**
- `E-031-F1` is closed by this record plus the §9 amendment. The code is unchanged: no test moved, no behaviour moved.
- A reader of §9 alone would previously have expected two temporal applications. There is exactly one, and it runs
  first; a second application would be dead work, since the statement has already excluded what the predicate rejects.
- `E-032` (expansion) must apply the same predicate to rows it reaches, in its own traversal, for the reason in
  `V2-D73`: an expanded row that the caller's temporal mode excludes must never be returned. §9's stage 3 is where that
  check lands, and this record is what makes "the predicate is a filter applied at the earliest stage that sees a row"
  a rule rather than a placement coincidence.

**Evidence.** `tests/test_planning.py::test_the_stages_are_named_for_e036` (the stage list is one declaration);
`tests/integration/test_planning_db.py` (all four modes execute through the plan path, with the stage boundaries entered
in order); `tests/integration/test_planning_db.py::test_the_temporal_predicate_cannot_move_a_row_between_channels`;
`tests/test_planning.py::test_the_architecture_diagram_agrees_with_the_planned_stages` (a static check run by CI: §9's
diagram draws exactly the five planned stages in order, attributes the temporal predicate to stage 2 and not stage 4,
and places usage ordering in stage 5). Control: restoring "temporal predicate" to the stage-4 node fails it.

**Status:** decided under the Project Owner's delegation (2026-11-17). Amends the §9 diagram only.

---

### V2-D77 — A procedure's `preconditions` and `steps` live in their own side table, `procedure_shape`, not on `outcome_stats` (E-030-F1; supersedes the storage note in `V2-D75`'s consequences)

**Context.** `V2-D75`'s consequences and migration 20's header both said `E-030` would add `preconditions` and `steps`
as **columns on `outcome_stats`**. `E-030` (#201) found that home wrong once both fields were real, and raised it as
`E-030-F1`. Migration 21 shipped a third side table. Until now no decision record said the earlier note was
superseded, so `V2-D75`, migration 20's header, `ROADMAP_V2.md`'s `E-030` row and `schema/README.md` all still described
a design that does not exist.

**Decision.** `preconditions` and `steps` live in **`procedure_shape`** (migration 21), one row per procedure:
append-only (every `UPDATE` refused), procedure-only in the writer's tenant, composite tenant FK
`(version_id, user_id) → memory_version ON DELETE CASCADE`, forced RLS, runtime grant `SELECT, INSERT`, the precondition
grammar enforced in SQL by `guard_procedure_shape` and mirrored by `engine/procedure_shape.py`.

**Why not `outcome_stats`.** Three reasons, each sufficient:
1. **Two mutability rules in one row.** `outcome_stats` counters move on every `record_outcome`; a shape is asserted
   content, written once. Migration 20 chose a side table precisely to keep different mutability rules apart.
2. **Two lifetimes.** An `outcome_stats` row is created lazily by the first `record_outcome`. A shape exists from the
   moment the procedure is asserted. Putting it there would make `assert` create the counters row, changing
   `record_outcome`'s first-insert path, which `E-030` W6 forbids.
3. **No gain.** Nothing reads the two together: matching reads the shape before routing, ranking reads the counters
   after it.

**Consequences, recorded rather than hidden.**
- `V2-D75`'s sentence "`E-030` ... adds `preconditions`/`steps` as columns on that table" is **superseded** by this
  record; `V2-D75`'s ranking decision is unaffected.
- Migration 20's header carries the old note and **cannot be edited**: migration sources are checksum-bound
  (`schema/migrate.py`, `MA-016` X1), so editing it would be reported as drift on every deployed database. The
  correction lives here and in `schema/README.md`'s migration-20 entry, which now points to this record.
- Rollback of migration 21 drops every stored shape (`E-030-F3`); the procedures remain and read as "no preconditions",
  i.e. always applicable. That is now asserted by a test with data, not only stated in the rollback header.

**Evidence.** `schema/021_procedure_shape.sql`; `tests/integration/test_migration_m21.py` (guards, the 48-entry
Python/SQL agreement corpus, vocabulary agreement, the fingerprint-exact rollback, and the rollback-with-data test);
`tests/integration/test_procedure_shape_db.py`.

**Status:** decided under the Project Owner's delegation (2026-11-17). Supersedes one storage sentence of `V2-D75`.

---

### V2-D78 — `query` expands over associations by default; the candidate `limit` bounds seeds, not the response (E-032)

**Context.** `E-031` recorded `expansion="none"` in every plan and traversed nothing, because `E-032` owned
traversal. `E-032` (#202) implements the stage. Its planner now records `expansion="association"` for every intent,
so **every `query` traverses** — a change in the default behaviour of a public operation, not only a new capability
behind a flag. `API_V2.md` §4.2 was updated to describe it; until now no decision record said the default changed.

**Decision.** Expansion runs on the answer and audit routes whenever the plan records it, and the planner records it
for every intent. A recorded plan carrying `expansion="none"` still replays with expansion disabled, so plans written
before this record keep their behaviour. The default hop budget is **1**.

**Why the limit does not bound the response.** `limit` bounds *candidate generation* — the rows the temporal and read
predicates admit before `ORDER BY … LIMIT`. Expansion then adds reached rows that are themselves independently
eligible. Capping the response at `limit` would either drop reached rows the caller is entitled to read or force
expansion to re-rank against candidates it cannot see, which is `E-033`'s job (`V2-D28`: expansion returns rows;
channel membership and budgeting belong to the assembler).

**Consequences, recorded rather than hidden.**
- A response can exceed `limit`. The bound is `limit` seeds plus their reached rows: one hop is capped by migration
  3's degree cap (32 incident edges per endpoint), so a one-hop response is bounded by `32 × limit`. At `MAX_LIMIT`
  (1000) that is up to 32,000 rows for one request, with no `resource_limit` raised. `API_V2.md` §4.2 calls
  `resource_limit` the code for "a declared bound was exceeded … never a silent truncation", and a page-size
  overrun is one. This is logged as **`E-032-F2`**; the bound's shape and number belong with the cost work that
  measures it (`E-036`), not with a guess here.
- The hop budget is **not** in the plan record. `expand_associations` takes it as an argument defaulting to
  `DEFAULT_HOP_BUDGET`, and the executor does not pass one, so a plan records that expansion happens but not how far.
  A replay under a different default would traverse differently while reading as the same plan. Recorded as
  **`E-032-F3`**; adding the field changes `Plan`'s frozen shape and belongs with `E-031`'s record, not this wave.
- Audit applies the requested belief time to the **artifact** rows and uses the current stored association topology:
  `association` carries no temporal range. An audit therefore shows what was reachable *now* among what was believed
  *then*. Documented in `API_V2.md`; historical edge reconstruction is not claimed.

**Evidence.** `engine/expansion.py`; `api/read_handlers.py::execute_plan`'s `expansion` stage;
`tests/integration/test_association_expansion_db.py` (17 tests, including the three controls and the legacy-plan
replay); `tests/test_planning.py::test_a_legacy_plan_replays_without_expansion`.

**Status:** decided under the Project Owner's delegation (2026-11-18). Records a default-behaviour change `E-032`
shipped without a record.

## V2-D79 — Expansion is bounded by the page size, and the plan records its depth

**Context.** `V2-D78` recorded two consequences of `E-032` as findings rather than fixes: `E-032-F2` (a response was
bounded only by the degree cap times the page size — up to `32 × limit`, i.e. 32,000 rows at `MAX_LIMIT`, with no
`resource_limit`) and `E-032-F3` (the plan recorded that expansion happens but not how far, so a replay under a
different default traversed differently while reading as the same plan). Both are closed here at the root.

**Decision 1 — `E-032-F2`: the response is bounded by one page.** `expand_associations` takes `row_budget`, and the
executor passes the plan's own `rows`. A response is therefore at most **twice `limit`**: the candidate page, plus at
most one page of reached rows. The parameter has **no default** — a forgotten budget is precisely the unbounded
response the finding describes, so the type system refuses to let a caller forget it.

**Why a page and not an error.** `API_V2.md` §4.2 calls `resource_limit` the code for a declared bound exceeded. A
page-size overrun is not that: the caller asked for a page and gets a page. Raising `resource_limit` for an ordinary
fan-out would turn a normal query into a failure, and the finding's own §10 row is about *documented* truncation, not
refusal. Truncation therefore follows the order expansion already guarantees — nearest hop first, then artifact id —
so a truncated page is a **deterministic prefix**, never an arbitrary subset. `V2-D78`'s warning that capping at
`limit` would "drop reached rows the caller is entitled to read" is honoured in the sense that matters: the caller
still receives a full additional page, and `E-033` remains the place where per-channel budgets and coverage are
decided.

**Decision 2 — `E-032-F3`: the plan records its hop budget.** `Plan` carries `hop_budget: int`, and the executor
passes `plan.hop_budget` rather than letting the callee default it.
- `expansion="none"` requires `hop_budget == 0`; `expansion="association"` requires `hop_budget >= 1`. A plan that
  expands at zero depth, or declines to expand at a positive depth, is **unrepresentable** rather than merely unused.
- `to_dict` emits the field only for an expanding plan: `"none"` already says it traverses nothing, so a pre-`E-032`
  record stays byte-identical and keeps replaying.
- `from_dict` **refuses** an expanding record with no budget. Filling it in from the current default is exactly the
  defect; a record that does not say how far it went cannot be replayed faithfully.
- `engine.planning.DEFAULT_HOP_BUDGET` restates `engine.expansion`'s constant, because `planning` must not import the
  connection layer (it does no I/O). A test pins the two together so they cannot drift silently.

**Consequences.**
- `Plan`'s record shape changes, which `E-031` owns. This record amends it rather than deferring: a frozen record that
  omits a value the executor takes from a mutable default is not replayable, and replayability is the property `E-031`
  criterion 6 claims.
- A caller who wants a wider reached page cannot ask for one yet. A `reached_limit` input is a contract change and
  belongs with `E-033`'s per-channel budgets, which is where a caller-visible page size would be designed.

**Decision 3 — `E-038-F2`: a dissent's target is evidence.** `negative_constraint.dissented_version_id` was the one
non-cascading reference to `memory_version` left out of `purge.EVIDENCE_REFERENCES`, excused on the premise that "an
episode is never inferred". That premise is false — `axes.LEGAL_LIFECYCLE` permits `episode/inferred` and
`SHORT_RETENTION_KINDS` permits a working episode — so a dissent can name exactly a row a purge deletes. The column is
`ON DELETE SET NULL`, so the purge did not fail: it silently cleared the record of *what* was rejected while keeping
the rejection. It is now retained as evidence, like a lift's evidence, and the classification test compares the
consulted set to the schema's own foreign keys with no hand-written exception.

**Evidence.** `engine/expansion.py` (`row_budget`, no default); `engine/planning.py` (`hop_budget`, validation, replay);
`api/read_handlers.py::execute_plan`; `engine/purge.py::EVIDENCE_REFERENCES`;
`tests/integration/test_association_expansion_db.py` (`test_a_dense_seed_cannot_make_one_response_unbounded`,
`test_a_truncated_page_is_the_nearest_hop_prefix_of_the_order`, `test_query_bounded_the_way_the_plan_says`,
`test_the_executor_traverses_the_depth_the_plan_recorded`, `test_the_row_budget_is_required_not_defaulted`);
`tests/test_planning.py` (`test_an_expanding_record_without_a_budget_is_refused_rather_than_replayed`,
`test_a_contradictory_expansion_and_budget_is_unrepresentable`, `test_the_two_hop_budget_defaults_are_one_value`);
`tests/test_purge_rule.py` and `tests/integration/test_purge_working_db.py`
(`test_a_dissented_working_episode_is_retained_so_the_record_of_what_was_rejected_survives`, whose control — removing
the citation — reports `assert [(None,)] == [('dissented-ep',)]`).

**Status:** decided under the Project Owner's delegation (2026-11-18). Closes `E-032-F2`, `E-032-F3` and `E-038-F2`.

## V2-D80 — A conflict's counterparts are loaded from its own edges, never inferred from the candidate page

**Context.** `E-033` (#218) routed `query` through the pure `assemble`, which renders an `open_contradiction` as a
`ConflictItem` and refuses one with no counterpart (`conflict_counterpart_missing`). The read path built each view's
counterparts from **the rows the query had retrieved**. Two defects followed, both reproduced against the PR head:

1. **Membership depended on the query.** A claim in `/u/alice/p/one/` contradicted by a claim in `/p/two/` rendered
   in `conflicts` for an unscoped query, but a `scope=/p/one/` query *refused* it, so it appeared in no channel.
   `CHANNELS.md` §1 makes membership a pure function of five inputs, "not on … caller, time of day **or the query**";
   `open_contradiction` is one of them and is derived from the edge (§2.2), not from the page.
2. **Contradictions were merged.** Every contradicted row was offered as a side of every other, so independent pairs
   `a1 ↔ b1` and `a2 ↔ b2` rendered `a1` against `a2` and `b2`. R5: a `conflicts` item shows both sides and **never
   merges them**.

The PR's test edits asserted defect 1 as the expected behaviour (`(w.claim, "conflict_counterpart_missing") in
repo_view.refused`). That assertion was removed: it encoded the regression, not the contract.

**Decision.**
- The read path maps each contradicted artifact to **its own** counterparts, from its own `contradicts` edges
  (`ReadHandlers._contradictions`), and **loads** each counterpart whether or not it was retrieved
  (`_conflict_counterparts`).
- A counterpart the caller may read renders with id, lifecycle and text. One it may not read renders as R6 /
  `V2-D37` render an unreadable source: a per-caller **opaque handle** and **no text**. The lifecycle is shown,
  because `C-P4` already restricts an open contradiction to grounded endpoints, so it discloses nothing the
  contradiction's existence does not. Visible sides are ordered by id and precede opaque ones ordered by handle,
  so ordering never encodes an unreadable id.
- `get` renders the same sides as `query`. R5 applies to a `conflicts` item wherever it is rendered.

**What was *not* changed, and why.** `assemble`'s refusal of a view with `open_contradiction` and no counterpart
stays, as does `test_redteam_a_contradiction_without_a_counterpart_is_refused`. For a pure function such a view is
malformed input: R5 cannot be met, and rendering one side would silently pick a winner. Refusing it keeps it
accounted for under the `no_loss` invariant (rendered once, refused, or excluded). The defect was that the engine
produced such views. It no longer does, and a test pins that boundary from the engine's side.

**This settles `E-032-F1`.** An artifact whose contradicting evidence lies outside the request's read set stays in
`conflicts`: the fail-closed direction (`C-P3`), and the one §1 requires. The caller learns *that* the artifact is
contradicted, never *by what*. The option of classifying only within the read set would render such an artifact as a
fact and would need §1 amended first, which this record declines.

**Consequences.** Counterparts are loaded in one extra statement per query that has any contradiction, bounded by the
association degree cap. As with `V2-D78`, a counterpart is shown at its current state: `association` carries no
temporal range.

**Evidence.** `api/read_handlers.py` (`_contradictions`, `_conflict_counterparts`, `get`, `execute_plan`);
`tests/integration/test_conflict_counterparts_db.py` (`test_each_conflict_shows_only_its_own_counterpart`,
`test_a_readable_counterpart_outside_the_page_is_loaded_not_required`,
`test_membership_does_not_depend_on_the_query_scope`, `test_get_shows_both_sides_exactly_as_query_does`,
`test_an_opaque_counterpart_leaks_no_identifier_or_content`,
`test_the_engine_never_marks_an_open_contradiction_it_cannot_show_both_sides_of`). Controls: reverting to
retrieved-row counterparts fails three tests, and union pairing fails three.
`tests/integration/test_purge_working_db.py::test_an_episode_that_opens_a_contradiction_is_retained_and_the_claim_stays_in_conflicts`
again asserts the claim in `conflicts` for the scoped query, now with both sides (the contradicting episode opaque).

**Status:** decided under the Project Owner's delegation (2026-11-18). Closes `E-032-F1`.

## V2-D81 — `change` diffs two `historical_as_of` views under ρ, keyed by the identity a belief keeps

**Context.** `API_V2.md` §4.2 has listed `mode='change'` since `E-031`, and `query` refused it with
`validation_error/mode_change_not_wired`. `E-033` owns the mode but could not implement it, because no decision record
said *which two views* it compares, *what row key* it matches them on, or *what outcomes* it reports. `E-033-F1` (#73)
recorded that gap rather than letting the mode land with an invented contract.

**Decision — the two views.** `TECHNICAL_SPECIFICATION.md` §3.5 already groups change queries with the **window** form
of the historical predicate, so `change` takes `window_start` and `window_end` (the names and the refusal codes the
window form already uses: `window_start_required`, `window_end_required`,
`historical_window_must_be_nonempty`, `historical_window_after_reference`). It evaluates two of them:

- *previous* = `historical_as_of(user, window_start, ρ)`, *current* = `historical_as_of(user, window_end, ρ)`.

Both are `status='active'` under ρ with the read set applied in SQL, so both are answer-context material. `change` is
therefore **not** a route to `audit_as_of` (`V2-D73`): a retired or archived version cannot enter either view, and no
channel receives one.

**Decision — the key.** A row is matched on the identity a **belief** keeps across its versions, which is *not*
`memory_key`: the API mints a fresh `memory_key` per assertion (`ingest_handlers` calls `new_id("ep")`), so keying on it
would report every re-assertion as one removal plus one addition.

- **single-valued** predicate → `(subject_key, predicate_key)`. Supersession (`R1`/`R2`) closes the old version and
  opens the new one under exactly that pair.
- **multi-valued** predicate → `memory_key`, which `R7` preserves across the close-then-insert that ends one value.

**Decision — the outcomes.** Per key: `added` (only in *current*), `removed` (only in *previous*), `changed` (in both,
different version sets). A key whose version set is identical in both views is unchanged and is not reported — the diff
names what moved, and a caller reads what it *is* from the channels, which are assembled from the union of the two
views. `changes` therefore carries ids, never content: a second copy of the text in the diff would be a second place
for the budget and the redaction rules to disagree.

**Decision — no expansion.** A row reached by following an association is not a belief the window changed; it is a
neighbour of one, and expanding would make "removed" mean "no longer reachable from a different candidate set" rather
than "no longer believed". `change` therefore records `expansion='none'`, `hop_budget=0`, and `Plan` refuses a recorded
`change` plan that expands.

**Decision — a truncated view is refused, not diffed.** Each view is fetched one row past the plan's limit. If either
exceeds it, `query` raises `resource_limit/change_view_exceeds_limit` rather than returning a diff computed from a
truncated view: truncation makes a key look `removed` (or `added`) when it was merely not fetched, and a diff that
silently means "changed beyond the page" is worse than a bounded refusal.

**Consequences.** `change` is one statement per view, both inside the same transaction, so both see one ρ (`I4`). The
channels are built exactly as a single-view query builds them — same assembler, same budgets, same coverage, same
provenance — so a changed artifact is rendered identically whether it is asked for by `change` or by
`historical_as_of`. `QueryResult.changes` is empty for every other mode.

**Evidence.** `API_V2.md` §4.2 (`QueryResult.changes`, `Change`, the `change` mode bullet and its
`resource_limit`); `engine/change.py` (`change_key`, `diff_views`); `api/read_handlers.py` (`_change_queries_for`,
`execute_plan`'s `CHANGE_MODE` branch); `engine/planning.py` (`MODES`, `PlanMode`, the no-expansion invariant);
`engine/predicates.py` (`ScopedRow` identity columns, `fetch_scoped_rows(with_identity=True)`).
`tests/test_change_diff.py` pins the key and the three outcomes against both predicates and a multi-valued change;
`tests/integration/test_change_mode_db.py` drives `Engine.call(…, "query", {"mode": "change", …})` over a
supersession, a close-then-insert and a multi-valued end, and asserts each appears once with the right outcome in the
right channel. Controls (run on this branch): keying on `memory_key` throughout turns the single-valued cases into an
add plus a remove (3 tests fail, all in `tests/test_change_diff.py`); diffing a truncated view answers instead of
refusing (`test_a_truncated_view_is_refused_not_diffed` fails); letting the planner build an expanding `change` plan is
refused by `Plan`'s own invariant (11 fail); and removing that invariant alone lets a recorded expanding `change` plan
replay (`test_a_change_plan_never_expands_and_a_record_that_does_is_refused` fails).

**Status:** decided under the Project Owner's delegation (2026-11-19). Closes `E-033-F1`.

## V2-D82 — a close-then-insert re-issues a belief where it was

**Context.** Found while wiring `change` (`V2-D81`). `R1` makes every revision of a belief a new row: supersession
re-issues the old value under a closed validity range, boundary revision re-issues both sides, conflict resolution
re-issues the value an accepted candidate covers, and ending a multi-valued fact re-issues the row with an upper bound.
None of those paths wrote `scope_path`, `visibility`, `kind`, `method_id` or `subject_entity_id`, so the axes trigger
(`fill_scope_path()`, migration 11) placed every re-issued row at the user's **personal root** at `private`. A belief
stored at `/u/alice/p/chrona/` left that scope the first time it was superseded: it vanished for every reader holding
only the project scope, and a scoped `change` query reported it `removed` when it had only been revised.

**Decision.** A re-issued row is the **same belief**, so it carries the placement of the row it re-issues, read back
from the database under the writer's own lock (`transactions.py::_placement_of`). A **new** value that supersedes a
belief (`_successor_placement`) goes where the candidate names; a candidate that names no scope (every v1 writer, the
extraction pipeline) replaces the belief in place, at its scope and visibility. A candidate that names the prior's own
scope keeps its visibility too; one that names **another** scope goes there at the column default (`private`), because
sharing is never inherited across scopes. `kind`, `method_id` and `subject_entity_id` describe the new value, so they
come from the candidate and the trigger derives what it leaves unset. A first version with nothing to supersede now
honours the candidate's placement as well.

**Consequences.** No schema change and no migration: the columns already existed and the triggers still derive
whatever a writer leaves unset. Rows written before this fix keep the root placement they were given; nothing
rewrites history (`I10`).

**Evidence.** `engine/transactions.py` (`_placement_of`, `_successor_placement`, and every close-then-insert in
`supersede_single`, `revise_transition_boundary`, `resolve_conflict_pending`, `end_multivalued`).
`tests/integration/test_version_placement_db.py` (6 tests) asserts each copy against the original read back from the
database. Control: with the placement carry removed, all 6 fail, and so does
`test_change_reports_each_outcome_once_and_names_the_versions` (the superseded predicate leaves the scoped view).

**Status:** decided under the Project Owner's delegation (2026-11-19). Recorded on #73 as `E-033-F3`.

## V2-D83 — `refine` drills into a result handle under the recorded read set, with a total rule-based vocabulary

**Context.** `API_V2.md` §4.2 specified `refine(result_id, instruction) → QueryResult` ("drill-down on a prior result
within the same read set; cannot widen it") but nothing stored a result: `query` returned a `QueryResult` and the
value was gone, so `result_id` had no referent. Three questions had to be answered before code: what a handle is,
what "the same read set" means when the caller's read set is resolved per call, and what an instruction is allowed
to mean (`E-034`, #204).

**Decision — the handle is a capability, not a row.** `query` issues `res_` + 128 random bits into a bounded
(`MAX_RESULTS = 1024`), time-limited (15 minutes) map owned by the engine and shared by the read handlers. It is not
persisted, because a result is answer-context material and not a memory: there is nothing to purge and nothing that
`export`/`delete` should reach. A restart, a different worker, another principal or an expired entry are therefore
**one failed lookup** and one answer, `forbidden`, indistinguishably (`THREAT_MODEL.md` T-11). The map records the
principal, the **read set of the issuing call**, the planner intent (so a refine re-enters `E-031`'s planner, never
the assembler), the **channel budgets the answer used** (a refine carries no `request`, so defaulting there would
re-assemble to a different answer than the one being drilled into), the candidate ids the result named, and the
chain depth.

**Decision — contain the read set, do not re-resolve it.** `CallerContext.read_set` is derived per call from live
grants, so "the same read set" cannot mean equality of two independently resolved sets. A refine is authorised only
while the current credential still **covers** every entry the issuing call held (`scope.access.covers`); a narrowed
credential is `forbidden` rather than silently redirected, and a widened one passes but executes under the
**recorded** set. That makes "cannot widen" structural: association expansion is cut (`expansion='none'`,
`hop_budget=0`), the statement fetches only the result's candidate ids re-read from the database, and provenance
renders against the same set, so a foreign source stays opaque. A row purged or retired since the query is absent
from the re-read, never resurrected.

**Decision — one operator per instruction, matched exhaustively, never a model.** `artifact <id>`,
`channel <facts|hypotheses|procedures|conflicts>`, `mention <text>` (case-insensitive substring) and
`scope <path>` (canonical path, at or inside). `channel` is answered by `channel_of` on the same tuple assembly
uses, so narrowing can never move an artifact between channels. The filter runs **before** assembly, so coverage and
cost describe the narrowed candidates and a kept abstraction keeps its label, support summary and bound (`I14`).
A narrowed `change` result keeps each change that touches a kept row whole, with its original outcome.
Anything else — absent, blank, unknown operator, missing argument, a multi-word argument to any operator but
`mention`, unknown channel, malformed path — is
`validation_error` with a static detail (`instruction_required` / `instruction_unrecognised`), never a silent no-op
returning the unmodified result. A chain is bounded at `MAX_REFINE_DEPTH = 4`; the fifth answers
`validation_error/refine_depth_exceeded`.

**Decision — no second `scope_read` for a refine.** `ARCHITECTURE_V2.md` §5 rule 6 records a *read that reached*
outside the caller's namespace. The issuing `query` already recorded it, and a refine reaches no scope that read set
does not already name; recording it again would multiply audit rows without adding a locator.

**Consequences.** `Plan` is unchanged: a refine is `refine_plan(prior, candidates=n)`, a pure narrowing of the
recorded plan that still replays. No schema change and no migration. A deployment with several workers must route a
refine to the worker that answered the query (sticky routing) or accept the fail-closed `forbidden`; both are safe,
and neither widens.

**Evidence.** `api/refine.py` (store, interpreter), `engine/planning.py::refine_plan`,
`api/read_handlers.py::ReadHandlers.refine`, `api/entry.py` (`_result_id`, `RESULT_TARGETS`,
`Engine.resolve_result`). `tests/test_refine.py` (47 tests) covers the store, the vocabulary, `covers` and the plan.
`tests/integration/test_refine_db.py` (13 tests) drives every acceptance row through `Engine.call`. Controls: allowing
a widened read set to execute fails `test_a_widened_credential_does_not_widen_the_drill_down`; returning the
unmodified result for an unrecognised instruction fails
`test_an_unrecognised_instruction_is_a_static_error_never_the_unmodified_result`.

**Status:** decided under the Project Owner's delegation (2026-11-19). Recorded on #204 as `E-034`.

## V2-D84 — `D-16`'s scenario-level sample size survives the four cognition channels, and Suite `D` records which channels it measured

**Context.** W3's tracker (#73 §6) makes revisiting `D-16` (`N_D`) a condition of the wave: "revisit if abstraction
channels change the per-scenario leak probability". `E-037` extended the deletion scan from seven channels to eleven
(`ARCHITECTURE_V2.md` §10.4), and the pre-closure audit made Suite `D` able to measure all eleven
(`evaluation/harness/deletion_suite.py`, `--channels 11`). Two questions follow: does the frozen sample size still
hold, and does a recorded report say what it measured?

**Decision 1 — `N_D` is unchanged, because the bound is on the scenario, not the channel.** `verdict` and the suite
score a scenario as leaking when **any** targeted channel reports a residual, so the statistic the Clopper–Pearson
bound is computed over is the per-scenario leak indicator. Adding channels can only *raise* that indicator's
probability, so a zero over eleven channels is strictly stronger evidence than a zero over seven at the same `N`;
the number of scenarios needed for a one-sided 97.5% bound at `k = 0` does not depend on how many channels were
scanned. `pilot_rules.py`'s `u* = 0.10` therefore still requires `N_D >= 36` zero-leak scenarios, and the frozen
split that meets it is unchanged: Suite `D` `test` has `N = 50` (`cp_upper_97_5 = 0.071122`). Reading that split is
forbidden before gate `G-TS` (`V2-D42`), so **G3's evidence is the mechanism check on the frozen `dev` split**
(`N = 20`, `0.168433` — the same basis `G2` was signed off on), and the confirmatory number stays a measurement-phase
obligation, never a W3 claim.

**Decision 2 — a report names its channel set, and channel 9's rendered half is rendered.** `SuiteReport.to_json`
emits `channels`, so a reader can tell a seven-channel run from an eleven-channel one without counting keys; a
targeted channel that could not be measured is still counted in `incomplete_measurements` and never as a zero. The
harness's channel-9 probe now reads the **rendered** answer context (`_render`, abstractions only) instead of
re-reading `memory_version.kind = 'abstraction'`: `scan_deleted_key(..., connection=...)` already supplies the
storage half (`abstraction_db_probe`), so counting rows in both halves doubled every residual. The seventh-channel
default is unchanged, so every recorded v1 report keeps its meaning and its numbers.

**Consequences.**
- The recorded G3 runs are `reports/deletion/suite_d_dev_11ch_P.json` (0 leaking of 20, `incomplete_measurements = 0`,
  all eleven channels at 0) and `suite_d_dev_11ch_A8.json` (20 of 20 leaking, with residuals on
  `channel_8_hypothesis_rendering` beside the v1 channels). Channels 9–11 record zero under this ablation because no
  Suite `D` scenario carries an abstraction, verification record or export snapshot for the canary; their controls
  live beside the probes (`test_eleven_channel_scan_clean_on_v2_deletion_and_a8_leaks_through_abstraction` proves
  `A8` leaks on channel 9 through a verified abstraction; channel 11 is proven by the stale-epoch export refusal,
  `test_export_snapshot_taken_before_deletion_is_not_served_after`).
- The frozen seven-channel reports were regenerated with the frozen model and differ **only** by the added
  `channels` field, which is what makes the regeneration checkable rather than merely asserted.
- A leak count on channel 9 is now the rendered surface plus storage, each counted once. No `N_D`, `u*` or pilot
  parameter changes, and no frozen split was executed.

**Evidence.** `evaluation/harness/deletion_suite.py` (`channels`, `_render`, `abstraction_rendered_surface`,
`--channels`); `deletion/scanner.py` (`_supplemented`, `ALL_CHANNELS`); `tests/integration/test_deletion_suite_db.py`
(`test_eleven_channel_suite_d_under_p_is_clean_and_a8_is_detected`, whose `A8` assertion requires a residual on
channel 8 — asserting only `leaking_scenarios` would pass with every cognition channel dead, because channels 1–7
already leak under that ablation); `reports/deletion/suite_d_dev_11ch_{P,A8}.json`; `scripts/check_statistics.py`,
`scripts/check_test_split_gate.py`.

**Status:** decided under the Project Owner's delegation (2026-11-20). Closes W3's `D-16` revisit obligation
(#73 §6) and records the eleven-channel Suite `D` evidence G3 cites.

## V2-D85 — `V2-D57`'s review gate is executed by a repository-owned procedure, not by a stock plugin prompt

**Context.** `V2-D57` makes the AI review the project's review gate but says nothing about *how* it runs, so it ran on
the generic `code-review` plugin. Measured on this repository: across 106 pull requests, 107 summary comments — every
one "No issues found" — and four inline comments. The plugin's own instructions explain the result — it stops when "Claude has already commented on
this PR", so only the first push of a pull request was ever reviewed, and its bug agents are told to "focus only on the
diff itself without reading extra context", which excludes exactly the defects `V2-D57`'s own rationale lists (an
audit-key collision, a ledger reverted by merge order, a property test running 315 cases where its issue required
1,000). The defects that mattered on `main` were found by hand afterwards: `#228`'s `SECURITY DEFINER` helper gated on
a forgeable GUC, and `#229`'s `get` assembling its response without `assemble`.

**Decision.** The review procedure is repository-owned and evidence-bearing:

1. **The standard is executable.** `.claude/skills/review-pr/SKILL.md` implements `PLAN/CONTRIBUTING.md` §1.1 as steps;
   `REVIEW.md` holds review-only rules (severity, the six-part evidence bar, the do-not-report list, the nit cap, the
   re-review rule) and `custom-memory-engine/CLAUDE.md` holds the context a reviewer must read (authority order, what is
   actually enforced, the traps). Instructions are a routing layer: none of the three restates `PLAN/V2/`.
2. **The instructions are read from the base branch.** A pull request must not be able to edit the standard it is held
   to. `REVIEW.md` is not among the paths the Claude Code action restores from the base ref, so the skill reads it — and
   the engine's instructions — from the base commit explicitly; the base branch is also the workspace root, and the
   pull request is checked out beside it at `pr-head`.
3. **Every push is reviewed, and a re-review is stricter.** A later commit is reviewed for new regressions and unfixed
   earlier findings only, with its resolved threads resolved rather than declared fixed.
4. **The reviewer cannot change what it reviews.** The review jobs run `--permission-mode default` with an explicit
   allow list and `--disallowedTools` covering editing, pushing, merging, approving and `PUT`; no approval is ever
   fabricated, as `V2-D57` requires. This is enforced by the workflow, not asked of the model.
5. **The instructions cannot rot silently.** `tests/test_claude_integration.py` holds the instruction files to
   `scripts/check_evidence.py` (a dead `path::symbol` fails CI) and pins the workflow's triggers, permissions,
   base-branch checkout and deny list.
6. **One reviewer, not two.** If the managed Code Review service is enabled for this repository, the repository variable
   `CLAUDE_REVIEW_MODE=native` makes the Action job skip before minting a token.

**What this does not change.** `V2-D57`'s policy, branch protection, and the requirement that a review means reading the
code rather than the description. The review is still advisory to the owner and still not a required check; it
complements deterministic verification and never replaces it. A clean review remains not a proof of correctness.

**What it does not do.** A green run does not show that a review finds bugs. Only a planted defect does, so after any
change to the standard the reviewer is exercised on a throwaway pull request that contains one, and the recorded result
is the evidence — not the run's conclusion.

**Evidence.** `.claude/skills/review-pr/SKILL.md`; `REVIEW.md`; `custom-memory-engine/CLAUDE.md`;
`.github/workflows/claude-code-review.yml` and `claude.yml`; `.github/workflows/README.md` ("The Claude review");
`custom-memory-engine/tests/test_claude_integration.py` (25 checks); `actionlint` over both workflows.

**Status:** decided and implemented (2026-11-20), under the Project Owner's instruction to make the review gate
repository-aware. `V2-D57` stands; this records how it runs.

## V2-D86 — `query` ranks the caller's text, and the temporal predicate stays a filter

**Context.** The end-to-end review that opened `W3.5` found the read path *text-blind*: `execute_plan` fetched
`ORDER BY lower(recorded_range) DESC LIMIT n` and expanded one association hop, and **never read `request["text"]`**.
`engine/ingest.py` never wrote `memory_embedding` either, so the v1 dense+lexical ranker existed, was tested, and was
connected to nothing on the V2 path. The Gate G3 latency figure (`11.82 ms` P95) was therefore honest about the code and
meaningless about the product: it measured a recency scan against V1's `32.61 ms` hybrid search, which is not a
comparison.

**Decision.** `query` accepts the request's own `text` (and optionally `embedding` + `embedding_model`), and produces its
candidate set by **hybrid recall** instead of a recency scan:

1. The **filters are unchanged and run first**: the compiled temporal predicate, the typed-artifact guard and the
   caller's read predicate, in one statement, before any `LIMIT` (`V2-D73`). Relevance changes the *order* of authorized
   rows, never their membership.
2. Three **ranking arms** run over that population — dense cosine in the version's embedding slot (HNSW), a
   language-agnostic lexical arm (`content_fts_simple`, `to_tsvector('simple', …)`), and a `pg_trgm` substring arm keyed
   on the longest query token — and are fused by **Reciprocal Rank Fusion** (`k = 60`), which is rank-based and so
   needs no normalisation between cosine distance, `ts_rank_cd` and trigram similarity.
3. The fused ids are composed into the **same** statement's filter (`m.id = ANY(...)`) beside the temporal and read
   predicates, so the fetcher cannot return a row the recall did not authorise even if the two disagreed about what is
   current. Rows come back in fused order; graph expansion (`V2-D76`) then appends reached rows behind them.
4. **Zero generative model calls.** The reranker seam (`hybrid.Reranker`) is a protocol the engine does not implement: a
   cross-encoder is deterministic code over fixed weights, and a deployment injects one. That is what keeps the hot read
   path model-free, replayable and at tens of milliseconds.
5. **A relevance query that matches nothing answers nothing.** There is no fallback to recency: a page of unrelated
   memories presented as an answer to a question is a wrong answer that looks like a right one.

`Plan` records `recall` and a **digest** of the question, never the question: a plan is a value a recorder may hold, and
a query is caller content (`RL-072`). A hybrid plan replayed with a different text is refused
(`validation_error/plan_query_mismatch`) rather than silently ranked by it. `change` and `audit_as_of` are refused a
relevance question outright (`text_unsupported_for_mode`): a diff must report every authorized row and an audit must
report a past belief unchanged, so silently ignoring the question would be a lie about what ran.

**What this supersedes and what it does not.** §9.3's non-negotiable stands: the temporal predicate is a filter, never a
score term — which is why the arms rank a population the predicate already decided. `V2-D75`'s rule that ranking follows
channel routing stands: relevance orders candidates, and `channel_of` alone decides where a row is presented.
`V2-D11`'s staged retrieval stands, and the five measured stages are unchanged (`E-036`), so cost attribution does not
move.

**Exclusion, recorded honestly.** `memory_embedding` is now written by the ingest path (`attach_embedding`, in the slot
the vector's width belongs to). Until a deployment supplies an embedder, a text query uses the two lexical arms and the
gates' `G3` latency figure is re-baselined against *that* — see `ROADMAP_V2.md` §6.1.

**Evidence.** `engine/hybrid.py` (`hybrid_recall`, `fuse`, `vector_literal`, `trigram_token`, `rerank`);
`engine/planning.py` (`Intent.text`, `Plan.recall`, `Plan.query_digest`, `query_digest`);
`api/read_handlers.py` (`_recall_query_for`, `_recall`, `execute_plan` candidate generation);
`tests/test_hybrid.py` (22 unit checks); `tests/integration/test_hybrid_recall_db.py` (11 checks: relevance over recency,
Arabic and CJK queries, mixed-script, dense paraphrase, refusals, RLS, refine containment).

**Status:** decided and implemented (2026-11-21) under the Project Owner's instruction to implement the review's findings.

## V2-D87 — Structure stays closed, domain vocabulary becomes open, and third-party evidence is quarantined

**Context.** Three defects shared one root cause: the engine treated *structural* decisions and *domain* vocabulary as
the same kind of thing.

* `memory_version.predicate_key` is a foreign key into `predicate_schema`, so every claim needed a pre-registered
  predicate. V2 `assert` therefore wrote `predicate_key = 'statement'` for everything and `single_valued = False`, which
  silently disabled the whole conflict pipeline: `apply_candidate` only consults `existing_relation` when
  `candidate.single_valued` is true, and `'statement'` is multi-valued by design. Supersession, contradiction detection
  and the exclusion constraint were dormant on the public API.
* `um_facet` — the functional tag of the evolving user model — was read by the user-model view and **written by
  nothing**.
* Any evidence that was not a chat turn was refused (`external_evidence_cannot_be_grounded`), so a coding agent, a
  research agent or a user pasting a document could not record what it said, despite migrations 8/16/17 having built the
  columns for exactly that.

**Decision.** The closed/open split is made explicit and the write path is repaired:

1. **Closed, and enforced in the schema**: `kind`, `lifecycle`, `verification`, `visibility`, the six `um_facet`
   values, `assertion_mode`, `cardinality`. These are epistemic and structural; a model may not invent them.
2. **Open, and not a catalog**: `memory_version.semantic_slot` (migration 24) is a soft grouping key with **no foreign
   key**, normalized once by `semantic.slots.normalize_slot` and re-checked by a `CHECK` (trimmed, 1–120 characters, no
   control characters). A domain nobody anticipated needs no migration to be sayable. `predicate_key` **stays** a
   foreign key, because the closed structural vocabulary is exactly what a foreign key should protect.
3. **The exclusion key gains the slot**: `no_bitemporal_overlap` compares `COALESCE(semantic_slot, predicate_key)`
   instead of `predicate_key`. For every row that names no slot this is the version-15 key unchanged, so all existing
   data keeps its guarantee; it becomes slot-scoped only where a slot was supplied. Without this, two unrelated
   single-valued facts about one person ("residence" and "preferred_editor") collide on the generic predicate and the
   constraint refuses a legal write.
4. **`assert` accepts the four semantic options**: `cardinality` (`single`/`multi`/`event`, default `multi`) decides
   `single_valued`; `semantic_slot`; `um_facet`; `assertion_mode`. Omitting them reproduces the pre-W3.5 multi-valued
   write exactly, so no existing caller's behaviour changes.
5. **Non-turn evidence is accepted and quarantined.** The row stores the declared `source_type`, the reference and the
   caller's `content_hash`. A claim whose evidence is *entirely* non-turn is forced to `assertion_mode = 'third_party'`
   whatever the caller asked for, and `'third_party'` now **holds** rather than rejects: the claim is stored `contested`,
   which the channel router sends to `conflicts`. Presenting retrieved text as fact is what the channel separation
   exists to prevent, and discarding it would destroy evidence a user pasted. The gate's evidence check accepts a
   non-turn citation on its structure (reference, span, well-formed hash, all schema-enforced) and still requires a
   user turn for every **turn** span.
6. **Authority is derived, not declared.** Migration 24 adds
   `memory_evidence.authority_rank` as a **generated** column from `source_type` and `evidence_role`
   (`USER 0 > SYSTEM 1 > ASSISTANT 2 > THIRD_PARTY 3`), the one form a compromised runtime cannot forge. A
   lower-ranked source may not supersede a higher-ranked belief: the attempt is recorded
   (`memory_conflict.reason_code = 'authority_quarantine'`), never silently dropped. Equal rank is allowed, because a
   user correcting their own earlier statement is the commonest supersession there is.

**What this replaces.** `engine.gate._INSTRUCTION_PATTERNS` — twelve hardcoded English substrings — is no longer the
primary injection defence. Quarantine by *source* is structural: it depends on where text came from, not on what it
says, so it cannot be paraphrased around in another language. The phrase list remains as a narrow belt-and-braces check
on user text.

**Evidence.** `schema/024_semantic_slot.sql` and its rollback; `semantic/slots.py`, `semantic/authority.py`;
`api/ingest_handlers.py` (`_assert`, `_choice`); `api/contract.py` (`assert` inputs);
`engine/gate.py` (`evidence_supported`, `classify_assertion`); `engine/transactions.py`
(`CandidateVersionInput.semantic_slot`, `_PLACEMENT_COLUMNS`, slot-aware supersession lookup);
`tests/integration/test_migration_m24.py` (23 checks, including the two-slot coexistence and the refusal of a
non-representable downgrade); `tests/integration/test_api_wiring_ingest.py` (third-party quarantine, slot-scoped
supersession); `tests/test_semantic_authority.py`; `PLAN/V2/API_V2.md` §4.3.

**Status:** decided and implemented (2026-11-21).

## V2-D88 — The LLM owns semantic understanding; deterministic code owns every check on it

**Context.** The review's second finding was a placement error in both directions: the LLM was absent where it is
irreplaceable (reading natural, paraphrased, multilingual turns; classifying modality; resolving "my boss" to a person)
and present, or planned, only where it adds least (rendering prose after a non-LLM counted a pattern). No engine
inspected in the same review got this right: nine of twelve hardcode English NLP, and the two that do not are
single-node embedded stores.

**Decision.** Every capability has exactly one owner, and the boundary is a **typed contract** rather than a prompt:

| capability | owner | mechanism |
|---|---|---|
| atomic claims, entities, facets, modality, relative time, the open slot | **model** | `semantic.contracts.StructuredExtractor` → `ExtractionResult` (typed, closed structural enums, open domain vocabulary) |
| quote exists in the source; its numbers/dates/identifiers appear in the same source | **deterministic** | `semantic.grounding` (`normalize`, `quote_grounded`, `specifics_in`, `ground_claim`, `ground_text`) |
| authority of a source | **deterministic, database-derived** | `semantic.authority` and migration 24's generated column |
| one normalization of a slot key | **deterministic** | `semantic.slots.normalize_slot` + the schema `CHECK` |
| temporal bounds are aware, ordered, anchored, non-empty | **deterministic** | `semantic.temporal` |
| entity resolution | **hybrid cascade** | exact → trigram → vector → injected `EntityAdjudicator` (`semantic.entities`) |
| conflict / supersession relation | **hybrid, 3 stages** | deterministic recall → injected `RelationAdjudicator` → deterministic commit (`semantic.conflict`, `V2-D89`) |
| retrieval ranking | **deterministic + neural embeddings, zero generative calls** | `engine.hybrid` (`V2-D86`) |
| lifecycle, provenance, exclusion, RLS, deletion | **the database** | unchanged since W1–W3 |

**The quote is never translated.** `verbatim_quote` is in the source language, because a translated quote cannot be
compared with anything and grounding would degrade to trusting the model. A claim may be stored in one language and
retrieved by a question in another — that is what the multilingual arms are for (`V2-D86`) — and it can still be
*proved* against what was said.

**No provider enters the engine.** `semantic/contracts.py` imports no SDK and names no model. Extraction, embedding and
adjudication are protocols; the deployment injects implementations. The engine contributes the contract and the checks,
because that is what makes an injected model safe to trust, and the evaluation harness freezes the implementation so a
replay is exact (`RL-032`).

**What this does not do.** It does not make the output of a model *correct*. It makes a wrong output **detectable** —
a fabricated quote fails grounding, a fabricated number fails specifics, a third-party claim cannot overwrite a user's,
and a contradiction cannot be silently resolved. That is the whole of the claim being made.

**Evidence.** `semantic/contracts.py`, `grounding.py`, `authority.py`, `slots.py`, `temporal.py`;
`tests/test_semantic_contracts.py`, `tests/test_semantic_grounding.py`, `tests/test_semantic_slots.py`,
`tests/test_semantic_temporal.py`, `tests/test_semantic_authority.py`.

**Status:** decided and implemented (2026-11-21).

## V2-D89 — Conflict resolution is recall → adjudicate → commit, and the GiST constraint keeps the last word

**Context.** `engine.gate.existing_relation` compared `(subject_key, predicate_key)` exactly and then compared content
for exact lowercased equality. Combined with `V2-D87`'s findings it meant the engine had *no* semantic deduplication and
*no* contradiction detection on the public API: "I moved to Berlin" and "Living in Berlin now" were two unrelated
memories, and a correction superseded nothing. Graphiti and Quipu both solve this; both pay for it with a model call on
every write, and Quipu's answer (propose → isolated verify → deterministic check) is the one whose cost curve and
failure modes fit a governed ledger.

**Decision.** Three stages, each with a single owner:

1. **Recall** — deterministic, one statement (`semantic.conflict.recall_neighbours`): cosine over the multilingual
   embedding slot (HNSW, migration 23) or, with no embedder, `pg_trgm` similarity; restricted to the caller's own
   `(user, scope_path, subject_key)`, to `status = 'active'`, `single_valued`, `upper_inf(recorded_range)` — exactly the
   population `no_bitemporal_overlap` governs, so recall can find no more than the constraint could hold. At most ten
   neighbours, ordered by descending similarity then id, so two identical calls produce identical shortlists.
2. **Adjudicate** — model, isolated (`RelationAdjudicator`): one relation per plausible neighbour
   (`duplicate` / `update` / `correction` / `contradiction` / `additive` / `unrelated`). It is shown the neighbour's text
   and id and **no score and no preferred order**, because an adjudicator shown a favoured answer tends to confirm it —
   Quipu's isolated verifier makes the same choice for the same reason.
3. **Commit** — deterministic (`commit_strategy`), with a fixed precedence: a contradiction opens a `contradicts`
   association (it is the one relation that must not be resolved silently); a duplicate reinforces; a correction or an
   update supersedes; everything else — including a relation this module does not recognise, and an adjudicator's
   hallucinated id — inserts. The authority ladder may veto any of them (`V2-D87`), and the database's exclusion
   constraint gets the last word whatever the model said, which is why a model can be wrong here without the ledger being
   wrong.

**Why `single_valued` only.** A `multi` claim is a series; two of them are two occurrences, not a contradiction.
Recalling them would invite the adjudicator to "resolve" a non-conflict.

**Why no adjudicator is a supported configuration.** `adjudicator=None` returns no relations and the write inserts, which
is the pre-W3.5 behaviour kept reachable as an ablation. It is deliberately *not* replaced by exact string equality:
silence is honest, and a caller who wants the cheap check asks for it explicitly.

**Open risk, stated.** Contradiction handling opens an association and stores the candidate; it does **not** retire the
existing belief automatically. Retirement needs `C-026`'s refutation path (`W5`), and inventing one here would be a
second, unreviewed lifecycle transition.

**Evidence.** `semantic/conflict.py`; `engine/gate.existing_relation` (slot-scoped); `tests/test_semantic_conflict.py`
(strategy precedence, quarantine, unknown ids, empty recall); `tests/integration/test_migration_m24.py` (slot-scoped
exclusivity at the database level).

**Status:** decided and implemented (2026-11-21).

## V2-D90 — Entity resolution is a cascade, and ambiguity is reported rather than guessed

**Context.** ChronaMind's entity model is the strongest in the comparison and the reason it cannot resolve anything by
itself: entities are **opaque principal-scoped ids** (migration 10) and `memory_version.subject_entity_id` is a composite
foreign key proving the entity exists, belongs to the same principal and lives in the same scope (migration 19). That is
correct for isolation and silent about language: `"Mom"`, `"my mother"` and `"سارة"` are three strings and no id.

**Decision.** A four-stage cascade (`semantic.entities.resolve_mention`), cheapest first: exact normalized label
equality (no query cost beyond one indexed lookup) → `pg_trgm` similarity → cosine over `entity.embedding` when an
embedder is supplied → an injected `EntityAdjudicator` for what the deterministic stages cannot separate.

**A merge needs evidence.** The deterministic stages return *candidates*; a merge requires an exact match or an
adjudicated one. Two candidates within `AMBIGUITY_MARGIN` of each other and no adjudicator is `unresolved` **with both
ids named** — a caller may store the claim against the subject and leave the entity link unset, but picking a winner
would attach one person's claim to another, and the typed foreign key that makes that structurally sound is exactly what
would make the mistake permanent.

**Label normalization is deliberately narrow**: NFKC, casefold, possessives dropped, whitespace collapsed, surrounding
punctuation trimmed. Nothing else, and in particular **diacritics are kept**: `أحمد` and `احمد` are different spellings,
and deciding they are the same name belongs to the adjudication stage where a false merge is visible, not to a normalizer
where it would be invisible.

**Scope.** Every stage filters on `(user_id, scope_path)` exactly as the composite foreign key will re-check at write
time, so a mention cannot resolve to an entity of another scope or another principal.

**Evidence.** `semantic/entities.py`; `tests/test_semantic_entities.py`.

**Status:** decided and implemented (2026-11-21). Wiring `resolve_mention` into the write path is `E-041`'s remaining
acceptance criterion and is tracked in `ROADMAP_V2.md` §6.1.

## V2-D91 — Ingestion is two lanes: durable immediately, understood shortly after

**Context.** The engine had the hard half and no front door. `api.ingest_handlers` accepts pre-extracted claims plus a
verbatim span, and every consumer that wanted to feed it raw conversation had to reinvent extraction, grounding and
conflict classification — which in practice means each consumer either reimplements governance badly or bypasses it. The
review named this "the engine is a storage driver, not a memory engine".

**Decision.** `semantic.ingestor.ingest_turn` runs two lanes with different budgets:

* **fast** (synchronous, milliseconds, no model): the verbatim turn is persisted and its episode committed through the
  same write path every artifact uses. From that instant it is durable, searchable by the language-agnostic lexical arms
  and inside the deletion boundary.
* **slow** (one structured extraction per unit of text, then per claim: grounding → authority → temporal validation →
  slot normalization → optional embedding → three-stage conflict → governed write). Every refusal is a typed outcome with
  a reason recorded (`grounding_failed`, `authority_quarantine`, `adjudicated_contradiction`), never a silent drop.

A deployment chooses how much of the slow lane runs per call (`extractor=None` is the pure fast lane; `adjudicator=None`
is grounding without a second model) and **every combination is a supported configuration of the same code**, so an
ablation measures what the product runs (`RL-032`). Two code paths would let the measured system diverge from the shipped
one, which is the defect this phase exists to remove.

**What the ingestor does not do.** It computes no embedding and calls no model itself: the caller injects an `Embedder`,
a `StructuredExtractor` and a `RelationAdjudicator`, and **every row goes through `engine.ingest` / `engine.transactions`
exactly as `assert` does**. RLS, the temporal exclusion, the provenance trigger and the deletion closure therefore remain
in charge, and removing the package degrades the engine to what it was before rather than leaving a hole in the ledger.

**Evidence.** `semantic/ingestor.py`; `tests/test_semantic_ingestor.py` (a fake extractor/embedder/adjudicator driving
the real write path).

**Status:** decided and implemented (2026-11-21). Asynchronous scheduling (a worker, a budget, backpressure) is `C-023`'s
and stays in `W5`.

## V2-D92 — W5 proposes patterns with a model and admits them with statistics, and a real user reaches a usable model sooner

**Context.** Two W5 specifications were written before the semantic layer existed, and both would have made G5 measure
the wrong thing.

1. `C-020` mandated **non-LLM** co-occurrence/window heuristics for candidate generation, with the LLM confined to prose
   rendering (`C-022`). On the synthetic suites (`own_P`/`own_U`) that works, because each latent rule fires on a
   discrete predicate drawn from a fixed vocabulary. On a real conversation it cannot work: "I hate morning standups" and
   "don't book me anything before 10am" share no entity, no window and no token, and they are the same preference. A
   generator that cannot see that produces no candidate, so `C-021`'s verifier is handed an empty set and G5 is green
   about a capability the product does not have.
2. `V2-D19` requires `o >= 36` zero-counter opportunities for `confidence_lower >= 0.90` at a 97.5% one-sided
   Clopper–Pearson bound. That is correct for a statistical claim about a latent process. It is also, for a real personal
   digital twin, an empty user model: a genuine preference recurs 3–10 times, and the user sees nothing until month
   nine.

**Decision.** Three amendments, none of which relaxes a bound:

1. **`C-020` is replaced in role, not in spirit.** Candidate generation becomes *semantic clustering → LLM hypothesis
   proposal → blind LLM evidence scoring*, with the deterministic operators retained as **recall arms**. The LLM proposes;
   it does not admit. The `(s, k, u)` ledger, the partial-identification CP bound (`SD-002`), the dependence correction
   (`SD-008`), always-valid looks (`SD-005`) and sticky dissent (`UM-012`) are unchanged — they are a stronger verifier
   than the reference implementation this pattern comes from, because Quipu tests a `p`-value while ChronaMind bounds an
   interval.
2. **The instrument gains a linguistic realism track (`UM-016`).** Every scenario is rendered in three paraphrased
   registers, an Arabic register and a code-switched register from the same latent rule, and G4b requires the oracle
   solver to recover the same rule from every register. A precision claim about natural language cannot be earned on a
   corpus with one phrasing per event; MIRIX's own code records a `-7.7` QA regression from exactly the kind of
   unmeasured shortcut this closes.
3. **Sparsity is calibrated honestly rather than hidden (`SD-009`).** Three mechanisms, each separately measurable:
   * `SD-003`'s hierarchical pooling, which already exists in the plan and is now a *prerequisite* rather than a later
     refinement;
   * a **stated-preference fast path**: a `single`-valued claim the user asserted is not a sample of a latent process,
     it is an observation of the process — `assert`-written claims with `cardinality = 'single'` reach `verified` on
     their own evidence, which is what `V2-D87`'s write-path repair made possible;
   * **tiered trust** (`provisional` / `verified`), which `UM-011` already distinguishes at the *rendering* layer:
     `provisional` never reaches `facts` (the channel router routes it by lifecycle, unchanged), never drives a
     recommendation, and is labelled as provisional wherever it appears.

**What must be measured, so the tier is not a loophole.** `UM-002` gains `UM11`: the precision of the `provisional`
tier, reported beside `verified`'s. G5b's `UM8 = 0` (no harmful personalization) and `UM4` (stale-harm, non-increasing
across noise levels) apply to both tiers. A `provisional` tier whose precision is not measured is a way of hiding a weak
model behind a word, and `SD-009` exists to make that impossible.

**What this does not change.** `V2-D19`'s bound, `V2-D42`'s frozen split, `V2-D84`'s scenario-level sample size and the
prohibition on reading the confirmatory split before G-TS all stand. Nothing here may be evaluated on the confirmatory
split early.

**Evidence.** `PLAN/V2/ROADMAP_V2.md` §7 (`UM-016`, `SD-009`) and §8 (`C-020` amended); `PLAN/V2/STATISTICS.md` §5.2;
`PLAN/V2/USER_MODEL.md` §4. Prose specification at decision time: no implementation exists yet, and none is claimed.

**Status:** decided (2026-11-21). Implementation belongs to W4 (`UM-016`, `SD-009`) and W5 (`C-020`), both of which
remain gated on G3.5 and G4.

## V2-D93 — The root application reaches the engine over a narrow HTTP contract, not an import, and `mem0ai` leaves the path

**Context.** The root FastAPI/LangGraph app (`app/`) still ran `mem0ai` (`AsyncMemory` + pgvector) through
`app/services/memory.py`, with its own schema and its own semantics. The review named this as architectural drift and
recorded why the dependency is indefensible for a governed memory service: Mem0's OSS engine (v2.2.1) is ADD-only with
no supersession, keeps deleted plaintext in its SQLite `history`/`messages` tables, and does not check tenant ownership
on `get` / `delete(id)`. Meanwhile the engine and the app run on **different interpreters on purpose** — CPython 3.12 for
the engine with its own Ruff, Pyright, lock file and CI job (`PLAN/INDEPENDENCE_BOUNDARY.md` §4) against 3.14 for the app
— so an in-process import would erase a boundary the project maintains deliberately.

**Decision.** The app's memory layer is a client of the engine's **public operations over HTTP**
(`app/services/memory_engine.py`), with three calls that map onto three operations:

| app call | engine call | note |
|---|---|---|
| `initialize()` | `GET /healthz` | a pre-warm; a failure is logged and swallowed, because memory is an enhancement and an agent that refuses to answer is worse than one without a memory |
| `search(user_id, query)` | `query` with `text` and the caller's scope | renders the four channels with their **epistemic labels**: a hypothesis is labelled as inferred and a conflict as unresolved |
| `add(user_id, messages)` | `ingest_episode` then `assert` | the turn is recorded **first** (fast lane, `V2-D91`), then each `role == "user"` message becomes a grounded claim |

Four properties are deliberate and tested (`tests/test_memory_engine_boundary.py`, 11 checks):

1. **No fallback.** A deployment without `MEMORY_ENGINE_URL` has **no** long-term memory. Silently falling back to mem0
   would mean a deployment believes it has governance it does not have — the exact failure this decision removes.
2. **No shared partition.** An anonymous caller gets an empty context rather than a common one, which would leak one
   caller's facts into another's prompt.
3. **Ordering is the fast lane.** The episode is written before the claim, so a turn is durable and inside the deletion
   boundary even when the claim step fails.
4. **Rendering cannot launder.** `_render` emits a channel heading only when that channel contributes a line, so an
   empty channel never reads as "there is a fact here".

**What is still owed, stated rather than implied.** The engine side of this contract — an HTTP host that authenticates a
caller and dispatches to `Engine.call` — does not exist. It is `E-046`, with the acceptance criteria below, and until it
lands a deployment that sets `MEMORY_ENGINE_URL` gets a client that cannot connect (loudly, by design).

**`E-046` acceptance criteria.** (a) A host that maps `POST /v1/call {operation, tier, arguments}` to
`Engine.call` under a credential derived from a bearer token, never from a request field; (b) `tier` accepted only from
the token's claim, so a caller cannot promote itself (`V2-D47`); (c) every one of the seven static error codes mapped to
an HTTP status without ever carrying an identifier or content (`API_V2.md` §1.1); (d) request bodies bounded and the
`request` object validated by the operation's own handler, not by the host; (e) `GET /healthz` unauthenticated and
content-free; (f) an integration test that drives one `query` and one `assert` through the host into a disposable
database and asserts the four channels come back intact; (g) the app's boundary test re-run against the real host rather
than a mock transport.

**What this does not change.** The engine's contract, its invariants, and the root app's independence rules. The legacy
`app/services/memory.py` remains in the tree until `E-046` lands, and
`tests/test_memory_engine_boundary.py::test_no_module_on_the_agents_path_imports_mem0` asserts that **nothing on the
agent's path** uses it any more — `graph.py` and `main.py` are on the engine client.

**Evidence.** `app/services/memory_engine.py`; `app/core/config.py` (`MEMORY_ENGINE_URL`, `MEMORY_ENGINE_TOKEN`);
`app/core/langgraph/graph.py` and `app/main.py` (wired to `memory_engine`); `tests/test_memory_engine_boundary.py`;
`make check` green on the root app.

**Status:** decided and implemented for the client side (2026-11-21). The engine-side host is `E-046`, open, in `W3.5`'s
remaining work and blocking `W7`'s "app on the public API only" gate.

## V2-D94 — Two wire protocols, one local embedder, and models chosen by measurement

**Context.** The engine takes injected `StructuredExtractor`, `Embedder` and `RelationAdjudicator` (``V2-D88``) and knows
nothing about a provider. A deployment still has to *choose* concrete implementations, and the choice is a decision with
quality, cost, latency and licensing consequences. This record is that decision, taken from measurements rather than
from a leaderboard. The harness is `scripts/eval_models.py`; the results are in `reports/model_selection/README.md`.

**One protocol per model family.** The gateway serves Claude models on `/v1/messages` and GPT models on
`/v1/chat/completions`, and refuses the other shape with `400 protocol_mismatch`. So there are two small clients — no SDK
for either — and one selector (`integrations.provider.client_for`) that picks by model family and takes an explicit
override. Two facts about *compatibility*, not preference, and both were discovered by calling the API:

* `response_format: {"type": "json_object"}` is refused by Claude models, so the Messages client asks for JSON in the
  system prompt instead. A single client could not serve both families without such a fallback;
* `temperature` is refused by several models (`400 Omit temperature for this model`). The clients learn it per model and
  drop the field, so a deployment's configuration does not have to encode a compatibility matrix that changes per model.

**Embeddings are local, because the provider has none.** `/embeddings` answers `403` for every candidate name tried.
`integrations/onnx_embedder.py` runs a pinned `bge-m3` ONNX file (1024-d, CLS pooling) in the `halfvec(1024)` slot, with
`tokenizers`/`onnxruntime` — already the project's dependencies for its evaluation baselines. Pooling and the
`query:`/`passage:` prefixes are parameters of the asset and are validated, because getting either wrong degrades recall
silently while every test still passes.

**The chosen combination.**

| capability | model | the measurement that decided it |
|---|---|---|
| extraction | `gpt-6.2-sol` | zero JSON parse failures (the others had 3–4), 100% verbatim quote grounding, 12/13 on multilingual cases |
| adjudication | `claude-haiku-5-5` | 6/6 once the label semantics are read correctly, and the cheapest/fastest of the three; its weakness (long structured output) does not apply to a 3-field answer |
| embeddings | local `bge-m3` | the only option: the provider serves no embedding endpoint |
| reranking | none | an LLM rerank over the top 5 scored 10/14 top-1 against the fused order's 9.6/14 — inside noise — while abstaining on the cross-script questions the dense arm already solved, and costing a model call per read |

**The number that changed the prompt, not the choice.** The first sweep showed the best model returning *verbatim*
quotes only ~25% of the time. A quote that is not a verbatim substring is a claim `grounding.ground_text` refuses, so
three of four extracted memories would never be stored — a defect no leaderboard reports and no unit test of the
extractor would have caught. Adding a worked example and a hard rule to `EXTRACTION_SYSTEM` took the rate to 86–100%.
The evaluation harness itself also had a bug at that point (`quote_grounded` called with reversed arguments), which made
every model look equally bad; `tests/test_eval_harness.py` now pins the argument order, because a wrong instrument
reports the wrong winner.

**What this does not do.** No model name reaches `src/custom_memory_engine` outside `integrations/`: a test asserts it,
and the `ModelClient` protocol is the only shape the engine sees. A deployment that prefers a different provider
implements the same four methods; a deployment that prefers a self-hosted model implements the same four methods over a
local runtime; neither touches a decision, a migration or the read path.

**Status:** decided and implemented (2026-11-21). `scripts/eval_models.py` reproduces the table; the sweep history and
the per-case failures are kept under `reports/model_selection/`.

## V2-D95 — The entity resolver had never run against the real table

**Context.** `E-041` shipped `semantic/entities.py` with a cascade documented as exact → trigram → vector →
adjudicator, and only ever exercised by unit tests that did not touch the database. Wiring it into the write path
found two columns that do not exist: it queried `entity.label` (the column is `canonical_name`) and
`entity.embedding` (there is no such column — migration 10 never added one).

**Decision.** Fix the query to the real schema and **drop the vector stage** rather than add a column to keep a
documented stage alive. Entity names are short labels; a per-name vector would be a new column, a new index and a
re-embedding rollout, and the cascade still has exact, alias, trigram and adjudicator. The floor constants that
described the vector stage are removed, and `resolve_mention` accepts an `embedding` argument it documents as unused so
an existing caller does not break.

**The rule the resolver now obeys, and that the wiring test enforces.** A tie is `unresolved`, never a guess: two
entities whose scores are within `AMBIGUITY_MARGIN` produce no attachment and a count in the ingestion report, because
attaching a claim to the wrong person is worse than attaching it to nobody. An adjudicator that *abstains* on a single
candidate also yields `unresolved` rather than `created` — creating a duplicate identity next to one the resolver had
already found is the same mistake seen from the other side.

**Wiring.** `semantic.ingestor` resolves every mention (one cache per claim, so a repeated mention costs one query),
creates the unknown ones in the claim's own scope, and sets `subject_key`/`subject_entity_id` only when the extractor
designated that mention as the subject (`ExtractedClaim.subject_mention`). A claim about the speaker — "I moved to
Berlin" — keeps `subject_key='self'` and still creates the Berlin entity, which is the distinction between *about* and
*mentioned*. Stage counts go into `IngestionReport.diagnostics` as content-free integers, so an operator can see that
mentions are being created rather than matched without logging a single label.

**Evidence.** `semantic/entities.py`; `semantic/ingestor.py`; `tests/integration/test_semantic_entities_db.py` (9 tests
against the migrated schema, including one that drives two turns through the ingestor and asserts exactly one entity
row); `tests/integration/test_semantic_ingestor_db.py`.

**Status:** implemented (2026-11-21). The vector stage is deferred until `entity` has an embedding column and a
measurement justifies it.

## V2-D96 — The host, the refutation operation, and the asynchronous lane (``E-046``, ``E-047``, ``C-026``)

**The host is the engine's contract over HTTP, and nothing more.** `host/service.py` holds a connection pool and builds
the deployment's wiring per call; `host/app.py` adds exactly three things the engine cannot know: who is calling (a
bearer token mapped to a `Credential`, tier from the token, never from the body), a body bound enforced before parsing,
and the seven static codes of `API_V2.md` §1.1 mapped to HTTP statuses. `forbidden` answers 403 for "absent" and
"unreadable" alike, so the transport does not re-introduce the oracle the engine's error contract removes. Every
operation's result is serialised by deriving the wire shape from the result object (dataclass fields, `__slots__`
entries), so a field added to a result cannot be forgotten here.

**One wiring function, and an explicit list of what is not wired.** Before this, a deployment had to call six
`register_*` functions in the right order; forgetting one produced a live service where an operation answers `forbidden`,
which reads exactly like a permission bug. `api/wiring.wire_engine` is the single call, and
`UNWIRED_OPERATIONS` states every operation the contract declares and no handler owns, with the reason. A test fails if
the list and reality disagree.

**`refute` completes the lifecycle.** It was in the contract since `API_V2.md` §3 with no handler: a belief the engine
inferred could be argued with only by an administrator, and the semantic ingestor's contradictions could only be staged,
never retired. `refute` is Tier B and fenced three ways (`own_lineage_only`, `targets_readable`, `evidence_required`), and
it only ever moves an artifact **out of** `inferred`: an `asserted` claim is the user's own word, withdrawn by the user
or resolved by adjudication. The retirement is the recorded-time close (branch (S) of `I10`), the archive row, one
content-free audit event carrying the reason code and two counts, and — when a counter-evidence ref names one of the
caller's readable versions — a `contradicts` association, so the justification is traversable *and* inside the deletion
closure. The refs themselves are never copied into the audit row: a ref can be a path or a URL, and `audit_event`
outlives deletion.

**The asynchronous lane is two transactions, and that is the point.** `queue.process_one` runs a handler inside one
transaction, which is right for database-only work and wrong for a model call: five seconds of extraction would hold row
locks, and a serialization retry would re-bill the model. `semantic/slow_lane.py` adds `claim_one` (transaction A:
`FOR UPDATE SKIP LOCKED`, mark `running`, count the attempt) and `complete_one` (transaction B: write, mark `done`), with
`recover_stale` for the one state the split introduces — a worker that died holding a lease. `semantic.ingestor` is split
accordingly: `ingest_turn` is now exactly fast lane + extract + `apply_extraction`, and `apply_extraction` is the pure
database half a worker calls.

**What the app sends, and the contract extension it needed.** The root application keeps its own transcript, so its
evidence is a non-`turn` source. `_source` now accepts an optional `role` on non-`turn` sources: without it every claim
the app made would have been quarantined as third-party, which would have made the engine unusable for its only
deployment. A `turn` source may not declare a role — there the role is a column of the row being cited, and a
caller-supplied copy would be a second source of truth.

**Evidence.** `host/` (credentials, service, app); `api/lifecycle_handlers.py`; `api/wiring.py`;
`semantic/slow_lane.py`; `scripts/run_host.py`; `scripts/run_worker.py`;
`tests/integration/test_host_e2e_db.py` (18 tests, real uvicorn on a real socket against a real database);
`tests/integration/test_slow_lane_db.py` (8 tests: identity, claim, completion, dead-letter after `MAX_ATTEMPTS`, lease
recovery, tenant scoping, idle-connection requirement); `tests/integration/test_host_e2e_db.py::test_refute_*`.

**Status:** implemented (2026-11-21). `dissent`, `verify`, `expire`, `rebuild_index`, `export` and `stats` remain
unwired with their reasons recorded in `api/wiring.UNWIRED_OPERATIONS`.

---

## V2-D97 — Every operation is wired: the six, and what each one's missing piece actually was

**Status:** adopted (2026-11-25). Supersedes the "remain unwired" paragraph of `V2-D96`. Closes `A-014`'s handler half
and the `I11`/`I15` rows that read "`verify`/`stats` are not wired, so the ledger is not reachable through `Engine.call`".

`api.wiring.UNWIRED_OPERATIONS` is now **empty**. That is not a bookkeeping change: each entry had a stated reason, and
each reason named a missing *piece of machinery*, not missing plumbing. This decision records what that machinery is and
where it lives, because the alternative — a handler that satisfies the signature — would have turned six honest gaps into
six silent lies.

| operation | why it was unwired | what was actually built |
|---|---|---|
| `stats` | "needs an outcome-stats reader" | `engine/statistics.py`: the ledger, the verification record behind the status, the derivation ids, the derived staleness causes and procedure outcomes, in one report that returns **ids and never content** |
| `verify` | "the `verification` wire shape is unspecified while the schema requires held-out splits, disjoint windows and context keys" | `engine/verification.py`: a `VerificationRecord` with exactly `I11`'s six facts, a disjointness assessment, the append-only insert, the status move, and the `F5` epoch fence |
| `dissent` | "needs a `negative_constraint` reader in inference; a constraint nothing consults would be a write-only column" | the handler **and** the reader: `refute`'s sibling in `api/lifecycle_handlers.py`, plus `InferHandlers._blocked`, so `infer` answers `Rejected("negative_constraint_active")` |
| `expire` | "`V2-D14` is still open on the retention values" | `engine/expiry.py` + a handler that reads the window from `profile.abstraction_expiry` and stamps `abstraction_stats.expires_at`; no defaults are compiled in, so an unconfigured deployment expires nothing and says so |
| `rebuild_index` | "returns a job id and needs the queue's index-maintenance handler" | `engine/index_maintenance.py`: three logical indexes, two execution shapes (`REINDEX … CONCURRENTLY` outside a transaction for `fts`/`vector`, an in-transaction reseal for `closure_hash`), and the job the handler enqueues |
| `export` | "`ExportSnapshotStore` exists, the handler does not" | migration 25 + `engine/export.py`: the store was a **process-local dict**, which cannot implement channel 11 at all; a manifest is now a durable, epoch-bound, append-only row |

**Three declarations were corrected, and each correction is a safety change rather than a convenience.**

1. `expire` no longer declares `deletes=True`. It stamps a derived-cause column and destroys nothing; a declared deletion
   that never happens invites a reviewer to assume a guarantee, and aims `TB1` at an operation that deletes nothing.
2. `expire`'s output is `ExpiryReceipt` (two counts and the belief time) instead of `Receipt`. `PurgeReceipt`'s reasoning
   applies unchanged: an expiry touches no caller-named artifact, so there is no id to return and an id is the one thing
   `API_V2.md` §1.1 forbids in a maintenance receipt. It also gains `precondition_failed`, because a caller's `as_of` can
   be well-formed and still not a legal observation of the clock.
3. `dissent` records its constraint against the method **family** (the method's name, not its id), so a user's rejection
   survives a method version upgrade. Lifting a constraint needs grounded evidence and has no operation yet, so a dissent
   is currently permanent — the conservative direction, since silently re-learning what a user rejected is the failure
   this mechanism exists to prevent.

**`verify`'s disjointness algorithm** is the part `INVARIANT_LEDGER.md` recorded as "not built". `I11` requires the
held-out split to be genuinely held out, which the schema alone cannot check: overlapping **windows** re-check the same
evidence, and a shared **context key** is the near-duplicate leakage that makes a finite-sample bound meaningless. Both
are refused before the insert, with distinct codes, so the two faults stay distinguishable in an incident. One split is
recorded once per artifact: re-running a split until it agrees is not verification.

**Evidence.** `tests/integration/test_api_wiring_lifecycle_db.py` (23 tests through `Engine.call` as the restricted role:
ledger and ids without content, cross-tenant `forbidden`, a grounded artifact's empty ledger, the verification record
behind the status, disjoint/leaky/duplicate/invalid splits, dissent's retirement and constraint, the re-learn refusal,
the expiry stamp with and without a policy, `as_of` in the future, the queued rebuild, the empty `UNWIRED_OPERATIONS`,
the export manifest and its replay, the tier check, and channel 11); `tests/integration/test_migration_m25.py` (6:
self-consistency, the count constraint, the digest constraint, append-only on `UPDATE`/`DELETE`, RLS under the runtime
role, and the rollback fingerprint); `tests/integration/test_migration_m24.py` (rollback composition through 25).

---

## V2-D98 — The engine embeds the query, and the write path embeds the document

**Status:** adopted (2026-11-25). Completes `V2-D86`'s dense arm, which was measured but unreachable from a deployment.

`V2-D86` gave the read path a dense arm and `hybrid.RecallQuery` an `embedding` field, and `V2-D93` gave the app an HTTP
client. The gap between them was decisive: **the caller had to supply the vector**, so the app would have had to own a
model to receive hybrid recall — and it does not. In practice every app query ran the two lexical arms, and the dense arm
was reachable only from a test that hand-built a vector.

**The engine now owns both sides of one model.** `ReadHandlers` takes an optional `semantic.contracts.Embedder`;
`wire_engine` passes the deployment's and `host.HostService` builds it from `CME_EMBEDDING_ASSET`/`CME_EMBEDDING_DIR`.
`ingest_episode` and `assert` embed the **document**, `query` embeds the **question** (`embed=True` → `query=True`), and
both name `embedder.model`, which is what the dense arm's `embedding_model = %(model)s` predicate joins on. A vector
stored under one model is invisible to a question encoded by another; there is no silent cross-model cosine.

**Failure degrades and says so.** A model that cannot load must not turn every question into `internal_error`, and it
must not degrade silently either: the read logs `query_embedding_unavailable` with a static code and the result reports
exactly which arms ran (`QueryResult.arms`). That field is the reason the degradation is honest — a benchmark, a test and
a caller can all see whether an answer was hybrid. On the write path the rule is the opposite and deliberate: a model
failure never fails the write (a lost user message is far worse than a row the lexical arms must find), and the failure
is logged instead.

**Advanced callers still win.** A request that supplies `embedding`/`embedding_model` keeps them; the engine's embedder is
only consulted when the caller sent text and no vector. A supplied vector is validated against `hybrid.EMBEDDING_SLOTS`
at the boundary, so a width the schema cannot index is refused rather than dropped.

**One model, one process.** The embedder is a single object per process (it owns a loaded ONNX session); building one per
request would reload a 2.2 GB graph per request. A width the schema cannot index is refused at **start-up**
(`embedder_from_environment`), which is the only place it can be refused without answering a caller's question with an
error about a vector they never sent.

**Evidence.** `tests/integration/test_hybrid_recall_db.py` (a configured embedder finds a paraphrase with no caller
vector and reports `("dense","lexical","trigram")`; a failing embedder degrades to `("lexical","trigram")`; a
caller-supplied vector still wins; the ingest path stores the vector under the configured model);
`tests/integration/test_host_e2e_db.py` (the real `bge-m3` model through the real HTTP host: a paraphrase is found over
the wire, `arms` contains `dense`, and an unrelated question still answers nothing).

---

## V2-D99 — An export is a durable manifest, not a copy of the memories

**Status:** adopted (2026-11-25). Supersedes `deletion.closure.ExportSnapshotStore` as the export mechanism (the class
remains as the in-memory helper the deletion suite uses).

`API_V2.md` §4.9 asks for three things — a manifest of the caller's read set, exclusion of deletion-pending content, and
"a snapshot made before a deletion must not be served after it (channel 11)". What existed was a Python dict: correct
enough to prove `F11` inside one process, and useless as an implementation, because the host builds its wiring per call
and a second worker would not see the dict at all.

**The design decision is what a snapshot contains.** A manifest: the sorted ids the caller could read, a SHA-256 over
them, the count, the read set's scope paths and the deletion epoch. **Not** the content. A second copy of content is
exactly what a deletion cannot reach, so an export that copies is an export that leaks on channel 11; serving a snapshot
re-reads through the ordinary read path, so every deletion, retraction and expiry lands in it as it lands everywhere
else. Migration 25 makes the manifest append-only, self-consistent (count = array length, digest = SHA-256) and
user-isolated under `FORCE ROW LEVEL SECURITY`.

**Channel 11 is a comparison, not a trigger.** `captured_epoch` against `current_deletion_epoch` (the `MAX` over the
append-only `deletion_request` log), checked through the same `require_current_deletion_epoch` guard a verification
commit uses — so a serve racing a deletion either completes before it or is refused after it, and there is no third
outcome. A replay of the same read set at the same epoch returns the existing manifest: the row already there *is* the
answer, and re-inserting would mint a second id for the same fact.

**Evidence.** `tests/integration/test_migration_m25.py`; `tests/integration/test_api_wiring_lifecycle_db.py`
(`test_export_manifests_what_the_caller_can_read_and_replays_the_same_manifest`,
`test_export_is_tier_c_only_and_refuses_a_malformed_selector`,
`test_a_snapshot_made_before_a_deletion_is_not_served_after_it`, which drives the real `delete` operation).

---

## V2-D100 — The retrieval decision, re-made on 64 labelled questions

**Status:** adopted (2026-11-25). Supersedes the numbers in `reports/retrieval/README.md`'s first round and the "BM25 is not
implemented" paragraph's reasoning; the *decision* (no BM25, no reranker) survives, on much stronger evidence.

The first round measured 16 questions and concluded that the dense arm earns its place. It could not answer the question
the plan left open — whether a term-weighting ranker or a second-stage reranker improves the *order* — because a set with
two unanswerable questions cannot measure a precision claim, and it did not: the 0.45 similarity floor chosen there
answered **7 of 8** unanswerable questions on the larger set.

**What was measured.** 49 memories and 64 questions (20 lexical, 12 paraphrase, 8 cross-script, 8 with no answer), six
configurations through the real read path, `bm25` and both rerank rows computed offline over the same candidates so they
cannot win by retrieving what the engine could not.

| configuration | recall@5 | top-1 | nDCG@5 | no-match FP |
|---|---|---|---|---|
| recency | 0.089 | 0.018 | 0.053 | 1.000 |
| lexical only | 0.393 | 0.375 | 0.386 | 0.000 |
| **hybrid (shipped)** | **0.982** | **0.732** | **0.882** | 0.125 |
| bm25 over the corpus | 0.143 | 0.071 | 0.106 | 1.000 |
| hybrid → rerank by BM25 | 0.982 | 0.482 | 0.765 | 0.125 |
| hybrid → rerank by recency (control) | 0.982 | 0.411 | 0.726 | 0.125 |

**Three decisions.**

1. **BM25 is rejected.** Standalone it recalls 0.143 — term weighting cannot connect a paraphrase to its memory. As a
   reranker it keeps recall and destroys ranking (top-1 0.732 → 0.482), because it pulls lexical overlap above meaning.
   The recency control degrades too (0.726), so the method is sound and BM25 is simply worse informed than the fusion.
2. **No reranker is shipped.** The two cheapest rerankers both make the order worse; a cross-encoder is one model call
   per *read* for a gain the first round measured inside noise. `Reranker` stays a seam.
3. **`DENSE_SIMILARITY_FLOOR` is 0.55, measured, and the constant is asserted against its sweep.** The criterion is
   stated before the numbers: minimise the no-match false-positive rate subject to recall@5 ≥ 0.90. 0.55 gives 0.982
   recall and one FP in eight; 0.62 gives zero FP and 0.768 recall. The residual is not hidden: the weakest *true*
   match on this set is 0.46 and the strongest *false* one is 0.605, so no single cosine threshold separates them, and
   that is a limit of a bi-encoder rather than of the threshold.

**One read-path defect the measurement found.** With `simple` as the FTS configuration (no stopword list, by design, since
a stopword list is language-specific), a question like "what is the wifi password at the office" matched every row
containing "the". The lexical arm now prunes query terms whose document frequency in the caller's own rows exceeds
`TERM_PRUNING_DF_RATIO = 0.35` — a statistic computed inside the same temporal and read predicate, so it is a statistic
about rows the caller may already read and never a cross-tenant oracle. Membership was the defect; ranking was not.

**Evidence.** `tests/integration/test_retrieval_eval_db.py` (the set's own coverage, the floor sweep asserted against the
constant, and the relative claim that the fusion dominates what it replaced and is not beaten by any alternative),
`evaluation/retrieval/`, `reports/retrieval/{evaluation.json,evaluation.md,floor_sweep.json}`.

---

## V2-D101 — Entity embeddings propose, and never merge

**Status:** adopted (2026-11-25). Migration 26.

`semantic/entities.py` documented a four-stage cascade with a vector stage that was dead code: it queried
`entity.embedding`, a column that never existed. The earlier fix removed the query and recorded that restoring the stage
needs a migration. Migration 26 adds `entity.embedding_1024 halfvec(1024)`, `embedding_model` and an HNSW index, and the
stage is implemented.

**Why it earns its place.** A cross-script mention shares no trigram with its entity: `ليلى حسن` scores 0.0 with
`Layla Hassan` under `pg_trgm` and 0.573 under the model. `مكتب برلين` scores 0.791 against `Berlin office`. No amount of
string matching serves that case.

**Why it may not merge.** The same measurement produced the counter-examples that decide the design: `Munich office`
scores **0.769** against `Berlin office`, and `Project Beta` scores **0.791** against `Project Alpha`. Two *different*
entities that differ by one token are nearly identical in embedding space — exactly the pairs where a merge is
destructive and permanent. So the stage runs **only** when exact, alias and trigram found nothing, requires
`VECTOR_FLOOR = 0.75`, filters by `embedding_model`, and returns a *candidate*. The existing fence is unchanged: a merge
needs an exact match or an adjudicated confirmation, which is why `Munich office` proposes Berlin and stays `unresolved`.

**What this does not claim.** A deployment with no adjudicator resolves no cross-script mention (it reports them
`unresolved`). That is the correct half of the capability, and the alternative — merging on cosine — is the failure the
measurement exists to prevent.

**Evidence.** `tests/integration/test_semantic_entities_db.py::test_a_cross_script_mention_can_be_proposed_and_needs_confirmation_to_merge`,
`::test_a_different_entity_of_the_same_shape_is_proposed_but_never_merged`,
`::test_without_an_embedding_the_cascade_behaves_exactly_as_before`;
`tests/integration/test_migration_m25.py`'s composed-rollback check; `reports/entity_resolution/README.md`.

---

## V2-D102 — The model evaluation set is 46 cases, and one of its labels was wrong

**Status:** adopted (2026-11-25). Supersedes the 21-case selection in `reports/model_selection/README.md`.

Extraction and adjudication were being chosen from 21 cases, 12 of them extraction. The set is now 46 (28 extraction
across English, Arabic and mixed-script, 3 schema, 14 adjudication, 1 rewrite) and it separates the candidates where the
old one could not:

| model | ext-en | ext-ar | ext-mixed | schema | adjudication (corrected labels) | p50 |
|---|---|---|---|---|---|---|
| `gpt-6.2-sol` | **13/14** | **10/10** | **6/6** | 3/3 | **13/14** | 9.0 s |
| `claude-sonnet-5-5` | 10/14 | 8/10 | 3/6 | 3/3 | 11/14 | 6.4 s |
| `claude-haiku-5-5` | 12/14 | 8/10 | 2/6 | 3/3 | 10/14 | 2.5 s |

The adjudication column is the *second* run, after the label defect below was fixed: the first run's figures (5-6 of 14
for every model) were the instrument failing, not the models. `claude-haiku-5-5` stays the documented choice for a
latency-bound deployment — 10 of 14 at 2.5 s against 13 of 14 at 9.0 s — and `CLAUDE.md` records that trade explicitly
rather than hiding it behind "gpt is better".

**The defect the expansion exposed.** The second adjudication block used the engine's *strategy* names as its expected
labels: `supersede` and `reinforce`, which are what `conflict.commit_strategy` decides. The models are asked for
`duplicate|update|correction|contradiction|additive|unrelated` and can never answer either, so three candidates were
scored wrong for correct answers (a move answered `update`, which is exactly right). Every adjudication figure before
this correction is therefore void, and the earlier conclusion "all three models score 4/6, so the choice is a tie" was an
artefact of the instrument. `tests/test_eval_harness.py::test_every_adjudication_label_is_a_relation_a_model_may_emit`
now fails if a label is outside the vocabulary.

**Selection.** Both stages go to `gpt-6.2-sol`: extraction 29 of 30 against 21 and 22, adjudication 13 of 14 against 10
and 11, with 100% verbatim-quote grounding and no JSON parse failures across either run — a margin no longer inside
noise. `claude-haiku-5-5` is recorded as the latency fallback (2.5 s against 9.0 s at four points of adjudication
accuracy), because adjudication runs once per conflicting candidate and a deployment with a tight write budget may
legitimately prefer it.

---

## V2-D103 — The worker is deployed, probed from the queue, and tested

**Status:** adopted (2026-11-25).

The slow lane existed and nothing ran it. `deploy/` now holds a systemd unit (`cme-worker.service`), a container image
(`Dockerfile.worker`) and a compose file (`compose.worker.yaml`), and `scripts/worker_healthcheck.py` is the probe all
three use.

**The probe reads the queue, not the process.** Exit 0 healthy, 1 degraded (the oldest due job is older than 30 minutes),
2 unhealthy (a lease past `semantic.slow_lane.LEASE`, or the database is unreachable). A PID check passes through the
failure this lane is most likely to have — a worker alive but stuck behind a hung model call — so the probe measures the
property that matters: is the queue being drained.

**Graceful stop is the loop's own.** `SIGTERM` sets the loop's stop event, the job in flight finishes, and the process
exits; `TimeoutStopSec=60`/`stop_grace_period: 60s` outlive one extraction with retries. A hard kill loses only a lease,
which `recover_stale` returns.

**Deployment stays outside the engine.** No import of `deploy/` from `src/`, and the probe imports only the queue and the
lease constant, so it never loads the embedding model to answer "is the loop alive".

**What is tested and what is not.** `tests/integration/test_worker_deployment_db.py` drives a real queue: a lease past its
window is returned to `queued`; the probe returns 0/1/2 for healthy/degraded/unhealthy; a job that fails repeatedly
reaches `dead_letter` after `MAX_ATTEMPTS`. What is *not* tested is that systemd or Docker restarts the process — that is
the supervisor's contract, and the unit and compose file are reviewed rather than executed.

## V2-D104 — The read path carries a relevance judge, and the judge gates

**Context.** `V2-D100` re-derived the dense floor to 0.55 and recorded a residual it could not remove: one unanswerable
question in eight ("our neighbours complain about the noise every night") still returned a neighbour, because its
nearest memory sat at cosine 0.605 while the weakest *genuine* paraphrase in the set sat at 0.46. Before accepting that
as a model limit, three cheaper rules were measured on the full query × corpus cosine matrix and **rejected**: a
corpus-relative z-score (unanswerable questions reached 3.44, answerable ones fell to 2.16), a top-1/top-2 gap (gaps
overlap completely), and requiring a lexical or trigram arm to corroborate the dense one (it would have dropped 30 of 56
genuine answers). Encoding only the informative terms of the question made the overlap worse (0.361 vs 0.569).

**Decision.** A local cross-encoder reads each candidate *together* with the question and returns one logit on a fixed
scale, and the page keeps only candidates at or above `JUDGE_RELEVANCE_FLOOR`. Two multilingual rerankers were
measured on the labelled set; `bge-reranker-v2-m3` (int8 ONNX, pinned by revision and SHA-256 in
`scripts/fetch_models.py`) separates the classes — weakest answerable -6.68, strongest unanswerable -11.01 — and
`jina-reranker-v2-base-multilingual` does not (-2.91 vs -2.38), so it is recorded and not chosen. The floor is the
midpoint, **-8.8**, asserted against `reports/retrieval/evaluation.json` by
`tests/test_hybrid.py::test_the_judge_floor_sits_between_the_weakest_answer_and_the_strongest_non_answer`.

**Where it lives.** In `api.read_handlers`, after the id-restricted fetch, because that is where the candidate *content*
is already in hand — the judge costs one forward pass per candidate and no second statement. `engine.hybrid` stays pure
SQL over ids. The old order-only `Reranker` seam is removed: nothing implemented it, and its contract ("only the order is
used") was exactly what made it unable to fix this.

**Bounds and failure.** At most `JUDGE_CANDIDATES` (10) are read (~28 ms each, pairs scored one at a time because the
graph is padding-sensitive), and rows beyond the bound are *not* returned unjudged. A judge failure, a score-count
mismatch or a non-finite score degrades to the fused page and reports `judgement=None`; a judged page reports
`QueryResult.judgement` (model, floor, scored, kept, best), so "no judge", "judged and empty" and "judged and kept" are
three distinguishable answers. No LLM is on the read path.

**Measured.** `hybrid_judged`: recall@5 0.982 (unchanged), top-1 0.768 (from 0.732), nDCG@5 0.896 (from 0.882),
no-match false positives **0.000** (from 0.125). End to end over real HTTP, the exact near-miss returns an empty page
(`test_host_e2e_db.py::test_the_judge_refuses_the_near_miss_the_dense_arm_alone_would_have_answered`).

**Cost.** ~28 ms per candidate on the reference CPU, and a 570 MB model file. The judge is optional
(`CME_RERANKER_ASSET`); a deployment without it behaves exactly as before and reports `judgement=None`.

## V2-D105 — A dissent can be lifted, on new grounded evidence, and named

**Context.** `I16` says a denied element "cannot be re-inferred … *without new grounded evidence*". After `V2-D97` the
constraint was written and read, but no operation lifted it and none returned its id, so a dissent was permanent and
unnamable — `I16` was `PARTIAL` for exactly that reason.

**Decision.** Two operations. `lift(constraint_id, evidence_artifact_id, note?)` (Adjudicate, Tier C) stamps the
schema's existing lift record. The evidence must be an `observed` or `asserted` artifact of the caller's: readability is
the entry point's `targets_readable` condition (so an unreadable ground is `forbidden`, indistinguishable from absent),
and groundedness is checked in the handler — an `inferred` artifact is refused (`evidence_not_grounded`), because a
constraint on inference that inference can lift is no constraint. One lift per constraint; a second is
`constraint_not_active`. `constraints(include_lifted?)` (Read, Tier B and C) lists the caller's own constraints, ids
and element names only, active first. The caller names the *constraint*, not the belief, because the constraint
survives both the retirement and the deletion of the belief that provoked it.

**What the schema already guaranteed and is now reachable.** Deleting the lift's evidence clears the reference and the
guard withdraws the whole lift, so the dissent is active again and `infer` refuses the element again —
`test_api_wiring_lifecycle_db.py::test_deleting_the_lift_evidence_withdraws_the_lift_and_the_element_blocks_again`.
`I16` moves to `ENFORCED`.

## V2-D106 — The slow lane asks its models before it writes, under the tenant's role, inside the deletion fence

**Context.** A review of the worker found six defects that no test reached, because the loop lived in
`scripts/run_worker.py` and `tests/` may not import `scripts/`:

1. **Model calls inside the write transaction.** `apply_extraction` was documented as pure database work, but the
   worker handed it the embedder and the relation adjudicator, which it called after `allocate_clock` had locked the
   tenant's watermark — seconds of lock per adjudicated claim, during which the fast lane for the same user queued.
2. **No restricted role.** `CME_RUNTIME_ROLE` was read and never used; every slow-lane write ran as the connecting owner.
3. **Vector proposals merged without confirmation.** With no entity adjudicator — the state of every deployment, since
   none was wired — `resolve_mention` returned a single vector candidate as *resolved*, merging "Munich office" into
   "Berlin office" on a 0.769 cosine: the exact case `V2-D101` forbids. And an adjudicator's answer was trusted even if
   it named an entity that was never offered.
4. **No deletion fence.** A turn read at deletion epoch *E* and written after a deletion committed would resurrect what
   the user removed; `process_one` fences this (`F5`) and the slow lane did not.
5. **Recovery only at start-up**, so a lease held by a dead worker stayed `running` until some worker restarted; and an
   extractor exception left its job `running` for the whole ten-minute lease instead of backing off.
6. **A probe that could not see the queue.** `job` forces RLS and the probe never bound `app.user_id`, so in any
   non-superuser deployment it reported "healthy" over any backlog. The test DSN is a superuser, which hid it.

**Decision.**

* The loop is engine code (`custom_memory_engine.worker`); the script is the process around it.
* **Ask, then write** (`semantic.judgements`): embed every text up front; dry-run the write in a rolled-back
  transaction with *recording* adapters to collect exactly the questions it asks; answer them with no transaction open;
  write with *replay* adapters that cannot call a model and abstain on anything unrecorded. A second dry run is made
  only when an entity answer could move a claim's subject.
* Every slow-lane transaction runs under `SET LOCAL ROLE` + `app.user_id` (`security.scoping.tenant_scope`); only
  `REINDEX ... CONCURRENTLY` keeps the owner, because it needs the index owner.
* `EntityAdjudicator.choose` is asynchronous, and an LLM implementation exists
  (`integrations.openai_compatible.OpenAiCompatibleEntityAdjudicator`) that shows the model numbered candidates and
  never an id. A vector proposal without confirmation is `unresolved`; an answer outside the offered set is an
  abstention.
* The turn and the deletion epoch are read in one snapshot; the write requires the epoch unchanged, and a stale one
  dead-letters the job with the same content-free audit event `process_one` writes.
* Expired leases are returned on every idle cycle; a pre-write failure is recorded through `complete_one`'s backoff.
* The probe reads each tenant inside its own scope, under the runtime role.

**Tests.** `test_worker_loop_db.py` (every model call asserted to see an idle connection — fails with
`embed:in_transaction` if the write is handed live adapters; cross-script confirmation; unconfirmed proposal left
unattached; backoff; recovery by a live loop; graceful stop mid-call; RLS on another tenant's turn; deletion during the
model call dead-letters). `test_worker_process_db.py` runs the real script as a child: SIGTERM exit 0, SIGTERM mid-call
still commits, SIGKILL then restart recovers, missing variable is exit 2. `test_semantic_entities_db.py` pins the merge
regression and the offered-set fence; `test_worker_deployment_db.py` runs the probe as the restricted role with a control
proving the unbound read sees nothing. Each regression test was run against the old code and failed.

## V2-D107 — Models are scored by the write they cause, and entity adjudication is chosen for its error profile

**Context.** Two defects in the measuring instrument, found while extending it: the adjudication scorer accepted
`duplicate`, `update` and `correction` for one another although the engine reinforces on the first and supersedes on
the other two, so an answer that keeps a stale value scored as correct; and the adjudication prompt never offered
`additive`, so a statement adding a detail to an existing belief had no correct answer that kept the detail (two cases
had been labelled `duplicate` for that reason, which the engine would execute as "keep the old one").

**Decision.** `evaluation.model_selection.cases.strategy_of` calls `conflict.commit_strategy`, and a case is correct
when the answer causes the expected write (alternatives argued per case); no answer is never credited. The production
prompt offers `additive` and says the two statements may be in different scripts. Entity adjudication is a measured
task through the shipped adapter, with false merges counted apart from misses. Mixed-script extraction gains five cases.

**Measured** (`reports/model_selection/round3/`): `gpt-6.2-sol` 20/20 adjudication, 10/11 mixed-script extraction,
zero JSON failures, 10/12 entity with **one false merge**; `claude-haiku-5-5` 17/20, 7/11, two JSON failures, 10/12
entity with **zero** false merges, 2.8 s p50 against 11.6 s; `claude-sonnet-5-5` 17/20, 6/11, 10/12 with zero.

**Selection.** Extraction and relation adjudication stay on `gpt-6.2-sol`: the alternatives answered genuine
contradictions as `update`, which supersedes a belief silently. Entity adjudication moves to `claude-haiku-5-5`
(`CME_ENTITY_ADJUDICATOR_MODEL`): equal accuracy and the safe error profile — a false merge corrupts identity, a miss is
repaired by the next mention. The sample (12 entity cases) is stated as finding failure kinds, not ranking within a
point; a re-run that showed haiku merging falsely would reverse the choice.

## V2-D108 — A deletion reaches the entities it orphans

**Context.** An `entity` row holds a name (`canonical_name`, `aliases`) and, since migration 26, a vector derived from
it — content in `I8`'s sense. No deletion path touched the table, and the runtime role holds no `DELETE` grant on it, so
`delete(scope='all')` removed every memory and left the name of every person those memories were about. The leak
scanner never looked at `entity`, which is why nothing reported it.

**Decision.** Migration 27 adds `delete_orphaned_entities(user, ids)`, a `SECURITY DEFINER` function with a pinned
`search_path`, gated by `deletion_in_this_transaction` exactly like `delete_exclusive_turns` (the transaction must have
appended a `deletion_request`; the marker GUC alone is refused). It deletes only entities no surviving memory cites as
subject; the `ON DELETE RESTRICT` foreign key stays the guarantee. `delete(scope='all')` erases every such entity of the
user, including when no memory matched; a `topic` deletion whose target is an entity id — the subject key of the claims
about it — erases that entity: per-person erasure. ~~A `memory` deletion leaves entities alone, because without a record
of which claim *mentioned* which entity the engine cannot tell a by-product from the user's declared structure; that
boundary is stated here rather than guessed at.~~ (**Superseded by `V2-D111` (migration 30):** `entity_mention` and `entity.origin` now record mentions vs declared structure explicitly, so `delete(scope='memory')` erases unreferenced `origin='mention'` entities.) The receipt and the audit row carry `deleted_entities`, a count.

**Tests.** `test_api_wiring_lifecycle_db.py`: `delete all` erases declared and mentioned names and audits only a count;
a topic deletion by entity id erases that entity and nothing else, and a memory deletion erases none; the function is
refused outside a deletion transaction even with the marker set. `test_migration_m26.py` covers 27's exact rollback.

## V2-D109 — An export manifest's identity is (user, epoch, digest), and it goes with its user

**Context.** Two defects found in review of PR #241. The manifest id was `exp-` + the digest, and `id` is a global
primary key: a second export of an unchanged read set at a *new* deletion epoch collided with the old row and returned
an id whose stored epoch was stale (never servable), and two users with identical readable sets (most simply both
empty) collided, so the second got `validation_error`. Separately, migration 25 promised that a manifest is removable
only by cascade from its user, while its guard raised on every `DELETE`, the cascade included.

**Decision.** `engine.export.snapshot_id_for` hashes `user \0 epoch \0 digest`. Migration 28 replaces only the guard
body: a `DELETE` is admitted when the parent `app_user` row is no longer visible (the cascade), and refused otherwise;
`UPDATE` and `TRUNCATE` are refused as before. Tests: an export after an epoch change with an identical read set is
servable and gets a new id; two users' empty exports get distinct ids; a direct delete is refused and the user cascade
succeeds; migration 28 rolls back to the recorded version-27 fingerprint.

---

## 7. Decisions closing post-W3.7 audit gaps and Phase completion (2026-11-25)

### V2-D110 — Structural artifacts record commit-time provenance in `artifact_provenance`

**Status:** adopted (2026-11-25). Migration 29. Closes `I2` remainder (#243).

`memory_version` enforced provenance since migrations 1/7/10, while `association`, `entity` (and its aliases), and
`entity_link` recorded no attribution to the evidence or API call that produced them. Migration 29 adds one generic
table `artifact_provenance` (`artifact_kind IN ('association', 'entity', 'entity_alias', 'entity_link')`), enforced at
commit by deferred constraint triggers (`enforce_artifact_provenance()`). When a cited `memory_version` is deleted,
`BEFORE DELETE` transitions matching rows to `source_version_id = NULL, origin_kind = 'source_deleted'` ("provenance
removed by deletion") with no dangling ID; deleting an `association`, `entity`, or `entity_link` cascades its
provenance rows (including `entity_alias` rows). Pre-migration rows are labelled `"unattributed (pre-migration)"`.

### V2-D111 — Entity mentions and `origin` enable per-memory entity erasure

**Status:** adopted (2026-11-25). Migration 30. Supersedes `V2-D108`'s memory-scope boundary (#247).

Migration 30 adds `entity.origin IN ('declared', 'mention')` and `entity_mention(user_id, version_id, entity_id,
resolution_stage)` inside the deletion closure. `delete(scope='memory')` counts removed mentions (`deleted_mentions`),
calls `delete_orphaned_mention_entities` to erase any `origin='mention'` entity left with zero surviving mentions and
zero `subject_entity_id` references (preserving declared entities and cited subjects), and the leak scanner adds Channel
12 (`channel_12_entity_registry`) covering `entity` and `artifact_provenance` aliases.

**Limitation (pre-migration entities).** Nothing recorded which entities were mention by-products before migration 30,
so `ADD COLUMN origin ... DEFAULT 'declared'` labels every entity that already existed `declared`: such an entity
survives `delete(scope='memory')` even when the memory being deleted was its only mention, and only
`delete(scope='all')` (or a `topic` deletion naming it) erases it. Pinned by
`tests/integration/test_migration_m30_entity_mentions_db.py::test_a_pre_migration_entity_survives_memory_deletion_and_is_erased_by_delete_all`.

### V2-D112 — Verification records pin their support and held-out artifact IDs

**Status:** adopted (2026-11-25). Migration 31 (#246).

`VerificationRecord` requires non-empty, caller-readable, ID-disjoint `support_artifact_ids` and `held_out_artifact_ids`.
Migration 31 adds `verification.artifact_pinning_status` (`pinned` vs `unpinned_pre_migration`) and child table
`verification_artifact(verification_id, user_id, role, artifact_id)` with `PRIMARY KEY (verification_id, artifact_id)`
and a deferred commit guard requiring both roles on every `'pinned'` record. `artifact_id` carries no foreign key so
append-only verification records outlive deleted artifacts (IDs are opaque and content-free).

### V2-D113 — Visibility writing, cross-user grant reads, and no `move` primitive

**Status:** adopted (2026-11-25). Migration 32. Closes `I7` (#244).

1. **Writer.** Tier C Scope operation `set_visibility(artifact_id, visibility)` (`api/handlers.py::Handlers.set_visibility`,
   `api/runtime.py::change_visibility`) and an optional `visibility` on `assert`, `ingest_episode`, `create_entity`
   and `link`. It is an ordinary `UPDATE` under row-level security, not a `SECURITY DEFINER` function: the own-user
   policy's `WITH CHECK` is what decides whether the caller may write the row, so a grantee can read but never change
   a shared artifact and no GUC-gated privileged path exists (`CLAUDE.md` rule 4). Unknown and foreign ids are one
   `forbidden`. One content-free `visibility_changed` audit row (artifact kind, new value) per change.
2. **Children follow their parent.** Migration 32 replaces the independent migration-11 scope policy on
   `memory_evidence`, `memory_derivation`, `memory_embedding`, `abstraction_stats` and `verification`, and adds one to
   `outcome_stats` and `procedure_shape`, with `scope.access.rls_parent_read`: a child row is readable exactly when
   its `memory_version` parent is. No policy reads a second copy of "is this shared" (the children's own migration-11
   `visibility` columns are left stale and inert; `V2-D123`), so a private artifact
   cannot leak its evidence and a shared one cannot render as `missing_provenance`.
3. **Reads.** Read paths no longer add `user_id = <caller>` to statements RLS already governs (`compile_predicate`'s
   `cross_tenant`, `read_handlers`, `provenance`, `expansion`, `statistics`, `staleness`, `outcomes`,
   `procedure_shape`); the scope read filter and forced RLS decide, and the result's scope paths feed the rule-6
   `scope_read` recorder. Without this a granted row was admitted by RLS and then filtered out by the application.
4. **Scope identity is immutable** (`ARCHITECTURE_V2.md` §5 rule 5), now enforced: `guard_scope_identity` refuses any
   change of `scope_path` on `memory_version`, `entity` and `association`. Nothing enforced it before.
5. **No `move`.** A faithful move must re-cite the original's evidence; the contract returns evidence as ids only
   (`V2-D37`), so no client composition can, and the sharing need is met without a copy. `API_V2.md` §5.2 says so;
   a server-side primitive is #258.

   *Re-evaluated 2026-11-26 (kept deferred).* No consumer needs it: the only client, the FastAPI app, writes and reads
   `/u/<user>/` alone, and the collaboration case is served by `set_visibility` plus a grant with one copy and one
   deletion surface. A `move` would add a second copy of the content (two deletion surfaces until the old one is
   retired), a new Tier C write path with its own provenance rows, and an idempotency key spanning two scopes — risk
   with no caller. It becomes needed when a client writes sub-scopes and must re-file memories between them, or a
   product needs to hand a memory to another namespace rather than share it; #258 holds the design and stays open.

Tests: `tests/integration/test_visibility_sharing_db.py`, `tests/integration/test_migration_m2.py::test_the_read_policy_is_the_library_expression_on_every_scoped_table`.

### V2-D114 — Abstraction ledgers are derived from evidence and gated on verification

**Status:** adopted (2026-11-25). Closes `I15` (#245) under Amendment 3 authorization.

Adds engine evidence derivation (`engine/abstraction_ledger.py`, `engine/refutation.py`) plus **two early drafts of
W4 instruments**, landed only because `I15` and `I11` need a bound and a run to be meaningful, not as a claim that
W4 has started (`ROADMAP_V2.md` §7 marks what each still owes):

* `evaluation/stats/abstraction_metrics.py` — a draft of `SD-001`'s three-state metrics (`o = s + k + u`,
  `confidence_lower = 1 - CP_upper(k + u, o)`, coverage, `FAR_τ`, Brier, ECE with a hand-computable reference).
  W4 still owes `SD-001`'s acceptance: `precision@verified` against a base rate, per-facet reliability, and the
  hand-computed constants in `STATISTICS.md` §10.
* `evaluation/stats/admission.py` and `dependence.py` — drafts of `SD-002` and `SD-008`: the §4.2 predicate composed
  with Holm at the specified **independent** levels (per-abstraction one-sided 97.5%, FWER 0.05), refusing to admit
  without refutation and negative-control evidence, and gating on the dependence report's conservative bound. W4
  still owes `SD-002`'s equivalence pinning with the engine's `soundness.admission` (landed: 
  `tests/test_admission_and_dependence.py::test_the_evaluation_admission_predicate_equals_the_engine_admission_predicate`)
  and §5.4.3's third condition (`SD-005`), which no module here implements.
* `evaluation/suites/admission_calibration.py` — **not** `C-012` and **not** gate `G4`. It checks the admission
  rule's size and the separation of its controls against a known generative model, per stratum, and never calls the
  engine. `C-012`'s null suite — no-pattern streams through `infer` and `verify` — remains W4 work, and `G4` is not
  claimed here.

An earlier version of this record described `evaluation/suites/null_suite.py` as a "G4 null-suite proof
`FAR_τ <= 0.05`". That was wrong: the module is a Monte-Carlo self-check of the predicate, and the boundary stratum's
near-zero rate follows from Clopper–Pearson's coverage rather than from anything the engine did. The module is renamed
`admission_calibration.py`, its `g4_passed` field is `calibration_passed`, and its per-stratum gate is on the observed
rate with the CP bound reported beside it (at 20 scenarios a perfect zero bounds at 0.168, so a gated bound needs 72
scenarios per stratum and is a W4 decision). `infer` derives `(s, k, u, o)` directly from the named
evidence sources and structured predicate (refusing zero/contradictory ledgers), runs active refutation probes, and
`verify` enforces `soundness.admission.assess` for `'verified'` outcomes while sealing `confidence`,
`confidence_method_id`, `counter_absence_basis`, and `closed_over_hash`.

### V2-D115 — Fast-lane vs slow-lane cardinality and slot ownership

**Status:** adopted (2026-11-25). Fixes `#248`.

**Who decides.** The *application* decides `cardinality` and `semantic_slot` for the writes it makes; the engine stays
slot-vocabulary-free and the write path model-free. Flipping `assert` unconditionally to `cardinality='single'` with
no slot would collapse every unrelated message onto `COALESCE(NULL, 'statement')` under `no_bitemporal_overlap` (`I5`),
so a message is single-valued only when its slot can be named too, from a closed vocabulary (`residence`, `employer`,
`name`). A false `multi` only leaves an old belief standing; a false `single` would retire a true one, which is why
precision wins. Open-domain extraction stays in the slow lane. *How* the app recognises a slot was first an English
regular grammar; `V2-D121` replaced it with a semantic, language-independent classifier.

**What the product renders.** `facts` also carries recorded turns (`kind='episode'`). The app renders only non-episode
facts under "Known facts"; turns go last under "Earlier conversation (verbatim; may be outdated; not confirmed facts)",
deduplicated against rendered claims. So a superseded value is never presented as a known fact, and an assistant turn
never is either.

**Turn identity.** The agent resends the whole session history on every turn, so a message's idempotency identity is
`(session, position, role, text)`: a resend replays, and the same words said again later are a new claim that wins
the slot back.

**Engine fix found on the way.** `engine/gate.py::existing_relation` matched a duplicate on content alone, including a
superseded value reissued as a historical fact, so Berlin → Paris → Berlin left Paris current and returned a receipt
for a version that was never written. A duplicate must now be valid at the candidate's `valid_from`.

Tests: `tests/test_memory_slots.py`, root `tests/integration/test_app_engine_boundary.py` (Berlin → Paris renders one
known fact; the no-slot control keeps two; whole-history resends), `tests/integration/test_host_e2e_db.py::test_returning_to_an_earlier_single_valued_value_supersedes_the_current_one`.

### V2-D116 — No plaintext memory content cache in the FastAPI client

**Status:** adopted (2026-11-25). Fixes `#249`.

Removes the 60s TTL content cache in `app/services/memory_engine.py::search` so deleted, retracted, or unshared memories
can never be served from client memory after an engine change. Documents client-side caching in `THREAT_MODEL.md` and
`CHANNELS.md`.

### V2-D117 — Transport-layer rate limits and concurrency bounds on `host/`

**Status:** adopted (2026-11-25). Fixes `#251`.

Adds a per-principal token-bucket rate limiter (off by default, `rate_limit_rps = 0.0`) and an `asyncio.Semaphore`
concurrency guard (`pool_max >= max_inflight + 1` validated at startup) in `host/limits.py` and `host/service.py`,
returning `429 resource_limit` with static codes only so one caller cannot exhaust all pool connections.

### V2-D118 — Asset-derived judge floor verification and exact power analysis

**Status:** adopted (2026-11-25). Fixes `#254`.

Replaces censored `N=1` unanswerable max-logit reporting with full-set evaluation (`evaluation/retrieval/judge_floor.py`
and `scripts/check_judge_floor.py`), adds live-asset floor tests in `tests/test_hybrid.py`, and provides exact
binomial/two-proportion power calculations (`evaluation/stats/power.py`).

### V2-D119 — Operational backup/restore policy, maintenance scheduler, and metrics

**Status:** adopted (2026-11-25). Completes `#255` (`A-014`).

Records the backup/restore policy in `deploy/README.md` (dumps require `BYPASSRLS`, separate secret backup of
`CME_PROVENANCE_KEY`/`CME_DELETION_RECEIPT_KEY`, `public` extensions bootstrapped before schema restore, runtime role
re-provisioned post-restore; pre-deletion backups resurrect rows deleted after the backup until retention expiry because
no automated deletion-log replay ships). Adds `worker/maintenance.py` + `scripts/run_maintenance.py` + systemd timer for
scheduled `expire`/`purge_working`/`rebuild_index`, dependency-free Prometheus `/metrics` (`host/metrics.py`) without
tenant/content labels, and a real `pg_dump`/`pg_restore` rehearsal (`scripts/rehearse_restore.py`). The resurrection
caveat is narrowed by `V2-D122` (the erasure journal).


### V2-D120 — A delegating service token acts for the end user it names

**Status:** adopted (2026-11-25). Found while closing #250; prerequisite of #248/#249/#250's end-to-end proof.

**The defect.** A host token maps to exactly one principal (`host/credentials.py`, "one entry per service principal"),
and the engine's namespace rule makes that principal's `/u/<principal>/` the only writable scope. The FastAPI app holds
one token and sends `scope=/u/<end user>/` for every user, so every write and read for every end user but one was
`forbidden` — and the app logs and swallows memory failures, so a multi-user deployment silently had no memory. Nothing
crossed the app↔engine boundary in a test, so nothing noticed. Separately, nothing provisioned an end user's `app_user`
row, which every tenant table references.

**Decision.** A token may be marked `"delegate": true` in `CME_HOST_TOKENS`. A delegating token **must** name the end
user in the envelope's top-level `principal`; that end user is then the caller for every engine rule (own namespace,
RLS `app.user_id`, read set, rate/concurrency limits). Rules, all static refusals and all tested
(`tests/test_host_credentials.py`, `tests/integration/test_host_e2e_db.py`):

1. A token without the flag that names a `principal` is `forbidden` (`delegation_not_permitted`): delegation is a
   property of the token, never of the request.
2. A delegating token without `principal` is `validation_error` (`principal_required`): an implicit fallback would write
   an end user's memory into the service's namespace.
3. `principal` must satisfy the user-segment grammar of `/u/<id>/` (`principal_invalid`), so it cannot forge a path.
4. A principal that owns a token of its own is `forbidden`: delegation never reaches an operator's or another
   service's namespace.
5. The delegated credential keeps the token's tier and is never `admin`; a delegating token cannot itself be `admin`
   (refused at start-up).
6. The host creates the end user's `app_user` row idempotently (`INSERT … ON CONFLICT DO NOTHING`), as that user under
   the restricted role, before the call — the `app_user` policy admits only the session's own id.

**Trust consequence (stated, not hidden).** A delegating token is a credential for *every* end user's memory at its
tier, exactly as the app's own database credential is for its own tables. It is a service secret, rotated like one;
`THREAT_MODEL.md` records it. Per-user tokens were rejected: the app would have to mint and store a secret per user, and
the engine would gain an issuance API, for no isolation the RLS boundary does not already provide.


### V2-D121 — The app's slot classifier is semantic and language-independent

**Status:** adopted (2026-11-26). Amends `V2-D115` (which keeps *who* decides: the application).

**The defect.** `V2-D115` shipped an English regular grammar (`I live in X`, `I moved to X`, a capitalised value). An
Arabic or mixed-language user ("أنا ساكن في برلين", "انتقلت إلى باريس", "I live in Berlin, بس دلوقتي انتقلت لـ Paris")
never got a single-valued claim, so their moves left two "known facts" — #248 fixed for one language only. More
keyword rules per language would repeat the defect in every language not yet written.

**Decision.** Understanding is delegated to a language model; acceptance stays deterministic and language-independent
(`app/services/memory_slots.py`):

1. One structured-output call (`SlotExtraction`: slot, temporal status, whether the subject is the speaker, the value
   as written, confidence) on the app's LLM service, with Langfuse tracing. The prompt names no language's words.
2. `decide` accepts it only when the slot is in the closed vocabulary, the status is `current` (a past value, plan,
   hypothetical, denial or question is not), the subject is the speaker, the confidence is at least 0.8, and the value
   is **grounded**: it has at least two letters or digits and occurs verbatim in the message after NFKC, case folding
   and removal of combining marks. A translated, transliterated or invented value is refused, in any script. No
   word-boundary rule: it would break Arabic, where a preposition attaches to the name (ببرلين).
3. Only the newest user message of an `add()` call is classified: each earlier one was the newest of an earlier call and
   replays by idempotency key, so one model call per turn. Up to four preceding messages are context, so a correction
   ("No, I meant Paris") is readable; the value must still come from the newest message.
4. Every failure — no model, timeout, invalid answer, any exception — is `multi` with no slot. `MEMORY_SLOT_CLASSIFIER=off`
   selects that behaviour outright. The app logs the proposed slot and outcome, never the message or the value.

**Alternatives rejected.** Per-language keyword grammars (the defect, multiplied). Classifying in the engine's slow lane:
the engine has an LLM extractor, but the product path enqueues nothing and the worker serves a fixed user list, so it
would be a larger change with no gain in what is decided; `V2-D115` keeps the decision in the app. Embedding similarity
to prototype sentences: it cannot tell "I live in Berlin" from "I lived in Berlin in 2010", which is the distinction that
matters.

**Evidence.** `tests/test_memory_slots.py` (each refusal in Arabic, English and mixed text);
`tests/integration/test_app_engine_boundary.py` (Arabic and English Berlin → Paris → Berlin and a mixed-language
correction through the real host, one model call per turn); `tests/live/test_memory_slots_live.py` against real models
(opt-in), 39 cases: Arabic, English, mixed, corrections, historical facts, plans, questions, third parties,
Berlin → Paris → Berlin, and — since `V2-D124` item 9 — messages that state a slot *and* something else, plus reported
speech. Measured on the final commit: `gpt-6.2-sol` **39/39, no false `single`, no failed call**. An earlier 34-case
run measured `gpt-5.6-luna` through the production path and `gpt-6.2-sol`, `claude-sonnet-5-5` and `claude-haiku-5-5`
through the gateway at 34/34 each. The two Claude models are **unmeasured** on the final commit: the gateway answered
`400` with "this organization has been disabled", which is a provider condition, not a misclassification (the run
counts failed calls separately and fails on them). The production path is unmeasured here because this checkout's
`OPENAI_API_KEY` is a placeholder.
Model choice matters less than the checks: a failed or malformed call is `multi`, never a false `single`, and the
classifier no longer lets another model answer for the configured one (`V2-D124` item 8).

**Cost and limits.** One small structured call per user turn, off the response path (`add()` runs as a background
task). The model's tense judgement is the remaining precision risk; the grounding check bounds what it can invent, not
whether it misreads a past statement as current. The live evaluation is opt-in because CI has no model credential.


### V2-D122 — The erasure journal: a restore re-applies the deletions its backup predates

**Status:** adopted (2026-11-26). Narrows the resurrection caveat of `V2-D119`; `D-ENG-04` is unchanged.

**The risk.** A backup taken before a deletion holds the deleted rows, and restoring it also rolls back the
`deletion_request` rows that recorded the erasure. The rehearsal measured it: the deleted memory is readable again and
the restored database holds no trace of the deletion. A right-to-erasure promise that one routine restore undoes is not
one.

**Decision.** `deletion_request` is already an append-only, content-free log (user, scope, HMAC digest of the target,
per-user epoch). It is copied *out of the database* and replayed after a restore (`deletion/journal.py`,
`scripts/erasure_journal.py`):

1. **Export** (`cme-erasure-journal.timer`, every five minutes, and before any restore): every completed request is
   merged into a JSON-lines file. The merge never drops an entry, so an export from a restored, older database cannot
   shrink the journal; two different entries under one id or one `(user, epoch)` are refused. The export role needs
   `BYPASSRLS` (checked: under forced RLS a role without it reads zero rows and would write an empty journal that
   looks valid) and is granted `SELECT` on `deletion_request` only. The export runs as its own transient Unix user
   (`DynamicUser=yes`) from a `root:root 0600` environment file, so the worker's user can read neither the file nor the
   process's environment; an exclusive lock keeps two exports from interleaving their read-merge-write. The header
   carries a one-way key check of the deletion key, never the key. A replayed request differs from the original only
   in `requested_at` and merges into it.
2. **Replay** (a step of the restore procedure, before the host serves traffic): per user in epoch order, every
   journalled request the restored database lacks is re-applied through `DeleteHandlers.replay` — the engine's own
   deletion path, the deployment's handler configuration, the restricted role and the user's own RLS — keeping the
   original request id, digest and epoch, so a second replay finds it and does nothing. The target is found by
   recomputing the digest over the user's restored keys (`memory_key`; for `topic` also `subject_key` and entity ids):
   the journal never holds a plaintext target (`T-29`). A target the backup never held is recorded with nothing to
   erase. An `all` request needs no target (its digest may cover a `target_ref` the caller sent, which selects
   nothing), so only a resolved target is checked against the digest. A journalled user the backup predates is re-provisioned first, exactly as the host provisions one, so their
   epochs are taken and their next deletion continues after them. A replay under another deletion key is refused
   before it writes (it would otherwise match nothing and
   record every deletion as done); a restored database holding a *different* request at a journalled epoch is refused
   as divergent.

**Alternatives rejected.** Per-user encryption keys destroyed on deletion (crypto-shredding) would make old backups
unreadable for that user and is the stronger design, but it touches every content column, index and embedding; it is
the follow-up if the recovery-point residual below is unacceptable. A journal table in a second schema of the same
database: zero recovery point when only the engine schema is restored, but lost with the cluster, and a second schema
outside the migration runner's catalog checks. A plaintext or encrypted target in the journal: it would keep, outside
the database, the very name the digest exists to hide.

**Residual (stated).** Deletions after the newest journal copy the operator still has (the export interval plus the
off-host shipping delay), and a journal lost together with the database. Retention expiry is re-applied by running
maintenance after the restore.

**Evidence.** `tests/integration/test_backup_restore_db.py` over a real `pg_dump`/`pg_restore`: three deletions after
the backup (a resolved memory deletion, one the backup never held, a user's `delete all`); before the replay the
deleted memories are readable and the leak scanner finds residuals in 6 measured channels; after it, nothing deleted is
readable, the scanner finds 0, the kept memory survives, the request ids are the originals, and a second replay does
nothing. Life after the restore is exercised too: the next export merges the replayed requests (no conflict), a user the
backup predated deletes again at the next epoch, and the grown journal replays as all present. The independent final
review of #259 found the two lifecycle defects these steps now pin (a replayed `requested_at` that made every later
export conflict, and an unprovisioned user that made the next epoch collide). `tests/test_erasure_journal.py` pins the file, the merge and the key check.


### V2-D123 — Hardening from the independent review of #259

**Status:** adopted (2026-11-26).

1. **Guarded rollbacks refuse a role that cannot see every row.** Thirteen rollback scripts guard a data loss with
   `IF EXISTS (SELECT … FROM <forced-RLS table>)`. Run by an ordinary owner (the deployment `test_migration_owner.py`
   supports), forced RLS hides every row, the guard passes on an empty view and the rollback drops the data it was
   written to protect. Each now refuses first with `rollback_requires_bypassrls`, inside the same transaction.
   (`tests/integration/test_migration_owner.py::test_a_guarded_rollback_refuses_an_owner_that_cannot_see_every_row`
   fails without it.)
2. **An unmeasured queue is not a zero.** `/metrics` counted `job` rows on a connection without a tenant scope; for a
   login without `BYPASSRLS` forced RLS returned nothing and the host rendered `cme_queue_dead_letters_total 0`. The
   host now reports `cme_queue_metrics_available 0` and omits the queue families when it cannot measure them; no
   privileged function was added to measure them.
3. **The rate limiter's map is bounded.** With a delegating token every end user is a principal; buckets that have
   refilled to the burst (indistinguishable from absent ones) are swept at an amortised high-water mark.
4. **Migration 32's header said the child visibility columns could not disagree with the parent.** They do, and are
   inert because no policy reads them; the header now says so, and `test_migration_m2.py` pins every child policy to
   `rls_parent_read`.


### V2-D124 — The second independent review of #259: what it found, and what stays open

**Status:** adopted (2026-11-26). Covers findings from four parallel read-only reviews (deletion/recovery, security and
RLS, schema and invariants, app and instruments) plus the parent's own reading. Every fix below landed with a test that
fails on the code before it.

**Deletion and recovery (`V2-D122` completed).**

1. **A journal export is now durable.** `os.replace` was not followed by an `fsync` of the directory, so a crash could
   leave the *older, shorter* journal behind — silently losing exactly the newest deletions the feature exists to hold
   (`tests/test_erasure_journal.py::test_the_written_journal_is_durable_in_its_directory`).
2. **A truncated journal is refused, not read as a shorter one.** The header's `entries` count is now checked
   (`journal_truncated`): an off-host copy cut at a line boundary used to load as valid.
3. **A journal whose header carries no key check is never stamped with the current key** (`journal_key_check_missing`).
4. **The replay's success is honest.** `scripts/erasure_journal.py replay` exits `1` on any unresolved entry
   (`unresolved_entries_remain`) unless the operator passes `--accept-unresolved`, and on any divergent user
   (`divergent_users_remain`); a missing journal file is `journal_missing`, not an empty success. An unresolved entry
   is *indistinguishable* from a replay under the wrong deletion key, and exit `0` made both look like a clean restore.
5. **One divergent tenant no longer costs every other tenant its replay.** `ReplayReport.divergent_users` names them,
   the rest are applied, and the run is not a success.
6. **The host requires `CME_DELETION_RECEIPT_KEY`.** `engine.deletion.default_receipt_key()` invents a random key per
   process, and a digest made under it can never be matched by a replay or by an idempotent retry after a restart.
   `settings_from_environment` now refuses (`cme_deletion_receipt_key_required`), `HostSettings` keeps both deployment
   keys out of its `repr`, and re-provisioning a journalled user verifies the row exists rather than assuming it
   (`journal_user_provision_failed`).
7. **What is still not covered is stated, not hidden** (`THREAT_MODEL.md` T-46): the journal records the *target* of a
   deletion, not the evidence turns it erased, so a turn ingested before a backup whose extraction completed after it
   can be re-extracted once the worker runs. The replay's non-zero exit and the restore procedure (worker starts only
   after a clean replay) are what keep that from happening silently; recording evidence-turn digests needs a schema
   change and is recorded as the follow-up.

**App (`V2-D121` amended).**

8. **The classifier runs one model and never falls back.** `llm_service.call` walks the whole registry after a provider
   error, so an untested model could decide that a past fact is current. `call(fallback=False)` is new, the classifier
   uses it, and its model resolves at start-up to `MEMORY_SLOT_CLASSIFIER_MODEL` or `DEFAULT_LLM_MODEL`.
9. **A message that states more than one fact is `multi`.** The claim's content is the whole message, so
   "I live in Cairo and I work at Siemens" labelled as a residence update would retire the employer fact the next time
   the user moved. The prompt now says one message/one attribute, and the live set gains multi-fact negatives.
10. **`DELETE /messages` no longer makes a repeated message replay.** A message's identity was (session, position,
    role, text) and clearing the history restarts the positions; `session.history_epoch` (migration
    `c4a7f1e29b03`) is part of the identity now.
11. **An unrecognized `MEMORY_SLOT_CLASSIFIER` value disables the classifier**: only `llm` enables it.

**Schema and invariants.**

12. **Migration 29's two `SECURITY DEFINER` trigger functions** rewrite `app.user_id`; `EXECUTE` is revoked from
    `PUBLIC` and `app.user_id` is *reset* rather than set to `''` when it was unset (an empty tenant silently matches
    nothing for the rest of the transaction). They take no receipt gate on purpose: re-derivation deletes and
    re-inserts the version, and item 13 is what keeps that attribution.
13. **Re-derivation no longer erases `I2` attribution.** The tombstoning trigger fires on the rebuild's own `DELETE`,
    so an entity, alias or link citing a live, active version rendered "provenance removed by deletion" for ever.
    `rederive.py` carries `(id, origin_kind, source_version_id)` across the rebuild and restores it.
14. **`I11`'s pinning status is not caller-settable.** `verification.artifact_pinning_status` admitted
    `'unpinned_pre_migration'` from any inserter, which skipped the artifact check entirely; a `BEFORE INSERT` trigger
    refuses it. The value that predates the migration is created by `ADD COLUMN … DEFAULT`, which fires no trigger, so
    upgrading a legacy database still works; the one path it refuses is a *data-only* restore of pre-migration
    `verification` rows into an already-migrated schema, which `deploy/README.md` says to do at the dump's own version
    (or with `pg_restore --disable-triggers`). Restorability wins over re-enforcing an invariant the product path
    already enforces in code — but the value would otherwise be a one-token switch for skipping `I11`.
15. **`I7` now has a behavioural alarm.** `verification_artifact_scope_read` is the one cross-user read policy the
    migration-2 pin cannot cover (it delegates two hops); it is pinned against its own expression and against its own
    `visibility` column, and the sharing test asserts that a grantee's `provenance` view of a shared row returns
    *named* references rather than opaque handles (a `200` was true either way before).

**Instruments and contracts.**

16. **The admission levels are independent, as §5.4.3 specifies.** `closed_testing_admit` passed its FWER level into
    the per-abstraction CP bound, so at FWER 0.05 the `(s=29, k=0, u=0)` row of §4.2 was admitted — the exact
    convention error `r3` exists to prevent. The bound is always one-sided 97.5%; `fwer_alpha` (default 0.05) only sets
    Holm. `assess_admission` also **requires** refutation and negative-control evidence (two of its five gates used to
    pass by default), refuses a dependence report that describes another ledger, and gates on that report's
    conservative bound (`SD-008`), and a new grid test pins it to the engine's `soundness.admission` over 1 352
    tuples.
17. **The calibration study is not `C-012` and not `G4`.** `evaluation/suites/null_suite.py` became
    `admission_calibration.py` with `calibration_passed`; `V2-D114` is amended with what it actually proves and what
    W4 still owes. Its per-stratum gate is on the observed rate with the CP bound reported beside it, because at 20
    scenarios a perfect zero bounds at 0.168.
18. **An export's manifest cannot state a scope its ids exceed.** `_narrow` kept the *grant's* entry while the manifest
    recorded the *requested* selector, so a grantee exporting a sub-scope received the whole grant under a narrower
    label. Entries are anchored at the requested path.
19. **`x-request-id` is unique and is logged**, and transport refusals answer the contract's `validation_error` with a
    static `detail` instead of six undefined codes.

**Known and deliberately not done.** The restore reconciliation cannot see a data-only or PITR restore, because both
keep the relation that is its identity (**#262**); it is stated in `deploy/README.md` and tracked, not fixed. Two
neighbours of that gap are no longer in this list: a backup is restorable only with a journal at least as new
(`V2-D126`, **#263**), and the drill-down stores hold capacity per principal instead of evicting across them
(`V2-D127`, **#261**).
A
delegating token re-creates a tenant row that an operator removed, because `delete` erases memory, never tenancy
(T-42). `read_artifact_provenance`/`read_entity_provenance` have no caller in `src/`: the structural read model is
tested but not yet an operation (W4+). Rate limiting stays off by default, and `THREAT_MODEL.md` §3 now says the
default mitigation for timing inference is the concurrency bound rather than a rate bound.

### V2-D125 — A deletion's whole live effect survives a restore (`T-46`)

**Decision.** A deletion request records, in its own transaction, keyed digests of what it erased *besides* its target:
the evidence turns of the versions it physically deleted (for `all`, every turn of the user), and the idempotency
identities of the writes whose results it erased, with each result's engine-generated id. The erasure journal carries
them (`format 2`); a replay inserts them again, erases every marked turn the restored database still holds (with its
jobs), and from then on a client's resend under an erased idempotency key answers with the erased result instead of
writing the claim again. A stored turn a deletion marked but could not erase is flagged (`erased_turn`) and no
extraction ever runs on it. A database the journal has not been replayed into is refused by both the worker and the
host (`restore_reconciliation`).

**Why.** `V2-D122` journalled the *target*, and the target is not the effect. Two paths re-created the deleted memory
from what an older backup still held, and neither is exotic: the app resends its whole history every turn, and a turn
whose extraction completed after the backup keeps its queued job in it. Without the marker the resend is a *first*
write in the restored database, and the worker has a turn it can read. The old guard was procedural (replay before the
worker, non-zero exit on an unresolved entry), and it cannot be complete: `recorded_unresolved` is exactly the case
where the backup lacks the target, which is precisely when the surviving evidence matters most.

**Consequences for the design.**

* **The marker is a digest, never the identity.** The journal leaves the host, so a marker is an HMAC of the identity
  under the deployment's deletion key, bound to the user and the kind (`T-29`). A wrong key makes the replay refuse
  before it writes anything, because the file's key check is compared first; it cannot silently produce no-ops.
* **The flag is a plaintext id, and that is deliberate.** `erased_turn` holds a turn's own id, which `turn` already
  holds, and goes with the turn by cascade (migration 33). The worker therefore needs no deletion key -- the key stays
  in the host and the journal unit -- and a marked turn cannot be re-extracted by any code path.
* **A migration never stamps itself.** The first version of this fix recorded the identity inside migration 33, and a
  review caught what that costs: `deploy/README.md` restores a pre-33 dump *at its own version and then migrates it*, so
  the migration ran in the restored database, stamped its own new relation, and marked a restored copy reconciled with
  no replay at all -- the exact state the check exists to catch. Only `MigrationRunner.apply` on a schema created from
  nothing stamps -- and `reset` does too, because it drops every row and what it leaves is a schema created from
  nothing; an upgraded or restored database is stamped by the
  replay, or by `replay --mark-reconciled` when an operator asserts there is no journal to replay.
* **Reconciliation is an identity, not a timestamp.** `restore_reconciliation` records its own relation OID: a
  `pg_restore` re-creates the table, so the row it carries names another relation and the check fails closed. A clone
  (`CREATE DATABASE ... TEMPLATE`, which every test fixture makes) keeps relation OIDs and is a reconciled copy of a
  reconciled database, so the check passes there; that is why the database OID is not part of the identity.
* **A replay of an unresolved target still erases.** The request's markers are applied before the closure is walked,
  so the one deletion whose target the backup never held is not the one that leaves the most behind.
* **Live behaviour does not change for running jobs.** A deletion still purges only jobs that are not `running` and
  leaves the epoch guard to fence those; a replay purges running jobs too, because a restored lease belongs to no live
  worker and the worker refuses to run before the replay.

**Evidence.** `tests/integration/test_t46_restore_erasure_db.py` (both resurrection paths, each with an in-test control
that removes the marker and reproduces the resurrection: the worker re-extracting the claim, and the resend writing it
again); `scripts/rehearse_restore.py` over real `pg_dump`/`pg_restore`, which seeds a turn and its job before the dump,
extracts and deletes the claim after it, and asserts after the restore and replay that the turn and its job are gone,
the worker finds nothing to extract, and the claim does not return (`t46_markers_exported`,
`t46_turn_erased_by_replay`, `t46_worker_cannot_re_extract`); `tests/test_erasure_journal.py` (format 2, a format-1
journal still readable, a malformed marker refused, the markers part of a request's identity);
`tests/integration/test_backup_restore_db.py` runs the rehearsal in CI;
`test_migrating_a_restored_database_does_not_mark_it_reconciled` rolls a schema back to 32 and migrates it forward, the
way a restored dump is upgraded, and requires that it stay unreconciled until a replay or an explicit assertion.

**Cost, measured.** Two statements per deletion (the marker insert and the turn bookkeeping: 3.0 ms for 50 markers,
0.4 ms to flag a turn), one indexed lookup per extraction (the flag) and one per idempotent write (the marker probe:
0.125 ms → 0.186 ms median at 50 000 markers and 50 000 idempotency rows). The reconciliation check costs one query per
*claimed* job, not per idle tick: the worker claims first and puts the job back when the database is not reconciled, so
an idle worker on a healthy database is exactly as cheap as it was. The journal grows by the digests a deletion erased,
which is bounded by the closure it already traversed. A replay resolves an entry's `turn` digests against the user's
stored turns (the key is only in Python), so its cost is O(requests x turns) per user, alongside the deletion closure it
already walks for every entry; that is an offline recovery step measured in seconds, not a request path.

**Limit, stated.** A data-only restore into the same relation, and a physical (PITR) restore, keep every OID and are not
detected by `restore_reconciliation`; the procedure replays after any restore regardless. Markers are exported with the
journal, so their recovery point is the journal's.

### V2-D126 — Pair the backup with a journal recovery point (#263)

A replay requires `--restored-from-backup-at` and refuses with `journal_older_than_backup` when the journal header's
`exported_at` predates that recovery point. `exported_at` is the instant the export **read** the database, captured
before its `SELECT` and not the instant the file was written: an export that took a second would otherwise claim a
recovery point a second newer than the rows it holds, and a backup starting inside that second would be paired with a
journal that never saw its deletions. The bound is conservative in the safe direction -- it can only refuse a pair the
relabelled header would have accepted. Both instants in a refusal are re-formatted from the parsed values, never echoed
from the operator's text. Equal timestamps are accepted. The comparison is the file's own recovery
point and never a principal's newest `requested_at`: a principal who has deleted nothing since before the backup is not
evidence of a lost deletion, and refusing on it would block a restore the pair actually covers, leaving
`--mark-reconciled` -- which replays nothing -- as the only way through. The report includes the newest journal epoch
and newest completed deletion epoch present before replay for each user, so requests applied to a database that never
held their targets remain visible as unresolved recovery work.

### V2-D127 — Per-principal capacity for process-local read stores (`T-47`, #261)

**Decision.** `ResultStore` and `CostStore` keep principal-specific ordered buckets while retaining one global capacity ceiling. **The reservation is one resident entry per principal, not every entry a principal has ever held.** Capacity is reclaimed in this order: a principal that needs room sheds its *own* surplus first (oldest entry); when only an incumbent holds surplus, admission of a newcomer reissues the largest surplus, so a principal's first query can cost another principal its oldest surplus entry while leaving that principal its reservation; only when every resident principal holds exactly one entry is the least-recently-used principal evicted whole. Lookup and report semantics remain principal-scoped, and expired entries are still removed on the existing read and write paths. Resolving a handle and reading a *validated* `cost` report both count as use of that principal and refresh its position; a rejected report does not, because a request answered `validation_error` must not change which other principal is evicted next, and a report never creates a bucket, so reading cannot make the store grow. `CostReport` carries a non-constructible `report_scope: "process"` so the numbers say what they cover (`API_V2.md` §4.11).

**Why.** A flat global LRU lets one caller's sustained query volume remove another caller's live drill-down handle and cost history. Principal buckets preserve availability across callers while the global ceiling prevents the number of principals from multiplying memory without bound. The stores remain process-local under `V2-D83` because result snapshots are answer-context material and cost rows are read-path telemetry, not durable memory or a ledger.

**Evidence.** `tests/integration/test_refine_db.py::test_a_principal_burst_preserves_another_principals_handle_and_cost_window` drives two principals through one wired engine with capacity three. `tests/test_refine.py::test_another_principal_burst_preserves_a_live_handle_and_total_capacity_stays_bounded` and `tests/test_cost.py::test_another_principal_burst_preserves_costs_and_the_global_ceiling` assert the isolated burst and ceiling. `tests/test_refine.py::test_an_evicted_handle_still_resolves_as_unknown` preserves the T-11 indistinguishability property. `tests/test_refine.py::test_admission_reissues_a_surplus_slot_before_evicting_a_principal_and_keeps_one_entry_each` and `tests/test_cost.py::test_a_new_principals_admission_trims_a_surplus_count_and_keeps_one_record_each` pin the reclamation order, including the admission exception that the burst tests do not reach, and `tests/test_refine.py::test_admission_reclaims_from_the_largest_surplus_not_the_oldest_holder` and `tests/test_cost.py::test_admission_takes_a_record_from_the_largest_surplus_not_the_oldest_holder` pin that the donor is the *largest* surplus (resident counts `(2, 4)` admit a third principal as `(2, 3, 1)`, never `(1, 4, 1)`). `tests/integration/test_refine_db.py::test_an_evicted_handle_is_the_same_answer_as_an_unknown_one` drives a capacity eviction through `Engine.call` and asserts the same `forbidden` as a handle that never existed. `tests/test_cost.py::test_a_cost_report_states_the_process_scope_it_actually_covers` pins the additive field, its constancy and its unconstructibility, `tests/test_cost.py::test_a_cost_report_counts_as_principal_use_and_never_creates_a_principal` pins the report-as-use and no-growth rules, and `tests/test_cost.py::test_a_rejected_cost_report_does_not_count_as_use` pins that a `validation_error` does not reorder eviction. `deploy/README.md` documents the per-process scope and reset behavior.
