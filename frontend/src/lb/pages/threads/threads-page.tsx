import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { compact } from "../../format";
import { FilterSelect, PageHead, EmptyState } from "../../kit/primitives";
import {
  useThreadsBurn,
  type PoolTotals,
  type ThreadBurn,
  type ThreadPoolBurn,
} from "../../api/threads";
import "./threads.css";

const ranges: Record<string, string> = { "24h": "Last 24 hours", "7d": "Last 7 days" };
const pools = [
  { key: "anthropic", label: "Claude" },
  { key: "openai", label: "Codex" },
];
const parse = (value: string) => new Date(/(Z|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
type SortKey = "thread" | "anthropic" | "openai" | "pace" | "requests" | "last";
const columns: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "thread", label: "Thread", numeric: false },
  { key: "anthropic", label: "Claude pool", numeric: true },
  { key: "openai", label: "Codex pool", numeric: true },
  { key: "pace", label: "×sustainable", numeric: true },
  { key: "requests", label: "Requests", numeric: true },
  { key: "last", label: "Last active", numeric: true },
];
const segments = [
  { key: "t3Threads", label: "T3 threads" },
  { key: "t3Unregistered", label: "Unregistered T3" },
  { key: "outsideT3", label: "Outside T3" },
  { key: "unexplained", label: "Unexplained" },
] as const;

const pct = (value: number) =>
  value <= 0
    ? "0%"
    : value < 0.1
      ? "<0.1%"
      : `${value < 10 ? value.toFixed(1) : Math.round(value)}%`;
const multiple = (value: number | null) =>
  value == null ? "—" : value < 0.1 ? "<0.1×" : `${value.toFixed(1)}×`;
const when = (value: string | null) => {
  if (!value) return "—";
  const minutes = Math.max(0, Math.round((Date.now() - parse(value).getTime()) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
};
const share = (thread: ThreadBurn) =>
  pools.reduce((sum, p) => sum + (thread.pools[p.key]?.poolSharePct ?? 0), 0);
const requestCount = (thread: ThreadBurn) =>
  pools.reduce((sum, p) => sum + (thread.pools[p.key]?.requests ?? 0), 0);
const pace = (thread: ThreadBurn) => {
  const values = pools
    .map((p) => thread.pools[p.key]?.paceMultiple)
    .filter((value): value is number => value != null);
  return values.length ? Math.max(...values) : null;
};
const sortValue = (thread: ThreadBurn, key: SortKey): number | string => {
  if (key === "thread") return (thread.title || "").toLowerCase();
  if (key === "anthropic" || key === "openai") return thread.pools[key]?.poolSharePct ?? 0;
  if (key === "pace") return pace(thread) ?? -1;
  if (key === "requests") return requestCount(thread);
  return thread.lastRequestAt ? parse(thread.lastRequestAt).getTime() : 0;
};
const sortRows = (rows: ThreadBurn[], key: SortKey | null, desc: boolean) => {
  if (!key) return [...rows].sort((a, b) => share(b) - share(a));
  const sign = desc ? -1 : 1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, key);
    const right = sortValue(b, key);
    return sign * (left < right ? -1 : left > right ? 1 : 0);
  });
};
const tag = (thread: ThreadBurn) =>
  thread.deleted ? "deleted" : thread.archived ? "archived" : "";

function PoolCell({ burn }: { burn?: ThreadPoolBurn }) {
  if (!burn || (burn.requests === 0 && burn.poolSharePct === 0)) {
    return <span className="c-pool muted">—</span>;
  }
  return (
    <span className="c-pool">
      <span className="mono">{pct(burn.poolSharePct)}</span>
      <span className="s mono">{pct(burn.accountWeekPct)} of an account</span>
    </span>
  );
}

function ThreadRow({ thread, child = false }: { thread: ThreadBurn; child?: boolean }) {
  const [open, setOpen] = useState(false);
  const state = tag(thread);
  const expandable = !child;
  return (
    <>
      <div className={`row thr-row ${child ? "thr-child" : ""} ${state ? "dim" : ""}`}>
        <div className="who thr-who">
          {expandable ? (
            <button
              className="thr-toggle"
              aria-expanded={open}
              aria-label={open ? "Collapse thread" : "Expand thread"}
              onClick={() => setOpen(!open)}
            >
              <ChevronRight size={14} className={open ? "open" : ""} />
            </button>
          ) : (
            <span className="thr-toggle" />
          )}
          <div className="t">
            <span className="n">{thread.title || "Untitled thread"}</span>
            <span className="s">
              {[thread.projectTitle, thread.host, state].filter(Boolean).join(" · ")}
            </span>
          </div>
        </div>
        {pools.map((p) => (
          <PoolCell key={p.key} burn={thread.pools[p.key]} />
        ))}
        <span className="mono r">{multiple(pace(thread))}</span>
        <span className="mono r">{compact(requestCount(thread))}</span>
        <span className="muted r">{when(thread.lastRequestAt)}</span>
      </div>
      {open && <ThreadDetail thread={thread} />}
    </>
  );
}

function ThreadDetail({ thread }: { thread: ThreadBurn }) {
  const models = Object.entries(thread.models).sort((a, b) => b[1] - a[1]);
  return (
    <div className="thr-detail">
      <div className="thr-facts">
        {models.length > 0 && (
          <p>
            <span className="muted">Requests by model</span>{" "}
            {models.map(([name, count]) => `${name} ${compact(count)}`).join(" · ")}
          </p>
        )}
        {pools.map((p) => {
          const burn = thread.pools[p.key];
          if (!burn || burn.requests === 0) return null;
          return (
            <p key={p.key}>
              <span className="muted">{p.label} tokens</span> {compact(burn.inputTokens)} in ·{" "}
              {compact(burn.outputTokens)} out · {compact(burn.cacheReadTokens)} cache read
              {burn.paceMultiple != null && ` · ${multiple(burn.paceMultiple)} sustainable`}
            </p>
          );
        })}
        {thread.subagentCount > 0 && (
          <p className="muted">Includes {thread.subagentCount} subagents and workflow agents.</p>
        )}
        {thread.nativeSessions.length > 0 && (
          <p>
            <span className="muted">Sessions</span>{" "}
            {thread.nativeSessions.map((session) => (
              <span key={session.nativeId} className="thr-session">
                <Link to={`/sessions/${encodeURIComponent(session.nativeId)}`} className="mono">
                  {session.nativeId.slice(0, 8)}
                </Link>{" "}
                <Link to={`/usage?session=${encodeURIComponent(session.nativeId)}`}>usage</Link>{" "}
                <span className="muted">{session.driver}</span>
              </span>
            ))}
          </p>
        )}
      </div>
      {thread.children.map((child) => (
        <ThreadRow key={`${child.host}:${child.threadId}`} thread={child} child />
      ))}
    </div>
  );
}

function PoolSentence({
  label,
  totals: t,
  hours,
  host,
}: {
  label: string;
  totals?: PoolTotals;
  hours: number;
  host: string;
}) {
  if (!t) return null;
  const window = hours === 24 ? "24 hours" : "7 days";
  const rest = host ? "All hosts: unregistered T3" : "Unregistered T3";
  return (
    <div className="thr-pool">
      <p className="thr-sentence">
        T3 threads{host ? ` on ${host}` : ""} used {pct(t.t3Threads.poolSharePct)} of the {label}{" "}
        pool&apos;s weekly quota in the last {window}. {rest} {pct(t.t3Unregistered.poolSharePct)},
        outside T3 {pct(t.outsideT3.poolSharePct)}, unexplained {pct(t.unexplained.poolSharePct)}.
      </p>
      <div className="thr-bar" role="img" aria-label={`${label} pool weekly quota by source`}>
        {segments.map((segment, index) => (
          <span
            key={segment.key}
            className={`thr-seg s${index + 1}`}
            title={`${segment.label} ${pct(t[segment.key].poolSharePct)}`}
            style={{ width: `${Math.min(100, Math.max(0, t[segment.key].poolSharePct))}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function SortHead({
  sort,
  desc,
  onSort,
}: {
  sort: SortKey | null;
  desc: boolean;
  onSort: (key: SortKey) => void;
}) {
  return (
    <div className="row hd">
      {columns.map((column) => {
        const active = sort === column.key;
        return (
          <span
            key={column.key}
            className={
              column.numeric && column.key !== "anthropic" && column.key !== "openai" ? "r" : ""
            }
            aria-sort={active ? (desc ? "descending" : "ascending") : "none"}
          >
            <button className={`thr-sort ${active ? "on" : ""}`} onClick={() => onSort(column.key)}>
              {column.label}
              {active && <span aria-hidden="true">{desc ? " ↓" : " ↑"}</span>}
            </button>
          </span>
        );
      })}
    </div>
  );
}

export function ThreadsPage() {
  const [params, setParams] = useSearchParams();
  const range = params.get("range") === "7d" ? "7d" : "24h";
  const host = params.get("host") || "";
  const { data, isPending, isError } = useThreadsBurn(range, host);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };
  const [sort, setSort] = useState<SortKey | null>(null);
  const [desc, setDesc] = useState(true);
  const onSort = (key: SortKey) => {
    if (sort === key) setDesc(!desc);
    else {
      setSort(key);
      setDesc(key !== "thread");
    }
  };
  const hours = data?.windowHours || (range === "7d" ? 168 : 24);
  const rows = sortRows(data?.threads || [], sort, desc);
  return (
    <div className="threads-page">
      <PageHead title="Threads">
        Which T3 threads burn which pool&apos;s weekly quota, with their subagents and workflow
        agents rolled up under the root thread.
      </PageHead>
      <div className="toolbar filters thr-filters">
        <FilterSelect
          label="Time"
          value={range}
          options={Object.entries(ranges).map(([value, label]) => ({ value, label }))}
          onChange={(v) => update("range", v === "24h" ? "" : v)}
        />
        <FilterSelect
          label="Host"
          value={host}
          options={[
            { value: "", label: "All" },
            { value: "studio", label: "Studio" },
            { value: "ax42", label: "ax42" },
          ]}
          onChange={(v) => update("host", v)}
        />
      </div>
      <div className="thr-summary">
        {pools.map((p) => (
          <PoolSentence
            key={p.key}
            label={p.label}
            totals={data?.pools[p.key]}
            hours={hours}
            host={data?.host ?? ""}
          />
        ))}
      </div>
      <div className="rows thr">
        <SortHead sort={sort} desc={desc} onSort={onSort} />
        {isPending && (
          <div className="thr-empty" role="status">
            Loading threads…
          </div>
        )}
        {isError && <div className="thr-empty">Could not load thread burn.</div>}
        {rows.map((thread) => (
          <ThreadRow key={`${thread.host}:${thread.rootThreadId}`} thread={thread} />
        ))}
        {data && rows.length === 0 && (
          <EmptyState title="No threads" description="No T3 thread traffic in this window." />
        )}
      </div>
    </div>
  );
}
