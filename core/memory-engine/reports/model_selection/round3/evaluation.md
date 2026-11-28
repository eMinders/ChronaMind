# Model evaluation — measured through the provider API

Endpoint: `https://api.pateway.ai/v1`  ·  models: 3  ·  cases: 57  ·  2026-11-25T14:33:59Z

Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear
verbatim in the turn — the property that decides whether an extraction is usable at all.

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |
|---|---|---|---|---|---|---|---|---|---|
| `gpt-6.2-sol` | — | — | 10/11 | — | 20/20 | — | 11636.2 | 28989.8 | 252181 |
| `claude-haiku-5-5` | — | — | 7/11 | — | 17/20 | — | 2813.6 | 7076.5 | 35268 |
| `claude-sonnet-5-5` | — | — | 6/11 | — | 17/20 | — | 3062.9 | 10962.2 | 31001 |

## Embeddings

The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not evidence that embeddings are served. Where it is unavailable the deployment embeds locally (`integrations.onnx_embedder`) so the dense arm stays measurable.
