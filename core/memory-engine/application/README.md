# application/

**Scope:** Personal Digital Twin (PDT) demonstration application.
**Phase:** P12.
**Normative references:** [`../Graduation_Project_Proposal.md`](../Graduation_Project_Proposal.md) §1.1, §11; [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §D.12, §H.
**Status:** Not Started.

## Purpose and boundaries

The PDT application is a **conservatively-defined demonstration** (proposal §1.1, §11) that uses the engine from `../src/custom_memory_engine/`. It is **not** the primary research contribution, and its evaluation does **not** substitute for engine evaluation (proposal §A.3).

Per [`../PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §J row J6, the PDT demonstration is **Mandatory for the application track and optional for the research track**.

## Planned flows (per proposal §11)

- Inspector view (browse by layer, timeline with valid-time and recorded-time axes).
- Provenance panel ("why is this memory here?").
- Correction flow (user asserts a replacement; triggers the gate and the supersession procedure).
- Historical query view ("what did you believe about X on date Y?").
- Deletion flow with a confirmation panel and audit record.
- Uncertainty cues for inferred items.
- Error handling and observability dashboard.
- Persona layer: off by default (per Frozen decision D-03); if enabled, every trait is marked as an inference with evidence links.

## Independence

- The PDT demo **MUST NOT** be built on top of the main-app FastAPI/LangGraph application. It is a standalone demonstrator that imports the Research Lab engine only.
- Integration with the main app, if ever desired, is a Phase P13+ activity under [`../PLAN/INDEPENDENCE_BOUNDARY.md`](../PLAN/INDEPENDENCE_BOUNDARY.md) §6.
