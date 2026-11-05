# Own Suite `C` — Coding-Agent Benchmark: Script-First CI-Failure Patterns (`E-022`)

**Target Requirement:** **`G2` generality proof / dual-profile parity denominator** (`PLAN/V2/ROADMAP_V2.md` §5 `E-022` row, §12 cut 1; `PLAN/V2/ARCHITECTURE_V2.md` §13 coding column).
**Unit of Evaluation:** Independent synthetic developer/repo scenario in a repo scope (`dev = 20` scenarios, `4` per family; `test = 50` scenarios, `10` per family; one probe per scenario).
**Vocabulary:** Engine terms only — `scenario`, `episode`, `probe`, `split`, `seed`, `control`; artifact axes `Kind`, `Lifecycle`, `Verification`, `SOURCE` types `turn | tool | file | event` (`E-021`, `E-013`); channels via `channel_of` (`E-017`).
**License:** MIT (`D-26`; independently authored templates, fictional repos, high-entropy fictional canaries; zero `LoCoMo`/`LongMemEval` text reuse; nothing resembling real repository content).

---

## 1. The five scenario families (ticket §5)

Scoring-only labels (gold answers, expected offers, expected channels, `is_near_miss_control`, evidence ids) are never handed to a system under test — the `PreparedCase.case` / `PreparedCase.gold` split (`E-022` §4.6). The generator freezes them in the same JSON files; any consumer must strip them before dispatch.

### 1.1 `prior_attempt_recall` — "what did we try before?"

**Scripted content:** a CI failure (`tool` episode, error signature recorded), a fix attempt (`file` diff episode), and its failure outcome (`event` episode, run id canary). A later session asks: *what fix did we already try and what happened?*
**What it proves:** the store holds non-turn evidence (`E-013`) and retrieval finds it in a repo scope (`E-011`).
**Metric:** `prior_attempt_recall_correct` — binary per probe; hypothesis is correct iff `exact_or_normalized_match(hypothesis, gold_answer).matched` (instrument local and deterministic, reusing the pinned normalization, no provider judge), and a failure to answer (abstention) scores `0`.
**Trade-off bounds:**
- trivially-failing (never retrieves/never answers): `0.0` on every probe;
- trivially-fabricating (answers whatever is nearest without evidence): `0.0` on every probe (matched-gold containment must hit the scripted phrasing);
- the maximum achievable by a perfect retriever: `1.0`.

### 1.2 `procedure_reuse` — recurring CI failure, stored procedure

**Scripted content:** a CI failure with a stored recipe recorded as one `kind="procedure", lifecycle="asserted"` artifact (`turn` source; renders in the `procedures` channel, `E-017`), plus precondition bindings observed in the trigger (`tool` evidence). A later session fails again with the **same** signature and the **same** precondition bindings.
**What it proves:** a `procedure` artifact is stored, matched on its recorded preconditions, and would be re-rendered.
**Metric:** `procedure_reuse_correct` — binary per probe; the suite scorer (precondition matcher, §2.2) must mark the procedure `offered`, equal to `expected_offer.should_offer=True`.
**Trade-off bounds:**
- trivially-failing (offers nothing, e.g. keywords-only refusal): `0.0`;
- trivial similarity matcher (word-overlap trigger search, no precondition gate): `1.0` here — this family alone cannot distinguish a similarity matcher from a correct one.

### 1.3 `near_miss_control` — same symptom, different cause

**Scripted content:** the sibling scenario's stored procedure for the same error-signature text; the recurrence has **one precondition binding changed** (planted divergent cause), a `kind="abstraction", lifecycle="inferred"` distractor explicitly suggesting to re-apply the old recipe, and the probe asks the *same question in the same phrasing* as the reuse sibling.
**What it proves:** procedures are matched on recorded preconditions, not on symptom similarity (`is_near_miss_control: True` marks the stratum, the `own_D`/`own_I` control convention).
**Metric:** `near_miss_false_offer` — count of probes where the system under test offers the procedure; expected `0`. Reported with its one-sided 97.5% Clopper–Pearson upper bound (`STATISTICS.md` §4.2; zero-event bound via `zero_leak_upper`, opportunities counted per probe). The generator records `false_offer_cp_975_upper_bound` for each split's own count directly in the JSON payload (e.g. the test split's bound covers its recorded opportunity count as a count, never a bare rate claim).
**Trade-off bounds:**
- a similarity-only matcher scores `0.0` here (it offers where it must not) and `1.0` on the reuse siblings: the paired strata are what separate it from a correct matcher;
- a trivially-refusing matcher (never offers) scores `1.0` here — and is exposed by `procedure_reuse` where it scores `0.0`;
- a correct precondition matcher scores `1.0` on both.

### 1.4 `repo_fact_recall` — build invariants across sessions

**Scripted content:** one build invariant (`ticket`-canary-stamped) recorded once as a `kind="claim", lifecycle="asserted"` artifact; a later session asks for it. Interference: one unrelated observed episode.
**What it proves:** claims survive across sessions inside a repo scope.
**Metric:** `repo_fact_recall_correct` — same deterministic containment instrument as §1.1.
**Trade-off bounds:**
- trivially-failing: `0.0`;
- trivially-fabricating: `0.0`;
- perfect: `1.0`.

### 1.5 `abstention_control` — no supporting episode

**Scripted content:** plausible distractor episodes and no episode supporting the question.
**What it proves:** the suite distinguishes "not known" from "remembered wrongly".
**Metric:** `control_abstention_correct` — binary per probe; correct iff the response abstains (`detect_abstention` from the pinned deterministic instrumentation).
**Trade-off bounds:**
- a trivially-answering system (fabricates with highest-rank filler): `0.0` on every probe;
- a trivially-refusing system: `1.0` here, at the price of `~0` on the three answerable families.

### 1.6 Reporting convention (ticket §3.5, `STATISTICS.md` §4.2)

Every reported number is a **count** plus a **one-sided 97.5% Clopper–Pearson upper bound on the failure rate** computed from the same manifest's opportunity count; no p-values, no comparative performance claims (`PLAN/CONTRIBUTING.md` §2; `validation/mechanical_checks.py` §6). The zero-expected strata (near-miss false offers) carry their exact zero-event bounds in the frozen JSON (`zero_leak_upper(n_opportunities)`).

---

## 2. Suite construction

### 2.1 Template catalog

Four independent, fictional repo/pattern entities (cycled deterministically by `((idx - 1) // 5) % 4`); each carries the error signature, the recorded trigger preconditions, the stored recipe, and the **planted alternate cause** (a changed precondition binding) for the `near_miss_control` siblings. Per-scenario content is assembled from these tables plus a per-scenario high-entropy canary drawn from a fixed non-ambiguous letter set, `own_I` style; the source `span` is always a verbatim substring of its own `content`. No text is composed by free-form string concatenation.

### 2.2 The precondition matcher and the declared similarity strawman

- `offer_by_preconditions(probe, library)`: the procedure applies iff **every** recorded trigger precondition binding appears unchanged in the probe's observed bindings. This is the suite's scoring instrument.
- `offer_by_similarity(probe, library)`: the declared **trivial strawman** — offers the most token-overlapping procedure if its trigger overlap ratio exceeds `0.5`. It exists only as a negative control; every near-miss probe in the frozen data deliberately matches its sibling's trigger strings, so the strawman offers on every one of them and only a precondition matcher refuses. Tests assert the strawman is beaten on every near-miss probe.

`score_offer_probe` computes matching-choice correctness: `offered == expected_offer.should_offer` for the probe's own procedure. Replacing the precondition matcher behind it with the similarity strawman flips every `near_miss_control` label — the dedicated test fails in that mode.

---

## 3. Freezing and usage

The generator is offline: no network, no provider call, no judge model (`E-022` §3). The same seed always yields the same content; run it from `custom-memory-engine/`:

```bash
uv run python -m research.benchmarks.own_C.generator --write
```

The `dev` split is available for tuning; the `test` split is **never executed** (`V2-D42` / gate `G-TS`); `RL-101`–`RL-105` run it once after a signed freeze.
