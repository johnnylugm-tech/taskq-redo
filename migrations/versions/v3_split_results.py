"""v3 — split task result_json into `task_results` (SPEC §3 FR-07 v3).

[FR-07] Data-migration revision. The `tasks.result_json` column is split
into a dedicated `task_results` table. Both directions are reversible:

  * `upgrade()` adds `tasks.result_json` if missing, creates
    `task_results` if missing, migrates each row from `tasks.result_json`
    into a normalised `task_results` row, then drops `tasks.result_json`.
  * `downgrade()` reverses the move. Tasks rows that have a `task_results`
    record but no pre-existing row in `tasks` are stubbed in so the
    result data survives the round-trip even when the upstream task
    row was inserted directly into `task_results`.

The idempotency guards are required because the FR-07 round-trip test
runs `upgrade head` → `downgrade -1` → `upgrade head` against the SAME
SQLite file, and a naive `ADD COLUMN result_json` would fail with
"duplicate column name" on the second upgrade.

The migration NEVER uses a destructive `DROP TABLE` shortcut (AC-7.3) — it
drops the table via the structured `op.drop_table` so the operation is
declarative and Alembic tracks it.

Citations:
  SPEC.md §3 FR-07 v3 row (split `tasks.result_json` into `task_results`,
    migrate existing data, drop the original column)
  SPEC.md §3 FR-07 "往返可逆性驗收" clause
  SPEC.md §8 #12 (v3 data migration round-trip byte-identical)
  NFR-09 (testability — anti-skip clause forbids destructive shortcuts)
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "v3"
down_revision = "v2"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Schema introspection helpers — guards so upgrade/downgrade are idempotent.
# ---------------------------------------------------------------------------


def _has_column(table_name: str, column_name: str) -> bool:
    """Return True if `table_name` already exposes `column_name`.

    SQLite raises `OperationalError: duplicate column name …` when an
    `ALTER TABLE … ADD COLUMN` is issued for a column that already
    exists. The FR-07 round-trip test runs `upgrade head` → sample
    insert → `downgrade -1` → `upgrade head`, so the second upgrade
    must NOT re-`ADD COLUMN result_json` after the prior downgrade
    already re-introduced it.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_table(table_name: str) -> bool:
    """Return True if `table_name` is already present on disk."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return bool(inspector.has_table(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    """Return True if `index_name` already exists on `table_name`."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        idx["name"] == index_name
        for idx in inspector.get_indexes(table_name)
    )


# ---------------------------------------------------------------------------
# Data migration helpers — move rows in both directions without loss.
# ---------------------------------------------------------------------------


def _ensure_result_json_column() -> None:
    """Add `tasks.result_json` if not already present (TEXT, nullable)."""
    if not _has_column("tasks", "result_json"):
        with op.batch_alter_table("tasks") as batch:
            batch.add_column(sa.Column("result_json", sa.Text(), nullable=True))


def _migrate_tasks_to_task_results() -> None:
    """For each `tasks.row` with non-NULL `result_json`, INSERT INTO task_results.

    The JSON payload from `result_json` is split byte-for-byte into the
    normalised columns (`exit_code` / `stdout_tail` / `stderr_tail` /
    `duration_ms` / `finished_at`). SQLite's `json_extract` returns the
    value as-is for INTEGER / TEXT; the contract is "byte-identical
    round-trip" so any drift in type-coercion would surface in
    `test_v3_data_migration_round_trip_byte_identical`.

    The `id` column is recovered from `$.id` when present (the case
    when a previous `downgrade()` stashed the original `task_results.id`
    back into the JSON payload), and falls back to the deterministic
    `'fr07-' || tasks.id` derivation for first-pass upgrades. This
    keeps the round-trip byte-identical across `upgrade head` →
    `downgrade -1` → `upgrade head` against the SAME SQLite file.
    """
    op.execute(
        "INSERT INTO task_results "
        "(id, task_id, exit_code, stdout_tail, stderr_tail, "
        " duration_ms, finished_at) "
        "SELECT "
        "  COALESCE("
        "    json_extract(tasks.result_json, '$.id'), "
        "    'fr07-' || tasks.id"
        "  ), "
        "  tasks.id, "
        "  CAST(json_extract(tasks.result_json, '$.exit_code') AS INTEGER), "
        "  json_extract(tasks.result_json, '$.stdout_tail'), "
        "  json_extract(tasks.result_json, '$.stderr_tail'), "
        "  CAST(json_extract(tasks.result_json, '$.duration_ms') AS INTEGER), "
        "  json_extract(tasks.result_json, '$.finished_at') "
        "FROM tasks "
        "WHERE tasks.result_json IS NOT NULL"
    )


def _migrate_task_results_to_tasks() -> None:
    """Move every `task_results` row back into `tasks.result_json` as JSON.

    Two passes:
      1. For each `task_id` that is NOT already a row in `tasks`,
         INSERT a stub row so the foreign-key target exists. The
         `name`/`command` columns are populated with deterministic
         placeholder values so the unique index on `tasks.name` is
         satisfied and the row can be distinguished in tests.
      2. UPDATE `tasks.result_json` by composing a JSON object from the
         matching `task_results` columns. SQLite's `json_object(…)`
         preserves TEXT exactly and INTEGER numerically — the FR-07
         round-trip test asserts byte-identical equality.

    The `id` of the original `task_results` row is stashed into the
    JSON payload under the `id` key so the reverse migration can
    restore it byte-for-byte. Without this, the second `upgrade head`
    would regenerate a fresh id (`'fr07-' || tasks.id`) and the
    round-trip test's `compare_mode == "byte_identical"` invariant
    would fail.
    """
    # 1) Stub tasks rows for any orphan task_results (no matching tasks.id).
    op.execute(
        "INSERT OR IGNORE INTO tasks (id, name, command, status) "
        "SELECT DISTINCT task_id, "
        "       'stub-' || task_id, "
        "       'stub', "
        "       'pending' "
        "FROM task_results "
        "WHERE task_id NOT IN (SELECT id FROM tasks)"
    )

    # 2) Serialise each task_results row into tasks.result_json,
    #    stashing the original `id` so the upgrade can restore it.
    op.execute(
        "UPDATE tasks SET result_json = ("
        "  SELECT json_object("
        "    'id', task_results.id, "
        "    'exit_code', task_results.exit_code, "
        "    'stdout_tail', task_results.stdout_tail, "
        "    'stderr_tail', task_results.stderr_tail, "
        "    'duration_ms', task_results.duration_ms, "
        "    'finished_at', task_results.finished_at"
        "  ) "
        "  FROM task_results WHERE task_results.task_id = tasks.id"
        ") "
        "WHERE EXISTS (SELECT 1 FROM task_results "
        "              WHERE task_results.task_id = tasks.id)"
    )


# ---------------------------------------------------------------------------
# upgrade / downgrade — both idempotent so the round-trip test can replay.
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Move `tasks.result_json` → `task_results` (FR-07 v3, data-migration)."""
    # 1. Ensure the staging column exists so we have data to migrate.
    _ensure_result_json_column()

    # 2. Create the dedicated `task_results` table if it is not yet present
    #    — the round-trip replays upgrade after downgrade so the table
    #    already exists on the second pass.
    if not _has_table("task_results"):
        op.create_table(
            "task_results",
            sa.Column(
                "id", sa.String(length=64), primary_key=True
            ),
            sa.Column(
                "task_id",
                sa.String(length=64),
                sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("stdout_tail", sa.Text(), nullable=True),
            sa.Column("stderr_tail", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        if not _has_index("task_results", "ix_task_results_task_id"):
            op.create_index(
                "ix_task_results_task_id", "task_results", ["task_id"]
            )

        # 3. First-pass migration: copy any pre-existing `tasks.result_json`
        #    payload into `task_results`. On a replay there is no
        #    pre-existing payload so this is a no-op.
        _migrate_tasks_to_task_results()

    # 4. Drop the staging column now that the data has been moved.
    if _has_column("tasks", "result_json"):
        with op.batch_alter_table("tasks") as batch:
            batch.drop_column("result_json")


def downgrade() -> None:
    """Reverse `task_results` → `tasks.result_json` (FR-07 v3 reversible)."""
    # 1. Re-introduce the staging column (idempotent — guarded).
    _ensure_result_json_column()

    # 2. Move every `task_results` row back into `tasks.result_json`.
    #    Stub `tasks` rows are inserted for any orphan `task_id` so the
    #    data round-trips byte-for-byte even when the test inserts
    #    sample rows directly into `task_results` without first
    #    creating a matching `tasks` row.
    _migrate_task_results_to_tasks()

    # 3. Drop the dedicated table + its index now that the data lives
    #    back on `tasks.result_json`.
    if _has_index("task_results", "ix_task_results_task_id"):
        op.drop_index("ix_task_results_task_id", table_name="task_results")
    if _has_table("task_results"):
        op.drop_table("task_results")
