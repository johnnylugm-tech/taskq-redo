"""Coverage tests for the FR-08 BackgroundRunner `asyncio.CancelledError`
cleanup branch (`runner.py:458-459`).

The `except ProcessLookupError: pass` defensive block handles a race where
the subprocess exits between the `wait_for` cancellation and the explicit
`proc.kill()` call. Reproducing the real race in a test is flaky — easier
to monkeypatch `proc.kill` so the `except ProcessLookupError` branch fires
deterministically while the rest of the path runs normally.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from taskq_api.repository.task_repo import TaskRepo
from taskq_api.service.runner import BackgroundRunner


def test_cancelled_error_with_already_dead_subprocess_swallows_process_lookup_error():
    """COVERAGE — runner.py:458-459 (CancelledError cleanup branch).

    Simulates the corner case where `proc.kill()` raises
    `ProcessLookupError` because the subprocess already exited between
    `wait_for` cancellation and the explicit kill. The `except
    ProcessLookupError: pass` swallows it so the CancelledError still
    propagates.
    """
    repo = TaskRepo()
    new_id = str(uuid.uuid4())
    # Long-running command so `wait_for` is awaiting when we cancel.
    repo.create(id=new_id, name="cov-task", command="sleep 5")
    runner = BackgroundRunner(repo=repo)
    # `_run_subprocess` asserts `self._semaphore is not None` at line 406
    # — BackgroundRunner.start() normally creates it, but the test
    # bypasses start() to drive `_run_subprocess` directly.
    runner._semaphore = asyncio.Semaphore(1)

    def _kill_raises_process_lookup(self):
        raise ProcessLookupError

    async def _drive() -> None:
        # Patch proc.kill on every Process instance to raise
        # ProcessLookupError so the `except ProcessLookupError: pass`
        # branch fires even though the rest of the runner sees a healthy
        # subprocess.
        with patch.object(
            asyncio.subprocess.Process,
            "kill",
            _kill_raises_process_lookup,
        ):
            # Drive the runner in a task so we can cancel mid-flight.
            task = asyncio.create_task(runner._run_subprocess(new_id))
            # Yield long enough for the subprocess to spawn and
            # `wait_for` to start awaiting the long sleep; cancel the
            # task to trigger the CancelledError handler in
            # `_run_subprocess`.
            await asyncio.sleep(0.5)
            task.cancel()
            # Re-raise CancelledError so `pytest.raises` (the outer
            # context) catches it. Without this, the runner swallows the
            # exception inside the test body and `pytest.raises` sees
            # nothing.
            try:
                await task
            except asyncio.CancelledError:
                raise

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_drive())