"""taskq_api.repository — task/key/rate data-access layer.

[FR-01] Repository implementations live here. The api/service layers
must not import SQLAlchemy directly (SPEC.md §3 NFR-06); they call
through this package.
Citations: SPEC.md §3 FR-06, NFR-06.
"""
