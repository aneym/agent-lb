from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import select_proxy_authorization, validate_proxy_api_key_authorization
from app.core.exceptions import AppError
from app.core.providers.gemini_models import GEMINI_MODEL_REGISTRY, GeminiModel
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.gemini_errors import GeminiGatewayError, from_app_error, gemini_error_response
from app.modules.proxy.gemini_exchange import GeminiStreamResponse, collect_response
from app.modules.proxy.gemini_gateway import GeminiGateway
from app.modules.proxy.gemini_models import model_resource, model_visible, models_page
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


def _key_allows(api_key: ApiKeyData | None, model: GeminiModel) -> bool:
    try:
        validate_model_access(api_key, model.canonical_model)
    except AppError:
        return False
    return True


def _not_found(name: str) -> GeminiGatewayError:
    return GeminiGatewayError(
        404, f"models/{name} is not found for API version v1beta on this agent-lb.", code="model_not_found"
    )


def _is_antigravity(request: Request) -> bool:
    return request.headers.get("user-agent", "").startswith("antigravity/cli/")


def _visible_model(name: str) -> GeminiModel:
    model = GEMINI_MODEL_REGISTRY.get(name)
    if model is None or not model_visible(model):
        raise _not_found(name.removeprefix("models/"))
    return model


def _page_number(raw: str | None, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 6):
        raise GeminiGatewayError(400, f"{name} must be a non-negative integer.", code="invalid_page_parameter")
    return int(raw)


@router.get("/models")
async def list_models(request: Request) -> Response:
    try:
        api_key = await _authenticate(request)
        page_size = _page_number(request.query_params.get("pageSize"), "pageSize")
        start = _page_number(request.query_params.get("pageToken"), "pageToken") or 0
        await _gateway(request).require_pool(api_key)
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
    return JSONResponse(
        models_page(page_size, start, lambda model: _key_allows(api_key, model), antigravity=_is_antigravity(request)),
        headers={"Vary": "User-Agent"},
    )


@router.get("/models/{name}")
async def get_model(request: Request, name: str) -> Response:
    try:
        api_key = await _authenticate(request)
        model = _visible_model(name)
        if not _key_allows(api_key, model):
            raise _not_found(model.model_id)
        await _gateway(request).require_pool(api_key)
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
    return JSONResponse(model_resource(model, antigravity=_is_antigravity(request)), headers={"Vary": "User-Agent"})


@router.post("/models/{target}")
async def generate(request: Request, target: str) -> Response:
    name, _, method = target.rpartition(":")
    gateway = _gateway(request)
    try:
        if method not in _METHODS:
            raise GeminiGatewayError(
                404, f"Method {method or target} is not served on v1beta.", code="method_not_found"
            )
        model = _visible_model(name)
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
            choice=model,
            body=body,
            raw_size=len(raw),
            useragent=request.headers.get("user-agent"),
        )
        if method == "streamGenerateContent":
            return GeminiStreamResponse(gateway, attempt, sse=request.query_params.get("alt") == "sse")
        return JSONResponse(await collect_response(gateway, attempt))
    except AppError as exc:
        return gemini_error_response(request, from_app_error(exc))
    except GeminiGatewayError as exc:
        return gemini_error_response(request, exc)
