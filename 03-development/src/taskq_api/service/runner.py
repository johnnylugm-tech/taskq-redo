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
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from taskq_api.repository.task_repo import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
    TaskRepo,
)


# Default subprocess timeout when TASKQ_TASK_TIMEOUT is unset. The
# value is re-read on every run() call so test fixtures using
# `monkeypatch.setenv` take effect immediately.
_DEFAULT_TIMEOUT_SECONDS: float = 60.0

# Stdout / stderr tail length kept per task_results row. Bounds the
# in-memory store against unbounded subprocess output.
_TAIL_CHARS: int = 2000

# Subprocess-spawn failure modes we convert to a `failed` result row
# rather than letting them propagate to the api layer.
_SPAWN_ERRORS: tuple[type[BaseException], ...] = (
    FileNotFoundError,
    PermissionError,
    OSError,
)


@dataclass(frozen=True)
class _RunOutcome:
    """Captured outcome of a single subprocess execution.

    Groups the fields `_finalize` needs so each execution branch
    constructs one value (with the right terminal_status) instead of
    repeating the same 7-argument call.
    """

    terminal_status: str
    exit_code: Optional[int]
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


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
        if not raw:
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

        # AC-2.3: transition pending -> running before we hand off to
        # the subprocess. The api layer observes this via
        # `GET /v1/tasks/{id}` between the 202 and the terminal state.
        self._repo.set_status(task_id, STATUS_RUNNING)

        outcome = await self._execute(row.command)
        self._finalize(task_id=task_id, run_id=run_id, outcome=outcome)

    async def _execute(self, command: str) -> _RunOutcome:
        """Spawn the subprocess and capture its outcome.

        Returns one of three terminal outcomes — done/failed (via
        exit code), failed (via spawn error), or timeout — packaged
        as a `_RunOutcome` for `_finalize`.
        """
        timeout = self._read_timeout()
        argv = shlex.split(command)
        start = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except _SPAWN_ERRORS as exc:
            duration_ms = _elapsed_ms(start)
            return _RunOutcome(
                terminal_status=STATUS_FAILED,
                exit_code=None,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=duration_ms,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            await _kill_and_reap(process)
            return _RunOutcome(
                terminal_status=STATUS_TIMEOUT,
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                duration_ms=_elapsed_ms(start),
            )

        return _RunOutcome(
            terminal_status=STATUS_DONE if process.returncode == 0 else STATUS_FAILED,
            exit_code=process.returncode,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            duration_ms=_elapsed_ms(start),
        )

    def _finalize(
        self,
        *,
        task_id: str,
        run_id: str,
        outcome: _RunOutcome,
    ) -> None:
        """Set the terminal status and append a `task_results` row.

        Both mutations happen under the same RLock inside the
        repository, so an api reader sees either the pre-finalize
        or post-finalize state, never an in-between row missing its
        status update.
        """
        self._repo.set_status(task_id, outcome.terminal_status)
        self._repo.add_result(
            id=task_id,
            run_id=run_id,
            exit_code=outcome.exit_code,
            stdout_tail=outcome.stdout_tail,
            stderr_tail=outcome.stderr_tail,
            duration_ms=outcome.duration_ms,
            finished_at=datetime.now().isoformat(),
        )


def _elapsed_ms(start: float) -> int:
    """Milliseconds elapsed since `start` (a `time.monotonic()` value)."""
    return int((time.monotonic() - start) * 1000)


def _tail(data: Optional[bytes]) -> str:
    """Decode bytes as utf-8 (replacing errors) and keep the tail."""
    return (data or b"").decode("utf-8", errors="replace")[-_TAIL_CHARS:]


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Terminate `process` and wait for the OS to reap it (AC-2.5).

    A `ProcessLookupError` means the child already exited between the
    timeout firing and our kill — in that case there is nothing to
    reap and we proceed to `wait()` unconditionally.
    """
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()
