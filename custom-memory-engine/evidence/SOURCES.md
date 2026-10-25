# Primary-source verification ledger — 2026-10-24

Fetched live and inspected for the specific passages below, not claimed read comprehensively. Hashes refer to response bytes fetched in this session; dynamic pages may change. No paper experiments reproduced. Research Lab's historical source ledger remains unchanged. Raw fetched pages were temporary working files, not committed vendor documentation.

| Source | Verified scope | Fetched-byte SHA-256 |
|---|---|---|
| [PostgreSQL 16 btree_gist](https://www.postgresql.org/docs/16/btree-gist.html) | Scalar equality GiST operator classes with range exclusion constraints | `87777e6058a9846643a44d6010393a2bf60c80110caa196383065243c3a075eb` |
| [PostgreSQL 16 explicit locking](https://www.postgresql.org/docs/16/explicit-locking.html) | Transaction-level advisory lock lifetime, row locking/deadlocks | `36b962cf7573f3a6f93593f3d6ec1b5ecab929a319f65f73109c4ddf546befbf` |
| [PostgreSQL 16 admin functions](https://www.postgresql.org/docs/16/functions-admin.html) | pg_advisory_xact_lock / pg_try_advisory_xact_lock | `cd9ccdf810f19934f3effbf65977cee79b72666f75be34f1ecdecb330f0c9437` |
| [PostgreSQL 16 SELECT](https://www.postgresql.org/docs/16/sql-select.html) | SKIP LOCKED is appropriate for queue consumers, not general snapshot consistency | `92116f38653ee954487f1f2cbe8985859fe541a0856235c1d18c59dd94e2707f` |
| [PostgreSQL 16 isolation](https://www.postgresql.org/docs/16/transaction-iso.html) | SERIALIZABLE transactions can abort; retry whole transaction | `05d46458aba7b3d33fb5cab5494ab2ee1ee282f37d3f564654f03baed1501757` |
| [pgvector v0.8.7 README](https://github.com/pgvector/pgvector/blob/v0.8.7/README.md) | Filtering/iterative scans since 0.8.0, exact default search, tenant recall caveat | `19cebadeeb8d3181d01ef00399674ec41bd2f0363850cdccc7685d4260c5a1c6` |
| [Psycopg PostgreSQL types](https://www.psycopg.org/psycopg3/docs/basic/pgtypes.html) | Native range adaptation | `02cacc2cb6bef18203384f72b94b251c8421104f0fdd1018bfa7082b74f90e92` |
| [Psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html) | Async pool connection/transaction lifecycle | `e69c46b4f415e5a584bc1202b7013bf0cbf910d25e884a8e55e5ca85532f9432` |
| [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) | Snapshot identifier, standard input/output price reference; not an API inference test | `01ebbc8f3dac14e8ba7bc529bb7692a6c63e7a720277c8fdb210be5b3d60b4cb` |
| [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1) | Snapshot identifier, standard price reference; same-family judge limitation | `c29f72e72c0044163d84343b48650ca80ca20778b21ab15ee9e49cd984ff04f3` |
| [Pinned embedding config](https://huggingface.co/BAAI/bge-small-en-v1.5/blob/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/config.json) | hidden_size=384, max_position_embeddings=512 | `094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750` |
| [Pinned embedding model card](https://huggingface.co/BAAI/bge-small-en-v1.5/blob/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/README.md) | English model, CLS pooling, normalization, retrieval query prefix | `ddb964361a55c6e5dfca6361615854b260c9c960205d04c7520151aaa1d75837` |

## Repository authority

- [Proposal](../Graduation_Project_Proposal.md) §§7.4, 7.5, 8, 9, 10, 11, 14.
- [Technical specification](../TECHNICAL_SPECIFICATION.md), normative subject to higher proposal authority.
- [Existing source ledger](../reference/Source_Evidence_Ledger.md), rows [38]/[39] and benchmark/license caveats.
- [Existing CP arithmetic output](../validation/cp_bounds.output.txt).
- [Executed reduced SQL baseline](bitemporal-baseline.txt), deliberately limited and not V4 archival validation.

## Accuracy caveat found during review (resolved 2026-10-25)

The Technical Specification §9.3 multiplicity/H2 and H3 summaries previously conflicted with the higher-authority Proposal §§4.1/10.6 (in particular H2 contrasts/global Holm family, and relative LUAR non-inferiority). On 2026-10-25, `TECHNICAL_SPECIFICATION.md` §9.3 was reconciled directly with `Graduation_Project_Proposal.md` §§4.1 and 10.6 (3-test global Holm family across H1 superiority, H2 intersection-union `max(p_A5, p_A6)` with both contrasts positive, and H3 ASR reduction; paired `LUAR(P) − LUAR(A3) > −δ_luar`). Judge κ/accuracy thresholds and proposed budgets remain project decisions subject to pilot validation, not claims established by outside studies.
