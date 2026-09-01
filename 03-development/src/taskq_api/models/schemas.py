"""Pydantic schemas for FR-01 task CRUD API.

[FR-01] Request and response models consumed by `taskq_api.api.tasks`.
The validators here are the architectural enforcement of the SPEC.md §3
FR-01 "validation rules" paragraph — non-empty command, ≤1000 char limit,
injection-character blacklist, name uniqueness. A violation here surfaces
as a 422 problem+json response via the handler registered in
`taskq_api.errors.install_error_handlers` (AC-1.2).

`ListTasksQuery` enforces AC-1.5 (limit ≤ 200) and AC-1.6 (cursor only —
`offset` is rejected at the api layer because Pydantic has no built-in
not-supported validator for query params).
Citations:
  SPEC.md §3 FR-01 (validation rules / list endpoint)
  SPEC.md §7 status map (422/404/409)
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# pragma: no error-handling


# SPEC.md §3 FR-01 "injection-character blacklist" — characters that
# would let a task command escape into shell metacharacter territory.
# Kept here so both the validator and the README can reference one source.
_INJECTION_BLACKLIST: frozenset[str] = frozenset(
    list(";|&$`<>(){}[]!\\\"'\n\r")
)


class TaskCreate(BaseModel):
    """POST /v1/tasks request body (AC-1.1 / AC-1.2).

    Fields:
      command — non-empty, ≤1000 chars, no injection metacharacters.
      name    — non-empty, unique (enforced at repository layer AC-1.4).
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., max_length=1000)
    name: str = Field(..., min_length=1)

    @field_validator("command")
    @classmethod
    def _command_nonempty_and_safe(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("command must be non-empty")
        for ch in _INJECTION_BLACKLIST:
            if ch in value:
                raise ValueError(f"command contains forbidden character: {ch!r}")
        return value

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must be non-empty")
        return value


class TaskRead(BaseModel):
    """GET /v1/tasks/{id} response and POST /v1/tasks success body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    command: str
    status: str = "pending"


class TaskListResponse(BaseModel):
    """GET /v1/tasks list response (AC-1.6)."""

    model_config = ConfigDict(extra="forbid")

    items: list[TaskRead]
    next_cursor: Optional[str] = None


class ListTasksQuery(BaseModel):
    """Query parameter shape for GET /v1/tasks.

    `limit` is bounded [1, 200] so that Pydantic raises a 422 for any
    value above 200 (AC-1.5). `offset` is intentionally NOT a field here
    so the api layer can reject it explicitly with a 400 (AC-1.6).
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(50, ge=1, le=200)
    cursor: Optional[str] = None
    status: Optional[str] = None
