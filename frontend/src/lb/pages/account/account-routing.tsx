import type { AccountSummary } from "@/features/accounts/schemas";
import type { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useStickySessions } from "../../api";

export function Routing({
  account,
  mutations,
}: {
  account: AccountSummary;
  mutations: ReturnType<typeof useAccounts>;
}) {
  const sessions = useStickySessions().data;
  const stickyCount = sessions?.entries.filter((entry) => entry.key === account.accountId).length;
  return (
    <section className="card account-routing">
      <h2>Routing</h2>
      <div className="account-setting">
        <span>Policy</span>
        <div className="seg">
          {(
            [
              { name: "Prefer", value: "burn_first" },
              { name: "Normal", value: "normal" },
              { name: "Avoid", value: "preserve" },
            ] as const
          ).map(({ name, value }) => (
            <button
              key={value}
              aria-pressed={(account.routingPolicy || "normal") === value}
              disabled={mutations.routingPolicyMutation.isPending}
              onClick={() =>
                mutations.routingPolicyMutation.mutate({
                  accountId: account.accountId,
                  routingPolicy: value,
                })
              }
            >
              {name}
            </button>
          ))}
        </div>
      </div>
      <div className="account-setting">
        <span>Warm-up</span>
        <span className="account-warmup">
          <button
            type="button"
            role="switch"
            aria-label="Warm-up 10 min before reset"
            aria-checked={account.limitWarmupEnabled}
            className={`toggle ${account.limitWarmupEnabled ? "on" : ""}`}
            disabled={mutations.limitWarmupMutation.isPending}
            onClick={() =>
              mutations.limitWarmupMutation.mutate({
                accountId: account.accountId,
                enabled: !account.limitWarmupEnabled,
              })
            }
          />
          10 min before reset
        </span>
      </div>
      {stickyCount ? (
        <div className="account-setting">
          <span>Sticky sessions</span>
          <span>{stickyCount} pinned</span>
        </div>
      ) : null}
    </section>
  );
}
