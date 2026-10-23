from __future__ import annotations

import base64
import io
import json
import struct
import wave
from typing import Any

import openai
import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import BaseModel

import app.modules.proxy.service as proxy_module
from app.core.openai.model_registry import (
    ReasoningLevel,
    UpstreamModel,
    get_model_registry,
)
from tests.e2e.sdk_response_events import (
    codex_rate_limits_event,
    function_call_output_block,
    message_output_block,
    response_completed_empty,
    response_created,
    sse,
)

pytestmark = pytest.mark.e2e

DEFAULT_MODEL = "gpt-5.5"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
IMAGE_MODEL = "gpt-image-2"


def _make_upstream_model(slug: str, *, modalities: tuple[str, ...] = ("text",)) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=modalities,
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


@pytest_asyncio.fixture
async def sdk_client(
    e2e_client: AsyncClient,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    import_test_account,
):

    await setup_dashboard_password(e2e_client)
    await enable_api_key_auth(e2e_client)
    created = await create_api_key(e2e_client, name="e2e-sdk-compat")
    await import_test_account(
        e2e_client,
        account_id="acc_e2e_compat",
        email="e2e-compat@example.com",
    )

    registry = get_model_registry()
    snapshot = {
        "plus": [
            _make_upstream_model(DEFAULT_MODEL),
            _make_upstream_model(TRANSCRIPTION_MODEL),
            _make_upstream_model(
                IMAGE_MODEL,
                modalities=("text", "image"),
            ),
            _make_upstream_model(
                "gpt-image-1",
                modalities=("text", "image"),
            ),
        ],
        "pro": [
            _make_upstream_model(DEFAULT_MODEL),
            _make_upstream_model(TRANSCRIPTION_MODEL),
            _make_upstream_model(
                IMAGE_MODEL,
                modalities=("text", "image"),
            ),
            _make_upstream_model(
                "gpt-image-1",
                modalities=("text", "image"),
            ),
        ],
    }
    result = registry.update(snapshot)
    if hasattr(result, "__await__"):
        await result

    import httpx
    import httpx2

    transport = e2e_client._transport  # noqa: SLF001
    assert isinstance(transport, httpx.ASGITransport)

    client = openai.AsyncOpenAI(
        api_key=created["key"],
        base_url="http://testserver/v1",
        http_client=httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=transport.app),
            base_url="http://testserver",
        ),
    )
    yield client
    await client.close()


def _patch_upstream_stream(monkeypatch, blocks: list[str]) -> None:
    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        for block in blocks:
            yield block

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)


class TestChatCompletions:
    @pytest.mark.asyncio
    async def test_non_streaming_plain_text(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_nonstream"
        _patch_upstream_stream(
            monkeypatch,
            [
                codex_rate_limits_event(),
                response_created(resp_id, 0),
                *message_output_block("msg_a", "hi there", 0, 1),
                response_completed_empty(resp_id, 7),
            ],
        )

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "hello"}],
        )

        assert result.choices
        assert result.choices[0].message.role == "assistant"
        assert result.choices[0].message.content == "hi there"
        assert result.choices[0].finish_reason in {"stop", "length"}

    @pytest.mark.asyncio
    async def test_streaming_plain_text(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_stream"
        _patch_upstream_stream(
            monkeypatch,
            [
                codex_rate_limits_event(),
                response_created(resp_id, 0),
                *message_output_block("msg_b", "streamed content", 0, 1),
                response_completed_empty(resp_id, 7),
            ],
        )

        chunks: list[str] = []
        roles_seen: list[str | None] = []
        async with await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "stream"}],
            stream=True,
        ) as stream:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.role:
                    roles_seen.append(delta.role)
                if delta.content:
                    chunks.append(delta.content)

        joined = "".join(chunks)
        assert "streamed content" in joined
        assert "assistant" in roles_seen

    @pytest.mark.asyncio
    async def test_tool_call_non_streaming(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_tool"
        _patch_upstream_stream(
            monkeypatch,
            [
                codex_rate_limits_event(),
                response_created(resp_id, 0),
                *function_call_output_block(
                    "call_w",
                    "get_weather",
                    '{"city":"Seoul"}',
                    0,
                    1,
                ),
                response_completed_empty(resp_id, 5),
            ],
        )

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "weather?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
        )

        message = result.choices[0].message
        assert message.tool_calls
        tc = message.tool_calls[0]
        assert tc.function.name == "get_weather"
        assert json.loads(tc.function.arguments) == {"city": "Seoul"}
        assert result.choices[0].finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_multi_turn_payload_round_trip(self, sdk_client, monkeypatch):

        resp_id = "resp_chat_multi"
        seen_payload: dict[str, Any] = {}

        async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
            seen_payload["payload"] = payload
            for block in [
                codex_rate_limits_event(),
                response_created(resp_id, 0),
                *message_output_block("msg_m", "ack", 0, 1),
                response_completed_empty(resp_id, 7),
            ]:
                yield block

        monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again?"},
            ],
        )

        assert result.choices[0].message.content == "ack"

        forwarded = seen_payload["payload"]
        assert hasattr(forwarded, "input") or "input" in (forwarded if isinstance(forwarded, dict) else {})


class _CityForecast(BaseModel):
    city: str
    temperature_c: int


class TestResponsesParse:
    @pytest.mark.asyncio
    async def test_parse_with_pydantic_text_format(self, sdk_client, monkeypatch):

        resp_id = "resp_parse_struct"
        payload = json.dumps({"city": "Seoul", "temperature_c": 21})
        _patch_upstream_stream(
            monkeypatch,
            [
                codex_rate_limits_event(),
                response_created(resp_id, 0),
                *message_output_block("msg_parse", payload, 0, 1),
                response_completed_empty(resp_id, 7),
            ],
        )

        result = await sdk_client.responses.parse(
            model=DEFAULT_MODEL,
            input=[{"role": "user", "content": "forecast for Seoul"}],
            text_format=_CityForecast,
        )

        assert result.output_parsed == _CityForecast(
            city="Seoul",
            temperature_c=21,
        )


class TestModels:
    @pytest.mark.asyncio
    async def test_list_returns_registered_models(self, sdk_client):
        result = await sdk_client.models.list()
        ids = {model.id for model in result.data}
        assert DEFAULT_MODEL in ids


def _make_wav_bytes(*, seconds: float = 0.05, sample_rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        n_samples = int(seconds * sample_rate)
        w.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))
    return buf.getvalue()


class TestAudioTranscriptions:
    @pytest.mark.asyncio
    async def test_create_returns_transcription_text(
        self,
        sdk_client,
        monkeypatch,
    ):

        captured: dict[str, Any] = {}

        async def fake_transcribe(self, *, audio_bytes, filename, content_type, prompt, headers, api_key):
            captured["filename"] = filename
            captured["bytes_len"] = len(audio_bytes)
            return {"text": "hello transcription"}

        from app.modules.proxy.service import ProxyService

        monkeypatch.setattr(ProxyService, "transcribe", fake_transcribe)

        wav = _make_wav_bytes()
        result = await sdk_client.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL,
            file=("sample.wav", wav, "audio/wav"),
        )

        assert result.text == "hello transcription"
        assert captured["bytes_len"] == len(wav)


_PNG_1X1_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
_PNG_1X1_BYTES = base64.b64decode(_PNG_1X1_B64)


def _patch_images_upstream(
    monkeypatch,
    *,
    result_b64: str = "FAKE_IMAGE_B64",
    revised_prompt: str = "neat",
    resp_id: str = "resp_img",
    size: str = "1024x1024",
    output_format: str = "png",
    input_tokens: int = 11,
    output_tokens: int = 17,
    captured: dict[str, Any] | None = None,
) -> None:

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        if captured is not None:
            captured["model"] = payload.model
            captured["account_id"] = account_id
            captured["tools"] = list(payload.tools)
        yield sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_e2e",
                    "status": "completed",
                    "result": result_b64,
                    "revised_prompt": revised_prompt,
                    "size": size,
                    "quality": "low",
                    "background": "auto",
                    "output_format": output_format,
                },
            }
        )
        yield sse(
            {
                "type": "response.completed",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "status": "completed",
                    "tool_usage": {
                        "image_gen": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    },
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_ensure_fresh_with_budget",
        fake_ensure_fresh,
    )


class TestImages:
    @pytest.mark.asyncio
    async def test_generate_returns_b64_image(self, sdk_client, monkeypatch):

        captured: dict[str, Any] = {}
        _patch_images_upstream(
            monkeypatch,
            result_b64="GENERATED_B64",
            revised_prompt="a clean red circle",
            captured=captured,
        )

        result = await sdk_client.images.generate(
            model=IMAGE_MODEL,
            prompt="a red circle",
            n=1,
            size="1024x1024",
            quality="low",
        )

        assert result.data
        assert result.data[0].b64_json == "GENERATED_B64"

        assert result.data[0].revised_prompt == "a clean red circle"
        # Image routes hide the fixed internal host model from clients;
        # ensure the upstream call really used a host model.
        assert captured["model"] not in {None, ""}
        tools = captured["tools"]
        assert tools, "image_generation tool must be forwarded to upstream"
        assert tools[0]["type"] == "image_generation"
        assert tools[0]["model"] == IMAGE_MODEL

    @pytest.mark.asyncio
    async def test_edit_returns_b64_image(self, sdk_client, monkeypatch):

        _patch_images_upstream(
            monkeypatch,
            result_b64="EDITED_B64",
            revised_prompt="edited",
        )

        result = await sdk_client.images.edit(
            model="gpt-image-1",
            image=("input.png", _PNG_1X1_BYTES, "image/png"),
            prompt="add a yellow border",
            n=1,
            size="1024x1024",
        )

        assert result.data
        assert result.data[0].b64_json == "EDITED_B64"

    @pytest.mark.asyncio
    async def test_variation_is_clean_4xx(self, sdk_client):

        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.images.create_variation(
                model="gpt-image-1",
                image=("input.png", _PNG_1X1_BYTES, "image/png"),
                n=1,
                size="1024x1024",
            )
        assert 400 <= ei.value.status_code < 500, f"images.variation returned non-4xx: {ei.value.status_code}"


class TestUnsupportedSurfaces:
    @staticmethod
    def _assert_clean_4xx(exc: openai.APIStatusError) -> None:
        assert 400 <= exc.status_code < 500, f"Unsupported route returned non-4xx status: {exc.status_code}"

    @pytest.mark.asyncio
    async def test_embeddings_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.embeddings.create(
                model=DEFAULT_MODEL,
                input="hello",
            )
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_moderations_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.moderations.create(input="hello")
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_files_list_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.files.list()
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_batches_list_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.batches.list()
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_responses_retrieve_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.responses.retrieve("resp_does_not_exist")
        self._assert_clean_4xx(ei.value)
