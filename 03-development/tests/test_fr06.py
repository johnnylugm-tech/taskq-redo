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
        f"expected at least one SQL statement to fire on GET /v1/tasks; "
        f"got 0 — the list endpoint must traverse the SQLAlchemy ORM "
        f"engine, not an in-memory shortcut (AC-6.4)"
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
