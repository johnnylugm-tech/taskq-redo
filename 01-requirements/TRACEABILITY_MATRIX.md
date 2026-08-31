# Traceability Matrix — taskq-api

> Bidirectional Requirements Traceability Matrix
> Framework: harness-methodology v2.7.0
> Project: taskq-api (round 2 of 3 in the progressive test-bed)
> Canonical spec: `SPEC.md` §3 / §4; transcribed verbatim into `01-requirements/SRS.md` §3 / §4
> Sources: `01-requirements/SRS.md`, `01-requirements/SPEC_TRACKING.md`, `02-architecture/SAD.md`, `02-architecture/TEST_SPEC.md`, `TEST_INVENTORY.yaml`, `02-architecture/SAD.md` §5 SAB block, `08-config/RISK_REGISTER.md` (transcribed from `SPEC.md` §9)
> ASPICE process areas supported: SWE.3 (bidirectional traceability), SWE.4/SYS.4 (verification consistency)

---

## Overview

This matrix provides complete **FR ↔ NFR ↔ Spec ↔ Design ↔ Code ↔ Test** bidirectional traceability for the `taskq-api` project. The status columns are **machine-refreshed** by `harness_cli.py advance-phase` from the live code/test scan (`build_traceability` / `quality_manifest.json`); a hand-edit to a Status cell is overwritten on the next advance. The semantic cells — Spec Description / Decision Framework / Coverage / Links — are filled here from `SPEC.md` and `SRS.md` and are the single source of human-readable traceability.

**Scope**: 10 functional requirements (FR-01..FR-10) and 12 non-functional requirements (NFR-01..NFR-12). Every row carries a status drawn from `SPEC_TRACKING.md`; every Spec Description is a verbatim transcription of `SRS.md` §3 / §4; every Implementation Module / Function / Test File cell is drawn from `SRS.md` §10 FR Block or the corresponding AC's "decided by" clause.

**Layering contract**: All `/v1/*` routes flow through the single authn/authz dependency (`taskq_api.api.deps.require_scope`) per FR-04 / NFR-06; `taskq_api.repository.*` is the only `sqlalchemy` importer per FR-06 / NFR-06. The traceability rows below reflect this layering.

---

## FR ↔ NFR ↔ Spec Mapping (P1 Authoritative Source)

| ID | Spec Description (verbatim from `SRS.md` §3 / §4) | Priority | `SPEC.md` § / `SRS.md` § | Status (machine-refreshed) | Notes |
|----|---------------------------------------------------|----------|---------------------------|----------------------------|-------|
| FR-01 | Task resource CRUD API — POST/GET/LIST/DELETE `/v1/tasks` with cursor pagination (limit 50, cap 200); 422 validation / 404 unknown id / 409 duplicate name. | HIGH | SPEC §3 FR-01 / SRS §3 FR-01 | DRAFT | AC-1.1..AC-1.7 (7 ACs); ties to NFR-01 (list latency) and NFR-10 (integration coverage). |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` → 202 + `run_id`; `asyncio.create_subprocess_exec`; `shell=True` forbidden; writes `task_results` rows; `GET /v1/tasks/{id}/runs` returns history. | HIGH | SPEC §3 FR-02 / SRS §3 FR-02 | DRAFT | AC-2.1..AC-2.6 (6 ACs); timeout behaviour shared with FR-08; couples to NFR-02 / NFR-03. |
| FR-03 | API key authentication — `X-API-Key` required on every `/v1/*`; SHA-256 hashed storage, `hmac.compare_digest`; `revoked_at` invalidation; plaintext printed once at `key create`. | HIGH | SPEC §3 FR-03 / SRS §3 FR-03 | DRAFT | AC-3.1..AC-3.7 (7 ACs); `/healthz` + `/readyz` exempt (FR-09); evidence `SPEC.md` §8 #18. |
| FR-04 | Scope authorisation — `read` < `write` < `admin` hierarchical; 403 must not disclose resource existence; one shared FastAPI dependency for every `/v1` route. | HIGH | SPEC §3 FR-04 / SRS §3 FR-04 | DRAFT | AC-4.1..AC-4.5 (5 ACs); leak-guard `SPEC.md` §8 #6; risk R4. |
| FR-05 | Rate limiting — per-token DB-backed token bucket (`TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC`); 429 + `Retry-After`; row-level lock in a single transaction; health endpoints exempt. | HIGH | SPEC §3 FR-05 / SRS §3 FR-05 | DRAFT | AC-5.1..AC-5.5 (5 ACs); burst evidence `SPEC.md` §8 #9; risk R12. |
| FR-06 | Persistence layer and transaction boundaries — repository-only `sqlalchemy`; one `Session` per request via context manager; explicit eager loading (no N+1); `pool_pre_ping = True`. | HIGH | SPEC §3 FR-06 / SRS §3 FR-06 | DRAFT | AC-6.1..AC-6.5 (5 ACs); couples to NFR-01 / NFR-02 / NFR-06. |
| FR-07 | Schema migration — Alembic v1→v2→v3; v3 moves `tasks.result_json` into `task_results` with real data migration; every revision reversible; no `op.execute("DROP TABLE ...")` shortcut. | HIGH | SPEC §3 FR-07 / SRS §3 FR-07 | DRAFT | AC-7.1..AC-7.5 (5 ACs); round-trip evidence `SPEC.md` §8 #12/#13; risk R1; real-DB clause NFR-09. |
| FR-08 | Async runner — `asyncio.TaskGroup`; cap `TASKQ_MAX_CONCURRENT`; graceful drain to `TASKQ_DRAIN_TIMEOUT` (excess `interrupted`); timeout `kill()` + `await wait()`; `CancelledError` propagates. | HIGH | SPEC §3 FR-08 / SRS §3 FR-08 | DRAFT | AC-8.1..AC-8.5 (5 ACs); drain evidence `SPEC.md` §8 #25; risks R7/R8. |
| FR-09 | Health and observability — `/healthz` 200 liveness; `/readyz` 503 when DB unreachable **or** `alembic current` != head (fail closed); `/v1/metrics` (admin) counts + latency percentiles + rate-limit rejects. | HIGH | SPEC §3 FR-09 / SRS §3 FR-09 | DRAFT | AC-9.1..AC-9.5 (5 ACs); evidence `SPEC.md` §8 #10/#11; risk R9. |
| FR-10 | Error contract (RFC 7807) — all non-2xx `application/problem+json` with `type`/`title`/`status`/`detail`/`instance`/`correlation_id`; no internals in `detail`; `X-Correlation-Id` header linked to logs; full code map. | HIGH | SPEC §3 FR-10 / SRS §3 FR-10 | DRAFT | AC-10.1..AC-10.5 (5 ACs); evidence `SPEC.md` §8 #5–#11/#19; risk R6. |
| NFR-01 | Performance — `GET /v1/tasks/{id}` p95 < 30ms and `GET /v1/tasks?limit=50` p95 < 80ms at 10k rows; list SQL statement count constant. | HIGH | SPEC §4 NFR-01 / SRS §4 NFR-01 | DRAFT | AC-N1.1..AC-N1.3 (3 ACs); evidence `SPEC.md` §8 #14/#15; risk R5; type=`performance`. |
| NFR-02 | Security — `shell=True` / `eval(` / `exec(` and string-concatenated SQL forbidden; hashed keys + constant-time compare; 403 leaks nothing; CORS deny-by-default; bandit 0 HIGH / 0 MEDIUM. | HIGH | SPEC §4 NFR-02 / SRS §4 NFR-02 | DRAFT | AC-N2.1..AC-N2.4 (4 ACs); evidence `SPEC.md` §8 #16/#17/#18/#23; risk R2; type=`security`. |
| NFR-03 | Reliability — explicit transaction boundaries; no bare `except:` / `except Exception: pass`; `CancelledError` re-raised; timeout kills child; failed migration rolls back. | HIGH | SPEC §4 NFR-03 / SRS §4 NFR-03 | DRAFT | AC-N3.1..AC-N3.4 (4 ACs); risks R7/R8; type=`reliability`, dimension=`error_handling` (vocabulary split tracked as NFR-99). |
| NFR-04 | Security (redaction) — secret redaction before write/emit (`[REDACTED]` whole line); DB URL password never in logs, errors, or `/v1/metrics`; key plaintext never persisted. | HIGH | SPEC §4 NFR-04 / SRS §4 NFR-04 | DRAFT | AC-N4.1..AC-N4.3 (3 ACs); evidence `SPEC.md` §8 #20; risk R3; type=`security`. |
| NFR-05 | Documentation — 100% public docstrings carrying `[FR-XX]`/`[NFR-XX]`; every endpoint has `summary` + `description` in `/openapi.json`. | MEDIUM | SPEC §4 NFR-05 / SRS §4 NFR-05 | DRAFT | AC-N5.1..AC-N5.2 (2 ACs); type=`documentation`. |
| NFR-06 | Layering — `.importlinter` layers contract `api > service > repository > models` plus forbidden contract banning `sqlalchemy` outside `repository/`; `lint-imports` exit 0. | HIGH | SPEC §4 NFR-06 / SRS §4 NFR-06 | DRAFT | AC-N6.1..AC-N6.3 (3 ACs); evidence `SPEC.md` §8 #21; type=`layering`; contract downgrade forbidden. |
| NFR-07 | Licensing — `==` pinning + `requirements.lock` for transitives; license allowlist (MIT/BSD-2/BSD-3/Apache-2.0/PSF) over the full tree; SBOM at `08-config/SBOM.json` marking direct vs transitive. | HIGH | SPEC §4 NFR-07 / SRS §4 NFR-07 | DRAFT | AC-N7.1..AC-N7.4 (4 ACs); evidence `SPEC.md` §8 #22; risk R11; type=`licensing`. |
| NFR-08 | Mutation — `features.mutation_testing: true` in `.methodology/harness_config.json`; mutation score ≥ 70 over `service/` + `repository/`. | MEDIUM | SPEC §4 NFR-08 / SRS §4 NFR-08 | DRAFT | AC-N8.1..AC-N8.2 (2 ACs); evidence `SPEC.md` §8 #24; type=`mutation`. |
| NFR-09 | Testability (zero-skip iron rule) — 0 skipped, 0 assertion-free tests, no `--ignore`/`-k`/`--deselect`/`collect_ignore` exclusion; FR-07 migration tested against a real SQLite file. | HIGH | SPEC §4 NFR-09 / SRS §4 NFR-09 | DRAFT | AC-N9.1..AC-N9.4 (4 ACs); evidence `SPEC.md` §8 #1; gates `VERIFIED` in this matrix; type=`testability`. |
| NFR-10 | Integration — integration coverage ≥ 80% driven through `httpx.AsyncClient(transport=ASGITransport(app))`; every error code exercised. | HIGH | SPEC §4 NFR-10 / SRS §4 NFR-10 | DRAFT | AC-N10.1..AC-N10.3 (3 ACs); evidence `SPEC.md` §8 #3; type=`integration`. |
| NFR-11 | Maintainability — MI ≥ 80 (LLOC-weighted); per-function CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines per API handler. | MEDIUM | SPEC §4 NFR-11 / SRS §4 NFR-11 | DRAFT | AC-N11.1..AC-N11.4 (4 ACs); type=`maintainability`. |
| NFR-12 | Verifiability — `make verify-system` chains `alembic upgrade head` → full suite → health smoke → downgrade/upgrade round-trip; exit 0 and `verify-system: PASS` on stdout. | HIGH | SPEC §4 NFR-12 / SRS §4 NFR-12 | DRAFT | AC-N12.1 (1 AC); evidence `SPEC.md` §8 #27; type=`verifiability`. |

> **Total ACs**: **92** (FR: 55 ACs across 10 rows — FR-01:7, FR-02:6, FR-03:7, FR-04..FR-10:5 each → 7+6+7+5*7 = **55**; NFR: 37 ACs across 12 rows — NFR-01:3, NFR-02:4, NFR-03:4, NFR-04:3, NFR-05:2, NFR-06:3, NFR-07:4, NFR-08:2, NFR-09:4, NFR-10:3, NFR-11:4, NFR-12:1 → **37**. Grand total: **55 + 37 = 92 ACs**.) Note: the §5 AC Summary table in `SRS.md` enumerates 27 machine commands which is a separate, command-level view (`SPEC.md` §8), not an AC-level view.

---

## Spec ↔ Design Module Mapping (SRS §10 → SAD §5 SAB)

| ID | Module(s) declared in `SRS.md` §10 `implementation_functions` | Layer (`SAD.md` §2 / §5) | High-Risk Module (`SAD.md` §5) | SAB `fr_module_traceability` Entry |
|----|---------------------------------------------------------------|--------------------------|-------------------------------|------------------------------------|
| FR-01 | `taskq_api.api.tasks.{create_task,get_task,list_tasks,delete_task}`; `taskq_api.service.tasks.{create_task,get_task,list_tasks,delete_task}`; `taskq_api.repository.task_repo.{create,get,list_paginated,delete}` | api / service / repository | yes (api) | `taskq_api.api.tasks` |
| FR-02 | `taskq_api.api.tasks.{run_task,list_runs}`; `taskq_api.service.runner.run`; `taskq_api.service.tasks.schedule_run`; `taskq_api.repository.task_repo.insert_run` | api / service / repository | yes (service/runner) | `taskq_api.service.runner` |
| FR-03 | `taskq_api.service.auth.{hash_key,verify_key}`; `taskq_api.repository.key_repo.{create,get_by_hash,revoke}`; `taskq_api.__main__.key_create` | service / repository | yes (service/auth) | `taskq_api.service.auth` |
| FR-04 | `taskq_api.api.deps.require_scope`; `taskq_api.service.auth.check_scope` | api / service | yes (api/deps — single-dependency invariant) | `taskq_api.api.deps` |
| FR-05 | `taskq_api.api.deps.rate_limit`; `taskq_api.service.ratelimit.consume`; `taskq_api.repository.rate_repo.take_token` | api / service / repository | yes (repository/rate_repo — race) | `taskq_api.service.ratelimit` |
| FR-06 | `taskq_api.repository.session.{session_scope,engine}` | repository | yes (repository/session — transaction boundary) | `taskq_api.repository.session` |
| FR-07 | `migrations.versions.{v1_initial,v2_tags,v3_split_results}` | migrations (side-tree) | yes (every revision is high-risk) | `migrations.versions` |
| FR-08 | `taskq_api.service.runner.{TaskGroupRunner,run_with_timeout,shutdown}` | service | yes (service/runner) | `taskq_api.service.runner` |
| FR-09 | `taskq_api.api.health.{healthz,readyz,metrics}`; `taskq_api.service.health.{db_reachable,alembic_at_head}` | api / service | yes (api/health — fail-closed) | `taskq_api.api.health` |
| FR-10 | `taskq_api.errors.{problem_json,exception_handlers,install_handlers}` | errors (independence module) | yes (errors — leak guard) | `taskq_api.errors` |
| NFR-01 | (cross-cutting) — `taskq_api.repository.task_repo.get`, `taskq_api.repository.task_repo.list_paginated` must not N+1; benchmark harness `tests/performance/*` | repository | yes (perf budget) | cross-references FR-01 / FR-06 |
| NFR-02 | (cross-cutting grep) — whole `03-development/src/` tree; `taskq_api.service.auth.verify_key`; `taskq_api.errors` | all layers | yes (forensic grep + bandit) | cross-references FR-02 / FR-03 / FR-04 / FR-10 |
| NFR-03 | (cross-cutting) — `taskq_api.repository.session.session_scope`; `taskq_api.service.runner`; `migrations.versions`; `taskq_api.api.health.readyz` | all layers | yes | cross-references FR-06 / FR-07 / FR-08 / FR-09 |
| NFR-04 | (cross-cutting) — `taskq_api.repository.session.engine`; `taskq_api.__main__.key_create`; `taskq_api.errors`; `taskq_api.api.health.metrics` | all layers | yes (DB-URL leak guard) | cross-references FR-03 / FR-09 / FR-10 |
| NFR-05 | (cross-cutting) — every public symbol under `taskq_api.*`; FastAPI route `summary`+`description` in `taskq_api.app` | all layers | no | cross-references FR-01..FR-10 |
| NFR-06 | (architecture-level) — `.importlinter` at repo root; enforced across `taskq_api.api > service > repository > models` | all layers | yes (architecture) | cross-references FR-06 |
| NFR-07 | (config-level) — `requirements.txt`, `requirements.lock`, `08-config/SBOM.json`; `taskq_api.repository.session.engine` | config / repository | no | cross-references FR-06 |
| NFR-08 | (test infra) — `.methodology/harness_config.json`; scope limited to `taskq_api.service` + `taskq_api.repository` | test infra | no (mutation surface limited) | cross-references FR-01..FR-08 |
| NFR-09 | (test infra) — `03-development/tests/` tree; `tests/integration/test_migration_round_trip.py` | test infra | no | cross-references FR-07 / NFR-12 |
| NFR-10 | (test infra) — `03-development/tests/integration/` | test infra | no | cross-references FR-01..FR-10 |
| NFR-11 | (code-level) — `taskq_api.api.*` handlers ≤ 40 lines; `taskq_api.*` per-file ≤ 400; project MI ≥ 80 | all layers | no | cross-references FR-01..FR-10 |
| NFR-12 | (build infra) — `Makefile` `verify-system` target; `migrations.versions`; `tests/integration/test_verify_system.py` | build infra | yes (`verify-system` is the gate) | cross-references FR-07 / FR-09 |

---

## Spec ↔ Code Function Mapping (SRS §10 `implementation_functions` ↔ Code)

> Source: `01-requirements/SRS.md` §10 FR Block (`functional_requirements[].implementation_functions` and `non_functional_requirements[].test_method`). The code path is the authoritative binding; the test path is the verification binding. Code paths are under `03-development/src/taskq_api/` unless prefixed with `migrations.` or `taskq_api.__main__`.

| ID | Code File | Function/Class | Module Layer | Status (machine-refreshed) |
|----|-----------|----------------|--------------|----------------------------|
| FR-01 | `taskq_api/api/tasks.py` | `create_task`, `get_task`, `list_tasks`, `delete_task` | api | DRAFT |
| FR-01 | `taskq_api/service/tasks.py` | `create_task`, `get_task`, `list_tasks`, `delete_task` | service | DRAFT |
| FR-01 | `taskq_api/repository/task_repo.py` | `create`, `get`, `list_paginated`, `delete` | repository | DRAFT |
| FR-02 | `taskq_api/api/tasks.py` | `run_task`, `list_runs` | api | DRAFT |
| FR-02 | `taskq_api/service/runner.py` | `run` | service | DRAFT |
| FR-02 | `taskq_api/service/tasks.py` | `schedule_run` | service | DRAFT |
| FR-02 | `taskq_api/repository/task_repo.py` | `insert_run` | repository | DRAFT |
| FR-03 | `taskq_api/service/auth.py` | `hash_key`, `verify_key` | service | DRAFT |
| FR-03 | `taskq_api/repository/key_repo.py` | `create`, `get_by_hash`, `revoke` | repository | DRAFT |
| FR-03 | `taskq_api/__main__.py` | `key_create` | entry point | DRAFT |
| FR-04 | `taskq_api/api/deps.py` | `require_scope` | api (deps) | DRAFT |
| FR-04 | `taskq_api/service/auth.py` | `check_scope` | service | DRAFT |
| FR-05 | `taskq_api/api/deps.py` | `rate_limit` | api (deps) | DRAFT |
| FR-05 | `taskq_api/service/ratelimit.py` | `consume` | service | DRAFT |
| FR-05 | `taskq_api/repository/rate_repo.py` | `take_token` | repository | DRAFT |
| FR-06 | `taskq_api/repository/session.py` | `session_scope`, `engine` | repository | DRAFT |
| FR-07 | `migrations/versions/v1_initial.py` | `upgrade`, `downgrade` | migrations | DRAFT |
| FR-07 | `migrations/versions/v2_tags.py` | `upgrade`, `downgrade` | migrations | DRAFT |
| FR-07 | `migrations/versions/v3_split_results.py` | `upgrade`, `downgrade` (data-moving) | migrations | DRAFT |
| FR-08 | `taskq_api/service/runner.py` | `TaskGroupRunner`, `run_with_timeout`, `shutdown` | service | DRAFT |
| FR-09 | `taskq_api/api/health.py` | `healthz`, `readyz`, `metrics` | api | DRAFT |
| FR-09 | `taskq_api/service/health.py` | `db_reachable`, `alembic_at_head` | service | DRAFT |
| FR-10 | `taskq_api/errors.py` | `problem_json`, `exception_handlers`, `install_handlers` | errors (independence) | DRAFT |

---

## Code ↔ Test Mapping (verified_by per AC)

> Source: `01-requirements/SRS.md` §3 / §4 "decided by" clauses; cross-referenced to `02-architecture/TEST_SPEC.md` test cases and `TEST_INVENTORY.yaml` `test_inventory.tests`. Each row names the **primary** verifier; FR/NFR rows with multiple ACs may list one or more test functions per row.

| ID | AC Range | Test File | Test Function(s) | Coverage Status |
|----|----------|-----------|------------------|-----------------|
| FR-01 | AC-1.1..AC-1.7 | `tests/integration/test_tasks_crud.py` | `test_post_task_returns_201_with_id`, `test_post_task_validation_violations_returns_422`, `test_get_task_unknown_id_returns_404`, `test_post_task_duplicate_name_returns_409`, `test_list_task_limit_above_200_returns_422`, `test_list_pagination_is_cursor_based`, `test_delete_task_removes_results_in_same_transaction` | DRAFT |
| FR-02 | AC-2.1..AC-2.6 | `tests/integration/test_task_run.py`; `tests/unit/test_runner_subprocess.py` | `test_post_run_returns_202_with_run_id`, `test_shell_true_absent_from_src_tree`, `test_state_machine_transitions`, `test_run_writes_task_results_row`, `test_timeout_kills_child_no_orphan`, `test_get_runs_returns_history_newest_first` | DRAFT |
| FR-03 | AC-3.1..AC-3.7 | `tests/integration/test_auth.py`; `tests/unit/test_auth_compare.py`; `tests/unit/test_key_create.py` | `test_missing_api_key_returns_401`, `test_invalid_api_key_returns_401`, `test_api_keys_table_holds_no_plaintext`, `test_key_compare_uses_hmac_compare_digest`, `test_revoked_key_treated_as_invalid`, `test_key_create_prints_plaintext_once_persists_hash`, `test_health_endpoints_no_auth_required` | DRAFT |
| FR-04 | AC-4.1..AC-4.5 | `tests/integration/test_authz.py`; `tests/unit/test_authz.py`; `tests/unit/test_single_authz_dependency.py` | `test_read_scope_post_tasks_returns_403`, `test_write_scope_delete_returns_403_no_resource_leak`, `test_admin_scope_delete_succeeds`, `test_single_authz_dependency_used_by_every_v1_route`, `test_scope_hierarchy_admin_satisfies_write` | DRAFT |
| FR-05 | AC-5.1..AC-5.5 | `tests/integration/test_ratelimit.py`; `tests/unit/test_ratelimit.py`; `tests/integration/test_health.py` | `test_burst_over_limit_returns_429_with_retry_after`, `test_rate_limit_recovers_after_refill`, `test_bucket_state_shared_across_workers`, `test_bucket_update_uses_row_level_lock`, `test_health_endpoints_exempt_from_rate_limit` | DRAFT |
| FR-06 | AC-6.1..AC-6.5 | `tests/unit/test_session.py`; `tests/unit/test_no_string_sql.py`; `tests/unit/test_engine.py`; `tests/performance/test_list_no_n_plus_one.py`; `lint-imports` (CLI) | `test_one_session_per_request_context_manager`, `test_no_string_concatenated_sql_in_src`, `test_engine_pool_config_matches_env`, `test_list_endpoint_sql_count_is_constant`; `lint-imports` exit 0 | DRAFT |
| FR-07 | AC-7.1..AC-7.5 | `tests/integration/test_migration_round_trip.py`; `tests/unit/test_migrations.py` | `test_upgrade_downgrade_base_clean`, `test_v3_data_migration_round_trip_byte_identical`, `test_no_destructive_drop_table_shortcuts`, `test_every_revision_downgrade_works`, `test_v2_unique_index_survives_round_trip` | DRAFT |
| FR-08 | AC-8.1..AC-8.5 | `tests/unit/test_runner.py`; `tests/unit/test_runner_cancellation.py`; `tests/integration/test_task_run.py`; `tests/integration/test_shutdown_drain.py`; `tests/performance/test_runner_concurrency_cap.py` | `test_runner_uses_task_group`, `test_concurrency_capped_at_max_concurrent`, `test_timeout_kills_child_no_orphan` (shared with FR-02), `test_shutdown_drains_inflight_within_budget`, `test_cancelled_error_propagates_not_swallowed` | DRAFT |
| FR-09 | AC-9.1..AC-9.5 | `tests/integration/test_health.py`; `tests/integration/test_metrics.py` | `test_healthz_returns_200_when_alive`, `test_readyz_returns_503_when_db_down`, `test_readyz_returns_503_when_migration_behind_head`, `test_readyz_returns_200_when_healthy`, `test_metrics_returns_required_series` | DRAFT |
| FR-10 | AC-10.1..AC-10.5 | `tests/integration/test_error_contract.py` | `test_non_2xx_content_type_is_problem_json`, `test_problem_json_fields`, `test_500_detail_no_internals`, `test_correlation_id_in_header_and_logs`, `test_each_error_code_exercised` | DRAFT |
| NFR-01 | AC-N1.1..AC-N1.3 | `tests/performance/test_get_by_id_latency.py`; `tests/performance/test_list_latency.py`; `tests/performance/test_list_no_n_plus_one.py` | `test_get_by_id_p95_under_30ms_at_10k`, `test_list_p95_under_80ms_at_10k`, `test_list_endpoint_sql_count_is_constant` | DRAFT |
| NFR-02 | AC-N2.1..AC-N2.4 | `tests/unit/test_security_scan.py`; `tests/unit/test_bandit.py`; `tests/integration/test_auth.py` | `test_no_shell_eval_exec_in_src`, `test_no_string_concatenated_sql_in_src`, `test_api_keys_table_holds_no_plaintext` (shared with FR-03), `test_bandit_zero_high_zero_medium` | DRAFT |
| NFR-03 | AC-N3.1..AC-N3.4 | `tests/unit/test_error_handling.py`; `tests/unit/test_runner_cancellation.py`; `tests/integration/test_health.py`; `tests/integration/test_migration_round_trip.py` | `test_no_bare_except_in_src`, `test_cancelled_error_propagates_not_swallowed` (shared with FR-08), `test_readyz_returns_503_when_db_down` (shared with FR-09), `test_migration_failure_rolls_back` | DRAFT |
| NFR-04 | AC-N4.1..AC-N4.3 | `tests/unit/test_redaction.py`; `tests/unit/test_key_create.py` | `test_redaction_replaces_matching_lines`, `test_db_url_password_never_logged_or_emitted`, `test_key_create_prints_plaintext_once_persists_hash` (shared with FR-03) | DRAFT |
| NFR-05 | AC-N5.1..AC-N5.2 | `tests/unit/test_docstrings.py`; `tests/integration/test_openapi.py` | `test_public_symbols_have_fr_or_nfr_docstring`, `test_every_endpoint_has_summary_and_description` | DRAFT |
| NFR-06 | AC-N6.1..AC-N6.3 | `tests/unit/test_lint_imports.py`; `lint-imports` (CLI) | `test_lint_imports_exit_zero`, `test_sqlalchemy_forbidden_outside_repository`, `test_importlinter_file_present_with_both_contracts` | DRAFT |
| NFR-07 | AC-N7.1..AC-N7.4 | `tests/unit/test_requirements_pin.py`; `tests/unit/test_requirements_lock.py`; `tests/unit/test_license_allowlist.py`; `tests/unit/test_sbom.py` | `test_requirements_txt_pinned_with_double_equals`, `test_requirements_lock_locks_transitives`, `test_every_dep_license_in_allowlist`, `test_sbom_has_required_fields_per_dep` | DRAFT |
| NFR-08 | AC-N8.1..AC-N8.2 | `tests/unit/test_harness_config.py`; `tests/unit/test_mutation_score.py`; `mutmut` (CLI) | `test_mutation_testing_feature_enabled`, `test_mutation_score_at_least_70` | DRAFT |
| NFR-09 | AC-N9.1..AC-N9.4 | `tests/unit/test_zero_skip.py`; `tests/unit/test_assertions.py`; `tests/integration/test_migration_round_trip.py`; `tests/unit/test_no_exclusion.py` | `test_pytest_skipped_count_zero`, `test_zero_assertion_free_tests`, `test_v3_data_migration_round_trip_byte_identical` (shared with FR-07), `test_no_test_exclusion_paths` | DRAFT |
| NFR-10 | AC-N10.1..AC-N10.3 | `tests/unit/test_integration_coverage.py`; `tests/unit/test_integration_uses_asgi_transport.py`; `tests/integration/test_error_contract.py` | `test_integration_coverage_at_least_80_percent`, `test_integration_tests_use_asgi_transport_not_direct_handler`, `test_each_error_code_exercised` (shared with FR-10) | DRAFT |
| NFR-11 | AC-N11.1..AC-N11.4 | `tests/unit/test_readability.py` | `test_project_mi_at_least_80`, `test_no_function_cc_above_10`, `test_file_and_dir_size_limits`, `test_api_handlers_within_40_lines` | DRAFT |
| NFR-12 | AC-N12.1 | `tests/integration/test_verify_system.py`; `make verify-system` (CLI) | `test_make_verify_system_exits_zero_and_prints_pass` | DRAFT |

---

## FR ↔ Test Inventory Cross-Reference (`TEST_INVENTORY.yaml`)

> The `tests:` enumeration in `TEST_INVENTORY.yaml` is the **P1 Naming Authority** and the 1:1 mapping source for `TEST_SPEC.md`. The table below shows a representative cross-reference drawn from the populated `TEST_INVENTORY.yaml` (92 entries); the authoritative names per AC are those listed in the `Code ↔ Test Mapping` table above and the "decided by" clauses in `SRS.md` §3 / §4, and the full 92-row enumeration lives in `TEST_INVENTORY.yaml` (do not duplicate it here).

| tc_id | fr / nfr | ac | layer | test_function | test_file | Cross-refs |
|-------|----------|----|-------|---------------|-----------|-----------|
| TC-FR01-01 | FR-01 | AC-1.1 | integration | `test_post_task_returns_201_with_id` | `tests/integration/test_tasks_crud.py` | (matches FR-01 row above; 7 ACs in `SRS.md` FR-01) |
| TC-FR01-02 | FR-01 | AC-1.2 | integration | `test_post_task_validation_violations_returns_422` | `tests/integration/test_tasks_crud.py` | cross_ref_nfrs: [NFR-10] |
| TC-N01-03 | NFR-01 | AC-N1.3 | performance | `test_list_endpoint_sql_count_is_constant` | `tests/performance/test_list_no_n_plus_one.py` | cross_ref_frs: [FR-06] |
| TC-N02-01 | NFR-02 | AC-N2.1 | unit | `test_no_shell_eval_exec_in_src` | `tests/unit/test_security_scan.py` | (matches NFR-02 row above) |

> **Coverage summary (from `TEST_INVENTORY.yaml#coverage_summary`)**: total_test_cases = 92; by_fr: FR-01=7, FR-02=6, FR-03=7, FR-04=5, FR-05=5, FR-06=5, FR-07=5, FR-08=5, FR-09=5, FR-10=5, NFR-01=3, NFR-02=4, NFR-03=4, NFR-04=3, NFR-05=2, NFR-06=3, NFR-07=4, NFR-08=2, NFR-09=4, NFR-10=3, NFR-11=4, NFR-12=1; by_layer: integration=47, unit=39, performance=5, static=1. The authoritative per-AC counts are the **55 FR ACs + 37 NFR ACs = 92 ACs** tracked in the `FR ↔ NFR ↔ Spec Mapping` table; the 4 rows above are a representative slice (Phase-2 `derive_test_cases.md` expands to the full 92 in `TEST_SPEC.md`).

---

## Risk ↔ FR / NFR Mapping (`SPEC.md` §8 / §9 → `RISK_REGISTER.md`)

> Source: `01-requirements/SRS.md` §8 transcribed from `SPEC.md` §9.

| Risk ID | Risk | Primary FR/NFR | Mitigation Row | Status |
|---------|------|----------------|----------------|--------|
| R1 | v3 data migration loses data | FR-07 | round-trip test against real DB, column-by-column (`tests/integration/test_migration_round_trip.py::test_v3_data_migration_round_trip_byte_identical`) | DRAFT |
| R2 | SQL injection | NFR-02 | no string concatenation + ORM/parameterised + grep gate (`tests/unit/test_security_scan.py`) | DRAFT |
| R3 | API key leak | FR-03 / NFR-04 | hashed storage + constant-time compare + printed once (`tests/integration/test_auth.py`, `tests/unit/test_key_create.py`) | DRAFT |
| R4 | 403 reveals resource existence | FR-04 | authorise before lookup (`tests/integration/test_authz.py::test_write_scope_delete_returns_403_no_resource_leak`) | DRAFT |
| R5 | N+1 collapses on large table | NFR-01 | explicit eager loading + SQL count assertion (`tests/performance/test_list_no_n_plus_one.py`) | DRAFT |
| R6 | error body leaks internals | FR-10 / NFR-02 | fixed RFC 7807 fields + detail allowlist (`tests/integration/test_error_contract.py::test_500_detail_no_internals`) | DRAFT |
| R7 | `CancelledError` swallowed → shutdown hangs | FR-08 / NFR-03 | explicit ban + assertion (`tests/unit/test_runner_cancellation.py`) | DRAFT |
| R8 | task timeout leaves orphan processes | FR-08 | `kill()` + `await wait()` (`tests/integration/test_task_run.py::test_timeout_kills_child_no_orphan`) | DRAFT |
| R9 | deploy without migration | FR-09 | `/readyz` fail closed (`tests/integration/test_health.py::test_readyz_returns_503_when_migration_behind_head`) | DRAFT |
| R10 | connection pool exhaustion | FR-06 / FR-08 | `pool_pre_ping` + concurrency cap (`tests/unit/test_engine.py`) | DRAFT |
| R11 | transitive dep with incompatible license | NFR-07 | lock file + whole-tree scan (`tests/unit/test_license_allowlist.py`, `tests/unit/test_sbom.py`) | DRAFT |
| R12 | rate bucket race over-admits | FR-05 | single transaction + row-level lock (`tests/unit/test_ratelimit.py::test_bucket_update_uses_row_level_lock`) | DRAFT |

---

## Completeness Verification

> Targets are derived from `SPEC.md` §8 verification commands and `SRS.md` §5 AC Summary; **Actual** values are produced by `harness_cli.py advance-phase` / `build_traceability` / `pytest --cov` runs and stamped here on each phase exit.

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR ↔ SRS mapping | 100% (10/10 FRs) | 10/10 (FR-01..FR-10 all linked to `SRS.md` §3) | DRAFT |
| NFR ↔ SRS mapping | 100% (12/12 NFRs) | 12/12 (NFR-01..NFR-12 all linked to `SRS.md` §4) | DRAFT |
| SRS ↔ Code mapping | 100% (every `implementation_functions` entry linked) | 23 module-level code rows across 8 files | DRAFT |
| SRS ↔ Test mapping | 100% (every AC has a "decided by" test function) | 92 ACs each named in `SRS.md` §3 / §4 | DRAFT |
| FR ↔ AC coverage | 100% (every FR has ≥1 AC) | 10/10 (FR-01:7, FR-02:6, FR-03:7, FR-04..FR-10:5 each = 55 total FR ACs) | DRAFT |
| NFR ↔ AC coverage | 100% (every NFR has ≥1 AC) | 12/12 (NFR-01:3, NFR-02:4, NFR-03:4, NFR-04:3, NFR-05:2, NFR-06:3, NFR-07:4, NFR-08:2, NFR-09:4, NFR-10:3, NFR-11:4, NFR-12:1 = 37 total NFR ACs) | DRAFT |
| Total ACs (FR + NFR) | matches `SRS.md` §3 / §4 + `SPEC.md` §3 / §4 | 55 + 37 = 92 ACs | DRAFT |
| Test coverage — unit | ≥ 80% (`SPEC.md` §8 #2) | (machine-filled) | DRAFT |
| Test coverage — integration | ≥ 80% (`SPEC.md` §8 #3 / NFR-10) | (machine-filled) | DRAFT |
| Mutation score (`service/` + `repository/`) | ≥ 70 (NFR-08) | (machine-filled) | DRAFT |
| `lint-imports` exit code | 0 (NFR-06) | (machine-filled) | DRAFT |
| `bandit -r 03-development/src/` HIGH/MEDIUM | 0 / 0 (NFR-02) | (machine-filled) | DRAFT |
| `pytest -q` skipped count | 0 (NFR-09) | (machine-filled) | DRAFT |
| `make verify-system` exit code + stdout | 0 + `verify-system: PASS` (NFR-12) | (machine-filled) | DRAFT |

> NFR-09 (`TRACEABILITY_MATRIX.md`'s `VERIFIED` is set only when a test actually runs and passes) is the rule governing the Status column above: `VERIFIED` is set by the `advance-phase` machine refresh after a green test run, not by a hand-edit.

---

## ASPICE Compliance Mapping

| ASPICE Capability | Status | Evidence in this matrix |
|-------------------|--------|-------------------------|
| SWE.3.B.SP1 Task-to-work-product traceability | DRAFT | `FR ↔ NFR ↔ Spec Mapping` table links every FR/NFR to its `SPEC.md`/`SRS.md` section and verification file. |
| SWE.3.B.SP2 Bidirectional traceability | DRAFT | Reverse path: every test function in `Code ↔ Test Mapping` traces back to its AC and FR/NFR; every implementation module in `Spec ↔ Code Function Mapping` traces back to its FR/NFR. |
| SWE.3.B.SP3 Traceability consistency | DRAFT | `Risk ↔ FR / NFR Mapping` shows every risk (R1..R12) maps to ≥1 FR/NFR; every FR/NFR has ≥1 AC; every AC has ≥1 test. |
| SWE.5.B.SP1 Verification consistency | DRAFT | `Completeness Verification` table binds each `SPEC.md` §8 verification command to a target metric; machine-refresh enforces. |
| SYS.4.B.SP1 System requirements traceability | DRAFT | `FR ↔ NFR ↔ Spec Mapping` rows are sourced from `SPEC.md` (the system spec) and `SRS.md` (the software spec); both are linked. |

---

## Update Log

| Date | Change | By |
|------|--------|----|
| 2026-08-31 | Initial creation (template placeholders) | harness-cli |
| 2026-08-31 | Populated FR ↔ NFR ↔ Spec, Spec ↔ Code, Code ↔ Test, Risk, Completeness, ASPICE sections from `SRS.md`, `SPEC.md`, `TEST_INVENTORY.yaml`, `SAD.md` §5 SAB block, `SPEC.md` §9 risks | Agent A (Sub-Task 3/4 Round 1) |
