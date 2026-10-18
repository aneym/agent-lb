from __future__ import annotations

from app.core.anthropic.models import AnthropicUsage
from app.core.anthropic.pricing import calculate_anthropic_cost_breakdown
from app.core.anthropic.pricing import get_pricing_for_model as anthropic_price
from app.core.usage.pricing import UsageTokens, calculate_cost_breakdown_from_usage, get_pricing_for_model

CLASSES = ("fresh_input", "cache_read", "cache_write", "output", "reasoning")


def token_classes(row):
    def count(key):
        return max(0, row.get(key) or 0)

    inp, cached = count("input_tokens"), count("cached_input_tokens")
    anthropic = row.get("provider") == "anthropic"
    return dict(
        zip(
            CLASSES,
            (
                inp if anthropic else inp - min(inp, cached),
                count("cache_read_tokens") if anthropic else min(inp, cached),
                count("cache_creation_tokens") if anthropic else 0,
                count("output_tokens"),
                count("reasoning_tokens"),
            ),
            strict=True,
        )
    )


def price_row(row):

    split = {key: 0.0 for key in CLASSES if key != "reasoning"}
    breakdown = None
    provider, model = row.get("provider"), row.get("model") or ""
    if provider == "anthropic" and (resolved := anthropic_price(model)):
        if any(
            row.get(k) is not None
            for k in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
        ):
            breakdown = calculate_anthropic_cost_breakdown(
                AnthropicUsage(
                    input_tokens=row.get("input_tokens"),
                    output_tokens=row.get("output_tokens"),
                    cache_creation_input_tokens=row.get("cache_creation_tokens"),
                    cache_read_input_tokens=row.get("cache_read_tokens"),
                ),
                resolved[1],
            )
            split.update(
                fresh_input=breakdown.input_usd,
                cache_read=breakdown.cache_read_input_usd,
                cache_write=breakdown.cache_creation_input_usd,
                output=breakdown.output_usd,
            )
    elif provider == "openai" and (resolved := get_pricing_for_model(model, None, None)):
        if row.get("input_tokens") is not None and row.get("output_tokens") is not None:
            breakdown = calculate_cost_breakdown_from_usage(
                UsageTokens(
                    row["input_tokens"],
                    row["output_tokens"],
                    min(max(0, row.get("cached_input_tokens") or 0), max(0, row["input_tokens"])),
                ),
                resolved[1],
                service_tier=row.get("service_tier"),
            )
            split.update(
                fresh_input=breakdown.input_usd, cache_read=breakdown.cached_input_usd, output=breakdown.output_usd
            )
    estimated = sum(split.values())
    if row.get("cost_usd") is not None:
        usd, source = float(row["cost_usd"]), "log"
        if estimated:
            split = {k: v * usd / estimated for k, v in split.items()}
        else:
            split["unallocated"] = usd
    elif breakdown is not None:
        usd, source = estimated, "recomputed"
    else:
        usd, source = 0.0, "unpriced"
    return usd, source, split


def input_cost(event, premium=False):
    model = event.get("model") or ""
    if model.startswith("gpt-"):
        resolved = get_pricing_for_model(model, None, None)
        if not resolved or premium:
            return 0.0
        breakdown = calculate_cost_breakdown_from_usage(
            UsageTokens(event.get("input_tokens") or 0, 0, event.get("cached_input_tokens") or 0),
            resolved[1],
        )
        return (breakdown.input_usd or 0) + (breakdown.cached_input_usd or 0)
    resolved = anthropic_price(model)
    if not resolved:
        return 0.0
    price = resolved[1]
    write = event.get("cache_creation_tokens") or 0
    one_hour = min(write, event.get("write_1h") or 0)
    cost = (write - one_hour) * price.cache_creation_5m_input_per_1m + one_hour * price.cache_creation_1h_input_per_1m
    if premium:
        return (cost - write * price.cache_read_input_per_1m) / 1_000_000
    return (
        cost
        + (event.get("input_tokens") or 0) * price.input_per_1m
        + (event.get("cache_read_tokens") or 0) * price.cache_read_input_per_1m
    ) / 1_000_000
