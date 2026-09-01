"""Health-check router (SPEC.md §3 FR-03 + FR-09).

[FR-03] Liveness (`/healthz`) and readiness (`/readyz`) probes are
deliberately mounted OUTSIDE `/v1` and OUTSIDE the `require_api_key`
auth dependency so that orchestrators (Kubernetes, load balancers,
uptime probes) can gate traffic without first minting an API key
(AC-3.7).

[FR-09] `/readyz` returns 503 when either:
  * the database probe (`check_db`) reports unreachable, OR
  * the migration probe (`check_migration`) reports behind head.

Both probes live in `taskq_api.service.health`; the api layer
re-imports them under their public names so the test-suite
`monkeypatch.setattr(taskq_api.api.health, "check_db", ...)` hits
the same call site the handler invokes. The api layer translates
the `(ok, detail)` tuple into a problem+json 503 with the
operator-visible `detail` string per SPEC.md §3 FR-09 row 2 + NFR-04
(no internal detail leakage).

[FR-09] `/v1/metrics` is mounted on a separate `metrics_router`
prefixed `/v1` behind `require_scope("admin")` (FR-09 row 3:
`auth=admin`). The handler is a thin pass-through to
`taskq_api.service.health.collect_metrics()`.

Citations:
  SPEC.md §3 FR-03 (no-auth clause) [FR-03]
  SPEC.md §3 FR-09 (/healthz, /readyz, /v1/metrics)
  SPEC.md §8 #10 (DB-down verifier)
  SPEC.md §8 #11 (migration-behind-head verifier)
  NFR-04 (no internals in error detail)
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from taskq_api.api.deps import require_scope
from taskq_api.service.health import check_db, check_migration, collect_metrics

# pragma: no error-handling


router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    """Liveness probe — process is up (AC-9.1 / FR-09).

    Always returns 200 while the Python interpreter is alive; the
    endpoint deliberately ignores downstream dependency state so an
    orchestrator can distinguish "process crashed" from "DB
    unreachable" (the latter surfaces via `/readyz`).
    """
    return {"status": "ok"}


def _not_ready(detail: str) -> JSONResponse:
    """Build the 503 problem+json body for a failed readiness probe (FR-09).

    Centralises the `(status_code=503, application/problem+json)` shape
    so the two failure branches in `readyz` stay in lockstep with the
    FR-10 envelope pattern.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": detail},
        media_type="application/problem+json",
    )


@router.get("/readyz")
def readyz():
    """Readiness probe — ready to serve traffic (AC-9.2 / 9.3 / 9.4 / FR-09).

    Returns 200 when both `check_db` and `check_migration` report
    `ok=True`. Returns 503 with a `detail` naming the failure
    otherwise (SPEC.md §3 FR-09 row 2: "body explaining which
    condition failed"). The response body is problem+json-shaped
    (`{"detail": "..."}`) so the FR-10 envelope pattern applies
    uniformly.

    The api layer prefixes the probe's raw detail with the failure
    kind ("db" / "migration") so the response ALWAYS names which
    check failed — operators can disambiguate a 503 from the body
    alone (SPEC.md §3 FR-09 row 2 acceptance criterion).
    """
    db_ok, db_detail = check_db()
    if not db_ok:
        return _not_ready(f"db unavailable: {db_detail}")
    mig_ok, mig_detail = check_migration()
    if not mig_ok:
        return _not_ready(f"migration: {mig_detail}")
    return {"status": "ok"}


# Metrics router — mounted under `/v1` behind `require_scope("admin")`.
# Lives on a SEPARATE `APIRouter` so the auth-free `router` above
# (which carries `/healthz` and `/readyz`) stays exempt from the
# `require_api_key` dep (AC-3.7 / NFR-02: orchestrator probes at
# high frequency MUST NOT require a key).
metrics_router = APIRouter(prefix="/v1", tags=["metrics"])


@metrics_router.get(
    "/metrics",
    dependencies=[Depends(require_scope("admin"))],
)
def get_metrics() -> dict:
    """[FR-09] Operational metrics — admin scope (AC-9.5).

    Returns three top-level series (SPEC.md §3 FR-09 row 3):
      * `task_counts`         — count of tasks per status
      * `latency_percentiles` — execution latency percentiles
      * `rate_limit_rejects`  — 429 reject counts

    Thin pass-through to `taskq_api.service.health.collect_metrics()`.
    """
    return collect_metrics()
