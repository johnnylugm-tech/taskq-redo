"""Alembic environment for FR-07 (SPEC.md §3 FR-07 — three revisions).

[FR-07] Alembic entry-point. The URL is read from the `-x db_url=...`
override that the integration test harness passes on the CLI; absent
that, the `sqlalchemy.url` in `alembic.ini` is used (development
default). Wiring the engine here keeps the SQLAlchemy import surface
narrow — only `migrations/env.py` and `taskq_api.repository.session`
touch `sqlalchemy.engine`, satisfying NFR-06 layering from inside the
migration runner as well as the application.

Online mode (`alembic upgrade/downgrade`) is the only mode the FR-07
tests exercise; offline SQL emission is left as a no-op so a stray
`alembic upgrade --sql` does not silently connect to the file URL.

Citations:
  SPEC.md §3 FR-07 (three revisions, each with a working downgrade)
  SPEC.md §5.2 (database schema is defined by Alembic revisions)
  NFR-06 (architecture — narrow sqlalchemy import surface)
"""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context


# --- Alembic config object (provides access to alembic.ini entries) -----

config = context.config


# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# We do not wire autogenerate to an ORM `MetaData` — every revision is
# written by hand per FR-07's hand-authored migration contract.
target_metadata = None


# --- URL resolution ------------------------------------------------------
# FR-07 tests override the database URL via `-x db_url=<url>` on the
# alembic CLI so each test gets a fresh, isolated SQLite file under
# pytest's `tmp_path`. The override is read once here and pushed into
# the `sqlalchemy.url` that `engine_from_config` consumes below.

def _resolve_url() -> str:
    """Return the database URL — `-x db_url=` override wins over `alembic.ini`."""
    x_args = context.get_x_argument(as_dictionary=True) or {}
    override = x_args.get("db_url")
    if override:
        return override
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    # Last resort: a sentinel the harness never reaches because the
    # FR-07 test always supplies `-x db_url=`. This branch keeps the
    # file importable from callers that omit both.
    return "sqlite:///:memory:"


config.set_main_option("sqlalchemy.url", _resolve_url())


# --- Migration runners ---------------------------------------------------

def run_migrations_offline() -> None:
    """Emit SQL to stdout (no DB connection) — unused by FR-07, defined for completeness."""
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url must be set")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the DB and run all pending revisions — the FR-07 hot path."""
    cfg_section = config.get_section(config.config_ini_section, {}) or {}
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
