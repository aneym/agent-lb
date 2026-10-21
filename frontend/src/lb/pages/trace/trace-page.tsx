import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "@/lib/api-client";
import { PARTS, useSessionTrace, type SessionTrace, type TraceRequest } from "../../api/trace";
import { compact } from "../../format";
import { EmptyState, PageHead, Section, SidePanel } from "../../kit/primitives";
import { latency, number, percent } from "../usage/usage-format";
import { PartsBar, TraceTimeline } from "./trace-timeline";
import "./trace.css";

const share = (part: number, total: number) => (total > 0 ? Math.round((part / total) * 100) : 0);
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

function Totals({ trace }: { trace: SessionTrace }) {
  const { totals } = trace;
  const partsTotal = PARTS.reduce((sum, part) => sum + totals.estParts[part.key], 0);
  const figures = [
    ["Prompt tokens", compact(totals.promptTokens), `${number(totals.promptTokens)} in all`],
    [
      "Read from cache",
      `${share(totals.cacheReadTokens, totals.promptTokens)}%`,
      `${compact(totals.cacheReadTokens)} tokens`,
    ],
    ["Total latency", seconds(totals.latencyMs), `${totals.requests} requests`],
    [
      "Measured",
      `${Math.round(totals.coverage * 100)}%`,
      `${totals.measured} of ${totals.requests} bodies`,
    ],
  ];
  return (
    <div
      className="trace-totals"
      data-testid="trace-totals"
      data-prompt={totals.promptTokens}
      data-measured={totals.measured}
    >
      <div className="stats">
        {figures.map(([label, value, sub]) => (
          <div className="stat" key={label}>
            <div className="k">{label}</div>
            <div className="v">{value}</div>
            <div className="s">{sub}</div>
          </div>
        ))}
      </div>
      <div className="trace-context">
        <div className="trace-context-h">What filled the context, est.</div>
        {partsTotal > 0 ? (
          <PartsBar parts={totals.estParts} className="wide" />
        ) : (
          <span className="trace-parts wide unmeasured" title="Context not measured" />
        )}
        <ul className="trace-legend">
          {PARTS.map((part) => (
            <li key={part.key}>
              <i
                className={part.key === "toolResults" ? "outlined" : undefined}
                style={{ background: part.fill }}
              />
              <span>{part.label}</span>
              <span className="mono">
                {compact(totals.estParts[part.key])} est. ·{" "}
                {share(totals.estParts[part.key], partsTotal)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function RequestPanel({ request, onClose }: { request: TraceRequest | null; onClose: () => void }) {
  if (!request) return null;
  const parts: [string, string][] = PARTS.map((part) => [
    `${part.label}, est.`,
    request.estParts && request.partChars
      ? `${number(request.estParts[part.key])} tok · ${number(request.partChars[part.key])} chars`
      : "Not measured",
  ]);
  const fields: [string, string][] = [
    ["Model", request.model],
    ["Time", new Date(request.requestedAt).toLocaleString()],
    ["Status", request.status],
    ["First token", latency(request.latencyFirstTokenMs)],
    ["Latency", latency(request.latencyMs)],
    ["Prompt tokens", number(request.promptTokens)],
    ["Uncached", number(request.uncachedTokens)],
    ["Cache read", `${number(request.cacheReadTokens)} · ${percent(request.cacheReadRatio)}`],
    ["Cache write", number(request.cacheWriteTokens)],
    ["Cache event", request.cacheKind ? request.cacheKind.replace(/_/g, " ") : "none"],
    ["Output tokens", number(request.outputTokens)],
    ...parts,
    [
      "Chars per token check",
      request.estimateRatio == null ? "—" : request.estimateRatio.toFixed(3),
    ],
    ["Body", request.bodyBytes == null ? "—" : `${compact(request.bodyBytes)} bytes`],
  ];
  return (
    <SidePanel
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Request"
    >
      <div data-testid="trace-panel" data-log-id={request.logId} data-prompt={request.promptTokens}>
        <dl className="dl">
          {fields.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="mono">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </SidePanel>
  );
}

export function TracePage() {
  const { sessionId = "" } = useParams();
  const query = useSessionTrace(sessionId);
  const [selected, setSelected] = useState<TraceRequest | null>(null);
  if (query.isPending) {
    return (
      <div className="trace-page">
        <p className="trace-note" role="status">
          Loading trace…
        </p>
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="trace-page">
        {query.error instanceof ApiError && query.error.status === 404 ? (
          <EmptyState title="No requests for this session" description={sessionId} />
        ) : (
          <p className="trace-note" role="alert">
            Could not load this trace. Try refreshing the page.
          </p>
        )}
      </div>
    );
  }
  const trace = query.data;
  return (
    <div className="trace-page">
      <PageHead title="Session trace">
        <span className="mono trace-id">{trace.sessionId}</span>
        {" · "}
        {trace.totals.requests} requests · {trace.totals.measured} measured
      </PageHead>
      <Totals trace={trace} />
      <Section
        title="Requests"
        aside="Oldest first · width is latency · colour is share read from cache · tag is cache event"
      >
        <TraceTimeline requests={trace.requests} onSelect={setSelected} />
      </Section>
      <RequestPanel request={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
