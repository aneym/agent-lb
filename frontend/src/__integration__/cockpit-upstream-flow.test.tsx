import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { z } from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { TeamMemberSchema } from "@/features/team/schemas";
import { post } from "@/lib/api-client";
import {
  createAccountSummary,
  createApiKey,
  createDashboardAuthSession,
  createDashboardSettings,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const multiProviderAccounts = [
  createAccountSummary({
    accountId: "claude",
    provider: "anthropic",
    email: "claude@example.test",
    displayName: "claude@example.test",
  }),
  createAccountSummary({
    accountId: "codex",
    provider: "openai",
    email: "codex@example.test",
    displayName: "codex@example.test",
  }),
];

describe("fork cockpit with upstream v1.24 capabilities", () => {
  beforeEach(() => {
    useAuthStore.setState({
      initialized: false,
      authenticated: false,
      loading: true,
      passwordRequired: false,
      adminLoginRequested: false,
      role: "admin",
      permissions: ["read", "write"],
      canWrite: true,
    });
    server.use(
      http.get("/api/accounts", () => HttpResponse.json({ accounts: multiProviderAccounts })),
    );
  });

  afterEach(() => {
    cleanup();
    act(() =>
      useAuthStore.setState({ canWrite: true, role: "admin", permissions: ["read", "write"] }),
    );
  });

  it("keeps provider and seat accounts on the default cockpit and exposes native account and automation pages", async () => {
    server.use(
      http.get("/api/pools/seat-accounts", () =>
        HttpResponse.json({
          accounts: [
            {
              id: "seat-cursor",
              vendor: "cursor",
              enabled: true,
              ready: true,
              authOk: true,
              planLabel: "Team seat",
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Providers" })).toBeInTheDocument();
    expect(
      await screen.findByText("claude@example.test", {
        selector: "span.privacy-blur, span:not([class])",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("codex@example.test").length).toBeGreaterThan(0);
    expect(await screen.findByText("Cursor · Team seat")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Add account/ })).toHaveAttribute(
      "href",
      "/providers/add",
    );
    expect(screen.getByRole("link", { name: "List view" })).toHaveAttribute("href", "/accounts");

    await user.click(screen.getByRole("link", { name: "Automations" }));
    expect(await screen.findByRole("heading", { name: "Automations" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/automations");
  });

  it("makes the cockpit read only for a guest and prevents mutating API calls", async () => {
    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json(
          createDashboardAuthSession({
            role: "guest",
            permissions: ["read"],
            passwordRequired: false,
            guestAccessEnabled: true,
          }),
        ),
      ),
    );
    const mutation = vi.fn();
    server.use(
      http.post("/api/accounts/claude/pause", () => {
        mutation();
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<App />);

    expect(await screen.findByText("Guest access · read only")).toBeInTheDocument();
    expect(document.querySelector('a[href="/providers/add"]')).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Actions for Plus · claude" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Open" })).toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Pause…" })).not.toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: "Check limits now" }),
    ).not.toBeInTheDocument();
    await expect(post("/api/accounts/claude/pause", z.object({}))).rejects.toMatchObject({
      status: 403,
      code: "read_only",
    });
    expect(mutation).not.toHaveBeenCalled();
  });

  it("preserves team key ownership when parsing the expanded upstream key schema", async () => {
    const member = TeamMemberSchema.parse({
      id: "member-team",
      name: "Teammate",
      status: "active",
      gate: "ok",
      createdAt: "2026-10-22T00:00:00Z",
      updatedAt: "2026-10-22T00:00:00Z",
      usage: { day: {}, week: {}, month: {} },
    });
    const keys = [
      createApiKey({ id: "personal", name: "Personal workstation", memberId: null }),
      createApiKey({ id: "team", name: "Team workstation", memberId: member.id }),
    ];
    server.use(
      http.get("/api/team/members", () => HttpResponse.json([member])),
      http.get("/api/api-keys/", () => HttpResponse.json(keys)),
    );
    window.history.pushState({}, "", "/keys");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Keys" })).toBeInTheDocument();
    expect(await screen.findByText("Teammate")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Team workstation/ })).toBeInTheDocument();
    expect(screen.getByText("team member · 1 key")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Personal workstation/ })).toBeInTheDocument();
  });

  it("saves native reset-credit controls with the settings version and fork team settings", async () => {
    const settings = createDashboardSettings({
      version: 17,
      teamModeEnabled: true,
      teamPublicBaseUrl: "https://team.example.test",
    });
    let payload: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/settings", () => HttpResponse.json(settings)),
      http.put("/api/settings", async ({ request }) => {
        payload = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...settings, ...payload, version: 18 });
      }),
    );
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/settings/general");
    renderWithProviders(<App />);
    await user.click(await screen.findByRole("switch", { name: "Show reset-credit badges" }));
    await waitFor(() =>
      expect(payload).toMatchObject({
        expectedVersion: 17,
        showResetCreditBadges: false,
        teamModeEnabled: true,
        teamPublicBaseUrl: "https://team.example.test",
      }),
    );
  });
});
