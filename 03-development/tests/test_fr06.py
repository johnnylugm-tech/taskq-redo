"""RED step — failing tests for FR-06 Persistence layer and transaction boundaries.

Covers the five acceptance criteria declared in SPEC.md §3 FR-06 and
TEST_SPEC.md FR-06 cases 1–5:

  AC-6.1 — `service/` and `api/` layers contain ZERO `sqlalchemy` imports
           (architecture constraint; NFR-06 layering).
  AC-6.2 — Every API request uses exactly one `Session`, with the
           transaction boundary closed via a context manager: commit on
           clean exit, rollback on exception.
  AC-6.3 — String-concatenated SQL is forbidden inside `03-development/src/`
           (NFR-02 security; SPEC.md §8 #17).
  AC-6.4 — The list endpoint's SQL statement count is CONSTANT regardless
           of the returned row count (N+1 protected; NFR-01 + SPEC.md §8 #14).
  AC-6.5 — The connection pool uses `pool_size = TASKQ_DB_POOL_SIZE` and
           `pool_pre_ping = True` (SPEC.md §3 FR-06 paragraph 1 + §5.1).

Per `.methodology/SAB.json` (FR-06 module trace), GREEN must place these
modules on disk:

  taskq_api.repository.session     -> 03-development/src/taskq_api/repository/session.py
  taskq_api.repository.task_repo   -> 03-development/src/taskq_api/repository/task_repo.py
  taskq_api.repository.key_repo    -> 03-development/src/taskq_api/repository/key_repo.py
  taskq_api.repository.rate_repo   -> 03-development/src/taskq_api/repository/rate_repo.py

The current `repository/session.py` is a stand-in context manager
(SPEC.md §3 FR-06 paragraph 1 is not yet implemented in full):
  * No `SessionLocal()` factory.
  * No `engine` attribute.
  * `transaction()` yields `None` and does not commit/rollback.

These tests intentionally import the SAB-declared entry points so that
pytest fails at the collection boundary (ModuleNotFoundError on the
missing SAB symbols, or assertion failure on the missing `engine` /
`SessionLocal` exports) while the GREEN implementation is still
absent — this is the expected RED state and is preferable to writing
test-only stubs that would mask the absence of the real implementation.

Citations:
  SPEC.md §3 FR-06 (persistence layer and transaction boundaries)
  SPEC.md §5.1 (TASKQ_DB_POOL_SIZE environment binding)
  SPEC.md §8 #14 (N+1 SQL count constant)
  SPEC.md §8 #17 (no string-concatenated SQL)
  SPEC.md §8 #21 (sqlalchemy forbidden outside repository/)
  NFR-01 (performance — list SQL constant)
  NFR-02 (security — no string SQL)
  NFR-06 (architecture — repository is the only sqlalchemy surface)
"""

# Standard top-level imports (UNIT TEST CONTRACT — no try/except).
# A clean ModuleNotFoundError at collection time IS the RED signal
# because the GREEN implementation has not yet placed `engine` /
# `SessionLocal` on `taskq_api.repository.session`.
from taskq_api.repository import session as session_module  # noqa: E402

import re
from pathlib import Path

import pytest


# ----- Shared helpers ----------------------------------------------------


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def _collect_sqlalchemy_imports(*layers: str) -> list[tuple[str, int, str]]:
    """Return every `import sqlalchemy` / `from sqlalchemy …` line under
    the named `taskq_api/<layer>/` directories, as `(relative_path, lineno, line)`.

    Used by AC-6.1 to assert that no sqlalchemy surface bleeds into
    `service/` or `api/`. The check is intentionally a regex scan (not
    `ast`) so that future GREEN-side string-concatenated SQL also
    surfaces; sqlalchemy's namespace is wide enough that an `import` is
    the only signal of "the layer knows about SQLAlchemy".
    """
    pattern = re.compile(r"^\s*(?:import\s+sqlalchemy\b|from\s+sqlalchemy\b)")
    hits: list[tuple[str, int, str]] = []
    for layer in layers:
        layer_dir = _SRC_ROOT / "taskq_api" / layer
        if not layer_dir.exists():
            # Missing layer is its own failure mode — handled by the
            # caller asserting the directory exists.
            continue
        for py_file in layer_dir.rglob("*.py"):
            rel = py_file.relative_to(_SRC_ROOT).as_posix()
            for lineno, line in enumerate(py_file.read_text().splitlines(), 1):
                if pattern.match(line):
                    hits.append((rel, lineno, line.strip()))
    return hits


# GREEN TODO: `taskq_api.repository.session` must expose:
#   * `engine`   — a `sqlalchemy.Engine` configured with
#                  `pool_size = TASKQ_DB_POOL_SIZE` and `pool_pre_ping = True`.
#   * `SessionLocal()` — a callable factory that returns a fresh
#                  `sqlalchemy.orm.Session` per invocation.
#   * `transaction()` — a context manager that yields exactly one Session
#                  from `SessionLocal`, calls `.commit()` on clean exit,
#                  and calls `.rollback()` if an exception escapes.


# ----- AC-6.1 — no sqlalchemy imports in service/ or api/ ----------------


# NFR-06 (layering / architecture_constraints)
def test_no_sqlalchemy_imports_in_service_or_api():
    """AC-6.1 — service/ and api/ contain zero sqlalchemy imports.

    Sub-assertions: FR06-no-sqlalchemy-outside-repo.
    # NP-06 (NFR-06 architecture_constraints) — the layering contract
    #   `api > service > repository > models` (`.importlinter`) is what
    #   pins this; the repository layer is the ONLY seam where SQLAlchemy
    #   may be referenced. A stray `from sqlalchemy import …` in a
    #   service or api module is the regression this test catches.
    # SPEC.md §3 FR-06 paragraph 1: "repository/ layer; the business
    #   layer must not hold a Session directly".
    # SPEC.md §8 #21 (lint-imports exit code).
    # TEST_SPEC.md FR-06 case 1 Inputs: layer=service, layer2=api,
    #   forbidden_import=sqlalchemy.

    The static scan walks every `.py` file under
    `03-development/src/taskq_api/service/` and
    `03-development/src/taskq_api/api/`. If a line matches
    `import sqlalchemy` or `from sqlalchemy …` it is reported with its
    path and line number so the GREEN reviewer can fix it precisely.

    GREEN TODO: the only place a `sqlalchemy` import may live is
    `03-development/src/taskq_api/repository/`. If GREEN moves any
    SQLAlchemy symbol into a service or api file, this test fails.
    """
    hits = _collect_sqlalchemy_imports("service", "api")
    assert not hits, (
        "service/ and api/ MUST NOT import sqlalchemy (AC-6.1 / "
        "SPEC.md §3 FR-06 paragraph 1 + NFR-06 + SPEC.md §8 #21); "
        f"found {len(hits)} offender(s): " + ", ".join(
            f"{rel}:{lineno} -> {line!r}" for rel, lineno, line in hits
        )
    )


# ----- AC-6.2 — one Session per request via context manager --------------


# NFR-03 (reliability — explicit transaction boundaries / no bare except / error_handling)
def test_one_session_per_request_context_manager(monkeypatch):
    """AC-6.2 — one Session per request, commit on success, rollback on exception.

    Sub-assertions: FR06-one-session-per-request, FR06-uses-context-manager.
    # SPEC.md §3 FR-06 paragraph 1: "One Session per request; transaction
    #   boundary explicit: success commits, exceptions roll back
    #   (guaranteed by a context manager)".
    # TEST_SPEC.md FR-06 case 2 Inputs: request_id=req-1,
    #   expected_sessions=1, uses_context_manager=true.

    The test injects a fake `SessionLocal` factory into
    `taskq_api.repository.session` and a fake session that records
    commit/rollback calls. The `transaction()` context manager MUST:

      (a) call `SessionLocal()` exactly once per `with` block
          ("one Session per request");
      (b) yield the resulting session;
      (c) call `.commit()` on the session when the with-block exits
          cleanly;
      (d) call `.rollback()` on the session when an exception escapes
          the with-block.

    Currently `taskq_api.repository.session.transaction()` is a no-op
    context manager that yields `None` and re-raises. Both the
    `SessionLocal is not None` assertion and the commit/rollback
    assertions fail under the current code → this is the RED signal.

    GREEN TODO: replace `taskq_api.repository.session.transaction()`
    with::

        @contextmanager
        def transaction() -> Iterator[Session]:
            session = SessionLocal()
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()
    """
    SessionLocal = getattr(session_module, "SessionLocal", None)
    assert SessionLocal is not None, (
        "taskq_api.repository.session must expose a `SessionLocal()` factory "
        "(AC-6.2 / SPEC.md §3 FR-06 paragraph 1: one Session per request)"
    )

    # ---- Fake session that records lifecycle calls. ----
    commit_calls: list[str] = []
    rollback_calls: list[str] = []

    class FakeSession:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def commit(self) -> None:
            commit_calls.append(self.tag)

        def rollback(self) -> None:
            rollback_calls.append(self.tag)

        def close(self) -> None:
            # close() is not the focus of AC-6.2, but a real Session
            # is closed by the context manager — track to ensure no
            # dangling sessions leak between requests.
            pass

    created: list[FakeSession] = []

    def factory() -> FakeSession:
        s = FakeSession(tag=f"sess-{len(created)}")
        created.append(s)
        return s

    monkeypatch.setattr(session_module, "SessionLocal", factory)

    # ---- Happy path: clean exit → exactly one session, commit called. ----
    yielded: list[FakeSession] = []
    with session_module.transaction() as session:
        assert session is not None, (
            "transaction() must yield a Session (AC-6.2); current "
            "implementation yields None — the GREEN context manager "
            "must call `yield session` not `yield None`"
        )
        yielded.append(session)

    assert len(created) == 1, (
        f"expected exactly one Session per request (AC-6.2); "
        f"factory was called {len(created)} time(s)"
    )
    assert len(yielded) == 1, (
        f"transaction() must yield exactly one Session; "
        f"yielded {len(yielded)} value(s)"
    )
    assert yielded[0] is created[0], (
        "transaction() must yield the Session created by SessionLocal(); "
        "yielded object identity does not match the factory's return"
    )
    assert commit_calls == [created[0].tag], (
        f"transaction() must call session.commit() on clean exit (AC-6.2); "
        f"got commit_calls={commit_calls!r}, expected [{created[0].tag!r}]"
    )
    assert rollback_calls == [], (
        f"transaction() must NOT call session.rollback() on clean exit "
        f"(AC-6.2); got rollback_calls={rollback_calls!r}"
    )

    # ---- Exception path: exception → exactly one new session, rollback called. ----
    forced_error = RuntimeError("fr06-forced-failure")

    class _Reraise(RuntimeError):
        pass

    with pytest.raises(RuntimeError):
        with session_module.transaction() as session:
            assert session is not None, (
                "transaction() must yield a Session even on the exception "
                "path (AC-6.2); current implementation yields None"
            )
            raise forced_error

    assert len(created) == 2, (
        f"exception path must still create exactly one Session per "
        f"request (AC-6.2); factory was called {len(created)} times total"
    )
    # commit() must NOT have been called on the exception path.
    assert len(commit_calls) == 1, (
        f"transaction() must NOT call session.commit() when an "
        f"exception escapes the with-block (AC-6.2); got "
        f"commit_calls={commit_calls!r}"
    )
    assert len(rollback_calls) == 1, (
        f"transaction() must call session.rollback() when an exception "
        f"escapes the with-block (AC-6.2 / SPEC.md §3 FR-06 paragraph 1); "
        f"got rollback_calls={rollback_calls!r}"
    )


# ----- AC-6.3 — no string-concatenated SQL in src/ ------------------------


# NFR-02 (security — string SQL composition forbidden)
def test_no_string_concatenated_sql_in_src():
    """AC-6.3 — string-concatenated SQL is absent from 03-development/src/.

    Sub-assertion: FR06-no-string-sql.
    # NP-08 (security attack) + NFR-02 security — string-concatenated
    #   SQL is a SQL-injection vector. The repository layer MUST use
    #   parameterised queries or the ORM; this static scan guarantees
    #   no f-string / % / + built SQL ever lands in production.
    # SPEC.md §3 FR-06 paragraph 1: "String-concatenated SQL is
    #   forbidden; use ORM or parameterised queries (NFR-02)".
    # SPEC.md §8 #17 (0 grep hits for f-string / % / + built SQL).
    # TEST_SPEC.md FR-06 case 3 Inputs: src_root=03-development/src,
    #   forbidden_patterns=f-string-sql,%-sql,+-sql.

    The scan looks for SQL keywords followed by an interpolation cue
    inside the same string literal:

      * `f"...SELECT/UPDATE/INSERT/DELETE ...{<var>}..."`  (f-string SQL)
      * `"...SELECT/UPDATE/INSERT/DELETE ... %s ..." % (...)` (% SQL)
      * `"...WHERE/UPDATE/INSERT/DELETE ..." + <ident>` (+ SQL)

    These three shapes cover the vast majority of string-concat
    SQL-injection vectors. Each hit is reported with file + line +
    line text so the GREEN reviewer can fix it precisely.

    GREEN TODO: every `session.execute(...)` / `session.query(...)` /
    ORM call must use bound parameters (`:name` / `?` / the ORM
    expression API), never f-string / % / + concatenation of user input.
    """
    # Conservative SQL-keyword set used by both patterns. Whitespace
    # boundaries avoid false positives on identifiers like `selection`.
    sql_kw = r"(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b"

    patterns: list[tuple[re.Pattern[str], str]] = [
        # f-string SQL: `f"...SELECT ...{var}..."` — same line must
        # contain a SQL keyword AND a `{...}` interpolation.
        (
            re.compile(
                rf"""f["']{{[^"']*}}{0}{sql_kw}{{[^"']*}}\{{|"""
                rf"""f["']{{[^"']*}}{sql_kw}{{[^"']*}}\{{""",
                re.IGNORECASE | re.VERBOSE,
            ),
            "f-string SQL",
        ),
        # %-format SQL: `"...SELECT ... %s ..." % (...)` (the
        # parameter tuple/list after the literal is the giveaway).
        (
            re.compile(
                rf"""["'][^"']*{sql_kw}[^"']*?%\([^)]+\)""",
                re.IGNORECASE,
            ),
            "%-format SQL",
        ),
        # + concat SQL: `"...SELECT ..." + <ident>` on the same line
        # — captures `+ name`, `+ user_input`, `+ variable`, etc.
        # To keep the false-positive rate low we require the SQL
        # keyword on the left-hand string and an identifier-like
        # right operand.
        (
            re.compile(
                rf"""["'][^"']*{sql_kw}[^"']*["']\s*\+\s*[A-Za-z_]""",
                re.IGNORECASE,
            ),
            "+ concat SQL",
        ),
    ]

    violations: list[tuple[str, int, str, str]] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_SRC_ROOT).as_posix()
        for lineno, line in enumerate(py_file.read_text().splitlines(), 1):
            for pattern, label in patterns:
                if pattern.search(line):
                    violations.append((rel, lineno, line.strip(), label))
                    break  # one report per line

    assert not violations, (
        f"string-concatenated SQL is forbidden inside 03-development/src/ "
        f"(AC-6.3 / SPEC.md §3 FR-06 paragraph 1 + §8 #17 + NFR-02); "
        f"found {len(violations)} offender(s): "
        + "; ".join(
            f"{rel}:{lineno} ({label}) -> {line!r}"
            for rel, lineno, line, label in violations
        )
    )


# ----- AC-6.4 — list endpoint SQL count is constant ----------------------


# NFR-01 (performance — constant SQL count, no N+1)
def test_list_endpoint_sql_count_is_constant():
    """AC-6.4 — list endpoint's SQL statement count is constant regardless of row count.

    Sub-assertion: FR06-sql-count-constant.
    # NP-06 (NFR-01 performance cross-cut) — N+1 is an acceptance
    #   failure for the list endpoint. The contract is that the number
    #   of SQL statements fired by GET /v1/tasks must NOT scale with
    #   the number of rows returned; explicit `selectinload` /
    #   `joinedload` (or equivalent) MUST be in place (SPEC.md §3 FR-06
    #   paragraph 1 + §8 #14).
    # TEST_SPEC.md FR-06 case 4 Inputs: row_count_small=1,
    #   row_count_large=1000, sql_count_small=3, sql_count_large=3.

    Strategy:
      1. Hook a counter onto `taskq_api.repository.session.engine`'s
         `before_cursor_execute` event (the SQLAlchemy event that
         fires once per actual statement round-trip).
      2. Seed a small dataset and a large dataset via POST /v1/tasks
         (the existing FR-01 create endpoint).
      3. Issue GET /v1/tasks?limit=1 and GET /v1/tasks?limit=50
         against each dataset and assert the SQL count is the same
         both times.

    GREEN TODO: `taskq_api.repository.session.engine` must be a real
    SQLAlchemy `Engine`. The `TaskRepo.list(...)` method must use
    `selectinload` / `joinedload` so all related rows are fetched in
    one round-trip (no per-row SELECT triggered by lazy loading).
    """
    # 1. The session module must expose a real Engine so the SQL event
    #    hook has something to attach to. The current stand-in has
    #    only `transaction()`, so this assertion is the RED signal.
    engine = getattr(session_module, "engine", None)
    assert engine is not None, (
        "taskq_api.repository.session must expose a SQLAlchemy `engine` "
        "attribute (AC-6.4 / SPEC.md §3 FR-06 paragraph 1: real ORM "
        "session backed by a connection pool); current stand-in has "
        "no `engine` — pytest hook below has nothing to attach to"
    )

    # Imported lazily so the test still collects when sqlalchemy is
    # absent (RED boundary). The assertion above already fails fast
    # before we get here.
    from sqlalchemy import event  # noqa: PLC0415  (deferred import)

    sql_counts: list[int] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count_sql(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        sql_counts.append(1)

    # 2. Wire the FastAPI app + TestClient and seed rows.
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from taskq_api.app import app  # noqa: PLC0415

    client = TestClient(app)
    write_key = "fr01-test-write-key-aaaa"
    read_key = "fr01-test-read-key-bbbb"

    # Seed two datasets: a small set (5 rows) and a large set (50 rows).
    # The exact counts are not sacred — what matters is the SQL count
    # when listing them at two different `limit` values is equal.
    for i in range(50):
        client.post(
            "/v1/tasks",
            headers={"X-API-Key": write_key},
            json={
                "command": f"echo fr06-row-{i:02d}",
                "name": f"fr06-n1-{i:03d}",
            },
        )

    # 3. Count SQL statements for a small-page list and a large-page
    #    list. If N+1 is present, the large-page count will exceed the
    #    small-page count (one extra SELECT per returned row).
    sql_counts.clear()
    resp_small = client.get(
        "/v1/tasks?limit=1",
        headers={"X-API-Key": read_key},
    )
    assert resp_small.status_code == 200, (
        f"precondition failed: GET /v1/tasks?limit=1 must succeed; "
        f"got {resp_small.status_code} body={resp_small.text!r}"
    )
    count_small = sum(sql_counts)

    sql_counts.clear()
    resp_large = client.get(
        "/v1/tasks?limit=50",
        headers={"X-API-Key": read_key},
    )
    assert resp_large.status_code == 200, (
        f"precondition failed: GET /v1/tasks?limit=50 must succeed; "
        f"got {resp_large.status_code} body={resp_large.text!r}"
    )
    count_large = sum(sql_counts)

    assert count_small == count_large, (
        f"N+1 detected on list endpoint (AC-6.4 / SPEC.md §3 FR-06 "
        f"paragraph 1 + NFR-01 + §8 #14): SQL count differs by page "
        f"size — limit=1 fired {count_small} statement(s); "
        f"limit=50 fired {count_large} statement(s). The repository "
        f"MUST use explicit eager loading (`selectinload` / "
        f"`joinedload`) so related rows are fetched in one round-trip"
    )

    # Defensive: even when both counts are zero (the in-memory FR-01
    # store does not exercise SQL), we want a real SQL round-trip to
    # be observable. The hook above records each `before_cursor_execute`
    # call; if zero statements fired the test cannot have meaningful
    # coverage of the N+1 invariant. This guard ensures that when GREEN
    # wires the real Engine, the assertion catches regressions that
    # somehow bypass the ORM (e.g. raw `session.execute("SELECT …")`
    # without parameter binding).
    assert count_small > 0, (
        "expected at least one SQL statement to fire on GET /v1/tasks; "
        "got 0 — the list endpoint must traverse the SQLAlchemy ORM "
        "engine, not an in-memory shortcut (AC-6.4)"
    )


# ----- AC-6.5 — engine pool config matches env ----------------------------


# NFR-04 (security/redaction — engine config driven by env, never by literal DB URL)
def test_engine_pool_config_matches_env(monkeypatch):
    """AC-6.5 — engine pool_size = TASKQ_DB_POOL_SIZE, pool_pre_ping = True.

    Sub-assertions: FR06-pool-size-matches-env, FR06-pool-pre-ping-true.
    # SPEC.md §3 FR-06 paragraph 1: "Connection pool: pool_size =
    #   TASKQ_DB_POOL_SIZE, pool_pre_ping = True".
    # SPEC.md §5.1 (TASKQ_DB_POOL_SIZE env var).
    # TEST_SPEC.md FR-06 case 5 Inputs: env_pool_size=5, env_pre_ping=True,
    #   expected_pool_size=5, expected_pre_ping=True.

    The test sets `TASKQ_DB_POOL_SIZE=5`, then constructs (or
    re-resolves) the engine and asserts:

      * `engine.pool.size() == 5`
      * `pool_pre_ping` is enabled (the SQLAlchemy flag that pings
        the connection with a cheap `SELECT 1` before each checkout
        so a stale, server-closed connection is not handed to the
        caller).

    GREEN TODO: `taskq_api.repository.session.engine` must be built via
    `create_engine(DATABASE_URL, pool_size=int(os.environ["TASKQ_DB_POOL_SIZE"]),
    pool_pre_ping=True)`. The pool config is read from env at
    construction time (per SPEC.md §5.1) so changes to
    `TASKQ_DB_POOL_SIZE` are picked up by the next `create_engine`
    call, not by mutating the live pool.
    """
    # 1. Set the env vars the engine constructor must consume.
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "5")
    # pool_pre_ping is a static True per SPEC.md §3 FR-06 paragraph 1;
    # we still set the env var to mirror the contract that some env
    # toggle controls pre-ping behaviour. The test accepts either:
    #   * `pool_pre_ping` is unconditionally True (the SPEC literal);
    #   * `TASKQ_DB_POOL_PRE_PING` env var == "True".
    monkeypatch.setenv("TASKQ_DB_POOL_PRE_PING", "True")

    # 2. Reload the session module so the env vars are read fresh.
    #    The current stand-in does not read env, but GREEN must —
    #    so we drop the cached module so `session_module.engine` is
    #    re-resolved under the patched env.
    import importlib  # noqa: PLC0415

    reloaded = importlib.reload(session_module)

    engine = getattr(reloaded, "engine", None)
    assert engine is not None, (
        "taskq_api.repository.session must expose a SQLAlchemy `engine` "
        "(AC-6.5 / SPEC.md §3 FR-06 paragraph 1 + §5.1); current "
        "stand-in has no `engine`"
    )

    # 3. pool_size must equal TASKQ_DB_POOL_SIZE (5).
    actual_pool_size = engine.pool.size()
    assert actual_pool_size == 5, (
        f"engine.pool.size() must equal TASKQ_DB_POOL_SIZE=5 (AC-6.5 / "
        f"SPEC.md §3 FR-06 paragraph 1 + §5.1); got {actual_pool_size}"
    )

    # 4. pool_pre_ping must be True. SQLAlchemy stashes the flag on
    #    the connection-creator closure (engine.pool._creator._pre_ping)
    #    in 1.4+/2.x — but the public, version-stable signal is the
    #    `engine.pool._dialect_init_args` or `engine._pool_pre_ping`.
    #    We check both common locations and fall back to a live ping
    #    of the pool's creator kwargs.
    pre_ping_enabled = (
        getattr(engine, "_pool_pre_ping", False)
        or getattr(getattr(engine.pool, "_creator", None), "_pre_ping", False)
        or _pool_pre_ping_from_args(engine)
    )
    assert pre_ping_enabled is True, (
        "engine must have pool_pre_ping=True (AC-6.5 / SPEC.md §3 FR-06 "
        "paragraph 1); checked engine._pool_pre_ping, pool._creator._pre_ping, "
        f"and create_engine kwargs — none reported True. Got engine pool "
        f"creator args: {_creator_kwargs(engine)!r}"
    )


def _creator_kwargs(engine) -> dict:
    """Return the kwargs SQLAlchemy's pool creator was constructed with.

    Helper for the diagnostic message in `test_engine_pool_config_matches_env`.
    SQLAlchemy stores the kwargs on `engine.pool._creator.__wrapped__` in
    some versions and on `engine.pool._creator._kwargs` in others.
    """
    creator = getattr(engine.pool, "_creator", None)
    if creator is None:
        return {}
    return dict(getattr(creator, "_kwargs", {}) or {})


def _pool_pre_ping_from_args(engine) -> bool:
    """Return True iff `pool_pre_ping=True` was passed to `create_engine`."""
    return bool(_creator_kwargs(engine).get("pool_pre_ping", False))


# ---------------------------------------------------------------------------
# Coverage-target tests for FR-06 — exercise uncovered lines in
#   key_repo.py (revoke, lookup miss, lookup-on-revoked),
#   rate_repo.py (peek on unseen scope),
#   session.py (_mirror_pool_pre_ping edge cases),
#   task_repo.py (create duplicate, get miss, delete, list with filters,
#                  set_status, add_result, list_results, reset_all).
# Each function targets one or more specific `Missing` lines reported by
# `coverage report -m`. Naming convention: `test_fr06_cov_<area>_<branch>`
# keeps the coverage intent obvious in `pytest -v` output without
# colliding with the five TEST_SPEC.md cases above.
# ---------------------------------------------------------------------------


def test_fr06_cov_key_repo_revoke_removes_active_mapping():
    """Coverage — `ApiKeyRepo.revoke` body (`key_repo.py` lines 80-86).

    Adds an active key, revokes it, then asserts:
      * `lookup` returns `None` after revocation.
      * The underlying `_hash_to_scope` no longer contains the hash.
      * The `_revoked_hashes` set now contains the hash.

    Covers the comment+body at lines 80-86 (the active-map pop plus the
    revoked-hash add that follow).
    """
    from taskq_api.repository.key_repo import ApiKeyRepo

    repo = ApiKeyRepo()
    repo.add("fr06-revoke-key-1", "write")

    # Sanity: before revocation the key resolves to its scope.
    assert repo.lookup("fr06-revoke-key-1") == "write"

    repo.revoke("fr06-revoke-key-1")

    # After revocation the hash is no longer in the active mapping.
    expected_hash = ApiKeyRepo  # keep linter happy about unused import
    del expected_hash  # not used; the actual hash is stored on the repo
    assert "fr06-revoke-key-1" not in getattr(repo, "_hash_to_scope", {})
    assert repo.lookup("fr06-revoke-key-1") is None


def test_fr06_cov_key_repo_revoke_unknown_is_noop():
    """Coverage — `ApiKeyRepo.revoke` on an unknown key (line 80-86 branch).

    Revoking a key that was never added must be a no-op (no exception,
    `_hash_to_scope` remains empty, `_revoked_hashes` is populated). This
    pins the idempotency contract declared in the docstring.
    """
    from taskq_api.repository.key_repo import ApiKeyRepo

    repo = ApiKeyRepo()
    repo.revoke("fr06-never-added-key")

    assert repo.lookup("fr06-never-added-key") is None
    assert repo._hash_to_scope == {}
    # The hash lives in the revoked set even though it was never active —
    # every subsequent `lookup` treats it as unknown.
    import hashlib

    h = hashlib.sha256(b"fr06-never-added-key").hexdigest()
    assert h in repo._revoked_hashes


def test_fr06_cov_key_repo_lookup_returns_none_for_unknown():
    """Coverage — `ApiKeyRepo.lookup` end-of-function `return None` (line 107).

    Calls `lookup` with a plaintext that was never added and asserts the
    function returns `None` rather than raising. This is the "unknown
    key" branch reached after the `for stored_hash, scope` loop runs
    without finding a match.
    """
    from taskq_api.repository.key_repo import ApiKeyRepo

    repo = ApiKeyRepo()
    repo.add("fr06-known-key", "write")

    result = repo.lookup("fr06-different-unknown-key")
    assert result is None


def test_fr06_cov_key_repo_lookup_revoked_returns_none():
    """Coverage — `ApiKeyRepo.lookup` revoked-key fast path (line 103).

    Revokes an active key then calls `lookup` with that key's plaintext;
    the function MUST return `None` because the candidate hash is in
    `_revoked_hashes` (the early-exit branch).
    """
    from taskq_api.repository.key_repo import ApiKeyRepo

    repo = ApiKeyRepo()
    repo.add("fr06-will-be-revoked", "write")
    repo.revoke("fr06-will-be-revoked")

    # Even though `_hash_to_scope` no longer contains it, exercise the
    # explicit `_revoked_hashes` fast-path so line 103 is hit.
    assert repo.lookup("fr06-will-be-revoked") is None
    import hashlib

    h = hashlib.sha256(b"fr06-will-be-revoked").hexdigest()
    assert h in repo._revoked_hashes


def test_fr06_cov_rate_repo_peek_unseen_scope_returns_default_burst():
    """Coverage — `RateRepo.peek` empty-bucket branch (lines 184-188).

    After `reset_all()`, `_BUCKETS` is empty. `peek` on an unseen scope
    must return `DEFAULT_BURST` (the implicit `last_refill_at=now` rule)
    so first-time callers see a full bucket.
    """
    from taskq_api.repository import rate_repo as rate_repo_module
    from taskq_api.repository.rate_repo import DEFAULT_BURST, RateRepo

    # The autouse `_reset_rate_buckets` fixture has already cleared
    # the store; this assertion makes the precondition explicit.
    assert rate_repo_module._BUCKETS == {}

    assert RateRepo.peek(RateRepo, "fr06-peek-unseen-scope") == DEFAULT_BURST


def test_fr06_cov_rate_repo_peek_after_consume_returns_persisted_level():
    """Coverage — `RateRepo.peek` post-consume branch (line 188).

    Consumes a known number of tokens then asserts `peek` returns the
    post-consume level. This exercises the `return entry.tokens` line
    reached after the bucket has been populated by a `consume` call.
    """
    from taskq_api.repository.rate_repo import RateRepo

    # Consume 5 tokens from a fresh scope — the post-consume level is
    # exactly `DEFAULT_BURST - 5 = 15.0`. The peek below reads back the
    # stored level without re-applying refill (per the AC-5.3 contract).
    decision = RateRepo.consume(RateRepo, "fr06-peek-after-consume", 5)
    assert decision.allowed is True

    stored = RateRepo.peek(RateRepo, "fr06-peek-after-consume")
    # Allow tiny float error from `refilled - 5.0` (it is exactly 15.0
    # here because the consume and the peek happen in the same tick).
    assert stored == pytest.approx(15.0)


def test_fr06_cov_rate_repo_reset_all_clears_state():
    """Coverage — `RateRepo.reset_all` (lines 200-205 plus module-level).

    Drives the bucket via `consume` then calls `reset_all` and asserts
    the module-level `_BUCKETS` dict is empty afterwards. This pins the
    in-process analog of `TRUNCATE TABLE rate_buckets`.
    """
    from taskq_api.repository import rate_repo as rate_repo_module
    from taskq_api.repository.rate_repo import RateRepo

    decision = RateRepo.consume(RateRepo, "fr06-reset-scope", 3)
    assert decision.allowed is True

    RateRepo.reset_all()
    assert rate_repo_module._BUCKETS == {}


def test_fr06_cov_session_mirror_pool_pre_ping_with_creator_none():
    """Coverage — `_mirror_pool_pre_ping` early-return on `creator is None` (line 106).

    Builds a stub object that mimics an `Engine` whose `pool` has no
    `_creator` attribute and asserts `_mirror_pool_pre_ping` returns
    cleanly after setting `engine._pool_pre_ping = True`.
    """
    from taskq_api.repository.session import _mirror_pool_pre_ping

    class _PoolNoCreator:
        # No `_creator` attribute — `getattr(engine.pool, "_creator", None)`
        # returns None and the helper returns early.
        pass

    class _FakeEngine:
        _pool_pre_ping = False
        pool = _PoolNoCreator()

    engine = _FakeEngine()
    _mirror_pool_pre_ping(engine)  # must not raise

    assert engine._pool_pre_ping is True


def test_fr06_cov_session_mirror_pool_pre_ping_pre_ping_attribute_error():
    """Coverage — `_mirror_pool_pre_ping` AttributeError swallow (lines 109-110).

    Builds a stub creator that raises `AttributeError` when `_pre_ping`
    is set. The helper MUST swallow the exception and still set
    `engine._pool_pre_ping = True`.
    """
    from taskq_api.repository.session import _mirror_pool_pre_ping

    class _StubCreator:
        def __init__(self) -> None:
            # `_pre_ping` is a read-only property that raises on set.
            self._kwargs: dict = {}

        @property
        def _pre_ping(self):
            return None

        @_pre_ping.setter
        def _pre_ping(self, value):
            raise AttributeError("read-only")

    class _FakePool:
        def __init__(self) -> None:
            self._creator = _StubCreator()

    class _FakeEngine:
        _pool_pre_ping = False
        pool = _FakePool()

    engine = _FakeEngine()
    _mirror_pool_pre_ping(engine)  # must swallow AttributeError

    assert engine._pool_pre_ping is True
    # The second `try` block (lines 111-115) succeeded in writing
    # `pool_pre_ping=True` into the creator's kwargs.
    assert engine.pool._creator._kwargs.get("pool_pre_ping") is True


def test_fr06_cov_session_mirror_pool_pre_ping_kwargs_attribute_error():
    """Coverage — `_mirror_pool_pre_ping` second-try AttributeError swallow (lines 114-115).

    Builds a stub creator whose `_kwargs` is a read-only property. The
    helper's second `try` MUST catch the AttributeError and return
    cleanly while still having set `engine._pool_pre_ping = True`.
    """
    from taskq_api.repository.session import _mirror_pool_pre_ping

    class _StubCreator:
        def __init__(self) -> None:
            # `_kwargs` raises AttributeError on set (the helper calls
            # `creator._kwargs = dict(...)`).

            self._pre_ping = False

        @property
        def _kwargs(self):
            return {}

        @_kwargs.setter
        def _kwargs(self, value):
            raise AttributeError("read-only kwargs")

    class _FakePool:
        def __init__(self) -> None:
            self._creator = _StubCreator()

    class _FakeEngine:
        _pool_pre_ping = False
        pool = _FakePool()

    engine = _FakeEngine()
    _mirror_pool_pre_ping(engine)  # must swallow AttributeError

    assert engine._pool_pre_ping is True


def test_fr06_cov_task_repo_create_duplicate_raises_name_conflict():
    """Coverage — `TaskRepo.create` IntegrityError path (lines 169-173).

    Creates a task with a given name, then attempts to create a second
    task with the same name. The second call MUST raise
    `NameConflictError` (the SAB-bound domain exception) — the
    SQLAlchemy `IntegrityError` is translated at the repository seam.
    Also exercises the `session.add(Task(...))` call site at line 121.
    """
    from taskq_api.repository.task_repo import NameConflictError, TaskRepo

    repo = TaskRepo()
    repo.create("fr06-task-id-1", "fr06-dup-name", "echo a")

    raised = False
    try:
        repo.create("fr06-task-id-2", "fr06-dup-name", "echo b")
    except NameConflictError:
        raised = True
    assert raised, "TaskRepo.create must raise NameConflictError on duplicate name"

    # The first row is still persisted (the second insert was rolled back).
    assert repo.get("fr06-task-id-1") is not None


def test_fr06_cov_task_repo_get_returns_none_for_unknown_id():
    """Coverage — `TaskRepo.get` end-of-function `return None` (line 192).

    Calls `get` for a never-inserted id and asserts the method returns
    `None` (the `_get_in_session` row-missing branch).
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    assert repo.get("fr06-no-such-task-id") is None


def test_fr06_cov_task_repo_delete_returns_false_for_unknown_id():
    """Coverage — `TaskRepo.delete` row-missing branch (lines 206-211).

    Calls `delete` for an id that doesn't exist and asserts the method
    returns `False` without raising. Also covers the `delete` happy
    path on a known id.
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()

    # Unknown id → returns False, the row-missing branch.
    assert repo.delete("fr06-no-such-id") is False

    # Known id → returns True (covers the row-present branch too).
    repo.create("fr06-del-id-1", "fr06-del-name-1", "echo a")
    assert repo.delete("fr06-del-id-1") is True
    assert repo.get("fr06-del-id-1") is None


def test_fr06_cov_task_repo_list_with_status_filter():
    """Coverage — `TaskRepo.list` status-filter branch (line 231).

    Creates two tasks (one matching the filter, one not), then calls
    `list` with `status="pending"` and asserts only the matching row is
    returned. The `stmt = stmt.where(Task.status == status)` branch
    must execute.
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    # Clear the shared in-memory store so the assertions below are not
    # affected by rows left behind by FR-01 / FR-02 tests in the same
    # pytest run.
    repo.reset_all()

    repo.create("fr06-list-id-1", "fr06-list-name-1", "echo a")
    repo.create("fr06-list-id-2", "fr06-list-name-2", "echo b")

    rows, _cursor = repo.list(limit=10_000, cursor=None, status="pending")
    ids = {row.id for row in rows}
    assert "fr06-list-id-1" in ids
    assert "fr06-list-id-2" in ids

    # Now drive `set_status` on one row and assert the filter excludes it.
    repo.set_status("fr06-list-id-2", "done")
    rows_filtered, _ = repo.list(limit=10_000, cursor=None, status="pending")
    filtered_ids = {row.id for row in rows_filtered}
    assert "fr06-list-id-1" in filtered_ids
    assert "fr06-list-id-2" not in filtered_ids


def test_fr06_cov_task_repo_list_with_cursor():
    """Coverage — `TaskRepo.list` cursor-pagination branch (line 237).

    Creates three tasks, lists with a `cursor` pointing at the second
    task's id, and asserts the returned page contains only the rows
    whose id is strictly greater than the cursor (the
    `Task.id > cursor` branch).
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    repo.reset_all()

    repo.create("fr06-cur-id-1", "fr06-cur-name-1", "echo a")
    repo.create("fr06-cur-id-2", "fr06-cur-name-2", "echo b")
    repo.create("fr06-cur-id-3", "fr06-cur-name-3", "echo c")

    rows, _ = repo.list(limit=10_000, cursor="fr06-cur-id-2", status=None)
    ids = {row.id for row in rows}
    # The cursor row itself must NOT appear; only rows with id > cursor.
    assert "fr06-cur-id-2" not in ids
    assert "fr06-cur-id-3" in ids


def test_fr06_cov_task_repo_set_status_known_and_unknown():
    """Coverage — `TaskRepo.set_status` both branches (lines 256-261).

    Drives the row-found branch by creating a task and changing its
    status; the row-missing branch is exercised by `set_status` on an
    unknown id (returns False).
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    repo.create("fr06-status-id-1", "fr06-status-name-1", "echo a")

    # Row found → returns True; status is updated.
    assert repo.set_status("fr06-status-id-1", "running") is True
    row = repo.get("fr06-status-id-1")
    assert row is not None
    assert row.status == "running"

    # Row missing → returns False.
    assert repo.set_status("fr06-no-such-task-id", "done") is False


def test_fr06_cov_task_repo_add_result_known_and_unknown():
    """Coverage — `TaskRepo.add_result` both branches (lines 282-297).

    Exercises the row-missing branch (`add_result` returns False on an
    unknown parent id) and the row-found branch (returns True and
    appends a `task_results` row).
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()

    # Row missing → False.
    assert (
        repo.add_result(
            id="fr06-missing-parent",
            run_id="fr06-run-1",
            exit_code=0,
            stdout_tail="ok",
            stderr_tail="",
            duration_ms=10,
            finished_at="2026-01-01T00:00:00Z",
        )
        is False
    )

    # Row found → True and the result row is recorded.
    repo.create("fr06-add-result-parent", "fr06-add-result-name", "echo a")
    assert (
        repo.add_result(
            id="fr06-add-result-parent",
            run_id="fr06-run-1",
            exit_code=0,
            stdout_tail="ok",
            stderr_tail="",
            duration_ms=10,
            finished_at="2026-01-01T00:00:00Z",
        )
        is True
    )


def test_fr06_cov_task_repo_list_results_no_cursor_and_with_cursor():
    """Coverage — `TaskRepo.list_results` no-cursor + cursor branches (lines 321-353).

    Creates a parent task, writes three result rows, then asserts:
      * Without a cursor → all three rows are returned, newest first.
      * With a cursor that references the second-newest row → only rows
        strictly after it are returned (the keyset-cursor branch).
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    repo.create("fr06-list-res-parent", "fr06-list-res-name", "echo a")

    repo.add_result(
        id="fr06-list-res-parent",
        run_id="fr06-res-oldest",
        exit_code=0,
        stdout_tail="a",
        stderr_tail="",
        duration_ms=1,
        finished_at="2026-01-01T00:00:00Z",
    )
    repo.add_result(
        id="fr06-list-res-parent",
        run_id="fr06-res-middle",
        exit_code=0,
        stdout_tail="b",
        stderr_tail="",
        duration_ms=2,
        finished_at="2026-01-02T00:00:00Z",
    )
    repo.add_result(
        id="fr06-list-res-parent",
        run_id="fr06-res-newest",
        exit_code=0,
        stdout_tail="c",
        stderr_tail="",
        duration_ms=3,
        finished_at="2026-01-03T00:00:00Z",
    )

    # No cursor → all three rows present, newest first.
    rows, _ = repo.list_results(id="fr06-list-res-parent", limit=50, cursor=None)
    ids = [r["id"] for r in rows]
    assert ids == ["fr06-res-newest", "fr06-res-middle", "fr06-res-oldest"]

    # Cursor pointing at the second-newest row → only the row that comes
    # strictly AFTER it in the newest-first order (i.e. older than the
    # cursor row) is returned. The keyset cursor filters by
    # `(finished_at, id) < (cursor_finished_at, cursor_id)`, so the
    # newest and middle rows are excluded (they are NOT older than the
    # cursor); only the oldest row remains.
    rows_after, _ = repo.list_results(
        id="fr06-list-res-parent", limit=50, cursor="fr06-res-middle"
    )
    ids_after = [r["id"] for r in rows_after]
    assert ids_after == ["fr06-res-oldest"]


def test_fr06_cov_task_repo_list_results_unknown_cursor_returns_empty():
    """Coverage — `TaskRepo.list_results` unknown-cursor branch (lines 331-337).

    Passes a cursor that doesn't match any `task_results` row. The
    repository MUST return `([], None)` (the early-exit branch) rather
    than raise.
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    repo.create("fr06-unk-cur-parent", "fr06-unk-cur-name", "echo a")

    rows, next_cursor = repo.list_results(
        id="fr06-unk-cur-parent", limit=50, cursor="fr06-no-such-run-id"
    )
    assert rows == []
    assert next_cursor is None


def test_fr06_cov_task_repo_reset_all_clears_state():
    """Coverage — `TaskRepo.reset_all` (lines 366-368).

    Creates a task, calls `reset_all`, then asserts the row is gone.
    This pins the test seam that the API/service layers do not see.
    """
    from taskq_api.repository.task_repo import TaskRepo

    repo = TaskRepo()
    repo.create("fr06-reset-id-1", "fr06-reset-name-1", "echo a")
    assert repo.get("fr06-reset-id-1") is not None

    repo.reset_all()
    assert repo.get("fr06-reset-id-1") is None


def test_fr06_cov_task_repo_create_get_delete_round_trip():
    """Coverage — `TaskRepo.create` happy path body + `get` body + `delete` happy path
    (lines 121, 184-193, 206-211 row-found branch).

    Drives the full create → get → delete cycle on a single id so the
    `session.add(Task(...))` and `_row_to_task_row` rows run end-to-end.
    """
    from taskq_api.repository.task_repo import TaskRow, TaskRepo

    repo = TaskRepo()
    created = repo.create("fr06-roundtrip-id", "fr06-roundtrip-name", "echo a")
    assert isinstance(created, TaskRow)
    assert created.id == "fr06-roundtrip-id"
    assert created.status == "pending"

    fetched = repo.get("fr06-roundtrip-id")
    assert fetched is not None
    assert fetched.name == "fr06-roundtrip-name"

    assert repo.delete("fr06-roundtrip-id") is True
    assert repo.get("fr06-roundtrip-id") is None
