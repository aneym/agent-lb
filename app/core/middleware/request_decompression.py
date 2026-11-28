from __future__ import annotations

import gzip
import io
import zlib
from typing import Protocol

import zstandard as zstd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config.settings import get_settings
from app.core.errors import dashboard_error

_RESPONSES_DECOMPRESSION_PATHS = frozenset(
    {
        "/backend-api/codex/responses",
        "/v1/responses",
    }
)


class _DecompressedBodyTooLarge(Exception):
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Decompressed body exceeded {max_size} bytes")
        self.max_size = max_size


class _Readable(Protocol):
    def read(self, size: int = ...) -> bytes: ...


def _read_limited(reader: _Readable, max_size: int) -> bytes:
    buffer = bytearray()
    total = 0
    chunk_size = 64 * 1024
    while True:
        chunk = reader.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise _DecompressedBodyTooLarge(max_size)
        buffer.extend(chunk)
    return bytes(buffer)


def _decompress_gzip(data: bytes, max_size: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as reader:
        return _read_limited(reader, max_size)


def _decompress_deflate(data: bytes, max_size: int) -> bytes:
    decompressor = zlib.decompressobj()
    buffer = bytearray()
    chunk_size = 64 * 1024
    for start in range(0, len(data), chunk_size):
        chunk = data[start : start + chunk_size]

        while chunk:
            remaining = max_size - len(buffer)
            if remaining == 0:
                raise _DecompressedBodyTooLarge(max_size)
            buffer.extend(decompressor.decompress(chunk, max_length=remaining))
            chunk = decompressor.unconsumed_tail
    while True:
        remaining = max_size - len(buffer)
        if remaining == 0:
            raise _DecompressedBodyTooLarge(max_size)
        drained = decompressor.decompress(b"", max_length=remaining)
        if not drained:
            break
        buffer.extend(drained)
    if not decompressor.eof:
        raise zlib.error("Incomplete deflate stream")
    return bytes(buffer)


def _decompress_zstd(data: bytes, max_size: int) -> bytes:
    try:
        decompressed = zstd.ZstdDecompressor().decompress(data, max_output_size=max_size)
        if len(decompressed) > max_size:
            raise _DecompressedBodyTooLarge(max_size)
        return decompressed
    except _DecompressedBodyTooLarge:
        raise
    except Exception:
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return _read_limited(reader, max_size)


def _decompress_body(data: bytes, encodings: list[str], max_size: int) -> bytes:
    supported = {"zstd", "gzip", "deflate", "identity"}
    if any(encoding not in supported for encoding in encodings):
        raise ValueError("Unsupported content-encoding")
    result = data
    for encoding in reversed(encodings):
        if encoding == "zstd":
            result = _decompress_zstd(result, max_size)
        elif encoding == "gzip":
            result = _decompress_gzip(result, max_size)
        elif encoding == "deflate":
            result = _decompress_deflate(result, max_size)
        elif encoding == "identity":
            continue
    return result


def _max_decompressed_body_bytes_for_path(path: str) -> int:
    settings = get_settings()
    if path.rstrip("/") in _RESPONSES_DECOMPRESSION_PATHS:
        return max(settings.max_decompressed_body_bytes, settings.max_decompressed_responses_body_bytes)
    return settings.max_decompressed_body_bytes


class RequestDecompressionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_encoding = Headers(scope=scope).get("content-encoding")
        if not content_encoding:
            await self.app(scope, receive, send)
            return
        encodings = [enc.strip().lower() for enc in content_encoding.split(",") if enc.strip()]
        if not encodings:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            msg_type = message["type"]
            if msg_type == "http.disconnect":
                return
            if msg_type == "http.request":
                body_part = message.get("body", b"")
                if body_part:
                    chunks.append(body_part)
                if not message.get("more_body", False):
                    break
        body = b"".join(chunks)
        max_size = _max_decompressed_body_bytes_for_path(scope.get("path", ""))
        try:
            decompressed = _decompress_body(body, encodings, max_size)
        except _DecompressedBodyTooLarge:
            response = JSONResponse(
                status_code=413,
                content=dashboard_error(
                    "payload_too_large",
                    "Request body exceeds the maximum allowed size",
                ),
            )
            await response(scope, receive, send)
            return
        except ValueError:
            response = JSONResponse(
                status_code=400,
                content=dashboard_error(
                    "invalid_request",
                    "Unsupported Content-Encoding",
                ),
            )
            await response(scope, receive, send)
            return
        except Exception:
            response = JSONResponse(
                status_code=400,
                content=dashboard_error(
                    "invalid_request",
                    "Request body is compressed but could not be decompressed",
                ),
            )
            await response(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in (b"content-encoding", b"content-length")
        ]
        headers.append((b"content-length", str(len(decompressed)).encode("ascii")))
        updated_scope = {**scope, "headers": headers}
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": decompressed, "more_body": False}
            return await receive()

        await self.app(updated_scope, replay_receive, send)


def add_request_decompression_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestDecompressionMiddleware)
