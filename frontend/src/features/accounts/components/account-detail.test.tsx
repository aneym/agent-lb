import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { AccountDetail } from "@/features/accounts/components/account-detail";
import { createAccountSummary } from "@/test/mocks/factories";

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("AccountDetail", () => {
  it("lets operators change account routing policy", async () => {
    const user = userEvent.setup();
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    renderWithClient(
      <AccountDetail
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onSetAlias={vi.fn().mockResolvedValue(undefined)}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={onRoutingPolicyChange}
        onSubscriptionSave={vi.fn().mockResolvedValue(undefined)}
        onSecurityWorkAuthorizedChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Routing policy" }));
    await user.click(await screen.findByRole("option", { name: "Preserve" }));

    expect(onRoutingPolicyChange).toHaveBeenCalledWith(account.accountId, "preserve");
  });

  it("shows cancel-pending subscriptions as active until the period end", () => {
    const account = createAccountSummary({
      subscription: {
        status: "cancel_pending",
        currentPeriodEndAt: "2026-06-22T04:00:00.000Z",
        amount: 200,
        currency: "USD",
      },
    });

    renderWithClient(
      <AccountDetail
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onSetAlias={vi.fn().mockResolvedValue(undefined)}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
        onSubscriptionSave={vi.fn().mockResolvedValue(undefined)}
        onSecurityWorkAuthorizedChange={vi.fn()}
      />,
    );

    expect(screen.getAllByText("Cancel pending")).not.toHaveLength(0);
    expect(screen.getByText(/Active until:/)).toBeInTheDocument();
  });

  it("shows a check sub action for canceled subscriptions", async () => {
    const user = userEvent.setup();
    const onSubscriptionCheck = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({
      subscription: {
        status: "canceled",
        currentPeriodEndAt: "2026-06-13T15:38:02.000Z",
        lastVerifiedAt: "2026-06-13T15:38:02.000Z",
      },
    });

    renderWithClient(
      <AccountDetail
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onSetAlias={vi.fn().mockResolvedValue(undefined)}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
        onSubscriptionSave={vi.fn().mockResolvedValue(undefined)}
        onSubscriptionCheck={onSubscriptionCheck}
        onSecurityWorkAuthorizedChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Check sub/i }));

    expect(onSubscriptionCheck).toHaveBeenCalledWith(account.accountId);
  });
});
