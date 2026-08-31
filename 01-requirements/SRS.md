# Software Requirements Specification (SRS) — taskq-api

> INGESTION MODE: 100% transcription of SPEC.md (v1.0.0, 2026-07-30) —
> canonical source of truth for round 2 of the harness-methodology
> progressive test-bed. All `### FR-NN` and `### NFR-NN` headings are
> verbatim transcriptions; no invention, no silent omission.

## 1. Introduction

### 1.1 Project name

`taskq-api` (canonical: SPEC.md §0, §1).

### 1.2 Purpose

HTTP task-queue service: submit, query, and execute shell-command tasks
over a REST API; persist to a relational database through SQLAlchemy;
evolve the schema with Alembic; authenticate with hashed API keys,
authorise by scope, and throttle per token (canonical: SPEC.md §1,
PROJECT_BRIEF.md "Project Domain").

### 1.3 Language and form

Python 3.11; ASGI service started with `uvicorn taskq_api.app:app`;
management entry point `python -m taskq_api` (migrate / seed /
healthcheck) (canonical: SPEC.md §1).

### 1.4 Test-bed context

Round 2 of 3 in the harness-methodology progressive validation
test-bed (round 1 = `taskq-plus` CLI; round 3 = TypeScript, deferred)
(canonical: PROJECT_BRIEF.md "Stakeholders").

## 2. Constraints

### 2.1 Technical constraints

- Python 3.11 (canonical: SPEC.md §2, PROJECT_BRIEF.md "Key
  Constraints — Technical").
- FastAPI ASGI app, launched as `uvicorn taskq_api.app:app`
  (canonical: SPEC.md §2).
- SQLAlchemy 2.x with explicit `Session` transaction boundaries
  (canonical: SPEC.md §2).
- Alembic for schema migrations (canonical: SPEC.md §2).
- `asyncio.create_subprocess_exec` for task execution;
  `shell=True` forbidden everywhere (canonical: SPEC.md §2, PROJECT_BRIEF.md
  "Key Constraints — Technical").

### 2.2 Architecture constraints

- Four layers `api > service > repository > models` enforced by a
  mandatory `.importlinter` contract (canonical: PROJECT_BRIEF.md "Key
  Constraints — Architecture", SPEC.md §2 "分層約束").
- `config` and `errors` are independence modules (canonical:
  PROJECT_BRIEF.md "Key Constraints — Architecture").
- `sqlalchemy` may only be imported by `repository/` — ORM leakage into
  the business layer is the specific anti-pattern this round guards
  against (NFR-06) (canonical: PROJECT_BRIEF.md "Key Constraints —
  Architecture").

### 2.3 Security constraints

- API keys stored as SHA-256 hashes and compared with
  `hmac.compare_digest` (canonical: PROJECT_BRIEF.md "Key Constraints —
  Security").
- 403 responses must not reveal whether the resource exists (canonical:
  PROJECT_BRIEF.md "Key Constraints — Security").
- No string-concatenated SQL anywhere (canonical: PROJECT_BRIEF.md
  "Key Constraints — Security").
- CORS denies all origins by default (canonical: PROJECT_BRIEF.md "Key
  Constraints — Security").
- Error bodies must not carry stack traces, SQL, or file paths (canonical:
  PROJECT_BRIEF.md "Key Constraints — Security").

### 2.4 Migration constraints

- Three revisions: v1 base tables, v2 tags many-to-many, v3 moves
  `tasks.result_json` into a `task_results` table with real data
  migration (canonical: PROJECT_BRIEF.md "Key Constraints —
  Migration").
- `upgrade head` → sample write → `downgrade -1` → `upgrade head` must
  leave every column byte-identical (canonical: PROJECT_BRIEF.md "Key
  Constraints — Migration", SPEC.md FR-07 "往返可逆性驗收").

### 2.5 Async correctness constraints

- `asyncio.CancelledError` must propagate — it must never be swallowed
  by `except Exception` (canonical: PROJECT_BRIEF.md "Key Constraints —
  Async correctness").
- Task timeouts must actually kill the child process (`kill()` then
  `await wait()`), leaving no orphans (canonical: PROJECT_BRIEF.md "Key
  Constraints — Async correctness").
- Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` (canonical:
  PROJECT_BRIEF.md "Key Constraints — Async correctness").

### 2.6 Query efficiency constraints

- Relationship loads must be explicit (`selectinload` / `joinedload`)
  (canonical: PROJECT_BRIEF.md "Key Constraints — Query efficiency").
- N+1 is an acceptance failure — the list endpoint's SQL statement
  count must be constant regardless of how many rows come back
  (canonical: PROJECT_BRIEF.md "Key Constraints — Query efficiency",
  NFR-01).

### 2.7 Readiness constraints

- `/readyz` returns 503 when the database is unreachable **or** when
  `alembic current` is not at head — deploying new code without
  running the migration must fail closed (canonical: PROJECT_BRIEF.md
  "Key Constraints — Readiness", FR-09).

### 2.8 Verification-honesty constraints

- Same zero-skip rule as round 1; the three-step migration must be
  tested against a **real database file**, not a mock, and may not be
  downgraded to a skip on the grounds that "migration logic is hard to
  test" (canonical: PROJECT_BRIEF.md "Key Constraints — Verification
  honesty", NFR-09).

## 3. Functional Requirements

### FR-01: 任務資源 CRUD API

> DERIVED: SPEC.md §3 FR-01 — AC sub-numbers (AC-1.1..AC-1.7) and test
> function / file identifiers chosen to bind to SPEC.md §8 verification
> commands and the round-1 FR-01 validation rule set.

Canonical: SPEC.md §3 FR-01, PROJECT_BRIEF.md FR Inventory row
FR-01, HTTP Status Map SPEC.md §7 rows 422/404/409.

The task-resource CRUD API exposes four endpoints:

| Method | Path | scope | Behaviour |
|--------|------|-------|-----------|
| `POST` | `/v1/tasks` | `write` | create task; body validated by `TaskCreate` pydantic model |
| `GET` | `/v1/tasks/{id}` | `read` | retrieve single task (all columns) |
| `GET` | `/v1/tasks` | `read` | paginated list, supports `?status=`, `?limit=`, `?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | delete task (with results row, same transaction) |

Validation rules identical to round-1 FR-01 (non-empty /
≤ 1000 characters / injection-character blacklist / name unique);
violation → **HTTP 422** + problem+json. Unknown id →
**HTTP 404** + problem+json. Pagination is **cursor-based** (offset
forbidden — large-table offset scan is an N+1 cousin). Default `limit`
on the list endpoint is 50, cap 200; over the cap → 422.

**Acceptance criteria**

- **AC-1.1** `POST /v1/tasks` with a valid body and a `write` API key
  returns 201 and a task id — decided by `tests/integration/test_tasks_crud.py::test_post_task_returns_201_with_id`, per SPEC.md §3 FR-01 row 1.
- **AC-1.2** `POST /v1/tasks` with a body violating validation rules
  returns **422** + problem+json — decided by `tests/integration/test_tasks_crud.py::test_post_task_validation_violations_returns_422`, per SPEC.md §3 FR-01 "驗證規則" paragraph.
- **AC-1.3** `GET /v1/tasks/{unknown_id}` returns **404** + problem+json
  — decided by `tests/integration/test_tasks_crud.py::test_get_task_unknown_id_returns_404`, per SPEC.md §3 FR-01 "未知 id" paragraph.
- **AC-1.4** `POST /v1/tasks` with a duplicate `name` returns **409** +
  problem+json — decided by `tests/integration/test_tasks_crud.py::test_post_task_duplicate_name_returns_409`, per SPEC.md §3 FR-01 "名稱唯一" clause.
- **AC-1.5** `GET /v1/tasks?limit=` exceeding 200 returns **422** —
  decided by `tests/integration/test_tasks_crud.py::test_list_task_limit_above_200_returns_422`, per SPEC.md §3 FR-01 "預設 limit 為 50, 上限 200" clause.
- **AC-1.6** `GET /v1/tasks` pagination is cursor-based (no `offset`
  query parameter is accepted) — decided by `tests/integration/test_tasks_crud.py::test_list_pagination_is_cursor_based`, per SPEC.md §3 FR-01 "分頁為 cursor-based" clause.
- **AC-1.7** `DELETE /v1/tasks/{id}` removes the task and its
  `task_results` row in one transaction (no orphaned results) — decided by `tests/integration/test_tasks_crud.py::test_delete_task_removes_results_in_same_transaction`, per SPEC.md §3 FR-01 row 4 "連同結果列, 同一交易" clause.

### FR-02: 任務執行端點

> DERIVED: SPEC.md §3 FR-02 — AC sub-items, test function names, and
> the `tests/integration/test_task_run.py` location chosen to bind to
> SPEC.md §8 verification table + §3 FR-08 timeout interaction.

Canonical: SPEC.md §3 FR-02.

`POST /v1/tasks/{id}/run` (scope `write`) → **HTTP 202 Accepted**, body
contains `run_id`. Execution runs through
`asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True`
forbidden, timeout = `TASKQ_TASK_TIMEOUT`. State machine:
`pending → running → done | failed | timeout`. Execution result written
into the `task_results` table (FR-07's v3 schema), columns:
`exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` /
`finished_at`. `GET /v1/tasks/{id}/runs` (scope `read`) → the task's
historical runs, newest first.

**Acceptance criteria**

- **AC-2.1** `POST /v1/tasks/{id}/run` returns **202** with a `run_id` in
  the body — decided by `tests/integration/test_task_run.py::test_post_run_returns_202_with_run_id`, per SPEC.md §3 FR-02 paragraph 1.
- **AC-2.2** Execution runs through `asyncio.create_subprocess_exec` and
  `shell=True` is absent from the entire `03-development/src/` tree
  (grep 0 hits) — decided by `tests/unit/test_runner_subprocess.py::test_shell_true_absent_from_src_tree`, per SPEC.md §3 FR-02 paragraph 1 "禁 `shell=True`" clause.
- **AC-2.3** A task's state transitions `pending → running → done |
  failed | timeout` per outcome — decided by `tests/integration/test_task_run.py::test_state_machine_transitions`, per SPEC.md §3 FR-02 "狀態機" clause.
- **AC-2.4** A successful run writes a row into `task_results` with
  `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, and
  `finished_at` populated — decided by `tests/integration/test_task_run.py::test_run_writes_task_results_row`, per SPEC.md §3 FR-02 "執行結果寫入" clause.
- **AC-2.5** A task that exceeds `TASKQ_TASK_TIMEOUT` is terminated by
  `process.kill()` followed by `await process.wait()`; no orphan child
  process remains — decided by `tests/integration/test_task_run.py::test_timeout_kills_child_no_orphan`, per SPEC.md §3 FR-08 paragraph 3 + FR-02 timeout interaction.
- **AC-2.6** `GET /v1/tasks/{id}/runs` returns the run history newest
  first — decided by `tests/integration/test_task_run.py::test_get_runs_returns_history_newest_first`, per SPEC.md §3 FR-02 paragraph 4.

### FR-03: API Key 認證

> DERIVED: SPEC.md §3 FR-03 — AC sub-items and test identifiers
> (table-no-plaintext, hmac-compare-digest assertion, revocation path)
> chosen to bind to SPEC.md §8 #18 verification and §4 NFR-02.

Canonical: SPEC.md §3 FR-03.

All `/v1/*` endpoints require the `X-API-Key` header; missing or
invalid → **HTTP 401** + problem+json. Keys are stored **hashed with
SHA-256** in `api_keys`; plaintext is forbidden. Comparison uses
`hmac.compare_digest` (constant time). Keys are produced by
`python -m taskq_api key create --scope <scope>`; plaintext is printed
**only once** at creation. Revoking a key: any key with `revoked_at`
set is treated as invalid. `/healthz` and `/readyz` do not require
authentication (FR-09).

**Acceptance criteria**

- **AC-3.1** Request to any `/v1/*` endpoint without `X-API-Key` returns
  **401** + problem+json — decided by `tests/integration/test_auth.py::test_missing_api_key_returns_401`, per SPEC.md §3 FR-03 paragraph 1.
- **AC-3.2** Request with an invalid `X-API-Key` returns **401** +
  problem+json — decided by `tests/integration/test_auth.py::test_invalid_api_key_returns_401`, per SPEC.md §3 FR-03 paragraph 1.
- **AC-3.3** `api_keys` rows store `key_hash` as a 64-hex SHA-256
  digest; no plaintext key exists in the table — decided by `tests/integration/test_auth.py::test_api_keys_table_holds_no_plaintext`, per SPEC.md §3 FR-03 paragraph 1 + SPEC.md §8 #18.
- **AC-3.4** Key comparison uses `hmac.compare_digest` (constant time)
  — decided by `tests/unit/test_auth_compare.py::test_key_compare_uses_hmac_compare_digest`, per SPEC.md §3 FR-03 paragraph 1.
- **AC-3.5** A key with `revoked_at` set is treated as invalid —
  decided by `tests/integration/test_auth.py::test_revoked_key_treated_as_invalid`, per SPEC.md §3 FR-03 paragraph 1 "停用金鑰" clause.
- **AC-3.6** `python -m taskq_api key create --scope <scope>` prints
  plaintext exactly once and persists only the hash — decided by `tests/unit/test_key_create.py::test_key_create_prints_plaintext_once_persists_hash`, per SPEC.md §3 FR-03 paragraph 1 + PROJECT_BRIEF.md NFR-04.
- **AC-3.7** `/healthz` and `/readyz` are reachable without
  authentication — decided by `tests/integration/test_health.py::test_health_endpoints_no_auth_required`, per SPEC.md §3 FR-03 paragraph 1 + FR-09.

### FR-04: Scope 授權

> DERIVED: SPEC.md §3 FR-04 — AC sub-items and the
> "single-dependency" assertion (AC-4.4) chosen to bind to SPEC.md §3
> FR-04 paragraph 1 and §8 #6 leak-guard verification.

Canonical: SPEC.md §3 FR-04.

Each key carries a scope: `read` < `write` < `admin` (hierarchical
inclusion). Required scopes per endpoint are listed in the FR-01 / FR-02
tables; insufficient scope → **HTTP 403** + problem+json, and **the
body must not reveal whether the resource exists**. Authorisation must
be performed by **a single dependency (middleware-style)**, not
scattered across handlers — enforced by an assertion that "every
`/v1` route passes through the same dependency".

**Acceptance criteria**

- **AC-4.1** A `read` key calling `POST /v1/tasks` returns **403** +
  problem+json — decided by `tests/integration/test_authz.py::test_read_scope_post_tasks_returns_403`, per SPEC.md §3 FR-04 paragraph 1 + FR-01 row 1.
- **AC-4.2** A `write` key calling `DELETE /v1/tasks/{id}` returns
  **403**; the response body does not disclose whether the id exists
  — decided by `tests/integration/test_authz.py::test_write_scope_delete_returns_403_no_resource_leak`, per SPEC.md §8 #6.
- **AC-4.3** An `admin` key calling `DELETE /v1/tasks/{id}` succeeds —
  decided by `tests/integration/test_authz.py::test_admin_scope_delete_succeeds`, per SPEC.md §3 FR-01 row 4 scope `admin`.
- **AC-4.4** Every `/v1/*` route shares exactly one FastAPI dependency
  for the authn/authz decision — decided by `tests/unit/test_single_authz_dependency.py::test_single_authz_dependency_used_by_every_v1_route`, per SPEC.md §3 FR-04 paragraph 1 "授權判定必須在單一中介層" clause.
- **AC-4.5** Scope precedence (`read` < `write` < `admin`, hierarchical
  inclusion) is enforced: an `admin` key satisfies a `write` requirement
  — decided by `tests/unit/test_authz.py::test_scope_hierarchy_admin_satisfies_write`, per SPEC.md §3 FR-04 paragraph 1.

### FR-05: 流量控制

> DERIVED: SPEC.md §3 FR-05 — AC sub-items (cross-worker sharing,
> row-level lock assertion, /healthz exemption) chosen to bind to §8
> #9 burst test and §9 R12 race-condition mitigation.

Canonical: SPEC.md §3 FR-05.

Per-token token bucket: capacity `TASKQ_RATE_BURST`, refill rate
`TASKQ_RATE_PER_SEC`. Over limit → **HTTP 429** + problem+json +
`Retry-After` header (seconds). Token-bucket state is stored in the
database (consistent across workers); updates must occur within a
single transaction using a row-level lock. `/healthz` and `/readyz`
are not rate-limited.

**Acceptance criteria**

- **AC-5.1** Requests exceeding `TASKQ_RATE_BURST` within the bucket's
  refill window return **429** + problem+json + `Retry-After` header —
  decided by `tests/integration/test_ratelimit.py::test_burst_over_limit_returns_429_with_retry_after`, per SPEC.md §3 FR-05 paragraph 1 + SPEC.md §8 #9.
- **AC-5.2** After the bucket refills at `TASKQ_RATE_PER_SEC`, requests
  succeed again — decided by `tests/integration/test_ratelimit.py::test_rate_limit_recovers_after_refill`, per SPEC.md §3 FR-05 paragraph 1.
- **AC-5.3** Bucket state is shared across workers (database-backed,
  not in-process) — decided by `tests/integration/test_ratelimit.py::test_bucket_state_shared_across_workers`, per SPEC.md §3 FR-05 paragraph 1 "存於資料庫" clause.
- **AC-5.4** Bucket update uses a single transaction with row-level
  lock (no race over-admit) — decided by `tests/unit/test_ratelimit.py::test_bucket_update_uses_row_level_lock`, per SPEC.md §3 FR-05 paragraph 1 + §9 R12.
- **AC-5.5** `/healthz` and `/readyz` are exempt from rate limiting —
  decided by `tests/integration/test_health.py::test_health_endpoints_exempt_from_rate_limit`, per SPEC.md §3 FR-05 paragraph 1 + FR-09.

### FR-06: 持久化層與交易邊界

> DERIVED: SPEC.md §3 FR-06 — AC sub-items (one-session-per-request,
> pool-pre-ping config, N+1 guard, no-string-SQL) chosen to bind to §8
> #14/#17/#21 verification and §4 NFR-01/NFR-02/NFR-06.

Canonical: SPEC.md §3 FR-06.

All data access goes through the `repository/` layer; the business
layer must not hold a `Session` directly. One `Session` per request;
transaction boundary explicit: success commits, exceptions roll back
(guaranteed by a context manager). String-concatenated SQL is
forbidden; use ORM or parameterised queries (NFR-02). Relationship
queries must use explicit `selectinload` / `joinedload` — **N+1 is an
acceptance failure** (NFR-01). Connection pool: `pool_size =
TASKQ_DB_POOL_SIZE`, `pool_pre_ping = True`.

**Acceptance criteria**

- **AC-6.1** The `service/` and `api/` layers contain zero `sqlalchemy`
  imports — decided by `lint-imports` exit code, per SPEC.md §3 FR-06 paragraph 1 + NFR-06 + SPEC.md §8 #21.
- **AC-6.2** Every API request uses exactly one `Session`, with the
  transaction closed via a context manager (commit on success, rollback
  on exception) — decided by `tests/unit/test_session.py::test_one_session_per_request_context_manager`, per SPEC.md §3 FR-06 paragraph 1.
- **AC-6.3** String-concatenated SQL is absent from `03-development/src/`
  (0 grep hits for f-string / `%` / `+` built SQL) — decided by `tests/unit/test_no_string_sql.py::test_no_string_concatenated_sql_in_src`, per SPEC.md §3 FR-06 paragraph 1 + §8 #17 + NFR-02.
- **AC-6.4** The list endpoint's SQL statement count is constant
  regardless of returned row count (N+1 protected) — decided by `tests/performance/test_list_no_n_plus_one.py::test_list_endpoint_sql_count_is_constant`, per SPEC.md §3 FR-06 paragraph 1 + NFR-01 + §8 #14.
- **AC-6.5** The connection pool uses `pool_size = TASKQ_DB_POOL_SIZE`
  and `pool_pre_ping = True` — decided by `tests/unit/test_engine.py::test_engine_pool_config_matches_env`, per SPEC.md §3 FR-06 paragraph 1 + §5.1.

### FR-07: Schema Migration (Alembic 三步演進)

> DERIVED: SPEC.md §3 FR-07 — AC sub-items (no-destructive-drop guard,
> per-revision downgrade test, v2 unique-index survival, real-file
> round-trip) chosen to bind to §8 #12/#13 verification and §4 NFR-09
> anti-skip clause.

Canonical: SPEC.md §3 FR-07.

Three revisions, each with a working `downgrade`:

| revision | upgrade | downgrade |
|----------|---------|-----------|
| **v1** | create `tasks` and `api_keys` tables | drop both tables |
| **v2** | add `tags`, `task_tags` (many-to-many) + unique index on `tasks.name` | drop new tables and index, leave v1 data intact |
| **v3** | **data-moving**: split `tasks.result_json` into a separate `task_results` table, migrate existing data, then drop the original column | reverse-migrate back into `tasks.result_json`, drop `task_results` — **no data loss** |

`alembic upgrade head` and `alembic downgrade base` must both succeed.
**Round-trip reversibility acceptance**: `upgrade head` → write sample
data → `downgrade -1` → `upgrade head`; every column of the sample data
must be byte-identical (v3's data migration is the focus). Destructive
shortcuts like `op.execute("DROP TABLE ...")` are forbidden as a
substitute for a real `downgrade`. Migration files themselves are
covered by tests (offline SQL generation + assertions).

**Acceptance criteria**

- **AC-7.1** `alembic upgrade head` and `alembic downgrade base` both
  exit 0 against a real SQLite database file — decided by `tests/integration/test_migration_round_trip.py::test_upgrade_downgrade_base_clean`, per SPEC.md §3 FR-07 paragraph 1 + §8 #13.
- **AC-7.2** `upgrade head` → write sample → `downgrade -1` → `upgrade
  head` leaves every column of the sample byte-identical (v3 data
  migration is reversible) — decided by `tests/integration/test_migration_round_trip.py::test_v3_data_migration_round_trip_byte_identical`, per SPEC.md §3 FR-07 "往返可逆性驗收" clause + §8 #12.
- **AC-7.3** No migration uses `op.execute("DROP TABLE ...")` as a
  substitute for a real `downgrade` (forensic grep) — decided by `tests/unit/test_migrations.py::test_no_destructive_drop_table_shortcuts`, per SPEC.md §3 FR-07 "禁止以 op.execute" clause.
- **AC-7.4** Each migration's `downgrade()` is exercised by a test that
  runs the full cycle against a real SQLite file — decided by `tests/integration/test_migration_round_trip.py::test_every_revision_downgrade_works`, per SPEC.md §3 FR-07 "每一步都必須有可運作的 downgrade" clause + NFR-09 anti-skip clause.
- **AC-7.5** v2's `tasks.name` unique index survives the full
  round-trip without loss — decided by `tests/integration/test_migration_round_trip.py::test_v2_unique_index_survives_round_trip`, per SPEC.md §3 FR-07 v2 row.

### FR-08: 非同步執行器

> DERIVED: SPEC.md §3 FR-08 — AC sub-items (TaskGroup assertion,
> drain timeout, cancel-propagation, kill-then-wait) chosen to bind to
> §8 #25 graceful-drain verification and §4 NFR-03.

Canonical: SPEC.md §3 FR-08.

Background execution managed by `asyncio.TaskGroup`; on shutdown must
perform a **graceful drain** (wait for in-flight tasks up to
`TASKQ_DRAIN_TIMEOUT`; those exceeding the budget are marked
`interrupted`). Concurrency cap `TASKQ_MAX_CONCURRENT`; over-cap new
tasks queue rather than spawning unlimited coroutines. Task timeout
implemented with `asyncio.wait_for`; timeout must **actually terminate
the child process** (`process.kill()` then `await process.wait()`),
leaving no orphans. Cancellation semantics:
`asyncio.CancelledError` must propagate upward; it must never be
swallowed by `except Exception` (NFR-03).

**Acceptance criteria**

- **AC-8.1** Background runner uses `asyncio.TaskGroup` for management
  — decided by `tests/unit/test_runner.py::test_runner_uses_task_group`, per SPEC.md §3 FR-08 paragraph 1.
- **AC-8.2** Concurrency is capped at `TASKQ_MAX_CONCURRENT`; excess
  tasks queue rather than spawning unlimited coroutines — decided by `tests/performance/test_runner_concurrency_cap.py::test_concurrency_capped_at_max_concurrent`, per SPEC.md §3 FR-08 paragraph 1.
- **AC-8.3** A timed-out task is killed via `process.kill()` followed by
  `await process.wait()`; no orphan child process remains — decided by `tests/integration/test_task_run.py::test_timeout_kills_child_no_orphan`, per SPEC.md §3 FR-08 paragraph 1 + §8 #25.
- **AC-8.4** On shutdown the runner drains in-flight tasks up to
  `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked
  `interrupted` — decided by `tests/integration/test_shutdown_drain.py::test_shutdown_drains_inflight_within_budget`, per SPEC.md §3 FR-08 paragraph 1 + §8 #25.
- **AC-8.5** `asyncio.CancelledError` propagates upward; it is never
  swallowed by `except Exception` — decided by `tests/unit/test_runner_cancellation.py::test_cancelled_error_propagates_not_swallowed`, per SPEC.md §3 FR-08 paragraph 1 + NFR-03.

### FR-09: 健康檢查與可觀測性

> DERIVED: SPEC.md §3 FR-09 — AC sub-items (DB-down vs migration-behind
> split, /metrics series list) chosen to bind to §8 #10/#11 verification
> and §4 NFR-04.

Canonical: SPEC.md §3 FR-09.

| Endpoint | Auth | Behaviour |
|----------|------|-----------|
| `GET /healthz` | none | process alive → 200 `{"status":"ok"}` |
| `GET /readyz` | none | DB reachable **and** `alembic current` == head → 200; otherwise **503** with body explaining which condition failed |
| `GET /v1/metrics` | `admin` | task counts (by status), execution latency percentiles, rate-limit reject counts |

`/readyz`'s "migration behind head" judgement is critical: deploying
new code without running the migration must **fail closed**.

**Acceptance criteria**

- **AC-9.1** `GET /healthz` returns **200** `{"status":"ok"}` while the
  process is alive — decided by `tests/integration/test_health.py::test_healthz_returns_200_when_alive`, per SPEC.md §3 FR-09 row 1.
- **AC-9.2** `GET /readyz` returns **503** when the database is
  unreachable, with body detail naming the failure — decided by `tests/integration/test_health.py::test_readyz_returns_503_when_db_down`, per SPEC.md §3 FR-09 row 2 + §8 #10.
- **AC-9.3** `GET /readyz` returns **503** when `alembic current` is not
  at head, with body detail naming the failure — decided by `tests/integration/test_health.py::test_readyz_returns_503_when_migration_behind_head`, per SPEC.md §3 FR-09 row 2 + §8 #11.
- **AC-9.4** `GET /readyz` returns **200** when DB is reachable and
  migration is at head — decided by `tests/integration/test_health.py::test_readyz_returns_200_when_healthy`, per SPEC.md §3 FR-09 row 2.
- **AC-9.5** `GET /v1/metrics` returns task counts by status, execution
  latency percentiles, and rate-limit reject counts — decided by `tests/integration/test_metrics.py::test_metrics_returns_required_series`, per SPEC.md §3 FR-09 row 3.

### FR-10: 錯誤契約 (RFC 7807)

> DERIVED: SPEC.md §3 FR-10 — AC sub-items (Content-Type, six-field
> shape, no-internals-on-500, correlation-id linkage, every-code
> coverage) chosen to bind to §8 #5/#6/#7/#8/#9/#10/#11/#19
> verification and §4 NFR-10.

Canonical: SPEC.md §3 FR-10.

All non-2xx responses have `Content-Type: application/problem+json`.
Body fields: `type` (URI), `title`, `status`, `detail`, `instance`,
`correlation_id`. **`detail` must not leak internal detail**: no SQL
statements, no stack traces, no file paths, no schema description.
`correlation_id` appears both in the `X-Correlation-Id` response
header and in server logs, linkable. Error-code mapping: 422
validation / 401 unauthenticated / 403 insufficient scope / 404
unknown resource / 409 name conflict / 429 over limit / 503 not ready /
500 other.

**Acceptance criteria**

- **AC-10.1** Every non-2xx response carries
  `Content-Type: application/problem+json` — decided by `tests/integration/test_error_contract.py::test_non_2xx_content_type_is_problem_json`, per SPEC.md §3 FR-10 paragraph 1.
- **AC-10.2** Problem+json bodies contain exactly `type`, `title`,
  `status`, `detail`, `instance`, `correlation_id` — decided by `tests/integration/test_error_contract.py::test_problem_json_fields`, per SPEC.md §3 FR-10 paragraph 1.
- **AC-10.3** `detail` does not contain SQL statements, stack traces,
  file paths, or schema descriptions (verified on a 500 response) —
  decided by `tests/integration/test_error_contract.py::test_500_detail_no_internals`, per SPEC.md §3 FR-10 "detail 不得洩漏內部細節" clause + §8 #19 + NFR-02.
- **AC-10.4** Every response carries `X-Correlation-Id`, and the same
  id appears in server logs — decided by `tests/integration/test_error_contract.py::test_correlation_id_in_header_and_logs`, per SPEC.md §3 FR-10 paragraph 1.
- **AC-10.5** Each error-code mapping (422/401/403/404/409/429/503/500)
  is exercised by at least one integration test — decided by `tests/integration/test_error_contract.py::test_each_error_code_exercised`, per SPEC.md §3 FR-10 paragraph 1 + §8 #5/#6/#7/#8/#9/#10/#11 + NFR-10.

## 4. Non-Functional Requirements

> **Dimension map (every `type:` below is one of `documentation |
> integration | layering | licensing | maintainability | mutation |
> performance | reliability | security | testability | verifiability |
> deployability | scalability | usability`)**. `error_handling` is a
> valid `dimension:` per `sab_parser` but is never `type:`; the
> `type:` vocabulary is pinned by
> `tests/test_sab_parser.py::TestCanonicalTemplate::test_srs_template_nfr_type_example_matches_vocabulary`.

### NFR-01: 效能與查詢效率

> DERIVED: SPEC.md §4 NFR-01 — AC sub-items, test file paths under
> `tests/performance/`, and `pytest-benchmark` framing chosen to bind
> to §8 #14/#15 verification and §11 row 1/2/3 thresholds.

- **dimension**: `performance`
- **type**: `performance`
- Canonical: SPEC.md §4 NFR-01; SPEC.md §8 #14/#15; SPEC.md §11 row 1/2/3.
- `GET /v1/tasks/{id}` at 10,000 rows has **p95 < 30ms** (network
  excluded, measured over ASGI transport).
- `GET /v1/tasks?limit=50` at 10,000 rows has **p95 < 80ms**.
- **N+1 is a failure condition**: the list endpoint's SQL statement
  count per request must be **constant** (independent of returned row
  count), asserted via a SQLAlchemy event listener counter.

**Acceptance criteria**

- **AC-N1.1** `GET /v1/tasks/{id}` at 10,000 rows has p95 < 30ms —
  decided by `tests/performance/test_get_by_id_latency.py::test_get_by_id_p95_under_30ms_at_10k`, per SPEC.md §4 NFR-01 clause 1.
- **AC-N1.2** `GET /v1/tasks?limit=50` at 10,000 rows has p95 < 80ms —
  decided by `tests/performance/test_list_latency.py::test_list_p95_under_80ms_at_10k`, per SPEC.md §4 NFR-01 clause 2.
- **AC-N1.3** List endpoint SQL statement count is constant
  regardless of row count — decided by `tests/performance/test_list_no_n_plus_one.py::test_list_endpoint_sql_count_is_constant`, per SPEC.md §4 NFR-01 "N+1 為失敗條件" clause.

### NFR-02: HTTP 與資料層安全

> DERIVED: SPEC.md §4 NFR-02 — AC sub-items (no-shell-eval-exec,
> no-string-SQL, hashed-key, bandit-zero) chosen to bind to §8 #16/#17/
> #18/#23 verification and §11 rows 12/14.

- **dimension**: `security`
- **type**: `security`
- Canonical: SPEC.md §4 NFR-02; SPEC.md §8 #16/#17/#18/#19/#21/#23;
  SPEC.md §11 rows 12/14.
- `shell=True`, `eval(`, `exec(` forbidden across the codebase (grep 0
  hits).
- **String-concatenated SQL forbidden** (no f-string / `%` / `+`-built
  SQL; ORM or parameterised only), double-checked by grep + code
  review.
- API keys **hashed**, compared with `hmac.compare_digest` (FR-03).
- 403 responses do not reveal resource existence (FR-04).
- Error bodies do not contain stack / SQL / path (FR-10).
- CORS **denies all origins** by default; allowlist via
  `TASKQ_CORS_ORIGINS`.
- `bandit -r 03-development/src/`: **0 HIGH, 0 MEDIUM**.

**Acceptance criteria**

- **AC-N2.1** `grep -rn "shell=True\|eval(\|exec(" 03-development/src/`
  returns 0 hits — decided by `tests/unit/test_security_scan.py::test_no_shell_eval_exec_in_src`, per SPEC.md §4 NFR-02 clause 1 + §8 #16.
- **AC-N2.2** No string-concatenated SQL exists in `03-development/src/`
  (f-string / `%` / `+` built SQL = 0 hits) — decided by `tests/unit/test_security_scan.py::test_no_string_concatenated_sql_in_src`, per SPEC.md §4 NFR-02 clause 1 + §8 #17.
- **AC-N2.3** API keys are hashed with SHA-256 and compared with
  `hmac.compare_digest` — decided by `tests/integration/test_auth.py::test_api_keys_table_holds_no_plaintext`, per SPEC.md §4 NFR-02 clause 1 + FR-03.
- **AC-N2.4** `bandit -r 03-development/src/` reports 0 HIGH, 0 MEDIUM
  — decided by `tests/unit/test_bandit.py::test_bandit_zero_high_zero_medium`, per SPEC.md §4 NFR-02 clause 1 + §8 #23.

### NFR-03: 錯誤處理、交易與非同步正確性

> DERIVED: SPEC.md §4 NFR-03 — AC sub-items (no-bare-except, cancel
> propagation, DB-down→503, migration rollback) chosen to bind to §9
> R7/R8 risk rows and §11 row 5. `type:` mapped from
> `error_handling` to `reliability` per `sab_parser` vocabulary; the
> `dimension:` field still carries `error_handling`.

- **dimension**: `error_handling`
- **type**: `reliability`
- Canonical: SPEC.md §4 NFR-03; SPEC.md §9 R7/R8; SPEC.md §11 row 5.
- Each request has an explicit transaction boundary: success commits,
  exceptions roll back, guaranteed by a context manager (FR-06).
- Bare `except:` / `except Exception: pass` **forbidden**.
- **`asyncio.CancelledError` must not be swallowed** — must re-raise
  (the async-specific swallowing trap).
- DB connection failure → `/readyz` 503 with explicit detail; no
  infinite silent retry.
- Task timeout must terminate the child process; no orphans (FR-08).
- Migration failure → transaction rollback; DB remains at the previous
  revision (FR-07).

**Acceptance criteria**

- **AC-N3.1** No bare `except:` or `except Exception: pass` exists in
  `03-development/src/` — decided by `tests/unit/test_error_handling.py::test_no_bare_except_in_src`, per SPEC.md §4 NFR-03 clause 1.
- **AC-N3.2** `asyncio.CancelledError` propagates; never swallowed by
  `except Exception` — decided by `tests/unit/test_runner_cancellation.py::test_cancelled_error_propagates_not_swallowed`, per SPEC.md §4 NFR-03 clause 1 + FR-08.
- **AC-N3.3** DB-down → `/readyz` returns 503 with explicit detail —
  decided by `tests/integration/test_health.py::test_readyz_returns_503_when_db_down`, per SPEC.md §4 NFR-03 clause 1 + FR-09.
- **AC-N3.4** A failed migration leaves the database at the previous
  revision (rollback) — decided by `tests/integration/test_migration_round_trip.py::test_migration_failure_rolls_back`, per SPEC.md §4 NFR-03 clause 1 + FR-07.

### NFR-04: 敏感資料遮蔽

> DERIVED: SPEC.md §4 NFR-04 — AC sub-items (regex-driven redaction,
> DB-URL-password absence, plaintext-printed-once) chosen to bind to
> §8 #20 verification and §11 row 15.

- **dimension**: `security`
- **type**: `security`
- Canonical: SPEC.md §4 NFR-04; SPEC.md §8 #20; SPEC.md §11 row 15.
- `stdout_tail` / `stderr_tail` / logs / error bodies — before write or
  emit, lines matching
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
  have the entire line replaced with `[REDACTED]`.
- **Database connection string** (including password) must not appear
  in any log, error message, or `/v1/metrics` response.
- API-key plaintext is output only once at `key create`; never written
  to any persistent location.

**Acceptance criteria**

- **AC-N4.1** Lines matching the redaction regex in `stdout_tail` /
  `stderr_tail` / logs / error bodies are replaced with `[REDACTED]` —
  decided by `tests/unit/test_redaction.py::test_redaction_replaces_matching_lines`, per SPEC.md §4 NFR-04 clause 1.
- **AC-N4.2** No log, error message, or `/v1/metrics` response contains
  the password segment of `TASKQ_DB_URL` — decided by `tests/unit/test_redaction.py::test_db_url_password_never_logged_or_emitted`, per SPEC.md §4 NFR-04 clause 1 + §8 #20.
- **AC-N4.3** API-key plaintext appears in no persistent artefact after
  `key create` completes — decided by `tests/unit/test_key_create.py::test_key_create_prints_plaintext_once_persists_hash`, per SPEC.md §4 NFR-04 clause 1 + FR-03.

### NFR-05: 文件覆蓋

> DERIVED: SPEC.md §4 NFR-05 — AC sub-items (public-docstring coverage
> 100%, every-endpoint summary+description) chosen to bind to §11
> documentation dimension and FR-01/02/09/10 endpoints.

- **dimension**: `documentation`
- **type**: `documentation`
- Canonical: SPEC.md §4 NFR-05.
- Every public function/class has a docstring containing a `[FR-XX]`
  or `[NFR-XX]` reference; coverage **100%**.
- Every API endpoint has `summary` and `description` in the OpenAPI
  schema (asserted on the `/openapi.json` that FastAPI auto-generates).

**Acceptance criteria**

- **AC-N5.1** Public functions/classes have docstrings referencing
  `[FR-XX]` or `[NFR-XX]` at 100% coverage — decided by `tests/unit/test_docstrings.py::test_public_symbols_have_fr_or_nfr_docstring`, per SPEC.md §4 NFR-05 clause 1.
- **AC-N5.2** Every API endpoint has `summary` and `description` in
  `/openapi.json` — decided by `tests/integration/test_openapi.py::test_every_endpoint_has_summary_and_description`, per SPEC.md §4 NFR-05 clause 1.

### NFR-06: 架構分層契約

> DERIVED: SPEC.md §4 NFR-06 — AC sub-items (lint-imports exit 0,
> sqlalchemy-forbidden-outside-repository, .importlinter file present)
> chosen to bind to §8 #21 verification and §11 rows 9/10.

- **dimension**: `architecture_constraints`
- **type**: `layering`
- Canonical: SPEC.md §4 NFR-06; SPEC.md §8 #21; SPEC.md §11 row 9/10.
- Project root must contain `.importlinter` declaring a layers contract:
  `api > service > repository > models`. Upper layers may import lower
  layers; lower layers must not import upper layers; `config` and
  `errors` are independence modules.
- **Forbidden contract (additional)**: any layer other than
  `repository` must not import `sqlalchemy` — ORM leakage into the
  business layer is the specific anti-pattern this round guards
  against.
- `lint-imports` must **exit 0**.
- Passing by deleting `.importlinter`, using wildcard `ignore_imports`,
  or downgrading the contract is forbidden.

**Acceptance criteria**

- **AC-N6.1** `lint-imports` exits 0 against the project's
  `.importlinter` — decided by `tests/unit/test_lint_imports.py::test_lint_imports_exit_zero`, per SPEC.md §4 NFR-06 clause 1 + §8 #21.
- **AC-N6.2** Importing `sqlalchemy` from `service/` or `api/` is
  blocked by the forbidden contract — decided by `tests/unit/test_lint_imports.py::test_sqlalchemy_forbidden_outside_repository`, per SPEC.md §4 NFR-06 "額外禁令" clause.
- **AC-N6.3** `.importlinter` exists in the repo root and contains
  both the layers contract and the forbidden contract — decided by `tests/unit/test_lint_imports.py::test_importlinter_file_present_with_both_contracts`, per SPEC.md §4 NFR-06 clauses 1–2.

### NFR-07: 依賴與授權合規

> DERIVED: SPEC.md §4 NFR-07 — AC sub-items (==-pinned,
> requirements.lock existence, pip-licenses allowlist, SBOM fields)
> chosen to bind to §8 #22 verification and §11 row 12.

- **dimension**: `license_compliance`
- **type**: `licensing`
- Canonical: SPEC.md §4 NFR-07; SPEC.md §8 #22; SPEC.md §11 row 12.
- All runtime dependencies pinned with `==` in `requirements.txt`;
  **transitive dependencies fully locked** via `requirements.lock`.
- Allowed licenses: MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF;
  any other license → the dependency is forbidden.
- **Scan scope must include the full dependency tree** (direct +
  transitive); evidence command:
  `pip-licenses --format=json --with-system`.
- Produce SBOM at `08-config/SBOM.json` with each dependency's
  `name` / `version` / `license` / `direct|transitive`.

**Acceptance criteria**

- **AC-N7.1** Every runtime dependency in `requirements.txt` is pinned
  with `==` — decided by `tests/unit/test_requirements_pin.py::test_requirements_txt_pinned_with_double_equals`, per SPEC.md §4 NFR-07 clause 1.
- **AC-N7.2** `requirements.lock` exists and fully locks transitive
  dependencies — decided by `tests/unit/test_requirements_lock.py::test_requirements_lock_locks_transitives`, per SPEC.md §4 NFR-07 clause 1.
- **AC-N7.3** `pip-licenses --format=json --with-system` shows every
  dependency's license in the allowlist — decided by `tests/unit/test_license_allowlist.py::test_every_dep_license_in_allowlist`, per SPEC.md §4 NFR-07 clause 1 + §8 #22.
- **AC-N7.4** `08-config/SBOM.json` exists with `name`, `version`,
  `license`, and `direct|transitive` for every dependency — decided by `tests/unit/test_sbom.py::test_sbom_has_required_fields_per_dep`, per SPEC.md §4 NFR-07 clause 1.

### NFR-08: 變異測試

> DERIVED: SPEC.md §4 NFR-08 — AC sub-items (harness_config flag,
> mutation-score-≥-70 assertion) chosen to bind to §8 #24 verification
> and §11 row 7.

- **dimension**: `mutation_testing`
- **type**: `mutation`
- Canonical: SPEC.md §4 NFR-08; SPEC.md §8 #24; SPEC.md §11 row 7.
- `.methodology/harness_config.json` sets
  `features.mutation_testing: true`.
- **mutation score ≥ 70**.
- Scope limited to `service/` and `repository/`; rationale recorded in
  `harness_config.json` (runtime budget).

**Acceptance criteria**

- **AC-N8.1** `.methodology/harness_config.json` has
  `features.mutation_testing: true` — decided by `tests/unit/test_harness_config.py::test_mutation_testing_feature_enabled`, per SPEC.md §4 NFR-08 clause 1.
- **AC-N8.2** `mutmut run` followed by `mutmut results` reports
  mutation score ≥ 70 over `service/` + `repository/` — decided by `tests/unit/test_mutation_score.py::test_mutation_score_at_least_70`, per SPEC.md §4 NFR-08 clause 1 + §8 #24.

### NFR-09: 驗證真實性 (零 skip 鐵律)

> DERIVED: SPEC.md §4 NFR-09 — AC sub-items (zero-skip, zero-no-assert,
> real-DB migration test, no-exclusion-paths) chosen to bind to §8 #1
> verification and §11 rows 4/5.

- **dimension**: `test_assertion_quality`
- **type**: `testability`
- Canonical: SPEC.md §4 NFR-09; SPEC.md §8 #1; SPEC.md §11 rows 4/5.
- **No FR/NFR verification test may be a `pytest.skip` / `skipif` /
  `xfail` / assertion-free stub**.
- `pytest 03-development/tests -q` **skipped count must be 0**.
- Every test function has at least one `assert` (`zero_assert == 0`).
- **Anti-fabrication clause**: tests may not be excluded via `--ignore`
  / `-k` / `--deselect` / `collect_ignore` / removing directories from
  `testpaths`.
- **Round-2 specific clause**: FR-07's three-step migration must be
  tested against a **real database** (SQLite file, not in-memory
  mock); round-trip reversibility is verified by actual data
  comparison. **It must not be downgraded to a skip on the grounds
  that "migration logic is hard to test"** — this is precisely the
  failure mode of the previous two rounds.
- `TRACEABILITY_MATRIX.md`'s `VERIFIED` is set only when a test
  actually runs and passes.

**Acceptance criteria**

- **AC-N9.1** `pytest 03-development/tests -q` reports 0 skipped —
  decided by `tests/unit/test_zero_skip.py::test_pytest_skipped_count_zero`, per SPEC.md §4 NFR-09 clause 1 + §8 #1.
- **AC-N9.2** Every test function contains at least one `assert`
  (`zero_assert == 0`) — decided by `tests/unit/test_assertions.py::test_zero_assertion_free_tests`, per SPEC.md §4 NFR-09 clause 1.
- **AC-N9.3** FR-07's three-step migration runs against a real SQLite
  file with column-by-column round-trip comparison — decided by `tests/integration/test_migration_round_trip.py::test_v3_data_migration_round_trip_byte_identical`, per SPEC.md §4 NFR-09 "本輪特別條款" clause + FR-07.
- **AC-N9.4** No tests are excluded via `--ignore` / `-k` /
  `--deselect` / `collect_ignore` / removed `testpaths` entries —
  decided by `tests/unit/test_no_exclusion.py::test_no_test_exclusion_paths`, per SPEC.md §4 NFR-09 "反造假條款" clause.

### NFR-10: 整合覆蓋

> DERIVED: SPEC.md §4 NFR-10 — AC sub-items (≥-80% line coverage via
> httpx ASGITransport, every-error-code exercised) chosen to bind to §8
> #3 verification and §11 row 6.

- **dimension**: `integration_coverage`
- **type**: `integration`
- Canonical: SPEC.md §4 NFR-10; SPEC.md §8 #3; SPEC.md §11 row 6.
- `03-development/tests/integration/` row coverage **≥ 80%**.
- Integration tests driven through `httpx.AsyncClient(transport=
  ASGITransport(app))`; **handler functions must not be called
  directly**.
- Minimum coverage: full CRUD chain; one example each of 401/403/404/
  409/422/429/503; migration round-trip; rate limit triggered and
  recovered; graceful drain.

**Acceptance criteria**

- **AC-N10.1** `pytest 03-development/tests/integration --cov=03-development/src
  --cov-report=term` reports TOTAL ≥ 80% — decided by `tests/unit/test_integration_coverage.py::test_integration_coverage_at_least_80_percent`, per SPEC.md §4 NFR-10 clause 1 + §8 #3.
- **AC-N10.2** Integration tests use `httpx.AsyncClient(transport=
  ASGITransport(app))` and never call handler functions directly —
  decided by `tests/unit/test_integration_uses_asgi_transport.py::test_integration_tests_use_asgi_transport_not_direct_handler`, per SPEC.md §4 NFR-10 clause 1.
- **AC-N10.3** Every error code (401/403/404/409/422/429/503) has at
  least one integration test exercising it — decided by `tests/integration/test_error_contract.py::test_each_error_code_exercised`, per SPEC.md §4 NFR-10 "至少涵蓋" clause + FR-10.

### NFR-11: 可讀性

> DERIVED: SPEC.md §4 NFR-11 — AC sub-items (MI ≥ 80, CC ≤ 10,
> ≤-400-lines/file, ≤-15-files/dir, ≤-40-lines/handler) chosen to bind
> to §11 row 17.

- **dimension**: `readability`
- **type**: `maintainability`
- Canonical: SPEC.md §4 NFR-11; SPEC.md §11 row 17.
- Project MI (LLOC-weighted) **≥ 80**; single function CC **≤ 10**.
- Single file ≤ 400 lines; single directory ≤ 15 files.
- Each API handler ≤ 40 lines (business logic must sink into
  `service/`).

**Acceptance criteria**

- **AC-N11.1** Project MI ≥ 80 (LLOC-weighted) — decided by `tests/unit/test_readability.py::test_project_mi_at_least_80`, per SPEC.md §4 NFR-11 clause 1.
- **AC-N11.2** No single function has CC > 10 — decided by `tests/unit/test_readability.py::test_no_function_cc_above_10`, per SPEC.md §4 NFR-11 clause 1.
- **AC-N11.3** No file > 400 lines; no directory > 15 files — decided by `tests/unit/test_readability.py::test_file_and_dir_size_limits`, per SPEC.md §4 NFR-11 clause 1.
- **AC-N11.4** Every API handler is ≤ 40 lines — decided by `tests/unit/test_readability.py::test_api_handlers_within_40_lines`, per SPEC.md §4 NFR-11 clause 1.

### NFR-12: 系統驗證目標

> DERIVED: SPEC.md §4 NFR-12 — AC sub-item (exit-0 + PASS stdout)
> chosen to bind to §8 #27 verification and §11 row 18.

- **dimension**: `execute_verification_target`
- **type**: `verifiability`
- Canonical: SPEC.md §4 NFR-12; SPEC.md §8 #27; SPEC.md §11 row 18.
- `Makefile`'s `verify-system` target chains:
  1. `alembic upgrade head`
  2. full test suite
  3. service start + `/healthz`, `/readyz` smoke
  4. `alembic downgrade base` then `upgrade head` (round-trip
     verification)
- `make verify-system` must **exit 0** and print `verify-system: PASS`
  on stdout.

**Acceptance criteria**

- **AC-N12.1** `make verify-system` exits 0 and prints
  `verify-system: PASS` on stdout — decided by `tests/integration/test_verify_system.py::test_make_verify_system_exits_zero_and_prints_pass`, per SPEC.md §4 NFR-12 clause 1 + §8 #27.

## 5. Acceptance Criteria Summary

Twenty-seven machine-decidable acceptance items, each a single command
with expected output (canonical: SPEC.md §8). The full table is
transcribed verbatim:

| # | Command | Expected |
|---|---------|----------|
| 1 | `pytest 03-development/tests -q` | all green, **skipped count 0** (NFR-09) |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL **100%** |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL **≥ 80%** (NFR-10) |
| 4 | `POST /v1/tasks` (valid write key) | 201 + task id |
| 5 | `POST /v1/tasks` (no `X-API-Key`) | **401** + problem+json |
| 6 | `DELETE /v1/tasks/{id}` (write key, not admin) | **403**, body does not disclose whether id exists |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json |
| 8 | `POST /v1/tasks` duplicate name | **409** |
| 9 | consecutive requests exceeding `TASKQ_RATE_BURST` | **429** + `Retry-After` header |
| 10 | stop DB then `GET /readyz` | **503**, detail names DB unavailable |
| 11 | `alembic downgrade -1` then `GET /readyz` | **503**, detail names migration behind head |
| 12 | `alembic upgrade head` → write sample → `downgrade -1` → `upgrade head` | sample data identical column-by-column (v3 data migration reversible — FR-07) |
| 13 | `alembic downgrade base` | exit 0, no residual tables |
| 14 | `GET /v1/tasks?limit=50` (10,000 rows) SQL statement count | **constant** (independent of row count — N+1 guard, NFR-01) |
| 15 | `GET /v1/tasks/{id}` p95 (10,000 rows) | **< 30ms** (NFR-01) |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | **0 hits** |
| 17 | scan SQL string concatenation (f-string / `%` / `+` built SQL) | **0 hits** (NFR-02) |
| 18 | query `api_keys` table | no plaintext key; `key_hash` is 64 hex (NFR-02) |
| 19 | trigger 500 then inspect response body | no stack / SQL / file path (FR-10 / NFR-02) |
| 20 | full logs and `/v1/metrics` | no password segment of `TASKQ_DB_URL` (NFR-04) |
| 21 | `lint-imports` | **exit 0**, and `service`/`api` layers importing `sqlalchemy` is blocked (NFR-06) |
| 22 | `pip-licenses --format=json --with-system` | every dependency's license ∈ allowlist (NFR-07) |
| 23 | `bandit -r 03-development/src/` | 0 HIGH, 0 MEDIUM |
| 24 | `mutmut run` then `mutmut results` | mutation score **≥ 70** (NFR-08) |
| 25 | service shutdown with in-flight tasks | graceful drain; budget-exceeded tasks marked `interrupted`, no orphan processes (FR-08) |
| 26 | `grep -c "^TASKQ_" .env.example` | **12** (§5.1 fully declared) |
| 27 | `make verify-system` | exit 0 and stdout contains `verify-system: PASS` (NFR-12) |

## 6. Out-of-Scope

- Public-internet deployment hardening (TLS termination, WAF, DDoS
  protection) — the service binds `127.0.0.1:8000` by default; a
  reverse proxy is the assumed front (canonical: SPEC.md §5.1
  `TASKQ_HOST` default).
- Multi-region replication, read-replicas, or sharding — the design
  uses a single relational database (canonical: SPEC.md §2 "資料庫"
  row).
- Web UI / dashboard — service is API-only (canonical: SPEC.md §1
  "形態").
- Task queue distributed across external brokers (Celery/RQ) — the
  runner is in-process via `asyncio.TaskGroup` (canonical: SPEC.md
  §3 FR-08).
- Webhook callbacks / event subscription — out of scope; results are
  read back via `GET /v1/tasks/{id}/runs` (canonical: SPEC.md §3
  FR-02 / FR-09).
- TypeScript round 3 deferred (canonical: PROJECT_BRIEF.md
  "Stakeholders").

## 7. Open Issues

- NFR-99: **Resolve `error_handling` vocabulary split** — the
  canonical spec uses `dimension: error_handling` for NFR-03; the SRS
  machine-readable JSON requires `type:` to be from
  `documentation|integration|layering|licensing|maintainability|mutation|performance|reliability|security|testability|verifiability|deployability|scalability|usability`,
  and `reliability` is the closest map. The `dimension:` field still
  carries the canonical name; Phase-2 SAB generator must accept
  `dimension: error_handling` even though `type: reliability`. Test
  harness to confirm with stakeholder before Phase-3 lock.

## 8. Risks

Risks R1–R12 are transcribed verbatim from SPEC.md §9.

| ID | Risk | Impact | Likelihood | Mitigation |
|----|------|--------|-----------|-----------|
| R1 | **v3 data migration loses data** | High | Medium | round-trip test against real DB, column-by-column (FR-07 / §8 #12) |
| R2 | SQL injection | High | Low | no string concatenation + ORM/parameterised + grep gate (NFR-02) |
| R3 | API key leak | High | Medium | hashed storage + constant-time compare + printed once (FR-03) |
| R4 | 403 reveals resource existence | Medium | Medium | authorise before lookup (FR-04 / §8 #6) |
| R5 | N+1 collapses on large table | High | High | explicit eager loading + SQL count assertion (NFR-01 / §8 #14) |
| R6 | error body leaks internals | Medium | High | fixed RFC 7807 fields + detail allowlist (FR-10) |
| R7 | **`CancelledError` swallowed → shutdown hangs** | Medium | Medium | explicit ban + assertion (NFR-03) |
| R8 | task timeout leaves orphan processes | Medium | Medium | `kill()` + `await wait()` (FR-08 / §8 #25) |
| R9 | deploy without migration | High | Medium | `/readyz` fail closed (FR-09 / §8 #11) |
| R10 | connection pool exhaustion | Medium | Medium | `pool_pre_ping` + concurrency cap (FR-06/08) |
| R11 | transitive dep with incompatible license | Medium | Medium | lock file + whole-tree scan (NFR-07) |
| R12 | rate bucket race over-admits | Low | Medium | single transaction + row-level lock (FR-05) |

## 9. Glossary

| Term | Definition | Canonical ref |
|------|-----------|---------------|
| ASGI | Asynchronous Server Gateway Interface; the protocol used by FastAPI and uvicorn. | SPEC.md §2 |
| Alembic | SQLAlchemy's database migration tool; the project uses three revisions (v1→v2→v3). | SPEC.md §2, FR-07 |
| problem+json | RFC 7807 error envelope; all non-2xx responses use this `Content-Type`. | SPEC.md §2, FR-10 |
| `asyncio.TaskGroup` | Python 3.11 structured concurrency primitive for background work. | SPEC.md §3 FR-08 |
| `X-API-Key` | Header carrying the API key; required on every `/v1/*` route. | SPEC.md §3 FR-03 |
| scope | Per-token authorisation level: `read` < `write` < `admin` (hierarchical inclusion). | SPEC.md §3 FR-04 |
| token bucket | Per-token rate-limit algorithm; capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC`. | SPEC.md §3 FR-05 |
| N+1 | A query pattern that issues a constant-plus-N SQL statements for N returned rows; an acceptance failure. | SPEC.md §3 FR-06, NFR-01 |
| `lint-imports` | `import-linter` CLI; enforces layers + forbidden contracts. | SPEC.md §4 NFR-06 |
| `mutmut` | Python mutation testing tool; score ≥ 70 required. | SPEC.md §4 NFR-08 |
| mutation score | Killed mutants / total mutants × 100; ≥ 70 across `service/` + `repository/`. | SPEC.md §4 NFR-08 |
| `pip-licenses` | CLI emitting license metadata for installed packages; full-tree scan with `--with-system`. | SPEC.md §4 NFR-07 |
| SBOM | Software Bill of Materials; `08-config/SBOM.json` with `name` / `version` / `license` / `direct|transitive`. | SPEC.md §4 NFR-07 |
| `bandit` | Python security linter; 0 HIGH / 0 MEDIUM required. | SPEC.md §4 NFR-02 |
| `pytest-benchmark` | Latency micro-benchmark framework used for the p95 budgets. | SPEC.md §4 NFR-01 |
| MI | Maintainability Index (LLOC-weighted); ≥ 80 required. | SPEC.md §4 NFR-11 |
| CC | Cyclomatic Complexity; per-function ≤ 10. | SPEC.md §4 NFR-11 |
| graceful drain | Shutdown protocol that waits for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; excess marked `interrupted`. | SPEC.md §3 FR-08 |
| `correlation_id` | UUID tying a response header (`X-Correlation-Id`) and server log line together. | SPEC.md §3 FR-10 |
| `[REDACTED]` | Replacement token for any line matching the secret / token / DB-URL regex (NFR-04). | SPEC.md §4 NFR-04 |
| round-trip reversibility | `upgrade head` → write → `downgrade -1` → `upgrade head` leaves data byte-identical; the central v3 acceptance. | SPEC.md §3 FR-07 |

## 10. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-08-31",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API — POST/GET/LIST/DELETE /v1/tasks with cursor pagination; 422 on validation, 404 on unknown id, 409 on duplicate name.",
      "implementation_functions": [
        "taskq_api.api.tasks.create_task",
        "taskq_api.api.tasks.get_task",
        "taskq_api.api.tasks.list_tasks",
        "taskq_api.api.tasks.delete_task",
        "taskq_api.service.tasks.create_task",
        "taskq_api.service.tasks.get_task",
        "taskq_api.service.tasks.list_tasks",
        "taskq_api.service.tasks.delete_task",
        "taskq_api.repository.task_repo.create",
        "taskq_api.repository.task_repo.get",
        "taskq_api.repository.task_repo.list_paginated",
        "taskq_api.repository.task_repo.delete"
      ],
      "verification_method": "tests/integration/test_tasks_crud.py — AC-1.1..AC-1.7"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint — POST /v1/tasks/{id}/run returns 202; asyncio.create_subprocess_exec (shell=True forbidden); writes task_results rows; GET /v1/tasks/{id}/runs returns history.",
      "implementation_functions": [
        "taskq_api.api.tasks.run_task",
        "taskq_api.api.tasks.list_runs",
        "taskq_api.service.runner.run",
        "taskq_api.service.tasks.schedule_run",
        "taskq_api.repository.task_repo.insert_run"
      ],
      "verification_method": "tests/integration/test_task_run.py — AC-2.1..AC-2.6"
    },
    {
      "id": "FR-03",
      "description": "API Key auth — X-API-Key required on /v1/*; SHA-256 hashed storage; hmac.compare_digest; revocation via revoked_at; plaintext printed once at creation.",
      "implementation_functions": [
        "taskq_api.service.auth.hash_key",
        "taskq_api.service.auth.verify_key",
        "taskq_api.repository.key_repo.create",
        "taskq_api.repository.key_repo.get_by_hash",
        "taskq_api.repository.key_repo.revoke",
        "taskq_api.__main__.key_create"
      ],
      "verification_method": "tests/integration/test_auth.py — AC-3.1..AC-3.7"
    },
    {
      "id": "FR-04",
      "description": "Scope authorisation — read < write < admin, hierarchical inclusion; insufficient → 403 with no resource-existence leak; single FastAPI dependency enforces every /v1 route.",
      "implementation_functions": [
        "taskq_api.api.deps.require_scope",
        "taskq_api.service.auth.check_scope"
      ],
      "verification_method": "tests/integration/test_authz.py — AC-4.1..AC-4.5"
    },
    {
      "id": "FR-05",
      "description": "Rate limiting — per-token DB-backed token bucket (capacity TASKQ_RATE_BURST, refill TASKQ_RATE_PER_SEC); 429 + Retry-After; row-level lock in single transaction.",
      "implementation_functions": [
        "taskq_api.api.deps.rate_limit",
        "taskq_api.service.ratelimit.consume",
        "taskq_api.repository.rate_repo.take_token"
      ],
      "verification_method": "tests/integration/test_ratelimit.py — AC-5.1..AC-5.5"
    },
    {
      "id": "FR-06",
      "description": "Persistence layer and transaction boundaries — repository layer is the only sqlalchemy importer; one Session per request via context manager; explicit eager loading (no N+1); pool_pre_ping enabled.",
      "implementation_functions": [
        "taskq_api.repository.session.session_scope",
        "taskq_api.repository.session.engine"
      ],
      "verification_method": "tests/unit/test_session.py + tests/unit/test_lint_imports.py + tests/performance/test_list_no_n_plus_one.py — AC-6.1..AC-6.5"
    },
    {
      "id": "FR-07",
      "description": "Schema migration — Alembic v1→v2→v3 with v3 splitting tasks.result_json into task_results (data-moving) and every step reversible; round-trip byte-identical acceptance; no destructive op.execute shortcuts.",
      "implementation_functions": [
        "migrations.versions.v1_initial",
        "migrations.versions.v2_tags",
        "migrations.versions.v3_split_results"
      ],
      "verification_method": "tests/integration/test_migration_round_trip.py — AC-7.1..AC-7.5"
    },
    {
      "id": "FR-08",
      "description": "Async runner — asyncio.TaskGroup management; concurrency cap TASKQ_MAX_CONCURRENT; graceful drain up to TASKQ_DRAIN_TIMEOUT; timeout kills child process; CancelledError propagates.",
      "implementation_functions": [
        "taskq_api.service.runner.TaskGroupRunner",
        "taskq_api.service.runner.run_with_timeout",
        "taskq_api.service.runner.shutdown"
      ],
      "verification_method": "tests/unit/test_runner.py + tests/integration/test_task_run.py + tests/integration/test_shutdown_drain.py — AC-8.1..AC-8.5"
    },
    {
      "id": "FR-09",
      "description": "Health and observability — GET /healthz (no auth, liveness); GET /readyz (no auth, 503 on DB-down OR alembic current != head); GET /v1/metrics (admin, status counts + latency percentiles + rate-limit rejects).",
      "implementation_functions": [
        "taskq_api.api.health.healthz",
        "taskq_api.api.health.readyz",
        "taskq_api.api.health.metrics",
        "taskq_api.service.health.db_reachable",
        "taskq_api.service.health.alembic_at_head"
      ],
      "verification_method": "tests/integration/test_health.py + tests/integration/test_metrics.py — AC-9.1..AC-9.5"
    },
    {
      "id": "FR-10",
      "description": "Error contract — all non-2xx responses Content-Type application/problem+json with type/title/status/detail/instance/correlation_id; detail leaks no internals; X-Correlation-Id in header and logs.",
      "implementation_functions": [
        "taskq_api.errors.problem_json",
        "taskq_api.errors.exception_handlers",
        "taskq_api.errors.install_handlers"
      ],
      "verification_method": "tests/integration/test_error_contract.py — AC-10.1..AC-10.5"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms and GET /v1/tasks?limit=50 p95 < 80ms at 10k rows; constant SQL statement count (N+1 guarded).",
      "test_method": "tests/performance/test_get_by_id_latency.py + tests/performance/test_list_latency.py + tests/performance/test_list_no_n_plus_one.py — AC-N1.1..AC-N1.3"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "shell=True/eval(/exec( forbidden; string-concatenated SQL forbidden; hashed keys + constant-time compare; 403 leaks nothing; CORS deny-by-default; bandit 0 HIGH / 0 MEDIUM.",
      "test_method": "tests/unit/test_security_scan.py + tests/unit/test_bandit.py + tests/integration/test_auth.py — AC-N2.1..AC-N2.4"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Explicit transaction boundaries; no bare except: / except Exception: pass; CancelledError propagates; timeouts kill children; failed migration rolls back.",
      "test_method": "tests/unit/test_error_handling.py + tests/unit/test_runner_cancellation.py + tests/integration/test_health.py + tests/integration/test_migration_round_trip.py — AC-N3.1..AC-N3.4"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Redaction before write/emit, including the database URL password — never in logs, errors, or metrics.",
      "test_method": "tests/unit/test_redaction.py + tests/unit/test_key_create.py — AC-N4.1..AC-N4.3"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "100% public docstrings with [FR-XX]/[NFR-XX]; every endpoint documented in /openapi.json.",
      "test_method": "tests/unit/test_docstrings.py + tests/integration/test_openapi.py — AC-N5.1..AC-N5.2"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": ".importlinter layers contract api > service > repository > models + forbidden contract banning sqlalchemy outside repository/.",
      "test_method": "tests/unit/test_lint_imports.py — AC-N6.1..AC-N6.3"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "== pinning + requirements.lock for transitives; allowlist; full-tree scan; SBOM marks direct vs transitive.",
      "test_method": "tests/unit/test_requirements_pin.py + tests/unit/test_requirements_lock.py + tests/unit/test_license_allowlist.py + tests/unit/test_sbom.py — AC-N7.1..AC-N7.4"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "features.mutation_testing: true; mutation score ≥ 70 over service/ + repository/.",
      "test_method": "tests/unit/test_harness_config.py + tests/unit/test_mutation_score.py — AC-N8.1..AC-N8.2"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "0 skipped, 0 assertion-free tests, anti-fabrication clause, migration tested against real DB file.",
      "test_method": "tests/unit/test_zero_skip.py + tests/unit/test_assertions.py + tests/integration/test_migration_round_trip.py + tests/unit/test_no_exclusion.py — AC-N9.1..AC-N9.4"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration coverage ≥ 80% driven through httpx.ASGITransport, covering every error code.",
      "test_method": "tests/unit/test_integration_coverage.py + tests/unit/test_integration_uses_asgi_transport.py + tests/integration/test_error_contract.py — AC-N10.1..AC-N10.3"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "MI ≥ 80; CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines per API handler.",
      "test_method": "tests/unit/test_readability.py — AC-N11.1..AC-N11.4"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "make verify-system chains upgrade → tests → health smoke → migration round-trip, exit 0 with verify-system: PASS on stdout.",
      "test_method": "tests/integration/test_verify_system.py — AC-N12.1"
    }
  ]
}
```
<!-- FR:END -->

> `type:` vocabulary note: this list mirrors
> `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES`; `error_handling`
> is a valid `dimension:` per `sab_parser` (used by NFR-03 above) but is
> never `type:`. The mapping is pinned by
> `tests/test_sab_parser.py::TestCanonicalTemplate::test_srs_template_nfr_type_example_matches_vocabulary`.

> INGESTION MODE note: every `### FR-NN` (FR-01..FR-10) and `### NFR-NN`
> (NFR-01..NFR-12) heading from canonical SPEC.md appears in the JSON
> arrays above. `harness_cli.py check-spec-alignment` blocks on a
> dropped or invented requirement; omission here would be a P1
> exit-checklist failure.
