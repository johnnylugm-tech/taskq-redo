"""API key authentication service (SPEC.md §3 FR-03 + FR-04).

[FR-03] Thin service-layer entry point so the api layer depends on
`taskq_api.service.*` (allowed per SAB) rather than reaching directly
into `taskq_api.repository.*` (forbidden per NFR-06 / SAB layers).

[FR-04] The resolved scope string returned by `resolve_scope` feeds
the FR-04 `require_scope` factory at `taskq_api.api.deps:73`, which
enforces the hierarchical `read < write < admin` ordering. The
service layer therefore owns the authn contract; the api layer owns
the authz contract (single authz seam, AC-4.4).

The service deliberately returns `None` for both "no such key" and
"revoked key" — the api layer surfaces both as a single 401 problem+json
so the operator cannot distinguish them at the response layer
(NFR-02 / AC-3.5).

Citations:
  SPEC.md §3 FR-03 (X-API-Key authn) [FR-03]
  SPEC.md §3 FR-04 (scope authorisation) [FR-04]
  NFR-02 ("constant-time compare via hmac.compare_digest") [NFR-02]
"""
from typing import Optional

from taskq_api.repository.key_repo import ApiKeyRepo

__all__ = ["resolve_scope"]


def resolve_scope(key_repo: ApiKeyRepo, plaintext: str) -> Optional[str]:
    """[FR-03][FR-04] Return the scope string for a plaintext API key, or None.

    `None` is returned for unknown, empty, or revoked keys; the caller
    (api layer) translates that into a 401 problem+json. The actual
    constant-time comparison lives in `ApiKeyRepo.lookup`. The returned
    scope string is the input to the FR-04 `require_scope` factory at
    `taskq_api.api.deps:73` — the service layer owns the authn contract
    only; authz (scope ordering) lives at the api seam.

    Citations:
      SPEC.md §3 FR-03 (X-API-Key authn) [FR-03]
      SPEC.md §3 FR-04 (per-token scope) [FR-04]
      NFR-02 (constant-time comparison) [NFR-02]
    """
    return key_repo.lookup(plaintext)
