from __future__ import annotations

import re

from app.core.anthropic.models import AnthropicMessageRequest, AnthropicMessageRequestContentPart

_MODEL_REMINDER = re.compile(
    r"\A(<system-reminder>\s*)You are powered by the model named [^\n]+?(\s*</system-reminder>)\Z"
)


def correct_generated_model_identity(payload: AnthropicMessageRequest, model: str) -> AnthropicMessageRequest:
    if model.startswith("claude-"):
        return payload
    statement = route_model_identity(model)

    def corrected(text: str) -> str:
        return _MODEL_REMINDER.sub(lambda match: match[1] + statement + match[2], text)

    system = payload.system
    if isinstance(system, str):
        system = corrected(system)
    elif isinstance(system, list):
        system = [
            part.model_copy(update={"text": corrected(part.text)}) if part.type == "text" and part.text else part
            for part in system
        ]
    messages = [
        message.model_copy(
            update={
                "content": [
                    part.model_copy(update={"text": corrected(part.text)})
                    if part.type == "text" and part.text
                    else part
                    for part in message.content
                ]
            }
        )
        if message.role == "user" and isinstance(message.content, list)
        else message
        for message in payload.messages
    ]
    return payload.model_copy(update={"system": system, "messages": messages})


def route_model_identity(model: str) -> str:
    return (
        f"The inference model serving this session is {model}, routed through Agent LB. "
        "Claude Code / Claude Desktop is the client application, not the inference model. "
        "Client compatibility model aliases do not identify the serving model. "
        "No model knowledge-cutoff date is asserted by this relay."
    )


def with_route_model_identity(payload: AnthropicMessageRequest) -> AnthropicMessageRequest:
    if payload.model.startswith("claude-"):
        return payload
    corrected = correct_generated_model_identity(payload, payload.model)
    system = corrected.system
    blocks = (
        list(system)
        if isinstance(system, list)
        else ([AnthropicMessageRequestContentPart(type="text", text=system)] if system else [])
    )
    blocks.append(AnthropicMessageRequestContentPart(type="text", text=route_model_identity(payload.model)))
    return corrected.model_copy(update={"system": blocks})
