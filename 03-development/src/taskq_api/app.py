"""FastAPI application factory and module-level `app` instance.

[FR-01] This is the SAB-bound entry point. `uvicorn taskq_api.app:app`
boots the server; `fastapi.testclient.TestClient(app)` (used by the
test suite) builds a sync client around the same instance.

[FR-02] Adds the `TaskRunner` wiring so the FR-02 `POST
/v1/tasks/{id}/run` endpoint can schedule background subprocess
execution. The runner shares the same `TaskRepo` as the service so
the state machine and `task_results` writes are observed by the
read endpoints in real time.

The factory wires:
  * the FR-10 problem+json exception handlers (`install_error_handlers`),
  * an `ApiKeyRepo` seeded with the dev/test keys declared in
    `taskq_api.config.API_KEY_SEEDS`,
  * a `TaskRepo` + `TaskService` pair (FR-01),
  * a `TaskRunner` injected into the service (FR-02),
  * the FR-01/FR-02 router mounted under `/v1`.

The module-level `app = create_app()` is a top-level constant — there
is no `if __name__ == "__main__":` guard here. Per the implementation
contract, that guard lives only in `<pkg>/__main__.py` (which is
intentionally not created in this FR — see FR-03 / `python -m taskq_api`).

Citations:
  SPEC.md §2 ("`uvicorn taskq_api.app:app`")
  SPEC.md §3 FR-10 (problem+json handlers)
  SPEC.md §3 FR-03 (X-API-Key authn)
  SPEC.md §3 FR-02 (task execution runner)
"""
from fastapi import FastAPI

from taskq_api.api.health import metrics_router, router as health_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.config import API_KEY_SEEDS
from taskq_api.errors import SuppressServerExceptionReraise, install_error_handlers
from taskq_api.repository.key_repo import ApiKeyRepo
from taskq_api.repository.rate_repo import RateRepo
from taskq_api.repository.task_repo import TaskRepo
from taskq_api.service.runner import TaskRunner
from taskq_api.service.tasks import TaskService


def create_app() -> FastAPI:
    """Build a fully wired FastAPI app (idempotent for tests)."""
    app = FastAPI(title="taskq-api")

    # FR-10: install problem+json handlers BEFORE routers so the
    # RequestValidationError handler is in place before any request
    # arrives.
    install_error_handlers(app)

    # FR-03 / FR-04: seed the api_key store. Plaintext is hashed on
    # add(); the store never sees the plaintext after construction.
    key_repo = ApiKeyRepo()
    for plaintext, scope in API_KEY_SEEDS.items():
        key_repo.add(plaintext, scope)
    app.state.api_key_repo = key_repo

    # FR-01: in-memory task store + service.
    task_repo = TaskRepo()
    app.state.task_repo = task_repo

    # FR-02: build the runner against the same repo so the state
    # machine and task_results writes are immediately observable
    # through the read endpoints.
    task_runner = TaskRunner(task_repo=task_repo)
    app.state.task_runner = task_runner

    app.state.task_service = TaskService(
        task_repo=task_repo, task_runner=task_runner
    )

    # FR-05: shared bucket store (AC-5.3). The RateRepo carries no
    # per-instance state — all persisted rows live in the module-level
    # dict in `taskq_api.repository.rate_repo` — but the app.state
    # reference is the DI seam the api-layer dep uses.
    app.state.rate_repo = RateRepo()

    # FR-03 / FR-09: health endpoints mounted at the app root — NOT
    # under `/v1` and NOT behind `require_api_key`, so orchestrators
    # can probe liveness/readiness without an API key (AC-3.7).
    app.include_router(health_router)

    # Mount FR-01 + FR-02 router. Subsequent FRs will add their own
    # routers (e.g. metrics under FR-09) without touching this line.
    app.include_router(tasks_router, prefix="/v1")

    # [FR-09] Mount `/v1/metrics` (admin scope). The metrics router
    # already carries `prefix="/v1"` so `app.include_router` does NOT
    # add another one — the route becomes exactly `/v1/metrics`.
    app.include_router(metrics_router)

    return app


# Module-level instance — what `uvicorn taskq_api.app:app` and the
# test client's `from taskq_api.app import app` both bind to.
#
# [FR-10] Wrap in `SuppressServerExceptionReraise` so Starlette's
# `ServerErrorMiddleware` re-raise (after our generic-exception
# handler has already written the 500 problem+json response) is
# discarded instead of propagating to `TestClient` (whose default
# `raise_server_exceptions=True` would otherwise re-raise it back to
# the test code).
app = SuppressServerExceptionReraise(create_app())
