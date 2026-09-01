"""[FR-09] Health and observability service (SPEC.md §3 FR-09).

[FR-09] Owns the readiness probes (`check_db`, `check_migration`) and
the metrics collector (`collect_metrics`). Per SPEC.md §3 FR-09 + §8
#10 / #11, these functions are the read-side of operational
visibility for the API: orchestrators probe `/readyz`, operators
inspect `/v1/metrics`.

The probe functions return `(ok: bool, detail: str)` tuples so the
api layer can translate them into a problem+json response with the
operator-visible `detail` string (SPEC.md §3 FR-09 row 2 + NFR-04:
no internal detail leakage).

`collect_metrics` returns three top-level series per SPEC.md §3
FR-09 row 3:
  * `task_counts`           — count of tasks per status
  * `latency_percentiles`   — execution latency percentiles
  * `rate_limit_rejects`    — 429 reject counts

Each series is guaranteed to exist (possibly empty) so downstream
tooling can rely on a stable schema across versions.

Citations:
  SPEC.md §3 FR-09 (whole section)
  SPEC.md §8 #10 (DB-down verifier)
  SPEC.md §8 #11 (migration-behind-head verifier)
  SPEC.md §4 NFR-04 (no internals in error detail)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from taskq_api.models.orm import Task, TaskResult
from taskq_api.repository.rate_repo import RateRepo
from taskq_api.repository.session import engine, transaction


# ---------------------------------------------------------------------------
# Readiness probes — SPEC.md §3 FR-09 row 2 + §8 #10 / #11
# ---------------------------------------------------------------------------


def check_db(target: Engine = engine) -> Tuple[bool, str]:
    """Probe database connectivity (FR-09 / SPEC.md §8 #10).

    Returns `(ok, detail)`. `ok=True` when the engine answers
    `SELECT 1`; `ok=False` otherwise with `detail` naming the
    failure. The exception class name is included (safe) but the
    exception args / traceback are NOT — NFR-04 forbids internal
    detail leakage.
    """
    try:
        with target.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "database reachable"
    except Exception as exc:  # noqa: BLE001 — readiness probe must never raise
        return False, f"database unreachable: {type(exc).__name__}"


def check_migration(
    cfg_path: Optional[Path] = None,
    target: Engine = engine,
) -> Tuple[bool, str]:
    """Probe `alembic current == head` (FR-09 / SPEC.md §8 #11).

    Returns `(ok, detail)`. `ok=True` when the live DB's alembic
    revision matches the script directory head; `ok=False` when
    the schema is stale, with `detail` naming the failure.

    The function is tolerant of dev environments where
    `alembic.ini` is absent or alembic is not initialised — those
    conditions return `(True, "...")` so a sandbox without
    migrations can still pass readiness (the FR-09 contract is
    about live deployments; the absence of a migration directory
    is an operator choice, not a fault).
    """
    try:
        from alembic.config import Config  # noqa: PLC0415 (deferred)
        from alembic.runtime.migration import MigrationContext  # noqa: PLC0415
        from alembic.script import ScriptDirectory  # noqa: PLC0415

        # Resolve `alembic.ini` relative to CWD or one parent up so
        # the probe works both from the repo root and from a test
        # runner whose CWD is `03-development/`.
        if cfg_path is None:
            cwd = Path(os.getcwd()).resolve()
            candidates = [cwd / "alembic.ini", cwd.parent / "alembic.ini"]
            cfg_path = next((p for p in candidates if p.exists()), None)
        if cfg_path is None:
            return True, "migration check skipped (no alembic.ini)"
        cfg = Config(str(cfg_path))
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        with target.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()
        if current == head:
            return True, "migration at head"
        return False, f"migration behind head (current={current}, head={head})"
    except Exception as exc:  # noqa: BLE001 — readiness probe must never raise
        return False, f"migration check failed: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Metrics — SPEC.md §3 FR-09 row 3
# ---------------------------------------------------------------------------


def _task_counts_by_status() -> dict:
    """Return a `{status: count}` mapping for every persisted task row."""
    counts: dict[str, int] = {}
    with transaction() as session:
        rows = session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        ).all()
    for status_value, count in rows:
        counts[str(status_value)] = int(count)
    return counts


def _latency_percentiles() -> dict:
    """Compute p50/p90/p99 execution-latency percentiles from `task_results`.

    Uses the linear-interpolation method (NIST / Excel default): for
    a sorted list of length `n`, the `p`-th percentile index is
    `(n - 1) * p`; integer and fractional parts are linearly
    interpolated. Empty input returns zeros so the series shape is
    stable when no runs have completed yet.
    """
    with transaction() as session:
        durations = [
            int(row[0])
            for row in session.execute(select(TaskResult.duration_ms)).all()
        ]
    if not durations:
        return {"p50": 0, "p90": 0, "p99": 0}
    durations.sort()
    return {
        "p50": _percentile(durations, 0.5),
        "p90": _percentile(durations, 0.9),
        "p99": _percentile(durations, 0.99),
    }


def _percentile(sorted_values: list[int], p: float) -> int:
    """Linear-interpolation percentile (NIST / Excel default).

    Assumes `sorted_values` is non-empty and sorted ascending. The
    `p`-th percentile index is `(n - 1) * p`; integer and fractional
    parts are linearly interpolated.
    """
    n = len(sorted_values)
    k = (n - 1) * p
    f = int(k)
    c = min(f + 1, n - 1)
    if f == c:
        return sorted_values[f]
    return int(
        round(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f))
    )


def _rate_limit_rejects(rate_repo: RateRepo = RateRepo()) -> dict:
    """Return the rate-limit reject count series (FR-09 row 3 series 3).

    Reads the per-scope reject counter the FR-05 `RateRepo.consume`
    path increments on every rejected `try_consume` call. The series
    is exposed as `{scope: count}` so operators can spot which scope
    is being throttled hardest; an empty dict is returned when no
    rejection has happened yet (so the series shape stays stable).
    """
    return rate_repo.reject_counts()


def collect_metrics(rate_repo: RateRepo = RateRepo()) -> dict:
    """Return the three FR-09 metrics series as a JSON-serialisable dict.

    The handler at `/v1/metrics` returns this dict verbatim; every
    series key MUST be present so callers do not need to handle
    KeyError when a series is empty (SPEC.md §3 FR-09 row 3
    enumeration).
    """
    return {
        "task_counts": _task_counts_by_status(),
        "latency_percentiles": _latency_percentiles(),
        "rate_limit_rejects": _rate_limit_rejects(rate_repo),
    }


__all__ = ["check_db", "check_migration", "collect_metrics"]
