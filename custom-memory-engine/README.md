# Custom Memory Engine

Independent top-level implementation workspace for ChronaMind. **Current scope: the six published P1 decision/verification issues, not a completed production memory engine.** Research Lab files remain normative references; the owner's 2026-10-24 placement revision keeps all new implementation outside Research Lab and isolated from `app/`.

## Issue deliverables

| GitHub | Task | Canonical record | Remaining human gate |
|---|---|---|---|
| #1 | RL-010 scope/models/enforcement | [Scope and models](decisions/RL-010-scope-and-models.md) | Owner/peer/supervisor approval, funded model access |
| #2 | RL-012 database/concurrency | [PostgreSQL and pgvector](decisions/RL-012-postgres-pgvector.md) | Owner/peer review; later eight-worker load test |
| #3 | RL-013 runtime/UI choice | [Engine runtime](decisions/RL-013-engine-runtime.md) | Owner/peer review |
| #4 | RL-014 A2b/Suite D | [A2b and cardinality](decisions/RL-014-a2b-and-suite-d.md) | Peer review; final pilot freeze |
| #5 | RL-015 ethics | [H5 ethics](decisions/RL-015-ethics-h5.md) | Scope review; written clearance if H5 reopens |
| #6 | RL-016 replay/judge/license | [Paired decisions](decisions/RL-016-paired-d23-d24-d26.md) | Member 3/4 co-signatures, peer/supervisor/license authority |

The [Lab register](../research-lab/PLAN/DECISIONS_REGISTER.md) and [Technical Specification §2](../research-lab/TECHNICAL_SPECIFICATION.md) index current values. Original issue-draft locations are historical; no duplicate decision source is maintained. #1–#5 meet their decision-preparation/verification acceptance scope; downstream human approvals remain distinct gates, not fabricated signatures. #6 remains blocked on its explicit Member 3/4 co-signature acceptance criterion. Direct-to-main delivery is owner-authorized instead of a PR.

## Layout and boundaries

- `src/custom_memory_engine/protocols.py`: deterministic replay scheduling/reference clamping, exact zero-leak scenario bound, binary agreement and fail-closed numeric judge gate.
- `tests/`: unit, import/link, native-driver and real-DB compatibility checks.
- `decisions/`: six evidence-backed records with explicit pending signatures.
- `evidence/`: source ledger, verification report, captured passing output.
- `pyproject.toml` / `uv.lock`: independent CPython 3.12 dependency graph.
- `compose.yaml`: **disposable test database only**, pinned by digest, loopback port 5433, no product data/credentials.

No ORM, product imports, paid LLM calls, API routes, participant recruitment or benchmark test-split access. There are no claims of engine latency, full deletion safety, RLS, extraction correctness or H1–H4 results. Async DB probes use unique schemas and clean them in fixture finalizers. Use only a disposable database; privileges in tests are not a production role policy.

## Reproduce verification

Install `uv` and CPython 3.12, then:

```bash
cd custom-memory-engine
uv sync --frozen --python 3.12
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run pytest -q -m 'not integration'

# A private .env was generated locally (mode 0600, ignored by Git).
# On another checkout: copy .env.example to .env, generate a password, and
# update the URL consistently. Never source an untrusted .env as shell code.
docker compose --env-file .env -p cme-verify up -d --wait
uv run --env-file .env pytest -q

# Historical reduced-schema check (not V4 archival or full-engine validation).
docker compose --env-file .env -p cme-verify exec -T db psql -U cme_test -d cme_compatibility \
  -v ON_ERROR_STOP=1 < ../research-lab/validation/bitemporal_checks.sql
docker compose --env-file .env -p cme-verify down -v

cd ../research-lab
python3 validation/mechanical_checks.py Graduation_Project_Proposal.md \
  Methodology_and_Consistency_Checklist.md CHANGELOG.md
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt
```

`config.py` reads exported environment explicitly, redacts the DSN, and restricts destructive probes to the local `cme_test` / `cme_compatibility` database. `uv run --env-file .env` loads the private test environment; no product .env is implicitly read. No LLM credentials are required or provisioned for this P1 implementation.

DB tests skip without `CME_TEST_DATABASE_URL`; a skipped DB suite is **not** database verification. Full CI supplies the pinned service and runs all tests. Captured failure traces may include connection URLs: do not publish unreviewed logs or use real credentials; committed evidence here contains passing output only.

## Next engineering gates

Obtain human approvals first, then implement the later schema/clock/queue/gate/retrieval/lineage-deletion tasks against these contracts. Require T1–T5, tenant/RLS security tests with non-owner roles, idempotency/deletion-race regressions, provider tracing/redaction, migration/rollback strategy, and measured eight-worker load before production claims. Resolve the existing statistical-specification discrepancy noted in the [source ledger](evidence/SOURCES.md) before implementing RL-034. Never silently freeze pilot parameters or change model snapshots.

See [verification results and limitations](evidence/VERIFICATION.md).
