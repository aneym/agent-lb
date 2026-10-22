from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)


ANTHROPIC_SESSION_ROUTE_QUOTA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "anthropic_standard",
        "anthropic_top",
        "anthropic_top_thinking",
        "anthropic_fast",
    }
)


ANTHROPIC_SESSION_ROUTE_AFFINITY_QUOTA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "anthropic_standard",
        "anthropic_top",
        "anthropic_top_thinking",
        "anthropic_standard_fable",
        "anthropic_top_fable",
        "anthropic_top_thinking_fable",
    }
)


PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES = frozenset(
    {
        "message",
        "function_call",
        "function_call_output",
        "reasoning",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "code_interpreter_call",
        "mcp_approval_request",
        "mcp_list_tools",
        "output_image",
        "compaction",
    }
)


PUBLIC_RESPONSE_TEXT_PART_TYPES = frozenset({"output_text", "input_text", "text", "refusal"})


PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES = frozenset(
    {"response.completed", "response.incomplete", "response.failed", "error"}
)


PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT = 64


TRANSCRIPTION_MODEL = "gpt-4o-transcribe"


UNAVAILABLE_SELECTION_ERROR_CODES = {
    "no_accounts",
    "no_plan_support_for_model",
    "additional_quota_data_unavailable",
    "quota_exhausted",
    "no_additional_quota_eligible_accounts",
}


STREAM_STARTUP_ERROR_PROBE_SECONDS = 0.05


HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS = 2.0


CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 2.0


CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 15.0


CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS: Final[int] = 1_000_000


V1_MAX_OUTPUT_TOKEN_OVERRIDES: Final[dict[str, int]] = {
    "gpt-5.4": 128_000,
    "gpt-5.5": 128_000,
    "gpt-5.4-mini": 128_000,
    "gpt-5.3-codex": 128_000,
}


OPPORTUNISTIC_RETRY_AFTER_SECONDS = 60


IMAGE_ERROR_TYPE_STATUS: Final[dict[str, int]] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "insufficient_quota": 429,
}


IMAGE_ERROR_CODE_STATUS: Final[dict[str, int]] = {
    "content_policy_violation": 400,
    "rate_limit_exceeded": 429,
    "insufficient_quota": 429,
}


WARMUP_MODES: Final[frozenset[str]] = frozenset({"normal", "strict", "force"})


CODEX_CONTROL_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "openai-processing-ms",
        "request-id",
        "x-request-id",
    }
)


UNTAGGED_HEADLESS_OPUS_MESSAGE = (
    "agent-lb: untagged headless Opus refused (claude-week rule (a), p6 2026-10-01). "
    "Set the seat: ANTHROPIC_CUSTOM_HEADERS='x-agent-lb-seat: <seat>' or launch through "
    "claude-lb-launch / run-workflow, which tag it."
)


FILES_CREATE_LIMIT_MODEL: Final = "files-create"


FILES_FINALIZE_LIMIT_MODEL: Final = "files-finalize"


CHAT_COMPLETIONS_STARTUP_EVENT_TYPES: Final[set[str]] = {
    "response.created",
    "response.in_progress",
}


REPLAYABLE_PUBLIC_PRE_CREATED_EVENT_TYPES = frozenset(
    {
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.refusal.delta",
        "response.refusal.done",
    }
)


INDEXED_PRE_CREATED_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
    }
)
