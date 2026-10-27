# RL-043 temporal predicates — verification record

Date: 27 October 2026. Issue: [#34](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/34).
Baseline: `20e206c320ce74dabcc0061f8413531c735414b2` (merged schema/migration and
replay/schema assurance). All changes are inside `custom-memory-engine/`.

## Implemented contract

[`../implementation/engine/predicates.py`](../implementation/engine/predicates.py)
provides all four typed query builders, parameterized WHERE clauses and guarded
asynchronous execution over the canonical full schema. The
[`API guide`](../implementation/engine/README.md) documents caller identity,
transactions, database clocks, range results and audit/context separation.

The frozen Proposal §7.4, Technical Specification §3.5 and both canonical SQL
checks use `recorded_range @> reference_time` for ordinary reads. Issue #34's
`upper_inf(recorded_range)` bullets are stale. The earlier-reference DB test
selects a closed recorded version believed at that reference and rejects a
later-recorded open version, so reverting to the issue shorthand fails.

## Requirement-to-evidence map

| Requirement / failure boundary | Direct evidence |
|---|---|
| Four modes and E1–E3 expected beliefs | Full-schema test matrices check current fish, corrected prior vegetarian/fish and original audit beliefs by independently specified version IDs |
| `t ≤ ρ`, `start < end ≤ ρ`; equality and microsecond rejection | Pure guard cases, UTC/DST comparisons and copied/constructed query regression checks |
| `β ≤ clock_now()`; future valid target remains legal | DB tests use both replay `app.clock` and transaction-clock fallback; reject future beliefs even for nonexistent users/empty results |
| Ordinary `active` only; audit `active` + `archived`; no staging | Evidence-backed rows cover all five statuses; archived matches the selected recorded time but stays absent from all ordinary modes |
| Valid/recorded half-open boundaries and window overlap | Exact lower/upper ±1µs cases; touching windows return no boundary-only match |
| Exact user scope, existing tenant identity and RLS | Actual `NOLOGIN NOSUPERUSER NOBYPASSRLS` role checks all modes under matching, different and unset GUCs; privileged bypass control separately proves bound user filtering |
| Safe composition and schema resolution | User/time binds, immutable parameter mapping, quoted alias probe, isolated schema validation and unrelated caller search path |
| Caller transaction and GUC ownership | Outer rollback probe, success/rollback savepoints, local tenant/clock restoration and existing session-clock preservation |
| Complete version output and range fidelity | Full row fields and JSON round trip; actual SQL/DMY + Africa/Cairo session distinguishes literal `infinity` from unbounded `None` |
| Audit excluded from answer context | Mode-tagged result accessor rejects audit with `audit_not_for_answer_context`; ordinary result visibility validators reject archived/cross-user rows |

The DB fixtures install the unchanged twelve-table canonical DDL in unique owned
schemas and insert real `APP_USER`/`SESSION`/`TURN`/`MEMORY_EVIDENCE` support. The
deferred provenance trigger stays enabled. Expected values come from the frozen
worked example and native range definitions, not from the implementation's SQL.

## Executed local checks

Environment: Windows, CPython 3.12.12; frozen engine dependencies plus baseline
extras; Ruff 0.15.6 / Pyright 1.1.408. Disposable PostgreSQL **16.15**, pgvector
**0.8.7**, `btree_gist` **1.7**, using the pinned Compose image. Full baseline
software checks also used pinned FalkorDB 6.0.3 and existing immutable model
assets; no paid provider calls were made.

| Check | Result |
|---|---|
| `pytest -q implementation/tests/test_predicates.py` | **28 passed**; separately repeated by two independent reviewers |
| Initial full service-backed workspace suite | **717 passed, 1 failed**, zero skips/collection errors; 55 integration cases collected. The sole failure was a new test expecting a session clock to disappear after the fixture itself set it |
| Corrected predicate suite against real PostgreSQL | **41 passed**, including **13/13 integration cases**, zero skips/failures/collection errors |
| Frozen default install in a separate environment | **633 passed, 69 explicit skips** (66 test skips + 3 module collection skips); no failures/collection errors. Confirmed `mem0`, `graphiti_core`, `onnxruntime`, `tokenizers` and `tiktoken` are absent |
| `validation/bitemporal_checks.sql` against owned PostgreSQL | **43/43 assertions passed** |
| Engine Ruff / format / Pyright, including corrected test | Passed; **0 type errors** |
| Root application Ruff / format / Pyright using its existing interpreter | Passed; **0 type errors**; no root application changes |
| Five canonical-LF document hashes; mechanical checks; exact CP stdout | Passed; `ALL HARD CHECKS PASSED`; CP output matches the committed reference |
| Scope / unchanged schema / import checks | All changed paths in engine workspace; canonical DDL, dependency manifests/locks and root product unchanged; no forbidden cross-imports |

The initial test failure was classified and corrected rather than hidden by a
retry. Its final success path now establishes a `JUN1` session clock, temporarily
uses local `OCT1`, and verifies restoration to `JUN1`. Production predicate source
did not change after the full run. The focused native rerun verifies the corrected
expectation. Final submitted-head Linux full-suite/default-install checks are
reported on the PR; this record does not label the initial full run successful.

## Findings resolved and independent review

An independent source review reproduced an R8 bypass through Pydantic
`model_copy`/`model_construct`, which skip constructor validation. Added regression
tests failed before the fix. Both public compilation and execution now reconstruct
and validate the query fields; **28 pure tests pass**, including those regressions.
An independent probe also confirms audit valid targets after the clock remain
allowed while `β = clock + 1µs` fails with SQLSTATE `22023`.

A separate acceptance/oracle review checked the full source, both suites, frozen
contracts, true RLS setup and documentation. Neither review reports a remaining
material source or oracle defect. Review agreement supplements the executed
database evidence; it does not replace it.

## Cleanup and limits

Both owned PostgreSQL runs removed their containers, networks and volumes:
**0 / 0 / 0 remaining**. The full-run FalkorDB graph list was empty after tests and
the uniquely labelled temporary container was removed. No existing shared
container, graph or project database was selected for deletion. Temporary role
grants and schemas are fixture-owned.

This is synthetic software verification. It does not complete production grants
and all-table isolation (`RL-071`), write/clock sequencing (`RL-042`/`RL-044`), the
whole invariants/load/deletion suites, ranking/rendering (`RL-050`/`RL-051`), or any
pilot/confirmatory research gate. Audit types/accessors require trusted consumers
to preserve the separation; direct SQL is outside that Python guard. No new
DDL/functions, migration fingerprints, dependency locks or paid inference are
introduced. Leader review and merge remain required.

Primary technical references:
[PostgreSQL range types](https://www.postgresql.org/docs/16/rangetypes.html),
[range operators](https://www.postgresql.org/docs/16/functions-range.html),
[Psycopg parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html).
