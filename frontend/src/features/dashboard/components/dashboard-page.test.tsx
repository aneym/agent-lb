import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect, type PropsWithChildren } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { DashboardPage } from "@/features/dashboard/components/dashboard-page";
import {
  createAccountSummary,
  createDashboardOverview,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

let lastLocation = { pathname: "", search: "" };

function LocationSpy() {
  const location = useLocation();
  useEffect(() => {
    lastLocation = { pathname: location.pathname, search: location.search };
  }, [location]);
  return null;
}

function mockMixedOverview() {
  server.use(
    http.get("/api/dashboard/overview", () =>
      HttpResponse.json(
        createDashboardOverview({
          accounts: [
            createAccountSummary({
              accountId: "acc_codex",
              email: "codex@example.com",
              displayName: "codex@example.com",
              provider: "openai",
            }),
            createAccountSummary({
              accountId: "acc_claude",
              email: "claude@example.com",
              displayName: "claude@example.com",
              provider: "anthropic",
            }),
          ],
        }),
      ),
    ),
  );
}

function renderDashboard(initialEntry = "/dashboard") {
  const queryClient = createTestQueryClient();
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <LocationSpy />
          <Routes>
            <Route path="/dashboard" element={children} />
            <Route path="/accounts" element={<div>accounts page</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(<DashboardPage />, { wrapper: Wrapper });
}

describe("DashboardPage provider filter", () => {
  beforeEach(() => {
    lastLocation = { pathname: "", search: "" };
    mockMixedOverview();
  });

  it("initializes the provider filter from the URL and scopes account cards", async () => {
    renderDashboard("/dashboard?provider=anthropic");

    // The email renders in several scoped widgets (account card, donut
    // legends); any occurrence proves the filtered account is shown.
    await screen.findAllByText("claude@example.com");

    expect(screen.getByRole("button", { name: /Claude/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /^All/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryAllByText("codex@example.com")).toHaveLength(0);
  });

  it("shows live per-provider account counts in the segments", async () => {
    renderDashboard("/dashboard");

    await screen.findAllByText("codex@example.com");

    const filterGroup = screen.getByRole("group", {
      name: "Filter dashboard by provider",
    });
    const segments = within(filterGroup).getAllByRole("button");
    expect(segments.map((segment) => segment.textContent)).toEqual([
      "All2",
      "Codex1",
      "Claude1",
    ]);
  });

  it("writes the provider to the URL and omits it for all", async () => {
    const user = userEvent.setup();
    renderDashboard("/dashboard");

    await screen.findAllByText("codex@example.com");

    await user.click(screen.getByRole("button", { name: /Codex/ }));
    await waitFor(() =>
      expect(lastLocation.search).toContain("provider=openai"),
    );
    await waitFor(() =>
      expect(screen.queryAllByText("claude@example.com")).toHaveLength(0),
    );

    await user.click(screen.getByRole("button", { name: /^All/ }));
    await waitFor(() => expect(lastLocation.search).not.toContain("provider"));
    await screen.findAllByText("claude@example.com");
  });

  it("marks server-aggregate stat cards as all providers when scoped", async () => {
    renderDashboard("/dashboard?provider=openai");

    await screen.findAllByText("codex@example.com");

    expect(
      screen.getByText(/Requests \(7d\) · all providers/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Error rate \(7d\) · all providers/),
    ).toBeInTheDocument();
  });

  it("carries the provider filter into the account details deep link", async () => {
    const user = userEvent.setup();
    renderDashboard("/dashboard?provider=anthropic");

    await screen.findAllByText("claude@example.com");

    await user.click(screen.getByRole("button", { name: "Details" }));

    await waitFor(() => expect(lastLocation.pathname).toBe("/accounts"));
    expect(lastLocation.search).toContain("selected=acc_claude");
    expect(lastLocation.search).toContain("provider=anthropic");
  });
});
