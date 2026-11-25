from __future__ import annotations

import logging
from contextlib import aclosing

from app.core.providers.gemini_models import GEMINI_NEON_MODEL
from app.db.models import Account
from app.modules.proxy.gemini_code_assist import CodeAssistGeminiUpstream
from app.modules.proxy.gemini_upstream import GeminiUpstreamCall, GeminiUpstreamError

logger = logging.getLogger(__name__)

GEMINI_VALIDATION_CODE = "gemini_validation_required"
_CLEARED_KINDS = frozenset({"rate_limit", "quota"})


def validation_failure_code(url: str | None) -> str:
    return f"{GEMINI_VALIDATION_CODE} {url}" if url else GEMINI_VALIDATION_CODE


def needs_validation(reason: str | None) -> bool:
    return reason is not None and GEMINI_VALIDATION_CODE in reason


async def validation_cleared(account: Account, access_token: str) -> bool:
    call = GeminiUpstreamCall(
        model=GEMINI_NEON_MODEL,
        effort="low",
        body={"contents": [{"role": "user", "parts": [{"text": "ping"}]}], "generationConfig": {"maxOutputTokens": 1}},
        access_token=access_token,
        project_id=account.workspace_id,
        account_id=account.id,
    )
    try:
        async with aclosing(CodeAssistGeminiUpstream().stream(call)) as stream:
            async for _ in stream:
                break
    except GeminiUpstreamError as exc:
        if exc.kind == "validation_required":
            logger.error(
                "gemini_account_validation_pending alert=true account_id=%s validation_url=%s",
                account.id,
                exc.validation_url,
            )
            return False
        if exc.kind not in _CLEARED_KINDS:
            logger.warning("gemini_validation_probe_inconclusive account_id=%s kind=%s", account.id, exc.kind)
            return False
    logger.info("gemini_account_validation_cleared account_id=%s", account.id)
    return True
