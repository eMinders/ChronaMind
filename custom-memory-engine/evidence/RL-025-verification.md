# RL-025 benchmark adapter verification

Scope: issue #21, based on the merged RL-030 harness from main `244e20a`.
These are software tests and source-compatibility checks, not benchmark research
results, model-judge validation, real baselines or a pilot/test freeze.

## Acceptance and traceability

| Requirement | Implementation and evidence |
| --- | --- |
| Read-only oracle/S/M and LoCoMo loading | Checksum-bound streamed arrays, no fetch/repair calls, immutable input/manifest assertions, all variant identity roundtrips, mismatch/malformed/duplicate/missing-selected-ID rejection tests. Streaming retains one history at a time. |
| D-23 and R3 | Stable session order, source/recorded clocks kept distinct, cumulative microsecond ties, empty history and clamped reference-time tests; original-date anomaly sensitivity is separate. |
| Evidence mapping | Source turn/dialogue IDs map through retrieved MEMORY_EVIDENCE links; unique gold coverage and top-k truncation tests; multi-gold all-found diagnostics, primary exclusions and nullable missingness. |
| Gold isolation | PreparedCase separates immutable labels/audit from system-visible EvaluationCase; tests use unspoken gold markers, retained original source text, and collision/tenant checks. |
| LoCoMo speakers/categories | Both humans preserve source_speaker with role user in an isolated conversation tenant; numeric categories and conversation/stratum aggregation tested. No invented human assistant or category-name mapping. |
| Official scorer wrappers | Archived original source hashes, retained licenses, selected function/class AST parity, hand-computed and differential examples. Legacy Porter algorithm retained; the exact small legacy interface is retained alongside it. No upstream CLI/provider/BERT execution. |
| Synthetic B0-style smoke through both sources/scorers | Fixed local responder runs through source.run and public harness; exact LongMemEval task prompts receive scripted local judge replies; LoCoMo official category functions execute. Deterministic answer/score bytes and complete source/scorer/config/artifact provenance asserted. |
| Guard before data access | Unauthorized test run rejected before selection/source/manifest opens or adapter calls. Source/suite/split/hash/k must match selection, whose complete bytes are bound by the existing signed freeze/one-shot guard. |
| Reproducible measurement | Actual metric k recorded and checked against the frozen selection. Source SHA, exact scorer/algorithm revisions and replay audit hashes/counts in manifest. Unknown/missing recall stays null; no default perfect recall. |
| Isolation and collection | Changes only under custom-memory-engine; engine lock gains exact ijson/regex pins. Default pytest collection includes adapter/scorer tests; root product and root manifest/lock untouched. |

## Commands and results

- `uv sync --frozen --python 3.12.12` reproduces the isolated engine lock.
- Focused adapters/scorers: `uv run pytest -q research/benchmarks/longmemeval/tests research/benchmarks/locomo/tests research/benchmarks/tests`.
- Integrated suite: `uv run pytest -q` with the documented disposable PostgreSQL test environment. The final coordinator report records exact collected/passed/integration counts; GitHub checks independently run this suite for the submitted commit.
- Root and engine Ruff lint/format, both Pyright scopes, mechanical document checks, exact cp_bounds output and canonical document hashes passed during preparation of this contribution. Canonical CHANGELOG hash is updated; the frozen Proposal/checklist/reference documents are unchanged.
- The disposable database verifies PostgreSQL 16.15, vector 0.8.7, btree_gist 1.7 and 43 reduced SQL assertions; the coordinator verifies removal of all owned containers/networks/volumes. This does not validate future production schema/RLS/deletion/performance.

The local coordinator's current final report and exact PR CI are the authority for
completion, not this description alone. Do not infer a passing command from its
presence in this list.

## Independent review and regression evidence

Independent review identified three issues: a Decimal-valued JSON answer rejected
by the LoCoMo loader; missing multi-gold all-evidence-found diagnostics; and metric
k not bound to selection/provenance. Separate regression tests demonstrated each
failure before its fix. The coordinator additionally caught Decimal lexical
formatting changing official JSON-float answer text (2.50 versus 2.5); both source
loaders now preserve the official numeric conversion semantics with regressions.
A final spy-system regression also exposed answerability hints in source IDs/reporting strata. LongMemEval now uses opaque system identifiers while retaining original IDs in source audit, and the harness removes strata from answer inputs. The test failed before the fix and passes after it. No unresolved material finding was established in the independent review.

Archived upstream bytes intentionally retain original whitespace and line endings;
local .gitattributes excludes only those exact archives/licenses from whitespace
rewrites/checks. Executable subsets remain formatted/linted and AST-compared.
NLTK's unmodified legacy class retains narrow documented static-analysis
exceptions rather than changing upstream algorithm branches to satisfy style.

## Explicit limits

- New adapter/scorer tests use independently authored synthetic fixtures. Existing
  acquisition tests internally verify approved public caches. All three actual
  pinned LongMemEval files additionally passed full mechanical source-schema,
  envelope and clock validation (500 records each; 414 primary-eligible in S).
  No real system/scorer/model inference, pilot, confirmatory split, judge
  validation or H1-H4 result occurred. CI machines without S/M still exercise
  their source-mode contracts through synthetic fixtures.
- During implementation one worker's broad search incidentally emitted timestamp-
  only metadata lines from the existing public LoCoMo cache. No dialogue, questions,
  gold labels or actual split membership was inspected through that search; later
  searches were confined to source/docs. Do not claim zero payload-byte access.
- LoCoMo is text dialogue only. Images, generated captions/observations and source
  summaries are excluded consistently; downstream systems must honor source_speaker.
- Current selections use versioned JSON, a YAML1.2-compatible subset. RL-026 owns
  actual split generation, full YAML requirements and genuine J5 approval.
- LongMemEval's official prompt/legacy yes-substring compatibility with a fake local
  judge is software evidence only. Real provider judges need RL-033's accounted
  transport and real development-only human validation; no model access is implied.
- Dataset/hash verification is cooperative local protection, not a hostile local
  user or distributed authorization service. Existing research gates still apply.

A final isolated subprocess import check exceeded its 30-second startup bound. Import tracing showed unnecessary full NLTK toolkit initialization for a small base interface. The exact licensed legacy StemmerI class is now retained with PorterStemmer; the full NLTK dependency is removed. The same import test keeps its original deadline and asserts that NLTK/provider/model modules never load. The prior timeout is retained in coordinator evidence rather than hidden by retries or a widened deadline.

A final mechanical schema check of already-approved local public caches exposed
LoCoMo source quirks absent from the first synthetic fixtures: unpaired date-only
metadata and compound/dangling evidence annotations. Synthetic regression tests
failed first, then the loader was corrected to retain records and unresolved
annotations without fabricating dialogue/evidence or claiming a measured recall.
The content-free audit now validates 500 oracle records and 10 LoCoMo
conversations/1,986 questions. It counts 16 unpaired date keys and 5 questions with
unresolved evidence; no source content, IDs, gold answers or split membership was
saved. No system, scorer, model, replay execution or research evaluation ran.

## Follow-up review and complete source-format coverage

The public S (277,383,467 bytes) and M (2,737,100,077 bytes) files were acquired
using the existing immutable source revision and committed manifests. Full
content-free mechanical normalization now passes all 500 records in each of
oracle/S/M and all 10 LoCoMo conversations. Raw checksums stay intact; only
aggregate counts and hashes are saved outside the raw cache. Raw assets stay
gitignored and are not committed.

The first S pass rejected 13 histories because repeated source-session labels
were incorrectly assumed invalid; 449 M histories also contain repeated labels.
Two synthetic regressions failed before the
fix, covering reverse date order and tied dates with identical labels. The
loader now retains every original array occurrence with distinct indexed IDs,
preserves all annotated gold turns, and records replay_session_indices so the
sort permutation remains unambiguous. All 414 primary-eligible S questions are
retained. This replaces the earlier conditional S/M source-format limitation;
it does not establish real inference, judge quality or research outcomes.
The exact S/M source-date anomaly counts were recomputed separately and are
recorded in ADAPTERS.md; no development/test membership was generated.

A separate malformed-input regression exposed exceptions raised inside ijson's
C callback leaking as SystemError. The callback now records duplicate-key flags
without raising; iterative validation rejects duplicates before yielding. Eight
duplicate shapes and a valid distinct-object case cover nested/escaped keys,
overwritten values, per-record behavior and unchanged raw bytes. Three checks
failed before this correction, then all focused checks passed. The exception
catch remains limited to parser JSON errors.

Independent review checked both changes and compared official scorer closures
against independently compiled source on 2,500 synthetic LoCoMo cases, 12 exact
LongMemEval prompts and six reply-parser cases. No material finding remains in
that scoped review. The final coordinator report and exact submitted-head CI
must still establish current integrated test counts and successful checks.
