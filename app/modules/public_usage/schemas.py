from __future__ import annotations

from datetime import datetime

from app.modules.shared.schemas import DashboardModel


class PublicUsagePeriod(DashboardModel):
    days: int
    start: str
    end: str


class PublicUsageTotals(DashboardModel):
    cost_usd: float
    tokens: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    requests: int
    avg_latency_ms: float
    success_rate: float


class PublicUsageDaily(DashboardModel):
    date: str
    cost_usd: float
    tokens: int
    requests: int
    top_model: str


class PublicUsageByModel(DashboardModel):
    model: str
    label: str
    provider: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    tokens: int
    cost_usd: float


class PublicUsageProviderEntry(DashboardModel):
    requests: int
    cost_usd: float
    tokens: int


class PublicUsageByProvider(DashboardModel):
    openai: PublicUsageProviderEntry
    anthropic: PublicUsageProviderEntry


class PublicUsageTrend(DashboardModel):
    t: str
    cost: float
    tokens: int
    requests: int


class PublicUsageResponse(DashboardModel):
    period: PublicUsagePeriod
    generated_at: datetime
    source: str = "live"
    totals: PublicUsageTotals
    daily: list[PublicUsageDaily]
    by_model: list[PublicUsageByModel]
    by_provider: PublicUsageByProvider
    trends: list[PublicUsageTrend]
