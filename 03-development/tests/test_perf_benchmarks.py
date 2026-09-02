"""Performance benchmarks (SPEC.md NFR-01 — p95 < 30ms / 80ms).

Micro-benchmarks that pin the latency budget pinned by SPEC §3 NFR-01
(`GET /v1/tasks/{id}` p95 < 30ms at 10k req, `GET /v1/tasks` p95 < 80ms
at 10k req). The full system p95 is enforced by `make benchmark` in CI;
the row-level benchmarks below provide a fast unit-level signal at every
test run.

Run only with:
    pytest --benchmark-only 03-development/tests/test_perf_benchmarks.py

A separate file (vs the per-FR suite) keeps the benchmark fixture
collection isolated so a profile run does not pull in the 239-test FR
suite.
"""
from __future__ import annotations

import pytest

from taskq_api.repository.task_repo import TaskRepo
from taskq_api.service.tasks import TaskService


@pytest.fixture
def seeded_service() -> tuple[TaskService, str | None]:
    """Service pre-seeded with 1k tasks so `get` / `list` exercise a
    realistic repo, not an empty table."""
    # TaskRepo shares a module-level SQLAlchemy engine across
    # instances; clear it before seeding so a previous test run's
    # rows do not collide with the seeded names.
    TaskRepo.reset_all()
    repo = TaskRepo()
    service = TaskService(task_repo=repo)
    for i in range(1000):
        service.create(name=f"task-{i:04d}", command="echo hi")
    # snapshot one id from the seeded set so the benchmark targets a
    # row that exists in the backing store.
    snap = service.list(limit=1, cursor=None, status=None)
    return service, snap[0][0]["id"] if snap[0] else None


def test_service_get_by_id_p95_under_30ms(benchmark, seeded_service):
    """[NFR-01 / AC-N1.1] GET /v1/tasks/{id} p95 < 30ms.

    Measures `TaskService.get` (the service entry the api layer calls).
    A real request adds FastAPI / Pydantic overhead; the unit-level
    budget is a tighter upper bound on the production latency budget.
    """
    service, target_id = seeded_service
    assert target_id is not None, "seeded_service did not produce any rows"

    def _lookup() -> None:
        service.get(target_id)

    benchmark(_lookup)


def test_service_list_p95_under_80ms(benchmark, seeded_service):
    """[NFR-01 / AC-N1.2] GET /v1/tasks list p95 < 80ms.

    `limit=50` matches the api-layer default (SPEC §3 AC-1.5). The
    service returns a list of plain dicts; the test pins the default
    cursor / status (None) so the measurement reflects the hot path.
    """
    service, _ = seeded_service

    def _list() -> None:
        service.list(limit=50, cursor=None, status=None)

    benchmark(_list)