import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";
import {
  DEFAULT_ACCOUNT_FILTERS,
  type AccountFilterState,
} from "@/features/accounts/filters";
import type { AccountSummary } from "@/features/accounts/schemas";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";

type HarnessProps = {
  accounts: AccountSummary[];
  initialFilters?: Partial<AccountFilterState>;
  selectedAccountId?: string | null;
  onSelect?: (accountId: string) => void;
};

function Harness({
  accounts,
  initialFilters,
  selectedAccountId = null,
  onSelect = () => {},
}: HarnessProps) {
  const [filters, setFilters] = useState<AccountFilterState>({
    ...DEFAULT_ACCOUNT_FILTERS,
    ...initialFilters,
  });
  return (
    <AccountList
      accounts={accounts}
      filters={filters}
      onFiltersChange={(patch) =>
        setFilters((current) => ({ ...current, ...patch }))
      }
      onClearFilters={() =>
        setFilters((current) => ({
          ...current,
          provider: "all",
          status: "all",
          query: "",
        }))
      }
      selectedAccountId={selectedAccountId}
      onSelect={onSelect}
      onOpenImport={() => {}}
      onOpenOauth={() => {}}
    />
  );
}

function account(overrides: Partial<AccountSummary>): AccountSummary {
  return {
    accountId: "acc-default",
    email: "default@example.com",
    displayName: "Default",
    planType: "plus",
    status: "active",
    limitWarmupEnabled: false,
    additionalQuotas: [],
    ...overrides,
  };
}

describe("AccountList", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-01-01T12:00:00.000Z").getTime(),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders items and filters by search", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <Harness
        accounts={[
          account({ accountId: "acc-1", email: "primary@example.com" }),
          account({
            accountId: "acc-2",
            email: "secondary@example.com",
            planType: "pro",
            status: "paused",
          }),
        ]}
        selectedAccountId="acc-1"
        onSelect={onSelect}
      />,
    );

    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText("Search accounts..."),
      "secondary",
    );
    expect(screen.queryByText("primary@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.click(screen.getByText("secondary@example.com"));
    expect(onSelect).toHaveBeenCalledWith("acc-2");
  });

  it("groups accounts by provider with operator labels and counts", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-openai",
            provider: "openai",
            email: "codex@example.com",
            planType: "pro",
          }),
          account({
            accountId: "acc-claude",
            provider: "anthropic",
            email: "claude@example.com",
            planType: "claude",
          }),
        ]}
      />,
    );

    expect(
      screen.getByRole("region", { name: "Codex accounts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Claude accounts" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Codex — 1 account")).toBeInTheDocument();
    expect(screen.getByText("Claude — 1 account")).toBeInTheDocument();
  });

  it("filters by provider through the segmented control", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-openai",
            provider: "openai",
            email: "codex@example.com",
          }),
          account({
            accountId: "acc-claude",
            provider: "anthropic",
            email: "claude@example.com",
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^Claude\s*1$/ }));

    expect(screen.queryByText("codex@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("claude@example.com")).toBeInTheDocument();
  });

  it("groups by status with problems first", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({ accountId: "acc-active", email: "active@example.com" }),
          account({
            accountId: "acc-reauth",
            email: "reauth@example.com",
            status: "reauth_required",
          }),
          account({
            accountId: "acc-paused",
            email: "paused@example.com",
            status: "paused",
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Group accounts" }));
    await user.click(
      await screen.findByRole("option", { name: "Group: Status" }),
    );

    const headings = screen
      .getAllByText(/— \d+ account/)
      .map((el) => el.textContent);
    expect(headings).toEqual([
      "Re-auth required — 1 account",
      "Paused — 1 account",
      "Active — 1 account",
    ]);
  });

  it("renders a flat list with no headers when grouping is off", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-openai",
            provider: "openai",
            email: "codex@example.com",
          }),
          account({
            accountId: "acc-claude",
            provider: "anthropic",
            email: "claude@example.com",
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Group accounts" }));
    await user.click(
      await screen.findByRole("option", { name: "Group: None" }),
    );

    expect(screen.queryByText(/— \d+ account/)).not.toBeInTheDocument();
    expect(screen.getByText("codex@example.com")).toBeInTheDocument();
    expect(screen.getByText("claude@example.com")).toBeInTheDocument();
  });

  it("sorts accounts by the rows actually rendered", () => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });

    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-hidden-early",
            email: "hidden-early@example.com",
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: 18,
            },
            resetAtPrimary: "2026-01-01T12:05:00.000Z",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
          }),
          account({
            accountId: "acc-visible-early",
            email: "visible-early@example.com",
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: 73,
            },
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
            resetAtSecondary: "2026-01-01T12:10:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
          }),
        ]}
      />,
    );

    expect(
      screen
        .getAllByText(/^(hidden-early|visible-early)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual(["visible-early@example.com", "hidden-early@example.com"]);
  });

  it("can sort accounts by subscription date", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-late",
            email: "late@example.com",
            subscription: {
              status: "active",
              nextChargeAt: "2026-02-01T12:00:00.000Z",
            },
          }),
          account({
            accountId: "acc-early",
            email: "early@example.com",
            subscription: {
              status: "active",
              currentPeriodEndAt: "2026-01-10T12:00:00.000Z",
            },
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Sort accounts" }));
    await user.click(
      await screen.findByRole("option", { name: "Subscription date" }),
    );

    expect(
      screen
        .getAllByText(/^(early|late)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual(["early@example.com", "late@example.com"]);
  });

  it("ignores elapsed reset timestamps when sorting", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-stale",
            email: "stale@example.com",
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: 18,
            },
            resetAtPrimary: "2026-01-01T11:30:00.000Z",
            resetAtSecondary: "2026-01-01T11:45:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
          }),
          account({
            accountId: "acc-fresh",
            email: "fresh@example.com",
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: 73,
            },
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
            resetAtSecondary: "2026-01-01T12:20:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
          }),
        ]}
      />,
    );

    expect(
      screen
        .getAllByText(/^(fresh|stale)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual(["fresh@example.com", "stale@example.com"]);
  });

  it("sorts legacy primary quota rows by their reset timestamp", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-late",
            email: "late@example.com",
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: null,
            },
            resetAtPrimary: "2026-01-01T13:00:00.000Z",
            resetAtSecondary: null,
          }),
          account({
            accountId: "acc-early",
            email: "early@example.com",
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: null,
            },
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            resetAtSecondary: null,
          }),
        ]}
      />,
    );

    expect(
      screen
        .getAllByText(/^(early|late)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual(["early@example.com", "late@example.com"]);
  });

  it("sorts accounts by name", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-z",
            email: "z@example.com",
            displayName: "Zeta",
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
          }),
          account({
            accountId: "acc-a",
            email: "a@example.com",
            displayName: "Alpha",
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
          }),
        ]}
        initialFilters={{ sort: "name_asc" }}
      />,
    );

    expect(
      screen.getAllByText(/^(a|z)@example\.com$/).map((el) => el.textContent),
    ).toEqual(["a@example.com", "z@example.com"]);
  });

  it("supports reverse name sorting", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-b",
            email: "b@example.com",
            displayName: "Beta",
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
          }),
          account({
            accountId: "acc-a",
            email: "a@example.com",
            displayName: "Alpha",
            resetAtPrimary: "2026-01-01T12:20:00.000Z",
          }),
        ]}
        initialFilters={{ sort: "name_desc" }}
      />,
    );

    expect(
      screen.getAllByText(/^(a|b)@example\.com$/).map((el) => el.textContent),
    ).toEqual(["b@example.com", "a@example.com"]);
  });

  it("can sort by latest reset first", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-a",
            email: "a@example.com",
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
          }),
          account({
            accountId: "acc-z",
            email: "z@example.com",
            resetAtPrimary: "2026-01-01T12:40:00.000Z",
          }),
        ]}
        initialFilters={{ sort: "reset_latest" }}
      />,
    );

    expect(
      screen.getAllByText(/^(a|z)@example\.com$/).map((el) => el.textContent),
    ).toEqual(["z@example.com", "a@example.com"]);
  });

  it("keeps unknown resets last when sorting by latest reset", () => {
    render(
      <Harness
        accounts={[
          account({ accountId: "acc-unknown", email: "unknown@example.com" }),
          account({
            accountId: "acc-stale",
            email: "stale@example.com",
            resetAtPrimary: "2026-01-01T11:30:00.000Z",
          }),
          account({
            accountId: "acc-latest",
            email: "latest@example.com",
            resetAtPrimary: "2026-01-01T12:40:00.000Z",
          }),
          account({
            accountId: "acc-earlier",
            email: "earlier@example.com",
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
          }),
        ]}
        initialFilters={{ sort: "reset_latest" }}
      />,
    );

    expect(
      screen
        .getAllByText(/^(latest|earlier|stale|unknown)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual([
      "latest@example.com",
      "earlier@example.com",
      "stale@example.com",
      "unknown@example.com",
    ]);
  });

  it("explains active filters and clears them from the empty state", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({ accountId: "acc-1", email: "primary@example.com" }),
        ]}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("Search accounts..."),
      "not-found",
    );
    expect(screen.getByText("No matching accounts")).toBeInTheDocument();
    expect(screen.getByText(/search "not-found"/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
  });

  it("filters re-auth required accounts by status", async () => {
    const user = userEvent.setup();

    render(
      <Harness
        accounts={[
          account({ accountId: "acc-active", email: "active@example.com" }),
          account({
            accountId: "acc-reauth",
            email: "reauth@example.com",
            status: "reauth_required",
          }),
        ]}
      />,
    );

    await user.click(
      screen.getByRole("combobox", { name: "Filter accounts by status" }),
    );
    await user.click(screen.getByRole("option", { name: "Re-auth required" }));

    expect(screen.queryByText("active@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("reauth@example.com")).toBeInTheDocument();
  });

  it("uses the backend duplicate indicator instead of recomputing by email", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5",
            email: "dup@example.com",
            isEmailDuplicate: false,
          }),
          account({
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            isEmailDuplicate: true,
          }),
          account({ accountId: "acc-3", email: "unique@example.com" }),
        ]}
      />,
    );

    expect(
      screen.queryByText(/ID d48f0bfc\.\.\.12b5d5/),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/ID 7f9de2ad\.\.\.a95cee/)).toBeInTheDocument();
  });

  it("shows the alias prominently for duplicate-email accounts", () => {
    render(
      <Harness
        accounts={[
          account({
            accountId: "acc-dup",
            email: "dup@example.com",
            alias: "Personal Plus",
            isEmailDuplicate: true,
          }),
        ]}
      />,
    );

    expect(screen.getByText("Personal Plus")).toBeInTheDocument();
    expect(screen.getByText(/dup@example\.com/)).toBeInTheDocument();
  });
});
