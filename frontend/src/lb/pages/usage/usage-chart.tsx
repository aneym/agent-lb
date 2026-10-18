import { useState } from "react";
import type { ReceiptSummary } from "../../api/receipts";
import { compact } from "../../format";
import { ProviderMark } from "../../kit/provider-mark";
import { Panel } from "../../kit/primitives";
import { number, providerName } from "./usage-format";

export function UsageChart({ series, bucket }: { series: ReceiptSummary["series"]; bucket: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...series.map((row) => row.requests));
  const ceiling = Math.ceil(max / (max > 1000 ? 1000 : 10)) * (max > 1000 ? 1000 : 10);
  const colors = ["claude", "codex", "other"] as const;
  const total = (key: (typeof colors)[number]) =>
    series.reduce(
      (sum, row) =>
        sum +
        (key === "other"
          ? Object.entries(row.byProvider)
              .filter(([p]) => p !== "anthropic" && p !== "openai")
              .reduce((v, [, n]) => v + n, 0)
          : row.byProvider[key === "claude" ? "anthropic" : "openai"] || 0),
      0,
    );
  const labels = series.map((row, i) => {
    const date = new Date(row.start);
    const previous = i > 0 ? new Date(series[i - 1].start) : null;
    return !previous || date.toDateString() !== previous.toDateString()
      ? new Intl.DateTimeFormat("en-US", {
          weekday: "short",
          ...(bucket === "day" ? { day: "numeric" } : {}),
        }).format(date)
      : "";
  });
  const active = hover == null ? null : series[hover];
  return (
    <Panel className="usage-chart-panel">
      <div className="usage-chart-header">
        <h2>Requests every {bucket === "6h" ? "6 hours" : bucket === "hour" ? "hour" : "day"}</h2>
        <div className="legend">
          {colors.map((p, i) => (
            <div className="it" key={p}>
              <span className={`sw ${i === 1 ? "s2" : i === 2 ? "s3" : ""}`} />
              {p === "other" ? (
                <>
                  <ProviderMark id="cursor" size={16} />
                  <ProviderMark id="glm" size={16} />
                </>
              ) : (
                <ProviderMark id={p === "claude" ? "claude" : "openai"} size={16} />
              )}
              {p === "other" ? "Other" : p === "claude" ? "Claude" : "Codex"}
              <span className="mono">{number(total(p))}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="chart">
        <div className="grid">
          {[4, 3, 2, 1, 0].map((i) => (
            <div key={i}>
              <span>{compact((ceiling * i) / 4)}</span>
            </div>
          ))}
        </div>
        <div className="plot" onMouseLeave={() => setHover(null)}>
          {series.map((row, i) => {
            const values = [
              row.byProvider.anthropic || 0,
              row.byProvider.openai || 0,
              Object.entries(row.byProvider)
                .filter(([p]) => p !== "anthropic" && p !== "openai")
                .reduce((v, [, n]) => v + n, 0),
            ];
            return (
              <div
                className={`col ${hover === i ? "hot" : ""}`}
                key={row.start}
                onMouseEnter={() => setHover(i)}
                aria-label={`${row.start}: ${row.requests} requests`}
              >
                {values.map(
                  (v, index) =>
                    v > 0 && (
                      <i
                        key={index}
                        className={index === 1 ? "s2" : index === 2 ? "s3" : ""}
                        style={{ height: `${Math.max(1, (v / ceiling) * 100)}%` }}
                      />
                    ),
                )}
              </div>
            );
          })}
        </div>
        <div className="x">
          {labels.map((label, i) => (
            <span key={i} className={label ? "" : "blank"}>
              {label}
            </span>
          ))}
        </div>
        {active && (
          <div className="tip usage-tip">
            <div className="th">
              {new Date(active.start).toLocaleString("en-US", {
                weekday: "short",
                day: "numeric",
                month: "short",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </div>
            {["anthropic", "openai", "other"].map((p, i) => (
              <div className="tr" key={p}>
                <span>
                  <span className={`sw ${i === 1 ? "s2" : i === 2 ? "s3" : ""}`} />
                  {providerName(p)}
                </span>
                <span className="mono">
                  {number(
                    p === "other"
                      ? Object.entries(active.byProvider)
                          .filter(([key]) => key !== "anthropic" && key !== "openai")
                          .reduce((v, [, n]) => v + n, 0)
                      : active.byProvider[p] || 0,
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}

