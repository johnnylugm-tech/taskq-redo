"""API key authentication service (SPEC.md §3 FR-03).

[FR-01] Thin wrapper around `ApiKeyRepo.lookup` so the api layer
depends on `taskq_api.service.*` (allowed per SAB) rather than
`taskq_api.repository.*` (forbidden per NFR-06).
Citations:
  SPEC.md §3 FR-03
  NFR-02 ("constant-time compare via hmac.compare_digest")
"""
from typing import Optional

from taskq_api.repository.key_repo import ApiKeyRepo


def resolve_scope(key_repo: ApiKeyRepo, plaintext: str) -> Optional[str]:
    """Return the scope string for a plaintext API key, or None."""
    return key_repo.lookup(plaintext)
