from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.auth.dependencies import select_proxy_authorization, validate_proxy_api_key_authorization
from app.core.exceptions import AppError
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.gemini_errors import GeminiGatewayError, from_app_error, gemini_error_response
from app.modules.proxy.gemini_exchange import collect_response, stream_bytes
from app.modules.proxy.gemini_gateway import GeminiGateway
from app.modules.proxy.gemini_models import (
    GeminiModelChoice,
    choice_is_visible,
    model_resource,
    models_page,
    resolve_choice,
)
from app.modules.proxy.request_policy import validate_model_access

router = APIRouter(prefix="/v1beta", tags=["gemini"])

_METHODS = frozenset({"generateContent", "streamGenerateContent"})


def _gateway(request: Request) -> GeminiGateway:
    gateway = getattr(request.app.state, "gemini_gateway", None)
    if not isinstance(gateway, GeminiGateway):
        gateway = GeminiGateway()
        request.app.state.gemini_gateway = gateway
    return gateway


async def _authenticate(request: Request) -> ApiKeyData | None:
    authorization = select_proxy_authorization(
        request.headers.get("authorization"), request.headers.get("x-goog-api-key")
    )
    return await validate_proxy_api_key_authorization(authorization, request=request)


def _key_allows(api_key: ApiKeyData | None, choice: GeminiModelChoice) -> bool:
    try:
        validate_model_access(api_key, choice.canonical)
    except AppError:
        return False
    return True


def _not_found(name: str) -> GeminiGatewayError:
    return GeminiGatewayError(
        404, f"models/{name} is not found for API version v1beta on this agent-lb.", code="model_not_found"
    )


def _visible_choice(name: str) -> GeminiModelChoice:
    choice = resolve_choice(name)
    if choice is None or not choice_is_visible(choice):
        raise _not_found(name.removeprefix("models/"))
    return choice


def _page_size(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    if not raw.strip().isdigit():
        raise GeminiGatewayError(400, "pageSize must be a positive integer.", code="invalid_page_size")
    return int(raw)


@router.get("/models")
async def list_models(request: Request) -> Response:
    try:
        api_key = await _authenticate(request)
        page_size = _page_size(request.query_params.get("pageSize"))
        await _gateway(request).require_pool(api_key)
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
    page = models_page(page_size, request.query_params.get("pageToken"))
    page["models"] = [
        model
        for model in page["models"]
        if (choice := resolve_choice(model["name"])) is not None and _key_allows(api_key, choice)
    ]
    return JSONResponse(page)


@router.get("/models/{name}")
async def get_model(request: Request, name: str) -> Response:
    try:
        api_key = await _authenticate(request)
        choice = _visible_choice(name)
        if not _key_allows(api_key, choice):
            raise _not_found(choice.requested)
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
    return JSONResponse(model_resource(choice))


@router.post("/models/{target}")
async def generate(request: Request, target: str) -> Response:
    name, _, method = target.rpartition(":")
    gateway = _gateway(request)
    try:
        if method not in _METHODS:
            raise GeminiGatewayError(
                404, f"Method {method or target} is not served on v1beta.", code="method_not_found"
            )
        choice = _visible_choice(name)
        raw = await request.body()
        try:
            body = json.loads(raw or b"{}")
        except ValueError as exc:
            raise GeminiGatewayError(400, "Request body is not valid JSON.", code="invalid_json") from exc
        if not isinstance(body, dict):
            raise GeminiGatewayError(400, "Request body must be a JSON object.", code="invalid_body")
        api_key = await _authenticate(request)
        attempt = await gateway.open(
            api_key=api_key,
            choice=choice,
            body=body,
            raw_size=len(raw),
            useragent=request.headers.get("user-agent"),
        )
        if method == "streamGenerateContent":
            sse = request.query_params.get("alt") == "sse"
            return StreamingResponse(
                stream_bytes(gateway, attempt, sse=sse),
                media_type="text/event-stream" if sse else "application/json",
            )
        return JSONResponse(await collect_response(gateway, attempt))
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
