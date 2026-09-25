from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.receipts.repository import POOL_IDS, ReceiptFilters, ReceiptsRepository
from app.modules.receipts.schemas import (
    Receipt,
    ReceiptGroup,
    ReceiptSeries,
    ReceiptsResponse,
    ReceiptSummary,
    ReceiptTotals,
)


def _ratio(read: int, input_tokens: int, write: int) -> float | None:
    context = input_tokens + read + write
    return round(read / context, 4) if context else None


def _date(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ReceiptsService:
    def __init__(self, repository: ReceiptsRepository):
        self.repository = repository

    async def page(self, filters: ReceiptFilters, limit: int = 50, offset: int = 0) -> ReceiptsResponse:
        rows, total = await self.repository.page(filters, limit, offset)
        receipts = []
        for log, key_name, key_prefix in rows:
            read = log.cache_read_tokens if log.cache_read_tokens is not None else (log.cached_input_tokens or 0)
            write = log.cache_creation_tokens or 0
            raw = log.input_tokens or 0
            inp = raw if log.provider == "anthropic" else max(raw - read, 0)
            receipts.append(
                Receipt(
                    id=log.id,
                    requested_at=log.requested_at,
                    session_id=log.session_id,
                    client_session_id=log.client_session_id,
                    api_key_id=log.api_key_id,
                    api_key_name=key_name,
                    api_key_prefix=key_prefix,
                    account_id=log.account_id,
                    provider=log.provider,
                    pool=POOL_IDS.get(log.provider, log.provider),
                    model=log.model,
                    reasoning_effort=log.reasoning_effort,
                    input_tokens=inp,
                    output_tokens=log.output_tokens or 0,
                    cache_read_tokens=read,
                    cache_write_tokens=write,
                    cache_read_ratio=_ratio(read, inp, write),
                    latency_ms=log.latency_ms,
                    latency_first_token_ms=log.latency_first_token_ms,
                    status="ok" if log.status == "success" else "error",
                    http_status=log.upstream_status_code,
                    error_code=log.error_code,
                    cost_usd=log.cost_usd,
                )
            )
        return ReceiptsResponse(receipts=receipts, total=total, limit=limit, offset=offset)

    async def summary(self, filters: ReceiptFilters, group_by: str = "account", bucket: str = "6h") -> ReceiptSummary:
        seconds = {"hour": 3600, "6h": 21600, "day": 86400}[bucket]
        total, groups, series_rows, p50, p95, top_error = await self.repository.aggregate(filters, group_by, seconds)
        input_tokens = int(total.input_tokens)
        output_tokens = int(total.output_tokens)
        read = int(total.cache_read_tokens)
        write = int(total.cache_write_tokens)
        requests = int(total.requests)
        errors = int(total.errors)
        totals = ReceiptTotals(
            requests=requests,
            errors=errors,
            error_rate=errors / requests if requests else 0.0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=write,
            tokens=input_tokens + output_tokens + read + write,
            cache_read_ratio=_ratio(read, input_tokens, write),
            cost_usd=round(float(total.cost_usd), 6),
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            top_error_code=top_error,
        )
        group_values = []
        for row in groups:
            count = int(row.requests)
            inp, out, r, w = (
                int(row.input_tokens),
                int(row.output_tokens),
                int(row.cache_read_tokens),
                int(row.cache_write_tokens),
            )
            group_values.append(
                ReceiptGroup(
                    key=str(row.key) if row.key is not None else None,
                    provider=row.provider if group_by in ("account", "pool", "provider") else None,
                    requests=count,
                    errors=int(row.errors),
                    error_rate=int(row.errors) / count if count else 0.0,
                    tokens=inp + out + r + w,
                    cache_read_ratio=_ratio(r, inp, w),
                    cost_usd=round(float(row.cost_usd), 6),
                )
            )
        # The requested window drives zero-fill; without bounds use the observed extent.
        start = filters.since or total.first
        end = filters.until or (_date(total.last) + timedelta(microseconds=1) if total.last else None)
        series = []
        if start is not None and end is not None:
            start_epoch = int(_date(start).timestamp())
            end_epoch = _date(end).timestamp()
            counts: dict[int, dict[str, int]] = {}
            for row in series_rows:
                counts.setdefault(int(row.epoch), {})[row.provider] = int(row.requests)
            for epoch in range(
                start_epoch // seconds * seconds, int((end_epoch - 0.000001) // seconds) * seconds + 1, seconds
            ):
                by_provider = counts.get(epoch, {})
                series.append(
                    ReceiptSeries(
                        start=datetime.fromtimestamp(epoch, timezone.utc),
                        requests=sum(by_provider.values()),
                        by_provider=by_provider,
                    )
                )
        return ReceiptSummary(totals=totals, groups=group_values, series=series)
