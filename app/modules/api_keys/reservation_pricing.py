from __future__ import annotations

import uuid
from math import ceil

from sqlalchemy.exc import OperationalError

from app.core.anthropic.models import AnthropicUsage
from app.core.anthropic.pricing import calculate_anthropic_cost_from_usage
from app.core.anthropic.pricing import get_list_pricing_for_known_model as get_anthropic_list_pricing
from app.core.anthropic.pricing import get_pricing_for_model as get_anthropic_pricing_for_model
from app.core.usage.pricing import DEFAULT_MODEL_ALIASES as OPENAI_MODEL_ALIASES
from app.core.usage.pricing import DEFAULT_PRICING_MODELS as OPENAI_PRICING_MODELS
from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model
from app.db.request_models import ApiKeyLimit, LimitType
from app.modules.api_keys.service_data import _API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_BASE_TOKENS, _API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_MICRODOLLARS, API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS, API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET, ApiKeyRateLimitExceededError, ApiKeyRequestUsageBudget  # noqa: I001  # fmt: skip


def _rate_limit_exceeded_error(limit: ApiKeyLimit) -> ApiKeyRateLimitExceededError:
    return ApiKeyRateLimitExceededError(
        message=f"API key {limit.limit_type.value} {limit.limit_window.value} limit exceeded"
        + (f" for model {limit.model_filter}" if limit.model_filter else ""),
        reset_at=limit.reset_at,
    )


def _limit_applies_for_request(limit: ApiKeyLimit, *, request_model: str | None) -> bool:
    if limit.model_filter is None:
        return True
    if request_model is None:
        return False
    return limit.model_filter == request_model


def _reserve_delta_for_limit(
    limit: ApiKeyLimit,
    *,
    request_model: str | None,
    request_service_tier: str | None,
    request_usage_budget: ApiKeyRequestUsageBudget,
) -> int:
    remaining = limit.max_value - limit.current_value
    if remaining <= 0:
        return 0
    budget = _reserve_budget_for_limit_type(
        limit.limit_type,
        request_model=request_model,
        request_service_tier=request_service_tier,
        request_usage_budget=request_usage_budget,
    )
    return min(remaining, budget)


def _normalize_request_usage_budget(budget: ApiKeyRequestUsageBudget | None) -> ApiKeyRequestUsageBudget:
    if budget is None:
        return ApiKeyRequestUsageBudget(
            input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
            output_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
        )
    return ApiKeyRequestUsageBudget(
        input_tokens=_normalize_reservation_token_budget(
            budget.input_tokens,
            default=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
        ),
        output_tokens=_normalize_reservation_token_budget(
            budget.output_tokens,
            default=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
        ),
    )


def _normalize_reservation_token_budget(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    return max(0, min(value, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET))


def _reserve_budget_for_limit_type(
    limit_type: LimitType,
    *,
    request_model: str | None,
    request_service_tier: str | None,
    request_usage_budget: ApiKeyRequestUsageBudget,
) -> int:
    input_tokens = request_usage_budget.input_tokens or 0
    output_tokens = request_usage_budget.output_tokens or 0
    if limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit_type == LimitType.COST_USD:
        return _reserve_cost_budget_microdollars(
            request_model,
            request_service_tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    if limit_type == LimitType.CREDITS:
        return 0
    return 1


def _reserve_cost_budget_microdollars(
    model: str | None,
    service_tier: str | None,
    *,
    input_tokens: int,
    output_tokens: int,
) -> int:
    if not model:
        return _unknown_model_reserve_cost_budget_microdollars(input_tokens=input_tokens, output_tokens=output_tokens)
    cost_microdollars = _calculate_cost_microdollars(
        model,
        input_tokens,
        output_tokens,
        0,
        service_tier,
    )
    return (
        cost_microdollars
        if cost_microdollars is not None and cost_microdollars > 0
        else _unknown_model_reserve_cost_budget_microdollars(input_tokens=input_tokens, output_tokens=output_tokens)
    )


def _unknown_model_reserve_cost_budget_microdollars(*, input_tokens: int, output_tokens: int) -> int:
    token_budget = max(0, input_tokens) + max(0, output_tokens)
    if token_budget <= 0:
        return 1
    return ceil(
        _API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_MICRODOLLARS
        * token_budget
        / _API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_BASE_TOKENS
    )


def _compute_increment_for_limit_type(
    limit_type: LimitType,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_microdollars: int,
) -> int:
    if limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit_type == LimitType.COST_USD:
        return cost_microdollars
    if limit_type == LimitType.CREDITS:
        return 0
    return 0


def _next_usage_reservation_id() -> str:
    return f"ur_{uuid.uuid4().hex}"


def _model_has_list_price(model: str) -> bool:

    normalized = model.lower()
    prices = {name.lower() for name in OPENAI_PRICING_MODELS}
    explicit_aliases = {
        name.lower(): target.lower() for name, target in OPENAI_MODEL_ALIASES.items() if "*" not in name
    }
    return (
        normalized in prices
        or explicit_aliases.get(normalized) in prices
        or get_anthropic_list_pricing(model) is not None
    )


def _calculate_cost_microdollars(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    service_tier: str | None = None,
    *,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    cache_creation_tier: str | None = None,
    cache_creation_1h_input_tokens: int | None = None,
) -> int | None:

    if not model:
        return None
    resolved = get_pricing_for_model(model)
    if resolved is not None:
        _, price = resolved
        usage = UsageTokens(
            input_tokens=float(input_tokens),
            output_tokens=float(output_tokens),
            cached_input_tokens=float(cached_input_tokens),
        )
        cost_usd = calculate_cost_from_usage(usage, price, service_tier=service_tier)
        if cost_usd is None:
            return 0
        return int(cost_usd * 1_000_000)
    anthropic_resolved = get_anthropic_pricing_for_model(model)
    if anthropic_resolved is None:
        return None
    _, anthropic_price = anthropic_resolved
    if cache_creation_input_tokens is None and cache_read_input_tokens is None:
        cache_read_input_tokens = cached_input_tokens
    writes = max(0, int(cache_creation_input_tokens or 0))
    if cache_creation_1h_input_tokens is not None:
        one_hour_writes = min(writes, max(0, int(cache_creation_1h_input_tokens)))
    else:
        one_hour_writes = writes if cache_creation_tier == "1h" else 0
    anthropic_cost_usd = calculate_anthropic_cost_from_usage(
        AnthropicUsage(
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            cache_creation_input_tokens=writes - one_hour_writes,
            cache_read_input_tokens=max(0, int(cache_read_input_tokens or 0)),
        ),
        anthropic_price,
        cache_creation_tier="5m",
    )
    if anthropic_cost_usd is None:
        return 0
    anthropic_cost_usd += one_hour_writes * anthropic_price.cache_creation_1h_input_per_1m / 1_000_000

    return round(anthropic_cost_usd * 1_000_000)


def _is_sqlite_database_locked(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database schema is locked" in message
    )
