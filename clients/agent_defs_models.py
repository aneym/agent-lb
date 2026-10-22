from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_MODELS = frozenset(
    {
        "inherit",
        "opus",
        "sonnet",
        "haiku",
        "fable",
        "claude-opus-5-5",
        "claude-opus-5-5[1m]",
        "claude-opus-5",
        "claude-opus-5[1m]",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-fable-5",
        "claude-fable-5[1m]",
        "claude-fable-5-1",
        "claude-fable-5-1[1m]",
        "claude-mythos-5",
        "claude-planner",
        "claude-opus-4-6",
        "claude-opus-4-8",
        "claude-sonnet-4-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "gpt-5.6-sol",
        "gpt-5.6-sol-medium",
        "gpt-5.6-sol-high",
        "gpt-5.6-sol-xhigh",
        "gpt-5.6-terra",
        "gpt-5.6-terra-medium",
        "gpt-5.6-luna",
        "gpt-6-sol",
        "gpt-6-sol-low",
        "gpt-6-sol-medium",
        "gpt-6-sol-high",
        "gpt-6-sol-xhigh",
        "gpt-6.1-sol",
        "gpt-6.1-sol-low",
        "gpt-6.1-sol-medium",
        "gpt-6.1-sol-high",
        "gpt-6.1-sol-xhigh",
        "gpt-6-luna",
        "gpt-6.1-luna",
        "gpt-6-astra",
    }
)


def load_models(path: str | Path | None) -> tuple[set[str], str]:
    models = set(DEFAULT_MODELS)
    if path is None:
        return models, hashlib.sha256(b"builtin").hexdigest()
    data = Path(path).read_bytes()
    decoded = json.loads(data)
    if isinstance(decoded, list):
        entries = decoded
    elif isinstance(decoded, dict) and isinstance(decoded.get("models"), list):
        entries = decoded["models"]
    else:
        raise ValueError("models catalog must be a JSON list or an object with a models list")
    for entry in entries:
        model_id = entry.get("id") if isinstance(entry, dict) else entry
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("each catalog model must be a string or an object with a string id")
        models.add(model_id)
    return models, hashlib.sha256(data).hexdigest()
