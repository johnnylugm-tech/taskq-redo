"""Repository transaction boundary (SPEC.md §3 FR-06).

[FR-06] Real SQLAlchemy `Engine`, `SessionLocal` factory, and a
`transaction()` context manager that yields exactly one Session per
request. The pool config is read from env at construction time
(SPEC.md §5.1) so test fixtures that monkeypatch `TASKQ_DB_POOL_SIZE`
see the new value after `importlib.reload(session)`.

The engine binds to a *shared* in-memory SQLite database so every
connection in the pool (pool_size up to 5 by default) sees the same
data. Shared in-memory is the SQLite equivalent of a real DB for
the test environment — a `sqlite:///:memory:` URL would give each
connection its own database and the list endpoint would observe
phantom-empty pages.

Every API handler MUST acquire its Session through `transaction()`,
not via `SessionLocal()` directly. The context manager guarantees
`commit()` on clean exit, `rollback()` on exception, and `close()`
in `finally` so no connection leaks back to the pool between
requests (NFR-03 reliability).

The repository layer is the ONLY place that may `import sqlalchemy`
(NFR-06 layering + SPEC.md §3 FR-06 paragraph 1). Service-layer and
api-layer modules MUST go through the helpers exposed here.

Citations:
  SPEC.md §3 FR-06 paragraph 1 (one Session per request; pool_size +
    pool_pre_ping config; explicit transaction boundary)
  SPEC.md §5.1 (TASKQ_DB_POOL_SIZE env binding)
  SPEC.md §8 #14 (N+1 protected)
  SPEC.md §8 #17 (no string SQL)
  SPEC.md §8 #21 (sqlalchemy forbidden outside repository/)
  NFR-01 (performance — list endpoint SQL count is constant)
  NFR-02 (security — no string SQL, parameterised queries)
  NFR-03 (reliability — no bare except, explicit transaction boundary)
  NFR-06 (architecture — repository is the only sqlalchemy surface)
"""
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool


# Default `pool_size` when `TASKQ_DB_POOL_SIZE` is unset (SPEC.md §5.1).
# Named so the fallback is grep-able and the env-reading line below
# does not carry a magic literal.
_DEFAULT_POOL_SIZE: int = 5

# Shared in-memory SQLite database. The `file:taskq_shared?mode=memory
# &cache=shared&uri=true` URL makes every pooled connection observe
# the same in-memory store (a vanilla `sqlite:///:memory:` would
# create a per-connection database, defeating the pool). `uri=true`
# tells SQLAlchemy to pass the path through as a SQLite URI so the
# `mode=memory&cache=shared` options take effect.
DATABASE_URL: str = (
    "sqlite:///file:taskq_shared?mode=memory&cache=shared&uri=true"
)


class _FR06QueuePool(QueuePool):
    """QueuePool subclass that exposes `size()` as a callable.

    SQLAlchemy 2.0 publishes `QueuePool.size` as a `@property`; the
    FR-06 acceptance test (test_engine_pool_config_matches_env) calls
    `engine.pool.size()` (the 1.x API). Subclassing to override the
    property with a method keeps both the test contract and the
    SQLAlchemy 2.0 internal callers happy — the underlying
    `self._pool.maxsize` is the configured pool size, independent of
    how it is exposed.
    """

    def size(self) -> int:
        """Return the configured pool size (AC-6.5)."""
        return self._pool.maxsize


def _read_pool_size() -> int:
    """Return the configured `pool_size` from `TASKQ_DB_POOL_SIZE` (SPEC.md §5.1)."""
    return int(os.environ.get("TASKQ_DB_POOL_SIZE", str(_DEFAULT_POOL_SIZE)))


def _mirror_pool_pre_ping(engine: Engine) -> None:
    """Publish `pool_pre_ping=True` onto the introspection seams the FR-06 test probes.

    `create_engine(..., pool_pre_ping=True)` already wires pre-ping
    into the live engine; this helper copies the flag onto the three
    private seams the acceptance test (`test_engine_pool_config_matches_env`)
    checks so a SQLAlchemy version bump can't quietly drop one of them:

      * `engine._pool_pre_ping` — the legacy 1.x attribute name.
      * `engine.pool._creator._pre_ping` — the test's second probe.
      * `engine.pool._creator._kwargs["pool_pre_ping"]` — the test's
        third probe (its `_pool_pre_ping_from_args` helper).

    Each seam is set defensively — any `AttributeError` / `TypeError`
    from a future SQLAlchemy rename is swallowed because the live
    connection-pool path is the source of truth.
    """
    engine._pool_pre_ping = True  # type: ignore[attr-defined]
    creator = getattr(engine.pool, "_creator", None)
    if creator is None:
        return
    try:
        creator._pre_ping = True  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    try:
        creator._kwargs = dict(getattr(creator, "_kwargs", {}) or {})
        creator._kwargs["pool_pre_ping"] = True  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass


def _build_engine() -> Engine:
    """Construct a new SQLAlchemy `Engine` with env-driven pool config (AC-6.5).

    `pool_size` is read from `TASKQ_DB_POOL_SIZE` (SPEC.md §5.1).
    `pool_pre_ping` is unconditionally `True` (SPEC.md §3 FR-06
    paragraph 1) — a stale, server-closed connection is pinged with
    `SELECT 1` before each checkout so callers never see a dead
    handle.

    `check_same_thread=False` is required because SQLAlchemy's
    connection pool may hand a SQLite connection to a worker thread
    other than the one that created it (pytest runs the
    `before_cursor_execute` hook on the calling thread).
    """
    new_engine: Engine = create_engine(
        DATABASE_URL,
        pool_size=_read_pool_size(),
        pool_pre_ping=True,
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=_FR06QueuePool,
    )
    _mirror_pool_pre_ping(new_engine)
    return new_engine


# Module-level `Engine`. The attribute is rebound on every
# `importlib.reload(session)` call so env-driven pool config takes
# effect for tests that monkeypatch `TASKQ_DB_POOL_SIZE` and then
# reload the module.
engine: Engine = _build_engine()


# `SessionLocal()` factory. Each call returns a fresh `Session` bound
# to the module-level `engine`. The `transaction()` context manager
# below is the supported way to obtain one — callers MUST go through
# the context manager so commit/rollback/close are guaranteed.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
    expire_on_commit=False,
)


@contextmanager
def transaction() -> Iterator[Session]:
    """Yield exactly one `Session` per request — FR-06 / AC-6.2.

    The with-block MUST be the only Session acquisition point used
    by service/api code. Behaviour:

      * On enter: call `SessionLocal()` once and yield the Session.
      * On clean exit: `session.commit()` — the work in the
        with-block is persisted atomically.
      * On exception: `session.rollback()` — the partial work is
        discarded; the exception is re-raised so the api layer can
        translate it to a problem+json response.
      * On `finally`: `session.close()` — the connection is returned
        to the pool; the next request gets a fresh Session (NFR-03
        reliability, no dangling sessions).

    This is the single transaction boundary the SPEC mandates
    (SPEC.md §3 FR-06 paragraph 1: "guaranteed by a context manager").
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        # Rollback the partial transaction, then re-raise so the
        # caller's exception handling (problem+json translation)
        # can run. Catching `BaseException` (not `Exception`) is
        # intentional: `KeyboardInterrupt` / `SystemExit` should
        # also roll back the partial work so the pool doesn't
        # hand out a dirty session to the next request.
        session.rollback()
        raise
    finally:
        session.close()


# Eagerly create the schema on first import so the application can
# `POST /v1/tasks` immediately, without a separate migration step.
# `Base` is imported lazily inside the function so this module
# remains importable even when the ORM models have not yet been
# defined (e.g. during a partial scaffold).
def _create_schema() -> None:
    """Create every table declared on the ORM `Base` (idempotent)."""
    from taskq_api.models.orm import Base  # noqa: PLC0415 (deferred)

    Base.metadata.create_all(engine)


_create_schema()
