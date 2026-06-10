import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountUsagePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows '--' for missing quota percent instead of 0%", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 67,
      },
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText("5h remaining")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("hides 5h row for weekly-only accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 76,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.queryByText("5h remaining")).not.toBeInTheDocument();
    expect(screen.getByText("Weekly remaining")).toBeInTheDocument();
  });

  it("shows only Monthly for monthly-only free accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: null,
        monthlyRemainingPercent: 95,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
      resetAtPrimary: null,
      resetAtSecondary: null,
      resetAtMonthly: "2026-01-31T00:00:00.000Z",
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText("Monthly remaining")).toBeInTheDocument();
    expect(screen.queryByText("5h remaining")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly remaining")).not.toBeInTheDocument();
  });

  it("renders mapped label for the known gated additional quota limit", () => {
    const account = createAccountSummary({
      additionalQuotas: [
        {
          limitName: "codex_spark",
          meteredFeature: "codex_bengalfox",
          routingPolicy: "inherit",
          primaryWindow: {
            usedPercent: 35,
            resetAt: Math.floor(
              new Date("2026-01-07T13:00:00.000Z").getTime() / 1000,
            ),
            windowMinutes: 300,
          },
          secondaryWindow: null,
        },
      ],
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText("Additional quotas")).toBeInTheDocument();
    expect(screen.getByText("GPT-5.3-Codex-Spark")).toBeInTheDocument();
    expect(screen.getByText("5h used")).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: "5h used" }),
    ).toHaveAttribute("aria-valuenow", "35");
    expect(screen.getByText("Resets in 6d 13h")).toBeInTheDocument();
  });

  it("renders request log usage summary when available", () => {
    const account = createAccountSummary({
      requestUsage: {
        requestCount: 7,
        totalTokens: 51_480,
        cachedInputTokens: 41_470,
        totalCostUsd: 0.13,
      },
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText("Request logs total")).toBeInTheDocument();
    expect(screen.getByText(/\$0\.13/)).toBeInTheDocument();
    expect(screen.getByText(/51\.48K tok/)).toBeInTheDocument();
  });

  it("renders Anthropic cache creation and cache read token totals", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      requestUsage: {
        requestCount: 7,
        totalTokens: 51_480,
        cachedInputTokens: 0,
        cacheCreationTokens: 12_300,
        cacheReadTokens: 29_170,
        totalCostUsd: 0.13,
      },
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText(/12\.3K cache create/)).toBeInTheDocument();
    expect(screen.getByText(/29\.17K cache read/)).toBeInTheDocument();
  });

  it("renders Anthropic OAuth usage windows as session and week", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      usage: {
        primaryRemainingPercent: 80,
        secondaryRemainingPercent: 4,
      },
      resetAtPrimary: "2026-01-01T01:00:00.000Z",
      resetAtSecondary: "2026-01-02T00:00:00.000Z",
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountUsagePanel account={account} />);

    expect(screen.getByText("Session remaining")).toBeInTheDocument();
    expect(screen.getByText("Week remaining")).toBeInTheDocument();
    expect(screen.queryByText("5h remaining")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly remaining")).not.toBeInTheDocument();
  });
});
