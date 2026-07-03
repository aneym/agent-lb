import { Fragment } from "react";

import { MonoMeter } from "@/components/ui/mono-meter";
import { StatusGlyph } from "@/components/ui/status-glyph";
import { cn } from "@/lib/utils";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { OwnerInstanceBadge } from "@/features/accounts/components/owner-instance-badge";
import type { AccountSummary } from "@/features/accounts/schemas";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatQuotaResetLabel, formatSlug } from "@/utils/formatters";

const ROUTING_POLICY_NOTES: Record<string, string> = {
  burn_first: "burn first",
  preserve: "preserve",
};

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  onSelect: (accountId: string) => void;
};

export function AccountListItem({
  account,
  selected,
  onSelect,
}: AccountListItemProps) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);

  // Identity first: alias when set (it exists to disambiguate duplicate
  // emails), otherwise the full email.
  const alias = account.alias?.trim() ?? "";
  const title = alias || account.email;
  const titleIsEmail = title === account.email;
  const deactivated = account.status === "deactivated";

  const contextParts: string[] = [];
  if (!titleIsEmail) {
    contextParts.push(account.email);
  }
  contextParts.push(formatSlug(account.planType));
  const workspaceLabel = account.workspaceLabel || account.workspaceId;
  if (workspaceLabel) {
    contextParts.push(
      account.seatType
        ? `${workspaceLabel} / ${formatSlug(account.seatType)}`
        : workspaceLabel,
    );
  } else if (account.seatType) {
    contextParts.push(formatSlug(account.seatType));
  }
  const routingNote = ROUTING_POLICY_NOTES[account.routingPolicy ?? "normal"];
  if (routingNote) {
    contextParts.push(routingNote);
  }
  if (account.isEmailDuplicate && !alias) {
    contextParts.push(`ID ${formatCompactAccountId(account.accountId)}`);
  }

  const isAnthropic = (account.provider ?? "openai") === "anthropic";
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const monthly = account.usage?.monthlyRemainingPercent ?? null;
  const hasPrimaryWindow =
    account.windowMinutesPrimary != null ||
    primary !== null ||
    account.resetAtPrimary != null;
  const hasSecondaryWindow =
    account.windowMinutesSecondary != null ||
    secondary !== null ||
    account.resetAtSecondary != null;
  const hasMonthlyWindow =
    account.windowMinutesMonthly != null ||
    monthly !== null ||
    account.resetAtMonthly != null;
  const monthlyOnly =
    hasMonthlyWindow && !hasPrimaryWindow && !hasSecondaryWindow;
  const showPrimaryRow =
    !monthlyOnly &&
    hasPrimaryWindow &&
    (quotaDisplay !== "weekly" || !hasSecondaryWindow);
  const showSecondaryRow =
    !monthlyOnly &&
    hasSecondaryWindow &&
    (quotaDisplay !== "5h" || !hasPrimaryWindow);
  const visibleMeters =
    Number(showPrimaryRow) + Number(showSecondaryRow) + Number(monthlyOnly);

  return (
    <button
      type="button"
      onClick={() => onSelect(account.accountId)}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "w-full rounded-md px-3 py-2.5 text-left transition-colors duration-150 ease-out outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
        selected ? "bg-accent ring-1 ring-border" : "hover:bg-accent/50",
        deactivated && "opacity-60",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <p
          className={cn(
            "min-w-0 truncate text-sm",
            selected ? "font-semibold" : "font-medium",
          )}
          title={title}
        >
          {titleIsEmail && blurred ? (
            <span className="privacy-blur">{title}</span>
          ) : (
            title
          )}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <OwnerInstanceBadge
            ownerInstance={account.ownerInstance}
            isLocallyOwned={account.isLocallyOwned}
          />
          <StatusGlyph
            status={account.status}
            showLabel={account.status !== "active"}
          />
        </div>
      </div>

      <p
        className="mt-0.5 truncate text-xs text-muted-foreground"
        title={contextParts.join(" · ")}
      >
        {contextParts.map((part, index) => (
          <Fragment key={`${part}-${index}`}>
            {index > 0 ? <span aria-hidden="true"> · </span> : null}
            <span
              className={
                !titleIsEmail && index === 0 && blurred
                  ? "privacy-blur"
                  : undefined
              }
            >
              {part}
            </span>
          </Fragment>
        ))}
      </p>

      {visibleMeters > 0 ? (
        <div
          className={cn(
            "mt-2 grid gap-3",
            visibleMeters > 1 ? "grid-cols-2" : "grid-cols-1",
          )}
        >
          {monthlyOnly ? (
            <RowMeter
              label="Monthly"
              percent={monthly}
              resetAt={account.resetAtMonthly}
            />
          ) : (
            <>
              {showPrimaryRow ? (
                <RowMeter
                  label={isAnthropic ? "Session" : "5h"}
                  percent={primary}
                  resetAt={account.resetAtPrimary}
                />
              ) : null}
              {showSecondaryRow ? (
                <RowMeter
                  label={isAnthropic ? "Week" : "Weekly"}
                  percent={secondary}
                  resetAt={account.resetAtSecondary}
                />
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </button>
  );
}

function formatRowResetLabel(resetAt: string | null): string {
  const label = formatQuotaResetLabel(resetAt);
  return label.startsWith("Reset ") ? label : `Reset ${label}`;
}

function RowMeter({
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
            {label}
          </span>
          <span className="shrink-0 font-mono text-xs leading-none text-muted-foreground tabular-nums">
            --
          </span>
        </div>
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={100}
          data-testid={`mini-quota-track-${label.toLowerCase()}`}
          className="h-1 w-full overflow-hidden rounded-full bg-muted"
        />
        <p className="mt-1 truncate text-xs leading-none text-muted-foreground">
          {formatRowResetLabel(resetAt ?? null)}
        </p>
      </div>
    );
  }
  return (
    <MonoMeter
      label={label}
      percent={percent}
      sublabel={formatRowResetLabel(resetAt ?? null)}
    />
  );
}
