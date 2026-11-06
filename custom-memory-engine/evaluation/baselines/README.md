# RL-031 baseline adapters

These six adapters implement the harness's seven-method `MemorySystemAdapter`
contract. They use one common answer path, frozen settings digest and bounded
model runtime. The root application's services, environment and database are
independent. This is software infrastructure; implementing B4 does not decide
its D-21 inclusion or authorize a pilot, paid model call or test-split run.

| System | Memory supplied to the common answerer |
| --- | --- |
| B0 | Only the explicitly identified `QuestionRecord.session_id`; a missing session means question-only. Never infer a session from the latest history. |
| B1 | Original turns in replay order, dropping oldest whole turns until the complete request fits the answer model window. |
| B2 | Exact cosine over frozen raw-turn embedding chunks. A turn's best chunk determines rank; its original record is rendered once. |
| B2h | B2 plus actual PostgreSQL English `plainto_tsquery`/`ts_rank_cd`, fused by unweighted one-based RRF with `k=60`. |
| B3 | Real Mem0 2.2.1 ADD-only extraction, deduplication and Qdrant-local semantic search. |
| B4 | Real Graphiti 0.31.2 extraction and FalkorDB hybrid edge search; inclusion remains conditional and **unmeasured**. |

`B_mem` caps retrieved context for B2/B2h/B3/B4. B0/B1 use the model window
instead, as D-09 requires. Counts include separators and actual rendered text;
only the last ranked item may be partially visible. A raw metadata header with
no utterance cannot claim source evidence. Truncation diagnostics and B1 case
counts enter artifacts. All baselines preserve source roles, named speakers and
timestamps. None adds P's extraction, write gate or temporal query analyzer.

## Install and prepare local assets

History fitting uses exact segmented counts when the frozen tokenizer certifies
the rendered turn boundaries. It selects the earliest fitting suffix without
assuming opaque token counts are monotone and validates the complete selected
request, including framing and output reserve. Other codecs retain the exact
fallback scan. The production path's tokenizer work grows linearly with rendered
history length; this does not claim arbitrary custom codecs have that bound.

Completed cases use an optional local `release_user` hook. Reset and release do
not touch storage or providers; they queue only generations that may own backend
state. The next accounted operation retires those exact scopes before dispatch.
Final close handles remaining resources. Mem0 generations own separate local
Qdrant/SQLite instances; Graphiti retirement closes the exact owned graph/client.
Active tenants remain usable. Episode mappings are generation-scoped, and completed
history diagnostics fold into aggregate counters. This teardown is resource
management, separate from the unsupported vendor seven-channel deletion metric.

From this directory's engine root (`custom-memory-engine/`):

```powershell
uv sync --frozen --extra baselines   # CPython from .python-version
uv run --extra baselines python -m evaluation.baselines.assets prepare --directory C:\chronamind-assets
```

Choose an absolute asset directory you own. Preparation is the only download
entry point. It verifies immutable official files against code-pinned sizes and
hashes; constructors and inference never download. Keep model assets out of Git.
The default dependency installation omits vendor/model packages. Public factory
imports and all six adapter constructors remain lazy with respect to those SDKs.

D-01 is `gpt-4.1-mini-2025-04-14`, temperature 0, top-p 1 and maximum completion
2048. Seed 42 is recorded; sending it requires explicit `send_seed=True` for a
supported endpoint. Settings require explicit reviewed token prices and operation
limits. No price, API key, paid access or final pilot parameter is inferred.

D-02 is `BAAI/bge-small-en-v1.5` at
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, official ONNX CPU weights and matching
tokenizer, 384 dimensions, CLS pooling and L2 normalization. The 512-token limit
includes special tokens and the query-only prefix
`Represent this sentence for searching relevant passages: `. All overflow text
survives offset chunks. Long SDK texts aggregate normalized chunk vectors then
normalize again; raw B2 indexes chunks separately. Same-length batches of at most
16 avoid padding tokens. The exact policy is part of the settings digest.

Answer context uses local pinned tiktoken 0.14.0 `o200k_base` bytes. Message framing
is a conservative estimate plus a 64-token request allowance, rather than a claim
of exact provider billing. The common answer request includes the injected UTC
reference clock, query mode and optional target time, without reporting labels
or gold answers. The request format policy enters the settings digest.

## Public construction and harness use

Load a reviewed `BaselineSettings` and `HarnessConfig` JSON record. The latter
must bind `settings.sha256()`, prompt hash, exact model/revision/tokenizer pins,
adapter version, context policy and operation limits. `model_copy` cannot bypass
validation at public assembly or shared runtime boundaries.

```python
import os
from pathlib import Path

from evaluation.baselines.contracts import BaselineSettings
from evaluation.baselines.factory import RuntimeOptions, baseline_adapter
from evaluation.harness.config import load_config

settings = BaselineSettings.model_validate_json(Path("reviewed-settings.json").read_bytes())
config = load_config(Path("reviewed-harness-config.json"))  # e.g. B2
options = RuntimeOptions(assets_directory=Path("C:/chronamind-assets"))
with baseline_adapter(
    config, settings, options, api_key=os.environ["CME_BASELINE_LLM_API_KEY"]
) as adapter:
    # Supply this adapter to run_evaluation(adapter_factory=lambda config: adapter,
    # ...) with the approved case loader and real scorer for the intended suite.
    # The harness continues to enforce campaign reservations and test-run gates.
    pass
```

`create_adapter` builds the real local-embedding/HTTPS runtime. `assemble_adapter`
accepts explicit caller model services for controlled software tests or alternative
accounted transports; metadata distinguishes injected services from the production
assembly. Never present a scripted transport's output as measured QA performance.
The existing harness CLI remains a provider-free dummy demonstration.

Additional runtime options are mandatory only for their respective system:

| System | Options |
| --- | --- |
| B2h | `postgres_dsn=SecretStr(...)` for an isolated `cme_*` database. The store creates and later drops only its ownership-marked schema. |
| B3 | `vendor_python=Path(...)` naming the absolute interpreter with locked baseline dependencies. |
| B4 | The same interpreter plus `local_graph=LocalGraphOptions(host="127.0.0.1", port=...)` for an owned disposable FalkorDB service. |

Extraneous options are rejected. Keys and DSNs are absent from manifests and
configuration digests. Constructors, reset, clock and question preparation perform
no provider or store operations. Retrieval prepares the exact context subsequently
answered; answering does not perform a hidden second retrieval. Clock/history/reset
changes invalidate prepared context. The harness closes owned resources before
publishing success, including failure paths; the outer context manager is a fallback.

## Vendor fidelity, instrumentation and limits

Parent adapters live in `vendor.py`; `b3_mem0.py` and `b4_graphiti.py` are private
isolated SDK workers. Only the B3 worker imports Mem0. Workers receive no provider
credentials, disable telemetry/dotenv and cannot select default provider clients.
Their LLM and embedding callbacks return to the common metered parent. Native
Mem0 JSON mode and Graphiti response schemas are retained; the HTTP boundary
normalizes schemas to strict required properties without modifying SDK validators.
Every provider attempt has a cap, latency and usage record. Unknown failed usage
retains a conservative bound; successful retries do not erase failed attempts.
Per-episode events include success/failure, ingestion wall time, LLM attempts,
per-attempt latency, token usage and cost. SDK retries are replaced by one bounded
parent tenacity policy. Pending worker tasks settle before reconciliation.

Worker shims are release/source-hash checked: Mem0 observation dates use the
original source date while its current/recorded dates use replay UTC; Graphiti
uses injected recorded time and distinguishes internal query embeddings from
stored document embeddings. Graphiti's native 16384-token request is explicitly
capped to frozen 2048; integration tests inspect both limits in worker IPC. The
persisted per-attempt accounting records actual dispatched usage and latency. These
are documented integration policies, not changes to vendor extraction prompts.

Mem0 uses its semantic-only fallback without spaCy/fastembed/BM25; these optional
autodownload paths are refused. Mem0 has no audited fact-to-original-evidence map.
Its six source-recall/all-found metrics are unavailable (`null`), while QA scoring
remains available. Graphiti maps real episode UUIDs to original turns; incomplete
maps also make evidence metrics unavailable. Trigger metadata is never fabricated
into gold provenance. Native retrieval does not implement P's temporal modes;
the shared answerer can reason from the question and retrieved dated facts.

Vendor deletion is reported unsupported for the seven-channel hard-erasure
contract. Mem0's native delete retains history. Raw baseline deletion covers its
current replay generation only; it does not establish full-engine Suite D claims.
Ordinary Graphiti errors and shutdown delete exact owned graph namespaces. Forced
child exit uses a separate bounded Redis-only cleanup helper. Use a disposable
run-owned service; a machine/parent crash still requires external lifecycle cleanup.

See [verification and provenance](../../evidence/RL-031-verification.md) for
commands, immutable sources and remaining research gates.
