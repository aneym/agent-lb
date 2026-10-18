import type { AccountSummary } from "@/features/accounts/schemas";
import { accountLabel } from "../../format";
import { providerMark } from "../../kit/provider-mark-helpers";

export const number = (n: number) => new Intl.NumberFormat("en-US").format(n);
export const percent = (n: number | null) => (n == null ? "—" : `${Math.round(n * 100)}%`);
export const latency = (n: number | null) =>
  n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`;
export const displayAccount = (a: AccountSummary | undefined, hideEmails: boolean, pending = false, provider?: string | null) =>
  a ? accountLabel(a, hideEmails) : provider === "muse" ? "Muse · host login" : pending ? "Loading account" : "Deleted or unknown account";
export const usageMoney = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Math.abs(value) < 10 ? 2 : 0,
    maximumFractionDigits: Math.abs(value) < 10 ? 2 : 0,
  }).format(value);
export const markFor = (provider?: string | null) =>
  provider === "anthropic" ? "claude" : providerMark(provider);
export const providerName = (provider?: string | null) =>
  provider === "anthropic"
    ? "Claude"
    : provider === "openai"
      ? "Codex"
      : provider === "glm"
        ? "GLM"
        : provider
          ? provider.charAt(0).toUpperCase() + provider.slice(1)
          : "Other";
