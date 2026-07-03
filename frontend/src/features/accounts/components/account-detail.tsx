import { ShieldCheck, User } from "lucide-react";

import { StatusGlyph } from "@/components/ui/status-glyph";
import { Switch } from "@/components/ui/switch";
import { isEmailLabel } from "@/components/blur-email";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { AccountAliasForm } from "@/features/accounts/components/account-alias-form";
import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountProxyBinding } from "@/features/accounts/components/account-proxy-binding";
import { AccountSubscriptionLedgerPanel } from "@/features/accounts/components/account-subscription-ledger";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountTrendSection } from "@/features/accounts/components/account-trend-section";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { OwnerInstanceBadge } from "@/features/accounts/components/owner-instance-badge";
import { providerLabel } from "@/features/accounts/components/provider-label";
import type {
  AccountRoutingPolicy,
  AccountSubscriptionLedger,
  AccountSummary,
} from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import type {
  AccountProxyBindingRequest,
  UpstreamProxyAdmin,
} from "@/features/settings/schemas";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatSlug } from "@/utils/formatters";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onProbe: (accountId: string) => void;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExportAuth: (accountId: string) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
  onSubscriptionSave: (
    accountId: string,
    payload: AccountSubscriptionLedger,
  ) => Promise<unknown>;
  onSubscriptionCheck?: (accountId: string) => Promise<unknown>;
  subscriptionCheckBusy?: boolean;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
  upstreamProxyAdmin?: UpstreamProxyAdmin | null;
  onProxyBindingSave?: (
    accountId: string,
    payload: AccountProxyBindingRequest,
  ) => Promise<unknown>;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  onPause,
  onResume,
  onProbe,
  onSetAlias,
  onDelete,
  onReauth,
  onExportAuth,
  onLimitWarmupChange,
  onRoutingPolicyChange,
  onSubscriptionSave,
  onSubscriptionCheck,
  subscriptionCheckBusy = false,
  onSecurityWorkAuthorizedChange,
  upstreamProxyAdmin = null,
  onProxyBindingSave,
}: AccountDetailProps) {
  const { data: trends } = useAccountTrends(account?.accountId ?? null);
  const blurred = usePrivacyStore((s) => s.blurred);

  if (!account) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-12">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-muted">
          <User className="h-5 w-5 text-muted-foreground" />
        </div>
        <p className="mt-3 text-sm font-medium text-muted-foreground">
          Select an account
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Choose an account from the list to view details.
        </p>
      </div>
    );
  }

  const title = account.displayName || account.email;
  const titleIsEmail = isEmailLabel(title, account.email);
  const compactId = formatCompactAccountId(account.accountId);
  const emailSubtitle =
    account.displayName && account.displayName !== account.email
      ? account.email
      : null;
  const workspaceLabel = account.workspaceLabel || account.workspaceId;
  const contextParts = [
    providerLabel(account.provider),
    formatSlug(account.planType),
    workspaceLabel ?? null,
    account.seatType ? formatSlug(account.seatType) : null,
    showAccountId ? `ID ${compactId}` : null,
  ].filter((part): part is string => part !== null);
  const hasTrends =
    (trends?.primary?.length ?? 0) > 0 ||
    (trends?.secondary?.length ?? 0) > 0 ||
    (trends?.secondaryScheduled?.length ?? 0) > 0;

  return (
    <div
      key={account.accountId}
      className="animate-fade-in-up rounded-xl border bg-card"
    >
      {/* Header: identity + status + actions */}
      <div className="space-y-3 p-5">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <h2 className="min-w-0 text-base font-semibold" title={title}>
            {titleIsEmail && blurred ? (
              <span className="privacy-blur">{title}</span>
            ) : (
              title
            )}
          </h2>
          <div className="flex shrink-0 items-center gap-2">
            <OwnerInstanceBadge
              ownerInstance={account.ownerInstance}
              isLocallyOwned={account.isLocallyOwned}
            />
            <StatusGlyph status={account.status} />
          </div>
        </div>
        {emailSubtitle ? (
          <p className="text-xs text-muted-foreground">
            <span className={blurred ? "privacy-blur" : undefined}>
              {emailSubtitle}
            </span>
          </p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {contextParts.join(" · ")}
        </p>
        <AccountAliasForm
          account={account}
          busy={busy}
          onSetAlias={onSetAlias}
        />
        <AccountActions
          account={account}
          busy={busy}
          onPause={onPause}
          onResume={onResume}
          onProbe={onProbe}
          onDelete={onDelete}
          onReauth={onReauth}
          onExportAuth={onExportAuth}
          onLimitWarmupChange={onLimitWarmupChange}
          onRoutingPolicyChange={onRoutingPolicyChange}
        />
      </div>

      {/* Usage */}
      <div className="border-t p-5">
        <AccountUsagePanel account={account} />
      </div>

      {/* Trend */}
      {hasTrends ? (
        <div className="border-t p-5">
          <AccountTrendSection account={account} trends={trends} />
        </div>
      ) : null}

      {/* Subscription ledger (collapsed by default) */}
      <div className="border-t p-5">
        <AccountSubscriptionLedgerPanel
          account={account}
          busy={busy}
          onSave={onSubscriptionSave}
          onCheckSubscription={onSubscriptionCheck}
          checking={subscriptionCheckBusy}
        />
      </div>

      {/* Connection: tokens, proxy binding, trusted access */}
      <div className="space-y-3 border-t p-5">
        <h3 className="text-sm font-medium">Connection</h3>
        <AccountTokenInfo account={account} />
        {onProxyBindingSave ? (
          <AccountProxyBinding
            account={account}
            admin={upstreamProxyAdmin}
            busy={busy}
            onSave={onProxyBindingSave}
          />
        ) : null}
        <label className="flex items-center justify-between gap-3 text-xs">
          <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate">Trusted Access for Cyber</span>
          </span>
          <Switch
            checked={account.securityWorkAuthorized ?? false}
            disabled={busy}
            onCheckedChange={(checked) =>
              onSecurityWorkAuthorizedChange(account.accountId, checked)
            }
          />
        </label>
      </div>
    </div>
  );
}
