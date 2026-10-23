from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    http_status: int | None = Field(default=None, ge=400, le=599)
    error_code: str | None = None
    message_regex: str | None = None
    action: Literal["pause", "cooldown", "fail_request"]
    reason: str = Field(min_length=1)
    cooldown_seconds: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def validate_match(self) -> Self:
        if self.message_regex is not None:
            try:
                re.compile(self.message_regex, re.IGNORECASE)
            except re.error as exc:
                raise ValueError("Invalid error rule message regex") from exc
        return self
