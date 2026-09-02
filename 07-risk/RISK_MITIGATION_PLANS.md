# Risk Mitigation Plans — taskq-redo

| Field | Value |
|---|---|
| Document ID | RMP-2026-09-02 |
| Project | taskq-redo |
| Scope | HIGH risks (likelihood × impact ≥ 9) per `RISK_REGISTER.md` |
| Author | P7 Risk Author |
| Owners | DRI = Directly Responsible Individual; where the SPEC does not name an owner, default to the module owner listed in SPEC.md §10 ("高風險模組") |

Plans ordered by score descending.

---

## Plan summary

| Risk | Score | Owner | Status | Target |
|------|-------|-------|--------|--------|
| R5 — N+1 崩潰 | 20 | `taskq_api.repository.session` owner | In progress (NFR-01 baseline asserted) | Gate 2 exit |
| R1 — 資料搬遷遺失 | 15 | `migrations/versions/v3_split_results.py` owner | Done (FR-07 implemented, round-trip test green) | Gate 2 exit — verify |
| R3 — API key 洩漏 | 15 | `taskq_api.service.auth` owner | Done (FR-03 implemented; coverage gap on `_redact_db_url_password` noted) | Gate 4 — close coverage gap |
| R9 — 未跑 migration 就上線 | 15 | DevOps / release engineer | Implemented (FR-09 `/readyz` fail-closed) | Gate 2 exit — verify |
| R6 — 錯誤 body 洩漏 | 12 | `taskq_api.api.errors` owner | In progress (FR-10 RFC 7807 fields + detail allowlist) | Gate 2 exit |
| R2 — SQL injection | 10 | `taskq_api.repository.*` owners | Implemented (NFR-02: ORM-only + grep CI gate) | Gate 2 — verify grep gate runs |
| R4 — 403 side-channel | 9 | `taskq_api.service.auth` owner | Implemented (FR-04: authz before lookup) | Gate 2 — verify |
| R7 — CancelledError swallowed | 9 | `taskq_api.service.runner` owner | Implemented (NFR-03 + tests) | Gate 4 — verify |
| R8 — Orphan subprocess | 9 | `taskq_api.service.runner` owner | Implemented (FR-08: kill + await wait) | Gate 4 — verify |
| R10 — Pool exhaustion | 9 | `taskq_api.repository.session` owner | Implemented (FR-06/08: pool_pre_ping + concurrency cap) | Gate 4 — load test |
| R11 — Transitive license | 9 | Release / dependency steward | Implemented (NFR-07: lock + tree scan) | Gate 2 — verify scan green |

---

## R5 — N+1 查詢在大表上崩潰 (score 20)

**Category:** Performance
**Linked modules:** `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.service.tasks`
**Linked FR/NFR:** NFR-01 / SPEC §8 #14

**Risk description**
List endpoints (`GET /v1/tasks?limit=50`) issue additional queries per row when relationships are accessed lazily. On a 10 000-row production table this multiplies query count by N, blowing past p95 budget (SPEC §11: list endpoint p95 < 80 ms).

**Mitigation approach**
1. Use `joinedload` / `selectinload` for every relationship on list-query code paths (no implicit lazy loads).
2. SQLAlchemy event listener counts `select` statements per request and asserts a constant upper bound (SPEC §11: "與筆數無關").
3. pytest-benchmark p95 regression test on a 10 k-row fixture.

**Verification**
- `pytest -k n_plus_one` → asserts SQL count constant
- `pytest -k tasks_list_p95` → asserts p95 < 80 ms on 10 k seed
- `lint-imports` still green (no new forbidden imports)

**Owner:** `repository.session` maintainer (SPEC §10 high-risk module)
**Target date:** Gate 2 exit (P3 → P4 transition). Already passing per `gate1_result.json` (`architecture_constraints: 100`), so tracking shifts to load verification only.

---

## R1 — v3 資料搬遷遺失資料 (score 15)

**Category:** Data integrity
**Linked module:** `migrations/versions/v3_split_results.py` (SPEC §10 high-risk module)
**Linked FR/NFR:** FR-07 / SPEC §8 #12

**Risk description**
The Alembic `v3_split_results` migration restructures the results schema; partial backfill or column-mismatch silently corrupts user data.

**Mitigation approach**
1. Round-trip reversibility test using a real SQLite DB seeded with v2 data.
2. Column-by-column comparison of pre-/post-upgrade and post-downgrade payloads.
3. Test must run against actual SQLite file (no mocks — SPEC §11: "真實 SQLite 檔案測試").
4. NFR-09: test must NOT be skipped; must contain real assertions (ast-assertions gate).

**Verification**
- `pytest -k v3_split_results_roundtrip` → green
- `make verify-system` → includes migration round-trip (NFR-12)
- `pytest --strict-markers` shows 0 skipped tests

**Owner:** migrations maintainer (DRI: author of `v3_split_results.py`)
**Target date:** Gate 2 exit. Implementation complete; ongoing verification per FR-07 acceptance.

---

## R3 — API key 洩漏 (score 15)

**Category:** Security / Compliance
**Linked module:** `taskq_api.service.auth`, `taskq_api.repository.key_repo`
**Linked FR/NFR:** FR-03 / NFR-04

**Risk description**
Plaintext or partially-redacted API keys may end up in logs, error bodies, or DB error traces. Constant-time comparison prevents timing attacks; one-time plaintext display at creation is the only acceptable plaintext surface.

**Mitigation approach**
1. Hash keys at rest; store only hash + prefix.
2. Constant-time comparison using `hmac.compare_digest`.
3. Plaintext shown ONCE at creation, never logged, never returned by any other endpoint.
4. NFR-04: log redaction unit test for `TASKQ_DB_URL` password fragment.
5. FR-03: CLI prints plaintext only on the create response, and the runtime never echoes it again.

**Known gap (from `gate1_result.json`)**
- `errors.py::_redact_db_url_password` (lines 495–499) is uncovered by FR-10 tests → 97.71 % coverage.
- Action: add a test asserting that a log line containing a DB URL has its password replaced before emission.

**Verification**
- Unit test: hash + verify round-trip via `hmac.compare_digest`
- Unit test: log capture redaction (closes coverage gap)
- Integration test: `GET /v1/keys/{id}` never returns plaintext

**Owner:** `service.auth` owner (SPEC §10 high-risk module)
**Target date:** Gate 4 — close coverage gap on `_redact_db_url_password` before final sign-off.

---

## R9 — 部署後忘記跑 migration (score 15)

**Category:** Operations / Data integrity
**Linked module:** deployment / runtime readiness
**Linked FR/NFR:** FR-09 / SPEC §8 #11

**Risk description**
A new release ships with schema changes; ops forgets to run `alembic upgrade head`. New code then writes against a stale schema, producing silent data corruption or 500s.

**Mitigation approach**
1. `/readyz` reads `alembic_version` and compares to the head revision baked into the build.
2. If drift detected → 503 (fail closed) — NOT 200, NOT 200 with warning.
3. Probe wired into the deployment pipeline; rollout halts on non-200.

**Verification**
- Integration test: simulate `alembic_version` behind head → expect 503
- Integration test: matching version → 200
- Make target included in `make verify-system`

**Owner:** DevOps / release engineer (DRI by convention; project-level DRI listed in handover)
**Target date:** Gate 2 exit. Implemented per FR-09; verification step to be run during P3→P4 transition.

---

## R6 — 錯誤 body 洩漏內部結構 (score 12)

**Category:** Information disclosure
**Linked module:** `taskq_api.api.errors`
**Linked FR/NFR:** FR-10

**Risk description**
Unhandled exceptions leak stack traces, SQL fragments, or internal class names into the response body, giving attackers reconnaissance material.

**Mitigation approach**
1. RFC 7807 fixed fields: `type`, `title`, `status`, `detail`, `instance`.
2. `detail` is allowlisted — only known safe messages pass through.
3. Internal `request_id` correlation lives in response headers, not body.
4. Catch-all middleware: any uncaught exception → RFC 7807 `Internal Server Error` with empty detail.

**Verification**
- Integration test: trigger each known exception class → assert body schema
- Integration test: trigger unknown exception → assert body has NO stack/import path/SQL fragment
- `lint-imports` still green

**Owner:** `api.errors` owner
**Target date:** Gate 2 exit. Implementation landed in FR-10; ongoing verification.

---

## R2 — SQL injection (score 10)

**Category:** Security
**Linked modules:** all `taskq_api.repository.*` modules
**Linked FR/NFR:** NFR-02

**Risk description**
String concatenation in SQL execution paths opens injection vectors even though the ORM is in use.

**Mitigation approach**
1. ORM-only access (no `text()` with format/percent interpolation).
2. `grep` CI gate for `execute(.*%.*)` / `text(.*%.*)` / `f"SELECT"` / `+ .*SELECT` patterns → exit non-zero on hit.
3. `bandit -r 03-development/src/` → 0 HIGH, 0 MEDIUM.
4. All parameters bound via SQLAlchemy bind params (`bindparam`, `:name`).

**Verification**
- `grep -RE 'execute\(.*%|text\(.*%|f".*SELECT|\+ .*SELECT' 03-development/src/` → 0 hits
- `bandit -r 03-development/src/` → 0 HIGH, 0 MEDIUM
- `pytest -k sql_injection` → regression tests cover attempt vectors

**Owner:** `repository.*` owners collectively; gate owner = repo steward
**Target date:** Gate 2 — verify grep gate is wired into CI (not just a manual command).

---

## R4 — 403 洩漏資源存在性 (score 9)

**Category:** Information disclosure
**Linked module:** `taskq_api.service.auth`
**Linked FR/NFR:** FR-04 / SPEC §8 #6

**Risk description**
Returning 404 for "not found" vs 403 for "found but not yours" lets an attacker enumerate resource IDs. Conversely, returning 403 for both leaks existence.

**Mitigation approach**
1. Authorization decision happens BEFORE resource lookup. If unauthorized → 403 immediately (with a stable, non-distinguishing message).
2. If authorized but resource not found → 404 with the same generic message.
3. Timing: both paths should have comparable cost (no DB hit on the 403 path that would otherwise short-circuit).

**Verification**
- Integration test: unauthorized access to existing resource → 403 with generic message
- Integration test: unauthorized access to non-existent resource → 403 with same shape
- Authorized access to non-existent resource → 404 with same generic message
- No stack trace / internal class names in any of these bodies

**Owner:** `service.auth` owner
**Target date:** Gate 2 — verify with FR-04 test suite.

---

## R7 — `CancelledError` 被吞 → 關閉時卡死 (score 9)

**Category:** Concurrency / reliability
**Linked module:** `taskq_api.service.runner` (SPEC §10 high-risk module)
**Linked FR/NFR:** NFR-03

**Risk description**
If `asyncio.CancelledError` is caught by a bare `except Exception` (or worse, swallowed by `try/finally` that doesn't re-raise), the shutdown loop hangs because the task never receives the cancellation signal.

**Mitigation approach**
1. Style/lint rule: no `except Exception` around `await` on runner paths — re-raise `CancelledError` explicitly.
2. ast-error-handling scanner checks that `except` clauses that catch `Exception` inside async functions re-raise `CancelledError` (or use a narrower exception type).
3. Test: simulate cancellation → assert the runner task exits within a tight deadline.

**Verification**
- `pytest -k cancel_during_run` → green; assert runner exits ≤ 500 ms after cancel
- ast-error-handling scanner → 0 violations

**Owner:** `service.runner` owner
**Target date:** Gate 4 — final quality gate.

---

## R8 — 任務 timeout 留下孤兒進程 (score 9)

**Category:** Resource leak / reliability
**Linked module:** `taskq_api.service.runner`
**Linked FR/NFR:** FR-08 / SPEC §8 #25

**Risk description**
When a task exceeds its timeout, the parent coroutine must kill the spawned subprocess AND await its exit; otherwise the subprocess is orphaned.

**Mitigation approach**
1. On timeout: `proc.terminate()` (SIGTERM), wait with bounded timeout, then `proc.kill()` (SIGKILL) and `await proc.wait()`.
2. Integration test: spawn a long-running subprocess, trigger cancel, assert `ps` shows no leftover PID.
3. Final-state DB row marked `interrupted` (SPEC §8 #25).

**Verification**
- `pytest -k orphan_subprocess` → 0 leftover PIDs after cancel
- `pytest -k graceful_drain` → all in-flight rows reach `interrupted` state on shutdown

**Owner:** `service.runner` owner
**Target date:** Gate 4.

---

## R10 — 連線池耗盡 (score 9)

**Category:** Capacity / performance
**Linked module:** `taskq_api.repository.session`
**Linked FR/NFR:** FR-06 / FR-08

**Risk description**
Under load, the DB connection pool can be exhausted by requests holding transactions across await boundaries, causing cascading 500s.

**Mitigation approach**
1. `pool_pre_ping=True` on the engine.
2. Concurrency cap enforced at request admission (semaphore / middleware).
3. Connection checkout timeout shorter than request timeout.
4. Load test: N concurrent requests > pool size → all eventually succeed (via pre-ping + retry, not via silent 500).

**Verification**
- `pytest -k pool_exhaustion` → all requests eventually 2xx
- `pytest-benchmark` p95 stays within budget at the cap

**Owner:** `repository.session` owner
**Target date:** Gate 4 — after load harness is wired.

---

## R11 — Transitive 依賴引入不相容 license (score 9)

**Category:** License compliance
**Linked module:** dependency manifest
**Linked FR/NFR:** NFR-07

**Risk description**
A transitive dependency update introduces a license not in the allowlist (e.g. AGPL/SSPL/Commons Clause), which the project cannot ship.

**Mitigation approach**
1. `requirements.lock` is the source of truth (not just `requirements.txt`).
2. `pip-licenses --format=json --with-system` is wired into CI; fails the build on any non-allowlist license.
3. Allowlist is documented in SPEC §0 and version-pinned.
4. SBOM (`sbom.json`) regenerated on every dependency change.

**Verification**
- `pip-licenses --format=json --with-system` → every dep ∈ allowlist
- `sbom.json` is current
- CI gate is wired (not just a manual command)

**Owner:** release / dependency steward
**Target date:** Gate 2 — verify CI wiring; Gate 4 — verify SBOM currency.

---

## Cross-cutting owner / DRI matrix

| Module | DRI (per SPEC §10 high-risk-module list) | Risks |
|---|---|---|
| `taskq_api.service.runner` | runner maintainer | R5 (N+1 via tasks list), R7 (cancellation), R8 (orphan) |
| `taskq_api.service.auth` | auth maintainer | R3 (key leak), R4 (403 side-channel) |
| `taskq_api.repository.session` | session maintainer | R5 (N+1), R10 (pool) |
| `migrations/versions/v3_split_results.py` | migrations maintainer | R1 (data loss) |
| Deployment / release | DevOps lead | R9 (migration drift), R11 (license) |
| `taskq_api.api.errors` | errors maintainer | R6 (error body leakage) |
| `taskq_api.repository.*` (collective) | repo steward | R2 (injection) |