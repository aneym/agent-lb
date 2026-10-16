import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/mocks/server";

import example from "./__fixtures__/seat-usage.example.json";
import { ProvidersPage } from "./providers-page";

function serve(seatAccounts: object = example.seatAccounts, pools: object[] = example.poolsExcerpt) {
  server.use(
    http.get("/api/pools", () =>
      HttpResponse.json({ generatedAt: "2026-10-16T14:00:00Z", pools }),
    ),
    http.get("/api/pools/seat-accounts", () => HttpResponse.json(seatAccounts)),
    http.get("/api/receipts/summary", () =>
      HttpResponse.json({ totals: { requests: 0, costUsd: 0, errors: 0 }, groups: [], series: [] }),
    ),
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <div className="lb">
          <ProvidersPage />
        </div>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const seatRow = (id: string) => screen.findByRole("group", { name: new RegExp(id) });
const markUrl = (row: HTMLElement) =>
  (row.querySelector(".logo") as HTMLElement | null)?.style.getPropertyValue("--m") ?? "";

describe("providers page CLI seats", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-16T14:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders Cursor, Devin and Muse seats as account rows with their real windows in ET", async () => {
    serve();
    renderPage();

    const muse = await seatRow("muse-main");
    expect(muse).toHaveTextContent("Muse · seat");
    expect(muse).toHaveTextContent("Not reported by Muse");
    expect(muse).not.toHaveTextContent("No reading");
    expect(muse).not.toHaveTextContent("Cursor");
    expect(muse).toHaveTextContent("0 runs");
    expect(markUrl(muse)).toContain("/brand/providers/muse.svg");

    const devin = await seatRow("devin-main");
    expect(devin).toHaveTextContent("Devin · Devin Pro");
    expect(devin).toHaveTextContent("80%");
    expect(devin).toHaveTextContent("Daily · resets 12:00 AM ET");
    expect(devin).toHaveTextContent("45%");
    expect(devin).toHaveTextContent("Weekly · resets Tue 12:00 AM ET");
    expect(devin).toHaveTextContent("2 runs");
    expect(devin).toHaveTextContent("tokens not reported");
    expect(devin).not.toHaveTextContent(/\b0 tokens/);

    const cursor = await seatRow("cursor-gmail");
    expect(cursor).toHaveTextContent("Cursor · Ultra");
    expect(cursor).toHaveTextContent("Rate limited");
    expect(cursor).toHaveTextContent("until Oct 29, 8:00 PM ET");
    expect(cursor).toHaveTextContent("Cursor models · Grok, Composer · resets Oct 29, 8:00 PM ET");
    expect(cursor).toHaveTextContent("38%");
    expect(cursor).toHaveTextContent("Other models · resets Oct 29, 8:00 PM ET");
    expect(cursor).toHaveTextContent("6 runs");
    expect(cursor).toHaveTextContent("est. value");
    expect(markUrl(cursor)).toContain("/brand/providers/cursor.svg");

    expect(screen.queryByText(/Seat details coming/)).not.toBeInTheDocument();
    expect(screen.queryByText("CLI logins")).not.toBeInTheDocument();
  });

  it("drives seat pool rows from their windows and names a missing reading", async () => {
    serve();
    renderPage();
    const details = (await screen.findByRole("heading", { name: "Pool details" })).closest(
      "section",
    ) as HTMLElement;
    const cursorPool = (await within(details).findByText("Cursor models")).closest(
      ".row",
    ) as HTMLElement;
    expect(cursorPool).toHaveTextContent("38%");
    expect(cursorPool).toHaveTextContent("resets Oct 29, 8:00 PM ET");
    const musePool = within(details).getByText("Muse").closest(".row") as HTMLElement;
    expect(musePool).toHaveTextContent("Not reported by Muse");
    expect(musePool).not.toHaveTextContent("No reading");
    expect(markUrl(musePool)).toContain("/brand/providers/muse.svg");
  });

  it("keeps rendering seats from a server without the quota fields", async () => {
    serve({
      stateUpdatedAt: "2026-10-16T14:00:00Z",
      accounts: [
        {
          id: "muse-main",
          vendor: "muse",
          tier: null,
          enabled: true,
          ready: true,
          authOk: true,
          cooldownUntil: null,
          lastRunAt: null,
        },
      ],
    });
    renderPage();
    const muse = await seatRow("muse-main");
    expect(muse).toHaveTextContent("Muse · seat");
    expect(muse).toHaveTextContent("Active");
    expect(markUrl(muse)).toContain("muse.svg");
  });
});
