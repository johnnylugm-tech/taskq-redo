# Risk Register — taskq-redo

| Field | Value |
|---|---|
| Document ID | RR-2026-09-02 |
| Project | taskq-redo |
| Phase | P7 (Per-FR Delta) |
| Author | P7 Risk Author |
| Source of truth | SPEC.md §9 risk matrix (R1–R12) |
| Current Gate | Gate 1 (P7 ongoing); Gate 3/4 not yet run for current cycle |
| Scoring scale | Likelihood 1 (Rare) – 5 (Almost Certain); Impact 1 (Negligible) – 5 (Severe) |
| Risk score | Likelihood × Impact; threshold for formal mitigation plan = ≥ 9 |

---

## 1. Scoring legend

| Score | Likelihood label | Impact label | Action |
|---|---|---|---|
| 1 | Rare | Negligible | Accept |
| 2 | Unlikely | Minor | Monitor |
| 3 | Possible | Moderate | Mitigate (lightweight) |
| 4 | Likely | Major | Mitigate (formal plan) |
| 5 | Almost Certain | Severe | Mitigate (formal plan + escalation) |

HIGH risks (score ≥ 9): formal mitigation plan required → see `RISK_MITIGATION_PLANS.md`.
MEDIUM risks (score 4–8): lightweight mitigation tracked in §3 below.
LOW risks (score 1–3): accepted; reviewed at Gate exit.

---

## 2. Risk register (seeded from SPEC.md §9)

SPEC.md §9 uses a 3-tier scale ("高/中/低"). Mapping to 1–5:
- 高 (High) = Impact 5 (Severe)
- 中 (Medium) = Impact 3 (Moderate)
- 低 (Low) = Impact 2 (Minor)
- 高 (High likelihood) = Likelihood 4
- 中 (Medium likelihood) = Likelihood 3
- 低 (Low likelihood) = Likelihood 2

| ID | Risk name | L | I | Score | Category | Mitigation approach (SPEC §9) | Linked FR/NFR |
|----|-----------|---|---|-------|----------|------------------------------|--------------|
| R1 | v3 資料搬遷遺失資料 (data migration data loss) | 3 | 5 | **15** | Data integrity | 往返可逆性測試以真實 DB 逐欄比對 (round-trip reversibility with real DB column-by-column compare) | FR-07 / §8 #12 |
| R2 | SQL injection | 2 | 5 | **10** | Security | 禁字串拼接 + ORM/參數化 + grep gate (ban string concat + ORM/parameterized + grep CI gate) | NFR-02 |
| R3 | API key 洩漏 (API key leak in logs/storage) | 3 | 5 | **15** | Security / Compliance | 雜湊儲存 + 常數時間比對 + 明文只印一次 (hash storage + constant-time compare + plaintext shown once at creation) | FR-03 |
| R4 | 403 洩漏資源存在性 (403 leakage of resource existence) | 3 | 3 | **9** | Information disclosure | 授權判定在資源查詢之前 (authorization decided BEFORE resource lookup) | FR-04 / §8 #6 |
| R5 | N+1 查詢在大表上崩潰 (N+1 query collapse on large tables) | 4 | 5 | **20** | Performance | 顯式預載 (joinedload/selectinload) + SQL 計數斷言 (SQL statement count assertion) | NFR-01 / §8 #14 |
| R6 | 錯誤 body 洩漏內部結構 (error body leaks internal structure) | 4 | 3 | **12** | Information disclosure | RFC 7807 固定欄位 + detail 白名單 (fixed RFC 7807 fields + detail allowlist) | FR-10 |
| R7 | `CancelledError` 被吞 → 關閉時卡死 (swallowed CancelledError → shutdown deadlock) | 3 | 3 | **9** | Concurrency / reliability | 明文禁令 + 測試斷言 (explicit ban + assertion in tests) | NFR-03 |
| R8 | 任務 timeout 留下孤兒進程 (task timeout leaves orphan subprocess) | 3 | 3 | **9** | Resource leak / reliability | `kill()` + `await wait()` (SIGTERM then SIGKILL with await wait) | FR-08 / §8 #25 |
| R9 | 部署後忘記跑 migration (deployed without running migration) | 3 | 5 | **15** | Operations / data integrity | `/readyz` fail closed (readiness probe hard-fails if alembic_version absent or behind) | FR-09 / §8 #11 |
| R10 | 連線池耗盡 (DB connection pool exhaustion) | 3 | 3 | **9** | Capacity / performance | `pool_pre_ping` + 併發上限 (pool_pre_ping + concurrency cap) | FR-06 / FR-08 |
| R11 | transitive 依賴引入不相容 license (transitive dep pulls incompatible license) | 3 | 3 | **9** | License compliance | lock 檔 + 全樹掃描 (lock file + transitive tree scan) | NFR-07 |
| R12 | rate bucket 競態導致超放行 (rate bucket race → over-allowance) | 3 | 2 | **6** | Concurrency / fairness | 單一交易 + row-level lock (single transaction + `SELECT ... FOR UPDATE`) | FR-05 |

### 2.1 Scoring summary

| Tier | Count | IDs | Total weighted score |
|---|---|---|---|
| HIGH (≥ 9) | 10 | R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11 | 121 |
| MEDIUM (4–8) | 1 | R12 | 6 |
| LOW (1–3) | 0 | — | — |

Note: R5 (N+1) is the highest single risk (score 20 = 4×5). All 11 risks except R12 require formal mitigation plans under the ≥ 9 threshold.

---

## 3. Open issues / deferred items (cross-reference)

The task asked us to ingest `.methodology/deferred_fixes.md` and `.sessi-work/issue_registry.json`. Status:

- `.methodology/deferred_fixes.md` — **does not exist**. No deferred-fix ledger has been written.
- `.sessi-work/issue_registry.json` — **does not exist**. No issue registry has been written.
- `.sessi-work/gate3_result.json` / `gate4_result.json` — **do not exist**; Gate 3/4 have not yet run in the current cycle (current state: Gate 1 complete at FR-10, P7 in progress).
- `.sessi-work/decision_logs/` directory — **does not exist** as a directory of decision logs.

Inferred open items from observable artifacts:

| Source | Open item | Maps to risk |
|--------|-----------|--------------|
| Gate 1 result (`gate1_result.json`) | `errors.py::_redact_db_url_password` (lines 495–499) uncovered by FR-10 tests → 97.71 % line coverage | R3 (key/secret leakage surface) |
| `degradations.jsonl` | Phase 3 `env-check` did not PASS (unresolved) | R10 / R9 (env contract drift) |
| `workflow_blocks.jsonl` | SAB `mutation:scope` resolved to non-existent directories (`key_repo`, `rate_repo`, `session`, `task_repo`, `auth`, `health`, `ratelimit`, `runner`, `tasks`) | R11 (compliance + spec drift) |
| `setup.cfg` (mutation score exclusion list) | `taskq_api.__main__` excluded from coverage at file level | R6 (entry-point surface area) |

---

## 4. Risk categories used

- **Security** — injection, auth/secrets, authorization leakage
- **Information disclosure** — error bodies, 403 side-channels
- **Data integrity** — migration loss, readyz state
- **Performance** — N+1, connection pool
- **Concurrency / reliability** — async cancellation, race conditions, orphan processes
- **Operations** — deployment hygiene, env contract
- **License compliance** — transitive dependencies
- **Resource leak** — orphan subprocesses, pool exhaustion

---

## 5. Owner & next-review

| Item | Owner | Cadence |
|---|---|---|
| Register maintenance | P7 Risk Author | On every Phase transition |
| Mitigation plan execution | Per risk (see `RISK_MITIGATION_PLANS.md`) | Tracked in Gate 4 |
| Next review trigger | Gate 4 exit OR any new FR touching a high-risk module | Per Gate schedule |