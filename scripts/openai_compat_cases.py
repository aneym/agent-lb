from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from typing import Any, Callable, Literal, cast
from urllib.parse import urlparse
from urllib.request import urlopen

from openai.types.chat import (
    ChatCompletionContentPartInputAudioParam,
    ChatCompletionContentPartParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionMessageParam,
    ChatCompletionUserMessageParam,
)


class CaseSkipped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CaseError(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail.get("message", "case error"))
        self.detail = detail


@dataclass
class CaseResult:
    name: str
    status: str
    detail: dict[str, Any]


def _error_detail(exc: Exception) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "type": exc.__class__.__name__,
        "message": str(exc),
    }
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        detail["status_code"] = status_code
    body = getattr(exc, "body", None)
    if body is not None:
        detail["body"] = body
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail["response_json"] = response.json()
        except (ValueError, TypeError, AttributeError):
            try:
                detail["response_text"] = response.text
            except (AttributeError, TypeError):
                pass
    return detail


def _chat_user_message(content: str | list[ChatCompletionContentPartParam]) -> ChatCompletionMessageParam:
    return ChatCompletionUserMessageParam(role="user", content=content)


def _chat_text_part(text: str) -> ChatCompletionContentPartTextParam:
    return {"type": "text", "text": text}


def _chat_image_part(url: str) -> ChatCompletionContentPartParam:
    return {"type": "image_url", "image_url": {"url": url}}


def _chat_audio_format(value: str) -> Literal["wav", "mp3"]:
    normalized = value.strip().lower()
    if normalized in ("wav", "mp3"):
        return cast(Literal["wav", "mp3"], normalized)
    raise CaseSkipped("C.audio_input requires CODEX_TEST_AUDIO_FORMAT to be wav or mp3")


def _chat_audio_part(data_b64: str, audio_format: Literal["wav", "mp3"]) -> ChatCompletionContentPartInputAudioParam:
    return {"type": "input_audio", "input_audio": {"data": data_b64, "format": audio_format}}


def _chat_file_part_from_data(*, file_data_b64: str, filename: str) -> ChatCompletionContentPartParam:
    return {"type": "file", "file": {"file_data": file_data_b64, "filename": filename}}


def _chat_file_data_from_url(file_url: str) -> tuple[str, str]:
    parsed = urlparse(file_url)
    filename = parsed.path.rsplit("/", 1)[-1] or "input.bin"
    with urlopen(file_url, timeout=15) as response:
        payload = response.read()
    return base64.b64encode(payload).decode("ascii"), filename


def run_case(name: str, fn: Callable[[], Any]) -> CaseResult:
    try:
        result = fn()
        return CaseResult(name=name, status="ok", detail={"result": result})
    except CaseSkipped as exc:
        return CaseResult(name=name, status="skipped", detail={"reason": exc.reason})
    except CaseError as exc:
        return CaseResult(name=name, status="error", detail=exc.detail)
    except Exception as exc:
        return CaseResult(name=name, status="error", detail=_error_detail(exc))


def _input_file_payload(file_ref: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Summarize this file in one sentence."},
                {"type": "input_file", **file_ref},
            ],
        }
    ]


def _input_text_payload(text: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
    ]


def _build_wav_base64(duration_ms: int = 100, sample_rate: int = 8000) -> str:
    num_channels = 1
    sample_width = 2
    num_frames = int(sample_rate * duration_ms / 1000)
    data = b"\x00\x00" * num_frames
    byte_rate = sample_rate * num_channels * sample_width
    block_align = num_channels * sample_width
    riff_size = 36 + len(data)
    header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
    fmt = b"fmt " + struct.pack(
        "<IHHIIHH",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,
    )
    data_chunk = b"data" + struct.pack("<I", len(data)) + data
    wav_bytes = header + fmt + data_chunk
    return base64.b64encode(wav_bytes).decode("ascii")


def _response_stream_summary(stream: Any) -> dict[str, Any]:
    event_types: list[str] = []
    output_text = ""
    for event in stream:
        event_type = getattr(event, "type", None)
        if isinstance(event_type, str):
            event_types.append(event_type)
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if isinstance(delta, str):
                output_text += delta
    return {
        "event_types": event_types,
        "event_count": len(event_types),
        "output_text": output_text,
    }


def _chat_stream_summary(stream: Any) -> dict[str, Any]:
    output_text = ""
    chunk_count = 0
    for chunk in stream:
        chunk_count += 1
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            output_text += delta.content
    return {"chunk_count": chunk_count, "output_text": output_text}
