"""Task business-logic service (SPEC.md §3 FR-01).

[FR-01] All FR-01 routes delegate here. The service translates
repository-layer exceptions (`NameConflictError`) into service-layer
exceptions (`TaskNameConflictError`) so the api layer can `except`
without crossing the api → repository SAB boundary.

Transaction boundaries: every mutation is wrapped in
`taskq_api.repository.session.transaction()` per SPEC.md §3 FR-06.
The current `transaction()` is a no-op (the in-memory repo already
serialises its own writes); the future SQLAlchemy implementation
will gain `session.commit()` / `session.rollback()` here without
any api-layer change.
Citations: SPEC.md §3 FR-01, FR-06.
"""
import uuid
from dataclasses import asdict as _row_to_dict
from typing import Optional

from taskq_api.repository.session import transaction
from taskq_api.repository.task_repo import NameConflictError, TaskRepo


class TaskNameConflictError(Exception):
    """Raised when the requested task name is already in use (AC-1.4)."""


class TaskNotFoundError(Exception):
    """Raised when a task id is unknown (AC-1.3 / AC-1.7)."""


class TaskService:
    """Business operations for the `tasks` resource."""

    def __init__(self, task_repo: TaskRepo) -> None:
        self._repo = task_repo

    # ----- AC-1.1 / AC-1.4 -----
    def create(self, name: str, command: str) -> dict:
        new_id = str(uuid.uuid4())
        try:
            with transaction():
                row = self._repo.create(id=new_id, name=name, command=command)
        except NameConflictError as exc:
            raise TaskNameConflictError(str(exc)) from exc
        return _row_to_dict(row)

    # ----- AC-1.3 -----
    def get(self, id: str) -> dict:
        row = self._repo.get(id)
        if row is None:
            raise TaskNotFoundError(id)
        return _row_to_dict(row)

    # ----- AC-1.7 -----
    def delete(self, id: str) -> None:
        with transaction():
            ok = self._repo.delete(id)
        if not ok:
            raise TaskNotFoundError(id)

    # ----- AC-1.5 / AC-1.6 -----
    def list(
        self,
        limit: int,
        cursor: Optional[str],
        status: Optional[str],
    ) -> tuple[list[dict], Optional[str]]:
        rows, next_cursor = self._repo.list(
            limit=limit, cursor=cursor, status=status
        )
        items = [_row_to_dict(r) for r in rows]
        return items, next_cursor
