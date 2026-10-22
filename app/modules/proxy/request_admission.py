from __future__ import annotations

from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError, ProxyModelNotAllowed, ProxyRateLimitError
from app.db.session import get_background_session
from app.dependencies import ProxyContext
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import (
    TRAFFIC_CLASS_OPPORTUNISTIC,
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeyUnpricedModelError,
    ApiKeyUsageReservationData,
)
from app.modules.proxy.constants import OPPORTUNISTIC_RETRY_AFTER_SECONDS
from app.modules.proxy.reservations import effective_optional_model_for_api_key
from app.modules.proxy.response_errors import logged_error_json_response
from app.modules.team.gate import check_member_gate
from app.modules.team.service import MEMBER_CONTACT_LINE


async def enforce_request_limits(
    api_key: ApiKeyData | None,
    *,
    request_model: str | None,
    request_service_tier: str | None,
    request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    request_is_file_operation: bool = False,
) -> ApiKeyUsageReservationData | None:
    if api_key is None:
        return None

    await check_member_gate(api_key, model=request_model)

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            return await service.enforce_limits_for_request(
                api_key.id,
                request_model=request_model,
                request_service_tier=request_service_tier,
                request_usage_budget=request_usage_budget,
                **({"request_is_file_operation": True} if request_is_file_operation else {}),
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise ProxyRateLimitError(message) from exc
        except ApiKeyUnpricedModelError as exc:
            raise ProxyModelNotAllowed(str(exc), code="model_unpriced_under_cost_cap") from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


async def opportunistic_admission_denial(
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    model: str | None,
    lease_kind: Literal["response_create", "stream"] | None = "stream",
) -> JSONResponse | None:
    if api_key is None or api_key.traffic_class != TRAFFIC_CLASS_OPPORTUNISTIC:
        return None
    selection = await context.service.check_opportunistic_admission(
        api_key=api_key,
        model=effective_optional_model_for_api_key(api_key, model),
        lease_kind=lease_kind,
    )
    if selection.account is not None:
        return None
    message = selection.error_message or "opportunistic burn window closed"
    if not message.startswith("opportunistic burn window closed"):
        message = f"opportunistic burn window closed: {message}"
    if getattr(api_key, "member_id", None):
        message = (
            "The shared accounts are busy right now, so this request was held back. "
            f"Try again in a minute. {MEMBER_CONTACT_LINE}"
        )
    return logged_error_json_response(
        request,
        429,
        openai_error("rate_limit_exceeded", message, error_type="rate_limit_error"),
        headers={"Retry-After": str(OPPORTUNISTIC_RETRY_AFTER_SECONDS)},
    )
