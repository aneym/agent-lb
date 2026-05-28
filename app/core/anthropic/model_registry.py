from __future__ import annotations

import time
from dataclasses import dataclass
from typing import AsyncContextManager, Protocol

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, TypeAdapter, ValidationError

from app.core.types import JsonValue


class AnthropicModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: StrictStr
    type: StrictStr | None = None
    display_name: StrictStr
    created_at: StrictStr | None = None


class AnthropicModelList(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[AnthropicModel]
    first_id: StrictStr | None = None
    last_id: StrictStr | None = None
    has_more: StrictBool | None = None


@dataclass(frozen=True, slots=True)
class AnthropicModelRegistrySnapshot:
    models: dict[str, AnthropicModel]
    fetched_at: float


class AnthropicModelRegistry:
    def __init__(self) -> None:
        self._snapshot: AnthropicModelRegistrySnapshot | None = None

    def get_snapshot(self) -> AnthropicModelRegistrySnapshot | None:
        return self._snapshot

    def get_models_with_fallback(self) -> dict[str, AnthropicModel]:
        if self._snapshot is None:
            return {}
        return self._snapshot.models

    def update_from_payload(self, payload: JsonValue) -> AnthropicModelRegistrySnapshot:
        model_list = parse_model_list_payload(payload)
        models = {model.id: model for model in model_list.data}
        self._snapshot = AnthropicModelRegistrySnapshot(models=models, fetched_at=time.monotonic())
        return self._snapshot


_MODEL_LIST_ADAPTER = TypeAdapter(AnthropicModelList)


class ModelListResponse(Protocol):
    def raise_for_status(self) -> None: ...

    async def json(self) -> JsonValue: ...


class ModelListSession(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> AsyncContextManager[ModelListResponse]: ...


def parse_model_list_payload(payload: JsonValue) -> AnthropicModelList:
    try:
        return _MODEL_LIST_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValueError("Invalid Anthropic model list payload") from exc


async def fetch_model_list(
    session: ModelListSession,
    *,
    base_url: str,
    headers: dict[str, str] | None = None,
) -> AnthropicModelList:
    url = f"{base_url.rstrip('/')}/v1/models"
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        payload = await response.json()
    return parse_model_list_payload(payload)
