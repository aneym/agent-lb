import { AccountTrendChart } from "@/features/accounts/components/account-trend-chart";
import type {
  AccountSummary,
  AccountTrendsResponse,
} from "@/features/accounts/schemas";

export type AccountTrendSectionProps = {
  account: AccountSummary;
  trends: AccountTrendsResponse | null | undefined;
};

export function AccountTrendSection({
  account,
  trends,
}: AccountTrendSectionProps) {
  const isAnthropic = (account.provider ?? "openai") === "anthropic";
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;
  const primaryLabel = isAnthropic ? "Session" : "5h";
  const secondaryLabel = isAnthropic
    ? "Week"
    : monthlyOnly
      ? "Monthly"
      : "Weekly";

  const primaryTrendPoints = trends?.primary ?? [];
  const secondaryTrendPoints = trends?.secondary ?? [];
  const secondaryScheduledTrendPoints = trends?.secondaryScheduled ?? [];
  const hasTrends =
    primaryTrendPoints.length > 0 ||
    secondaryTrendPoints.length > 0 ||
    secondaryScheduledTrendPoints.length > 0;

  if (!hasTrends) {
    return null;
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">7-day trend</h3>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: "var(--chart-1)" }}
            />
            {primaryLabel}
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: "var(--chart-2)" }}
            />
            {secondaryLabel}
          </span>
          {secondaryScheduledTrendPoints.length > 0 ? (
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block h-0 w-4 border-t border-dashed"
                style={{ borderColor: "var(--chart-3)" }}
              />
              {monthlyOnly ? "Monthly plan" : "Weekly plan"}
            </span>
          ) : null}
        </div>
      </div>
      <AccountTrendChart
        primary={primaryTrendPoints}
        secondary={secondaryTrendPoints}
        secondaryScheduled={secondaryScheduledTrendPoints}
      />
    </div>
  );
}
