# Model evaluation — measured through the provider API

Endpoint: `https://api.pateway.ai/v1`  ·  models: 3  ·  cases: 46  ·  2026-11-25T01:16:12Z

Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear
verbatim in the turn — the property that decides whether an extraction is usable at all.

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |
|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | — | — | — | — | 10/14 | — | 2511.2 | 4300.5 | 5637 |
| `claude-sonnet-5-5` | — | — | — | — | 11/14 | — | 3737.5 | 8171.6 | 4121 |
| `gpt-6.2-sol` | — | — | — | — | 13/14 | — | 9134.2 | 16976.2 | 79091 |

## Embeddings

The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not evidence that embeddings are served. Where it is unavailable the deployment embeds locally (`integrations.onnx_embedder`) so the dense arm stays measurable.
