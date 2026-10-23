import { useState } from "react";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { Link } from "react-router-dom";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import {
  usePools,
  useReceiptSummary,
  useSeatAccounts,
  useStickySessions,
  type Pool,
} from "../../api";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { ProviderMark } from "../../kit/provider-mark";
import { providerMark, vendorName } from "../../kit/provider-mark-helpers";
import {
  EmptyState,
  FilterSelect,
  PageHead,
  SearchField,
  Section,
  Seg,
} from "../../kit/primitives";
import { RoutingDashboard } from "@/features/dashboard/components/routing-dashboard";
import { useAccountPlan } from "@/features/dashboard/use-account-plan";
import {
  PoolRow,
  ProxyAccountRow,
  SeatAccountRow,
  SEAT_VENDORS,
  accountName,
  seatStatus,
  seatName,
  seatSortKey,
} from "./provider-rows";

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

export function ProvidersPage() {
  const canWrite = useAuthStore((state) => state.canWrite);
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
  const activePools =
    pools.data?.pools.filter(
      (p) =>
        p.kind !== "fable_scoped" &&
        !(
          p.kind === "cli_seat" &&
          pools.data.pools.some(
            (other) => other.provider === p.provider && other.kind === "cli_seat_budget",
          )
        ),
    ) || [];
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
          canWrite && (
            <Link className="btn primary" to="/providers/add">
              ＋ Add account
            </Link>
          )
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
          <span className="aside">What each group of accounts has left</span>
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
            <Link className="btn sm" to="/accounts">
              List view
            </Link>
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
                      {canWrite && (
                        <span className="ga">
                          <Link className="btn ghost sm" to="/providers/add">
                            ＋ Add
                          </Link>
                        </span>
                      )}
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
            </>
          )}
          <>
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
            {!accountsQuery.isPending && !filtered.length && !seatFiltered.length && (
              <EmptyState
                title={search || filter !== "all" ? "No matching accounts" : "No accounts yet"}
              />
            )}
          </>
          {canWrite && (
            <div className="add-more">
              Not connected yet:{" "}
              <Link className="btn sm" to="/providers/add">
                <ProviderMark id="gemini" size={16} /> Gemini
              </Link>
              <Link className="btn sm" to="/providers/add">
                <ProviderMark id="ollama" size={16} /> Ollama
              </Link>
            </div>
          )}
        </div>
      </Section>
    </>
  );
}
