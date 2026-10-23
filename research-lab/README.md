# Personal Digital Twin Agent with Temporally-Aware, Governed Long-Term Memory

**Status.** Research proposal. **No experiment described in the proposal has been run.** The only code executed is a reduced-schema design check of the bi-temporal constraints (`validation/bitemporal_checks.sql`, 43 PASS lines in the saved log), the Clopper–Pearson arithmetic (`validation/cp_bounds.py`, byte-identically reproducible), the mermaid syntax check (`validation/mermaid_parse.cjs`), and the mechanical consistency check (`validation/mechanical_checks.py`).

## Standing authoritative documents

| Document | Path |
|---|---|
| Proposal | [`Graduation_Project_Proposal.md`](Graduation_Project_Proposal.md) |
| Methodology and consistency checklist | [`Methodology_and_Consistency_Checklist.md`](Methodology_and_Consistency_Checklist.md) |
| Research and fact-check audit | [`Research_and_Fact_Check_Audit.md`](Research_and_Fact_Check_Audit.md) |
| Source evidence ledger | [`Source_Evidence_Ledger.md`](Source_Evidence_Ledger.md) |
| Changelog (including the finalization pass and the earlier V2/V3/V4 patch histories) | [`CHANGELOG.md`](CHANGELOG.md) |
| End-to-end execution guide | [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) |
| Implementation-level normative specification | [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md) |
| RQ → H → Mechanism → Component → Test → Metric → Result-artifact matrix | [`TRACEABILITY_MATRIX.md`](TRACEABILITY_MATRIX.md) |

Earlier revisions (V1 first revision, V2 patch, V3 patch, V4 patch) were deleted during the finalization pass. The diffs they recorded are preserved as historical sections inside `CHANGELOG.md`, `Research_and_Fact_Check_Audit.md`, and `Methodology_and_Consistency_Checklist.md`.

## Engineering and planning navigation

- **Engineering architecture, component contracts and dependency policy:** [`PLAN/ARCHITECTURE.md`](PLAN/ARCHITECTURE.md).
- **Tasks and acceptance:** [`PLAN/TASK_GRAPH.md`](PLAN/TASK_GRAPH.md); synchronized diagrams and execution sequence: [`PLAN/DEPENDENCY_MAP.md`](PLAN/DEPENDENCY_MAP.md).
- **Actual progress and approval record:** [`PLAN/PROJECT_STATUS.md`](PLAN/PROJECT_STATUS.md). Assignments and calendar estimates remain provisional.
- **Open decisions:** [`PLAN/DECISIONS_REGISTER.md`](PLAN/DECISIONS_REGISTER.md), with source-specific numbering.
- **Advisory evidence vs owner direction:** [`review/README.md`](review/README.md). Hybrid is owner-selected for implementation planning; S-07 is no longer an unanswered architecture choice. RL-001 team approval is owner-confirmed and recorded in [`PLAN/PROJECT_STATUS.md`](PLAN/PROJECT_STATUS.md) §6; individual response/audit details were not supplied.
- **Documentation inventory and consolidation dispositions:** [`review/EXISTING_WORK_IMPACT_ANALYSIS.md`](review/EXISTING_WORK_IMPACT_ANALYSIS.md). Main-app `docs/` is contextual/legacy material, not the V4 research contract.

## Validation artifacts

Under `validation/`:

| File | Purpose | Current state |
|---|---|---|
| `mechanical_checks.py` | Citations, cross-refs, anchors, tables, ablations, stale-wording guard, H1/H4 wording, persona, excluded terms, fabricated-results guard, reference-list byte-identity (pinned SHA-256). | Re-run in the finalization pass. |
| `mechanical_checks.output.txt` | Saved output of the above. | Final line `SUMMARY: ALL HARD CHECKS PASSED`. |
| `bitemporal_checks.sql` | Reduced-schema design check of the bi-temporal constraints and query predicates. | Executed historically on PostgreSQL 16.15; saved log unchanged. |
| `bitemporal_checks.output.txt` | 43 PASS lines; final line `ALL ASSERTIONS PASSED`. | MD5 `66a4dae36f20f17d19f3b75d612e61fb`. |
| `bitemporal_log_inspection.txt` | Re-inspection record of the SQL log (SQL not re-run). | — |
| `cp_bounds.py` | Clopper–Pearson zero-leak upper bounds and clustering illustration. | Byte-identically reproducible. |
| `cp_bounds.output.txt` | Saved output of the above. | — |
| `mermaid_parse.cjs` / `mermaid_parse.output.txt` | Mermaid syntax check on the five diagrams. | Not re-executed in the finalization pass. |
| `document_hashes.txt` | MD5 of each authoritative document at the end of the finalization pass. | — |

## Source evidence

Under `research/sources/`:

- `research/sources/fulltext/` — PDFs and `pdftotext -layout` extracts for refs [20]–[29] with `MANIFEST.txt` listing SHA-256 hashes.
- `research/sources/pages/` — fetched benchmark / publisher / repository / documentation / licence pages used to substantiate every claim in `Source_Evidence_Ledger.md`.

## Re-run the validation suite

From `research-lab/` (run `cd research-lab` from the repository root):

```sh
# 1. Mechanical consistency (hard checks on proposal, checklist, changelog).
python3 validation/mechanical_checks.py \
    Graduation_Project_Proposal.md \
    Methodology_and_Consistency_Checklist.md \
    CHANGELOG.md

# 2. Record MD5s of the authoritative documents.
md5sum Graduation_Project_Proposal.md \
       Methodology_and_Consistency_Checklist.md \
       Research_and_Fact_Check_Audit.md \
       Source_Evidence_Ledger.md \
       CHANGELOG.md

# 3. Clopper–Pearson arithmetic (reproducible byte-for-byte).
diff <(python3 validation/cp_bounds.py) validation/cp_bounds.output.txt

# 4. Bi-temporal SQL log re-inspection (log and SQL unchanged since first execution).
grep -c "^PASS\|^ *PASS " validation/bitemporal_checks.output.txt   # -> 43
tail -1 validation/bitemporal_checks.output.txt                      # -> ALL ASSERTIONS PASSED
md5sum validation/bitemporal_checks.output.txt                       # -> 66a4dae36f20f17d19f3b75d612e61fb
md5sum validation/bitemporal_checks.sql                              # -> 9050a08b497a0acc4e5e19d87059e185
```

Expected outcomes:

- Step 1 ends with `SUMMARY: ALL HARD CHECKS PASSED` (exit 0).
- Step 2 compares against the **historical finalization snapshot** in `validation/document_hashes.txt`; later documented navigation/planning changes legitimately change hashes. Do not overwrite that evidence snapshot.
- Step 3 produces an empty diff.
- Step 4 produces the four values shown.

## Re-execute the SQL design check (optional)

If PostgreSQL 16 is available (for example `docker run postgres:16-alpine`), the saved SQL check can be rerun. It does **not** exercise the full application schema, pgvector, RLS, the job queue, concurrent writers, the end-to-end deletion procedure, or the archival-precondition tests T1 – T5.

```sh
psql -f validation/bitemporal_checks.sql
```

## Historical finalization

Earlier documents describe `scripts/finalize_repository.py`, but that script is **absent from the inspected workspace** (both Lab-local and repository-root paths). Its idempotency cannot be verified here. Do not run or recreate a destructive finalization from those historical instructions; the current version-free documents and retained evidence are the working set.

## What is still unverified

See `TRACEABILITY_MATRIX.md` for the exhaustive list. Highlights:

- Research experiments **H1, H2, H3, H4** — not run.
- Archival-precondition regression tests **T1 – T5** — specification only; harness not built.
- Full application schema with concurrent writers; RLS; `SKIP LOCKED` queue; pgvector; end-to-end deletion.
- Judge validation on development split; pilot study; freeze values (δ_pres, δ, u*, detection share, stratum mix).

**Immediate next action:** distribute the first-wave handoff in [`PLAN/PROJECT_STATUS.md`](PLAN/PROJECT_STATUS.md) §10 and [`PLAN/ASSIGNMENTS.md`](PLAN/ASSIGNMENTS.md) §2. RL-001 is **Done by owner-confirmed team approval**; its individual responses, signatures, reviewed revision/hash and actual review timestamp were not supplied. Begin controlled decision preparation and RL-002 scaffold review, not implementation, publication or experiments. Supervisor, ethics, compatibility, task acceptance and experimental-freeze gates remain in force. Follow [`PLAN/CONTRIBUTING.md`](PLAN/CONTRIBUTING.md) §3 for local claims; no claims are inferred.

**Before the P10 pilot (`RL-100`) begins:** complete its prerequisites `RL-010`, `RL-011`, `RL-026`, `RL-031`, `RL-033`, `RL-034`, and `RL-094` — approved scope and pilot plan, frozen development/test splits, working baselines/judge/statistics, and the P4–P8 engine tested through the P9 §G sign-off. The pilot uses the **development split only**; its signed freeze list is required before H1–H4 test-split runs (`PROJECT_ROADMAP.md` §D.10/§K; `PLAN/issue_drafts/RL-100.md`).

## Licence and attribution

- The code under `validation/` and `scripts/` is project-internal.
- Downloaded sources under `research/sources/` are copies of third-party material (academic papers, repository READMEs, licence files, standards). Each is retained for audit under the usual fair-use research exception and under the licences recorded in `Source_Evidence_Ledger.md`. Specifically: Mem0 and Graphiti are Apache-2.0; LongMemEval is MIT; LoCoMo is CC BY-NC 4.0.
- The project itself (proposal, checklist, audit, ledger, changelog, roadmap, specification, traceability matrix, this README) is produced for a graduation submission; the final licence will be fixed at release (`PROJECT_ROADMAP.md` Phase P13).
