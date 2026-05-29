from __future__ import annotations

import re
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.modules.public_usage.schemas import (
    PublicUsageByModel,
    PublicUsageByProvider,
    PublicUsageDaily,
    PublicUsagePeriod,
    PublicUsageProviderEntry,
    PublicUsageResponse,
    PublicUsageTotals,
    PublicUsageTrend,
)

# Mirrors the warm-up exclusion used by the dashboard usage aggregations.
_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"

_MIN_DAYS = 7
_MAX_DAYS = 730

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_KNOWN_MODEL_LABELS: dict[str, str] = {
    "claude-opus-4-5": "Claude Opus 4.5",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-opus-4-1": "Claude Opus 4.1",
    "claude-sonnet-4": "Claude Sonnet 4",
    "claude-3-5-sonnet": "Claude 3.5 Sonnet",
    "claude-3-5-haiku": "Claude 3.5 Haiku",
}


def _model_label(model: str | None) -> str:
    if not model:
        return "Unknown"
    base = _DATE_SUFFIX.sub("", model.lower())
    return _KNOWN_MODEL_LABELS.get(base, _DATE_SUFFIX.sub("", model))


def _normalize_date(value: object) -> str:
    if isinstance(value, (datetime, date_cls)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _top_model(model_cost: dict[str, float], model_req: dict[str, int]) -> str | None:
    candidates = set(model_cost) | set(model_req)
    if not candidates:
        return None
    return max(candidates, key=lambda m: (model_cost.get(m, 0.0), model_req.get(m, 0)))


async def build_public_usage(session: AsyncSession, days: int) -> PublicUsageResponse:
    """Aggregate request_logs into the public, anonymized usage contract.

    Only rolled-up numbers are returned — no account_id, email, api_key, request
    bodies, IPs, or raw error text ever leaves this function.
    """
    days = max(_MIN_DAYS, min(_MAX_DAYS, days))
    now = utcnow()
    cutoff = now - timedelta(days=days)
    date_expr = func.date(RequestLog.requested_at)

    stmt = (
        select(
            date_expr.label("d"),
            RequestLog.model.label("model"),
            RequestLog.provider.label("provider"),
            func.count(RequestLog.id).label("requests"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(RequestLog.latency_ms), 0).label("latency_sum"),
            func.coalesce(func.sum(case((RequestLog.latency_ms.isnot(None), 1), else_=0)), 0).label("latency_count"),
            func.coalesce(func.sum(case((RequestLog.status == "success", 1), else_=0)), 0).label("success_count"),
        )
        .where(RequestLog.requested_at >= cutoff)
        .where((RequestLog.source.is_(None)) | (RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE))
        .group_by(date_expr, RequestLog.model, RequestLog.provider)
    )
    rows = (await session.execute(stmt)).all()

    t_cost = 0.0
    t_in = t_out = t_cached = t_reasoning = t_requests = 0
    t_latency_sum = t_latency_count = t_success = 0
    daily_acc: dict[str, dict[str, float]] = {}
    daily_model_cost: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_model_req: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    model_acc: dict[str, dict] = {}
    provider_acc: dict[str, dict] = {
        OPENAI_PROVIDER_NAME: {"requests": 0, "cost_usd": 0.0, "tokens": 0},
        ANTHROPIC_PROVIDER_NAME: {"requests": 0, "cost_usd": 0.0, "tokens": 0},
    }

    for r in rows:
        d = _normalize_date(r.d)
        model = r.model or "unknown"
        provider = r.provider or OPENAI_PROVIDER_NAME
        in_t, out_t = int(r.input_tokens), int(r.output_tokens)
        cached_t, reason_t = int(r.cached_input_tokens), int(r.reasoning_tokens)
        row_tokens = in_t + out_t + cached_t + reason_t
        cost = float(r.cost_usd)
        reqs = int(r.requests)

        t_cost += cost
        t_in += in_t
        t_out += out_t
        t_cached += cached_t
        t_reasoning += reason_t
        t_requests += reqs
        t_latency_sum += int(r.latency_sum)
        t_latency_count += int(r.latency_count)
        t_success += int(r.success_count)

        da = daily_acc.setdefault(d, {"cost_usd": 0.0, "tokens": 0, "requests": 0})
        da["cost_usd"] += cost
        da["tokens"] += row_tokens
        da["requests"] += reqs
        daily_model_cost[d][model] += cost
        daily_model_req[d][model] += reqs

        ma = model_acc.setdefault(
            model,
            {
                "provider": provider,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            },
        )
        ma["provider"] = provider
        ma["requests"] += reqs
        ma["input_tokens"] += in_t
        ma["output_tokens"] += out_t
        ma["cached_input_tokens"] += cached_t
        ma["tokens"] += row_tokens
        ma["cost_usd"] += cost

        pa = provider_acc.setdefault(provider, {"requests": 0, "cost_usd": 0.0, "tokens": 0})
        pa["requests"] += reqs
        pa["cost_usd"] += cost
        pa["tokens"] += row_tokens

    start_date, end_date = cutoff.date(), now.date()
    daily: list[PublicUsageDaily] = []
    trends: list[PublicUsageTrend] = []
    cur = start_date
    while cur <= end_date:
        ds = cur.strftime("%Y-%m-%d")
        da = daily_acc.get(ds)
        if da:
            cost = round(da["cost_usd"], 6)
            daily.append(
                PublicUsageDaily(
                    date=ds,
                    cost_usd=cost,
                    tokens=int(da["tokens"]),
                    requests=int(da["requests"]),
                    top_model=_top_model(daily_model_cost.get(ds, {}), daily_model_req.get(ds, {})) or "",
                )
            )
            trends.append(PublicUsageTrend(t=ds, cost=cost, tokens=int(da["tokens"]), requests=int(da["requests"])))
        else:
            daily.append(PublicUsageDaily(date=ds, cost_usd=0.0, tokens=0, requests=0, top_model=""))
            trends.append(PublicUsageTrend(t=ds, cost=0.0, tokens=0, requests=0))
        cur += timedelta(days=1)

    by_model = [
        PublicUsageByModel(
            model=m,
            label=_model_label(m),
            provider=v["provider"],
            requests=v["requests"],
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            cached_input_tokens=v["cached_input_tokens"],
            tokens=v["tokens"],
            cost_usd=round(v["cost_usd"], 6),
        )
        for m, v in model_acc.items()
    ]
    by_model.sort(key=lambda x: x.cost_usd, reverse=True)

    def _provider_entry(name: str) -> PublicUsageProviderEntry:
        acc = provider_acc[name]
        return PublicUsageProviderEntry(
            requests=acc["requests"],
            cost_usd=round(acc["cost_usd"], 6),
            tokens=acc["tokens"],
        )

    totals = PublicUsageTotals(
        cost_usd=round(t_cost, 6),
        tokens=t_in + t_out + t_cached + t_reasoning,
        input_tokens=t_in,
        output_tokens=t_out,
        cached_input_tokens=t_cached,
        reasoning_tokens=t_reasoning,
        requests=t_requests,
        avg_latency_ms=(round(t_latency_sum / t_latency_count, 2) if t_latency_count else 0.0),
        success_rate=(round(t_success / t_requests, 4) if t_requests else 0.0),
    )

    return PublicUsageResponse(
        period=PublicUsagePeriod(
            days=days,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
        ),
        generated_at=now,
        source="live",
        totals=totals,
        daily=daily,
        by_model=by_model,
        by_provider=PublicUsageByProvider(
            openai=_provider_entry(OPENAI_PROVIDER_NAME),
            anthropic=_provider_entry(ANTHROPIC_PROVIDER_NAME),
        ),
        trends=trends,
    )
