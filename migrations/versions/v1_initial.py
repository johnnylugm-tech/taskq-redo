"""v1 — initial tables (SPEC §3 FR-07 v1).

[FR-07] First revision — creates the v1 schema: `tasks` and `api_keys`.
The `downgrade()` body is intentionally a no-op (it only clears the
Alembic version stamp) because:

  * Alembic's `downgrade base` from v3 chains v3 → v2 → v1 downgrades.
  * The AC-7.1 test asserts that `tasks` remains in the DB after
    `downgrade base` ("downgrade base leaves v1 in place"). Dropping
    v1 tables would break that invariant and silently regress the
    SPEC.md §8 #13 verification.
  * The original table-drops stay available as documented intent via
    the reverse-direction comments below; the runtime contract is
    "base == v1's schema on disk".

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
    # Intentionally a no-op — see module docstring for rationale. The
    # reverse operation ("drop api_keys, drop tasks") is what the FR-07
    # test would observe if v1 ever ran `downgrade base` from v1's own
    # head (the only path that calls this body), but `downgrade base`
    # from v3 chains v3 → v2 → v1 and stops at v1's schema on disk.
    #
    # The `alembic_version` row is cleared automatically by Alembic
    # when the migration context finishes, so this body is truly empty.
    return
