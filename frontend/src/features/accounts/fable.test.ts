import { describe, expect, it } from "vitest";

import {
  FABLE_SCOPED_WEEKLY_QUOTA_KEY,
  getFableQuota,
} from "@/features/accounts/fable";
import { createAccountSummary } from "@/test/mocks/factories";

function fableQuotaEntry(overrides: { usedPercent?: number; resetAt?: number | null } = {}) {
  return {
    quotaKey: FABLE_SCOPED_WEEKLY_QUOTA_KEY,
    limitName: FABLE_SCOPED_WEEKLY_QUOTA_KEY,
    meteredFeature: FABLE_SCOPED_WEEKLY_QUOTA_KEY,
    displayLabel: "Fable weekly (scoped)",
    routingPolicy: "inherit" as const,
    primaryWindow: {
      usedPercent: overrides.usedPercent ?? 38,
      resetAt: overrides.resetAt === undefined ? 1_785_704_400 : overrides.resetAt,
      windowMinutes: 10_080,
    },
    secondaryWindow: null,
  };
}

describe("getFableQuota", () => {
  it("returns remaining percent and ISO reset for an anthropic account", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      fableEligible: true,
      additionalQuotas: [fableQuotaEntry()],
    });

    const quota = getFableQuota(account);
    expect(quota).not.toBeNull();
    expect(quota?.usedPercent).toBe(38);
    expect(quota?.remainingPercent).toBe(62);
    expect(quota?.resetAtIso).toBe(
      new Date(1_785_704_400 * 1000).toISOString(),
    );
    expect(quota?.eligible).toBe(true);
  });

  it("returns null for openai accounts even with a matching quota entry", () => {
    const account = createAccountSummary({
      provider: "openai",
      additionalQuotas: [fableQuotaEntry()],
    });

    expect(getFableQuota(account)).toBeNull();
  });

  it("returns null when the anthropic account never reported the scoped limit", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      additionalQuotas: [],
    });

    expect(getFableQuota(account)).toBeNull();
  });

  it("clamps out-of-range used percent and tolerates a missing reset", () => {
    const account = createAccountSummary({
      provider: "anthropic",
      fableEligible: false,
      additionalQuotas: [fableQuotaEntry({ usedPercent: 130, resetAt: null })],
    });

    const quota = getFableQuota(account);
    expect(quota?.usedPercent).toBe(100);
    expect(quota?.remainingPercent).toBe(0);
    expect(quota?.resetAtIso).toBeNull();
    expect(quota?.eligible).toBe(false);
  });
});
