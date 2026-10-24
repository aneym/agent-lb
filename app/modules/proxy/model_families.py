from __future__ import annotations

import re

from app.core.openai.model_registry import get_model_registry
from app.modules.proxy.claude_codex_bridge import CCGPT_MODEL, CCGPT_WORKER_MODEL

CCGPT_EFFORTS = ("low", "medium", "high", "xhigh")

CCGPT_LATEST_FAMILIES = ("sol", "luna", "astra")


def resolve_ccgpt_model(name: str | None) -> tuple[str, str | None] | None:
    if not name:
        return None
    served = {slug for slug in get_model_registry().get_models_with_fallback() if slug.startswith("gpt-")}

    served.update((CCGPT_MODEL, CCGPT_WORKER_MODEL))
    if name in served:
        return name, None
    base, _, effort = name.rpartition("-")
    if effort not in CCGPT_EFFORTS:
        base, effort = name, ""
    if base in served:
        return base, effort or None
    family = base.removesuffix("-latest")
    if family == base or family not in CCGPT_LATEST_FAMILIES:
        return None
    pattern = re.compile(rf"^gpt-(\d+(?:\.\d+)*)-{re.escape(family)}$")
    versions = [
        (tuple(int(part) for part in match.group(1).split(".")), slug)
        for slug in served
        if (match := pattern.match(slug))
    ]
    if not versions:
        return None
    return max(versions)[1], effort or None


def default_ccgpt_model() -> tuple[str, str | None]:
    return resolve_ccgpt_model("sol-latest") or (CCGPT_MODEL, None)
