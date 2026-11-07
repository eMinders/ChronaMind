# RL-025 benchmark adapters

`longmemeval.adapter.LongMemEvalAdapter` and `locomo.adapter.LoCoMoAdapter`
connect checksum-pinned source records to the uniform evaluation harness. They
never acquire, repair, rewrite or cache raw data. Source construction reads no
data. `run()` defers selection and source access until the existing test guard
authorizes the run; low-level `prepare_item`, `prepare_conversation` and
`iter_cases` are for already-authorized source preparation.

The loaders stream a top-level JSON array with `ijson`. Hashing uses fixed-size
chunks and normalization retains one history/conversation at a time. Memory is
bounded by a single source history, not the entire 2.7 GB M file. A source is
checksummed before consumption, after consumption, and before successful
manifest publication. Duplicate JSON keys or case IDs, malformed shapes, missing
selected IDs and altered bytes fail closed. Files and manifests remain intact
on failure. Only selected records are normalized or exposed to systems/scoring;
the raw array is scanned for its checksum and unique source identities.
Nested duplicate JSON keys are detected before yielding a record; validation
errors are raised outside the parser's C callbacks, consistently across backends.

## Selection and controlled execution

RL-026 owns actual development/test split creation and J5 approval. These
adapters accept an explicit version-1 **JSON selection document** (also valid
YAML 1.2); they do not yet parse general YAML. For example, an independently
authored synthetic development selection has this structure:

```json
{
  "schema_version": 1,
  "suite_id": "LongMemEval_S",
  "split": "dev",
  "dataset_sha256": "<exact 64-character source SHA-256 from MANIFEST.txt>",
  "case_ids": ["<original selected source question_id>"],
  "retrieval_k": 5
}
```

LoCoMo selects original `sample_id` values. Order follows the immutable source
array. The guarded harness hashes/freezes the complete selection bytes, binding
case membership, source digest, suite and metric `k`; the runtime `k` must agree.
An artifact with a wrong suite/split/source hash is rejected. A complete signed
RL-100 freeze and explicit one-shot confirmation are still required for test
runs. The example is a schema illustration, not an approved split or permission
to run a benchmark. All tuning, debugging and pilot work stays on development.

From `custom-memory-engine/`, an already-authorized caller supplies its own
uniform system adapter and pinned configuration:

```python
source = LongMemEvalAdapter(raw_path, variant="s")
destination = source.run(
    selection_path=selection_path, split="dev", config=config,
    adapter_factory=system_factory, run_id=run_id,
    output_root=runs_root, repo_root=repository_root, k=5,
)
```

LoCoMo uses the same API with `LoCoMoAdapter(raw_path)`. The constructor's
`manifest_path` override supports synthetic fixtures; actual benchmark usage
must retain the committed acquisition manifest. Real system adapters are a
separate RL-031 task. An object cannot execute concurrent runs; use separate
instances for independent runs. Changing the output root or budget ID does not
grant new budget or one-shot authority.

## Replay, identity and evidence

- LongMemEval supports `oracle`, `s`, and `m`, identified as
  `LongMemEval_oracle`, `LongMemEval_S`, and `LongMemEval_M`. Only S contributes
  to the primary H2 endpoint. Stable session sorting preserves original
  session/turn identities in audit/provenance and source time. System-facing LongMemEval identifiers use a stable SHA-256 of the original question ID, with source session indices; the original _abs suffix and annotation IDs cannot hint answerability. Source dates use the documented
  `YYYY/MM/DD (Day) HH:MM` format interpreted as UTC. Optional explicit turn
  timestamps must follow that format and cannot precede/decrease within their
  session. Recorded clocks advance by 1 microsecond on ties, and
  `rho = max(question_date, last_recorded_clock)` includes all such advances.
  Raw session labels may repeat in the pinned S/M haystacks. Every array
  occurrence remains a distinct indexed session/turn sequence; no deduplication
  or text merging occurs. `replay_session_indices` records the original-index
  permutation, since repeated raw labels alone cannot describe the sort.
  The source question type is retained as a stratum. Free-text temporal
  questions have no authoritative target-time annotation: the adapter supplies
  the reference clock without guessing an as-of target from the gold answer.
- Each LongMemEval history is an independent tenant/question cluster. Gold
  `has_answer` turns remain in a separate immutable scoring envelope. Retrieved
  memories expose their `MEMORY_EVIDENCE` links through `evidence_turn_ids`;
  recall measures unique recovered gold turns among the first `k` memories.
  Raw-turn systems use the same source turn IDs. Duplicated links do not inflate
  coverage. Multiple-gold questions also report nullable `all_evidence_found`.
- Abstentions and `single-session-assistant` questions remain outside primary
  recall; their diagnostics are retained. Missing gold evidence is unscored,
  and does not silently erase a history's primary-population eligibility.
  Original-date anomalies retain the same-pipeline exclusion-sensitivity
  metric. Both ordinary and diagnostic missing metrics remain null.
- LoCoMo is a human-to-human conversation, not an assistant conversation. Both
  speakers are `role="user"` with unchanged text and explicit `source_speaker`,
  inside one isolated synthetic conversation tenant. Consumers must honor that
  speaker field when assembling prompts/extractions; they must not silently
  treat every first-person statement as the same human's fact. This declared
  representation is identical for every compared system. Images, captions,
  generated observations and summaries are not treated as dialogue or fetched.
  Session dates use `h:mm am/pm on d Month, YYYY`, explicitly interpreted as UTC.
  Source dialogue IDs map gold evidence; missing evidence stays missing and
  compound references are resolved when unambiguous; unresolved/malformed annotations stay in scoring-only provenance and make evidence recall null. QA remains scoreable. Unpaired date-only metadata does not invent dialogue or extend replay time. Strata are numeric categories 1–5, reported per
  conversation; no pooled confirmatory result is computed.

## Exact official scoring and provenance

The licensed function subsets in [`_official/`](_official/README.md) retain
hash-pinned originals, immutable commit IDs, licenses, and AST equality checks.
They exclude upstream provider clients, CLI side effects, BERTScore and model
downloads. Only the LoCoMo count-only console print is locally discarded.

LongMemEval uses the exact task/abstention prompt builder and legacy
case-insensitive `yes` substring interpretation. A caller may inject a **local,
uncharged** judge for synthetic compatibility testing; absent that callback QA
is null. This does not implement or validate a real LLM judge. RL-033 must add
accounted provider dispatch, approved prompts and actual human validation before
any real judged research run. Configuration's judge/model pins do not prove a
callback's honesty, funding or validation.

LoCoMo invokes the official category scorer: category 1 partial F1 over
comma-separated answers; categories 2–4 stemmed token F1; category 3 uses the
first semicolon clause; category 5 checks the official literal abstention
phrases. Scores retain full function precision. The historical NLTK 3.8.1
Porter class is retained under Apache-2.0 because the modern algorithm changed;
its tiny original StemmerI interface is retained alongside it. Legacy upstream missing-context
recall defaults are discarded; project evidence recall is measured separately.

The success manifest's `benchmark_source` records raw source digest, selected
suite, metric `k`, scorer/algorithm pins, source/replay session permutations,
adjustment counts and a digest of exact clock adjustments, anomaly and
eligibility/missing-evidence counts. Large per-turn audit structures remain
available in `PreparedCase.audit`, rather than accumulating full histories in
the manifest. The existing harness separately binds selection/config/artifact
hashes and the current code commit. Gold answers are never system inputs. The harness removes reporting strata from the question passed to the answering system, retaining them only for scoring/artifacts, since an abstention/category stratum could reveal the target label.

## Software verification

```sh
uv sync --frozen   # CPython from .python-version
uv run pytest -q research/benchmarks/longmemeval/tests research/benchmarks/locomo/tests research/benchmarks/tests
```

The default pytest scope also collects these tests. Independently authored
synthetic fixtures cover clocks, identity, gold separation, immutability,
streaming, exact scorer parity, and a fixed B0-style test responder through both
source adapters and official scorer boundaries. Fake judge replies establish
software plumbing, not real judge quality, a production baseline, an approved
pilot/test run, or an H1–H4 result. Actual source-format coverage is described below.

## Mechanical validation against the pinned public sources

A content-free offline schema audit (no system adapter, replay execution, scorer,
model or split selection) validated all 500 records in each pinned LongMemEval
oracle, S and M file, and all 10 pinned LoCoMo conversations/1,986 questions.
S and M were acquired through the existing pinned fetcher and their committed
SHA-256 manifests matched before and after validation. S retains all 414
primary-eligible questions. Thirteen S and 449 M histories have repeated source
session labels; the adapter preserves every occurrence with indexed identities and
explicit replay-index provenance. Source text/IDs/gold were not saved in audit
reports. The raw files remain gitignored and are not part of the PR.

The source-date audit recomputed D-23 counts on the exact files, before any split:

| Mechanical source statistic | S | M |
| --- | ---: | ---: |
| Total histories | 500 | 500 |
| Abstention / assistant-evidence questions | 30 / 56 | 30 / 56 |
| Evidence-session dates out of order | 39 | 39 |
| Question date before last evidence-session date | 44 | 42 |
| All haystack-session dates out of order | 211 | 211 |
| Question date before last haystack-session date | 76 | 118 |
| Histories with tied session timestamps | 95 | 480 |
| Histories with repeated raw session labels | 13 | 449 |

These aggregate source counts do not define development/test membership or
change the restriction of the primary H2 endpoint to S.

LoCoMo contains 16
unpaired session-date metadata keys, compound evidence annotations, and five
questions with unresolved evidence. They are preserved and counted; no dialogue
or gold link is invented, no record is dropped, and affected evidence recall is
null. Exact official QA scoring is unchanged. This is input-format, envelope and
clock conformance, not a real system/scorer/model benchmark evaluation.
