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
| `D-16` `N_D` | In force; **revisit** if abstraction channels change per-scenario leak probability | |

---

## 4. Open decisions after the review
| ID | Question | Owner | Needed before |
|---|---|---|---|
| ~~V2-D13~~ | ~~Association-types cap per artifact (default suggestion: 32)~~ — **resolved (r7)**, see §5 | engine lead | W1 |
| V2-D14 | Retention default per profile and abstraction expiry (suggest: 180 d for preferences, 365 d for constraints and decision patterns, 14 d for context; re-verified rather than renewed) | product + methods | W5 |
| V2-D16 | Whether `S8` (fact/hypothesis labelling detectability) runs at all; ethics determination required first | supervisor | W7 |
| V2-D17 | Public API stability policy: semver with deprecation windows vs "experimental" prefix for Tier B | engine lead | W7 |
| V2-D30 | `tau_confidence` and `tau_coverage` defaults (suggest 0.90 / 0.60, frozen in the pilot like v1's `D-09`-`D-14`) | methods | W4 |
| V2-D31 | `mu`/`kappa` estimation window for hierarchical pooling: all users, or a rolling window to handle population drift | methods | W4 |
| V2-D32 | Whether abstraction expiry triggers automatic re-derivation or a user prompt in the PDT profile (privacy-relevant in the personal profile) | product | W7 |
| V2-D34 | `K` (dissent persistence window) confirmation: default 7 reflection cycles, frozen pre-test | methods | W4 |

`V2-D15` is **resolved** by `V2-D23` (conformal is for intervals only; admission uses CP bounds).
`V2-D13` is **resolved** in §5 (r7). The other rows remain open; r7 did not decide them.


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
(`test_gaps_closed_in_r7_are_enforced_with_tests`, `test_known_v1_gaps_are_reported_not_silently_passed`).
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
