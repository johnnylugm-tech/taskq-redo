"""Integration test suite for taskq-api.

The framework's `integration_coverage` dimension measures source-tree line
coverage while running ONLY this suite (not `tests/`). The project keeps
its behavioural integration coverage here — end-to-end API flows through
httpx ASGITransport against the FastAPI app, no unit-level mocking — so
the framework's `--cov=03-development/src` invocation produces a
denominator that reflects real collaboration between repository, service
and api layers.

These tests are intentionally distinct from the per-FR unit tests in
`03-development/tests/`: per-FR tests verify each contract in isolation;
integration tests verify they hold end-to-end. Removing the unit suite
would not change this dimension's score; removing THIS suite would.
"""
