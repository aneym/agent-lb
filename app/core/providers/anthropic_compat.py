from __future__ import annotations

from dataclasses import dataclass

from app.core.auth.refresh import TokenRefreshResult


@dataclass(frozen=True, slots=True)
class AnthropicCompatImportDefaults:
    email: str
    alias: str
    plan_type: str
    account_id_seed: str


@dataclass(frozen=True, slots=True)
class AnthropicCompatProfile:
    provider_name: str
    model_prefixes: tuple[str, ...]
    messages_quota_key: str | None
    messages_thinking_quota_key: str | None
    count_tokens_quota_key: str
    sticky_prefix: str
    label: str
    no_available_accounts_code: str
    quota_cooldown_code: str
    other_provider_routing_message: str
    upstream_settings_attr: str
    default_probe_model: str
    import_defaults: AnthropicCompatImportDefaults | None = None
    # Some Anthropic-compatible vendors do not implement
    # /v1/messages/count_tokens and answer it with a non-Anthropic 404
    # envelope, which breaks Claude Code at startup. Those providers are
    # served from the local estimator instead.
    supports_upstream_count_tokens: bool = True
    # Only providers whose credentials are OAuth bundles may supply a
    # separate refresh token on import; API-key providers must not, so their
    # import contract stays exactly "the key is both access and refresh".
    supports_oauth_bundle_import: bool = False

    @property
    def supports_api_key_import(self) -> bool:
        return self.import_defaults is not None

    def quota_key(self, *, is_thinking: bool) -> str | None:
        if is_thinking:
            return self.messages_thinking_quota_key
        return self.messages_quota_key


ANTHROPIC_COMPAT_PROFILES: tuple[AnthropicCompatProfile, ...] = (
    AnthropicCompatProfile(
        provider_name="anthropic",
        model_prefixes=(),
        messages_quota_key=None,
        messages_thinking_quota_key=None,
        count_tokens_quota_key="anthropic_count_tokens",
        sticky_prefix="claude",
        label="Anthropic",
        no_available_accounts_code="no_available_anthropic_accounts",
        quota_cooldown_code="anthropic_quota_cooldown",
        other_provider_routing_message="OpenAI accounts are not eligible for Claude routing.",
        upstream_settings_attr="anthropic_upstream_base_url",
        default_probe_model="claude-haiku-4-5",
    ),
    AnthropicCompatProfile(
        provider_name="glm",
        model_prefixes=("glm-",),
        messages_quota_key="glm_coding",
        messages_thinking_quota_key="glm_coding_thinking",
        count_tokens_quota_key="glm_count_tokens",
        sticky_prefix="glm",
        label="GLM",
        no_available_accounts_code="no_available_glm_accounts",
        quota_cooldown_code="glm_quota_cooldown",
        other_provider_routing_message="OpenAI and Anthropic accounts are not eligible for GLM routing.",
        upstream_settings_attr="glm_anthropic_upstream_base_url",
        default_probe_model="glm-5.2",
        import_defaults=AnthropicCompatImportDefaults(
            email="glm@z.ai",
            alias="GLM Coding Plan",
            plan_type="glm-coding",
            account_id_seed="zai_glm_coding",
        ),
    ),
    AnthropicCompatProfile(
        provider_name="kimi",
        model_prefixes=("kimi-", "k3"),
        messages_quota_key="kimi_coding",
        messages_thinking_quota_key="kimi_coding_thinking",
        count_tokens_quota_key="kimi_count_tokens",
        sticky_prefix="kimi",
        label="Kimi",
        no_available_accounts_code="no_available_kimi_accounts",
        quota_cooldown_code="kimi_quota_cooldown",
        other_provider_routing_message="OpenAI and Anthropic accounts are not eligible for Kimi routing.",
        upstream_settings_attr="kimi_anthropic_upstream_base_url",
        # ``k3-256k`` was retired upstream; ``kimi-k3`` is the current
        # Anthropic-compatible model alias accepted by the Coding endpoint.
        default_probe_model="kimi-k3",
        import_defaults=AnthropicCompatImportDefaults(
            email="kimi@moonshot.ai",
            alias="Kimi Coding Plan",
            plan_type="kimi-coding",
            account_id_seed="kimi_coding",
        ),
        supports_upstream_count_tokens=False,
        supports_oauth_bundle_import=True,
    ),
)

_PROFILES_BY_PROVIDER = {profile.provider_name: profile for profile in ANTHROPIC_COMPAT_PROFILES}


def echo_api_key_refresh(api_key: str, *, plan_type: str) -> TokenRefreshResult:
    return TokenRefreshResult(
        access_token=api_key,
        refresh_token=api_key,
        id_token=None,
        account_id=None,
        plan_type=plan_type,
        email=None,
    )


def get_anthropic_compat_profile(provider_name: str) -> AnthropicCompatProfile:
    try:
        return _PROFILES_BY_PROVIDER[provider_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported Anthropic-compatible provider: {provider_name}") from exc


def provider_name_for_anthropic_model(model: str) -> str:
    normalized = model.strip().lower()
    for profile in ANTHROPIC_COMPAT_PROFILES:
        if profile.model_prefixes and normalized.startswith(profile.model_prefixes):
            return profile.provider_name
    return "anthropic"
