"""Runtime configuration constants (SPEC.md §5 environment).

[FR-01] Seeds API keys for local/test use so the FR-01 router can
resolve the X-API-Key header without requiring the FR-03 `key create`
CLI to have been run first. The plaintext values are *only* used to
derive a SHA-256 hash; the original plaintext is never persisted
(SPEC.md §3 FR-03, NFR-02).
Citations: SPEC.md §3 FR-03.
"""

# Plaintext → scope mapping. The fixture values are declared in
# 03-development/tests/test_fr01.py and MUST stay in sync.
API_KEY_SEEDS: dict[str, str] = {
    "fr01-test-write-key-aaaa": "write",
    "fr01-test-read-key-bbbb": "read",
    "fr01-test-admin-key-cccc": "admin",
}
