import type { ReceiptSummary } from "../../api/receipts";
import type { Pool, SeatActivity } from "../../api";
import { compact } from "../../format";
import { vendorName } from "../../kit/provider-mark-helpers";
import { number, percent, latency, usageMoney } from "./usage-format";

export function SummaryStats({
  totals,
  hours,
}: {
  totals: ReceiptSummary["totals"];
  hours: number;
}) {
  const stats = [
    ["Requests", number(totals.requests), `${compact(totals.requests / (hours / 24))} a day`],
    ["Tokens", compact(totals.tokens), `${percent(totals.cacheReadRatio)} read from cache`],
    ["Errors", percent(totals.errorRate), `most: ${totals.topErrorCode || "none"}`],
    [
      "Median latency",
      latency(totals.p50LatencyMs),
      `1 in 20 over ${latency(totals.p95LatencyMs)}`,
    ],
  ];
  return (
    <>
      <p>API list-price equivalent (not billed): {usageMoney(totals.costUsd)}</p>
      <div className="stats">
        {stats.map(([label, value, sub]) => (
          <div className="stat" key={label}>
            <div className="k">{label}</div>
            <div className="v">{value}</div>
            <div className="s" title={sub}>
              {sub}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export function SeatActivityStats({
  activity,
  provider,
}: {
  activity: SeatActivity;
  provider: string;
}) {
  const tokens =
    activity.tokensKnown && activity.tokensIn != null && activity.tokensOut != null
      ? activity.tokensIn + activity.tokensOut
      : null;
  const who = provider ? vendorName(provider) : "These seats";
  const top = [...activity.models].sort((a, b) => b.runs - a.runs)[0];
  const stats = [
    [
      "Seat runs",
      number(activity.runs),
      `${activity.okKnown ? `${number(activity.ok)} ok` : "ok not recorded"} · ${(activity.runs / (activity.periodHours / 24)).toFixed(1)} a day`,
    ],
    [
      "Seat tokens",
      tokens == null ? "Not reported" : compact(tokens),
      tokens == null ? `${who} did not report tokens` : "in and out",
    ],
    [
      "API-equiv (not billed)",
      activity.valueUsd == null ? "Not reported" : usageMoney(activity.valueUsd),
      activity.valueUsd == null
        ? "no list price for these runs"
        : activity.valueSource === "estimate"
          ? "estimate at list prices"
          : "at list prices",
    ],
    [
      "Success",
      activity.okKnown && activity.runs ? percent(activity.ok / activity.runs) : "—",
      activity.okKnown
        ? `${number(activity.runs - activity.ok)} failed`
        : "not recorded before Oct 16",
    ],
    [
      "Top model",
      top ? top.model : "—",
      top ? `${number(top.runs)} runs · ${activity.models.length} models` : "no runs",
    ],
  ];
  return (
    <section className="usage-seat-activity" aria-label="CLI seat runs">
      <div className="sec-h">
        <h2>CLI seat runs</h2>
        <span className="aside">
          Last {Math.round(activity.periodHours / 24)} days · counted apart from requests
        </span>
      </div>
      <div className="stats">
        {stats.map(([label, value, sub]) => (
          <div className="stat" key={label}>
            <div className="k">{label}</div>
            <div className="v" title={value}>
              {value}
            </div>
            <div className="s" title={sub}>
              {sub}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function SubscriptionQuota({ pools, failed }: { pools?: Pool[]; failed: boolean }) {
  const quota = (remaining: number | null | undefined, used?: number | null) => {
    if (remaining == null && used == null) return "unknown";
    const left = remaining ?? 100 - used!;
    return `${left.toFixed(1)}% remaining · ${(used ?? 100 - left).toFixed(1)}% used`;
  };
  const reset = (value: string | null | undefined) =>
    value ? new Date(value).toLocaleString() : "unknown";
  return (
    <section aria-label="Subscription quota" data-testid="subscription-quota">
      <div className="sec-h">
        <h2>Subscription quota</h2>
      </div>
      <p>Current pool quota · independent of the receipt date range. Resets in local time.</p>
      {!pools?.length && <p>{failed ? "Quota unknown: pools unavailable" : "Loading quota…"}</p>}
      <div
        className="stats"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}
      >
        {pools?.map((pool) => (
          <div className="stat" key={pool.id} data-pool={pool.id}>
            <h3>{pool.id}</h3>
            <p>
              5h: {quota(pool.fiveHourRemainingPercent)} · reset {reset(pool.fiveHourResetAt)}
            </p>
            <p>
              Weekly: {quota(pool.weeklyRemainingPercent)} · reset {reset(pool.weeklyResetAt)}
            </p>
            {pool.windows?.map((window) => (
              <p key={window.id}>
                {window.label} ({window.kind}): {quota(window.remainingPercent, window.usedPercent)}{" "}
                · reset {reset(window.resetsAt)}
              </p>
            ))}
            {pool.percentUsed != null && !pool.windows?.length && (
              <p>
                Cycle: {quota(null, pool.percentUsed)} · reset{" "}
                {reset(pool.cycleResetAt ?? pool.resetAt)}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
