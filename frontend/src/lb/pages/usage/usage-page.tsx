import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Copy, Download } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { z } from "zod";
import { get } from "@/lib/api-client";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { usePrivacyStore } from "@/hooks/use-privacy";
import {
  fetchUsageReceipts,
  useUsageReceipts,
  useUsageSummary,
  type Receipt,
} from "../../api/receipts";
import { usePools, useSeatActivity } from "../../api";
import { FilterSelect, PageHead, Section, Toast } from "../../kit/primitives";
import { displayAccount, providerName } from "./usage-format";
import { Breakdown, type Group } from "./usage-breakdown";
import { SummaryStats, SeatActivityStats, SubscriptionQuota } from "./usage-stats";
import { UsageChart } from "./usage-chart";
import { ReceiptRow, RouteTrace } from "./usage-receipt";
import "./usage.css";

const ranges: Record<string, { label: string; hours: number; bucket: string }> = {
  "24h": { label: "Last 24 hours", hours: 24, bucket: "hour" },
  "7d": { label: "Last 7 days", hours: 168, bucket: "6h" },
  "30d": { label: "Last 30 days", hours: 720, bucket: "day" },
};
const keySchema = z.array(z.object({ id: z.string(), name: z.string() }));
const safeCsv = (value: string | number | null) => {
  const text = String(value ?? "");
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
};

export function UsagePage() {
  const [params, setParams] = useSearchParams();
  const [group, setGroup] = useState<Group>("account");
  const [limit, setLimit] = useState(50);
  const [selected, setSelected] = useState<Receipt | null>(null);
  const [modelOpen, setModelOpen] = useState(false);
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
  const pools = usePools();
  const seatActivity = useSeatActivity(params.get("provider") || "");
  const modelFilters = new URLSearchParams(filters);
  modelFilters.delete("model");
  const reuseModelSummary = group === "model" && !filters.has("model");
  const modelSummary = useUsageSummary(
    modelFilters.toString(),
    "model",
    range.bucket,
    modelOpen && !reuseModelSummary,
  );
  const modelGroups = reuseModelSummary ? summary.data?.groups : modelSummary.data?.groups;
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
        Every request agent-lb served: which key asked, which account served it, and its API
        list-price equivalent (not billed).
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
              "muse",
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
              label: displayAccount(a, blurred),
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
        <DropdownMenu.Root open={modelOpen} onOpenChange={setModelOpen}>
          <DropdownMenu.Trigger className="select">
            <span className="k">Model</span>
            {params.get("model") || "All"}
            <ChevronDown size={13} />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="lb-dropdown" sideOffset={5} align="end">
              <DropdownMenu.Item onSelect={() => update("model", "")}>All</DropdownMenu.Item>
              {modelGroups
                ?.filter((m) => m.key)
                .map((m) => (
                  <DropdownMenu.Item key={m.key} onSelect={() => update("model", m.key!)}>
                    {m.key}
                  </DropdownMenu.Item>
                ))}
              {modelOpen && !reuseModelSummary && modelSummary.isPending && (
                <div className="usage-option-loading" role="status">
                  Loading models…
                </div>
              )}
              {modelOpen && !reuseModelSummary && modelSummary.isError && (
                <div className="usage-option-loading" role="alert">
                  Could not load models.
                </div>
              )}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
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
      <SubscriptionQuota pools={pools.data?.pools} failed={pools.isError} />
      {seatActivity.data?.seatActivity && (
        <SeatActivityStats
          activity={seatActivity.data.seatActivity}
          provider={params.get("provider") || ""}
        />
      )}
      {summary.data ? (
        !(
          ["cursor", "devin", "muse"].includes(params.get("provider") || "") &&
          summary.data.totals.requests === 0
        ) && (
          <>
            <SummaryStats totals={summary.data.totals} hours={range.hours} />
            <UsageChart series={summary.data.series} bucket={range.bucket} />
          </>
        )
      ) : summary.isPending ? (
        <>
          <div className="usage-loading-stats" role="status">
            Loading usage totals…
          </div>
          <div className="usage-chart-panel usage-loading-chart" role="status">
            Loading request chart…
          </div>
        </>
      ) : null}
      <Breakdown
        summary={summary.data}
        group={group}
        onGroup={setGroup}
        accounts={accounts}
        accountsPending={accountsQuery.isPending}
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
          {receipts.isPending && (
            <div className="usage-empty" role="status">
              Loading receipts…
            </div>
          )}
          {receipts.data?.receipts.map((r) => (
            <ReceiptRow
              key={r.id}
              receipt={r}
              account={accounts.get(r.accountId || "")}
              accountsPending={accountsQuery.isPending}
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
        accountsPending={accountsQuery.isPending}
        onClose={() => setSelected(null)}
        blurred={blurred}
      />
    </div>
  );
}
