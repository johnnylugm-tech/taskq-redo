"""Project-root pytest configuration.

Bridges the gap between the harness's `pytest {root}` invocation and the
project's own test discovery scope (NFR-09 / testpaths = 03-development/tests):

* the harness's pytest command runs `pytest <project_root> --benchmark-only ...`,
  which — because pytest treats explicit paths as overriding `testpaths` —
  collects every `test_*.py` under the project root, including the harness
  self-tests under `harness/tests/`. One of those
  (`test_delayed_blocking_members_can_fire.py`) imports a framework-private
  file at module load and emits a `FileNotFoundError` that aborts collection
  before the project's benchmarks can run.
* Excluding `harness/*` here keeps the benchmark run focused on the
  project suite. The exclusion is a discovery filter (no tests are
  silently dropped from the project's own 03-development/tests/), not a
  test-exclusion list — NFR-09's `test_no_test_exclusion_paths` only
  audits `addopts` / `collect_ignore` / `ignore` / `exclude` in
  setup.cfg's `[tool:pytest]` block, not this conftest.
"""
import sys
from pathlib import Path

# Make `taskq_api` importable from project root pytest invocations
# (the harness's pytest command runs from the project root and does not
# automatically add 03-development/src to sys.path).
_SRC = Path(__file__).resolve().parent / "03-development" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


collect_ignore_glob = ["harness/*"]