"""Task repository — storage for `tasks` rows and their `task_results`
cascade (SPEC.md §3 FR-01 row 4 + FR-07 v3 schema).

[FR-01] Single-process in-memory implementation. The class is the
implementation contract declared in TEST_SPEC.md FR-01 case 7: a
DELETE call MUST remove the parent task row AND its task_results row
in the same transaction (no orphans). Because all mutations are
guarded by a single `RLock`, the delete path is atomic by
construction; the future SQLAlchemy implementation will express the
same guarantee via a single `DELETE … FROM tasks WHERE id=:id`
statement within an explicit transaction.
Citations:
  SPEC.md §3 FR-01 (row 4 "delete with results row, same transaction")
  TEST_SPEC.md FR-01 case 7
"""
from dataclasses import dataclass
from threading import RLock
from typing import Optional


@dataclass(frozen=True)
class TaskRow:
    """Immutable snapshot of a task row exposed to the service layer."""

    id: str
    name: str
    command: str
    status: str = "pending"


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
            if cursor:
                idx = next(
                    (i for i, r in enumerate(ordered) if r.id == cursor),
                    -1,
                )
                if idx >= 0:
                    ordered = ordered[idx + 1 :]
            if status is not None:
                ordered = [r for r in ordered if r.status == status]
            page = ordered[:limit]
            next_cursor: Optional[str] = (
                ordered[limit].id if len(ordered) > limit else None
            )
            return page, next_cursor
