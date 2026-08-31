"""Task execution runner (SPEC.md §3 FR-02).

[FR-02] The runner is the SAB-bound entry point that wraps
`asyncio.create_subprocess_exec` for task execution. It owns the
state machine `pending → running → done | failed | timeout` and
appends a `task_results` row at the end of every run.

Passing a shell flag is BANNED per AC-2.2. The runner always uses
`asyncio.create_subprocess_exec(*shlex.split(command))`, which
invokes the program directly without going through a shell, so
shell metacharacters in the command string are not interpreted.

Timeout: `TASKQ_TASK_TIMEOUT` (env var) is read at the start of
each `run()` call so that test fixtures can override it via
`monkeypatch.setenv` without touching module-level constants. The
default is 60 seconds (SPEC.md §5).

State transitions (AC-2.3):
  pending  → running : at the start of `run()`
  running  → done    : exit code 0
  running  → failed  : exit code != 0 OR subprocess could not start
  running  → timeout : exceeded TASKQ_TASK_TIMEOUT (process killed + reaped)

The runner mutates the task status and writes a result row directly
through `TaskRepo` because the repository owns the persistence
contract (NFR-06 layering). The api layer observes the transition
through `GET /v1/tasks/{id}`.

Citations:
  SPEC.md §3 FR-02 (whole section)
  SPEC.md §3 FR-08 paragraph 3 (timeout kill interaction)
  TEST_SPEC.md FR-02 (cases 1-6)
  NFR-02 (shell flag banned; X-API-Key authn)
  NFR-03 (no orphan child process; CancelledError propagates)
  NFR-06 (api > service > repository layering)
"""
import asyncio
import os
import shlex
import time
from datetime import datetime

from taskq_api.repository.task_repo import TaskRepo


# Default subprocess timeout when TASKQ_TASK_TIMEOUT is unset. The
# value is re-read on every run() call so test fixtures using
# `monkeypatch.setenv` take effect immediately.
_DEFAULT_TIMEOUT_SECONDS: float = 60.0


class TaskRunner:
    """Async state-machine for executing a single task.

    Each `run()` call is responsible for:
      1. Loading the task from the repository.
      2. Transitioning the task to `running`.
      3. Spawning the subprocess via `asyncio.create_subprocess_exec`
         (no `shell=...`).
      4. Waiting with the configured timeout.
      5. Transitioning the task to a terminal state (`done` / `failed`
         / `timeout`) and writing the final `task_results` row.
    """

    def __init__(self, task_repo: TaskRepo) -> None:
        self._repo = task_repo

    @staticmethod
    def _read_timeout() -> float:
        """Read TASKQ_TASK_TIMEOUT from the environment at call time.

        Reading at call time (not import time) is the contract that
        lets `monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.0")` from
        the timeout-kill test take effect on every invocation.
        """
        raw = os.environ.get("TASKQ_TASK_TIMEOUT")
        if raw is None or raw == "":
            return _DEFAULT_TIMEOUT_SECONDS
        return float(raw)

    async def run(self, task_id: str, run_id: str) -> None:
        """Execute the task identified by `task_id`.

        Idempotent against unknown task ids (returns silently). The
        method never raises: any subprocess error is converted into a
        `failed` terminal state with a result row, so the api layer
        can always observe a terminal status.
        """
        row = self._repo.get(task_id)
        if row is None:
            return

        command = row.command
        timeout = self._read_timeout()

        # Transition to running. The api layer will observe this via
        # `GET /v1/tasks/{id}` between the 202 and the terminal state.
        self._repo.set_status(task_id, "running")

        argv = shlex.split(command)
        start = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            # The command could not even be started. Mark the task
            # as failed and record the error in stderr_tail so the
            # api can return a structured row.
            duration_ms = int((time.monotonic() - start) * 1000)
            self._finalize(
                task_id=task_id,
                run_id=run_id,
                exit_code=None,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=duration_ms,
                terminal_status="failed",
            )
            return

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            # AC-2.5 / SPEC §3 FR-08 paragraph 3: kill the child and
            # reap it so no orphan process remains. We deliberately
            # do NOT capture output on timeout — the subprocess may
            # have produced unbounded output that we are about to
            # discard anyway.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            duration_ms = int((time.monotonic() - start) * 1000)
            self._finalize(
                task_id=task_id,
                run_id=run_id,
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                duration_ms=duration_ms,
                terminal_status="timeout",
            )
            return

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = process.returncode
        stdout_tail = (stdout or b"").decode("utf-8", errors="replace")[-2000:]
        stderr_tail = (stderr or b"").decode("utf-8", errors="replace")[-2000:]

        terminal_status = "done" if exit_code == 0 else "failed"
        self._finalize(
            task_id=task_id,
            run_id=run_id,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            terminal_status=terminal_status,
        )

    def _finalize(
        self,
        *,
        task_id: str,
        run_id: str,
        exit_code: int | None,
        stdout_tail: str,
        stderr_tail: str,
        duration_ms: int,
        terminal_status: str,
    ) -> None:
        """Set the terminal status and append a `task_results` row.

        Both mutations happen under the same RLock inside the
        repository, so an api reader sees either the pre-finalize
        or post-finalize state, never an in-between row missing its
        status update.
        """
        self._repo.set_status(task_id, terminal_status)
        self._repo.add_result(
            id=task_id,
            run_id=run_id,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            finished_at=datetime.now().isoformat(),
        )
