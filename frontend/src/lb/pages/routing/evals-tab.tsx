import { EmptyState } from "../../kit/primitives";
import "./policy.css";

export function EvalsTab() {
  return (
    <section className="policy-evals">
      <EmptyState
        title="No eval runs published to agent-lb yet"
        description="The first report ships as a doc. When runs are published here, you’ll see arms by dataset with pass rate, cost, tokens and wall time (with 95% intervals), startup cost per agent, and load per subscription. Trials will link to Harbor trajectories and receipts."
      />
    </section>
  );
}
