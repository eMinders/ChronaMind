# Model evaluation — measured through the provider API

Endpoint: `https://api.pateway.ai/v1`  ·  models: 1  ·  cases: 21  ·  2026-11-21T23:53:45Z

Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear
verbatim in the turn — the property that decides whether an extraction is usable at all.

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |
|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 6/6 (g 0%) | 4/4 | 2/3 | 3/3 | — | — | 6983.9 | 13072.3 | 19641 |

## Embeddings

The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not evidence that embeddings are served. Where it is unavailable the deployment embeds locally (`integrations.onnx_embedder`) so the dense arm stays measurable.
