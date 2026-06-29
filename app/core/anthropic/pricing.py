from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal, Mapping

from app.core.anthropic.models import AnthropicUsage

CacheCreationTier = Literal["5m", "1h"]


@dataclass(frozen=True, slots=True)
class AnthropicModelPrice:
    input_per_1m: float
    output_per_1m: float
    cache_creation_5m_input_per_1m: float
    cache_creation_1h_input_per_1m: float
    cache_read_input_per_1m: float


@dataclass(frozen=True, slots=True)
class AnthropicUsageCostBreakdown:
    input_usd: float
    cache_creation_input_usd: float
    cache_read_input_usd: float
    output_usd: float
    total_usd: float


DEFAULT_PRICING_MODELS: dict[str, AnthropicModelPrice] = {
    "claude-fable-5": AnthropicModelPrice(
        input_per_1m=10.0,
        cache_creation_5m_input_per_1m=12.50,
        cache_creation_1h_input_per_1m=20.0,
        cache_read_input_per_1m=1.0,
        output_per_1m=50.0,
    ),
    "claude-mythos-5": AnthropicModelPrice(
        input_per_1m=10.0,
        cache_creation_5m_input_per_1m=12.50,
        cache_creation_1h_input_per_1m=20.0,
        cache_read_input_per_1m=1.0,
        output_per_1m=50.0,
    ),
    "claude-opus-4-5": AnthropicModelPrice(
        input_per_1m=5.0,
        cache_creation_5m_input_per_1m=6.25,
        cache_creation_1h_input_per_1m=10.0,
        cache_read_input_per_1m=0.50,
        output_per_1m=25.0,
    ),
    "claude-sonnet-4-5": AnthropicModelPrice(
        input_per_1m=3.0,
        cache_creation_5m_input_per_1m=3.75,
        cache_creation_1h_input_per_1m=6.0,
        cache_read_input_per_1m=0.30,
        output_per_1m=15.0,
    ),
    "claude-haiku-4-5": AnthropicModelPrice(
        input_per_1m=1.0,
        cache_creation_5m_input_per_1m=1.25,
        cache_creation_1h_input_per_1m=2.0,
        cache_read_input_per_1m=0.10,
        output_per_1m=5.0,
    ),
    "claude-opus-4.1": AnthropicModelPrice(
        input_per_1m=15.0,
        cache_creation_5m_input_per_1m=18.75,
        cache_creation_1h_input_per_1m=30.0,
        cache_read_input_per_1m=1.50,
        output_per_1m=75.0,
    ),
    "claude-opus-4": AnthropicModelPrice(
        input_per_1m=15.0,
        cache_creation_5m_input_per_1m=18.75,
        cache_creation_1h_input_per_1m=30.0,
        cache_read_input_per_1m=1.50,
        output_per_1m=75.0,
    ),
    "claude-sonnet-4": AnthropicModelPrice(
        input_per_1m=3.0,
        cache_creation_5m_input_per_1m=3.75,
        cache_creation_1h_input_per_1m=6.0,
        cache_read_input_per_1m=0.30,
        output_per_1m=15.0,
    ),
    "claude-3-7-sonnet": AnthropicModelPrice(
        input_per_1m=3.0,
        cache_creation_5m_input_per_1m=3.75,
        cache_creation_1h_input_per_1m=6.0,
        cache_read_input_per_1m=0.30,
        output_per_1m=15.0,
    ),
    "claude-3-5-sonnet": AnthropicModelPrice(
        input_per_1m=3.0,
        cache_creation_5m_input_per_1m=3.75,
        cache_creation_1h_input_per_1m=6.0,
        cache_read_input_per_1m=0.30,
        output_per_1m=15.0,
    ),
    "claude-3-5-haiku": AnthropicModelPrice(
        input_per_1m=0.80,
        cache_creation_5m_input_per_1m=1.0,
        cache_creation_1h_input_per_1m=1.60,
        cache_read_input_per_1m=0.08,
        output_per_1m=4.0,
    ),
    "claude-3-opus": AnthropicModelPrice(
        input_per_1m=15.0,
        cache_creation_5m_input_per_1m=18.75,
        cache_creation_1h_input_per_1m=30.0,
        cache_read_input_per_1m=1.50,
        output_per_1m=75.0,
    ),
    "claude-3-haiku": AnthropicModelPrice(
        input_per_1m=0.25,
        cache_creation_5m_input_per_1m=0.30,
        cache_creation_1h_input_per_1m=0.50,
        cache_read_input_per_1m=0.03,
        output_per_1m=1.25,
    ),
}

DEFAULT_MODEL_ALIASES: dict[str, str] = {
    # 4.5-generation patterns are longer than the 4.0 ones, so resolve_model_alias's
    # longest-match wins — e.g. claude-opus-4-5-* maps to its own $5/$25 price, not Opus-4's $15/$75.
    "claude-fable-5*": "claude-fable-5",
    "claude-mythos-5*": "claude-mythos-5",
    "claude-opus-4-5*": "claude-opus-4-5",
    "claude-sonnet-4-5*": "claude-sonnet-4-5",
    "claude-haiku-4-5*": "claude-haiku-4-5",
    "claude-opus-4-1*": "claude-opus-4.1",
    "claude-opus-4*": "claude-opus-4",
    "claude-sonnet-4*": "claude-sonnet-4",
    "claude-3-7-sonnet*": "claude-3-7-sonnet",
    "claude-3-5-sonnet*": "claude-3-5-sonnet",
    "claude-3-5-haiku*": "claude-3-5-haiku",
    "claude-3-opus*": "claude-3-opus",
    "claude-3-haiku*": "claude-3-haiku",
}


def resolve_model_alias(model: str, aliases: Mapping[str, str]) -> str | None:
    normalized = model.lower()
    matched: list[tuple[int, str]] = []
    for pattern, target in aliases.items():
        if fnmatchcase(normalized, pattern.lower()):
            matched.append((len(pattern), target))
    if not matched:
        return None
    return max(matched, key=lambda item: item[0])[1]


def get_pricing_for_model(
    model: str,
    pricing: Mapping[str, AnthropicModelPrice] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, AnthropicModelPrice] | None:
    if not model:
        return None
    pricing = pricing or DEFAULT_PRICING_MODELS
    aliases = aliases or DEFAULT_MODEL_ALIASES

    normalized = model.lower()
    for key, value in pricing.items():
        if key.lower() == normalized:
            return key, value

    alias = resolve_model_alias(normalized, aliases)
    if alias is None:
        return None
    for key, value in pricing.items():
        if key.lower() == alias.lower():
            return key, value
    return None


def calculate_anthropic_cost_from_usage(
    usage: AnthropicUsage | None,
    price: AnthropicModelPrice,
    *,
    cache_creation_tier: CacheCreationTier = "5m",
) -> float | None:
    breakdown = calculate_anthropic_cost_breakdown(usage, price, cache_creation_tier=cache_creation_tier)
    if breakdown is None:
        return None
    return breakdown.total_usd


def calculate_anthropic_cost_breakdown(
    usage: AnthropicUsage | None,
    price: AnthropicModelPrice,
    *,
    cache_creation_tier: CacheCreationTier = "5m",
) -> AnthropicUsageCostBreakdown | None:
    if usage is None:
        return None

    input_tokens = _token_count(usage.input_tokens)
    output_tokens = _token_count(usage.output_tokens)
    cache_creation_tokens = _token_count(usage.cache_creation_input_tokens)
    cache_read_tokens = _token_count(usage.cache_read_input_tokens)
    cache_creation_rate = (
        price.cache_creation_1h_input_per_1m if cache_creation_tier == "1h" else price.cache_creation_5m_input_per_1m
    )

    input_usd = (input_tokens / 1_000_000) * price.input_per_1m
    cache_creation_input_usd = (cache_creation_tokens / 1_000_000) * cache_creation_rate
    cache_read_input_usd = (cache_read_tokens / 1_000_000) * price.cache_read_input_per_1m
    output_usd = (output_tokens / 1_000_000) * price.output_per_1m

    return AnthropicUsageCostBreakdown(
        input_usd=input_usd,
        cache_creation_input_usd=cache_creation_input_usd,
        cache_read_input_usd=cache_read_input_usd,
        output_usd=output_usd,
        total_usd=input_usd + cache_creation_input_usd + cache_read_input_usd + output_usd,
    )


def _token_count(value: int | None) -> float:
    if value is None:
        return 0.0
    return float(max(0, value))
