# RL-030 evaluation harness

This is the shared replay/configuration/artifact foundation for later benchmark
adapters (RL-025), baselines (RL-031), extraction replay (RL-032), scoring
(RL-033), and inference (RL-034). It imports no root application code and makes
no provider calls. The included dummy is a software fixture, **not a baseline,
an engine, or a scientific evaluation**.

Authority: the frozen Proposal §§7.3, 9, 10.1–10.7, Technical Specification §9,
the RL-010 budget memo, and the current consolidated independence boundary §0.
No human sign-off, pilot, test freeze, or H1–H4 result is created by this code.

## Run the synthetic smoke example

From `custom-memory-engine/`, after `uv sync --frozen --python 3.12.12`:

```sh
uv run python -m evaluation.harness --config evaluation/harness/fixtures/config.json --cases evaluation/harness/fixtures/dev.json --suite T --split dev --run-id rl030-smoke-a --runs-root evaluation/runs/scratch
uv run python -m evaluation.harness --config evaluation/harness/fixtures/config.json --cases evaluation/harness/fixtures/dev.json --suite T --split dev --run-id rl030-smoke-b --runs-root evaluation/runs/scratch
```

The two `answers.jsonl` and `scores.jsonl` files are byte-identical, including on
Windows (explicit UTF-8/LF). Their manifests contain different measured timing.
Reusing an existing run ID/config/suite refuses to overwrite it. Tests exercise
the same public runner and real filesystem operations. The CLI is intentionally
limited to `fixture-dummy` / `fixture-v1`; later adapters use `run_evaluation`.
The fixture pin strings/hash are synthetic identifiers, not approved model pins.
This is a source-checkout module; the existing engine wheel does not package
`evaluation/`.

## Adapter and input contract

`models.MemorySystemAdapter` exposes exactly the seven synchronous methods in
Technical Specification §9.1: `reset_user`, `set_clock`, `ingest_turn`,
`drain_queue`, `retrieve`, `answer`, and `delete`. Actual database implementations
must bridge their async internals without changing this common interface.

- Construct the adapter from the immutable `HarnessConfig`. The factory must
  honor `system_id`, `adapter_version`, model/tokenizer/prompt pins, seed,
  temperature zero and `B_mem`. Different systems/ablations retain their identity
  in the execution hash even when all controlled settings are identical.
- Constructors, `reset_user` and `set_clock` are local, uncharged control
  operations. Do not hide provider work in them. The harness seeds no process-wide
  random generator; adapters use the explicit seed in their own isolated state.
- Every charged operation returns `Usage` with exact Decimal USD and explicit
  nonnegative input/output/embedding counters, including all attempts. Unknown
  usage is never represented as zero. A known failed operation raises
  `AccountedOperationError(message, usage)`; an unaccounted exception conservatively
  charges its full reserved bound and fails the run. No automatic provider retry
  occurs here; implementations use bounded tenacity retries inside that bound.
- `retrieve` supplies rendered context/evidence and its actual token count. It
  must fit the frozen tokenizer budget. `answer` uses the frozen prompt and the
  retrieval state for this question; it must not silently introduce a second
  retrieval policy. The dummy counts Unicode code points as synthetic tokens.
- `retrieval_latency_ms` is optional adapter instrumentation from query receipt
  to assembled context **excluding LLM generation/external API calls**. Missing
  instrumentation remains null. Adapter-operation wall time is measured
  independently, after reserving and before reconciling budget, so ledger file I/O
  does not contaminate it. Runner visibility wall time includes harness overhead
  and is labelled separately; it is not production visibility-lag evidence.
- `EvaluationCase` explicitly lists tenants and a cluster ID. Typed ingest,
  question and deletion events preserve input order; clocks must be aware and
  nondecreasing per user, with strictly increasing ingestion clocks. Every case
  resets its users. Queues are drained before questions and pending writes abort.
  Source adapters own stable chronological/D-23 normalization. Gold answers and
  evidence labels are absent from `QuestionRecord` and `TurnRecord`.
- The optional `CaseDataset` envelope asserts its suite/split; the CLI verifies
  both. For the Python API, `case_loader` is a trusted source-adapter seam and must
  load exactly the declared, hashed split. It is invoked only after authorization.
- A local, uncharged `scorer` callback returns typed per-item metrics. Provider
  judges are out of scope: RL-033 must add accounted dispatch/usage rather than
  make untracked calls through this callback. Missing scores remain null;
  explicitly required unsupported capabilities are recorded `not_supported` and
  unscored, not assigned zero. No pooled dataset/overall score is computed.

## Artifacts and provenance

Outputs live at `evaluation/runs/<run_id>/<config_id>/<suite_id>/` by default:

| File | Contents |
|---|---|
| `answers.jsonl` | Stable item/cluster/tenant/stratum IDs, temporal mode/reference, rendered context, raw answer, evidence references, token/cost counters and timing join key |
| `scores.jsonl` | Stable item metrics or explicit unsupported status |
| `per_cluster.jsonl` | Counts and within-cluster, within-stratum metric means/missing counts; no bootstrap, pooling or hypothesis verdict |
| `manifest.json` | Git SHA/dirty flag, exact config and execution hash, split SHA-256, model pins/seed, environment/hardware, wall duration, per-item timing/P50/P95, usage/campaign ledger and artifact hashes |

Volatile timings are in the manifest, joined by `(cluster_id, question_id)`, to
preserve byte-identical answer/score outputs for deterministic fixtures/replay.
Clusters are scenarios for T/G/D/I, questions for LongMemEval_S and conversations
for LoCoMo; the source adapter supplies the corresponding cluster IDs. Strata
are never mixed. Finite nullable metrics preserve missingness.

The run directory is created exclusively and pre-existing links, Windows
junctions/reparse points and unsafe path components are refused. JSONL is flushed
before success. The manifest is fully staged/fsynced and atomically hard-linked
without overwrite; the output filesystem must support hard links (NTFS/ext4 do).
Failures preserve partial files plus a content-free `FAILED.json`, with no
successful manifest. A failed run ID cannot be reused. Do not bulk-commit run
outputs, private context, answers or ledger files; the ignore rules retain the
future signed `runs/pilot/FREEZE_LIST.md` exception.

## Budget reservations

All workers in a campaign share the same canonical runs root and `budget_id`.
`BudgetLedger` serializes exact persistent reservations under
`.budgets/<budget_id>/ledger.json` with a bounded lock. Before each dispatch it
reserves the operation's **aggregate worst case, including failed/retried calls**;
afterward it reconciles actual usage. Both the combined input+output token cap
and USD cap apply, with the separate development USD allocation. The RL-010
ceilings are maximums, not approval to spend; use smaller approved caps as needed.

Restarting the runner does not replenish the ledger. Unknown usage is charged at
the bound; over-reported actual usage is persisted, marks a breach and stops any
further dispatch. Interrupted reservations remain reserved and stale lock files
are not automatically removed. Investigate recovery against provider records;
do not delete state or change `budget_id`/runs root to obtain a new allowance.
These are local cooperative controls, not access controls against a user who can
edit the files or a distributed multi-machine budget service.

## Test-split guard and RL-100 integration

Before any test-split content read, loader/adapter call or output creation,
require both `--confirm-one-shot-test-run` and `--pilot-freeze <path>`. The latter
is a strict version-1 JSON envelope produced/reviewed during RL-100, **not an
existing approval in this repository**:

```text
schema_version: 1
task: RL-100
status: frozen
git_tag: approved freeze tag
config_sha256: HarnessConfig.execution_sha256()
dataset_split_sha256: {suite_id: exact split-file SHA-256, ...}
freeze_list_path: repository-relative signed FREEZE_LIST.md path
freeze_list_sha256: exact signed file SHA-256
signatures: three {role, signer, signed_at} objects
```

Required semantic sign-off roles are `methodology_owner`, `project_lead`, and
`supervisor`, with distinct non-pending identities and aware past timestamps.
They describe actual research approval responsibilities, not fixed numbered
issue assignments. The signed human-readable FREEZE_LIST, envelope and split
file must match exact committed bytes at the named Git tag. Freeze/config/suite/
split mismatches fail closed. Use LF bytes consistently before tagging; no
silent line-ending normalization weakens an exact freeze hash.

Structured attestations validate the recorded approval contract, **not a
cryptographic identity or independent verification that someone signed**. RL-100
must obtain genuine approvals, freeze all numeric decisions and prerequisite
gates, and commit/tag the artifacts before any confirmatory run. This issue
does not sign or complete J5/J7/J8.

Before dispatch, a fsynced exclusive `.one-shot/<identity>.json` claim binds the
execution configuration (including system/version) and split/suite. Cosmetic
config/run renames cannot bypass it. Crashes retain the claim; do not clear it to
select a rerun. This is cooperative local enforcement across processes sharing
one canonical runs root, not across machines or alternate roots. Future approved
repeated-run batches must aggregate all predeclared repeats inside that one
claim; this skeleton does not implement or authorize such a batch.

All guard tests use fictional temporary Git repositories and synthetic signed
fixture records. They do not inspect a real held-out split or impersonate a
research approval.

## RL-025 source integration

The [benchmark adapters](../../research/benchmarks/ADAPTERS.md) opt into per-history streaming while ordinary callers retain eager validation. Streaming failures retain FAILED.json and never publish success. LongMemEval oracle and M have separate suite IDs and question cluster keys; only S is primary H2. TurnRecord.source_speaker optionally preserves a human speaker independently of tenant/role. Consumers must honor it for LoCoMo dialogue. Optional source metadata is namespaced under benchmark_source in the success manifest; existing provenance, authorization and budget controls remain mandatory.

Reporting strata are removed from the question passed to `answer`; original strata remain in scorer inputs and artifacts. Source adapters must also use opaque system identifiers when original IDs encode answerability labels.
