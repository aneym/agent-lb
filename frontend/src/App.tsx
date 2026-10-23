import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TelemetryConsentDialog } from "@/features/settings/components/telemetry-consent-dialog";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { AppShell } from "@/lb/kit/app-shell";
const AccountPage = lazy(() =>
  import("@/lb/pages/account/account-page").then((module) => ({ default: module.AccountPage })),
);
const AddAccountPage = lazy(() =>
  import("@/lb/pages/add-account/add-account-page").then((module) => ({
    default: module.AddAccountPage,
  })),
);
const KeysPage = lazy(() =>
  import("@/lb/pages/keys/keys-page").then((module) => ({ default: module.KeysPage })),
);
const ProvidersPage = lazy(() =>
  import("@/lb/pages/providers/providers-page").then((module) => ({
    default: module.ProvidersPage,
  })),
);
const DecisionsTab = lazy(() =>
  import("@/lb/pages/routing/decisions-tab").then((module) => ({ default: module.DecisionsTab })),
);
const EvalsTab = lazy(() =>
  import("@/lb/pages/routing/evals-tab").then((module) => ({ default: module.EvalsTab })),
);
const PipelineTab = lazy(() =>
  import("@/lb/pages/routing/pipeline-tab").then((module) => ({ default: module.PipelineTab })),
);
const PolicyTab = lazy(() =>
  import("@/lb/pages/routing/policy-tab").then((module) => ({ default: module.PolicyTab })),
);
const RoutingLayout = lazy(() =>
  import("@/lb/pages/routing/routing-layout").then((module) => ({ default: module.RoutingLayout })),
);
const SettingsPage = lazy(() =>
  import("@/lb/pages/settings/settings-page").then((module) => ({ default: module.SettingsPage })),
);
const TracePage = lazy(() =>
  import("@/lb/pages/trace/trace-page").then((module) => ({ default: module.TracePage })),
);
const UsagePage = lazy(() =>
  import("@/lb/pages/usage/usage-page").then((module) => ({ default: module.UsagePage })),
);

const AutomationsPage = lazy(() =>
  import("@/features/automations/components/automations-page").then((module) => ({
    default: module.AutomationsPage,
  })),
);

const DashboardPage = lazy(() =>
  import("@/features/dashboard/components/dashboard-page").then((module) => ({
    default: module.DashboardPage,
  })),
);

const AccountsPage = lazy(() =>
  import("@/features/accounts/components/accounts-page").then((module) => ({
    default: module.AccountsPage,
  })),
);

const ReportsPage = lazy(() =>
  import("@/features/reports/components/reports-page").then((module) => ({
    default: module.ReportsPage,
  })),
);

const ApisPage = lazy(() =>
  import("@/features/apis/components/apis-page").then((module) => ({ default: module.ApisPage })),
);

const REDIRECTS: Record<string, string> = {
  "/sessions": "/usage",
  "/team": "/keys",
  "/firewall": "/settings/security",
  "/settings": "/settings/general",
};

export default function App() {
  const canWrite = useAuthStore((state) => state.canWrite);
  return (
    <TooltipProvider>
      <Toaster richColors />
      <AuthGate>
        <TelemetryConsentDialog />
        <Suspense fallback={<p>Loading dashboard…</p>}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<ProvidersPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/apis" element={<ApisPage />} />
              <Route
                path="/providers/add"
                element={canWrite ? <AddAccountPage /> : <Navigate to="/" replace />}
              />
              <Route path="/providers/:accountId" element={<AccountPage />} />
              <Route path="/routing" element={<RoutingLayout />}>
                <Route index element={<PipelineTab />} />
                <Route path="decisions" element={<DecisionsTab />} />
                <Route path="policy" element={<PolicyTab />} />
                <Route path="evals" element={<EvalsTab />} />
              </Route>
              <Route path="/usage" element={<UsagePage />} />
              <Route
                path="/automations"
                element={
                  <Suspense fallback={<p>Loading automations…</p>}>
                    <AutomationsPage />
                  </Suspense>
                }
              />
              <Route path="/sessions/:sessionId" element={<TracePage />} />
              <Route path="/keys" element={<KeysPage />} />
              <Route path="/settings/:section" element={<SettingsPage />} />
            </Route>
            {Object.entries(REDIRECTS).map(([from, to]) => (
              <Route key={from} path={from} element={<Navigate to={to} replace />} />
            ))}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </AuthGate>
    </TooltipProvider>
  );
}
