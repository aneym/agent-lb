import type { KeyboardEvent } from "react";
import { PARTS, type TraceParts, type TraceRequest } from "../../api/trace";
import { compact } from "../../format";
import { ModelChip } from "../../kit/primitives";
import { latency, percent } from "../usage/usage-format";

const CACHE_FILL: Record<string, string> = {
  growth: "var(--ok-fill)",
  first_write: "var(--warn-fill)",
  compaction: "var(--warn-fill)",
  ttl_expiry: "var(--bad-fill)",
  account_switch: "var(--bad-fill)",
  bust: "var(--bad-fill)",
  other_miss: "var(--bad-fill)",
};

const partsAttr = (parts: TraceParts | null) =>
  parts ? PARTS.map((part) => parts[part.key]).join(",") : "";

const time = (value: string) =>
  new Date(value).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

export function PartsBar({ parts, className }: { parts: TraceParts; className: string }) {
  return (
    <span className={`trace-parts ${className}`}>
      {PARTS.map((part) =>
        parts[part.key] > 0 ? (
          <i
            key={part.key}
            className={part.key === "toolResults" ? "outlined" : undefined}
            style={{ flexGrow: parts[part.key], background: part.fill }}
          />
        ) : null,
      )}
    </span>
  );
}

function TraceRow({
  request,
  maxLatency,
  maxPrompt,
  onSelect,
}: {
  request: TraceRequest;
  maxLatency: number;
  maxPrompt: number;
  onSelect: () => void;
}) {
  const latencyMs = request.latencyMs ?? 0;
  const barWidth = maxLatency > 0 ? (latencyMs / maxLatency) * 100 : 0;
  const stripWidth = maxPrompt > 0 ? (request.promptTokens / maxPrompt) * 100 : 0;
  const cache = request.cacheKind ?? "none";
  const failed = request.status !== "success";
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };
  return (
    <div
      className="trace-row"
      data-testid="trace-row"
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={onKey}
    >
      <span className="trace-time mono">
        {failed && (
          <span className="trace-fail" title={request.status}>
            ×
          </span>
        )}
        {time(request.requestedAt)}
      </span>
      <span className="trace-model">
        <ModelChip model={request.model} />
      </span>
      <span className="trace-track">
        <span
          className="trace-bar"
          data-testid="trace-bar"
          data-cache={cache}
          data-latency={latencyMs}
          data-parts={partsAttr(request.estParts)}
          title={cache === "none" ? "No cache write event" : cache.replace(/_/g, " ")}
          style={{
            width: `max(2px, ${barWidth}%)`,
            background: CACHE_FILL[cache] ?? "var(--g3)",
          }}
        />
        {request.estParts ? (
          <span className="trace-strip" style={{ width: `${stripWidth}%` }}>
            <PartsBar parts={request.estParts} className="thin" />
          </span>
        ) : (
          <span
            className="trace-strip unmeasured"
            title="Context not measured"
            style={{ width: `${stripWidth}%` }}
          />
        )}
      </span>
      <span className="trace-fig mono">{compact(request.promptTokens)}</span>
      <span className="trace-fig mono">{percent(request.cacheReadRatio)}</span>
      <span className="trace-fig mono">{latency(request.latencyMs)}</span>
    </div>
  );
}

export function TraceTimeline({
  requests,
  onSelect,
}: {
  requests: TraceRequest[];
  onSelect: (request: TraceRequest) => void;
}) {
  const maxLatency = Math.max(0, ...requests.map((r) => r.latencyMs ?? 0));
  const maxPrompt = Math.max(0, ...requests.map((r) => r.promptTokens));
  return (
    <div className="trace-rows">
      <div className="trace-row hd" aria-hidden="true">
        <span>Time</span>
        <span>Model</span>
        <span />
        <span className="trace-fig">Prompt</span>
        <span className="trace-fig">Cache</span>
        <span className="trace-fig">Latency</span>
      </div>
      {requests.map((request) => (
        <TraceRow
          key={request.logId}
          request={request}
          maxLatency={maxLatency}
          maxPrompt={maxPrompt}
          onSelect={() => onSelect(request)}
        />
      ))}
    </div>
  );
}
