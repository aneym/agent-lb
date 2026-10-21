from app.audit_token_report import cache_kind
from app.core.prompt_tokens import prompt_split
from app.modules.session_trace.repository import SessionTraceRepository
from app.modules.session_trace.schemas import SessionTraceResponse, TraceParts, TraceRequest, TraceTotals


def apportion(prompt: int, parts: TraceParts) -> TraceParts:
    values = list(parts.model_dump().values())
    total = sum(values)
    if not total or not prompt:
        return TraceParts()
    shares = [prompt * value // total for value in values]
    order = sorted(range(4), key=lambda i: -(prompt * values[i] % total))
    for index in order[: prompt - sum(shares)]:
        shares[index] += 1
    return TraceParts(system=shares[0], tools=shares[1], history=shares[2], tool_results=shares[3])


class SessionTraceService:
    def __init__(self, repository: SessionTraceRepository) -> None:
        self._repository = repository

    async def trace(self, session_id: str, limit: int) -> SessionTraceResponse | None:
        rows = await self._repository.rows(session_id, limit)
        if not rows:
            return None
        requests = []
        previous = None
        for log, captured in reversed(rows):
            uncached, read, write, prompt = prompt_split(
                log.provider,
                log.input_tokens,
                log.cached_input_tokens,
                log.cache_read_tokens,
                log.cache_creation_tokens,
            )
            audit_row = {
                "timestamp": log.requested_at.isoformat(),
                "account_id": log.account_id,
                "input_tokens": log.input_tokens,
                "cache_creation_tokens": log.cache_creation_tokens,
                "cache_read_tokens": log.cache_read_tokens,
            }
            chars = None
            if captured is not None and captured.body_schema is not None:
                chars = TraceParts(
                    system=captured.system_chars or 0,
                    tools=captured.tools_chars or 0,
                    history=captured.history_chars or 0,
                    tool_results=captured.tool_result_chars or 0,
                )
            total_chars = sum(chars.model_dump().values()) if chars is not None else 0
            requests.append(
                TraceRequest(
                    log_id=log.id,
                    request_id=log.request_id,
                    requested_at=log.requested_at,
                    provider=log.provider,
                    model=log.model,
                    status=log.status,
                    latency_ms=log.latency_ms,
                    latency_first_token_ms=log.latency_first_token_ms,
                    prompt_tokens=prompt,
                    uncached_tokens=uncached,
                    cache_read_tokens=read,
                    cache_write_tokens=write,
                    output_tokens=log.output_tokens or 0,
                    cache_read_ratio=round(read / prompt, 4) if prompt else None,
                    cache_kind=cache_kind(audit_row, previous),
                    body_schema=captured.body_schema if captured else None,
                    body_bytes=captured.body_bytes if captured else None,
                    est_parts=apportion(prompt, chars) if chars is not None else None,
                    part_chars=chars,
                    estimate_ratio=round(total_chars / 4 / prompt, 3) if chars is not None and prompt else None,
                )
            )
            previous = audit_row
        measured = sum(row.part_chars is not None for row in requests)
        summed = {
            key: sum(getattr(row.est_parts, key) for row in requests if row.est_parts is not None)
            for key in TraceParts.model_fields
        }
        return SessionTraceResponse(
            session_id=session_id,
            requests=requests,
            totals=TraceTotals(
                requests=len(requests),
                measured=measured,
                coverage=round(measured / len(requests), 4),
                prompt_tokens=sum(row.prompt_tokens for row in requests),
                cache_read_tokens=sum(row.cache_read_tokens for row in requests),
                latency_ms=sum(row.latency_ms or 0 for row in requests),
                est_parts=TraceParts(**summed),
            ),
        )
