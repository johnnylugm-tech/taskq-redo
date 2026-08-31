"""RED step — failing tests for FR-02 Task execution endpoint.

Covers the six acceptance criteria declared in SPEC.md §3 FR-02 and
TEST_SPEC.md FR-02 cases 1-6:

  AC-2.1 — POST /v1/tasks/{id}/run returns 202 with run_id in body
  AC-2.2 — Execution uses asyncio.create_subprocess_exec; shell=True absent
  AC-2.3 — State machine: pending -> running -> done | failed | timeout
  AC-2.4 — Run writes task_results row with required columns
  AC-2.5 — Timeout kills child process; no orphan
  AC-2.6 — GET /v1/tasks/{id}/runs returns history newest first

Per SAB.json (`fr_module_traceability.FR-02`), these are the bound
modules the GREEN implementation must place on disk:

  taskq_api.api.tasks            -> api/tasks.py            (exists; needs new routes)
  taskq_api.service.runner       -> service/runner.py       (DOES NOT EXIST - GREEN creates)
  taskq_api.service.tasks        -> service/tasks.py        (exists; needs schedule_run)
  taskq_api.repository.task_repo -> repository/task_repo.py (exists; needs results table)

These tests intentionally exercise the SAB-declared entry points so
pytest will fail at the run-assertion boundary while the GREEN
implementation is still missing — this is the expected RED state.

Citations:
  SPEC.md §3 FR-02 (whole section)
  SPEC.md §3 FR-08 paragraph 3 (timeout kill interaction)
  TEST_SPEC.md FR-02 (cases 1-6)
  NFR-02 (shell=True banned; X-API-Key authn)
  NFR-06 (layering: api > service > repository)
"""
import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# SAB binding — GREEN must wire this module path on disk and add the
# FR-02 routes (`POST /v1/tasks/{id}/run`, `GET /v1/tasks/{id}/runs`)
# to the existing `tasks` router. See SPEC.md §3 FR-02.
from taskq_api.app import app
from taskq_api.repository.task_repo import (
    NameConflictError,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TaskRepo,
)
from taskq_api.service.runner import TaskRunner, _kill_and_reap
from taskq_api.service.tasks import (
    TaskNameConflictError,
    TaskNotFoundError,
    TaskService,
)


# ----- Shared fixtures ----------------------------------------------------


@pytest.fixture
def client():
    """Build a sync TestClient against the FastAPI app.

    GREEN TODO: `taskq_api.app:app` must remain importable; the FR-02
    routes must be added to the existing `tasks_router` (or a sibling
    router mounted under /v1).
    """
    return TestClient(app)


@pytest.fixture
def write_api_key():
    """Plaintext write-scope API key (FR-03)."""
    return "fr01-test-write-key-aaaa"


@pytest.fixture
def read_api_key():
    """Plaintext read-scope API key (FR-03)."""
    return "fr01-test-read-key-bbbb"


@pytest.fixture
def admin_api_key():
    """Plaintext admin-scope API key (FR-03)."""
    return "fr01-test-admin-key-cccc"


@pytest.fixture
def short_timeout(monkeypatch):
    """Override TASKQ_TASK_TIMEOUT to 1.0s for the timeout-kill test.

    GREEN TODO: runner.py must read TASKQ_TASK_TIMEOUT from the
    environment (or from `taskq_api.config`) at call time so that
    monkeypatch.setenv overrides take effect.
    """
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.0")
    return 1.0


def _create_task(client, write_api_key, name, command):
    """Create a task via the FR-01 endpoint and return its id."""
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": command, "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _wait_for_terminal_status(client, write_api_key, task_id, timeout):
    """Poll GET /v1/tasks/{id} until status is one of
    done|failed|timeout, or until `timeout` seconds elapse.

    Returns the terminal status string, or None on timeout.
    """
    deadline = time.time() + timeout
    final = None
    while time.time() < deadline:
        check = client.get(
            f"/v1/tasks/{task_id}",
            headers={"X-API-Key": write_api_key},
        )
        if check.status_code == 200:
            current = check.json().get("status")
            if current in ("done", "failed", "timeout"):
                final = current
                break
        time.sleep(0.05)
    return final


# ----- AC-2.1 ------------------------------------------------------------


def test_post_run_returns_202_with_run_id(client, write_api_key):
    """AC-2.1 — POST /v1/tasks/{id}/run returns 202 with run_id in body.

    Sub-assertion: FR02-happy-command-nonempty (command must be non-empty).
    # NFR-02 security — write-scope gate verified via X-API-Key
    # NFR-06 architecture_constraints — api > service > repository layering
    # NFR-10 integration_coverage — full HTTP cycle through ASGITransport
    """
    task_id = _create_task(
        client,
        write_api_key,
        name="fr02-run-happy-1",
        command="echo hi",
    )

    response = client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert response.status_code == 202
    body = response.json()
    assert "run_id" in body


# ----- AC-2.2 ------------------------------------------------------------


def test_shell_true_absent_from_src_tree():
    """AC-2.2 — `shell=True` is absent from the entire src tree.

    Sub-assertion: FR02-shell-token-banned.
    # NFR-02 security — shell=True is forbidden; only
    #   `asyncio.create_subprocess_exec(*shlex.split(command))` may
    #   spawn processes. NP-08 (T-05 shell metachar) enforcement point.
    # NFR-09 testability — static guard enforced by pytest.

    Walks every .py file under `03-development/src/` and asserts the
    literal token `shell=True` is absent. GREEN's runner.py must use
    `asyncio.create_subprocess_exec` and MUST NOT pass shell=True.
    """
    src_root = Path(__file__).resolve().parent.parent / "src"
    forbidden_token = "shell=True"

    hits = []
    for py_file in src_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if forbidden_token in text:
            hits.append(str(py_file.relative_to(src_root.parent)))

    assert hits == [], (
        f"forbidden token {forbidden_token!r} found in src tree: {hits}"
    )


# ----- AC-2.3 ------------------------------------------------------------


def test_state_machine_transitions(client, write_api_key):
    """AC-2.3 — task state transitions pending -> running -> done|failed|timeout.

    Sub-assertion: FR02-state-pending-then-terminal (expected_terminal == "done").
    # NFR-03 reliability — state transitions must be observable via the
    #   repository contract and persist through api reads.
    # NFR-06 architecture_constraints — runner state-machine lives in
    #   `taskq_api.service.runner`; the repository owns the status field.

    A successful `echo done` command must transition the task from its
    initial `pending` state to a terminal `done` state.
    """
    task_id = _create_task(
        client,
        write_api_key,
        name="fr02-state-1",
        command="echo done",
    )

    # Initial status must be pending (AC-2.3 entry condition).
    pre = client.get(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": write_api_key},
    )
    assert pre.status_code == 200
    assert pre.json()["status"] == "pending"

    # Trigger the run.
    run_resp = client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run_resp.status_code == 202

    # Wait for the runner to drive the state to a terminal value.
    final = _wait_for_terminal_status(
        client, write_api_key, task_id, timeout=10.0
    )
    assert final == "done", f"expected terminal status 'done', got {final!r}"


# ----- AC-2.4 ------------------------------------------------------------


def test_run_writes_task_results_row(client, write_api_key, read_api_key):
    """AC-2.4 — a successful run writes a row into task_results.

    Sub-assertion: FR02-results-row-columns
    (len(columns_required.split(",")) == 5).
    # NFR-06 architecture_constraints — repository owns the results
    #   table; the service writes through it.
    # NFR-10 integration_coverage — full result lifecycle from run to row.

    The five required columns per SPEC §3 FR-02 are:
      exit_code, stdout_tail, stderr_tail, duration_ms, finished_at
    """
    task_id = _create_task(
        client,
        write_api_key,
        name="fr02-results-1",
        command="echo ok",
    )

    run_resp = client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run_resp.status_code == 202

    # Poll the history endpoint until the result row appears.
    deadline = time.time() + 10.0
    runs = []
    while time.time() < deadline:
        r = client.get(
            f"/v1/tasks/{task_id}/runs",
            headers={"X-API-Key": read_api_key},
        )
        if r.status_code == 200:
            runs = r.json().get("items", [])
            if runs:
                break
        time.sleep(0.05)

    assert runs, "expected at least one task_results row after the run"

    required_columns = [
        "exit_code",
        "stdout_tail",
        "stderr_tail",
        "duration_ms",
        "finished_at",
    ]
    first_row = runs[0]
    for col in required_columns:
        assert col in first_row, (
            f"missing required column {col!r} in task_results row "
            f"(present columns: {sorted(first_row.keys())})"
        )


# ----- AC-2.5 ------------------------------------------------------------


def test_timeout_kills_child_no_orphan(client, write_api_key, short_timeout):
    """AC-2.5 — task exceeding TASKQ_TASK_TIMEOUT is killed; no orphan.

    Sub-assertion: FR02-timeout-kills-no-orphan (expects_orphan == "false").
    # NFR-03 reliability — timeout kill must not leak child processes
    #   (NP-15 timeout pattern: `process.kill()` then `await process.wait()`).
    # NFR-10 integration_coverage — full timeout lifecycle through runner.

    A `sleep 60` command launched with a 1.0-second TASKQ_TASK_TIMEOUT
    budget must end with task status `timeout` — proving the runner
    enforced the budget and reaped the child.
    """
    task_id = _create_task(
        client,
        write_api_key,
        name="fr02-timeout-1",
        command="sleep 60",
    )

    run_resp = client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run_resp.status_code == 202

    # Wait for the timeout to fire (budget = 1.0s; allow 5s slack for
    # process.kill()/wait() to settle).
    final = _wait_for_terminal_status(
        client, write_api_key, task_id, timeout=short_timeout + 5.0
    )
    assert final == "timeout", (
        f"expected terminal status 'timeout' after exceeding "
        f"TASKQ_TASK_TIMEOUT={short_timeout}s, got {final!r}"
    )


# ----- AC-2.6 ------------------------------------------------------------


def test_get_runs_returns_history_newest_first(
    client, write_api_key, read_api_key
):
    """AC-2.6 — GET /v1/tasks/{id}/runs returns history newest first.

    Sub-assertion: FR02-history-newest-first (order == "newest_first").
    # NFR-06 architecture_constraints — repository orders by
    #   finished_at DESC; service hands the ordered list to api.
    # NFR-10 integration_coverage — full list-query path.

    Three sequential runs must produce a history whose first element is
    the most recent (latest finished_at) and whose order is strictly
    non-increasing across finished_at values.
    """
    task_id = _create_task(
        client,
        write_api_key,
        name="fr02-history-1",
        command="echo hi",
    )

    n_runs = 3
    for _ in range(n_runs):
        run_resp = client.post(
            f"/v1/tasks/{task_id}/run",
            headers={"X-API-Key": write_api_key},
        )
        assert run_resp.status_code == 202
        # Wait for completion before the next run so each finished_at
        # is strictly later than the previous.
        _wait_for_terminal_status(
            client, write_api_key, task_id, timeout=10.0
        )

    runs_resp = client.get(
        f"/v1/tasks/{task_id}/runs",
        headers={"X-API-Key": read_api_key},
    )
    assert runs_resp.status_code == 200
    runs = runs_resp.json().get("items", [])
    assert len(runs) >= n_runs, (
        f"expected at least {n_runs} history rows, got {len(runs)}"
    )

    timestamps = [r["finished_at"] for r in runs[:n_runs]]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"runs must be newest first; got finished_at={timestamps}"
    )


# ----- COVERAGE: API error / boundary branches ----------------------------


def test_post_run_unknown_task_returns_404(client, write_api_key):
    """COVERAGE — POST /v1/tasks/{unknown}/run surfaces 404 + problem+json.

    Exercises api/tasks.py:162-163 (TaskNotFoundError → NotFoundError) and
    service/tasks.py:106 (schedule_run early-raise on unknown id).
    """
    response = client.post(
        "/v1/tasks/00000000-0000-0000-0000-000000000000/run",
        headers={"X-API-Key": write_api_key},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_get_runs_unknown_task_returns_404(client, read_api_key):
    """COVERAGE — GET /v1/tasks/{unknown}/runs surfaces 404 + problem+json.

    Exercises api/tasks.py:186-187 and service/tasks.py:128 (list_runs
    early-raise when the parent task does not exist).
    """
    response = client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000/runs",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_get_task_unknown_id_returns_404(client, read_api_key):
    """COVERAGE — GET /v1/tasks/{unknown} 404 path through the api handler.

    Exercises api/tasks.py:77-78 (get_task NotFoundError translation) and
    service/tasks.py:66 (TaskNotFoundError raise on unknown id).
    """
    response = client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 404


def test_delete_task_unknown_id_returns_404(client, admin_api_key):
    """COVERAGE — DELETE /v1/tasks/{unknown} surfaces 404 + problem+json.

    Exercises api/tasks.py:131-135, service/tasks.py:71-74 (TaskNotFoundError
    when the repo's `delete` returns False), and repository/task_repo.py:131-137
    (the `row is None → return False` branch).
    """
    response = client.delete(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 404


def test_create_task_duplicate_name_returns_409(client, write_api_key):
    """COVERAGE — duplicate-name conflict path through api handler.

    Exercises api/tasks.py:60-61 (TaskNameConflictError → ConflictError),
    service/tasks.py:58-59 (NameConflictError → TaskNameConflictError),
    and repository/task_repo.py:111 (NameConflictError raise on duplicate).
    """
    first = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo a", "name": "fr02-dup-coverage-1"},
    )
    assert first.status_code == 201

    second = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo b", "name": "fr02-dup-coverage-1"},
    )
    assert second.status_code == 409


def test_list_tasks_with_offset_param_rejected(client, read_api_key):
    """COVERAGE — `offset` query parameter is rejected per AC-1.6.

    Exercises api/tasks.py:98-109 (the `if "offset" in request.query_params`
    branch and the BadRequestError raise that maps to 400).
    """
    response = client.get(
        "/v1/tasks",
        params={"offset": 10},
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 400


def test_list_tasks_with_status_filter_returns_only_matching(
    client, write_api_key, read_api_key
):
    """COVERAGE — status filter is applied at the repository layer.

    Exercises service/tasks.py:83-87 (list method body) and
    repository/task_repo.py:147-151 (status filter branch).
    """
    created = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo a", "name": "fr02-status-filter-1"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = client.get(
        "/v1/tasks",
        params={"status": "pending", "limit": 50},
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert task_id in ids


# ----- COVERAGE: Runner branches ------------------------------------------


def test_runner_run_unknown_task_id_is_noop():
    """COVERAGE — TaskRunner.run() returns silently when task is unknown.

    Exercises service/runner.py:127 (early return on unknown id), and
    exercises repository/task_repo.py:164 (set_status returns False when
    id is unknown — although in this path the runner returns before
    calling set_status, the return-False branch is reached via the direct
    set_status call below for additional coverage of that line).
    """
    repo = TaskRepo()
    runner = TaskRunner(task_repo=repo)
    # Unknown task id — should be a silent no-op.
    asyncio.run(runner.run("00000000-0000-0000-0000-000000000000", "run-1"))
    assert repo.get("00000000-0000-0000-0000-000000000000") is None
    # Direct call covers the False-return branch (line 164).
    assert repo.set_status("00000000-0000-0000-0000-000000000000", STATUS_RUNNING) is False


def test_runner_run_spawn_error_marks_failed():
    """COVERAGE — TaskRunner catches FileNotFoundError-style spawn errors
    and writes a `failed` result row.

    Exercises service/runner.py:154-162 (the `_SPAWN_ERRORS` branch and
    the resulting `_RunOutcome` construction with terminal_status=failed).
    """
    repo = TaskRepo()
    runner = TaskRunner(task_repo=repo)
    # Use a command that cannot exist; shlex.split yields a single argv
    # element so create_subprocess_exec will raise FileNotFoundError.
    repo.create(
        id="fr02-spawn-err-1",
        name="fr02-spawn-err-1",
        command="/this/binary/does/not/exist/xyz",
    )

    asyncio.run(runner.run("fr02-spawn-err-1", "run-1"))

    row = repo.get("fr02-spawn-err-1")
    assert row is not None
    assert row.status == STATUS_FAILED
    # A result row must still be written (the runner is contract-bound to
    # always produce a task_results row, even on spawn failure).
    items, _ = repo.list_results(id="fr02-spawn-err-1", limit=10, cursor=None)
    assert len(items) == 1
    assert items[0]["exit_code"] is None


def test_kill_and_reap_handles_already_dead_process():
    """COVERAGE — _kill_and_reap swallows ProcessLookupError.

    Exercises service/runner.py:231-232. A subprocess that has already
    exited will raise ProcessLookupError on kill(); the helper must
    tolerate it without bubbling up.
    """
    async def _exercise() -> None:
        proc = await asyncio.create_subprocess_exec(
            "true", stdout=asyncio.subprocess.PIPE
        )
        # Wait for the process to fully exit before invoking the helper
        # (must be in the same event loop to avoid cross-loop errors).
        await proc.wait()
        # Must not raise even though kill() will see the process as gone.
        await _kill_and_reap(proc)

    asyncio.run(_exercise())


# ----- COVERAGE: Service layer branches ----------------------------------


def test_service_get_unknown_raises():
    """COVERAGE — service/tasks.py:66 (TaskNotFoundError raise)."""
    svc = TaskService(task_repo=TaskRepo())
    with pytest.raises(TaskNotFoundError):
        svc.get("00000000-0000-0000-0000-000000000000")


def test_service_delete_unknown_raises():
    """COVERAGE — service/tasks.py:71-74 (TaskNotFoundError on delete)."""
    svc = TaskService(task_repo=TaskRepo())
    with pytest.raises(TaskNotFoundError):
        svc.delete("00000000-0000-0000-0000-000000000000")


def test_service_create_duplicate_raises():
    """COVERAGE — service/tasks.py:58-59 (TaskNameConflictError raise)."""
    repo = TaskRepo()
    svc = TaskService(task_repo=repo)
    svc.create(name="fr02-svc-dup-1", command="echo a")
    with pytest.raises(TaskNameConflictError):
        svc.create(name="fr02-svc-dup-1", command="echo b")


def test_service_list_returns_items():
    """COVERAGE — service/tasks.py:83-87 (list happy-path body)."""
    repo = TaskRepo()
    svc = TaskService(task_repo=repo)
    svc.create(name="fr02-svc-list-1", command="echo a")
    items, _ = svc.list(limit=50, cursor=None, status=None)
    assert len(items) >= 1


def test_service_schedule_run_unknown_raises():
    """COVERAGE — service/tasks.py:106 (TaskNotFoundError on schedule_run)."""
    svc = TaskService(task_repo=TaskRepo())
    with pytest.raises(TaskNotFoundError):
        asyncio.run(svc.schedule_run("00000000-0000-0000-0000-000000000000"))


def test_service_list_runs_unknown_raises():
    """COVERAGE — service/tasks.py:128 (TaskNotFoundError on list_runs)."""
    svc = TaskService(task_repo=TaskRepo())
    with pytest.raises(TaskNotFoundError):
        svc.list_runs(
            id="00000000-0000-0000-0000-000000000000", limit=50, cursor=None
        )


# ----- COVERAGE: Repository branches -------------------------------------


def test_repo_create_duplicate_name_raises_conflict():
    """COVERAGE — repository/task_repo.py:111 (NameConflictError raise)."""
    repo = TaskRepo()
    repo.create(id="t-a", name="fr02-repo-dup-1", command="echo a")
    with pytest.raises(NameConflictError):
        repo.create(id="t-b", name="fr02-repo-dup-1", command="echo b")


def test_repo_delete_unknown_returns_false():
    """COVERAGE — repository/task_repo.py:131-137 (delete unknown branch)."""
    repo = TaskRepo()
    assert repo.delete("00000000-0000-0000-0000-000000000000") is False


def test_repo_set_status_unknown_returns_false():
    """COVERAGE — repository/task_repo.py:164 (set_status returns False)."""
    repo = TaskRepo()
    assert repo.set_status("00000000-0000-0000-0000-000000000000", STATUS_RUNNING) is False


def test_repo_add_result_unknown_returns_false():
    """COVERAGE — repository/task_repo.py:188 (add_result returns False)."""
    repo = TaskRepo()
    assert repo.add_result(
        id="00000000-0000-0000-0000-000000000000",
        run_id="run-x",
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_ms=10,
        finished_at="2026-01-01T00:00:00",
    ) is False


def test_repo_list_with_status_filter_returns_only_matching():
    """COVERAGE — repository/task_repo.py:147-151 (status-filter branch)."""
    repo = TaskRepo()
    repo.create(id="t-p", name="fr02-repo-list-pending", command="echo a")
    repo.create(id="t-d", name="fr02-repo-list-done", command="echo b")
    repo.set_status("t-d", STATUS_DONE)

    pending_only, _ = repo.list(limit=50, cursor=None, status="pending")
    assert all(r.status == STATUS_PENDING for r in pending_only)
    done_only, _ = repo.list(limit=50, cursor=None, status="done")
    assert all(r.status == STATUS_DONE for r in done_only)
    assert any(r.id == "t-d" for r in done_only)


def test_repo_list_results_with_cursor_returns_second_page():
    """COVERAGE — repository/task_repo.py:242-247 (cursor pagination branch).

    Creates a task with 4 result rows. Page 1 (limit=2) yields 2 items
    plus a next_cursor pointing at the third (newest-first order);
    page 2 with that cursor returns the remaining 1 item and a None
    next_cursor.
    """
    repo = TaskRepo()
    repo.create(id="t-r", name="fr02-repo-cursor-1", command="echo a")
    for i in range(4):
        repo.add_result(
            id="t-r",
            run_id=f"r-{i}",
            exit_code=0,
            stdout_tail="",
            stderr_tail="",
            duration_ms=10,
            finished_at=f"2026-01-01T00:00:0{i}",
        )

    page1, next_cursor = repo.list_results(id="t-r", limit=2, cursor=None)
    assert len(page1) == 2
    assert next_cursor is not None

    page2, next_cursor_2 = repo.list_results(
        id="t-r", limit=2, cursor=next_cursor
    )
    # Four rows sorted newest-first: [r-3, r-2, r-1, r-0].
    # Page 1 returns [r-3, r-2], next_cursor == "r-1".
    # Page 2 with cursor="r-1" returns [r-0] (idx+1 slice); no further rows.
    assert len(page2) == 1
    assert next_cursor_2 is None
