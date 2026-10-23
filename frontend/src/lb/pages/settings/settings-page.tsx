import { Suspense, lazy, useState, type ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { ApiKeyAuthToggle } from "@/features/api-keys/components/api-key-auth-toggle";
import { ApiKeyQuotaPrivacyToggle } from "@/features/api-keys/components/api-key-quota-privacy-toggle";
import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { RequestArchivePanel } from "@/features/conversation-archive/components/request-archive-panel";
import { useConversationArchiveFiles } from "@/features/conversation-archive/hooks/use-conversation-archive";
import { FirewallSection } from "@/features/firewall/components/firewall-section";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { DataRetentionSettings } from "@/features/settings/components/data-retention-settings";
import { GuestAccessSettings } from "@/features/settings/components/guest-access-settings";
import { ResetCreditSettings } from "@/features/settings/components/reset-credit-settings";
import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { TelemetrySettings } from "@/features/settings/components/telemetry-settings";
import { ModelSourcesSettings } from "@/features/model-sources/components/model-sources-settings";
import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { ImportSettings } from "@/features/settings/components/import-settings";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { SessionSettings } from "@/features/settings/components/session-settings";
import { UpstreamProxySettings } from "@/features/settings/components/upstream-proxy-settings";
import { useSettings, useUpstreamProxyAdmin } from "@/features/settings/hooks/use-settings";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { Panel, PageHead } from "@/lb/kit/primitives";
import { getErrorMessageOrNull } from "@/utils/errors";

import "./settings.css";

const TotpSettings = lazy(() =>
  import("@/features/settings/components/totp-settings").then((module) => ({
    default: module.TotpSettings,
  })),
);

const sections = ["general", "security", "network", "data"] as const;
type SettingsSection = (typeof sections)[number];

function SettingsPanel({
  title,
  description,
  children,
  keepLegacyHeading = false,
}: {
  title: string;
  description: string;
  children: ReactNode;
  keepLegacyHeading?: boolean;
}) {
  return (
    <Panel className={`settings-panel${keepLegacyHeading ? " settings-keep-heading" : ""}`}>
      <div className="settings-panel-heading">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {children}
    </Panel>
  );
}

function ArchiveSettings() {
  const files = useConversationArchiveFiles();
  const [requestIdInput, setRequestIdInput] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);

  return (
    <div className="settings-archive">
      <p>
        {files.isPending
          ? "Checking archived request files…"
          : files.isError
            ? "Archived request files could not be loaded."
            : `${files.data.length} archived request ${files.data.length === 1 ? "file" : "files"} available.`}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setRequestId(requestIdInput.trim() || null);
        }}
      >
        <label htmlFor="archive-request-id">Request ID</label>
        <div className="settings-archive-search">
          <input
            id="archive-request-id"
            value={requestIdInput}
            onChange={(event) => setRequestIdInput(event.target.value)}
            placeholder="Enter a request ID"
          />
          <button className="btn" type="submit" disabled={!requestIdInput.trim()}>
            Find request
          </button>
        </div>
      </form>
      {requestId && <RequestArchivePanel requestId={requestId} />}
    </div>
  );
}

export function SettingsPage() {
  const { section } = useParams<{ section: string }>();
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const { accountsQuery } = useAccounts();
  const canWrite = useAuthStore((state) => state.canWrite);
  const {
    upstreamProxyQuery,
    createEndpointMutation,
    createPoolMutation,
    addPoolMemberMutation,
    testEndpointMutation,
  } = useUpstreamProxyAdmin();
  const authMode = useAuthStore((state) => state.authMode);
  const passwordManagementEnabled = useAuthStore((state) => state.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((state) => state.passwordSessionActive);

  if (!sections.includes(section as SettingsSection)) {
    return <Navigate to="/settings/general" replace />;
  }

  const settings = settingsQuery.data;
  const busy =
    !canWrite ||
    testEndpointMutation.isPending ||
    updateSettingsMutation.isPending ||
    createEndpointMutation.isPending ||
    createPoolMutation.isPending ||
    addPoolMemberMutation.isPending;
  const error =
    getErrorMessageOrNull(settingsQuery.error) ||
    (section === "network" ? getErrorMessageOrNull(upstreamProxyQuery.error) : null) ||
    getErrorMessageOrNull(updateSettingsMutation.error) ||
    getErrorMessageOrNull(createEndpointMutation.error) ||
    getErrorMessageOrNull(createPoolMutation.error) ||
    getErrorMessageOrNull(addPoolMemberMutation.error);
  const handleSave = async (payload: SettingsUpdateRequest): Promise<void> => {
    if (!settings || !canWrite) return;
    await updateSettingsMutation.mutateAsync(buildSettingsUpdateRequest(settings, payload));
  };

  return (
    <div className="settings-page">
      <PageHead title="Settings">Manage the dashboard, access, network and data.</PageHead>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {sections.map((item) => (
            <Link
              key={item}
              to={`/settings/${item}`}
              aria-current={section === item ? "page" : undefined}
            >
              {item[0].toUpperCase() + item.slice(1)}
            </Link>
          ))}
        </nav>
        <div className="settings-body">
          {error && <AlertMessage variant="error">{error}</AlertMessage>}
          {section === "general" && (
            <>
              <SettingsPanel
                title="Appearance"
                description="Choose how the dashboard looks and shows time."
              >
                <AppearanceSettings />
              </SettingsPanel>
              {settings && (
                <SettingsPanel
                  title="Reset credits"
                  description="Control credit badges and expiry redemption."
                >
                  <ResetCreditSettings settings={settings} busy={busy} onSave={handleSave} />
                </SettingsPanel>
              )}
              <Panel className="settings-moved">
                Routing strategy, sticky sessions and warm-ups moved to{" "}
                <Link className="link" to="/routing">
                  Routing → Pipeline
                </Link>
                .
              </Panel>
            </>
          )}
          {section === "security" && (
            <>
              {authMode === "disabled" && (
                <p className="settings-notice">
                  Dashboard authentication is disabled by configuration. Restrict access at the
                  network level.
                </p>
              )}
              {authMode === "trusted_header" && (
                <p className="settings-notice">
                  The dashboard uses a trusted proxy header. Password and TOTP are optional fallback
                  login.
                </p>
              )}
              {canWrite && (
                <SettingsPanel
                  title="Password"
                  description="Manage password access to the dashboard."
                  keepLegacyHeading
                >
                  <PasswordSettings disabled={busy} />
                </SettingsPanel>
              )}
              {canWrite && settings && (
                <SettingsPanel title="Guest access" description="Grant read-only dashboard access.">
                  <GuestAccessSettings
                    settings={settings}
                    busy={busy}
                    onSave={handleSave}
                    onRefresh={() => settingsQuery.refetch()}
                  />
                </SettingsPanel>
              )}
              {canWrite && passwordManagementEnabled && passwordSessionActive && settings && (
                <SettingsPanel
                  title="Two-factor authentication"
                  description="Protect password login with a one-time code."
                >
                  <Suspense fallback={<p>Loading two-factor settings…</p>}>
                    <TotpSettings settings={settings} disabled={busy} onSave={handleSave} />
                  </Suspense>
                </SettingsPanel>
              )}
              {canWrite && passwordManagementEnabled && settings && (
                <SettingsPanel
                  title="Dashboard sessions"
                  description="Choose how long new password sessions stay signed in."
                >
                  <SessionSettings settings={settings} busy={busy} onSave={handleSave} />
                </SettingsPanel>
              )}
              {settings && (
                <SettingsPanel
                  title="API key access"
                  description="Control key authentication and upstream quota visibility."
                >
                  <ApiKeyAuthToggle
                    enabled={settings.apiKeyAuthEnabled}
                    disabled={busy}
                    onChange={(enabled) => void handleSave({ apiKeyAuthEnabled: enabled })}
                  />
                  <ApiKeyQuotaPrivacyToggle
                    enabled={settings.hideUpstreamQuotaFromApiKeys}
                    disabled={busy}
                    onChange={(enabled) =>
                      void handleSave({ hideUpstreamQuotaFromApiKeys: enabled })
                    }
                  />
                </SettingsPanel>
              )}
              <SettingsPanel
                title="Allowed IPs"
                description="Limit which client addresses can reach the proxy."
              >
                <FirewallSection disabled={busy} />
              </SettingsPanel>
            </>
          )}
          {section === "network" && (
            <>
              <SettingsPanel
                title="Upstream proxy"
                description="Configure proxy endpoints and pools for outbound traffic."
              >
                {upstreamProxyQuery.data ? (
                  <UpstreamProxySettings
                    admin={upstreamProxyQuery.data}
                    busy={busy}
                    onSaveSettings={handleSave}
                    onCreateEndpoint={(payload) => createEndpointMutation.mutateAsync(payload)}
                    onTestEndpoint={(endpointId) => testEndpointMutation.mutateAsync(endpointId)}
                    onCreatePool={(payload) => createPoolMutation.mutateAsync(payload)}
                    onAddPoolMember={(poolId, payload) =>
                      addPoolMemberMutation.mutateAsync({ poolId, payload })
                    }
                  />
                ) : !upstreamProxyQuery.isError ? (
                  <p>Loading upstream proxy settings…</p>
                ) : null}
              </SettingsPanel>
              <SettingsPanel
                title="Model sources"
                description="Connect model endpoints and assign them to keys."
              >
                <ModelSourcesSettings disabled={busy} />
              </SettingsPanel>
              {settings && (
                <SettingsPanel
                  title="Transport and routing controls"
                  description="Configure transport, capacity sharing and quota warm-ups."
                >
                  <RoutingSettings
                    settings={settings}
                    accounts={accountsQuery.data ?? []}
                    accountsLoading={accountsQuery.isLoading}
                    busy={busy}
                    onSave={handleSave}
                  />
                </SettingsPanel>
              )}
            </>
          )}
          {section === "data" && (
            <>
              <SettingsPanel
                title="Import"
                description="Control how imported accounts are handled."
              >
                {settings ? (
                  <ImportSettings settings={settings} busy={busy} onSave={handleSave} />
                ) : !settingsQuery.isError ? (
                  <p>Loading import settings…</p>
                ) : null}
              </SettingsPanel>
              <SettingsPanel
                title="Export"
                description="Export an account’s credentials from its account page."
              >
                <p className="settings-copy">
                  Choose an account on{" "}
                  <Link className="link" to="/">
                    Providers
                  </Link>{" "}
                  to export it.
                </p>
              </SettingsPanel>
              {settings && (
                <SettingsPanel
                  title="Data retention"
                  description="Set how long request logs and usage history are retained."
                >
                  <DataRetentionSettings
                    key={`${settings.requestLogRetentionOverrideDays}:${settings.usageHistoryRetentionOverrideDays}`}
                    settings={settings}
                    busy={busy}
                    onSave={handleSave}
                  />
                </SettingsPanel>
              )}
              <SettingsPanel
                title="Telemetry"
                description="Review optional usage telemetry and consent."
              >
                <TelemetrySettings disabled={busy} />
              </SettingsPanel>
              <SettingsPanel
                title="Request archive"
                description="Find archived records for a request ID."
              >
                <ArchiveSettings />
              </SettingsPanel>
            </>
          )}
          <LoadingOverlay visible={canWrite && busy} label="Saving settings..." />
        </div>
      </div>
    </div>
  );
}
