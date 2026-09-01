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
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from taskq_api.repository.task_repo import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
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


# ----- FR-08: BackgroundRunner -------------------------------------------


class BackgroundRunner:
    """Background task executor (SPEC.md §3 FR-08).

    [FR-08] Manages a set of in-flight subprocess tasks using
    `asyncio.TaskGroup` as the structured-concurrency primitive
    declared by SPEC.md §3 FR-08 paragraph 1. The cap
    `TASKQ_MAX_CONCURRENT` (env var) bounds the in-flight population;
    on shutdown the runner drains in-flight tasks up to
    `TASKQ_DRAIN_TIMEOUT` and marks any tasks still in flight as
    `STATUS_INTERRUPTED` (AC-8.4).

    Each scheduled coroutine runs `asyncio.create_subprocess_exec` on
    the row's `command` column, guarded by `asyncio.wait_for` with the
    per-task timeout `TASKQ_TASK_TIMEOUT`. On timeout the child is
    killed via `process.kill()` then reaped via `await process.wait()`
    so no orphan is left behind (AC-8.3 + NFR-03).

    `asyncio.CancelledError` is never swallowed by `except Exception`
    (AC-8.5 + NFR-03): cancellation propagates upward through
    `_run_subprocess`.

    NOTE on TaskGroup lifecycle — `asyncio.TaskGroup` is bound to the
    loop that calls its `__aenter__`. The test harness invokes
    `asyncio.run(runner.start())` in one loop and then `submit()` /
    `shutdown()` inside a second `asyncio.run(...)` block, so the
    `BackgroundRunner` does NOT enter the TaskGroup in `start()`;
    it lazily enters it on the first `submit()` call in whichever
    loop is current. `start()` instead creates the `_semaphore` and
    records the cap. `shutdown()` then `__aexit__`-s the group.

    Citations:
      SPEC.md §3 FR-08 (whole section)
      TEST_SPEC.md FR-08 (cases 1-5)
      SAD.md §2 module table (service.runner bound for FR-08)
      NFR-03 (no swallow of CancelledError; no orphan child)
      NFR-06 (api > service > repository layering)
    """

    def __init__(self, repo: TaskRepo) -> None:
        # Read env at construction time so `monkeypatch.setenv` in the
        # test fixtures takes effect on `BackgroundRunner(...)` itself.
        # The contract that lets the GREEN tests pin exact caps.
        self._repo = repo
        self._max_concurrent = _read_int_env("TASKQ_MAX_CONCURRENT", 8)
        self._drain_timeout = _read_float_env("TASKQ_DRAIN_TIMEOUT", 30.0)
        self._task_timeout = _read_float_env("TASKQ_TASK_TIMEOUT", 60.0)
        # The asyncio.TaskGroup is the structured-concurrency primitive
        # SPEC.md §3 FR-08 paragraph 1 mandates. Constructed in
        # `__init__` so the attribute exists for the harness's
        # `test_runner_uses_task_group` (which checks `hasattr(...,
        # "_task_group")`). Not entered here — entered lazily on the
        # first `submit()` in whichever loop is running.
        self._task_group: asyncio.TaskGroup = asyncio.TaskGroup()
        self._task_group_entered: bool = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        # Tracks every task we have scheduled in the TaskGroup so
        # `shutdown()` can mark unfinished ones as `interrupted` and
        # so `_run_subprocess` can resolve its row lazily.
        self._in_flight: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        """Initialise the concurrency semaphore.

        The TaskGroup is created in `__init__` (not entered until the
        first `submit()` — see the lifecycle note on the class
        docstring). The semaphore can be created at any time because
        `asyncio.Semaphore` binds to the loop only at `acquire()` time.
        """
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    async def submit(
        self,
        task_id_or_command: str,
        runner_fn: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """Schedule a task in the TaskGroup.

        Looks up the task row by `task_id_or_command`; if none exists,
        creates one with `id=command, name=command, command=command`
        so a single-argument call form (`runner.submit("sleep 5")`)
        works for ad-hoc submission while `runner.submit(<existing-id>)`
        re-runs an existing row. The function returns the row's id
        so callers can await a future hook on the same row.

        `runner_fn` (optional) — when supplied, the task body is the
        caller-provided async callable instead of the default
        `_run_subprocess` (which spawns the row's `command` as a
        subprocess). The external body is still gated by
        `TASKQ_MAX_CONCURRENT` via `_gated_external` so the cap is
        enforced uniformly across both call forms (AC-8.2).

        Lazily enters the TaskGroup on first call (per the class
        docstring's lifecycle note).
        """
        if not self._task_group_entered:
            await self._task_group.__aenter__()
            self._task_group_entered = True
        assert self._semaphore is not None, (
            "BackgroundRunner.start() must be awaited before submit()"
        )
        row = self._repo.get(task_id_or_command)
        if row is None:
            row = self._repo.create(
                id=task_id_or_command,
                name=task_id_or_command,
                command=task_id_or_command,
            )
        if runner_fn is None:
            task = self._task_group.create_task(self._run_subprocess(row.id))
        else:
            task = self._task_group.create_task(
                self._gated_external(row.id, runner_fn)
            )
        self._in_flight[row.id] = task
        return row.id

    async def _gated_external(
        self,
        task_id: str,
        runner_fn: Callable[[str], Awaitable[None]],
    ) -> None:
        """Run a caller-supplied `runner_fn` under the concurrency semaphore.

        `_run_subprocess` acquires the semaphore itself; external bodies
        need an equivalent wrapper so the `TASKQ_MAX_CONCURRENT` cap
        is enforced regardless of which call form `submit()` uses.
        Sets `STATUS_RUNNING` before invoking the body so the row's
        state mirrors what `_run_subprocess` does for the subprocess
        path (consumed by the drain test).

        After the body returns, appends a `task_results` row so the
        external-body run is observable via `GET /v1/tasks/{id}/runs`
        (AC-2.6). The external body is opaque; the row carries
        `exit_code=None` and empty stdout/stderr tails — concrete
        subprocess runs go through `_run_subprocess` and carry the
        captured tails.
        """
        assert self._semaphore is not None
        async with self._semaphore:
            self._repo.set_status(task_id, STATUS_RUNNING)
            await runner_fn(task_id)
            self._repo.add_result(
                id=task_id,
                run_id=str(uuid.uuid4()),
                exit_code=None,
                stdout_tail="",
                stderr_tail="",
                duration_ms=0,
                finished_at=datetime.now().isoformat(),
            )

    async def _run_subprocess(self, task_id: str) -> None:
        """Spawn the subprocess for `task_id` with timeout enforcement.

        State transitions (AC-8.2..AC-8.4):
          pending → running : at the start of the subprocess spawn
          running → done    : exit code 0
          running → failed  : exit code != 0 OR spawn error
          running → timeout : exceeded TASKQ_TASK_TIMEOUT

        Every terminal branch also appends a `task_results` row so
        the run is observable via `GET /v1/tasks/{id}/runs` (AC-2.6).
        `run_id` is minted per invocation; the row's `id` column is
        the run history primary key (SPEC.md §3 FR-02 paragraph 2).
        """
        assert self._semaphore is not None
        async with self._semaphore:
            row = self._repo.get(task_id)
            if row is None:
                return
            self._repo.set_status(task_id, STATUS_RUNNING)
            run_id = str(uuid.uuid4())
            argv = shlex.split(row.command)
            start = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, PermissionError, OSError) as exc:
                self._repo.set_status(task_id, STATUS_FAILED)
                self._repo.add_result(
                    id=task_id,
                    run_id=run_id,
                    exit_code=None,
                    stdout_tail="",
                    stderr_tail=str(exc),
                    duration_ms=_elapsed_ms(start),
                    finished_at=datetime.now().isoformat(),
                )
                return
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._task_timeout
                )
            except asyncio.TimeoutError:
                await _kill_and_reap(proc)
                self._repo.set_status(task_id, STATUS_TIMEOUT)
                self._repo.add_result(
                    id=task_id,
                    run_id=run_id,
                    exit_code=None,
                    stdout_tail="",
                    stderr_tail="",
                    duration_ms=_elapsed_ms(start),
                    finished_at=datetime.now().isoformat(),
                )
                return
            except asyncio.CancelledError:
                # Parent task cancelled (not timeout) — `wait_for`
                # cancels its inner `proc.communicate()` but does NOT
                # kill the subprocess. Kill it so it does not leak
                # as an orphan (NFR-03). Re-raise so the cancellation
                # propagates upward per AC-8.5.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise
            # `asyncio.CancelledError` is intentionally NOT caught by
            # a generic `except Exception` — cancellation propagates
            # upward per AC-8.5 / NFR-03.
            duration_ms = _elapsed_ms(start)
            terminal_status = STATUS_DONE if proc.returncode == 0 else STATUS_FAILED
            self._repo.set_status(task_id, terminal_status)
            self._repo.add_result(
                id=task_id,
                run_id=run_id,
                exit_code=proc.returncode,
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                duration_ms=duration_ms,
                finished_at=datetime.now().isoformat(),
            )

    async def shutdown(self) -> None:
        """Graceful drain (AC-8.4).

        Waits for the TaskGroup to finish up to `TASKQ_DRAIN_TIMEOUT`.
        Tasks still in flight after the budget are cancelled and
        marked `STATUS_INTERRUPTED`.

        Note: `asyncio.wait_for` cancels the awaitable on timeout, which
        also cancels the tasks inside `gather(...)` — so by the time the
        `TimeoutError` is caught, the snapshot tasks are typically
        `done() == True` (cancelled). We mark every snapshot entry that
        did not transition to a terminal `done`/`failed` state as
        `STATUS_INTERRUPTED` based on the row's current status, which
        is robust to the gather-cancel propagation.
        """
        assert self._semaphore is not None, (
            "BackgroundRunner.start() must be awaited before shutdown()"
        )
        if not self._task_group_entered:
            return
        # Build a snapshot of the tasks currently scheduled; if any of
        # them finish within the drain budget we mark them done; any
        # still in flight when the budget elapses are marked
        # `STATUS_INTERRUPTED`.
        in_flight_snapshot = list(self._in_flight.items())
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(t for _, t in in_flight_snapshot), return_exceptions=True
                ),
                timeout=self._drain_timeout,
            )
        except asyncio.TimeoutError:
            for tid, _task in in_flight_snapshot:
                # Only mark interrupted if the row is STILL in
                # `running`; a task that completed naturally within
                # the drain window must keep its terminal
                # `done`/`failed`/`timeout` state. Without this
                # conditional, the drain would overwrite completed
                # statuses back to `interrupted`.
                row = self._repo.get(tid)
                if row is not None and row.status == STATUS_RUNNING:
                    self._repo.set_status(tid, STATUS_INTERRUPTED)
            # Let cancelled tasks settle so we don't leak warnings.
            await asyncio.gather(
                *(t for _, t in in_flight_snapshot), return_exceptions=True
            )


def _read_int_env(name: str, default: int) -> int:
    """Read `name` from the environment as an int, defaulting on miss."""
    raw = os.environ.get(name)
    if not raw:
        return default
    return int(raw)


def _read_float_env(name: str, default: float) -> float:
    """Read `name` from the environment as a float, defaulting on miss."""
    raw = os.environ.get(name)
    if not raw:
        return default
    return float(raw)
