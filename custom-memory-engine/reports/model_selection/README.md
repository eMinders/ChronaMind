# Model selection — measured, not assumed (revised 2026-11-25, `V2-D102`)

Everything below was measured through the provider's own API by `scripts/eval_models.py`, on the same 46-case set, with
the prompts the engine ships. The raw results are in `evaluation.json` (per-case failures included); the earlier sweeps
are kept in `sweep1/`, `sweep2/` and `final/`. **No model was selected from a description or a leaderboard.**

## What the provider can and cannot do

| capability | answer |
|---|---|
| chat completions | yes, **one protocol per model family**: Claude on `/v1/messages`, GPT on `/v1/chat/completions`. The other shape returns `400 protocol_mismatch`. |
| `/embeddings` | **no** — `403` for every candidate name tried (`text-embedding-3-small`, `text-embedding-3-large`, `bge-m3`, `multilingual-e5-small`, `qwen3-embedding-8b`, `embed-v3`). |
| `response_format: json_object` | accepted by GPT-6.x; **not** by Claude models (`400`), which is why `integrations/anthropic_messages.py` asks for JSON in the system prompt instead. |
| `temperature` | rejected by several models (`400 Omit temperature for this model`). The client learns this per model and drops the field rather than hard-coding a matrix. |

Because embeddings are unavailable, the dense arm is served by a **local `bge-m3`** (`integrations/onnx_embedder.py`,
1024-d, CLS pooling, `halfvec(1024)` slot). Without it the engine has no dense arm at all, and `reports/retrieval/`
shows what that costs (recall@5 0.393 lexical against 0.982 hybrid).

## The set, and the defect the expansion found

46 distinct cases in Round 2: 30 extraction (14 English, 10 Arabic, 6 mixed-script including Arabizi and code-switching),
14 adjudication pairs (including an Arabic pair and a cross-language pair), and 2 query rewrites — plus 3 schema checks
scored on extraction outputs. (Round 3 expanded `ALL_CASES` to 57 cases by adding 5 mixed-script extraction cases and
6 adjudication pairs, alongside 12 entity-adjudication cases.)

**A real harness defect surfaced when the set grew.** The first adjudication block used the engine's *strategy* names
(`supersede`, `reinforce`) as expected labels. Models are asked for `duplicate|update|correction|contradiction|additive|
unrelated` and can never emit either, so correct answers were scored as failures — every model looked like it scored 5-6 of
14, when a move answered `update` is exactly right. After remapping the labels to the model vocabulary the same run scores
10-13 of 14. `tests/test_eval_harness.py::test_every_adjudication_label_is_a_relation_a_model_may_emit` fails if a label
leaves the vocabulary again. **Every adjudication figure published before this correction is void**, including the
"all three models tie" conclusion in the previous revision of this file.

## What was measured (46 cases, `evaluation.json`)

| model | ext-en | ext-ar | ext-mixed | schema | adjudication | quote grounded | JSON failures | p50 | p95 | tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| `gpt-6.2-sol` | **13/14** | **10/10** | **6/6** | 3/3 | **13/14** | **100%** | **0** | 9.0 s | 19.2 s | 288 k |
| `claude-sonnet-5-5` | 10/14 | 8/10 | 3/6 | 3/3 | 11/14 | 100% | 0 | 6.4 s | 11.1 s | 47 k |
| `claude-haiku-5-5` | 12/14 | 8/10 | 2/6 | 3/3 | 10/14 | 100% | 0 | 4.9 s | 10.3 s | 61 k |

## Round 3 — scored by the write, with entity adjudication (`V2-D107`, `round3/`)

**What changed in the instrument, and why it matters more than the numbers.**

1. **Adjudication is scored by the engine's write, not by the label.** The previous scorer accepted `duplicate`,
   `update` and `correction` for one another. The engine *reinforces* on `duplicate` and *supersedes* on the other two,
   so a move answered "duplicate" kept the old address and was still scored correct. `cases.strategy_of` now calls
   `conflict.commit_strategy` itself, so the scorer and the engine cannot disagree about what an answer does.
2. **`additive` is in the prompt.** It was always in the engine's vocabulary (`RELATIONS`) and never offered to the
   model. Without it, "I work remotely three days a week" against "I work remotely" had no correct answer that keeps
   the new detail; two cases that had been labelled `duplicate` for that reason are relabelled and argued in place.
   The prompt also says the two statements may be in different scripts.
3. **Entity adjudication is measured** through the shipped adapter (`OpenAiCompatibleEntityAdjudicator`), on 12 cases
   of which 5 must *not* merge. False merges and misses are counted separately, because they cost differently.
4. **Five more mixed-script extraction cases** (Arabizi, code-switching mid-sentence).

| model | mixed-script extraction | quotes grounded | JSON failures | adjudication (20) | entity (12) | false merges | p50 |
|---|---|---|---|---|---|---|---|
| `gpt-6.2-sol` | **10/11** | 16/16 | **0** | **20/20** | 10/12 | **1** | 11.6 s |
| `claude-haiku-5-5` | 7/11 | 12/12 | 2 | 17/20 | 10/12 | **0** | 2.8 s |
| `claude-sonnet-5-5` | 6/11 | 14/14 | 2 | 17/20 | 10/12 | **0** | 3.1 s |

**What the errors were.** Both Claude models answered two genuine contradictions (no change signal) as `update` or with
no relation — `update` silently supersedes a belief the engine cannot know is stale, which is exactly what `conflicts`
exists to prevent. Their mixed-script failures include invalid JSON on Arabizi turns. `gpt-6.2-sol`'s one entity
error that matters merged "Berlin" into "berlin office"; its other was an abstention. The Claude models' four entity
errors were all abstentions.

## Selection (current)

| capability | model | why |
|---|---|---|
| semantic extraction | **`gpt-6.2-sol`** | best on every extraction set, including mixed-script (10/11 against 7 and 6), the only one with zero JSON failures; 100% quote grounding |
| relationship adjudication | **`gpt-6.2-sol`** | 20/20 scored by the write; the alternatives turn contradictions into silent supersessions |
| entity adjudication | **`claude-haiku-5-5`** (`CME_ENTITY_ADJUDICATOR_MODEL`) | equal accuracy, **zero false merges** against one, and 4× faster. A false merge corrupts identity; a miss is repaired by the next mention, so the safe error profile wins the tie |
| embeddings | **local `bge-m3`** | the provider serves none; `reports/retrieval/` |
| relevance judge (read path) | **local `bge-reranker-v2-m3`** | separates answerable from unanswerable where no cosine floor can (`reports/retrieval/` round 3, `V2-D104`) |

**Sample size, stated.** 12 entity cases, 11 mixed-script extraction cases, and 20 adjudication pairs are enough to
expose the failure *kinds* above (JSON syntax errors, silent contradiction supersession, false entity merges) and **not**
enough to establish statistical superiority between close models. Exact finite-sample power (`evaluation/stats/power.py`,
`alpha = 0.05`, target `80%` power, equal arms for Fisher / discordant pairs for McNemar):

| comparison regime | observed `N` | power at observed `N` (two-sided / one-sided) | required `N` for 80% power (two-sided / one-sided) |
|---|---|---|---|
| Entity adjudication (`12/12` vs `10/12`, Fisher) | 12 | `0.0364` / `0.1252` | `46` / `39` per arm |
| Entity adjudication (`2/12` vs `0/12` discordant, McNemar) | 12 | `0.0079` / `0.0364` | `46` / `39` pairs |
| Entity false merges (`0/12` vs `1/12`, Fisher) | 12 | `0.0019` / `0.0138` | `94` / `80` per arm |
| Relation adjudication (`20/20` vs `17/20`, Fisher) | 20 | `0.1702` / `0.1702` | `52` / `44` per arm |
| Relation adjudication (`3/20` vs `0/20` discordant, McNemar) | 20 | `0.0673` / `0.1702` | `52` / `44` pairs |
| Mixed-script extraction (`10/11` vs `7/11`, Fisher) | 11 | `0.1558` / `0.2937` | `40` / `34` per arm |
| Mixed-script extraction (`3/11` vs `0/11` discordant, McNemar) | 11 | `0.0512` / `0.1544` | `28` / `24` pairs |
| No-match FP (`0/8` vs `1/8`, Fisher; see `reports/retrieval/`) | 8 | `0.0012` / `0.0112` | `62` / `53` per arm |

All four small-sample suites operate at `< 30%` power (two-sided power `0.1%–17.0%`). In particular, the entity
selection rests on the asymmetric operational cost of the observed false merge (`Berlin` → `berlin office`), not on a
claimed significance difference; a re-run showing `claude-haiku-5-5` merging falsely would reverse it.

## Round 2 selection (superseded by the table above for entity adjudication)

| capability | model | why |
|---|---|---|
| semantic extraction | **`gpt-6.2-sol`** | 29 of 30 extraction cases against 21 and 22; 100% verbatim-quote grounding; zero JSON parse failures. The margin is no longer inside noise. |
| relationship adjudication | **`gpt-6.2-sol`** | 13 of 14, against 11 and 10. |
| entity adjudication | `gpt-6.2-sol` (same client) | the cascade's last stage asks a yes/no question about a short candidate list; the same model, no second selection to justify |
| embeddings | **local `bge-m3`** | the provider serves none; measured in `reports/retrieval/` |
| reranking | **none** | measured and rejected in `reports/retrieval/` (nDCG 0.882 fused against 0.765 BM25-reranked, 0.726 recency-reranked). Round 3 of the retrieval report adds a cross-encoder *judge*, which is a different thing: it gates as well as orders |

**The documented alternative.** `claude-haiku-5-5` costs 2.5-4.9 s per call against 9.0 s and scores four adjudication
points lower. Adjudication runs once per conflicting candidate on the write path, so a deployment with a tight write
budget may legitimately choose it; that trade is recorded rather than hidden behind "gpt is better".

## Cost, tokens and context

Token counts are the harness's own accounting (`usage` summed over calls), and they explain the latency difference:
`gpt-6.2-sol` used 288 k tokens for the same run `claude-sonnet-5-5` did in 47 k, because its extraction prompt asks for
more fields and it re-reads more context. Context windows were **not** measured: the harness never approaches one (the
largest request is under 3 k tokens), so a context-window figure here would be copied from a specification rather than
measured, and this file does not publish numbers it did not produce.

## Extraction quality (21 cases: 6 English, 4 Arabic, 3 code-switched, 6 adjudication pairs, 2 questions with no answer)

| model | ext-en | ext-ar | ext-mixed | schema | quote grounded | JSON parse failures | p50 | tokens |
|---|---|---|---|---|---|---|---|---|
| `gpt-6.2-sol` | **6/6** | **4/4** | 2/3 | 3/3 | **100%** | **0** | 7.3 s | 136 k |
| `claude-sonnet-5-5` | 5/6 | 4/4 | 0/3 | 3/3 | 100% | 3 | 6.1 s | 30 k |
| `claude-haiku-5-5` | 5/6 | 2/4 | 2/3 | 3/3 | 86% | 4 | 6.0 s | 30 k |

Earlier sweeps against a different gateway, same cases, for breadth:

| model | ext-en | ext-ar | ext-mixed | schema | note |
|---|---|---|---|---|---|
| `gpt-4.1-mini` | 6/6 | 4/4 | 3/3 | 3/3 | strongest all-round, immediately usable |
| `qwen3.7-flash` | 6/6 | 4/4 | 2/3 | 3/3 | best Arabic of the cheap models, slow (3 s p50) |
| `deepseek-flash` | 5/6 | 4/4 | 3/3 | 1/3 | good multilingual recall, **unreliable JSON** |
| `mistral-small-latest` | 6/6 | 1/4 | 2/3 | 3/3 | effectively monolingual |
| `gpt-5-mini` | 0/6 | 0/4 | 0/3 | 0/3 | spent its whole budget on reasoning and returned empty content |

**The two failure modes that decide this**, and neither appears in a leaderboard:

1. **Ungrounded quotes.** Before the prompt was fixed, the best model returned verbatim quotes 25% of the time. A quote
   that is not a verbatim substring is a claim the engine *must* refuse (`grounding.ground_text`), so a 25% rate means
   three of four extracted memories never reach the store. The prompt now carries a worked example and a hard rule; the
   rate went to 86–100%. This is the single most important number in the table and the reason `EXTRACTION_SYSTEM` reads
   the way it does.
2. **JSON parse failures.** A parse failure is a lost memory with no partial recovery. `gpt-6.2-sol` had none;
   `deepseek-flash` and `claude-haiku-5-5` failed on ordinary English turns.

## Adjudication

All three candidates produced the same four correct labels and the same two "failures":

```
adj_contradiction    "I live in Berlin" / "I live in Munich"      -> update
adj_ar_contradiction "أسكن في برلين"   / "أسكن في ميونخ"          -> update
```

Measured against the strict label set that is 4/6. Read correctly, it is 6/6: an `update` says exactly what the engine
needs to know — the two cannot both be true, and the new value replaces the old — and
`conflict.commit_strategy` treats `update` and `correction` as supersession, which is the right write for a move. The
label set in the harness has been widened accordingly, and this paragraph is here because silently changing a metric to
make a number look better is exactly what a measurement report must not do.

## Round 1 selection (historical; superseded twice)

| capability | choice | why |
|---|---|---|
| extraction (claims, slots, facets, entities) | **`gpt-6.2-sol`** | the only candidate with zero parse failures, 100% grounded quotes, and 12/13 on the multilingual cases. It is the most expensive per call and that is the right place to spend: a wrong claim costs a belief, a re-extraction and possibly a supersession. |
| adjudication (conflict relation) | **`claude-haiku-5-5`** | tied with the others on the task (6/6 once the label semantics are read correctly), fastest and cheapest, and its weakness — long structured extraction — does not apply to a 3-field answer. |
| embeddings | **local `bge-m3`** | the provider serves no embedding endpoint; the local model is pinned, reproducible, and measured (see `reports/retrieval/README.md`). |
| reranking | **none** | measured: an LLM rerank on the top-5 candidates scored 10/14 top-1 against the fused order's 9.6/14 — inside noise — while abstaining on the cross-script questions the dense arm already solved. It also costs one model call per *read*, which contradicts the zero-model read path (`V2-D86`). |

Reproduce round 3 with (credentials from the env file only; `PYTHONPATH=.` so `evaluation/` is importable):

```bash
PYTHONPATH=. uv run --env-file ../.env python scripts/eval_models.py \
  --models gpt-6.2-sol claude-haiku-5-5 claude-sonnet-5-5 \
  --tasks extraction_mixed adjudication entity_adjudication --out reports/model_selection/round3
```

The quotas on the gateway used for this evaluation are limited and it returned `insufficient_quota` mid-sweep once,
so the numbers above are from the runs recorded in `final/` and `sweep1/`, not from one uninterrupted pass. Any candidate
whose row is missing a task was not measured for it; nothing here is inferred.
