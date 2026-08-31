# Architecture Decision Records (ADR) — taskq-api

> Architecture Decision Records for the `taskq-api` Python ASGI
> service (harness-methodology progressive test-bed, round 2 of 3).
> Each `## ADR-NNN:` entry below is a binding decision derived from
> `02-architecture/SAD.md` §1–§6 and `SPEC.md` §3–§5; the H1 anchor
> is the one the Phase 2 orchestrator loader matches via
> `first_line.startswith("# Architecture Decision Records")`.
>
> Every ADR is anchored back to the srs requirements it satisfies
> and to the sad.md / spec.md specification sections that justify
> it; the consolidated traceability matrix at the end of this
> document is the canonical map and the single source of truth for
> "which ADR satisfies which srs fr / NFR identifier". The matrix
> is what makes the architecture decisions answerable back to the
> acceptance criteria enumerated in `01-requirements/SRS.md`.

## ADR-001: Python 3.11 ASGI Service on FastAPI + SQLAlchemy 2.x

### Status
Accepted

### Context
`taskq-api` is a REST task-queue over HTTP that lets clients submit,
query, and execute shell-command tasks. The runtime must (a) serve a
JSON HTTP API, (b) persist tasks/keys/rate-bucket state durably,
(c) drive an in-process async runner, and (d) evolve the schema
across three Alembic revisions. The verified runtime on disk is
Python 3.11.15 (`/Users/johnny/projects/taskq-redo/.venv/bin/python
--version`). `SAD.md §1` and `SPEC.md §5.1` lock the language
version to 3.11 and the binding contract to a layered ASGI app
exposed as `taskq_api.app:app`.

### Decision
Use Python 3.11.15 with the FastAPI/uvicorn ASGI stack for HTTP,
SQLAlchemy 2.x ORM for persistence, Alembic for schema migration,
and pydantic v2 for request/response models. The independence
modules `taskq_api.config` and `taskq_api.errors` are constrained
to the smallest possible surface — `config` is stdlib-only
(`os.environ` reads), `errors` adds only `fastapi`/`starlette`/`uuid`.

### Consequences
- Positive: matches the SPEC's `TASKQ_*` env contract verbatim;
  Phase-5 imports can be statically linted because the dependency
  surface per module is small and one-direction.
- Negative: ties the project to the FastAPI/SQLAlchemy/Alembic
  release line; major-version upgrades require re-running the
  migration round-trip (NFR-12) and re-running mutmut (NFR-08).
- Risk: Python 3.11 EOL is October 2024 (per CPython release
  schedule) — Round 3 must re-target 3.12+.

### Alternatives Considered
- **Pure stdlib `http.server` + `sqlite3`** — rejected: no async
  story, no OpenAPI generation (NFR-05), and the harness's
  `httpx ASGITransport` integration suite (NFR-10) needs an ASGI
  app object.
- **Starlette-only (no FastAPI)** — rejected: loses pydantic-v2
  request validation (T-02 mitigation) and the OpenAPI schema
  generator that satisfies NFR-05.
- **Django + DRF** — rejected: heavier ORM coupling makes the
  `sqlalchemy-only-in-repository` layering contract (NFR-06) much
  harder to express in `.importlinter`.

## ADR-002: Layered Architecture `api > service > repository > models`

### Status
Accepted

### Context
`SAD.md §2.1` enumerates four community-aligned directories that
match the Code Review Graph (CRG) communities and the `.importlinter`
contract (NFR-06). Two further "independence modules"
(`taskq_api.config`, `taskq_api.errors`) live at package root.
Each community must stay ≤15 files and ≤50 nodes and must clear the
CRG cohesion threshold (internal-edge density ≥ 0.3).

### Decision
Adopt the four-layer split with dependency direction strictly
`api → service → repository → models`. `config` and `errors` are
imported only at construction time / on the error-response path
respectively; they hold no business state. Each layer exposes a
"hub module" called from sibling function bodies (not just at module
level) so the internal-edge budget clears the CRG threshold:
`api.deps`, `service.tasks`, `repository.session`, `errors`.

### Consequences
- Positive: mechanical layering enforcement via `.importlinter`
  (AC-N6.1); CRG architecture-scoring favours clean communities;
  unit-test scope is unambiguous.
- Negative: any cross-cutting concern (logging, correlation_id)
  must be threaded explicitly rather than reaching into a service
  locator.
- Risk: future temptation to import `repository` from `api` for
  convenience — must be rejected at lint time.

### Alternatives Considered
- **Flat package** (no layers) — rejected: would put `sqlalchemy`
  imports inside HTTP handlers and break both NFR-06 and the
  bandit/grep gates.
- **Hexagonal / ports-and-adapters** — rejected: overkill for a
  single-deployment service; would inflate file count past the
  15-files/dir ceiling (NFR-11).

## ADR-003: SQLAlchemy 2.x ORM with SQLite (dev/test) and PostgreSQL (prod)

### Status
Accepted

### Context
`SAD.md §1` and `SPEC.md §5.1` pick SQLAlchemy 2.x ORM because the
service needs (a) `selectinload`/`joinedload` to defeat N+1 on
`GET /v1/tasks/{id}` (NFR-01 / R5), (b) row-level locking for the
rate-bucket update (FR-05 / R12), and (c) Alembic-driven schema
evolution (FR-07). Dev/test must use a real, file-backed SQLite
database so the migration round-trip is verifiable end-to-end
(AC-7.2 / NFR-09 round-2 clause).

### Decision
Use SQLAlchemy 2.x with the dialect selected at construction via
`TASKQ_DB_URL`. Development and the `make verify-system` gate use
SQLite; production targets PostgreSQL. The repository layer
(`taskq_api.repository`) is the **only** layer permitted to
`import sqlalchemy` (NFR-06) — enforced by `.importlinter`.

### Consequences
- Positive: one ORM, two backends, identical query API; the
  Alembic revision chain can be tested locally on SQLite and
  re-run on PostgreSQL.
- Negative: dialect-specific features (e.g. PostgreSQL
  `RETURNING`, SQLite `WITHOUT ROWID`) must be avoided in repository
  code, narrowing the SQL surface.
- Risk: a SQLite-only feature sneaks into a migration — gate
  test must use a real SQLite file (NFR-09).

### Alternatives Considered
- **Raw `sqlite3` + handwritten SQL** — rejected: no row-level
  lock, no typed ORM models, no Alembic story.
- **Tortoise ORM / SQLModel** — rejected: SQLModel doubles
  SQLAlchemy with pydantic; the resulting dual-model surface
  collides with NFR-06's "only repository imports sqlalchemy".
- **PostgreSQL-only (no SQLite dev)** — rejected: makes the
  CI migration round-trip dependent on a Postgres container,
  violating the round-2 "real SQLite file" clause (NFR-09).

## ADR-004: Alembic Three-Revision Migration Chain (v1 → v2 → v3)

### Status
Accepted

### Context
`SPEC.md §3` FR-07 requires schema evolution through three
reversible Alembic revisions: (v1) base tables `tasks` + `api_keys`,
(v2) adds `tags` + `task_tags` plus a unique index on `tasks.name`,
(v3) splits `tasks.result_json` out into a `task_results` table
with a byte-identical data-migration round-trip. The
`make verify-system` gate (`SAD.md §1.1`) runs `alembic upgrade
head` and then `alembic downgrade base → upgrade head` against a
real SQLite file (AC-7.3 / NFR-12).

### Decision
Place three revisions under `migrations/versions/`:
`v1_initial.py`, `v2_tags.py`, `v3_split_results.py`. Each
`downgrade()` is a real, structural reversal — never
`op.execute("DROP TABLE ...")` as a shortcut (AC-7.3). The v3
data migration is verified column-by-column against a real SQLite
file (AC-7.2).

### Consequences
- Positive: byte-identical round-trip is a mechanical, testable
  property; production rollback is symmetric with upgrade.
- Negative: each revision must be written defensively because
  SQLite and PostgreSQL types differ — the data-migration helpers
  must be dialect-portable.
- Risk: a "shortcut downgrade" regression would silently pass CI
  until production rollback — the gate must inspect
  `op.get_bind().engine.dialect.name` and assert no DROP shortcuts.

### Alternatives Considered
- **`CREATE TABLE IF NOT EXISTS` at startup** — rejected: violates
  the round-trip property; production rollback is then undefined.
- **Single mega-revision with all three changes** — rejected:
  removes the migration-on-real-DB acceptance criterion (NFR-09)
  and loses the per-step round-trip evidence.

## ADR-005: Async Subprocess Runner via `asyncio.create_subprocess_exec`

### Status
Accepted

### Context
FR-02 and FR-08 require the service to spawn task commands in the
background and to enforce a per-task timeout. The command string is
client-supplied, so the runner must (a) avoid shell interpolation
(NFR-02 / T-05) and (b) bound the wall-clock cost via
`TASKQ_TASK_TIMEOUT`. `SAD.md §3.2` and `SAD.md §2.6` lock the
execution surface.

### Decision
Use `asyncio.create_subprocess_exec(*shlex.split(command))` with
`shell=True` forbidden tree-wide (NFR-02). Bound execution with
`asyncio.wait_for(...)`; on timeout call `process.kill()` and
`await process.wait()` then write a `task_results` row. The runner
exposes `TaskGroupRunner` with `run_with_timeout` and `shutdown`,
and uses an in-process `asyncio` task graph (not a thread pool)
to track in-flight tasks.

### Consequences
- Positive: `shlex.split` tokenisation removes the shell
  metacharacter injection class (T-05); `wait_for` gives a single,
  testable timeout seam.
- Negative: subprocess lifecycle is tied to the event loop —
  `shutdown()` must `await` each task up to `TASKQ_DRAIN_TIMEOUT`
  and mark anything beyond the budget as `status="interrupted"`.
- Risk: `asyncio.CancelledError` being swallowed by a stray
  `except Exception` (NFR-03 / R7) — must be guarded explicitly
  in the runner.

### Alternatives Considered
- **`ThreadPoolExecutor` per worker** — rejected: a threaded model
  would either duplicate asyncio cancellation handling or hide it
  inside `Future.result()`, and would prevent `shutdown()` from
  cancelling cleanly via `task.cancel()`.
- **`subprocess.run` synchronous in a worker thread** — rejected:
  blocks the event loop on every task; defeats `wait_for`
  cancellation.
- **`os.system` / `subprocess.Popen(shell=True)`** — rejected
  outright by NFR-02 bandit + grep gates.

## ADR-006: SHA-256-Hashed X-API-Key Authentication with Constant-Time Compare

### Status
Accepted

### Context
FR-03 and NFR-02 require that API keys are never stored in
plaintext, that presented keys are compared in constant time, and
that a `revoked_at` timestamp invalidates a key without deleting
the row (audit retention). `SAD.md §2.6` and `SAD.md §3.3` and
`SAD.md §2.9` bind the design.

### Decision
Hash the presented key with SHA-256 (`hashlib.sha256(key).hexdigest()`)
and store the hex digest in `api_keys.key_hash`. Compare with
`hmac.compare_digest` to defeat timing oracles (T-01 mitigation).
The plaintext is printed **exactly once** on `key create` (FR-03
/ NFR-04) and is never persisted, logged, or echoed in error
bodies.

### Consequences
- Positive: no recoverable plaintext on disk; constant-time
  comparison removes the timing-attack class; revocation is a
  single-column update instead of a row delete.
- Negative: a lost key is unrecoverable (by design); the operator
  must rotate, not "look it up".
- Risk: a future "show me the key again" feature request — must
  be rejected because it would re-introduce plaintext storage.

### Alternatives Considered
- **Argon2 / bcrypt** — rejected for Round 2: the threat model
  does not include offline cracking of the `api_keys` table
  (compromise of the DB is a separate threat), and the heavier
  hashing cost would penalise the latency budget (NFR-01) on every
  request's auth dependency.
- **HMAC with a server-side pepper** — rejected: adds a secret to
  manage without changing the recoverability story.

## ADR-007: Per-Token Scope Authorisation (`admin > write > read`)

### Status
Accepted

### Context
FR-04 requires that read endpoints accept `read` (or higher)
scopes, write endpoints accept `write`, and admin-only endpoints
(e.g. `DELETE /v1/tasks/{id}`) accept `admin`. `SAD.md §3.3` and
`SAD.md §2.5` require that the same dependency
(`taskq_api.api.deps.require_scope`) is the **only** authz
choke point (AC-4.4 / T-03 mitigation).

### Decision
Implement `check_scope(key_scope, required)` such that
`admin` supersedes `write` supersedes `read`. Install
`require_scope(scope)` as a FastAPI dependency on every `/v1`
route. A 403 response body must **not** reveal whether the
resource exists (T-03 mitigation).

### Consequences
- Positive: one dependency, one decision site, mechanically
  verifiable (a single test scans every handler for a bypass).
- Negative: handlers must always pass the correct scope at
  decoration time — a typo like `require_scope("reed")` would
  silently pass in tests with a permissive key but fail closed in
  prod.
- Risk: a new route added without `require_scope` — must be
  blocked by `tests/unit/test_single_authz_dependency.py`.

### Alternatives Considered
- **Per-handler inline checks** — rejected: invites the bypass
  class; test `test_single_authz_dependency_used_by_every_v1_route`
  would become infeasible.
- **RBAC with role-to-scope mapping table** — rejected: scope is
  monotonic and three-valued; a table would add a query on the
  hot path without a behavioural change.

## ADR-008: DB-Backed Token-Bucket Rate Limiter with Row-Level Lock

### Status
Accepted

### Context
FR-05 / T-08 / R12 require a per-key rate limit (`TASKQ_RATE_BURST`
over `TASKQ_RATE_PER_SEC`) that survives process restart and is
correct under concurrent worker contention. `SAD.md §3.3` binds
the algorithm.

### Decision
Store bucket state in a `rate_buckets` row keyed by API key id.
`rate_repo.take_token(key_id)` performs the bucket read + refill +
decrement + write inside a **single transaction with row-level
lock** (SQLAlchemy `with_for_update()`), so two concurrent workers
cannot over-admit. `/healthz` and `/readyz` bypass the dependency
entirely.

### Consequences
- Positive: correctness under contention (T-10 mitigation);
  survives restart; 429 responses carry `Retry-After` (RFC 6585).
- Negative: every `/v1/*` request now costs an extra DB round
  trip — must be amortised by the connection pool
  (`TASKQ_DB_POOL_SIZE`).
- Risk: SQLite (dev) does not honour `with_for_update()` — the
  gate test for the lock must run on PostgreSQL or mock the
  dialect's lock method.

### Alternatives Considered
- **In-process counter (e.g. `asyncio.Queue`)** — rejected: dies
  on restart and is per-process, so two uvicorn workers would
  each allocate their own bucket.
- **Redis-backed token bucket** — rejected: adds an external
  dependency not in the SPEC's TASKQ_* env contract.
- **Circuit breaker on the bucket table** — rejected: would mask
  the over-admit class; the requirement is atomic admission, not
  failure-mode fallback.

## ADR-009: RFC 7807 `application/problem+json` Error Envelope

### Status
Accepted

### Context
FR-10 and T-07 require that every non-2xx response is a
`application/problem+json` body (RFC 7807), that `detail` is
filtered through an allowlist before serialisation, and that
uncaught exceptions surface as opaque 500s. `SAD.md §2.4` and
`SAD.md §3.3` bind the surface.

### Decision
Centralise error construction in `taskq_api.errors.problem_json`
and register the handlers via `taskq_api.errors.install_handlers(app)`.
The `correlation_id` is stamped by middleware, attached to the
response header, and emitted in the log line (T-09 mitigation).

### Consequences
- Positive: one envelope, one filter, one log shape — mechanical
  redaction (NFR-04) is tractable.
- Negative: every layer that produces a non-2xx must import
  `errors` — a deliberate cost that keeps the envelope from
  drifting.
- Risk: future contributors hand-rolling `JSONResponse` for an
  error path — must be flagged at code review because it would
  bypass the `detail` allowlist.

### Alternatives Considered
- **Plain `{"error": "..."}` responses** — rejected: loses
  `correlation_id`, `type` URI, and `instance` fields, and is the
  exact pattern that enables T-07 information disclosure.
- **Per-layer exception classes** — rejected: forces every layer
  to re-import FastAPI machinery; the independence module contract
  for `errors` becomes unenforceable.

## ADR-010: Mandatory `selectinload` / `joinedload` on Relationship Traversal

### Status
Accepted

### Context
NFR-01 requires that `GET /v1/tasks/{id}` and `GET /v1/tasks`
maintain a constant SQL-statement count regardless of rows, so an
N+1 query is an acceptance failure (R5). `SAD.md §2.7` binds the
mechanism.

### Decision
Every repository function that traverses a relationship
(`Task.tags`, `Task.results`, `ApiKey.scope`, etc.) must declare
the load strategy in the original query via `selectinload` /
`joinedload`. A SQLAlchemy event listener counts statements per
request and fails the test if it grows with row count.

### Consequences
- Positive: latency p95 stays under budget; SQLAlchemy warnings
  are silenced at the source.
- Negative: verbose query construction; authors must consciously
  pick the right strategy per relationship cardinality.
- Risk: a new relationship added without a load strategy —
  flagged at code review and at the next integration test run.

### Alternatives Considered
- **`lazy="selectin"` ORM default** — rejected: hides the
  strategy choice from the author; makes the event-listener
  count check less meaningful because every relationship would
  already eager-load.
- **Hand-rolled batching in the service layer** — rejected: puts
  optimisation logic in the wrong layer; would violate NFR-06 by
  leaking SQL concerns into `service`.

## ADR-011: `.importlinter` Layering Contract + `sqlalchemy`-Outside-`repository` Forbidden Contract

### Status
Accepted

### Context
NFR-06 requires a mechanical, exit-code-0 check that the layering
graph is exactly `api > service > repository > models` and that
`sqlalchemy` is never imported outside `taskq_api.repository`.
`SAD.md §2.1` and `SAD.md §4` make `lint-imports` a gate step.

### Decision
Encode the contract in `.importlinter` at the project root with
two layers: (a) the dependency-direction contract, and (b) a
forbidden-imports contract that grep-greps `import sqlalchemy`
across the source tree and fails on any hit outside
`taskq_api/repository/`.

### Consequences
- Positive: layering regressions are caught by CI, not by review.
- Negative: every new module must be classified into a layer at
  creation time — a small but unavoidable cost.
- Risk: a transitive `sqlalchemy` import (e.g. via
  `sqlmodel`) — must be checked at the install boundary, not just
  the source boundary.

### Alternatives Considered
- **Manual review only** — rejected: not a mechanical gate; the
  NFR-06 acceptance criterion explicitly requires `lint-imports
  exit 0`.
- **`pyright` / `mypy` strict layering plugin** — rejected: not
  idiomatic for layering; `importlinter` already understands the
  domain.

## ADR-012: Correlation-ID Middleware Stamped on Every Request

### Status
Accepted

### Context
T-09 (repudiation) requires that every request is tied to a
`correlation_id` present in both the response header
(`X-Correlation-Id`) and the server log line. `SAD.md §3.3` and
`SAD.md §2.5` bind the surface.

### Decision
Install a Starlette middleware in `taskq_api.api.deps
.correlation_id_middleware` that reads `X-Correlation-Id` if
present (else generates a UUID4), stamps it on `request.state`,
echoes it in the response header, and emits it as a structured
log field for every request-scoped log line.

### Consequences
- Positive: any operator-reported incident is traceable from a
  single header value to a log line to a DB row.
- Negative: every log call must thread `correlation_id` (or use a
  `contextvars`-backed filter) — a small ergonomic cost.
- Risk: a third-party library swallowing `request.state` — the
  middleware must run before any router.

### Alternatives Considered
- **AWS / GCP request-tracing SDK** — rejected: introduces a
  cloud SDK dependency for a service that binds to
  `127.0.0.1:8000`.
- **No correlation id, just request logs** — rejected: defeats
  T-09 mitigation and AC-10 traceability.

## ADR-013: Hub-Module-per-Community Design for CRG Cohesion

### Status
Accepted

### Context
`SAD.md §2.1` calls out that CRG architecture scoring rewards
communities with internal edge density ≥ 0.3; each layer must
expose a "hub module" whose functions are called from sibling
function bodies (not just at module level) so the internal-edge
budget clears. The required edge budget is `I ≥ ceil(0.4286 × E)`
internal edges for `E` external edges.

### Decision
Designate `api.deps`, `service.tasks`, `repository.session`, and
`errors` as the hub modules for their respective communities.
Callers invoke hub functions from inside other function bodies
(not just at import time) so the internal-edge count grows
multiplicatively with caller count, not linearly with module
count.

### Consequences
- Positive: CRG architecture score clears the 0.3 threshold;
  hub modules surface as natural integration-test seams.
- Negative: contributes to "hub module grows too large" risk —
  must be kept under the 400 lines/file ceiling (NFR-11).
- Risk: a contributor bypasses the hub by importing from the
  leaf module — must be caught at code review because it would
  collapse cohesion.

### Alternatives Considered
- **Flat community with module-level imports only** — rejected:
  produces a small number of internal edges, fails the 0.4286 × E
  threshold, and pulls the CRG architecture score below the
  acceptance bar.
- **Inverse dependency injection framework** — rejected: adds a
  runtime cost on the hot path and obscures the dependency graph
  that `.importlinter` is reading.

## ADR-014: Migration Round-Trip as Part of `make verify-system`

### Status
Accepted

### Context
NFR-12 and `SAD.md §1.1` require that the verification target
exercises a real acceptance criterion — not a mock — including a
real SQLite file for the migration round-trip. `SAD.md §3.4`
binds the sequence.

### Decision
The `verify-system` Makefile target runs:
`alembic upgrade head → pytest (full suite) → uvicorn taskq_api.app:app
(smoke `/healthz` + `/readyz`) → alembic downgrade base →
alembic upgrade head (byte-identical sample data)`. No `|| true`,
no leading `-`, no `--exit-zero` flags may swallow non-zero exits.

### Consequences
- Positive: the gate scores the delivered entry point, not a test
  double; rollback symmetry is exercised on every CI run.
- Negative: gate duration grows with the migration round-trip —
  acceptable because the gate runs once per CI cycle, not per
  request.
- Risk: a "shortcut downgrade" that silently succeeds — must be
  asserted byte-by-byte, not just by exit code.

### Alternatives Considered
- **Gate that only checks `alembic upgrade head` exit 0** —
  rejected: lacks the round-trip property; production rollback
  would be unverified.
- **Mocked DB for the gate** — rejected: directly violates the
  NFR-12 "real dependencies" requirement.

## ADR-015: Plaintext-Once for API Key Creation

### Status
Accepted

### Context
FR-03 / NFR-04 require that the plaintext API key is shown to the
operator exactly once at creation and never persisted, logged, or
echoed in any error body. `SAD.md §2.9` binds the surface via
`python -m taskq_api key create --scope <scope>`.

### Decision
`taskq_api.__main__ key create` generates the key, stores only the
SHA-256 hex digest in `api_keys.key_hash`, and prints the
plaintext to **stdout only**, exactly once. No log line, no
`/v1/*` response, no metrics endpoint, no error envelope ever
carries the plaintext.

### Consequences
- Positive: zero plaintext-at-rest attack surface; operator
  experience is a single copy-paste.
- Negative: a closed terminal window means re-issuing the key —
  acceptable cost because rotation is cheap.
- Risk: a future "print key on demand" feature — must be rejected
  outright.

### Alternatives Considered
- **Email the key** — rejected: adds an outbound-channel
  dependency and shifts the secret to the mail server.
- **Encrypt at rest with a KMS key** — rejected: requires a KMS
  dependency not in the SPEC's `TASKQ_*` env contract and does
  not improve the threat model meaningfully.

## ADR-016: Secret-Redaction Filter Applied at the Storage/Log Boundary

### Status
Accepted

### Context
NFR-04 and T-06 require that secrets (DB URL password, API-key
plaintext, user-supplied env values) never appear in
`stdout_tail`, `stderr_tail`, log lines, error bodies, or
`/v1/metrics` responses. `SAD.md §2.7` and `SAD.md §2.6` bind the
seams.

### Decision
A single regex-driven redactor (`[REDACTED]`) is applied at the
storage/log boundary in `taskq_api.errors`, `taskq_api.service.tasks`,
and `taskq_api.repository.session`. The DB-URL value is filtered
before logging; the DB-URL password is never emitted.

### Consequences
- Positive: one redaction surface, easy to test
  (`test_db_url_password_never_logged_or_emitted`).
- Negative: any new persistence or logging seam must remember to
  apply the filter — caught at code review and via the
  NFR-04 acceptance tests.
- Risk: a regex bypass via base64- or URL-encoded secret — the
  redactor must apply to normalised values, not the raw text.

### Alternatives Considered
- **Structured logging only (no redaction)** — rejected: shifts
  the burden to every log consumer and offers no protection at
  the storage boundary.
- **Encrypted-at-rest with a KMS key** — rejected: addresses
  at-rest disk theft, not log/stdout leakage.

## ADR-017: Shell-Injection Defence via `shell=True` Ban + `shlex.split`

### Status
Accepted

### Context
T-05 requires that shell metacharacters in a client-supplied
`command` cannot escape the sandbox. `SAD.md §2.6` and NFR-02 bind
the contract tree-wide.

### Decision
All subprocess invocation goes through
`asyncio.create_subprocess_exec(*shlex.split(command))`. `shell=True`
is forbidden tree-wide; bandit + a grep gate enforce zero hits in
`03-development/src/`.

### Consequences
- Positive: removes the entire shell-metacharacter injection
  class by tokenisation rather than by allowlist.
- Negative: pipe / redirect operators in the command string are
  rejected by `shlex.split` rather than silently failing — must
  be documented in the API contract.
- Risk: a future contributor reaching for `subprocess.run(cmd,
  shell=True)` "for ergonomics" — the grep gate blocks it.

### Alternatives Considered
- **Per-command allowlist of binaries** — rejected: the SPEC
  treats the command as opaque data; allowlisting would shrink
  the use case to a curated DSL.
- **Sandbox via gVisor / firejail** — rejected: requires a
  container or namespace primitive not part of the SPEC's
  deployment shape.

## ADR-018: Single `require_scope` Dependency as the Sole Authz Choke Point

### Status
Accepted

### Context
AC-4.4 and `SAD.md §2.5` require that every `/v1/*` route is
guarded by `taskq_api.api.deps.require_scope(scope)` and that no
handler implements its own scope check. `SAD.md §3.3` makes this
the single source of "yes/no" for authorisation.

### Decision
`require_scope(scope)` is the only authz decision point. The
acceptance test
`tests/unit/test_single_authz_dependency.py::test_single_authz_dependency_used_by_every_v1_route`
statically inspects every handler in `taskq_api.api.*` and fails
if a route is decorated without it.

### Consequences
- Positive: the bypass class is closed at test time, not at
  review time.
- Negative: scopes are spelled at decoration time, so a typo
  (`require_scope("reed")`) compiles and passes CI until the
  request hits the dependency — must be covered by an integration
  test per scope string.
- Risk: a new router file that forgets the dependency — the
  static-inspection test must scan all `*.py` under
  `taskq_api/api/`.

### Alternatives Considered
- **Global `Depends(require_scope)` at app level** — rejected:
  would force the same scope on `/healthz` and `/readyz`, which
  the SPEC exempts from auth.
- **Decorator-based authz** — rejected: FastAPI's dependency
  injection model is the idiomatic surface; layering a decorator
  on top would invite the bypass class the test is trying to
  prevent.

## ADR-019: `TASKQ_*` Environment Contract as the Sole Configuration Surface

### Status
Accepted

### Context
`SPEC.md §5.1` enumerates 12 environment variables (`TASKQ_DB_URL`,
`TASKQ_DB_POOL_SIZE`, `TASKQ_TASK_TIMEOUT`, `TASKQ_MAX_CONCURRENT`,
`TASKQ_DRAIN_TIMEOUT`, `TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC`,
`TASKQ_CORS_ORIGINS`, `TASKQ_LOG_LEVEL`, `TASKQ_LOG_FORMAT`,
`TASKQ_HOST`, `TASKQ_PORT`) and `SAD.md §2.3` binds them to
`taskq_api.config`.

### Decision
`taskq_api.config` exposes typed accessors for the 12 `TASKQ_*`
variables and nothing else — no dotenv file, no YAML config, no
CLI flags beyond what `__main__` accepts. The DB-URL value is
never logged (NFR-04).

### Consequences
- Positive: deployment shape is reproducible from environment
  alone; `.env.example` is the single configuration artefact.
- Negative: every new tunable requires a SPEC update — deliberate
  friction to keep the configuration surface small.
- Risk: a future contributor adding a CLI flag — must be rejected
  unless the SPEC grows the corresponding `TASKQ_*` variable.

### Alternatives Considered
- **Pydantic `BaseSettings` with auto-loaded `.env`** — rejected:
  adds a side-channel (`dotenv`) that the SPEC did not enumerate.
- **TOML config file** — rejected: same reason.

## ADR-020: Verification Target Is `python -m taskq_api` via `make verify-system`

### Status
Accepted

### Context
NFR-12 and `SAD.md §1.1` require that the gate scores the
delivered entry point (`taskq_api.__main__` and `taskq_api.app:app`),
not a test double. `SAD.md §3.4` binds the sequence.

### Decision
The `verify-system` Makefile target invokes the real `python -m
taskq_api` subcommands (`migrate`, `key create`, `healthcheck`)
and the real `uvicorn taskq_api.app:app` ASGI server. No mock
patching, no in-process import shim, no `monkeypatch` of the
entry-point module.

### Consequences
- Positive: the gate exercises the same code path as production
  boot; CI catches breakage of the management CLI.
- Negative: gate setup is heavier (needs a real SQLite file, a
  real subprocess for the runner smoke).
- Risk: a future refactor that splits `taskq_api.__main__` into a
  separate package — must keep it importable as `python -m
  taskq_api` for the gate to remain green.

### Alternatives Considered
- **Gate-only test suite (`pytest tests/integration/`)** —
  rejected: would not exercise `__main__` or the live ASGI app,
  and would not surface a broken CLI surface.
- **`tox` matrix over Python versions** — rejected: Python version
  is locked to 3.11 by the SPEC; matrix adds CI cost without
  coverage.

## ADR-021: Cross-Cutting Public-Docstring Coverage (NFR-05)

### Status
Accepted

### Context
NFR-05 requires that every public function/class carries a docstring
containing a `[FR-XX]` or `[NFR-XX]` reference (100% coverage) and
that every `/v1/*` endpoint exposes `summary` and `description` in
the auto-generated `/openapi.json`. `SAD.md §4` lists the OpenAPI
hookup as a quality-gate step. No single ADR-001..ADR-020 owns this
requirement — it is a cross-cutting discipline that every layer
must honour.

### Decision
Document the requirement explicitly so the `test_public_symbols_have_fr_or_nfr_docstring`
and `test_every_endpoint_has_summary_and_description` P4 tests have a
named owner. The actual enforcement is the P4 test suite under
`tests/unit/test_docstrings.py` and `tests/integration/test_openapi.py`;
no code-decision change is implied. Every ADR that introduces a new
public symbol inherits this obligation.

### Alternatives Considered
- **Per-module docstring-policy doc** — rejected: would duplicate the
  spec.md / srs binding without adding a binding decision.
- **Lint-only enforcement (e.g. pydocstyle)** — rejected: would miss
  the `[FR-XX]`/`[NFR-XX]` content check the srs requires.

## ADR-022: Cross-Cutting Mutation-Testing Scope (NFR-08)

### Status
Accepted

### Context
NFR-08 sets a `mutation score ≥ 70` floor over `service/` + `repository/`
with `features.mutation_testing: true` in
`.methodology/harness_config.json`. Like NFR-05, this is a
cross-cutting test-tooling constraint and is not bound to any single
ADR-001..ADR-020.

### Decision
Record the requirement as its own ADR so the P4
`test_mutation_score_at_least_70` test has a named owner and so the
mutation scope (`service/` + `repository/`) is not silently shrunk.
The runtime check is the `mutmut run` then `mutmut results` invocation
asserted by `tests/unit/test_mutation_score.py`; no architectural
shape change is implied.

### Alternatives Considered
- **Whole-tree mutation** — rejected: the srs narrows scope to
  `service/` + `repository/` for runtime-budget reasons, and a
  wider scope would breach the gate duration budget without adding
  coverage that the per-AC tests already provide.
- **No mutation testing** — rejected: violates NFR-08 outright.

## ADR–SRS Traceability Matrix

The table below is the consolidated traceability matrix linking every
ADR-NNN above to the srs fr / NFR identifiers it satisfies and to the
sad.md / spec.md specification sections that justify it. It is the
single source of truth for "which ADR discharges which requirement" and
is what makes the architecture decisions answerable back to the
acceptance criteria enumerated in `01-requirements/SRS.md`; absent this
matrix the per-decision prose above would be unmoored.

| ADR | Decision (one line) | SRS FR-IDs | SRS NFR-IDs | SAD § | SPEC § | Acceptance criteria surfaced |
|-----|---------------------|-----------|-------------|-------|--------|------------------------------|
| ADR-001 | Python 3.11 ASGI on FastAPI + SQLAlchemy 2.x | FR-01..FR-10 | NFR-06, NFR-11 | §1 | §5.1 | AC-1.1..AC-1.7, AC-6.1, AC-6.5, AC-N6.1..AC-N6.3, AC-N11.1..AC-N11.4 |
| ADR-002 | Four-layer `api > service > repository > models` | FR-01..FR-10 | NFR-06 | §2.1 | §2 | AC-N6.1..AC-N6.3 |
| ADR-003 | SQLAlchemy 2.x with SQLite (dev) / PostgreSQL (prod) | FR-01, FR-02, FR-05, FR-06, FR-07 | NFR-01 | §1, §3 | §5.1 | AC-1.1..AC-1.7, AC-N1.1..AC-N1.3 |
| ADR-004 | Alembic v1 → v2 → v3 reversible chain | FR-07 | NFR-09, NFR-12 | §1.1 | §3 FR-07 | AC-7.1..AC-7.5, AC-N9.3, AC-N12.1 |
| ADR-005 | Async subprocess runner (no `shell=True`) | FR-02, FR-08 | NFR-02, NFR-03 | §2.6, §3.2 | §3 FR-02 | AC-2.1..AC-2.6, AC-8.1..AC-8.5, AC-N2.1, AC-N3.2 |
| ADR-006 | SHA-256 + `hmac.compare_digest` for X-API-Key | FR-03 | NFR-02 | §2.6, §2.9, §3.3 | §3 FR-03 | AC-3.1..AC-3.7, AC-N2.3 |
| ADR-007 | Per-token `admin > write > read` scope | FR-04 | NFR-02 | §2.5, §3.3 | §3 FR-04 | AC-4.1..AC-4.5, AC-N2.3 |
| ADR-008 | DB-backed token bucket with row-level lock | FR-05 | NFR-01 | §3.3 | §3 FR-05 | AC-5.1..AC-5.5 |
| ADR-009 | RFC 7807 `application/problem+json` | FR-10 | NFR-02, NFR-04, NFR-10 | §2.4, §3.3 | §3 FR-10 | AC-10.1..AC-10.5, AC-N10.3, AC-N2.3 |
| ADR-010 | Mandatory `selectinload` / `joinedload` | FR-01, FR-06 | NFR-01 | §2.7 | §3 FR-06 | AC-1.4, AC-6.4, AC-N1.1..AC-N1.3 |
| ADR-011 | `.importlinter` + `sqlalchemy`-outside-repository forbidden | FR-06 | NFR-06 | §2.1, §4 | §3 FR-06 | AC-6.1, AC-N6.1..AC-N6.3 |
| ADR-012 | Correlation-ID middleware | FR-09, FR-10 | NFR-04, NFR-10 | §2.5, §3.3 | §3 FR-10 | AC-10.4, AC-N10.3 |
| ADR-013 | Hub-module-per-community for CRG cohesion | FR-06, FR-10 | NFR-06, NFR-11 | §2.1 | §2 | AC-N6.1..AC-N6.3, AC-N11.1..AC-N11.4 |
| ADR-014 | Migration round-trip in `verify-system` | FR-07, FR-09 | NFR-09, NFR-12 | §1.1, §3.4 | §8 | AC-7.1..AC-7.5, AC-9.2, AC-9.3, AC-N9.3, AC-N12.1 |
| ADR-015 | Plaintext-once for `key create` | FR-03 | NFR-02, NFR-04 | §2.9 | §3 FR-03 | AC-3.6, AC-N4.3 |
| ADR-016 | Secret-redaction filter at storage/log boundary | FR-09, FR-10 | NFR-04 | §2.6, §2.7 | §3 FR-10 | AC-N4.1, AC-N4.2, AC-N4.3 |
| ADR-017 | `shell=True` ban + `shlex.split` tokenisation | FR-02 | NFR-02 | §2.6 | §3 FR-02 | AC-2.2, AC-N2.1 |
| ADR-018 | Single `require_scope` dependency as sole authz | FR-04 | NFR-02, NFR-06 | §2.5, §3.3 | §3 FR-04 | AC-4.1..AC-4.5, AC-N2.3, AC-N6.1 |
| ADR-019 | `TASKQ_*` env vars as sole config surface | FR-01..FR-10 | NFR-07, NFR-12 | §2.3 | §5.1 | AC-N7.1..AC-N7.4, AC-N12.1 |
| ADR-020 | `make verify-system` exercises real entry point | FR-07, FR-09 | NFR-12 | §1.1, §3.4 | §8 | AC-N12.1, AC-N9.3 |
| ADR-021 | Cross-cutting public-docstring coverage | FR-01..FR-10 | NFR-05 | §4 | §3, §8 | AC-N5.1, AC-N5.2 |
| ADR-022 | Cross-cutting mutation-testing scope (`service/` + `repository/`) | FR-02, FR-06 | NFR-08 | §4 | §8 | AC-N8.1, AC-N8.2 |

Reading guide. Each row above is one `## ADR-NNN:` block in the body
of this document, one row in the srs requirements table, and one or
more rows in the sad.md §2 community / module map. The "Acceptance
criteria surfaced" column lists the srs identifiers that the ADR
mechanically satisfies; the corresponding P4 tests are named in
`srs §10 FR Block` `verification_method` and re-asserted by the
`make verify-system` gate (ADR-014, ADR-020). When a future
specification amendment changes an srs requirement, the matching
row here is the first place to update; when an architecture
decision is amended, the matching row here is what tells the srs
which fr/NFR identifiers to re-open.

Together with the per-decision context above, this traceability
matrix is what binds the srs requirements, the sad.md architecture
sections, and the spec.md specification authoritative at the
decision-record level: every ADR-NNN corresponds to one row in
this matrix, and every row corresponds to one or more srs fr or
NFR identifiers and their acceptance criteria in the srs document.
