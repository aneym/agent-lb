from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class UpstreamObservation:
    status: int | None = None
    error_class: str | None = None


observation: ContextVar[UpstreamObservation | None] = ContextVar("upstream_observation", default=None)


def observe_status(status: int) -> None:
    current = observation.get()
    if current is not None:
        current.status = status
        current.error_class = None


def observe_error(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    response = payload.get("response")
    source = response if isinstance(response, dict) else payload
    error = source.get("error")
    if not isinstance(error, dict):
        return
    code = error.get("code") or error.get("type")
    status = payload.get("status_code") or error.get("status_code")
    previous = observation.get()
    if not isinstance(status, int) or not 100 <= status <= 599:
        status = previous.status if previous else None
    if previous is not None:
        previous.status = status
        previous.error_class = code if isinstance(code, str) else None


def observe_text(text: str) -> None:
    try:
        observe_error(json.loads(text))
    except (ValueError, TypeError):
        pass


def failure_fields(
    status: int | None, upstream_code: str | None, error_code: str | None
) -> tuple[int | None, str | None]:
    observed = observation.get()
    if status is None and observed is not None:
        status = observed.status
    if upstream_code is None and observed is not None:
        upstream_code = observed.error_class
    if status is None and error_code and (match := re.fullmatch(r"upstream_([45]\d\d)", error_code)):
        status = int(match[1])
    return status, upstream_code or error_code
