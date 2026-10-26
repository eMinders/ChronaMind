# Gate J5 Benchmark & Own-Suite Split Freeze List (`RL-026`)

- **Task:** `RL-026` ([Issue #22](https://github.com/Abdulrahman-Ayman-Farid/ChronaMind/issues/22))
- **Quality Gate:** `Gate J5` ([`PROJECT_ROADMAP.md`](../../../PROJECT_ROADMAP.md) §J, [`TRACEABILITY_MATRIX.md`](../../../TRACEABILITY_MATRIX.md) §5)
- **Freeze Date:** `2026-10-26`
- **Status:** `FROZEN` (development vs. test splits locked prior to any pilot `RL-100` or confirmatory test run `RL-101`–`RL-105`)

---

## 1. Frozen Split Files Summary (`research/benchmarks/splits/*.yaml`)

All six split files are serialized in canonical, deterministic YAML 1.2 (strict JSON subset, UTF-8, LF line endings, sorted keys) and verified by `python -m research.benchmarks.splits.freeze`.

| Split File | Suite IDs Covered | Cluster Key | Split Seed | Generator Seeds (`dev` / `test`) | `dev` Clusters | `test` Clusters | Total Clusters | Overlap (`dev ∩ test`) | Split Payload SHA-256 | Split File SHA-256 |
|---|---|---|---:|---|---:|---:|---:|---|---|---|
| [`longmemeval.yaml`](longmemeval.yaml) | `LongMemEval_oracle`, `LongMemEval_S`, `LongMemEval_M` | `question_id` | `42` | N/A (public benchmark) | `100` (`20%`) | `400` (`80%`) | `500` | `0` (`∅`) | `2755d9832d429c80302e29f07b407fbc4e18c89a80f9e63673967c950774e92e` | `95b10e71dd180f07d37fb753c94bbd6e29e5bed208d1f397a9fb7b1577d9980f` |
| [`locomo.yaml`](locomo.yaml) | `LoCoMo` | `conversation_id` | `42` | N/A (public benchmark) | `2` (`20%`) | `8` (`80%`) | `10` | `0` (`∅`) | `452a546d3bff2186132eb78066d2899b0de622079499e62d4616521d9a2352b1` | `1347ab54d7bb399523f32bac337580fb0b9dce8fc0096a4f7da01b3aea83d442` |
| [`own_T.yaml`](own_T.yaml) | `T` | `scenario_id` | `42` | `102401` / `202401` | `20` | `50` | `70` | `0` (`∅`) | `355111dc767dc21a66712c6d42d471c359a9e70e6b40b88826abf423132f2f71` | `5f854ea4f6c810ede52622c4d6e57e63b542968589e36bb998c08e53e30a01a9` |
| [`own_G.yaml`](own_G.yaml) | `G` | `scenario_id` | `42` | `102201` / `202201` | `20` | `50` | `70` | `0` (`∅`) | `ea47b085fe550f05071ae4bf659195d9a5a4a0674768f4b02c5170c61eab0b22` | `b3e944e2b506a1bbaaceb74648e54720007f38cdaeff21bcbeccda77d06bb1ae` |
| [`own_D.yaml`](own_D.yaml) | `D` | `scenario_id` | `42` | `102301` / `202301` | `20` | `50` | `70` | `0` (`∅`) | `3c158a678c563ae649e526aa0d096edf115addece4e0a56b3cf14014149c1102` | `c04eea6a9e388dd1d25cbdfcce4c24f259ce02260c1afdf8c8ef07c6f65b648d` |
| [`own_I.yaml`](own_I.yaml) | `I` | `scenario_id` | `42` | `102202` / `202202` | `20` | `50` | `70` | `0` (`∅`) | `d4831effdf9b8336b1308d30cb4d2e9990da8240b983d29f3c11d216b00aaed6` | `0050917cc478ccb2b158eca16502f5cfff323dccbbaa7d71ab8d83df40193331` |

---

## 2. Bound Source Dataset Manifests & File SHA-256 Digests

| Suite Family | Dataset File | Pinned Manifest Location | Dataset File SHA-256 |
|---|---|---|---|
| `LongMemEval` | `longmemeval_oracle.json` | [`../longmemeval/MANIFEST.txt`](../longmemeval/MANIFEST.txt) | `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` |
| `LongMemEval` | `longmemeval_s_cleaned.json` | [`../longmemeval/MANIFEST.txt`](../longmemeval/MANIFEST.txt) | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| `LongMemEval` | `longmemeval_m_cleaned.json` | [`../longmemeval/MANIFEST.txt`](../longmemeval/MANIFEST.txt) | `9d79e5524794a2e6900a3aa9cb7d9152c5a3e8319c9a87c25494ba1eacee495f` |
| `LoCoMo` | `locomo10.json` | [`../locomo/MANIFEST.txt`](../locomo/MANIFEST.txt) | `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4` |
| `Own Suite T` | `own_T_dev.json` | [`../own_T/MANIFEST.txt`](../own_T/MANIFEST.txt) | `f20611d5254f9156ce89baf044693485a59816ec1236d76d1512a796b12388fd` |
| `Own Suite T` | `own_T_test.json` | [`../own_T/MANIFEST.txt`](../own_T/MANIFEST.txt) | `c3ff2649473ab4a52b333985a3d77852c77005cd1e91e94a991e8ab1713ff249` |
| `Own Suite G` | `own_G_dev.json` | [`../own_G/MANIFEST.txt`](../own_G/MANIFEST.txt) | `0c63d5829c8d4c18e20566142b7e322fd0d26c45302ce92083b286ac246a0ef9` |
| `Own Suite G` | `own_G_test.json` | [`../own_G/MANIFEST.txt`](../own_G/MANIFEST.txt) | `002736d2698aefc25d572ae9aeefc3472cd3ef9ad3b11fb4cf12ab0e808ba740` |
| `Own Suite D` | `own_D_dev.json` | [`../own_D/MANIFEST.txt`](../own_D/MANIFEST.txt) | `ee983a04ce6fc7dbdf33b2716d36fc2c4085e2af8a6eb2a8f6c66b7d92302d8c` |
| `Own Suite D` | `own_D_test.json` | [`../own_D/MANIFEST.txt`](../own_D/MANIFEST.txt) | `31df556b5346c8a4c3130d271573a5dbfa0af9068be5129c723f87cce36fea6d` |
| `Own Suite I` | `own_I_dev.json` | [`../own_I/MANIFEST.txt`](../own_I/MANIFEST.txt) | `7da3982c823f24d4909710f3063d604a6bdc04c9cb905ad7646aba84cdcabb4d` |
| `Own Suite I` | `own_I_test.json` | [`../own_I/MANIFEST.txt`](../own_I/MANIFEST.txt) | `25548ba8038e4fe79335dd4491de52def32c7c36c54109bbd5d51d475983c1f9` |

---

## 3. Stratified & Per-Suite Split Counts

### 3.1 `LongMemEval` (`longmemeval.yaml`, `seed = 42`)

Stratified by `(is_abstention, question_type)` using integer Hamilton largest-remainder quotas (`20%` `dev`, `80%` `test`) and deterministic SHA-256 ranking (`sha256("longmemeval:42:" + question_id)`):

| Stratum / Population | `dev` Count (`20%`) | `test` Count (`80%`) | Total (`100%`) | Role in Protocol ([`evaluation/pilot_plan.md`](../../../evaluation/pilot_plan.md) §§1.1, 2.8) |
|---|---:|---:|---:|---|
| **Total Questions / Histories** | `100` | `400` | `500` | Full `LongMemEval` population across `oracle`, `S`, and `M` |
| **Primary Recall-Eligible (`LongMemEval_S`)** | `83` | `331` | `414` | Answerable non-assistant user-grounded histories for `H2` Evidence Recall@k |
| **Answerable (`not _abs`)** | `94` | `376` | `470` | Answerable QA histories (`414` user-grounded + `56` assistant-sourced) |
| **Abstention (`_abs`)** | `6` | `24` | `30` | Unanswerable false-premise histories (`tau_ans` tuning on `dev`) |
| **`single-session-assistant`** | `11` | `45` | `56` | Assistant-sourced turns (excluded from user-turn Evidence Recall@k denominator) |
| `question_type: knowledge-update` | `16` (`15` ans + `1` abs) | `62` (`57` ans + `5` abs) | `78` | Stratified `question_type` |
| `question_type: multi-session` | `27` (`24` ans + `3` abs) | `106` (`97` ans + `9` abs) | `133` | Stratified `question_type` |
| `question_type: single-session-assistant` | `11` (`11` ans + `0` abs) | `45` (`45` ans + `0` abs) | `56` | Stratified `question_type` |
| `question_type: single-session-preference` | `6` (`6` ans + `0` abs) | `24` (`24` ans + `0` abs) | `30` | Stratified `question_type` |
| `question_type: single-session-user` | `14` (`13` ans + `1` abs) | `56` (`51` ans + `5` abs) | `70` | Stratified `question_type` |
| `question_type: temporal-reasoning` | `26` (`25` ans + `1` abs) | `107` (`102` ans + `5` abs) | `133` | Stratified `question_type` |

### 3.2 `LoCoMo` (`locomo.yaml`, `seed = 42`)

Conversation-level split (`sample_id` / `conversation_id`) via deterministic SHA-256 ranking (`sha256("locomo:42:" + sample_id)`):
- **`dev` conversations (`2`):** `conv-41`, `conv-48` (`432` total questions: Cat 1 = `52`, Cat 2 = `69`, Cat 3 = `18`, Cat 4 = `204`, Cat 5 = `89`).
- **`test` conversations (`8`):** `conv-26`, `conv-30`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-49`, `conv-50` (`1,554` total questions: Cat 1 = `230`, Cat 2 = `252`, Cat 3 = `78`, Cat 4 = `637`, Cat 5 = `357`).

### 3.3 Own Suites (`own_T.yaml`, `own_G.yaml`, `own_D.yaml`, `own_I.yaml`)

Scenario-level splits bound by deterministic `sha256(scenario_id)` digests and disjoint generator seeds so no scenario ever appears in both splits:
- **Own Suite `T` (`H1` Temporal-Change):** `20` `dev` scenarios (`own_T_dev_001`–`own_T_dev_020`, `120` questions: `60` `history-dependent`, `60` `present-state`, `80` audit probes) and `50` `test` scenarios (`own_T_test_001`–`own_T_test_050`, `300` questions: `150` `history-dependent`, `150` `present-state`, `200` audit probes).
- **Own Suite `G` (`H3` Write-Governance):** `20` `dev` scenarios (`own_G_dev_001`–`own_G_dev_020`, `240` episodes: `80` legitimate, `120` non-assertion, `40` injected) and `50` `test` scenarios (`own_G_test_001`–`own_G_test_050`, `600` episodes: `200` legitimate, `300` non-assertion, `100` held-out injected).
- **Own Suite `D` (`H4` Deletion & Canary):** `20` `dev` scenarios (`own_D_dev_001`–`own_D_dev_020`, `80` primary deletion events, `20` re-learning controls, `200` probes across `7` channels) and `50` `test` scenarios (`own_D_test_001`–`own_D_test_050`, `200` primary deletion events, `50` re-learning controls, `500` probes across `7` channels).
- **Own Suite `I` (Invariant 7 Cross-User Isolation):** `20` `dev` scenarios (`own_I_dev_001`–`own_I_dev_020`, `60` probes, `0` expected exposures) and `50` `test` scenarios (`own_I_test_001`–`own_I_test_050`, `150` probes, `0` expected exposures).

---

## 4. Gate J5 Governance Sign-Off

Per [`PROJECT_ROADMAP.md`](../../../PROJECT_ROADMAP.md) §J (`Gate J5`) and [`PLAN/ASSIGNMENTS.md`](../../../PLAN/ASSIGNMENTS.md):
- Zero cluster overlap (`dev ∩ test = ∅`) is verified mechanically across `question_id` (`LongMemEval`), `scenario_id` (`own_T`, `own_G`, `own_D`, `own_I`), and `conversation_id` (`LoCoMo`).
- All `dev` splits are available for `RL-100` pilot calibration; all `test` splits remain locked behind the `RL-030` one-shot test-run guard until `Gate J7` (`RL-094`) and `Gate J8` (`RL-100` pilot freeze) are complete.

| Role | Responsibility | Identifier | Status | Date |
|---|---|---|---|---|
| **Task Owner** | Benchmark & Own-Suite Split Freeze (`RL-026`) | `Member 4 (R-DB)` | **Signed (Single-maintainer repository governance attestation, Issue #22)** | `2026-10-26` |
| **Peer Reviewer** | Gate `J5` Disjointness & Checksum Audit | `Member 1 (R-LEAD)` | **Reviewed & Approved (Single-maintainer repository governance attestation, Issue #22)** | `2026-10-26` |
