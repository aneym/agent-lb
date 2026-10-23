from __future__ import annotations

import json
from contextvars import ContextVar
from fnmatch import fnmatchcase

from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config.settings import get_settings
from app.core.model_payloads import rewrite_models

requested_model_alias: ContextVar[str | None] = ContextVar("requested_model_alias", default=None)


def model_is_excluded(model: str) -> bool:
    return any(fnmatchcase(model, pattern) for pattern in get_settings().excluded_model_patterns)


def resolve_model(model: str) -> str:
    return get_settings().model_aliases.get(model, model)


def model_is_visible(model: str) -> bool:
    return not model_is_excluded(model) and not model_is_excluded(resolve_model(model))


def model_error(model: str, anthropic: bool = False) -> JSONResponse:
    error = {"type": "invalid_request_error", "message": f"Model '{model}' is excluded by operator policy"}
    if anthropic:
        return JSONResponse({"type": "error", "error": error}, status_code=400)
    return JSONResponse({"error": {**error, "code": "model_not_allowed", "param": "model"}}, status_code=400)


class ModelExcluded(ValueError):
    pass


class Aliases:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "").rstrip("/")
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        route_scope = {**scope, "path": path}
        if not any(
            getattr(getattr(route, "endpoint", None), "__module__", "").startswith("app.modules.proxy.")
            and route.matches(route_scope)[0] == Match.FULL
            for route in scope["app"].routes
        ):
            await self.app(scope, receive, send)
            return
        token = requested_model_alias.set(None)
        try:
            if scope["type"] == "websocket":

                async def receive_frame() -> Message:
                    message = await receive()
                    if message["type"] != "websocket.receive":
                        return message
                    raw = message.get("text") or message.get("bytes")
                    if not isinstance(raw, (str, bytes)):
                        return message
                    try:
                        body = json.loads(raw)
                    except (ValueError, TypeError):
                        return message
                    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
                        return message
                    model = body["model"]
                    if not model_is_visible(model):
                        await send(
                            {
                                "type": "websocket.send",
                                "text": json.dumps({"type": "error", **json.loads(bytes(model_error(model).body))}),
                            }
                        )
                        return await receive_frame()
                    target = resolve_model(model)
                    requested_model_alias.set(model if target != model else None)
                    body["model"] = target
                    return {"type": "websocket.receive", "text": json.dumps(body)}

                await self.app(scope, receive_frame, send)
                return
            if scope.get("method") != "POST":
                await self.app(scope, receive, send)
                return
            chunks = bytearray()
            limit = max(
                get_settings().max_decompressed_body_bytes, get_settings().max_decompressed_responses_body_bytes
            )
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunks.extend(message.get("body", b""))
                if len(chunks) > limit:
                    await JSONResponse(
                        {"error": {"type": "invalid_request_error", "message": "Request body too large"}},
                        status_code=413,
                    )(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            raw = bytes(chunks)

            def rewrite(model: str) -> str:
                if not model_is_visible(model):
                    raise ModelExcluded(model)
                target = resolve_model(model)
                if target != model:
                    requested_model_alias.set(model)
                return target

            content_type = next((v for k, v in scope["headers"] if k.lower() == b"content-type"), b"")
            try:
                rewritten = rewrite_models(raw, content_type, rewrite)
            except ModelExcluded as exc:
                await model_error(str(exc), "/messages" in path)(scope, receive, send)
                return
            if rewritten != raw:
                raw = rewritten
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
                scope["headers"].append((b"content-length", str(len(raw)).encode()))
            delivered = False

            async def receive_body() -> Message:
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}

            await self.app(scope, receive_body, send)
        finally:
            requested_model_alias.reset(token)
