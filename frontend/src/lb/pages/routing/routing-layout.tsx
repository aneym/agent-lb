import { NavLink, Outlet } from "react-router-dom";
import { PageHead } from "@/lb/kit/primitives";
import { localMidnight, useRouteDecisions, useRoutingPolicy } from "./data";
import "./routing.css";

export function RoutingLayout() {
  const decisions = useRouteDecisions(localMidnight());
  const policy = useRoutingPolicy();
  const tabs = [
    { to: "/routing", label: "Pipeline", end: true },
    { to: "/routing/decisions", label: "Decisions", badge: decisions.data?.counts.total },
    {
      to: "/routing/policy",
      label: "Policy",
      badge: policy.data?.activeVersion != null ? `v${policy.data.activeVersion}` : undefined,
    },
    { to: "/routing/evals", label: "Evals" },
  ];
  return (
    <>
      <PageHead title="Routing">
        Open Factory picks the model and agent for each task. agent-lb then picks the account that
        serves each request. Both steps are shown here, and both are editable.
      </PageHead>
      <nav className="tabs" aria-label="Routing">
        {tabs.map(({ to, label, badge, end }) => (
          <NavLink key={to} to={to} end={end} aria-current={undefined}>
            {label}
            {badge != null && <span className="n">{badge}</span>}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
  );
}
