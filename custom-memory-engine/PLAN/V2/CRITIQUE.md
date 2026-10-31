# Critique of v1 — What Must Change and Why

**Method.** This is a read of the implementation, not of the proposal's rhetoric.
Claims about v1 behaviour are cited to `file:line` in `src/custom_memory_engine/`.
Where v1 is genuinely strong, it is said so, because preserving an asset is as much
an architectural decision as deleting one.

---

## Part 1 — Structural findings (these force redesign)

### F1. The type system is one column doing three jobs

```python
# src/custom_memory_engine/engine/transactions.py:23
type MemoryLayer = Literal["episodic", "semantic", "opinion", "persona"]
```

`layer` simultaneously encodes:

- **kind** — is this a time-stamped experience, or a decontextualised proposition?
- **method** — was it extracted, consolidated, or inferred? (Note `"summary"` is
  not a layer value; `REFACTORING.md` §6 documents that a summary is a `semantic`
  row with `MEMORY_DERIVATION.derivation_type='summary'`, i.e. method is stored in
  a *different table* from the one that claims to describe the memory's nature.)
- **retention intent** — `persona` in practice means "durable, high-level".

The requested taxonomy (working / short-term / episodic / semantic / procedural /
long-term) cannot be expressed, and — critically — **should not be expressed as six
sibling enum values either**. Those six words mix:

| Axis | Values |
|---|---|
| **Kind** (what the content *is*) | episode, claim, procedure |
| **Retention** (how hot/costly to keep) | working, session, durable |
| **Lifecycle** (where in epistemic process) | observed, asserted, inferred, contested, retired |

A coding agent's "this repo's CI fails because of fixture leak in conftest.py" is a
*claim*, with *durable* retention, *asserted-then-verified* status, and *procedure*
adjacency (it suggests an action). One enum value can never describe it.

**Consequence:** any implementation that adds `working` and `procedural` to the
`CHECK` constraint (schema `001_full_schema.sql:53`) reproduces the defect at
larger scale. Query code becomes a 6-way dispatch and every new capability reopens
the constraint.

---

### F2. Epistemic status is not first-class, and inference bypasses the gate

The gate requires turn evidence:

```python
# src/custom_memory_engine/engine/gate.py — check 1
# evidence_span must occur in a user-authored turn
```

Derived rows cannot satisfy it, so consolidation writes them through a different
path (`commit_initial_version`, documented in `REFACTORING.md` §6 "Derived versions
must not go through the gate"). Therefore, today:

> **An inferred conclusion is a claim that skipped validation.**

That is the exact inversion of the required guarantee. For a personal assistant the
inversion is tolerable — almost all content is user speech. For an engine whose
selling point is "reflection and pattern discovery," it is fatal: the most
speculative artifacts in the system would be the least validated.

Missing entirely: a hypothesis state, a support set with explicit counts, a
counter-support channel, an inference method record, calibrated confidence, and a
verification status. `valid_from_basis IN ('explicit','inferred')` (schema:55) is
the only hint, and it describes *time*, not *belief*.

---

### F3. Abstraction has nowhere to live

```python
# src/custom_memory_engine/consolidation/consolidator.py
MAX_DERIVATION_DEPTH = 3
ConsolidationLayer = Literal["semantic"]
```

`plan_consolidation` enforces one `(subject, predicate)` per artifact, per the
branch-`(C)` coverage contract. That is *compaction*: N memories about the same fact
become one memory about the same fact. It is correct for its purpose.

It cannot express: "across 40 sessions this user consistently rejects terse answers
in code-review contexts, but not in casual chat." That statement is relational
(spans subjects), conditional (has a scope), quantitative (has support), and
falsifiable (has counterexamples). There is no table, no confidence, no
counterexample link, and no expiry.

**This is the single largest gap between v1 and the stated vision.**

---

### F4. Evidence is chat-only

```sql
-- schema/001_full_schema.sql
turn_id text NOT NULL REFERENCES turn (id),
role    text NOT NULL CHECK (role IN ('user', 'assistant'))
```

A coding agent's evidence is a diff, a stack trace, a test run, a file. A research
agent's is a paper span. Neither can be evidence today without being pasted into a
fake `TURN`. `TurnRecord` in the harness (`evaluation/harness/models.py`) has the
same restriction.

---

### F5. Scope is single-tenant-personal

`user_id` is on every table, RLS is keyed on it, and `TemporalQuery` takes exactly
one `user_id`. There is no project, workspace, task, thread, or agent scope. Multi-
agent memory and per-repository memory both require abusing the tenant key, which
then breaks isolation claims (Invariant 7).

---

### F6. Archival conflates belief and retention

`status IN ('active','archived')` (schema:53) is used for two unrelated purposes:
"the system no longer believes this" and "we don't need this hot." The V4
archival precondition `(S)∨(C)` (Invariant 10) is a correct *safety* fix for the
first meaning. But reflection needs to retire abstractions for the *second* reason
without touching belief status — and there is no such mechanism. The result is
either unbounded growth of derived artifacts or misuse of `archived`.

---

### F7. Retrieval is one shot, and there is no graph

```python
# engine/retrieval.py — dense + ts_rank_cd + RRF(k=60), exact per-user cosine
```

Correct and measured (P50 27 ms @ 2,000 candidates, `REFACTORING.md` §4). But it is
a single query over a flat candidate set. There is no:

- iterative retrieve→refine loop;
- relation traversal ("what superseded this, what depends on it, what contradicts it");
- drill-down from an abstraction to its supporting instances;
- cost-aware context assembly (only a token budget with rank-order preservation).

`MEMORY_DERIVATION` is the only edge type, and it is internal lineage, not knowledge
structure. "What did I already try that failed" — the archetypal coding-agent memory
query — is unanswerable.

---

## Part 2 — What v1 gets right (preserve verbatim)

| Asset | Why it matters for V2 |
|---|---|
| **Bi-temporal model** (`valid_range` × `recorded_range`) | The hardest part of memory is time. This is correct, formalised, and SQL-enforced. It survives unchanged into V2. |
| **Archive precondition `(S)∨(C)`** (Invariant 10) | A real bug found and fixed with a rule that generalises: *never let a retention action silently remove an answerable fact*. V2 extends the rule instead of replacing it. |
| **Deletion closure + channel enumeration + positive control** | The strongest contribution in the repo. V2 must extend it upward into the cognition layer, where leakage actually becomes dangerous. |
| **Archive/replay extraction cache** (`evaluation/harness/replay_cache.py`) | Makes ablations causally interpretable. Non-negotiable for V2's multi-condition experiments. |
| **Script-first suite generation** (`research/benchmarks/own_*/generator.py`) | The only honest way to measure precision of an abstraction. V2 extends this to a *generative-process* generator. |
| **Statistics module** (cluster bootstrap, sign-flip permutation, Holm, Clopper–Pearson) | Correct, tested, reusable as-is. |
| **Frozen substrate baseline + 1,374 passing tests** (full CI scope) | Becomes the regression wall. |

---

## Part 3 — What the vision *cannot* deliver (and must not promise)

Intellectual honesty about the ceiling, because the plan's credibility depends on it:

1. **"Understanding" is not a measurable outcome.** Reflection can produce
   *falsifiable* abstractions and *measured* precision. It cannot be shown to
   "understand" the user. Every claim in `RESEARCH_PROGRAM_V2.md` is phrased as
   precision/recall/calibration, never as understanding.

2. **Inferred conclusions will sometimes be wrong, and the engine cannot always
   detect it.** The only defensible design is: (a) never render an inference as a
   fact, (b) attach its support, (c) let it be refuted and retired, (d) measure the
   error rate rather than claim it is zero.

3. **Generality across domains will be partially demonstrated, not proven.** We can
   show one engine serving two genuinely different profiles (conversational + one
   tool-using agent) with zero engine forking. We cannot prove it serves all agents.
   The observable is: *no schema change, no engine code change, only a profile.*

4. **Pattern discovery over open-ended behaviour is an AI-complete problem.** The
   plan therefore scopes it to *statistically grounded, falsifiable, verified-on-held-out-data*
   abstractions. Free-form LLM "insight" generation is explicitly out of scope and is
   treated as a failure mode to be measured, not a feature.

5. **Bitemporal + graph + vector in one store has a real write-cost ceiling.** We
   accept it. Single-store transactional deletion is worth more than write
   throughput at this stage, and the measured numbers show headroom at current
   scale.

---

## Addendum — defects found in the V2 draft itself

Recorded so the review trail is complete; each was found by auditing V2 against its own
stated invariants.

| # | Defect | Severity | Fixed in |
|---|---|---|---|
| A1 | `scope.path LIKE 'S%'` matches `/u/4/` against `/u/42/…` — a cross-tenant read. | **Critical** | `ARCHITECTURE_V2.md` §5, `MA-011` |
| A2 | `ENTITY` described as "canonical referents" with no tenant scoping — an existence/attribute oracle across tenants. | **Critical** | §5.2, `V2-D25` |
| A3 | Tier B stated as unable to "change lifecycle directly" while `infer` necessarily creates `inferred` lifecycle — self-contradictory contract. | High | §8.1 |
| A4 | Retrieval listed three channels and never said where `contested` content goes. | High | §9.1 |
| A5 | `verification_status` included `stale`, which is a function of the provenance closure and can therefore disagree with it — the disagreement a deletion produces. | High | §9.2, `V2-D24` |
| A6 | "Six artifacts" counted four memories plus structure with no discriminator. | Medium | §2.1 |
| A7 | `E-037`/deletion-through-cognition was scheduled after cognition existed, so the reflection runtime would have to be built twice. | Medium | `ROADMAP_V2.md` §13 |
| A8 | Retention was declared orthogonal to answerability, but `working` expiry would silently remove claims. | Medium | `V2-D10` retention floor |
| A9 | Counter-support, ECE, and conformal were each specified in a form that is statistically invalid for sparse temporal data. | **High** | `STATISTICS.md` §8 |
| A10 | The PDT/user model appeared in no research family and was cuttable at ladder step 4. | **High** | `USER_MODEL.md`, `V2-D26` |
