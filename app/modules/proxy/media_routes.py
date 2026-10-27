from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, Path, Request, Response, Security, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.auth.dependencies import validate_proxy_api_key
from app.core.clients.files import FileProxyError
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.images import V1ImagesEditsForm, V1ImagesGenerationsRequest
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy import images_service as images_service_module
from app.modules.proxy.constants import FILES_CREATE_LIMIT_MODEL, FILES_FINALIZE_LIMIT_MODEL, TRANSCRIPTION_MODEL
from app.modules.proxy.image_requests import proxy_images_edit_request, proxy_images_generation_request
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.request_policy import openai_validation_error, validate_model_access
from app.modules.proxy.reservations import release_reservation
from app.modules.proxy.response_errors import (
    logged_error_json_response,
    openai_invalid_transcription_model_error,
    parse_error_envelope,
)
from app.modules.proxy.schemas import FileCreateRequest

media_routes_transcribe_router = APIRouter(prefix="/backend-api")


media_routes_files_router = APIRouter(prefix="/backend-api")


media_routes_v1_router = APIRouter(prefix="/v1")


@media_routes_transcribe_router.post("/transcribe")
async def backend_transcribe(
    request: Request,
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    response = await transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )
    return response


@media_routes_files_router.post("/files")
async def backend_files_create(
    request: Request,
    payload: FileCreateRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:

    reservation = await enforce_request_limits(
        api_key,
        request_model=FILES_CREATE_LIMIT_MODEL,
        request_service_tier=None,
        request_is_file_operation=True,
    )
    try:
        result = await context.service.create_file(
            payload.model_dump(mode="json", exclude_none=True),
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = parse_error_envelope(exc.payload)
        return logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = parse_error_envelope(exc.payload)
        return logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await release_reservation(reservation)
    return JSONResponse(content=result)


@media_routes_files_router.post("/files/{file_id}/uploaded")
async def backend_files_finalize(
    request: Request,
    file_id: str = Path(..., min_length=1),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:

    reservation = await enforce_request_limits(
        api_key,
        request_model=FILES_FINALIZE_LIMIT_MODEL,
        request_service_tier=None,
        request_is_file_operation=True,
    )
    try:
        result = await context.service.finalize_file(
            file_id,
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = parse_error_envelope(exc.payload)
        return logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = parse_error_envelope(exc.payload)
        return logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await release_reservation(reservation)
    return JSONResponse(content=result)


@media_routes_v1_router.post("/audio/transcriptions")
async def v1_audio_transcriptions(
    request: Request,
    model: str = Form(...),
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    if model != TRANSCRIPTION_MODEL:
        return logged_error_json_response(
            request,
            status_code=400,
            content=openai_invalid_transcription_model_error(model),
        )
    return await transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )


@media_routes_v1_router.post("/images/generations", response_model=None)
async def v1_images_generations(
    request: Request,
    payload: V1ImagesGenerationsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    response = await proxy_images_generation_request(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
    )
    return response


@media_routes_v1_router.post("/images/edits", response_model=None)
async def v1_images_edits(
    request: Request,
    model: str | None = Form(None),
    prompt: str = Form(...),
    image: list[UploadFile] | None = File(None),
    image_brackets: list[UploadFile] | None = File(None, alias="image[]"),
    mask: UploadFile | None = File(None),
    n: str | None = Form(None),
    size: str | None = Form(None),
    quality: str | None = Form(None),
    background: str | None = Form(None),
    output_format: str | None = Form(None),
    output_compression: str | None = Form(None),
    moderation: str | None = Form(None),
    partial_images: str | None = Form(None),
    stream: str | None = Form(None),
    input_fidelity: str | None = Form(None),
    user: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    raw_form: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "size": size if size is not None else "auto",
        "quality": quality if quality is not None else "auto",
        "background": background if background is not None else "auto",
        "output_format": output_format if output_format is not None else "png",
        "moderation": moderation if moderation is not None else "auto",
        "input_fidelity": input_fidelity,
        "user": user,
    }

    if n is not None:
        raw_form["n"] = n
    else:
        raw_form["n"] = 1
    if output_compression is not None:
        raw_form["output_compression"] = output_compression
    else:
        raw_form["output_compression"] = 100
    if partial_images is not None:
        raw_form["partial_images"] = partial_images
    if stream is not None:
        raw_form["stream"] = stream
    else:
        raw_form["stream"] = False
    try:
        form_payload = V1ImagesEditsForm.model_validate(raw_form)
    except ValidationError as exc:
        return logged_error_json_response(request, 400, openai_validation_error(exc))

    merged_images: list[UploadFile] = []
    if image:
        merged_images.extend(image)
    if image_brackets:
        merged_images.extend(image_brackets)
    if not merged_images:
        return logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "At least one ``image`` (or ``image[]``) multipart part is required.",
                param="image",
            ),
        )

    images_payload: list[tuple[bytes, str | None]] = []
    for upload in merged_images:
        try:
            data = await upload.read()
        finally:
            await upload.close()
        if not data:
            return logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "image part is empty",
                    param="image",
                ),
            )
        images_payload.append((data, upload.content_type))

    mask_payload: tuple[bytes, str | None] | None = None
    if mask is not None:
        try:
            data = await mask.read()
        finally:
            await mask.close()
        if not data:
            return logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "mask part is empty",
                    param="mask",
                ),
            )
        mask_payload = (data, mask.content_type)

    return await proxy_images_edit_request(
        request=request,
        payload=form_payload,
        images=images_payload,
        mask=mask_payload,
        context=context,
        api_key=api_key,
    )


@media_routes_v1_router.post("/images/variations", include_in_schema=False)
async def v1_images_variations(
    request: Request,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:

    del api_key
    return logged_error_json_response(
        request,
        status_code=404,
        content=images_service_module.make_not_found_error(
            "/v1/images/variations is not supported by agent-lb. Use /v1/images/edits with an explicit prompt instead."
        ),
    )


async def transcribe_request(
    *,
    request: Request,
    file: UploadFile,
    prompt: str | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> JSONResponse:
    validate_model_access(api_key, TRANSCRIPTION_MODEL)
    reservation = await enforce_request_limits(
        api_key,
        request_model=TRANSCRIPTION_MODEL,
        request_service_tier=None,
    )
    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        audio_bytes = await file.read()
        result = await context.service.transcribe(
            audio_bytes=audio_bytes,
            filename=file.filename or "audio.wav",
            content_type=file.content_type,
            prompt=prompt,
            headers=request.headers,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        error = parse_error_envelope(exc.payload)
        return logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await release_reservation(reservation)
    return JSONResponse(content=result, headers=rate_limit_headers)
