# ChronaMind custom memory engine — working notes

Project context for `custom-memory-engine/`, read by every Claude Code session and by Claude Code Review for changes
under this path. The repository-wide rules are in the root [`CLAUDE.md`](../CLAUDE.md) and [`REVIEW.md`](../REVIEW.md);
this file is what you need in addition before touching the engine.

**What it is.** A PostgreSQL-backed cognitive memory engine: bi-temporal artifacts (episodes, claims, procedures,
abstractions) with scope isolation, lifecycle governance, staged retrieval, and deletion that reaches every derived
artifact. It is a research project with a thesis attached, so its governance documents and its statistics are as
load-bearing as its code.

---

## Authority: which document wins

Read in this order, and when two disagree, the earlier one is right and the later one is a defect to report.

1. **[`PLAN/V2/ARCHITECTURE_V2.md`](PLAN/V2/ARCHITECTURE_V2.md)** — the target model: the axes, the artifacts, the
   closed lifecycle transition table, scope hierarchy, staged retrieval, deletion ordering, and invariants `I1′`–`I17`.
   Normative for implementation.
2. **[`PLAN/V2/DECISIONS_V2.md`](PLAN/V2/DECISIONS_V2.md)** — `V2-D01`…`V2-D84`: the binding record of what was decided,
   why, and what it supersedes. An amendment here beats the architecture text **only** for the passage it names.
3. **[`PLAN/V2/INVARIANT_LEDGER.md`](PLAN/V2/INVARIANT_LEDGER.md)** — one row per declared invariant with its enforcement
   status (`ENFORCED` / `PARTIAL` / `UNENFORCED` / `PLANNED`), its `path::symbol` site, its tests, and its honest gap with
   an owner. **This table, not the architecture text, says what is actually enforced today.**
4. **[`PLAN/V2/THREAT_MODEL.md`](PLAN/V2/THREAT_MODEL.md)** — `T-01`…`T-30`: every path by which a caller could learn
   that another tenant's memory exists, with its mitigation, test and status.
5. **[`PLAN/V2/CHANNELS.md`](PLAN/V2/CHANNELS.md)**, **[`DELETION_ORDER.md`](PLAN/V2/DELETION_ORDER.md)**,
   **[`COVERAGE.md`](PLAN/V2/COVERAGE.md)** — channel membership and integrity (`C-P1`…`C-P6`, `R1`…`R5`), the two-phase
   deletion order, and which task owns which architecture element.
6. **[`PLAN/V2/ROADMAP_V2.md`](PLAN/V2/ROADMAP_V2.md)** — waves W0–W8, gates, task IDs, and §4.1's register of the
   operations that are still unwired. **[`PLAN/V2/CLOSURE_PROTOCOL.md`](PLAN/V2/CLOSURE_PROTOCOL.md)** — what closes an
   issue, and why a passing test name is not enough.
7. **[`PLAN/CONTRIBUTING.md`](PLAN/CONTRIBUTING.md)** — §1.1 is the review standard itself, §1.2 branch protection, §1.3
   repository security settings.

Frozen and checksum-bound (`validation/document_hashes.txt`): `Graduation_Project_Proposal.md`,
`Methodology_and_Consistency_Checklist.md`, `CHANGELOG.md`. `TECHNICAL_SPECIFICATION.md` §3.3–3.5 is frozen in substance.
`PLAN/` outside `V2/`, `PROJECT_ROADMAP.md`, `REFACTORING.md` and `Graduation_Project_Proposal.md`'s phase plan are the
**superseded v1** record: history, never instruction for new work.

## Where the state is today (verified on `main`, 2026-11-21)

- Waves **W0–W3 complete**, gate **G3** signed off; **W3.5** (`E-040`–`E-045`) and **W3.6** (`E-046`–`E-052`) landed. W4
  and later are **not authorised** without the Project Owner's instruction (`PLAN/V2/APPROVAL.md`, `ROADMAP_V2.md` §4.1).
  The component map is [`PLAN/V2/COMPONENT_MAP.md`](PLAN/V2/COMPONENT_MAP.md).
- **All 31** contract operations dispatch through `Engine.call`; `api/wiring.UNWIRED_OPERATIONS` is **empty** and
  asserted. `E-048` (`V2-D97`) wired `stats`, `verify` (`I11`), `dissent`, `expire`, `rebuild_index` and `export`
  (migration 25, an epoch-bound manifest); `E-053` (`V2-D105`) added `lift` (reopen a dissent on *grounded* evidence;
  deleting that evidence re-blocks) and `constraints`, so `I16` is `ENFORCED`. `refute` and `host/` landed in `E-046`.
- **The engine owns the query embedding** (`V2-D98`): `ReadHandlers` takes the deployment's `Embedder`, so a client sends
  text and gets hybrid recall; `assert`/`ingest_episode` embed the document with the same model, and the dense arm joins
  on `embedding_model`. A model failure degrades to the lexical arms and `QueryResult.arms` says which ran.
- **The worker is engine code** (`custom_memory_engine.worker`, `V2-D106`); `scripts/run_worker.py` is only the
  process. Every model call — extraction, embedding, both adjudicators — runs with **no transaction open**:
  `semantic/judgements.py` dry-runs the write to collect its questions, answers them, then writes with replay adapters.
  Every slow-lane transaction runs under `CME_RUNTIME_ROLE`; the turn and the deletion epoch are read together and a
  stale epoch dead-letters the job (`F5`). Tested in-process and as a real child with SIGTERM/SIGKILL/restart.
- **Models are chosen by measurement** (`V2-D102`, `reports/model_selection/README.md`): extraction **and** adjudication
  on `gpt-6.2-sol` (29/30 extraction, 13/14 adjudication, 100% quote grounding, zero JSON failures), with
  `claude-haiku-5-5` the documented latency fallback; embeddings **locally** (`bge-m3` ONNX), because the provider serves
  no `/embeddings`. Read path: an optional **relevance judge** (`bge-reranker-v2-m3` ONNX, `V2-D104`) gates the fused
  page at a measured floor; no-match false positives 0.125 → 0. BM25 and order-only reranking stay rejected.
- **Migrations 1–26** applied and registered; rollbacks 25/26 refuse to discard data unless
  `SET LOCAL cme.rollback_discard` names it. The suite is **4,520+ passed / 21 skipped** against PostgreSQL 16.15 + pgvector 0.8.7 + `pg_trgm`; the three extensions are
  bootstrapped into `public` by `migrate.EXTENSIONS`, so the catalog fingerprint never carries another project's
  functions.

## How it runs

- **One door.** `api/entry.py::Engine.call` authorises, validates argument names against `api/contract.py`'s 29
  operations, enforces every declared condition code (`CONDITION_CHECKS`, in `CONDITION_ORDER`), dispatches, and maps
  every failure to the seven static codes of `API_V2.md` §1.1. `internal_error` carries no message and is returned only
  by `Engine.call` (`V2-D58`). `api/wiring.py::wire_engine` is the one composition a deployment uses.
- **One tenant boundary.** Every statement a handler runs goes through `api/runtime.py::Runtime.session`, which enters
  `security/scoping.py::tenant_scope`: `SET LOCAL ROLE` to the restricted role plus `set_config('app.user_id', …, true)`,
  in one transaction, on an idle connection. Row-level security then decides what the caller can see, so an id it may
  not read is absent to the engine itself — which is why "absent" and "unreadable" are the same answer (`T-11`, `T-18`).
- **The database is the guarantee.** Lifecycle legality, provenance, the belief-time watermark, scope read policy,
  tenant-integrity foreign keys, association caps, procedure shape, tombstone behaviour and the records of entry into the
  lifecycle are `CHECK` constraints, `EXCLUDE` constraints, and deferred constraint triggers — not handler code. The
  runtime role is `NOSUPERUSER NOBYPASSRLS` with the exact per-table grant matrix in `security/provisioning.py::
  TABLE_GRANTS`, mirrored by `security/rls.sql`.
- **Documented and *enforced* are different.** `INVARIANT_LEDGER.md` is the difference;
  `tests/test_invariants_declared_and_enforced.py` fails if a declared invariant has no honest row. Two catalog rules bind
  **every future migration**: `tests/integration/test_tenant_integrity_db.py` (both catalog rules are named tests there).
- **Migrations are append-only and checksum-bound.** `MIGRATIONS` in `schema/migrate.py` never loses or edits an entry;
  a new migration needs its source and its matching rollback source (the same file name under `schema/rollback/`),
  restoring the previous catalog fingerprint exactly, an entry in `MIGRATIONS`, an integration test named for it, and a
  paragraph in `schema/README.md`. Shipped sources are never edited — corrections go in a new migration or an additive
  note in `schema/README.md`.
- **Two linters, two interpreters, on purpose.** This project's CPython is 3.12 and its Ruff/Pyright live here; the root
  application's do not apply (`PLAN/INDEPENDENCE_BOUNDARY.md` §4, and the root `pyproject.toml` excludes this directory).
  `tests/engine_source_identity.py` pins the engine's own identity.

## The semantic layer (`src/custom_memory_engine/semantic/`)

Added by `W3.5` (`V2-D88`–`V2-D91`). It sits **above** the entry point and outside every invariant: it turns language
into the candidates `api.ingest_handlers` / `engine.ingest.apply_candidate` already accept, and every row it writes goes
through them. Removing the package degrades the engine to what it was before, it does not leave a hole in the ledger.

| module | owner of | what must stay true |
|---|---|---|
| `contracts.py` | the typed boundary and the three protocols (`StructuredExtractor`, `RelationAdjudicator`, `Embedder`) | imports **no** provider SDK; the closed enumerations equal their canonical homes (`tests/test_semantic_contracts.py` pins them) |
| `grounding.py` | quote exists, specifics appear | folds NFC + whitespace runs only — never case, never diacritics; a one-character quote is refused |
| `authority.py` | `USER > SYSTEM > ASSISTANT > THIRD_PARTY` | its Python twin must equal migration 24's generated `authority_rank`; a weaker source never supersedes a stronger one |
| `slots.py` | one normalization of an open slot key | must equal the schema `CHECK` (`memory_version_semantic_slot_check`) |
| `temporal.py` | bounds are aware, ordered, anchored, non-empty | only `planned` may begin after the utterance |
| `entities.py` | cascaded mention resolution and creation | a vector proposal is `unresolved` unless an adjudicator confirms it; an answer outside the offered ids is an abstention (`V2-D106`) |
| `conflict.py` | recall → adjudicate → commit | the GiST constraint gets the last word; a hallucinated id inserts |
| `ingestor.py` | the two lanes | one clock tick per transaction; a refusal is a typed outcome, never a silent drop; `apply_extraction` is the pure-database half a worker calls |
| `slow_lane.py` | claim → model → complete | every transaction under the runtime role; `MAX_ATTEMPTS` then `dead_letter`; a stale deletion epoch is terminal at once |
| `judgements.py` | every model answer a write needs | asked before the write transaction; the write may only replay, and abstains on anything unrecorded |

**Two traps here.** `halfvec` rejects PostgreSQL's `{...}` literal, so a vector always goes through
`engine.hybrid.vector_literal` (`[...]`); psycopg's `list` adapter works for the legacy 384 slot and fails for every
multilingual one. And `similarity()` lives in whichever schema `pg_trgm` was installed into — never
`pg_catalog.similarity`; use `engine.hybrid.trgm_schema`.

## The integrations layer (`src/custom_memory_engine/integrations/`, `V2-D94`)

The only place a provider, a model id or a wire protocol exists; `tests/test_integrations.py` scans `src/` for model
names. `wire.py` holds the shared primitives, `openai_compatible.py`/`anthropic_messages.py` are the two wire shapes,
`provider.client_for` picks by family (override wins), `onnx_embedder.py` runs a pinned ONNX model.

**The trap.** One protocol per family: Claude on `/v1/messages`, GPT on `/v1/chat/completions`, the other answering
`400 protocol_mismatch`. A cross-protocol retry would double-bill and hide it; family guess + `CME_LLM_PROTOCOL`.

**And the trap in the instrument.** Adjudication is scored by the *write the engine would make*
(`cases.strategy_of` calls `conflict.commit_strategy`), never by label (`V2-D107`): accepting `duplicate` for `update`
hid answers that keep a stale value. Labels stay in the model's vocabulary; a test pins them to `RELATIONS`.

## Things that will bite you

1. **A fix without a control is not a fix.** A stated guarantee is unproven until a mutation shows its test fails
   without it: every bug fix lands with a test that fails against the unmodified code, and every guard with a refusal
   *and* a positive control. `#229` is the exemplar: nine regressions, eight failing on the parent commit.
2. **Two implementations of one rule.** The SQL guard and its Python twin are kept equal deliberately —
   `engine/procedure_shape.py` against migration 21's `guard_procedure_shape`,
   `scope/access.py::rls_read_using` against migration 11's `*_scope_read` policy, `model/axes.py::LEGAL_LIFECYCLE`
   against migration 7's `memory_version_lifecycle_legal_for_kind`. Changing one and not the other is a defect even when
   both tests pass, because each test only knows its own twin.
3. **Deferred constraint triggers fire at `COMMIT`.** A violation surfaces at the wrong statement or not until the
   transaction ends. `engine/deletion.py::ARCHIVAL_GUARD_TRIGGERS` lists them so deletion judges the final state; a new
   deferred trigger belongs there too.
4. **`SECURITY DEFINER` and GUC gates.** `app.user_id` and `app.deletion_active` are ordinary session settings any
   caller can set. A privileged function gated only by them is a privilege escalation: it must additionally require a
   durable row written in the same transaction (migration 22's `deletion_in_this_transaction`), and `EXECUTE` must be
   revoked from `PUBLIC` wherever the intended caller is not already the runtime role (#228).
5. **Scope matching is `pg_catalog.starts_with` on canonical paths, never `LIKE`.** `LIKE` treats `_` and `%` inside an
   id as wildcards and `/u/4/` matches `/u/42/` — reproduced on PostgreSQL 16.15 (`V2-D35`, `T-01`, `T-02`). Paths carry
   a mandatory trailing separator and are parsed by `scope/paths.py`; a malformed path is a grammar error, not
   `forbidden` (`V2-D65`).
6. **`archived` is not a lifecycle value and `stale` is not stored.** `retired` is the lifecycle; retention is a cost
   policy that never changes answerability (`V2-D38`, `I6`). Staleness is derived from the provenance-closure hash
   (`I13`), so storing it lets the stored value disagree with the graph — which is exactly what a deletion creates.
7. **Abstractions never render bare.** `engine/channels.py::BoundedFact` makes an unbounded abstraction unconstructible,
   and every read path (`query`, `get`, `change`, `refine`) must route through `assemble` with its budgets and coverage.
   `#229` is the example: `get` built its `facts` and `hypotheses` from the raw row instead of assembling, so a verified
   abstraction could render as a bare proposition. `test_w3_audit_closures_db.py` now pins the routing, and the five
   regressions that caught it fail against the parent commit.
8. **Principles, not substitutes** (root `AGENTS.md`): its "all imports at the top of the file" rule is **false for this
   project** — `engine/__init__.py` and `engine/planning.py` use lazy imports by design — and so is "every route has a
   rate limiter" for four `app/api/v1/auth.py` session routes. Neither is a finding here; both are documented drift.
9. **Governance moves in the same commit.** A behaviour change updates `INVARIANT_LEDGER.md` (status, site, tests, gap,
   owner), `ROADMAP_V2.md` §4.1 (wired/unwired), `COVERAGE.md` / the generated `COVERAGE_MATRIX.md`, `THREAT_MODEL.md`
   rows, and the `V2-D##` record that decides it. `scripts/check_plan_refs.py`, `scripts/audit_status.py --check`,
   `scripts/plan_coverage.py --check` and `scripts/check_evidence.py` fail when they drift.
10. **Closure is a procedure.** Write the record to a file, run `scripts/check_evidence.py <record>`, `gh issue comment
    --body-file` (a body passed to `gh issue close` is silently ignored), *then* close, then read the issue back. A row
    needs a result, not "passed", and a criterion that cannot be met yet leaves the issue open.

## Commands

**Iterating** (no database, seconds):

```bash
uv run --no-sync pytest -q -m 'not integration'
```

**The full suite** (what CI `verify` runs; one private database per xdist worker, each test on its own clone):

```bash
docker compose --env-file .env -p cme-verify up -d --wait   # disposable PostgreSQL on :5433
uv run --no-sync pytest -q -n auto                          # what CI `verify` runs
docker compose --env-file .env -p cme-verify down -v
```

**The six static gates** (all of them are required CI, and all of them run without a database):

```bash
uv run --no-sync ruff check src tests research/benchmarks evaluation scripts
uv run --no-sync ruff format --check src tests research/benchmarks evaluation scripts
uv run --no-sync pyright
uv run --no-sync python -m tests.engine_source_identity
md5sum -c validation/document_hashes.txt
python3 validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt
uv run --no-sync python scripts/check_statistics.py
uv run --no-sync python scripts/check_test_split_gate.py
python3 scripts/check_plan_refs.py
python3 scripts/audit_status.py --check
uv run --no-sync python scripts/plan_coverage.py --check
uv run --no-sync python scripts/check_evidence.py --plan-document PLAN/V2/*.md   # advisory here
```

**Database work.** A URL must name host, user and database explicitly; `RL_DATABASE_URL` for the runner, and
`CME_TEST_DATABASE_URL` for the suite. The runner never reads a dotenv file.

```bash
uv run python -m custom_memory_engine.schema.migrate status --schema cme_engine
uv run python -m custom_memory_engine.schema.migrate apply  --schema cme_engine
docker compose --env-file .env -p cme-verify exec -T db psql -U cme_test -d cme_compatibility -v ON_ERROR_STOP=1 \
  < validation/bitemporal_checks.sql        # 43 assertions, reduced-schema check only
```

## Conventions

- Async everywhere below the API; every statement parameterised, run as the caller; `Runtime.table()` for identifiers.
- Errors carry **static codes** and never an identifier, path, scope or content (`EntryError`, `_map`), and every
  translation uses `raise … from None` so a traceback cannot leak the original value.
- `structlog` events are lowercase-with-underscores and carry no content: `logger.error("api_call_failed",
  operation=…, error_type=…)`. No f-strings in an event, no `content`/`evidence_span`/`embedding` keys anywhere
  (`security/audit_guard.py`, log channel 7).
- Type hints on every signature; `pyright` standard mode; `ruff` line length 119 with `E/F/B/ERA/D` selected.
- Test names are full sentences with no task prefix, and a property that could silently pass gets a `control` test:
  `tests/test_channel_integrity.py`, `tests/integration/test_tenant_integrity_db.py`.
- Documentation states what is enforced and what is not, and names the gap and its owner. An optimistic claim is worse
  than a gap.

## Known drift (report it, do not reproduce it)

Stale as of `d98f1b9`. Fix them in the commit that touches them; do not cite them as precedent.

| Document | Says | Actually |
|---|---|---|
| `PLAN/CONTRIBUTING.md` §1.1–1.2 | `research-lab/` paths, `rl/<n>-<slug>` branches, `[RL-xxx]` PR titles | `custom-memory-engine/`; `feat(e###)-…`/`fix(w3)-…` branches, `type(scope):` titles with `(Refs #N)` |
| `PLAN/INDEPENDENCE_BOUNDARY.md` §4 | a path-scoped research-lab workflow | `.github/workflows/custom-memory-engine.yaml`, with **no** `paths:` filter (a filtered workflow never reports, so a required check hangs) |
| `README.md` (root) and `docs/` | the FastAPI/LangGraph template | accurate for `app/`; it says nothing about the engine, and must not be read as engine documentation |
| root `AGENTS.md` | "all imports MUST be at the top"; "all routes must have rate limiting" | false for the engine's deliberate lazy imports and four `app/api/v1/auth.py` routes |
