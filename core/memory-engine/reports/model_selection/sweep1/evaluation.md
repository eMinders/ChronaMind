# Model evaluation — measured through the provider API

Endpoint: `https://api.pateway.ai/v1`  ·  models: 3  ·  cases: 21  ·  2026-11-22T00:02:09Z

Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear
verbatim in the turn — the property that decides whether an extraction is usable at all.

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |
|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 6/6 (g 10%) | 3/4 | 2/3 | 3/3 | 4/6 | 0/2 | 6388.9 | 11259.4 | 24244 |
| `claude-sonnet-5-5` | 5/6 (g 14%) | 4/4 | 1/3 | 3/3 | 4/6 | 0/2 | 5504.7 | 12389.9 | 25370 |
| `gpt-6.2-sol` | 6/6 (g 25%) | 4/4 | 2/3 | 3/3 | 4/6 | 0/2 | 7815.7 | 11428.8 | 143467 |

## Embeddings

The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not evidence that embeddings are served. Where it is unavailable the deployment embeds locally (`integrations.onnx_embedder`) so the dense arm stays measurable.
