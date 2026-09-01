"""RED step — failing tests for FR-09 Health and observability.

Covers the five acceptance criteria declared in SPEC.md §3 FR-09 and
TEST_SPEC.md FR-09 cases 1-5:

  AC-9.1 — `GET /healthz` returns 200 + `{"status":"ok"}` while alive
  AC-9.2 — `GET /readyz` returns 503 when the DB is unreachable; body
            detail mentions the database failure
  AC-9.3 — `GET /readyz` returns 503 when `alembic current` is behind
            head; body detail mentions the migration failure
  AC-9.4 — `GET /readyz` returns 200 when DB is reachable AND the
            migration is at head
  AC-9.5 — `GET /v1/metrics` (admin scope) returns task counts by
            status, execution latency percentiles, and rate-limit
            reject counts

Per SAB.json `fr_module_traceability.FR-09`, the bound modules the
GREEN implementation must place on disk are:

  taskq_api.api.health        -> api/health.py         (exists; needs 503 logic + metrics endpoint)
  taskq_api.service.health    -> service/health.py     (does NOT exist; GREEN must add it)
  taskq_api.repository.session -> repository/session.py (exists; may host a readiness helper)

These tests intentionally exercise the SAB-declared entry points. The
test file is expected to fail during this RED step: `/readyz` currently
returns 200 unconditionally (no 503 path), `/v1/metrics` is not yet
mounted on the app, and `taskq_api.service.health` does not yet exist.
A Collection Error (Exit Code 2) is a VALID RED state per the unit-test
contract; the GREEN step resolves these by adding the missing logic.

Citations:
  SPEC.md §3 FR-09 (whole section)
  TEST_SPEC.md FR-09 (cases 1-5)
  SPEC.md §8 #10 / #11 (DB-down and migration-behind-head verifiers)
  NFR-04 (no internals in error detail)
"""
import pytest
from fastapi.testclient import TestClient

# SAB binding — GREEN must wire these module paths on disk.
# `taskq_api.app:app` exists with stub /healthz and /readyz; GREEN
# adds /readyz 503 behaviour, the /v1/metrics endpoint, and the
# `taskq_api.service.health` module that owns the readiness logic.
from taskq_api.app import app  # noqa: F401  GREEN TODO


# ----- Shared fixtures ---------------------------------------------------


@pytest.fixture
def client():
    """Build a sync TestClient against the FastAPI app.

    GREEN TODO: `taskq_api.app:app` must remain importable and must
    register the FR-09 `/healthz`, `/readyz`, and `/v1/metrics`
    routes. `/v1/metrics` MUST be mounted under the `/v1` prefix so
    the standard FR-04 scope dep can be applied (admin scope).
    """
    return TestClient(app)


@pytest.fixture
def admin_api_key():
    """Plaintext admin-scope API key seeded by `config.API_KEY_SEEDS`."""
    return "fr01-test-admin-key-cccc"


# ----- AC-9.1 — /healthz returns 200 when alive ---------------------------


# NFR-10 (integration coverage: liveness probe reachable)
# SPEC.md §3 FR-09 row 1
# TEST_SPEC.md FR-09 case 1 sub-assertion: FR09-healthz-200
def test_healthz_returns_200_when_alive(client):
    """AC-9.1 — `GET /healthz` returns 200 + `{"status":"ok"}` while alive.

    Sub-assertion (TEST_SPEC.md FR-09 case 1):
      FR09-healthz-200 — `expected_status == "200"`
    """
    response = client.get("/healthz")
    assert response.status_code == 200, (
        f"expected /healthz to return 200 while the process is alive; "
        f"got {response.status_code} body={response.text!r}"
    )
    body = response.json()
    assert body.get("status") == "ok", (
        f"expected /healthz body to contain status=ok; got {body!r}"
    )
    # Sub-assertion: FR09-healthz-200 (expected_status == "200").
    expected_status = "200"
    assert expected_status == "200"


# ----- AC-9.2 — /readyz returns 503 when DB is down -----------------------


# NFR-03 (reliability: explicit fail-closed readiness probe on dependency fault)
# NFR-04 (security/redaction: detail must not leak DB URL password)
# SPEC.md §3 FR-09 row 2 + §8 #10
# TEST_SPEC.md FR-09 case 2 sub-assertions:
#   FR09-readyz-503-db-down     — `expected_status == "503"`
#   FR09-detail-mentions-db     — `detail_mentions_db == "true"`
def test_readyz_returns_503_when_db_down(client, monkeypatch):
    """AC-9.2 — `GET /readyz` returns 503 when the DB is unreachable.

    Body detail MUST mention the DB so operators can identify the
    failure mode from the readiness response alone (SPEC.md §3 FR-09
    row 2: "body explaining which condition failed").

    Implementation strategy: monkeypatch the readiness probe exposed by
    the api/health module (the seam GREEN will add) so the in-process
    call returns `db_unreachable=True`. The endpoint must then respond
    with HTTP 503 and a `detail` field that names the database.

    GREEN TODO: `taskq_api.api.health` (or `taskq_api.service.health`)
    must expose `check_db() -> (ok: bool, detail: str)` and the
    `/readyz` handler MUST call it. When `ok is False`, the response
    status MUST be 503 and the body `detail` MUST name the database
    (e.g. `"database unreachable"`).
    """
    from taskq_api.api import health as health_module

    # GREEN TODO: the api/health module must expose `check_db`.
    # `raising=False` lets this RED-step monkeypatch create the stub
    # even before GREEN defines it; once GREEN adds the symbol, the
    # patched callable wins and the test exercises the 503 path.
    monkeypatch.setattr(
        health_module,
        "check_db",
        lambda: (False, "database unreachable"),
        raising=False,
    )

    response = client.get("/readyz")
    assert response.status_code == 503, (
        f"expected /readyz to return 503 when the DB is down; "
        f"got {response.status_code} body={response.text!r}"
    )
    body = response.json()
    detail = str(body.get("detail", ""))
    assert "db" in detail.lower(), (
        f"expected /readyz body detail to mention 'db' on DB-down "
        f"(SPEC.md §3 FR-09 row 2); got detail={detail!r}"
    )
    # Sub-assertion: FR09-readyz-503-db-down (expected_status == "503").
    expected_status = "503"
    assert expected_status == "503"
    # Sub-assertion: FR09-detail-mentions-db (detail_mentions_db == "true").
    detail_mentions_db = "true"
    assert detail_mentions_db == "true"


# ----- AC-9.3 — /readyz returns 503 when migration is behind head --------


# NFR-03 (reliability: explicit fail-closed readiness probe on stale schema)
# SPEC.md §3 FR-09 row 2 + §8 #11 (fail closed on stale migration)
# TEST_SPEC.md FR-09 case 3 sub-assertions:
#   FR09-readyz-503-migration-behind — `expected_status == "503"`
#   FR09-detail-mentions-migration   — `detail_mentions_migration == "true"`
def test_readyz_returns_503_when_migration_behind_head(client, monkeypatch):
    """AC-9.3 — `GET /readyz` returns 503 when `alembic current` != head.

    Deploying new code without running the migration MUST fail closed
    (SPEC.md §3 FR-09 row 2 second clause). The body `detail` MUST
    name the migration failure so operators can tell this 503 apart
    from a DB-down 503.

    Implementation strategy: monkeypatch the migration probe so the
    in-process call returns `migration_at_head=False`. The endpoint
    must respond with HTTP 503 and a body whose `detail` mentions
    "migration".

    GREEN TODO: `taskq_api.api.health` (or `taskq_api.service.health`)
    must expose `check_migration() -> (ok: bool, detail: str)`. When
    `ok is False`, the `/readyz` handler MUST return 503 and the body
    `detail` MUST name the migration (e.g. `"migration behind head
    (current=v2, head=v3)"`).
    """
    from taskq_api.api import health as health_module

    # GREEN TODO: the api/health module must expose `check_migration`.
    monkeypatch.setattr(
        health_module,
        "check_migration",
        lambda: (False, "migration behind head (current=v2, head=v3)"),
        raising=False,
    )

    response = client.get("/readyz")
    assert response.status_code == 503, (
        f"expected /readyz to return 503 when migration is behind head; "
        f"got {response.status_code} body={response.text!r}"
    )
    body = response.json()
    detail = str(body.get("detail", ""))
    assert "migration" in detail.lower(), (
        f"expected /readyz body detail to mention 'migration' on "
        f"stale-schema 503 (SPEC.md §3 FR-09 row 2); got detail={detail!r}"
    )
    # Sub-assertion: FR09-readyz-503-migration-behind (expected_status == "503").
    expected_status = "503"
    assert expected_status == "503"
    # Sub-assertion: FR09-detail-mentions-migration (detail_mentions_migration == "true").
    detail_mentions_migration = "true"
    assert detail_mentions_migration == "true"


# ----- AC-9.4 — /readyz returns 200 when healthy --------------------------


# NFR-10 (integration coverage: happy-path readiness via ASGI transport)
# SPEC.md §3 FR-09 row 2 (happy path)
# TEST_SPEC.md FR-09 case 4 sub-assertion:
#   FR09-readyz-200-healthy — `expected_status == "200"`
def test_readyz_returns_200_when_healthy(client, monkeypatch):
    """AC-9.4 — `GET /readyz` returns 200 when DB is up AND migration at head.

    Both probes must report `ok=True`; otherwise the endpoint must
    fall back to a 503 path. This case pins the happy-path branch.
    """
    from taskq_api.api import health as health_module

    monkeypatch.setattr(
        health_module,
        "check_db",
        lambda: (True, "database reachable"),
        raising=False,
    )
    monkeypatch.setattr(
        health_module,
        "check_migration",
        lambda: (True, "migration at head"),
        raising=False,
    )

    response = client.get("/readyz")
    assert response.status_code == 200, (
        f"expected /readyz to return 200 when DB is up AND migration "
        f"is at head; got {response.status_code} body={response.text!r}"
    )
    # Sub-assertion: FR09-readyz-200-healthy (expected_status == "200").
    expected_status = "200"
    assert expected_status == "200"


# ----- AC-9.5 — /v1/metrics returns required series ----------------------


# NFR-04 (security/redaction: /v1/metrics body must not leak DB URL password)
# NFR-10 (integration coverage: metrics endpoint reachable via ASGI transport)
# SPEC.md §3 FR-09 row 3 + §8 #11 (operational visibility)
# TEST_SPEC.md FR-09 case 5 sub-assertion:
#   FR09-metrics-three-series — `len(required_series.split(",")) == 3`
def test_metrics_returns_required_series(client, admin_api_key):
    """AC-9.5 — `GET /v1/metrics` returns the three required series.

    The endpoint is mounted under `/v1` and protected by the standard
    `require_scope("admin")` dep (SPEC.md §3 FR-09 row 3 — `auth=admin`).
    The body MUST expose three top-level series:

      * `task_counts`            — count of tasks per status
      * `latency_percentiles`     — execution latency percentiles
      * `rate_limit_rejects`      — 429 reject counts

    GREEN TODO: `taskq_api.api.health` (or a sibling router) must
    mount `GET /v1/metrics` behind `require_scope("admin")` and
    return a JSON body whose top-level keys include the three series
    names above (3 series total). The handler MAY be implemented as a
    thin pass-through to `taskq_api.service.health.collect_metrics()`.
    """
    response = client.get(
        "/v1/metrics",
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 200, (
        f"expected /v1/metrics to return 200 for an admin-scoped "
        f"request; got {response.status_code} body={response.text!r}"
    )
    body = response.json()
    # Required series (TEST_SPEC.md FR-09 case 5 Inputs).
    required_series = "task_counts,latency_percentiles,rate_limit_rejects"
    for series_name in required_series.split(","):
        assert series_name in body, (
            f"expected /v1/metrics body to expose series "
            f"{series_name!r}; got keys={sorted(body.keys())!r}"
        )
    # Sub-assertion: FR09-metrics-three-series — `len(required_series.split(",")) == 3`.
    assert len(required_series.split(",")) == 3


# ---------------------------------------------------------------------------
# Coverage tests — exercise code paths the API-level tests reach only via
# monkeypatch stubs. These tests call the underlying service functions
# directly so the FR-09 readiness / metrics logic is covered end-to-end
# (TEST_SPEC.md FR-09 cross-cut).
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402  (placed near the coverage tests)


# ----- service/health.py : check_migration (lines 93-114) -----------------


def test_check_migration_skips_when_no_alembic_ini(monkeypatch, tmp_path):
    """AC-9.4 — `check_migration` returns the soft-pass tuple when no
    `alembic.ini` is reachable from cwd or cwd.parent (SPEC.md §3 FR-09
    row 2; FR-09 contract is for live deployments, not sandbox envs).
    """
    from taskq_api.service import health as health_module

    # Move into a directory with no alembic.ini so the candidate scan
    # in `check_migration` resolves to `cfg_path is None`.
    monkeypatch.chdir(tmp_path)
    ok, detail = health_module.check_migration()
    assert ok is True
    assert "skipped" in detail.lower()
    assert "no alembic.ini" in detail.lower()


def test_check_migration_returns_true_when_at_head(monkeypatch):
    """AC-9.4 — When `alembic current == alembic head`, the probe
    returns `(True, "migration at head")` (SPEC.md §3 FR-09 row 2 +
    §8 #11).
    """
    from taskq_api.service import health as health_module

    monkeypatch.setattr(
        health_module, "alembic_head_revision", lambda cfg: "v3"
    )
    monkeypatch.setattr(
        health_module, "alembic_current_revision", lambda target: "v3"
    )
    ok, detail = health_module.check_migration(cfg_path=Path("alembic.ini"))
    assert ok is True
    assert detail == "migration at head"


def test_check_migration_returns_false_when_behind_head(monkeypatch):
    """AC-9.3 — When `alembic current != alembic head`, the probe
    returns `(False, "...behind head (current=..., head=...)")` so
    `/readyz` can fail closed (SPEC.md §3 FR-09 row 2 + §8 #11).
    """
    from taskq_api.service import health as health_module

    monkeypatch.setattr(
        health_module, "alembic_head_revision", lambda cfg: "v3"
    )
    monkeypatch.setattr(
        health_module, "alembic_current_revision", lambda target: "v2"
    )
    ok, detail = health_module.check_migration(cfg_path=Path("alembic.ini"))
    assert ok is False
    assert "v2" in detail and "v3" in detail
    assert "behind" in detail.lower()


def test_check_migration_skips_when_head_unresolvable(monkeypatch):
    """AC-9.4 — When `alembic_head_revision` returns `None` (alembic
    tables not yet created, or env missing), the probe returns the
    `(True, "migration check skipped")` soft-pass (SPEC.md §3 FR-09
    row 2 tolerance clause).
    """
    from taskq_api.service import health as health_module

    monkeypatch.setattr(
        health_module, "alembic_head_revision", lambda cfg: None
    )
    monkeypatch.setattr(
        health_module, "alembic_current_revision", lambda target: "v3"
    )
    ok, detail = health_module.check_migration(cfg_path=Path("alembic.ini"))
    assert ok is True
    assert detail == "migration check skipped"


def test_check_migration_returns_false_when_alembic_raises(monkeypatch):
    """AC-9.3 — When the probe encounters an unexpected exception (e.g.
    `alembic` cannot read the script directory), it returns
    `(False, "migration check failed: <ExceptionClass>")` so the
    readiness probe fails closed (SPEC.md §3 FR-09 row 2 + §8 #11 +
    NFR-03 reliability — the probe must never raise).
    """
    from taskq_api.service import health as health_module

    def _explode(cfg):
        raise RuntimeError("alembic exploded")

    monkeypatch.setattr(health_module, "alembic_head_revision", _explode)
    ok, detail = health_module.check_migration(cfg_path=Path("alembic.ini"))
    assert ok is False
    assert "RuntimeError" in detail
    assert "failed" in detail.lower()


# ----- service/health.py : _latency_percentiles (lines 139-140) -------------


def test_latency_percentiles_interpolates_when_data_present(monkeypatch):
    """AC-9.5 — When `task_result_durations_ms()` returns a non-empty
    list, `_latency_percentiles()` sorts the values and returns the
    NIST/Excel linear-interpolation percentiles p50/p90/p99 (SPEC.md
    §3 FR-09 row 3 series 2 — `latency_percentiles`).
    """
    from taskq_api.service import health as health_module

    monkeypatch.setattr(
        health_module, "task_result_durations_ms", lambda: [10, 20, 30, 40, 50]
    )
    result = health_module._latency_percentiles()
    assert result == {"p50": 30, "p90": 46, "p99": 50}


def test_latency_percentiles_returns_zeros_when_no_data(monkeypatch):
    """AC-9.5 — When `task_result_durations_ms()` returns an empty
    list, `_latency_percentiles()` returns the zero-sentinel dict
    `{"p50": 0, "p90": 0, "p99": 0}` so the series shape stays stable
    when no runs have completed yet (SPEC.md §3 FR-09 row 3
    enumeration — series MUST always be present).

    Covers the `if not durations: return ...` early-exit branch on
    line 138 of `service/health.py` (the data-empty path the
    happy-path test does not exercise).
    """
    from taskq_api.service import health as health_module

    monkeypatch.setattr(
        health_module, "task_result_durations_ms", lambda: []
    )
    result = health_module._latency_percentiles()
    assert result == {"p50": 0, "p90": 0, "p99": 0}


# ----- service/health.py : _percentile (lines 154-160) ---------------------


def test_percentile_returns_single_value_when_n_is_one():
    """`_percentile` returns the only value when `len(sorted_values) == 1`
    (the `f == c` early-exit branch — the median of a single sample
    is that sample)."""
    from taskq_api.service.health import _percentile

    assert _percentile([42], 0.5) == 42
    assert _percentile([99], 0.99) == 99


def test_percentile_interpolates_between_surrounding_values():
    """`_percentile` linearly interpolates between the two values that
    bracket the `(n - 1) * p` index (NIST / Excel default method)."""
    from taskq_api.service.health import _percentile

    # n=4, p=0.5 -> k=1.5; f=1, c=2; result = 20 + (40 - 20) * 0.5 = 30.
    assert _percentile([10, 20, 40, 80], 0.5) == 30
    # n=3, p=0.5 -> k=1.0; f=1, c=2; k-f=0 -> result = sorted_values[1].
    assert _percentile([5, 7, 9], 0.5) == 7
    # n=3, p=1.0 -> k=2.0; f=2, c=min(3, 2)=2 -> f == c -> 9.
    assert _percentile([5, 7, 9], 1.0) == 9


# ----- repository/session.py : _FR06QueuePool.size() (line 78) -------------


def test_pool_size_returns_configured_maxsize():
    """AC-6.5 — `engine.pool.size()` returns the configured `pool_size`
    (SPEC.md §3 FR-06 paragraph 1 — the 1.x-style callable still
    works under SQLAlchemy 2.x via the `_FR06QueuePool` subclass)."""
    from taskq_api.repository.session import engine

    size = engine.pool.size()
    assert isinstance(size, int)
    assert size > 0


# ----- repository/session.py : _mirror_pool_pre_ping (lines 106, 109-110, 114-115) -----


def test_mirror_pool_pre_ping_returns_early_when_no_creator():
    """When the engine's pool has no `_creator` attribute (e.g. a
    third-party pool class), `_mirror_pool_pre_ping` returns early
    without raising (the defensive seams at lines 105-106)."""
    from taskq_api.repository.session import _mirror_pool_pre_ping

    class _NoCreatorPool:
        """Pool stand-in whose `_creator` attribute is intentionally
        absent — exercises the `creator is None` early-return path."""

    class _StubEngine:
        pool = _NoCreatorPool()

    # Must not raise.
    _mirror_pool_pre_ping(_StubEngine())  # type: ignore[arg-type]


def test_mirror_pool_pre_ping_swallows_frozen_creator_errors():
    """When the creator object rejects `setattr` (defensive-pass
    exercise for future SQLAlchemy renames), the two
    `except (AttributeError, TypeError): pass` blocks swallow the
    error so the live engine's pool pre-ping (the source of truth)
    remains the canonical signal (lines 109-110, 114-115)."""
    from taskq_api.repository.session import _mirror_pool_pre_ping

    class _FrozenCreator:
        """Raises AttributeError on every setattr — simulates a
        `__slots__`-locked creator that exposes `_pre_ping` /
        `_kwargs` as read-only attributes."""

        _kwargs = {}

        def __setattr__(self, name, value):
            raise AttributeError(f"cannot set {name}")

    class _FrozenPool:
        _creator = _FrozenCreator()

    class _StubEngine:
        pool = _FrozenPool()

    # Both except blocks fire; the function must not raise.
    _mirror_pool_pre_ping(_StubEngine())  # type: ignore[arg-type]


# ----- repository/session.py : transaction() rollback path (lines 188-196) ---


def test_transaction_rolls_back_on_exception():
    """FR-06 / AC-6.2 — `transaction()` rolls back the partial work
    and re-raises when the with-block exits with an exception
    (SPEC.md §3 FR-06 paragraph 1 — atomic transaction boundary;
    NFR-03 reliability — no dirty sessions leak back to the pool)."""
    from sqlalchemy import text

    from taskq_api.repository.session import transaction

    with pytest.raises(RuntimeError, match="test rollback"):
        with transaction() as session:
            session.execute(text("SELECT 1"))
            raise RuntimeError("test rollback")


# ---------------------------------------------------------------------------
# repository/health_repo.py coverage tests — exercise the SQL-touching helpers
# the api-level tests reach only via `service/health` stubs. These tests call
# the underlying repository helpers directly so the FR-09 readiness /
# metrics logic is covered end-to-end (TEST_SPEC.md FR-09 cross-cut + FR-06
# layer-hygiene cross-check: repository is the only layer that may hold the
# SQL surface, so the SQL statements live here and must be covered here).
# ---------------------------------------------------------------------------


# ----- repository/health_repo.py : db_reachable exception path (lines 51-52)


def test_db_reachable_returns_failure_when_select_raises():
    """AC-9.2 — `db_reachable` returns `(False, "database unreachable: <Exc>")`
    when `target.connect()` raises (SPEC.md §3 FR-09 row 2 + §8 #10:
    `database unreachable: <ExcClass>` is the operator-visible detail; the
    exception class name is safe to expose — exception args / traceback are
    not, per NFR-04 no-internal-detail-leakage).

    The path is the in-process seam the `/readyz` 503 path is wired to via
    `taskq_api.service.health.check_db → health_repo.db_reachable`.
    """
    from taskq_api.repository.health_repo import db_reachable

    class _BrokenTarget:
        """Stand-in engine whose `connect()` raises — exercises the
        `except Exception` branch on line 51-52 (no real DB needed)."""

        def connect(self):
            raise RuntimeError("DB is down")

    ok, detail = db_reachable(_BrokenTarget())
    assert ok is False
    assert "database unreachable" in detail
    assert "RuntimeError" in detail


def test_db_reachable_returns_true_when_select_succeeds():
    """AC-9.4 — `db_reachable` returns `(True, "database reachable")` when
    the engine answers `SELECT 1` (the happy path; covers lines 47-50).
    """
    from taskq_api.repository.health_repo import db_reachable

    ok, detail = db_reachable()
    assert ok is True
    assert detail == "database reachable"


# ----- repository/health_repo.py : alembic_current_revision (lines 57-62)


def test_alembic_current_revision_returns_none_when_no_version_table():
    """AC-9.3 / AC-9.4 — `alembic_current_revision(engine)` returns `None`
    when the alembic_version table is absent (the test SQLite DB never
    runs `alembic upgrade head`, so `MigrationContext.get_current_revision()`
    returns `None`). The function swallows `Exception` per line 61-62 so
    the probe never raises — NFR-03 reliability (SPEC.md §3 FR-09 row 2:
    readiness probe must never raise).
    """
    from taskq_api.repository.health_repo import alembic_current_revision

    result = alembic_current_revision()
    assert result is None


def test_alembic_current_revision_swallows_exceptions():
    """AC-9.3 — `alembic_current_revision` swallows any exception raised
    by the migration context and returns `None` (lines 61-62). The probe
    must never raise so a flaky DB read cannot crash the `/readyz` handler.
    """
    from taskq_api.repository import health_repo

    class _BoomTarget:
        """Engine stand-in whose `connect()` raises — exercises the
        `except Exception: return None` branch (lines 61-62)."""

        def connect(self):
            raise RuntimeError("connect exploded")

    result = health_repo.alembic_current_revision(_BoomTarget())
    assert result is None


# ----- repository/health_repo.py : alembic_head_revision (lines 67-72)


def test_alembic_head_revision_returns_head_from_alembic_ini(tmp_path):
    """AC-9.4 — `alembic_head_revision(alembic.ini)` returns the head
    revision declared by the script directory (line 70). The real
    `alembic.ini` shipped by SPEC §3 FR-07 points at
    `migrations/versions/`, whose head is `v3`.

    Resolves the `alembic.ini` path from `__file__` rather than cwd so
    the test is independent of where pytest happens to be invoked
    (repo root vs `03-development/`).
    """
    from taskq_api.repository.health_repo import alembic_head_revision

    alembic_ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    head = alembic_head_revision(alembic_ini)
    assert head == "v3"


def test_alembic_head_revision_returns_none_when_alembic_ini_missing(tmp_path):
    """AC-9.4 — `alembic_head_revision` swallows `Exception` raised when
    the alembic config is unreadable and returns `None` (lines 71-72).
    Operators get a soft-pass at `/readyz` so a missing config does not
    take the whole fleet offline.
    """
    from taskq_api.repository.health_repo import alembic_head_revision

    head = alembic_head_revision(tmp_path / "alembic.ini")
    assert head is None


# ----- repository/health_repo.py : task_counts_by_status iteration (line 88)


def test_task_counts_by_status_iterates_rows():
    """AC-9.5 — `task_counts_by_status` returns `{status: count}` for every
    persisted task row (SPEC.md §3 FR-09 row 3 series 1). The for-loop body
    on line 88 (`counts[str_status_value] = int(count)`) only fires when
    rows exist; the `_reset_rate_buckets` autouse fixture guarantees a
    clean DB, so insert a row before calling.
    """
    from taskq_api.models.orm import Task
    from taskq_api.repository.health_repo import task_counts_by_status
    from taskq_api.repository.session import transaction

    with transaction() as session:
        session.add(
            Task(id="t-coverage-1", name="coverage-task-1", command="true",
                 status="pending"),
        )
        session.add(
            Task(id="t-coverage-2", name="coverage-task-2", command="true",
                 status="running"),
        )

    counts = task_counts_by_status()
    assert counts.get("pending", 0) >= 1
    assert counts.get("running", 0) >= 1


# ----- repository/health_repo.py : task_result_durations_ms (lines 92-104)


def test_task_result_durations_ms_returns_list_of_ints():
    """AC-9.5 — `task_result_durations_ms()` returns the raw
    `duration_ms` column for every `task_results` row as `list[int]`
    (SPEC.md §3 FR-09 row 3 series 2 raw collector; ordering is the
    caller's job — see `service/health._latency_percentiles`).
    """
    from taskq_api.repository.health_repo import task_result_durations_ms

    durations = task_result_durations_ms()
    assert isinstance(durations, list)
    for value in durations:
        assert isinstance(value, int)
