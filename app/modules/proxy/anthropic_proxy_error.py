from __future__ import annotations

from typing import Any

from app.core.anthropic.identity import CLAUDE_CODE_ONLY_MESSAGE, is_claude_code_client


class AnthropicProxyError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str = "anthropic_proxy_error",
        retry_at: int | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_at = retry_at
        self.details = details


def require_claude_code_client(useragent_group: str | None) -> None:
    if not is_claude_code_client(useragent_group):
        raise AnthropicProxyError(403, CLAUDE_CODE_ONLY_MESSAGE, code="claude_code_client_required")
