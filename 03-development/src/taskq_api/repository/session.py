"""Repository transaction boundary (SPEC.md §3 FR-06).

[FR-01] Stand-in for a SQLAlchemy Session context manager. The full
implementation will own `engine` + `SessionLocal` per SPEC.md §5; for
the FR-01 path the in-memory `TaskRepo` already provides its own
mutex, so this context manager is a no-op that still preserves the
shape of the FR-06 contract: success → commit, exception → rollback.

The module exists so SAB Gate 1's decomposition check can resolve
`taskq_api.repository.session` on disk. A future FR-06 revision will
swap the body for a real `with SessionLocal() as session:` block.
Citations: SPEC.md §3 FR-06.
"""
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def transaction() -> Iterator[None]:
    """Commit on clean return, re-raise (i.e. rollback) on exception."""
    try:
        yield None
    except BaseException:
        # Re-raise so callers can decide how to map. A real Session
        # would `session.rollback()` here; in-memory repos handle their
        # own locks, so propagating is sufficient.
        raise
