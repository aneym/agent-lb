import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { useConnectAddress, usePools, useRuntimeVersion, useStickySessions } from "../api";
import { clock } from "../format";

const nav = [
  { label: "Providers", path: "/" },
  { label: "Routing", path: "/routing" },
  { label: "Usage", path: "/usage" },
  { label: "Keys", path: "/keys" },
  { label: "Settings", path: "/settings" },
];
export function AppShell() {
  const { pathname } = useLocation();
  const [mobile, setMobile] = useState(false);
  const address = useConnectAddress().data?.connectAddress;
  const version = useRuntimeVersion().data?.currentVersion;
  const pools = usePools().data;
  const sessions = useStickySessions().data;
  // Policy versions are shown by the routing page once available.
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
        <Outlet />
      </main>
      <footer className="foot">
        {pools && <span>Last sync {clock(pools.generatedAt)}</span>}
        {sessions && <span>{sessions.total.toLocaleString()} sticky sessions</span>}
      </footer>
    </div>
  );
}
