from __future__ import annotations

from dataclasses import dataclass

GEMINI_NEON_MODEL = "gemini-4-neon"
GEMINI_EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
GEMINI_DEFAULT_THINKING_LEVEL = "HIGH"


@dataclass(frozen=True, slots=True)
class GeminiModel:
    model_id: str
    canonical_model: str
    thinking_level: str


@dataclass(frozen=True, slots=True)
class GeminiModelRegistry:
    models: tuple[GeminiModel, ...]

    def get(self, model_id: str | None) -> GeminiModel | None:
        if not model_id:
            return None
        wanted = model_id.strip().lower().removeprefix("models/")
        return next((model for model in self.models if model.model_id == wanted), None)

    def model_ids(self) -> tuple[str, ...]:
        return tuple(model.model_id for model in self.models)


GEMINI_MODEL_REGISTRY = GeminiModelRegistry(
    models=(
        GeminiModel(GEMINI_NEON_MODEL, GEMINI_NEON_MODEL, GEMINI_DEFAULT_THINKING_LEVEL),
        *(
            GeminiModel(f"{GEMINI_NEON_MODEL}-{level}", GEMINI_NEON_MODEL, level.upper())
            for level in GEMINI_EFFORT_LEVELS
        ),
    )
)
