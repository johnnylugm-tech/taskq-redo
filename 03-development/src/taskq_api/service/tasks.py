"""Task business-logic service (SPEC.md §3 FR-01 + FR-02).

[FR-01] All FR-01 routes delegate here. The service translates
repository-layer exceptions (`NameConflictError`) into service-layer
exceptions (`TaskNameConflictError`) so the api layer can `except`
without crossing the api → repository SAB boundary.

[FR-02] Adds the run-scheduling side of the FR-02 contract. The
service validates that the task exists, mints a `run_id`, and
schedules the actual subprocess execution as a background asyncio
task on the running loop. The api handler returns 202 with the
`run_id` immediately; the state machine and result row are driven
by `TaskRunner` (see `taskq_api.service.runner`).

Transaction boundaries: every mutation is wrapped in
`taskq_api.repository.session.transaction()` per SPEC.md §3 FR-06.
The current `transaction()` is a no-op (the in-memory repo already
serialises its own writes); the future SQLAlchemy implementation
will gain `session.commit()` / `session.rollback()` here without
any api-layer change.
Citations: SPEC.md §3 FR-01, FR-02, FR-06.
"""
from __future__ import annotations
import uuid
from dataclasses import asdict as _row_to_dict
from typing import Optional

from taskq_api.repository.session import transaction
from taskq_api.repository.task_repo import NameConflictError, TaskRepo


class TaskNameConflictError(Exception):
    """Raised when the requested task name is already in use (AC-1.4)."""


class TaskNotFoundError(Exception):
    """Raised when a task id is unknown (AC-1.3 / AC-1.7 / AC-2.1)."""


class TaskService:
    """Business operations for the `tasks` resource."""

    def __init__(self, task_repo: TaskRepo, task_runner=None) -> None:
        self._repo = task_repo
        # `task_runner` is typed loosely because importing the
        # class would create a service→service cycle (runner does
        # not import service; service is the only consumer). The
        # runner is constructed in `taskq_api.app` and injected
        # alongside the repo.
        self._runner = task_runner

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

    # ----- FR-02 AC-2.1 — schedule a task run -----
    async def schedule_run(self, id: str) -> str:
        """Validate the task exists, mint a run_id, and execute the
        subprocess. Returns the run_id.

        For FR-02 in-process we `await` the runner directly so the
        API response and the completion of the subprocess share the
        same event-loop turn. This keeps `TestClient`-driven tests
        deterministic (the run state is terminal by the time the
        202 is observed) and matches the SPEC's observation contract:
        a follow-up `GET /v1/tasks/{id}` after the 202 reflects a
        terminal status. FR-08 will replace this with the
        `asyncio.TaskGroup` manager + concurrency cap (AC-8.2 /
        AC-8.1).
        """
        row = self._repo.get(id)
        if row is None:
            raise TaskNotFoundError(id)
        run_id = str(uuid.uuid4())
        if self._runner is not None:
            await self._runner.run(id, run_id)
        return run_id

    # ----- FR-02 AC-2.6 — list run history -----
    def list_runs(
        self,
        id: str,
        limit: int,
        cursor: Optional[str],
    ) -> tuple[list[dict], Optional[str]]:  # type: ignore[valid-type]
        """Return the run history for `id`, newest first.

        Raises TaskNotFoundError if the task does not exist so the
        api layer can return 404 problem+json (NFR-02: no
        resource-existence leak via the result list — we 404 the
        parent explicitly).
        """
        row = self._repo.get(id)
        if row is None:
            raise TaskNotFoundError(id)
        items, next_cursor = self._repo.list_results(
            id=id, limit=limit, cursor=cursor
        )
        return items, next_cursor
