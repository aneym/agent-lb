import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { ApiKey } from "@/features/api-keys/schemas";
import { formatPercentNullable } from "@/utils/formatters";

/** Monochrome meter: ink fill on muted track, never hue (DESIGN.md). */
function InkBar({
  percent,
  testId,
  "aria-label": ariaLabel,
}: {
  percent: number | null;
  testId?: string;
  "aria-label"?: string;
}) {
  if (percent === null) {
    return (
      <div
        aria-hidden="true"
        data-testid={testId}
        className="h-1 flex-1 overflow-hidden rounded-full bg-muted"
      />
    );
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      role="progressbar"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      data-testid={testId}
      className="h-1 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        data-testid={testId ? `${testId}-fill` : undefined}
        className="h-full rounded-full bg-foreground"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export type ApiListItemProps = {
  apiKey: ApiKey;
  selected: boolean;
  onSelect: (keyId: string) => void;
};

function formatLimitPercent(apiKey: ApiKey): number | null {
  if (apiKey.limits.length === 0) return null;
  let maxPercent = 0;
  for (const limit of apiKey.limits) {
    if (limit.maxValue > 0) {
      const pct = (limit.currentValue / limit.maxValue) * 100;
      if (pct > maxPercent) maxPercent = pct;
    }
  }
  return maxPercent;
}

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

export function ApiListItem({ apiKey, selected, onSelect }: ApiListItemProps) {
  const expired = isExpired(apiKey);
  const primary = apiKey.pooledRemainingPercentPrimary ?? null;
  const secondary = apiKey.pooledRemainingPercentSecondary ?? null;
  const hasPrimary =
    apiKey.pooledCapacityCreditsPrimary > 0 && primary !== null;
  const hasSecondary = secondary !== null;
  const visibleRows = Number(hasPrimary) + Number(hasSecondary);
  const limitPct = formatLimitPercent(apiKey);
  const inactive = !apiKey.isActive || expired;

  return (
    <button
      type="button"
      onClick={() => onSelect(apiKey.id)}
      className={cn(
        "w-full rounded-lg px-3 py-2.5 text-left transition-colors duration-150",
        selected ? "bg-primary/8 ring-1 ring-primary/25" : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{apiKey.name}</p>
        </div>
        <Badge
          variant="outline"
          className={cn(inactive && "text-muted-foreground")}
        >
          {!apiKey.isActive ? "Disabled" : expired ? "Expired" : "Active"}
        </Badge>
      </div>
      {visibleRows > 0 ? (
        <div
          className={cn(
            "mt-2 grid gap-2",
            visibleRows > 1 ? "grid-cols-2" : "grid-cols-1",
          )}
        >
          {hasPrimary ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">Pooled 5h</span>
                <span className="font-mono font-medium tabular-nums">
                  {formatPercentNullable(primary)}
                </span>
              </div>
              <InkBar
                aria-label="Pooled 5h credits remaining"
                percent={primary}
                testId="pooled-quota-track-5h"
              />
            </div>
          ) : null}
          {hasSecondary ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">Pooled Weekly</span>
                <span className="font-mono font-medium tabular-nums">
                  {formatPercentNullable(secondary)}
                </span>
              </div>
              <InkBar
                aria-label="Pooled weekly credits remaining"
                percent={secondary}
                testId="pooled-quota-track-weekly"
              />
            </div>
          ) : null}
        </div>
      ) : null}
      {limitPct !== null ? (
        <div className="mt-1.5 space-y-1">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-muted-foreground">API Limit</span>
            <span className="font-mono font-medium tabular-nums">
              {formatPercentNullable(limitPct)}
            </span>
          </div>
          <InkBar aria-label="API limit usage" percent={limitPct} />
        </div>
      ) : null}
    </button>
  );
}
