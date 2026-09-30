import { useState } from "react";
import "./routing-dashboard.css";

import {
  planFooter,
  poolRows,
  type AccountPlan,
  type DashboardPool,
  type PlanLevel,
} from "../pool-rows";

const LEVELS = ["budget", "balanced", "unlimited"] as const;
const LADDERS = [
  ["implement", "Write code"],
  ["mechanical", "Small edits"],
  ["explore", "Read and explore"],
  ["review", "Review"],
] as const;

const toneFill = {
  success: "var(--ok-fill)",
  warning: "var(--warn-fill)",
  danger: "var(--bad-fill)",
} as const;

export function RoutingDashboard({
  pools,
  plan,
  now = new Date(),
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone,
}: {
  pools: DashboardPool[];
  plan: AccountPlan | null;
  now?: Date;
  timeZone?: string;
}) {
  const rows = poolRows(pools, now, timeZone);
  const footer = planFooter(plan);
  return (
    <div className="routing-dashboard">
      {plan ? <AccountPlanCard plan={plan} /> : null}
      <section className="card">
        <h2>Pools</h2>
        <div className="pool-rows">
          {rows.map((row) => (
            <div key={row.name} className="pool-row">
              <div className="pool-row-head">
                <span className="pool-name">{row.name}</span>
                <span className="pool-value">{row.value}</span>
              </div>
              <div className="pool-bar" aria-hidden>
                <i style={{ width: `${row.fraction * 100}%`, background: toneFill[row.tone] }} />
                {row.ticks.map((tick, index) => (
                  <span key={index} className="pool-tick" style={{ left: `${tick * 100}%` }} />
                ))}
              </div>
              {row.subline ? <p className="pool-sub">{row.subline}</p> : null}
            </div>
          ))}
        </div>
        {footer ? <p className="pool-foot">{footer}</p> : null}
      </section>
    </div>
  );
}

function AccountPlanCard({ plan }: { plan: AccountPlan }) {
  const [level, setLevel] = useState<(typeof LEVELS)[number]>(plan.recommended);
  const current: PlanLevel = plan.levels[level];
  return (
    <section className="card" aria-label="Account plan">
      <div className="plan-head">
        <h2>Account plan</h2>
        <div className="seg" role="group" aria-label="Budget level">
          {LEVELS.map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={level === key}
              onClick={() => setLevel(key)}
            >
              {key === "budget" ? "Budget" : key === "balanced" ? "Balanced" : "Unlimited"}
              {plan.recommended === key ? <span className="rec"> recommended</span> : null}
            </button>
          ))}
        </div>
      </div>
      <p className="plan-why">{current.why}</p>
      <table className="plan-accounts">
        <tbody>
          {current.accounts.map((account) => (
            <tr key={account.pool}>
              <td>
                <span className="plan-label">
                  {account.label}, {account.count} {account.count === 1 ? "account" : "accounts"}
                </span>
                <span className="plan-reason">{account.reason}</span>
              </td>
              <td>
                <span className={`chip ${account.change}`}>{account.change}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="plan-ladders-label">Where work goes</p>
      <ol className="plan-ladders">
        {LADDERS.map(([key, label]) => {
          const steps = current.ladders[key] ?? [];
          return (
            <li key={key}>
              <span className="job">{label}</span>
              <span className="steps">
                {steps.map((step, index) => (
                  <span key={step.id} className="step">
                    {key !== "review" && index > 0 ? <span className="arrow"> → </span> : null}
                    {step.model}
                  </span>
                ))}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="plan-risk">{current.risk}</p>
    </section>
  );
}
