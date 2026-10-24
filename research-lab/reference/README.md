# Research Lab Reference Directory

This directory centralizes research materials, literature audit ledgers, feasibility analyses, and archived source corpora intended primarily for research agents and technical reviewers.

Keep essential project documentation (`Graduation_Project_Proposal.md`, `TECHNICAL_SPECIFICATION.md`, `PROJECT_ROADMAP.md`, `TRACEABILITY_MATRIX.md`, `Methodology_and_Consistency_Checklist.md`) and contributor workflows (`PLAN/`) at the root and under `PLAN/`. Consult the files here selectively rather than loading the entire corpus into context.

---

## 1. Directory Index & When to Consult Each Resource

| Path | Contents | When to Consult |
|---|---|---|
| [`Source_Evidence_Ledger.md`](Source_Evidence_Ledger.md) | Citation-by-citation verification ledger for all 41 references in `Graduation_Project_Proposal.md` (§§A–B with per-row *Reading depth* labels), claim-to-source mappings (§C), verification process (§D), and Stage-2 full-text notes for references `[20]`–`[29]` (§E). | Verifying literature claims, license constraints, benchmark statistics, or third-party repository commit hashes. |
| [`Research_and_Fact_Check_Audit.md`](Research_and_Fact_Check_Audit.md) | Historical audit of the original proposal against V1–V4 revisions: flaw analysis (§1), literature positioning (§2), novelty boundaries (§3), technical audit (§4), evaluation audit (§5), risk audit (§6), verification limits (§7), and V2–V4 patch ledgers (§§8–9). | Understanding why specific proposal claims were narrowed, why the V4 archival bug fix was introduced, or what remains unverified prior to implementation. |
| [`TECHNICAL_FEASIBILITY_REVIEW.md`](TECHNICAL_FEASIBILITY_REVIEW.md) | Engineering and empirical feasibility assessment across the bitemporal SQL core, slow-path extraction pipeline, public benchmarks (LongMemEval, LoCoMo), baselines (Mem0, Graphiti), and statistical protocol, plus risk-to-decision mappings (`S-01`–`S-07`). | Assessing runtime bottlenecks, token/API cost exposure, PostgreSQL `btree_gist` concurrency behavior, or baseline integration risks during Phase P1–P4 and P10. |
| [`ARCHITECTURE_DECISION_MATRIX.md`](ARCHITECTURE_DECISION_MATRIX.md) | Comparative evaluation of Option A (Full Monorepo Integration), Option B (Standalone Research Lab), and Option C (Hybrid Architecture), including the resolution of feasibility decisions `S-01`–`S-07`. | Reviewing the rationale behind the Hybrid architecture (`D-ARCH`) and the strict runtime/code isolation boundary with the root ChronaMind application. |
| [`sources/fulltext/`](sources/fulltext/) | Extracted Markdown full texts and `MANIFEST.txt` for the 10 Stage-2 2026 memory preprints (`[20]`–`[29]`: A-TMA, TOKI, Quipu, Graph-Native Bi-Temporal, MemTX, Eywa, Mi-Memory, Ground-Truth-First, GateMem, Agentic Unlearning). | Inspecting specific ablation tables, temporal rules, or deletion claims in `[20]`–`[29]`. |
| [`sources/pages/`](sources/pages/) | Offline snapshots of arXiv abstracts, ACL Anthology pages, GitHub READMEs/licenses (`mem0`, `graphiti`, `LongMemEval`, `locomo`), PostgreSQL 16 docs, pgvector docs, and NIST/OWASP/GDPR pages. | Checking exact upstream wording, commit hashes, or license terms without network access. |

---

## 2. Agent Lookup Protocol

1. **Start with the ledger, not raw source files:** Query [`Source_Evidence_Ledger.md`](Source_Evidence_Ledger.md) or [`sources/fulltext/MANIFEST.txt`](sources/fulltext/MANIFEST.txt) first to identify the exact reference number (`[1]`–`[41]`) and file path.
2. **Respect reading-depth boundaries:** Never promote a source's reading depth beyond what [`Source_Evidence_Ledger.md`](Source_Evidence_Ledger.md) records (*metadata/abstract inspected*, *selected full-text passages read*, etc.) unless you read the full text yourself and document the update.
3. **Root repository `docs/` vs. `research-lab/`:** Any documentation in the parent ChronaMind application relates to the standalone FastAPI/LangGraph/Mem0 app (`app/`). It is **not** part of the Research Lab specification and must not be treated as authoritative for the bitemporal memory engine (`Graduation_Project_Proposal.md` and `TECHNICAL_SPECIFICATION.md`).
