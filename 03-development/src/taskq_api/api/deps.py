"""HTTP-layer dependency providers (SPEC.md §3 FR-03 + FR-04).

[FR-01] Provides two dependency functions:
  * `require_api_key`   — validates the X-API-Key header (FR-03), returns
    the resolved scope string. Missing/invalid → 401 problem+json.
  * `require_scope(level)` — factory that produces a dependency which
    enforces the hierarchical scope `read < write < admin` (FR-04).
    Insufficient → 403 problem+json (with no resource-existence leak).

These dependencies are the *single* place where authn/authz is
performed; SPEC.md §3 FR-04 forbids scattering scope checks across
handlers.
Citations:
  SPEC.md §3 FR-03 (X-API-Key authn)
  SPEC.md §3 FR-04 (per-token scope, hierarchical)
  NFR-02 (no detail leakage in 403)
"""
from typing import Callable

from fastapi import Depends, Header, Request

from taskq_api.errors import ForbiddenError, UnauthorizedError
from taskq_api.repository.key_repo import ApiKeyRepo
from taskq_api.service.auth import resolve_scope


# Hierarchical scope ordering. Defined here (api layer) so the api
# package owns the authorization contract without crossing layers.
SCOPE_RANK: dict[str, int] = {"read": 0, "write": 1, "admin": 2}


def get_key_repo(request: Request) -> ApiKeyRepo:
    """Resolve the ApiKeyRepo from app state (DI seam for tests)."""
    return request.app.state.api_key_repo


def require_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    repo: ApiKeyRepo = Depends(get_key_repo),
) -> str:
    """Return the scope string for the X-API-Key header (FR-03).

    Raises 401 problem+json when the header is missing or invalid.
    """
    if not x_api_key:
        raise UnauthorizedError(detail="missing X-API-Key header")
    scope = resolve_scope(repo, x_api_key)
    if scope is None:
        raise UnauthorizedError(detail="invalid api key")
    return scope


def require_scope(required: str) -> Callable[..., str]:
    """Build a dependency that enforces the given scope (FR-04)."""

    def _checker(scope: str = Depends(require_api_key)) -> str:
        if SCOPE_RANK.get(scope, -1) < SCOPE_RANK.get(required, 99):
            # NFR-02: detail must not leak whether the resource exists.
            raise ForbiddenError(detail="insufficient scope")
        return scope

    return _checker
