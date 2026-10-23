import { useState } from "react";
import type { AccountSummary } from "@/features/accounts/schemas";
import { LimitMeter } from "../../kit/primitives";

export function Limits({ account }: { account: AccountSummary }) {
  const [now] = useState(() => Date.now());
  const reset = account.resetAtSecondary;
  const expected = reset
    ? Math.max(
        0,
        Math.min(
          100,
          ((new Date(reset).getTime() - now) /
            ((account.windowMinutesSecondary || 10080) * 60000)) *
            100,
        ),
      )
    : undefined;
  const quotas = (account.additionalQuotas || []).filter(
    (q) => q.quotaKey !== "anthropic_fable_scoped_weekly",
  );
  const maxUsed = Math.ceil(
    Math.max(
      0,
      ...quotas.flatMap((q) => [
        q.primaryWindow?.usedPercent || 0,
        q.secondaryWindow?.usedPercent || 0,
      ]),
    ),
  );
  return (
    <section className="card">
      <h2>Limits</h2>
      <div className="lrows">
        {account.windowMinutesSecondary && (
          <div className="lrow">
            <span className="k">
              {account.windowMinutesSecondary === 10080
                ? "Week"
                : account.windowMinutesSecondary === 43200
                  ? "Month"
                  : "Long window"}
            </span>
            <div>
              <LimitMeter
                remaining={account.usage?.secondaryRemainingPercent}
                resetAt={reset}
                paceTick={expected}
              />
              {expected != null && account.usage?.secondaryRemainingPercent != null && (
                <span className="lnote">
                  {Math.abs(Math.round(account.usage.secondaryRemainingPercent - expected))}%{" "}
                  {account.usage.secondaryRemainingPercent < expected
                    ? "short of pace"
                    : "to spare"}
                </span>
              )}
            </div>
          </div>
        )}
        {account.provider !== "openai" && account.windowMinutesPrimary === 300 && (
          <div className="lrow">
            <span className="k">5-hour</span>
            <LimitMeter
              remaining={account.usage?.primaryRemainingPercent}
              resetAt={account.resetAtPrimary}
              unit="5h"
            />
          </div>
        )}
        {account.provider === "openai" && (
          <p className="lnote">Weekly limit only. ChatGPT has no 5-hour window.</p>
        )}
        {quotas.length > 0 && (
          <details className="more">
            <summary>
              {quotas.length} model limits, all under {Math.max(1, maxUsed)}% used
            </summary>
            <div className="lrows account-extra">
              {quotas.map((q) => (
                <div className="lrow" key={q.quotaKey || q.limitName}>
                  <span className="k">{q.displayLabel || q.limitName}</span>
                  <LimitMeter
                    remaining={
                      q.secondaryWindow
                        ? 100 - q.secondaryWindow.usedPercent
                        : q.primaryWindow
                          ? 100 - q.primaryWindow.usedPercent
                          : null
                    }
                    resetAt={
                      q.secondaryWindow?.resetAt
                        ? new Date(q.secondaryWindow.resetAt * 1000).toISOString()
                        : null
                    }
                  />
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}
