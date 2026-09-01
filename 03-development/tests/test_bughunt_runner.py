"""Adversarial bug-hunt repro tests (Gate 3 — adversarial_review).

Each test is a RED-fail repro for a confirmed high/critical finding
recorded in `.methodology/bug_hunt_report.json`. The tests are
co-located with the FR-08 tests so they share the `repo` fixture
that runs alembic migrations per test.

Per the hunt_bugs.md anti-fabrication contract: the test must
RED-fail against the unfixed source, then GREEN-pass once the
source fix lands in the same commit. The commits carrying the
fixes reference these tests by path.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from taskq_api.repository.task_repo import STATUS_DONE, STATUS_INTERRUPTED, STATUS_RUNNING, TaskRepo
from taskq_api.service.runner import BackgroundRunner


# ---------------------------------------------------------------------------
# Shared fixtures — copied from test_fr08.py so this file is self-contained.
# ---------------------------------------------------------------------------


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    path = tmp_path / "bughunt.db"
    monkeypatch.setenv("TASKQ_DATABASE_URL", f"sqlite:///{path}")
    import importlib
    from taskq_api.repository import session as session_mod
    importlib.reload(session_mod)
    _alembic_up(f"sqlite:///{path}")
    return f"sqlite:///{path}"


def _alembic_up(url: str) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["TASKQ_DATABASE_URL"] = url
    src_root = repo_root / "03-development" / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root),
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(db_url) -> TaskRepo:
    return TaskRepo()


# ---------------------------------------------------------------------------
# runner#1 — shutdown must NOT overwrite a task's terminal status
# ---------------------------------------------------------------------------


def test_shutdown_does_not_overwrite_completed_status(monkeypatch, repo):
    """runner#1 — A task that completed within the drain budget must
    keep its terminal status; only still-running tasks are marked
    interrupted. The current code unconditionally sets every
    snapshot tid to STATUS_INTERRUPTED on drain timeout, which
    reverts 'done' / 'failed' tasks back to 'interrupted'.

    Uses the SUBPROCESS path so the runner itself drives the
    transition to STATUS_DONE — without that terminal update,
    the row stays at STATUS_RUNNING even after the subprocess
    exits, which would mask the bug.

    Failure mode (RED): task A runs `true` (exits 0 immediately);
    task B runs `sleep 30` (long-running). After shutdown with a
    0.5s drain budget, A's status is wrongly 'interrupted'.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.5")
    # Keep per-task timeout well above the drain so the drain path
    # is the one exercised, not the timeout path.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "30.0")

    # `true` exits 0 immediately; `sleep 30` holds past the drain.
    repo.create(id="bh-q", name="bh-quick", command="true")
    repo.create(id="bh-s", name="bh-slow", command="sleep 30")

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        await runner.submit("bh-q")
        await runner.submit("bh-s")
        # Wait for the quick task's `true` to finish and the slow
        # `sleep 30` to be in-flight.
        await asyncio.sleep(0.2)
        await runner.shutdown()

    asyncio.run(_go())

    # The quick task completed naturally — its status must NOT be
    # overwritten to 'interrupted'.
    quick_status = repo.get("bh-q").status
    assert quick_status != STATUS_INTERRUPTED, (
        f"runner#1: completed task wrongly marked interrupted "
        f"(status={quick_status!r}); shutdown must only mark "
        f"still-running rows as interrupted"
    )


# ---------------------------------------------------------------------------
# runner#2 — parent-task cancellation must kill the subprocess (NFR-03)
# ---------------------------------------------------------------------------


def test_cancelled_run_kills_subprocess_no_orphan(monkeypatch, repo):
    """runner#2 — If the BackgroundRunner task is cancelled mid-run,
    the spawned subprocess must be killed and reaped — no orphan.

    Failure mode (RED): cancelling the parent task cancels
    `proc.communicate()` but does NOT kill `proc`; the subprocess
    leaks as an orphan. The test scans the process tree for a
    child PID spawned by the test process and asserts no leaks.

    Uses the subprocess path (no `runner_fn`), so BackgroundRunner
    calls `_run_subprocess` → `asyncio.create_subprocess_exec`
    → `proc.communicate()` → the buggy cancel-leaves-proc-alive path.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "5.0")
    # Give BackgroundRunner enough per-task timeout that the cancel
    # path is the one exercised, not the timeout path.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "60.0")

    # Pre-create a row whose command spawns a long subprocess.
    repo.create(id="bh-cancel", name="bh-cancel", command="sleep 30")

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        # Subprocess path (no runner_fn) — spawns `sleep 30`.
        row_id = await runner.submit("bh-cancel")
        inner_task = runner._in_flight[row_id]
        # Give the subprocess a moment to actually start.
        await asyncio.sleep(0.3)
        inner_task.cancel()
        # Suppress the CancelledError; we want to observe the
        # post-cancel state.
        try:
            await inner_task
        except (asyncio.CancelledError, BaseException):
            pass

    asyncio.run(_go())

    # Allow a brief grace period for SIGKILL delivery + reap.
    time.sleep(0.5)

    # Look up child PIDs of the current process. With the fix in
    # place, the spawned `sleep 30` must be gone. On macOS the
    # /proc/{pid}/task/{pid}/children file does not exist, so fall
    # back to enumerating all processes and looking for one whose
    # ppid is us (the test runner pid was the runner.run caller).
    leaked = []
    try:
        my_pid = os.getpid()
        children_path = Path(f"/proc/{my_pid}/task/{my_pid}/children")
        if children_path.exists():
            for pid_str in children_path.read_text().split():
                try:
                    leaked.append(int(pid_str))
                except ValueError:
                    pass
        else:
            # POSIX fallback: `ps -axo pid,ppid,comm` and look for
            # children of the asyncio.run() subloop process.
            import subprocess as _sp
            ps = _sp.run(
                ["ps", "-axo", "pid,ppid,comm"],
                capture_output=True, text=True, check=False,
            )
            for line in ps.stdout.splitlines()[1:]:
                parts = line.split(None, 2)
                if len(parts) < 3:
                    continue
                pid_i, ppid_i, comm = parts
                if comm.strip() == "sleep" and int(ppid_i) == my_pid:
                    leaked.append(int(pid_i))
    except OSError:
        pass  # If we cannot enumerate, the test cannot fail-on-leak.

    assert leaked == [], (
        f"runner#2: orphan child processes survived cancellation: {leaked}"
    )


# ---------------------------------------------------------------------------
# runner#3 — BackgroundRunner must write task_results row (AC-2.6)
# ---------------------------------------------------------------------------


def test_background_runner_writes_task_results_row(monkeypatch, repo):
    """runner#3 — A run executed via BackgroundRunner must appear in
    `GET /v1/tasks/{id}/runs` history. The current code only updates
    `set_status`; `add_result` is never called for the BackgroundRunner
    path.

    Failure mode (RED): run a BackgroundRunner task to completion;
    the resulting row has no task_results entry — run history is empty.
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "5.0")

    repo.create(id="bh-row", name="bh-row", command="ignored")

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        await runner.submit("bh-row", runner_fn=lambda _tid: asyncio.sleep(0.1))
        # Wait for the task body to finish.
        await asyncio.sleep(0.3)
        await runner.shutdown()

    asyncio.run(_go())

    # AC-2.6: the run must be observable via list_results.
    page, _ = repo.list_results("bh-row", limit=10, cursor=None)
    assert len(page) >= 1, (
        f"runner#3: BackgroundRunner run missing from task_results history "
        f"(got {len(page)} rows); AC-2.6 requires run history"
    )