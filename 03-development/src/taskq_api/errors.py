"""RFC 7807 problem+json exceptions and handlers (SPEC.md §3 FR-10).

[FR-01] All non-2xx responses MUST serialize as `application/problem+json`
per SPEC.md §3 FR-10 and §7 status map. The handlers registered here cover:
  * ProblemException subclasses raised by api/service layers (404/409/401/403)
  * FastAPI's RequestValidationError (422) — re-rendered so the content-type
    matches FR-10 instead of the framework default of application/json.

NFR-02 requires error bodies NOT to leak internal detail; every handler
here emits a fixed-shape envelope with only the user-safe `detail` text.
Citations: SPEC.md §3 FR-10, SPEC.md §7 status map, NFR-02.
"""
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ProblemException(Exception):
    """Base for RFC 7807 error envelope (FR-10).

    Subclasses carry the status code + title; detail is supplied by the
    raise site. The type URI defaults to `about:blank` (RFC 7807 §4.2).
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    title: str = "Internal Server Error"
    type_uri: str = "about:blank"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail or _DEFAULT_DETAIL.get(self.status_code, "")
        super().__init__(self.detail)


class UnauthorizedError(ProblemException):
    status_code = status.HTTP_401_UNAUTHORIZED
    title = "Unauthorized"
    type_uri = "about:blank"


class ForbiddenError(ProblemException):
    status_code = status.HTTP_403_FORBIDDEN
    title = "Forbidden"
    type_uri = "about:blank"


class NotFoundError(ProblemException):
    status_code = status.HTTP_404_NOT_FOUND
    title = "Not Found"
    type_uri = "about:blank"


class ConflictError(ProblemException):
    status_code = status.HTTP_409_CONFLICT
    title = "Conflict"
    type_uri = "about:blank"


class BadRequestError(ProblemException):
    status_code = status.HTTP_400_BAD_REQUEST
    title = "Bad Request"
    type_uri = "about:blank"


_DEFAULT_DETAIL: dict[int, str] = {
    400: "bad request",
    401: "missing or invalid api key",
    403: "insufficient scope",
    404: "resource not found",
    409: "resource conflict",
    422: "request failed validation",
}


def _problem_response(
    status_code: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
) -> JSONResponse:
    """Build an `application/problem+json` response (FR-10 body shape)."""
    body: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": "",
    }
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register FR-10 handlers on the given FastAPI app."""

    @app.exception_handler(ProblemException)
    async def _problem_handler(_request: Request, exc: ProblemException) -> JSONResponse:
        return _problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            type_uri=exc.type_uri,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FR-10 / AC-1.2: validation errors MUST serialize as problem+json.
        # We deliberately do NOT echo the raw pydantic errors[] in `detail`
        # to honour NFR-02 (no internal detail leakage).
        return _problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation Error",
            detail="request body or parameters failed validation",
            type_uri="about:blank",
        )
