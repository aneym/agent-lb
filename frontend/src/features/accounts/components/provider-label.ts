import type { AccountProvider } from "@/features/accounts/schemas";

const PROVIDER_LABELS: Record<AccountProvider, string> = {
  openai: "Codex",
  anthropic: "Claude",
};

export function providerLabel(provider: AccountProvider | undefined): string {
  return PROVIDER_LABELS[provider ?? "openai"];
}
