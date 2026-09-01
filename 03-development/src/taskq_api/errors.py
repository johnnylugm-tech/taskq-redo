"""RFC 7807 problem+json exceptions and handlers (SPEC.md §3 FR-10).

[FR-01] All non-2xx responses MUST serialize as `application/problem+json`
per SPEC.md §3 FR-10 and §7 status map. The handlers registered here cover:
  * ProblemException subclasses raised by api/service layers (404/409/401/403)
  * FastAPI's RequestValidationError (422) — re-rendered so the content-type
    matches FR-10 instead of the framework default of application/json.
  * Unhandled `Exception` (500) — sanitised envelope with no internals
    leaked in the body (NFR-02 / AC-10.3).

[FR-05] `TooManyRequestsError` carries the integer `Retry-After` second
count required by SPEC.md §3 FR-05 paragraph 1. The handler reads it
off the exception and copies it into the response headers so the
client can back off appropriately.

[FR-10] The `CorrelationIdMiddleware` (also installed here) issues a
fresh UUID4 per request — or honours an inbound `X-Correlation-Id` —
stamps `request.state.correlation_id`, echoes the id on the response
header, and emits a log line carrying the same id so the response and
the server log can be joined on the id alone (AC-10.4 / SPEC.md §3
FR-10 paragraph 1).

NFR-02 requires error bodies NOT to leak internal detail; every handler
here emits a fixed-shape envelope with only the user-safe `detail` text.

Citations:
  SPEC.md §3 FR-10 (whole section)
  SPEC.md §7 status map
  SPEC.md §3 FR-05 (429 + Retry-After)
  SPEC.md §3 FR-10 paragraph 1 (correlation_id linkage)
  NFR-02 (no internal detail leakage)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


logger = logging.getLogger("taskq_api")


# [FR-10] correlation_id header name. Single source of truth so the
# middleware, exception handlers, and tests agree on the same casing.
CORRELATION_ID_HEADER = "X-Correlation-Id"


class CorrelationIdMiddleware:
    """[FR-10] Issue per-request correlation_id (AC-10.4).

    Pure ASGI middleware (NOT `BaseHTTPMiddleware`) — `BaseHTTPMiddleware`
    wraps `call_next` in an `anyio` task group which RE-RAISES the
    raised exception back out of the ASGI app, bypassing FastAPI's
    exception-handler dispatch. That would mean the 500 path bypasses
    our generic-exception handler and the raw `RuntimeError` reaches
    Starlette's default 500 response (plain `application/json`,
    missing `X-Correlation-Id`). Implementing as a raw ASGI middleware
    lets exceptions propagate to FastAPI's handlers normally; we only
    intercept the `send` channel to stamp the response header.

    On every request:
      1. Reads `X-Correlation-Id` from the request (or generates a
         fresh UUID4 if absent),
      2. Stores the id on `scope["state"]["correlation_id"]` so
         handlers + exception handlers can pick it up,
      3. Echoes the id on the response via the `X-Correlation-Id`
         header.

    Citations:
      SPEC.md §3 FR-10 paragraph 1 (correlation_id linkage)
      NFR-02 (no internal detail leakage)
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Pick up inbound X-Correlation-Id or mint a fresh UUID4. Store
        # on `scope["state"]` so `request.state.correlation_id` resolves
        # to the same id downstream (FastAPI populates `request.state`
        # from `scope["state"]`).
        cid = _extract_inbound_correlation_id(scope.get("headers", [])) or str(uuid.uuid4())
        scope.setdefault("state", {})["correlation_id"] = cid

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # Append the X-Correlation-Id header on the outgoing
                # response ONLY if the response does not already carry
                # one — exception handlers built by `install_error_handlers`
                # stamp the header directly on their returned
                # JSONResponse, and the raw ASGI dispatch order would
                # otherwise yield duplicate `, `-joined headers.
                raw_headers = list(message.get("headers", []))
                key = CORRELATION_ID_HEADER.lower().encode()
                if not any(
                    raw_name == key for raw_name, _ in raw_headers
                ):
                    raw_headers.append((key, cid.encode("latin-1")))
                message["headers"] = raw_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _extract_inbound_correlation_id(headers: list[tuple[bytes, bytes]]) -> Optional[str]:
    """Return the inbound X-Correlation-Id header value, or None.

    Decoded as `latin-1` because HTTP/1.1 headers are byte-oriented
    but Starlette stores them as `bytes`; `latin-1` is the standard
    1:1 byte→str mapping for header values and round-trips losslessly
    when re-encoded on the response side. Latin-1 can decode ANY byte
    sequence, so a `try/except UnicodeDecodeError` wrapper would be
    pure dead code (a Python library guarantee). Returns `None` for
    a missing header so the caller can mint a fresh UUID4.
    """
    key = CORRELATION_ID_HEADER.lower().encode()
    for raw_name, raw_value in headers:
        if raw_name.lower() == key:
            return raw_value.decode("latin-1")
    return None


class ProblemException(Exception):
    """Base for RFC 7807 error envelope (FR-10).

    Subclasses carry the status code + title; detail is supplied by the
    raise site. The type URI defaults to `about:blank` (RFC 7807 §4.2).
    `_default_detail` is the user-facing string the handler emits when
    the raise site omits one — co-located with `status_code` so each
    subclass owns its own error contract.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    title: str = "Internal Server Error"
    type_uri: str = "about:blank"
    _default_detail: str = "internal server error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail or self._default_detail
        super().__init__(self.detail)


class UnauthorizedError(ProblemException):
    status_code = status.HTTP_401_UNAUTHORIZED
    title = "Unauthorized"
    type_uri = "about:blank"
    _default_detail = "missing or invalid api key"


class ForbiddenError(ProblemException):
    status_code = status.HTTP_403_FORBIDDEN
    title = "Forbidden"
    type_uri = "about:blank"
    _default_detail = "insufficient scope"


class NotFoundError(ProblemException):
    status_code = status.HTTP_404_NOT_FOUND
    title = "Not Found"
    type_uri = "about:blank"
    _default_detail = "resource not found"


class ConflictError(ProblemException):
    status_code = status.HTTP_409_CONFLICT
    title = "Conflict"
    type_uri = "about:blank"
    _default_detail = "resource conflict"


class BadRequestError(ProblemException):
    status_code = status.HTTP_400_BAD_REQUEST
    title = "Bad Request"
    type_uri = "about:blank"
    _default_detail = "bad request"


class ServiceUnavailableError(ProblemException):
    """[FR-09] 503 — readiness-probe failure (DB down / migration behind head)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    title = "Service Unavailable"
    type_uri = "about:blank"
    _default_detail = "service unavailable"


# [FR-05] 429 — the body is the FR-10 problem+json envelope, the
# `Retry-After` header carries the integer second count.
class TooManyRequestsError(ProblemException):
    """[FR-05] Bucket exhausted — surfaces as 429 + problem+json + Retry-After."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    title = "Too Many Requests"
    type_uri = "about:blank"
    _default_detail = "rate limit exceeded"

    def __init__(
        self,
        detail: str = "",
        retry_after_seconds: int = 1,
    ) -> None:
        super().__init__(detail)
        # Always >= 1 — a 0-second Retry-After would be a "retry now"
        # hint and trigger an immediate retry storm against the same
        # exhausted bucket (SPEC.md §3 FR-05 paragraph 1).
        self.retry_after_seconds: int = max(1, int(retry_after_seconds))


def _correlation_id_for(request: Request) -> str:
    """Return the per-request correlation_id stamped by the middleware.

    Falls back to an empty string if the middleware has not run yet
    (defensive only — the `install_error_handlers` factory wires the
    middleware ahead of the router so every request carries an id).
    """
    return str(getattr(request.state, "correlation_id", "") or "")


def _forbidden_instance(path: str) -> str:
    """[FR-04 / NFR-02] Sanitised instance URI for 403 responses.

    A 403 body MUST NOT disclose whether the requested resource exists
    (SPEC.md §8 #6). The default `_problem_body` writes `request.url`
    verbatim, which leaks the resource id (the URL's last path segment)
    back to the caller. We redact the trailing path segment IFF it
    looks like a resource id (UUID/ULID/hex token); collection paths
    (`/v1/tasks`, `/v1/keys`) and bare health endpoints (`/healthz`)
    pass through unchanged so authorised verbs on a collection are
    not over-redacted.
    """
    import re as _re

    if not path or path == "/":
        return path or "/"
    base, _, last = path.rpartition("/")
    # A resource id is treated as the trailing segment when it
    # contains at least one dash (UUID4/ULID) OR is a long hex blob
    # (>=16 chars, all hex). Plain collection names (`tasks`, `keys`,
    # `runs`) and short verb tokens (`run`) do NOT match, so we
    # never over-redact a collection-level 403 (e.g. POST /v1/tasks
    # with insufficient scope).
    _ID_RE = _re.compile(r"^[0-9a-fA-F]{8,}-|^[0-9a-fA-F]{16,}$")
    if not last or not _ID_RE.match(last):
        return path
    # `base` always non-empty because `path` contains "/"; the
    # outer `or "/"` only fires for the impossible "no slash" case.
    return base or "/"


def _problem_body(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
    include_correlation_id: bool = True,
    instance: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Build the FR-10 problem+json body dict (AC-10.2).

    The body has EXACTLY the six FR-10 fields
    (`type`, `title`, `status`, `detail`, `instance`,
    `correlation_id`) — SPEC.md §3 FR-10 paragraph 1. Returns the
    body dict AND the correlation_id so callers can stamp the same
    id on the response header / log line without re-reading it from
    `request.state`.

    `correlation_id` is taken from `request.state.correlation_id` so
    the body is self-describing even when a proxy strips custom
    headers (AC-10.2). Callers pass `include_correlation_id=False`
    to drop the field from the body (the header still carries it)
    — used for 403 where per-request body data would defeat the
    NFR-02 "no resource-existence leak" guarantee by making two
    responses on different ids byte-distinguishable.

    `instance` defaults to the request URI per RFC 7807 §3.1; the
    403 handler overrides this with the sanitised path so the body
    is deterministic for any caller-supplied id.
    """
    correlation_id = _correlation_id_for(request)
    body: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance if instance is not None else str(request.url),
    }
    if include_correlation_id:
        body["correlation_id"] = correlation_id
    return body, correlation_id


def _problem_response(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    type_uri: str = "about:blank",
    include_correlation_id: bool = True,
    instance: Optional[str] = None,
) -> JSONResponse:
    """Wrap the FR-10 body in an `application/problem+json` JSONResponse.

    The `X-Correlation-Id` response header is stamped directly on the
    JSONResponse (AC-10.4). The middleware also stamps it on
    non-exception paths; setting it here covers the exception paths
    that bypass the middleware's send_wrapper, and the middleware's
    dedup logic prevents double-stamping when both fire.

    `include_correlation_id=False` skips the `correlation_id` field
    on the body (the header is still stamped) — used by the 403 path
    to make the body deterministic across different resource ids.
    `instance` overrides the default request URI in the body's
    `instance` field — used by the 403 path to redact the requested
    resource id.
    """
    body, correlation_id = _problem_body(
        request,
        status_code=status_code,
        title=title,
        detail=detail,
        type_uri=type_uri,
        include_correlation_id=include_correlation_id,
        instance=instance,
    )
    resp = JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )
    if correlation_id:
        resp.headers[CORRELATION_ID_HEADER] = correlation_id
    return resp


def install_error_handlers(app: FastAPI) -> None:
    """Register FR-10 handlers and correlation-id middleware on the app."""

    # [FR-10] Install the correlation-id middleware BEFORE any router
    # is mounted. Order matters: middleware added later wraps earlier
    # ones, so a router mounted on `app` will see `request.state`
    # already populated by the time its deps run. The middleware is
    # idempotent across re-instantiations in the same process.
    app.add_middleware(CorrelationIdMiddleware)

    @app.exception_handler(ProblemException)
    async def _problem_handler(request: Request, exc: ProblemException) -> JSONResponse:
        cid = _correlation_id_for(request)
        # [FR-10 / AC-10.4] Emit a structured log line carrying the
        # same correlation_id that the response header + body will
        # carry. A log search by id can join this line to the response
        # by id alone — the FR-10 linkability contract.
        logger.warning(
            "fr10 problem response: status=%d correlation_id=%s title=%r",
            exc.status_code,
            cid,
            exc.title,
        )
        # [FR-04 / NFR-02] 403 bodies MUST be deterministic across
        # different resource ids: drop per-request correlation_id from
        # the body (the header still carries it for AC-10.4) and
        # replace `instance` with the sanitised path. Without this,
        # two 403s on different ids differ in both fields and the
        # discriminator is leaked (SPEC.md §8 #6).
        is_forbidden = exc.status_code == status.HTTP_403_FORBIDDEN
        resp = _problem_response(
            request=request,
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            type_uri=exc.type_uri,
            include_correlation_id=not is_forbidden,
            instance=_forbidden_instance(request.url.path) if is_forbidden else None,
        )
        # [FR-05] Copy Retry-After off the exception if present. The
        # attribute only exists on `TooManyRequestsError`; the
        # `getattr(..., None)` default keeps the base handler usable
        # for every other `ProblemException` subclass.
        retry_after: Optional[int] = getattr(exc, "retry_after_seconds", None)
        if retry_after is not None:
            resp.headers["Retry-After"] = str(retry_after)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FR-10 / AC-1.2: validation errors MUST serialize as problem+json.
        # We deliberately do NOT echo the raw pydantic errors[] in `detail`
        # to honour NFR-02 (no internal detail leakage).
        cid = _correlation_id_for(request)
        logger.warning(
            "fr10 validation rejection: correlation_id=%s", cid
        )
        return _problem_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation Error",
            detail="request body or parameters failed validation",
        )

    @app.exception_handler(Exception)
    async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # [FR-10 / AC-10.1 / AC-10.3] Generic-exception handler. The
        # default FastAPI handler returns plain `application/json` with
        # `{"detail": "Internal Server Error"}` which fails AC-10.1
        # (wrong content-type) and AC-10.3 (right shape but no
        # correlation_id, and the body shape is missing 5 of the 6
        # FR-10 fields). We replace it with the FR-10 envelope using
        # a sanitised `detail` so the raised exception's message
        # (potentially carrying a traceback / SQL / file path) is
        # NEVER copied into the response body (NFR-02).
        cid = _correlation_id_for(request)
        # Log the actual exception server-side so operators can debug
        # by joining on the correlation_id. The client only ever sees
        # the generic `internal server error` string.
        logger.exception(
            "fr10 unhandled exception: correlation_id=%s", cid
        )
        return _problem_response(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail=ProblemException._default_detail,
        )


class SuppressServerExceptionReraise:
    """[FR-10] ASGI wrapper that suppresses the post-handler `raise exc`
    Starlette's `ServerErrorMiddleware` performs after sending the
    500 response (Starlette middleware/errors.py line ~190:
    "We always continue to raise the exception. ... allows test
    clients to optionally raise the error within the test case").

    With `raise_server_exceptions=True` (the Starlette `TestClient`
    default), the re-raise propagates through `TestClient.handle_request`
    and the test code sees a `RuntimeError` instead of the 500 response
    — which breaks AC-10.1's "every non-2xx response is
    application/problem+json" assertion. Wrapping the FastAPI instance
    in this ASGI app places us OUTSIDE `ServerErrorMiddleware`, so we
    catch the re-raise AFTER the response has already been written to
    the wire and simply discard the exception.

    Citations:
      SPEC.md §3 FR-10 paragraph 1 (every non-2xx is problem+json)
      starlette/middleware/errors.py ServerErrorMiddleware
    """

    def __init__(self, app) -> None:
        self.app = app

    def __getattr__(self, name: str):
        # Delegate attribute access (e.g. `app.router`, `app.routes`,
        # `app.state`, `app.dependency_overrides`) to the wrapped
        # FastAPI instance. This keeps the wrapper transparent for
        # in-process route introspection — tests that walk
        # `app.router.routes` to audit the dependency wiring do not
        # need to know the FastAPI app was wrapped, and Starlette's
        # `ServerErrorMiddleware` still sees the wrapper as the
        # outermost ASGI app so its post-handler `raise exc` is
        # caught by our `__call__` instead of propagating.
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        except Exception:
            # `ServerErrorMiddleware` has already written the 500
            # response by the time it raises. There is nothing left to
            # do — propagating the exception would only confuse the
            # test client (or any caller) into thinking no response was
            # produced. `Exception` (not `BaseException`) so genuine
            # interrupts (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
            # still propagate normally.
            return


def _redact_db_url_password(url: str) -> str:
    """[NFR-04] Redact password from a DB URL.

    Replaces the password segment in `user:password@host` style URLs with
    `***`. Returns the input unchanged if no password is present.

    Examples:
      `postgres://u:hunter2@db/x` → `postgres://u:***@db/x`
      `sqlite:///./foo.db` → `sqlite:///./foo.db`
    """
    import re
    if not url:
        return url
    # Match user:password@ pattern
    return re.sub(
        r"(://[^:]+:)[^@]+(@)",
        r"\1***\2",
        url,
    )
