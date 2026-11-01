# Architecture → Plan Coverage Matrix

**Generated** by `scripts/plan_coverage.py --write` (`MA-017`); do not edit. CI fails if it is stale
(`--check`) or if any element is unowned (`tests/test_plan_coverage.py`). Elements are extracted from
`ARCHITECTURE_V2.md`; owners come from `INVARIANT_LEDGER.md` (invariants) and `COVERAGE.md` (the rest).

**70 elements, 0 unowned.**

## Kinds (4)

| Element | Declared | Owners |
|---|---|---|
| `EPISODE` | §2.1 | `E-010`, `E-013` |
| `CLAIM` | §2.1 | `E-010`, `E-015` |
| `PROCEDURE` | §2.1 | `E-010`, `E-030` |
| `ABSTRACTION` | §2.1 | `E-010`, `C-010` |

## Tables (7)

| Element | Declared | Owners |
|---|---|---|
| `SCOPE` | §2.1 | `E-011` |
| `ASSOCIATION` | §2.1 | `E-014` |
| `ENTITY` | §2.1 | `E-014`, `E-024` |
| `METHOD` | §2.1 | `E-014` |
| `ABSTRACTION_STATS` | §12 M6 | `E-014`, `C-010` |
| `VERIFICATION` | §12 M6 | `E-014`, `C-021` |
| `NEGATIVE_CONSTRAINT` | §12 M6 | `E-014`, `UM-012` |

## Invariants (17)

| Element | Declared | Owners |
|---|---|---|
| `I1′` | §11 | `E-016`, `E-017`, `E-035` |
| `I2` | §11 | `E-013` |
| `I3` | §11 | `E-014` |
| `I4` | §11 | `E-012` |
| `I5` | §11 | `E-011`, `E-012` |
| `I6` | §11 | `E-010`, `E-018`, `E-020` |
| `I7` | §11 | `E-011` |
| `I8` | §11 | `E-037` |
| `I9` | §11 | `E-011`, `E-037` |
| `I10` | §11 | `E-016`, `E-037` |
| `I11` | §11 | `C-010`, `C-021` |
| `I12` | §11 | `E-025` |
| `I13` | §11 | `E-037`, `E-038` |
| `I14` | §11 | `E-017`, `E-035` |
| `I15` | §11 | `C-010`, `C-021` |
| `I16` | §11 | `E-014`, `UM-012` |
| `I17` | §11 | `E-011`, `E-014`, `E-017` |

## Channels (4)

| Element | Declared | Owners |
|---|---|---|
| `facts` | §9.1 | `E-017`, `E-033`, `E-035` |
| `hypotheses` | §9.1 | `E-017`, `E-033`, `E-035` |
| `procedures` | §9.1 | `E-017`, `E-030`, `E-033` |
| `conflicts` | §9.1 | `E-017`, `E-033`, `E-035` |

## Operations (29)

| Element | Declared | Owners |
|---|---|---|
| `ingest_episode` | §8.1 Ingest | `E-039`, `E-013` |
| `ingest_artifact` | §8.1 Ingest | `E-039`, `E-015` |
| `query` | §8.1 Read | `E-039`, `E-031`, `E-033` |
| `refine` | §8.1 Read | `E-039`, `E-034` |
| `get` | §8.1 Read | `E-039`, `E-033` |
| `provenance` | §8.1 Read | `E-039`, `E-033` |
| `assert` | §8.1 Write (grounded) | `E-039`, `E-015` |
| `retract_own` | §8.1 Write (grounded) | `E-039`, `E-016` |
| `infer` | §8.1 Write (inferred) | `E-039`, `C-021` |
| `verify` | §8.1 Write (inferred) | `E-039`, `C-021` |
| `refute` | §8.1 Write (inferred) | `E-039`, `C-026` |
| `link` | §8.1 Structure | `E-039`, `E-014` |
| `unlink` | §8.1 Structure | `E-039`, `E-014` |
| `create_entity` | §8.1 Structure | `E-039`, `E-014` |
| `link_entity` | §8.1 Structure | `E-039`, `E-024` |
| `record_outcome` | §8.1 Structure | `E-039`, `E-030` |
| `adjudicate` | §8.1 Adjudicate | `E-039`, `E-016` |
| `dissent` | §8.1 Adjudicate | `E-039`, `UM-012` |
| `purge_working` | §8.1 Maintain | `E-039`, `E-026` |
| `expire` | §8.1 Maintain | `E-039`, `A-014` |
| `rebuild_index` | §8.1 Maintain | `E-039`, `A-014` |
| `register_method` | §8.1 Maintain | `E-039`, `E-014` |
| `delete` | §8.1 Delete | `E-039`, `E-037` |
| `export` | §8.1 Export | `E-039`, `A-015` |
| `stats` | §8.1 Observe | `E-039`, `C-010` |
| `cost` | §8.1 Observe | `E-039`, `E-036` |
| `create_scope` | §8.1 Scope | `E-039`, `E-011` |
| `grant_access` | §8.1 Scope | `E-039`, `E-011` |
| `revoke_access` | §8.1 Scope | `E-039`, `E-011` |

## Migrations (9)

| Element | Declared | Owners |
|---|---|---|
| `M1` | §12 | `E-010` |
| `M2` | §12 | `E-011` |
| `M3` | §12 | `E-012` |
| `M4` | §12 | `E-013` |
| `M5` | §12 | `deferred` |
| `M6` | §12 | `E-014` |
| `M7` | §12 | `E-010` |
| `M8` | §12 | `E-020` |
| `M9` | §12 | `E-011` |

## Differences from the historical matrix (`SYNC_LOG.md` §3, not modified)

- `M1`: SYNC_LOG §3 ['E-010', 'E-016'] → now ['E-010']
- `M2`: SYNC_LOG §3 ['E-011', 'MA-011'] → now ['E-011']
- `M9`: SYNC_LOG §3 ['E-011', 'MA-011'] → now ['E-011']
- `M8`: SYNC_LOG §3 ['E-018', 'E-020'] → now ['E-020']
- `I12`: SYNC_LOG §3 ['E-004'] → now ['E-025']
- `I1′`: SYNC_LOG §3 ['E-004'] → now ['E-016', 'E-017', 'E-035']
- `I13`: SYNC_LOG §3 ['E-017', 'E-037', 'E-038', 'MA-011', 'UM-012'] → now ['E-037', 'E-038']
- `I17`: SYNC_LOG §3 ['E-017', 'E-037', 'E-038', 'MA-011', 'UM-012'] → now ['E-011', 'E-014', 'E-017']
- `SCOPE` (table): absent from SYNC_LOG §3
- `M5` (migration): absent from SYNC_LOG §3
- `M7` (migration): absent from SYNC_LOG §3
- Operations, channels and individual invariants are listed only by group in SYNC_LOG §3.
