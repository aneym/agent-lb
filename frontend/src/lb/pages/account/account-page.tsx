import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";
import { get } from "@/lib/api-client";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { OauthDialog } from "@/features/accounts/components/oauth-dialog";
import { AuthExportDialog } from "@/features/accounts/components/auth-export-dialog";
import type { AccountSummary } from "@/features/accounts/schemas";
import { useStickySessions } from "../../api";
import { clock, compact, money, relative } from "../../format";
import { PausePopover } from "../../kit/pause-popover";
import { ProviderMark } from "../../kit/provider-mark";
import { providerMark } from "../../kit/provider-mark-helpers";
import {
  AccountState,
  Crumbs,
  EmptyState,
  LimitMeter,
  ModelChip,
  RowMenu,
} from "../../kit/primitives";
import "./account.css";

const Receipt = z.object({
  id: z.union([z.string(), z.number()]),
  requestedAt: z.string(),
  sessionId: z.string().nullable(),
  apiKeyName: z.string().nullable().optional(),
  model: z.string().nullable(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cacheReadRatio: z.number().nullable().optional(),
  latencyMs: z.number().nullable().optional(),
  httpStatus: z.number().nullable().optional(),
  status: z.string(),
});
const Receipts = z.object({ receipts: z.array(Receipt) });
const Summary = z.object({
  totals: z.object({
    requests: z.number(),
    inputTokens: z.number(),
    cacheReadRatio: z.number().nullable(),
    costUsd: z.number(),
    errorRate: z.number().nullable(),
    errors: z.number(),
  }),
  series: z.array(z.object({ start: z.string(), requests: z.number() })),
});
type ReceiptItem = z.infer<typeof Receipt>;
const nameOf = (a: AccountSummary) => a.alias || `${a.planType} · ${a.email.split("@")[0]}`;
const groupOf = (a: AccountSummary) =>
  a.provider === "anthropic"
    ? "Claude"
    : a.provider === "openai"
      ? "ChatGPT · Codex"
      : a.provider?.toUpperCase() || "Other";
const usageLink = (id: string) => `/usage?account=${encodeURIComponent(id)}`;

function Limits({ account }: { account: AccountSummary }) {
  const [now] = useState(() => Date.now());
  const reset = account.resetAtSecondary;
  const expected = reset
    ? Math.max(
        0,
        Math.min(
          100,
          ((new Date(reset).getTime() - now) /
            ((account.windowMinutesSecondary || 10080) * 60000)) *
            100,
        ),
      )
    : undefined;
  const quotas = account.additionalQuotas || [];
  const maxUsed = Math.ceil(
    Math.max(
      0,
      ...quotas.flatMap((q) => [
        q.primaryWindow?.usedPercent || 0,
        q.secondaryWindow?.usedPercent || 0,
      ]),
    ),
  );
  return (
    <section className="card">
      <h2>Limits</h2>
      <div className="lrows">
        {account.windowMinutesSecondary && (
          <div className="lrow">
            <span className="k">
              {account.windowMinutesSecondary === 10080
                ? "Week"
                : account.windowMinutesSecondary === 43200
                  ? "Month"
                  : "Long window"}
            </span>
            <div>
              <LimitMeter
                remaining={account.usage?.secondaryRemainingPercent}
                resetAt={reset}
                paceTick={expected}
              />
              {expected != null && account.usage?.secondaryRemainingPercent != null && (
                <span className="lnote">
                  {Math.abs(Math.round(account.usage.secondaryRemainingPercent - expected))}%{" "}
                  {account.usage.secondaryRemainingPercent < expected
                    ? "short of pace"
                    : "to spare"}
                </span>
              )}
            </div>
          </div>
        )}
        {account.provider !== "openai" && account.windowMinutesPrimary === 300 && (
          <div className="lrow">
            <span className="k">5-hour</span>
            <LimitMeter
              remaining={account.usage?.primaryRemainingPercent}
              resetAt={account.resetAtPrimary}
              label="5h"
            />
          </div>
        )}
        {account.provider === "openai" && (
          <p className="lnote">Weekly limit only. ChatGPT has no 5-hour window.</p>
        )}
        {quotas.length > 0 && (
          <details className="more">
            <summary>
              {quotas.length} model limits, all under {Math.max(1, maxUsed)}% used
            </summary>
            <div className="lrows account-extra">
              {quotas.map((q) => (
                <div className="lrow" key={q.quotaKey || q.limitName}>
                  <span className="k">{q.displayLabel || q.limitName}</span>
                  <LimitMeter
                    remaining={
                      q.secondaryWindow
                        ? 100 - q.secondaryWindow.usedPercent
                        : q.primaryWindow
                          ? 100 - q.primaryWindow.usedPercent
                          : null
                    }
                    resetAt={
                      q.secondaryWindow?.resetAt
                        ? new Date(q.secondaryWindow.resetAt * 1000).toISOString()
                        : null
                    }
                  />
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}

function LastSevenDays({ accountId }: { accountId: string }) {
  const [since] = useState(() => new Date(Date.now() - 7 * 86400000).toISOString());
  const query = useQuery({
    queryKey: ["lb", "account-summary", accountId, since],
    queryFn: () =>
      get(
        `/api/receipts/summary?account_id=${encodeURIComponent(accountId)}&since=${encodeURIComponent(since)}&bucket=day`,
        Summary,
      ),
  });
  const totals = query.data?.totals;
  const series = query.data?.series.slice(-7) || [];
  const max = Math.max(1, ...series.map((day) => day.requests));
  return (
    <section className="card">
      <div className="account-section-head">
        <h2>Last 7 days</h2>
        <Link to={usageLink(accountId)}>Open in Usage ↗</Link>
      </div>
      {query.isError ? (
        <p className="lnote">Usage could not load.</p>
      ) : (
        <>
          <div className="account-stats">
            <span>
              <b>{totals?.requests.toLocaleString() ?? "—"}</b> requests
            </span>
            <span>
              <b>{totals ? compact(totals.inputTokens) : "—"}</b> input tokens
            </span>
            <span>
              <b>
                {totals?.cacheReadRatio != null
                  ? `${Math.round(totals.cacheReadRatio * 100)}%`
                  : "—"}
              </b>{" "}
              cache read
            </span>
            <span>
              <b>{totals ? money(totals.costUsd) : "—"}</b> API equivalent
            </span>
            <span>
              <b>{totals?.errorRate != null ? `${(totals.errorRate * 100).toFixed(1)}%` : "—"}</b>{" "}
              errors
            </span>
          </div>
          <div
            className="account-chart"
            role="img"
            aria-label="Requests per day for the last seven days"
          >
            {series.map((day) => (
              <div
                className="account-chart-day"
                key={day.start}
                title={`${day.requests.toLocaleString()} requests`}
              >
                <div className="account-chart-track">
                  <i style={{ height: `${(day.requests / max) * 100}%` }} />
                </div>
                <span>
                  {new Intl.DateTimeFormat("en", { weekday: "short" }).format(new Date(day.start))}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
function ReceiptRow({ receipt }: { receipt: ReceiptItem }) {
  return (
    <div className="row">
      <span className="c-t mono">{clock(receipt.requestedAt)}</span>
      <span className="c-s mono">
        {receipt.sessionId ? `${receipt.sessionId.slice(0, 10)} · ` : ""}
        {receipt.apiKeyName || "—"}
      </span>
      <span className="c-m">{receipt.model ? <ModelChip model={receipt.model} /> : "—"}</span>
      <span className="c-tok mono">
        {compact(receipt.inputTokens)} / {compact(receipt.outputTokens)}
      </span>
      <span className="c-cache mono">
        {receipt.cacheReadRatio != null ? `${Math.round(receipt.cacheReadRatio * 100)}%` : "—"}
      </span>
      <span className="c-lat mono">
        {receipt.latencyMs != null ? `${(receipt.latencyMs / 1000).toFixed(1)}s` : "—"}
      </span>
      <span className="c-st mono">
        {receipt.httpStatus && receipt.httpStatus >= 400 ? "△" : "●"}{" "}
        {receipt.httpStatus || receipt.status}
      </span>
    </div>
  );
}
function RecentRequests({ accountId }: { accountId: string }) {
  const query = useQuery({
    queryKey: ["lb", "account-receipts", accountId],
    queryFn: () =>
      get(`/api/receipts?account_id=${encodeURIComponent(accountId)}&limit=8`, Receipts),
  });
  return (
    <section className="card">
      <div className="account-section-head">
        <h2>Recent requests</h2>
        <Link to={usageLink(accountId)}>All receipts for this account ↗</Link>
      </div>
      <div className="rows rcpt">
        <div className="row hd">
          <span>Time</span>
          <span>Session · key name</span>
          <span>Model</span>
          <span>In / out</span>
          <span>Cache</span>
          <span>Latency</span>
          <span>Status</span>
        </div>
        {query.data?.receipts.map((r) => (
          <ReceiptRow key={r.id} receipt={r} />
        ))}
        {!query.isPending && !query.data?.receipts.length && (
          <p className="account-no-requests">
            {query.isError ? "Requests could not load." : "No recent requests."}
          </p>
        )}
      </div>
    </section>
  );
}

function Routing({
  account,
  mutations,
}: {
  account: AccountSummary;
  mutations: ReturnType<typeof useAccounts>;
}) {
  const sessions = useStickySessions().data;
  const stickyCount = sessions?.entries.filter((entry) => entry.key === account.accountId).length;
  return (
    <section className="card account-routing">
      <h2>Routing</h2>
      <div className="account-setting">
        <span>Policy</span>
        <div className="seg">
          {(
            [
              { name: "Prefer", value: "burn_first" },
              { name: "Normal", value: "normal" },
              { name: "Avoid", value: "preserve" },
            ] as const
          ).map(({ name, value }) => (
            <button
              key={value}
              aria-pressed={(account.routingPolicy || "normal") === value}
              disabled={mutations.routingPolicyMutation.isPending}
              onClick={() =>
                mutations.routingPolicyMutation.mutate({
                  accountId: account.accountId,
                  routingPolicy: value,
                })
              }
            >
              {name}
            </button>
          ))}
        </div>
      </div>
      <div className="account-setting">
        <label htmlFor="account-warmup">Warm-up</label>
        <span>
          <input
            type="checkbox"
            id="account-warmup"
            checked={account.limitWarmupEnabled}
            disabled={mutations.limitWarmupMutation.isPending}
            onChange={(event) =>
              mutations.limitWarmupMutation.mutate({
                accountId: account.accountId,
                enabled: event.target.checked,
              })
            }
          />{" "}
          10 min before reset
        </span>
      </div>
      {stickyCount ? (
        <div className="account-setting">
          <span>Sticky sessions</span>
          <span>{stickyCount} pinned</span>
        </div>
      ) : null}
    </section>
  );
}

export function AccountPage() {
  const { accountId = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const mutations = useAccounts();
  const account = mutations.accountsQuery.data?.find((item) => item.accountId === accountId);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [alias, setAlias] = useState("");
  const [confirm, setConfirm] = useState("");
  const [oauthOpen, setOauthOpen] = useState(() => params.get("signin") === "1");
  const [exportOpen, setExportOpen] = useState(false);
  const [exportData, setExportData] = useState<Awaited<
    ReturnType<typeof mutations.exportAuthMutation.mutateAsync>
  > | null>(null);
  const oauth = useOauth();
  const refresh = useQuery({
    queryKey: ["lb", "account-refresh", accountId],
    queryFn: () =>
      get(
        "/api/accounts",
        z.object({
          accounts: z.array(
            z.object({ accountId: z.string(), lastRefreshAt: z.string().nullable().optional() }),
          ),
        }),
      ),
  });
  const lastRefreshAt = refresh.data?.accounts.find(
    (entry) => entry.accountId === accountId,
  )?.lastRefreshAt;
  if (mutations.accountsQuery.isPending) return <EmptyState title="Loading account…" />;
  if (!account)
    return (
      <EmptyState
        title="Account not found"
        description="Return to Providers to choose an account."
      />
    );
  const name = nameOf(account);
  const isOAuth = account.provider === "anthropic" || account.provider === "openai";
  const signIn = () =>
    isOAuth ? setOauthOpen(true) : navigate(`/providers/add?provider=${account.provider || "glm"}`);
  const exportSignIn = async () => {
    try {
      setExportData(await mutations.exportAuthMutation.mutateAsync(accountId));
      setExportOpen(true);
    } catch {
      /* Mutation reports the error. */
    }
  };
  const saveAlias = async () => {
    try {
      await mutations.setAliasMutation.mutateAsync({ accountId, alias: alias.trim() || null });
      setRenameOpen(false);
    } catch {
      /* Mutation reports the error. */
    }
  };
  const remove = async () => {
    try {
      await mutations.deleteMutation.mutateAsync({ accountId, deleteHistory: false });
      navigate("/");
    } catch {
      /* Mutation reports the error. */
    }
  };
  return (
    <div className="account-page">
      <Crumbs
        items={[
          { label: "Providers", to: "/" },
          { label: groupOf(account), to: "/" },
          { label: name },
        ]}
      />
      <div className="acct-head">
        <ProviderMark
          id={account.provider === "anthropic" ? "claude" : providerMark(account.provider)}
          size={28}
        />
        <div className="id">
          <h1>{name}</h1>
          <span className="meta">
            {account.planType} · <span className="privacy-blur">{account.email}</span>
          </span>
          <AccountState
            status={account.status}
            resetAt={account.rateLimitResetAt || account.resetAtPrimary}
            sub={account.status === "rate_limited" ? "· 5-hour window used up" : undefined}
          />
        </div>
        <div className="actions">
          <div className="lb-pop-wrap">
            {account.status === "paused" ? (
              <button className="btn" onClick={() => mutations.resumeMutation.mutate(accountId)}>
                Resume
              </button>
            ) : (
              <button className="btn" onClick={() => setPauseOpen(!pauseOpen)}>
                Pause ⌄
              </button>
            )}
            {pauseOpen && (
              <PausePopover
                accountId={accountId}
                pool={groupOf(account)}
                fiveHourResetAt={
                  account.windowMinutesPrimary === 300 ? account.resetAtPrimary : null
                }
                weeklyResetAt={account.resetAtSecondary}
                onClose={() => setPauseOpen(false)}
              />
            )}
          </div>
          <button
            className="btn"
            disabled={mutations.probeMutation.isPending}
            onClick={() => mutations.probeMutation.mutate({ accountId })}
          >
            Check limits now
          </button>
          <RowMenu
            label="Account actions"
            items={[
              {
                label: "Rename",
                onSelect: () => {
                  setAlias(account.alias || "");
                  setRenameOpen(true);
                },
              },
              {
                label: "Export sign-in file",
                hidden: !isOAuth || account.provider !== "openai",
                onSelect: () => void exportSignIn(),
              },
              { label: "Sign in again", onSelect: signIn },
            ]}
          />
        </div>
      </div>
      <div className="layout">
        <div>
          <Limits account={account} />
          <LastSevenDays accountId={accountId} />
          <RecentRequests accountId={accountId} />
        </div>
        <aside>
          <Routing account={account} mutations={mutations} />
          <section className="card account-signin">
            <h2>Sign-in</h2>
            <div className="account-setting">
              <span>Method</span>
              <span>{isOAuth ? `${groupOf(account)} OAuth` : "API key"}</span>
            </div>
            {lastRefreshAt && (
              <div className="account-setting">
                <span>Token refreshed</span>
                <span>{relative(lastRefreshAt)}</span>
              </div>
            )}
            <div className="toolbar">
              <button
                className={`btn sm ${account.status === "reauth_required" ? "primary" : ""}`}
                onClick={signIn}
              >
                Sign in again
              </button>
              {account.provider === "openai" && (
                <button className="btn sm" onClick={() => void exportSignIn()}>
                  Export sign-in file
                </button>
              )}
            </div>
          </section>
          <section className="card danger">
            <h2>Remove account</h2>
            <p>
              Stops routing to {name} and deletes its stored sign-in. Receipts stay. Type the
              account name to confirm.
            </p>
            <input
              className="account-confirm"
              aria-label="Type account name to remove"
              placeholder={name}
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
            <button
              className="btn sm"
              disabled={confirm !== name || mutations.deleteMutation.isPending}
              onClick={() => void remove()}
            >
              Remove account
            </button>
          </section>
        </aside>
      </div>
      {renameOpen && (
        <div className="account-overlay" role="presentation">
          <div
            className="card account-rename"
            role="dialog"
            aria-modal="true"
            aria-label="Rename account"
          >
            <h2>Rename account</h2>
            <input
              autoFocus
              aria-label="Account name"
              value={alias}
              onChange={(event) => setAlias(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void saveAlias();
              }}
            />
            <div className="toolbar">
              <button className="btn" onClick={() => setRenameOpen(false)}>
                Cancel
              </button>
              <button
                className="btn primary"
                disabled={mutations.setAliasMutation.isPending}
                onClick={() => void saveAlias()}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
      {isOAuth && (
        <OauthDialog
          key={account.provider}
          open={oauthOpen}
          state={oauth.state}
          initialProvider={account.provider === "anthropic" ? "anthropic" : "openai"}
          onOpenChange={(open) => {
            setOauthOpen(open);
            if (!open && params.has("signin")) {
              const next = new URLSearchParams(params);
              next.delete("signin");
              setParams(next, { replace: true });
            }
          }}
          onStart={async (method, provider) => {
            await oauth.start(method, provider);
          }}
          onComplete={async () => {
            await oauth.complete();
            await mutations.accountsQuery.refetch();
            toast.success("Sign-in updated");
          }}
          onManualCallback={async (callback) => {
            await oauth.manualCallback(callback);
          }}
          onReset={oauth.reset}
        />
      )}
      <AuthExportDialog open={exportOpen} exportData={exportData} onOpenChange={setExportOpen} />
    </div>
  );
}
