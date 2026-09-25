import { Navigate, Route, Routes } from "react-router-dom";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { AppShell } from "@/lb/kit/app-shell";
import { AccountPage } from "@/lb/pages/account/account-page";
import { AddAccountPage } from "@/lb/pages/add-account/add-account-page";
import { KeysPage } from "@/lb/pages/keys/keys-page";
import { ProvidersPage } from "@/lb/pages/providers/providers-page";
import { DecisionsTab } from "@/lb/pages/routing/decisions-tab";
import { EvalsTab } from "@/lb/pages/routing/evals-tab";
import { PipelineTab } from "@/lb/pages/routing/pipeline-tab";
import { PolicyTab } from "@/lb/pages/routing/policy-tab";
import { RoutingLayout } from "@/lb/pages/routing/routing-layout";
import { SettingsPage } from "@/lb/pages/settings/settings-page";
import { UsagePage } from "@/lb/pages/usage/usage-page";

// Paths from the previous dashboard, kept so bookmarks and the menubar app still land somewhere useful.
const REDIRECTS: Record<string, string> = {
  "/dashboard": "/",
  "/accounts": "/",
  "/reports": "/usage",
  "/sessions": "/usage",
  "/apis": "/keys",
  "/team": "/keys",
  "/firewall": "/settings/security",
  "/settings": "/settings/general",
};

export default function App() {
  return (
    <TooltipProvider>
      <Toaster richColors />
      <AuthGate>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ProvidersPage />} />
            <Route path="/providers/add" element={<AddAccountPage />} />
            <Route path="/providers/:accountId" element={<AccountPage />} />
            <Route path="/routing" element={<RoutingLayout />}>
              <Route index element={<PipelineTab />} />
              <Route path="decisions" element={<DecisionsTab />} />
              <Route path="policy" element={<PolicyTab />} />
              <Route path="evals" element={<EvalsTab />} />
            </Route>
            <Route path="/usage" element={<UsagePage />} />
            <Route path="/keys" element={<KeysPage />} />
            <Route path="/settings/:section" element={<SettingsPage />} />
          </Route>
          {Object.entries(REDIRECTS).map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
