"""API key repository (SPEC.md §3 FR-03).

[FR-03] The plaintext key never leaves the request header; it is
hashed (SHA-256, per SPEC.md §3 FR-03 / NFR-02) and matched against
the store with `hmac.compare_digest` for constant-time comparison.

[FR-03] Adds revocation support (AC-3.5). A key whose `revoked_at`
timestamp is set MUST be treated as invalid by `lookup`. The
in-memory record is split into two structures:
  * `_hash_to_scope` — active SHA-256 hex → scope mapping.
  * `_revoked_hashes` — set of SHA-256 hexes that have been revoked.
After revocation, the hash moves from active → revoked; `lookup`
returns `None` for revoked hashes (the operator-facing intent is
"this key is now dead, behave as if it never existed").

The store is keyed by SHA-256 hash → scope string. Adding a key with
the same plaintext is idempotent; adding a key whose hash collides
with a different scope would be a hash-collision attack and is not
guarded here (it cannot happen in practice with SHA-256).
Citations:
  SPEC.md §3 FR-03 ("API key authentication … stored as SHA-256 hash")
  NFR-02 ("constant-time compare")
  SPEC.md §3 FR-03 "停用金鑰" (revocation clause) [FR-03]
"""
import hashlib
import hmac
from threading import RLock
from typing import Optional

__all__ = ["ApiKeyRepo", "hash_key"]


def hash_key(plaintext: str) -> str:
    """Return the 64-hex-char SHA-256 digest of `plaintext` (FR-03).

    Encoding is UTF-8 so the function round-trips any non-ASCII
    characters an operator might paste into a header by mistake; the
    resulting digest is what the repo persists, never the plaintext.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class ApiKeyRepo:
    """In-memory store of hashed API keys → scope (with revocation)."""

    def __init__(self) -> None:
        self._lock = RLock()
        # Stored hashes → scope string. Plaintext is never persisted.
        self._hash_to_scope: dict[str, str] = {}
        # SHA-256 hexes that have been revoked (AC-3.5 / FR-03 "停用金鑰").
        # A revoked key MUST resolve as unknown to `lookup` from the
        # instant the `revoke` call returns, so this is a fast path
        # separate from `_hash_to_scope` rather than a tombstone on
        # the active mapping.
        self._revoked_hashes: set[str] = set()

    def add(self, plaintext: str, scope: str) -> None:
        """Insert (or overwrite) a key → scope mapping.

        The plaintext is hashed and then dropped; the repo never
        retains the original value (NFR-02 / AC-3.3).
        """
        key_hash = hash_key(plaintext)
        with self._lock:
            self._hash_to_scope[key_hash] = scope
            # Re-adding a previously-revoked key resurrects it — that
            # is the only legitimate way to "undo" a revoke in this
            # in-memory model and matches the operator workflow of
            # "I revoked the wrong key, let me re-issue it".
            self._revoked_hashes.discard(key_hash)

    def revoke(self, plaintext: str) -> None:
        """Revoke the key derived from `plaintext` (AC-3.5).

        Idempotent: revoking an already-revoked or unknown key is a
        no-op. The candidate hash is added to `_revoked_hashes` so
        subsequent `lookup` calls treat it as unknown, regardless of
        whether it was ever active.
        """
        candidate_hash = hash_key(plaintext)
        with self._lock:
            # If the key was active, drop it from the active map; the
            # hash still lives in `_revoked_hashes` so subsequent
            # `lookup` calls treat it as unknown.
            self._hash_to_scope.pop(candidate_hash, None)
            self._revoked_hashes.add(candidate_hash)

    def lookup(self, plaintext: str) -> Optional[str]:
        """Return the scope for a plaintext key, or None if unknown.

        Comparison is constant-time via `hmac.compare_digest` (NFR-02).
        The store iterates all known hashes; in practice the size is
        bounded by the number of operators/admins, not the request rate.

        A key whose hash is present in `_revoked_hashes` is treated
        as unknown (returns `None`) — AC-3.5 / FR-03 "停用金鑰".
        """
        candidate_hash = hash_key(plaintext)
        with self._lock:
            # Fast path: a revoked key is always invalid, regardless
            # of what the active map says.
            if candidate_hash in self._revoked_hashes:
                return None
            for stored_hash, scope in self._hash_to_scope.items():
                if hmac.compare_digest(stored_hash, candidate_hash):
                    return scope
            return None
