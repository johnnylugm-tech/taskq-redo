"""API key authentication service (SPEC.md §3 FR-03 + FR-04).

[FR-03 / FR-04] The single authn entry point used by the api layer.
`resolve_scope` owns every authn-side rule — non-empty plaintext,
known-and-active key — so the api layer never duplicates them and the
repository stays a pure data store (NFR-06 layering).

The function deliberately collapses every failure mode into a single
`None` return:
  * missing or empty plaintext header,
  * unknown key (hash not present in the active store),
  * revoked key (hash present in the revocation set).

The api layer maps `None` to one 401 problem+json so the operator
cannot distinguish failure modes at the response layer (NFR-02 /
AC-3.5). The service layer is allowed to know the difference; the api
layer is not.

The constant-time comparison and revocation check live in
`ApiKeyRepo.lookup`; this module adds the entry-point validation the
repository intentionally does not own (hashes never enter or leave
the repository, but the "is this even a key?" check belongs here, not
in HTTP glue).

Citations:
  SPEC.md §3 FR-03 (X-API-Key authn)
  SPEC.md §3 FR-04 (per-token scope)
  NFR-02 (constant-time compare via hmac.compare_digest)
"""
from typing import Optional

from taskq_api.repository.key_repo import ApiKeyRepo

# pragma: no error-handling

__all__ = ["resolve_scope"]


def resolve_scope(key_repo: ApiKeyRepo, plaintext: str) -> Optional[str]:
    """Return the scope string for `plaintext`, or None if it is not a valid key.

    A None return covers every rejection case — missing header, empty
    header, unknown key, revoked key. The api layer (`require_api_key`)
    maps None to a single 401 problem+json so the operator cannot
    distinguish "you forgot the header" from "the key was revoked"
    (NFR-02).

    The returned scope string is the input to the FR-04 `require_scope`
    factory in `taskq_api.api.deps`, which applies the hierarchical
    `read < write < admin` ordering at the api seam. The service layer
    owns authn only; authz lives at the api seam (AC-4.4).
    """
    if not plaintext:
        # Treat "no key presented" identically to "key presented but
        # invalid" so the api layer can collapse both into one 401
        # envelope. The repo's `lookup` does not see empty input —
        # SHA-256("") would be a valid hash to compare against, so
        # guarding here keeps the contract honest.
        return None
    return key_repo.lookup(plaintext)
