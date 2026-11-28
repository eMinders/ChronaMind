# Model evaluation — measured through the provider API

Endpoint: `https://api.pateway.ai/v1`  ·  models: 3  ·  cases: 21  ·  2026-11-22T00:23:51Z

Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear
verbatim in the turn — the property that decides whether an extraction is usable at all.

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |
|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 5/6 (g 86%) | 2/4 | 2/3 | 3/3 | 4/6 | — | 5967.6 | 10745.6 | 29735 |
| `gpt-6.2-sol` | 6/6 (g 100%) | 4/4 | 2/3 | 3/3 | 4/6 | — | 7256.3 | 14048.5 | 136147 |
| `claude-sonnet-5-5` | 5/6 (g 100%) | 4/4 | 0/3 | 3/3 | 4/6 | — | 6145.2 | 11161.6 | 30000 |

## Embeddings

The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not evidence that embeddings are served. Where it is unavailable the deployment embeds locally (`integrations.onnx_embedder`) so the dense arm stays measurable.
