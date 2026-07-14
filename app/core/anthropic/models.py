from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app.core.types import JsonObject


class AnthropicUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None
    cache_creation_input_tokens: StrictInt | None = None
    cache_read_input_tokens: StrictInt | None = None


class AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text"]
    text: StrictStr = ""


class AnthropicToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["tool_use"]
    id: StrictStr
    name: StrictStr
    input: JsonObject = Field(default_factory=dict)


class AnthropicThinkingBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["thinking"]
    thinking: StrictStr = ""
    signature: StrictStr | None = None


AnthropicContentBlock: TypeAlias = Annotated[
    AnthropicTextBlock | AnthropicToolUseBlock | AnthropicThinkingBlock,
    Field(discriminator="type"),
]


class AnthropicMessageRequestContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: StrictStr
    text: StrictStr | None = None
    source: JsonObject | None = None


class AnthropicMessageRequestMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Claude Code (Fable+) emits mid-conversation `system`-role messages that
    # the upstream Messages API accepts; keep this permissive so the proxy never
    # rejects a payload Anthropic itself would honor.
    role: StrictStr
    content: StrictStr | list[AnthropicMessageRequestContentPart]


class AnthropicToolChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: StrictStr
    name: StrictStr | None = None
    disable_parallel_tool_use: StrictBool | None = None


class AnthropicToolDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: StrictStr
    description: StrictStr | None = None
    input_schema: JsonObject


class AnthropicDefinedToolDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: StrictStr
    name: StrictStr


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: StrictStr
    max_tokens: StrictInt
    messages: list[AnthropicMessageRequestMessage]
    system: StrictStr | list[AnthropicMessageRequestContentPart] | None = None
    metadata: JsonObject | None = None
    stop_sequences: list[StrictStr] | None = None
    stream: StrictBool | None = None
    speed: StrictStr | None = None
    temperature: StrictFloat | StrictInt | None = None
    thinking: JsonObject | None = None
    tool_choice: AnthropicToolChoice | None = None
    tools: list[AnthropicToolDefinition | AnthropicDefinedToolDefinition] | None = None
    top_k: StrictInt | None = None
    top_p: StrictFloat | StrictInt | None = None


class AnthropicMessageResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: StrictStr
    type: Literal["message"]
    role: Literal["assistant"]
    model: StrictStr
    content: list[AnthropicContentBlock]
    stop_reason: StrictStr | None = None
    stop_sequence: StrictStr | None = None
    usage: AnthropicUsage | None = None


class AnthropicContentBlockDelta(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: StrictStr
    text: StrictStr | None = None
    partial_json: StrictStr | None = None
    thinking: StrictStr | None = None
    signature: StrictStr | None = None


class AnthropicMessageDelta(BaseModel):
    model_config = ConfigDict(extra="allow")

    stop_reason: StrictStr | None = None
    stop_sequence: StrictStr | None = None


class AnthropicMessageStartEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["message_start"]
    message: AnthropicMessageResponse


class AnthropicContentBlockStartEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["content_block_start"]
    index: StrictInt
    content_block: AnthropicContentBlock


class AnthropicContentBlockDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["content_block_delta"]
    index: StrictInt
    delta: AnthropicContentBlockDelta


class AnthropicContentBlockStopEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["content_block_stop"]
    index: StrictInt


class AnthropicMessageDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["message_delta"]
    delta: AnthropicMessageDelta
    usage: AnthropicUsage | None = None


class AnthropicMessageStopEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["message_stop"]


class AnthropicPingEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["ping"]


class AnthropicErrorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: StrictStr | None = None
    message: StrictStr | None = None


class AnthropicErrorEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["error"]
    error: AnthropicErrorPayload


AnthropicEvent: TypeAlias = Annotated[
    AnthropicMessageStartEvent
    | AnthropicContentBlockStartEvent
    | AnthropicContentBlockDeltaEvent
    | AnthropicContentBlockStopEvent
    | AnthropicMessageDeltaEvent
    | AnthropicMessageStopEvent
    | AnthropicPingEvent
    | AnthropicErrorEvent,
    Field(discriminator="type"),
]


def merge_usage_values(left: AnthropicUsage | None, right: AnthropicUsage | None) -> AnthropicUsage | None:
    if left is None:
        return right
    if right is None:
        return left
    return AnthropicUsage(
        input_tokens=right.input_tokens if right.input_tokens is not None else left.input_tokens,
        output_tokens=right.output_tokens if right.output_tokens is not None else left.output_tokens,
        cache_creation_input_tokens=(
            right.cache_creation_input_tokens
            if right.cache_creation_input_tokens is not None
            else left.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            right.cache_read_input_tokens if right.cache_read_input_tokens is not None else left.cache_read_input_tokens
        ),
    )
