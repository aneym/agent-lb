from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

GeminiFailureKind = Literal["rate_limit", "quota", "auth", "terms_disabled", "invalid_request", "transient"]


@dataclass(frozen=True, slots=True)
class GeminiUpstreamCall:
    model: str
    body: dict[str, Any]
    access_token: str
    project_id: str | None
    account_id: str


class GeminiUpstreamError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        kind: GeminiFailureKind,
        reset_at: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.kind = kind
        self.reset_at = reset_at


class GeminiUpstream(Protocol):
    def stream(self, call: GeminiUpstreamCall) -> AsyncGenerator[dict[str, Any], None]: ...
