# Architecture Element Ownership (`MA-017`)

Owners of every architecture element **except invariants** (their owners are the `Owner` column of
`INVARIANT_LEDGER.md`, validated by `E-004`). `scripts/plan_coverage.py` extracts the elements from
`ARCHITECTURE_V2.md` and fails CI if any element has no row here (or in the ledger), if an owner is not a
`ROADMAP_V2.md` task, or if a row names an element the architecture does not declare. A `deferred` owner needs a
reason and a decision reference. The generated result is `COVERAGE_MATRIX.md`.

Every public operation is also owned by `E-039` (the entry point: authorization, conditions, error contract);
the other owner builds the behaviour. `E-039` wires an operation **only** when that other owner lands it, so an
operation's row here carries two obligations: the behaviour (the other owner) and the dispatch through
`Engine.call` (`ROADMAP_V2.md` §4.1, `V2-D63`). The `Note` column records which, for the operations that are still
unwired. Ownership is the `Owners` column, and `plan_coverage.py --check` fails if any operation loses it.

| Element | Class | Owners | Note |
|---|---|---|---|
| `EPISODE` | kind | `E-010`, `E-013` | `kind` column (M1); raw-source evidence (M4) |
| `CLAIM` | kind | `E-010`, `E-015` | grounded or inferred through the two admission policies |
| `PROCEDURE` | kind | `E-010`, `E-030` | preconditions, outcome stats |
| `ABSTRACTION` | kind | `E-010`, `C-010` | the abstraction artifact and its ledger |
| `SCOPE` | table | `E-011` | M2 + M9 (`V2-D45`, `V2-D49`) |
| `ASSOCIATION` | table | `E-014` | M6; degree cap `V2-D13` |
| `ENTITY` | table | `E-014`, `E-024` | M6; principal-scoped (`V2-D25`) |
| `METHOD` | table | `E-014` | M6; written only by `register_method` (`V2-D46`) |
| `ABSTRACTION_STATS` | table | `E-014`, `C-010` | M6 |
| `VERIFICATION` | table | `E-014`, `C-021` | M6; disjoint split (`I11`) |
| `NEGATIVE_CONSTRAINT` | table | `E-014`, `UM-012` | M6; sticky dissent (`I16`) |
| `facts` | channel | `E-017`, `E-033`, `E-035` | `CHANNELS.md` (frozen, `V2-D48`) |
| `hypotheses` | channel | `E-017`, `E-033`, `E-035` | |
| `procedures` | channel | `E-017`, `E-030`, `E-033` | |
| `conflicts` | channel | `E-017`, `E-033`, `E-035` | C-P4 (`V2-D48`) |
| `ingest_episode` | operation | `E-039`, `E-013` | |
| `ingest_artifact` | operation | `E-039`, `E-015` | |
| `query` | operation | `E-039`, `E-031`, `E-033` | unwired: `E-033` lands it through `Engine.call` (§4.1) |
| `refine` | operation | `E-039`, `E-034` | unwired: `E-034` lands it through `Engine.call` (§4.1) |
| `get` | operation | `E-039`, `E-033` | unwired: `E-033` lands it through `Engine.call` (§4.1) |
| `provenance` | operation | `E-039`, `E-033` | opaque handles (`V2-D37`); unwired: `E-033` lands it through `Engine.call` (§4.1) |
| `assert` | operation | `E-039`, `E-015` | |
| `retract_own` | operation | `E-039`, `E-016` | |
| `infer` | operation | `E-039`, `C-021` | unwired: `C-021` lands it through `Engine.call` (§4.1) |
| `verify` | operation | `E-039`, `C-021` | unwired: `C-021` lands it through `Engine.call` (§4.1) |
| `refute` | operation | `E-039`, `C-026` | unwired: `C-026` lands it through `Engine.call` (§4.1) |
| `link` | operation | `E-039`, `E-014` | `grounded_endpoint_protected` (`V2-D48`) |
| `unlink` | operation | `E-039`, `E-014` | |
| `create_entity` | operation | `E-039`, `E-014` | `V2-D46` |
| `link_entity` | operation | `E-039`, `E-024` | wired by `E-024` (#139, `V2-D71`) through `Engine.call`; `entity_link` (migration 18) |
| `record_outcome` | operation | `E-039`, `E-030` | unwired: `E-030` lands it through `Engine.call` (§4.1) |
| `adjudicate` | operation | `E-039`, `E-016` | |
| `dissent` | operation | `E-039`, `UM-012` | `V2-D46`; unwired: `UM-012` lands it through `Engine.call` (§4.1) |
| `purge_working` | operation | `E-039`, `E-026` | admin-only (`V2-D47`); unwired: `E-026` lands it through `Engine.call` (§4.1) |
| `expire` | operation | `E-039`, `A-014` | admin-only; unwired: `A-014` lands it through `Engine.call` (§4.1) |
| `rebuild_index` | operation | `E-039`, `A-014` | admin-only; unwired: `A-014` lands it through `Engine.call` (§4.1) |
| `register_method` | operation | `E-039`, `E-014` | admin-only; `V2-D46` |
| `delete` | operation | `E-039`, `E-037` | unwired: `E-037` lands it through `Engine.call` (§4.1) |
| `export` | operation | `E-039`, `A-015` | unwired: `A-015` lands it through `Engine.call` (§4.1) |
| `create_scope` | operation | `E-039`, `E-040` | `V2-D46`; behaviour split from `E-011` at its closure (`V2-D63`); unwired: `E-040` lands it through `Engine.call` (§4.1) |
| `grant_access` | operation | `E-039`, `E-011` | `V2-D46`, `V2-D36` |
| `revoke_access` | operation | `E-039`, `E-011` | |
| `stats` | operation | `E-039`, `C-010` | unwired: `C-010` lands it through `Engine.call` (§4.1) |
| `cost` | operation | `E-039`, `E-036` | unwired: `E-036` lands it through `Engine.call` (§4.1) |
| `M1` | migration | `E-010` | |
| `M2` | migration | `E-011` | |
| `M3` | migration | `E-012` | |
| `M4` | migration | `E-013` | |
| `M5` | migration | deferred | Deferred past W1 by `V2-D43`: it weakens `I5`, is one-way and no W1 task needs it; revisit with `E-021` |
| `M6` | migration | `E-014` | |
| `M7` | migration | `E-010` | `V2-D38`, `V2-D43` |
| `M8` | migration | `E-020` | |
| `M9` | migration | `E-011` | |
