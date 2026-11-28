# Entity resolution — does a vector stage earn its place? (`V2-D101`, migration 26)

Entity resolution decides identity: which stored entity a mention is. A wrong answer is not a ranking error — it attaches
one person's (or project's) claims to another, permanently, through the typed foreign key that makes the model sound. So
the question was never "does a model help", it was "does a model help *more than it costs in false merges*".

The measurement below is what the shipped cascade answers on a hand-written set of 25 mentions over 6 entities, using the
real `bge-m3` model. It is reproduced by the offline probe whose numbers are quoted here (cosine values as printed), and
the behaviour it justifies is asserted in `tests/integration/test_semantic_entities_db.py`.

## What the deterministic stages handle

| mention | kind | what resolves it |
|---|---|---|
| `Layla Hassan`, `layla hassan` | same label, case | exact (normalized) |
| `Dr. Yusuf Kamal`, `Weber`, `Alpha`, `Sara`, `Okafor` | substring of the canonical name | trigram |
| `the landlord` | paraphrase of "Herr Weber" | trigram (0.52) then adjudication |
| `Munich office` vs `Berlin office` | *different* entity, shared suffix | trigram proposes, adjudication refuses |

## What only a vector can reach — and what it cannot be trusted with

Cosine similarity, mention (query side) against each entity's canonical name:

| mention | correct entity | best match | second |
|---|---|---|---|
| `مكتب برلين` ("Berlin office") | Berlin office | **0.791** (correct) | 0.491 |
| `ليلى حسن` ("Layla Hassan") | Layla Hassan | **0.573** (correct) | 0.403 |
| `يوسف كمال` ("Yusuf Kamal") | Dr. Yusuf Kamal | **0.500** (correct) | 0.370 |
| `سارة` ("Sara") | Sara Okafor | **0.585** (correct) | 0.442 |
| `the landlord` | Herr Weber | **0.521** (correct) | 0.455 |
| `Munich office` | *nothing* | **0.769 — Berlin office** | 0.453 |
| `Project Beta` | *nothing* | **0.791 — Project Alpha** | 0.460 |
| `the dentist` | Dr. Yusuf Kamal | **0.510 — Herr Weber** | 0.471 |

Two facts, and both matter:

1. **Cross-script mentions are real and only the vector reaches them.** `ليلى حسن` shares no trigram with `Layla Hassan`
   (0.0); the cosine is 0.573. This is the case that cannot be served by any amount of string matching.
2. **A vector score is not identity evidence.** `Munich office` scores 0.769 against `Berlin office` and `Project Beta`
   0.791 against `Project Alpha`: two *different* entities that differ by one token are nearly identical in embedding
   space. Any threshold that admits the cross-script matches admits these too — the distributions overlap exactly where
   the decision is destructive.

## The decision

**The stage is implemented (migration 26) and proposes only.** `semantic.entities.resolve_mention` runs the vector stage
*only* when exact, alias and trigram found nothing, requires `VECTOR_FLOOR = 0.75`, filters by `embedding_model`, and
returns a **candidate**. The existing fence then applies unchanged: a merge needs an exact match or an adjudicated
confirmation, so `Munich office` proposes Berlin and stays `unresolved` unless a model confirms a merge that is false.
`tests/integration/test_semantic_entities_db.py::test_a_different_entity_of_the_same_shape_is_proposed_but_never_merged`
is that requirement as a regression test.

**What the floor buys and costs.** At 0.75 the proposals are `مكتب برلين` (0.791) and, just below, nothing else — which is
conservative on purpose. 0.55 would also propose `ليلى حسن` and `سارة`; it would also propose `Munich office` and
`Project Beta`. Since a proposal is free (the adjudicator decides) and a merge is not, the floor could be lowered — but it
is not, because a proposal that is wrong costs a model call and an `unresolved` count, and 0.75 keeps the count honest
about which mentions the deterministic stages actually failed on.

**What is deliberately not claimed.** A deployment with no adjudicator gets proposals and refuses them
(`unresolved`), which is the correct behaviour and not a complete one: cross-script mentions resolve only where a model
confirms them. The alternative — merging on cosine — is the failure this measurement exists to prevent.
