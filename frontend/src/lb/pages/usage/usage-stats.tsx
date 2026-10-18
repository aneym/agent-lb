import type { ReceiptSummary } from "../../api/receipts";
import type { SeatActivity } from "../../api";
import { compact } from "../../format";
import { vendorName } from "../../kit/provider-mark-helpers";
import { number, percent, latency, usageMoney } from "./usage-format";

export function SummaryStats({ totals, hours }: { totals: ReceiptSummary["totals"]; hours: number }) {
  const stats = [
    ["Requests", number(totals.requests), `${compact(totals.requests / (hours / 24))} a day`],
    ["Tokens", compact(totals.tokens), `${percent(totals.cacheReadRatio)} read from cache`],
    ["API equivalent", usageMoney(totals.costUsd), "at API list prices"],
    ["Errors", percent(totals.errorRate), `most: ${totals.topErrorCode || "none"}`],
    [
      "Median latency",
      latency(totals.p50LatencyMs),
      `1 in 20 over ${latency(totals.p95LatencyMs)}`,
    ],
  ];
  return (
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
  );
}

export function SeatActivityStats({ activity, provider }: { activity: SeatActivity; provider: string }) {
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
      "Seat value",
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
      activity.okKnown ? `${number(activity.runs - activity.ok)} failed` : "not recorded before Oct 16",
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

