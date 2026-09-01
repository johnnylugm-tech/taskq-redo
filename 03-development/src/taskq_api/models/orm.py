"""SQLAlchemy ORM models (SPEC.md §3 FR-06 schema boundary).

[FR-06] Every persisted table the repository layer reads or writes
is declared here. The `models` package is the only layer that defines
schema; the repository layer queries these models through the
`taskq_api.repository.session.transaction()` context manager
(SPEC.md §3 FR-06 paragraph 1 + NFR-06 architecture_constraints).

The four tables map one-to-one to the FR-01 / FR-02 / FR-03 / FR-05
persisted resources:

  * `tasks`          — FR-01 row schema.
  * `task_results`   — FR-02 per-run result rows.
  * `api_keys`       — FR-03 SHA-256 hash → scope mapping.
  * `rate_buckets`   — FR-05 per-scope token-bucket row.

The `Task.results` relationship is the eager-load target the
list endpoint uses (`selectinload(Task.results)`) to keep the
SQL count constant per page (NFR-01 + SPEC.md §8 #14 N+1 guard).

`cascade="all, delete-orphan"` on `Task.results` is the schema-level
expression of SPEC.md §3 FR-01 row 4 — removing a parent drops its
`task_results` rows in the same transaction.

Plaintext API keys MUST NEVER reach this table (NFR-02 + AC-3.3);
only the 64-hex SHA-256 digest is stored in `api_keys.key_hash`.

Citations:
  SPEC.md §3 FR-01 (tasks table, transactional cascade)
  SPEC.md §3 FR-02 (task_results table + state machine)
  SPEC.md §3 FR-03 (api_keys table — hash only)
  SPEC.md §3 FR-05 (rate_buckets table)
  SPEC.md §3 FR-06 paragraph 1 (repository is the only sqlalchemy seam)
  SPEC.md §8 #14 (N+1 guard via selectinload)
  NFR-01 (performance — constant SQL count)
  NFR-02 (security — no plaintext keys; parameterised queries)
  NFR-06 (architecture — schema in models/, queries in repository/)
"""
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

# pragma: no error-handling


# `Base` is the declarative root every ORM class inherits from. The
# session module (`taskq_api.repository.session`) calls
# `Base.metadata.create_all(engine)` at import time so the schema
# is ready before the first request lands.
Base = declarative_base()


class Task(Base):  # type: ignore[misc, valid-type]
    """[FR-01] tasks table — primary row for a task.

    SPEC.md §3 FR-01 row 4: removing a `Task` also drops its
    `task_results` rows in the same transaction. The
    `cascade="all, delete-orphan"` setting on the `results`
    relationship expresses that invariant at the schema level —
    SQLAlchemy emits the child `DELETE` in the same transaction
    as the parent.

    `Task.results` is the eager-load target for the list endpoint:
    the repository's `list(...)` query uses
    `options(selectinload(Task.results))` so all related rows are
    fetched in one round-trip regardless of page size (NFR-01 +
    SPEC.md §8 #14 N+1 guard).
    """

    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)
    command = Column(String, nullable=False)
    # Default `pending` matches the SPEC's initial state for a
    # freshly-created task (SPEC.md §3 FR-02 state machine entry
    # condition).
    status = Column(String, nullable=False, default="pending")

    results = relationship(
        "TaskResult",
        back_populates="task",
        cascade="all, delete-orphan",
        # `select` (lazy=True at attribute access) is the default; the
        # repository overrides it with `selectinload` in list()
        # queries to keep the SQL count constant per page (NFR-01).
        lazy="select",
    )


class TaskResult(Base):  # type: ignore[misc, valid-type]
    """[FR-02] task_results table — per-run result row.

    The five required columns per SPEC.md §3 FR-02 are
    `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`,
    `finished_at`. The primary key is the `run_id` (passed in from
    the service layer) so the cursor in `list_results` can
    reference an individual row.
    """

    __tablename__ = "task_results"

    id = Column(String, primary_key=True)
    task_id = Column(
        String,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    exit_code = Column(Integer, nullable=True)
    stdout_tail = Column(Text, nullable=False, default="")
    stderr_tail = Column(Text, nullable=False, default="")
    duration_ms = Column(Integer, nullable=False, default=0)
    finished_at = Column(String, nullable=False)

    task = relationship("Task", back_populates="results")


class ApiKey(Base):  # type: ignore[misc, valid-type]
    """[FR-03] api_keys table — SHA-256 hash → scope mapping.

    Plaintext is NEVER stored in this table (NFR-02 + AC-3.3);
    only the 64-hex SHA-256 digest lives in `key_hash`. The
    `revoked` flag is the AC-3.5 revocation marker; `lookup`
    treats a row with `revoked=True` as unknown.
    """

    __tablename__ = "api_keys"

    key_hash = Column(String, primary_key=True)
    scope = Column(String, nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)


class RateBucket(Base):  # type: ignore[misc, valid-type]
    """[FR-05] rate_buckets table — per-scope token-bucket row.

    SPEC.md §3 FR-05 paragraph 1 ("存於資料庫") requires the
    bucket state to be shared across workers. `scope` is the
    primary key; `tokens` is the post-refill, post-consume level;
    `last_refill_at` is a UNIX `time.monotonic()` value so the
    refill math is monotonic and not affected by wall-clock
    adjustments.
    """

    __tablename__ = "rate_buckets"

    scope = Column(String, primary_key=True)
    tokens = Column(Float, nullable=False, default=20.0)
    last_refill_at = Column(Float, nullable=False, default=0.0)
