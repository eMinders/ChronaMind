# Retrieval evaluation (`V2-D100`)

49 memories, 64 questions, top 5, measured through the real read path
real read path; `bm25` and the two rerank rows are computed offline over the same candidates.

| configuration | recall@5 | top-1 | MRR@5 | nDCG@5 | no-match FP | misses |
|---|---|---|---|---|---|---|
| `recency` | 0.089 | 0.018 | 0.041 | 0.053 | 1.000 | 59 |
| `lexical` | 0.393 | 0.375 | 0.384 | 0.386 | 0.000 | 34 |
| `hybrid` | 0.982 | 0.732 | 0.847 | 0.882 | 0.125 | 2 |
| `hybrid_judged` | 0.982 | 0.768 | 0.867 | 0.896 | 0.000 | 1 |
| `bm25` | 0.143 | 0.071 | 0.094 | 0.106 | 1.000 | 56 |
| `hybrid_then_bm25` | 0.982 | 0.482 | 0.691 | 0.765 | 0.125 | 2 |
| `hybrid_then_recency` | 0.982 | 0.411 | 0.640 | 0.726 | 0.125 | 2 |

### Recall by question kind

| configuration | lexical | paraphrase | cross_script | no_match |
|---|---|---|---|---|
| `recency` | 0.333 | 0.000 | 0.000 | 0.000 |
| `lexical` | 0.733 | 0.333 | 0.000 | 1.000 |
| `hybrid` | 1.000 | 0.970 | 1.000 | 0.875 |
| `hybrid_judged` | 1.000 | 0.970 | 1.000 | 1.000 |
| `bm25` | 0.067 | 0.182 | 0.125 | 0.000 |
| `hybrid_then_bm25` | 1.000 | 0.970 | 1.000 | 0.875 |
| `hybrid_then_recency` | 1.000 | 0.970 | 1.000 | 0.875 |
