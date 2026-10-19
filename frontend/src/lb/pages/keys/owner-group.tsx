import { useState } from "react";
import { Link } from "react-router-dom";
import { Play, UserRound } from "lucide-react";

import type { ApiKey } from "@/features/api-keys/schemas";
import type { TeamMember } from "@/features/team/schemas";
import { relative, duration } from "@/lb/format";
import { RowMenu } from "@/lb/kit/primitives";
import { ProviderMark } from "@/lb/kit/provider-mark";
import { providerMark } from "@/lb/kit/provider-mark-helpers";
import type { KeyWithOwner } from "./keys-model";

const dateLabel = (date: string) =>
  new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(date));
const money = (amount: number) =>
  `$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(amount)}`;

function Meter({ value, max }: { value: number; max: number }) {
  return (
    <span className="bar" role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <i style={{ width: `${Math.min(100, Math.max(0, (value / max) * 100))}%` }} />
    </span>
  );
}

function KeyRow({
  apiKey,
  accounts,
  onEdit,
  onRegenerate,
  onRevoke,
}: {
  apiKey: KeyWithOwner;
  accounts: { accountId: string; provider?: string }[];
  onEdit: (key: ApiKey) => void;
  onRegenerate: (key: KeyWithOwner) => void;
  onRevoke: (key: KeyWithOwner) => void;
}) {
  const providers = [
    ...new Set(
      apiKey.assignedAccountIds
        .map((id) => accounts.find((account) => account.accountId === id)?.provider)
        .filter((provider): provider is string => Boolean(provider)),
    ),
  ];
  const cap = apiKey.limits.find(
    (limit) => limit.limitType === "cost_usd" && ["weekly", "7d"].includes(limit.limitWindow),
  );
  const spent = apiKey.usageSummary?.totalCostUsd;
  const [now] = useState(() => Date.now());
  const expires = apiKey.expiresAt ? new Date(apiKey.expiresAt).getTime() - now : null;
  return (
    <div className={`row link ${apiKey.isActive ? "" : "dim"}`}>
      <div className="c-who who">
        <div className="t">
          <Link to={`/usage?key=${encodeURIComponent(apiKey.id)}`} className="n">
            {apiKey.name} <span className="mono muted">••{apiKey.keyPrefix.slice(-4)}</span>
          </Link>
          <span className="s">
            created {dateLabel(apiKey.createdAt)}
            {!apiKey.isActive ? " · revoked" : ""}
          </span>
        </div>
      </div>
      <div className="c-scope scope">
        {apiKey.assignedAccountIds.length === 0 ? (
          <span className="muted">All accounts</span>
        ) : providers.length > 0 ? (
          providers.map((provider) => (
            <ProviderMark key={provider} id={providerMark(provider)} size={16} />
          ))
        ) : (
          <span className="muted">{apiKey.assignedAccountIds.length} accounts</span>
        )}
      </div>
      <div className="c-spend">
        <div className="spend">
          <div className="t">
            <span className="num">{spent == null ? "—" : money(spent)}</span>
            <span className="muted">
              {cap ? `of ${money(cap.maxValue / 1_000_000)} cap` : "no cap"}
            </span>
          </div>
          {cap && <Meter value={cap.currentValue} max={cap.maxValue} />}
        </div>
      </div>
      <span className="c-used muted">
        {apiKey.lastUsedAt ? relative(apiKey.lastUsedAt) : "never"}
      </span>
      <span className="c-exp muted">
        {expires === null
          ? "never"
          : expires <= 0
            ? `expired ${dateLabel(apiKey.expiresAt!)}`
            : `in ${duration(expires)}`}
      </span>
      <div className="c-more">
        <RowMenu
          label={`Actions for ${apiKey.name}`}
          items={[
            { label: "Edit limits", onSelect: () => onEdit(apiKey) },
            { label: "Regenerate", onSelect: () => onRegenerate(apiKey) },
            {
              label: "Revoke",
              onSelect: () => onRevoke(apiKey),
              hidden: !apiKey.isActive,
            },
          ]}
        />
      </div>
    </div>
  );
}

export function OwnerGroup({
  name,
  detail,
  keys,
  member,
  accounts,
  onEdit,
  onRegenerate,
  onRevoke,
  onRevokeAll,
  onEditMember,
  onSuspend,
  onRemove,
  onOnboarding,
  onIssueKey,
}: {
  name: string;
  detail: string;
  keys: KeyWithOwner[];
  member?: TeamMember;
  accounts: { accountId: string; provider?: string }[];
  onEdit: (key: ApiKey) => void;
  onRegenerate: (key: KeyWithOwner) => void;
  onRevoke: (key: KeyWithOwner) => void;
  onRevokeAll?: () => void;
  onEditMember: (member: TeamMember) => void;
  onSuspend: (member: TeamMember) => void;
  onRemove: (member: TeamMember) => void;
  onOnboarding: (member: TeamMember) => void;
  onIssueKey: (member: TeamMember) => void;
}) {
  const cap =
    member &&
    (member.costCapWeekUsd != null
      ? {
          limit: member.costCapWeekUsd,
          value: member.usage.week.costUsd,
          reset: "Mon",
        }
      : member.costCapDayUsd != null
        ? {
            limit: member.costCapDayUsd,
            value: member.usage.day.costUsd,
            reset: "tomorrow",
          }
        : member.costCapMonthUsd != null
          ? {
              limit: member.costCapMonthUsd,
              value: member.usage.month.costUsd,
              reset: "next month",
            }
          : null);
  return (
    <>
      <div className="grp">
        <span aria-hidden="true">{onRevokeAll ? <Play size={16} /> : <UserRound size={16} />}</span>
        <span className="gn">{name}</span>
        <span className="gs">{detail}</span>
        {cap && (
          <span className="cap">
            <span className="num">
              {money(cap.value)} of {money(cap.limit)}
            </span>
            <Meter value={cap.value} max={cap.limit} />
            <span>resets {cap.reset}</span>
          </span>
        )}
        {member && (
          <span className="ga">
            <RowMenu
              label={`Manage ${member.name}`}
              items={[
                { label: "Issue key", onSelect: () => onIssueKey(member) },
                {
                  label: "Edit caps and details",
                  onSelect: () => onEditMember(member),
                },
                {
                  label: member.status === "active" ? "Suspend" : "Reactivate",
                  onSelect: () => onSuspend(member),
                },
                { label: "Onboarding", onSelect: () => onOnboarding(member) },
                { label: "Remove", onSelect: () => onRemove(member) },
              ]}
            />
          </span>
        )}
        {onRevokeAll && (
          <span className="ga">
            <button type="button" className="btn ghost sm" onClick={onRevokeAll}>
              Revoke all
            </button>
          </span>
        )}
      </div>
      {keys.map((key) => (
        <KeyRow
          key={key.id}
          apiKey={key}
          accounts={accounts}
          onEdit={onEdit}
          onRegenerate={onRegenerate}
          onRevoke={onRevoke}
        />
      ))}
    </>
  );
}

