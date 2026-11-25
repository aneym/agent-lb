from __future__ import annotations

from dataclasses import dataclass

GEMINI_NEON_MODEL = "gemini-4-neon"
GEMINI_FLASH_LITE_MODEL = "gemini-3.5-flash-lite"
GEMINI_HYDROGEN_SONAR_MODEL = "gemini-4-hydrogen-sonar"
GEMINI_EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
GEMINI_DEFAULT_EFFORT = "high"


@dataclass(frozen=True, slots=True)
class GeminiModel:
    model_id: str
    canonical_model: str
    effort: str | None

    @property
    def auxiliary(self) -> bool:
        return self.canonical_model != GEMINI_NEON_MODEL


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


GEMINI_FLASH_LITE = GeminiModel(GEMINI_FLASH_LITE_MODEL, GEMINI_FLASH_LITE_MODEL, "minimal")
GEMINI_HYDROGEN_SONAR = GeminiModel(GEMINI_HYDROGEN_SONAR_MODEL, GEMINI_HYDROGEN_SONAR_MODEL, "low")

GEMINI_MODEL_REGISTRY = GeminiModelRegistry(
    models=(
        GeminiModel(GEMINI_NEON_MODEL, GEMINI_NEON_MODEL, None),
        *(GeminiModel(f"{GEMINI_NEON_MODEL}-{level}", GEMINI_NEON_MODEL, level) for level in GEMINI_EFFORT_LEVELS),
        GEMINI_FLASH_LITE,
        GEMINI_HYDROGEN_SONAR,
    )
)
