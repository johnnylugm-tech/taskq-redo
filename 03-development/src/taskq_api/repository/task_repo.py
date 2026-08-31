"""Task repository — storage for `tasks` rows and their `task_results`
cascade (SPEC.md §3 FR-01 row 4 + FR-07 v3 schema + FR-02 run history).

[FR-01] Single-process in-memory implementation. The class is the
implementation contract declared in TEST_SPEC.md FR-01 case 7: a
DELETE call MUST remove the parent task row AND its task_results row
in the same transaction (no orphans). Because all mutations are
guarded by a single `RLock`, the delete path is atomic by
construction; the future SQLAlchemy implementation will express the
same guarantee via a single `DELETE … FROM tasks WHERE id=:id`
statement within an explicit transaction.

[FR-02] Adds the result-row contract declared in SPEC.md §3 FR-02:
  * `set_status(id, status)`  — drives the
    `pending → running → done | failed | timeout` state machine.
  * `add_result(id, run_id, …)` — appends a row to the per-task
    `task_results` list (the v3 split table).
  * `list_results(id, limit, cursor)` — returns rows ordered by
    `finished_at DESC` (newest first) for `GET /v1/tasks/{id}/runs`.
All three go through the same `RLock` so the API can observe
consistent state across reads and writes.

Citations:
  SPEC.md §3 FR-01 (row 4 "delete with results row, same transaction")
  SPEC.md §3 FR-02 ("task_results" table + state machine)
  TEST_SPEC.md FR-01 case 7
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Callable, Optional, TypeVar


# State machine constants (SPEC.md §3 FR-02). Defined here so the
# repository — the single owner of the `status` field — is the single
# source of truth; the runner and service import these instead of
# repeating string literals.
STATUS_PENDING: str = "pending"
STATUS_RUNNING: str = "running"
STATUS_DONE: str = "done"
STATUS_FAILED: str = "failed"
STATUS_TIMEOUT: str = "timeout"

# All valid states, in transition order. Useful for input validation
# at the api boundary.
ALL_STATUSES: frozenset[str] = frozenset(
    {STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED, STATUS_TIMEOUT}
)

# Tail length (chars) kept for stdout/stderr in a task_results row.
# SPEC.md §3 FR-02 requires the column to exist; bounding the size
# protects the in-memory store from unbounded output.
_TAIL_CHARS: int = 2000

T = TypeVar("T")


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


class TaskRepo:
    """In-memory task store. Thread-safe via a single reentrant lock.

    The store deliberately keeps the parent (`tasks`) and child
    (`task_results`) structures in one place so `delete()` can drop
    both atomically without an intermediate observable state.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._tasks: dict[str, TaskRow] = {}
        self._results: dict[str, list[dict]] = {}
        self._name_index: dict[str, str] = {}

    # ----- AC-1.1 / AC-1.4 -----
    def create(self, id: str, name: str, command: str) -> TaskRow:
        """Insert a new task. Raises NameConflictError on duplicate name."""
        with self._lock:
            if name in self._name_index:
                raise NameConflictError(name)
            row = TaskRow(id=id, name=name, command=command)
            self._tasks[id] = row
            self._name_index[name] = id
            self._results[id] = []
            return row

    # ----- AC-1.3 -----
    def get(self, id: str) -> Optional[TaskRow]:
        with self._lock:
            return self._tasks.get(id)

    # ----- AC-1.7 -----
    def delete(self, id: str) -> bool:
        """Remove task AND its task_results atomically.

        Returns True on success, False if the id was unknown. The lock
        guarantees that no reader can observe a parent-without-results
        intermediate state.
        """
        with self._lock:
            row = self._tasks.pop(id, None)
            if row is None:
                return False
            self._name_index.pop(row.name, None)
            self._results.pop(id, None)
            return True

    # ----- AC-1.5 / AC-1.6 -----
    def list(
        self,
        limit: int,
        cursor: Optional[str],
        status: Optional[str],
    ) -> tuple[list[TaskRow], Optional[str]]:
        """Return a page of tasks plus the next cursor (or None)."""
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda r: r.id)
            if status is not None:
                ordered = [r for r in ordered if r.status == status]
            return _paginate(ordered, limit=limit, cursor=cursor, key=lambda r: r.id)

    # ----- FR-02 AC-2.3 — state machine transitions -----
    def set_status(self, id: str, status: str) -> bool:
        """Drive the task's `status` field to a new value.

        Returns True on success, False if the id is unknown. The
        `TaskRow` is replaced atomically under the lock so readers
        either see the old or the new value, never a partial update.
        """
        with self._lock:
            row = self._tasks.get(id)
            if row is None:
                return False
            self._tasks[id] = replace(row, status=status)
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

        The five required columns per SPEC §3 FR-02 are
        `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`,
        `finished_at`. The `id` and `task_id` columns are stored so
        the cursor can identify individual rows in the history view.
        """
        with self._lock:
            if id not in self._tasks:
                return False
            self._results.setdefault(id, []).append(
                {
                    "id": run_id,
                    "task_id": id,
                    "exit_code": exit_code,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "duration_ms": duration_ms,
                    "finished_at": finished_at,
                }
            )
            return True

    # ----- FR-02 AC-2.6 — run history newest first -----
    def list_results(
        self,
        id: str,
        limit: int,
        cursor: Optional[str],
    ) -> tuple[list[dict], Optional[str]]:
        """Return a page of `task_results` rows, newest first.

        The order is `finished_at DESC` (SPEC §3 FR-02 paragraph 4).
        Rows without a `finished_at` (i.e. in-flight) sort to the
        end, but every FR-02 row is written with a finished_at at
        the moment of completion, so this branch is unreachable in
        practice.
        """
        with self._lock:
            rows = list(self._results.get(id, []))
        # Sort newest-first before paginating so the cursor references
        # the post-sort order (a row id encodes its finished_at).
        rows.sort(
            key=lambda r: r.get("finished_at") or "", reverse=True
        )
        return _paginate(rows, limit=limit, cursor=cursor, key=lambda r: r["id"])


def _paginate(
    rows: list[T],
    *,
    limit: int,
    cursor: Optional[str],
    key: Callable[[T], str],
) -> tuple[list[T], Optional[str]]:
    """Slice `rows` at the cursor and return `(page, next_cursor)`.

    Shared by `TaskRepo.list` (AC-1.5) and `TaskRepo.list_results`
    (AC-2.6) — both use a stable id-based cursor and a hard cap on
    page size. The caller is responsible for sorting `rows` first;
    this helper preserves caller-supplied order.
    """
    if cursor:
        idx = next(
            (i for i, row in enumerate(rows) if key(row) == cursor),
            -1,
        )
        if idx >= 0:
            rows = rows[idx + 1 :]
    page = rows[:limit]
    next_cursor: Optional[str] = (
        key(rows[limit]) if len(rows) > limit else None
    )
    return page, next_cursor
