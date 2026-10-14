

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.openai.exceptions import ClientPayloadError
from app.core.types import JsonValue

GPT_IMAGE_MODEL_PREFIX: Final[str] = "gpt-image-"


_GPT_IMAGE_2_MODELS: Final[frozenset[str]] = frozenset({"gpt-image-2"})



_LEGACY_GPT_IMAGE_MODELS: Final[frozenset[str]] = frozenset({"gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"})

_GPT_IMAGE_2_QUALITY: Final[frozenset[str]] = frozenset({"low", "medium", "high", "auto"})




_LEGACY_QUALITY: Final[frozenset[str]] = frozenset({"low", "medium", "high", "auto"})




_INPUT_FIDELITY_SUPPORTED_MODELS: Final[frozenset[str]] = frozenset({"gpt-image-1.5", "gpt-image-1"})
_LEGACY_FIXED_SIZES: Final[frozenset[str]] = frozenset({"1024x1024", "1536x1024", "1024x1536", "auto"})
_BACKGROUND_VALUES: Final[frozenset[str]] = frozenset({"transparent", "opaque", "auto"})
_OUTPUT_FORMATS: Final[frozenset[str]] = frozenset({"png", "jpeg", "webp"})
_MODERATION_VALUES: Final[frozenset[str]] = frozenset({"auto", "low"})
_INPUT_FIDELITY_VALUES: Final[frozenset[str]] = frozenset({"low", "high"})


_GPT_IMAGE_2_MAX_EDGE: Final[int] = 3840
_GPT_IMAGE_2_MIN_PIXELS: Final[int] = 655_360
_GPT_IMAGE_2_MAX_PIXELS: Final[int] = 8_294_400
_GPT_IMAGE_2_RATIO_MAX: Final[float] = 3.0
_GPT_IMAGE_2_DIM_MULTIPLE: Final[int] = 16

_SIZE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d+)x(\d+)$")


def _images_invalid(
    message: str, *, param: str | None = None, code: str = "invalid_request_error"
) -> ClientPayloadError:

    return ClientPayloadError(
        message,
        param=param,
        code=code,
        error_type="invalid_request_error",
    )


def is_supported_image_model(model: str) -> bool:
    return model.startswith(GPT_IMAGE_MODEL_PREFIX) and (
        model in _GPT_IMAGE_2_MODELS or model in _LEGACY_GPT_IMAGE_MODELS
    )


def validate_image_size(model: str, size: str) -> None:

    if size == "auto":
        return
    match = _SIZE_PATTERN.match(size)
    if match is None:
        raise _images_invalid(
            f"Invalid size '{size}'. Expected 'auto' or 'WIDTHxHEIGHT'.",
            param="size",
        )
    width = int(match.group(1))
    height = int(match.group(2))
    if model in _GPT_IMAGE_2_MODELS:
        _validate_gpt_image_2_size(width, height)
        return

    if size not in _LEGACY_FIXED_SIZES:
        raise _images_invalid(
            f"Invalid size '{size}' for model '{model}'. Allowed sizes: 1024x1024, 1536x1024, 1024x1536, auto.",
            param="size",
        )


def _validate_gpt_image_2_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise _images_invalid("size dimensions must be positive integers", param="size")
    if width % _GPT_IMAGE_2_DIM_MULTIPLE != 0 or height % _GPT_IMAGE_2_DIM_MULTIPLE != 0:
        raise _images_invalid(
            f"size dimensions must be multiples of {_GPT_IMAGE_2_DIM_MULTIPLE} for gpt-image-2",
            param="size",
        )
    if max(width, height) > _GPT_IMAGE_2_MAX_EDGE:
        raise _images_invalid(
            f"size edges must be <= {_GPT_IMAGE_2_MAX_EDGE} px for gpt-image-2",
            param="size",
        )
    long_edge = max(width, height)
    short_edge = min(width, height)
    if short_edge == 0 or (long_edge / short_edge) > _GPT_IMAGE_2_RATIO_MAX:
        raise _images_invalid(
            f"size aspect ratio must be at most {int(_GPT_IMAGE_2_RATIO_MAX)}:1 for gpt-image-2",
            param="size",
        )
    pixels = width * height
    if pixels < _GPT_IMAGE_2_MIN_PIXELS or pixels > _GPT_IMAGE_2_MAX_PIXELS:
        raise _images_invalid(
            f"size total pixels must be between {_GPT_IMAGE_2_MIN_PIXELS} and "
            f"{_GPT_IMAGE_2_MAX_PIXELS} for gpt-image-2",
            param="size",
        )


def validate_image_request_parameters(
    *,
    model: str,
    quality: str,
    size: str,
    background: str,
    output_format: str,
    moderation: str,
    input_fidelity: str | None,
    is_edit: bool,
    n: int,
    partial_images: int | None,
    output_compression: int,
    images_max_partial_images: int,
) -> None:

    if not is_supported_image_model(model):
        raise _images_invalid(
            f"Unsupported image model '{model}'. Use a 'gpt-image-*' model.",
            param="model",
        )










    if n < 1 or n > 1:
        raise _images_invalid(
            "n must be 1; multiple images per request are not supported by the "
            "upstream image_generation tool yet. Issue the request multiple "
            "times to get more images.",
            param="n",
        )

    if background not in _BACKGROUND_VALUES:
        raise _images_invalid(
            f"Invalid background '{background}'. Expected one of: " + ", ".join(sorted(_BACKGROUND_VALUES)),
            param="background",
        )

    if output_format not in _OUTPUT_FORMATS:
        raise _images_invalid(
            f"Invalid output_format '{output_format}'. Expected one of: png, jpeg, webp.",
            param="output_format",
        )

    if not 0 <= output_compression <= 100:
        raise _images_invalid(
            "output_compression must be between 0 and 100",
            param="output_compression",
        )

    if moderation not in _MODERATION_VALUES:
        raise _images_invalid(
            f"Invalid moderation '{moderation}'. Expected one of: auto, low.",
            param="moderation",
        )

    if partial_images is not None:
        if partial_images < 0 or partial_images > images_max_partial_images:
            raise _images_invalid(
                f"partial_images must be between 0 and {images_max_partial_images}",
                param="partial_images",
            )

    if model in _GPT_IMAGE_2_MODELS:
        if quality not in _GPT_IMAGE_2_QUALITY:
            raise _images_invalid(
                f"Invalid quality '{quality}' for gpt-image-2. Expected one of: low, medium, high, auto.",
                param="quality",
            )
        if background == "transparent":
            raise _images_invalid(
                "background='transparent' is not supported by gpt-image-2",
                param="background",
            )
        if input_fidelity is not None:
            raise _images_invalid(
                "input_fidelity is not supported by gpt-image-2",
                param="input_fidelity",
            )
    else:

        if quality not in _LEGACY_QUALITY:
            raise _images_invalid(
                f"Invalid quality '{quality}' for model '{model}'.",
                param="quality",
            )
        if input_fidelity is not None:
            if not is_edit:
                raise _images_invalid(
                    "input_fidelity is only supported on /v1/images/edits",
                    param="input_fidelity",
                )
            if model not in _INPUT_FIDELITY_SUPPORTED_MODELS:
                raise _images_invalid(
                    f"input_fidelity is not supported by {model}",
                    param="input_fidelity",
                )
            if input_fidelity not in _INPUT_FIDELITY_VALUES:
                raise _images_invalid(
                    f"Invalid input_fidelity '{input_fidelity}'. Expected one of: low, high.",
                    param="input_fidelity",
                )

    validate_image_size(model, size)







class V1ImagesGenerationsRequest(BaseModel):


    model_config = ConfigDict(extra="ignore")




    model: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    n: int = 1
    size: str = "auto"
    quality: str = "auto"
    background: str = "auto"
    output_format: str = "png"
    output_compression: int = 100
    moderation: str = "auto"
    partial_images: int | None = None
    stream: bool = False



    input_fidelity: str | None = None
    user: str | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_supported_image_model(value):
            raise ValueError(f"Unsupported image model '{value}'. Use a 'gpt-image-*' model.")
        return value


class V1ImagesEditsForm(BaseModel):


    model_config = ConfigDict(extra="ignore")




    model: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    n: int = 1
    size: str = "auto"
    quality: str = "auto"
    background: str = "auto"
    output_format: str = "png"
    output_compression: int = 100
    moderation: str = "auto"
    partial_images: int | None = None
    stream: bool = False
    input_fidelity: str | None = None
    user: str | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_supported_image_model(value):
            raise ValueError(f"Unsupported image model '{value}'. Use a 'gpt-image-*' model.")
        return value







class V1ImageData(BaseModel):


    model_config = ConfigDict(extra="ignore")

    b64_json: str
    revised_prompt: str | None = None


class V1ImageUsage(BaseModel):




    model_config = ConfigDict(extra="allow")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    input_tokens_details: dict[str, JsonValue] | None = None
    output_tokens_details: dict[str, JsonValue] | None = None


class V1ImageResponse(BaseModel):


    model_config = ConfigDict(extra="ignore")

    created: int
    data: list[V1ImageData]
    usage: V1ImageUsage | None = None


__all__ = [
    "GPT_IMAGE_MODEL_PREFIX",
    "V1ImageData",
    "V1ImageResponse",
    "V1ImageUsage",
    "V1ImagesEditsForm",
    "V1ImagesGenerationsRequest",
    "is_supported_image_model",
    "validate_image_request_parameters",
    "validate_image_size",
]
