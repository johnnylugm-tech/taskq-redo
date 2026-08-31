"""FR-01 + FR-02 Task CRUD and execution router (SPEC.md §3 FR-01/02).

[FR-01] Four endpoints, all mounted under `/v1`:

  POST   /v1/tasks         — scope=write  → 201 (AC-1.1) / 409 (AC-1.4) / 422 (AC-1.2)
  GET    /v1/tasks/{id}    — scope=read   → 200 / 404 (AC-1.3) / 422
  GET    /v1/tasks         — scope=read   → 200 (AC-1.6) / 422 (AC-1.5)
  DELETE /v1/tasks/{id}    — scope=admin  → 204 (AC-1.7) / 404

[FR-02] Two endpoints added on the same router:

  POST   /v1/tasks/{id}/run — scope=write → 202 with `run_id` (AC-2.1)
  GET    /v1/tasks/{id}/runs — scope=read → 200, history newest first (AC-2.6)

Validation:
  * Pydantic `TaskCreate` enforces command non-empty / ≤1000 / blacklist
    (AC-1.2). Validation failures surface as 422 problem+json via the
    handler registered in `taskq_api.errors.install_error_handlers`.
  * `limit` is bounded [1, 200] by `ListTasksQuery` (AC-1.5).
  * `offset` query parameter is rejected explicitly (AC-1.6, NFR-06):
    the SPEC forbids it because large-table offset scans are N+1-cousins.

Citations:
  SPEC.md §3 FR-01 (whole section)
  SPEC.md §3 FR-02 (whole section)
  SPEC.md §7 HTTP status map (201/202/204/404/409/422)
  SPEC.md §3 FR-10 (problem+json envelope)
  NFR-06 (architectural enforcement of pagination contract)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status

from taskq_api.api.deps import get_task_service, rate_limit, require_scope
from taskq_api.errors import BadRequestError, ConflictError, NotFoundError
from taskq_api.models.schemas import TaskCreate, TaskListResponse, TaskRead
from taskq_api.service.tasks import (
    TaskNameConflictError,
    TaskNotFoundError,
    TaskService,
)


router = APIRouter(
    dependencies=[Depends(rate_limit)],
    tags=["tasks"],
)


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    payload: TaskCreate,
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("write")),
) -> TaskRead:
    """AC-1.1 / AC-1.4 — Create a new task (scope: write)."""
    try:
        result = service.create(name=payload.name, command=payload.command)
    except TaskNameConflictError as exc:
        raise ConflictError(detail="task name already exists") from exc
    return TaskRead(**result)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskRead,
)
def get_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("read")),
) -> TaskRead:
    """AC-1.3 — Fetch a task by id (scope: read)."""
    try:
        result = service.get(task_id)
    except TaskNotFoundError as exc:
        raise NotFoundError(detail="task not found") from exc
    return TaskRead(**result)


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("read")),
) -> TaskListResponse:
    """AC-1.5 / AC-1.6 — Paginated task list (scope: read).

    `limit` is bounded [1, 200] by FastAPI's Query validation, so values
    above 200 surface as 422 via the registered RequestValidationError
    handler. The `offset` query parameter is rejected explicitly with a
    400 problem+json because the SPEC forbids it (cursor-only pagination).
    """
    if "offset" in request.query_params:
        # AC-1.6: `offset` is the forbidden parameter. FastAPI does not
        # surface it because it's not in the function signature, so we
        # reject it here at the handler boundary.
        raise BadRequestError(
            detail="offset parameter is not supported; use cursor pagination"
        )

    items, next_cursor = service.list(
        limit=limit, cursor=cursor, status=status_filter
    )
    return TaskListResponse(
        items=[TaskRead(**item) for item in items],
        next_cursor=next_cursor,
    )


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("admin")),
) -> Response:
    """AC-1.7 — Delete a task AND its task_results row (scope: admin).

    The transactional-orphan guarantee is enforced inside `TaskRepo.delete`
    via the in-process lock; a follow-up `GET /v1/tasks/{id}` returns 404
    because the repository entry is gone.
    """
    try:
        service.delete(task_id)
    except TaskNotFoundError as exc:
        raise NotFoundError(detail="task not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# FR-02 — Task execution and run history
# ---------------------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("write")),
) -> dict:
    """AC-2.1 — Schedule a task for execution (scope: write).

    Returns 202 Accepted with a `run_id` in the body. The actual
    subprocess execution is scheduled as a background asyncio task
    inside the service; the state machine
    (`pending → running → done | failed | timeout`) is observed
    through subsequent `GET /v1/tasks/{id}` reads.
    """
    try:
        run_id = await service.schedule_run(task_id)
    except TaskNotFoundError as exc:
        raise NotFoundError(detail="task not found") from exc
    return {"run_id": run_id}


@router.get("/tasks/{task_id}/runs")
def list_runs(
    task_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    service: TaskService = Depends(get_task_service),
    _scope: str = Depends(require_scope("read")),
) -> dict:
    """AC-2.6 — Run history for a task, newest first (scope: read).

    Rows are ordered by `finished_at DESC` at the repository layer
    (NFR-06 layering); the service hands the ordered list to the
    api unchanged. `limit` is bounded [1, 200] just like the
    task-list endpoint (AC-1.5 contract).
    """
    try:
        items, next_cursor = service.list_runs(
            task_id, limit=limit, cursor=cursor
        )
    except TaskNotFoundError as exc:
        raise NotFoundError(detail="task not found") from exc
    return {"items": items, "next_cursor": next_cursor}
