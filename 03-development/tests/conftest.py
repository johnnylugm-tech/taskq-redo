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
