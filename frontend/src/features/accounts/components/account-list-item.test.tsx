import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountListItem } from "@/features/accounts/components/account-list-item";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountListItem", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the full email as the row title", () => {
    const account = createAccountSummary();

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
  });

  it("renders an empty quota track when secondary remaining percent is unknown", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: null,
      },
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByTestId("mini-quota-track-weekly")).toHaveClass(
      "bg-muted",
    );
    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1h")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1d")).toBeInTheDocument();
  });

  it("does not show a status label for active accounts", () => {
    const account = createAccountSummary({ status: "active" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    // The glyph carries the status for screen readers only.
    expect(screen.getByText("Active")).toHaveClass("sr-only");
  });

  it("shows an icon + label for non-default statuses", () => {
    const account = createAccountSummary({ status: "quota_exceeded" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("Quota exceeded")).toBeInTheDocument();
  });

  it("dims deactivated accounts", () => {
    const account = createAccountSummary({ status: "deactivated" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByRole("button")).toHaveClass("opacity-60");
    expect(screen.getByText("Deactivated")).toBeInTheDocument();
  });

  it("labels Anthropic OAuth usage windows as session and week", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      usage: {
        primaryRemainingPercent: 80,
        secondaryRemainingPercent: 4,
      },
      resetAtPrimary: "2026-01-01T13:00:00.000Z",
      resetAtSecondary: "2026-01-02T12:00:00.000Z",
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("Session")).toBeInTheDocument();
    expect(screen.getByText("Week")).toBeInTheDocument();
    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("omits the 5h row for weekly-only accounts", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 73,
      },
      resetAtPrimary: null,
      resetAtSecondary: "2026-01-02T12:00:00.000Z",
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1d")).toBeInTheDocument();
  });

  it("shows only the monthly row for monthly-only accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: null,
        monthlyRemainingPercent: 73,
      },
      resetAtPrimary: null,
      resetAtSecondary: null,
      resetAtMonthly: "2026-01-31T12:00:00.000Z",
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
  });

  it("renders legacy primary quota data without window metadata", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 64,
        secondaryRemainingPercent: null,
      },
      resetAtPrimary: "2026-01-01T13:00:00.000Z",
      resetAtSecondary: null,
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "5h" })).toHaveAttribute(
      "aria-valuenow",
      "64",
    );
    expect(screen.getByText("Reset in 1h")).toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("does not duplicate unavailable reset labels", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 64,
        secondaryRemainingPercent: null,
      },
      resetAtPrimary: null,
      resetAtSecondary: null,
      windowMinutesPrimary: 300,
      windowMinutesSecondary: null,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("Reset --")).toBeInTheDocument();
    expect(
      screen.queryByText("Reset Reset unavailable"),
    ).not.toBeInTheDocument();
  });

  it("shows only the 5h row when the account quota preference is 5h", () => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "5h" });

    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: 73,
      },
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("renders quota fill when secondary remaining percent is available", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: 73,
      },
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByRole("progressbar", { name: "Weekly" })).toHaveAttribute(
      "aria-valuenow",
      "73",
    );
  });

  it("marks burn-first accounts in the context line", () => {
    const account = createAccountSummary({ routingPolicy: "burn_first" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("burn first")).toBeInTheDocument();
  });

  it("marks preserved accounts in the context line", () => {
    const account = createAccountSummary({ routingPolicy: "preserve" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("preserve")).toBeInTheDocument();
  });

  it("shows a mirror badge for non-locally-owned accounts", () => {
    const account = createAccountSummary({
      ownerInstance: "studio",
      isLocallyOwned: false,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("mirror: studio")).toBeInTheDocument();
  });

  it("does not show a mirror badge for locally-owned accounts", () => {
    const account = createAccountSummary({
      ownerInstance: null,
      isLocallyOwned: true,
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.queryByText(/^mirror/)).not.toBeInTheDocument();
  });

  it("does not annotate normal routing policy", () => {
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.queryByText("normal")).not.toBeInTheDocument();
    expect(screen.queryByText("Normal")).not.toBeInTheDocument();
  });

  it("shows the alias as the title and keeps the email visible", () => {
    const account = createAccountSummary({
      alias: "Personal Plus",
      email: "work@example.com",
      isEmailDuplicate: true,
      planType: "team",
      workspaceLabel: "Design Workspace",
      seatType: "member",
    });

    render(
      <AccountListItem account={account} selected={false} onSelect={vi.fn()} />,
    );

    expect(screen.getByText("Personal Plus")).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "P" &&
          element.textContent ===
            "work@example.com · Team · Design Workspace / Member",
      ),
    ).toBeInTheDocument();
  });
});
