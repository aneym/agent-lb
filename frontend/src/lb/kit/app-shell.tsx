import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { useConnectAddress, usePools, useRuntimeVersion, useStickySessions } from "../api";
import { clock } from "../format";

const nav = [{ label: "Providers", path: "/" }, { label: "Routing", path: "/routing" }, { label: "Usage", path: "/usage" }, { label: "Keys", path: "/keys" }, { label: "Settings", path: "/settings" }];
export function AppShell() {
  const { pathname } = useLocation(); const [mobile, setMobile] = useState(false);
  const address = useConnectAddress().data?.connectAddress;
  const version = useRuntimeVersion().data?.currentVersion;
  const pools = usePools().data;
  const sessions = useStickySessions().data;
  // Policy versions are shown by the routing page once available.
  const links = nav.map(link => <Link key={link.path} to={link.path} onClick={() => setMobile(false)} aria-current={link.path === "/" ? pathname === "/" || pathname.startsWith("/providers") ? "page" : undefined : pathname.startsWith(link.path) ? "page" : undefined}>{link.label}</Link>);
  return <div className="lb"><header className="top"><div className="top-in"><Link className="brand" to="/"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 17V7l8-4 8 4v10l-8 4zM4 7l8 5 8-5m-8 5v9" /></svg>agent-lb</Link><nav className="nav" aria-label="Primary">{links}</nav><div className="top-r"><span className="serving"><span className="pulse" />Serving <span className="addr mono">{address && !address.startsWith("<") ? address : window.location.host}</span></span>{version && <span className="mono hide-m">v{version}</span>}</div><button className="btn ghost icon lb-mobile-menu" aria-label="Menu" aria-expanded={mobile} onClick={() => setMobile(!mobile)}><Menu size={18} /></button></div>{mobile && <nav className="lb-menu" aria-label="Mobile primary">{links}</nav>}</header><main><Outlet /></main><footer className="foot">{pools && <span>Last sync {clock(pools.generatedAt)}</span>}{sessions && <span>{sessions.total.toLocaleString()} sticky sessions</span>}</footer></div>;
}
