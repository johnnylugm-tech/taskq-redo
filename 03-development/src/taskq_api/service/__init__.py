"""taskq_api.service — business-logic layer.

[FR-01] Service modules orchestrate repository calls and translate
repository exceptions into the service-layer exceptions consumed by
the api layer. Per SPEC.md §3 FR-06 the service layer MUST NOT hold
a Session directly; it operates against `TaskRepo` (and friends).
Citations: SPEC.md §3 FR-06.
"""
