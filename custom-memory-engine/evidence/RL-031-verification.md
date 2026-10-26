# RL-031 verification and provenance

This contribution implements [issue #23](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/23).
The [adapter guide](../evaluation/baselines/README.md) documents operational setup
and limitations. Component checks passed during implementation. Final integrated
verification and exact submitted-commit CI are still in progress; this document
will be updated with those outcomes before completion is claimed.

## Requirement-to-evidence map

| Requirement | Implementation and behavioral evidence |
| --- | --- |
| B0 explicit session / question-only fallback | `b0_session.py`; `test_raw_baselines.py`, including cross-session exclusion and missing-session behavior. |
| B1 full model-window context / logged truncation | `context.py`, `b1_full_history.py`; actual request exact-fit/one-over tests with output and framing reserves, truncation events/counts. |
| B2 raw dense / role and speaker preservation / no extraction or time gate | `b2_dense.py`, `embedding.py`; chunk-to-turn cosine, tenant/generation, overflow and source-evidence tests; real frozen-model B2 synthetic retrieval. |
| B2h actual FTS / unweighted RRF60 | `b2h_hybrid.py`, `stores.py`; actual PostgreSQL `ts_rank_cd`, stemming, no-overlap, source deduplication, tenant/deletion/reset and ownership checks. |
| B3 real pinned SDK / sole Mem0 import boundary | `b3_mem0.py`, `vendor.py`; actual Mem0/Qdrant/SQLite child tests with only model boundaries scripted; AST positive/negative path and dynamic-import guards. |
| Conditional B4 / per-episode cost and stability instrumentation | `b4_graphiti.py`, `vendor.py`, `runtime.py`; actual Graphiti/Falkor ingestion/search, real episode provenance, isolated IPC, failed/successful attempt usage and latency, poisoned-worker and cleanup tests. D-21 remains unmeasured. |
| Frozen model/embedder/prompt / equal retrieved budget | `contracts.py`, `factory.py`, `runtime.py`; actual HTTP serialization, settings-copy rejection, all-six common answer wrapper, frozen asset tests, B_mem/context-policy regressions. |
| Complete attempted accounting | Pre-dispatch reservation, bounded retries, unknown-usage conservative bounds, exact embedding-ID counts, mismatched-model/output/embedding guards; failed-attempt and rejected-before-dispatch tests. |
| Replay dates and clean answering context | Source/replay-date Mem0 tests, all loaded Graphiti clock aliases, original-time storage, distinct reference/target UTC answer prompts, no reporting-label injection. |
| Honest unavailable capabilities | Unknown source evidence produces nullable recall/all-found metrics while retaining QA; vendor seven-channel deletion is unsupported. Artifact/score tests cover capability propagation. |
| Resource and product isolation | Constructor laziness, no default provider/telemetry/network in controlled worker tests, dedicated PostgreSQL ownership markers, exact owned graph cleanup and retry, harness cleanup before success publication, independent engine manifest/lock. |

Tests live under `evaluation/baselines/tests/` and `evaluation/harness/tests/`.
Fixtures use synthetic text, scripted paid-model boundaries and actual local SDK,
model, HTTP and database implementations. They are not benchmark experiments.

## Reproducible commands

From `custom-memory-engine/` with disposable PostgreSQL and FalkorDB services:

```text
uv sync --frozen --extra baselines --python 3.12.12
uv run --no-sync python -m evaluation.baselines.assets prepare --directory ABSOLUTE_ASSET_DIRECTORY
uv run --no-sync ruff check src tests research/benchmarks implementation/tests evaluation
uv run --no-sync ruff format --check src tests research/benchmarks implementation/tests evaluation
uv run --no-sync pyright
uv run --no-sync pytest -q
```

Set `CME_TEST_DATABASE_URL` and `CME_BASELINE_TEST_DATABASE_URL` to an isolated
`cme_*` database, `CME_BASELINE_TEST_FALKOR_PORT` to the owned loopback graph port,
and `CME_BASELINE_TEST_ASSETS` to the prepared directory. Missing optional
dependencies/assets/services cause explicitly explained integration skips; they
do not count as successful integration coverage. CI provisions both databases,
prepares the immutable assets and runs the complete optional configuration. A
separate CI job exercises the default installation without vendor/model packages.

Reference checks remain `md5sum -c validation/document_hashes.txt`, the documented
`validation/mechanical_checks.py` command, exact `cp_bounds.output.txt` comparison,
and the 43 reduced-schema SQL assertions. These SQL checks and raw-baseline store
tests do not establish the teammate-owned full 12-table schema or production RLS.

## Immutable primary sources

| Component | Release / immutable source | Integrity |
| --- | --- | --- |
| Mem0 | [2.2.1](https://pypi.org/pypi/mem0ai/2.2.1/json), [commit 94c3fe9f](https://github.com/mem0ai/mem0/tree/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd) | Wheel SHA256 `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d` |
| Graphiti | [0.31.2](https://pypi.org/pypi/graphiti-core/0.31.2/json), [commit d72407dc](https://github.com/getzep/graphiti/tree/d72407dcedac911e1be9fc15b55e8c05a3efcc36) | Wheel SHA256 `b26e1f350482266aec24beb1cd4c58d5e32ede1a897af8fa25b10800a16bbb8d` |
| BGE model and tokenizer | [Official immutable model tree](https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a) | ONNX SHA256 `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35`, 133093490 bytes; other files pinned in `assets.py` |
| o200k BPE | [Official OpenAI asset](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken), tiktoken 0.14.0 | SHA256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |
| FalkorDB | [6.0.3 release](https://github.com/FalkorDB/FalkorDB/releases/tag/v6.0.3), `falkordb/falkordb:6.0.3` | Linux amd64 manifest `sha256:a3f7f58482504f49bc1c0d703c472f73e6eb0fc421d0c31a391cc26e21753c33`; actual module version 60003 |
| PostgreSQL / vector | PostgreSQL 16.15 / vector 0.8.7 / btree_gist 1.7 | Existing engine compose and CI image digest retained |

The independent engine `uv.lock` records exact resolved distribution artifacts
and hashes. Vendor startup checks SDK versions; private shims additionally check
source hashes before patching pinned call sites. Worker Python version enters
adapter metadata. The official [structured output requirements](https://developers.openai.com/api/docs/guides/structured-outputs)
inform provider-bound schema normalization. No provider request was made to
validate funded model access or real-world schema reliability.

## Review findings and limits

Independent review reproduced and checked fixes for copied settings bypassing
frozen identity, reference clocks missing from answer requests, and embedding
underreported usage. Integration review additionally corrected JSON-mode loss,
Mem0 wall-clock dates, Graphiti clock aliases/query-role embedding, metadata-only
partial evidence, request-framing window bounds and abnormal-worker cleanup.
Each material regression has a failing-before/fixed-after behavioral check.

D-21 requires its approved development probe, actual extraction stability and
projected token/cost measurements. The local SDK checks do not include or exclude
B4. Pilot freezes, human/supervisor approvals, paid model access, corpus baselines,
one-shot test runs, judge validation, H1–H4 outcomes and full-engine temporal or
seven-channel deletion conformance remain separate work.
