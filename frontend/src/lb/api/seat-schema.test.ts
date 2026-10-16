import { describe, expect, it } from "vitest";

import example from "../pages/providers/__fixtures__/seat-usage.example.json";
import { PoolsSchema, SeatAccountsSchema, UsageSummarySchema } from "./index";

describe("CLI seat usage contract", () => {
  it("accepts the contract example for seat accounts, pools and the usage summary", () => {
    const seats = SeatAccountsSchema.parse(example.seatAccounts);
    expect(seats.quotaRefreshedAt).toBe("2026-10-16T14:00:05Z");
    const muse = seats.accounts.find((a) => a.vendor === "muse");
    expect(muse?.authOk).toBeNull();
    expect(muse?.quota).toMatchObject({ source: "unavailable", unavailableReason: "not_reported" });
    expect(muse?.today?.tokensIn).toBeNull();
    const devin = seats.accounts.find((a) => a.vendor === "devin");
    expect(devin?.quota?.windows.map((w) => [w.id, w.kind, w.remainingPercent])).toEqual([
      ["daily", "daily", 80],
      ["weekly", "weekly", 45],
    ]);
    const pools = PoolsSchema.parse({ generatedAt: "2026-10-16T14:00:00Z", pools: example.poolsExcerpt });
    expect(pools.pools[0].windows?.[0]).toMatchObject({ id: "cursor-models", measuredAccounts: 1 });
    expect(pools.pools[1]).toMatchObject({ quotaSource: "unavailable", windows: [] });
    const summary = UsageSummarySchema.parse(example.usageSummaryCursor);
    expect(summary.provider).toBe("cursor");
    expect(summary.seatActivity).toMatchObject({ periodHours: 168, runs: 31, tokensKnown: true });
  });

  it("accepts a seat account from a server that predates the contract", () => {
    const legacy = SeatAccountsSchema.parse({
      accounts: [
        {
          id: "cursor-main",
          vendor: "cursor",
          tier: "Pro+",
          enabled: true,
          ready: true,
          authOk: true,
          cooldownUntil: null,
          lastRunAt: null,
        },
      ],
    });
    expect(legacy.accounts[0].quota).toBeUndefined();
  });

  it("rejects a window kind or quota source outside the contract", () => {
    const account = example.seatAccounts.accounts[1];
    const badKind = {
      ...example.seatAccounts,
      accounts: [{ ...account, quota: { ...account.quota, windows: [{ ...account.quota.windows[0], kind: "hourly" }] } }],
    };
    expect(SeatAccountsSchema.safeParse(badKind).success).toBe(false);
    const badSource = {
      ...example.seatAccounts,
      accounts: [{ ...account, quota: { ...account.quota, source: "guess" } }],
    };
    expect(SeatAccountsSchema.safeParse(badSource).success).toBe(false);
  });
});
