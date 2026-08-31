"""v1 — initial tables (SPEC §3 FR-07 v1).

[FR-07] First revision — creates the v1 schema: `tasks` and `api_keys`.
The v1 schema IS the declared base state of the database, so the
`downgrade()` body is a no-op. Dropping the tables here would leave
the database below `base`, which the AC-7.1 round-trip test pins
against (it asserts `tasks` remains in the DB after `alembic
downgrade base`). The `alembic_version` row is cleared automatically
by Alembic when the migration context finishes, so no explicit
cleanup is needed.

Runtime contract:
  * `alembic upgrade head` (from a fresh DB) chains v1 → v2 → v3 and
    ends with the v3 schema on disk.
  * `alembic downgrade base` chains v3 → v2 → v1 and calls each
    `downgrade()` along the way; v3 and v2 unwind their additions,
    v1 leaves the v1 schema on disk because this body is a no-op.

Each revision in this directory MUST have a working `downgrade()` —
FR-07's three-step migration is verified by a round-trip in
`make verify-system`.

Citations:
  SPEC.md §3 FR-07 v1 row (create tasks + api_keys; downgrade base
    leaves v1 intact)
  SPEC.md §8 #13 (full upgrade head / downgrade base cycle)
  NFR-09 (testability — anti-skip clause forbids destructive shortcuts)
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
    # No-op: the v1 schema is the declared base state. Dropping the
    # tables here would leave the database below `base`, which the
    # AC-7.1 round-trip test pins against (it asserts `tasks` remains
    # in the DB after `alembic downgrade base`). The `alembic_version`
    # row is cleared automatically by Alembic when the migration
    # context finishes.
    pass
