from __future__ import annotations

from fastapi import APIRouter, Depends, Security

from app.core.auth.dependencies import set_openai_error_format, validate_proxy_api_key
from app.core.middleware.api_firewall import parse_trusted_proxy_networks
from app.modules.proxy.anthropic_routes import anthropic_routes_frame_db_router, anthropic_routes_v1_router
from app.modules.proxy.anthropic_service import anthropic_request_session_id, is_fable_model
from app.modules.proxy.api_dependencies import proxy_dependencies
from app.modules.proxy.chat_routes import chat_routes_v1_router
from app.modules.proxy.compact_requests import compact_requests_router, compact_requests_v1_router
from app.modules.proxy.control_routes import (
    control_routes_router,
    control_routes_usage_router,
    control_routes_wham_router,
)
from app.modules.proxy.helpers import rate_limit_details
from app.modules.proxy.media_routes import (
    media_routes_files_router,
    media_routes_transcribe_router,
    media_routes_v1_router,
)
from app.modules.proxy.response_routes import (
    response_routes_internal_router,
    response_routes_router,
    response_routes_v1_router,
    response_routes_v1_ws_router,
    response_routes_ws_router,
)
from app.modules.proxy.usage_routes import usage_routes_router, usage_routes_usage_router, usage_routes_v1_router

router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


ws_router = APIRouter(
    tags=["proxy"],
)


wham_router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


v1_router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


frame_db_router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


v1_ws_router = APIRouter(
    tags=["proxy"],
)


usage_router = APIRouter(
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)


transcribe_router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


files_router = APIRouter(
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)


internal_router = APIRouter(
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)


usage_router.include_router(control_routes_usage_router)
router.include_router(control_routes_router)
wham_router.include_router(control_routes_wham_router)
router.include_router(response_routes_router)
ws_router.include_router(response_routes_ws_router)
v1_router.include_router(anthropic_routes_v1_router)
frame_db_router.include_router(anthropic_routes_frame_db_router)
v1_router.include_router(response_routes_v1_router)
internal_router.include_router(response_routes_internal_router)
v1_ws_router.include_router(response_routes_v1_ws_router)
router.include_router(usage_routes_router)
v1_router.include_router(usage_routes_v1_router)
transcribe_router.include_router(media_routes_transcribe_router)
files_router.include_router(media_routes_files_router)
v1_router.include_router(media_routes_v1_router)
v1_router.include_router(chat_routes_v1_router)
router.include_router(compact_requests_router)
v1_router.include_router(compact_requests_v1_router)
usage_router.include_router(usage_routes_usage_router)
proxy_dependencies.anthropic_request_session_id = anthropic_request_session_id
proxy_dependencies.is_fable_model = is_fable_model
proxy_dependencies.rate_limit_details = rate_limit_details
proxy_dependencies.parse_trusted_proxy_networks = parse_trusted_proxy_networks
