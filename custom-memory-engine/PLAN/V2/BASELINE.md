# v1.0.0 Baseline

**Task:** `E-002` · **Measured:** 2026-10-31 on `main` at `490904d` (`Merge pull request #81`) ·
**Purpose:** the fixed reference every V2 wave regresses against. Every number below was produced by the
command next to it in this session, not remembered from an earlier run. After this baseline, changes to v1
tables must be additive (`PLAN/V2/ROADMAP_V2.md` section 2).

> **The tag is not part of this document.** Which commit becomes `v1.0.0`, the tag name and the package
> version are Project Owner decisions that are still open (section 7). Every figure below is pinned to the
> commit it was measured at, `490904d`, and was reproduced on Linux at that commit (section 2), so it stays
> true whichever commit is chosen; a different tag commit needs its own re-measurement.

## 1. Environment

| Item | Value |
|---|---|
| Engine | `custom-memory-engine`, CPython 3.12 (`uv sync --frozen`), `psycopg` 3.x |
| Database | `pgvector/pgvector:0.8.7-pg16-bookworm` (PostgreSQL 16.15, pgvector 0.8.7, `btree_gist`), the engine's own `compose.yaml` |
| Host | Windows 11, Docker Desktop; the Windows `Selector` event loop is selected for `psycopg` (the default `Proactor` loop is unsupported) |
| Optional pieces | baselines extra (`uv sync --frozen --extra baselines`), prepared frozen model assets, FalkorDB 6.0.3, the three benchmark corpora |

The suite was first run on Windows. **It was re-run on Linux at the same commit** (2026-10-31, maintainer
session): `ubuntu`, CPython 3.12.12, the same pinned PostgreSQL and FalkorDB images as CI, prepared model
assets and checksum-verified corpora. The Linux column in section 2 is the reference for CI comparisons.

## 2. Test counts (the Tier-A regression wall)

Commands run from `custom-memory-engine/`. "DB" means `CME_TEST_DATABASE_URL` pointing at the disposable
database. The corpora (`research/benchmarks/*/data`) were fetched with the repository's own
checksum-verifying fetchers (`python -m research.benchmarks.longmemeval.fetch`, `...locomo.fetch`).

| Mode | Command | Collected | Windows: passed / skipped | **Linux: passed / skipped** |
|---|---|---:|---:|---:|
| **Full CI scope** (extras + assets + FalkorDB + DB) | `uv sync --frozen --extra baselines`; `uv run pytest -q` | 1,423 | 1,423 / 0 | **1,423 / 0** |
| No-database subset, extras installed | `uv run pytest -q -m 'not integration'` (no `CME_*` variables) | 1,266 selected (157 deselected) | 1,248 / 18 | **1,248 / 18** |
| Default install + DB | `uv sync --frozen`; `uv run pytest -q` | 1,407 | 1,384 / 23 | **1,387 / 20** |
| Default install, no DB (the CI `default-install` job) | `uv sync --frozen && uv run --no-sync pytest -q` | 1,407 | 1,236 / 171 | **1,236 / 171** |
| Default install, no DB, `-m 'not integration'` | `uv run pytest -q -m 'not integration'` | 1,250 selected | 1,230 / 20 | not re-run |

No test failed in any mode on either platform. The only platform difference is three tests in the default-install
+ DB mode that skip on Windows and run on Linux; every other count is identical.

**The wall.** The full-CI-scope row is declared the **Tier-A regression wall**: 1,423 collected, 1,423
passed, 0 skipped. Every V2 wave must keep it green or explicitly record a superseded invariant. The
no-database subset (1,266 selected, 1,248 passed, 18 skipped) is recorded separately as the no-database mode.
For reference, the earlier CI-scope record at `998558f` was 1,395 collected, 1,374 passed, 21 skipped; the
growth is the engine, deletion, scanner, queue-starvation and statistics tests merged since.

Every skip in the table is an environment gate, not a failure. The 23 skips (20 on Linux) in the default-install DB run
are: optional SDK integrations (`mem0ai`, Graphiti, vendor lifecycle), the prepared model assets
(`CME_BASELINE_TEST_ASSETS`), `onnxruntime`/`tokenizers`, and the dedicated baseline PostgreSQL run.
Installing the extra, preparing the assets and starting FalkorDB removes all of them.

**Corpora matter.** Without the downloaded corpora, 7 tests fail (`test_benchmarks_public.py` and
`research/benchmarks/tests/test_splits.py`) with `missing_or_unverified_file`. That is a missing data
file, not a code defect, but a baseline measured without the corpora would be wrong.

## 3. Schema and bitemporal checks

| Check | Command | Result |
|---|---|---|
| Reduced-schema bitemporal assertions | `psql -v ON_ERROR_STOP=1 -f validation/bitemporal_checks.sql` | **43** `PASS` rows, `ALL ASSERTIONS PASSED` |
| Same assertions on the real table | `schema/bitemporal_on_full.sql` (replayed by `tests/integration/test_full_schema.py`) | 43 `PASS` (part of the 1,423) |
| Migration source checksum | `python -m custom_memory_engine.schema.migrate apply` then `status` | version `1`, state `applied`, checksum `999de7516bbb9988ed85c0f27f9172a1194b6d301bcb730745e1cc1987e9622a` |

The checksum is SHA-256 of `schema/001_full_schema.sql` read with universal newlines; it also equals the
SHA-256 of the committed blob (`git show HEAD:custom-memory-engine/src/custom_memory_engine/schema/001_full_schema.sql`).
It is the value the runner records and `status` verifies; any later edit to that file is refused as a drifted
historical source.

## 4. Frozen research records

| Check | Command | Result |
|---|---|---|
| Document hashes | `md5sum -c validation/document_hashes.txt` | all 5 `OK` on a Linux/LF checkout; on a Windows checkout with `core.autocrlf=true` the working tree is CRLF and `md5sum` reports `FAILED`, so the committed blobs were hashed instead: `git show HEAD:custom-memory-engine/<file> \| md5sum`, all 5 equal |
| Mechanical checks | `python validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md` | `ALL HARD CHECKS PASSED` |
| Clopper–Pearson | `diff <(python validation/cp_bounds.py) validation/cp_bounds.output.txt` | identical (modulo line endings) |

## 5. Retrieval benchmark

The committed measurements are in `reports/benchmarks/`. `scripts/bench_retrieval.py` was re-run on this
machine for comparison; latency is a property of the host, so the re-run **does not** reproduce the committed
numbers exactly and is not expected to.

| Candidates | Channel | Committed P50 / P95 (ms) | Re-run here P50 / P95 (ms) |
|---:|---|---:|---:|
| 500 | dense | 10.52 / 12.00 | 13.64 / 15.82 |
| 500 | lexical | 4.17 / 4.68 | 6.85 / 8.49 |
| 500 | fused (RRF) | 10.59 / 12.00 | 14.25 / 18.19 |
| 2000 | dense | 26.99 / 29.41 | 28.19 / 35.04 |
| 2000 | lexical | 11.11 / 11.95 | 13.33 / 14.59 |
| 2000 | fused (RRF) | 28.16 / 29.84 | 29.10 / 33.61 |

Integrity of the committed files (SHA-256, first 16 hex): `retrieval_500.json` `a3ffc6977fdb3657`,
`retrieval_2000.json` `d838ebe5a98143f1`. **The regression reference for V2 is the committed file.** Re-measure on the same host before comparing; do
not compare a number from this host against one from another.

**G3 figure and a discrepancy for the Project Owner.** `ROADMAP_V2.md` G3 says "Retrieval P95 … within 2× of the
27 ms baseline". In `retrieval_2000.json`, **27 ms (26.99) is the dense P50**, not a P95; the P95s are **29.41 ms
(dense)** and **29.84 ms (fused/RRF)**. The two readings give different limits:

| Reading | Baseline | 2× limit |
|---|---:|---:|
| Literal G3 text (dense P50 at 2000 candidates) | 26.99 ms | **53.98 ms** |
| Fused (RRF) P95 at 2000 candidates (the end-to-end retrieval path, and a P95 as G3 says) | 29.84 ms | 59.68 ms |

Until the Project Owner amends G3, compare V2's **fused P95 at 2000 candidates** against the **stricter** limit,
**53.98 ms**. The stricter limit cannot pass a regression that the intended reading would fail.

## 6. What this baseline does and does not claim

- It records what the repository does at `490904d`. It is not a research result: no hypothesis `H1`-`H4`
  has been run.
- Known open gaps at `490904d`, each a v1 issue that was still open at that commit:

  | Issue | Gap at `490904d` | Later status |
  |---|---|---|
  | [#46](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/46) (`RL-070`) | Scanner channels 3, 5, 6, 7 had no harness probes; Suite D runner absent | Closed in r7 (harness `leak_scanner.py`, Suite D runner); not part of a `490904d` tag |
  | [#47](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/47) (`RL-071`) | No restricted runtime role, grants or tenant-scoping guard | Closed in r7 (`3d0f7bb`, `84e504f`); not part of a `490904d` tag |

  Task-level evidence is in `PLAN/V2/STATUS_AUDIT.md`, and declared-versus-enforced invariants in
  `PLAN/V2/INVARIANT_LEDGER.md` (both describe current `main`, not `490904d`).
- The engine passes its own tests on Windows; CI remains the authority for the Linux figures.

## 7. Reproducing and tagging

Reproduce the table in section 2 with the commands shown, from `custom-memory-engine/`:

```bash
docker compose -p cme-verify up -d --wait          # set CME_TEST_DB_PORT if 5433 is reserved on your host
uv sync --frozen --extra baselines --python 3.12
python -m research.benchmarks.longmemeval.fetch    # oracle + S; and: python -m research.benchmarks.locomo.fetch
python -m evaluation.baselines.assets prepare --directory <dir>
# export CME_TEST_DATABASE_URL, CME_BASELINE_TEST_DATABASE_URL, CME_BASELINE_TEST_FALKOR_PORT, CME_BASELINE_TEST_ASSETS
uv run pytest -q
```

### 7.1 Decisions required before tagging (#51 section 4; Project Owner, open)

| # | Decision | Options and consequence |
|---|---|---|
| 1 | **Which commit** | (a) `490904d`, the commit measured here: the pre-V2 substrate; every figure in this document reproduces there; r7's migrations 2 and 3 (`scope_grant`, association cap) are then post-tag additive migrations, as the additivity rule intends. (b) A later `main` commit: it must be re-measured first (at r8, `main` collects ~1,800+ tests), and the tag would include the r7 V2 primitives. **Recommendation: (a)** |
| 2 | **Tag name** | The repository is a monorepo whose root package is `langgraph-fastapi-template` 0.1.0. A plain `v1.0.0` reads as the whole repository's version; `cme-v1.0.0` names the engine. **Recommendation: `cme-v1.0.0`** |
| 3 | **Package version** | `custom-memory-engine/pyproject.toml` is `0.1.0` at `490904d`. A tag on an existing commit cannot change it; bumping to `1.0.0` would need a new commit (and so option (b)). **Recommendation: let the tag stand alone and record the mismatch** |

None of these is decided by this document. The tag is outward-facing and is created by a maintainer after the
decisions are recorded here.

To tag, after the decisions above are recorded:

```bash
git tag -a <tag-name> -m "<tag-name>: frozen substrate baseline (PLAN/V2/BASELINE.md)" <decided-commit>
git push origin <tag-name>
```

The tag is outward-facing and should be created by a maintainer deliberately; it is deliberately not created
by this pull request.
