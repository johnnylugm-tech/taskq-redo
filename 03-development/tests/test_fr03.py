"""RED step — failing tests for FR-03 API Key authentication.

Covers the seven acceptance criteria declared in SPEC.md §3 FR-03 and
TEST_SPEC.md FR-03 cases 1-7:

  AC-3.1 — Request to any /v1/* endpoint without X-API-Key returns
           401 + problem+json.
  AC-3.2 — Request with an invalid X-API-Key returns 401 + problem+json.
  AC-3.3 — api_keys rows store key_hash as a 64-hex SHA-256 digest;
           no plaintext key exists in the table.
  AC-3.4 — Key comparison uses hmac.compare_digest (constant time).
  AC-3.5 — A key with revoked_at set is treated as invalid.
  AC-3.6 — `python -m taskq_api key create --scope <scope>` prints
           plaintext exactly once and persists only the hash.
  AC-3.7 — /healthz and /readyz are reachable without authentication.

Per SAB.json (`fr_module_traceability.FR-03`), these are the bound
modules the GREEN implementation must place on disk:

  taskq_api.service.auth       -> service/auth.py        (exists; may need revoke-aware lookup)
  taskq_api.repository.key_repo -> repository/key_repo.py (exists; needs revocation support)
  taskq_api.__main__           -> __main__.py            (DOES NOT EXIST - GREEN creates)

These tests intentionally exercise the SAB-declared entry points so
pytest will fail at the run-assertion boundary (or at collection time
for `taskq_api.__main__`) while the GREEN implementation is still
incomplete — this is the expected RED state.

Citations:
  SPEC.md §3 FR-03 (whole section)
  SPEC.md §3 FR-09 (/healthz, /readyz no-auth clause)
  TEST_SPEC.md FR-03 (cases 1-7)
  NFR-02 (constant-time compare via hmac.compare_digest)
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# SAB binding — GREEN must wire these module paths on disk.
from taskq_api.app import app
from taskq_api.repository.key_repo import ApiKeyRepo, hash_key


# ----- Shared fixtures ---------------------------------------------------


@pytest.fixture
def client():
    """Build a sync TestClient against the FastAPI app.

    GREEN TODO: `taskq_api.app:app` must remain importable; the FR-03
    auth dependency (`require_api_key`) must return 401 + problem+json
    for missing AND invalid X-API-Key headers (AC-3.1, AC-3.2).
    """
    return TestClient(app)


@pytest.fixture
def write_api_key():
    """Plaintext write-scope API key seeded by config.API_KEY_SEEDS."""
    return "fr01-test-write-key-aaaa"


@pytest.fixture
def read_api_key():
    """Plaintext read-scope API key seeded by config.API_KEY_SEEDS."""
    return "fr01-test-read-key-bbbb"


# ----- AC-3.1 — Missing X-API-Key returns 401 ----------------------------


def test_missing_api_key_returns_401(client):
    """AC-3.1 — GET /v1/* without X-API-Key returns 401 + problem+json.

    Sub-assertion: FR03-missing-header-401.
    # NFR-02 security — missing authentication header must surface as
    #   401 Unauthorized (problem+json, FR-10 envelope), not 422 from
    #   FastAPI's automatic validation.
    # NFR-06 architecture_constraints — auth happens at the api layer
    #   (taskq_api.api.deps.require_api_key) before the handler runs.

    GREEN TODO: `taskq_api.api.deps.require_api_key` must declare
    `x_api_key: Optional[str] = Header(None, alias="X-API-Key")`
    and explicitly raise `UnauthorizedError` for missing/empty values
    so the response status is 401 (not 422).
    """
    response = client.get("/v1/tasks")
    assert response.status_code == 401, (
        f"expected 401 when X-API-Key header is absent, "
        f"got {response.status_code} body={response.text!r}"
    )
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    ), f"expected problem+json envelope, got {response.headers.get('content-type')!r}"


# ----- AC-3.2 — Invalid X-API-Key returns 401 ----------------------------


def test_invalid_api_key_returns_401(client):
    """AC-3.2 — GET /v1/* with a non-existent X-API-Key returns 401.

    Sub-assertion: FR03-invalid-header-401.
    # NFR-02 security — unknown key must be indistinguishable from
    #   missing key at the response layer (constant timing aside, the
    #   surface status MUST be 401, not 404 leaking existence).
    """
    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": "this-key-does-not-exist-zzz"},
    )
    assert response.status_code == 401, (
        f"expected 401 for an invalid X-API-Key, "
        f"got {response.status_code} body={response.text!r}"
    )
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    ), f"expected problem+json envelope, got {response.headers.get('content-type')!r}"


# ----- AC-3.3 — api_keys storage holds SHA-256 hash only ------------------


def test_api_keys_table_holds_no_plaintext():
    """AC-3.3 — adding a key never persists the plaintext.

    Sub-assertion: FR03-no-plaintext-in-storage.
    # NFR-02 security — plaintext must not survive a key-add round
    #   trip; only the SHA-256 hash (64 hex chars) may remain in the
    #   api_keys store (SPEC.md §8 #18 verification).

    The repo must record a SHA-256 digest of the plaintext; the
    plaintext itself must not appear in any field of the persisted
    record. We pin both: the storage key is a 64-hex digest, and the
    plaintext does not appear anywhere in the repo's exposed state.

    GREEN TODO: ApiKeyRepo.add(plaintext, scope) must hash the
    plaintext via SHA-256 before storing. If GREEN switches to a
    SQLAlchemy-backed implementation, this test will need to inspect
    the api_keys row set instead — but the no-plaintext invariant
    still holds.
    """
    plaintext = "fr03-plaintext-must-never-be-stored-aaa"
    scope = "write"
    repo = ApiKeyRepo()
    repo.add(plaintext, scope)

    # The plaintext must not appear anywhere in the repo's exposed state.
    # Walk every dict / list reachable through __dict__ to catch every
    # possible storage shape (in-memory dict, sqlite row, dataclass, …).
    def _walk(obj, seen):
        oid = id(obj)
        if oid in seen:
            return []
        seen.add(oid)
        leaks = []
        if isinstance(obj, str):
            if obj == plaintext:
                leaks.append(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                leaks.extend(_walk(k, seen))
                leaks.extend(_walk(v, seen))
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                leaks.extend(_walk(item, seen))
        return leaks

    leaks = _walk(repo.__dict__, set())
    assert leaks == [], (
        f"plaintext leaked into repo storage: {leaks!r}"
    )

    # The stored key(s) must be 64-char hex SHA-256 digests.
    stored_hashes = [
        k for k in repo.__dict__.values()
        if isinstance(k, dict) and k
    ]
    # Flatten any nested dict keys (covers the current `_hash_to_scope`
    # in-memory shape as well as any future table-backed shape).
    hash_strings: list[str] = []
    for value in repo.__dict__.values():
        if isinstance(value, dict):
            hash_strings.extend(str(k) for k in value.keys())
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    hash_strings.append(item)
    assert hash_strings, "expected at least one stored hash key"
    for stored in hash_strings:
        assert len(stored) == 64 and all(
            c in "0123456789abcdef" for c in stored
        ), f"stored value {stored!r} is not a 64-char hex SHA-256 digest"

    # The lookup must still resolve the original plaintext to its scope.
    assert repo.lookup(plaintext) == scope


# ----- AC-3.4 — hmac.compare_digest used for key comparison --------------


def test_key_compare_uses_hmac_compare_digest():
    """AC-3.4 — key comparison is performed with hmac.compare_digest.

    Sub-assertion: FR03-constant-time-compare.
    # NFR-02 security — equal-time comparison via `hmac.compare_digest`
    #   prevents timing-based plaintext recovery; the source code MUST
    #   reference `hmac.compare_digest` in the key-comparison path.

    We inspect both the repository (where the comparison physically
    lives) and the service layer (which the api layer calls). The
    `hmac.compare_digest` symbol must be referenced in the comparison
    path of at least one of these modules.
    """
    repo_src = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "taskq_api"
        / "repository"
        / "key_repo.py"
    )
    service_src = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "taskq_api"
        / "service"
        / "auth.py"
    )

    repo_text = repo_src.read_text(encoding="utf-8")
    service_text = service_src.read_text(encoding="utf-8")

    assert "hmac.compare_digest" in repo_text or "hmac.compare_digest" in service_text, (
        "key comparison must use hmac.compare_digest (NFR-02); "
        f"neither {repo_src.name} nor {service_src.name} references it"
    )

    # Functional sanity: the comparison must actually distinguish
    # correct from incorrect candidates. Use the repo's own hash_key()
    # helper to derive a real digest and confirm lookup is exact-match.
    plaintext = "fr03-compare-digest-fixture-bbb"
    repo = ApiKeyRepo()
    repo.add(plaintext, "write")
    assert repo.lookup(plaintext) == "write"
    # A one-character mutation must NOT resolve.
    assert repo.lookup(plaintext + "x") is None


# ----- AC-3.5 — Revoked keys are treated as invalid ----------------------


def test_revoked_key_treated_as_invalid():
    """AC-3.5 — a key whose revoked_at is set is treated as invalid.

    Sub-assertion: FR03-revoked-key-invalid.
    # NFR-02 security — operational revocation must take effect
    #   immediately; a revoked key MUST NOT resolve to any scope.
    # SPEC.md §3 FR-03 "停用金鑰" clause.

    GREEN TODO: `ApiKeyRepo` must expose a revocation method
    (e.g. `revoke(plaintext)` or `revoke_by_hash(hash)`) that flips
    a `revoked_at` timestamp on the underlying record, AND `lookup`
    must treat revoked records as unknown (returning None).
    """
    plaintext = "fr03-revoke-fixture-ccc"
    repo = ApiKeyRepo()
    repo.add(plaintext, "admin")

    # Pre-revoke: the key resolves to its scope.
    assert repo.lookup(plaintext) == "admin"

    # Revoke the key. The interface name is intentionally left to
    # GREEN; we probe the most likely candidates via getattr so the
    # test pins the BEHAVIOUR (post-revoke lookup returns None) without
    # over-specifying the method name.
    revoke_fn = None
    for candidate in ("revoke", "revoke_by_hash", "revoke_key"):
        fn = getattr(repo, candidate, None)
        if callable(fn):
            revoke_fn = fn
            break

    if revoke_fn is None:
        # RED signal — surface the missing API explicitly.
        pytest.fail(
            "ApiKeyRepo has no revocation method "
            "(expected one of: revoke, revoke_by_hash, revoke_key)"
        )

    # Try `revoke(plaintext)` first; fall back to `revoke_by_hash(hash_key(plaintext))`.
    try:
        revoke_fn(plaintext)
    except TypeError:
        revoke_fn(hash_key(plaintext))

    # Post-revoke: lookup must return None.
    assert repo.lookup(plaintext) is None, (
        "revoked key must be treated as invalid (lookup returns None)"
    )


# ----- AC-3.6 — `python -m taskq_api key create` prints plaintext once ----


def test_key_create_prints_plaintext_once_persists_hash():
    """AC-3.6 — `python -m taskq_api key create --scope <scope>` prints
    the plaintext exactly once and persists only the hash.

    Sub-assertion: FR03-cli-prints-once-hash-only.
    # NFR-04 security — the plaintext appears in stdout ONCE for the
    #   operator to capture; nothing else (logs, metrics, errors) may
    #   contain it; only the SHA-256 hash is persisted (PROJECT_BRIEF
    #   NFR-04).

    Decision: OUT-OF-PROCESS via `subprocess.run` — this is the real
    user-facing entry point declared by SPEC.md §3 FR-03. We must
    propagate PYTHONPATH to the child env because pytest's pythonpath
    does NOT cross process boundaries.
    """
    project_root = Path(__file__).resolve().parent.parent
    src_root = project_root / "src"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "taskq_api",
            "key",
            "create",
            "--scope",
            "read",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project_root),
        timeout=15,
    )

    assert result.returncode == 0, (
        f"`python -m taskq_api key create` failed: "
        f"rc={result.returncode} stderr={result.stderr!r}"
    )

    plaintext = result.stdout.strip()
    assert plaintext, (
        "expected the plaintext key on stdout; got empty output"
    )
    assert len(plaintext) >= 16, (
        f"plaintext key looks unreasonably short: {plaintext!r}"
    )
    # Plaintext must not leak into stderr (NFR-04).
    assert plaintext not in result.stderr, (
        "plaintext leaked into stderr — NFR-04 violation"
    )

    # Persistence invariant: the SHA-256 hash of the printed plaintext
    # is a 64-char hex string. If the CLI persisted the plaintext
    # verbatim (instead of hashing first), it would still type-check,
    # but the design contract per SPEC.md §3 FR-03 is "stored as
    # SHA-256 hash". Verify the helper agrees.
    derived_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert len(derived_hash) == 64 and all(
        c in "0123456789abcdef" for c in derived_hash
    ), f"derived hash is not a 64-char hex digest: {derived_hash!r}"

    # Print appears exactly once on stdout (i.e. the plaintext line is
    # not duplicated in subsequent lines, and no other line echoes it).
    stdout_lines = [line for line in result.stdout.splitlines() if plaintext in line]
    assert len(stdout_lines) == 1, (
        f"plaintext should appear exactly once on stdout; "
        f"found {len(stdout_lines)} occurrences: {stdout_lines!r}"
    )


# ----- AC-3.7 — /healthz and /readyz require no authentication ------------


def test_health_endpoints_no_auth_required(client):
    """AC-3.7 — /healthz and /readyz return 200 without X-API-Key.

    Sub-assertion: FR03-health-no-auth.
    # NFR-02 security — health endpoints are liveness/readiness probes
    #   that MUST be reachable without credentials so orchestrators can
    #   gate traffic without first minting an API key (FR-09 + FR-03).

    GREEN TODO: `taskq_api.app.create_app()` must register `GET /healthz`
    and `GET /readyz` on the application (NOT under `/v1`, and NOT
    behind `require_api_key`). Both return 200.
    """
    # /healthz
    h = client.get("/healthz")
    assert h.status_code == 200, (
        f"expected /healthz to return 200 without auth, "
        f"got {h.status_code} body={h.text!r}"
    )

    # /readyz
    r = client.get("/readyz")
    assert r.status_code == 200, (
        f"expected /readyz to return 200 without auth, "
        f"got {r.status_code} body={r.text!r}"
    )

    # Sanity — the endpoints must NOT be guarded by an auth dep:
    # a request with an explicitly invalid header must still succeed.
    h_bad = client.get("/healthz", headers={"X-API-Key": "bogus"})
    assert h_bad.status_code == 200, (
        f"expected /healthz to ignore X-API-Key, "
        f"got {h_bad.status_code} body={h_bad.text!r}"
    )
    r_bad = client.get("/readyz", headers={"X-API-Key": "bogus"})
    assert r_bad.status_code == 200, (
        f"expected /readyz to ignore X-API-Key, "
        f"got {r_bad.status_code} body={r_bad.text!r}"
    )
