"""Project-root pytest configuration.

[FR-01] Inject `pytest` into Python builtins for the test session so
test modules that reference `@pytest.fixture` without an explicit
`import pytest` line still collect cleanly. This is purely a
discovery-time convenience; nothing about test semantics changes —
the same fixtures and assertions run either way.
"""
import builtins

import pytest as _pytest

builtins.pytest = _pytest
