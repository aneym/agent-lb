from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

from app.core.types import JsonValue

logger = logging.getLogger(__name__)
MODEL_SOURCE_KIND_SUBSCRIPTION = "subscription"
MODEL_SOURCE_KIND_OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ReasoningLevel:
    effort: str
    description: str


@dataclass(frozen=True)
class UpstreamModel:
    slug: str
    display_name: str
    description: str
    context_window: int
    input_modalities: tuple[str, ...]
    supported_reasoning_levels: tuple[ReasoningLevel, ...]
    default_reasoning_level: str | None
    supports_reasoning_summaries: bool
    support_verbosity: bool
    default_verbosity: str | None
    prefer_websockets: bool
    supports_parallel_tool_calls: bool
    supported_in_api: bool
    minimal_client_version: str | None
    priority: int
    available_in_plans: frozenset[str]
    base_instructions: str = ""
    source_kind: str = MODEL_SOURCE_KIND_SUBSCRIPTION
    source_id: str | None = None
    raw: dict[str, JsonValue] = field(default_factory=dict, hash=False, compare=False)


@dataclass
class ModelRegistrySnapshot:
    models: dict[str, UpstreamModel]
    model_plans: dict[str, frozenset[str]]
    plan_models: dict[str, frozenset[str]]
    model_service_tier_plans: dict[str, dict[str, frozenset[str]]]
    model_service_tier_accounts: dict[str, dict[str, frozenset[str]]]
    account_plans: dict[str, str]
    fetched_at: float
    model_accounts: dict[str, frozenset[str]] = field(default_factory=dict)
    account_catalogs_authoritative: bool = False
    bootstrap_floor_active: bool = False
    suppressed_model_slugs: frozenset[str] = field(default_factory=frozenset)


@dataclass(slots=True)
class ModelRegistryExport:
    snapshot: ModelRegistrySnapshot | None
    metadata_models: dict[str, UpstreamModel] | None


_BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS = (
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.5-*",
    "gpt-5.4",
    "gpt-5.4-*",
    "gpt-5.6-*",
)
_REASONING_LEVELS_STANDARD = (
    ReasoningLevel(effort="low", description="Low reasoning effort"),
    ReasoningLevel(effort="medium", description="Medium reasoning effort"),
    ReasoningLevel(effort="high", description="High reasoning effort"),
)
_REASONING_LEVELS_EXTENDED = (
    ReasoningLevel(effort="low", description="Low reasoning effort"),
    ReasoningLevel(effort="medium", description="Medium reasoning effort"),
    ReasoningLevel(effort="high", description="High reasoning effort"),
    ReasoningLevel(effort="xhigh", description="Extra high reasoning effort"),
)
_REASONING_LEVELS_MAX = (
    ReasoningLevel(effort="low", description="Fast responses with lighter reasoning"),
    ReasoningLevel(effort="medium", description="Balances speed and reasoning depth for everyday tasks"),
    ReasoningLevel(effort="high", description="Greater reasoning depth for complex problems"),
    ReasoningLevel(effort="xhigh", description="Extra high reasoning depth for complex problems"),
    ReasoningLevel(effort="max", description="Maximum reasoning depth for the hardest problems"),
)
_REASONING_LEVELS_ULTRA = (
    *_REASONING_LEVELS_MAX,
    ReasoningLevel(effort="ultra", description="Maximum reasoning with automatic task delegation"),
)
_BOOTSTRAP_FAST_SERVICE_TIERS: list[JsonValue] = [
    {"id": "priority", "name": "Fast", "description": "1.5x speed, increased usage"}
]
_BOOTSTRAP_AVAILABLE_IN_PLANS = frozenset(
    {
        "plus",
        "pro",
        "prolite",
        "team",
        "business",
        "enterprise",
        "edu",
        "education",
        "k12",
        "go",
        "hc",
        "finserv",
        "free",
        "free_workspace",
        "quorum",
        "self_serve_business_usage_based",
        "enterprise_cbp_usage_based",
    }
)
_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS = frozenset(
    (plan for plan in _BOOTSTRAP_AVAILABLE_IN_PLANS if plan not in {"free", "free_workspace", "k12"})
)
_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS = frozenset(
    {*_BOOTSTRAP_AVAILABLE_IN_PLANS, "edu_plus", "edu_pro", "enterprise_cbp_automation", "sci"}
)
_GPT56_SOL_AVAILABILITY_NUX: dict[str, JsonValue] = {
    "message": "Our most capable model yet. GPT-5.6 Sol can tackle complex code changes, dig into research, produce polished documents, and take on your most ambitious work. Sol is highly capable at lower reasoning efforts—try starting lower, then turn it up for harder jobs."
}


def _bootstrap_model(
    slug: str,
    display_name: str,
    *,
    prefer_websockets: bool,
    minimal_client_version: str | None,
    description: str | None = None,
    reasoning_levels: tuple[ReasoningLevel, ...] = _REASONING_LEVELS_EXTENDED,
    context_window: int = 272000,
    input_modalities: tuple[str, ...] = ("text", "image"),
    default_reasoning_level: str | None = "medium",
    default_verbosity: str | None = "low",
    supported_in_api: bool = True,
    available_in_plans: frozenset[str] = _BOOTSTRAP_AVAILABLE_IN_PLANS,
    visibility: str = "list",
    shell_type: str = "shell_command",
    priority: int = 0,
    raw: dict[str, JsonValue] | None = None,
) -> UpstreamModel:
    raw_fields: dict[str, JsonValue] = {
        "shell_type": shell_type,
        "visibility": visibility,
        "availability_nux": None,
        "max_context_window": context_window,
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "experimental_supported_tools": [],
    }
    if raw:
        raw_fields.update(raw)
    return UpstreamModel(
        slug=slug,
        display_name=display_name,
        description=description or display_name,
        context_window=context_window,
        input_modalities=input_modalities,
        supported_reasoning_levels=reasoning_levels,
        default_reasoning_level=default_reasoning_level,
        supports_reasoning_summaries=True,
        support_verbosity=True,
        default_verbosity=default_verbosity,
        prefer_websockets=prefer_websockets,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=minimal_client_version,
        priority=priority,
        available_in_plans=available_in_plans,
        raw=raw_fields,
    )


def _gpt56_raw(
    *, multi_agent_version: str, availability_nux: dict[str, JsonValue] | None = None
) -> dict[str, JsonValue]:
    return {
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "supports_image_detail_original": True,
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "tool_mode": "code_mode_only",
        "multi_agent_version": multi_agent_version,
        "use_responses_lite": True,
        "include_skills_usage_instructions": False,
        "auto_review_model_override": None,
        "max_context_window": 872000,
        "auto_compact_token_limit": None,
        "comp_hash": "3000",
        "reasoning_summary_format": "experimental",
        "default_reasoning_summary": "none",
        "availability_nux": availability_nux,
        "upgrade": None,
        "experimental_supported_tools": [],
        "supports_search_tool": True,
        "default_service_tier": None,
        "service_tiers": _BOOTSTRAP_FAST_SERVICE_TIERS,
        "additional_speed_tiers": ["fast"],
    }


_BOOTSTRAP_STATIC_MODELS: tuple[UpstreamModel, ...] = (
    _bootstrap_model(
        "gpt-5.6-sol",
        "GPT-5.6-Sol",
        description="Latest frontier agentic coding model.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_ULTRA,
        context_window=272000,
        default_reasoning_level="low",
        priority=1,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v2", availability_nux=_GPT56_SOL_AVAILABILITY_NUX),
    ),
    _bootstrap_model(
        "gpt-5.6-terra",
        "GPT-5.6-Terra",
        description="Balanced agentic coding model for everyday work.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_ULTRA,
        context_window=272000,
        default_reasoning_level="medium",
        priority=2,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v2"),
    ),
    _bootstrap_model(
        "gpt-5.6-luna",
        "GPT-5.6-Luna",
        description="Fast and affordable agentic coding model.",
        prefer_websockets=True,
        minimal_client_version="0.144.0",
        reasoning_levels=_REASONING_LEVELS_MAX,
        context_window=272000,
        default_reasoning_level="medium",
        priority=3,
        available_in_plans=_BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
        raw=_gpt56_raw(multi_agent_version="v1"),
    ),
    _bootstrap_model("gpt-5.5", "GPT-5.5", prefer_websockets=True, minimal_client_version="0.124.0"),
    _bootstrap_model(
        "gpt-5.4",
        "GPT-5.4",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
        raw={"max_context_window": 1000000},
    ),
    _bootstrap_model(
        "gpt-5.4-mini",
        "GPT-5.4 Mini",
        prefer_websockets=True,
        default_verbosity="medium",
        minimal_client_version="0.98.0",
    ),
    _bootstrap_model(
        "gpt-5.3-codex",
        "GPT-5.3 Codex",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
    ),
    _bootstrap_model(
        "gpt-5.3-codex-spark",
        "GPT-5.3 Codex Spark",
        prefer_websockets=True,
        context_window=128000,
        input_modalities=("text",),
        default_reasoning_level="high",
        minimal_client_version="0.100.0",
    ),
    _bootstrap_model(
        "gpt-5.2",
        "GPT-5.2",
        prefer_websockets=True,
        minimal_client_version="0.0.1",
        raw={"truncation_policy": {"mode": "bytes", "limit": 10000}},
    ),
    _bootstrap_model(
        "codex-auto-review",
        "Codex Auto Review",
        prefer_websockets=True,
        minimal_client_version="0.98.0",
        available_in_plans=_BOOTSTRAP_CORE_AVAILABLE_IN_PLANS,
        visibility="hide",
        raw={"max_context_window": 1000000},
    ),
)
_SERVICE_TIER_OBJECT_KEY_FIELDS = ("slug", "name", "id", "tier")


def _union_string_tiers(primary: JsonValue, secondary: JsonValue) -> list[JsonValue] | None:
    merged: list[JsonValue] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        if isinstance(source, list):
            for item in source:
                if not isinstance(item, str):
                    continue
                identity = canonical_service_tier_value(item)
                if identity not in seen:
                    seen.add(identity)
                    merged.append(item)
    return merged or None


def _service_tier_identity(entry: JsonValue) -> str:
    if isinstance(entry, dict):
        identities: list[str] = []
        for key in _SERVICE_TIER_OBJECT_KEY_FIELDS:
            value = entry.get(key)
            if isinstance(value, str) and value:
                canonical = canonical_service_tier_value(value)
                if canonical:
                    identities.append(canonical)
        if "priority" in identities:
            return "tier:priority"
        if identities:
            return f"tier:{identities[0]}"
    return json.dumps(entry, sort_keys=True, default=str)


def canonical_service_tier_value(value: str) -> str:
    canonical = value.strip().lower()
    if canonical in {"fast", "priority"}:
        return "priority"
    return canonical


def _union_object_tiers(primary: JsonValue, secondary: JsonValue) -> list[JsonValue] | None:
    merged: list[JsonValue] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        if isinstance(source, list):
            for item in source:
                identity = _service_tier_identity(item)
                if identity not in seen:
                    seen.add(identity)
                    merged.append(item)
    return merged or None


def _merge_default_service_tier(primary: JsonValue, secondary: JsonValue) -> str | None:
    if not isinstance(primary, str) or not primary:
        return secondary if isinstance(secondary, str) and secondary else None
    if not isinstance(secondary, str) or not secondary:
        return primary
    if primary == secondary:
        return primary
    plain_defaults = {"auto", "default"}
    if primary in plain_defaults and secondary not in plain_defaults:
        return secondary
    return primary


def _model_service_tier_keys(model: UpstreamModel) -> frozenset[str]:
    tiers: set[str] = set()

    def add_tier(value: str) -> None:
        tiers.add(value)
        canonical = canonical_service_tier_value(value)
        if canonical:
            tiers.add(canonical)

    additional_speed_tiers = model.raw.get("additional_speed_tiers")
    if isinstance(additional_speed_tiers, list):
        for item in additional_speed_tiers:
            if isinstance(item, str) and item:
                add_tier(item)
    service_tiers = model.raw.get("service_tiers")
    if isinstance(service_tiers, list):
        for item in service_tiers:
            if not isinstance(item, dict):
                continue
            for key in _SERVICE_TIER_OBJECT_KEY_FIELDS:
                value = item.get(key)
                if isinstance(value, str) and value:
                    add_tier(value)
    default_service_tier = model.raw.get("default_service_tier")
    if isinstance(default_service_tier, str) and default_service_tier:
        add_tier(default_service_tier)
    return frozenset(tiers)


def _merge_service_tier_metadata(existing: UpstreamModel, incoming: UpstreamModel) -> UpstreamModel:
    merged_raw = dict(incoming.raw)
    speed_tiers = _union_string_tiers(
        incoming.raw.get("additional_speed_tiers"), existing.raw.get("additional_speed_tiers")
    )
    if speed_tiers is not None:
        merged_raw["additional_speed_tiers"] = speed_tiers
    service_tiers = _union_object_tiers(incoming.raw.get("service_tiers"), existing.raw.get("service_tiers"))
    if service_tiers is not None:
        merged_raw["service_tiers"] = service_tiers
    default_service_tier = _merge_default_service_tier(
        incoming.raw.get("default_service_tier"), existing.raw.get("default_service_tier")
    )
    if default_service_tier is not None:
        merged_raw["default_service_tier"] = default_service_tier
    if merged_raw == incoming.raw:
        return incoming
    return replace(incoming, raw=merged_raw)


def is_public_model(model: UpstreamModel, allowed_models: set[str] | None) -> bool:
    if not model.supported_in_api:
        return False
    if allowed_models is None:
        return True
    return model.slug in allowed_models
