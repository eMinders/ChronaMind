# RL-016 — Replay anomalies, judge validation, and release license (#6)

Prepared 2026-10-24; updated 2026-10-25 with `RL-020` benchmark verification. **Owner single-maintainer review and self-attestation recorded for P1 decision preparation (2026-10-24); institutional supervisor approval and final pilot validation (`RL-100`, `RL-132`) remain separate downstream gates.**

## D-23 replay rule

Retain anomalous histories; stable-sort `(session timestamp, original session index)`, preserve turn order in each session. Parse dates as timezone-aware UTC using dataset-specific documented formats (`YYYY/MM/DD (Day) HH:MM` for `LongMemEval`). Never silently infer an unspecified timezone; the adapter must document its timezone interpretation or reject the data. Reject any turn timestamp that precedes its session timestamp or decreases within a session. Preserve original source timestamps/IDs separately from the recorded clock. R3: advance repeated instants to `max(source_ts, previous_recorded_ts + 1 µs)` per user; no wall-clock replay.

Reference `ρ = max(question_date, last_recorded_turn_clock)`, including cumulative R3 increments. Merely using the last raw session date can leave tied turns recorded after ρ. This is an explicit precision clarification of Proposal §7.4 rule 3a, not a change to source validity timestamps. Empty histories use question_date. Each independent question/history uses isolated user/clock state.

Report original-date anomaly counts, sort permutations and clock adjustments. Recomputed on checksum-pinned inputs in `RL-020` (`xiaowu0162/longmemeval-cleaned` at commit `98d7416c24c778c2fee6e6f3006e7a073259d48f`):
- `longmemeval_oracle.json` (`SHA-256: 821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`): **500** total questions (**30** `_abs` abstention, **56** `single-session-assistant`), **34/500** out-of-order `haystack_dates`, **43/500** with `question_date < max(haystack_dates)`, **0/500** tied session timestamps.
- `longmemeval_s_cleaned.json` (`SHA-256: d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`): **500** total questions (**30** `_abs` abstention, **56** `single-session-assistant`), **211/500** out-of-order `haystack_dates` across all filler+evidence sessions (**39/500** when restricted to `answer_session_ids`), **76/500** with `question_date < max(haystack_dates)` across all haystack sessions (**44/500** on `answer_session_ids`), and **95/500** histories with tied session timestamps.
`RL-025` / `RL-102` must report sensitivity excluding questions with original `question_date < max(session_date)`. Primary and exclusion analysis use the identical frozen pipeline; never select the preferred result after evaluation.

## D-24 human judge-validation gate

Development sample **n = 100 minimum**, stratified across scored datasets, query modes (current, historical, change), answerability, configurations and correct/incorrect labels. Fix sampling seed 42 before annotation; ensure both gold classes and all applicable modes represented, report per-stratum counts. Increase dev sample before test freeze if classes/modes lack coverage; never tune using test items.

Member 5 and Member 1 independently annotate shuffled blinded items (no system/model identity or each other's labels), using the frozen per-mode rubric. Preserve both raw labels. Report inter-human confusion matrix/accuracy/κ; resolve disagreements with a documented adjudication by Member 3 or Member 4 before comparing judge to adjudicated gold. Adjudication must not use judge output. Judge sees one answer, question and gold; length-neutral rubric. Record version/prompt/seed and only cache successful validated responses.

Required gates: **inter-human κ ≥ 0.75**, **judge-vs-adjudicated-gold κ ≥ 0.75 and accuracy ≥ 0.90**, n≥100, both classes present, mode coverage, completed blind annotation and reviewer approval. Undefined κ (constant identical labels) fails closed. These are **project acceptance requirements, not universally validated scientific cutoffs**. Report confusion matrices, disagreement counts and a 95% agreement uncertainty interval (paired/cluster-aware where repeated items share scenarios) in RL-033; do not claim n=100 guarantees precise endpoint agreement. Use additional dev items if uncertainty/strata reveal insufficient instrument reliability.

Run dev perturbation checks (irrelevant appended detail and meaning-preserving paraphrases), inspect label flips and same-family self-preference risk (D-03). Freeze rubric only after defects are resolved and validation rerun. No test scoring permitted before all gates and reviewer sign-off; post-test blind human sensitivity audit remains RL-104. `binary_agreement` and `judge_gate` provide deterministic numeric checks, **not** an actual human audit or approval substitute.

## D-26 synthetic release license

Select **MIT for independently authored generators and own suites T/G/D/I**, subject to ownership/supervisor approval and pilot validation in RL-132. Current repository root MIT license does not authorize relicensing third-party data. Keep provenance records for script/schema design and generated text; zero LoCoMo text/schema reuse or translated/paraphrased copying. LoCoMo remains CC BY-NC 4.0 and is not packaged into MIT own-suite artifacts. Benchmark adapters/download metadata are separate from dataset redistribution; check upstream licenses on pinned files before release.

## Sign-off

Proposal §§7.4, 10.2, 10.5; Lab decisions register and source ledger rows [4], [9], [15], [36]. Co-owner Member 3 (R-METHOD), Co-owner Member 4 (R-DB), and Peer Member 1 (R-LEAD): **Reviewed and self-attested by Project Owner (single-maintainer repository governance, 2026-10-24, GitHub issue `#6`)** to unblock P2/P3 benchmark tasks (`RL-020`–`RL-024`). Institutional supervisor sign-off and pre-test research freeze (`RL-100`) remain separate downstream gates.
