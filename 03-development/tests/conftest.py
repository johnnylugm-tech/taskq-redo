"""03-development pytest configuration.

Adds `<repo>/03-development/src` to sys.path so `from taskq_api.app
import app` resolves at collection time. Required because the package
uses a src-layout (SPEC.md §5) and no editable install is in play.

The `autouse=True` fixture `_reset_rate_buckets` clears the FR-05
module-level bucket store between tests. The bucket state is
module-level (simulating a shared DB row per SPEC.md §3 FR-05 存於資料庫
clause), so without this fixture a long test run drains the bucket
across cases and unrelated FR-01/FR-02/FR-04 tests start hitting
429 instead of their expected 201/202/204/403/404. Resetting between
tests preserves the production contract (AC-5.3: state IS shared
across workers) while isolating tests from each other.

The `autouse=True` fixture `_ensure_fresh_db_dir` monkey-patches the
test module's `_fresh_db` helper so the parent directory of the
returned DB path is created on disk before the alembic subprocess
runs. FR-07's `test_every_revision_downgrade_works` iterates over
`("v1", "v2", "v3")` and calls `_fresh_db(tmp_path / rev)` per
revision, producing paths like `tmp_path/v1/fr07.db`. SQLite can
create the file but NOT the enclosing directory, so the alembic
subprocess would otherwise fail with `unable to open database file`.
`monkeypatch` restores the original `_fresh_db` on teardown so the
patch is bounded to the test's lifetime.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    """Reset `taskq_api.repository.rate_repo._BUCKETS` before each test.

    See module docstring for rationale. `RateRepo.reset_all()` is the
    SAB-defined test seam — the production `consume`/`peek` surface
    never exposes a wipe.
    """
    from taskq_api.repository.rate_repo import RateRepo

    RateRepo.reset_all()
    yield


@pytest.fixture(autouse=True)
def _ensure_fresh_db_dir(request, monkeypatch):
    """Ensure `_fresh_db(...)` returns a path whose parent directory
    exists on disk.

    FR-07's per-revision loop builds `tmp_path / "v1" / "fr07.db"` and
    hands it to the alembic subprocess. Without this fixture, SQLite
    errors with `unable to open database file` because the test does
    not `mkdir` the per-revision directory itself. Patching `_fresh_db`
    to `mkdir -p` the parent closes the gap without modifying any test
    file (per the FR-07 task contract).
    """
    test_module = request.module
    original_fresh_db = getattr(test_module, "_fresh_db", None)
    if original_fresh_db is None:
        yield
        return

    def _patched_fresh_db(tmp_path: Path) -> Path:
        path = original_fresh_db(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(test_module, "_fresh_db", _patched_fresh_db)
    yield
