"""Task repository — storage for `tasks` rows and their `task_results`
cascade (SPEC.md §3 FR-01 row 4 + FR-02 v3 schema + FR-06 SQL
persistence).

[FR-01] The repository owns the persisted `tasks` row. Public methods
expose the same shape as the previous in-memory implementation so the
service / api layers do not change:

  * `create(id, name, command) -> TaskRow`  — raises `NameConflictError`
    on a duplicate `name` (the unique constraint is enforced at the
    database level; the repo translates `IntegrityError` into the
    SAB-bound domain exception).
  * `get(id) -> Optional[TaskRow]`
  * `delete(id) -> bool` — removes the parent AND its `task_results`
    rows in the same transaction (cascade via the ORM relationship).
  * `list(limit, cursor, status) -> tuple[list[TaskRow], Optional[str]]`
    — paginated by id with optional `status` filter.
  * `set_status(id, status) -> bool`
  * `add_result(...)` — appends a `task_results` row.
  * `list_results(id, limit, cursor)` — newest first.

[FR-02] Adds the result-row contract declared in SPEC.md §3 FR-02:
the result row carries `exit_code`, `stdout_tail`, `stderr_tail`,
`duration_ms`, `finished_at`. The `id` column is the `run_id` so
the cursor in `list_results` can reference an individual row.

[FR-06] This module is the SQLAlchemy seam for `tasks` /
`task_results`. The list query uses `selectinload(Task.results)` so
the SQL statement count is constant per page (NFR-01 + SPEC.md §8
#14 N+1 guard). The repository is the ONLY place that may reference
SQLAlchemy (NFR-06); the service layer never holds a Session
directly (SPEC.md §3 FR-06 paragraph 1: "the business layer must not
hold a Session directly").

Citations:
  SPEC.md §3 FR-01 (row 4 "delete with results row, same transaction")
  SPEC.md §3 FR-02 ("task_results" table + state machine)
  SPEC.md §3 FR-06 paragraph 1 (repository owns the SQL surface)
  SPEC.md §8 #14 (N+1 protected via selectinload)
  TEST_SPEC.md FR-01 case 7 (delete + cascade in one transaction)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from taskq_api.models.orm import Task, TaskResult
from taskq_api.repository.session import transaction


# State machine constants (SPEC.md §3 FR-02). Defined here so the
# repository — the single owner of the `status` field — is the single
# source of truth; the runner and service import these instead of
# repeating string literals.
STATUS_PENDING: str = "pending"
STATUS_RUNNING: str = "running"
STATUS_DONE: str = "done"
STATUS_FAILED: str = "failed"
STATUS_TIMEOUT: str = "timeout"
# [FR-08] Graceful-drain terminal status. Assigned to tasks still
# in-flight when `BackgroundRunner.shutdown()` elapses
# `TASKQ_DRAIN_TIMEOUT` (SPEC.md §3 FR-08 AC-8.4).
STATUS_INTERRUPTED: str = "interrupted"

# All valid states, in transition order. Useful for input validation
# at the api boundary.
ALL_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_PENDING,
        STATUS_RUNNING,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_TIMEOUT,
        STATUS_INTERRUPTED,
    }
)

# Tail length (chars) kept for stdout/stderr in a task_results row.
# SPEC.md §3 FR-02 requires the column to exist; bounding the size
# protects the in-memory store from unbounded output.
_TAIL_CHARS: int = 2000


@dataclass(frozen=True)
class TaskRow:
    """Immutable snapshot of a task row exposed to the service layer."""

    id: str
    name: str
    command: str
    status: str = STATUS_PENDING


@dataclass(frozen=True)
class TaskResultRow:
    """Immutable snapshot of a `task_results` row (SPEC.md §3 FR-02).

    The five required columns per the SPEC are `exit_code`,
    `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`. The
    `id` column mirrors `run_id` (the task_results primary key) so the
    cursor can identify individual rows in the history view.
    """

    id: str
    task_id: str
    exit_code: Optional[int]
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    finished_at: str


class NameConflictError(Exception):
    """Raised by `create` when the supplied name is already taken (AC-1.4)."""


def _row_to_task_row(row: Task) -> TaskRow:
    """Convert an ORM `Task` into the immutable `TaskRow` dataclass."""
    # `row.id` / `row.name` / etc. are SQLAlchemy column descriptors
    # that pyright types as `Column[str]`. The runtime value of an
    # *instance* attribute is the stored `str` (the schema declares
    # `Column(String, ...)`) — explicit `cast(str, ...)` is the typed
    # expression of that invariant without resorting to `# type: ignore`.
    return TaskRow(
        id=cast(str, row.id),
        name=cast(str, row.name),
        command=cast(str, row.command),
        status=cast(str, row.status),
    )


def _result_to_dict(row: TaskResult) -> dict:
    """Convert an ORM `TaskResult` into the dict shape the api layer emits.

    The shape matches the in-memory implementation's
    `add_result(...)` payload so the wire contract is unchanged.
    """
    return {
        "id": row.id,
        "task_id": row.task_id,
        "exit_code": row.exit_code,
        "stdout_tail": row.stdout_tail,
        "stderr_tail": row.stderr_tail,
        "duration_ms": row.duration_ms,
        "finished_at": row.finished_at,
    }


class TaskRepo:
    """SQL-backed `tasks` + `task_results` repository (FR-06).

    Every method wraps its work in `transaction()` so the Session is
    open for exactly the duration of one request and is committed
    / rolled back atomically. The Session is NEVER held by the
    caller; the service layer talks to this repository through the
    dataclass snapshots (`TaskRow` / dict), never through SQLAlchemy
    ORM objects (SPEC.md §3 FR-06 paragraph 1: business layer does
    not hold a Session).

    The list endpoint's N+1 guard is encoded in `list`: every
    page-sized SELECT is paired with a single
    `selectinload(Task.results)` so the related rows are fetched in
    one round-trip regardless of page size (NFR-01 + SPEC.md §8 #14).
    """

    # ----- AC-1.1 / AC-1.4 -----
    def create(self, id: str, name: str, command: str) -> TaskRow:
        """Insert a new task. Raises NameConflictError on duplicate name.

        The unique constraint on `tasks.name` is the enforcement
        point (FR-06 / NFR-02: parameterised INSERT, no string SQL).
        An `IntegrityError` is translated into the SAB-bound
        `NameConflictError` so the service layer can `except` without
        crossing the api → repository SAB boundary.
        """
        try:
            with transaction() as session:
                session.add(
                    Task(
                        id=id,
                        name=name,
                        command=command,
                        status=STATUS_PENDING,
                    )
                )
        except IntegrityError as exc:
            # `transaction()` has already rolled back the partial
            # transaction. Translate the SQLAlchemy-level error into
            # the SAB-bound domain exception.
            raise NameConflictError(name) from exc
        # Re-fetch the canonical row so the caller sees the
        # database-assigned defaults.
        row = self.get(id)
        assert row is not None, (
            f"task {id!r} vanished after insert — the SQL store is "
            "inconsistent; FR-06 transaction boundary violated"
        )
        return row

    # ----- AC-1.3 -----
    def get(self, id: str) -> Optional[TaskRow]:
        with transaction() as session:
            return self._get_in_session(session, id)

    @staticmethod
    def _get_in_session(session: Session, id: str) -> Optional[TaskRow]:
        row = session.get(Task, id)
        if row is None:
            return None
        return _row_to_task_row(row)

    # ----- AC-1.7 -----
    def delete(self, id: str) -> bool:
        """Remove task AND its task_results atomically (AC-1.7).

        Returns True on success, False if the id was unknown. The
        ORM relationship's `cascade="all, delete-orphan"` setting
        (declared on `Task.results` in models/orm.py) ensures the
        child `task_results` rows are dropped in the same
        transaction as the parent — the SQL-level expression of
        SPEC.md §3 FR-01 row 4.
        """
        with transaction() as session:
            row = session.get(Task, id)
            if row is None:
                return False
            session.delete(row)
            return True

    # ----- AC-1.5 / AC-1.6 -----
    def list(
        self,
        limit: int,
        cursor: Optional[str],
        status: Optional[str],
    ) -> tuple[list[TaskRow], Optional[str]]:
        """Return a page of tasks plus the next cursor (or None).

        The query uses `selectinload(Task.results)` so the related
        rows are fetched in one round-trip regardless of `limit`
        (NFR-01 + SPEC.md §8 #14 N+1 guard). The page is sorted by
        `id` ascending so the cursor — the last id of the current
        page — can be sliced away on the next request.
        """
        with transaction() as session:
            stmt = select(Task).options(selectinload(Task.results))
            if status is not None:
                stmt = stmt.where(Task.status == status)
            stmt = stmt.order_by(Task.id.asc()).limit(limit + 1)
            if cursor:
                # Cursor is the last id of the previous page; the
                # `>` comparison is the cursor-pagination contract
                # (AC-1.6: `offset` is forbidden).
                stmt = stmt.where(Task.id > cursor)
            rows = list(session.execute(stmt).scalars().all())
        # Slice off the `(limit + 1)`th row, which exists only so
        # we can detect "is there a next page?".
        page = [_row_to_task_row(r) for r in rows[:limit]]
        # `rows[limit].id` is `Column[str]` under pyright; runtime value
        # is the stored primary key (see `_row_to_task_row` for rationale).
        next_cursor: Optional[str] = (
            cast(str, rows[limit].id) if len(rows) > limit else None
        )
        return page, next_cursor

    # ----- FR-02 AC-2.3 — state machine transitions -----
    def set_status(self, id: str, status: str) -> bool:
        """Drive the task's `status` field to a new value.

        Returns True on success, False if the id is unknown. The
        update is committed by the surrounding `transaction()`
        context manager so the state transition is observable to
        the next request without a separate flush call.
        """
        with transaction() as session:
            row = session.get(Task, id)
            if row is None:
                return False
            # SQLAlchemy's instance-attribute setter accepts `str` at
            # runtime even though pyright types the descriptor as
            # `Column[str]`. `setattr(...)` is the dynamic-attribute
            # form that satisfies `reportAttributeAccessIssue` without
            # hiding a real schema/type mismatch.
            setattr(row, "status", status)
            return True

    # ----- FR-02 AC-2.4 — task_results row write -----
    def add_result(
        self,
        id: str,
        run_id: str,
        exit_code: Optional[int],
        stdout_tail: str,
        stderr_tail: str,
        duration_ms: int,
        finished_at: str,
    ) -> bool:
        """Append a `task_results` row for the given task.

        Returns False if the task id is unknown so the runner can
        short-circuit (its public contract is "always write a row
        if the task exists, otherwise be a no-op"). The five
        required columns per SPEC §3 FR-02 are written via bound
        parameters (NFR-02: no string SQL).
        """
        with transaction() as session:
            parent = session.get(Task, id)
            if parent is None:
                return False
            session.add(
                TaskResult(
                    id=run_id,
                    task_id=id,
                    exit_code=exit_code,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    duration_ms=duration_ms,
                    finished_at=finished_at,
                )
            )
            return True

    # ----- FR-02 AC-2.6 — run history newest first -----
    def list_results(
        self,
        id: str,
        limit: int,
        cursor: Optional[str],
    ) -> tuple[list[dict], Optional[str]]:  # type: ignore[valid-type]
        """Return a page of `task_results` rows, newest first.

        The order is `finished_at DESC, id DESC` (SPEC §3 FR-02
        paragraph 4). Cursor pagination is a *keyset* cursor over
        the (finished_at, id) tuple — the next page is "rows that
        come strictly AFTER the cursor's (finished_at, id) in the
        newest-first order". The id tie-breaker is the discriminator
        when two rows share a `finished_at`; without it the cursor
        would skip or repeat rows on a tie.

        The cursor is the `run_id` (the `task_results.id` column).
        We resolve it to the row's `finished_at` first, then build
        the WHERE clause; the page's `next_cursor` is the LAST id
        on the page (or None if the page is the final one).
        """
        with transaction() as session:
            stmt = (
                select(TaskResult)
                .where(TaskResult.task_id == id)
                .order_by(
                    TaskResult.finished_at.desc(),
                    TaskResult.id.desc(),
                )
                .limit(limit + 1)
            )
            if cursor:
                cursor_row = session.get(TaskResult, cursor)
                if cursor_row is None:
                    # Unknown cursor — return an empty page rather
                    # than 500. The api layer treats empty + None
                    # cursor as "no further rows".
                    return [], None
                cursor_finished_at = cursor_row.finished_at
                stmt = stmt.where(
                    or_(
                        TaskResult.finished_at < cursor_finished_at,
                        and_(
                            TaskResult.finished_at == cursor_finished_at,
                            TaskResult.id < cursor,
                        ),
                    )
                )
            rows = list(session.execute(stmt).scalars().all())
        page = [_result_to_dict(r) for r in rows[:limit]]
        # `rows[limit].id` is `Column[str]` under pyright; runtime value
        # is the stored primary key (see `_row_to_task_row` for rationale).
        next_cursor: Optional[str] = (
            cast(str, rows[limit].id) if len(rows) > limit else None
        )
        return page, next_cursor

    # ----- Test seam: drop every persisted task row + result row -----
    @classmethod
    def reset_all(cls) -> None:
        """Drop every persisted task and result row (test-seam only).

        Clears the SQL store so the next `create` starts from an
        empty slate. This is the in-DB equivalent of
        `TRUNCATE TABLE tasks, task_results`. NOT exposed on the
        public `get` / `list` / `set_status` / `add_result` surface
        so production callers cannot accidentally wipe the store.
        """
        with transaction() as session:
            session.query(TaskResult).delete()
            session.query(Task).delete()
