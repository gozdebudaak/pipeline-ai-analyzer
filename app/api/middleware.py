"""HTTP middleware: cross-cutting behaviour applied to every request."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.logging import correlation_id_var
from app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = logging.getLogger("app.request")


async def correlation_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:

    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    correlation_id = incoming if _VALID_REQUEST_ID.match(incoming) else str(uuid.uuid4())
    token = correlation_id_var.set(correlation_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        correlation_id_var.reset(token)

    response.headers[REQUEST_ID_HEADER] = correlation_id
    logger.info(
        "request completed",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


def _route_template(request: Request) -> str:
    """The matched route's template (``/api/v1/analyze``), never the raw URL.

    Raw paths are user input: ``/nope-1``, ``/nope-2`` ... would each become a new
    time series and grow memory without bound (label cardinality explosion).
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    path = _route_template(request)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_REQUEST_DURATION.labels(request.method, path).observe(time.perf_counter() - started)
    return response
