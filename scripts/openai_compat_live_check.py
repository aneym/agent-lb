from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any, Callable, cast

import openai
from openai.types.chat import ChatCompletionMessageParam
from openai_compat_cases import (
    CaseError,
    CaseResult,
    CaseSkipped,
    _build_wav_base64,
    _chat_audio_format,
    _chat_audio_part,
    _chat_file_data_from_url,
    _chat_file_part_from_data,
    _chat_image_part,
    _chat_stream_summary,
    _chat_text_part,
    _chat_user_message,
    _error_detail,
    _input_file_payload,
    _input_text_payload,
    _response_stream_summary,
    run_case,
)

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("CODEX_BASE_URL", "http://localhost:2455/v1")
API_KEY = os.environ.get("CODEX_API_KEY", "sk-local")
MODEL_OVERRIDE = os.environ.get("CODEX_MODEL")

FILE_URL = os.environ.get(
    "CODEX_TEST_FILE_URL",
    "https://www.berkshirehathaway.com/letters/2024ltr.pdf",
)
FILE_ID = os.environ.get("CODEX_TEST_FILE_ID", "file-unknown")
VECTOR_STORE_ID = os.environ.get("CODEX_VECTOR_STORE_ID")
IMAGE_URL = os.environ.get(
    "CODEX_TEST_IMAGE_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/"
    "Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-"
    "madison-the-nature-boardwalk.jpg",
)
AUDIO_B64 = os.environ.get("CODEX_TEST_AUDIO_B64")
AUDIO_FORMAT = os.environ.get("CODEX_TEST_AUDIO_FORMAT", "wav")
CHAT_FILE_ID = os.environ.get("CODEX_TEST_CHAT_FILE_ID", "file-unknown")
CHAT_FILE_DATA_B64 = os.environ.get("CODEX_TEST_CHAT_FILE_DATA_B64")
CHAT_FILE_NAME = os.environ.get("CODEX_TEST_CHAT_FILE_NAME", "input.bin")

RUN_RESPONSES = os.environ.get("CODEX_RUN_RESPONSES", "1") != "0"
RUN_CHAT = os.environ.get("CODEX_RUN_CHAT", "1") != "0"
RUN_TOOLS = os.environ.get("CODEX_RUN_TOOLS", "1") != "0"
RUN_REASONING = os.environ.get("CODEX_RUN_REASONING", "1") != "0"

DEFAULT_TEXT = "Return 'ok' only."

EXPECTED_UNSUPPORTED = [
    "R.file_input_id",
    "R.previous_response_id",
    "R.truncation_auto",
    "T.file_search",
    "T.code_interpreter",
    "T.computer_use",
    "T.image_generation",
    "T.tool_choice_required",
    "C.audio_input",
    "C.file_input_id",
]


def _pick_model(client: openai.OpenAI) -> str:
    if MODEL_OVERRIDE:
        return MODEL_OVERRIDE
    models = client.models.list()
    if models.data:
        return models.data[0].id
    raise RuntimeError("No models returned from /v1/models")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        model = _pick_model(client)
    except (openai.APIError, RuntimeError) as exc:
        logger.error("Failed to list models: %s", exc)
        return 2

    audio_b64 = AUDIO_B64 or _build_wav_base64()

    logger.info("Base URL: %s", BASE_URL)
    logger.info("Model: %s", model)
    logger.info("File URL: %s", FILE_URL)
    logger.info("File ID: %s", FILE_ID)
    logger.info("Vector Store ID: %s", VECTOR_STORE_ID or "(missing)")
    logger.info("Image URL: %s", IMAGE_URL)
    logger.info("Audio format: %s", AUDIO_FORMAT)
    logger.info("Chat file ID: %s", CHAT_FILE_ID)
    logger.info("Expected unsupported: %s", ", ".join(EXPECTED_UNSUPPORTED))

    results: list[CaseResult] = []

    cases: list[tuple[str, Callable[[], Any]]] = []

    if RUN_RESPONSES:

        def r_text_input_string() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=DEFAULT_TEXT)
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_text_input_list() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_text_payload(DEFAULT_TEXT)))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_image_input_url() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(
                    Any,
                    [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Describe the image."},
                                {"type": "input_image", "image_url": IMAGE_URL},
                            ],
                        }
                    ],
                ),
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_file_input_url() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_file_payload({"file_url": FILE_URL})))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_file_input_id() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_file_payload({"file_id": FILE_ID})))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_streaming() -> dict[str, Any]:
            stream = client.responses.create(
                model=model, input=cast(Any, _input_text_payload("Stream ok")), stream=True
            )
            return _response_stream_summary(stream)

        def r_previous_response_id() -> dict[str, Any]:
            first = client.responses.create(model=model, input=cast(Any, _input_text_payload(DEFAULT_TEXT)))
            first_id = getattr(first, "id", None)
            if not first_id:
                raise CaseError({"message": "missing response id from first call"})
            try:
                second = client.responses.create(
                    model=model,
                    input=cast(Any, _input_text_payload("Repeat exactly: ok")),
                    previous_response_id=first_id,
                )
            except Exception as exc:
                detail = _error_detail(exc)
                detail["first_id"] = first_id
                raise CaseError(detail) from exc
            return {
                "first_id": first_id,
                "second_id": getattr(second, "id", None),
                "second_output_text": getattr(second, "output_text", None),
            }

        def r_truncation_auto() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Short reply.")),
                truncation="auto",
            )
            return {"response_id": getattr(resp, "id", None)}

        def r_text_format_json_object() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload('Return {"ok": true} as JSON.')),
                text={"format": {"type": "json_object"}},
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_include_logprobs() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Return ok.")),
                include=["message.output_text.logprobs"],
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("R.text_input_string", r_text_input_string),
                ("R.text_input_list", r_text_input_list),
                ("R.image_input_url", r_image_input_url),
                ("R.file_input_url", r_file_input_url),
                ("R.file_input_id", r_file_input_id),
                ("R.streaming", r_streaming),
                ("R.previous_response_id", r_previous_response_id),
                ("R.truncation_auto", r_truncation_auto),
                ("R.text_format_json_object", r_text_format_json_object),
                ("R.include_logprobs", r_include_logprobs),
            ]
        )

    if RUN_REASONING:

        def r_reasoning_effort_low() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Solve 1+1.")),
                reasoning={"effort": "low"},
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_reasoning_summary_detailed() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Explain briefly.")),
                reasoning={"summary": "detailed"},
            )
            return {"response_id": getattr(resp, "id", None)}

        def r_reasoning_encrypted_content() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Reason about 2+2.")),
                include=["reasoning.encrypted_content"],
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("R.reasoning_effort_low", r_reasoning_effort_low),
                ("R.reasoning_summary_detailed", r_reasoning_summary_detailed),
                ("R.reasoning_encrypted_content", r_reasoning_encrypted_content),
            ]
        )

    if RUN_TOOLS:

        def t_web_search() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("What was a positive news story today?")),
                tools=[{"type": "web_search"}],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_file_search() -> dict[str, Any]:
            vector_store_id = VECTOR_STORE_ID or "vs_dummy"
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Find notes about onboarding.")),
                tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
                include=["file_search_call.results"],
            )
            return {
                "response_id": getattr(resp, "id", None),
                "output_text": getattr(resp, "output_text", None),
                "vector_store_id": vector_store_id,
                "used_dummy_vector_store_id": VECTOR_STORE_ID is None,
            }

        def t_code_interpreter() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Compute 2+2 and return only the number.")),
                tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_computer_use() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Open example.com and report the page title.")),
                tools=[
                    {
                        "type": "computer_use_preview",
                        "display_width": 1024,
                        "display_height": 768,
                        "environment": "browser",
                    }
                ],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_image_generation() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Generate a small red circle on white background.")),
                tools=[{"type": "image_generation"}],
            )
            return {"response_id": getattr(resp, "id", None)}

        def t_tool_choice_required() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Draw a red dot.")),
                tools=[{"type": "image_generation"}],
                tool_choice="required",
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("T.web_search", t_web_search),
                ("T.file_search", t_file_search),
                ("T.code_interpreter", t_code_interpreter),
                ("T.computer_use", t_computer_use),
                ("T.image_generation", t_image_generation),
                ("T.tool_choice_required", t_tool_choice_required),
            ]
        )

    if RUN_CHAT:

        def c_text() -> dict[str, Any]:
            messages: list[ChatCompletionMessageParam] = [
                _chat_user_message(DEFAULT_TEXT),
            ]
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_image_url() -> dict[str, Any]:
            messages: list[ChatCompletionMessageParam] = [
                _chat_user_message(
                    [
                        _chat_text_part("Describe the image."),
                        _chat_image_part(IMAGE_URL),
                    ]
                )
            ]
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_audio_input() -> dict[str, Any]:
            audio_format = _chat_audio_format(AUDIO_FORMAT)
            messages: list[ChatCompletionMessageParam] = [
                _chat_user_message(
                    [
                        _chat_text_part("Transcribe the audio."),
                        _chat_audio_part(audio_b64, audio_format),
                    ]
                )
            ]
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_file_input() -> dict[str, Any]:
            try:
                if CHAT_FILE_DATA_B64:
                    file_data_b64 = CHAT_FILE_DATA_B64
                    filename = CHAT_FILE_NAME
                else:
                    file_data_b64, filename = _chat_file_data_from_url(FILE_URL)
            except Exception as exc:
                raise CaseSkipped(f"C.file_input could not prepare file payload: {exc}") from exc

            messages: list[ChatCompletionMessageParam] = [
                _chat_user_message(
                    [
                        _chat_text_part("Summarize the file."),
                        _chat_file_part_from_data(file_data_b64=file_data_b64, filename=filename),
                    ]
                )
            ]
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_file_input_id() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Summarize the file."},
                            {"type": "file", "file": {"file_id": CHAT_FILE_ID}},
                        ],
                    }
                ],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_tools_function() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "What is the weather in Boston?"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_current_weather",
                            "description": "Get the current weather",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "location": {"type": "string"},
                                    "unit": {"type": "string", "enum": ["c", "f"]},
                                },
                                "required": ["location"],
                            },
                        },
                    }
                ],
                tool_choice="auto",
            )
            return {"id": getattr(resp, "id", None), "finish_reason": resp.choices[0].finish_reason}

        def c_response_format_json_object() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": 'Return {"ok": true} as JSON.'}],
                response_format={"type": "json_object"},
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_streaming() -> dict[str, Any]:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Stream ok"}],
                stream=True,
            )
            return _chat_stream_summary(stream)

        cases.extend(
            [
                ("C.text", c_text),
                ("C.image_url", c_image_url),
                ("C.audio_input", c_audio_input),
                ("C.file_input", c_file_input),
                ("C.file_input_id", c_file_input_id),
                ("C.tools_function", c_tools_function),
                ("C.response_format_json_object", c_response_format_json_object),
                ("C.streaming", c_streaming),
            ]
        )

    for name, fn in cases:
        result = run_case(name, fn)
        results.append(result)
        status = result.status.upper()
        summary = result.detail.get("reason") or result.detail.get("message") or ""
        logger.info("- %s: %s%s", name, status, f" {summary}" if summary else "")

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE_URL,
        "model": model,
        "expected_unsupported": EXPECTED_UNSUPPORTED,
        "results": [asdict(item) for item in results],
    }

    os.makedirs("refs", exist_ok=True)
    with open("refs/openai-compat-live-results.json", "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=True, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
