# Risk Status Report — taskq-redo

| Field | Value |
|---|---|
| Document ID | RSR-2026-09-02 |
| Project | taskq-redo |
| Phase | P7 (Per-FR Delta) |
| Reporting date | 2026-09-02 |
| Author | P7 Risk Author |
| Scope | All 12 risks (R1–R12) seeded from SPEC.md §9 |
| Companion docs | `RISK_REGISTER.md`, `RISK_MITIGATION_PLANS.md` |

---

## 1. Executive summary

- **12 risks tracked**, seeded from SPEC.md §9.
- **10 HIGH risks** (score ≥ 9) — formal mitigation plans exist in `RISK_MITIGATION_PLANS.md`.
- **1 MEDIUM risk** (R12, score 6) — lightweight mitigation (single-transaction + row lock on the rate-bucket path).
- **0 LOW risks**.
- Top single risk: **R5 N+1 collapse on large tables** (score 20).
- **Open external inputs not yet available:**
  - `.methodology/deferred_fixes.md` — does not exist (no deferred-fix ledger written).
  - `.sessi-work/issue_registry.json` — does not exist.
  - `.sessi-work/gate3_result.json`, `gate4_result.json` — do not exist; current cycle is at Gate 1 (FR-10 done).
- **Known coverage gap from Gate 1:** `errors.py::_redact_db_url_password` (lines 495–499) uncovered — linked to R3.

---

## 2. Risk-level dashboard

| Tier | Count | IDs | Notes |
|------|-------|-----|-------|
| HIGH (≥ 9) | 10 | R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11 | All have formal plans; status below |
| MEDIUM (4–8) | 1 | R12 | Single-transaction + row-level lock |
| LOW (1–3) | 0 | — | — |

---

## 3. Per-risk status

Legend — Status: **Open** (no mitigation yet), **In progress** (mitigation in flight), **Implemented** (mitigation in code, awaiting verification), **Verified** (Gate 2 / Gate 4 sign-off complete), **Accepted** (low risk, no action).

| ID | Name | Score | Status | Mitigation owner | Target date | Verification gate |
|----|------|-------|--------|------------------|-------------|-------------------|
| R1 | v3 資料搬遷遺失資料 | 15 | Implemented | migrations maintainer | Gate 2 exit | `make verify-system` (NFR-12) |
| R2 | SQL injection | 10 | Implemented | repo steward (collective) | Gate 2 — verify CI grep wiring | bandit 0/0 + grep gate |
| R3 | API key 洩漏 | 15 | Implemented; coverage gap open | `service.auth` owner | Gate 4 (close coverage gap) | ast-cov 100% on errors.py redact path |
| R4 | 403 洩漏資源存在性 | 9 | Implemented | `service.auth` owner | Gate 2 — verify | FR-04 integration tests |
| R5 | N+1 崩潰 | 20 | In progress (baseline green) | `repository.session` owner | Gate 2 exit | SQL-count assertion + p95 < 80 ms |
| R6 | 錯誤 body 洩漏 | 12 | In progress (FR-10 in flight) | `api.errors` owner | Gate 2 exit | RFC 7807 integration tests |
| R7 | CancelledError 被吞 | 9 | Implemented | `service.runner` owner | Gate 4 | ast-error-handling + cancel test |
| R8 | Orphan subprocess | 9 | Implemented | `service.runner` owner | Gate 4 | `pytest -k orphan_subprocess` |
| R9 | 未跑 migration 就上線 | 15 | Implemented | DevOps / release engineer | Gate 2 — verify | `/readyz` integration tests |
| R10 | 連線池耗盡 | 9 | Implemented | `repository.session` owner | Gate 4 | load test ≤ pool size |
| R11 | Transitive 不相容 license | 9 | Implemented | release / dependency steward | Gate 2 — verify CI wiring | pip-licenses allowlist scan |
| R12 | Rate bucket 競態超放行 | 6 | Implemented | `service.ratelimit` + `repository.rate_repo` owners | Gate 4 | FR-05 race test |

---

## 4. Open items (not from SPEC §9, but observed in artifacts)

| Item | Source | Owner | Linked risk | Action |
|------|--------|-------|-------------|--------|
| `errors.py::_redact_db_url_password` (lines 495–499) uncovered | `gate1_result.json` (97.71 % coverage) | `service.auth` / `api.errors` owners | R3 | Add unit test asserting log line redacts DB URL password fragment |
| `mutation:scope` resolves to non-existent directories | `workflow_blocks.jsonl` | harness / SAB maintainer | R11 (spec drift) | Reconcile SAB layer→module map against actual `03-development/src/taskq_api/repository/*` and `service/*` modules |
| Phase 3 `env-check` did not PASS (unresolved) | `degradations.jsonl` | harness owner | R9 / R10 | Investigate whether env-check failure persists into Phase 7; verify no env contract drift |
| `taskq_api.__main__` file-level coverage exclude | `setup.cfg` | quality gate owner | R6 (entry-point surface) | Document exclusion in `RISK_REGISTER.md` §3 — already done here; ensure downstream gates accept it |

---

## 5. Verification roadmap

| Gate | Risks verified |
|------|----------------|
| Gate 2 (P3 exit) | R1, R2, R4, R5, R6, R9, R11 — full implementation + verification |
| Gate 4 (P6 exit) | R3 (close coverage gap), R7, R8, R10, R12 — final quality gate sign-off |

---

## 6. Risk register refresh triggers

This status report is regenerated when **any** of the following occurs:

1. A new FR is added that touches a high-risk module (`taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.repository.session`, `migrations/versions/v3_split_results.py`).
2. Gate 2 or Gate 4 completes (new evidence updates status column).
3. A new degradation appears in `.methodology/degradations.jsonl` with no matching register entry.
5. SPEC.md is amended and §9 risk matrix changes.
6. `.methodology/deferred_fixes.md` or `.sessi-work/issue_registry.json` are created (currently absent).

---

## 7. Sign-off block

| Role | Name | Date | Status |
|------|------|------|--------|
| P7 Risk Author | (auto) | 2026-09-02 | Drafted |
| P7 Owner review | pending | — | Open |
| Gate 2 DRI | pending | — | Open |
| Gate 4 DRI | pending | — | Open |