from __future__ import annotations

from app.core.openai.requests import ResponsesCompactRequest
from app.db.session import get_background_session
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy.constants import logger


async def release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
    if reservation is None:
        return
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)


async def finalize_image_reservation(
    reservation: ApiKeyUsageReservationData | None,
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
) -> None:

    if reservation is None:
        return
    try:
        if not input_tokens and not output_tokens:
            await release_reservation(reservation)
            return
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                service_tier=None,
            )
    except Exception:
        logger.warning(
            "failed to finalize image reservation reservation_id=%s model=%s",
            reservation.reservation_id,
            model,
            exc_info=True,
        )


def effective_model_for_api_key(api_key: ApiKeyData | None, requested_model: str) -> str:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def effective_optional_model_for_api_key(api_key: ApiKeyData | None, requested_model: str | None) -> str | None:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def compact_request_service_tier(payload: ResponsesCompactRequest) -> str | None:
    value = payload.service_tier
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
