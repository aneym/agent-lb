import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { ApisPage } from "@/features/apis/components/apis-page";
import { DashboardPage } from "@/features/dashboard/components/dashboard-page";
import { ReportsPage } from "@/features/reports/components/reports-page";
import { SessionsPage } from "@/features/sessions/components/sessions-page";
import { SettingsPage } from "@/features/settings/components/settings-page";
import { TeamPage } from "@/features/team/components/team-page";
import { useTimeFormatStore } from "@/hooks/use-time-format";
import { AppShell } from "@/lb/kit/app-shell";
import { EmptyState } from "@/lb/kit/primitives";
import { ProvidersPage } from "@/lb/pages/providers/providers-page";

function LegacyLayout() {
  const logout = useAuthStore((state) => state.logout);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const timeFormat = useTimeFormatStore((state) => state.timeFormat);

  return (
    <div className="flex min-h-screen flex-col bg-background pb-10" data-time-format={timeFormat}>
      <AppHeader onLogout={() => { void logout(); }} showLogout={passwordRequired} />
      <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-8 sm:px-6"><Outlet /></main>
      <StatusBar />
    </div>
  );
}

export default function App() {
  return <TooltipProvider><Toaster richColors /><AuthGate><Routes>
    <Route element={<AppShell />}>
      <Route path="/" element={<ProvidersPage />} />
      <Route path="/providers/add" element={<EmptyState title="Coming in the next slice" />} />
      <Route path="/providers/:accountId" element={<EmptyState title="Coming in the next slice" />} />
      <Route path="/routing" element={<SettingsPage />} />
      <Route path="/usage" element={<ReportsPage />} />
      <Route path="/keys" element={<ApisPage />} />
    </Route>
    <Route element={<LegacyLayout />}>
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/reports" element={<ReportsPage />} />
      <Route path="/sessions" element={<SessionsPage />} />
      <Route path="/accounts" element={<AccountsPage />} />
      <Route path="/apis" element={<ApisPage />} />
      <Route path="/team" element={<TeamPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/firewall" element={<Navigate to="/settings" replace />} />
    </Route>
  </Routes></AuthGate></TooltipProvider>;
}
