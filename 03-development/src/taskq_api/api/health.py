"""Health-check router (SPEC.md §3 FR-03 + FR-09).

[FR-03] Liveness (`/healthz`) and readiness (`/readyz`) probes are
deliberately mounted OUTSIDE `/v1` and OUTSIDE the `require_api_key`
auth dependency so that orchestrators (Kubernetes, load balancers,
uptime probes) can gate traffic without first minting an API key
(AC-3.7). Both endpoints return a small JSON body with `status: ok`
and HTTP 200 — sufficient for liveness/readiness checks per
SPEC.md §3 FR-09.

The router does NOT depend on the api-layer auth dependency, on the
service layer, or on the repository layer: a health probe must
succeed even when downstream dependencies are degraded (a separate
concern, surfaced through `/readyz` readiness transitions in later
work).
Citations:
  SPEC.md §3 FR-03 (no-auth clause) [FR-03]
  SPEC.md §3 FR-09 (/healthz, /readyz)
"""
from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    """Liveness probe — process is up (AC-3.7 / FR-09)."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    """Readiness probe — ready to serve traffic (AC-3.7 / FR-09)."""
    return {"status": "ok"}
