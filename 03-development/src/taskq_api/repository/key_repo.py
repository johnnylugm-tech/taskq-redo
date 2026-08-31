"""API key repository (SPEC.md §3 FR-03).

[FR-01] The plaintext key never leaves the request header; it is
hashed (SHA-256, per SPEC.md §3 FR-03 / NFR-02) and matched against
the store with `hmac.compare_digest` for constant-time comparison.

The store is keyed by SHA-256 hash → scope string. Adding a key with
the same plaintext is idempotent; adding a key whose hash collides
with a different scope would be a hash-collision attack and is not
guarded here (it cannot happen in practice with SHA-256).
Citations:
  SPEC.md §3 FR-03 ("API key authentication … stored as SHA-256 hash")
  NFR-02 ("constant-time compare")
"""
import hashlib
import hmac
from threading import RLock
from typing import Optional


def hash_key(plaintext: str) -> str:
    """SHA-256 hex digest of a UTF-8 encoded API key (FR-03)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class ApiKeyRepo:
    """In-memory store of hashed API keys → scope."""

    def __init__(self) -> None:
        self._lock = RLock()
        # Stored hashes → scope string. Plaintext is never persisted.
        self._hash_to_scope: dict[str, str] = {}

    def add(self, plaintext: str, scope: str) -> None:
        """Insert (or overwrite) a key → scope mapping.

        Uses SHA-256 over the plaintext; the plaintext itself is
        dropped on the floor after hashing.
        """
        h = hash_key(plaintext)
        with self._lock:
            self._hash_to_scope[h] = scope

    def lookup(self, plaintext: str) -> Optional[str]:
        """Return the scope for a plaintext key, or None if unknown.

        Comparison is constant-time via `hmac.compare_digest` (NFR-02).
        The store iterates all known hashes; in practice the size is
        bounded by the number of operators/admins, not the request rate.
        """
        candidate = hash_key(plaintext)
        with self._lock:
            for stored_hash, scope in self._hash_to_scope.items():
                if hmac.compare_digest(stored_hash, candidate):
                    return scope
            return None
