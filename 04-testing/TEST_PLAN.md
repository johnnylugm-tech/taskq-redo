# TEST_PLAN — taskq-api (Phase 4)

Source of truth: `01-requirements/SRS.md` §3 (FR-01..FR-10 acceptance criteria),
§4 (NFR-01..NFR-12) and `.methodology/quality_manifest.json`
(`fr_ids`, `nfr_traceability`, `quality_targets`).

## 1. Scope

Every FR in the manifest (`FR-01`..`FR-10`) and every NFR (`NFR-01`..`NFR-12`)
has at least one test case below. Each FR block covers four categories:

| Category | Meaning |
|----------|---------|
| POS | positive / happy path |
| NEG | negative — rejected input, wrong auth, wrong state |
| BND | boundary — limits, caps, exact thresholds |
| EDG | edge case — races, orphans, reversibility, leakage |

Priority: **P0** = release blocker (AC-decided, gate-scored), **P1** = important,
**P2** = defensive.

## 2. Conventions

- Integration tests drive the ASGI app through `httpx` `ASGITransport`
  (NFR-10); no live network socket.
- Unit tests target a single module; static/forensic assertions (grep-style)
  live in unit tests.
- Test-ID column `AC` cites the SRS acceptance criterion the case decides;
  `—` means the case is a supporting case with no dedicated AC.
- All error bodies asserted as `application/problem+json` with the six FR-10
  fields; per-FR rows below only note the status code.

---

## 3. FR-01 — Task resource CRUD API

Target file: `tests/integration/test_tasks_crud.py` (unless noted).

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-01-01 | POS | AC-1.1 | Create task with valid body + `write` key | `POST /v1/tasks` `{"name":"t1","command":"echo hi"}`, `X-API-Key` scope `write` | 201, body has `id`, row persisted with `status=pending` | P0 |
| TC-01-02 | POS | AC-1.1 | Retrieve created task, all columns present | `GET /v1/tasks/{id}`, `read` key | 200, body has `id,name,command,status,created_at` | P0 |
| TC-01-03 | POS | AC-1.6 | List tasks, cursor pagination walks whole set | seed 5 tasks, `GET /v1/tasks?limit=2` then follow returned cursor | 200 pages of 2/2/1, no duplicate/missing id, `next_cursor` null on last page | P0 |
| TC-01-04 | POS | — | List filtered by `?status=` | seed pending+done, `GET /v1/tasks?status=done` | 200, only `done` rows | P1 |
| TC-01-05 | POS | AC-1.7 | Delete task removes task + its `task_results` row in one transaction | task with a results row, `DELETE /v1/tasks/{id}` admin key | 204/200; both rows gone; no orphan `task_results` row | P0 |
| TC-01-06 | NEG | AC-1.2 | Empty `name` rejected | `POST /v1/tasks` `{"name":"","command":"echo"}` | 422 problem+json | P0 |
| TC-01-07 | NEG | AC-1.2 | Injection-blacklist character in `command` rejected | `{"name":"t","command":"echo hi; rm -rf /"}` | 422 problem+json | P0 |
| TC-01-08 | NEG | AC-1.3 | Unknown id | `GET /v1/tasks/999999` | 404 problem+json | P0 |
| TC-01-09 | NEG | AC-1.4 | Duplicate `name` | POST same `name` twice | first 201, second 409 problem+json | P0 |
| TC-01-10 | NEG | AC-1.6 | `offset` query parameter not accepted | `GET /v1/tasks?offset=10` | request does not paginate by offset (param ignored/422); no offset SQL emitted | P0 |
| TC-01-11 | BND | AC-1.2 | `command` length exactly 1000 accepted, 1001 rejected | two POSTs | 201 then 422 | P0 |
| TC-01-12 | BND | AC-1.5 | `limit` boundary: 200 accepted, 201 rejected | `GET /v1/tasks?limit=200`, `?limit=201` | 200 then 422 | P0 |
| TC-01-13 | BND | — | Default `limit` is 50 when omitted | seed 60 tasks, `GET /v1/tasks` | 200, exactly 50 items | P1 |
| TC-01-14 | BND | AC-1.5 | `limit=0` / negative rejected | `?limit=0`, `?limit=-1` | 422 | P1 |
| TC-01-15 | EDG | — | Malformed/unknown cursor value | `GET /v1/tasks?cursor=@@@` | 422 problem+json, no traceback in `detail` | P1 |
| TC-01-16 | EDG | AC-1.7 | Delete rolls back fully if the results delete fails | force error inside the same transaction | task row still present (atomicity) | P1 |

## 4. FR-02 — Task execution endpoint

Target files: `tests/integration/test_task_run.py`, `tests/unit/test_runner_subprocess.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-02-01 | POS | AC-2.1 | Run accepted | `POST /v1/tasks/{id}/run`, `write` key | 202, body has `run_id` | P0 |
| TC-02-02 | POS | AC-2.3 | Success path state machine | task `echo hi`, run to completion | states observed `pending → running → done` | P0 |
| TC-02-03 | POS | AC-2.4 | Result row populated | successful run | `task_results` row with non-null `exit_code, stdout_tail, stderr_tail, duration_ms, finished_at` | P0 |
| TC-02-04 | POS | AC-2.6 | Run history newest first | run task 3 times, `GET /v1/tasks/{id}/runs` | 200, `finished_at`/`id` strictly descending | P0 |
| TC-02-05 | NEG | AC-2.3 | Failing command → `failed` | command exiting 1 | terminal state `failed`, `exit_code != 0` | P0 |
| TC-02-06 | NEG | — | Run unknown task id | `POST /v1/tasks/999999/run` | 404 problem+json | P0 |
| TC-02-07 | NEG | AC-2.2 | `shell=True` absent from `03-development/src/` | forensic scan of source tree | 0 hits | P0 |
| TC-02-08 | BND | AC-2.5 | Command just under timeout completes; just over is `timeout` | `TASKQ_TASK_TIMEOUT` small, two sleeps around it | `done` then `timeout` | P0 |
| TC-02-09 | BND | — | `stdout_tail` truncated to configured tail size | command emitting more than tail bytes | stored tail length == cap, keeps the *last* bytes | P1 |
| TC-02-10 | EDG | AC-2.5 | Timeout kills child, no orphan | long-running child, exceed timeout | `process.kill()` then `await process.wait()`; child pid no longer alive | P0 |
| TC-02-11 | EDG | AC-2.2 | Execution uses `create_subprocess_exec(*shlex.split(cmd))` | unit-level inspection/spy | exec form used, argv split, never a shell string | P0 |
| TC-02-12 | EDG | — | Concurrent runs of the same task produce distinct `run_id`s | two runs in flight | two rows, unique `run_id` | P1 |

## 5. FR-03 — API key authentication

Target files: `tests/integration/test_auth.py`, `tests/unit/test_auth_compare.py`,
`tests/unit/test_key_create.py`, `tests/integration/test_health.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-03-01 | POS | — | Valid key authenticates | valid `write` key on `POST /v1/tasks` | 201 | P0 |
| TC-03-02 | POS | AC-3.7 | `/healthz` and `/readyz` need no auth | both without `X-API-Key` | 200 (not 401) | P0 |
| TC-03-03 | POS | AC-3.6 | `key create --scope` prints plaintext once, stores hash | `python -m taskq_api key create --scope read` | plaintext appears exactly once in stdout; DB holds only the SHA-256 hash | P0 |
| TC-03-04 | NEG | AC-3.1 | Missing header | `GET /v1/tasks` no key | 401 problem+json | P0 |
| TC-03-05 | NEG | AC-3.2 | Invalid key | random key value | 401 problem+json | P0 |
| TC-03-06 | NEG | AC-3.5 | Revoked key | key with `revoked_at` set | 401 problem+json | P0 |
| TC-03-07 | BND | AC-3.3 | `key_hash` is 64-hex SHA-256; no plaintext column value | inspect `api_keys` rows | every `key_hash` matches `^[0-9a-f]{64}$`; plaintext not found anywhere in the table | P0 |
| TC-03-08 | BND | — | Empty `X-API-Key` header treated as missing | `X-API-Key: ""` | 401, not 500 | P1 |
| TC-03-09 | EDG | AC-3.4 | Comparison uses `hmac.compare_digest` | unit inspection of auth path | `hmac.compare_digest` used; no `==` on secrets | P0 |
| TC-03-10 | EDG | AC-3.2 | 401 body does not echo the submitted key | invalid key with recognisable value | key value absent from body/headers/log | P1 |

## 6. FR-04 — Scope authorisation

Target files: `tests/integration/test_authz.py`, `tests/unit/test_authz.py`,
`tests/unit/test_single_authz_dependency.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-04-01 | POS | AC-4.3 | `admin` may delete | `DELETE /v1/tasks/{id}` admin key | 2xx, row deleted | P0 |
| TC-04-02 | POS | AC-4.5 | Hierarchy: `admin` satisfies `write`, `write` satisfies `read` | admin key on write route; write key on read route | both allowed | P0 |
| TC-04-03 | NEG | AC-4.1 | `read` key on write route | `POST /v1/tasks` read key | 403 problem+json | P0 |
| TC-04-04 | NEG | — | `write` key on `GET /v1/metrics` (admin) | write key | 403 | P0 |
| TC-04-05 | BND | AC-4.2 | 403 precedes 404: unknown id with insufficient scope | `DELETE /v1/tasks/999999` write key | 403, body identical to the existing-id 403 (no existence disclosure) | P0 |
| TC-04-06 | EDG | AC-4.4 | Single authn/authz dependency across all `/v1/*` routes | route-table introspection | every `/v1` route shares exactly one dependency callable | P0 |
| TC-04-07 | EDG | — | Unknown scope value on a key is denied, not defaulted | key with scope `"root"` | 403, never elevated | P1 |

## 7. FR-05 — Rate limiting

Target files: `tests/integration/test_ratelimit.py`, `tests/unit/test_ratelimit.py`,
`tests/integration/test_health.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-05-01 | POS | — | Requests within burst all succeed | `TASKQ_RATE_BURST` requests | all 2xx | P0 |
| TC-05-02 | POS | AC-5.2 | Bucket refills at `TASKQ_RATE_PER_SEC` | exhaust, wait one refill interval, retry | request succeeds again | P0 |
| TC-05-03 | POS | AC-5.5 | `/healthz`, `/readyz` exempt | flood both past burst | always 200, never 429 | P0 |
| TC-05-04 | NEG | AC-5.1 | Over burst | burst+1 requests in window | 429 problem+json with `Retry-After` (integer seconds ≥ 1) | P0 |
| TC-05-05 | BND | AC-5.1 | Exactly at burst passes, burst+1 rejects | N then N+1 | 2xx then 429 | P0 |
| TC-05-06 | BND | — | Rate limit is per token, not global | two distinct keys, one exhausted | second key unaffected | P1 |
| TC-05-07 | EDG | AC-5.3 | Bucket state is DB-backed / shared across workers | mutate bucket via a second session/"worker" | limit observed by both; no in-process-only state | P0 |
| TC-05-08 | EDG | AC-5.4 | Bucket update in one transaction with row-level lock | unit inspection + concurrent decrement | locking construct used; no over-admit beyond capacity | P0 |

## 8. FR-06 — Persistence layer and transaction boundaries

Target files: `tests/unit/test_session.py`, `tests/unit/test_no_string_sql.py`,
`tests/unit/test_engine.py`, `tests/performance/test_list_no_n_plus_one.py`,
plus `lint-imports`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-06-01 | POS | AC-6.2 | One session per request; commit on success | successful request | exactly one `Session` opened and committed, then closed | P0 |
| TC-06-02 | POS | AC-6.5 | Engine pool config from env | `TASKQ_DB_POOL_SIZE` set | `pool_size` equals env value, `pool_pre_ping is True` | P0 |
| TC-06-03 | NEG | AC-6.2 | Exception rolls back | handler raising inside the unit of work | rollback called, no partial row persisted | P0 |
| TC-06-04 | NEG | AC-6.1 | `service/` and `api/` hold no `sqlalchemy` import | `lint-imports` | exit 0; 0 sqlalchemy imports outside `repository/` | P0 |
| TC-06-05 | NEG | AC-6.3 | No string-built SQL in `03-development/src/` | forensic scan for f-string/`%`/`+` SQL | 0 hits | P0 |
| TC-06-06 | BND | AC-6.4 | List endpoint statement count constant | list 1 row vs 50 rows with relationships | identical SQL statement count (no N+1) | P0 |
| TC-06-07 | BND | — | Eager loading uses explicit `selectinload`/`joinedload` | inspect relationship query | explicit loader option present | P1 |
| TC-06-08 | EDG | — | Business layer never receives a `Session` | inspect service signatures/usage | service layer holds no `Session` object | P1 |
| TC-06-09 | EDG | AC-6.2 | Session closed even when commit itself fails | commit raising | rollback + close still executed (context manager) | P1 |

## 9. FR-07 — Alembic three-step schema migration

Target files: `tests/integration/test_migration_round_trip.py`, `tests/unit/test_migrations.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-07-01 | POS | AC-7.1 | `upgrade head` then `downgrade base` on a real SQLite file | fresh DB file | both exit 0; schema empty at the end | P0 |
| TC-07-02 | POS | AC-7.4 | Every revision's `downgrade()` exercised | step down v3→v2→v1→base and back up | each step succeeds against a real file | P0 |
| TC-07-03 | POS | — | Offline SQL generation for each revision | `alembic upgrade --sql` | SQL emitted, contains the expected DDL per revision | P1 |
| TC-07-04 | NEG | AC-7.3 | No `op.execute("DROP TABLE ...")` shortcut in place of a real downgrade | forensic scan of `migrations/versions/` | 0 hits | P0 |
| TC-07-05 | NEG | — | `downgrade` of v2 leaves v1 data intact | seed v1 rows, run v2 up then down | v1 rows unchanged, v2 tables/index gone | P0 |
| TC-07-06 | BND | AC-7.2 | v3 round-trip byte-identical | write sample results, `downgrade -1`, `upgrade head` | every column byte-identical; zero rows lost | P0 |
| TC-07-07 | BND | AC-7.5 | v2 unique index on `tasks.name` survives round-trip | round-trip then insert duplicate name | index present; duplicate insert rejected | P0 |
| TC-07-08 | EDG | AC-7.2 | v3 downgrade re-populates `tasks.result_json` from `task_results` | rows only in `task_results` | after downgrade the JSON payload matches exactly | P0 |
| TC-07-09 | EDG | — | v3 upgrade with NULL/empty `result_json` rows | mixed NULL and populated rows | migration completes, NULLs not turned into bogus rows | P1 |

## 10. FR-08 — Asynchronous runner

Target files: `tests/unit/test_runner.py`, `tests/unit/test_runner_cancellation.py`,
`tests/performance/test_runner_concurrency_cap.py`, `tests/integration/test_shutdown_drain.py`,
`tests/integration/test_task_run.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-08-01 | POS | AC-8.1 | Runner managed by `asyncio.TaskGroup` | inspect runner | `TaskGroup` used for lifecycle | P0 |
| TC-08-02 | POS | AC-8.4 | Shutdown drains in-flight tasks within budget | task finishing inside `TASKQ_DRAIN_TIMEOUT`, then shutdown | task reaches `done`; shutdown clean | P0 |
| TC-08-03 | NEG | AC-8.4 | Task exceeding drain budget marked `interrupted` | task longer than `TASKQ_DRAIN_TIMEOUT` | state `interrupted`; shutdown still completes | P0 |
| TC-08-04 | BND | AC-8.2 | Concurrency capped at `TASKQ_MAX_CONCURRENT` | submit cap+N runs | max observed in-flight == cap; excess queued, none dropped | P0 |
| TC-08-05 | BND | AC-8.3 | Timeout via `asyncio.wait_for` at the exact budget | run at/over `TASKQ_TASK_TIMEOUT` | terminal `timeout` state | P0 |
| TC-08-06 | EDG | AC-8.3 | `process.kill()` then `await process.wait()`, no orphan | child ignoring soft signals | child reaped; no orphan pid | P0 |
| TC-08-07 | EDG | AC-8.5 | `CancelledError` propagates, never swallowed | cancel a running task; scan for bare/`except Exception` swallow | `CancelledError` re-raised; no bare `except` in runner | P0 |
| TC-08-08 | EDG | — | Queued-but-unstarted task at shutdown is not left `running` | shutdown with queued backlog | queued tasks left in a consistent, non-`running` state | P1 |

## 11. FR-09 — Health checks and observability

Target files: `tests/integration/test_health.py`, `tests/integration/test_metrics.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-09-01 | POS | AC-9.1 | `/healthz` alive | `GET /healthz` | 200 `{"status":"ok"}` | P0 |
| TC-09-02 | POS | AC-9.4 | `/readyz` healthy | DB reachable, alembic at head | 200 | P0 |
| TC-09-03 | POS | AC-9.5 | `/v1/metrics` series | admin key | 200 with task counts by status, latency percentiles, rate-limit reject counts | P0 |
| TC-09-04 | NEG | AC-9.2 | `/readyz` DB unreachable | break DB connectivity | 503, `detail` names the DB condition | P0 |
| TC-09-05 | NEG | AC-9.3 | `/readyz` migration behind head | stamp DB one revision behind | 503, `detail` names the migration condition (fail closed) | P0 |
| TC-09-06 | NEG | — | `/v1/metrics` without admin scope | read/write key | 403 | P0 |
| TC-09-07 | BND | — | `/healthz` still 200 while DB is down (liveness ≠ readiness) | DB down | `/healthz` 200, `/readyz` 503 | P1 |
| TC-09-08 | EDG | AC-9.2 | `/readyz` failure body leaks no DSN/password or stack trace | DB down with password in URL | password and traceback absent from body | P0 |
| TC-09-09 | EDG | — | Metrics percentiles with zero runs | empty DB | 200, well-formed zero/empty values, no divide-by-zero | P1 |

## 12. FR-10 — Error contract (RFC 7807)

Target file: `tests/integration/test_error_contract.py`.

| ID | Cat | AC | Description | Input | Expected | Pri |
|----|-----|----|-------------|-------|----------|-----|
| TC-10-01 | POS | AC-10.4 | `X-Correlation-Id` on success too, and in logs | any 2xx request | header present; same id in server log record | P0 |
| TC-10-02 | POS | AC-10.2 | Problem body field set | any 4xx | exactly `type,title,status,detail,instance,correlation_id` | P0 |
| TC-10-03 | NEG | AC-10.1 | Content type on every non-2xx | 401/403/404/409/422/429/503/500 responses | all `application/problem+json` | P0 |
| TC-10-04 | NEG | AC-10.5 | Every mapped code exercised | one request per code 422/401/403/404/409/429/503/500 | each observed at least once with correct `status` field | P0 |
| TC-10-05 | BND | AC-10.2 | `status` field equals the HTTP status line | each error above | numeric equality | P0 |
| TC-10-06 | EDG | AC-10.3 | 500 `detail` leaks no internals | force an unexpected server error | no SQL text, no traceback, no file path, no schema/table names | P0 |
| TC-10-07 | EDG | AC-10.4 | Correlation id is per-request unique and echoed when client supplies one | two requests; one with inbound `X-Correlation-Id` | distinct ids; supplied id preserved | P1 |
| TC-10-08 | EDG | — | `type` is a URI and `instance` points at the request path | any error | `type` parses as URI; `instance` matches request path | P1 |

---

## 13. NFR test cases

| ID | NFR | Dimension | Description | Verification | Expected | Pri |
|----|-----|-----------|-------------|--------------|----------|-----|
| TC-N01-01 | NFR-01 | performance | Task API latency budget | benchmark `taskq_api.service.tasks` hot path | p95 < 30 ms | P0 |
| TC-N01-02 | NFR-01 | performance | No N+1 on relationship reads | statement counter (see TC-06-06) | statement count constant | P0 |
| TC-N02-01 | NFR-02 | security | Static security scan | `bandit` over `src/` | 0 HIGH, 0 MEDIUM | P0 |
| TC-N02-02 | NFR-02 | security | No string-built SQL | forensic scan (see TC-06-05) | 0 hits | P0 |
| TC-N03-01 | NFR-03 | error_handling | No bare `except` in source | forensic scan | 0 bare `except:` / no `except Exception` swallowing `CancelledError` | P0 |
| TC-N03-02 | NFR-03 | error_handling | `CancelledError` propagates | see TC-08-07 | re-raised | P0 |
| TC-N04-01 | NFR-04 | security | DB-URL password never in logs/errors/metrics | DSN with password, trigger error + read logs and `/v1/metrics` | password string absent everywhere | P0 |
| TC-N05-01 | NFR-05 | documentation | Public docstring coverage with `[FR-XX]`/`[NFR-XX]` tags | docstring coverage check | 100% of public symbols documented and tagged | P0 |
| TC-N06-01 | NFR-06 | architecture_constraints | Layering contract | `lint-imports` | exit 0; sqlalchemy forbidden outside `repository` | P0 |
| TC-N06-02 | NFR-06 | architecture_constraints | No circular dependencies | import graph check | 0 cycles | P0 |
| TC-N07-01 | NFR-07 | license_compliance | Dependency licenses in allowlist | license scan of all deps | every license in {MIT, BSD, Apache, PSF} | P0 |
| TC-N08-01 | NFR-08 | mutation_testing | Mutation score, service+repository layers | `mutmut` run | score ≥ 70 | P0 |
| TC-N09-01 | NFR-09 | test_assertion_quality | No skipped and no zero-assertion tests | suite introspection | 0 skipped, 0 zero-assert | P0 |
| TC-N09-02 | NFR-09 | test_assertion_quality | Migrations tested against a real DB file | see TC-07-01/TC-07-02 | real SQLite file used, not mocks | P0 |
| TC-N10-01 | NFR-10 | integration_coverage | Integration coverage via `httpx` ASGITransport | coverage run of integration suite | ≥ 80% line coverage | P0 |
| TC-N11-01 | NFR-11 | readability | Maintainability and size limits | radon MI/CC + file/dir/handler size checks | MI ≥ 80; CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines/handler | P0 |
| TC-N12-01 | NFR-12 | execute_verification_target | System verification target | `make verify-system` | exit 0 and prints `verify-system: PASS` | P0 |

## 14. Coverage check

| FR | Cases | POS | NEG | BND | EDG |
|----|-------|-----|-----|-----|-----|
| FR-01 | 16 | ✅ | ✅ | ✅ | ✅ |
| FR-02 | 12 | ✅ | ✅ | ✅ | ✅ |
| FR-03 | 10 | ✅ | ✅ | ✅ | ✅ |
| FR-04 | 7 | ✅ | ✅ | ✅ | ✅ |
| FR-05 | 8 | ✅ | ✅ | ✅ | ✅ |
| FR-06 | 9 | ✅ | ✅ | ✅ | ✅ |
| FR-07 | 9 | ✅ | ✅ | ✅ | ✅ |
| FR-08 | 8 | ✅ | ✅ | ✅ | ✅ |
| FR-09 | 9 | ✅ | ✅ | ✅ | ✅ |
| FR-10 | 8 | ✅ | ✅ | ✅ | ✅ |

All 10 manifest FRs and all 12 NFRs are covered. Every SRS acceptance
criterion AC-1.1..AC-10.5 is cited by at least one test case ID above.
