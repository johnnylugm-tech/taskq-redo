# Software Architecture Document (SAD) — taskq-api

> Round-2 Software Architecture Document for the `taskq-api` Python
> ASGI service (harness-methodology progressive test-bed, round 2 of 3).
> Source of truth for module decomposition, interfaces, NFR handling,
> and the machine-readable SAB / Security contracts the harness
> ingests at later phases.

## 1. Architecture Overview

`taskq-api` is a Python 3.11 ASGI service that exposes a REST task-queue
over HTTP: clients submit, query, and execute shell-command tasks via
`/v1/*` routes; persistence is on SQLAlchemy 2.x ORM (SQLite for
dev/test, PostgreSQL for production); schema evolves through three
Alembic revisions; background execution is in-process via
`asyncio.TaskGroup`; authentication uses SHA-256-hashed `X-API-Key`
tokens with per-key scope authorisation and a DB-backed token-bucket
rate limiter. The whole service is layered `api > service >
repository > models` (mandatory `.importlinter` contract, NFR-06),
with `config` and `errors` as independence modules.

The verification target is the ASGI app — `make verify-system` runs
`alembic upgrade head`, the full test suite, a service-startup smoke
against `/healthz` and `/readyz`, then a `downgrade base → upgrade
head` round-trip (NFR-12). The harness gate reads only that exit code.

### 1.1 System Verification Target

**Makefile target**: `verify-system`
**Exercises**: `taskq_api.app:app` assembled in `app.py`; the Alembic
revision chain (`migrations/versions/v1_initial.py`,
`v2_tags.py`, `v3_split_results.py`) exercised end-to-end; the
background runner (`taskq_api.service.runner`) brought up to drain
in-flight tasks; the auth dependency
(`taskq_api.api.deps.require_scope`) reached from every `/v1` route;
the repository transaction context
(`taskq_api.repository.session.session_scope`) used on every request;
the rate-limit repository
(`taskq_api.repository.rate_repo.take_token`) hit under burst load.

The `verify-system` target must:

1. invoke `python -m taskq_api` (the real delivered entry point) so
   the gate scores modules a test double would otherwise stand in
   for;
2. be able to fail — no `|| true`, no leading `-`, no
   `--exit-zero` flags swallowing non-zero exits;
3. exercise a real acceptance criterion (real SQLite file for the
   migration round-trip, real subprocess for the runner smoke) against
   real dependencies.

## 2. Module Design

### 2.1 Directory Structure Design Principles

> **CRG Architecture Scoring**: Phase 3+ judges code cohesion via
> the Code Review Graph (CRG). CRG groups files by **directory** —
> each directory is one community. The architecture score is the
> fraction of communities that are "healthy" (internal edge density
> ≥ 0.3 AND size ≤ 50 nodes).
>
> **Required edge budget**: To reach cohesion ≥ 0.3 with E external
> edges, you need I ≥ ceil(0.4286 × E) internal edges. Each
> function-body call to a hub function = 1 internal edge. Module-level
> calls create 1 edge per file, but per-function-body calls multiply
> the count.

The `taskq_api` package is split into four community-aligned
directories, each ≤15 files (NFR-11) and each ≤50 nodes:

| Directory | Layer | Role | Files |
|-----------|-------|------|-------|
| `taskq_api.api/` | L4 — HTTP | FastAPI routers + the single auth dependency | `__init__.py`, `deps.py`, `tasks.py`, `health.py` |
| `taskq_api.service/` | L3 — Business | Async runner, auth/scope logic, rate-limit logic | `__init__.py`, `tasks.py`, `runner.py`, `auth.py`, `ratelimit.py` |
| `taskq_api.repository/` | L2 — Persistence | **Only layer permitted to import `sqlalchemy`** (NFR-06) | `__init__.py`, `session.py`, `task_repo.py`, `key_repo.py`, `rate_repo.py` |
| `taskq_api.models/` | L1 — Schema | ORM declarative + pydantic request/response models | `__init__.py`, `orm.py`, `schemas.py` |

Plus two **independence modules** (`config.py`, `errors.py`) at package
root — neither layer may import them transitively into another layer
in a way that crosses the layering contract.

**Hub module per community.** Each layer has a hub module whose
functions are called from sibling function bodies (not just at module
level) so the internal edge budget clears:

- `taskq_api.api.deps` exposes `require_scope`, `rate_limit`,
  `correlation_id_middleware` — called from every router's handler
  bodies.
- `taskq_api.service.tasks` exposes `create_task`, `get_task`,
  `list_tasks`, `delete_task` — called from `runner.run` and from
  `api.tasks` handlers (via the router calling `service.tasks.*`).
- `taskq_api.repository.session` exposes `session_scope` (the
  context manager) and `engine` — every repository function takes a
  session built through it.
- `taskq_api.errors` exposes `problem_json`, `install_handlers`,
  `exception_handlers` — registered as FastAPI handlers and called
  from `app.py` startup.

**Entry point inside a hub directory.** The management entry point
`taskq_api.__main__` lives at package root so its `python -m
taskq_api` form works; it imports both `errors` (problem+json for
the `healthcheck` command) and `repository.session` (for the
`migrate` command's engine), producing cross-cutting internal edges.

**Edge budget sanity check.** The `api` community imports
`fastapi`, `httpx`, `pydantic`, `starlette` (external) but also
calls into `service.*` and `errors.*` from every handler body
(internal) — comfortably above the 0.4286×E internal-edge threshold.
The `service` community imports `asyncio`, `hmac`, `hashlib`,
`shlex` (external) but every function body also calls
`service.tasks` helpers and the `errors` module, producing enough
internal edges.

### 2.2 FR → Module Mapping

Every FR (FR-01..FR-10, enumerated from `SPEC.md §3`) maps to
≥1 module; the mapping below is the canonical binding consumed by
the §5 SAB `fr_module_traceability` block and by
`harness_cli.py check-spec-alignment`.

| FR | Title | Primary modules |
|----|-------|-----------------|
| FR-01 | 任務資源 CRUD API | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.schemas` |
| FR-02 | 任務執行端點 | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo` |
| FR-03 | API Key 認證 | `taskq_api.service.auth`, `taskq_api.repository.key_repo`, `taskq_api.__main__` |
| FR-04 | Scope 授權 | `taskq_api.api.deps.require_scope`, `taskq_api.service.auth.check_scope` |
| FR-05 | 流量控制 | `taskq_api.api.deps.rate_limit`, `taskq_api.service.ratelimit.consume`, `taskq_api.repository.rate_repo.take_token` |
| FR-06 | 持久化層與交易邊界 | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` |
| FR-07 | Schema Migration (Alembic 三步演進) | `migrations/versions/v1_initial`, `migrations/versions/v2_tags`, `migrations/versions/v3_split_results`, `alembic.ini`, `taskq_api.repository.session` |
| FR-08 | 非同步執行器 | `taskq_api.service.runner`, `taskq_api.repository.task_repo` |
| FR-09 | 健康檢查與可觀測性 | `taskq_api.api.health`, `taskq_api.service.health` (db_reachable / alembic_at_head), `taskq_api.repository.session` |
| FR-10 | 錯誤契約 (RFC 7807) | `taskq_api.errors`, `taskq_api.api.deps` (correlation_id middleware), `taskq_api.repository.session` |

### 2.3 `taskq_api.config` (independence — env loader)

| Attribute | Value |
|-----------|-------|
| Responsibility | Loads every `TASKQ_*` env var (12 total, per SPEC §5.1) and exposes typed accessors; no I/O beyond `os.environ` |
| External Interface | `TASKQ_DB_URL`, `TASKQ_DB_POOL_SIZE`, `TASKQ_TASK_TIMEOUT`, `TASKQ_MAX_CONCURRENT`, `TASKQ_DRAIN_TIMEOUT`, `TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC`, `TASKQ_CORS_ORIGINS`, `TASKQ_LOG_LEVEL`, `TASKQ_LOG_FORMAT`, `TASKQ_HOST`, `TASKQ_PORT` |
| Dependencies | stdlib `os` only |

#### Logical Constraints
- Independence module — must not be imported by `models/` or
  `repository/` so that env-var leakage cannot flow into the
  persistence boundary.
- The DB URL value is never logged (NFR-04).

### 2.4 `taskq_api.errors` (independence — RFC 7807 envelope)

| Attribute | Value |
|-----------|-------|
| Responsibility | Builds `application/problem+json` responses; installs FastAPI exception handlers; emits `correlation_id` into both response header and log line |
| External Interface | `problem_json(status, type_uri, title, detail, instance, correlation_id)`, `install_handlers(app)`, `exception_handlers` |
| Dependencies | `fastapi`, `starlette`, `uuid` (stdlib) |

#### Logical Constraints
- Independence module — referenced by every layer that produces an
  error response, but never holds business state.
- `detail` field is filtered through an allowlist before serialisation
  (FR-10 / NFR-02).

### 2.5 `taskq_api.api` (L4 — HTTP)

| Attribute | Value |
|-----------|-------|
| Responsibility | FastAPI routers for `/v1/*`, `/healthz`, `/readyz`; the **single** auth dependency used by every `/v1` route (FR-04) |
| External Interface | routers in `tasks.py`, `health.py`; dependency `require_scope(scope)` and `rate_limit()` in `deps.py`; correlation_id middleware |
| Dependencies | `taskq_api.service.*`, `taskq_api.errors`, `taskq_api.models.schemas`; **must NOT import** `taskq_api.repository` or `sqlalchemy` (NFR-06) |

#### Logical Constraints
- Each handler ≤ 40 lines (NFR-11); business logic lives in `service/`.
- The `deps.require_scope` dependency is the **only** authz decision
  point — `tests/unit/test_single_authz_dependency.py::test_single_authz_dependency_used_by_every_v1_route`
  asserts no handler bypasses it (AC-4.4).

### 2.6 `taskq_api.service` (L3 — Business)

| Attribute | Value |
|-----------|-------|
| Responsibility | Task CRUD orchestration, async subprocess runner, auth/scope decisions, rate-limit consumption |
| External Interface | `tasks.{create,get,list,delete,schedule_run}`, `runner.{TaskGroupRunner,run_with_timeout,shutdown}`, `auth.{hash_key,verify_key,check_scope}`, `ratelimit.consume` |
| Dependencies | `taskq_api.repository.*`, `taskq_api.errors`, `asyncio`, `shlex`, `hashlib`, `hmac`; **must NOT import** `sqlalchemy` directly (NFR-06) |

#### Logical Constraints
- Holds **no** `Session` objects — sessions are passed in or opened
  by `repository.session.session_scope` and consumed before return
  (FR-06).
- `asyncio.CancelledError` is **never** swallowed by `except
  Exception` (NFR-03 / R7).
- Subprocess execution uses
  `asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True`
  is forbidden tree-wide (NFR-02).

### 2.7 `taskq_api.repository` (L2 — Persistence)

| Attribute | Value |
|-----------|-------|
| Responsibility | SQLAlchemy ORM operations; the **only** layer permitted to `import sqlalchemy` (NFR-06) |
| External Interface | `session.{engine,session_scope}`, `task_repo.{create,get,list_paginated,delete,insert_run}`, `key_repo.{create,get_by_hash,revoke}`, `rate_repo.{take_token}` |
| Dependencies | `sqlalchemy`, `taskq_api.models.orm`, `taskq_api.config` (read-only) |

#### Logical Constraints
- Every public function takes a `Session` (or is a context-manager
  factory) and returns ORM-mapped rows or column projections.
- `selectinload` / `joinedload` is **mandatory** for any relationship
  traversal — N+1 is an acceptance failure (NFR-01 / R5).
- `take_token` performs the bucket update under a single transaction
  with row-level lock so two workers cannot over-admit (FR-05 / R12).

### 2.8 `taskq_api.models` (L1 — Schema)

| Attribute | Value |
|-----------|-------|
| Responsibility | SQLAlchemy declarative ORM classes (`tasks`, `api_keys`, `tags`, `task_tags`, `task_results`, `rate_buckets`); pydantic v2 request/response models |
| External Interface | `orm.{Task,ApiKey,Tag,TaskTag,TaskResult,RateBucket}`, `schemas.{TaskCreate,TaskRead,TaskList,RunRead,…}` |
| Dependencies | `sqlalchemy`, `pydantic`, `uuid`, `datetime` (stdlib) |

#### Logical Constraints
- Schema definitions live here; no business logic, no I/O.
- `models/` imports nothing from `repository/`, `service/`, `api/`
  — strict layering.

### 2.9 `taskq_api.__main__` (management entry point)

| Attribute | Value |
|-----------|-------|
| Responsibility | `python -m taskq_api` CLI subcommands: `migrate` (alembic wrapper), `key create --scope <scope>` (FR-03), `healthcheck` |
| External Interface | argparse subparsers; stdout-only plaintext output on `key create` |
| Dependencies | `taskq_api.repository.session`, `taskq_api.repository.key_repo`, `taskq_api.errors` |

#### Logical Constraints
- API-key plaintext is printed **exactly once** at creation; no log
  line, no persistent artefact (FR-03 / NFR-04).

### 2.10 `migrations.versions` (Alembic chain — FR-07)

| Attribute | Value |
|-----------|-------|
| Responsibility | Three reversible revisions: v1 base tables (`tasks`, `api_keys`); v2 adds `tags`, `task_tags` + `tasks.name` unique index; v3 splits `tasks.result_json` into a `task_results` table with byte-identical round-trip data migration |
| External Interface | `alembic upgrade head`, `alembic downgrade base`, `alembic downgrade -1` |
| Dependencies | `alembic`, `sqlalchemy` |

#### Logical Constraints
- Every `downgrade()` is real (no `op.execute("DROP TABLE ...")`
  shortcut — AC-7.3).
- v3 data-migration round-trip is verified column-by-column against a
  real SQLite file (AC-7.2 / NFR-09 round-2 specific clause).

## 3. Interfaces & Data Flows

### 3.1 Layered request flow

```
Client
  │  HTTP + X-API-Key
  ▼
┌──────────────────────────────────────────────────────────────┐
│ api/  FastAPI routers                                        │
│   ├── deps.require_scope(scope)   [authn + authz, FR-03/04]  │
│   ├── deps.rate_limit()           [token bucket, FR-05]      │
│   ├── correlation_id middleware   [FR-10]                    │
│   └── handler() ≤ 40 lines        [FR-01/02/09]              │
└──────────────────┬───────────────────────────────────────────┘
                   │ call
                   ▼
┌──────────────────────────────────────────────────────────────┐
│ service/  business logic                                     │
│   ├── tasks.{create,get,list,delete,schedule_run}  [FR-01]   │
│   ├── runner.{run_with_timeout, shutdown}          [FR-02/08]│
│   ├── auth.{hash_key, verify_key, check_scope}     [FR-03/04]│
│   └── ratelimit.consume                            [FR-05]   │
└──────────────────┬───────────────────────────────────────────┘
                   │ call
                   ▼
┌──────────────────────────────────────────────────────────────┐
│ repository/  ONLY layer permitted to import sqlalchemy       │
│   ├── session.session_scope()  [FR-06 transaction boundary]  │
│   ├── task_repo.*               [FR-01/02/07/08]             │
│   ├── key_repo.*                [FR-03]                      │
│   └── rate_repo.take_token      [FR-05 row-level lock]       │
└──────────────────┬───────────────────────────────────────────┘
                   │ SQLAlchemy 2.x ORM
                   ▼
              ┌────────┐
              │  DB    │   tasks / api_keys / tags / task_tags
              └────────┘   task_results (v3) / rate_buckets

Independence: errors.problem_json is invoked from api/, service/, and
repository/ for any non-2xx return path. config.* is read by service/
and repository/ only at construction time.
```

### 3.2 Async subprocess flow (FR-02 / FR-08)

```
POST /v1/tasks/{id}/run
  → api.tasks.run_task → service.tasks.schedule_run
  → service.runner.TaskGroupRunner
        ├ if concurrency cap reached → enqueue, do NOT spawn
        └ else: asyncio.create_task(_run_one(task_id))
                 │
                 ├ asyncio.create_subprocess_exec(*shlex.split(cmd))
                 ├ asyncio.wait_for(... TASKQ_TASK_TIMEOUT ...)
                 │     on timeout: process.kill(); await process.wait()
                 └ on done/failed/timeout: write task_results row

Shutdown signal
  → service.runner.shutdown()
        ├ wait up to TASKQ_DRAIN_TIMEOUT for in-flight tasks
        └ tasks exceeding budget → mark status="interrupted"
```

### 3.3 Auth + rate-limit flow (FR-03 / FR-04 / FR-05)

```
request enters /v1/*
  → correlation_id middleware (FR-10: stamp id, attach to log)
  → deps.require_scope(scope)
        ├ X-API-Key missing/invalid → 401 + problem+json
        ├ key_repo.get_by_hash(sha256(key)) — uses hmac.compare_digest
        ├ revoked_at is set        → 401 + problem+json
        └ check_scope(key.scope, required) — admin ⊇ write ⊇ read
              ├ insufficient  → 403 + problem+json (body does NOT reveal
              │                  whether resource exists)
              └ sufficient    → key attached to request.state
  → deps.rate_limit()
        ├ rate_repo.take_token(key_id) under row-level lock
        │     ├ bucket empty → 429 + problem+json + Retry-After
        │     └ bucket refilled → proceed
        └ /healthz, /readyz bypass both deps
```

### 3.4 Migration round-trip flow (FR-07 / NFR-12)

```
make verify-system
  ├─ alembic upgrade head          (v1 → v2 → v3)
  ├─ pytest ...                    (full suite, 0 skipped)
  ├─ uvicorn taskq_api.app:app &   (start service)
  │     ├─ smoke GET /healthz → 200
  │     └─ smoke GET /readyz  → 200 (DB up + alembic at head)
  ├─ alembic downgrade base        (v3 → v2 → v1, no residual tables)
  └─ alembic upgrade head          (back to v3; byte-identical sample data)
```

## 4. NFR Handling

Every NFR (NFR-01..NFR-12, enumerated from `SPEC.md §4`) is bound to
the modules / tests that satisfy it.

| NFR | Dimension | Type | Module(s) | Acceptance / Measurement |
|-----|-----------|------|-----------|--------------------------|
| NFR-01 | performance | performance | `taskq_api.service.tasks`, `taskq_api.repository.task_repo` | pytest-benchmark: `GET /v1/tasks/{id}` p95 < 30ms at 10k rows; list p95 < 80ms; SQL-statement count constant (SQLAlchemy event listener) — AC-N1.1..AC-N1.3 |
| NFR-02 | security | security | `taskq_api.service.runner` (shell=True ban), `taskq_api.service.auth` (hmac.compare_digest), `taskq_api.repository.task_repo` (no string SQL), `taskq_api.errors` (detail filter) | grep gates + `bandit -r 03-development/src/` 0 HIGH / 0 MEDIUM — AC-N2.1..AC-N2.4 |
| NFR-03 | error_handling | reliability | `taskq_api.repository.session`, `taskq_api.service.runner`, `taskq_api.errors` | context-manager commit/rollback; no bare `except:`; `CancelledError` not swallowed; migration rollback on failure — AC-N3.1..AC-N3.4 |
| NFR-04 | security | security | `taskq_api.service.tasks` (stdout/stderr redaction), `taskq_api.errors` (error-body redaction), `taskq_api.repository.session` (DB-URL filter), `taskq_api.__main__` (plaintext-once) | regex-driven `[REDACTED]` substitution; DB-URL password never in log / error / metrics — AC-N4.1..AC-N4.3 |
| NFR-05 | documentation | documentation | all modules (docstrings with `[FR-XX]` / `[NFR-XX]`), `taskq_api.api.*` (OpenAPI summary/description) | 100% public-symbol coverage; `/openapi.json` asserts — AC-N5.1..AC-N5.2 |
| NFR-06 | architecture_constraints | layering | project root (`.importlinter`); `taskq_api.repository` (only sqlalchemy importer) | `lint-imports` exit 0; `sqlalchemy` forbidden outside repository — AC-N6.1..AC-N6.3 |
| NFR-07 | license_compliance | licensing | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | `==` pinning; allowlist scan (`pip-licenses --with-system`); SBOM fields per dep — AC-N7.1..AC-N7.4 |
| NFR-08 | mutation_testing | mutation | `taskq_api.service.*`, `taskq_api.repository.*` (scope), `.methodology/harness_config.json` (flag) | mutmut score ≥ 70 over scope — AC-N8.1..AC-N8.2 |
| NFR-09 | test_assertion_quality | testability | `03-development/tests/integration/test_migration_round_trip.py` (real SQLite file) | 0 skipped; 0 assertion-free; no exclusion paths; migration on real DB — AC-N9.1..AC-N9.4 |
| NFR-10 | integration_coverage | integration | `03-development/tests/integration/` (httpx ASGITransport) | ≥80% line coverage; every error code exercised — AC-N10.1..AC-N10.3 |
| NFR-11 | readability | maintainability | all modules | MI ≥ 80; CC ≤ 10; ≤400 lines/file; ≤15 files/dir; ≤40 lines/handler — AC-N11.1..AC-N11.4 |
| NFR-12 | execute_verification_target | verifiability | `Makefile`, `migrations/versions/v{1,2,3}*.py`, `taskq_api.app:app` | `make verify-system` exit 0 + `verify-system: PASS` stdout — AC-N12.1 |

Latency budget (NFR-01): `GET /v1/tasks/{id}` 30ms p95, list 80ms p95.
Security budget (NFR-02): 0 HIGH / 0 MEDIUM bandit findings; 0 string
SQL grep hits.
Cost budget: N/A — service binds `127.0.0.1:8000` (SPEC §5.1
`TASKQ_HOST` default); horizontal cost is a deployment concern outside
this round's scope (SPEC §6 out-of-scope).

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as
> int must match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-09-01"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.deps"
        - name: "taskq_api.api.health"
      allowed_dependencies: ["service", "errors"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
        - name: "taskq_api.service.health"
      allowed_dependencies: ["repository", "errors"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "config"]
    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: []
    - name: errors
      modules:
        - name: "taskq_api.errors"
      allowed_dependencies: []
    - name: config
      modules:
        - name: "taskq_api.config"
      allowed_dependencies: []

  allowed_dependencies:
    - from: api
      to: service
    - from: service
      to: repository
    - from: repository
      to: models
    - from: api
      to: errors
    - from: service
      to: errors
    - from: repository
      to: config

  quality_targets:
    max_complexity: 10           # NFR-11 CC ≤ 10
    min_coverage: 100            # §11 TOTAL 100%
    max_coupling: 0.3            # CRG ceiling

  nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 < 30ms"
      module: taskq_api.service.tasks
    NFR-02:
      type: security
      dimension: security
      target: "bandit 0 HIGH/0 MEDIUM; 0 string-SQL grep hits"
      module: taskq_api.errors
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "no bare except; CancelledError propagates"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security
      target: "DB-URL password never in logs/errors/metrics"
      module: taskq_api.errors
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% public docstring coverage with [FR-XX]/[NFR-XX]"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0; sqlalchemy forbidden outside repository"
      module: taskq_api.repository.session
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "every dep license in allowlist (MIT/BSD/Apache/PSF)"
      module: taskq_api.repository.session
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: "mutmut score >= 70"
      module: taskq_api.service.tasks
      scope_layers: ["service", "repository"]
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "0 skipped; 0 zero-assert; migration tested on real DB"
      module: taskq_api.repository.session
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: ">= 80% line coverage via httpx ASGITransport"
      module: taskq_api.api.tasks
    NFR-11:
      type: maintainability
      dimension: readability
      target: "MI >= 80; CC <= 10; <= 400 lines/file; <= 15 files/dir; <= 40 lines/handler"
      module: taskq_api.service.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exit 0 + verify-system: PASS"
      module: taskq_api.repository.session

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:
    FR-01:
      - taskq_api.api.tasks
      - taskq_api.service.tasks
      - taskq_api.repository.task_repo
      - taskq_api.models.schemas
    FR-02:
      - taskq_api.api.tasks
      - taskq_api.service.runner
      - taskq_api.service.tasks
      - taskq_api.repository.task_repo
    FR-03:
      - taskq_api.service.auth
      - taskq_api.repository.key_repo
      - taskq_api.__main__
    FR-04:
      - taskq_api.api.deps
      - taskq_api.service.auth
    FR-05:
      - taskq_api.api.deps
      - taskq_api.service.ratelimit
      - taskq_api.repository.rate_repo
    FR-06:
      - taskq_api.repository.session
      - taskq_api.repository.task_repo
      - taskq_api.repository.key_repo
      - taskq_api.repository.rate_repo
    FR-07:
      - migrations.versions.v1_initial
      - migrations.versions.v2_tags
      - migrations.versions.v3_split_results
      - taskq_api.repository.session
    FR-08:
      - taskq_api.service.runner
      - taskq_api.repository.task_repo
    FR-09:
      - taskq_api.api.health
      - taskq_api.service.health
      - taskq_api.repository.session
    FR-10:
      - taskq_api.errors
      - taskq_api.api.deps

  architecture_constraints:
    - "no_circular_dependencies"
    - "layering: api > service > repository > models"
    - "sqlalchemy only importable by repository"
    - "config and errors are independence modules"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"

  required_artifacts:
    - ".importlinter"
    - ".env.example"
    - "requirements.txt"
    - "requirements.lock"
    - "requirements-dev.txt"
    - "alembic.ini"
    - ".methodology/harness_config.json"
    - "Makefile"
    - "08-config/SBOM.json"
    - "migrations/versions/v1_initial.py"
    - "migrations/versions/v2_tags.py"
    - "migrations/versions/v3_split_results.py"
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and
gate scoring. Generate:
`python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are
> parsed by `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`
>
> `applicability: none` is a fully valid, honest declaration for a
> project with no real attack surface (e.g. a pure CLI formatting
> tool) — it requires a `justification` (>=20 chars) and skips the
> rest of this block. This is a decidable structural check, not a
> keyword scorer: an honest `none` always passes.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full   # full | none — none REQUIRES justification and skips the rest
  justification: ""     # required (>=20 chars) when applicability: none
  trust_boundaries:
    - id: TB-01
      name: "external HTTP client → API layer"
      description: "unauthenticated client requests crossing into the FastAPI router; only authentication is the X-API-Key header"
    - id: TB-02
      name: "API layer → business (service) layer"
      description: "validated HTTP input becoming typed pydantic models and entering service-level handlers; authn/authz already checked at this seam"
    - id: TB-03
      name: "service layer → persistence (repository) layer"
      description: "service functions calling repository functions; ORM/SQL composition happens here; sqlalchemy is isolated to this boundary"
    - id: TB-04
      name: "application → child subprocess"
      description: "subprocess execution for task commands via asyncio.create_subprocess_exec; the only execution boundary the OS enforces for us"
    - id: TB-05
      name: "application → persistent storage / log streams"
      description: "writes to DB tables, to stdout_tail/stderr_tail fields, to log lines, to /v1/metrics responses — sensitive-data redaction must happen before this boundary"
  threats:
    - id: T-01
      boundary: TB-01
      category: spoofing
      description: "attacker presents a guessed/forged X-API-Key to impersonate an authorised client"
      mitigation: "SHA-256 hash storage + hmac.compare_digest (constant-time) + revocation via revoked_at; plaintext printed once at key create (FR-03)"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_api_keys_table_holds_no_plaintext"
    - id: T-02
      boundary: TB-01
      category: tampering
      description: "malformed payload bypasses validation and mutates task state with attacker-controlled content"
      mitigation: "pydantic TaskCreate model rejects unknown fields / oversized bodies / injection-character blacklist; 422 + problem+json on violation (FR-01)"
      owner_module: "taskq_api.api.tasks"
      nfr: NFR-02
      verified_by: "test_post_task_validation_violations_returns_422"
    - id: T-03
      boundary: TB-02
      category: elevation_of_privilege
      description: "read or write token attempts an admin-only operation (e.g. DELETE /v1/tasks/{id})"
      mitigation: "single require_scope dependency enforces every /v1 route; insufficient → 403 + problem+json, body does NOT reveal resource existence (FR-04)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_write_scope_delete_returns_403_no_resource_leak"
    - id: T-04
      boundary: TB-03
      category: tampering
      description: "SQL injection through string-concatenated ORM/SQL in repository layer"
      mitigation: "ORM and parameterised queries only; grep gate forbids f-string / % / + SQL composition; lint-imports bans sqlalchemy outside repository (NFR-02 / FR-06)"
      owner_module: "taskq_api.repository.task_repo"
      nfr: NFR-02
      verified_by: "test_no_string_concatenated_sql_in_src"
    - id: T-05
      boundary: TB-04
      category: elevation_of_privilege
      description: "shell metacharacters in task command escape sandbox and execute arbitrary host commands"
      mitigation: "asyncio.create_subprocess_exec(*shlex.split(command)) with shell=True forbidden tree-wide; bandit + grep gates enforce 0 hits (NFR-02)"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_shell_true_absent_from_src_tree"
    - id: T-06
      boundary: TB-05
      category: information_disclosure
      description: "secret / token / DB-URL leaks into stdout_tail, stderr_tail, log lines, error bodies, or /v1/metrics response"
      mitigation: "regex-driven redaction to [REDACTED] before write/emit; DB-URL password never emitted; key plaintext printed once and never persisted (NFR-04)"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_db_url_password_never_logged_or_emitted"
    - id: T-07
      boundary: TB-05
      category: information_disclosure
      description: "500-class error response leaks stack trace, SQL statement, file path, or schema description in detail field"
      mitigation: "RFC 7807 problem+json with allowlisted detail; exception_handlers normalise every uncaught exception to opaque 500 (FR-10 / NFR-02)"
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_500_detail_no_internals"
    - id: T-08
      boundary: TB-01
      category: denial_of_service
      description: "attacker floods /v1/* endpoints to exhaust a token's bucket or starve other clients"
      mitigation: "per-token DB-backed token bucket; row-level lock in single transaction prevents over-admit under concurrency; /healthz and /readyz exempt (FR-05 / R12)"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-02
      verified_by: "test_burst_over_limit_returns_429_with_retry_after"
    - id: T-09
      boundary: TB-02
      category: repudiation
      description: "operator denies a request happened because no correlation_id ties log line to response"
      mitigation: "correlation_id stamped on every request, present in X-Correlation-Id header AND in the server log line for that request (FR-10)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_correlation_id_in_header_and_logs"
    - id: T-10
      boundary: TB-03
      category: tampering
      description: "rate-bucket race condition over-admits requests because two workers update the same bucket row without coordination"
      mitigation: "rate_repo.take_token executes the bucket read+update under a row-level lock inside a single transaction (FR-05 / R12)"
      owner_module: "taskq_api.repository.rate_repo"
      nfr: NFR-02
      verified_by: "test_bucket_update_uses_row_level_lock"
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test
that proves the mitigation — from Phase 5 onward,
`check-artifact-consistency` blocks if that test doesn't exist yet.
Threats also seed `bug-hunt-targets`' adversarial-review targeting
and force NFR-pattern test cases in `derive_test_cases.md` Step 1c
regardless of SRS keywords.

---

## Appendix A — No Circular Dependencies

The dependency graph (edges point from caller to callee) is:

```
api.tasks        → service.tasks, service.runner, errors, models.schemas
api.deps         → service.auth, service.ratelimit, errors
api.health       → service.health, errors
service.tasks    → repository.task_repo, errors, models.schemas
service.runner   → repository.task_repo, errors
service.auth     → repository.key_repo
service.ratelimit → repository.rate_repo
service.health   → repository.session
repository.*     → models.orm, config
migrations.versions.* → models.orm, repository.session
__main__         → repository.session, repository.key_repo, errors
config           → (stdlib only)
errors           → (stdlib + fastapi only)
```

No edge returns to a caller: this is a DAG. `lint-imports` with the
`.importlinter` contract (`api > service > repository > models` plus
the `sqlalchemy`-outside-`repository` forbidden contract) is the
mechanical enforcement.
