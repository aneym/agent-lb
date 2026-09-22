"""Separate Opus response cooldowns from legacy Fable/top-model markers."""


def model_quota_key(model: str | None, quota_key: str) -> str:
    if model and "opus" in model.lower():
        if quota_key == "anthropic_top":
            return "anthropic_opus"
        if quota_key == "anthropic_top_thinking":
            return "anthropic_opus_thinking"
    return quota_key
