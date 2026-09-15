"""Maps domain exceptions to standard JSON error responses."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.exceptions import (
    HTTPException,
)  # base of fastapi.HTTPException; also raised for 404s

from app.core.logging import correlation_id_var
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError

logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "request_id": correlation_id_var.get()}
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable(request: Request, exc: LLMUnavailableError) -> JSONResponse:
        logger.error("llm unavailable", extra={"reason": str(exc)})
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "llm_unavailable", str(exc))

    @app.exception_handler(LLMInvalidResponseError)
    async def llm_invalid(request: Request, exc: LLMInvalidResponseError) -> JSONResponse:
        logger.error("llm invalid response", extra={"reason": str(exc)})
        return _error(status.HTTP_502_BAD_GATEWAY, "llm_invalid_response", str(exc))

    @app.exception_handler(HTTPException)
    async def http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        # Keep FastAPI's own errors (404, 503 from dependencies...) in the same envelope.
        if isinstance(exc.detail, dict):
            return _error(
                exc.status_code,
                str(exc.detail.get("code", "error")),
                str(exc.detail.get("message", "")),
            )
        return _error(exc.status_code, "http_error", str(exc.detail))
