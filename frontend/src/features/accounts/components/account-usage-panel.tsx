import { MonoMeter } from "@/components/ui/mono-meter";
import type { AccountSummary } from "@/features/accounts/schemas";
import {
  formatCompactNumber,
  formatCurrency,
  formatPercentNullable,
  formatQuotaResetLabel,
  formatResetRelative,
  formatWindowLabel,
} from "@/utils/formatters";

export type AccountUsagePanelProps = {
  account: AccountSummary;
};

function QuotaRow({
  label,
  percent,
  resetAt,
}: {
  label: string;
  percent: number | null;
  resetAt: string | null | undefined;
}) {
  if (percent === null) {
    return (
      <div className="min-w-0">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="truncate text-[13px] leading-none font-medium">
            {label} remaining
          </span>
          <span className="shrink-0 font-mono text-xs leading-none text-muted-foreground tabular-nums">
            {formatPercentNullable(percent)}
          </span>
        </div>
        <div
          role="progressbar"
          aria-label={`${label} remaining`}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1 w-full overflow-hidden rounded-full bg-muted"
        />
        <p className="mt-1 truncate text-xs leading-none text-muted-foreground">
          Reset {formatQuotaResetLabel(resetAt ?? null)}
        </p>
      </div>
    );
  }
  return (
    <MonoMeter
      label={`${label} remaining`}
      percent={percent}
      sublabel={`Reset ${formatQuotaResetLabel(resetAt ?? null)}`}
    />
  );
}

const ADDITIONAL_LIMIT_LABELS: Record<string, string> = {
  codex_spark: "GPT-5.3-Codex-Spark",
  codex_other: "GPT-5.3-Codex-Spark",
  "gpt-5.3-codex-spark": "GPT-5.3-Codex-Spark",
};

function formatAdditionalLimitName(
  limitName: string,
  quotaKey?: string | null,
): string {
  const normalizedQuotaKey = quotaKey?.trim().toLowerCase();
  if (normalizedQuotaKey && ADDITIONAL_LIMIT_LABELS[normalizedQuotaKey]) {
    return ADDITIONAL_LIMIT_LABELS[normalizedQuotaKey];
  }
  const normalized = limitName.trim().toLowerCase();
  return (
    ADDITIONAL_LIMIT_LABELS[normalized] ??
    limitName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

function formatResetCountdown(resetAt: number | null): string | null {
  if (resetAt === null) return null;
  const diffMs = resetAt * 1000 - Date.now();
  if (diffMs <= 0) return "Resetting...";
  return `Resets ${formatResetRelative(diffMs)}`;
}

const ADDITIONAL_ROUTING_POLICY_LABELS: Record<string, string> = {
  burn_first: "Burn first",
  normal: "Normal",
  preserve: "Preserve",
};

export function AccountUsagePanel({ account }: AccountUsagePanelProps) {
  const isAnthropic = (account.provider ?? "openai") === "anthropic";
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const monthly = account.usage?.monthlyRemainingPercent ?? null;
  const requestUsage = account.requestUsage ?? null;
  const hasRequestUsage = (requestUsage?.requestCount ?? 0) > 0;
  const requestUsageCacheLabel = isAnthropic
    ? `${formatCompactNumber(requestUsage?.cacheCreationTokens ?? 0)} cache create | ${formatCompactNumber(requestUsage?.cacheReadTokens ?? 0)} cache read`
    : `${formatCompactNumber(requestUsage?.cachedInputTokens)} cached`;
  const weeklyOnly =
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary != null;
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;
  const hasPrimaryWindow =
    account.windowMinutesPrimary != null ||
    primary !== null ||
    account.resetAtPrimary != null;
  const hasSecondaryWindow =
    account.windowMinutesSecondary != null ||
    secondary !== null ||
    account.resetAtSecondary != null;
  const primaryLabel = isAnthropic ? "Session" : "5h";
  const secondaryLabel = isAnthropic
    ? "Week"
    : monthlyOnly
      ? "Monthly"
      : "Weekly";

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">Usage</h3>

      {(hasPrimaryWindow || hasSecondaryWindow || monthlyOnly) && (
        <div
          className={
            weeklyOnly || monthlyOnly
              ? "grid grid-cols-1 gap-4"
              : "grid grid-cols-2 gap-4"
          }
        >
          {monthlyOnly ? (
            <QuotaRow
              label="Monthly"
              percent={monthly}
              resetAt={account.resetAtMonthly}
            />
          ) : (
            <>
              {!weeklyOnly && hasPrimaryWindow ? (
                <QuotaRow
                  label={primaryLabel}
                  percent={primary}
                  resetAt={account.resetAtPrimary}
                />
              ) : null}
              {hasSecondaryWindow ? (
                <QuotaRow
                  label={secondaryLabel}
                  percent={secondary}
                  resetAt={account.resetAtSecondary}
                />
              ) : null}
            </>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          Request logs total
        </span>
        {hasRequestUsage ? (
          <span className="font-mono text-xs tabular-nums">
            {formatCompactNumber(requestUsage?.totalTokens)} tok |{" "}
            {requestUsageCacheLabel} |{" "}
            {formatCompactNumber(requestUsage?.requestCount)} req |{" "}
            {formatCurrency(requestUsage?.totalCostUsd)}
          </span>
        ) : (
          <span className="font-mono text-xs text-muted-foreground">
            No request usage yet
          </span>
        )}
      </div>

      {account.additionalQuotas.length > 0 ? (
        <div className="space-y-3 border-t pt-3">
          <p className="text-xs font-medium text-muted-foreground">
            Additional quotas
          </p>
          {account.additionalQuotas.map((quota) => (
            <div key={quota.quotaKey ?? quota.limitName} className="space-y-2">
              <p className="text-xs font-medium">
                {quota.displayLabel ??
                  formatAdditionalLimitName(quota.limitName, quota.quotaKey)}
                {quota.routingPolicy != null &&
                quota.routingPolicy !== "inherit" ? (
                  <span className="ml-2 text-muted-foreground">
                    {ADDITIONAL_ROUTING_POLICY_LABELS[quota.routingPolicy] ??
                      quota.routingPolicy}
                  </span>
                ) : null}
              </p>
              {quota.primaryWindow != null ? (
                <MonoMeter
                  label={`${formatWindowLabel("primary", quota.primaryWindow.windowMinutes ?? null)} used`}
                  percent={quota.primaryWindow.usedPercent}
                  warnBelow={0}
                  sublabel={
                    formatResetCountdown(quota.primaryWindow.resetAt ?? null) ??
                    undefined
                  }
                />
              ) : null}
              {quota.secondaryWindow != null ? (
                <MonoMeter
                  label={`${formatWindowLabel("secondary", quota.secondaryWindow.windowMinutes ?? null)} used`}
                  percent={quota.secondaryWindow.usedPercent}
                  warnBelow={0}
                  sublabel={
                    formatResetCountdown(
                      quota.secondaryWindow.resetAt ?? null,
                    ) ?? undefined
                  }
                />
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
