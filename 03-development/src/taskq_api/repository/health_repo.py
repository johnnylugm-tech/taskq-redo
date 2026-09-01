"""[FR-09] Repository-layer helpers for the health / observability probes.

[FR-09] Owns the SQLAlchemy access the readiness probes
(`db_reachable`, `alembic_current_revision`, `alembic_head_revision`)
and the metrics collector helpers (`task_counts_by_status`,
`task_result_durations_ms`) need. These functions live here — not in
`service/health` — because the `architecture_constraints` contract
forbids the `service` layer from importing `sqlalchemy` directly
(NFR-06 + FR-06 layer hygiene). `repository/` is the only layer
permitted to hold the SQL surface, so the SQL statements belong here.

The signatures return plain Python values (`tuple`, `dict`, `list`,
`Optional[str]`) so the service layer can orchestrate them without
ever touching a SQLAlchemy primitive.

Citations:
  SPEC.md §3 FR-09 (whole section)
  SPEC.md §3 FR-06 paragraph 1 (repository owns the SQL surface)
  SPEC.md §8 #10 / #11 (DB-down + migration-behind-head verifiers)
"""
from __future__ import annotations

from typing import Optional

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from taskq_api.models.orm import Task, TaskResult
from taskq_api.repository.session import engine, transaction


# ---------------------------------------------------------------------------
# Readiness probes — SPEC.md §3 FR-09 row 2 + §8 #10 / #11
# ---------------------------------------------------------------------------


def db_reachable(target=engine) -> tuple[bool, str]:
    """Run `SELECT 1` against `target`; return `(ok, detail)`.

    `ok=True` when the engine answers the trivial query; `ok=False`
    otherwise with `detail` naming the failure. The exception class
    name is included (safe) but the exception args / traceback are
    NOT — NFR-04 forbids internal detail leakage.
    """
    try:
        with target.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "database reachable"
    except Exception as exc:  # noqa: BLE001 — readiness probe must never raise
        return False, f"database unreachable: {type(exc).__name__}"


def alembic_current_revision(target=engine) -> Optional[str]:
    """Return the live alembic revision observed on `target` (or `None`)."""
    try:
        with target.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    except Exception:  # noqa: BLE001 — readiness probe must never raise
        return None


def alembic_head_revision(cfg_path) -> Optional[str]:
    """Return the alembic script-directory head revision (or `None`)."""
    try:
        cfg = Config(str(cfg_path))
        script = ScriptDirectory.from_config(cfg)
        return script.get_current_head()
    except Exception:  # noqa: BLE001 — readiness probe must never raise
        return None


# ---------------------------------------------------------------------------
# Metrics collectors — SPEC.md §3 FR-09 row 3
# ---------------------------------------------------------------------------


def task_counts_by_status() -> dict[str, int]:
    """Return `{status: count}` for every persisted task row."""
    counts: dict[str, int] = {}
    with transaction() as session:
        rows = session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        ).all()
    for status_value, count in rows:
        counts[str(status_value)] = int(count)
    return counts


def task_result_durations_ms() -> list[int]:
    """Return `duration_ms` for every `task_results` row (raw, unsorted).

    The percentile helper sorts in-place; this function leaves the
    ordering to the caller so the repository seam can be tested
    without coupling to a specific ordering decision.
    """
    with transaction() as session:
        durations = [
            int(row[0])
            for row in session.execute(select(TaskResult.duration_ms)).all()
        ]
    return durations


__all__ = [
    "db_reachable",
    "alembic_current_revision",
    "alembic_head_revision",
    "task_counts_by_status",
    "task_result_durations_ms",
]
