# Open Decisions and Blockers

**Status:** Audit deliverable. Not approval, and not a decision.
**Audit:** r6 — Plan Corrections & Final Readiness Audit.
**Baseline audited:** `main` @ `490904d` (plus the nine open PRs read as proposals only).
**Rule applied here:** no resolution is chosen to unblock implementation. Where the
correct behaviour is not already settled by an approved normative statement, the item
stays **open** and is recorded as requiring Project Owner approval.

Each entry states: the conflicting requirements, the boundary affected, the affected
decisions and issues, the consequence of each resolution, whether a safe fail-closed
behaviour exists meanwhile, and the latest point at which the decision must be made.

---

## Summary

| ID | Severity | Kind | Blocks | Latest decision point |
|---|---|---|---|---|
| `B-01` | **Critical** | security defect | `MA-011`, `E-011`, G0b, G1 | before `MA-011` is frozen (G0b) |
| `B-02` | **High** | missing architecture element | `E-011`, `E-014` | before `E-011` implements `with_visibility` |
| `B-03` | **High** | contradiction | `MA-013`, `E-017` | before `MA-013` freezes (G0b) |
| `B-04` | Medium | incomplete normative statement | `MA-012`, `MA-015` | before `MA-012` freezes (G0b) |
| `B-05` | Medium | contradiction | `E-010`, `E-020` | before `M1`/`M7` are written |
| `B-06` | Medium | ambiguity | `E-015`, `C-021` | before `E-015` is merged |
| `B-07` | Medium | undefined behaviour | `E-013`, `E-015` | before `M4` is written |
| `B-08` | **High** | gate unsatisfiable as written | W1 entry | before G0b is declared |
| `B-09` | Medium | missing gate | W6 test-split use | before any confirmatory split runs |

---

## B-01 — `LIKE` wildcard expansion in scope selectors (Critical)

**Conflicting requirements.**
- `ARCHITECTURE_V2.md` §5 rule 4 (approved, normative): RLS enforces the read predicate with
  `path_with_prefix LIKE ANY(...)`.
- `ARCHITECTURE_V2.md` §5 (approved, normative): "Trailing separator is mandatory… without it
  `/u/4/` matches `/u/42/…` — a cross-tenant read." This treats the separator as sufficient.
- `ARCHITECTURE_V2.md` §11 `I7` and §5.2: cross-tenant isolation, no existence oracle.
- `V2-D27` (ADOPTED): trailing separator on scope paths.

**Reproduced, on PostgreSQL 16.** Not a theoretical reading:

```sql
-- '/u/a_b/' is alice's selector. It also matches a DIFFERENT tenant:
SELECT path FROM (VALUES ('/u/a_b/'), ('/u/aXb/')) v(path)
 WHERE path LIKE ANY (ARRAY['/u/a_b/']);
--   /u/a_b/
--   /u/aXb/        <-- cross-tenant row returned
```

`_` (one character) and `%` (any run) are pattern metacharacters. A trailing separator
prevents the *prefix-extension* leak; it does **not** prevent the *wildcard* leak. Where
ids legitimately contain `_` — `user_1`, `bench_0`, tenant slugs — the leak is live.
`starts_with(path_with_prefix, prefix)` and an escaped pattern both return only the true
row; both were verified.

**Boundary affected.** Tenant isolation (`I7`), the RLS layer, and the public read API.

**Affected decisions and issues.** `V2-D27`; `MA-011` (#54), `MA-010` (#53),
`E-011` (#62); gate G0b; gate G1. `THREAT_MODEL.md` in PR #86 already raises this as
`T-02` and proposes the same fix, so the work is in review, not undiscovered.

**Consequences of each resolution.**
1. *Replace `LIKE ANY` with `starts_with(..., prefix)` (or an escaped pattern) and reject
   `%`, `_`, `\` and control characters in scope ids at creation.* Closes the leak at both
   layers. Costs: a small text edit to §5 rule 4 and a new id-validation rule at the API
   boundary. `text_pattern_ops` indexing still applies to `starts_with`, which is
   index-usable in PostgreSQL.
2. *Keep `LIKE`, escape patterns at every call site.* Correct but fragile: safety depends on
   every future call site escaping, and RLS is defence in depth, so a missed site is a
   silent cross-tenant read.
3. *Restrict ids to `[a-z0-9-]` only, no escaping.* Makes the class of bug unreachable, but
   is a product constraint on ids and would invalidate v1-shaped ids like `bench_0` unless
   migration M2 rewrites them.

**Fail-closed behaviour while unresolved.** Yes. Treat a scope id containing `%`, `_`, `\`
or a control character as **inadmissible**: reject the write, and return an empty read set
for a selector containing one, rather than compiling it into a pattern. This closes the
hole without deciding between (1) and (3).

**Owner's decision required?** Yes for the normative text of §5 rule 4. Resolutions (1) and
(2) are compatible with the approved architecture and would be a **controlled correction**;
(3) changes an approved data constraint and needs an approval amendment.

**Latest decision point.** Before `MA-011`'s design is frozen at G0b. After that, W1 code is
written against a leaky predicate.

---

## B-02 — No storage or lifecycle for project/shared access lists (High)

**Conflicting requirements.**
- `ARCHITECTURE_V2.md` §5 rule 2 (normative): selectors include
  `with_visibility(path, {project, shared})`.
- `ARCHITECTURE_V2.md` §5 rule 3 (normative): a `project` row is returned only to a caller
  "whose ACL grants the caller membership".
- Migration `M2` adds `visibility` with values `private | project | shared`.

**Reproduced.** The v1 schema has **no** ACL, membership, principal-grant or sharing table
(`001_full_schema.sql`: no `acl`, `member`, `principal_*`, or grant-storage construct). No
migration in `ARCHITECTURE_V2.md` §12 creates one, and `MA-017`'s coverage matrix —
which asserts every structural element has an owning task — has no row for it. So §5
rules 2 and 3 reference a table that neither exists nor is scheduled.

**Boundary affected.** Read authorization, RLS, and the meaning of `visibility`.

**Affected decisions and issues.** `V2-D05`; `MA-011` (#54), `E-011` (#62), `E-014` (#65);
`MA-017` (#60) coverage matrix.

**Consequences of each resolution.**
1. *Add an `ACL`/`MEMBERSHIP` structural table plus a migration and a task.* Makes
   `project`/`shared` implementable. Costs: a new architecture element, a new migration
   (`M2` or a new `M9`-class addition), a W1 task, and non-trivial RLS work — RLS policies
   must consult a join, which is where cross-tenant bugs live.
2. *Defer `project`/`shared` to a later wave; implement `private` only in W1.* Simplest and
   honest. Costs: §5 rules 2–3 describe unbuilt behaviour; the selector enum must reject
   `with_visibility` at the API until then, and `E-023`/`A-011` must not assume sharing.
3. *Treat `project`/`shared` as scope-path conventions with no membership check.* Rejected:
   it silently converts "shared with project" into "readable by anyone who names the path",
   which contradicts `I7`.

**Fail-closed behaviour while unresolved.** Yes. `private` semantics are fully specified and
safe: a `private` row is readable only when the selector path equals the row's own scope
path. Implement `private`; make `with_visibility(path, {project, shared})` a **rejected
selector** returning an empty read set until storage exists.

**Owner's decision required?** Yes — options (1) and (2) differ in scope, and (1) adds an
approved architecture element.

**Latest decision point.** Before `E-011` implements `with_visibility` (W1 execution). The
selector can be rejected safely until then.

---

## B-03 — Provenance rendering can disclose an unreadable scope (High)

**Conflicting requirements.** Two approved, normative statements:
- `ARCHITECTURE_V2.md` §9.3: cross-scope provenance is rendered as "an opaque reference
  (**id + scope + type**)" when the caller cannot read the source scope.
- `ARCHITECTURE_V2.md` §11 `I17`: "a provenance reference to an unreadable scope exposes
  **no content and no existence oracle**."

**Why this conflicts.** Rendering the source's *scope path* to a caller who cannot read that
scope discloses the existence and structure of another tenant's scope — precisely the
oracle `I17` forbids. The scope is the one field that makes the reference non-opaque: a
tenant id, a project name, or a count of distinct scopes is obtainable by deriving across
the boundary. §5.2 sets the same precedent for entities ("no 'does entity `e` exist'
oracle across tenants"), which is the stronger and more specific rule.

**Boundary affected.** Cross-scope read authorization; `I7`, `I17`.

**Affected decisions and issues.** `MA-013` (#56), `E-017` (#68), `MA-010` (#53);
`MA-015` (#58) tier matrix (provenance is a Tier B read).

**Consequences of each resolution.**
1. *Omit `scope` when unreadable: render `(opaque_id, type)` only; include `scope` solely
   when the caller can read it.* Consistent with `I17` and with §5.2. Costs: a text edit to
   §9.3; the provenance bundle is slightly less informative in the cross-scope case, which
   is the point.
2. *Render a stable salted hash of the scope instead of the path.* Preserves correlatability
   ("these two memories come from the same hidden scope") without disclosing identity.
   Costs: a new field and a key-management decision; correlation is itself a weak oracle
   unless the salt rotates.
3. *Keep `scope` and relax `I17` to forbid content but permit scope.* Rejected: it makes the
   invariant false as written and permits tenant enumeration.

**Fail-closed behaviour while unresolved.** Yes — (1) is strictly the more conservative
behaviour and requires no new mechanism.

**Owner's decision required?** Yes, because it edits approved normative text. (1) is a
controlled correction; (2) adds a mechanism and is an amendment.

**Latest decision point.** Before `MA-013` freezes at G0b, since `E-017` renders against it.

---

## B-04 — `V2-D07`'s five-call list contradicts the approved API surface (Medium)

**Conflicting requirements.**
- `DECISIONS_V2.md` `V2-D07` (ADOPTED): Tier B uses "only: `query`, `infer`, `verify`,
  `retract`, `stats`."
- `ARCHITECTURE_V2.md` §8.1 (normative): **23** operations, of which **17** are Tier B.
- `ARCHITECTURE_V2.md` §8 intro: the five-call draft "was incomplete… **every operation a
  tier needs appears here**".

**Reproduced.** `retract` is not an operation in §8.1; the operation is `retract_own`. The
five-call list omits `ingest_episode`, `ingest_artifact`, `refine`, `get`, `provenance`,
`assert`, `refute`, `link`, `unlink`, `link_entity`, `record_outcome`, `cost`.

**Why this matters.** §8 self-describes the five-call list as superseded, so the *invariant*
("cognition is a client, not a privileged component") is intact and approved. What is stale
is the enumeration. If read literally, `V2-D07` would forbid Tier B from ingesting episodes
— which W4/W5 cognition cannot function without.

**Boundary affected.** The Tier B contract; G7's permission audit.

**Affected decisions and issues.** `V2-D07`; `MA-012` (#55), `MA-015` (#58), `A-017`.

**Consequences of each resolution.**
1. *Restate `V2-D07`'s enumeration to point at §8.1, keeping the invariant and the
   three prohibitions (no delete, no adjudicate, no grounded write without evidence).*
   No behaviour change; removes a contradiction.
2. *Enforce the five calls literally.* Rejected: it prevents Tier B from ingesting the
   episodes it reasons over, contradicting W4/W5 task definitions.

**Fail-closed behaviour while unresolved.** Yes — §8.1 is the operative contract and is
what implementations must follow; the five-call list is not actionable.

**Owner's decision required?** Yes for the text of an ADOPTED decision. (1) is a controlled
correction that preserves intent.

**Latest decision point.** Before `MA-012` freezes at G0b.

---

## B-05 — `archived` cannot be a retention value (Medium)

**Conflicting requirements.**
- `ARCHITECTURE_V2.md` §2.1 (normative): `retention ∈ {working, session, durable}`.
- `ARCHITECTURE_V2.md` §12 `M7`: "`archived` status retained as a **retention** value for
  compatibility."
- `DECISIONS_V2.md` (line 35): "`archived` survives as a *retention* value only."
- Migration `M1` gives no status→`lifecycle`/`retention` mapping for v1 `archived`.

**Reproduced.** The retention enum has exactly three values and `archived` is not among
them, so `M7` cannot be implemented as written. v1 `status` has five values
(`candidate`, `conflict_pending`, `rejected`, `active`, `archived`) while v2 `lifecycle` has
five *different* values (`observed`, `asserted`, `inferred`, `contested`, `retired`), and
`M1` maps only `episodic`/`semantic`/`opinion`/`persona` layers — not these statuses.

**Boundary affected.** Lifecycle semantics; the `facts`/`hypotheses` channel membership
rule (`retired` is excluded from both); retention cost policy.

**Affected decisions and issues.** `V2-D01`, `V2-D04`; `E-010` (#61), `E-020` (#71),
`MA-016` (#59).

**Consequences of each resolution.**
1. *Map v1 `archived` → `lifecycle='retired'` and leave `retention` untouched (default
   `durable`); keep `archived` only as a read-compatibility alias in the legacy `status`
   column.* Treats `archived` as a belief value, matching §2.1. Costs: a compatibility view
   or generated column; `archived` rows become invisible in both channels, which is what
   `retired` means.
2. *Add `archived` as a fourth retention value.* Rejected: it reintroduces the v1
   conflation of belief and cost that `CRITIQUE.md` F6 and `V2-D04` exist to remove.
3. *Map `archived` → `retention='working'`.* Rejected: `working` implies TTL and
   purge-survival semantics that a v1 `archived` row does not have.

**Fail-closed behaviour while unresolved.** Yes — `M1` can map `archived` → `retired` and
leave `retention='durable'`; nothing is deleted by that choice, and it preserves v1
auditability.

**Owner's decision required?** Yes for the `M1`/`M7` text.

**Latest decision point.** Before `M1` and `M7` are written (`E-010`, `MA-016`).

---

## B-06 — Is the CP bound applied at inference, or at verification? (Medium)

**Conflicting requirements.**
- `ARCHITECTURE_V2.md` §2.2 (normative flowchart): `G2` includes
  "partially identified CP upper bound … ≥ τ" and on failure routes to
  `REJ["rejected, with reason code"]` — the artifact is **not created**.
- `STATISTICS.md` §4.2 (returns "hypothesis only" for below-τ rows) and
  `ARCHITECTURE_V2.md` §2.3 (`inferred → inferred (verified=true)` is a later transition)
  describe a below-τ artifact that **exists** and renders in `hypotheses`.

**Why this conflicts.** If `G2` rejects at inference, no below-τ abstraction is ever stored,
so the `hypothesis only` rendering row and the whole `hypotheses` channel for unverified
abstractions have nothing to display. If below-τ artifacts are stored, `G2`'s `fail → REJECT`
branch is wrong. The same word, "admission", is used for two different moments.

**Boundary affected.** Soundness policy; channel membership; W4 instrumentation validity
(`own_P` precision is measured over verified artifacts).

**Affected decisions and issues.** `V2-D19`, `V2-D02`; `E-015` (#66), `C-021`, `SD-002`,
`E-017` (#68).

**Consequences of each resolution.**
1. *Bound gates `verified`, not creation.* Candidates are created as `inferred`
   (`verified=false`) and always render in `hypotheses`; reaching `verified=true` requires
   the bound to clear τ **and** held-out verification. Costs: `hypotheses` grows, and
   `G2`'s failure branch becomes "created, unverified" rather than "rejected".
2. *Bound gates creation (`G2`).* Below-τ candidates are not stored. Costs: the
   `hypothesis only` rendering path is dead code for abstractions, `STATISTICS.md` §4.2's
   table loses its subject, and refutation attempts cannot accrue over time to eventually
   admit — so a claim can never become admissible by accumulating evidence.
3. *Both: a permissive create-time floor plus the τ bound for `verified`.* Adds a second
   threshold that `STATISTICS.md` does not define, and `V2-D30` is already open on
   thresholds.

**Fail-closed behaviour while unresolved.** Yes — option (1) is fail-closed for the
knowledge claim (nothing unverified reaches `facts`) while preserving evidence accumulation.

**Owner's decision required?** Yes. Two approved documents imply different things.

**Latest decision point.** Before `E-015` is merged, because `ground()`/`assess_soundness()`
encode the choice.

---

## B-07 — Grounding rules for evidence sources other than turns (Medium)

**Conflicting requirements.**
- `ARCHITECTURE_V2.md` §2.2 `G1a`: "evidence exists in a **declared source type**".
- Migration `M4` permits `SOURCE(type='external')`.
- The v1 gate (`engine/gate.py::evidence_supported`) verifies spans occur in
  **user-authored turns** — it has no notion of an external source.

**Reproduced.** `GateInput.evidence_turns` is `tuple[tuple[role, content], …]`. There is no
path by which an external source (a file, a tool result, a URL capture) is checked for
support. So "declared source type" is unimplemented for anything but `turn`.

**Boundary affected.** Grounding policy `G1`; `I1′`; the meaning of "grounded".

**Affected decisions and issues.** `V2-D02`, `V2-D04`; `E-013` (#64), `E-015` (#66).

**Consequences of each resolution.**
1. *Define `ground()` per source type, with an explicit predicate for `external` (e.g. the
   span must occur in the fetched content whose `content_hash` matches).* Enables the
   coding-agent profile's non-turn evidence. Costs: a new grounding predicate and its tests;
   content-hash canonicalisation must be fixed (see B-07's dependency on `M4`).
2. *Restrict `G1` to `turn` and reject `external` grounding in W1.* Simplest and safe.
   Costs: `E-021` (coding profile) cannot ground non-turn evidence, so W2 is affected.

**Fail-closed behaviour while unresolved.** Yes — `ground()` fails closed: an unrecognised
source type is rejected, never passed.

**Owner's decision required?** Yes — (2) has a cross-wave cost that only the owner can accept.

**Latest decision point.** Before `M4` and `E-015` are written.

---

## B-08 — `V2-D13` is a W1 blocker that is not in any gate, and G0b is unsatisfiable meanwhile (High)

**Conflicting requirements.**
- `DECISIONS_V2.md` §4: `V2-D13` (association-types cap per artifact, suggested default 32)
  is **open**, owner "engine lead", "needed before **W1**".
- `ROADMAP_V2.md` §3 `G0b`: "Every issue raised in the review has either a design decision
  recorded in `DECISIONS_V2.md` or a written, tested rejection", and "`MA-017`/`MA-018` run
  green".
- `ROADMAP_V2.md` §1: W1's entry gate is `G0b`.
- `E-014` (#65) implements `ASSOCIATION` in W1 and needs the cap.

**Reproduced.** G0b requires every review item to be resolved; `V2-D13` is an unresolved
review item and is not listed in W1's readiness statement. Separately, `MA-017` (#60) is
required green at G0b, but its own specification states it *fails* while `M5` and `M7` have
no owning task (see B-05 and the register's C-series findings). So **G0b cannot be declared**
without either assigning owners or recording explicit `deferred` markers.

**Boundary affected.** W1 entry; association integrity; the plan's own gating.

**Affected decisions and issues.** `V2-D13`; `E-014` (#65), `MA-017` (#60), `MA-016` (#59).

**Consequences of each resolution.**
1. *Owner confirms the cap (or a different value), and `M5`/`M7` get owners or explicit
   `deferred` markers.* G0b becomes satisfiable. Costs: a decision plus a small plan edit.
2. *Move `V2-D13` to "needed before W3" and implement associations without a cap in W1.*
   Costs: unbounded association growth is a deletion-closure and performance risk (`I5`).
3. *Declare G0b met with `MA-017` red.* Rejected: it makes the gate non-falsifiable.

**Fail-closed behaviour while unresolved.** Partially — `E-014` can enforce a hard cap
constant with a recorded rationale, which is safe (a resource limit, not a security bound),
but it is still a decision taken by the implementer.

**Owner's decision required?** Yes — both the `V2-D13` value and the `M5`/`M7` disposition.

**Latest decision point.** Before G0b is declared, i.e. before W1 starts.

---

## B-09 — No V2 gate certifies test-split integrity before W6 (Medium)

**Conflicting requirements.**
- `PLAN/CONTRIBUTING.md` §2: confirmatory test splits may run **only once**, after gates
  `J5` (`RL-026`), `J7` (`RL-094`) and `J8` (`RL-100`) are signed off.
- `TRANSITION_AUDIT.md` §3.4: `RL-094` is "replaced by gates G0–G8"; `RL-100`–`105` fold
  into W6 `R-010`.
- No V2 document states **which** gate carries `J7`/`J8`, or that a V2 gate certifies split
  freezing at all.
- `ROADMAP_V2.md` §7 G5 consumes the `own_P` **test** split.

**Reproduced.** G5's confirmatory analysis requires an unburned test split, but nothing in
G0–G4 certifies that new V2 suites (`own_P`, `own_U`, null) were frozen under the same
discipline as `RL-026`.

**Boundary affected.** Research validity of F3/F4 claims.

**Affected decisions and issues.** W4/W6 trackers, `C-011`, `UM-001`, `R-010`.

**Consequences of each resolution.**
1. *Add an explicit split-freeze certification to G4 (or a `G4c`).* Costs: one gate clause.
2. *State that `J7`/`J8` remain in force and are prerequisites for W6.* Costs: a mapping
   statement in `TRANSITION_AUDIT.md`.

**Fail-closed behaviour while unresolved.** Yes — no confirmatory split is run before W6,
and W6 must not start until the mapping is recorded.

**Owner's decision required?** Yes for the gate/mapping text.

**Latest decision point.** Before W6 performs any confirmatory analysis.
