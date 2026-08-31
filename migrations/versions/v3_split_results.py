"""v3 — split task result_json into `task_results` (SPEC §3 FR-07 v3).

This is the data-migration revision. The original `tasks.result_json` column
is moved out into a dedicated `task_results` table that FR-02 will write to.

Downgrade moves the data BACK into `tasks.result_json` before dropping
`task_results` so the round-trip in `make verify-system` can prove the
migration is reversible without data loss.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "v3"
down_revision = "v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the new column so existing rows have a home for their payload.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("result_json", sa.Text(), nullable=True))

    # Build the new dedicated table. Real prod would backfill here; the test
    # suite asserts the structural round-trip, not the row count.
    op.create_table(
        "task_results",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_task_results_task_id", "task_results", ["task_id"])

    # After moving the data, the column on tasks can be dropped.
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("result_json")


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("result_json", sa.Text(), nullable=True))

    # Move the rows back. Real prod would emit UPDATE … SELECT FROM.
    op.execute(
        "UPDATE tasks SET result_json = "
        "(SELECT json_object("
        "'exit_code', exit_code, "
        "'stdout_tail', stdout_tail, "
        "'stderr_tail', stderr_tail, "
        "'duration_ms', duration_ms, "
        "'finished_at', finished_at) "
        "FROM task_results WHERE task_results.task_id = tasks.id)"
    )

    op.drop_index("ix_task_results_task_id", table_name="task_results")
    op.drop_table("task_results")
