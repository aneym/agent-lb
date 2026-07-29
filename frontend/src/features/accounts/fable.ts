import type { AccountSummary } from "@/features/accounts/schemas";

/** Quota key of Anthropic's dedicated Fable-scoped weekly limit. */
export const FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly";

export type FableQuota = {
  usedPercent: number;
  remainingPercent: number;
  /** ISO datetime of the window reset, when upstream reported one. */
  resetAtIso: string | null;
  /** Balancer eligibility for Fable-class requests (null when unknown). */
  eligible: boolean | null;
};

/**
 * The Fable-scoped weekly window for an account, or null when the account
 * is not Anthropic or has never reported the scoped limit.
 */
export function getFableQuota(account: AccountSummary): FableQuota | null {
  if ((account.provider ?? "openai") !== "anthropic") return null;
  const window = account.additionalQuotas.find(
    (quota) => quota.quotaKey === FABLE_SCOPED_WEEKLY_QUOTA_KEY,
  )?.primaryWindow;
  if (window == null) return null;
  const usedPercent = Math.min(100, Math.max(0, window.usedPercent));
  const resetAt = window.resetAt ?? null;
  return {
    usedPercent,
    remainingPercent: 100 - usedPercent,
    resetAtIso: resetAt !== null ? new Date(resetAt * 1000).toISOString() : null,
    eligible: account.fableEligible ?? null,
  };
}
