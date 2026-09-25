import { useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Copy, Download } from "lucide-react";
import { z } from "zod";
import { get } from "@/lib/api-client";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import type { AccountSummary } from "@/features/accounts/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";
import {
  fetchUsageReceipts,
  useUsageReceipts,
  useUsageSummary,
  type Receipt,
  type ReceiptSummary,
} from "../../api/receipts";
import { compact, money } from "../../format";
import { ProviderMark } from "../../kit/provider-mark";
import { providerMark } from "../../kit/provider-mark-helpers";
import {
  FilterSelect,
  ModelChip,
  PageHead,
  Panel,
  Section,
  Seg,
  SidePanel,
  Toast,
} from "../../kit/primitives";
import "./usage.css";

const groups = ["account", "pool", "key", "session", "model", "day"] as const;
type Group = (typeof groups)[number];
const ranges: Record<string, { label: string; hours: number; bucket: string }> = {
  "24h": { label: "Last 24 hours", hours: 24, bucket: "hour" },
  "7d": { label: "Last 7 days", hours: 168, bucket: "6h" },
  "30d": { label: "Last 30 days", hours: 720, bucket: "day" },
};
const keySchema = z.array(z.object({ id: z.string(), name: z.string() }));
const number = (n: number) => new Intl.NumberFormat("en-US").format(n);
const percent = (n: number | null) => (n == null ? "—" : `${Math.round(n * 100)}%`);
const latency = (n: number | null) =>
  n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`;
const displayAccount = (a?: AccountSummary) =>
  a ? a.alias || `${a.planType} · ${a.email.split("@")[0]}` : "Unknown account";
const markFor = (provider?: string | null) =>
  provider === "anthropic" ? "claude" : providerMark(provider || "anthropic");
const providerName = (provider?: string | null) =>
  provider === "anthropic"
    ? "Claude"
    : provider === "openai"
      ? "Codex"
      : provider
        ? provider.charAt(0).toUpperCase() + provider.slice(1)
        : "Other";
const safeCsv = (value: string | number | null) => {
  const text = String(value ?? "");
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
};

function SummaryStats({ totals, hours }: { totals: ReceiptSummary["totals"]; hours: number }) {
  const stats = [
    ["Requests", number(totals.requests), `${compact(totals.requests / (hours / 24))} a day`],
    ["Tokens", compact(totals.tokens), `${percent(totals.cacheReadRatio)} read from cache`],
    ["API equivalent", money(totals.costUsd), "at API list prices"],
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

function UsageChart({ series, bucket }: { series: ReceiptSummary["series"]; bucket: string }) {
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

function Breakdown({
  summary,
  group,
  onGroup,
  accounts,
  keys,
  blurred,
}: {
  summary?: ReceiptSummary;
  group: Group;
  onGroup: (group: Group) => void;
  accounts: Map<string, AccountSummary>;
  keys: Map<string, string>;
  blurred: boolean;
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
        {summary?.groups.map((row) => {
          const account = accounts.get(row.key || "");
          const name =
            group === "account"
              ? displayAccount(account)
              : group === "key"
                ? keys.get(row.key || "") || row.key
                : row.key || "Unknown";
          const body = (
            <>
              {group === "account" || group === "pool" ? (
                <ProviderMark id={markFor(row.provider || account?.provider)} />
              ) : null}
              <div className="t">
                {group === "model" ? (
                  <ModelChip model={row.key || "Unknown"} />
                ) : (
                  <span
                    className={`n ${group === "session" ? "mono" : ""} ${group === "account" && blurred && account && !account.alias ? "privacy-blur" : ""}`}
                  >
                    {name}
                  </span>
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
                    to={`/usage?session=${encodeURIComponent(row.key || "")}`}
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
              <span className="c-cost mono right">{money(row.costUsd)}</span>
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

function ReceiptRow({
  receipt,
  account,
  onSelect,
  blurred,
}: {
  receipt: Receipt;
  account?: AccountSummary;
  onSelect: () => void;
  blurred: boolean;
}) {
  const session = receipt.clientSessionId || receipt.sessionId;
  const status = receipt.status === "ok" ? "✓" : receipt.httpStatus === 429 ? "◷" : "×";
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };
  return (
    <div
      className="row usage-receipt"
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={onKey}
    >
      <span className="c-t mono">
        {new Date(receipt.requestedAt).toLocaleTimeString("en-GB", {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </span>
      <span className="c-ses mono">
        {session || "—"}
        <span className="usage-key"> {receipt.apiKeyName || receipt.apiKeyPrefix || "—"}</span>
      </span>
      <span className="c-acct cellm">
        <ProviderMark id={markFor(receipt.provider || account?.provider)} size={16} />
        <span className={blurred && account && !account.alias ? "privacy-blur" : ""}>
          {displayAccount(account)}
        </span>
      </span>
      <span className="c-model">
        <ModelChip model={receipt.model || "Unknown"} />
      </span>
      <span className="c-tok mono right">
        {compact(receipt.inputTokens + receipt.cacheReadTokens + receipt.cacheWriteTokens)} /{" "}
        {compact(receipt.outputTokens)}
      </span>
      <span className="c-cache mono right">{percent(receipt.cacheReadRatio)}</span>
      <span className="c-lat mono right">{latency(receipt.latencyMs)}</span>
      <span className="c-st mono right">
        {status} {receipt.httpStatus || receipt.errorCode || receipt.status}
      </span>
      <span className="c-cost mono right">
        {receipt.costUsd == null ? "—" : money(receipt.costUsd)}
      </span>
    </div>
  );
}
function RouteTrace({
  receipt,
  account,
  onClose,
  blurred,
}: {
  receipt: Receipt | null;
  account?: AccountSummary;
  onClose: () => void;
  blurred: boolean;
}) {
  if (!receipt) return null;
  const fields = [
    ["Time", new Date(receipt.requestedAt).toLocaleString()],
    ["Account", displayAccount(account)],
    ["Pool", receipt.pool],
    ["Model", receipt.model],
    ["Input tokens", number(receipt.inputTokens)],
    ["Output tokens", number(receipt.outputTokens)],
    ["Cache read", number(receipt.cacheReadTokens)],
    ["Cache write", number(receipt.cacheWriteTokens)],
    ["Cache read ratio", percent(receipt.cacheReadRatio)],
    ["Latency", latency(receipt.latencyMs)],
    ["First token", latency(receipt.latencyFirstTokenMs)],
    ["Status", `${receipt.status}${receipt.httpStatus ? ` · ${receipt.httpStatus}` : ""}`],
    ["Error code", receipt.errorCode],
    ["Request ID", receipt.id],
  ];
  const session = receipt.clientSessionId || receipt.sessionId;
  return (
    <SidePanel
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Route trace"
    >
      <dl className="dl usage-trace">
        {fields
          .filter(([, value]) => value != null)
          .map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd
                className={`mono ${key === "Account" && blurred && account && !account.alias ? "privacy-blur" : ""}`}
              >
                {value}
              </dd>
            </div>
          ))}
      </dl>
      {session && (
        <Link to={`/routing/decisions?session=${encodeURIComponent(session)}`}>
          Decisions for this session →
        </Link>
      )}
    </SidePanel>
  );
}

export function UsagePage() {
  const [params, setParams] = useSearchParams();
  const [group, setGroup] = useState<Group>("account");
  const [limit, setLimit] = useState(50);
  const [selected, setSelected] = useState<Receipt | null>(null);
  const [sessionDraft, setSessionDraft] = useState(params.get("session") || "");
  const [showMore, setShowMore] = useState(() =>
    ["account", "key", "session", "model"].some((key) => params.has(key)),
  );
  const [end] = useState(() => Date.now());
  const range = ranges[params.get("range") || "7d"] || ranges["7d"];
  const filters = new URLSearchParams({
    since: new Date(end - range.hours * 3_600_000).toISOString(),
    until: new Date(end).toISOString(),
  });
  for (const [ui, api] of [
    ["account", "account_id"],
    ["key", "api_key_id"],
    ["session", "session_id"],
    ["provider", "provider"],
    ["model", "model"],
  ]) {
    if (params.get(ui)) filters.set(api, params.get(ui)!);
  }
  const filterString = filters.toString();
  const summary = useUsageSummary(filterString, group, range.bucket);
  const modelSummary = useUsageSummary(
    new URLSearchParams({ since: filters.get("since")!, until: filters.get("until")! }).toString(),
    "model",
    range.bucket,
  );
  const receipts = useUsageReceipts(filterString, limit);
  const { accountsQuery } = useAccounts();
  const keyQuery = useQuery({
    queryKey: ["usage", "keys"],
    queryFn: () => get("/api/api-keys/", keySchema),
  });
  const blurred = usePrivacyStore((state) => state.blurred);
  const accounts = new Map(accountsQuery.data?.map((a) => [a.accountId, a]));
  const keys = new Map(keyQuery.data?.map((k) => [k.id, k.name]));
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
    setLimit(50);
  };
  const choices = (items: { value: string; label: string }[], all: string) => [
    { value: "", label: all },
    ...items,
  ];
  const copyQuery = async () => {
    try {
      const url = `${window.location.origin}/api/receipts?${new URLSearchParams([...filters, ["limit", "50"]])}`;
      await navigator.clipboard.writeText(`curl ${JSON.stringify(url)}`);
      Toast.success("API query copied");
    } catch {
      Toast.error("Could not copy API query");
    }
  };
  const exportCsv = async () => {
    try {
      const rows: Receipt[] = [];
      for (let offset = 0; offset < 5000; offset += 500) {
        const query = new URLSearchParams(filters);
        query.set("limit", "500");
        query.set("offset", String(offset));
        const page = await fetchUsageReceipts(query);
        rows.push(...page.receipts);
        if (rows.length >= page.total || page.receipts.length < 500) break;
      }
      const columns: (keyof Receipt)[] = [
        "id",
        "requestedAt",
        "sessionId",
        "clientSessionId",
        "apiKeyId",
        "apiKeyName",
        "accountId",
        "provider",
        "pool",
        "model",
        "inputTokens",
        "outputTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "latencyMs",
        "status",
        "httpStatus",
        "errorCode",
        "costUsd",
      ];
      const csv = [
        columns.join(","),
        ...rows.map((row) => columns.map((column) => safeCsv(row[column])).join(",")),
      ].join("\r\n");
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agent-lb-receipts.csv";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (rows.length === 5000) Toast.message("Export limited to the newest 5,000 receipts");
    } catch {
      Toast.error("Could not export receipts");
    }
  };
  return (
    <div className="usage-page">
      <PageHead
        title="Usage"
        action={
          <div className="toolbar">
            <button className="btn" onClick={() => void copyQuery()}>
              <Copy size={14} />
              Copy API query
            </button>
            <button className="btn" onClick={() => void exportCsv()}>
              <Download size={14} />
              Export CSV
            </button>
          </div>
        }
      >
        Every request agent-lb served: which key asked, which account paid, and what it would have
        cost at API prices.
      </PageHead>
      <div className="toolbar filters usage-filters">
        <FilterSelect
          label="Time"
          value={params.get("range") || "7d"}
          options={Object.entries(ranges).map(([value, r]) => ({ value, label: r.label }))}
          onChange={(v) => update("range", v)}
        />
        <FilterSelect
          label="Provider"
          value={params.get("provider") || ""}
          options={choices(
            [
              "anthropic",
              "openai",
              "cursor",
              "devin",
              "glm",
              "kimi",
              "openrouter",
              "gemini",
              "ollama",
              "grok",
            ].map((p) => ({ value: p, label: providerName(p) })),
            "All",
          )}
          onChange={(v) => update("provider", v)}
        />
        <FilterSelect
          label="Account"
          value={params.get("account") || ""}
          options={choices(
            (accountsQuery.data || []).map((a) => ({
              value: a.accountId,
              label: blurred && !a.alias ? `${a.planType} · Account` : displayAccount(a),
            })),
            "All",
          )}
          onChange={(v) => update("account", v)}
        />
        <FilterSelect
          label="Key"
          value={params.get("key") || ""}
          options={choices(
            (keyQuery.data || []).map((k) => ({ value: k.id, label: k.name })),
            "All",
          )}
          onChange={(v) => update("key", v)}
        />
        <FilterSelect
          label="Session"
          value={params.get("session") || ""}
          options={choices(
            params.get("session")
              ? [{ value: params.get("session")!, label: params.get("session")! }]
              : [],
            "Any",
          ).concat({ value: "__custom__", label: "Enter session ID…" })}
          onChange={(v) => {
            if (v === "__custom__") {
              setShowMore(true);
              return;
            }
            update("session", v);
            setSessionDraft(v);
            setShowMore(v !== "");
          }}
        />
        <FilterSelect
          label="Model"
          value={params.get("model") || ""}
          options={choices(
            (modelSummary.data?.groups || []).map((m) => ({
              value: m.key || "",
              label: m.key || "Unknown",
            })),
            "All",
          )}
          onChange={(v) => update("model", v)}
        />
        <button className="btn usage-more" onClick={() => setShowMore(!showMore)}>
          ☷ More filters
        </button>
      </div>
      {showMore && (
        <form
          className="usage-session"
          onSubmit={(e) => {
            e.preventDefault();
            update("session", sessionDraft.trim());
          }}
        >
          <label htmlFor="usage-session">Session ID</label>
          <input
            id="usage-session"
            value={sessionDraft}
            onChange={(e) => setSessionDraft(e.target.value)}
            placeholder="Any session"
          />
          <button className="btn" type="submit">
            Apply
          </button>
        </form>
      )}
      {summary.isError || receipts.isError ? (
        <p role="alert">Could not load usage. Try refreshing the page.</p>
      ) : null}
      {summary.data && (
        <>
          <SummaryStats totals={summary.data.totals} hours={range.hours} />
          <UsageChart series={summary.data.series} bucket={range.bucket} />
        </>
      )}
      <Breakdown
        summary={summary.data}
        group={group}
        onGroup={setGroup}
        accounts={accounts}
        keys={keys}
        blurred={blurred}
      />
      <Section title="Receipts" aside="Newest first · select a row for its route trace">
        <div className="rows rc">
          <div className="row hd">
            <span>Time</span>
            <span>Session · key</span>
            <span>Account</span>
            <span>Model</span>
            <span>In / out</span>
            <span>Cache</span>
            <span>Latency</span>
            <span>Status</span>
            <span>Cost</span>
          </div>
          {receipts.data?.receipts.map((r) => (
            <ReceiptRow
              key={r.id}
              receipt={r}
              account={accounts.get(r.accountId || "")}
              blurred={blurred}
              onSelect={() => setSelected(r)}
            />
          ))}
          {receipts.data?.total === 0 && (
            <div className="usage-empty">No receipts match these filters.</div>
          )}
        </div>
        {receipts.data && receipts.data.total > receipts.data.receipts.length && (
          <div className="usage-load">
            <button className="btn" onClick={() => setLimit((n) => n + 50)}>
              Load 50 more
            </button>
          </div>
        )}
      </Section>
      <RouteTrace
        receipt={selected}
        account={accounts.get(selected?.accountId || "")}
        onClose={() => setSelected(null)}
        blurred={blurred}
      />
    </div>
  );
}
