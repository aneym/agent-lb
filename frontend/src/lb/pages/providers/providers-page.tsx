import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import type { AccountSummarySchema } from "@/features/accounts/schemas";
import {
  usePools,
  useReceiptSummary,
  useSeatAccounts,
  useStickySessions,
  type Pool,
} from "../../api";
import { duration, money } from "../../format";
import { ProviderMark } from "../../kit/provider-mark";
import { providerMark } from "../../kit/provider-mark-helpers";
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
import { toast } from "sonner";
import type { z } from "zod";

type Account = z.infer<typeof AccountSummarySchema>;
const poolName: Record<string, string> = {
  "anthropic-general": "Anthropic general",
  "openai-codex": "ChatGPT · Codex",
  glm: "GLM",
  kimi: "Kimi",
  openrouter: "OpenRouter",
  cursor: "Cursor",
  devin: "Devin",
};
const poolModels: Record<string, string> = {
  "anthropic-general": "opus-latest · sonnet-latest",
  "openai-codex": "sol-latest · luna-latest",
  cursor: "Cursor seat",
  devin: "Devin seat",
};
const accountName = (a: Account) => a.alias || `${a.planType} · ${a.email.split("@")[0]}`;
function PoolRow({ pool }: { pool: Pool }) {
  const has5h = pool.fiveHourRemainingPercent != null;
  const long = pool.windowLabel
    ? (pool.weeklyRemainingPercent ?? pool.aggregateRemainingPercent)
    : null;
  const reset = pool.weeklyResetAt || pool.resetAt;
  const longLabel = pool.windowLabel === "month" ? "mo" : "wk";
  return (
    <div className="row link">
      <div className="c-who who">
        <ProviderMark id={pool.provider === "anthropic" ? "claude" : providerMark(pool.provider)} />
        <div className="t">
          <span className="n">{poolName[pool.id] || pool.id}</span>
          <span className="s">
            {poolModels[pool.id] || (pool.kind === "cli_seat" ? "CLI seat" : pool.provider)}
          </span>
        </div>
      </div>
      <div className="c-st">
        <PoolState status={pool.status} ready={pool.eligibleAccounts} total={pool.accounts} />
      </div>
      <div className={`c-h5 ${has5h ? "" : "none"}`}>
        <LimitMeter
          remaining={pool.fiveHourRemainingPercent}
          resetAt={pool.fiveHourResetAt}
          empty="No 5-hour window"
          label="5h"
        />
      </div>
      <div className="c-wk">
        <LimitMeter
          remaining={long}
          resetAt={reset}
          paceTick={
            pool.weeklyPacePercent != null && long != null
              ? long + pool.weeklyPacePercent
              : undefined
          }
          empty={pool.windowLabel === "month" ? "No monthly cap" : "No weekly cap"}
          label={longLabel}
        />
      </div>
      <div className={`c-pace ${pool.weeklyPacePercent == null ? "none" : ""}`}>
        <Pace value={pool.weeklyPacePercent} />
      </div>
    </div>
  );
}
function AccountRow({
  a,
  summary,
  sessions,
}: {
  a: Account;
  summary?: { requests: number; costUsd: number };
  sessions?: number;
}) {
  const navigate = useNavigate();
  const [pauseOpen, setPauseOpen] = useState(false);
  const { resumeMutation, probeMutation } = useAccounts();
  const isOpenai = a.provider === "openai";
  const has5h = !isOpenai && a.windowMinutesPrimary === 300;
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
  function open(e: MouseEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest("button,a,.pop")) return;
    navigate(rowLink);
  }
  return (
    <div
      className={`row link ${["paused", "reauth_required", "deactivated"].includes(a.status) ? "dim" : ""}`}
      role="link"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) navigate(rowLink);
      }}
    >
      <div className="c-who who">
        <ProviderMark id={a.provider === "anthropic" ? "claude" : providerMark(a.provider)} />
        <div className="t">
          <span className="n">{accountName(a)}</span>
          <span className="s privacy-blur">{a.email}</span>
        </div>
      </div>
      <div className="c-st">
        <AccountState
          status={a.status}
          resetAt={a.rateLimitResetAt || a.resetAtSecondary}
          sub={a.status === "active" && sessions ? `holding ${sessions} sessions` : undefined}
        />
      </div>
      <div className="c-h5">
        <LimitMeter
          remaining={has5h ? a.usage?.primaryRemainingPercent : null}
          resetAt={has5h ? a.resetAtPrimary : null}
          empty={has5h ? "No reading" : "No 5-hour window"}
          label="5h"
        />
      </div>
      <div className="c-wk">
        <LimitMeter
          remaining={secondary}
          resetAt={reset}
          paceTick={expected}
          empty="No weekly cap"
          label="wk"
        />
      </div>
      <div className="c-today today">
        <span className="num">{summary?.requests.toLocaleString() || "—"}</span>
        <span className="s">
          {summary ? `${money(summary.costUsd)} API value` : "No usage today"}
        </span>
      </div>
      <div className="c-more lb-pop-wrap">
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
      </div>
    </div>
  );
}
export function ProvidersPage() {
  const pools = usePools();
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
  const paused = accounts.filter((a) => a.status === "paused").length;
  const disconnected = accounts.filter((a) => a.status === "reauth_required").length;
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
  const filtered = accounts
    .filter(
      (a) =>
        (filter === "all" ||
          (filter === "paused" ? a.status === "paused" : a.status === "reauth_required")) &&
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
          ? `${ready} of ${activePools.length} pools can take work. ${soon?.resetAtPrimary ? `Next 5-hour reset in ${duration(new Date(soon.resetAtPrimary).getTime() - now)} (${accountName(soon)}).` : "No upcoming 5-hour reset."}`
          : "See which providers can take work."}
      </PageHead>
      <section>
        <div className="sec-h">
          <h2>Pools</h2>
          <span className="aside">Groups of accounts that can take work right now</span>
        </div>
        <div className="rows pools">
          <div className="row hd">
            <span>Pool</span>
            <span>Status · Ready</span>
            <span>5-hour</span>
            <span>Week or month</span>
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
            activePools.map((p) => <PoolRow key={p.id} pool={p} />)
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
                  count: accounts.length + (seat.data?.accounts.length || 0),
                },
                { label: "Paused", value: "paused", count: paused },
                { label: "Disconnected", value: "disconnected", count: disconnected },
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
            <span>5-hour left</span>
            <span>Week left</span>
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
                      <AccountRow
                        key={a.accountId}
                        a={a}
                        summary={summaryById.get(a.accountId)}
                        sessions={sticky.data?.entries.filter((e) => e.key === a.accountId).length}
                      />
                    ))}
                  </div>
                ) : null,
              )}
              {seat.data?.accounts.filter(
                (a) =>
                  filter === "all" &&
                  `${a.vendor} ${a.id}`.toLowerCase().includes(search.toLowerCase()),
              ).length ? (
                <div>
                  <div className="grp">
                    <span className="gn">CLI seats</span>
                    <span className="gs">· {seat.data.accounts.length} accounts</span>
                  </div>
                  {seat.data.accounts
                    .filter(
                      (a) =>
                        filter === "all" &&
                        `${a.vendor} ${a.id}`.toLowerCase().includes(search.toLowerCase()),
                    )
                    .map((a) => (
                      <div
                        className="row link"
                        key={a.id}
                        onClick={() => toast("Seat details coming in the next slice")}
                      >
                        <div className="c-who who">
                          <ProviderMark id={providerMark(a.vendor)} />
                          <div className="t">
                            <span className="n">
                              {a.vendor === "devin" ? "Devin" : "Cursor"} · {a.tier || "seat"}
                            </span>
                            <span className="s">{a.id}</span>
                          </div>
                        </div>
                        <div className="c-st">
                          <AccountState
                            status={
                              !a.enabled
                                ? "deactivated"
                                : a.ready
                                  ? "active"
                                  : a.authOk
                                    ? "rate_limited"
                                    : "reauth_required"
                            }
                          />
                        </div>
                        <div className="c-h5">
                          <LimitMeter empty="No 5-hour window" />
                        </div>
                        <div className="c-wk">
                          <LimitMeter empty="No monthly cap" />
                        </div>
                        <div className="c-today today">—</div>
                      </div>
                    ))}
                </div>
              ) : null}
              {!filtered.length && !seat.data?.accounts.length && (
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
