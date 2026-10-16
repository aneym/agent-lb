import { useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import type { AccountSummarySchema } from "@/features/accounts/schemas";
import {
  usePools,
  useReceiptSummary,
  useSeatAccounts,
  useStickySessions,
  type Pool,
  type SeatAccount,
  type SeatQuota,
  type SeatQuotaWindow,
} from "../../api";
import { accountLabel, compact, money } from "../../format";
import { usePrivacyStore } from "@/hooks/use-privacy";

function durationWords(milliseconds: number) {
  const minutes = Math.max(0, Math.ceil(milliseconds / 60_000));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [
    hours && `${hours} ${hours === 1 ? "hour" : "hours"}`,
    (rest || !hours) && `${rest} ${rest === 1 ? "minute" : "minutes"}`,
  ]
    .filter(Boolean)
    .join(" ");
}
import { ProviderMark } from "../../kit/provider-mark";
import { poolProvider, providerMark, vendorName } from "../../kit/provider-mark-helpers";
import {
  AccountState,
  EmptyState,
  FilterSelect,
  LimitMeter,
  Pace,
  PageHead,
  PoolState,
  RowMenu,
  SearchField,
  Section,
  Seg,
} from "../../kit/primitives";
import { PausePopover } from "../../kit/pause-popover";
import { RoutingDashboard } from "@/features/dashboard/components/routing-dashboard";
import { useAccountPlan } from "@/features/dashboard/use-account-plan";
import type { z } from "zod";

type Account = z.infer<typeof AccountSummarySchema>;
const poolName: Record<string, string> = {
  "anthropic-general": "Anthropic general",
  "openai-codex": "ChatGPT · Codex",
  glm: "GLM",
  kimi: "Kimi",
  openrouter: "OpenRouter",
  cursor: "Cursor",
  "cursor-models": "Cursor models",
  "cursor-other": "Cursor · other models",
  devin: "Devin",
  muse: "Muse",
};
const poolModels: Record<string, string> = {
  "anthropic-general": "opus-latest · sonnet-latest",
  "openai-codex": "sol-latest · luna-latest",
  cursor: "Cursor seat",
  "cursor-models": "Grok · Composer",
  "cursor-other": "Cursor seat",
  devin: "Devin seat",
  muse: "Muse seat",
};
const SEAT_VENDORS = ["cursor", "devin", "muse"];
const ET = "America/New_York";

function etWhen(iso: string, now: number) {
  const at = new Date(iso);
  const ahead = at.getTime() - now;
  const options: Intl.DateTimeFormatOptions =
    Math.abs(ahead) > 6 * 86_400_000
      ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
      : Math.abs(ahead) > 18 * 3_600_000
        ? { weekday: "short", hour: "numeric", minute: "2-digit" }
        : { hour: "numeric", minute: "2-digit" };
  return `${new Intl.DateTimeFormat("en-US", { ...options, timeZone: ET }).format(at)} ET`;
}
function unavailableText(vendor: string, reason?: SeatQuota["unavailableReason"]) {
  const name = vendorName(vendor);
  switch (reason) {
    case "auth_unsupported":
      return "Login can't read usage";
    case "credential_unavailable":
      return "Usage login unavailable";
    case "vendor_error":
      return "Vendor error";
    case "not_collected":
      return "Not checked yet";
    case "not_reported":
    default:
      return `Not reported by ${name}`;
  }
}
const isFiveHour = (w: { kind: string; durationMins?: number | null }) =>
  w.kind === "session" && (w.durationMins == null || w.durationMins <= 300);
const unitFor = (kind: string): "wk" | "mo" | undefined =>
  kind === "weekly" ? "wk" : kind === "monthly" ? "mo" : undefined;
function sourceNote(source: string | null | undefined, observedAt: string | null | undefined, stale: boolean, now: number) {
  if (source === "estimate") return "est.";
  if ((source === "vendor_observed" || stale) && observedAt) return `as of ${etWhen(observedAt, now)}`;
  return null;
}
function isSeatPool(pool: Pool) {
  return (
    pool.kind === "cli_seat" ||
    pool.kind === "cli_seat_budget" ||
    SEAT_VENDORS.includes(pool.provider)
  );
}
function MeterStack({ children }: { children: ReactNode }) {
  return <div style={{ display: "grid", gap: 10, minWidth: 0 }}>{children}</div>;
}

const accountName = (a: Account) => accountLabel(a, usePrivacyStore.getState().blurred);
function seatPoolMeters(pool: Pool, now: number) {
  const windows = pool.windows;
  const vendor = pool.provider;
  if (windows == null) {
    const remaining =
      pool.percentUsed != null
        ? Math.max(0, 100 - pool.percentUsed)
        : (pool.aggregateRemainingPercent ?? pool.headroomPercent ?? null);
    const reset = pool.cycleResetAt || pool.resetAt;
    const parts = [
      reset ? `resets ${etWhen(reset, now)}` : null,
      sourceNote(pool.percentSource, null, false, now),
    ].filter(Boolean);
    return {
      long: (
        <LimitMeter
          remaining={remaining}
          detail={remaining != null && parts.length ? parts.join(" · ") : undefined}
          empty="No reading"
          unit={pool.windowLabel === "month" ? "mo" : "wk"}
        />
      ),
      session: null,
    };
  }
  const long = windows.filter((w) => !isFiveHour(w));
  const session = windows.find((w) => isFiveHour(w)) ?? null;
  return {
    long: long.length ? (
      <MeterStack>
        {long.map((w) => (
          <LimitMeter
            key={w.id}
            remaining={w.remainingPercent}
            unit={unitFor(w.kind)}
            empty={unavailableText(vendor)}
            detail={[
              long.length > 1 ? w.label : null,
              w.resetsAt ? `resets ${etWhen(w.resetsAt, now)}` : null,
              sourceNote(w.source, null, false, now),
            ]
              .filter(Boolean)
              .join(" · ")}
          />
        ))}
      </MeterStack>
    ) : (
      <LimitMeter
        remaining={null}
        empty={session ? "No weekly window" : unavailableText(vendor, "not_reported")}
      />
    ),
    session: session ? (
      <LimitMeter
        remaining={session.remainingPercent}
        resetAt={session.resetsAt}
        unit="5h"
        detail={sourceNote(session.source, null, false, now) ?? undefined}
      />
    ) : null,
  };
}
function PoolRow({ pool, bestAccount }: { pool: Pool; bestAccount?: string }) {
  const [now] = useState(() => Date.now());
  const seatPool = isSeatPool(pool);
  const seatMeters = seatPool ? seatPoolMeters(pool, now) : null;
  const no5h = seatPool ? !seatMeters?.session : ["openai", "kimi", "openrouter"].includes(pool.provider);
  const long = pool.weeklyRemainingPercent ?? (pool.windowLabel ? pool.aggregateRemainingPercent : null);
  const reset = pool.weeklyResetAt || pool.resetAt;
  const longLabel = pool.windowLabel === "month" ? "mo" : "wk";
  const paceTick =
    pool.weeklyPacePercent != null && long != null ? long - pool.weeklyPacePercent : undefined;
  return (
    <div className="row link">
      <div className="c-who who">
        <ProviderMark id={pool.provider === "anthropic" ? "claude" : poolProvider(pool.id)} />
        <div className="t">
          <span className="n">{poolName[pool.id] || pool.id}</span>
          <span className="s">
            {poolModels[pool.id] || (seatPool ? `${vendorName(pool.provider)} seat` : pool.provider)}
          </span>
        </div>
      </div>
      <div className="c-st">
        <PoolState status={pool.status} ready={pool.eligibleAccounts} total={pool.accounts} />
      </div>
      <div className="c-wk">
        {seatMeters ? (
          seatMeters.long
        ) : (
          <LimitMeter
            remaining={long}
            resetAt={reset}
            paceTick={paceTick}
            paceDelta={pool.weeklyPacePercent}
            empty={["glm", "kimi"].includes(pool.provider) ? "No cap" : "No reading"}
            unit={longLabel}
          />
        )}
      </div>
      <div className={`c-h5 ${no5h ? "none" : ""}`}>
        {seatMeters ? (
          (seatMeters.session ?? <LimitMeter noWindow />)
        ) : (
          <LimitMeter
            remaining={pool.fiveHourRemainingPercent}
            resetAt={pool.fiveHourResetAt}
            detail={bestAccount ? `on ${bestAccount}` : undefined}
            noWindow={no5h}
            unit="5h"
          />
        )}
      </div>
      <div className={`c-pace ${pool.weeklyPacePercent == null ? "none" : ""}`}>
        <Pace value={pool.weeklyPacePercent} />
      </div>
    </div>
  );
}
function AccountRow({
  dim,
  onOpen,
  label,
  mark,
  name,
  sub,
  state,
  limit,
  fiveHour,
  today,
  menu,
}: {
  dim?: boolean;
  onOpen?: () => void;
  label: string;
  mark: ReactNode;
  name: ReactNode;
  sub: ReactNode;
  state: ReactNode;
  limit: ReactNode;
  fiveHour: ReactNode | null;
  today: ReactNode;
  menu?: ReactNode;
}) {
  function open(e: MouseEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest("button,a,.pop")) return;
    onOpen?.();
  }
  return (
    <div
      className={`row ${onOpen ? "link" : ""} ${dim ? "dim" : ""}`}
      aria-label={label}
      {...(onOpen
        ? {
            role: "link",
            tabIndex: 0,
            onClick: open,
            onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => {
              if (e.key === "Enter" && e.target === e.currentTarget) onOpen();
            },
          }
        : { role: "group" })}
    >
      <div className="c-who who">
        {mark}
        <div className="t">
          <span className="n">{name}</span>
          <span className="s">{sub}</span>
        </div>
      </div>
      <div className="c-st">{state}</div>
      <div className="c-wk">{limit}</div>
      <div className={`c-h5 ${fiveHour ? "" : "none"}`}>{fiveHour ?? <LimitMeter noWindow />}</div>
      <div className="c-today today">{today}</div>
      <div className="c-more lb-pop-wrap">{menu}</div>
    </div>
  );
}
function ProxyAccountRow({
  a,
  summary,
  sessions,
}: {
  a: Account;
  summary?: { requests: number; costUsd: number };
  sessions?: number;
}) {
  const navigate = useNavigate();
  const hideEmails = usePrivacyStore((state) => state.blurred);
  const [pauseOpen, setPauseOpen] = useState(false);
  const { resumeMutation, probeMutation } = useAccounts();
  const no5h = ["openai", "kimi", "cursor", "devin", "muse", "openrouter"].includes(a.provider || "");
  const has5h = !no5h && a.windowMinutesPrimary === 300;
  const secondary = a.usage?.secondaryRemainingPercent;
  const reset = a.resetAtSecondary;
  const [now] = useState(() => Date.now());
  const expected = reset
    ? Math.max(
        0,
        Math.min(
          100,
          ((new Date(reset).getTime() - now) / ((a.windowMinutesSecondary || 10080) * 60000)) * 100,
        ),
      )
    : undefined;
  const rowLink = `/providers/${encodeURIComponent(a.accountId)}`;
  return (
    <AccountRow
      label={accountLabel(a, hideEmails)}
      dim={["paused", "reauth_required", "deactivated"].includes(a.status)}
      onOpen={() => navigate(rowLink)}
      mark={<ProviderMark id={a.provider === "anthropic" ? "claude" : providerMark(a.provider)} />}
      name={accountLabel(a, hideEmails)}
      sub={
        <>
          <span className={hideEmails ? "privacy-blur" : undefined}>{a.email}</span>
          <span className="mono id"> · {a.accountId.slice(0, 6)}</span>
        </>
      }
      state={
        <AccountState
          status={a.status}
          resetAt={a.rateLimitResetAt || a.resetAtSecondary}
          sub={a.status === "active" && sessions ? `holding ${sessions} sessions` : undefined}
        />
      }
      limit={
        <LimitMeter
          remaining={a.status === "reauth_required" ? null : secondary}
          resetAt={reset}
          paceTick={secondary != null ? expected : undefined}
          paceDelta={secondary != null && expected != null ? secondary - expected : undefined}
          empty={["glm", "kimi"].includes(a.provider || "") ? "No cap" : "No reading"}
          unit={a.windowMinutesSecondary && a.windowMinutesSecondary > 20160 ? "mo" : "wk"}
        />
      }
      fiveHour={
        no5h ? null : (
          <LimitMeter
            remaining={has5h ? a.usage?.primaryRemainingPercent : null}
            resetAt={has5h ? a.resetAtPrimary : null}
            notStarted={has5h && a.usage?.primaryRemainingPercent === 100 && !a.resetAtPrimary}
            unit="5h"
          />
        )
      }
      today={
        <>
          <span className="num">{summary?.requests.toLocaleString() || "—"}</span>
          {summary && <span className="s">{money(summary.costUsd)} API value</span>}
        </>
      }
      menu={
        <>
          <RowMenu
            label={`Actions for ${accountName(a)}`}
            items={[
              { label: "Open", onSelect: () => navigate(rowLink) },
              { label: "Pause…", hidden: a.status === "paused", onSelect: () => setPauseOpen(true) },
              {
                label: "Resume",
                hidden: a.status !== "paused",
                onSelect: () => resumeMutation.mutate(a.accountId),
              },
              {
                label: "Check limits now",
                onSelect: () => probeMutation.mutate({ accountId: a.accountId }),
              },
              {
                label: "Sign in again",
                hidden: a.status !== "reauth_required",
                onSelect: () => navigate(`${rowLink}?signin=1`),
              },
              { label: "Remove…", onSelect: () => navigate(rowLink) },
            ]}
          />
          {pauseOpen && (
            <PausePopover
              accountId={a.accountId}
              pool={a.provider || "this pool"}
              fiveHourResetAt={has5h ? a.resetAtPrimary : null}
              weeklyResetAt={a.resetAtSecondary}
              onClose={() => setPauseOpen(false)}
            />
          )}
        </>
      }
    />
  );
}
function seatStatus(a: SeatAccount) {
  if (a.status) return a.status;
  if (!a.enabled) return "paused";
  if (a.authOk === false) return "reauth_required";
  return a.ready ? "active" : "rate_limited";
}
function seatName(a: SeatAccount) {
  return `${vendorName(a.vendor)} · ${a.planLabel || a.tier || "seat"}`;
}
function primaryWindow(a: SeatAccount) {
  const windows = a.quota?.windows ?? [];
  return windows.find((w) => w.id === a.quota?.primaryWindowId) ?? windows[0] ?? null;
}
function SeatToday({ today, vendor }: { today?: SeatAccount["today"]; vendor: string }) {
  if (!today) return <span className="num">—</span>;
  const tokens =
    today.tokensKnown && today.tokensIn != null && today.tokensOut != null
      ? today.tokensIn + today.tokensOut
      : null;
  const value =
    today.valueUsd != null
      ? `${money(today.valueUsd)}${today.valueSource === "estimate" ? " est." : ""} value`
      : tokens != null
        ? `${compact(tokens)} tokens`
        : today.runs
          ? "tokens not reported"
          : null;
  const title = [
    `${today.runs} ${today.runs === 1 ? "run" : "runs"}, ${today.ok} ok in the last 24 hours`,
    tokens != null ? `${compact(tokens)} tokens` : `tokens not reported by ${vendorName(vendor)}`,
    ...today.models.map((m) => `${m.model}: ${m.runs}`),
  ].join(" · ");
  return (
    <>
      <span className="num" title={title}>
        {today.runs.toLocaleString()} {today.runs === 1 ? "run" : "runs"}
      </span>
      {value && (
        <span className="s" title={title}>
          {value}
        </span>
      )}
    </>
  );
}
function SeatAccountRow({ a }: { a: SeatAccount }) {
  const hideEmails = usePrivacyStore((state) => state.blurred);
  const [now] = useState(() => Date.now());
  const status = seatStatus(a);
  const quota = a.quota;
  const windows = quota?.windows ?? [];
  const long = windows.filter((w) => !isFiveHour(w));
  const session = windows.find((w) => isFiveHour(w)) ?? null;
  const note = (w: SeatQuotaWindow) =>
    sourceNote(w.source, quota?.observedAt, quota?.stale ?? false, now);
  const meter = (w: SeatQuotaWindow) => {
    const expected =
      w.durationMins && w.resetsAt && w.kind !== "session"
        ? Math.max(0, Math.min(100, ((new Date(w.resetsAt).getTime() - now) / (w.durationMins * 60000)) * 100))
        : undefined;
    return (
      <LimitMeter
        key={w.id}
        remaining={w.remainingPercent}
        unit={unitFor(w.kind)}
        paceTick={w.remainingPercent != null ? expected : undefined}
        paceDelta={w.remainingPercent != null && expected != null ? w.remainingPercent - expected : undefined}
        empty={unavailableText(a.vendor, quota?.unavailableReason)}
        detail={[w.label, w.resetsAt ? `resets ${etWhen(w.resetsAt, now)}` : null, note(w)]
          .filter(Boolean)
          .join(" · ")}
      />
    );
  };
  const reasonText = quota
    ? unavailableText(a.vendor, quota.unavailableReason)
    : "No reading";
  const limit =
    status === "reauth_required" ? (
      <LimitMeter remaining={null} empty="Sign in to read usage" />
    ) : long.length ? (
      <MeterStack>{long.map(meter)}</MeterStack>
    ) : (
      <LimitMeter remaining={null} empty={session ? "No weekly reading" : reasonText} />
    );
  const fiveHour =
    session && status !== "reauth_required" ? (
      <LimitMeter
        remaining={session.remainingPercent}
        resetAt={session.resetsAt}
        unit="5h"
        detail={note(session) ?? undefined}
      />
    ) : null;
  const stateSub =
    status === "rate_limited" && a.statusResetAt
      ? `until ${etWhen(a.statusResetAt, now)}`
      : status === "paused"
        ? "turned off"
        : status === "active" && a.lastRunAt
          ? `last run ${etWhen(a.lastRunAt, now)}`
          : undefined;
  return (
    <AccountRow
      label={`${seatName(a)} ${a.id}`}
      dim={["paused", "reauth_required"].includes(status)}
      mark={<ProviderMark id={providerMark(a.vendor)} />}
      name={seatName(a)}
      sub={
        <>
          {a.email && (
            <span className={hideEmails ? "privacy-blur" : undefined}>{a.email} · </span>
          )}
          <span className="mono id">{a.id}</span>
        </>
      }
      state={<AccountState status={status} sub={stateSub} />}
      limit={limit}
      fiveHour={fiveHour}
      today={<SeatToday today={a.today} vendor={a.vendor} />}
    />
  );
}
function seatSortKey(a: SeatAccount, sort: string) {
  const primary = primaryWindow(a);
  if (sort === "left") return -(primary?.remainingPercent ?? -1);
  return new Date(a.statusResetAt || primary?.resetsAt || "9999-01-01").getTime();
}
export function ProvidersPage() {
  usePrivacyStore((state) => state.blurred);
  const pools = usePools();
  const plan = useAccountPlan();
  const { accountsQuery } = useAccounts();
  const seat = useSeatAccounts();
  const sticky = useStickySessions();
  const [now] = useState(() => Date.now());
  const since = new Date(now);
  since.setHours(0, 0, 0, 0);
  const summary = useReceiptSummary(
    `group_by=account&since=${encodeURIComponent(since.toISOString())}`,
  );
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("reset");
  const activePools = pools.data?.pools.filter((p) => p.kind !== "fable_scoped") || [];
  const accounts = accountsQuery.data || [];
  const seatAccounts = seat.data?.accounts ?? [];
  const paused =
    accounts.filter((a) => a.status === "paused").length +
    seatAccounts.filter((a) => seatStatus(a) === "paused").length;
  const soon = accounts
    .filter(
      (a) =>
        a.windowMinutesPrimary === 300 &&
        a.resetAtPrimary &&
        new Date(a.resetAtPrimary).getTime() > now,
    )
    .sort(
      (a, b) => new Date(a.resetAtPrimary!).getTime() - new Date(b.resetAtPrimary!).getTime(),
    )[0];
  const ready = activePools.filter(
    (p) => !["exhausted", "unavailable"].includes(p.status) && p.eligibleAccounts > 0,
  ).length;
  const bestAccountForPool = (pool: Pool) => {
    if (!pool.fiveHourResetAt) return undefined;
    const match = accounts.find(
      (a) => a.provider === pool.provider && a.resetAtPrimary === pool.fiveHourResetAt,
    );
    return match ? accountName(match) : undefined;
  };
  const filtered = accounts
    .filter(
      (a) =>
        (filter === "all" || a.status === "paused") &&
        `${a.alias} ${a.email} ${a.planType}`.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "name"
        ? accountName(a).localeCompare(accountName(b))
        : sort === "left"
          ? (b.usage?.secondaryRemainingPercent || 0) - (a.usage?.secondaryRemainingPercent || 0)
          : new Date(a.resetAtPrimary || a.resetAtSecondary || "9999-01-01").getTime() -
            new Date(b.resetAtPrimary || b.resetAtSecondary || "9999-01-01").getTime(),
    );
  const groups = [
    {
      title: "Claude",
      mark: "claude" as const,
      list: filtered.filter((a) => a.provider === "anthropic"),
    },
    {
      title: "ChatGPT · Codex",
      mark: "openai" as const,
      list: filtered.filter((a) => a.provider === "openai"),
    },
    {
      title: "Other providers",
      mark: "glm" as const,
      list: filtered.filter((a) => !["anthropic", "openai"].includes(a.provider || "")),
    },
  ];
  const seatFiltered = seatAccounts
    .filter(
      (a) =>
        (filter === "all" || seatStatus(a) === "paused") &&
        `${a.vendor} ${vendorName(a.vendor)} ${a.id} ${a.planLabel ?? ""} ${a.tier ?? ""} ${a.email ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "name"
        ? seatName(a).localeCompare(seatName(b)) || a.id.localeCompare(b.id)
        : seatSortKey(a, sort) - seatSortKey(b, sort),
    );
  const seatVendors = [
    ...SEAT_VENDORS,
    ...new Set(seatAccounts.map((a) => a.vendor).filter((v) => !SEAT_VENDORS.includes(v))),
  ];
  const seatGroups = seatVendors
    .map((vendor) => ({ vendor, list: seatFiltered.filter((a) => a.vendor === vendor) }))
    .filter((group) => group.list.length);
  const summaryById = new Map(summary.data?.groups.map((g) => [g.key, g]));
  return (
    <>
      <PageHead
        title="Providers"
        action={
          <Link className="btn primary" to="/providers/add">
            ＋ Add account
          </Link>
        }
      >
        {pools.data
          ? `${ready} of ${activePools.length} pools can take work. ${soon?.resetAtPrimary ? `Next 5-hour reset in ${durationWords(new Date(soon.resetAtPrimary).getTime() - now)} (${accountName(soon)}).` : "No upcoming 5-hour reset."}`
          : "See which providers can take work."}
      </PageHead>
      <RoutingDashboard pools={pools.data?.pools ?? []} plan={plan.data ?? null} />
      <section>
        <div className="sec-h">
          <h2>Pool details</h2>
          <span className="aside">Groups of accounts that can take work right now</span>
        </div>
        <div className="rows pools">
          <div className="row hd">
            <span>Pool</span>
            <span>Status</span>
            <span>Limit</span>
            <span>5-hour</span>
            <span className="r">Pace</span>
          </div>
          {pools.isPending ? (
            Array.from({ length: 6 }, (_, i) => (
              <div className="row" key={i}>
                <div className="lb-skeleton" />
              </div>
            ))
          ) : pools.isError ? (
            <EmptyState title="Pools could not load" description="Try refreshing this page." />
          ) : activePools.length ? (
            activePools.map((p) => (
              <PoolRow key={p.id} pool={p} bestAccount={bestAccountForPool(p)} />
            ))
          ) : (
            <EmptyState title="No pools yet" />
          )}
        </div>
      </section>
      <Section
        title="Accounts"
        aside={
          <div className="toolbar acct-tools">
            <SearchField value={search} onChange={setSearch} />
            <Seg
              options={[
                {
                  label: "All",
                  value: "all",
                  count: accounts.length + seatAccounts.length,
                },
                { label: "Paused", value: "paused", count: paused },
              ]}
              value={filter}
              onChange={setFilter}
            />
            <FilterSelect
              label="Sort"
              value={sort}
              onChange={setSort}
              options={[
                { label: "Soonest reset", value: "reset" },
                { label: "Most left", value: "left" },
                { label: "Name", value: "name" },
              ]}
            />
          </div>
        }
      >
        <div className="rows accts">
          <div className="row hd">
            <span>Account</span>
            <span>State</span>
            <span>Limit</span>
            <span>5-hour</span>
            <span className="r">Today</span>
            <span />
          </div>
          {accountsQuery.isPending ? (
            Array.from({ length: 8 }, (_, i) => (
              <div className="row" key={i}>
                <div className="lb-skeleton" />
              </div>
            ))
          ) : accountsQuery.isError ? (
            <EmptyState title="Accounts could not load" description="Try refreshing this page." />
          ) : (
            <>
              {groups.map((group) =>
                group.list.length ? (
                  <div key={group.title}>
                    <div className="grp">
                      <ProviderMark id={group.mark} />
                      <span className="gn">{group.title}</span>
                      <span className="gs">· {group.list.length} accounts</span>
                      <span className="ga">
                        <Link className="btn ghost sm" to="/providers/add">
                          ＋ Add
                        </Link>
                      </span>
                    </div>
                    {group.list.map((a) => (
                      <ProxyAccountRow
                        key={a.accountId}
                        a={a}
                        summary={summaryById.get(a.accountId)}
                        sessions={sticky.data?.entries.filter((e) => e.key === a.accountId).length}
                      />
                    ))}
                  </div>
                ) : null,
              )}
              {seatGroups.map((group) => (
                <div key={group.vendor}>
                  <div className="grp">
                    <ProviderMark id={providerMark(group.vendor)} />
                    <span className="gn">{vendorName(group.vendor)}</span>
                    <span className="gs">
                      · {group.list.length} {group.list.length === 1 ? "account" : "accounts"} · CLI
                      seat
                    </span>
                  </div>
                  {group.list.map((a) => (
                    <SeatAccountRow key={a.id} a={a} />
                  ))}
                </div>
              ))}
              {!filtered.length && !seatFiltered.length && (
                <EmptyState
                  title={search || filter !== "all" ? "No matching accounts" : "No accounts yet"}
                />
              )}
            </>
          )}
          <div className="add-more">
            Not connected yet:{" "}
            <Link className="btn sm" to="/providers/add">
              <ProviderMark id="gemini" size={16} /> Gemini
            </Link>
            <Link className="btn sm" to="/providers/add">
              <ProviderMark id="ollama" size={16} /> Ollama
            </Link>
          </div>
        </div>
      </Section>
    </>
  );
}
