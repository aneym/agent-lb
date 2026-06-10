import { providerLabel } from "@/features/accounts/components/provider-label";
import type { AccountProvider } from "@/features/accounts/schemas";

export type ProviderFilterValue = "all" | AccountProvider;

export const PROVIDER_FILTER_OPTIONS: ReadonlyArray<{
  value: ProviderFilterValue;
  label: string;
}> = [
  { value: "all", label: "All" },
  { value: "openai", label: providerLabel("openai") },
  { value: "anthropic", label: providerLabel("anthropic") },
];

/** Parse a `provider` URL param; unknown values fall back to "all". */
export function parseProviderFilterValue(
  value: string | null,
): ProviderFilterValue {
  return value === "openai" || value === "anthropic" ? value : "all";
}
