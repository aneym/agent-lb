from __future__ import annotations

from app.core.providers.anthropic import ANTHROPIC_PROVIDER_NAME, AnthropicProvider
from app.core.providers.openai import OPENAI_PROVIDER_NAME, OpenAIProvider
from app.core.providers.types import Provider


class ProviderLookupError(ValueError):
    def __init__(self, provider: str | None) -> None:
        self.provider = provider
        super().__init__(f"Unsupported provider: {provider or '<missing>'}")


_PROVIDERS: dict[str, Provider] = {
    ANTHROPIC_PROVIDER_NAME: AnthropicProvider(),
    OPENAI_PROVIDER_NAME: OpenAIProvider(),
}


def normalize_provider_name(provider: str | None) -> str:
    normalized = (provider or OPENAI_PROVIDER_NAME).strip().lower()
    return normalized or OPENAI_PROVIDER_NAME


def get_provider(provider: str | None = None) -> Provider:
    normalized = normalize_provider_name(provider)
    try:
        return _PROVIDERS[normalized]
    except KeyError as exc:
        raise ProviderLookupError(provider) from exc


def list_provider_names() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))
