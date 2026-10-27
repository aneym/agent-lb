from __future__ import annotations

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, bridge_public_contract_error_total
from app.core.openai.models import OpenAIError
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.modules.proxy.constants import logger


def public_contract_error_message(kind: str) -> str:
    if kind == "invalid_json":
        return "Responses stream produced an invalid JSON payload"
    if kind == "invalid_output_item":
        return "Responses stream produced unsupported output items"
    if kind == "upstream_stream_truncated":
        return "Responses stream ended before a terminal event"
    return "Responses stream violated the public contract"


def public_contract_error_envelope(kind: str, message: str) -> OpenAIErrorEnvelopeModel:
    record_public_contract_violation(kind)
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message=message,
            type="server_error",
            code=kind,
        )
    )


def record_public_contract_violation(kind: str) -> None:
    logger.warning("bridge_public_contract_violation kind=%s", kind)
    if PROMETHEUS_AVAILABLE and bridge_public_contract_error_total is not None:
        bridge_public_contract_error_total.labels(kind=kind).inc()
