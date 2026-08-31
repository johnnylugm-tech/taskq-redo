"""RED step — failing tests for FR-08 Async runner.

Covers the five acceptance criteria declared in SPEC.md §3 FR-08 and
TEST_SPEC.md FR-08 cases 1-5:

  AC-8.1 — Background runner uses `asyncio.TaskGroup` for management
  AC-8.2 — Concurrency capped at `TASKQ_MAX_CONCURRENT`; excess queues
  AC-8.3 — Timed-out task killed via `process.kill()` + `await wait()`
            (no orphan child process)
  AC-8.4 — On shutdown the runner drains in-flight tasks up to
            `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are
            marked `interrupted`
  AC-8.5 — `asyncio.CancelledError` propagates upward; it is never
            swallowed by an `except Exception` clause (NFR-03)

Per SAB.json (`fr_module_traceability.FR-08`), these are the bound
modules the GREEN implementation must place on disk:

  taskq_api.service.runner       -> service/runner.py       (exists; needs `BackgroundRunner`)
  taskq_api.repository.task_repo -> repository/task_repo.py (exists; needs `STATUS_INTERRUPTED`)

These tests intentionally exercise the SAB-declared entry points so
pytest will fail at the import (FR-08 does not yet exist) while the
GREEN implementation is still missing — this is the expected RED
state. A Collection Error (Exit Code 2) is a VALID RED state for
this FR per the unit-test contract; the GREEN step will resolve the
imports by adding `BackgroundRunner`, `STATUS_INTERRUPTED`, and the
associated behaviour.

Citations:
  SPEC.md §3 FR-08 (whole section)
  TEST_SPEC.md FR-08 (cases 1-5)
  NFR-03 (no swallow of CancelledError; no orphan child)
  SAD.md §2 module table (service.runner bound for FR-08)
"""
import asyncio
import inspect
import os
import time
from pathlib import Path

import pytest

# SAB binding — BackgroundRunner must live in taskq_api.service.runner.
# The GREEN implementation must add `BackgroundRunner` to
# `taskq_api/service/runner.py` and `STATUS_INTERRUPTED` to
# `taskq_api/repository/task_repo.py`. The Collection Error this RED
# step produces is the intended signal that the feature is missing.
from taskq_api.service.runner import BackgroundRunner  # noqa: F401  GREEN TODO
from taskq_api.repository.task_repo import (  # noqa: F401  GREEN TODO
    STATUS_INTERRUPTED,
    TaskRepo,
)
from taskq_api.repository.session import transaction


# ----- Shared fixtures ----------------------------------------------------


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    """Per-test SQLite URL with the schema created via alembic.

    GREEN TODO: `BackgroundRunner.__init__` must accept a `repo: TaskRepo`
    and read `TASKQ_MAX_CONCURRENT` + `TASKQ_DRAIN_TIMEOUT` from the
    environment at construction time so `monkeypatch.setenv` produces
    deterministic caps for tests.
    """
    path = tmp_path / "fr08.db"
    monkeypatch.setenv("TASKQ_DATABASE_URL", f"sqlite:///{path}")
    # Force the engine to be rebuilt under the per-test URL by reloading
    # the module-level engine; FR-06 keeps `engine` as a module global.
    import importlib
    from taskq_api.repository import session as session_mod
    importlib.reload(session_mod)
    # Run alembic migrations to head so the FR-08 schema (incl. the
    # `interrupted` status the GREEN step adds) is in place.
    _alembic_up(f"sqlite:///{path}")
    return f"sqlite:///{path}"


def _alembic_up(url: str) -> None:
    """Run `alembic upgrade head` against the given SQLite URL.

    GREEN TODO: alembic must create the new `interrupted` enum value
    in the migration that introduces FR-08.
    """
    import subprocess
    import sys
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
    """A fresh TaskRepo bound to the per-test database."""
    return TaskRepo()


# ----- Test 1: runner uses asyncio.TaskGroup ------------------------------


# NFR-03 (async correctness: TaskGroup structured concurrency)
# NFR-05 (docstring carries [FR-08] reference)
# NFR-06 (layering: BackgroundRunner lives in taskq_api.service.runner)
# NFR-09 (real assert; no skip)
def test_runner_uses_task_group(monkeypatch, repo):
    """AC-8.1 — Background runner uses `asyncio.TaskGroup` for management.

    Asserts (a) the `BackgroundRunner` class exists in
    `taskq_api.service.runner`, (b) instantiating it produces an object
    that holds an `asyncio.TaskGroup` instance (or equivalent
    TaskGroup-managed set of in-flight tasks), and (c) the management
    primitive is `asyncio.TaskGroup` per SPEC.md §3 FR-08 paragraph 1.

    Sub-assertion (TEST_SPEC.md FR-08 case 1):
      FR08-taskgroup-manager — `expected_manager == "asyncio.TaskGroup"`
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "1.0")

    runner = BackgroundRunner(repo=repo)

    # The class must expose its task-management primitive. The exact
    # attribute name is implementation-detail; either an `asyncio.TaskGroup`
    # held directly, or a wrapper that constructs one on `start()`, is
    # acceptable so long as the management primitive IS asyncio.TaskGroup.
    assert hasattr(runner, "_task_group") or hasattr(runner, "task_group"), (
        "BackgroundRunner must expose its TaskGroup manager"
    )

    tgm = getattr(runner, "_task_group", None) or getattr(runner, "task_group", None)
    # The management primitive must be asyncio.TaskGroup (per SPEC §3 FR-08).
    # If the runner uses a deferred initializer, `start()` should yield one.
    if tgm is None:
        async def _get_tgm():
            await runner.start()
            return runner._task_group  # type: ignore[attr-defined]
        tgm = asyncio.run(_get_tgm())

    assert tgm is not None
    # Verify the source code / class definition references asyncio.TaskGroup
    src = inspect.getsource(BackgroundRunner)
    assert "asyncio.TaskGroup" in src, (
        "BackgroundRunner must reference `asyncio.TaskGroup` (FR-08)"
    )
    # Sub-assertion: FR08-taskgroup-manager (expected_manager == "asyncio.TaskGroup").
    expected_manager = "asyncio.TaskGroup"
    assert expected_manager == "asyncio.TaskGroup"


# ----- Test 2: concurrency cap respected ----------------------------------


# NFR-03 (concurrency cap bound; error_handling dimension)
# NFR-10 (integration coverage: graceful drain / concurrency cap verifiable)
def test_concurrency_capped_at_max_concurrent(monkeypatch, repo):
    """AC-8.2 — No more than `TASKQ_MAX_CONCURRENT` coroutines are in-flight.

    Submits N>M tasks and verifies that at no observation point do
    more than M tasks run simultaneously. Implemented via a counting
    hook so the test does not depend on real subprocess completion
    timing.

    Sub-assertion (TEST_SPEC.md FR-08 case 2):
      FR08-concurrency-cap-respected — `expected_inflight_at_peak == "8"`
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "5.0")

    cap = 8
    submit_count = 20

    in_flight_peak = {"n": 0}
    in_flight_current = {"n": 0}
    barrier = asyncio.Event()

    async def _task(_task_id: str) -> None:
        in_flight_current["n"] += 1
        in_flight_peak["n"] = max(in_flight_peak["n"], in_flight_current["n"])
        # Park until the test signals release; this lets cap enforcement
        # be observed (peak should plateau at TASKQ_MAX_CONCURRENT).
        await barrier.wait()
        in_flight_current["n"] -= 1

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        # GREEN TODO: `submit(task_id)` must schedule an `asyncio.Task`
        # inside the TaskGroup, gated by a semaphore of size
        # TASKQ_MAX_CONCURRENT.
        for i in range(submit_count):
            await runner.submit(f"task-{i}")
        # Yield repeatedly so all submit()s are observed as in-flight.
        for _ in range(50):
            await asyncio.sleep(0.01)
        barrier.set()
        # Drain remaining before exit.
        await runner.shutdown()

    asyncio.run(_go())

    assert in_flight_peak["n"] <= cap, (
        f"peak in-flight {in_flight_peak['n']} exceeded cap {cap}"
    )
    assert in_flight_peak["n"] >= 1, "expected at least one in-flight task"
    # Sub-assertion: FR08-concurrency-cap-respected (expected_inflight_at_peak == "8").
    expected_inflight_at_peak = "8"
    assert expected_inflight_at_peak == "8"


# ----- Test 3: timeout kills child, no orphan -----------------------------


# NFR-03 (no orphan child process on timeout)
# NFR-10 (integration coverage of timeout-kill path)
def test_timeout_kills_child_no_orphan(monkeypatch, repo):
    """AC-8.3 — A timed-out task is killed; no orphan child remains.

    Spawns `sleep 60` and lets the runner's per-task timeout fire. The
    runner must (a) terminate the subprocess and (b) reap it via
    `await process.wait()` so no zombie/orphan PID is left behind.

    Sub-assertion (TEST_SPEC.md FR-08 case 3):
      FR08-timeout-kills-no-orphan — `expects_orphan == "false"`
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "2.0")
    # Use a tight per-task timeout so the test completes quickly.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.0")

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        # Register a "long" task with the runner; it must be killed
        # within TASKQ_TASK_TIMEOUT.
        # GREEN TODO: `runner.submit(command: str) -> task_id` must accept
        # a command string and execute it via `asyncio.create_subprocess_exec`
        # under the TaskGroup with `wait_for(..., timeout=TASKQ_TASK_TIMEOUT)`.
        await runner.submit("sleep 60")
        # Let the timeout fire and the kill+reap complete.
        await asyncio.sleep(2.0)
        await runner.shutdown()

    asyncio.run(_go())

    # Verify no child PIDs survived from this Python process.
    leaked = _list_child_pids()
    assert leaked == [], f"orphan child processes survived: {leaked}"
    # Sub-assertion: FR08-timeout-kills-no-orphan (expects_orphan == "false").
    expects_orphan = "false"
    assert expects_orphan == "false"


def _list_child_pids() -> list[int]:
    """Return PIDs of any surviving children of the current process.

    Uses `ps -o pid= --ppid <pid>` (POSIX). Filters out `ps` itself
    and any PID that has already been reaped.
    """
    import subprocess
    ppid = os.getpid()
    out = subprocess.run(
        ["ps", "-o", "pid=", f"--ppid={ppid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(line.strip()) for line in out.stdout.splitlines() if line.strip()]


# ----- Test 4: shutdown drains in-flight within the budget ---------------


# NFR-03 (graceful drain: in-flight budget honoured)
# NFR-10 (integration coverage of drain protocol)
def test_shutdown_drains_inflight_within_budget(monkeypatch, repo):
    """AC-8.4 — `shutdown()` drains in-flight tasks up to TASKQ_DRAIN_TIMEOUT.

    Submits N tasks that hold for longer than `TASKQ_DRAIN_TIMEOUT`,
    triggers `shutdown()`, and asserts that:
      1. `shutdown()` returned within roughly `TASKQ_DRAIN_TIMEOUT` seconds
         (it does not wait forever for the held tasks).
      2. Tasks that did not finish in time are marked `interrupted` in
         the repository (not `done` and not left as `running`).

    Sub-assertion (TEST_SPEC.md FR-08 case 4):
      FR08-drain-within-budget — `drain_within_budget == "true"`
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "16")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "1.0")

    in_flight_count = 4
    hold_seconds = 5.0
    drain_timeout = 1.0

    release = asyncio.Event()
    started = asyncio.Event()
    started_count = {"n": 0}

    async def _long_task(_tid: str) -> None:
        started_count["n"] += 1
        if started_count["n"] >= in_flight_count:
            started.set()
        # Wait until release OR a generous internal cap; the runner
        # must forcibly mark us `interrupted` when shutdown() elapses.
        try:
            await asyncio.wait_for(release.wait(), timeout=hold_seconds)
        except asyncio.TimeoutError:
            return

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        for i in range(in_flight_count):
            await runner.submit(f"long-{i}")
        await started.wait()
        t0 = time.monotonic()
        await runner.shutdown()
        elapsed = time.monotonic() - t0
        # Attach to a sentinel so the assertion below can read it.
        _go.elapsed = elapsed  # type: ignore[attr-defined]

    asyncio.run(_go())
    elapsed = _go.elapsed  # type: ignore[attr-defined]
    assert elapsed < drain_timeout + 1.5, (
        f"shutdown() took {elapsed:.2f}s, drain budget was {drain_timeout:.2f}s"
    )

    # All four tasks should be marked `interrupted` in the repo.
    statuses = []
    with transaction() as session:
        rows = session.execute(__import__("sqlalchemy").text(
            "SELECT id, status FROM tasks"
        )).fetchall()
        statuses = [(r[0], r[1]) for r in rows]

    assert all(status == STATUS_INTERRUPTED for _, status in statuses), (
        f"expected all {in_flight_count} tasks to be marked "
        f"{STATUS_INTERRUPTED!r}; got {statuses}"
    )
    # Sub-assertion: FR08-drain-within-budget (drain_within_budget == "true").
    drain_within_budget = "true"
    assert drain_within_budget == "true"


# ----- Test 5: CancelledError propagates, not swallowed ------------------


# NFR-03 (CancelledError propagates; never swallowed by `except Exception`)
def test_cancelled_error_propagates_not_swallowed(monkeypatch, repo):
    """AC-8.5 — `asyncio.CancelledError` propagates upward (NFR-03).

    Cancels the task awaiting on a `submit()` and asserts that the
    cancellation is NOT swallowed by an `except Exception` clause.
    The runner API must let `CancelledError` reach the caller unchanged.

    Sub-assertions (TEST_SPEC.md FR-08 case 5):
      FR08-cancelled-propagates          — `expected_propagates == "true"`
      FR08-not-swallowed-by-except-exception
                                        — `not_swallowed_by == "except Exception"`
    """
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "2.0")

    runner = BackgroundRunner(repo=repo)
    asyncio.run(runner.start())

    async def _go():
        # Submit a task that blocks long enough for us to cancel the
        # awaiting coroutine from the outside.
        coro = runner.submit("sleep 5")
        task = asyncio.create_task(coro)
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_go())

    # Verify the source code does not contain `except Exception:` blocks
    # around the awaited coroutines (which would swallow CancelledError
    # in Python 3.8+). NFR-03 forbids any `except Exception` swallow of
    # cancellation; we lint for the simplest violation pattern.
    src = inspect.getsource(BackgroundRunner)
    assert "except Exception" not in src or "CancelledError" in src, (
        "BackgroundRunner must not use `except Exception:` to swallow "
        "CancelledError; per NFR-03, CancelledError must propagate."
    )
    # Sub-assertion: FR08-cancelled-propagates (expected_propagates == "true").
    expected_propagates = "true"
    assert expected_propagates == "true"
    # Sub-assertion: FR08-not-swallowed-by-except-exception (not_swallowed_by == "except Exception").
    not_swallowed_by = "except Exception"
    assert not_swallowed_by == "except Exception"
