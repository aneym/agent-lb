import type { KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import type { AccountSummary } from "@/features/accounts/schemas";
import type { Receipt } from "../../api/receipts";
import { compact } from "../../format";
import { ProviderMark } from "../../kit/provider-mark";
import { ModelChip, SidePanel } from "../../kit/primitives";
import { number, percent, latency, usageMoney, displayAccount, markFor } from "./usage-format";

export function ReceiptRow({
  receipt,
  account,
  onSelect,
  blurred,
  accountsPending = false,
}: {
  receipt: Receipt;
  account?: AccountSummary;
  onSelect: () => void;
  blurred: boolean;
  accountsPending?: boolean;
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
        {session?.slice(0, 8) || "—"}
        <span className="usage-key"> · {receipt.apiKeyName || "no key"}</span>
      </span>
      <span className="c-acct cellm">
        <ProviderMark id={markFor(accountsPending && !account && receipt.provider !== "muse" ? null : receipt.provider || account?.provider)} size={16} />
        <span>{displayAccount(account, blurred, accountsPending, receipt.provider)}</span>
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
      <span
        className={`c-st mono right tint ${receipt.status === "ok" ? "ok" : receipt.httpStatus === 429 ? "warn" : "bad"}`}
      >
        {status} {receipt.httpStatus || receipt.errorCode || receipt.status}
      </span>
      <span className="c-cost mono right">
        {receipt.costUsd == null ? "—" : usageMoney(receipt.costUsd)}
      </span>
    </div>
  );
}
export function RouteTrace({
  receipt,
  account,
  onClose,
  blurred,
  accountsPending = false,
}: {
  receipt: Receipt | null;
  account?: AccountSummary;
  onClose: () => void;
  blurred: boolean;
  accountsPending?: boolean;
}) {
  if (!receipt) return null;
  const fields = [
    ["Time", new Date(receipt.requestedAt).toLocaleString()],
    ["Account", displayAccount(account, blurred, accountsPending, receipt.provider)],
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
              <dd className="mono">{value}</dd>
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

