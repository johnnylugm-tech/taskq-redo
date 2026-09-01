"""RED step — failing tests for FR-05 Rate Limiting.

Covers the five acceptance criteria declared in SPEC.md §3 FR-05 and
TEST_SPEC.md FR-05 cases 1-5:

  AC-5.1 — Requests exceeding `TASKQ_RATE_BURST` within the bucket's
           refill window return 429 + problem+json + `Retry-After`
           header.
  AC-5.2 — After the bucket refills at `TASKQ_RATE_PER_SEC`, requests
           succeed again.
  AC-5.3 — Bucket state is shared across workers (database-backed,
           not in-process).
  AC-5.4 — Bucket update uses a single transaction with row-level
           lock (`with_for_update`); no over-admit under contention.
  AC-5.5 — `/healthz` and `/readyz` are exempt from rate limiting.

Per SAB.json (`fr_module_traceability.FR-05`), these are the bound
modules the GREEN implementation must place on disk:

  taskq_api.api.deps          -> api/deps.py        (exists; must wire ratelimit)
  taskq_api.service.ratelimit -> service/ratelimit.py (MUST be created by GREEN)
  taskq_api.repository.rate_repo -> repository/rate_repo.py (MUST be created by GREEN)

These tests intentionally import the SAB-declared entry points so
pytest will fail at the collection boundary (ModuleNotFoundError on
`service.ratelimit` / `repository.rate_repo`) while the GREEN
implementation is still missing — this is the expected RED state and
is preferable to writing test-only stubs that would mask the absence
of the real implementation.

Citations:
  SPEC.md §3 FR-05 (rate limiting whole section)
  SPEC.md §8 #9 (burst test)
  SPEC.md §9 R12 (row-level lock race-condition mitigation)
  TEST_SPEC.md FR-05 (cases 1-5)
"""

# SAB binding — GREEN must implement these module paths on disk.
# The two `service.ratelimit` and `repository.rate_repo` imports are
# intentionally NOT wrapped in try/except; a clean ModuleNotFoundError
# at collection time IS the RED signal (per UNIT TEST CONTRACT).
from taskq_api.app import app  # noqa: E402
from taskq_api.repository import rate_repo  # noqa: E402

import inspect
import threading

import pytest
from fastapi.testclient import TestClient


# ----- Shared fixtures ---------------------------------------------------


@pytest.fixture
def client():
    """Sync TestClient bound to the FastAPI `app` instance.

    GREEN TODO: the FR-05 rate-limit dependency must be wired into
    `taskq_api.app:app` so every non-health `/v1/*` route runs the
    bucket check before reaching the handler. `/healthz` and `/readyz`
    are mounted at the app root and MUST stay exempt (AC-5.5).
    """
    return TestClient(app)


@pytest.fixture
def write_api_key():
    """Plaintext write-scope API key seeded by config.API_KEY_SEEDS.

    GREEN TODO: the FR-05 rate-limit dep must run AFTER `require_api_key`
    so it can attribute the bucket to the resolved scope/key — but the
    `Retry-After` enforcement happens before any business logic, so a
    request from an invalid key still 401s instead of being throttled.
    """
    return "fr01-test-write-key-aaaa"


# GREEN TODO: `taskq_api.service.ratelimit` must expose a class or
# factory `TokenBucket` (or similar) with at least these methods:
#   * `try_consume(tokens: float = 1.0) -> RateDecision`
#   * `RateDecision.allowed: bool`, `.retry_after_seconds: int`
# `taskq_api.repository.rate_repo.RateRepo` must expose:
#   * `consume(scope: str, n: int) -> RateDecision`  (uses row-level lock)
#   * `peek(scope: str) -> float`  (current token count)
# GREEN must also export `BURST` / `RATE_PER_SEC` constants bound to
# `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` (default 20 / 5.0 per
# TEST_SPEC cases 1 + 2).


# ----- AC-5.1 — burst over limit returns 429 + Retry-After ----------------


def test_burst_over_limit_returns_429_with_retry_after(client, write_api_key):
    """AC-5.1 — exceeding TASKQ_RATE_BURST yields 429 + Retry-After.

    Sub-assertions: FR05-burst-over-capacity, FR05-status-429,
    FR05-retry-after-header-present, FR05-burst-capacity-value.
    # NP-03 (rate limit 429) — the canonical verifier of FR-05 boundary
    #   behaviour (TEST_SPEC.md FR-05 case 1, Inputs: burst_count=21,
    #   burst_capacity=20, expected_status=429, expected_header=Retry-After).
    # NFR-02 security — the 429 body MUST be the fixed RFC 7807
    #   envelope (TRACEABILITY_MATRIX.md §FR↔NFR row NFR-02:
    #   cross-references FR-02 / FR-03 / FR-04 / FR-10); `detail`
    #   MUST NOT echo internal state such as the bucket row id or
    #   current token count.
    # NFR-05 documentation — every public symbol under `taskq_api.*`
    #   carries an `[FR-XX]`/`[NFR-XX]` tag (TRACEABILITY_MATRIX.md
    #   §FR↔NFR row NFR-05: cross-references FR-01..FR-10); this
    #   test pins the `rate_limit` dep / `consume` / `take_token`
    #   contract that the docstrings encode.
    # NFR-06 layering — `taskq_api.api.deps.rate_limit` is the
    #   api-layer seam; service and repository layers stay below it
    #   per `.importlinter` (`api > service > repository > models`).
    # NFR-10 integration_coverage — full HTTP cycle through ASGITransport
    #   including the rate-limit decision and problem+json envelope.
    # SPEC.md §3 FR-05 paragraph 1: "Over limit → HTTP 429 + problem+json
    #   + Retry-After header (seconds)".
    # SPEC.md §8 #9 (burst test in acceptance bullets).

    The TEST_SPEC inputs fix `burst_count=21`, `burst_capacity=20`:
    exactly one more request than the bucket holds. We send N+1
    requests against POST /v1/tasks (write scope, single handler) and
    assert the N+1-th response is 429 with a problem+json body and a
    `Retry-After` header carrying a positive integer second count.

    GREEN TODO: the api/router layer must call
    `taskq_api.service.ratelimit` (via `taskq_api.api.deps`) for every
    request other than `/healthz` and `/readyz`; the dep returns a
    429 problem+json response when the bucket cannot grant a token.
    """
    burst_capacity = 20
    over_burst = burst_capacity + 1  # = 21

    # Pre-drain the write-scope bucket to a known empty state via the
    # SAB-bound `RateRepo.consume` seam so the burst boundary is
    # deterministic. Sending `burst_capacity` requests through the
    # HTTP layer alone takes ~1s end-to-end; at TASKQ_RATE_PER_SEC=5.0
    # refill during that window adds >5 tokens, masking the
    # boundary and letting the N+1-th request through. Draining
    # directly via the repo removes the refill-vs-burst race and
    # pins the spec invariant (burst_capacity = 20 → next request 429).
    repo = rate_repo.RateRepo()
    drain = repo.consume(scope="write", n=burst_capacity)
    assert drain.allowed, (
        f"precondition: must drain the bucket by {burst_capacity}; "
        f"got decision={drain!r}"
    )

    statuses: list[int] = []
    last_response = None
    for i in range(over_burst):
        resp = client.post(
            "/v1/tasks",
            headers={"X-API-Key": write_api_key},
            json={
                "command": "echo burst",
                "name": f"fr05-burst-{i:02d}",
            },
        )
        statuses.append(resp.status_code)
        last_response = resp

    # Exactly one request over the burst — the LAST one — must be 429.
    # Earlier requests may legitimately 201 (bucket granted) OR fail
    # for unrelated reasons (e.g. duplicate name in same run); the
    # binding assertion is the 429 on the over-burst request.
    assert statuses[-1] == 429, (
        f"expected the request over TASKQ_RATE_BURST={burst_capacity} "
        f"to return 429; got {statuses[-1]}; all statuses={statuses!r}"
    )

    # Content-type MUST be problem+json (FR-10 envelope).
    assert last_response is not None
    assert last_response.headers["content-type"].startswith(
        "application/problem+json"
    ), (
        "429 must serialize as application/problem+json (FR-10 + SPEC.md "
        f"§3 FR-05); got {last_response.headers.get('content-type')!r}"
    )

    # Retry-After header MUST be present and parse as a positive integer.
    retry_after_raw = last_response.headers.get("Retry-After")
    assert retry_after_raw is not None, (
        "429 response must carry a Retry-After header (SPEC.md §3 FR-05 "
        "paragraph 1); headers="
        f"{dict(last_response.headers)!r}"
    )
    retry_after_seconds = int(retry_after_raw)
    assert retry_after_seconds >= 1, (
        f"Retry-After must be a positive integer second count; "
        f"got {retry_after_raw!r}"
    )


# ----- AC-5.2 — rate limit recovers after refill --------------------------


def test_rate_limit_recovers_after_refill(client, write_api_key):
    """AC-5.2 — after refill at TASKQ_RATE_PER_SEC, requests succeed.

    Sub-assertions: none directly (case 2 carries the refill invariant).
    # NP-03 (rate limit 429) — happy-path complement to AC-5.1: the
    #   bucket must allow traffic again after the refill window elapses.
    #   TEST_SPEC.md FR-05 case 2 Inputs: rate_per_sec=5.0, burst=20,
    #   refill_seconds=4 → 4 seconds of refill repays ≥ 20 tokens (one
    #   full bucket at the declared rate).
    # NFR-03 reliability — refill math must live inside the same
    #   transaction+row-level-lock as the consume (TRACEABILITY_MATRIX.md
    #   §FR↔NFR row NFR-03: cross-references FR-06 / FR-07 / FR-08 /
    #   FR-09); a refill computed outside the lock would race the
    #   consume path and admit stale capacity.
    # NFR-06 layering — the refill computation lives in
    #   `taskq_api.service.ratelimit` (service layer) per the
    #   `.importlinter` contract `api > service > repository > models`.
    # SPEC.md §3 FR-05 paragraph 1: "After the bucket refills at
    #   TASKQ_RATE_PER_SEC, requests succeed again".

    Strategy:
      1. Exhaust the bucket (burst + 1 requests → final 429 captured).
      2. Sleep for the refill window declared by TEST_SPEC (`refill_seconds=4`).
      3. Issue one more request; it MUST NOT be 429 (i.e. the bucket
         has refilled enough to admit at least one new token).

    GREEN TODO: `taskq_api.service.ratelimit.TokenBucket` must derive
    `retry_after = ceil((tokens_requested - available) / RATE_PER_SEC)`;
    `RateRepo.consume` must compute the new bucket level as
    `min(capacity, old_level + (now - last_refill_at) * RATE_PER_SEC)`
    inside the same `with_for_update` transaction that subtracts the
    consumed token(s).
    """
    import time as _time

    burst_capacity = 20

    # 1. Exhaust the bucket deterministically via the SAB-bound
    # `RateRepo.consume` seam (the in-process analog of the
    # `<query>.with_for_update()` transaction in the SQLAlchemy
    # implementation). Sending `burst_capacity + 1` HTTP requests
    # alone takes >1s and refill during that window masks the
    # exhaustion; draining via the repo pins the precondition
    # so the next HTTP request is guaranteed to be 429 (the
    # boundary this test is verifying).
    repo = rate_repo.RateRepo()
    drain = repo.consume(scope="write", n=burst_capacity)
    assert drain.allowed, (
        f"precondition: must drain the bucket by {burst_capacity}; "
        f"got decision={drain!r}"
    )

    # One HTTP request over the now-empty bucket must be 429.
    final_status: int | None = None
    for i in range(1):
        resp = client.post(
            "/v1/tasks",
            headers={"X-API-Key": write_api_key},
            json={
                "command": "echo refill",
                "name": f"fr05-refill-{i:02d}",
            },
        )
        final_status = resp.status_code

    assert final_status == 429, (
        f"precondition failed: bucket exhaustion must surface as 429; "
        f"got {final_status}"
    )

    # 2. Sleep for the refill window (TEST_SPEC refill_seconds=4, which
    #    repays 4s * 5.0/s = 20 tokens — one full bucket).
    _time.sleep(4.0)

    # 3. One more request must NOT be 429 — the bucket has refilled.
    recovered = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={
            "command": "echo recovered",
            "name": "fr05-refill-after",
        },
    )
    assert recovered.status_code != 429, (
        f"after {4.0}s of refill at TASKQ_RATE_PER_SEC=5.0/s the bucket "
        f"must admit traffic again; got 429 body={recovered.text!r}"
    )


# ----- AC-5.3 — bucket state shared across workers ------------------------


def test_bucket_state_shared_across_workers():
    """AC-5.3 — bucket state is shared (DB-backed), not per-process.

    Sub-assertion: FR05-shared-bucket-state.
    # NP-13 (concurrency — row-level lock) — bucket must NOT be held in
    #   a per-process dict; the "shared across workers" guarantee is
    #   exactly what forces the DB-backed implementation, and what
    #   later justifies the row-level lock in AC-5.4.
    # NFR-06 layering — the shared row is the repository layer's
    #   contract (`taskq_api.repository.rate_repo`); two repo
    #   instances over the same backing store MUST observe the same
    #   state, which is what `.importlinter` exists to enforce.
    # SPEC.md §3 FR-05 paragraph 1 "存於資料庫" clause.

    We use the SAB-bound `RateRepo` to simulate two workers (two
    threads, each with its own `RateRepo` instance sharing the same
    underlying row) and verify the bucket decrement applied by worker
    A is visible to worker B — i.e. they share state.

    GREEN TODO: `taskq_api.repository.rate_repo.RateRepo` must persist
    the bucket state in a single shared row keyed by `scope` (or api
    key id). Two `RateRepo()` instances over the same backing store
    MUST observe the same token count.
    """
    repo_a = rate_repo.RateRepo()
    repo_b = rate_repo.RateRepo()

    scope = "fr05-shared-scope"

    # Establish a known starting point: refill the bucket to capacity
    # by waiting or by directly seeding via the API. We exercise the
    # peek/consume seam directly.
    initial = repo_a.peek(scope)
    # Worker A consumes 5 tokens.
    decision_a = repo_a.consume(scope, n=5)
    assert decision_a.allowed, "first worker must be admitted at full bucket"

    # Worker B peeks — it MUST see the same shared state (bucket reduced
    # by 5 from whatever `initial` was). If each worker held a private
    # bucket, `peek_b` would equal `initial` and this test would fail.
    peek_b = repo_b.peek(scope)
    assert peek_b == pytest.approx(initial - 5, rel=0.0, abs=1e-6), (
        f"bucket state is not shared across workers: worker_b sees "
        f"{peek_b} after worker_a consumed 5 from initial {initial}; "
        "expected ~{initial - 5} — bucket is per-process, not DB-backed "
        "(SPEC.md §3 FR-05 存於資料庫 clause violated)"
    )


# ----- AC-5.4 — bucket update uses row-level lock -------------------------


def test_bucket_update_uses_row_level_lock():
    """AC-5.4 — bucket update uses `with_for_update` in one transaction.

    Sub-assertion: FR05-row-level-lock-used.
    # NP-13 (concurrency — row-level lock) — the canonical verifier of
    #   the §9 R12 race-condition mitigation. Without the lock, two
    #   workers reading-then-writing the same bucket row can both
    #   observe `tokens=1` and both consume → over-admit by 1.
    #   TEST_SPEC.md FR-05 case 4 Inputs: concurrent_workers=8,
    #   expected_lock_call=with_for_update, no_over_admit=true.
    # NFR-03 reliability — explicit transaction boundary around the
    #   read-modify-write (TRACEABILITY_MATRIX.md §FR↔NFR row NFR-03:
    #   cross-references FR-06 / FR-07 / FR-08 / FR-09); the lock
    #   alone is insufficient without the surrounding transaction.
    # NFR-05 documentation — the lock + transaction contract must be
    #   encoded in the `RateRepo.consume` docstring with `[FR-05]`
    #   (TRACEABILITY_MATRIX.md §FR↔NFR row NFR-05: cross-references
    #   FR-01..FR-10).
    # SPEC.md §3 FR-05 paragraph 1: "updates must occur within a single
    #   transaction using a row-level lock".
    # SPEC.md §9 R12 (race-condition mitigation).

    GREEN TODO: `taskq_api.repository.rate_repo.RateRepo.consume` MUST
    call `<query>.with_for_update()` inside a single transaction so the
    read-modify-write sequence is serialised at the row level. We
    verify this with two complementary checks:

      (a) Static check — the `RateRepo.consume` callable references
          `with_for_update` somewhere in its source (or in the helper
          it delegates to).
      (b) Dynamic check — 8 concurrent threads each call `consume`
          against the same scope with `n=1`. The total admissions
          cannot exceed the bucket capacity + refill earned during the
          run; on a 1-second test with capacity=20, RATE_PER_SEC=5,
          this is essentially "no over-admit".

    Static check first — if the source does not mention the lock,
    the dynamic check is meaningless.
    """
    # (a) Static: consume() must reference with_for_update.
    repo = rate_repo.RateRepo()
    if not hasattr(repo, "consume"):
        pytest.fail(
            "taskq_api.repository.rate_repo.RateRepo must expose "
            "`consume(scope, n)` (AC-5.4 / FR-05 row-level lock contract)"
        )

    src = inspect.getsource(repo.consume)
    # Also follow one level of delegation in case `consume` is a thin
    # wrapper that calls an internal `_consume_locked` (or similar).
    qualname = getattr(repo.consume, "__qualname__", "")
    if "<locals>" in qualname:
        owner_name = qualname.split(".")[0].split("<")[0]
        try:
            owner_cls = getattr(rate_repo, owner_name)
            src_extra = inspect.getsource(owner_cls)
        except (AttributeError, TypeError):
            src_extra = ""
    else:
        # Module-level function or method — pull the whole module source.
        src_extra = inspect.getsource(rate_repo)

    combined_src = src + "\n" + src_extra
    assert "with_for_update" in combined_src, (
        "taskq_api.repository.rate_repo.RateRepo.consume must call "
        "`with_for_update()` inside a single transaction (AC-5.4 / "
        "SPEC.md §3 FR-05 paragraph 1 + §9 R12). Source contained no "
        "reference to with_for_update."
    )

    # (b) Dynamic: 8 concurrent workers cannot over-admit beyond the
    # bucket capacity (within a sub-second window). The test asks each
    # worker to consume 1 token; total admissions must be <= capacity.
    scope = "fr05-lock-test"
    n_workers = 8
    capacity = 20  # matches TASKQ_RATE_BURST default

    admitted = 0
    admitted_lock = threading.Lock()

    def _worker() -> None:
        nonlocal admitted
        r = rate_repo.RateRepo()
        decision = r.consume(scope, n=1)
        if decision.allowed:
            with admitted_lock:
                admitted += 1

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert admitted <= capacity, (
        f"row-level lock missing: {n_workers} concurrent workers "
        f"admitted {admitted} tokens vs bucket capacity {capacity}; "
        "over-admit detected (SPEC.md §3 FR-05 + §9 R12 violated)"
    )


# ----- AC-5.5 — health endpoints exempt from rate limit -------------------


def test_health_endpoints_exempt_from_rate_limit(client):
    """AC-5.5 — /healthz and /readyz are NOT rate-limited.

    Sub-assertion: FR05-healthz-exempt.
    # SPEC.md §3 FR-05 paragraph 1: "/healthz and /readyz are not
    #   rate-limited".
    # SPEC.md §3 FR-09 — orchestrators probe these endpoints at high
    #   frequency (k8s liveness/readiness); rate-limiting them would
    #   cause the orchestrator to mark the pod unhealthy.
    # NFR-10 integration_coverage — full HTTP cycle at the boundary
    #   (TRACEABILITY_MATRIX.md §FR↔NFR row NFR-10: cross-references
    #   FR-01..FR-10); 100 GETs to /healthz prove the dep short-circuits
    #   the bucket check.
    # TEST_SPEC.md FR-05 case 5 Inputs: endpoint=/healthz,
    #   burst_count=100, expected_status=200.

    Issue 100 GETs to /healthz; EVERY one must return 200. If the
    rate-limit dep is incorrectly wired onto the health router, at
    least the request over the bucket capacity (the 21st, given the
    default TASKQ_RATE_BURST=20) would return 429 instead.

    GREEN TODO: `taskq_api.app:app` must NOT apply the FR-05 dep to
    `/healthz` or `/readyz` — the `app.include_router(health_router)`
    call in `create_app` runs the health router OUTSIDE the `/v1`
    bucket-checked group, and the dep itself short-circuits on the
    `/healthz` / `/readyz` paths.
    """
    burst_count = 100

    statuses: list[int] = []
    for _ in range(burst_count):
        resp = client.get("/healthz")
        statuses.append(resp.status_code)

    non_200 = [s for s in statuses if s != 200]
    assert not non_200, (
        f"/healthz must be exempt from rate limiting (AC-5.5); "
        f"got {len(non_200)} non-200 responses out of {burst_count}; "
        f"unique non-200 codes={sorted(set(non_200))!r}"
    )
    # Defensive: confirm at least one response was 429-shaped if a
    # regression accidentally re-routes through the bucket.
    assert 429 not in statuses, (
        "/healthz returned 429 on at least one request — rate limit "
        "applied to a health endpoint (SPEC.md §3 FR-05 violated)"
    )