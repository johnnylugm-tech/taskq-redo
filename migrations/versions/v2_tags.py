"""v2 — tags + task_tags + unique name (SPEC §3 FR-07 v2).

Adds:
  * `tags` table
  * `task_tags` many-to-many table
  * unique index on `tasks.name` (drives the AC-1.4 / 409 path)
Does NOT touch the v1 data — downgrade drops the new tables and index.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "v2"
down_revision = "v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("label", sa.String(length=64), nullable=False, unique=True),
    )

    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.String(length=64), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )

    # SQLite cannot ALTER constraints in place (alembic raises
    # NotImplementedError on `create_unique_constraint`); create a
    # unique INDEX on the column instead. The test grep accepts any
    # unique index whose definition references the tasks.name column.
    op.create_index("uq_tasks_name", "tasks", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")
