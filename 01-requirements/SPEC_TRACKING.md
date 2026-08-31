# Specification Tracking Matrix — taskq-api

> Human-readable view over the requirement set. **Not the SSOT**: the
> authoritative status is `build_traceability`'s scan / `quality_manifest.json`,
> and the canonical requirement source is `SPEC.md` (project root), transcribed
> into `01-requirements/SRS.md`.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Created: 2026-08-31

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework / Notes);
> leave Status to refresh itself (a hand-edit is overwritten on the next advance).

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| FR-01 | Task resource CRUD API — POST/GET/LIST/DELETE `/v1/tasks`, cursor pagination (limit 50, cap 200); 422 validation / 404 unknown id / 409 duplicate name | API surface | AC-1.1..AC-1.7 via `tests/integration/test_tasks_crud.py` | DRAFT | Source: `SPEC.md` §3 FR-01; status map `SPEC.md` §7 |
| FR-02 | Task execution endpoint — `POST /v1/tasks/{id}/run` → 202 + `run_id`; `asyncio.create_subprocess_exec`, `shell=True` forbidden; results into `task_results`; `GET /v1/tasks/{id}/runs` history | Execution | AC-2.1..AC-2.6 via `tests/integration/test_task_run.py`, `tests/unit/test_runner_subprocess.py` | DRAFT | Source: `SPEC.md` §3 FR-02; timeout behaviour shared with FR-08 |
| FR-03 | API key authentication — `X-API-Key` on every `/v1/*`; SHA-256 hashed storage, `hmac.compare_digest`; `revoked_at` invalidation; plaintext printed once at `key create` | Security / authn | AC-3.1..AC-3.7 via `tests/integration/test_auth.py`, `tests/unit/test_auth_compare.py`, `tests/unit/test_key_create.py` | DRAFT | Source: `SPEC.md` §3 FR-03; evidence `SPEC.md` §8 #18; health endpoints exempt (FR-09) |
| FR-04 | Scope authorisation — `read` < `write` < `admin` hierarchical; 403 must not disclose resource existence; one shared FastAPI dependency for every `/v1` route | Security / authz | AC-4.1..AC-4.5 via `tests/integration/test_authz.py`, `tests/unit/test_authz.py`, `tests/unit/test_single_authz_dependency.py` | DRAFT | Source: `SPEC.md` §3 FR-04; leak guard `SPEC.md` §8 #6; risk R4 |
| FR-05 | Rate limiting — per-token DB-backed token bucket (`TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC`); 429 + `Retry-After`; row-level lock in a single transaction; health endpoints exempt | Traffic control | AC-5.1..AC-5.5 via `tests/integration/test_ratelimit.py`, `tests/unit/test_ratelimit.py`, `tests/integration/test_health.py` | DRAFT | Source: `SPEC.md` §3 FR-05; burst evidence `SPEC.md` §8 #9; risk R12 |
| FR-06 | Persistence layer and transaction boundaries — repository-only `sqlalchemy`; one `Session` per request via context manager; explicit eager loading (no N+1); `pool_pre_ping = True` | Data access | AC-6.1..AC-6.5 via `lint-imports`, `tests/unit/test_session.py`, `tests/unit/test_no_string_sql.py`, `tests/unit/test_engine.py`, `tests/performance/test_list_no_n_plus_one.py` | DRAFT | Source: `SPEC.md` §3 FR-06; couples to NFR-01 / NFR-02 / NFR-06 |
| FR-07 | Schema migration — Alembic v1→v2→v3; v3 moves `tasks.result_json` into `task_results` with real data migration; every revision reversible; no `op.execute("DROP TABLE ...")` shortcut | Migration | AC-7.1..AC-7.5 via `tests/integration/test_migration_round_trip.py`, `tests/unit/test_migrations.py` | DRAFT | Source: `SPEC.md` §3 FR-07; round-trip evidence `SPEC.md` §8 #12/#13; risk R1; real-DB clause NFR-09 |
| FR-08 | Async runner — `asyncio.TaskGroup`; cap `TASKQ_MAX_CONCURRENT`; graceful drain to `TASKQ_DRAIN_TIMEOUT` (excess `interrupted`); timeout `kill()` + `await wait()`; `CancelledError` propagates | Concurrency | AC-8.1..AC-8.5 via `tests/unit/test_runner.py`, `tests/unit/test_runner_cancellation.py`, `tests/integration/test_task_run.py`, `tests/integration/test_shutdown_drain.py`, `tests/performance/test_runner_concurrency_cap.py` | DRAFT | Source: `SPEC.md` §3 FR-08; drain evidence `SPEC.md` §8 #25; risks R7/R8 |
| FR-09 | Health and observability — `/healthz` 200 liveness; `/readyz` 503 when DB unreachable **or** `alembic current` != head (fail closed); `/v1/metrics` (admin) counts + latency percentiles + rate-limit rejects | Operability | AC-9.1..AC-9.5 via `tests/integration/test_health.py`, `tests/integration/test_metrics.py` | DRAFT | Source: `SPEC.md` §3 FR-09; evidence `SPEC.md` §8 #10/#11; risk R9 |
| FR-10 | Error contract (RFC 7807) — all non-2xx `application/problem+json` with `type`/`title`/`status`/`detail`/`instance`/`correlation_id`; no internals in `detail`; `X-Correlation-Id` header linked to logs; full code map | Error contract | AC-10.1..AC-10.5 via `tests/integration/test_error_contract.py` | DRAFT | Source: `SPEC.md` §3 FR-10; evidence `SPEC.md` §8 #5–#11/#19; risk R6 |

## Non-Functional Status

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| NFR-01 | `GET /v1/tasks/{id}` p95 < 30ms and `GET /v1/tasks?limit=50` p95 < 80ms at 10k rows; list SQL statement count constant | performance | AC-N1.1..AC-N1.3 via `tests/performance/test_get_by_id_latency.py`, `test_list_latency.py`, `test_list_no_n_plus_one.py` | DRAFT | Source: `SPEC.md` §4 NFR-01; evidence `SPEC.md` §8 #14/#15; risk R5 |
| NFR-02 | `shell=True` / `eval(` / `exec(` and string-concatenated SQL forbidden; hashed keys + constant-time compare; 403 leaks nothing; CORS deny-by-default; bandit 0 HIGH / 0 MEDIUM | security | AC-N2.1..AC-N2.4 via `tests/unit/test_security_scan.py`, `tests/unit/test_bandit.py`, `tests/integration/test_auth.py` | DRAFT | Source: `SPEC.md` §4 NFR-02; evidence `SPEC.md` §8 #16/#17/#18/#23; risk R2 |
| NFR-03 | Explicit transaction boundaries; no bare `except:` / `except Exception: pass`; `CancelledError` re-raised; timeout kills child; failed migration rolls back | reliability (dimension `error_handling`) | AC-N3.1..AC-N3.4 via `tests/unit/test_error_handling.py`, `tests/unit/test_runner_cancellation.py`, `tests/integration/test_health.py`, `tests/integration/test_migration_round_trip.py` | DRAFT | Source: `SPEC.md` §4 NFR-03; risks R7/R8; vocabulary split tracked as Open Issue NFR-99 |
| NFR-04 | Secret redaction before write/emit (`[REDACTED]` whole line); DB URL password never in logs, errors, or `/v1/metrics`; key plaintext never persisted | security | AC-N4.1..AC-N4.3 via `tests/unit/test_redaction.py`, `tests/unit/test_key_create.py` | DRAFT | Source: `SPEC.md` §4 NFR-04; evidence `SPEC.md` §8 #20; risk R3 |
| NFR-05 | 100% public docstrings carrying `[FR-XX]`/`[NFR-XX]`; every endpoint has `summary` + `description` in `/openapi.json` | documentation | AC-N5.1..AC-N5.2 via `tests/unit/test_docstrings.py`, `tests/integration/test_openapi.py` | DRAFT | Source: `SPEC.md` §4 NFR-05 |
| NFR-06 | `.importlinter` layers contract `api > service > repository > models` plus forbidden contract banning `sqlalchemy` outside `repository/`; `lint-imports` exit 0 | layering | AC-N6.1..AC-N6.3 via `tests/unit/test_lint_imports.py` | DRAFT | Source: `SPEC.md` §4 NFR-06; evidence `SPEC.md` §8 #21; contract downgrade forbidden |
| NFR-07 | `==` pinning + `requirements.lock` for transitives; license allowlist (MIT/BSD-2/BSD-3/Apache-2.0/PSF) over the full tree; SBOM at `08-config/SBOM.json` marking direct vs transitive | licensing | AC-N7.1..AC-N7.4 via `tests/unit/test_requirements_pin.py`, `test_requirements_lock.py`, `test_license_allowlist.py`, `test_sbom.py` | DRAFT | Source: `SPEC.md` §4 NFR-07; evidence `SPEC.md` §8 #22; risk R11 |
| NFR-08 | `features.mutation_testing: true` in `.methodology/harness_config.json`; mutation score ≥ 70 over `service/` + `repository/` | mutation | AC-N8.1..AC-N8.2 via `tests/unit/test_harness_config.py`, `tests/unit/test_mutation_score.py` | DRAFT | Source: `SPEC.md` §4 NFR-08; evidence `SPEC.md` §8 #24 |
| NFR-09 | Zero-skip iron rule — 0 skipped, 0 assertion-free tests, no `--ignore`/`-k`/`--deselect`/`collect_ignore` exclusion; FR-07 migration tested against a real SQLite file | testability | AC-N9.1..AC-N9.4 via `tests/unit/test_zero_skip.py`, `test_assertions.py`, `test_no_exclusion.py`, `tests/integration/test_migration_round_trip.py` | DRAFT | Source: `SPEC.md` §4 NFR-09; evidence `SPEC.md` §8 #1; gates `VERIFIED` in `TRACEABILITY_MATRIX.md` |
| NFR-10 | Integration coverage ≥ 80% driven through `httpx.AsyncClient(transport=ASGITransport(app))`; every error code exercised | integration | AC-N10.1..AC-N10.3 via `tests/unit/test_integration_coverage.py`, `test_integration_uses_asgi_transport.py`, `tests/integration/test_error_contract.py` | DRAFT | Source: `SPEC.md` §4 NFR-10; evidence `SPEC.md` §8 #3 |
| NFR-11 | MI ≥ 80 (LLOC-weighted); per-function CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines per API handler | maintainability | AC-N11.1..AC-N11.4 via `tests/unit/test_readability.py` | DRAFT | Source: `SPEC.md` §4 NFR-11 |
| NFR-12 | `make verify-system` chains `alembic upgrade head` → full suite → health smoke → downgrade/upgrade round-trip; exit 0 and `verify-system: PASS` on stdout | verifiability | AC-N12.1 via `tests/integration/test_verify_system.py` | DRAFT | Source: `SPEC.md` §4 NFR-12; evidence `SPEC.md` §8 #27 |

## Completeness Check

- FR rows: 10 (FR-01..FR-10) — matches the `functional_requirements` array in
  `SRS.md` §10 and the `### FR-NN` headings in `SPEC.md` §3. No gaps, no
  invented IDs.
- NFR rows: 12 (NFR-01..NFR-12) — matches the `non_functional_requirements`
  array in `SRS.md` §10 and `SPEC.md` §4.
- Open issue NFR-99 (`error_handling` dimension vs `reliability` type) is
  tracked in `SRS.md` §7; it is a vocabulary question, not a missing
  requirement, so it holds no row here.
- Every Source cell points at the root `SPEC.md`; no `01-requirements/` prefix
  is used for the canonical spec (that path does not exist).

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-31 | Initial creation (template) | Agent A |
| 2026-08-31 | Populated FR-01..FR-10 and NFR-01..NFR-12 from `SRS.md` / `SPEC.md`; added completeness check | Agent A |
