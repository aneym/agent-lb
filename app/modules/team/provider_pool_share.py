from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.anthropic.pricing import DEFAULT_PRICING_MODELS
from app.core.exceptions import TeamMemberOverCapError
from app.core.openai.model_registry import get_model_registry
from app.core.providers.anthropic import ANTHROPIC_PROVIDER_NAME
from app.core.providers.anthropic_compat import ANTHROPIC_COMPAT_PROFILES, provider_name_for_anthropic_model
from app.core.providers.openai import OPENAI_PROVIDER_NAME
from app.core.providers.openrouter import OPENROUTER_PROVIDER_NAME, is_deepseek_model
from app.db.access_models import TeamMember
from app.modules.proxy.request_policy import resolve_model_alias, strip_model_alias_suffix
from app.modules.team.pool_share import PoolWindow, pool_share_windows, reachable_account_providers
from app.modules.usage.additional_quota_keys import get_additional_quota_definition_for_model

GEMINI_PROVIDER_NAME = "gemini"
_OPENROUTER_CREDITS_QUOTA_KEY = "openrouter_credits"
_MODEL_CONTEXT_SUFFIX = re.compile(r"(?:\[[^\]]*\]|-1m)$")
_PROVIDER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_OPENAI_PREFIXES = ("gpt-", "gpt5", "codex", "chatgpt-", "ccgpt", "o1-", "o3-", "o4-")
_OPENAI_EXACT = frozenset({"o1", "o3", "o4"})
_ANTHROPIC_PREFIXES = ("claude-", "opus-", "sonnet-", "haiku-")
_ANTHROPIC_EXACT = frozenset({"claude", "opus", "sonnet", "haiku"})
_ANTHROPIC_PRICING_MODELS = frozenset(
    model.lower()
    for model in DEFAULT_PRICING_MODELS
    if provider_name_for_anthropic_model(model) == ANTHROPIC_PROVIDER_NAME
)


@dataclass(frozen=True, slots=True)
class PoolShareUpdate:
    percent: float | None = None
    percent_set: bool = False
    percent_by_provider: Mapping[str, float | None] | None = None
    percent_by_provider_set: bool = False
    account_ids: list[str] | None = None
    account_ids_set: bool = False


def _model_candidates(model: str) -> tuple[str, ...]:
    raw = model.strip().lower()
    if not raw:
        return ()
    if raw.startswith("models/"):
        raw = raw.removeprefix("models/")
    ordered: list[str] = []
    for item in (raw, _MODEL_CONTEXT_SUFFIX.sub("", raw)):
        if item and item not in ordered:
            ordered.append(item)
    for item in tuple(ordered):
        alias = resolve_model_alias(item)
        if alias:
            normalized_alias = alias.strip().lower()
            if normalized_alias and normalized_alias not in ordered:
                ordered.append(normalized_alias)
        stripped = strip_model_alias_suffix(item)
        if stripped and stripped not in ordered:
            ordered.append(stripped)
    return tuple(ordered)


def resolve_provider_for_model(model: str | None) -> str | None:
    if not model:
        return None
    candidates = _model_candidates(model)
    if not candidates:
        return None
    for candidate in candidates:
        if is_deepseek_model(candidate):
            return OPENROUTER_PROVIDER_NAME
        definition = get_additional_quota_definition_for_model(candidate)
        if definition is not None and definition.quota_key == _OPENROUTER_CREDITS_QUOTA_KEY:
            return OPENROUTER_PROVIDER_NAME
    for candidate in candidates:
        for profile in ANTHROPIC_COMPAT_PROFILES:
            if profile.model_prefixes and candidate.startswith(profile.model_prefixes):
                return profile.provider_name
            if profile.listed_models and candidate in profile.listed_models:
                return profile.provider_name
    for candidate in candidates:
        if candidate == GEMINI_PROVIDER_NAME or candidate.startswith(f"{GEMINI_PROVIDER_NAME}-"):
            return GEMINI_PROVIDER_NAME
    openai_models = {slug.strip().lower() for slug in get_model_registry().get_models_with_fallback()}
    for candidate in candidates:
        if candidate in openai_models or candidate in _OPENAI_EXACT or candidate.startswith(_OPENAI_PREFIXES):
            return OPENAI_PROVIDER_NAME
    for candidate in candidates:
        if (
            candidate in _ANTHROPIC_PRICING_MODELS
            or candidate in _ANTHROPIC_EXACT
            or candidate.startswith(_ANTHROPIC_PREFIXES)
            or "fable" in candidate
            or "mythos" in candidate
        ):
            return ANTHROPIC_PROVIDER_NAME
    return None


def _validate_share_value(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.001 or numeric > 100:
        raise ValueError(f"{label} must be between 0.001 and 100")
    if abs(numeric * 1000 - round(numeric * 1000)) > 1e-8:
        raise ValueError(f"{label} must have at most three decimal places")
    return numeric


def normalize_pool_share_percent_by_provider(
    raw: Mapping[str, float | None] | None,
) -> dict[str, float] | None:
    if raw is None:
        return None
    normalized: dict[str, float] = {}
    for key, value in raw.items():
        provider = key.strip().lower() if isinstance(key, str) else ""
        if not provider or not _PROVIDER_KEY_PATTERN.match(provider):
            raise ValueError(f"Invalid provider key for pool_share_percent_by_provider: {key!r}")
        if value is None:
            continue
        normalized[provider] = _validate_share_value(value, f"pool_share_percent_by_provider[{provider}]")
    if not normalized:
        return None
    return {provider: normalized[provider] for provider in sorted(normalized)}


def member_pool_share_percent_by_provider(member: TeamMember) -> dict[str, float] | None:
    raw = member.pool_share_percent_by_provider
    if not isinstance(raw, Mapping):
        return None
    cleaned = {
        str(key).strip().lower(): float(value)
        for key, value in raw.items()
        if isinstance(key, str) and key.strip() and value is not None
    }
    if not cleaned:
        return None
    return {provider: cleaned[provider] for provider in sorted(cleaned)}


def effective_pool_share_percent(member: TeamMember, provider: str | None) -> float | None:
    if provider is not None:
        overrides = member_pool_share_percent_by_provider(member)
        if overrides is not None and provider in overrides:
            return overrides[provider]
    if member.pool_share_percent is None:
        return None
    return float(member.pool_share_percent)


async def member_pool_share_windows(
    session: AsyncSession,
    member: TeamMember,
    *,
    now: datetime,
    model: str | None = None,
    provider: str | None = None,
) -> list[PoolWindow]:
    overrides = member_pool_share_percent_by_provider(member)
    if member.pool_share_percent is None and not overrides:
        return []
    target_provider = (provider.strip().lower() or None) if provider is not None else resolve_provider_for_model(model)
    if target_provider is not None:
        limit = effective_pool_share_percent(member, target_provider)
        if limit is None:
            return []
        return await pool_share_windows(
            session,
            member.id,
            limit,
            now=now,
            account_ids=member.pool_share_account_ids,
            provider=target_provider,
        )
    providers = await reachable_account_providers(session, member.id, member.pool_share_account_ids)
    windows: list[PoolWindow] = []
    for account_provider in providers:
        limit = effective_pool_share_percent(member, account_provider)
        if limit is None:
            continue
        windows.extend(
            await pool_share_windows(
                session,
                member.id,
                limit,
                now=now,
                account_ids=member.pool_share_account_ids,
                provider=account_provider,
            )
        )
    return windows


async def enforce_pool_share_gate(
    session: AsyncSession,
    member: TeamMember,
    *,
    now: datetime,
    model: str | None,
    provider: str | None,
    contact_line: str,
) -> None:
    windows = await member_pool_share_windows(session, member, now=now, model=model, provider=provider)
    for pool_window in windows:
        if pool_window.used_percent >= pool_window.limit_percent:
            reset_text = pool_window.reset_at.strftime("%a %H:%M UTC")
            raise TeamMemberOverCapError(
                f"You've used your share of the shared accounts for this {pool_window.window} window; "
                f"it resets at {reset_text}. {contact_line}",
                window=pool_window.window,
                reset_at=pool_window.reset_at,
            )


def apply_pool_share_update(member: TeamMember, update: PoolShareUpdate) -> None:
    if update.percent_set:
        member.pool_share_percent = update.percent
    if update.percent_by_provider_set:
        member.pool_share_percent_by_provider = normalize_pool_share_percent_by_provider(update.percent_by_provider)
    if update.account_ids_set:
        member.pool_share_account_ids = sorted(set(update.account_ids or [])) or None
