from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping

from app.core.anthropic.models import AnthropicMessageRequest
from app.modules.proxy.model_families import default_ccgpt_model, resolve_ccgpt_model

CLASSIFIER_BETA = "auto-mode-classifier-"
SESSION_LIMIT = 4096

main_models: OrderedDict[str, str] = OrderedDict()


def is_classifier_request(payload: AnthropicMessageRequest, headers: Mapping[str, str]) -> bool:
    beta = headers.get("anthropic-beta") or ""
    return CLASSIFIER_BETA in beta and payload.model.startswith("claude-")


def classifier_payload(payload: AnthropicMessageRequest, headers: Mapping[str, str]) -> AnthropicMessageRequest:
    session = headers.get("x-claude-code-session-id")
    if session is None:
        return payload
    if headers.get("x-claude-code-request-class") == "main":
        main_models[session] = payload.model
        main_models.move_to_end(session)
        while len(main_models) > SESSION_LIMIT:
            main_models.popitem(last=False)
        return payload
    if not is_classifier_request(payload, headers) or resolve_ccgpt_model(payload.model):
        return payload
    if main_models.get(session, "").startswith("claude-sonnet"):
        return payload
    return payload.model_copy(update={"model": default_ccgpt_model()[0]})
