"""API key authentication service (SPEC.md §3 FR-03).

[FR-03] Thin service-layer entry point so the api layer depends on
`taskq_api.service.*` (allowed per SAB) rather than reaching directly
into `taskq_api.repository.*` (forbidden per NFR-06 / SAB layers).

The service deliberately returns `None` for both "no such key" and
"revoked key" — the api layer surfaces both as a single 401 problem+json
so the operator cannot distinguish them at the response layer
(NFR-02 / AC-3.5).

Citations:
  SPEC.md §3 FR-03 (X-API-Key authn)
  NFR-02 ("constant-time compare via hmac.compare_digest")
"""
from typing import Optional

from taskq_api.repository.key_repo import ApiKeyRepo

__all__ = ["resolve_scope"]


def resolve_scope(key_repo: ApiKeyRepo, plaintext: str) -> Optional[str]:
    """Return the scope string for a plaintext API key, or None.

    `None` is returned for unknown, empty, or revoked keys; the caller
    (api layer) translates that into a 401 problem+json. The actual
    constant-time comparison lives in `ApiKeyRepo.lookup`.
    """
    return key_repo.lookup(plaintext)
