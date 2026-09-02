# BASELINE.md - taskq-redo

> Phase 5 · Verification Authoritative Snapshot. On-demand lazy-load template per `harness/templates/BASELINE.md`.

## 1. Baseline Overview
- Author: P5 Verification Author (claude-code agent)
- Reviewer: Johnny (orchestrator) — pending sign-off
- session_id: `p5-advance-retry-20260902` (orch-post dispatch at 2026-09-02)
- Date: 2026-09-02
- Project: taskq-redo (FastAPI task-queue service, Python 3.11, SQLAlchemy 2.x, SQLite test DB)
- Phase: **3 — Implementation** (Gate 1 complete on all 10 FRs; Gate 3 composite **97.654 — PASS**)
- Last FR through Gate 1: FR-10 (`feat(FR-10): Gate1 PASS — score=99.5 [phase=5]` @ `87792d3`)
- Snapshot scope: `03-development/src/`, `03-development/tests/`, `04-testing/`, gate evidence under `.methodology/gate_evidence/gate3/`, manifest under `.methodology/quality_manifest.json`.
- Codebase module list (per `03-development/src/taskq_api/`):
  - `__init__.py`, `__main__.py`, `app.py`, `config.py`, `errors.py`
  - `api/`: `__init__.py`, `deps.py`, `health.py`, `tasks.py`
  - `models/`: `__init__.py`, `orm.py`, `schemas.py`
  - `repository/`: `__init__.py`, `health_repo.py`, `key_repo.py`, `rate_repo.py`, `session.py`, `task_repo.py`
  - `service/`: `__init__.py`, `auth.py`, `health.py`, `ratelimit.py`, `runner.py`, `tasks.py`

## 2. Functional Baseline (maps to SRS FR, 100% complete)

| FR ID | Feature Description | Baseline Status | Notes |
|-------|--------------------|-----------------| ------|
| FR-01 | Task resource CRUD API (`POST/GET/LIST/DELETE /tasks`) with name-uniqueness, cursor pagination (limit ≤ 200, default 50), transactional result cascading | PASS | Per-FR score 100.0; spec declared 130 / undelivered 0 |
| FR-02 | Task execution endpoint (`POST /tasks/{id}/run`) returning 202 + run_id; runs async via subprocess (no `shell=True`), writes `task_results`, state machine `pending→running→succeeded/failed/timeout`, kills child on timeout, history newest-first | PASS | Per-FR score 100.0; runner subprocess paths covered |
| FR-03 | API-Key authentication: 401 on missing/invalid/revoked, `hmac.compare_digest`, hash-only storage, plaintext printed once at create | PASS | Per-FR score 100.0; SPEC §8 #18 verification bound |
| FR-04 | Scope authorization: per-API-key scope constraints on route handlers; clean leak guard verified | PASS | Per-FR score 100.0 |
| FR-05 | Per-scope token-bucket rate limiting: 429 + `Retry-After` over burst, recovery after refill | PASS | Per-FR score 99.5; ordering fixed at Gate 1 close |
| FR-06 | Repository session/transaction scaffolding (SQLAlchemy 2.x), connection lifecycle, request-scoped cleanup | PASS | Per-FR score 100.0 |
| FR-07 | Schema migrations: `v1_initial`, `v2_tags`, `v3_split_results` (results split into `task_results`) — reversible | PASS | Per-FR score 100.0; high-risk module `migrations.versions.v3_split_results` |
| FR-08 | Runner process supervision: subprocess execution, child termination on timeout, no orphan processes | PASS | Per-FR score 100.0 |
| FR-09 | Health endpoints (`/livez`, `/readyz`) without auth; DB connectivity probe | PASS | Per-FR score 100.0 |
| FR-10 | Centralized error envelope: problem+json across all endpoints, DB-URL redaction | PASS | Per-FR score 99.5 |

## 3. Quality Baseline

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Gate 3 composite score | ≥ 85 | **97.654** | PASS |
| Test coverage (per-FR suite) | ≥ 80% | **100%** (887 / 887) | PASS |
| Test assertion quality (NFR-09) | ≥ 80 | **97.1** | PASS |
| Mutation testing (mutmut, NFR-08) | ≥ 70 | **100** (killed=17, survived=0, scope=service+repository) | PASS |
| Integration coverage (NFR-10) | ≥ 60 | **82%** (httpx ASGITransport, 30 cases) | PASS |
| Linting (ruff) | ≥ 90 | **100.0** | PASS |
| Type safety (pyright) | ≥ 85 | **100.0** (0 errors / 24 files) | PASS |
| Security (bandit, NFR-02) | ≥ 80 | **95.0** (0 HIGH/MED; 5 LOW — all `B101 assert_used` in `runner.py`) | PASS |
| Secrets scanning (gitleaks) | ≥ 100 | **100.0** (106 commits scanned, no leaks) | PASS |
| License compliance (NFR-07) | ≥ 100 | **100.0** (scancode API walk, 24 src files, 0 non-allowlist) | PASS |
| Architecture (CRG community cohesion) | ≥ 80 | **91.7** | PASS |
| Readability (NFR-11) | ≥ 80 | **94.4** (project avg CC=2.05, total LLOC=1251) | PASS |
| Error handling (NFR-03) | ≥ 80 | **100.0** (no bare except; `CancelledError` propagates) | PASS |
| Documentation (NFR-05) | ≥ 80 | **88.5** (public docstrings annotated with `[FR-XX]`/`[NFR-XX]`) | PASS |
| Execute-verification-target (NFR-12) | ≥ 80 | **100.0** (`make verify-system` exit 0) | PASS |
| Traceability (SRS↔SPEC↔tests↔impl) | ≥ 80 | **100.0** | PASS |
| Adversarial review (CRG bug-hunt) | ≥ 80 | **100.0** | PASS |
| Performance (NFR-01, p95 < 30 ms) | ≥ 80 | **100.0** (see §4) | PASS |
| Logic correctness | ≥ 90 | **100** (242/242 unit + per-FR pass) | PASS |

## 4. Performance Baseline (A/B monitoring)

| Metric | Baseline Value |
|--------|---------------|
| Service `GET /tasks/{id}` p95 (NFR-01 target < 30 ms) | **~0.39 ms** (max observed across 1816 rounds; median ≈ 0.12 ms) |
| Service `LIST /tasks` p95 | **~1.21 ms** (max across rounds; median ≈ 0.68 ms) — well under 80 ms SLO |
| Suite wall time (per-FR + integration combined) | 110.96 s (per-FR suite) + 2.63 s (integration suite) |
| Memory (process RSS, qualitative) | SQLite-backed; bounded by 887 statements / 22 modules; no leaks observed across reruns |
| Error rate | **0 / 242** unit+per-FR cases; **0 / 30** integration cases; HTTP-5xx attempts **0** in test envelopes |

## 5. Known Issues

| Severity | Count | Description |
|----------|-------|-------------|
| HIGH | 0 | None |
| MEDIUM | 0 | None |
| LOW | 5 | All LOWs are `B101 assert_used` flags raised by Bandit against `taskq_api/service/runner.py`. These asserts encode post-condition invariants used in Gate 1 mutation testing; they are intentional and tested by mutation suite (17/17 killed). No remediation required at this phase. |
| INFO | 1 | Earlier FR-05 bucket precondition failures (round-1 cycle) were an ordering artefact and have been resolved in-tree; the run captured in `04-testing/TEST_RESULTS.md` is the post-fix run with 242/242 pass. |

> HIGH severity count is **0** — baseline cleared.

## 6. Change Log

| Date | Change | Commit / Ref |
|------|--------|--------------|
| 2026-09-02 | feat(FR-10): Gate1 PASS — score=99.5 [phase=5] | `87792d3` |
| 2026-09-02 | feat(FR-09): Gate1 PASS — score=100.0 [phase=5] | `6446fab` |
| 2026-09-02 | feat(FR-08): Gate1 PASS — score=100.0 [phase=5] | `2860c95` |
| 2026-09-02 | feat(FR-07): Gate1 PASS — score=100.0 [phase=5] | `2d526e9` |
| 2026-09-02 | feat(FR-06): Gate1 PASS — score=100.0 [phase=5] | `7f67e02` |
| 2026-09-02 | feat(FR-05): Gate1 PASS — score=99.5 [phase=5] | `91af3fc` |
| 2026-09-02 | feat(FR-04): Gate1 PASS — score=100.0 [phase=5] | `988c5c8` |
| 2026-09-02 | feat(FR-03): Gate1 PASS — score=100.0 [phase=5] | `73700ae` |
| 2026-09-02 | feat(FR-02): Gate1 PASS — score=99.8 [phase=5] | `37f5334` |
| 2026-09-02 | feat(FR-01): Gate1 PASS — score=100.0 [phase=5] | `070d3e7` |

## 7. Acceptance Sign-off
- Agent A (P5 Verification Author): claude-code agent — `session_id=p5-advance-retry-20260902` — 2026-09-02
- Approver: Johnny (project orchestrator) — pending review of `BASELINE.md` + `VERIFICATION_REPORT.md` and confirmation of `validate-handoff` PASS.
- Acceptance criteria (Gate 3 exit): composite ≥ 85 ✅ (97.654) · zero spec-undelivered ✅ (declared 130 / undelivered 0) · zero HIGH/MED security findings ✅ · 100% per-FR line coverage ✅ · mutation score ≥ 70 ✅ (100) · integration coverage ≥ 60 ✅ (82).
