from __future__ import annotations

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.types import JsonValue

BOOTSTRAP_MODEL_SLUGS = {
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "codex-auto-review",
}


EXPECTED_CORE_MODEL_PLANS = {
    "plus",
    "pro",
    "prolite",
    "team",
    "business",
    "enterprise",
    "edu",
    "education",
    "go",
    "hc",
    "finserv",
    "quorum",
    "self_serve_business_usage_based",
    "enterprise_cbp_usage_based",
}


EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS = {
    "gpt-5.6-sol": "0.124.0",
    "gpt-5.5": "0.124.0",
    "gpt-5.4": "0.98.0",
    "gpt-5.4-mini": "0.98.0",
    "gpt-5.3-codex": "0.98.0",
    "gpt-5.3-codex-spark": "0.100.0",
    "gpt-5.2": "0.0.1",
    "codex-auto-review": "0.98.0",
}


def _make_upstream_model(
    slug: str,
    *,
    supported_in_api: bool = True,
    base_instructions: str = "",
    raw: dict[str, JsonValue] | None = None,
) -> UpstreamModel:
    default_raw: dict[str, JsonValue] = {
        "shell_type": "shell_command",
        "visibility": "list",
        "availability_nux": None,
    }
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=("text", "image"),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=True,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        base_instructions=base_instructions,
        raw=raw or default_raw,
    )


async def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
    ]
    await registry.update({"plus": models, "pro": models})


def _raw_with_max_context_window(max_context_window: int) -> dict[str, JsonValue]:
    return {
        "shell_type": "shell_command",
        "visibility": "list",
        "max_context_window": max_context_window,
        "auto_compact_token_limit": None,
    }
