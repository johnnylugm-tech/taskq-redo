"""HTTP-layer dependency providers (SPEC.md §3 FR-03 + FR-04).

[FR-03] Authn dependency `require_api_key` validates the X-API-Key
header (FR-03) and returns the resolved scope string; missing/invalid
keys surface as 401 problem+json (AC-3.1).

[FR-04] Authz factory `require_scope(level)` returns a dependency that
enforces the hierarchical scope ordering `read < write < admin`
(SPEC.md §3 FR-04, AC-4.5). Insufficient scope raises `ForbiddenError`
→ 403 problem+json with no resource-existence leak (NFR-02, AC-4.1,
AC-4.2). Every `/v1/*` route carries this dep (AC-4.4 — single
authz seam).

[FR-03] `require_api_key` declares the X-API-Key header as *optional*
(`Header(None, ...)`) so that a missing/empty header does NOT trip
FastAPI's automatic 422 validation. We surface the missing-header
case as 401 problem+json ourselves, which is the FR-10-mandated
response shape for unauthorised requests (AC-3.1).

These dependencies are the *single* place where authn/authz is
performed; SPEC.md §3 FR-04 forbids scattering scope checks across
handlers.
Citations:
  SPEC.md §3 FR-03 (X-API-Key authn) [FR-03]
  SPEC.md §3 FR-04 (per-token scope, hierarchical) [FR-04]
  NFR-02 (no detail leakage in 403) [NFR-02]
"""
from typing import Callable, Optional

from fastapi import Depends, Header, Request

from taskq_api.errors import ForbiddenError, UnauthorizedError
from taskq_api.repository.key_repo import ApiKeyRepo
from taskq_api.service.auth import resolve_scope
from taskq_api.service.tasks import TaskService


# Hierarchical scope ordering. Defined here (api layer) so the api
# package owns the authorization contract without crossing layers.
# [FR-04] AC-4.5 — `read` < `write` < `admin`; an admin key MUST
# satisfy any endpoint whose required scope is write (or read).
SCOPE_RANK: dict[str, int] = {"read": 0, "write": 1, "admin": 2}


def get_key_repo(request: Request) -> ApiKeyRepo:
    """Resolve the ApiKeyRepo from app state (DI seam for tests)."""
    return request.app.state.api_key_repo


def get_task_service(request: Request) -> TaskService:
    """Resolve the TaskService from app state (DI seam for tests)."""
    return request.app.state.task_service


def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    repo: ApiKeyRepo = Depends(get_key_repo),
) -> str:
    """Return the scope string for the X-API-Key header (FR-03 / AC-3.1).

    The header is declared as optional so that FastAPI's automatic
    422 validation does not fire for missing values. A missing/empty
    header (or an unknown key) is converted to a 401 problem+json
    response via `UnauthorizedError`, satisfying both the missing
    header case (AC-3.1) and the unknown-key case (AC-3.2) without
    leaking which one occurred.
    """
    if not x_api_key:
        raise UnauthorizedError(detail="missing X-API-Key header")
    scope = resolve_scope(repo, x_api_key)
    if scope is None:
        raise UnauthorizedError(detail="invalid api key")
    return scope


def require_scope(required: str) -> Callable[..., str]:
    """[FR-04] Build a dependency that enforces the given scope.

    AC-4.5 — hierarchical inclusion (`read` < `write` < `admin`):
    an admin key satisfies a write-required endpoint; the returned
    scope string equals the resolved key's scope (so admins can be
    distinguished when downstream logic requires it).

    The returned closure captures `required` so each route gets its
    own independent dep instance (the factory pattern enforced by
    AC-4.4 — every /v1 route shares exactly one factory reference).
    Insufficient scope → `ForbiddenError` → 403 problem+json (NFR-02:
    detail MUST NOT echo the requested resource id).

    Citations:
      SPEC.md §3 FR-04 paragraph 1 (hierarchical scope ordering) [FR-04]
      SPEC.md §8 #6 (no resource-existence leak in 403) [NFR-02]
      SPEC.md §3 FR-10 (problem+json envelope) [FR-10]
    """

    def _checker(scope: str = Depends(require_api_key)) -> str:
        if SCOPE_RANK.get(scope, -1) < SCOPE_RANK.get(required, 99):
            # NFR-02: detail must not leak whether the resource exists.
            raise ForbiddenError(detail="insufficient scope")
        return scope

    return _checker
