from __future__ import annotations

import json
import logging
import math
import time
from datetime import timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError, TeamMemberOverCapError
from app.core.runtime_logging import log_error_response

logger = logging.getLogger(__name__)

_GOOGLE_STATUS = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "ABORTED",
    413: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    499: "CANCELLED",
    500: "INTERNAL",
    501: "UNIMPLEMENTED",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


class GeminiGatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str,
        retry_at: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_at = retry_at
        self.headers = headers or {}


def google_status(status_code: int) -> str:
    if status_code in _GOOGLE_STATUS:
        return _GOOGLE_STATUS[status_code]
    return "INTERNAL" if status_code >= 500 else "FAILED_PRECONDITION"


def gemini_error_body(status_code: int, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "code": status_code,
            "message": message,
            "status": google_status(status_code),
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": code,
                    "domain": "agent-lb",
                }
            ],
        }
    }


def gemini_error_sse_event(error: GeminiGatewayError) -> bytes:
    body = gemini_error_body(error.status_code, error.code, error.message)
    return b"data: " + json.dumps(body, separators=(",", ":")).encode() + b"\r\n\r\n"


def from_app_error(exc: AppError) -> GeminiGatewayError:
    retry_at = None
    if isinstance(exc, TeamMemberOverCapError):
        reset_at = exc.reset_at if exc.reset_at.tzinfo is not None else exc.reset_at.replace(tzinfo=timezone.utc)
        retry_at = reset_at.timestamp()
    headers = getattr(exc, "headers", None)
    return GeminiGatewayError(
        exc.status_code,
        exc.message,
        code=exc.code,
        retry_at=retry_at,
        headers=dict(headers) if isinstance(headers, dict) else None,
    )


def gemini_error_response(request: Request, error: GeminiGatewayError) -> JSONResponse:
    log_error_response(logger, request, error.status_code, error.code, error.message, category="gemini_error_response")
    headers = dict(error.headers)
    if error.retry_at is not None and error.status_code in (429, 503):
        headers["Retry-After"] = str(max(1, math.ceil(error.retry_at - time.time())))
    return JSONResponse(
        status_code=error.status_code,
        content=gemini_error_body(error.status_code, error.code, error.message),
        headers=headers or None,
    )
