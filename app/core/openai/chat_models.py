from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.openai.models import OpenAIErrorEnvelope


class ChatToolCallFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    arguments: str | None = None


class ChatToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    id: str | None = None
    type: str = "function"
    function: ChatToolCallFunction | None = None


class ChatChunkDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[ChatToolCallDelta] | None = None


class ChatChunkChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    delta: ChatChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatChunkChoice]
    usage: "ChatCompletionUsage | None" = None


class ChatMessageToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: str = "function"
    function: ChatToolCallFunction | None = None


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[ChatMessageToolCall] | None = None


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    message: ChatCompletionMessage
    finish_reason: str | None = None


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_tokens_details: "ChatPromptTokensDetails | None" = None
    completion_tokens_details: "ChatCompletionTokensDetails | None" = None


class ChatPromptTokensDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cached_tokens: int | None = None


class ChatCompletionTokensDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_tokens: int | None = None


class ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None


ChatCompletionResult = ChatCompletion | OpenAIErrorEnvelope
