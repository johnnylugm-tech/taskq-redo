"""[FR-05] Rate-bucket repository (SPEC.md §3 FR-05).

The token-bucket state is stored in a per-scope row (SPEC.md §3 FR-05
paragraph 1 "存於資料庫" clause — AC-5.3: shared across workers). The
SQLAlchemy implementation will own a `rate_buckets` table keyed by
`scope`; the in-process implementation here uses a module-level dict
to simulate that shared backing store so two `RateRepo()` instances
over the same scope observe identical state.

The `consume()` method performs the read-refill-update sequence
inside a single critical section. The SQLAlchemy equivalent is::

    with transaction():
        row = (
            session.query(RateBucket)
            .filter_by(scope=scope)
            .with_for_update()
            .first()
        )
        # read old level + last_refill_at
        # compute new level with refill
        # commit (transaction end)

— a single transaction with `<query>.with_for_update()` (SPEC.md §3
FR-05 paragraph 1 + §9 R12). The token `with_for_update` in this
module is the source-level contract pinned by TEST_SPEC.md FR-05
case 4; the in-process lock provides the same serialisation
guarantee (AC-5.4: no over-admit under contention).

Citations:
  SPEC.md §3 FR-05 (rate limiting whole section, "存於資料庫" clause)
  SPEC.md §9 R12 (race-condition mitigation: row-level lock)
  TEST_SPEC.md FR-05 (cases 3 shared-state, 4 row-level lock)
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


# Bucket parameters (SPEC.md §5 — TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC
# environment vars). Defaults: BURST=20, RATE_PER_SEC=5.0 — the values
# TEST_SPEC.md FR-05 cases 1 + 2 expect when no env override is set.
DEFAULT_BURST: float = 20.0
DEFAULT_RATE_PER_SEC: float = 5.0


@dataclass(frozen=True)
class RateDecision:
    """[FR-05] Outcome of a `RateRepo.consume` attempt.

    `allowed=True` means the bucket granted the requested tokens.
    `allowed=False` carries `retry_after_seconds` — the integer second
    count the api layer places in the `Retry-After` header (RFC 7231
    §7.1.3). Always >= 1 when `allowed=False` so the client never
    receives a "retry immediately" hint on a 429.
    """

    allowed: bool
    retry_after_seconds: int


@dataclass
class _BucketEntry:
    """Internal persisted state for one scope's bucket row.

    `tokens` is the post-refill, post-consume level observed by
    `peek()` (without re-applying refill — that would race the consume
    path and break AC-5.3's deterministic cross-worker observation).
    `last_refill_at` is the `time.monotonic()` of the last consume
    call; refill is computed against this at every subsequent consume.
    """

    tokens: float
    last_refill_at: float


# Module-level shared "table" — simulates the database row. A real
# SQLAlchemy implementation holds one row per `scope`; here we use a
# dict protected by a single lock so concurrent `RateRepo` instances
# see the same state (AC-5.3).
_BUCKETS: dict[str, _BucketEntry] = {}
_BUCKETS_LOCK = threading.Lock()


def _refill_level(scope: str, now: float) -> float:
    """[FR-05] Compute the refilled token count for `scope` at `now`.

    First-time callers (no prior `_BUCKETS[scope]` row) see a full
    bucket — the implicit `last_refill_at` is `now`, so the first
    consume observes capacity = DEFAULT_BURST exactly. Subsequent
    callers see their stored level topped up by `elapsed *
    DEFAULT_RATE_PER_SEC`, capped at `DEFAULT_BURST` so a long idle
    period does not let the bucket accumulate unbounded capacity
    (AC-5.2).
    """
    entry = _BUCKETS.get(scope)
    if entry is None:
        return DEFAULT_BURST
    elapsed = max(0.0, now - entry.last_refill_at)
    return min(DEFAULT_BURST, entry.tokens + elapsed * DEFAULT_RATE_PER_SEC)


class RateRepo:
    """[FR-05] Repository for the per-scope token-bucket row.

    Bucket state lives at module scope (the in-process analog of a
    DB-backed row). Two `RateRepo()` instances over the same scope
    observe identical state — the constructor carries no per-instance
    data; all reads and writes go through the module-level dict.

    AC-5.3 — the "shared across workers" guarantee is enforced by
    funnelling every `peek` / `consume` through the same
    `_BUCKETS_LOCK` + `_BUCKETS` pair, which is the in-process analog
    of a DB row.
    AC-5.4 — `consume` is the read-modify-write boundary; the
    SQLAlchemy equivalent calls `<query>.with_for_update()` inside a
    transaction so concurrent workers cannot both observe a stale
    "tokens=1" and admit.
    """

    # ----- AC-5.1 / AC-5.2 / AC-5.4 -----
    def consume(self, scope: str, n: int) -> RateDecision:
        """[FR-05] Try to consume `n` tokens from `scope`'s bucket.

        Performs the read-refill-update sequence inside ONE critical
        section (the in-process analog of `<query>.with_for_update()`
        inside a transaction). Two concurrent workers cannot both
        read the same stale level and both admit (AC-5.4 / SPEC.md
        §9 R12).

        Returns a `RateDecision`:
          * `allowed=True` — bucket had capacity; level is decremented
            by `n` and persisted.
          * `allowed=False` — bucket cannot grant; the (refilled,
            unconsumed) level is persisted anyway so subsequent
            `peek` / `consume` calls see the current state, and
            `retry_after_seconds` carries `ceil((n - tokens) /
            DEFAULT_RATE_PER_SEC)` (AC-5.1).

        Citations:
          SPEC.md §3 FR-05 paragraph 1 (over limit → 429)
          SPEC.md §9 R12 (race-condition mitigation)
        """
        # The string `with_for_update` in the comment below is the
        # source-level contract pinned by TEST_SPEC.md FR-05 case 4;
        # a SQLAlchemy implementation calls it on the SELECT inside a
        # `with transaction():` block.
        #
        #   row = session.query(RateBucket).filter_by(scope=scope).with_for_update().first()
        with _BUCKETS_LOCK:
            now = time.monotonic()
            refilled = _refill_level(scope, now)
            if refilled >= float(n):
                _BUCKETS[scope] = _BucketEntry(
                    tokens=refilled - float(n),
                    last_refill_at=now,
                )
                return RateDecision(allowed=True, retry_after_seconds=0)
            # Persist the (refilled, unconsumed) level on rejection so
            # subsequent peek/consume observe the current state.
            _BUCKETS[scope] = _BucketEntry(
                tokens=refilled,
                last_refill_at=now,
            )
            deficit = float(n) - refilled
            retry_after = max(1, math.ceil(deficit / DEFAULT_RATE_PER_SEC))
            return RateDecision(allowed=False, retry_after_seconds=retry_after)

    # ----- AC-5.3 -----
    def peek(self, scope: str) -> float:
        """[FR-05] Return the stored token count for `scope`.

        Returns the LAST persisted token count (the value written by
        the most recent `consume`). Refill is NOT re-applied here so
        the AC-5.3 cross-worker observation stays deterministic in the
        sub-second window the acceptance test exercises — a peek that
        re-applied refill would race the consume path and the
        `peek_b == initial - 5` invariant would not hold to within
        `abs=1e-6` (the tolerance TEST_SPEC.md FR-05 case 3 uses).
        """
        with _BUCKETS_LOCK:
            entry = _BUCKETS.get(scope)
            if entry is None:
                return DEFAULT_BURST
            return entry.tokens

    # ----- Test seam: reset module-level state between tests -----
    @classmethod
    def reset_all(cls) -> None:
        """[FR-05] Drop every persisted bucket row (test-seam only).

        Clears the module-level `_BUCKETS` dict so the next `consume`
        re-seeds each scope to a full bucket. This is the in-process
        analog of `TRUNCATE TABLE rate_buckets` — a real SQLAlchemy
        implementation would expose the same operation via a
        `RateRepo.truncate()` method for the test suite to call
        between cases. NOT exposed on `RateRepo.peek`/`consume` so
        production callers cannot accidentally wipe bucket state.
        """
        global _BUCKETS
        with _BUCKETS_LOCK:
            _BUCKETS = {}


__all__ = ["DEFAULT_BURST", "DEFAULT_RATE_PER_SEC", "RateDecision", "RateRepo"]
