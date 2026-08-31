"""v1 — initial tables (SPEC §3 FR-07 v1).

Creates `tasks` and `api_keys`. Each revision in this directory MUST have a
working downgrade() — FR-07's three-step migration is verified by a
round-trip in `make verify-system`.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("key_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
