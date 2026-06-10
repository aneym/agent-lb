import type { ApiKey, LimitType } from "@/features/api-keys/schemas";
import { cn } from "@/lib/utils";
import {
  formatCompactNumber,
  formatCurrency,
  formatTimeLong,
} from "@/utils/formatters";

const LIMIT_TYPE_LABEL: Record<LimitType, string> = {
  total_tokens: "Total Tokens",
  input_tokens: "Input Tokens",
  output_tokens: "Output Tokens",
  cost_usd: "Cost (USD)",
  credits: "Credits",
};

export type ApiKeyInfoProps = {
  apiKey: ApiKey;
  usageSummary?: ApiKey["usageSummary"] | null;
  usageMessage?: string | null;
  allowUsageSummaryFallback?: boolean;
};

function formatExpiry(value: string | null): string {
  if (!value) return "Never";
  const parsed = formatTimeLong(value);
  return `${parsed.date} ${parsed.time}`;
}

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

export function ApiKeyInfo({
  apiKey,
  usageSummary,
  usageMessage,
  allowUsageSummaryFallback = true,
}: ApiKeyInfoProps) {
  const expired = isExpired(apiKey);
  const models = apiKey.allowedModels?.join(", ") || "All models";
  const enforcedModel = apiKey.enforcedModel || null;
  const enforcedEffort = apiKey.enforcedReasoningEffort || null;
  const trafficClass =
    apiKey.trafficClass === "opportunistic" ? "Opportunistic" : "Foreground";
  const usage = allowUsageSummaryFallback
    ? (usageSummary ?? apiKey.usageSummary)
    : (usageSummary ?? null);
  const hasUsage = usage && usage.requestCount > 0;

  return (
    <div className="space-y-4 border-t pt-4" data-testid="api-key-info">
      <h3 className="text-[13px] font-semibold text-foreground">Key Details</h3>
      <dl className="space-y-2 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Prefix</dt>
          <dd className="font-mono font-medium tabular-nums">
            {apiKey.keyPrefix}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Models</dt>
          <dd className="text-right font-medium">{models}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Traffic class</dt>
          <dd className="font-medium">{trafficClass}</dd>
        </div>
        {enforcedModel ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Enforced Model</dt>
            <dd className="font-mono font-medium tabular-nums">
              {enforcedModel}
            </dd>
          </div>
        ) : null}
        {enforcedEffort ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Enforced Effort</dt>
            <dd className="font-medium">{enforcedEffort}</dd>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Expiry</dt>
          <dd className={cn(expired ? "font-semibold" : "font-medium")}>
            {expired ? "Expired" : formatExpiry(apiKey.expiresAt)}
          </dd>
        </div>
        <div className="flex items-start justify-between gap-2">
          <dt className="text-muted-foreground">Usage</dt>
          <dd className="text-right font-mono tabular-nums">
            {hasUsage ? (
              <span>
                <span className="font-medium">
                  {formatCompactNumber(usage.totalTokens)} tok
                </span>
                <span className="mx-1 text-muted-foreground/40">|</span>
                <span className="font-medium">
                  {formatCompactNumber(usage.cachedInputTokens)} cached
                </span>
                <span className="mx-1 text-muted-foreground/40">|</span>
                <span className="font-medium">
                  {formatCompactNumber(usage.requestCount)} req
                </span>
                <span className="mx-1 text-muted-foreground/40">|</span>
                <span className="font-medium">
                  {formatCurrency(usage.totalCostUsd)}
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground">
                {usageMessage ?? "No usage recorded"}
              </span>
            )}
          </dd>
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Limits</dt>
            <dd className="text-right font-mono tabular-nums">
              {apiKey.limits.length > 0 ? (
                <span className="font-medium">
                  {apiKey.limits.length} configured
                </span>
              ) : (
                <span className="text-muted-foreground">
                  No limits configured
                </span>
              )}
            </dd>
          </div>
          {apiKey.limits.map((limit) => {
            const isCost = limit.limitType === "cost_usd";
            const percent =
              limit.maxValue > 0
                ? Math.min(100, (limit.currentValue / limit.maxValue) * 100)
                : 0;
            const current = isCost
              ? `$${(limit.currentValue / 1_000_000).toFixed(2)}`
              : formatCompactNumber(limit.currentValue);
            const max = isCost
              ? `$${(limit.maxValue / 1_000_000).toFixed(2)}`
              : formatCompactNumber(limit.maxValue);
            const modelFilter = limit.modelFilter || "all";

            return (
              <div key={limit.id} className="space-y-1 pl-2">
                <div className="flex items-center justify-between gap-2 text-xs tabular-nums">
                  <span className="text-muted-foreground">
                    {LIMIT_TYPE_LABEL[limit.limitType]} ({limit.limitWindow},{" "}
                    {modelFilter})
                  </span>
                  <span
                    className={cn(
                      "font-mono tabular-nums",
                      percent >= 90 ? "font-semibold" : "font-medium",
                    )}
                  >
                    {current} / {max}
                  </span>
                </div>
                <div className="h-1 w-full rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-foreground transition-[width] duration-200 ease-out motion-reduce:transition-none"
                    style={{ width: `${percent}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </dl>
    </div>
  );
}
