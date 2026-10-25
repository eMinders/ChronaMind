# RL-030 implementation verification

Verified locally on 26 October 2026 from branch
`codex/rl-030-evaluation-harness`, based on pulled main `0258f47`. The issue remains
open for the project leader's PR review and merge. No product runtime/configuration
was modified; the only file outside this workspace is its dedicated CI workflow.

## Acceptance evidence

| Requirement | Implementation | Executed evidence |
|---|---|---|
| Seven-method uniform adapter | `evaluation/harness/models.py` | Typed dummy through the public runner/CLI; Pyright passes |
| Explicit frozen controls | `config.py` | Strict/immutable config, zero-temperature/invalid pin/budget rejection; system/version identity differs while cosmetic renames do not |
| Deterministic output | `artifacts.py`, `runner.py` | Two real runs and two CLI invocations produce byte-identical UTF-8/LF answers/scores despite differing manifest timings |
| Complete manifest | `runner.py` | Actual Git SHA/dirty flag, split/config/artifact hashes, model IDs/seed, environment/hardware, duration/P50/P95, per-item timing and token/exact USD/campaign counters asserted |
| Immediate test-run refusal | `guard.py` | Missing flag/freeze/roles, pending/future attestations, changed config/list/split/tag all reject; denied paths do not call the loader/adapter or create output |
| One-shot protection | `guard.py` | Exclusive persistent claim survives another instance and cosmetic renames; real temporary Git tag/freezes used only as synthetic fixtures |
| Immutable/failure-safe writes | `paths.py`, `artifacts.py` | Duplicate paths, traversal, Windows junctions, pre-existing manifests and injected manifest-fsync failure cannot overwrite or falsely publish success |
| RL-010 budget controls | `budget.py` | Persistent shared reservations, concurrent cap races, exact Decimal tails, dev/total caps, failed/retried/unknown calls, breach-stop and invalid state/reparse regressions |
| Queue/tenant/cluster integrity | `models.py`, `runner.py` | Aware monotonic replay, pending-queue refusal, independent tenants, unsupported capabilities unscored, missing metrics retained and strata kept separate |

## Integrated checks

Using the existing locked CPython **3.12.12**, Ruff **0.15.6**, Pyright
**1.1.408** and pytest **9.0.2**:

```sh
uv run ruff check src tests research/benchmarks implementation/tests evaluation
uv run ruff format --check src tests research/benchmarks implementation/tests evaluation
uv run pyright
uv run pytest -q
python validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python validation/cp_bounds.py) validation/cp_bounds.output.txt
md5sum -c validation/document_hashes.txt
```

- Current CI-scoped lint and format pass (47 Python files); Pyright reports
  zero errors/warnings. The available-version notice was not treated as failure
  or an instruction to change the pinned version.
- Repository-wide PR lint/format also pass (99 Python files). Three pre-existing
  research helper files needed formatting/import cleanup: `validation/cp_bounds.py`,
  `validation/mechanical_checks.py`, and `reference/sources/pages/fetch_sources.py`.
  The two executed verification helpers' stdout is byte-identical before/after;
  normalized AST inspection confirms no logic/formula changes. The fetcher was
  compiled only, never executed or used to download anything.
- **166/166 tests passed**, including **5/5 database integration probes**, with
  **zero skips, failures or collection errors**. This includes 99 new harness
  tests, using temporary synthetic inputs and Git repositories.
- The exact pinned disposable PostgreSQL **16.15**, pgvector **0.8.7** and
  btree_gist **1.7** passed the existing reduced bitemporal SQL harness:
  **43 assertions; `ALL ASSERTIONS PASSED`**.
- Cleanup verified zero owned containers, networks and volumes. Temporary
  database credentials stayed in child memory/environment, not repository files
  or process arguments. App/engine/research locks were unchanged.
- Mechanical consistency: **25 hard checks passed**; contextual heuristic review
  groups retain their prior benign classifications. Clopper–Pearson output matches
  the expected file. All five canonical LF document hashes pass. Windows working
  files may have CRLF; this is canonical-byte verification rather than a claim
  that raw Windows `md5sum` succeeds. Only the changelog's integrity entry changed
  to accompany its new implementation entry; the frozen Proposal is unchanged.

The Windows test runner sets `WindowsSelectorEventLoopPolicy` for Psycopg's async
connection probes. A first disposable launch stopped before pytest when its
connection/version probe observed a startup transition; cleanup passed. The
local verifier was strengthened to wait for the final TCP listener rather than
the image's temporary initialization socket. The subsequent complete run above
passed. This helper change is local tooling, not an alteration of the pinned
database image, project Compose file or research protocol.

## Limits

These are software/integration checks, not research results. No provider/model
call, paid inference, pilot, real confirmatory test run or human approval occurred.
The existing oracle/LoCoMo caches support their current software tests; S/M asset
coverage is still conditional and is not established by a green pytest suite.
No full production DDL/RLS/deletion/latency or H1–H4 outcome is claimed.

The harness is a source-checkout foundation. Its scorer seam is local/uncharged;
provider judges need accounted dispatch in RL-033. Test approvals are validated
as explicit tagged human attestations, not cryptographic identities. Budget and
one-shot coordination require a shared canonical local runs root; they do not
coordinate machines or protect against someone editing/deleting their own state.
Those boundaries and future task integration are documented in
[`../evaluation/harness/README.md`](../evaluation/harness/README.md).
