from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.identity import attach_member, get_request_identity
from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.core.utils.request_id import get_request_id
from app.db.models import Account
from app.db.session import get_background_session
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy.gemini_stream import GeminiUsage
from app.modules.request_logs.repository import RequestLogsRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GeminiLogEntry:
    api_key: ApiKeyData | None
    account: Account | None
    model: str
    reasoning_effort: str | None
    useragent: str | None
    usage: GeminiUsage | None
    latency_ms: int
    latency_first_token_ms: int | None
    status: str
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    upstream_status_code: int | None = None


async def record_gemini_request(entry: GeminiLogEntry) -> None:
    request_id = get_request_id() or ""
    try:
        identity = await attach_member(get_request_identity(), entry.api_key.member_id if entry.api_key else None)
        async with get_background_session() as session:
            await RequestLogsRepository(session).add_log(
                identity=identity,
                account_id=entry.account.id if entry.account else None,
                api_key_id=entry.api_key.id if entry.api_key else None,
                request_id=request_id,
                model=entry.model,
                reasoning_effort=entry.reasoning_effort,
                input_tokens=entry.usage.input_tokens if entry.usage else None,
                output_tokens=entry.usage.output_tokens if entry.usage else None,
                cached_input_tokens=entry.usage.cached_input_tokens if entry.usage else None,
                reasoning_tokens=entry.usage.reasoning_tokens if entry.usage else None,
                latency_ms=entry.latency_ms,
                latency_first_token_ms=entry.latency_first_token_ms,
                status=entry.status,
                error_code=entry.error_code,
                error_message=entry.error_message,
                upstream_status_code=entry.upstream_status_code,
                plan_type=entry.account.plan_type if entry.account else None,
                provider=GEMINI_PROVIDER_NAME,
                transport="http",
                useragent=entry.useragent,
                session_id=entry.session_id,
                client_session_id=entry.session_id,
            )
    except Exception:
        logger.warning("Failed to persist Gemini request log request_id=%s", request_id, exc_info=True)


async def settle_gemini_reservation(
    reservation: ApiKeyUsageReservationData | None, *, model: str, usage: GeminiUsage | None
) -> None:
    if reservation is None:
        return
    try:
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            if usage is None or (usage.input_tokens == 0 and usage.output_tokens == 0):
                await service.release_usage_reservation(reservation.reservation_id)
                return
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                service_tier=None,
            )
    except Exception:
        logger.warning(
            "Failed to settle Gemini key reservation reservation_id=%s", reservation.reservation_id, exc_info=True
        )
