import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountActions", () => {
  it("renders an explicit routing policy selector", async () => {
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={onRoutingPolicyChange}
      />,
    );

    expect(
      screen.getByRole("combobox", { name: "Routing policy" }),
    ).toHaveTextContent("Normal");
  });

  it("renders re-authenticate action for re-auth required accounts", () => {
    const onReauth = vi.fn();
    const account = createAccountSummary({ status: "reauth_required" });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onDelete={vi.fn()}
        onReauth={onReauth}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Re-authenticate" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Pause" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Routing policy" }),
    ).not.toBeInTheDocument();
  });

  it("fires the per-account probe callback for active accounts", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary();
    const onProbe = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={onProbe}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Force probe" }));

    expect(onProbe).toHaveBeenCalledWith(account.accountId);
    expect(onProbe).toHaveBeenCalledTimes(1);
  });

  it("shows a banked count and offers reset limits for limited OpenAI accounts", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({
      provider: "openai",
      status: "rate_limited",
      resetCreditsAvailable: 2,
    });
    const onRedeemResetCredit = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={onRedeemResetCredit}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Reset limits (2)" }));

    expect(onRedeemResetCredit).toHaveBeenCalledWith(account.accountId);
    expect(onRedeemResetCredit).toHaveBeenCalledTimes(1);
  });

  it("shows a known empty bank without offering a redemption", () => {
    const account = createAccountSummary({
      provider: "openai",
      resetCreditsAvailable: 0,
    });
    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Banked resets: 0")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset limits/ })).not.toBeInTheDocument();
  });

  it("shows unknown inventory without treating it as zero", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      resetCreditsAvailable: null,
    });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Banked resets: unknown")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset limits/ })).not.toBeInTheDocument();
  });

  it("offers reset limits for limit-blocked Anthropic accounts with a banked credit", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      status: "quota_exceeded",
      resetCreditsAvailable: 3,
    });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Banked resets: 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset limits (3)" })).toBeEnabled();
  });

  it("does not offer redemption to active accounts with banked credits", () => {
    const account = createAccountSummary({
      provider: "openai",
      status: "active",
      resetCreditsAvailable: 3,
    });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Banked resets: 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset limits/ })).not.toBeInTheDocument();
  });

  it("does not offer redemption to canceled subscriptions", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      status: "quota_exceeded",
      resetCreditsAvailable: 3,
      subscription: { status: "canceled" },
    });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onRedeemResetCredit={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Banked resets: 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reset limits/ })).not.toBeInTheDocument();
  });

  it.each(["paused", "deactivated"] as const)(
    "disables force probe for %s accounts",
    async (status) => {
      const user = userEvent.setup();
      const account = createAccountSummary({ status });
      const onProbe = vi.fn();

      render(
        <AccountActions
          account={account}
          busy={false}
          onPause={vi.fn()}
          onResume={vi.fn()}
          onProbe={onProbe}
          onDelete={vi.fn()}
          onReauth={vi.fn()}
          onExportAuth={vi.fn()}
          onLimitWarmupChange={vi.fn()}
          onRoutingPolicyChange={vi.fn()}
        />,
      );

      const button = screen.getByRole("button", { name: "Force probe" });
      expect(button).toBeDisabled();

      await user.click(button);

      expect(onProbe).not.toHaveBeenCalled();
    },
  );
});
