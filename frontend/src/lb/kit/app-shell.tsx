import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Eye, EyeOff, Menu } from "lucide-react";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useConnectAddress, usePools, useRuntimeVersion, useStickySessions } from "../api";
import { clock } from "../format";

const nav = [
  { label: "Providers", path: "/" },
  { label: "Routing", path: "/routing" },
  { label: "Requests", path: "/dashboard" },
  { label: "Usage", path: "/usage" },
  { label: "Keys", path: "/keys" },
  { label: "Automations", path: "/automations" },
  { label: "Settings", path: "/settings" },
];
export function AppShell() {
  const { pathname } = useLocation();
  const logout = useAuthStore((state) => state.logout);
  const role = useAuthStore((state) => state.role);
  const canWrite = useAuthStore((state) => state.canWrite);
  const startAdminLogin = useAuthStore((state) => state.startAdminLogin);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const hideEmails = usePrivacyStore((state) => state.blurred);
  const toggleEmails = usePrivacyStore((state) => state.toggle);
  const [mobile, setMobile] = useState(false);
  const address = useConnectAddress().data?.connectAddress;
  const version = useRuntimeVersion().data?.currentVersion;
  const pools = usePools().data;
  const sessions = useStickySessions().data;

  const links = nav.map((link) => (
    <Link
      key={link.path}
      to={link.path}
      onClick={() => setMobile(false)}
      aria-current={
        link.path === "/"
          ? pathname === "/" || pathname.startsWith("/providers")
            ? "page"
            : undefined
          : pathname.startsWith(link.path)
            ? "page"
            : undefined
      }
    >
      {link.label}
    </Link>
  ));
  return (
    <div className="lb">
      <header className="top">
        <div className="top-in">
          <Link className="brand" to="/">
            <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <path
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2.4"
                d="M8.5 16h5m0 0 8.5-5.75M13.5 16l8.5 5.75M13.5 16H22M30.758 16c0 8.15-6.607 14.758-14.758 14.758-8.15 0-14.758-6.607-14.758-14.758C1.242 7.85 7.85 1.242 16 1.242c8.15 0 14.758 6.608 14.758 14.758Z"
              />
            </svg>
            agent-lb
          </Link>
          <nav className="nav" aria-label="Primary">
            {links}
          </nav>
          <div className="top-r">
            <span className="serving">
              <span className="pulse" />
              Serving{" "}
              <span className="addr mono">
                {address && !address.startsWith("<") ? address : window.location.host}
              </span>
            </span>
            {version && <span className="mono hide-m">v{version}</span>}
            <button
              className="btn ghost icon"
              aria-label={hideEmails ? "Show emails" : "Hide emails"}
              aria-pressed={hideEmails}
              title={hideEmails ? "Show emails" : "Hide emails"}
              onClick={toggleEmails}
            >
              {hideEmails ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
            {!canWrite && (
              <button className="btn ghost sm" onClick={startAdminLogin}>
                Admin sign in
              </button>
            )}
            {(passwordRequired || (role === "guest" && guestPasswordRequired)) && (
              <button className="btn ghost sm" onClick={() => void logout()}>
                Sign out
              </button>
            )}
          </div>
          <button
            className="btn ghost icon lb-mobile-menu"
            aria-label="Menu"
            aria-expanded={mobile}
            onClick={() => setMobile(!mobile)}
          >
            <Menu size={18} />
          </button>
        </div>
        {mobile && (
          <nav className="lb-menu" aria-label="Mobile primary">
            {links}
          </nav>
        )}
      </header>
      <main>
        {["/dashboard", "/reports", "/apis", "/accounts"].includes(pathname) && (
          <nav className="tabs" aria-label="Explorer">
            <Link to="/dashboard">Requests</Link>
            <Link to="/reports">Reports</Link>
            <Link to="/apis">Key usage</Link>
            <Link to="/accounts">Accounts</Link>
          </nav>
        )}
        {!canWrite && (
          <p className="settings-notice" role="status">
            Guest access · read only
          </p>
        )}
        <Outlet />
      </main>
      <footer className="foot">
        {pools && <span>Last sync {clock(pools.generatedAt)}</span>}
        {sessions && <span>{sessions.total.toLocaleString()} sticky sessions</span>}
      </footer>
    </div>
  );
}
