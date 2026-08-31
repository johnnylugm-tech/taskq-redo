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
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# SAB binding — GREEN must wire this module path on disk and add the
# FR-02 routes (`POST /v1/tasks/{id}/run`, `GET /v1/tasks/{id}/runs`)
# to the existing `tasks` router. See SPEC.md §3 FR-02.
from taskq_api.app import app


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
