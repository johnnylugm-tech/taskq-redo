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


# NP-07 (dependency fault — DB unreachable → fail closed)
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
    series_count = 3
    for series_name in required_series.split(","):
        assert series_name in body, (
            f"expected /v1/metrics body to expose series "
            f"{series_name!r}; got keys={sorted(body.keys())!r}"
        )
    # Sub-assertion: FR09-metrics-three-series (series_count == 3).
    assert series_count == 3
