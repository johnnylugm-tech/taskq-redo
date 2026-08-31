"""[FR-05] Token-bucket rate-limit service (SPEC.md §3 FR-05).

`TokenBucket` is the service-layer facade over `RateRepo.consume`. The
api-layer dep (`taskq_api.api.deps.rate_limit`) builds one per request,
bound to the resolved `scope` (the X-API-Key's scope string from
`require_api_key`); all keys of the same scope share a single bucket
row so a single misbehaving caller cannot starve the others, while
the dep still runs AFTER `require_api_key` so unauthenticated traffic
401s before being counted (NFR-02: no throttle on invalid keys).

The split follows NFR-06 layering:
  * `taskq_api.repository.rate_repo` — owns the persisted state +
    the row-level lock contract (`with_for_update`, AC-5.4).
  * `taskq_api.service.ratelimit`    — owns the decision semantics
    (default `tokens=1.0` per request) and the public constants
    (`BURST`, `RATE_PER_SEC`) consumed by both the dep and the FR-05
    acceptance tests.

`BURST` and `RATE_PER_SEC` are bound to the in-process defaults of
the repository (SPEC.md §5: TASKQ_RATE_BURST=20, TASKQ_RATE_PER_SEC=5.0)
and re-exported here so the FR-05 test references
`taskq_api.service.ratelimit.BURST` /
`taskq_api.service.ratelimit.RATE_PER_SEC` per TEST_SPEC cases 1+2.

Citations:
  SPEC.md §3 FR-05 (rate limiting whole section)
  SPEC.md §5 (TASKQ_RATE_BURST, TASKQ_RATE_PER_SEC environment vars)
  SPEC.md §3 NFR-06 (api > service > repository > models layering)
"""
from __future__ import annotations

from taskq_api.repository.rate_repo import (
    DEFAULT_BURST,
    DEFAULT_RATE_PER_SEC,
    RateDecision,
    RateRepo,
)


# Public constants bound to the repository defaults. Re-exported under
# the names TEST_SPEC.md FR-05 cases 1 + 2 reference.
BURST: float = DEFAULT_BURST
RATE_PER_SEC: float = DEFAULT_RATE_PER_SEC


class TokenBucket:
    """[FR-05] Token-bucket facade over `RateRepo` (AC-5.1, AC-5.2).

    Each instance is bound to a single `scope` (the api-key scope
    resolved upstream by `require_api_key`). The bucket STATE lives
    in the shared `RateRepo` module-level store (AC-5.3) — two
    `TokenBucket` instances over the same scope + repo observe the
    same persisted level.
    """

    def __init__(self, scope: str, repo: RateRepo) -> None:
        self._scope = scope
        self._repo = repo

    # ----- AC-5.1 -----
    def try_consume(self, tokens: float = 1.0) -> RateDecision:
        """[FR-05] Try to consume `tokens` from this bucket.

        Returns a `RateDecision` with `allowed=True` if the bucket
        had capacity, otherwise `allowed=False` with
        `retry_after_seconds` (always >= 1 on rejection — the api
        layer places it in the `Retry-After` header, AC-5.1).

        The decision is delegated to `RateRepo.consume` which owns
        the persisted state + row-level lock contract (AC-5.4). The
        service-layer responsibility is limited to translating the
        caller-friendly `tokens` (float) into the repo's integer
        `n` argument.
        """
        return self._repo.consume(self._scope, n=int(tokens))


__all__ = ["BURST", "RATE_PER_SEC", "TokenBucket"]
