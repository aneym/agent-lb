import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import type { AccountSummary } from "@/features/accounts/schemas";

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccounts: vi.fn(),
  useAccountTrends: vi.fn(() => ({ data: null })),
}));

vi.mock("@/features/accounts/hooks/use-oauth", () => ({
  useOauth: vi.fn(() => ({
    state: {
      status: "idle",
      method: null,
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
      errorMessage: null,
    },
    start: vi.fn(),
    complete: vi.fn(),
    manualCallback: vi.fn(),
    reset: vi.fn(),
  })),
}));

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useUpstreamProxyAdmin: vi.fn(() => ({
    upstreamProxyQuery: { data: null, error: null },
    accountBindingMutation: {
      isPending: false,
      error: null,
      mutateAsync: vi.fn(),
    },
  })),
}));

const { useAccounts } = await import("@/features/accounts/hooks/use-accounts");
const mockedUseAccounts = useAccounts as unknown as ReturnType<typeof vi.fn>;

function idleMutation() {
  return {
    isPending: false,
    error: null,
    mutateAsync: vi.fn(),
  };
}

function account(overrides: Partial<AccountSummary>): AccountSummary {
  return {
    accountId: "acc-default",
    email: "default@example.com",
    displayName: "Default",
    planType: "plus",
    status: "active",
    additionalQuotas: [],
    limitWarmupEnabled: false,
    ...overrides,
  };
}

function mockAccounts(accounts: AccountSummary[]) {
  mockedUseAccounts.mockReturnValue({
    accountsQuery: {
      data: accounts,
      error: null,
      refetch: vi.fn(),
    },
    importMutation: idleMutation(),
    pauseMutation: idleMutation(),
    resumeMutation: idleMutation(),
    probeMutation: idleMutation(),
    deleteMutation: idleMutation(),
    exportAuthMutation: idleMutation(),
    setAliasMutation: idleMutation(),
    limitWarmupMutation: idleMutation(),
    routingPolicyMutation: idleMutation(),
    subscriptionMutation: idleMutation(),
    updateMutation: idleMutation(),
  } as unknown as ReturnType<typeof useAccounts>);
}

describe("AccountsPage", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-01-01T12:00:00.000Z").getTime(),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults the selected account to the first account after display sorting", () => {
    mockAccounts([
      account({
        accountId: "acc-api-first",
        email: "api-first@example.com",
        displayName: "API First",
        resetAtSecondary: "2026-01-01T13:00:00.000Z",
        windowMinutesSecondary: 10_080,
      }),
      account({
        accountId: "acc-visible-first",
        email: "visible-first@example.com",
        displayName: "Visible First",
        resetAtSecondary: "2026-01-01T12:10:00.000Z",
        windowMinutesSecondary: 10_080,
      }),
    ]);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(
      screen
        .getAllByText(/^(visible-first|api-first)@example\.com$/)
        .map((el) => el.textContent),
    ).toEqual([
      "visible-first@example.com",
      "api-first@example.com",
      // Detail pane repeats the selected account's email.
      "visible-first@example.com",
    ]);
    expect(
      screen.getByRole("heading", { name: "Visible First" }),
    ).toBeInTheDocument();
  });

  it("initializes the provider filter from URL search params", () => {
    mockAccounts([
      account({
        accountId: "acc-codex",
        provider: "openai",
        email: "codex@example.com",
      }),
      account({
        accountId: "acc-claude",
        provider: "anthropic",
        email: "claude@example.com",
      }),
    ]);

    render(
      <MemoryRouter initialEntries={["/accounts?provider=anthropic"]}>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.queryByText("codex@example.com")).not.toBeInTheDocument();
    expect(screen.getAllByText("claude@example.com").length).toBeGreaterThan(0);
  });

  it("respects the deep-linked ?selected account", () => {
    mockAccounts([
      account({
        accountId: "acc-1",
        email: "first@example.com",
        displayName: "First",
      }),
      account({
        accountId: "acc-2",
        email: "second@example.com",
        displayName: "Second",
      }),
    ]);

    render(
      <MemoryRouter initialEntries={["/accounts?selected=acc-2"]}>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument();
  });
});
