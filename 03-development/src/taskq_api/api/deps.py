"""HTTP-layer dependency providers (SPEC.md §3 FR-03 + FR-04 + FR-05).

[FR-03] `require_api_key` delegates authn to
`taskq_api.service.auth.resolve_scope` and translates the result into
either a scope string or a `UnauthorizedError` → 401 problem+json
(AC-3.1, AC-3.5). All authn rules are owned by the service layer; the
api layer only translates to HTTP.

[FR-04] `require_scope(level)` is the *single* authz seam (AC-4.4):
every `/v1/*` route carries `require_scope(...)` so the factory
identity is the one and only dependency used for authz decisions
across the api layer. Hierarchical inclusion (`read < write < admin`,
AC-4.5) means an admin key satisfies any endpoint requiring write or
read. Insufficient scope raises `ForbiddenError` → 403 problem+json
with no resource-existence leak (NFR-02).

[FR-05] `rate_limit` is the bucket-check dep wired onto every
`/v1/*` route via the tasks router's `dependencies=[Depends(rate_limit)]`.
It depends on `require_api_key` so authn runs FIRST — an
unauthenticated request 401s before being counted against any bucket
(NFR-02: no throttle on invalid keys). On rejection it raises
`TooManyRequestsError` (HTTP 429, FR-10 envelope, `Retry-After`
header). `/healthz` and `/readyz` are mounted on the separate health
router and never see this dep (AC-5.5).

`require_api_key` declares the X-API-Key header as *optional*
(`Header(None, ...)`) so a missing/empty header does NOT trip
FastAPI's automatic 422 validation. We surface the missing-header
case as 401 problem+json ourselves — that is the FR-10-mandated
response shape for unauthorised requests (AC-3.1).

Citations:
  SPEC.md §3 FR-03 (X-API-Key authn)
  SPEC.md §3 FR-04 (per-token scope, hierarchical)
  SPEC.md §3 FR-05 (per-scope token bucket, 429 + Retry-After)
  SPEC.md §3 FR-09 (health endpoints exempt)
  NFR-02 (no detail leakage in 401/403)
"""
from typing import Callable, Optional

from fastapi import Depends, Header, Request

from taskq_api.errors import (
    ForbiddenError,
    TooManyRequestsError,
    UnauthorizedError,
)
from taskq_api.repository.key_repo import ApiKeyRepo
from taskq_api.repository.rate_repo import RateRepo
from taskq_api.service.auth import resolve_scope
from taskq_api.service.ratelimit import TokenBucket
from taskq_api.service.tasks import TaskService


# Hierarchical scope ordering (SPEC.md §3 FR-04, AC-4.5).
# `read` < `write` < `admin`: an admin key satisfies any endpoint
# whose required scope is write (or read). Defined at the api layer
# because authz ordering is an HTTP-route concern, not an authn
# service concern.
SCOPE_RANK: dict[str, int] = {"read": 0, "write": 1, "admin": 2}


def get_key_repo(request: Request) -> ApiKeyRepo:
    """Resolve the ApiKeyRepo from app state (DI seam for tests)."""
    return request.app.state.api_key_repo


def get_task_service(request: Request) -> TaskService:
    """Resolve the TaskService from app state (DI seam for tests)."""
    return request.app.state.task_service


def get_rate_repo(request: Request) -> RateRepo:
    """[FR-05] Resolve the RateRepo from app state (DI seam for tests)."""
    return request.app.state.rate_repo


def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    repo: ApiKeyRepo = Depends(get_key_repo),
) -> str:
    """Return the scope string for the X-API-Key header (FR-03 / AC-3.1).

    The header is declared as optional so FastAPI's automatic 422
    validation does not fire for missing values. A missing/empty
    header, an unknown key, and a revoked key all collapse into the
    same `UnauthorizedError` → 401 problem+json — the service layer's
    `resolve_scope` already returns `None` for every rejection case,
    so we just translate None to 401 here (NFR-02: no distinction
    leaks).
    """
    # Collapse `None` (missing header) into the same empty-string path
    # the service-layer `resolve_scope` already guards — both cases
    # surface as a single 401 below (NFR-02: no distinction leaks).
    scope = resolve_scope(repo, x_api_key or "")
    if scope is None:
        raise UnauthorizedError(detail="missing or invalid api key")
    return scope


def require_scope(required: str) -> Callable[..., str]:
    """[FR-04] Build a dependency that enforces the given scope.

    AC-4.5 — hierarchical inclusion (`read` < `write` < `admin`):
    an admin key satisfies a write-required endpoint; the returned
    scope string equals the resolved key's scope so admins can be
    distinguished when downstream logic requires it.

    The returned closure captures `required` so each route gets its
    own independent dep instance (the factory pattern enforced by
    AC-4.4 — every /v1 route shares exactly one factory reference).
    Insufficient scope → `ForbiddenError` → 403 problem+json (NFR-02:
    detail MUST NOT echo the requested resource id).

    Citations:
      SPEC.md §3 FR-04 paragraph 1 (hierarchical scope ordering)
      SPEC.md §8 #6 (no resource-existence leak in 403)
      SPEC.md §3 FR-10 (problem+json envelope)
    """

    def check_scope(scope: str = Depends(require_api_key)) -> str:
        if SCOPE_RANK[scope] < SCOPE_RANK[required]:
            # NFR-02: detail must not leak whether the resource exists.
            raise ForbiddenError(detail="insufficient scope")
        return scope

    return check_scope


# [FR-05] Token-bucket enforcement dep. Wired onto the tasks_router via
# `dependencies=[Depends(rate_limit)]` so EVERY `/v1/*` route runs the
# bucket check before reaching the handler (AC-5.1, AC-5.2). The health
# router is mounted separately and does not carry this dep (AC-5.5).
def rate_limit(
    request: Request,
    scope: str = Depends(require_api_key),
) -> None:
    """[FR-05] Enforce per-scope token-bucket rate limiting (AC-5.1, AC-5.2).

    Runs AFTER `require_api_key` so an invalid key 401s before being
    counted against any bucket (NFR-02: no throttle on invalid keys;
    a rate-limiter that consumes tokens for unauthenticated requests
    would let an attacker deplete legitimate users' buckets). On
    rejection, raises `TooManyRequestsError` which the FR-10 handler
    serialises as 429 problem+json + `Retry-After` header
    (SPEC.md §3 FR-05 paragraph 1).

    `/healthz` and `/readyz` are NOT routed through this dep — the
    health router is mounted outside `/v1` and carries no
    `rate_limit` dependency (AC-5.5: orchestrator probes at high
    frequency must NOT be throttled, otherwise k8s would mark the pod
    unhealthy per FR-09).

    Citations:
      SPEC.md §3 FR-05 paragraph 1 (over limit → 429 + Retry-After)
      SPEC.md §3 FR-09 (/healthz, /readyz exempt)
      SPEC.md §3 FR-10 (problem+json envelope)
    """
    repo = get_rate_repo(request)
    bucket = TokenBucket(scope=scope, repo=repo)
    decision = bucket.try_consume(1.0)
    if not decision.allowed:
        raise TooManyRequestsError(
            detail="rate limit exceeded",
            retry_after_seconds=decision.retry_after_seconds,
        )
