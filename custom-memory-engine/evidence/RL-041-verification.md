# RL-041 schema migration verification

Contribution for [issue #32](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/32),
originally based on main `e4168c0` and verified on 26 October 2026. The leader
merged PR #37 at `a9610b7`; the later catalog corrections belong to the separate
post-merge assurance follow-up. The twelve-table DDL and its adapted full-schema
assertion script are unchanged. See [`POSTMERGE-assurance.md`](POSTMERGE-assurance.md)
for the combined main-based follow-up evidence.

## Acceptance evidence

| Requirement | Implementation and executed oracle |
|---|---|
| Explicit research database | `MigrationConfig.from_environment()` reads only `RL_DATABASE_URL`; rejects implicit/system targets. Real API and subprocess CLI tests use a disposable research database. |
| Idempotent apply | First apply creates version 1; a repeat returns `unchanged` with identical table OIDs, one history row and retained seeded data. Concurrent applies settle to one install. |
| Status/version | Read-only connections inspect absent/applied schemas without creating objects. Source identity, malformed/unknown history and live catalog drift are tested independently. |
| Constraint enforcement drift | Real PostgreSQL tests move an unchanged FK to the wrong table and disable internal FK triggers. Status reports drift, apply refuses, and confirmed reset restores the correct owning table and rejects missing-parent rows with SQLSTATE 23503. |
| Clean reset | Exact namespace confirmation, owned schema marker/current owner and valid migration history are required. Real tests assert twelve empty domain tables, recreated history and untouched outside schema/extension identities. |
| Atomic failure | PostgreSQL errors injected after installation prove complete apply rollback and reset rollback retaining original data, table identities and history. Unknown objects and external foreign keys block destructive reset. |
| First extension bootstrap | Fresh `template0` databases start without either extension; installation puts both in `public`, reset preserves their identities, and a failed install rolls them back. |
| All 43 assertions after reset | The existing `bitemporal_on_full.sql` runs after a real clean reset. All 43 complete assertion statements correspond to `validation/bitemporal_checks.sql`; full-table OID/full-only columns and restored provenance trigger are checked. |
| Public commands | Real `python -m implementation.schema.migrate` subprocesses execute status/apply/repeated apply/status/reset using the explicit environment and parse JSON output. Help and invalid-configuration paths are also covered. |

## Reproduction

The standalone RL-041 correction checkpoint on 27 October passed **430/430 tests**, including **39/39
cases marked integration**, with zero failures, skips or collection errors.
The migration file contributes **22 unit tests and 21 integration cases**. The
21 migration integration cases all require PostgreSQL; the inherited suite's
integration marker also includes some static schema-contract checks. The
separate reduced SQL harness also passed all **43 assertions**. The owned local
database lifecycle verified the pinned server/extension versions and removed all
its containers, networks and volumes after testing. Credentials stayed in child
environment/memory and redacted failure output, outside repository files/arguments.

Engine Ruff 0.15.6 and repository Ruff 0.11.4 lint/format passed; engine Pyright
1.1.408 and root Pyright reported zero errors/warnings. All five canonical-LF
reference hashes, mechanical hard checks and exact Clopper–Pearson output passed.
The changelog's hash was updated for its new entry; frozen references are unchanged.

From `custom-memory-engine/`, with the locked CPython 3.12.12 environment:

```sh
uv sync --frozen --python 3.12.12
uv run ruff check src tests research/benchmarks implementation evaluation
uv run ruff format --check src tests research/benchmarks implementation evaluation
uv run pyright
uv run pytest -q
python validation/mechanical_checks.py Graduation_Project_Proposal.md Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python validation/cp_bounds.py) validation/cp_bounds.output.txt
md5sum -c validation/document_hashes.txt
```

Database tests require `CME_TEST_DATABASE_URL` to explicitly name a disposable
local test database. Test fixtures translate that value into runtime configuration;
production commands continue to require `RL_DATABASE_URL`. Provisioning tests
need schema/extension privileges; two bootstrap fixtures also create and drop
their own UUID-named databases. The dedicated engine CI supplies these privileges
on the pinned PostgreSQL 16.15 / vector 0.8.7 image, with btree_gist 1.7.
Windows API tests require a selector loop; the public CLI configures its own loop.

The raw reduced-schema SQL script creates a separate reduced fixture. It is also
run as a compatibility check, but the full-schema post-reset test above is the
acceptance oracle. The full-schema script contains explicit commits, fixture rows
and local query helpers; run it only on a disposable fixture, never runtime data.

Independent reviews covered the live issue contract, PostgreSQL DDL/transaction
semantics, source failure paths and the test oracles. An early review identified
malformed ledger scalar types escaping status classification; validation and
real-database tamper cases now cover that condition. Initial whole-suite testing
also exposed Windows CRLF conversion of frozen split YAML. Scoped Git attributes
preserve the original canonical LF bytes; no split membership or recorded hash
was changed. Four replay-cache `isinstance` expressions received equivalent
union syntax so the repository's older Ruff also accepts the existing cache code.

## Boundaries

Renewed assurance on 27 October found two catalog false negatives despite the
initial 428-test pass: constraint-owning relations were absent from the digest,
and internal FK triggers were excluded. Disposable probes directly reproduced
both, including an accepted orphan evidence row with enforcement disabled. Both
new regressions fail against the original fingerprint queries (41 pass / 2 fail)
and check recovery as well as detection. The correction records stable semantic
trigger identities, not generated trigger names or OIDs. Separate storage probes
confirmed advisory-lock timeout SQLSTATE 55P03 and cancellation both leave no
partial install and allow a successful retry. Earlier unmerged fingerprints
intentionally report drift; applying does not silently bless them.

These are software checks using synthetic fixture rows, not research outcomes.
Runtime role provisioning/RLS validation, transactional supersession/archival,
temporal query APIs, clock/queue, full deletion and load tests remain their own
tasks. No product code, root configuration or dependency lock was changed. No
paid provider call, pilot, confirmatory test execution or human approval occurred.
The runner records actual extension versions and detects subsequent drift; it
does not upgrade extensions or enforce the integration image's versions at runtime.
Reset requires stopping ordinary clients and intentionally discards managed data.
Unknown SQL objects can produce a static database diagnostic during inspection;
this is fail-closed and does not authorize adoption or cross-schema cascading.
