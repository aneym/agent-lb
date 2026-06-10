import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountTrendSection } from "@/features/accounts/components/account-trend-section";
import {
  createAccountSummary,
  createAccountTrends,
} from "@/test/mocks/factories";

describe("AccountTrendSection", () => {
  it("renders nothing without trend data", () => {
    const account = createAccountSummary();

    const { container } = render(
      <AccountTrendSection account={account} trends={null} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows the weekly plan legend when scheduled trend data exists", () => {
    const account = createAccountSummary();
    const trends = createAccountTrends(account.accountId, {
      secondaryScheduled: [
        { t: "2026-01-01T00:00:00.000Z", v: 100 },
        { t: "2026-01-01T01:00:00.000Z", v: 99.4 },
      ],
    });

    render(<AccountTrendSection account={account} trends={trends} />);

    expect(screen.getByText("7-day trend")).toBeInTheDocument();
    expect(screen.getByText("Weekly plan")).toBeInTheDocument();
  });

  it("shows trends when only secondary scheduled trend points are present", () => {
    const account = createAccountSummary();
    const trends = createAccountTrends(account.accountId, {
      primary: [],
      secondary: [],
      secondaryScheduled: [
        { t: "2026-01-01T00:00:00.000Z", v: 100 },
        { t: "2026-01-01T06:00:00.000Z", v: 92 },
      ],
    });

    render(<AccountTrendSection account={account} trends={trends} />);

    expect(screen.getByText("7-day trend")).toBeInTheDocument();
    expect(screen.getByText("Weekly plan")).toBeInTheDocument();
  });
});
