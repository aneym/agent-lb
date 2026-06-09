import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AccountProvider } from "@/features/accounts/schemas";
import { ProviderIcon } from "@/features/accounts/components/provider-icon";
import { providerLabel } from "@/features/accounts/components/provider-label";

const PROVIDER_BADGE_CLASSES: Record<AccountProvider, string> = {
  openai: "border-sky-500/20 bg-sky-500/10 text-sky-700 dark:text-sky-400",
  anthropic: "border-stone-500/20 bg-stone-500/10 text-stone-700 dark:text-stone-300",
};

export function ProviderBadge({
  provider,
  className,
}: {
  provider?: AccountProvider;
  className?: string;
}) {
  const normalizedProvider = provider ?? "openai";
  return (
    <Badge
      variant="outline"
      className={cn(PROVIDER_BADGE_CLASSES[normalizedProvider], className)}
    >
      <ProviderIcon provider={normalizedProvider} className="size-3" />
      {providerLabel(normalizedProvider)}
    </Badge>
  );
}
