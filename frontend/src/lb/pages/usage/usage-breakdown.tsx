import { Link } from "react-router-dom";
import type { AccountSummary } from "@/features/accounts/schemas";
import type { ReceiptSummary } from "../../api/receipts";
import { compact } from "../../format";
import { ProviderMark } from "../../kit/provider-mark";
import { Section, Seg, ModelChip } from "../../kit/primitives";
import { number, percent, usageMoney, displayAccount, markFor, providerName } from "./usage-format";

export const groups = ["account", "pool", "key", "session", "model", "day"] as const;
export type Group = (typeof groups)[number];

export function Breakdown({
  summary,
  group,
  onGroup,
  accounts,
  keys,
  blurred,
  accountsPending = false,
}: {
  summary?: ReceiptSummary;
  group: Group;
  onGroup: (group: Group) => void;
  accounts: Map<string, AccountSummary>;
  keys: Map<string, string>;
  blurred: boolean;
  accountsPending?: boolean;
}) {
  const max = Math.max(0, ...(summary?.groups.map((row) => row.costUsd) || []));
  return (
    <Section
      title="Breakdown"
      aside={
        <Seg
          options={groups.map((g) => ({ value: g, label: g[0].toUpperCase() + g.slice(1) }))}
          value={group}
          onChange={(g) => onGroup(g as Group)}
        />
      }
    >
      <div className="rows brk">
        <div className="row hd">
          <span>{group[0].toUpperCase() + group.slice(1)}</span>
          <span>Requests</span>
          <span>Tokens</span>
          <span>Cache</span>
          <span>API value</span>
          <span>Errors</span>
          <span>Share of cost</span>
        </div>
        {!summary && (
          <div className="usage-empty" role="status">
            Loading breakdown…
          </div>
        )}
        {summary?.groups.map((row) => {
          const account = accounts.get(row.key || "");
          const name =
            group === "account"
              ? displayAccount(account, blurred, accountsPending, row.provider)
              : group === "key"
                ? keys.get(row.key || "") || row.key
                : row.key || "Unknown";
          const body = (
            <>
              {group === "account" || group === "pool" ? (
                <ProviderMark
                  id={markFor(
                    accountsPending && !account && row.provider !== "muse"
                      ? null
                      : row.provider || account?.provider,
                  )}
                />
              ) : null}
              <div className="t">
                {group === "model" ? (
                  <ModelChip model={row.key || "Unknown"} />
                ) : (
                  <span className={`n ${group === "session" ? "mono" : ""}`}>{name}</span>
                )}
                {group === "account" && (
                  <span className="s">{providerName(row.provider || account?.provider)}</span>
                )}
              </div>
            </>
          );
          return (
            <div className="row" key={row.key || "Unknown"}>
              <div className="c-who who">
                {group === "account" && account ? (
                  <Link
                    className="usage-name"
                    to={`/providers/${encodeURIComponent(row.key || "")}`}
                  >
                    {body}
                  </Link>
                ) : group === "key" ? (
                  <Link className="usage-name" to="/keys">
                    {body}
                  </Link>
                ) : group === "session" ? (
                  <Link
                    className="usage-name"
                    to={`/sessions/${encodeURIComponent(row.key || "")}`}
                  >
                    {body}
                  </Link>
                ) : (
                  body
                )}
              </div>
              <span className="c-req mono right">{number(row.requests)}</span>
              <span className="c-tok mono right">{compact(row.tokens)}</span>
              <span className="c-cache mono right">{percent(row.cacheReadRatio)}</span>
              <span className="c-cost mono right">{usageMoney(row.costUsd)}</span>
              <span className="c-err mono right">{percent(row.errorRate)}</span>
              <div className="c-bar share">
                <i style={{ width: `${max ? Math.max(1, (row.costUsd / max) * 100) : 0}%` }} />
              </div>
              <span className="c-meta">
                {number(row.requests)} requests · {compact(row.tokens)} tokens ·{" "}
                {percent(row.cacheReadRatio)} cache · {percent(row.errorRate)} errors
              </span>
            </div>
          );
        })}
        {summary?.groups.length === 0 && (
          <div className="usage-empty">No requests match these filters.</div>
        )}
      </div>
    </Section>
  );
}
